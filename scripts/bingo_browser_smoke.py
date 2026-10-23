"""Bingo integration through real HTTP and normal UI login; temporary DBs only."""
import json
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    output = Path('test-results')
    output.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='qrcode-bingo-browser-') as directory, sync_playwright() as playwright:
        for engine, device in [('chromium', 'Pixel 7'), ('webkit', 'iPhone 13')]:
            app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'DATABASE': str(Path(directory) / (engine+'.sqlite3')),
                              'SECRET_KEY': 'bingo-browser-fixture-secret-000000000000', 'EVENT_NAME': '整合測試晚宴',
                              'PUBLIC_BASE_URL': 'http://127.0.0.1', 'INITIAL_ADMIN_USERNAME': '', 'INITIAL_ADMIN_PASSWORD_HASH': ''})
            password = 'bingo-browser-fixture-password!'
            with app.app_context(), transaction() as db:
                hashed = hash_password(password)
                for username, role in [('bingo-admin','admin'),('bingo-staff','staff')]:
                    db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)', (username,hashed,role,now()))
                insert_attendee(db, {'attendee_id':'JOIN001','name':'整合來賓一','affiliation':'測試系所','table_no':'03'})
                token = db.execute('SELECT token FROM tickets WHERE attendee_pk=1').fetchone()[0]
            server = make_server('127.0.0.1',0,app,threaded=True,request_handler=QuietHandler)
            base = f'http://127.0.0.1:{server.server_port}'
            app.config['PUBLIC_BASE_URL'] = base
            threading.Thread(target=server.serve_forever,daemon=True).start()
            browser = getattr(playwright,engine).launch()
            errors, urls = [], []

            def context(mobile=False):
                result = browser.new_context(locale='zh-TW', **(playwright.devices[device] if mobile else {'viewport':{'width':1522,'height':1000}}))
                result.on('page',lambda page:page.on('pageerror',lambda error:errors.append(str(error))))
                result.on('request',lambda request:urls.append(request.url))
                return result

            def login(page, role):
                page.goto(base+'/login')
                page.get_by_label('帳號',exact=True).fill('bingo-'+role)
                page.get_by_label('密碼',exact=True).fill(password)
                page.get_by_role('button',name='登入',exact=True).click()
                page.wait_for_url('**/admin' if role=='admin' else '**/staff')

            try:
                host = context().new_page()
                login(host,'admin')
                host.get_by_role('link',name='賓果主持台',exact=True).click()
                expect(host.locator('#registration-button')).to_have_text('開放領卡')
                expect(host.locator('#prize-count')).to_have_value('10')
                host.get_by_label('本局獎品份數',exact=True).fill('3')
                host.get_by_role('button',name='儲存獎品份數',exact=True).click()
                expect(host.locator('#stat-winners')).to_have_text('0 / 3')
                host.reload()
                expect(host.locator('#prize-count')).to_have_value('3')
                host.locator('#registration-button').click()
                expect(host.locator('#registration-button')).to_have_text('關閉領卡')

                player_context = context(mobile=True)
                player = player_context.new_page()
                player.goto(base+'/ticket#'+token)
                expect(player.locator('#ticket-name')).to_have_text('整合來賓一')
                expect(player.locator('#join-bingo')).to_be_hidden()
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0

                staff = context(mobile=True).new_page()
                login(staff,'staff')
                staff.locator('#search-input').fill('JOIN001')
                staff.get_by_role('button',name='搜尋',exact=True).click()
                staff.locator('.search-result').click()
                staff.get_by_role('button',name='確認報到',exact=True).click()
                expect(staff.locator('#result-label')).to_have_text('✓ 報到成功')
                player.locator('#refresh-ticket').click()
                expect(player.locator('#join-bingo')).to_be_visible()
                player.locator('#join-bingo').click()
                player.wait_for_url('**/bingo')
                expect(player.locator('.bingo-cell')).to_have_count(25)
                expect(player.locator('#player-greeting')).to_have_text('整合來賓一的賓果卡')
                expect(player.locator('#prize-rule')).to_have_text('本局共 3 份獎品，每人每局限得獎一次。')
                player.locator('.bingo-cell[data-index="0"]').click()
                expect(player.locator('#save-status')).to_have_text('已保存')
                player.reload()
                expect(player.locator('.bingo-cell[data-index="0"]')).to_have_attribute('aria-pressed','true')
                assert player.evaluate('document.documentElement.scrollWidth <= innerWidth')
                player.screenshot(path=str(output/(engine+'-bingo-player.png')),full_page=True)
                with app.app_context():
                    original = dict(get_db().execute('SELECT * FROM bingo_cards').fetchone())

                display = context().new_page()
                display.goto(base+'/bingo/display')
                expect(display.locator('#display-players')).to_have_text('1')
                expect(display.locator('#display-winner-count')).to_have_text('0 / 3')
                host.bring_to_front()
                expect(host.locator('#draw-button')).to_be_enabled()
                host.locator('#prize-count').fill('4')
                host.wait_for_timeout(2200)  # At least one automatic state refresh must preserve the draft.
                expect(host.locator('#prize-count')).to_have_value('4')
                expect(host.locator('#draw-button')).to_be_disabled()
                host.locator('#prize-count').fill('3')
                expect(host.locator('#draw-button')).to_be_enabled()
                host.set_viewport_size({'width':390,'height':844})
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.screenshot(path=str(output/(engine+'-bingo-prizes-mobile.png')),full_page=True)
                host.set_viewport_size({'width':1522,'height':1000})
                host.locator('#draw-button').click()
                expect(host.locator('#draw-stage')).to_have_class('draw-stage draw-stage-host is-rolling')
                expect(host.locator('#draw-button')).to_be_disabled()
                host.screenshot(path=str(output/(engine+'-bingo-host-animation.png')),full_page=True)
                expect(host.locator('#history-count')).to_have_text('1 / 49',timeout=10000)
                expect(host.locator('#prize-count')).to_be_disabled()
                display.bring_to_front()
                expect(display.locator('#display-drawn')).to_have_text('1',timeout=10000)
                display.screenshot(path=str(output/(engine+'-bingo-display.png')),full_page=True)

                recovered_context = context(mobile=True)
                recovered = recovered_context.new_page()
                recovered.goto(base+'/ticket#'+token)
                expect(recovered.locator('#join-bingo')).to_be_visible()
                recovered.locator('#join-bingo').click()
                recovered.wait_for_url('**/bingo')
                expect(recovered.locator('.bingo-cell')).to_have_count(25)
                expect(recovered.locator('.bingo-cell[data-index="0"]')).to_have_attribute('aria-pressed','true')
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 1
                    assert get_db().execute('SELECT numbers FROM bingo_cards').fetchone()[0] == original['numbers']
                    ticket_id = get_db().execute('SELECT id FROM tickets WHERE revoked_at IS NULL').fetchone()[0]
                # Use normal authenticated form POST for reissue, not a simulated session.
                host.bring_to_front()
                host.goto(base+'/admin/attendees/1')
                csrf = host.locator('meta[name="csrf-token"]').get_attribute('content')
                response = host.request.post(base+'/admin/attendees/1/reissue',form={'csrf_token':csrf,'reason':'瀏覽器整合測試補發',
                                                                                 'confirm':'yes','ticket_id':str(ticket_id)})
                assert response.ok
                recovered.bring_to_front()
                expect(recovered.locator('#card-panel')).to_be_hidden(timeout=10000)
                with app.app_context():
                    replacement = get_db().execute('SELECT token FROM tickets WHERE revoked_at IS NULL').fetchone()[0]
                recovered.goto(base+'/ticket#'+replacement)
                expect(recovered.locator('#join-bingo')).to_be_visible()
                recovered.locator('#join-bingo').click()
                recovered.wait_for_url('**/bingo')
                expect(recovered.locator('.bingo-cell')).to_have_count(25)
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 1
                    assert get_db().execute('SELECT numbers FROM bingo_cards').fetchone()[0] == original['numbers']
                host.bring_to_front()
                host.goto(base+'/bingo/host')
                expect(host.locator('#prize-count')).to_have_value('3')
                expect(host.locator('#prize-count')).to_be_disabled()
                host.locator('#reset-button').click()
                expect(host.get_by_label('下一局獎品份數',exact=True)).to_have_value('3')
                host.set_viewport_size({'width':390,'height':844})
                assert host.evaluate('document.documentElement.scrollWidth <= innerWidth')
                host.screenshot(path=str(output/(engine+'-bingo-next-game-prizes.png')),full_page=True)
                host.locator('#cancel-reset').click()
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_games').fetchone()[0] == 1
                host.locator('#reset-button').click()
                host.get_by_label('下一局獎品份數',exact=True).fill('201')
                host.get_by_label('請輸入「開啟下一局」',exact=True).fill('開啟下一局')
                host.locator('#reset-submit').click()
                expect(host.locator('#reset-dialog')).to_be_visible()
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_games').fetchone()[0] == 1
                host.get_by_label('下一局獎品份數',exact=True).fill('2')
                host.locator('#reset-submit').click()
                expect(host.locator('#reset-dialog')).not_to_be_visible()
                expect(host.locator('#stat-winners')).to_have_text('0 / 2')
                expect(host.locator('#prize-count')).to_be_enabled()
                recovered.bring_to_front()
                expect(recovered.locator('#game-badge')).to_have_text('第 2 局',timeout=10000)
                expect(recovered.locator('#prize-rule')).to_have_text('本局共 2 份獎品，每人每局限得獎一次。')
                display.bring_to_front()
                expect(display.locator('#display-winner-count')).to_have_text('0 / 2',timeout=10000)
                host.bring_to_front()
                host.locator('#game-select').select_option('1')
                expect(host.locator('#prize-count')).to_have_value('3')
                expect(host.locator('#prize-count')).to_be_disabled()
                host.locator('#game-select').select_option('2')
                expect(host.locator('#prize-count')).to_have_value('2')
                expect(host.locator('#prize-count')).to_be_enabled()
                assert all(token not in url and replacement not in url for url in urls)
                assert not errors, errors
                results.append({'browser':engine,'device_emulation':device,'checkin_ticket_claim':'PASS','no_claim_before_checkin':'PASS',
                                'one_card_across_devices':'PASS','marks_persist':'PASS','animation_and_projection':'PASS',
                                'prize_count_settings_and_lock':'PASS','draft_survives_polling':'PASS','next_game_prizes_and_history':'PASS',
                                'prize_settings_mobile_layout':'PASS',
                                'reissue_revokes_session_and_recovers_card':'PASS','no_token_in_http_url':'PASS','javascript_errors':errors})
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
    (output/'bingo-browser-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
