import base64
import csv
import io
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import qrcode
from flask import (Blueprint, Response, abort, current_app, flash, g, jsonify,
                   redirect, render_template, request, send_file, session, url_for)
from werkzeug.security import check_password_hash

from .db import audit, bump_roster, get_db, now, transaction
from .security import csrf_token, hash_password, rate_limit, require_role
from .services import (ATTENDEE_QUERY, USERNAME_RE, apply_import, check_in, find_attendee,
                       find_ticket, insert_attendee, new_ticket, preview_import,
                       search_attendees, serialize_attendee, ticket_url, validate_attendee)

bp = Blueprint('web', __name__)


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def qr_png(token):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(ticket_url(token))
    qr.make(fit=True)
    stream = io.BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(stream, format='PNG')
    stream.seek(0)
    return stream


def csv_response(filename, headers, rows):
    def safe(value):
        value = '' if value is None else str(value)
        if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
            return "'" + value
        return value
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(headers)
    writer.writerows([safe(value) for value in row] for row in rows)
    return Response('\ufeff' + stream.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Taipei')).isoformat() if value else ''


@bp.get('/healthz')
def health():
    get_db().execute('SELECT 1').fetchone()
    return jsonify(status='ok')


@bp.get('/robots.txt')
def robots():
    return Response('User-agent: *\nDisallow: /\n', mimetype='text/plain')


@bp.get('/')
def index():
    return render_template('index.html')


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()[:40]
        password = request.form.get('password', '')
        rate_limit('login-ip:' + request.remote_addr, current_app.config['LOGIN_LIMIT'], 600)
        rate_limit('login-account:' + username.lower(), current_app.config['ACCOUNT_LOGIN_LIMIT'], 600)
        user = get_db().execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
        # Dummy work keeps missing-user timing comparable to a real password check.
        hashed = user['password_hash'] if user else current_app.config['DUMMY_PASSWORD_HASH']
        if len(password) <= 128 and check_password_hash(hashed, password) and user:
            session.clear()
            session.update(user_id=user['id'], auth_version=user['auth_version'])
            session.permanent = True
            csrf_token()
            return redirect(url_for('web.admin' if user['role'] == 'admin' else 'web.staff'))
        flash('帳號或密碼錯誤，或帳號已停用。', 'error')
    return render_template('login.html')


@bp.post('/logout')
@require_role()
def logout():
    session.clear()
    return redirect(url_for('web.login'))


@bp.get('/ticket')
def ticket():
    return render_template('ticket.html')


@bp.post('/api/ticket')
def ticket_data():
    rate_limit('ticket:' + request.remote_addr, current_app.config['TICKET_LIMIT'])
    row = find_ticket(body().get('token'))
    data = serialize_attendee(row)
    data.update(event_name=current_app.config['EVENT_NAME'], qr='data:image/png;base64,' + base64.b64encode(qr_png(row['token']).getvalue()).decode())
    return jsonify(data)


@bp.get('/staff')
@require_role()
def staff():
    return render_template('staff.html', ticket_base_url=current_app.config['PUBLIC_BASE_URL'])


@bp.post('/api/staff/lookup')
@require_role()
def staff_lookup():
    rate_limit('staff-lookup:' + str(g.user['id']), 300)
    return jsonify(serialize_attendee(find_ticket(body().get('token')), staff=True))


@bp.get('/api/staff/search')
@require_role()
def staff_search():
    rate_limit('staff-search:' + str(g.user['id']), 120)
    query = request.args.get('q', '').strip()
    if not query or len(query) > 100:
        abort(400, description='請輸入姓名或參加者編號（最多 100 字）。')
    return jsonify(results=[serialize_attendee(row, staff=True) for row in search_attendees(query)])


@bp.post('/api/staff/checkin')
@require_role()
def staff_checkin():
    data = body()
    token = data.get('token')
    attendee_pk = data.get('attendee_pk')
    if (token is None) == (attendee_pk is None):
        abort(400)
    if attendee_pk is not None and (type(attendee_pk) is not int or attendee_pk < 1):
        abort(400)
    attendee, created = check_in(g.user, data.get('request_id'), token=token, attendee_pk=attendee_pk)
    return jsonify(attendee=attendee, created=created, message='報到成功' if created else '已報到'), 201 if created else 200


@bp.get('/admin')
@require_role('admin')
def admin():
    query = request.args.get('q', '').strip()
    status = request.args.get('status', 'all')
    rows = search_attendees(query, status)
    stats = get_db().execute('''SELECT COUNT(*) AS total, COUNT(c.id) AS checked
                               FROM attendees a LEFT JOIN checkins c ON c.attendee_pk=a.id AND c.undone_at IS NULL''').fetchone()
    tables = get_db().execute('''SELECT a.table_no, COUNT(*) AS total, COUNT(c.id) AS checked
                                FROM attendees a LEFT JOIN checkins c ON c.attendee_pk=a.id AND c.undone_at IS NULL
                                GROUP BY a.table_no ORDER BY a.table_no''').fetchall()
    return render_template('admin.html', rows=rows, stats=stats, tables=tables, query=query, status=status)


@bp.route('/admin/import', methods=['GET', 'POST'])
@require_role('admin')
def import_csv():
    preview = None
    if request.method == 'POST':
        uploaded = request.files.get('file')
        if not uploaded:
            abort(400, description='請選擇 UTF-8 CSV 檔案。')
        preview = preview_import(uploaded.read(), g.user['id'])
    return render_template('import.html', preview=preview)


@bp.post('/admin/import/confirm')
@require_role('admin')
def import_confirm():
    count = apply_import(request.form.get('preview_id', ''), g.user['id'])
    flash(f'已匯入 {count} 筆資料，既有票券與報到紀錄已保留。', 'success')
    return redirect(url_for('web.admin'))


@bp.route('/admin/attendees/new', methods=['GET', 'POST'])
@require_role('admin')
def attendee_new():
    if request.method == 'POST':
        try:
            data = validate_attendee(request.form)
            with transaction() as db:
                pk = insert_attendee(db, data)
                bump_roster(db)
                audit(db, g.user['id'], 'attendee_create', pk, '新增臨時來賓')
            flash('已建立來賓及獨立票券。', 'success')
            return redirect(url_for('web.attendee_detail', pk=pk))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else '此參加者編號已存在。', 'error')
    return render_template('attendee_form.html', attendee=request.form, creating=True)


