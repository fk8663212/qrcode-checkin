import csv
import io
import json
import re
import sqlite3
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook, load_workbook

from checkin import create_app
from checkin.db import get_db
from checkin.event_preferences import attendance_groups, get_preferences, save_preferences
from checkin.services import parse_csv
from conftest import client_for, perform_checkin, post, ticket

HEADERS = ['姓名', '年級', '學號', '職稱', '電子郵件地址']
TABLE_HEADERS = ['姓名', '年級', '學號', '職稱', '桌次', '電子郵件地址']
SETTINGS = '/admin/event-settings'


def configure(app, mode='grade'):
    return post(client_for(app, 1), SETTINGS, data={
        'roster_profile': 'campus', 'seating_mode': mode, 'default_affiliation': '同系測試單位'})


def excel(rows, headers=HEADERS, extra_sheet=False, id_format=None):
    workbook = Workbook()
    workbook.active.append(headers)
    for row in rows:
        workbook.active.append(row)
    if id_format:
        workbook.active['C2'].number_format = id_format
    if extra_sheet:
        workbook.create_sheet('另一份名單').append(headers)
    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def upload(app, raw, filename='guests.xlsx', profile='auto'):
    admin = client_for(app, 1)
    response = post(admin, '/admin/import', data={'file': (io.BytesIO(raw), filename), 'profile': profile})
    assert response.status_code == 200
    match = re.search(r'name="preview_id" value="([^"]+)"', response.text)
    return admin, response, match[1] if match else None


def confirm(admin, preview_id):
    assert preview_id
    return post(admin, '/admin/import/confirm', data={'preview_id': preview_id})


def test_delivered_excel_and_csv_import_same_fictional_roster(app):
    assert configure(app).status_code == 302
    root = Path(__file__).resolve().parent.parent
    assert list(next(csv.reader(io.StringIO((root / 'examples/attendees-campus.csv').read_text(encoding='utf-8-sig'))))) == TABLE_HEADERS
    workbook=load_workbook(root / 'examples/attendees-campus.xlsx')
    assert [cell.value for cell in workbook.active[1]] == TABLE_HEADERS
    assert workbook.active.auto_filter.ref == 'A1:F14'
    workbook.close()
    admin, response, preview_id = upload(app, (root / 'examples/attendees-campus.xlsx').read_bytes())
    assert '新增 <strong>13</strong>' in response.text and confirm(admin, preview_id).status_code == 302
    admin, response, preview_id = upload(app, (root / 'examples/attendees-campus.csv').read_bytes(), 'roster.csv')
    assert '新增 <strong>0</strong>' in response.text and '更新 <strong>13</strong>' in response.text
    assert confirm(admin, preview_id).status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 15
        assert db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0] == 15
        first = db.execute("SELECT * FROM attendees WHERE attendee_id='TEST-STU-001'").fetchone()
        assert (first['grade'], first['title'], first['email'], first['affiliation']) == (
            '碩一', '碩士生', 'guest01@example.org', '同系測試單位')


