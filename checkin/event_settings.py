"""Validated, deployment-wide settings for one event."""
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

TEXT_FIELDS = {
    'EVENT_NAME': (100, '活動名稱'),
    'EVENT_LOCATION': (200, '活動地點'),
    'EVENT_ORGANIZER': (100, '主辦單位'),
    'EVENT_CONTACT': (200, '聯絡方式'),
    'EVENT_NOTICE': (500, '票券提醒'),
}


def event_text(value, key):
    limit, label = TEXT_FIELDS[key]
    if not isinstance(value, str):
        raise ValueError(f'{label}必須是文字。')
    value = value.strip()
    if key == 'EVENT_NAME' and not value:
        raise ValueError('活動名稱不可空白。')
    if len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f'{label}最多 {limit} 字，不可含換行或控制字元。')
    return value


def event_start(value):
    if not isinstance(value, str):
        raise ValueError('活動時間必須是文字。')
    value = value.strip()
    if not value:
        return ''
    try:
        if 'T' not in value and ' ' not in value:
            raise ValueError()
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('活動時間請用 YYYY-MM-DD HH:MM，例如 2026-11-20 18:00。') from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo('Asia/Taipei'))
    return moment.astimezone(ZoneInfo('Asia/Taipei')).isoformat(timespec='minutes')


def public_base_url(value, require_https=False):
    message = '公開網址必須是 http:// 或 https:// 的網域根網址，不可含帳密、路徑、查詢或片段。'
    if not isinstance(value, str):
        raise ValueError(message)
    value = value.strip().rstrip('/')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                 and parsed.path == '' and not parsed.query and not parsed.fragment
                 and not parsed.username and not parsed.password
                 and not any(char.isspace() or ord(char) < 32 for char in value)
                 and (port is None or 1 <= port <= 65535))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(message)
    if require_https and parsed.scheme != 'https':
        raise ValueError('正式環境的公開網址必須使用 HTTPS。')
    return value


def validate_event_settings(config):
    for key in TEXT_FIELDS:
        config[key] = event_text(config[key], key)
    config['EVENT_STARTS_AT'] = event_start(config['EVENT_STARTS_AT'])
    config['PUBLIC_BASE_URL'] = public_base_url(config['PUBLIC_BASE_URL'])


def event_info(config):
    return {key: config['EVENT_' + suffix] for key, suffix in (
        ('starts_at', 'STARTS_AT'), ('location', 'LOCATION'), ('organizer', 'ORGANIZER'),
        ('contact', 'CONTACT'), ('notice', 'NOTICE'))}
