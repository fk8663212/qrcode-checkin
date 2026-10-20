import sqlite3

import pytest

from checkin import create_app
from checkin.db import get_db
from conftest import PASSWORD, client_for, perform_checkin, post, ticket


def test_real_login_logout_and_password_hash(app):
    client = client_for(app)
    assert post(client, '/login', data={'username': 'staff1', 'password': 'incorrect'}).status_code == 200
    assert post(client, '/login', data={'username': 'staff1', 'password': PASSWORD}).status_code == 302
    with client.session_transaction() as session:
        assert session['user_id'] == 2
    assert client.get('/staff').status_code == 200
    assert post(client, '/logout').status_code == 302
    assert client.get('/api/staff/search?q=A001').status_code == 401
    with app.app_context():
        value = get_db().execute('SELECT password_hash FROM users WHERE id=2').fetchone()[0]
        assert value != PASSWORD and value.startswith('pbkdf2:sha256:1000000$')


@pytest.mark.parametrize('path', ['/login','/api/ticket','/api/staff/checkin','/admin/import','/admin/export/tickets', '/admin/users'])
def test_every_post_requires_csrf(app, path):
    client = client_for(app, 1)
    assert client.post(path).status_code == 403
    assert client.post(path, headers={'X-CSRF-Token': 'invalid'}).status_code == 403


def test_staff_management_invalidates_sessions(app):
    admin, staff = client_for(app, 1), client_for(app, 2)
    assert post(admin, '/admin/users', data={'action': 'disable', 'username': 'staff1'}).status_code == 302
    assert staff.get('/api/staff/search?q=A001').status_code == 401
    assert post(admin, '/admin/users', data={'action': 'enable', 'username': 'staff1'}).status_code == 302
    fresh = client_for(app)
    assert post(fresh, '/login', data={'username': 'staff1', 'password': PASSWORD}).status_code == 302
    assert post(admin, '/admin/users', data={'action': 'reset', 'username': 'staff1', 'password': 'new-password-2026'}).status_code == 302
    assert fresh.get('/api/staff/search?q=A001').status_code == 401
    assert post(admin, '/admin/users', data={'action': 'create', 'username': 'staff-new', 'password': 'staff-new-password'}).status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT role FROM users WHERE username='staff-new'").fetchone()[0] == 'staff'
    assert post(admin, '/admin/users', data={'action': 'disable', 'username': 'admin'}).status_code == 404


def test_login_and_ticket_rate_limits_persist_across_clients(app):
    app.config.update(LOGIN_LIMIT=2, TICKET_LIMIT=2)
    for _ in range(2):
        assert post(client_for(app), '/login', data={'username': 'missing', 'password': 'wrong'}).status_code == 200
    assert post(client_for(app), '/login', data={'username': 'staff1', 'password': PASSWORD}).status_code == 429
    token = ticket(app)
    for _ in range(2):
        assert post(client_for(app), '/api/ticket', json={'token': token}).status_code == 200
    limited = post(client_for(app), '/api/ticket', json={'token': token})
    assert limited.status_code == 429 and limited.headers.get('Retry-After')
    with app.app_context():
        assert all(token not in row['key'] for row in get_db().execute('SELECT key FROM rate_limits'))


def test_no_cache_noindex_no_referrer_security_headers(app):
    response = app.test_client().get('/ticket')
    assert 'no-store' in response.headers['Cache-Control']
    assert 'noindex' in response.headers['X-Robots-Tag']
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert "script-src 'self'" in response.headers['Content-Security-Policy']
    assert 'HttpOnly' in response.headers['Set-Cookie'] and 'SameSite=Lax' in response.headers['Set-Cookie']


def test_production_requires_https_and_key_and_secure_cookie(tmp_path):
    config = {'TESTING': True, 'CHECKIN_ENV': 'production', 'DATABASE': str(tmp_path / 'prod.sqlite3'),
              'PUBLIC_BASE_URL': 'http://example.test', 'SECRET_KEY': 'x' * 64}
    with pytest.raises(RuntimeError):
        create_app(config)
    config['PUBLIC_BASE_URL'] = 'https://example.test'
    config['SECRET_KEY'] = 'short'
    with pytest.raises(RuntimeError):
        create_app(config)
    config['SECRET_KEY'] = 'x' * 64
    app = create_app(config)
    response = app.test_client().get('/login', base_url='https://example.test')
    assert 'Secure' in response.headers['Set-Cookie'] and 'HttpOnly' in response.headers['Set-Cookie']
    assert 'Strict-Transport-Security' in response.headers
    assert app.test_client().get('/login', base_url='https://other.test').status_code == 400


def test_restart_preserves_tokens_and_checkin(app):
    token = ticket(app)
    perform_checkin(client_for(app, 2), token=token)
    restarted = create_app(dict(app.config))
    assert ticket(restarted) == token
    value = post(client_for(restarted), '/api/ticket', json={'token': token})
    assert value.json['checked_in'] and value.json['checked_at']
    assert value.json['name'] == '同名來賓'


def test_online_backup_and_offline_restore(app, tmp_path):
    perform_checkin(client_for(app, 2), pk=1)
    runner = app.test_cli_runner()
    target = tmp_path / 'backup.sqlite3'
    result = runner.invoke(args=['backup-db', str(target)])
    assert result.exit_code == 0, result.output
    assert runner.invoke(args=['backup-db', str(target)]).exit_code != 0
    with sqlite3.connect(target) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 1
    assert perform_checkin(client_for(app, 2), pk=2).status_code == 201
    assert runner.invoke(args=['restore-db', str(target)]).exit_code != 0
    result = runner.invoke(args=['restore-db', str(target), '--confirm-stopped'])
    assert result.exit_code == 0, result.output
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 1
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 2
    assert list(tmp_path.glob('event.sqlite3.before-restore-*'))


def test_cli_admin_and_demo_guard(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=['create-admin', '--username', 'another-admin', '--password', 'admin-password-2026'])
    assert result.exit_code == 0, result.output
    assert runner.invoke(args=['seed-demo']).exit_code != 0
    assert runner.invoke(args=['reset-password', 'another-admin', '--password', 'new-admin-password']).exit_code == 0


def test_database_not_public(app):
    client = app.test_client()
    for path in ('/instance/checkin.sqlite3', '/backups/backup.sqlite3', '/static/event.sqlite3', '/.env'):
        assert client.get(path).status_code == 404
