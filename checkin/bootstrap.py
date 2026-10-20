"""Create the explicitly configured first administrator, only in an empty user store."""
import re

from .db import audit, now, transaction
from .services import USERNAME_RE

INITIAL_HASH_RE = re.compile(r'pbkdf2:sha256:1000000\$[A-Za-z0-9]{16,64}\$[a-f0-9]{64}')


def initialize_admin(app):
    username = app.config.get('INITIAL_ADMIN_USERNAME', '')
    password_hash = app.config.get('INITIAL_ADMIN_PASSWORD_HASH', '')
    if app.config['CHECKIN_ENV'] == 'demo' or not (username or password_hash):
        return
    with app.app_context(), transaction() as db:
        # Recheck under the same write lock as INSERT, including multiple workers.
        # Existing accounts always win over deployment bootstrap settings.
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            return
        if not isinstance(username, str) or not USERNAME_RE.fullmatch(username):
            raise RuntimeError('初始管理員帳號需 3–40 位英數或 _.-；請重新執行事前設定。')
        if not isinstance(password_hash, str) or not INITIAL_HASH_RE.fullmatch(password_hash):
            raise RuntimeError('初始管理員密碼設定不是支援的雜湊格式；請使用事前設定工具，勿填入明文密碼。')
        cursor = db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                            (username, password_hash, 'admin', now()))
        audit(db, cursor.lastrowid, 'admin_initialize', detail='首次部署建立管理員；未記錄密碼或雜湊')
