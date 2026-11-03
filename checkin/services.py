import json
import re
import secrets
import time

from flask import abort, current_app

from .db import audit, bump_roster, get_db, now, transaction
from .event_preferences import get_preferences, seating_info

TOKEN_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,40}$')
FIELDS = ('attendee_id', 'name', 'affiliation', 'table_no', 'grade', 'title', 'email', 'seating_area')
EXTRA_FIELDS = ('grade', 'title', 'email', 'seating_area')
LIMITS = {'attendee_id': 64, 'name': 100, 'affiliation': 120, 'table_no': 30,
          'grade': 30, 'title': 80, 'email': 254, 'seating_area': 50}
ATTENDEE_QUERY = '''SELECT a.*, c.id AS checkin_id, c.checked_at, c.operator_name,
                   t.id AS ticket_id, t.token
                   FROM attendees a LEFT JOIN checkins c ON c.attendee_pk=a.id AND c.undone_at IS NULL
                   LEFT JOIN tickets t ON t.attendee_pk=a.id AND t.revoked_at IS NULL'''


def validate_attendee(data):
    values = {field: str(data.get(field, '')).strip() for field in FIELDS if field not in EXTRA_FIELDS or field in data}
    for field, value in values.items():
        if field in ('attendee_id', 'name') and not value:
            raise ValueError(f'{field} 為必填。')
        if len(value) > LIMITS[field] or any(ord(char) < 32 for char in value):
            raise ValueError(f'{field} 最多 {LIMITS[field]} 字，不可含控制字元。')
    if values.get('email') and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', values['email']):
        raise ValueError('email 請填寫完整電子郵件地址，或留白。')
    return values


def new_ticket(db, attendee_pk):
    token = secrets.token_urlsafe(32)
    db.execute('INSERT INTO tickets(attendee_pk,token,created_at) VALUES (?,?,?)', (attendee_pk, token, now()))
    return token


def insert_attendee(db, data):
    if db.execute('SELECT COUNT(*) FROM attendees').fetchone()[0] >= 200:
        abort(409, description='名冊最多 200 人，請先核對資料。')
    cursor = db.execute('INSERT INTO attendees(' + ','.join(FIELDS) + ',created_at) VALUES (' + ','.join('?' for _ in range(len(FIELDS) + 1)) + ')',
                        tuple(data.get(field, '') for field in FIELDS) + (now(),))
    new_ticket(db, cursor.lastrowid)
    return cursor.lastrowid


def ticket_url(token):
    # Fragments are not sent in HTTP requests, including reverse-proxy access logs.
    return current_app.config['PUBLIC_BASE_URL'] + '/ticket#' + token


def find_attendee(pk):
    row = get_db().execute(ATTENDEE_QUERY + ' WHERE a.id=?', (pk,)).fetchone()
    if not row:
        abort(404)
    return row


def find_ticket(token):
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        abort(404)
    row = get_db().execute(ATTENDEE_QUERY + ' WHERE t.token=?', (token,)).fetchone()
    if not row:
        abort(404, description='票券不存在或已補發失效，請向主辦方索取最新票券。')
    return row


def serialize_attendee(row, staff=False):
    value = {key: row[key] for key in ('name', 'affiliation', 'table_no', 'checked_at')}
    value['checked_in'] = row['checked_at'] is not None
    value['seating'] = seating_info(row)
    if staff:
        value.update(id=row['id'], attendee_id=row['attendee_id'], operator_name=row['operator_name'])
    return value


def update_attendee(db, attendee_pk, data):
    fields = [field for field in FIELDS if field in data]
    db.execute('UPDATE attendees SET ' + ','.join(field + '=?' for field in fields) + ' WHERE id=?',
               tuple(data[field] for field in fields) + (attendee_pk,))


def search_attendees(query='', status='all'):
    # Escape LIKE wildcards: searches are literal, parameterized, capped at 200.
    escaped = query[:100].replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    where = " WHERE (a.name LIKE ? ESCAPE '\\' OR a.attendee_id LIKE ? ESCAPE '\\' OR a.affiliation LIKE ? ESCAPE '\\')"
    if status == 'checked':
        where += ' AND c.id IS NOT NULL'
    elif status == 'pending':
        where += ' AND c.id IS NULL'
    return get_db().execute(ATTENDEE_QUERY + where + ' ORDER BY a.table_no,a.attendee_id LIMIT 200',
                            (f'%{escaped}%',) * 3).fetchall()


