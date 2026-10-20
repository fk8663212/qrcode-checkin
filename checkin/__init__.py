import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, SecurityError
from werkzeug.middleware.proxy_fix import ProxyFix

from .db import init_db


def create_app(test_config=None):
    root = Path(__file__).resolve().parent.parent
    if test_config is None:
        load_dotenv(root / '.env')
    app = Flask(__name__, instance_path=str(root / 'instance'))
    mode = os.getenv('CHECKIN_ENV', 'development')
    instance = Path(app.instance_path)
    instance.mkdir(mode=0o700, parents=True, exist_ok=True)
    app.config.update(
        CHECKIN_ENV=mode,
        DATABASE=os.getenv('DATABASE_PATH', str(instance / ('demo.sqlite3' if mode == 'demo' else 'checkin.sqlite3'))),
        SECRET_KEY=os.getenv('SECRET_KEY'), EVENT_NAME=os.getenv('EVENT_NAME', '導生晚宴'),
        PUBLIC_BASE_URL=os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:5000').rstrip('/'),
        SESSION_COOKIE_NAME='dinner_checkin_demo' if mode == 'demo' else 'dinner_checkin', SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=mode == 'production',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=10), SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=1024 * 1024, MAX_FORM_MEMORY_SIZE=1024 * 1024,
        LOGIN_LIMIT=30, ACCOUNT_LOGIN_LIMIT=10, TICKET_LIMIT=300,
    )
    if test_config:
        app.config.update(test_config)
    base = urlsplit(app.config['PUBLIC_BASE_URL'])
    if (base.scheme not in ('http', 'https') or not base.netloc or base.path not in ('', '/')
            or base.query or base.fragment or base.username or base.password):
        raise RuntimeError('PUBLIC_BASE_URL 必須是獨立網域根網址，不可含路徑、帳密、查詢或片段。')
    if app.config['CHECKIN_ENV'] == 'production':
        if base.scheme != 'https' or not app.config['SECRET_KEY'] or len(app.config['SECRET_KEY']) < 32:
            raise RuntimeError('正式環境需要 HTTPS PUBLIC_BASE_URL 與至少 32 字元 SECRET_KEY。')
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['TRUSTED_HOSTS'] = [base.hostname]
    if not app.config['SECRET_KEY']:
        key_path = instance / ('.demo-session-key' if app.config['CHECKIN_ENV'] == 'demo' else '.session-key')
        try:
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as file:
                file.write(secrets.token_hex(32))
        app.config['SECRET_KEY'] = key_path.read_text().strip()
    db_path = Path(app.config['DATABASE']).expanduser().resolve()
    if db_path.is_relative_to(root / 'checkin' / 'static'):
        raise RuntimeError('資料庫不可放在公開靜態目錄。')
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    app.config['DATABASE'] = str(db_path)
    from .security import hash_password
    app.config['DUMMY_PASSWORD_HASH'] = hash_password(secrets.token_urlsafe(24))
    # Only enable when a single trusted proxy is the sole network entry point.
    if os.getenv('TRUST_PROXY', '0') == '1':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    init_db(app)
    os.chmod(db_path, 0o600)

    from .security import install_security
    from .views import bp
    from .cli import register_cli
    install_security(app)
    app.register_blueprint(bp)
    register_cli(app)

    @app.context_processor
    def shared():
        return {'event_name': app.config['EVENT_NAME'], 'is_demo': app.config['CHECKIN_ENV'] == 'demo'}

    @app.template_filter('taipei')
    def taipei(value):
        if not value:
            return '—'
        return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d %H:%M:%S')

    @app.errorhandler(HTTPException)
    def http_error(error):
        if isinstance(error, SecurityError):
            return '網域不正確，請使用主辦方提供的正式網址。', 400
        messages = {400: '資料格式錯誤，請重新確認。', 401: '請先登入工作人員帳號。',
                    403: '權限不足或驗證已過期，請重新整理後再試。',
                    404: '查無資料，票券可能已失效。請洽現場工作人員。',
                    405: '此操作方式不受支援。', 409: '資料已變更，請重新整理後再試。',
                    413: '檔案過大，請使用 1 MB 以下的 UTF-8 CSV。',
                    429: '操作過於頻繁，請稍候再試。'}
        message = error.description if error.description != error.__class__.description else messages.get(error.code, '無法完成操作。')
        if request.path.startswith('/api/'):
            response = jsonify(error=message)
        else:
            response = app.make_response(render_template('error.html', message=message, code=error.code))
        response.status_code = error.code
        if error.code == 429:
            response.headers['Retry-After'] = '60'
        return response

    @app.errorhandler(sqlite3.OperationalError)
    def database_busy(_error):
        # Never log request bodies, ticket credentials, SQL parameters or form data.
        app.logger.error('資料庫操作失敗；請檢查磁碟空間與資料卷權限。')
        message = '伺服器尚未確認寫入，請稍後安全重試，或聯絡管理員。'
        if request.path.startswith('/api/'):
            return jsonify(error=message), 503
        return render_template('error.html', message=message, code=503), 503

    return app
