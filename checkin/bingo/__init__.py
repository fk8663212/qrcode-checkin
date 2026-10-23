"""Flask adapter for the integrated bingo game."""
import json
import io

import qrcode
from flask import Blueprint, Response, abort, current_app, g, jsonify, render_template, request, send_file, session

from ..security import csrf_token, rate_limit, require_role
from ..views import body
from . import service

bp = Blueprint('bingo', __name__)


@bp.get('/bingo')
def player():
    return render_template('bingo/player.html')


@bp.get('/bingo/host')
@require_role('admin')
def host():
    return render_template('bingo/host.html', prize_limit=service.MAX_PRIZES)


@bp.get('/bingo/display')
def display():
    return render_template('bingo/display.html')


@bp.get('/bingo/join-qr.png')
def join_qr():
    image = qrcode.make(current_app.config['PUBLIC_BASE_URL'] + '/bingo')
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    stream.seek(0)
    return send_file(stream, mimetype='image/png')


@bp.get('/api/bingo/state')
def state():
    attendee = service.session_attendee()
    data = service.snapshot(attendee_pk=attendee['id'] if attendee else None)
    data.update(csrf=csrf_token(), authenticated=bool(attendee))
    return jsonify(data)


@bp.get('/api/bingo/display-state')
def display_state():
    return jsonify(service.snapshot())


@bp.post('/api/bingo/claim')
def claim():
    rate_limit('bingo-claim:' + request.remote_addr, current_app.config['TICKET_LIMIT'])
    attendee_pk = service.claim(body().get('token'))
    data = service.snapshot(attendee_pk=attendee_pk)
    data.update(csrf=csrf_token(), authenticated=True)
    return jsonify(data)


@bp.post('/api/bingo/leave')
def leave():
    session.pop('bingo_identity', None)
    return jsonify(ok=True)


@bp.post('/api/bingo/marks')
def marks():
    data = body()
    service.marks(data.get('game_id'), data.get('marks'))
    return jsonify(ok=True)


@bp.get('/api/bingo/admin/state')
@require_role('admin')
def admin_state():
    game_id = request.args.get('game', type=int)
    if request.args.get('game') and not game_id:
        abort(400)
    data = service.snapshot(admin=True, game_id=game_id)
    data['csrf'] = csrf_token()
    return jsonify(data)


@bp.post('/api/bingo/admin/<action>')
@require_role('admin')
def action(action):
    data = body()
    game_id = data.get('game_id')
    if type(game_id) is not int or game_id < 1:
        abort(400, description='缺少有效局次，請重新整理。')
    if action == 'registration':
        service.registration(g.user, game_id, data.get('open'))
    elif action == 'prizes':
        service.prizes(g.user, game_id, data.get('max_winners'))
    elif action == 'draw':
        service.draw(g.user, game_id, data.get('expected_round'))
    elif action == 'claimed':
        service.claimed(g.user, game_id, data.get('player_id'), data.get('claimed'))
    elif action == 'reset':
        service.reset(g.user, game_id, data.get('confirmation'), data.get('max_winners'))
    else:
        abort(404)
    result = service.snapshot(admin=True)
    result['csrf'] = csrf_token()
    return jsonify(result)


@bp.post('/api/bingo/admin-export')
@require_role('admin')
def export():
    # No bearer credentials, password hashes or sessions are exported.
    from ..db import audit, get_db, transaction
    db = get_db()
    db.execute('BEGIN')
    try:
        data = {'format_version': 1, 'games': []}
        for table in ('bingo_games','bingo_cards','bingo_draws','bingo_winners'):
            data[table.removeprefix('bingo_')] = [dict(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
    finally:
        db.rollback()
    with transaction() as db:
        audit(db, g.user['id'], 'bingo_export', detail='賓果完整紀錄，不含票券憑證')
    return Response(json.dumps(data, ensure_ascii=False, indent=2), mimetype='application/json',
                    headers={'Content-Disposition':'attachment; filename="bingo-history.json"'})


def init_app(app):
    service.initialize(app)
    app.register_blueprint(bp)
    app.config['BINGO_INTEGRATED'] = True
