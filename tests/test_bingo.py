import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest

from checkin import create_app
from checkin.bingo.rules import completed_lines, make_card
from checkin.db import get_db, now, transaction
from checkin.services import insert_attendee, new_ticket
from conftest import PASSWORD, client_for, perform_checkin, post, ticket


def host_state(app, game=None):
    path = '/api/bingo/admin/state' + (f'?game={game}' if game else '')
    response = client_for(app, 1).get(path)
    assert response.status_code == 200
    return response.json


def action(app, name, **data):
    return post(client_for(app, 1), '/api/bingo/admin/' + name, json={'game_id': 1, **data})


def enroll(app, count=2):
    with app.app_context(), transaction() as db:
        for index in range(3, count + 1):
            insert_attendee(db, {'attendee_id': f'BG{index:03}', 'name': f'賓果來賓{index:03}', 'affiliation': '', 'table_no': '02'})
    assert action(app, 'registration', open=True).status_code == 200
    players = []
    for pk in range(1, count + 1):
        assert perform_checkin(client_for(app, 2), pk=pk).status_code == 201
        player = client_for(app)
        response = post(player, '/api/bingo/claim', json={'token': ticket(app, pk)})
        assert response.status_code == 200
        players.append(player)
    return players


def arrange(app, player_ids, first_row):
    values = list(first_row) + list(range(11, 30))
    values.insert(12, 0)
    assert len(values) == 25 and len(set(values)) == 25
    with app.app_context(), transaction() as db:
        for pk in player_ids:
            db.execute('UPDATE bingo_cards SET numbers=? WHERE game_id=1 AND attendee_pk=?', (json.dumps(values), pk))


def draw_number(app, number):
    with patch('checkin.bingo.service.secrets.choice', return_value=number):
        response = action(app, 'draw', expected_round=len(host_state(app)['draws']))
    assert response.status_code == 200
    return response.json


def test_rules_retain_5x5_free_center_and_all_twelve_lines():
    card = make_card()
    assert len(card) == 25 and card[12] == 0 and len(set(card)) == 25
    assert all(1 <= value <= 49 for value in card if value)
    assert completed_lines(card, set()) == []
    assert len(completed_lines(card, set(range(1, 50)))) == 12


def test_only_checked_in_valid_ticket_can_claim_and_get_cannot_claim(app):
    token = ticket(app)
    player = client_for(app)
    assert post(player, '/api/bingo/claim', json={'token': token}).status_code == 403
    assert perform_checkin(client_for(app, 2), token=token).status_code == 201
    assert post(player, '/api/bingo/claim', json={'token': token}).status_code == 409
    assert action(app, 'registration', open=True).status_code == 200
    assert player.get('/api/bingo/claim?token=invalid').status_code == 405
    assert post(player, '/api/bingo/claim', json={'token': 'x' * 43}).status_code == 404
    response = post(player, '/api/bingo/claim', json={'token': token})
    assert response.status_code == 200 and response.json['card']['name'] == '同名來賓'
    assert response.json['card']['table_no'] == '01'
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 1


def test_same_attendee_has_one_card_across_tabs_devices_and_closed_registration(app):
    first = enroll(app, 1)[0]
    original = first.get('/api/bingo/state').json['card']
    draw_number(app, 49)
    other_device = client_for(app)
    restored = post(other_device, '/api/bingo/claim', json={'token': ticket(app)})
    assert restored.status_code == 200 and restored.json['card']['id'] == original['id']
    assert restored.json['card']['numbers'] == original['numbers']
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 1


def test_parallel_claims_use_database_uniqueness(app):
    assert perform_checkin(client_for(app, 2), pk=1).status_code == 201
    assert action(app, 'registration', open=True).status_code == 200
    token = ticket(app)
    def claim(_):
        result = post(client_for(app), '/api/bingo/claim', json={'token': token})
        assert result.status_code == 200
        return result.json['card']
    with ThreadPoolExecutor(max_workers=8) as pool:
        cards = list(pool.map(claim, range(8)))
    assert len({card['id'] for card in cards}) == 1
    assert len({tuple(card['numbers']) for card in cards}) == 1


def test_one_browser_cannot_silently_switch_attendees(app):
    players = enroll(app)
    assert post(players[0], '/api/bingo/claim', json={'token': ticket(app, 2)}).status_code == 409
    assert post(players[0], '/api/bingo/leave', json={}).status_code == 200
    assert post(players[0], '/api/bingo/claim', json={'token': ticket(app, 2)}).json['card']['player_id'] == 2