@bp.route('/admin/attendees/<int:pk>', methods=['GET', 'POST'])
@require_role('admin')
def attendee_detail(pk):
    attendee = find_attendee(pk)
    if request.method == 'POST':
        try:
            data = validate_attendee(request.form)
            with transaction() as db:
                before = find_attendee(pk)
                db.execute('UPDATE attendees SET attendee_id=?,name=?,affiliation=?,table_no=? WHERE id=?',
                           tuple(data[field] for field in ('attendee_id', 'name', 'affiliation', 'table_no')) + (pk,))
                bump_roster(db)
                # No credentials in audit logs. Preserve old/new roster fields for corrections.
                import json
                audit(db, g.user['id'], 'attendee_edit', pk,
                      json.dumps({'before': {field: before[field] for field in data}, 'after': data}, ensure_ascii=False))
            flash('來賓資料已更新，票券與報到紀錄保留。', 'success')
            return redirect(url_for('web.attendee_detail', pk=pk))
        except (ValueError, sqlite3.IntegrityError) as error:
            flash(str(error) if isinstance(error, ValueError) else '此參加者編號已存在。', 'error')
    history = get_db().execute('''SELECT c.*, u.username AS undo_operator FROM checkins c LEFT JOIN users u ON u.id=c.undone_by
                                 WHERE c.attendee_pk=? ORDER BY c.id DESC''', (pk,)).fetchall()
    return render_template('attendee_form.html', attendee=attendee, creating=False, history=history, link=ticket_url(attendee['token']))


def confirm_admin():
    reason = request.form.get('reason', '').strip()
    if request.form.get('confirm') != 'yes' or not 3 <= len(reason) <= 300:
        abort(400, description='請填寫 3–300 字原因，並勾選確認。')
    return reason


@bp.post('/admin/attendees/<int:pk>/undo')
@require_role('admin')
def undo(pk):
    reason = confirm_admin()
    with transaction() as db:
        row = find_attendee(pk)
        if not row['checkin_id'] or str(row['checkin_id']) != request.form.get('checkin_id'):
            abort(409, description='報到紀錄已變更，請重新整理並核對。')
        db.execute('UPDATE checkins SET undone_at=?,undone_by=?,undo_reason=? WHERE id=?',
                   (now(), g.user['id'], reason, row['checkin_id']))
        audit(db, g.user['id'], 'checkin_undo', pk, f"checkin_id={row['checkin_id']}; {reason}")
    flash('已撤回報到，歷史紀錄仍保留。', 'success')
    return redirect(url_for('web.attendee_detail', pk=pk))


@bp.post('/admin/attendees/<int:pk>/reissue')
@require_role('admin')
def reissue(pk):
    reason = confirm_admin()
    with transaction() as db:
        row = find_attendee(pk)
        if str(row['ticket_id']) != request.form.get('ticket_id'):
            abort(409, description='票券已補發，請重新整理後使用最新票券。')
        db.execute('UPDATE tickets SET revoked_at=? WHERE id=?', (now(), row['ticket_id']))
        new_ticket(db, pk)
        audit(db, g.user['id'], 'ticket_reissue', pk, f"revoked_ticket_id={row['ticket_id']}; {reason}")
    flash('已補發；舊票券立即失效。請將新連結發送給本人。報到狀態及歷史保留。', 'success')
    return redirect(url_for('web.attendee_detail', pk=pk))


