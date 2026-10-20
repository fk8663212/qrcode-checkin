import secrets

import pytest

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee

PASSWORD = 'test-password-2026!'
HASH = hash_password(PASSWORD)


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-secret-32-characters-minimum-0000',
                      'DATABASE': str(tmp_path / 'event.sqlite3'), 'CHECKIN_ENV': 'test',
                      'PUBLIC_BASE_URL': 'http://localhost', 'SESSION_COOKIE_SECURE': False})
    with app.app_context(), transaction() as db:
        for username, role in [('admin', 'admin'), ('staff1', 'staff'), ('staff2', 'staff')]:
            db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                       (username, HASH, role, now()))
        for attendee_id in ('A001', 'A002'):
            insert_attendee(db, {'attendee_id': attendee_id, 'name': '同名來賓', 'affiliation': '資訊學系', 'table_no': '01'})
    return app


def client_for(app, user_id=None):
    client = app.test_client()
    with client.session_transaction() as session:
        session['csrf'] = secrets.token_urlsafe(32)
        if user_id:
            session.update(user_id=user_id, auth_version=1)
    return client


def post(client, path, *, json=None, data=None):
    with client.session_transaction() as session:
        csrf = session['csrf']
    return client.post(path, json=json, data=data, headers={'X-CSRF-Token': csrf})


def ticket(app, pk=1):
    with app.app_context():
        return get_db().execute('SELECT token FROM tickets WHERE attendee_pk=? AND revoked_at IS NULL', (pk,)).fetchone()[0]


def perform_checkin(client, token=None, pk=None, request_id=None):
    data = {'request_id': request_id or secrets.token_hex(24)}
    data.update({'token': token} if token is not None else {'attendee_pk': pk or 1})
    return post(client, '/api/staff/checkin', json=data)
