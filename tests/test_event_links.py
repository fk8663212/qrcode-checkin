import json
import re
import sqlite3
from html import unescape

import pytest

from checkin import create_app
from checkin.db import get_db
from checkin.event_links import get_event_links
from conftest import client_for, perform_checkin, post, ticket

MENU = 'https://example.org/dinner/menu.pdf?lang=zh#page=1'
SCHEDULE = 'https://docs.example.org/schedule'
PATH = '/admin/event-links'


def save(client, menu=MENU, schedule=SCHEDULE):
    return post(client, PATH, data={'menu_url': menu, 'schedule_url': schedule})


def test_new_event_reserves_bilingual_buttons_without_links(app):
    assert post(client_for(app), '/api/ticket', json={'token': ticket(app)}).json['event_links'] == {
        'menu_url': '', 'schedule_url': ''}
    for language, labels in [('zh-TW', ['晚宴菜單', '活動流程', '即將公布']),
                             ('en', ['Dinner menu', 'Event schedule', 'Coming soon'])]:
        html = app.test_client().get('/ticket?lang=' + language).text
        assert all(label in html for label in labels)
        assert 'id="ticket-menu-pending"' in html and 'id="ticket-schedule-pending"' in html


@pytest.mark.parametrize('user_id,get_status,post_status', [(None, 302, 401), (2, 403, 403), (3, 403, 403)])
def test_only_administrators_can_read_or_change_links(app, user_id, get_status, post_status):
    client = client_for(app, user_id)
    assert client.get(PATH).status_code == get_status
    assert save(client).status_code == post_status
    with app.app_context():
        assert get_event_links() == {'menu_url': '', 'schedule_url': ''}
        assert get_db().execute("SELECT COUNT(*) FROM audit_log WHERE action='event_links_update'").fetchone()[0] == 0


