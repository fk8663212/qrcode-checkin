"""Real HTTP / normal login raffle checks, including lost responses; isolated DBs only."""
import csv
import io
import json
import re
import secrets
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import check_in, insert_attendee


class QuietHandler(WSGIRequestHandler):
    def log_request(self,*args,**kwargs):
        pass


def main():
    output=Path('test-results')
    output.mkdir(exist_ok=True)
    results=[]
    with tempfile.TemporaryDirectory(prefix='qrcode-raffle-browser-') as directory, sync_playwright() as playwright:
        for engine in ('chromium','webkit'):
            app=create_app({'TESTING':True,'CHECKIN_ENV':'test','DATABASE':str(Path(directory)/(engine+'.sqlite3')),
                            'SECRET_KEY':'raffle-browser-fixture-secret-00000000','EVENT_NAME':'抽獎測試晚宴',
                            'PUBLIC_BASE_URL':'http://127.0.0.1','INITIAL_ADMIN_USERNAME':'','INITIAL_ADMIN_PASSWORD_HASH':''})
            password='raffle-browser-fixture-password!'
            with app.app_context(),transaction() as db:
                hashed=hash_password(password)
                for username,role in [('raffle-admin','admin'),('raffle-staff','staff')]:
                    db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',(username,hashed,role,now()))
                for number in range(1,5):
                    insert_attendee(db,{'attendee_id':f'RAFFLE{number:03}','name':f'抽獎來賓{number}','affiliation':'測試單位','table_no':'03'})
                token=db.execute('SELECT token FROM tickets WHERE attendee_pk=1').fetchone()[0]
            with app.app_context():
                staff=dict(get_db().execute("SELECT * FROM users WHERE username='raffle-staff'").fetchone())
                for pk in range(1,4):
                    check_in(staff,secrets.token_hex(24),attendee_pk=pk)
            server=make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
            base=f'http://127.0.0.1:{server.server_port}'
            app.config['PUBLIC_BASE_URL']=base
            threading.Thread(target=server.serve_forever,daemon=True).start()
            browser=getattr(playwright,engine).launch()
            errors=[]
            context=browser.new_context(viewport={'width':1440,'height':1000})
            context.on('page',lambda page:page.on('pageerror',lambda error:errors.append(str(error))))
            host=context.new_page()
            try:
                ticket=browser.new_context(**playwright.devices['iPhone 13' if engine=='webkit' else 'Pixel 7']).new_page()
                ticket.goto(base+'/ticket#'+token)
                expect(ticket.locator('#ticket-raffle-label')).to_have_text('P001')
                assert ticket.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.goto(base+'/login')
                host.get_by_label('帳號',exact=True).fill('raffle-admin')
                host.get_by_label('密碼',exact=True).fill(password)
                host.get_by_role('button',name='登入',exact=True).click()
                host.wait_for_url('**/admin')
                host.get_by_role('link',name='一般抽獎',exact=True).click()
                expect(host.locator('#eligible-count')).to_have_text('3')
                host.get_by_label('輪次名稱',exact=True).fill('一般抽獎 A ')
                host.get_by_role('button',name='儲存本輪規則',exact=True).click()
                expect(host.locator('#round-title')).to_have_text('一般抽獎 A')
                expect(host.locator('#round-name')).to_have_value('一般抽獎 A')

                def add_prize(name,quantity):
                    host.locator('#prize-name').fill(name)
                    host.locator('#prize-quantity').fill(str(quantity))
                    host.get_by_role('button',name='新增獎品',exact=True).click()
                    expect(host.locator('.raffle-prize h3').filter(has_text=name)).to_be_visible()

                add_prize('禮券',2)
                display=browser.new_context(viewport={'width':1522,'height':1000}).new_page()
                display.goto(base+'/raffle/display')
                expect(display.locator('#raffle-display-winners')).to_contain_text('幸運得主即將揭曉')
                host.bring_to_front()
                host.set_viewport_size({'width':390,'height':844})
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.screenshot(path=str(output/(engine+'-raffle-host-mobile.png')),full_page=True)
                host.set_viewport_size({'width':320,'height':740})
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.set_viewport_size({'width':1440,'height':1000})

                # Lost before delivery: no awards, pending identity survives reload.
                host.route('**/api/raffle/admin/draw',lambda route:route.abort('failed'))
                host.get_by_role('button',name='抽出得主',exact=True).click()
                expect(host.locator('#raffle-pending')).to_be_visible()
                expect(host.locator('#raffle-result')).to_be_hidden()
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM raffle_draws').fetchone()[0] == 0
                host.reload()
                expect(host.locator('#raffle-pending')).to_be_visible()
                expect(host.locator('#raffle-retry')).to_be_enabled()
                host.unroute('**/api/raffle/admin/draw')
                # Commit then lose the response: the exact same pending request must be reused.
                def lose_committed_response(route):
                    response=route.fetch()
                    assert response.status == 200
                    route.abort('failed')
                host.route('**/api/raffle/admin/draw',lose_committed_response)
                host.locator('#raffle-retry').click()
                expect(host.locator('#raffle-retry')).to_be_enabled()
                expect(host.locator('#raffle-pending')).to_be_visible()
                expect(host.locator('#raffle-result')).to_be_hidden()
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM raffle_winners').fetchone()[0] == 1
                host.unroute('**/api/raffle/admin/draw')
                host.route('**/api/raffle/admin/draw',lambda route:route.fulfill(status=403,content_type='application/json',body=json.dumps({'error':'測試安全驗證過期'})))
                host.locator('#raffle-retry').click()
                expect(host.locator('#raffle-error')).to_have_text('測試安全驗證過期')
                expect(host.locator('#raffle-pending')).to_be_visible()
                host.unroute('**/api/raffle/admin/draw')
                host.locator('#raffle-retry').click()
                expect(host.locator('#raffle-pending')).to_be_hidden()
                expect(host.locator('#raffle-result')).to_contain_text('已確認抽出')
                expect(host.locator('#round-name')).to_be_disabled()
                host.set_viewport_size({'width':390,'height':844})
                host.get_by_role('button',name='抽出得主',exact=True).click()
                expect(host.locator('#raffle-animation')).to_have_class('raffle-animation is-rolling')
                expect(host.locator('#raffle-result')).to_be_hidden()
                expect(host.locator('.raffle-winner')).to_have_count(1)
                expect(host.get_by_role('button',name='抽出得主',exact=True)).to_be_disabled()
                host.locator('#raffle-animation').screenshot(path=str(output/(engine+'-raffle-animation-rolling.png')))
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                display.bring_to_front()
                display.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(display.locator('#raffle-animation')).to_have_class('raffle-animation is-rolling')
                display.locator('#raffle-animation').screenshot(path=str(output/(engine+'-raffle-animation-projection.png')))
                expect(display.locator('.raffle-display-winner')).to_have_count(2,timeout=10000)
                host.bring_to_front()
                expect(host.locator('.raffle-winner')).to_have_count(2)
                expect(host.locator('#raffle-animation')).to_have_class(re.compile(r'\bis-revealed\b'))
                host.locator('#raffle-animation').screenshot(path=str(output/(engine+'-raffle-animation-revealed.png')))
                host.set_viewport_size({'width':1440,'height':1000})
                with app.app_context():
                    rows=get_db().execute('SELECT attendee_pk FROM raffle_winners').fetchall()
                    assert len({row[0] for row in rows}) == 2
                    original=dict(get_db().execute('SELECT * FROM raffle_winners ORDER BY id LIMIT 1').fetchone())
                expect(host.get_by_role('button',name='抽出得主',exact=True)).to_be_disabled()
                add_prize('現場追加小禮',1)

                original_row=host.locator(f'.raffle-winner[data-winner-id="{original["id"]}"]')
                original_row.get_by_role('button',name='撤銷並重抽',exact=True).click()
                host.locator('#redraw-reason').fill('得主放棄')
                host.locator('#redraw-confirm').check()
                host.get_by_role('button',name='確認重抽',exact=True).click()
                expect(host.locator('.raffle-winner.voided')).to_have_count(1)
                expect(host.locator('.raffle-winner')).to_have_count(3)
                with app.app_context():
                    replacement=dict(get_db().execute('SELECT * FROM raffle_winners WHERE replaces_id=?',(original['id'],)).fetchone())
                    assert replacement['attendee_pk'] != original['attendee_pk']
                host.locator(f'.raffle-winner[data-winner-id="{replacement["id"]}"]').get_by_role('button',name='標記領獎',exact=True).click()
                expect(host.get_by_role('button',name='已領獎 ✓（取消標記）',exact=True)).to_be_visible()

                display.bring_to_front()
                expect(display.locator('.raffle-display-winner')).to_have_count(2)
                assert '抽獎來賓' not in display.locator('#raffle-app').inner_text()
                display.screenshot(path=str(output/(engine+'-raffle-display.png')),full_page=True)
                host.bring_to_front()
                host.locator('#new-round-button').click()
                host.locator('#new-include-previous').check()
                host.locator('#new-round-confirm').fill('確認')
                host.locator('#new-round-submit').click()
                expect(host.locator('#new-round-dialog .raffle-dialog-error')).to_contain_text('請完整輸入')
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM raffle_rounds').fetchone()[0] == 1
                host.locator('#new-round-confirm').fill('開啟新一輪抽獎')
                host.set_viewport_size({'width':390,'height':844})
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.screenshot(path=str(output/(engine+'-raffle-new-round.png')),full_page=True)
                host.locator('#new-round-submit').click()
                expect(host.locator('#new-round-dialog')).not_to_be_visible()
                expect(host.locator('#round-title')).to_have_text('加碼抽獎')
                expect(host.locator('#eligible-count')).to_have_text('3')
                add_prize('加碼獎',2)
                host.emulate_media(reduced_motion='reduce')
                host.locator('.raffle-draw-form input').fill('2')
                host.get_by_role('button',name='抽出得主',exact=True).click()
                expect(host.locator('.raffle-winner')).to_have_count(2)
                expect(host.locator('#raffle-animation')).to_have_class('raffle-animation without-motion is-revealed')
                host.reload()
                expect(host.locator('.raffle-winner')).to_have_count(2)
                expect(host.locator('#raffle-animation')).to_be_hidden()
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(DISTINCT attendee_pk) FROM raffle_winners WHERE round_id=2').fetchone()[0] == 2
                host.locator('#raffle-round').select_option('1')
                expect(host.locator('.raffle-winner')).to_have_count(3)
                expect(host.locator('#round-name')).to_be_disabled()
                with host.expect_download() as downloaded:
                    host.get_by_role('button',name='匯出 CSV',exact=True).click()
                data=Path(downloaded.value.path()).read_text(encoding='utf-8-sig')
                rows=list(csv.reader(io.StringIO(data)))
                assert len(rows) == 6 and any(row[6]=='已撤銷' for row in rows[1:])
                assert not errors, errors
                results.append({'browser':engine,'normal_admin_login':'PASS','checked_in_pool':'PASS','personal_ticket_raffle_label':'PASS',
                                'lost_request_and_lost_response_safe_retry':'PASS','pending_survives_reload':'PASS',
                                'no_repeat_and_rule_lock':'PASS','prize_topup':'PASS','redraw_history_and_claim':'PASS',
                                'bonus_round_previous_winners_and_history':'PASS','public_projection_privacy':'PASS',
                                'csv_download':'PASS','mobile_390_and_320_layout':'PASS','javascript_errors':errors})
                results[-1].update({'rolling_holds_winners_and_disables_next_draw':'PASS','projection_roll_and_reveal':'PASS',
                                    'reduced_motion_direct_reveal':'PASS','reload_does_not_replay':'PASS'})
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
    (output/'raffle-browser-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
