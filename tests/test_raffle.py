import csv
import io
import json
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.services import insert_attendee
from conftest import PASSWORD, client_for, perform_checkin, post, ticket


RULES = {'allow_repeat':False, 'include_previous':False, 'exclude_bingo':False}


def state(app, rid=None):
    response = client_for(app,1).get('/api/raffle/admin/state'+(f'?round={rid}' if rid else ''))
    assert response.status_code == 200
    return response.json


def action(app, action_name, **data):
    return post(client_for(app,1),'/api/raffle/admin/'+action_name,json={'round_id':1,**data})


def ready(app, count=2, **rules):
    with app.app_context(), transaction() as db:
        for index in range(3,count+1):
            insert_attendee(db,{'attendee_id':f'R{index:03}','name':f'抽獎來賓{index}', 'affiliation':'', 'table_no':'02'})
    for pk in range(1,count+1):
        assert perform_checkin(client_for(app,2),pk=pk).status_code == 201
    assert action(app,'configure',title='一般抽獎',**{**RULES,**rules}).status_code == 200


def prize(app, quantity=1, name='晚宴禮券', rid=1):
    response=action(app,'prize',round_id=rid,name=name,quantity=quantity)
    assert response.status_code == 200
    return response.json['prizes'][-1]['id']


def draw(app, pid, count=1, request_id=None, **data):
    return action(app,'draw',prize_id=pid,count=count,request_id=request_id or secrets.token_hex(24),**data)


def next_round(app, **rules):
    response=action(app,'round',title='加碼抽獎',confirmation='開啟新一輪抽獎',**{**RULES,**rules})
    assert response.status_code == 200
    return response.json


def test_only_checked_in_attendees_are_drawn_and_get_is_read_only(app):
    assert state(app)['eligible_count'] == 0
    pid=prize(app)
    assert draw(app,pid).status_code == 409
    for path in ('/raffle/display','/api/raffle/display-state','/api/raffle/admin/draw'):
        assert client_for(app,1).get(path).status_code in (200,405)
    assert state(app)['draws'] == []
    assert perform_checkin(client_for(app,2),pk=1).status_code == 201
    result=draw(app,pid)
    assert result.status_code == 200
    assert [w['attendee_pk'] for w in result.json['winners']] == [1]
    assert result.json['round']['status'] == 'playing'


def test_default_no_repeat_applies_across_all_prizes(app):
    ready(app)
    first=prize(app,1)
    second=prize(app,2)
    assert draw(app,first).status_code == 200
    result=draw(app,second)
    assert result.status_code == 200
    assert len({w['attendee_pk'] for w in result.json['winners']}) == 2
    assert result.json['eligible_count'] == 0
    assert draw(app,second).status_code == 409


def test_repeat_allows_later_draws_but_never_duplicates_within_one_batch(app):
    ready(app,allow_repeat=True)
    pid=prize(app,4)
    for _ in range(2):
        result=draw(app,pid,count=2)
        assert result.status_code == 200
        assert len(set(result.json['draws'][0]['selected'])) == 2
    assert len(result.json['winners']) == 4
    assert {w['attendee_pk'] for w in result.json['winners']} == {1,2}
    assert draw(app,pid).status_code == 409


def test_previous_round_winners_and_current_round_repeat_are_independent(app):
    ready(app)
    pid=prize(app,2)
    assert draw(app,pid,2).status_code == 200
    assert next_round(app)['eligible_count'] == 0
    assert action(app,'configure',round_id=2,title='加碼',**{**RULES,'include_previous':True}).status_code == 200
    pid=prize(app,2,rid=2)
    assert draw(app,pid,round_id=2).status_code == 200
    assert state(app)['eligible_count'] == 1
    assert draw(app,pid,round_id=2).status_code == 200
    assert len({w['attendee_pk'] for w in state(app)['winners']}) == 2
    assert len(state(app,1)['winners']) == 2
    assert action(app,'configure',title='舊局',**RULES).status_code == 409


def test_bingo_winners_are_allowed_by_default_and_can_be_excluded(app):
    ready(app)
    with app.app_context(),transaction() as db:
        db.execute('INSERT INTO bingo_winners(game_id,attendee_pk,round,position) VALUES (1,1,5,1)')
    assert state(app)['eligible_count'] == 2
    assert action(app,'configure',title='排除賓果',**{**RULES,'exclude_bingo':True}).status_code == 200
    assert state(app)['eligible_count'] == 1
    pid=prize(app)
    assert draw(app,pid).json['winners'][0]['attendee_pk'] == 2