@pytest.mark.parametrize('extension', ['xlsx', 'csv'])
def test_six_column_roster_mixes_tables_and_grades_and_retains_existing_ticket_and_card(app,extension):
    token=ticket(app)
    staff=client_for(app,2)
    assert perform_checkin(staff,token=token).status_code == 201
    admin=client_for(app,1)
    assert post(admin,'/api/bingo/admin/registration',json={'game_id':1,'open':True}).status_code == 200
    guest=client_for(app)
    original=post(guest,'/api/bingo/claim',json={'token':token}).json['card']
    assert configure(app,'table_grade').status_code == 302
    rows=[['同名來賓','碩一','A001','碩士生','14–15 桌','student@example.org'],
          ['同名來賓','碩一','A002','碩士生','','other@example.org']]
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    writer.writerows([TABLE_HEADERS]+rows)
    raw=excel(rows,TABLE_HEADERS) if extension=='xlsx' else stream.getvalue().encode('utf-8-sig')
    admin,preview,preview_id=upload(app,raw,'mixed.'+extension)
    assert '桌次 · 14–15 桌' in preview.text and '<th>桌次</th><th>Email</th>' in preview.text
    with app.app_context():
        assert get_db().execute('SELECT table_no FROM attendees WHERE id=1').fetchone()[0] == '01'
    assert confirm(admin,preview_id).status_code == 302
    first=post(client_for(app),'/api/ticket',json={'token':token}).json
    other=post(client_for(app),'/api/ticket',json={'token':ticket(app,2)}).json
    assert first['seating'] == {'mode':'table','label':'桌次','value':'14–15 桌','note':''}
    assert other['seating']['value'] == '碩一區' and other['seating']['mode'] == 'grade'
    duplicate=perform_checkin(staff,token=token)
    assert duplicate.status_code == 200 and not duplicate.json['created']
    assert duplicate.json['attendee']['seating']['value'] == '14–15 桌'
    card=guest.get('/api/bingo/state').json['card']
    assert (card['id'],card['numbers']) == (original['id'],original['numbers'])
    assert card['seating']['value'] == '14–15 桌' and ticket(app)==token
    bingo_host=admin.get('/api/bingo/admin/state').json
    assert bingo_host['seating_label'] == '桌次／區域'
    assert bingo_host['players'][0]['seating']['value'] == '14–15 桌'
    report=post(admin,'/admin/export/tables').get_data(as_text=True)
    values=list(csv.reader(io.StringIO(report.lstrip('\ufeff'))))
    assert values[0] == ['seating_type','seating','total','checked_in','pending']
    assert ['table','14–15 桌','1','1','0'] in values and ['grade','碩一區','1','0','1'] in values
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 1


def test_mixed_groups_keep_actual_denominators_and_separate_table_names_from_grade_names(app):
    configure(app,'table_grade')
    rows=[['甲','碩一','A001','學生','11–13',''],['乙','碩二','A002','學生','11–13',''],
          ['丙','博一','A003','學生','14–15 桌',''],['丁','碩一','A004','學生','',''],
          ['戊','碩一','A005','學生','',''],['己','','A006','學生','碩一',''],['庚','','A007','學生','','']]
    admin,_,preview_id=upload(app,excel(rows,TABLE_HEADERS))
    assert confirm(admin,preview_id).status_code == 302
    assert perform_checkin(client_for(app,2),pk=1).status_code == 201
    with app.app_context():
        groups=attendance_groups()
    assert {group['key']:(group['checked'],group['total']) for group in groups} == {
        'table:11–13':(1,2),'table:14–15 桌':(0,1),'table:碩一':(0,1),'grade:碩一':(0,2),'':(0,1)}
    assert {group['key']:group['label'] for group in groups}['table:14–15 桌'] == '14–15 桌'
    assert groups[-1]['label'] == '未安排座位'
    page=admin.get('/admin?q=A001&status=pending').text
    assert '現場桌次／區域概況' in page and 'data-table-no="grade:碩一"' in page
    assert 'data-table-no="table:碩一"' in page and '14–15 桌 桌' not in page


def test_mixed_priority_and_legacy_omission_do_not_clear_stored_table(app):
    configure(app,'table_grade')
    admin=client_for(app,1)
    fields={'attendee_id':'A001','name':'同名來賓','table_no':'08','grade':'碩一','seating_area':'教師'}
    assert post(admin,'/admin/attendees/1',data=fields).status_code == 302
    assert 'type="hidden" name="table_no"' not in admin.get('/admin/attendees/1').text
    rows=[['同名來賓','碩一','A001','碩士生','student@example.org']]
    admin,preview,preview_id=upload(app,excel(rows))
    assert '保留既有桌次' in preview.text and confirm(admin,preview_id).status_code == 302
    assert post(client_for(app),'/api/ticket',json={'token':ticket(app)}).json['seating']['value'] == '08'
    admin,_,preview_id=upload(app,excel([rows[0][:4]+['',rows[0][4]]],TABLE_HEADERS))
    assert confirm(admin,preview_id).status_code == 302
    assert post(client_for(app),'/api/ticket',json={'token':ticket(app)}).json['seating']['value'] == '教師區'
    fields.update(table_no='',seating_area='')
    assert post(admin,'/admin/attendees/1',data=fields).status_code == 302
    assert post(client_for(app),'/api/ticket',json={'token':ticket(app)}).json['seating']['value'] == '碩一區'
    fields['grade']=''
    assert post(admin,'/admin/attendees/1',data=fields).status_code == 302
    assert post(client_for(app),'/api/ticket',json={'token':ticket(app)}).json['seating']['value'] == '現場安排'