@bp.post('/admin/attendees/<int:pk>/qr')
@require_role('admin')
def download_qr(pk):
    row = find_attendee(pk)
    with transaction() as db:
        audit(db, g.user['id'], 'qr_download', pk)
    return send_file(qr_png(row['token']), mimetype='image/png', as_attachment=True, download_name=f'qr-{pk}.png')


@bp.post('/admin/export/<kind>')
@require_role('admin')
def export(kind):
    if kind not in ('tickets', 'attendance', 'tables', 'history', 'audit'):
        abort(404)
    # Snapshot report and its audit entry in one transaction; never write exports to disk.
    with transaction() as db:
        if kind in ('tickets', 'attendance'):
            attendees = db.execute(ATTENDEE_QUERY + ' ORDER BY a.attendee_id').fetchall()
            headers = ['attendee_id', 'name', 'affiliation', 'table_no']
            rows = [[row[key] for key in headers] for row in attendees]
            if kind == 'tickets':
                headers += ['ticket_url']
                for values, row in zip(rows, attendees):
                    values.append(ticket_url(row['token']))
            else:
                headers += ['status', 'checked_at_Asia_Taipei', 'operator']
                for values, row in zip(rows, attendees):
                    values.extend(['已報到' if row['checked_at'] else '未報到', local_time(row['checked_at']), row['operator_name']])
        elif kind == 'tables':
            headers = ['table_no', 'total', 'checked_in', 'pending']
            found = db.execute('''SELECT a.table_no, COUNT(*) AS total, COUNT(c.id) AS checked
                                  FROM attendees a LEFT JOIN checkins c ON c.attendee_pk=a.id AND c.undone_at IS NULL
                                  GROUP BY a.table_no ORDER BY a.table_no''').fetchall()
            rows = [[row['table_no'], row['total'], row['checked'], row['total'] - row['checked']] for row in found]
        elif kind == 'history':
            headers = ['checkin_id', 'attendee_id', 'name', 'checked_at_Asia_Taipei', 'operator', 'method',
                       'undone_at_Asia_Taipei', 'undone_by', 'undo_reason']
            found = db.execute('''SELECT c.*, a.attendee_id, a.name, u.username AS undo_operator FROM checkins c
                                  JOIN attendees a ON a.id=c.attendee_pk LEFT JOIN users u ON u.id=c.undone_by ORDER BY c.id''').fetchall()
            rows = [[row['id'], row['attendee_id'], row['name'], local_time(row['checked_at']), row['operator_name'],
                     row['method'], local_time(row['undone_at']), row['undo_operator'], row['undo_reason']] for row in found]
        else:
            headers = ['audit_id', 'time_Asia_Taipei', 'actor', 'action', 'attendee_id', 'detail']
            found = db.execute('''SELECT l.*, u.username, a.attendee_id FROM audit_log l LEFT JOIN users u ON u.id=l.actor_id
                                  LEFT JOIN attendees a ON a.id=l.attendee_pk ORDER BY l.id''').fetchall()
            rows = [[row['id'], local_time(row['created_at']), row['username'], row['action'], row['attendee_id'], row['detail']] for row in found]
        audit(db, g.user['id'], f'export_{kind}', detail=f'{len(rows)} 筆')
    return csv_response(f'{kind}.csv', headers, rows)


@bp.route('/admin/users', methods=['GET', 'POST'])
@require_role('admin')
def users():
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if action in ('create', 'reset'):
            if not USERNAME_RE.fullmatch(username) or not 12 <= len(password) <= 128:
                abort(400, description='帳號需 3–40 位英數或 _.-；密碼需 12–128 字。')
            password_hash = hash_password(password)
        try:
            with transaction() as db:
                if action == 'create':
                    cursor = db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                                        (username, password_hash, 'staff', now()))
                    target_id = cursor.lastrowid
                elif action in ('reset', 'disable', 'enable'):
                    target = db.execute("SELECT * FROM users WHERE username=? AND role='staff'", (username,)).fetchone()
                    if not target:
                        abort(404)
                    target_id = target['id']
                    if action == 'reset':
                        db.execute('UPDATE users SET password_hash=?,auth_version=auth_version+1 WHERE id=?', (password_hash, target_id))
                    else:
                        db.execute('UPDATE users SET active=?,auth_version=auth_version+1 WHERE id=?', (action == 'enable', target_id))
                else:
                    abort(400)
                audit(db, g.user['id'], 'user_' + action, detail=f'user_id={target_id}; username={username}')
            flash('帳號已更新。停用與重設密碼會立即撤銷該帳號既有登入。', 'success')
        except sqlite3.IntegrityError:
            flash('此帳號已存在。', 'error')
        return redirect(url_for('web.users'))
    return render_template('users.html', users=get_db().execute('SELECT id,username,role,active FROM users ORDER BY id').fetchall())
