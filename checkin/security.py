import hashlib
import hmac
import secrets
import time
from functools import wraps

from flask import abort, g, redirect, request, session, url_for
from werkzeug.security import generate_password_hash

from .db import get_db, transaction


def hash_password(password):
    # Explicit PBKDF2 works on macOS Python builds without OpenSSL scrypt.
    return generate_password_hash(password, method='pbkdf2:sha256:1000000')


def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']


def require_role(role=None):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not g.user:
                if request.path.startswith('/api/') or request.method != 'GET':
                    abort(401)
                return redirect(url_for('web.login'))
            if role and g.user['role'] != role:
                abort(403)
            return function(*args, **kwargs)
        return wrapped
    return decorate


def rate_limit(scope, limit, window=60):
    # Persistent and shared across Gunicorn workers; fixed windows, no tokens stored.
    key = hashlib.sha256(scope.encode()).hexdigest()
    moment = int(time.time())
    with transaction() as db:
        db.execute('DELETE FROM rate_limits WHERE expires < ?', (moment,))
        db.execute('''INSERT INTO rate_limits(key,count,expires) VALUES (?,1,?)
                   ON CONFLICT(key) DO UPDATE SET count=count+1''', (key, moment + window))
        count = db.execute('SELECT count FROM rate_limits WHERE key=?', (key,)).fetchone()['count']
    if count > limit:
        abort(429)


def install_security(app):
    app.jinja_env.globals['csrf_token'] = csrf_token

    @app.before_request
    def protect():
        g.user = None
        if session.get('user_id'):
            user = get_db().execute('SELECT * FROM users WHERE id=? AND active=1', (session['user_id'],)).fetchone()
            if user and user['auth_version'] == session.get('auth_version'):
                g.user = user
            else:
                session.clear()
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            provided = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            expected = session.get('csrf', '')
            if not expected or not hmac.compare_digest(str(provided), str(expected)):
                abort(403, description='安全驗證已過期，請重新整理頁面後再試。')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store, private, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if app.config['CHECKIN_ENV'] == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response