def test_import_warns_if_current_seating_mode_will_ignore_uploaded_table(app):
    configure(app,'grade')
    _,preview,_=upload(app,excel([['甲','碩一','A001','學生','11–13','']],TABLE_HEADERS))
    assert '目前座位方式不會顯示桌次' in preview.text


def test_mixed_mode_is_saved_in_consistent_backup_and_survives_restart(app,tmp_path):
    configure(app,'table_grade')
    runner=app.test_cli_runner()
    backup=tmp_path/'mixed-seating.sqlite3'
    assert runner.invoke(args=['backup-db',str(backup)]).exit_code == 0
    configure(app,'table')
    assert runner.invoke(args=['restore-db',str(backup),'--confirm-stopped']).exit_code == 0
    restarted=create_app(dict(app.config))
    with restarted.app_context():
        assert get_preferences()['seating_mode'] == 'table_grade'


def test_mixed_raffle_keeps_draw_time_table_range_after_later_roster_changes(app):
    configure(app,'table_grade')
    admin,_,preview_id=upload(app,excel([['同名來賓','碩一','A001','學生','14–15 桌','']],TABLE_HEADERS))
    assert confirm(admin,preview_id).status_code == 302
    assert perform_checkin(client_for(app,2),pk=1).status_code == 201
    result=post(admin,'/api/raffle/admin/prize',json={'round_id':1,'name':'測試獎品','quantity':1})
    result=post(admin,'/api/raffle/admin/draw',json={'round_id':1,'prize_id':result.json['prizes'][0]['id'],
                                                  'count':1,'request_id':'mixed-seating-raffle-00001'})
    assert result.status_code == 200 and result.json['winners'][0]['seating']['value'] == '14–15 桌'
    assert post(admin,'/admin/attendees/1',data={'attendee_id':'A001','name':'更新姓名','table_no':'','grade':'碩二'}).status_code == 302
    assert admin.get('/api/raffle/admin/state').json['winners'][0]['seating']['value'] == '14–15 桌'


def test_school_reimport_retains_ticket_checkin_card_and_hidden_old_table(app):
    token = ticket(app)
    assert perform_checkin(client_for(app, 2), token=token).status_code == 201
    assert configure(app).status_code == 302
    rows = [['同名來賓', '碩一', 'A001', '碩士生', 'student@example.org']]
    admin, _, preview_id = upload(app, excel(rows))
    assert confirm(admin, preview_id).status_code == 302
    assert post(admin, '/api/bingo/admin/registration', json={'game_id': 1, 'open': True}).status_code == 200
    guest = client_for(app)
    claim = post(guest, '/api/bingo/claim', json={'token': token})
    assert claim.status_code == 200
    original = claim.json['card']
    assert original['seating']['value'] == '碩一區'
    rows[0][1] = '碩二'
    admin, _, preview_id = upload(app, excel(rows))
    assert confirm(admin, preview_id).status_code == 302
    card = guest.get('/api/bingo/state').json['card']
    assert (card['id'], card['numbers']) == (original['id'], original['numbers'])
    assert card['seating']['value'] == '碩二區'
    assert ticket(app) == token
    with app.app_context():
        assert get_db().execute('SELECT table_no FROM attendees WHERE id=1').fetchone()[0] == '01'
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 1


