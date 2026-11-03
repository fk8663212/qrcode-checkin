"""Editable participant resources, shared by all tickets in this deployment."""
import json
from urllib.parse import urlsplit

from .db import audit, get_db, transaction

LINK_FIELDS = {'menu_url': '晚宴菜單', 'schedule_url': '活動流程'}


def validate_link(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label}連結必須是文字。')
    value = value.strip()
    if not value:
        return ''
    message = f'{label}請填寫完整 HTTPS 網址（最多 2048 字），不可含帳密、空白或控制字元。'
    try:
        parsed = urlsplit(value)
        valid = (len(value) <= 2048 and parsed.scheme == 'https' and parsed.hostname
                 and not parsed.username and not parsed.password
                 and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == '\\' for char in value)
                 and (parsed.port is None or 1 <= parsed.port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(message)
    return value


def get_event_links():
    return dict(get_db().execute('SELECT menu_url,schedule_url FROM event_links WHERE id=1').fetchone())


def save_event_links(actor, values):
    links = {key: validate_link(values.get(key, ''), label) for key, label in LINK_FIELDS.items()}
    with transaction() as db:
        before = get_event_links()
        if before != links:
            db.execute('UPDATE event_links SET menu_url=?,schedule_url=? WHERE id=1',
                       (links['menu_url'], links['schedule_url']))
            audit(db, actor['id'], 'event_links_update',
                  detail=json.dumps({'before': before, 'after': links}, ensure_ascii=False))

