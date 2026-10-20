from concurrent.futures import ThreadPoolExecutor

import pytest
from click.testing import CliRunner
from dotenv import dotenv_values
from werkzeug.security import check_password_hash

from checkin import create_app
from checkin.db import get_db
from checkin.security import hash_password
from checkin.setup_event import main
from conftest import client_for, perform_checkin, post, ticket

SETUP_PASSWORD = 'fixture-setup-password-2026!'


def setup_input(target='local'):
    values = ['校友交流晚宴', '2026-11-20 18:00', '第一會館', '校友會', 'host@example.org', '請攜帶個人票券']
    values += ['checkin.example.org', 'host@example.org'] if target == 'docker' else ['http://127.0.0.1:5000']
    values += ['organizer', SETUP_PASSWORD, SETUP_PASSWORD]
    return '\n'.join(values) + '\n'


def app_config(path, **overrides):
    return dict(TESTING=True, CHECKIN_ENV='test', DATABASE=str(path), SECRET_KEY='x' * 64,
                PUBLIC_BASE_URL='http://localhost', SESSION_COOKIE_SECURE=False, **overrides)


def test_interactive_setup_saves_hash_and_private_config_without_touching_db(tmp_path):
    output = tmp_path / '.env'
    result = CliRunner().invoke(main, ['--target', 'local', '--output', str(output)], input=setup_input())
    assert result.exit_code == 0, result.output
    values = dotenv_values(output, interpolate=False)
    assert values['EVENT_NAME'] == '校友交流晚宴'
    assert values['EVENT_STARTS_AT'] == '2026-11-20T18:00+08:00'
    assert values['EVENT_LOCATION'] == '第一會館'
    assert values['INITIAL_ADMIN_USERNAME'] == 'organizer'
    assert check_password_hash(values['INITIAL_ADMIN_PASSWORD_HASH'], SETUP_PASSWORD)
    assert len(values['SECRET_KEY']) == 64
    assert SETUP_PASSWORD not in output.read_text() and SETUP_PASSWORD not in result.output
    assert values['INITIAL_ADMIN_PASSWORD_HASH'] not in result.output
    assert values['SECRET_KEY'] not in result.output
    assert output.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('*.sqlite3'))


def test_setup_config_initializes_admin_and_renders_event_information(tmp_path):
    output = tmp_path / '.env'
    result = CliRunner().invoke(main, ['--target', 'local', '--output', str(output)], input=setup_input())
    assert result.exit_code == 0
    values = dict(dotenv_values(output, interpolate=False))
    values.update(TESTING=True, DATABASE=str(tmp_path / 'event.sqlite3'))
    app = create_app(values)
    anonymous = client_for(app)
    login = post(anonymous, '/login', data={'username': 'organizer', 'password': SETUP_PASSWORD})
    assert login.status_code == 302
    assert anonymous.get('/admin').status_code == 200
    for path in ('/', '/ticket'):
        page = app.test_client().get(path).text
        assert '校友交流晚宴' in page and '第一會館' in page and 'host@example.org' in page
        assert '2026/11/20 18:00:00' in page and '請攜帶個人票券' in page
        assert values['INITIAL_ADMIN_PASSWORD_HASH'] not in page and values['SECRET_KEY'] not in page
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 0
        audit = get_db().execute('SELECT * FROM audit_log').fetchone()
        assert audit['action'] == 'admin_initialize'
        assert SETUP_PASSWORD not in audit['detail'] and values['INITIAL_ADMIN_PASSWORD_HASH'] not in audit['detail']


def test_existing_config_is_never_overwritten(tmp_path):
    output = tmp_path / '.env'
    output.write_text('existing=keep\n')
    result = CliRunner().invoke(main, ['--output', str(output)])
    assert result.exit_code != 0
    assert output.read_text() == 'existing=keep\n'
    assert '已存在' in result.output


def test_password_is_masked_and_short_password_reprompts(tmp_path):
    values = setup_input().splitlines()
    values[-2:] = ['short', 'short', SETUP_PASSWORD, SETUP_PASSWORD]
    output = tmp_path / '.env'
    result = CliRunner().invoke(main, ['--target', 'local', '--output', str(output)], input='\n'.join(values) + '\n')
    assert result.exit_code == 0, result.output
    assert '密碼需 12–128 字' in result.output
    assert SETUP_PASSWORD not in result.output and 'short' not in result.output
    assert check_password_hash(dotenv_values(output)['INITIAL_ADMIN_PASSWORD_HASH'], SETUP_PASSWORD)


def test_docker_setup_configuration(tmp_path):
    output = tmp_path / 'production.env'
    result = CliRunner().invoke(main, ['--target', 'docker', '--output', str(output)], input=setup_input('docker'))
    assert result.exit_code == 0, result.output
    values = dotenv_values(output, interpolate=False)
    assert values['CHECKIN_ENV'] == 'production' and values['TRUST_PROXY'] == '1'
    assert values['PUBLIC_BASE_URL'] == 'https://checkin.example.org'
    assert values['DOMAIN'] == 'checkin.example.org'
    assert values['ACME_EMAIL'] == 'host@example.org'
    assert values['DATABASE_PATH'] == '/data/checkin.sqlite3'
    assert check_password_hash(values['INITIAL_ADMIN_PASSWORD_HASH'], SETUP_PASSWORD)