def test_reissued_ticket_revokes_old_token_and_existing_bingo_session(app):
    player = enroll(app, 1)[0]
    original = player.get('/api/bingo/state').json['card']
    old_token = ticket(app)
    with app.app_context(), transaction() as db:
        db.execute('UPDATE tickets SET revoked_at=? WHERE attendee_pk=1', (now(),))
        replacement = new_ticket(db, 1)
    assert 'card' not in player.get('/api/bingo/state').json
    assert post(player, '/api/bingo/marks', json={'game_id': 1, 'marks': [0]}).status_code == 401
    assert post(client_for(app), '/api/bingo/claim', json={'token': old_token}).status_code == 404
    recovered = post(player, '/api/bingo/claim', json={'token': replacement})
    assert recovered.status_code == 200 and recovered.json['card']['id'] == original['id']
    assert recovered.json['card']['numbers'] == original['numbers']


def test_undo_revokes_game_access_and_excludes_future_wins_without_deleting_history(app):
    player = enroll(app)[0]
    arrange(app, [1], [1,2,3,4,5])
    with app.app_context(), transaction() as db:
        db.execute('UPDATE checkins SET undone_at=?,undone_by=1,undo_reason=? WHERE attendee_pk=1', (now(), '測試撤回'))
    assert not player.get('/api/bingo/state').json['authenticated']
    assert post(player, '/api/bingo/claim', json={'token': ticket(app)}).status_code == 403
    for number in range(1,6):
        data = draw_number(app, number)
    assert 1 not in [winner['player_id'] for winner in data['winners']]
    with app.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM bingo_cards WHERE attendee_pk=1').fetchone()[0] == 1
        assert get_db().execute('SELECT COUNT(*) FROM checkins WHERE attendee_pk=1').fetchone()[0] == 1


def test_public_state_never_exposes_roster_or_other_cards(app):
    players = enroll(app)
    arrange(app, [1,2], [1,2,3,4,5])
    for number in range(1,6):
        draw_number(app, number)
    public = client_for(app).get('/api/bingo/state')
    assert '同名來賓' not in public.text
    assert all(key not in public.json for key in ('card','players','tickets','games','round_results'))
    own = players[0].get('/api/bingo/state').json
    assert own['card']['player_id'] == 1
    assert all('table_no' not in winner for winner in own['winners'])
    assert len(host_state(app)['players']) == 2
    assert all(winner['name'] == '同名來賓' for winner in host_state(app)['winners'])
    projection = players[0].get('/api/bingo/display-state')
    assert 'card' not in projection.json and '同名來賓' not in projection.text


@pytest.mark.parametrize('path', ['/bingo/host','/api/bingo/admin/state'])
def test_staff_cannot_host(app, path):
    assert client_for(app, 2).get(path).status_code == 403
    assert client_for(app).get(path).status_code in (302,401)


@pytest.mark.parametrize('path,data', [('/api/bingo/admin/draw',{'game_id':1,'expected_round':0}),
                                     ('/api/bingo/admin/reset',{'game_id':1,'confirmation':'開啟下一局','max_winners':3}),
                                     ('/api/bingo/admin/prizes',{'game_id':1,'max_winners':3}),
                                     ('/api/bingo/admin/registration',{'game_id':1,'open':True}),
                                     ('/api/bingo/admin/claimed',{'game_id':1,'player_id':1,'claimed':True}),
                                     ('/api/bingo/admin-export',{})])
def test_admin_writes_reject_staff_anonymous_and_missing_csrf(app, path, data):
    assert post(client_for(app, 2), path, json=data).status_code == 403
    assert post(client_for(app), path, json=data).status_code == 401
    assert client_for(app, 1).post(path, json=data).status_code == 403


@pytest.mark.parametrize('prize_count', [1, 3, 10, 11, 200])
def test_same_round_candidates_respect_configured_prizes_and_persist_one_lottery(app, prize_count):
    assert action(app, 'prizes', max_winners=prize_count).status_code == 200
    players = enroll(app, 12)
    arrange(app, range(1,13), [1,2,3,4,5])
    assert post(players[0], '/api/bingo/marks', json={'game_id':1,'marks':list(range(25))}).status_code == 200
    assert host_state(app)['winners'] == []
    for number in range(1,6):
        result = draw_number(app, number)
    expected_winners = min(12, prize_count)
    assert result['game']['max_winners'] == prize_count
    assert result['game']['status'] == ('finished' if prize_count <= 12 else 'playing')
    assert len(result['winners']) == expected_winners
    last = result['round_results'][-1]
    assert set(last['candidates']) == set(range(1,13))
    assert set(last['lottery_order']) == set(range(1,13))
    assert last['selected'] == last['lottery_order'][:expected_winners]
    assert all(winner['round'] == 5 for winner in result['winners'])
    assert host_state(app)['round_results'] == result['round_results']
    assert client_for(app).get('/api/bingo/display-state').json['game']['max_winners'] == prize_count
    assert players[0].get('/api/bingo/state').json['game']['max_winners'] == prize_count


