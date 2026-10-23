"""Auditable raffle operations. Every award, retry and replacement is one SQLite transaction."""
import json
import re
import secrets
import time
import unicodedata

from flask import abort

from ..db import audit, get_db, now, transaction

MAX_QUANTITY = 200
REQUEST_ID = re.compile(r'^[A-Za-z0-9_-]{16,100}$')


def initialize(app):
    with app.app_context(), transaction() as db:
        if not db.execute('SELECT 1 FROM raffle_rounds LIMIT 1').fetchone():
            db.execute('INSERT INTO raffle_rounds(title,created_at) VALUES (?,?)', ('一般抽獎', now()))


def text(value, label, limit=100):
    if not isinstance(value, str):
        abort(400, description=f'請填寫{label}。')
    value = value.strip()
    if not value or len(value) > limit or any(unicodedata.category(c).startswith('C') for c in value):
        abort(400, description=f'{label}須為 1～{limit} 字，不可含控制字元。')
    return value


def integer(value, label, maximum=None):
    if type(value) is not int or value < 1 or (maximum is not None and value > maximum):
        abort(400, description=f'{label}必須是 1～{maximum} 的整數。' if maximum else f'{label}無效，請重新整理。')
    return value


def rules(data):
    values = {key: data.get(key) for key in ('allow_repeat', 'include_previous', 'exclude_bingo')}
    if any(type(value) is not bool for value in values.values()):
        abort(400, description='請明確選擇三項抽獎資格設定。')
    return values


def current(db, round_id=None):
    row = db.execute('SELECT * FROM raffle_rounds ORDER BY id DESC LIMIT 1').fetchone()
    if not row:
        abort(503, description='抽獎尚未準備完成，請重新啟動服務。')
    if round_id is not None and (type(round_id) is not int or row['id'] != round_id):
        abort(409, description='抽獎輪次已更新，請重新整理後操作。')
    return row


def bump(db, round_id):
    db.execute('UPDATE raffle_rounds SET revision=revision+1 WHERE id=?', (round_id,))


def eligible(db, round_row):
    return db.execute('''SELECT a.id,a.attendee_id,a.name,a.table_no FROM attendees a
        WHERE EXISTS (SELECT 1 FROM checkins c WHERE c.attendee_pk=a.id AND c.undone_at IS NULL)
        AND (? OR NOT EXISTS (SELECT 1 FROM raffle_winners w WHERE w.attendee_pk=a.id
                              AND w.round_id=? AND w.voided_at IS NULL))
        AND (? OR NOT EXISTS (SELECT 1 FROM raffle_winners w WHERE w.attendee_pk=a.id
                              AND w.round_id<? AND w.voided_at IS NULL))
        AND (NOT ? OR NOT EXISTS (SELECT 1 FROM bingo_winners b WHERE b.attendee_pk=a.id))
        ORDER BY a.id''', (round_row['allow_repeat'], round_row['id'], round_row['include_previous'],
                           round_row['id'], round_row['exclude_bingo'])).fetchall()


def configure(actor, round_id, data):
    title = text(data.get('title'), '輪次名稱')
    options = rules(data)
    with transaction() as db:
        row = current(db, round_id)
        if row['status'] != 'waiting':
            abort(409, description='首次抽獎後資格規則已鎖定，請另開新一輪設定。')
        db.execute('UPDATE raffle_rounds SET title=?,allow_repeat=?,include_previous=?,exclude_bingo=? WHERE id=?',
                   (title, *options.values(), round_id))
        bump(db, round_id)
        audit(db, actor['id'], 'raffle_configure', detail=json.dumps({'round_id':round_id, 'title':title, **options}))


def new_round(actor, round_id, data):
    if data.get('confirmation') != '開啟新一輪抽獎':
        abort(400, description='請完整輸入「開啟新一輪抽獎」確認。')
    title = text(data.get('title'), '輪次名稱')
    options = rules(data)
    with transaction() as db:
        current(db, round_id)
        db.execute("UPDATE raffle_rounds SET status='finished',ended_at=COALESCE(ended_at,?) WHERE id=?", (now(), round_id))
        new_id = db.execute('''INSERT INTO raffle_rounds(title,allow_repeat,include_previous,exclude_bingo,created_at)
                               VALUES (?,?,?,?,?)''', (title, *options.values(), now())).lastrowid
        audit(db, actor['id'], 'raffle_new_round', detail=json.dumps({'previous_round':round_id, 'round_id':new_id, **options}))
        return new_id


