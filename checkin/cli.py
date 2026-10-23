import csv
import os
import secrets
import sqlite3
from pathlib import Path

import click

from .db import audit, bump_roster, connect, now, transaction
from .security import hash_password
from .services import USERNAME_RE, insert_attendee, validate_attendee


def password_valid(password):
    if not 12 <= len(password) <= 128:
        raise click.ClickException('密碼需 12–128 字。')


def safe_private_path(app, path):
    path = Path(path).expanduser().resolve()
    static = Path(app.static_folder).resolve()
    if path.is_relative_to(static) or path == Path(app.config['DATABASE']):
        raise click.ClickException('備份路徑不可等同資料庫或位於公開 static 目錄。')
    return path


def backup(source, target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        destination = connect(target)
        try:
            source.backup(destination)
            if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise click.ClickException('備份一致性驗證失敗。')
        finally:
            destination.close()
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def register_cli(app):
    @app.cli.command('create-admin')
    @click.option('--username', prompt='管理員帳號')
    @click.option('--password', prompt='管理員密碼', hide_input=True, confirmation_prompt=True)
    def create_admin(username, password):
        """Create an administrator without default production credentials."""
        if not USERNAME_RE.fullmatch(username):
            raise click.ClickException('帳號需 3–40 位英數或 _.-。')
        password_valid(password)
        try:
            with transaction() as db:
                cursor = db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                                    (username, hash_password(password), 'admin', now()))
                audit(db, cursor.lastrowid, 'admin_create', detail=f'CLI; username={username}')
        except sqlite3.IntegrityError:
            raise click.ClickException('帳號已存在，請使用 reset-password。')
        click.echo('管理員已建立。')

    @app.cli.command('reset-password')
    @click.argument('username')
    @click.option('--password', prompt='新密碼', hide_input=True, confirmation_prompt=True)
    def reset_password(username, password):
        """Emergency local password reset, invalidating existing sessions."""
        password_valid(password)
        with transaction() as db:
            user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if not user:
                raise click.ClickException('帳號不存在。')
            db.execute('UPDATE users SET password_hash=?,auth_version=auth_version+1 WHERE id=?',
                       (hash_password(password), user['id']))
            audit(db, None, 'password_reset_cli', detail=f'user_id={user["id"]}')
        click.echo('密碼已更新；舊登入已失效。')

    @app.cli.command('seed-demo')
    def seed_demo():
        """Create 20 fake guests in the separate demo database; never modifies existing data."""
        expected = Path(app.instance_path) / 'demo.sqlite3'
        if app.config['CHECKIN_ENV'] != 'demo' or Path(app.config['DATABASE']) != expected:
            raise click.ClickException('僅允許 CHECKIN_ENV=demo 且使用預設 instance/demo.sqlite3。請移除 DATABASE_PATH。')
        with transaction() as db:
            if db.execute('SELECT COUNT(*) FROM attendees').fetchone()[0] or db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                raise click.ClickException('示範資料庫已有資料；不會覆寫。')
            credentials = [(name, secrets.token_urlsafe(12), role) for name, role in (('demo-admin', 'admin'), ('demo-staff', 'staff'))]
            for username, password, role in credentials:
                db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                           (username, hash_password(password), role, now()))
            with (Path(app.root_path).parent / 'examples' / 'attendees.csv').open(encoding='utf-8-sig', newline='') as file:
                for row in csv.DictReader(file):
                    insert_attendee(db, validate_attendee(row))
            bump_roster(db)
            audit(db, None, 'demo_seed', detail='20 位虛構來賓')
        click.echo('示範資料庫：instance/demo.sqlite3（與正式資料完全分開）')
        for username, password, _role in credentials:
            click.echo(f'{username} / {password}')
        click.echo('請保存這次產生的示範密碼；忘記時可用 reset-password。')

    @app.cli.command('backup-db')
    @click.argument('output', type=click.Path())
    def backup_db(output):
        """Consistent online SQLite backup; destination must not exist."""
        target = safe_private_path(app, output)
        source = connect(app.config['DATABASE'])
        try:
            backup(source, target)
        except FileExistsError:
            raise click.ClickException('目的檔案已存在，請換新檔名。')
        finally:
            source.close()
        click.echo(f'一致性備份完成（integrity_check=ok）：{target}')

    @app.cli.command('restore-db')
    @click.argument('source_path', type=click.Path(exists=True, dir_okay=False))
    @click.option('--confirm-stopped', is_flag=True, help='確認已停止所有 web/worker；還原會覆寫目前資料。')
    def restore_db(source_path, confirm_stopped):
        """Restore only while all application workers are stopped; saves current database first."""
        if not confirm_stopped:
            raise click.ClickException('請先停止所有 web/worker，再使用 --confirm-stopped。')
        source_path = safe_private_path(app, source_path)
        source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
        destination = None
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise click.ClickException('來源資料庫完整性驗證失敗。')
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'attendees', 'tickets', 'checkins', 'users', 'audit_log', 'settings', 'import_previews', 'rate_limits'}.issubset(tables):
                raise click.ClickException('來源不是完整的本系統資料庫。')
            bingo_tables = {'bingo_games', 'bingo_cards', 'bingo_draws', 'bingo_winners'}
            if tables & bingo_tables and not bingo_tables.issubset(tables):
                raise click.ClickException('來源的賓果資料表不完整，請改用完整的一致性備份。')
            raffle_tables = {'raffle_rounds', 'raffle_prizes', 'raffle_draws', 'raffle_winners'}
            if tables & raffle_tables and not raffle_tables.issubset(tables):
                raise click.ClickException('來源的抽獎資料表不完整，請改用完整的一致性備份。')
            if source.execute('PRAGMA foreign_key_check').fetchone():
                raise click.ClickException('來源資料庫關聯驗證失敗。')
            destination = connect(app.config['DATABASE'])
            rollback = Path(app.config['DATABASE'] + '.before-restore-' + secrets.token_hex(4))
            backup(destination, rollback)
            source.backup(destination)
            if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise click.ClickException('還原驗證失敗，請使用還原前備份。')
            # Invalidate every browser session carried in the backup.
            destination.execute('UPDATE users SET auth_version=auth_version+1')
            destination.execute("INSERT INTO settings VALUES ('bingo_auth_version',1) ON CONFLICT(key) DO UPDATE SET value=value+1")
            audit(destination, None, 'database_restore', detail='CLI offline restore')
            destination.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            source.close()
            if destination:
                destination.close()
        click.echo(f'還原完成；原資料已保存在 {rollback}。請重新登入。')