def test_undo_checkin_immediately_removes_eligibility_without_removing_old_awards(app):
    ready(app,allow_repeat=True)
    pid=prize(app,4)
    assert draw(app,pid,2).status_code == 200
    with app.app_context(),transaction() as db:
        db.execute('UPDATE checkins SET undone_at=?,undone_by=1,undo_reason=? WHERE attendee_pk=1',(now(),'測試撤回'))
    result=draw(app,pid)
    assert result.status_code == 200 and result.json['draws'][0]['selected'] == [2]
    assert any(w['attendee_pk']==1 for w in result.json['winners'])


def test_rules_lock_after_draw_while_prizes_can_be_added_and_expanded(app):
    ready(app,3)
    pid=prize(app)
    assert draw(app,pid).status_code == 200
    assert action(app,'configure',title='改規則',**{**RULES,'allow_repeat':True}).status_code == 409
    assert action(app,'prize',prize_id=pid,name='追加禮券',quantity=2).status_code == 200
    assert draw(app,pid).status_code == 200
    assert action(app,'prize',prize_id=pid,name='減少禮券',quantity=1).status_code == 409
    assert prize(app,name='臨時加碼獎') != pid
    assert state(app)['prizes'][0]['quantity'] == 2


@pytest.mark.parametrize('quantity',[None,0,-1,201,True,False,1.5,'3',[],{}])
def test_invalid_prize_quantities_make_no_changes(app,quantity):
    assert action(app,'prize',name='獎品',quantity=quantity).status_code == 400
    assert state(app)['prizes'] == []


@pytest.mark.parametrize('name',['','   ','x'*101,'獎\n品',None,123])
def test_invalid_prize_names_are_rejected(app,name):
    assert action(app,'prize',name=name,quantity=1).status_code == 400


@pytest.mark.parametrize('flag',list(RULES))
@pytest.mark.parametrize('value',[None,0,'true'])
def test_rules_require_real_booleans(app,flag,value):
    assert action(app,'configure',title='設定',**{**RULES,flag:value}).status_code == 400


def test_short_pool_or_insufficient_prizes_cannot_partially_award(app):
    ready(app)
    pid=prize(app,3)
    assert draw(app,pid,3).status_code == 409
    assert draw(app,pid,4).status_code == 409
    assert state(app)['draws'] == [] and state(app)['round']['status'] == 'waiting'


def test_full_200_person_batch_has_unique_winners_and_preserves_candidate_pool(app):
    ready(app,200)
    pid=prize(app,200)
    result=draw(app,pid,200)
    assert result.status_code == 200
    assert len(result.json['winners']) == 200
    assert len(set(result.json['draws'][0]['selected'])) == 200
    assert set(result.json['draws'][0]['candidates']) == set(range(1,201))
    assert result.json['eligible_count'] == 0


def test_same_request_retry_after_commit_does_not_award_again(app):
    ready(app)
    pid=prize(app,2)
    key=secrets.token_hex(24)
    first=draw(app,pid,request_id=key)
    retried=draw(app,pid,request_id=key)
    assert first.status_code == retried.status_code == 200
    assert retried.json['result']['replayed'] is True
    assert retried.json['result']['draw_id'] == first.json['result']['draw_id']
    assert len(retried.json['winners']) == len(retried.json['draws']) == 1
    assert draw(app,pid,count=2,request_id=key).status_code == 409
    next_round(app)
    assert draw(app,pid,request_id=key).status_code == 200
    assert len(state(app,1)['winners']) == 1


@pytest.mark.parametrize('shared_key',[True,False])
def test_parallel_draws_do_not_overaward_or_repeat(app,shared_key):
    ready(app)
    pid=prize(app)
    key=secrets.token_hex(24)
    gate=Barrier(8)
    def run(_):
        gate.wait()
        return draw(app,pid,request_id=key if shared_key else None).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses=list(pool.map(run,range(8)))
    assert statuses.count(200) == (8 if shared_key else 1)
    assert len(state(app)['winners']) == len(state(app)['draws']) == 1