def test_administrator_changes_require_csrf_and_get_does_not_save(app):
    admin = client_for(app, 1)
    assert admin.post(PATH, data={'menu_url': MENU}).status_code == 403
    assert admin.post(PATH, data={'menu_url': MENU}, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert admin.get(PATH, query_string={'menu_url': MENU}).status_code == 200
    with app.app_context():
        assert get_event_links()['menu_url'] == ''


def test_save_update_and_clear_links_are_shared_without_changing_ticket_or_attendance(app):
    admin = client_for(app, 1)
    guest = client_for(app)
    token = ticket(app)
    assert save(admin).status_code == 302
    data = post(guest, '/api/ticket', json={'token': token}).json
    assert data['event_links'] == {'menu_url': MENU, 'schedule_url': SCHEDULE}
    assert not data['checked_in'] and data['name'] == '同名來賓'
    assert save(admin, menu='', schedule=SCHEDULE + '/v2').status_code == 302
    assert post(guest, '/api/ticket', json={'token': token}).json['event_links'] == {
        'menu_url': '', 'schedule_url': SCHEDULE + '/v2'}
    assert save(admin, menu='', schedule='').status_code == 302
    assert ticket(app) == token
    with app.app_context():
        assert get_event_links() == {'menu_url': '', 'schedule_url': ''}
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0
        assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 0
        assert get_db().execute('SELECT COUNT(*) FROM raffle_draws').fetchone()[0] == 0
        logs = get_db().execute("SELECT * FROM audit_log WHERE action='event_links_update' ORDER BY id").fetchall()
        assert len(logs) == 3 and all(log['actor_id'] == 1 for log in logs)
        assert json.loads(logs[0]['detail']) == {'before': {'menu_url': '', 'schedule_url': ''},
                                                'after': {'menu_url': MENU, 'schedule_url': SCHEDULE}}
        assert token not in ''.join(log['detail'] for log in logs)


def test_unchanged_save_does_not_add_audit_and_operator_page_stays_chinese(app):
    admin = client_for(app, 1)
    assert save(admin).status_code == 302
    assert save(admin).status_code == 302
    html = admin.get(PATH + '?lang=en', headers={'Accept-Language': 'en-US'}).text
    assert '<html lang="zh-Hant">' in html and '儲存活動連結' in html
    assert 'public-strings' not in html
    assert '活動連結' in admin.get('/admin').text
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM audit_log WHERE action='event_links_update'").fetchone()[0] == 1


@pytest.mark.parametrize('bad_url', [
    'http://example.org/menu', '//example.org/menu', '/menu.pdf', 'javascript:alert(1)',
    'data:text/html,test', 'file:///etc/passwd', 'ftp://example.org/menu', 'https://',
    'https://user:password@example.org/menu', 'https://example.org/menu with spaces',
    'https://example.org/menu\n.pdf', 'https://example.org/menu\x00.pdf',
    'https://example.org/menu\\x', 'https://example.org:0/menu',
    'https://example.org:65536/menu', 'https://example.org:invalid/menu',
    'https://[invalid/menu', 'https://example.org/' + 'x' * 2048,
])
def test_invalid_link_cannot_partially_update_or_add_audit(app, bad_url):
    admin = client_for(app, 1)
    assert save(admin).status_code == 302
    response = save(admin, menu=MENU + '-new', schedule=bad_url)
    assert response.status_code == 400
    assert '活動流程請填寫完整 HTTPS 網址' in response.text
    with app.app_context():
        assert get_event_links() == {'menu_url': MENU, 'schedule_url': SCHEDULE}
        assert get_db().execute("SELECT COUNT(*) FROM audit_log WHERE action='event_links_update'").fetchone()[0] == 1


def test_https_query_fragments_and_trimmed_links_are_preserved(app):
    assert save(client_for(app, 1), menu='  ' + MENU + '  ', schedule=' https://example.org:443/流程表 ').status_code == 302
    with app.app_context():
        assert get_event_links() == {'menu_url': MENU, 'schedule_url': 'https://example.org:443/流程表'}


def test_invalid_form_value_is_escaped(app):
    value = 'https://example.org/"/><script>alert(1)</script> bad'
    response = save(client_for(app, 1), menu=value)
    assert response.status_code == 400
    assert '<script>alert(1)</script>' not in response.text
    attribute = re.search(r'id="menu_url"[^>]*value="([^"]*)"', response.text)
    assert attribute and unescape(attribute[1]) == value


def test_links_are_only_in_valid_ticket_response_and_do_not_leak_other_attendees(app):
    assert save(client_for(app, 1)).status_code == 302
    guest = client_for(app)
    assert post(guest, '/api/ticket', json={'token': 'revoked-or-invalid'}).status_code == 404
    data = post(guest, '/api/ticket', json={'token': ticket(app)}).json
    assert data['event_links']['menu_url'] == MENU
    assert 'attendees' not in data and 'attendee_id' not in data
    assert ticket(app, 2) not in json.dumps(data)
    assert MENU not in guest.get('/ticket').text
    assert save(guest).status_code == 401


def test_links_persist_after_restart_backup_and_restore(app, tmp_path):
    token = ticket(app)
    assert perform_checkin(client_for(app, 2), token=token).status_code == 201
    assert save(client_for(app, 1)).status_code == 302
    restarted = create_app(dict(app.config))
    assert post(client_for(restarted), '/api/ticket', json={'token': token}).json['event_links']['menu_url'] == MENU
    runner = restarted.test_cli_runner()
    backup = tmp_path / 'links-backup.sqlite3'
    assert runner.invoke(args=['backup-db', str(backup)]).exit_code == 0
    assert save(client_for(restarted, 1), menu='', schedule='').status_code == 302
    result = runner.invoke(args=['restore-db', str(backup), '--confirm-stopped'])
    assert result.exit_code == 0, result.output
    restored = create_app(dict(app.config))
    data = post(client_for(restored), '/api/ticket', json={'token': token}).json
    assert data['event_links'] == {'menu_url': MENU, 'schedule_url': SCHEDULE}
    assert data['checked_in'] and ticket(restored) == token


def test_old_backup_without_links_table_upgrades_with_empty_buttons(app, tmp_path):
    token = ticket(app)
    assert perform_checkin(client_for(app, 2), token=token).status_code == 201
    runner = app.test_cli_runner()
    backup = tmp_path / 'legacy.sqlite3'
    assert runner.invoke(args=['backup-db', str(backup)]).exit_code == 0
    with sqlite3.connect(backup) as db:
        db.execute('DROP TABLE event_links')
    assert save(client_for(app, 1)).status_code == 302
    result = runner.invoke(args=['restore-db', str(backup), '--confirm-stopped'])
    assert result.exit_code == 0, result.output
    upgraded = create_app(dict(app.config))
    data = post(client_for(upgraded), '/api/ticket', json={'token': token}).json
    assert data['event_links'] == {'menu_url': '', 'schedule_url': ''}
    assert data['checked_in'] and ticket(upgraded) == token
