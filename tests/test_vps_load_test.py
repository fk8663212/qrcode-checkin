import csv
import io
import threading

import pytest
from flask import redirect
from werkzeug.serving import WSGIRequestHandler, make_server

from checkin.db import get_db, transaction
from conftest import PASSWORD, client_for, post
from scripts.vps_load_test import (Client, LoadTestError, duration_seconds, main,
                                   normalize_url, prepare_bingo, read_guests)


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


@pytest.fixture
def live_site(app):
    with app.app_context(), transaction() as db:
        for number in (1, 2):
            db.execute('UPDATE attendees SET attendee_id=?,name=? WHERE id=?',
                       (f'TEST120-{number:03}', f'來賓 {number}（測試）', number))
    app.add_url_rule('/external-redirect', view_func=lambda: redirect('https://example.invalid/never-follow'))
    server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
    base = f'http://127.0.0.1:{server.server_port}'
    app.config['PUBLIC_BASE_URL'] = base
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    with app.app_context():
        guests = [{'attendee_id': row['attendee_id'], 'name': row['name'], 'token': row['token']}
                  for row in get_db().execute('SELECT a.attendee_id,a.name,t.token FROM attendees a JOIN tickets t ON t.attendee_pk=a.id ORDER BY a.id')]
    try:
        yield base, guests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


@pytest.mark.parametrize('url', ['http://example.org', 'https://user:password@example.org',
                                 'https://example.org/path', 'https://example.org?token=bad',
                                 'https://example.org#bad', 'https://example.org:99999',
                                 'https://example.org\n', 'http://192.168.1.10:5001'])
def test_remote_url_requires_https_and_no_credentials_or_fragments(url):
    with pytest.raises(LoadTestError):
        normalize_url(url, allow_local_http=True)


def test_local_http_requires_explicit_loopback_flag():
    with pytest.raises(LoadTestError):
        normalize_url('http://127.0.0.1:9000')
    assert normalize_url('http://127.0.0.1:9000/', True) == 'http://127.0.0.1:9000'
    assert normalize_url('https://test.example.org/') == 'https://test.example.org'


@pytest.mark.parametrize('value', ['0s', '4s', '3h', '30', '-1m', 'nan', '1m; echo bad'])
def test_duration_rejects_unbounded_or_invalid_runs(value):
    with pytest.raises(LoadTestError):
        duration_seconds(value)


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['attendee_id', 'name', 'ticket_url'])
        writer.writeheader()
        writer.writerows(rows)


def test_csv_only_selects_test_ids_and_preserves_independent_tickets(tmp_path):
    path = tmp_path / 'tickets.csv'
    write_csv(path, [{'attendee_id': 'REAL001', 'name': 'Private', 'ticket_url': 'https://other.invalid/ticket#' + 'x' * 43},
                     {'attendee_id': 'TEST120-001', 'name': 'Alice (Test)', 'ticket_url': 'https://test.example.org/ticket#' + 'a' * 43},
                     {'attendee_id': 'TEST200-002', 'name': 'Bob (Test)', 'ticket_url': 'https://test.example.org/ticket#' + 'b' * 43}])
    rows = read_guests(path, 'https://test.example.org')
    assert len(rows) == 2 and len({row['token'] for row in rows}) == 2
    assert rows[0]['attendee_id'] == 'TEST120-001'


@pytest.mark.parametrize('url', ['https://other.example.org/ticket#' + 'x' * 43,
                                'https://test.example.org/ticket?token=bad#' + 'x' * 43,
                                'https://test.example.org/ticket#short',
                                'https://user:password@test.example.org/ticket#' + 'x' * 43])
def test_csv_rejects_wrong_origin_query_credentials_or_invalid_ticket(tmp_path, url):
    path = tmp_path / 'tickets.csv'
    write_csv(path, [{'attendee_id': 'TEST120-001', 'name': 'Alice (Test)', 'ticket_url': url}])
    with pytest.raises(LoadTestError) as error:
        read_guests(path, 'https://test.example.org')
    assert 'x' * 43 not in str(error.value) and 'password' not in str(error.value)


def test_csv_rejects_duplicate_tokens_and_roster_without_ticket_links(tmp_path):
    path = tmp_path / 'tickets.csv'
    write_csv(path, [{'attendee_id': f'TEST120-{index:03}', 'name': 'Test',
                     'ticket_url': 'https://test.example.org/ticket#' + 'a' * 43} for index in (1, 2)])
    with pytest.raises(LoadTestError):
        read_guests(path, 'https://test.example.org')
    path.write_text('attendee_id,name,affiliation,table_no\nTEST120-001,Test,Dept,01\n')
    with pytest.raises(LoadTestError):
        read_guests(path, 'https://test.example.org')


def test_client_does_not_follow_cross_origin_redirect(live_site):
    base, _guests = live_site
    status, _body, headers = Client(base).request('/external-redirect')
    assert status == 302 and headers['Location'] == 'https://example.invalid/never-follow'


def test_closed_registration_preflight_never_checks_anyone_in(app, live_site):
    base, guests = live_site
    with pytest.raises(LoadTestError, match='開放領卡'):
        prepare_bingo(base, guests, True, 'staff1', PASSWORD)
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0


def test_preparation_uses_normal_staff_login_and_repeated_checkin_is_safe(app, live_site):
    base, guests = live_site
    assert post(client_for(app, 1), '/api/bingo/admin/registration', json={'game_id': 1, 'open': True}).status_code == 200
    with pytest.raises(LoadTestError, match='尚未報到'):
        prepare_bingo(base, guests)
    prepared, count = prepare_bingo(base, guests, True, 'staff1', PASSWORD)
    assert count == 2 and prepared['game_id'] == 1 and len(prepared['guests']) == 2
    _again, count = prepare_bingo(base, guests, True, 'staff1', PASSWORD)
    assert count == 0
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 2
        assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 0


def test_all_identities_checked_before_first_attendance_write(app, live_site):
    base, guests = live_site
    assert post(client_for(app, 1), '/api/bingo/admin/registration', json={'game_id': 1, 'open': True}).status_code == 200
    guests[1] = {**guests[1], 'attendee_id': 'TEST120-999'}
    with pytest.raises(LoadTestError, match='名冊不一致'):
        prepare_bingo(base, guests, True, 'staff1', PASSWORD)
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0


def test_missing_k6_stops_before_network_or_mutation(monkeypatch):
    monkeypatch.setattr('scripts.vps_load_test.shutil.which', lambda _name: None)
    monkeypatch.setattr(Client, 'json', lambda *_args: pytest.fail('Must not send network traffic'))
    with pytest.raises(SystemExit) as error:
        main(['--url', 'https://test.example.org', '--users', '10'])
    assert error.value.code == 2