def prize(actor, round_id, data):
    name = text(data.get('name'), '獎品名稱')
    quantity = integer(data.get('quantity'), '獎品份數', MAX_QUANTITY)
    prize_id = data.get('prize_id')
    if prize_id is not None:
        integer(prize_id, '獎品編號')
    with transaction() as db:
        current(db, round_id)
        previous = None
        if prize_id is None:
            prize_id = db.execute('INSERT INTO raffle_prizes(round_id,name,quantity,created_at) VALUES (?,?,?,?)',
                                  (round_id, name, quantity, now())).lastrowid
        else:
            previous = db.execute('SELECT * FROM raffle_prizes WHERE id=? AND round_id=?', (prize_id, round_id)).fetchone()
            if not previous:
                abort(404, description='查無本輪獎品。')
            awarded = db.execute('SELECT COUNT(*) FROM raffle_winners WHERE prize_id=? AND voided_at IS NULL', (prize_id,)).fetchone()[0]
            if quantity < awarded:
                abort(409, description=f'此獎已抽出 {awarded} 份，不能將份數減到已抽出數量以下。')
            db.execute('UPDATE raffle_prizes SET name=?,quantity=? WHERE id=?', (name, quantity, prize_id))
        bump(db, round_id)
        audit(db, actor['id'], 'raffle_prize', detail=json.dumps({
            'round_id':round_id, 'prize_id':prize_id, 'name':name, 'quantity':quantity,
            'previous':dict(previous) if previous else None}))


def draw(actor, round_id, data, replacement=False):
    prize_id = integer(data.get('prize_id'), '獎品編號')
    count = integer(data.get('count'), '抽出人數', MAX_QUANTITY)
    request_id = data.get('request_id')
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        abort(400, description='缺少有效操作識別碼，請重新整理。')
    original_id, reason = None, None
    if replacement:
        original_id = integer(data.get('winner_id'), '原得獎紀錄')
        reason = text(data.get('reason'), '重抽原因', 300)
        if count != 1 or data.get('confirm') is not True:
            abort(400, description='請確認撤銷原得獎並重抽一位。')
    payload = json.dumps([round_id, prize_id, count, original_id, reason], ensure_ascii=False)
    with transaction() as db:
        existing = db.execute('SELECT * FROM raffle_draws WHERE request_id=?', (request_id,)).fetchone()
        if existing:
            if existing['operator_id'] != actor['id'] or existing['request_payload'] != payload:
                abort(409, description='操作識別碼已被其他抽獎使用，請重新整理並核對紀錄。')
            return {'draw_id':existing['id'], 'replayed':True}
        round_row = current(db, round_id)
        selected_prize = db.execute('SELECT * FROM raffle_prizes WHERE id=? AND round_id=?', (prize_id, round_id)).fetchone()
        if not selected_prize:
            abort(404, description='查無本輪獎品。')
        original = None
        if replacement:
            original = db.execute('SELECT * FROM raffle_winners WHERE id=? AND prize_id=? AND round_id=?',
                                   (original_id, prize_id, round_id)).fetchone()
            if not original or original['voided_at']:
                abort(409, description='原得獎紀錄已變更或已重抽，請核對紀錄。')
            if original['claimed_at']:
                abort(409, description='此獎已領取，請先核對並取消領獎標記，再辦理重抽。')
        awarded = db.execute('SELECT COUNT(*) FROM raffle_winners WHERE prize_id=? AND voided_at IS NULL', (prize_id,)).fetchone()[0]
        if not replacement and count > selected_prize['quantity'] - awarded:
            abort(409, description='獎品剩餘份數不足，請調整抽出人數或增加獎品份數。')
        pool = [row for row in eligible(db, round_row) if not original or row['id'] != original['attendee_pk']]
        if len(pool) < count:
            abort(409, description=f'符合資格的來賓僅 {len(pool)} 位，無法抽出 {count} 位；請檢查報到與排除規則。')
        selected = secrets.SystemRandom().sample(pool, count)
        stamp = now()
        draw_id = db.execute('''INSERT INTO raffle_draws(round_id,prize_id,request_id,request_payload,
            operator_id,operator_name,drawn_at,candidates,selected) VALUES (?,?,?,?,?,?,?,?,?)''',
            (round_id, prize_id, request_id, payload, actor['id'], actor['username'], stamp,
             json.dumps([row['id'] for row in pool]), json.dumps([row['id'] for row in selected]))).lastrowid
        if original:
            db.execute('UPDATE raffle_winners SET voided_at=?,voided_by=?,void_reason=? WHERE id=?',
                       (stamp, actor['id'], reason, original_id))
        for position, attendee in enumerate(selected, 1):
            db.execute('''INSERT INTO raffle_winners(draw_id,round_id,prize_id,attendee_pk,position,
                attendee_id,name,table_no,replaces_id) VALUES (?,?,?,?,?,?,?,?,?)''',
                (draw_id, round_id, prize_id, attendee['id'], position, attendee['attendee_id'],
                 attendee['name'], attendee['table_no'], original_id))
        db.execute("UPDATE raffle_rounds SET status='playing' WHERE id=?", (round_id,))
        bump(db, round_id)
        audit(db, actor['id'], 'raffle_redraw' if replacement else 'raffle_draw',
              detail=json.dumps({'round_id':round_id, 'prize_id':prize_id, 'draw_id':draw_id,
                                 'count':count, 'replaces':original_id, 'reason':reason}))
        return {'draw_id':draw_id, 'replayed':False}