def test_missing_new_optional_columns_preserve_data_and_explicit_blank_clears(app):
    configure(app)
    admin, _, preview_id = upload(app, excel([['同名來賓', '碩一', 'A001', '碩士生', 'student@example.org']]))
    assert confirm(admin, preview_id).status_code == 302
    admin, _, preview_id = upload(app, b'attendee_id,name\nA001,Updated\n', 'roster.csv')
    assert confirm(admin, preview_id).status_code == 302
    with app.app_context():
        row = get_db().execute('SELECT grade,title,email FROM attendees WHERE id=1').fetchone()
        assert tuple(row) == ('碩一', '碩士生', 'student@example.org')
    admin, _, preview_id = upload(app, b'attendee_id,name,email\nA001,Updated,\n', 'roster.csv')
    assert confirm(admin, preview_id).status_code == 302
    with app.app_context():
        assert get_db().execute('SELECT email FROM attendees WHERE id=1').fetchone()[0] == ''


def test_region_denominators_follow_actual_grades_and_filters_do_not_change_them(app):
    configure(app)
    grades = ['碩一', '碩二', '碩二', '碩二', '博一', '博一', '']
    rows = [[f'測試來賓{i}', grade, f'A{i:03}', '學生', ''] for i, grade in enumerate(grades, 1)]
    admin, _, preview_id = upload(app, excel(rows))
    assert confirm(admin, preview_id).status_code == 302
    assert perform_checkin(client_for(app, 2), pk=1).status_code == 201
    with app.app_context():
        assert {group['key']: (group['checked'], group['total']) for group in attendance_groups()} == {
            '碩一': (1, 1), '碩二': (0, 3), '博一': (0, 2), '': (0, 1)}
    page = admin.get('/admin?q=A001&status=pending').text
    assert '現場區域概況' in page and '碩一區' in page and '未安排區域' in page
    report = post(admin, '/admin/export/tables')
    values = list(csv.reader(io.StringIO(report.data.decode('utf-8-sig'))))
    assert values[0][0] == 'grade_or_area' and ['碩二', '3', '0', '3'] in values


def test_grade_override_and_all_seating_modes_preserve_source_fields(app):
    configure(app)
    assert post(client_for(app, 1), '/admin/attendees/1', data={
        'attendee_id': 'A001', 'name': '同名來賓', 'table_no': '08', 'grade': '碩一', 'seating_area': '教師'}).status_code == 302
    for mode, value in [('grade', '教師區'), ('area', '教師區'), ('open', '自由入座'), ('table', '08')]:
        assert configure(app, mode).status_code == 302
        response = post(client_for(app), '/api/ticket', json={'token': ticket(app)})
        assert response.json['seating']['value'] == value
    with app.app_context():
        row = get_db().execute('SELECT table_no,grade,seating_area FROM attendees WHERE id=1').fetchone()
        assert tuple(row) == ('08', '碩一', '教師')


def test_contact_information_and_student_ids_stay_in_administrator_exports(app):
    configure(app)
    admin, _, preview_id = upload(app, excel([['測試學生', '碩一', 'M-PRIVATE-001', '碩士生', 'private@example.org']]))
    assert confirm(admin, preview_id).status_code == 302
    with app.app_context():
        pk = get_db().execute("SELECT id FROM attendees WHERE attendee_id='M-PRIVATE-001'").fetchone()[0]
    data = post(client_for(app), '/api/ticket', json={'token': ticket(app, pk)}).json
    assert 'private@example.org' not in json.dumps(data) and 'M-PRIVATE-001' not in json.dumps(data)
    assert 'email' not in data and 'attendee_id' not in data
    staff_data = client_for(app, 2).get('/api/staff/search?q=M-PRIVATE-001').json
    assert 'private@example.org' not in json.dumps(staff_data)
    report = post(admin, '/admin/export/tickets').get_data(as_text=True)
    assert 'private@example.org' in report and 'M-PRIVATE-001' in report
    assert post(client_for(app, 2), '/admin/export/tickets').status_code == 403


