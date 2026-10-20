"""SQLite connections and explicit write transactions (one event, <= 200 guests)."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','staff')), active INTEGER NOT NULL DEFAULT 1,
 auth_version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendees (
 id INTEGER PRIMARY KEY, attendee_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 affiliation TEXT NOT NULL DEFAULT '', table_no TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
 id INTEGER PRIMARY KEY, attendee_pk INTEGER NOT NULL REFERENCES attendees(id),
 token TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_ticket ON tickets(attendee_pk) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS checkins (
 id INTEGER PRIMARY KEY, attendee_pk INTEGER NOT NULL REFERENCES attendees(id),
 ticket_id INTEGER REFERENCES tickets(id), checked_at TEXT NOT NULL,
 operator_id INTEGER NOT NULL REFERENCES users(id), operator_name TEXT NOT NULL,
 method TEXT NOT NULL CHECK(method IN ('scan','manual')),
 request_id TEXT UNIQUE NOT NULL, undone_at TEXT, undone_by INTEGER REFERENCES users(id), undo_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_checkin ON checkins(attendee_pk) WHERE undone_at IS NULL;
CREATE TABLE IF NOT EXISTS audit_log (
 id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, actor_id INTEGER REFERENCES users(id),
 action TEXT NOT NULL, attendee_pk INTEGER REFERENCES attendees(id), detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_previews (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), rows_json TEXT NOT NULL,
 errors_json TEXT NOT NULL, roster_version INTEGER NOT NULL, created_at INTEGER NOT NULL, consumed_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('roster_version', 0);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect(path):
    db = sqlite3.connect(str(path), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('PRAGMA synchronous=FULL')
    return db


def get_db():
    if 'db' not in g:
        g.db = connect(current_app.config['DATABASE'])
    return g.db


def init_db(app):
    db = connect(app.config['DATABASE'])
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
    finally:
        db.close()

    @app.teardown_appcontext
    def close_db(_error):
        db = g.pop('db', None)
        if db is not None:
            db.close()


@contextmanager
def transaction():
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise


def audit(db, actor, action, attendee=None, detail=''):
    db.execute('INSERT INTO audit_log(created_at,actor_id,action,attendee_pk,detail) VALUES (?,?,?,?,?)',
               (now(), actor, action, attendee, detail))


def bump_roster(db):
    db.execute("UPDATE settings SET value=value+1 WHERE key='roster_version'")
