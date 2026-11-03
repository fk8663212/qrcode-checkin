"""Small event presets: roster vocabulary and seating are independent choices."""
import json

from flask import g

from .db import audit, bump_roster, get_db, transaction

ROSTER_PROFILES = {'standard': '一般活動名冊', 'campus': '校園名冊'}
SEATING_MODES = {'table': '指定桌次', 'area': '依名冊區域自由座',
                 'grade': '依年級分區自由座', 'open': '全場自由入座'}


def get_preferences(refresh=False):
    if refresh or 'event_preferences' not in g:
        g.event_preferences = dict(get_db().execute('SELECT roster_profile,seating_mode,default_affiliation FROM event_preferences WHERE id=1').fetchone())
    return g.event_preferences


def save_preferences(actor, values):
    profile, mode = values.get('roster_profile'), values.get('seating_mode')
    affiliation = values.get('default_affiliation', '').strip()
    if profile not in ROSTER_PROFILES or mode not in SEATING_MODES:
        raise ValueError('請選擇有效的名冊模板及座位方式。')
    if len(affiliation) > 120 or any(ord(char) < 32 for char in affiliation):
        raise ValueError('預設單位／系所最多 120 字，不可含控制字元。')
    after = dict(roster_profile=profile, seating_mode=mode, default_affiliation=affiliation)
    with transaction() as db:
        before = get_preferences(refresh=True)
        if before != after:
            db.execute('UPDATE event_preferences SET roster_profile=?,seating_mode=?,default_affiliation=? WHERE id=1',
                       (profile, mode, affiliation))
            bump_roster(db)
            audit(db, actor['id'], 'event_preferences_update',
                  detail=json.dumps({'before': before, 'after': after}, ensure_ascii=False))
    g.pop('event_preferences', None)


def seating_info(row, preferences=None):
    preferences = preferences or get_preferences()
    mode = preferences['seating_mode']
    if mode == 'open':
        return dict(mode=mode, label='座位安排', value='自由入座', note='請依現場指引入座')
    value = row['table_no'] if mode == 'table' else row['seating_area']
    if mode == 'grade' and not value:
        value = row['grade']
    if mode != 'table' and value and not value.endswith('區'):
        value += '區'
    return dict(mode=mode, label='桌次' if mode == 'table' else '區域',
                value=value or '現場安排', note='' if mode == 'table' else '區域內自由入座')


def attendance_groups(preferences=None):
    preferences = preferences or get_preferences()
    mode = preferences['seating_mode']
    expression = {'table': 'a.table_no', 'area': 'a.seating_area',
                  'grade': "COALESCE(NULLIF(a.seating_area,''),a.grade)", 'open': "''"}[mode]
    rows = get_db().execute(f'''SELECT {expression} AS group_key, COUNT(*) AS total,COUNT(c.id) AS checked
        FROM attendees a LEFT JOIN checkins c ON c.attendee_pk=a.id AND c.undone_at IS NULL
        GROUP BY {expression} ORDER BY group_key='',group_key''').fetchall()
    groups = []
    for row in rows:
        key = row['group_key']
        label = ('全場自由座' if mode == 'open' else (key or ('未安排桌次' if mode == 'table' else '未安排區域')))
        if mode == 'table' and key and not key.endswith('桌'):
            label += ' 桌'
        elif mode in ('area', 'grade') and key and not key.endswith('區'):
            label += '區'
        groups.append(dict(key=key, label=label, total=row['total'], checked=row['checked'],
                           complete=(bool(key) or mode == 'open') and row['total'] > 0 and row['checked'] == row['total']))
    return groups