@pytest.mark.parametrize('rows,headers', [
    ([['甲', '碩一', '', '學生', '']], HEADERS),
    ([['', '碩一', 'S001', '學生', '']], HEADERS),
    ([['甲', '碩一', 'S001', '學生', 'bad-email']], HEADERS),
    ([['甲', '碩一', 'S001', '學生', ''], ['乙', '碩二', 'S001', '學生', '']], HEADERS),
    ([['=1+1', '碩一', 'S001', '學生', '']], HEADERS),
    ([['甲', '碩一', True, '學生', '']], HEADERS),
    ([['甲', '碩一', 10 ** 16, '學生', '']], HEADERS),
    ([['甲', '碩一', 'S001', '學生', '']], ['姓名', '年級', '學號', '職稱', '學號']),
    ([['甲', '碩一', 'S001', '學生', '']], ['姓名', '年級', '未知欄位', '職稱', '電子郵件地址']),
])
def test_invalid_excel_is_preview_only_without_partial_writes(app, rows, headers):
    _, response, preview_id = upload(app, excel(rows, headers))
    assert preview_id is None and '尚未匯入' in response.text
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 2


@pytest.mark.parametrize('raw,filename', [(b'not-a-zip', 'bad.xlsx'), (b'bad', 'old.xls'),
                                          (b'\xff\xfe bad', 'bad.csv')])
def test_unreadable_files_have_preview_errors(app, raw, filename):
    _, response, preview_id = upload(app, raw, filename)
    assert not preview_id and '尚未匯入' in response.text


def test_excel_extra_sheets_and_oversized_archives_rejected(app):
    _, _, preview_id = upload(app, excel([['甲', '碩一', 'S001', '學生', '']], extra_sheet=True))
    assert not preview_id
    stream = io.BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('xl/worksheets/sheet1.xml', b'0' * (21 * 1024 * 1024))
    _, response, preview_id = upload(app, stream.getvalue())
    assert not preview_id and '過大' in response.text


def test_excel_numeric_identifier_display_zeros_and_duplicate_names(app):
    rows = [['同名', '碩一', 123, '學生', 'shared@example.org'], ['同名', '碩二', 'S002', '學生', 'shared@example.org']]
    admin, _, preview_id = upload(app, excel(rows, id_format='000000'))
    assert confirm(admin, preview_id).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM attendees WHERE attendee_id IN ('000123','S002')").fetchone()[0] == 2


def test_raffle_keeps_draw_time_region_when_roster_or_template_changes(app):
    configure(app)
    admin, _, preview_id = upload(app, excel([['同名來賓', '碩一', 'A001', '碩士生', 'student@example.org']]))
    assert confirm(admin, preview_id).status_code == 302
    assert perform_checkin(client_for(app, 2), pk=1).status_code == 201
    prize = post(admin, '/api/raffle/admin/prize', json={'round_id': 1, 'name': '測試獎品', 'quantity': 1})
    assert prize.status_code == 200
    prize_id = prize.json['prizes'][0]['id']
    draw = post(admin, '/api/raffle/admin/draw', json={
        'round_id': 1, 'prize_id': prize_id, 'count': 1, 'request_id': 'campus-raffle-test-00001'})
    assert draw.status_code == 200 and draw.json['winners'][0]['seating']['value'] == '碩一區'
    configure(app, 'table')
    assert post(admin, '/admin/attendees/1', data={'attendee_id': 'A001', 'name': '改名', 'grade': '碩二', 'table_no': '99'}).status_code == 302
    winner = admin.get('/api/raffle/admin/state').json['winners'][0]
    assert winner['seating']['value'] == '碩一區' and winner['name'] == '同名來賓'
    report = post(admin, '/raffle/export/csv').get_data(as_text=True)
    assert '碩一區' in report and '座位資訊' in report
    public = client_for(app).get('/api/raffle/display-state').json
    assert all('seating' not in value and 'email' not in value for value in public['winners'])


