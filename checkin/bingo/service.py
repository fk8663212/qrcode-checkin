"""Transactional bingo rules; attendance, tickets and game history share one SQLite DB."""
import hashlib
import json
import secrets
import time

from flask import abort, current_app, session

from ..db import audit, get_db, transaction
from ..services import find_ticket
from .rules import completed_lines, make_card

MAX_PRIZES = 200


def validate_prize_count(value):
    if type(value) is not int or not 1 <= value <= MAX_PRIZES:
        abort(400, description=f'獎品份數必須是 1～{MAX_PRIZES} 的整數。')
    return value


def initialize(app):
    with app.app_context(), transaction() as db:
        for key, value in [('bingo_revision', 0), ('bingo_auth_version', 1), ('bingo_scope', secrets.randbits(52))]:
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, value))
        if not db.execute('SELECT 1 FROM bingo_games LIMIT 1').fetchone():
            db.execute('INSERT INTO bingo_games(title,created_at) VALUES (?,?)', (app.config['EVENT_NAME'], time.time()))


def setting(db, key):
    return db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()[0]


def bump(db):
    db.execute("UPDATE settings SET value=value+1 WHERE key='bingo_revision'")


def current(db, expected=None):
    game = db.execute('SELECT * FROM bingo_games ORDER BY id DESC LIMIT 1').fetchone()
    if not game:
        abort(503, description='賓果尚未準備完成，請聯絡管理員重新啟動服務。')
    if expected is not None and (type(expected) is not int or game['id'] != expected):
        abort(409, description='局次已更新，請重新整理後操作。')
    return game


def session_attendee(required=False):
    """Recheck revocation and attendance on every request, including existing sessions."""
    identity = session.get('bingo_identity')
    row = None
    if isinstance(identity, dict):
        db = get_db()
        row = db.execute('''SELECT a.*,t.id AS ticket_id,t.token FROM attendees a
                            JOIN tickets t ON t.attendee_pk=a.id AND t.revoked_at IS NULL
                            JOIN checkins c ON c.attendee_pk=a.id AND c.undone_at IS NULL
                            WHERE a.id=? AND t.id=?''', (identity.get('attendee'), identity.get('ticket'))).fetchone()
        if (not row or identity.get('version') != setting(db, 'bingo_auth_version')
                or not secrets.compare_digest(str(identity.get('credential', '')), hashlib.sha256(row['token'].encode()).hexdigest())):
            row = None
    if required and row is None:
        abort(401, description='賓果存取已失效或報到已撤回，請使用最新電子票券重新領卡；仍需保持已報到狀態。')
    return row


def claim(token):
    with transaction() as db:
        attendee = find_ticket(token)
        if not attendee['checked_at']:
            abort(403, description='請先由工作人員確認報到，再使用此電子票券領取賓果卡。')
        previous = session_attendee()
        if previous and previous['id'] != attendee['id']:
            abort(409, description='此瀏覽器已有另一位來賓的賓果卡，請先按「切換票券」，再開啟本人的電子票券。')
        game = current(db)
        card = db.execute('SELECT id FROM bingo_cards WHERE game_id=? AND attendee_pk=?', (game['id'], attendee['id'])).fetchone()
        if not card:
            if game['status'] != 'waiting' or not game['registration_open']:
                abort(409, description='目前未開放新領卡，請等候主持人開放。已領卡者可用最新票券恢復原卡。')
            db.execute('INSERT INTO bingo_cards(game_id,attendee_pk,numbers) VALUES (?,?,?)',
                       (game['id'], attendee['id'], json.dumps(make_card())))
            bump(db)
            audit(db, None, 'bingo_claim', attendee['id'], json.dumps({'game_id': game['id']}))
        identity = dict(attendee=attendee['id'], ticket=attendee['ticket_id'],
                        credential=hashlib.sha256(attendee['token'].encode()).hexdigest(),
                        version=setting(db, 'bingo_auth_version'))
    # Only authenticate the participant after the database commit succeeds.
    session['bingo_identity'] = identity
    return attendee['id']


def registration(actor, game_id, opened):
    if type(opened) is not bool:
        abort(400, description='領卡開關必須為布林值。')
    with transaction() as db:
        game = current(db, game_id)
        if opened and game['status'] != 'waiting':
            abort(409, description='已開始開獎，不能再開放本局新領卡。')
        db.execute('UPDATE bingo_games SET registration_open=? WHERE id=?', (int(opened), game['id']))
        bump(db)
        audit(db, actor['id'], 'bingo_registration', detail=json.dumps({'game_id': game['id'], 'open': opened}))