def redraw(app,winner,key=None,**data):
    return action(app,'redraw',prize_id=winner['prize_id'],winner_id=winner['id'],count=1,
                  reason='得主放棄',confirm=True,request_id=key or secrets.token_hex(24),**data)


def test_redraw_preserves_original_and_reason_and_awards_one_replacement(app):
    ready(app)
    pid=prize(app)
    original=draw(app,pid).json['winners'][0]
    key=secrets.token_hex(24)
    result=redraw(app,original,key)
    assert result.status_code == 200
    active=[w for w in result.json['winners'] if not w['voided_at']]
    old=[w for w in result.json['winners'] if w['voided_at']][0]
    assert len(active) == 1 and active[0]['attendee_pk'] != original['attendee_pk']
    assert active[0]['replaces_id'] == original['id']
    assert old['void_reason'] == '得主放棄' and old['voided_by'] == 1
    assert result.json['prizes'][0]['awarded'] == 1
    assert redraw(app,original,key).json['result']['replayed'] is True
    assert redraw(app,original).status_code == 409
    assert action(app,'claimed',winner_id=old['id'],claimed=True).status_code == 409


def test_failed_redraw_does_not_void_original_and_claimed_award_requires_unclaim(app):
    ready(app,1)
    pid=prize(app)
    original=draw(app,pid).json['winners'][0]
    assert redraw(app,original).status_code == 409
    assert not state(app)['winners'][0]['voided_at']
    assert len(state(app)['draws']) == 1
    assert action(app,'claimed',winner_id=original['id'],claimed=True).status_code == 200
    assert redraw(app,original).status_code == 409
    assert action(app,'claimed',winner_id=original['id'],claimed=False).status_code == 200
    assert perform_checkin(client_for(app,2),pk=2).status_code == 201
    assert redraw(app,original).status_code == 200


def test_parallel_redraws_replace_only_once(app):
    ready(app,3)
    pid=prize(app)
    original=draw(app,pid).json['winners'][0]
    gate=Barrier(6)
    def run(_):
        gate.wait()
        return redraw(app,original).status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses=list(pool.map(run,range(6)))
    assert statuses.count(200) == 1 and statuses.count(409) == 5
    assert len(state(app)['winners']) == 2
    assert len([w for w in state(app)['winners'] if not w['voided_at']]) == 1


def test_redraw_requires_confirmation_and_reason(app):
    ready(app)
    original=draw(app,prize(app)).json['winners'][0]
    for data in ({'confirm':False,'reason':'測試'},{'confirm':True,'reason':''}):
        response=action(app,'redraw',prize_id=original['prize_id'],winner_id=original['id'],count=1,
                        request_id=secrets.token_hex(24),**data)
        assert response.status_code == 400
    assert not state(app)['winners'][0]['voided_at']


@pytest.mark.parametrize('path', ['/raffle/host','/api/raffle/admin/state'])
def test_host_and_full_state_are_admin_only(app,path):
    assert client_for(app,2).get(path).status_code == 403
    assert client_for(app).get(path).status_code in (302,401)


@pytest.mark.parametrize('path,data',[
    ('/api/raffle/admin/configure',{'title':'測試',**RULES}),
    ('/api/raffle/admin/prize',{'name':'測試','quantity':1}),
    ('/api/raffle/admin/draw',{'prize_id':1,'count':1,'request_id':'a'*32}),
    ('/api/raffle/admin/redraw',{'winner_id':1,'reason':'測試','confirm':True}),
    ('/api/raffle/admin/claimed',{'winner_id':1,'claimed':True}),
    ('/api/raffle/admin/round',{'title':'加碼','confirmation':'開啟新一輪抽獎',**RULES}),
    ('/raffle/export/csv',{}),('/raffle/export/json',{})])
def test_all_mutations_and_exports_require_admin_and_csrf(app,path,data):
    data={'round_id':1,**data}
    assert post(client_for(app,2),path,json=data).status_code == 403
    assert post(client_for(app),path,json=data).status_code == 401
    assert client_for(app,1).post(path,json=data).status_code == 403