def test_excel_xml_entities_and_macros_are_rejected_before_import(app):
    original = excel([['甲', '碩一', 'S001', '學生', '']])
    for variant in ['entity', 'macro']:
        stream = io.BytesIO()
        with ZipFile(io.BytesIO(original)) as source, ZipFile(stream, 'w', ZIP_DEFLATED) as target:
            for entry in source.infolist():
                data = source.read(entry)
                if variant == 'entity' and entry.filename == 'xl/worksheets/sheet1.xml':
                    data = data.replace(b'<worksheet', b'<!DOCTYPE worksheet [<!ENTITY evil SYSTEM "file:///does-not-exist">]><worksheet', 1)
                target.writestr(entry, data)
            if variant == 'macro':
                target.writestr('xl/vbaProject.bin', b'fixture macro')
        _, response, preview_id = upload(app, stream.getvalue())
        assert not preview_id and '尚未匯入' in response.text


def test_preset_permissions_csrf_and_stale_import_previews(app):
    for user_id, status in [(None, 401), (2, 403)]:
        assert post(client_for(app, user_id), SETTINGS, data={}).status_code == status
    assert client_for(app, 1).post(SETTINGS, data={}).status_code == 403
    admin, _, preview_id = upload(app, excel([['甲', '碩一', 'S001', '學生', '']]))
    assert configure(app).status_code == 302
    assert confirm(admin, preview_id).status_code == 409
    assert post(admin, SETTINGS, data={'roster_profile': 'unknown', 'seating_mode': 'bad'}).status_code == 400
    with app.app_context():
        assert get_preferences()['seating_mode'] == 'grade'
        assert get_db().execute("SELECT COUNT(*) FROM audit_log WHERE action='event_preferences_update'").fetchone()[0] == 1


def test_changed_preferences_apply_to_next_import_in_same_context(app):
    with app.app_context():
        assert get_preferences()['default_affiliation'] == ''
        save_preferences({'id': 1}, {'roster_profile': 'campus', 'seating_mode': 'grade', 'default_affiliation': '新的同系單位'})
        rows, errors = parse_csv('姓名,年級,學號,職稱,電子郵件地址\n測試,碩一,S001,學生,\n'.encode())
        assert not errors and rows[0]['affiliation'] == '新的同系單位'
        assert get_preferences()['seating_mode'] == 'grade'


def test_preset_and_academic_fields_survive_restart_backup_restore_and_old_schema_upgrade(app, tmp_path):
    configure(app)
    token = ticket(app)
    admin, _, preview_id = upload(app, excel([['同名來賓', '碩一', 'A001', '碩士生', 'student@example.org']]))
    assert confirm(admin, preview_id).status_code == 302
    assert perform_checkin(client_for(app, 2), pk=1).status_code == 201
    runner = app.test_cli_runner()
    backup = tmp_path / 'campus.sqlite3'
    assert runner.invoke(args=['backup-db', str(backup)]).exit_code == 0
    configure(app, 'table')
    assert runner.invoke(args=['restore-db', str(backup), '--confirm-stopped']).exit_code == 0
    restored = create_app(dict(app.config))
    with restored.app_context():
        assert get_preferences()['seating_mode'] == 'grade'
    data = post(client_for(restored), '/api/ticket', json={'token': token}).json
    assert data['checked_in'] and data['seating']['value'] == '碩一區'
    # Simulate a pre-preset consistent backup, then run normal additive startup.
    with sqlite3.connect(backup) as db:
        db.execute('DROP TABLE event_preferences')
        for column in ['grade', 'title', 'email', 'seating_area']:
            db.execute('ALTER TABLE attendees DROP COLUMN ' + column)
        db.execute('ALTER TABLE raffle_winners DROP COLUMN seating_json')
    assert restored.test_cli_runner().invoke(args=['restore-db', str(backup), '--confirm-stopped']).exit_code == 0
    upgraded = create_app(dict(app.config))
    assert ticket(upgraded) == token
    assert post(client_for(upgraded), '/api/ticket', json={'token': token}).json['checked_in']
    with upgraded.app_context():
        assert get_preferences()['seating_mode'] == 'table'
        assert get_db().execute('SELECT table_no FROM attendees WHERE id=1').fetchone()[0] == '01'