def claimed(actor, data):
    winner_id = integer(data.get('winner_id'), '得獎紀錄')
    value = data.get('claimed')
    if type(value) is not bool:
        abort(400, description='領獎狀態格式錯誤。')
    with transaction() as db:
        winner = db.execute('SELECT * FROM raffle_winners WHERE id=?', (winner_id,)).fetchone()
        if not winner:
            abort(404, description='查無得獎紀錄。')
        if winner['voided_at']:
            abort(409, description='此中獎已撤銷，請核對替補得主。')
        if bool(winner['claimed_at']) == value:
            return
        db.execute('UPDATE raffle_winners SET claimed_at=?,claimed_by=? WHERE id=?',
                   (now() if value else None, actor['id'] if value else None, winner_id))
        bump(db, winner['round_id'])
        audit(db, actor['id'], 'raffle_claimed' if value else 'raffle_unclaimed', winner['attendee_pk'],
              json.dumps({'winner_id':winner_id}))


def snapshot(admin=False, round_id=None):
    db = get_db()
    db.execute('BEGIN')
    try:
        snapshot_time = time.time()
        latest = current(db)
        selected_round = db.execute('SELECT * FROM raffle_rounds WHERE id=?', (round_id,)).fetchone() if round_id else latest
        if not selected_round:
            abort(404, description='查無此抽獎輪次。')
        rid = selected_round['id']
        prizes = [dict(row) for row in db.execute('''SELECT p.*,
            (SELECT COUNT(*) FROM raffle_winners w WHERE w.prize_id=p.id AND w.voided_at IS NULL) AS awarded
            FROM raffle_prizes p WHERE p.round_id=? ORDER BY p.id''', (rid,))]
        winners = []
        for row in db.execute('''SELECT w.*,d.drawn_at,d.operator_name,p.name AS prize_name FROM raffle_winners w
            JOIN raffle_draws d ON d.id=w.draw_id JOIN raffle_prizes p ON p.id=w.prize_id
            WHERE w.round_id=? ORDER BY w.id DESC''', (rid,)):
            if admin:
                value = dict(row)
            else:
                if row['voided_at']:
                    continue
                value = {key:row[key] for key in ('id','draw_id','prize_id','prize_name','drawn_at','claimed_at')}
                value.update(name='幸運來賓', card_label='P%03d' % row['attendee_pk'])
            winners.append(value)
        data = {'round':dict(selected_round), 'latest_round_id':latest['id'], 'prizes':prizes, 'winners':winners,
                'eligible_count':len(eligible(db, selected_round)), 'server_time':snapshot_time}
        last = db.execute('''SELECT d.id,d.drawn_at,p.name AS prize_name,d.selected FROM raffle_draws d
                              JOIN raffle_prizes p ON p.id=d.prize_id WHERE d.round_id=? ORDER BY d.id DESC LIMIT 1''', (rid,)).fetchone()
        data['latest_draw'] = ({'id':last['id'], 'drawn_at':last['drawn_at'], 'prize_name':last['prize_name'],
                                'count':len(json.loads(last['selected']))} if last else None)
        data['draw_count'] = db.execute('SELECT COUNT(*) FROM raffle_draws WHERE round_id=?', (rid,)).fetchone()[0]
        if admin:
            data['rounds'] = [dict(row) for row in db.execute('SELECT * FROM raffle_rounds ORDER BY id DESC')]
            data['draws'] = [{**dict(row), 'candidates':json.loads(row['candidates']), 'selected':json.loads(row['selected'])}
                             for row in db.execute('SELECT * FROM raffle_draws WHERE round_id=? ORDER BY id DESC', (rid,))]
        return data
    finally:
        db.rollback()