def test_public_projection_never_exposes_roster_or_candidate_pool(app):
    ready(app)
    assert draw(app,prize(app)).status_code == 200
    response=client_for(app).get('/api/raffle/display-state')
    assert response.status_code == 200
    assert '同名來賓' not in response.text and ticket(app) not in response.text
    assert all(key not in response.json for key in ('draws','rounds','attendees','users','tickets'))
    assert response.json['winners'][0]['card_label'].startswith('P')
    assert set(response.json['latest_draw']) == {'id','drawn_at','prize_name','count'}
    assert response.json['latest_draw']['count'] == response.json['draw_count'] == 1
    assert all(key not in response.json['winners'][0] for key in ('attendee_id','attendee_pk','table_no','operator_name'))
    assert response.headers['Cache-Control'].startswith('no-store')


def test_restart_export_and_roster_edit_preserve_winning_identity_and_history(app):
    ready(app,1)
    pid=prize(app,name='=禮券')
    winner=draw(app,pid).json['winners'][0]
    assert action(app,'claimed',winner_id=winner['id'],claimed=True).status_code == 200
    with app.app_context(),transaction() as db:
        db.execute("UPDATE attendees SET name='更新姓名',table_no='99' WHERE id=1")
    restarted=create_app(dict(app.config))
    assert state(restarted)['winners'][0]['name'] == '同名來賓'
    assert state(restarted)['winners'][0]['claimed_at']
    exported=post(client_for(app,1),'/raffle/export/csv',data={})
    rows=list(csv.reader(io.StringIO(exported.data.decode('utf-8-sig'))))
    assert rows[1][1] == "'=禮券" and rows[1][4] == '同名來賓' and rows[1][5] == '01'
    assert '+08:00' in rows[1][7]
    exported=post(client_for(app,1),'/raffle/export/json',data={})
    assert exported.status_code == 200 and len(exported.json['raffle_draws']) == 1
    assert ticket(app) not in exported.text
    assert all(key not in exported.json for key in ('tickets','users','settings'))


def test_new_round_confirmation_and_stale_round_protect_existing_data(app):
    ready(app)
    pid=prize(app)
    assert action(app,'round',title='加碼',confirmation='錯誤',**RULES).status_code == 400
    assert state(app)['round']['status'] == 'waiting'
    next_round(app)
    assert draw(app,pid).status_code == 409
    assert action(app,'round',title='加碼',confirmation='開啟新一輪抽獎',**RULES).status_code == 409
    assert len(state(app)['rounds']) == 2


def test_backup_restore_includes_raffle_and_legacy_backup_upgrades(app,tmp_path):
    ready(app)
    assert draw(app,prize(app)).status_code == 200
    original=state(app)['winners']
    runner=app.test_cli_runner()
    saved=tmp_path/'raffle.sqlite3'
    assert runner.invoke(args=['backup-db',str(saved)]).exit_code == 0
    next_round(app)
    assert runner.invoke(args=['restore-db',str(saved),'--confirm-stopped']).exit_code == 0
    admin=client_for(app)
    assert post(admin,'/login',data={'username':'admin','password':PASSWORD}).status_code == 302
    assert admin.get('/api/raffle/admin/state').json['winners'] == original
    with sqlite3.connect(saved) as db:
        for table in ('raffle_winners','raffle_draws','raffle_prizes','raffle_rounds'):
            db.execute('DROP TABLE '+table)
    assert runner.invoke(args=['restore-db',str(saved),'--confirm-stopped']).exit_code == 0
    upgraded=create_app(dict(app.config))
    with upgraded.app_context():
        assert get_db().execute('SELECT COUNT(*) FROM raffle_rounds').fetchone()[0] == 1
        assert get_db().execute('SELECT COUNT(*) FROM raffle_winners').fetchone()[0] == 0
        assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 2


def test_partial_raffle_backup_is_rejected_without_overwriting_database(app,tmp_path):
    saved=tmp_path/'incomplete.sqlite3'
    runner=app.test_cli_runner()
    assert runner.invoke(args=['backup-db',str(saved)]).exit_code == 0
    with sqlite3.connect(saved) as db:
        db.execute('DROP TABLE raffle_winners')
    response=runner.invoke(args=['restore-db',str(saved),'--confirm-stopped'])
    assert response.exit_code != 0 and '抽獎資料表不完整' in response.output
    assert state(app)['round']['id'] == 1
