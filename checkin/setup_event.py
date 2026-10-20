"""Interactive pre-deployment configuration; never imports wsgi or touches an event DB."""
import os
import re
import secrets
from pathlib import Path

import click

from .event_settings import TEXT_FIELDS, event_start, event_text, public_base_url
from .security import hash_password
from .services import USERNAME_RE


def ask_validated(label, validate, default=None):
    while True:
        value = click.prompt(label, default=default, show_default=bool(default))
        try:
            return validate(value)
        except ValueError as error:
            click.echo(f'請修正：{error}', err=True)


def domain_name(value):
    try:
        value = value.strip().lower().encode('idna').decode('ascii')
    except UnicodeError:
        raise ValueError('請輸入有效網域。') from None
    if (len(value) > 253 or '.' not in value
            or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in value.split('.'))):
        raise ValueError('請輸入完整網域，例如 checkin.example.org，不含 https://、埠號或路徑。')
    return value


def acme_email(value):
    value = value.strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('請輸入憑證續期聯絡 Email。')
    return value


def admin_username(value):
    value = value.strip()
    if not USERNAME_RE.fullmatch(value):
        raise ValueError('帳號需 3–40 位英數或 _.-。')
    return value


def dotenv_literal(value, target):
    # Compose keeps backslashes in single quotes; python-dotenv decodes them.
    # Use Compose's double-quoted escapes for those values, including literal $.
    if target == 'docker' and '\\' in value:
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '$$') + '"'
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


@click.command()
@click.option('--target', type=click.Choice(['local', 'docker']), default=None,
              help='local 為本機；docker 為正式 HTTPS Compose。未指定時會詢問。')
@click.option('--output', type=click.Path(path_type=Path), default=None,
              help='新設定檔位置，預設專案 .env；不覆寫既有檔案。')
def main(target, output):
    """設定一場活動及初始管理員，輸出私有 .env；不啟動服務、不變更既有資料。"""
    root = Path(__file__).resolve().parent.parent
    output = output or root / '.env'
    if output.exists() or output.is_symlink():
        raise click.ClickException('設定檔已存在，未做任何變更。請使用 --output 指定新檔名，再自行核對。')
    output = output.expanduser().resolve()
    if output.is_relative_to(root / 'checkin' / 'static'):
        raise click.ClickException('設定檔含私有資料，不可放在公開 static 目錄。')
    click.echo('單一活動事前設定；活動時間皆為 Asia/Taipei，選填項目可按 Enter 略過。')
    target = target or click.prompt('部署方式', type=click.Choice(['local', 'docker']), default='local')
    values = {'CHECKIN_ENV': 'production' if target == 'docker' else 'development'}
    values['EVENT_NAME'] = ask_validated('活動名稱', lambda value: event_text(value, 'EVENT_NAME'), '導生晚宴')
    values['EVENT_STARTS_AT'] = ask_validated('活動時間（YYYY-MM-DD HH:MM，選填）', event_start, '')
    for key in ('EVENT_LOCATION', 'EVENT_ORGANIZER', 'EVENT_CONTACT', 'EVENT_NOTICE'):
        values[key] = ask_validated(TEXT_FIELDS[key][1] + '（選填）', lambda value, key=key: event_text(value, key), '')
    if target == 'docker':
        values['DOMAIN'] = ask_validated('正式網域（例如 checkin.example.org）', domain_name)
        values['ACME_EMAIL'] = ask_validated('HTTPS 憑證聯絡 Email', acme_email)
        values['PUBLIC_BASE_URL'] = 'https://' + values['DOMAIN']
        values['DATABASE_PATH'] = '/data/checkin.sqlite3'
        values['TRUST_PROXY'] = '1'
    else:
        values['PUBLIC_BASE_URL'] = ask_validated('公開網址（票券使用此網址）', public_base_url, 'http://127.0.0.1:5000')
        values['TRUST_PROXY'] = '0'
    values['INITIAL_ADMIN_USERNAME'] = ask_validated('初始管理員帳號', admin_username, 'admin')
    while True:
        password = click.prompt('初始管理員密碼（12–128 字）', hide_input=True, confirmation_prompt='再次輸入密碼')
        if 12 <= len(password) <= 128:
            break
        click.echo('密碼需 12–128 字，請重新輸入。', err=True)
    values['INITIAL_ADMIN_PASSWORD_HASH'] = hash_password(password)
    del password
    values['SECRET_KEY'] = secrets.token_hex(32)
    content = ('# 事前設定產生；一份部署對應一場活動。請勿公開或提交版本庫。\n'
               '# 初始管理員僅於空白帳號資料庫建立；後續重啟不覆寫帳號或密碼。\n'
               + ''.join(f'{key}={dotenv_literal(value, target)}\n' for key, value in values.items()))
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(str(output), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise click.ClickException('設定檔剛由其他程序建立，未覆寫。') from None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(content)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    click.echo(f'設定完成：{output}（權限 600）')
    click.echo('密碼以雜湊保存，session 金鑰已隨機產生；服務首次啟動時才建立管理員。')
    if target == 'docker':
        click.echo('下一步：docker compose config --quiet → docker compose build → docker compose up -d')
    else:
        click.echo('下一步：FLASK_SKIP_DOTENV=1 .venv/bin/python -m flask --app wsgi run --host 127.0.0.1 --port 5000')
    if output != root / '.env':
        click.echo('此檔不是預設 .env；請核對後再於新部署中使用，現行活動設定未變更。')


if __name__ == '__main__':
    main()
