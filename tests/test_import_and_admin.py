import csv
import io
import re

import pytest

from checkin.db import get_db
from conftest import client_for, perform_checkin, post, ticket


def preview(client, raw):
    response = post(client, '/admin/import', data={'file': (io.BytesIO(raw), 'guests.csv')})
    assert response.status_code == 200
    match = re.search(r'name="preview_id" value="([^"]+)"', response.text)
    return response, match.group(1) if match else None


def test_csv_reimport_upserts_and_preserves_ticket_and_checkin(app):
    old_token = ticket(app)
    perform_checkin(client_for(app, 2), token=old_token)
    admin = client_for(app, 1)
    raw = 'attendee_id,name,affiliation,table_no\nA001,更新姓名,新單位,02\nA003,更新姓名,,03\n'.encode()
    first, preview_id = preview(admin, raw)
    assert '新增 <strong>1</strong>' in first.text and '更新 <strong>1</strong>' in first.text
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 302
    _, preview_id = preview(admin, raw)
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 302
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM tickets').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(*) FROM checkins WHERE undone_at IS NULL').fetchone()[0] == 1
        assert db.execute('SELECT table_no FROM attendees WHERE id=1').fetchone()[0] == '02'
    assert ticket(app) == old_token


@pytest.mark.parametrize('raw', [
    b'name,table_no\nx,1\n', b'attendee_id,name,name\n1,a,b\n',
    b'attendee_id,name\n1,a\n1,b\n', b'attendee_id,name\n,a\n',
    b'attendee_id,name\n1,\n', b'attendee_id,name\n1,a,extra\n',
    b'attendee_id,name\n1\n', b'attendee_id,name\n', b'\xff\xfe invalid',
    b'attendee_id,name\n"unterminated,abc\n'
])
def test_csv_errors_preview_only_and_no_partial_import(app, raw):
    response, preview_id = preview(client_for(app, 1), raw)
    assert '尚未匯入' in response.text and preview_id is None
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 2


def test_bom_import_and_omitted_optional_fields(app):
    admin = client_for(app, 1)
    _, preview_id = preview(admin, '\ufeffattendee_id,name\nA003,新的來賓\n'.encode('utf-8'))
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 302


def test_stale_consumed_and_other_owner_preview_rejected(app):
    admin = client_for(app, 1)
    _, preview_id = preview(admin, b'attendee_id,name\nA003,new\n')
    assert post(admin, '/admin/attendees/1', data={'attendee_id': 'A001', 'name': '已修改'}).status_code == 302
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 409
    _, preview_id = preview(admin, b'attendee_id,name\nA003,new\n')
    with app.app_context():
        get_db().execute("UPDATE import_previews SET user_id=2 WHERE id=?", (preview_id,))
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 409
    _, preview_id = preview(admin, b'attendee_id,name\nA003,new\n')
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 302
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 409


def test_200_guest_capacity_and_201st_rejected(app):
    admin = client_for(app, 1)
    raw = ('attendee_id,name\n' + ''.join(f'B{i:03},Guest {i}\n' for i in range(198))).encode()
    _, preview_id = preview(admin, raw)
    assert post(admin, '/admin/import/confirm', data={'preview_id': preview_id}).status_code == 302
    response, preview_id = preview(admin, b'attendee_id,name\nOVER,extra\n')
    assert preview_id is None and '200 人上限' in response.text
    assert post(admin, '/admin/attendees/new', data={'attendee_id': 'OVER', 'name': 'extra'}).status_code == 409
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 200


@pytest.mark.parametrize('path', ['/admin','/admin/import','/admin/users','/admin/attendees/new','/admin/attendees/1'])
def test_staff_cannot_view_admin_pages(app, path):
    assert client_for(app, 2).get(path).status_code == 403


@pytest.mark.parametrize('path', ['/admin/import','/admin/import/confirm','/admin/attendees/new','/admin/attendees/1',
                                  '/admin/attendees/1/undo','/admin/attendees/1/reissue','/admin/attendees/1/qr',
                                  '/admin/export/tickets','/admin/export/attendance','/admin/export/tables','/admin/export/history',
                                  '/admin/export/audit','/admin/users'])
def test_staff_cannot_mutate_or_export_admin_data(app, path):
    assert post(client_for(app, 2), path).status_code == 403


def test_edit_create_search_and_report(app):
    admin, staff = client_for(app, 1), client_for(app, 2)
    assert post(admin, '/admin/attendees/new', data={'attendee_id': '=CSV-FORMULA', 'name': '<script>alert(1)</script>', 'table_no': '02'}).status_code == 302
    page = admin.get('/admin')
    assert '&lt;script&gt;' in page.text and '<script>alert(1)</script>' not in page.text
    assert post(admin, '/admin/attendees/new', data={'attendee_id': 'A001', 'name': '重複編號'}).status_code == 200
    assert len(staff.get('/api/staff/search?q=同名').json['results']) == 2
    assert len(staff.get('/api/staff/search?q=%25').json['results']) == 0
    assert perform_checkin(staff, pk=1).status_code == 201
    assert '目前顯示 1 人' in admin.get('/admin?status=checked').text
    assert '目前顯示 2 人' in admin.get('/admin?status=pending').text
    for kind in ('tickets', 'attendance', 'tables', 'history', 'audit'):
        report = post(admin, '/admin/export/' + kind)
        assert report.status_code == 200 and 'attachment' in report.headers['Content-Disposition']
        rows = list(csv.reader(io.StringIO(report.data.decode('utf-8-sig'))))
        assert len(rows) >= 2
        if kind == 'tickets':
            assert any(row[0] == "'=CSV-FORMULA" for row in rows[1:])
            assert all('/ticket#' in row[-1] for row in rows[1:])
        elif kind == 'tables':
            assert ['01', '2', '1', '1'] in rows[1:]
        else:
            assert ticket(app) not in report.text