def test_prize_settings_preserve_cards_and_record_changes_before_first_draw(app):
    players = enroll(app)
    assert post(players[0], '/api/bingo/marks', json={'game_id':1, 'marks':[0, 3]}).status_code == 200
    original = players[0].get('/api/bingo/state').json['card']
    assert host_state(app)['game']['max_winners'] == 10
    for count in (3, 5, 5):
        assert action(app, 'prizes', max_winners=count).status_code == 200
    assert players[0].get('/api/bingo/state').json['card'] == original
    assert host_state(create_app(dict(app.config)))['game']['max_winners'] == 5
    assert client_for(app, 1).get('/api/bingo/admin/prizes').status_code == 405
    with app.app_context():
        rows = get_db().execute("SELECT actor_id,detail FROM audit_log WHERE action='bingo_prizes' ORDER BY id").fetchall()
        assert [row['actor_id'] for row in rows] == [1, 1]
        assert [json.loads(row['detail']) for row in rows] == [
            {'game_id':1, 'previous_max_winners':10, 'max_winners':3},
            {'game_id':1, 'previous_max_winners':3, 'max_winners':5}]


@pytest.mark.parametrize('name', ['prizes', 'reset'])
@pytest.mark.parametrize('invalid', [None, 0, -1, 201, True, False, 1.5, '3', [], {}])
def test_invalid_prize_counts_do_not_change_or_end_game(app, name, invalid):
    before = host_state(app)
    response = action(app, name, max_winners=invalid, confirmation='開啟下一局')
    assert response.status_code == 400 and '獎品份數' in response.json['error']
    after = host_state(app)
    assert after['games'] == before['games'] and after['revision'] == before['revision']


@pytest.mark.parametrize('name', ['prizes', 'reset'])
def test_prize_count_is_required(app, name):
    assert action(app, name, confirmation='開啟下一局').status_code == 400
    assert host_state(app)['game']['status'] == 'waiting'


def test_prizes_lock_on_first_draw_and_old_games_cannot_be_modified(app):
    assert action(app, 'prizes', max_winners=3).status_code == 200
    enroll(app, 1)
    draw_number(app, 49)
    assert action(app, 'prizes', max_winners=2).status_code == 409
    assert host_state(app)['game']['max_winners'] == 3
    assert action(app, 'reset', confirmation='開啟下一局', max_winners=7).status_code == 200
    assert action(app, 'prizes', max_winners=2).status_code == 409
    assert host_state(app, game=1)['game']['max_winners'] == 3
    assert host_state(app)['game']['max_winners'] == 7


def test_parallel_prize_change_and_first_draw_use_one_transaction_order(app):
    enroll(app, 1)
    gate = Barrier(2)

    def run(name, data):
        gate.wait()
        return action(app, name, **data).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        change = pool.submit(run, 'prizes', {'max_winners':3})
        drawing = pool.submit(run, 'draw', {'expected_round':0})
        assert drawing.result() == 200
        changed = change.result()
    state = host_state(app)
    assert changed in (200, 409)
    assert state['game']['max_winners'] == (3 if changed == 200 else 10)
    assert state['game']['status'] == 'playing' and len(state['draws']) == 1
    assert action(app, 'prizes', max_winners=1).status_code == 409


def test_earlier_round_gets_priority_for_last_prize(app):
    enroll(app, 15)
    arrange(app, range(1,10), [1,2,3,4,5])
    arrange(app, range(10,16), [1,2,3,4,6])
    for number in range(1,7):
        result = draw_number(app, number)
    assert [winner['round'] for winner in result['winners']] == [5]*9+[6]
    assert len(result['round_results'][-1]['candidates']) == 6
    assert len(result['round_results'][-1]['selected']) == 1


def test_parallel_draw_commands_advance_once(app):
    enroll(app, 1)
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: action(app, 'draw', expected_round=0).status_code, range(8)))
    assert responses.count(200) == 1 and responses.count(409) == 7
    assert len(host_state(app)['draws']) == 1


def test_full_game_exhausts_unique_numbers_and_wins_without_marks(app):
    enroll(app, 1)
    for expected in range(49):
        response = action(app, 'draw', expected_round=expected)
        assert response.status_code == 200
    data = response.json
    assert len({draw['number'] for draw in data['draws']}) == 49
    assert data['game']['status'] == 'finished' and len(data['winners']) == 1
    assert action(app, 'draw', expected_round=48).status_code == 409