def prizes(actor, game_id, max_winners):
    validate_prize_count(max_winners)
    with transaction() as db:
        game = current(db, game_id)
        if game['status'] != 'waiting':
            abort(409, description='本局已結束或開始開獎，獎品份數已鎖定；請在下一局設定。')
        if game['max_winners'] == max_winners:
            return
        db.execute('UPDATE bingo_games SET max_winners=? WHERE id=?', (max_winners, game['id']))
        bump(db)
        audit(db, actor['id'], 'bingo_prizes', detail=json.dumps({
            'game_id': game['id'], 'previous_max_winners': game['max_winners'], 'max_winners': max_winners}))


def draw(actor, game_id, expected_round):
    if type(expected_round) is not int or not 0 <= expected_round < 49:
        abort(400, description='缺少有效的預期開獎回合，請重新整理。')
    with transaction() as db:
        game = current(db, game_id)
        previous = db.execute('SELECT number FROM bingo_draws WHERE game_id=? ORDER BY round', (game['id'],)).fetchall()
        if len(previous) != expected_round or game['status'] == 'finished':
            abort(409, description='開獎進度已更新或本局已結束，請確認畫面後再抽號。')
        cards = db.execute('''SELECT b.* FROM bingo_cards b WHERE b.game_id=? AND EXISTS
                              (SELECT 1 FROM checkins c WHERE c.attendee_pk=b.attendee_pk AND c.undone_at IS NULL)
                              ORDER BY b.attendee_pk''', (game['id'],)).fetchall()
        if not cards:
            abort(409, description='本局尚無已報到且已領卡的玩家。')
        drawn = {row['number'] for row in previous}
        number = secrets.choice(sorted(set(range(1, 50)) - drawn))
        drawn.add(number)
        round_no = len(previous) + 1
        candidates = []
        for card in cards:
            if card['qualified_round'] is None and completed_lines(json.loads(card['numbers']), drawn):
                candidates.append(card['attendee_pk'])
                db.execute('UPDATE bingo_cards SET qualified_round=? WHERE id=?', (round_no, card['id']))
        count = db.execute('SELECT COUNT(*) FROM bingo_winners WHERE game_id=?', (game['id'],)).fetchone()[0]
        order = candidates[:]
        secrets.SystemRandom().shuffle(order)
        selected = order[:max(0, game['max_winners'] - count)]
        for index, attendee_pk in enumerate(selected):
            db.execute('INSERT INTO bingo_winners(game_id,attendee_pk,round,position) VALUES (?,?,?,?)',
                       (game['id'], attendee_pk, round_no, count + index + 1))
        stamp = time.time()
        db.execute('INSERT INTO bingo_draws VALUES (?,?,?,?,?,?,?)',
                   (game['id'], round_no, number, stamp, json.dumps(candidates), json.dumps(order), json.dumps(selected)))
        ended = count + len(selected) >= game['max_winners'] or round_no == 49
        db.execute('UPDATE bingo_games SET registration_open=0,status=?,ended_at=? WHERE id=?',
                   ('finished' if ended else 'playing', stamp if ended else None, game['id']))
        bump(db)
        result = dict(game_id=game['id'], round=round_no, number=number, candidates=candidates, lottery_order=order, selected=selected)
        audit(db, actor['id'], 'bingo_draw', detail=json.dumps(result))
        return result


def marks(game_id, indices):
    if (type(game_id) is not int or game_id < 1 or not isinstance(indices, list) or len(indices) > 25
            or any(type(index) is not int or not 0 <= index < 25 for index in indices)):
        abort(400, description='勾選格子資料無效。')
    with transaction() as db:
        attendee = session_attendee(required=True)
        game = current(db, game_id)
        result = db.execute('UPDATE bingo_cards SET marks=? WHERE game_id=? AND attendee_pk=?',
                            (json.dumps(sorted(set(indices) | {12})), game['id'], attendee['id']))
        if not result.rowcount:
            abort(409, description='本局尚未領卡，請使用本人票券領卡。')


def claimed(actor, game_id, attendee_pk, value):
    if type(game_id) is not int or type(attendee_pk) is not int or type(value) is not bool:
        abort(400)
    with transaction() as db:
        row = db.execute('SELECT * FROM bingo_winners WHERE game_id=? AND attendee_pk=?', (game_id, attendee_pk)).fetchone()
        if not row:
            abort(404, description='查無這位得獎者。')
        stamp = row['claimed_at'] or time.time() if value else None
        db.execute('UPDATE bingo_winners SET claimed_at=? WHERE game_id=? AND attendee_pk=?', (stamp, game_id, attendee_pk))
        bump(db)
        audit(db, actor['id'], 'bingo_prize_claimed' if value else 'bingo_prize_unclaimed', attendee_pk,
              json.dumps({'game_id': game_id}))


