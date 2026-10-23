import json

from flask import Blueprint, Response, abort, current_app, g, jsonify, render_template, request

from ..db import audit, get_db, transaction
from ..security import csrf_token, require_role
from ..views import body, csv_response, local_time
from . import service

bp = Blueprint('raffle', __name__)


@bp.get('/raffle/host')
@require_role('admin')
def host():
    return render_template('raffle/host.html')


@bp.get('/raffle/display')
def display():
    return render_template('raffle/display.html')


@bp.get('/api/raffle/display-state')
def display_state():
    return jsonify(service.snapshot())


@bp.get('/api/raffle/admin/state')
@require_role('admin')
def state():
    round_id = request.args.get('round', type=int)
    if request.args.get('round') and (not round_id or round_id < 1):
        abort(400)
    result = service.snapshot(admin=True, round_id=round_id)
    result.update(csrf=csrf_token(), demo=current_app.config['CHECKIN_ENV'] == 'demo')
    return jsonify(result)


@bp.post('/api/raffle/admin/<action>')
@require_role('admin')
def action(action):
    data = body()
    round_id = service.integer(data.get('round_id'), '輪次')
    result = None
    if action == 'configure':
        service.configure(g.user, round_id, data)
    elif action == 'prize':
        service.prize(g.user, round_id, data)
    elif action in ('draw', 'redraw'):
        result = service.draw(g.user, round_id, data, replacement=action == 'redraw')
    elif action == 'claimed':
        service.claimed(g.user, data)
    elif action == 'round':
        service.new_round(g.user, round_id, data)
    else:
        abort(404)
    # Read after commit; only a committed draw can be reported as successful.
    response = service.snapshot(admin=True)
    response.update(csrf=csrf_token(), result=result)
    return jsonify(response)


@bp.post('/raffle/export/<kind>')
@require_role('admin')
def export(kind):
    if kind not in ('csv','json'):
        abort(404)
    db = get_db()
    db.execute('BEGIN')
    try:
        if kind == 'json':
            data = {'format_version':1}
            for table in ('raffle_rounds','raffle_prizes','raffle_draws','raffle_winners'):
                data[table] = [dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY id')]
            response = Response(json.dumps(data,ensure_ascii=False,indent=2),mimetype='application/json',
                                headers={'Content-Disposition':'attachment; filename="raffle-history.json"'})
        else:
            rows = db.execute('''SELECT r.title,p.name AS prize_name,w.*,d.drawn_at,d.operator_name FROM raffle_winners w
                JOIN raffle_rounds r ON r.id=w.round_id JOIN raffle_prizes p ON p.id=w.prize_id
                JOIN raffle_draws d ON d.id=w.draw_id ORDER BY w.id''').fetchall()
            response = csv_response('raffle-winners.csv',
                ['輪次','獎品','紀錄編號','參加者編號','姓名','桌次','狀態','抽出時間','操作者','領獎時間','撤銷時間','重抽原因','替補原紀錄'],
                [[row['title'],row['prize_name'],row['id'],row['attendee_id'],row['name'],
                  row['table_no'],'已撤銷' if row['voided_at'] else '已領獎' if row['claimed_at'] else '待領獎',
                  local_time(row['drawn_at']),row['operator_name'],local_time(row['claimed_at']),
                  local_time(row['voided_at']),row['void_reason'],row['replaces_id']] for row in rows])
    finally:
        db.rollback()
    with transaction() as db:
        audit(db,g.user['id'],'raffle_export',detail=kind)
    return response


def init_app(app):
    service.initialize(app)
    app.register_blueprint(bp)
    app.config['RAFFLE_INTEGRATED'] = True