def check_in(actor, request_id, token=None, attendee_pk=None):
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id):
        abort(400, description='缺少操作識別碼，請重新核對來賓。')
    with transaction() as db:
        # Resolve inside the same write transaction as the insert: reissue cannot race this.
        row = find_ticket(token) if token is not None else find_attendee(attendee_pk)
        previous = db.execute('SELECT * FROM checkins WHERE request_id=?', (request_id,)).fetchone()
        if previous:
            if previous['attendee_pk'] != row['id'] or previous['operator_id'] != actor['id']:
                abort(409, description='操作識別碼重複，請重新核對來賓。')
            if previous['undone_at']:
                abort(409, description='這筆操作已被管理員撤回，請重新搜尋並核對後再報到。')
            return serialize_attendee(find_attendee(row['id']), staff=True), False
        if row['checked_at']:
            return serialize_attendee(row, staff=True), False
        db.execute('''INSERT INTO checkins(attendee_pk,ticket_id,checked_at,operator_id,operator_name,method,request_id)
                      VALUES (?,?,?,?,?,?,?)''',
                   (row['id'], row['ticket_id'], now(), actor['id'], actor['username'],
                    'scan' if token is not None else 'manual', request_id))
        audit(db, actor['id'], 'checkin', row['id'], 'scan' if token is not None else 'manual')
        result = serialize_attendee(find_attendee(row['id']), staff=True)
    return result, True


def parse_csv(raw, profile='auto', filename='roster.csv'):
    from .roster_import import read_roster
    items, errors = read_roster(raw, filename, profile)
    rows, seen = [], set()
    preferences = get_preferences()
    for number, item in items:
        try:
            is_campus = item.pop('_profile') == 'campus'
            if 'affiliation' not in item:
                item['affiliation'] = preferences['default_affiliation']
            row = validate_attendee(item)
            if is_campus and 'table_no' not in item:
                row.pop('table_no')
            if row['attendee_id'] in seen:
                raise ValueError('學號／attendee_id 在檔案內重複。')
            seen.add(row['attendee_id'])
            rows.append(row)
        except ValueError as error:
            errors.append(f'第 {number} 列：{error}')
    if not rows:
        errors.append('沒有可匯入的資料。')
    existing = {row['attendee_id'] for row in get_db().execute('SELECT attendee_id FROM attendees')}
    if len(existing | seen) > 200:
        errors.append('匯入後總人數超過 200 人上限。')
    return rows, errors


def preview_import(raw, user_id, profile='auto', filename='roster.csv'):
    rows, errors = parse_csv(raw, profile, filename)
    preview_id = secrets.token_urlsafe(24)
    with transaction() as db:
        version = db.execute("SELECT value FROM settings WHERE key='roster_version'").fetchone()[0]
        # Count and version are captured together. Apply rejects stale previews.
        existing = {row['attendee_id'] for row in db.execute('SELECT attendee_id FROM attendees')}
        db.execute('DELETE FROM import_previews WHERE created_at < ?', (int(time.time()) - 900,))
        db.execute('INSERT INTO import_previews VALUES (?,?,?,?,?,?,NULL)',
                   (preview_id, user_id, json.dumps(rows, ensure_ascii=False), json.dumps(errors, ensure_ascii=False),
                    version, int(time.time())))
    return dict(id=preview_id, rows=rows, errors=errors,
                added=sum(row['attendee_id'] not in existing for row in rows),
                updated=sum(row['attendee_id'] in existing for row in rows))


def apply_import(preview_id, actor_id):
    with transaction() as db:
        preview = db.execute('SELECT * FROM import_previews WHERE id=? AND user_id=?', (preview_id, actor_id)).fetchone()
        if not preview or preview['consumed_at'] or preview['created_at'] < time.time() - 900:
            abort(409, description='預覽已使用或逾時，請重新上傳。')
        if json.loads(preview['errors_json']):
            abort(400, description='CSV 有錯誤，請修正後重新預覽。')
        version = db.execute("SELECT value FROM settings WHERE key='roster_version'").fetchone()[0]
        if version != preview['roster_version']:
            abort(409, description='名冊在預覽後已變更，請重新上傳預覽。')
        rows = json.loads(preview['rows_json'])
        for row in rows:
            found = db.execute('SELECT id FROM attendees WHERE attendee_id=?', (row['attendee_id'],)).fetchone()
            if found:
                update_attendee(db, found['id'], {key: value for key, value in row.items() if key != 'attendee_id'})
            else:
                insert_attendee(db, row)
        db.execute('UPDATE import_previews SET consumed_at=? WHERE id=?', (now(), preview_id))
        bump_roster(db)
        audit(db, actor_id, 'csv_import', detail=f'匯入 {len(rows)} 筆；保留票券與報到紀錄')
    return len(rows)