def reset(actor, game_id, confirmation, max_winners):
    if confirmation != '開啟下一局':
        abort(400, description='請完整輸入「開啟下一局」確認。')
    validate_prize_count(max_winners)
    with transaction() as db:
        old = current(db, game_id)
        db.execute("UPDATE bingo_games SET registration_open=0,status='finished',ended_at=COALESCE(ended_at,?) WHERE id=?",
                   (time.time(), old['id']))
        game_id = db.execute('INSERT INTO bingo_games(title,max_winners,created_at) VALUES (?,?,?)',
                             (current_app.config['EVENT_NAME'], max_winners, time.time())).lastrowid
        attendees = db.execute('''SELECT DISTINCT b.attendee_pk FROM bingo_cards b WHERE EXISTS
                                  (SELECT 1 FROM checkins c WHERE c.attendee_pk=b.attendee_pk AND c.undone_at IS NULL)''').fetchall()
        for row in attendees:
            db.execute('INSERT INTO bingo_cards(game_id,attendee_pk,numbers) VALUES (?,?,?)',
                       (game_id, row['attendee_pk'], json.dumps(make_card())))
        bump(db)
        audit(db, actor['id'], 'bingo_next_game', detail=json.dumps({
            'previous_game': old['id'], 'game_id': game_id, 'max_winners': max_winners}))
        return game_id


def snapshot(attendee_pk=None, admin=False, game_id=None):
    db = get_db()
    db.execute('BEGIN')
    try:
        game = db.execute('SELECT * FROM bingo_games WHERE id=?', (game_id,)).fetchone() if game_id else current(db)
        if not game:
            abort(404, description='查無這一局。')
        gid = game['id']
        draws = [dict(row) for row in db.execute('SELECT round,number,drawn_at FROM bingo_draws WHERE game_id=? ORDER BY round', (gid,))]
        winners = []
        for row in db.execute('''SELECT w.*,a.name,a.table_no FROM bingo_winners w JOIN attendees a ON a.id=w.attendee_pk
                                 WHERE w.game_id=? ORDER BY w.position''', (gid,)):
            label = 'P%03d' % row['attendee_pk']
            value = dict(player_id=row['attendee_pk'], card_label=label, round=row['round'], position=row['position'], claimed_at=row['claimed_at'])
            # Public projection/participants see card labels, never the attendee roster.
            value['name'] = row['name'] if admin else '幸運來賓'
            if admin:
                value['table_no'] = row['table_no']
            winners.append(value)
        data = dict(game=dict(game), draws=draws, winners=winners,
                    player_count=db.execute('''SELECT COUNT(*) FROM bingo_cards b WHERE b.game_id=? AND EXISTS
                                               (SELECT 1 FROM checkins c WHERE c.attendee_pk=b.attendee_pk AND c.undone_at IS NULL)''', (gid,)).fetchone()[0],
                    revision=setting(db, 'bingo_revision'), scope=setting(db, 'bingo_scope'), server_time=time.time(),
                    demo=current_app.config['CHECKIN_ENV'] == 'demo')
        if attendee_pk:
            row = db.execute('''SELECT b.*,a.name,a.table_no FROM bingo_cards b JOIN attendees a ON a.id=b.attendee_pk
                                WHERE b.game_id=? AND b.attendee_pk=?''', (gid, attendee_pk)).fetchone()
            if row:
                card = {key: row[key] for key in ('id', 'game_id', 'name', 'table_no', 'qualified_round')}
                card.update(numbers=json.loads(row['numbers']), marks=json.loads(row['marks']), player_id=attendee_pk, card_label='P%03d' % attendee_pk)
                card['lines'] = completed_lines(card['numbers'], {draw['number'] for draw in draws})
                data['card'] = card
        if admin:
            data['games'] = [dict(row) for row in db.execute('SELECT * FROM bingo_games ORDER BY id DESC')]
            data['round_results'] = [{**dict(row), **{key: json.loads(row[key]) for key in ('candidates','lottery_order','selected')}}
                                     for row in db.execute('SELECT * FROM bingo_draws WHERE game_id=? ORDER BY round', (gid,))]
            data['players'] = [dict(id=row['attendee_pk'], name=row['name'], table_no=row['table_no'], qualified_round=row['qualified_round'])
                               for row in db.execute('''SELECT b.attendee_pk,a.name,a.table_no,b.qualified_round FROM bingo_cards b
                                                         JOIN attendees a ON a.id=b.attendee_pk WHERE b.game_id=? ORDER BY b.attendee_pk''', (gid,))]
        return data
    finally:
        db.rollback()