def test_restart_next_game_and_export_preserve_history_and_one_card(app):
    assert action(app, 'prizes', max_winners=4).status_code == 200
    player = enroll(app, 1)[0]
    arrange(app, [1], [1,2,3,4,5])
    original_numbers = player.get('/api/bingo/state').json['card']['numbers']
    assert post(player, '/api/bingo/marks', json={'game_id':1,'marks':[0,3]}).status_code == 200
    for number in range(1,6):
        before = draw_number(app, number)
    assert action(app, 'claimed', player_id=1, claimed=True).status_code == 200
    restarted = create_app(dict(app.config))
    assert host_state(restarted)['round_results'] == before['round_results']
    assert host_state(restarted)['winners'][0]['claimed_at']
    assert action(app, 'reset', confirmation='wrong', max_winners=2).status_code == 400
    assert action(app, 'reset', confirmation='開啟下一局', max_winners=2).status_code == 200
    new = player.get('/api/bingo/state').json
    assert new['game']['id'] == 2 and not new['game']['registration_open']
    assert new['game']['max_winners'] == 2
    assert host_state(create_app(dict(app.config)))['game']['max_winners'] == 2
    assert new['card']['marks'] == [12] and new['card']['numbers'] != original_numbers
    assert len(host_state(app, game=1)['draws']) == 5
    assert host_state(app, game=1)['game']['max_winners'] == 4
    assert action(app, 'reset', confirmation='開啟下一局', max_winners=2).status_code == 409
    assert post(player, '/api/bingo/marks', json={'game_id':1,'marks':[0]}).status_code == 409
    exported = post(client_for(app, 1), '/api/bingo/admin-export', json={})
    assert exported.status_code == 200 and len(exported.json['games']) == 2
    assert [game['max_winners'] for game in exported.json['games']] == [4, 2]
    with app.app_context():
        detail = get_db().execute("SELECT detail FROM audit_log WHERE action='bingo_next_game'").fetchone()[0]
        assert json.loads(detail)['max_winners'] == 2
    assert all(key not in exported.json for key in ('tickets','sessions','users','settings'))
    assert ticket(app) not in exported.text


def test_backup_restore_covers_bingo_and_revokes_player_session(app, tmp_path):
    assert action(app, 'prizes', max_winners=7).status_code == 200
    player = enroll(app, 1)[0]
    original = player.get('/api/bingo/state').json['card']
    runner = app.test_cli_runner()
    backup = tmp_path / 'with-bingo.sqlite3'
    assert runner.invoke(args=['backup-db',str(backup)]).exit_code == 0
    assert action(app, 'prizes', max_winners=2).status_code == 200
    draw_number(app, 49)
    result = runner.invoke(args=['restore-db',str(backup),'--confirm-stopped'])
    assert result.exit_code == 0, result.output
    assert 'card' not in player.get('/api/bingo/state').json
    recovered = post(player, '/api/bingo/claim', json={'token':ticket(app)})
    assert recovered.status_code == 200 and recovered.json['card']['numbers'] == original['numbers']
    assert recovered.json['draws'] == []
    assert recovered.json['game']['max_winners'] == 7


def test_bingo_pages_and_assets_are_local_and_no_secret_in_public_html(app):
    for path in ('/bingo','/bingo/display','/bingo/join-qr.png'):
        response = client_for(app).get(path)
        assert response.status_code == 200
        assert response.headers['Cache-Control'].startswith('no-store')
        if path.endswith('.png'):
            assert response.mimetype == 'image/png'
        else:
            assert 'cdn.' not in response.text and ticket(app) not in response.text
    host = client_for(app, 1).get('/bingo/host')
    assert host.status_code == 200 and 'demo-host-2026!' not in host.text


def test_restore_rejects_incomplete_bingo_tables(app, tmp_path):
    backup = tmp_path / 'incomplete.sqlite3'
    runner = app.test_cli_runner()
    assert runner.invoke(args=['backup-db', str(backup)]).exit_code == 0
    with sqlite3.connect(backup) as db:
        db.execute('DROP TABLE bingo_cards')
    result = runner.invoke(args=['restore-db', str(backup), '--confirm-stopped'])
    assert result.exit_code != 0 and '賓果資料表不完整' in result.output
    assert host_state(app)['game']['id'] == 1


def test_legacy_checkin_backup_can_restore_and_upgrade_without_changing_tickets(app, tmp_path):
    backup = tmp_path / 'before-bingo.sqlite3'
    runner = app.test_cli_runner()
    original = ticket(app)
    assert runner.invoke(args=['backup-db', str(backup)]).exit_code == 0
    with sqlite3.connect(backup) as db:
        for table in ('bingo_winners', 'bingo_draws', 'bingo_cards', 'bingo_games'):
            db.execute('DROP TABLE ' + table)
        db.execute("DELETE FROM settings WHERE key LIKE 'bingo_%'")
    assert runner.invoke(args=['restore-db', str(backup), '--confirm-stopped']).exit_code == 0
    upgraded = create_app(dict(app.config))
    assert ticket(upgraded) == original
    admin = client_for(upgraded)
    assert post(admin, '/login', data={'username': 'admin', 'password': PASSWORD}).status_code == 302
    assert admin.get('/api/bingo/admin/state').json['game']['id'] == 1