def test_initialization_does_not_reset_existing_accounts_or_attendance(app):
    original_token = ticket(app)
    perform_checkin(client_for(app, 2), token=original_token)
    with app.app_context():
        original_hash = get_db().execute('SELECT password_hash FROM users WHERE id=1').fetchone()[0]
        original_checkin = dict(get_db().execute('SELECT * FROM checkins').fetchone())
    config = dict(app.config, INITIAL_ADMIN_USERNAME='another-owner', INITIAL_ADMIN_PASSWORD_HASH=hash_password(SETUP_PASSWORD))
    restarted = create_app(config)
    with restarted.app_context():
        db = get_db()
        assert db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 3
        assert db.execute('SELECT password_hash FROM users WHERE id=1').fetchone()[0] == original_hash
        assert dict(db.execute('SELECT * FROM checkins').fetchone()) == original_checkin
        assert db.execute("SELECT COUNT(*) FROM users WHERE username='another-owner'").fetchone()[0] == 0
    assert ticket(restarted) == original_token


def test_password_reset_survives_restart_with_original_initial_hash(tmp_path):
    config = app_config(tmp_path / 'event.sqlite3', INITIAL_ADMIN_USERNAME='owner', INITIAL_ADMIN_PASSWORD_HASH=hash_password(SETUP_PASSWORD))
    app = create_app(config)
    reset = app.test_cli_runner().invoke(args=['reset-password', 'owner', '--password', 'changed-password-2026!'])
    assert reset.exit_code == 0
    restarted = create_app(config)
    client = client_for(restarted)
    assert post(client, '/login', data={'username': 'owner', 'password': SETUP_PASSWORD}).status_code == 200
    assert post(client, '/login', data={'username': 'owner', 'password': 'changed-password-2026!'}).status_code == 302
    with restarted.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_parallel_first_start_creates_one_admin(tmp_path):
    path = tmp_path / 'parallel.sqlite3'
    config = app_config(path, INITIAL_ADMIN_USERNAME='owner', INITIAL_ADMIN_PASSWORD_HASH=hash_password(SETUP_PASSWORD))
    with ThreadPoolExecutor(max_workers=4) as pool:
        apps = list(pool.map(lambda _: create_app(config), range(4)))
    with apps[0].app_context():
        assert get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1
        assert get_db().execute("SELECT COUNT(*) FROM audit_log WHERE action='admin_initialize'").fetchone()[0] == 1


@pytest.mark.parametrize('username,password_hash', [('owner', ''), ('', 'invalid'), ('owner', 'plaintext-password-do-not-save')])
def test_invalid_initial_credentials_are_rejected_without_leaking(tmp_path, username, password_hash):
    with pytest.raises(RuntimeError) as error:
        create_app(app_config(tmp_path / 'bad.sqlite3', INITIAL_ADMIN_USERNAME=username, INITIAL_ADMIN_PASSWORD_HASH=password_hash))
    if password_hash:
        assert password_hash not in str(error.value)


def test_demo_ignores_initial_admin_settings(tmp_path):
    config = app_config(tmp_path / 'demo.sqlite3', INITIAL_ADMIN_USERNAME='owner', INITIAL_ADMIN_PASSWORD_HASH=hash_password(SETUP_PASSWORD))
    config['CHECKIN_ENV'] = 'demo'
    app = create_app(config)
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0


def test_optional_event_fields_are_escaped_and_credentials_are_not_public(tmp_path):
    app = create_app(app_config(tmp_path / 'event.sqlite3', EVENT_LOCATION='<script>alert(1)</script>', EVENT_NOTICE='請核對票券'))
    for path in ('/', '/ticket'):
        response = app.test_client().get(path)
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in response.text
        assert '<script>alert(1)</script>' not in response.text
        assert '請核對票券' in response.text


@pytest.mark.parametrize('field,value', [('EVENT_NAME', ''), ('EVENT_LOCATION', 'x' * 201),
                                       ('EVENT_CONTACT', 'line\nbreak'), ('EVENT_STARTS_AT', 'not-a-date'),
                                       ('EVENT_STARTS_AT', '2026-10-20'), ('PUBLIC_BASE_URL', 'http://localhost:bad')])
def test_bad_event_settings_fail_early(tmp_path, field, value):
    config = app_config(tmp_path / 'event.sqlite3')
    config[field] = value
    with pytest.raises(RuntimeError):
        create_app(config)


def test_configuration_preserves_quotes_backslashes_and_literal_variables(tmp_path):
    output = tmp_path / '.env'
    values = setup_input().splitlines()
    values[2] = "O'Brien\\館 ${HOME} # 1"
    result = CliRunner().invoke(main, ['--target', 'local', '--output', str(output)], input='\n'.join(values) + '\n')
    assert result.exit_code == 0, result.output
    parsed = dotenv_values(output, interpolate=False)
    assert parsed['EVENT_LOCATION'] == values[2]


def test_event_setup_does_not_add_public_configuration_endpoint(app):
    for path in ('/setup', '/setup-event', '/api/setup'):
        assert app.test_client().get(path).status_code == 404
