"""Real campus XLSX/CSV import, mixed seating and games; isolated local fixtures."""
import csv
import io
import json
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import WSGIRequestHandler, make_server

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    root = Path(__file__).resolve().parent.parent
    output = root / 'test-results'
    output.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='qrcode-campus-browser-') as directory, sync_playwright() as playwright:
        for engine, device in [('chromium', 'Pixel 7'), ('webkit', 'iPhone 13')]:
            app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'DATABASE': str(Path(directory) / (engine + '.sqlite3')),
                              'SECRET_KEY': 'campus-browser-fixture-secret-000000', 'PUBLIC_BASE_URL': 'http://127.0.0.1',
                              'EVENT_NAME': '導生晚宴 / Campus Dinner', 'INITIAL_ADMIN_USERNAME': '', 'INITIAL_ADMIN_PASSWORD_HASH': ''})
            password = 'campus-fixture-password!'
            with app.app_context(), transaction() as db:
                hashed = hash_password(password)
                for username, role in [('campus-admin', 'admin'), ('campus-staff', 'staff')]:
                    db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                               (username, hashed, role, now()))
            server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
            base = f'http://127.0.0.1:{server.server_port}'
            app.config['PUBLIC_BASE_URL'] = base
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            browser = None
            errors, urls = [], []
            try:
                browser = getattr(playwright, engine).launch()

                def page(locale='zh-TW', mobile=False):
                    context = browser.new_context(locale=locale, **(playwright.devices[device] if mobile else {'viewport': {'width': 1440, 'height': 1000}}))
                    context.on('request', lambda request: urls.append(request.url))
                    context.on('page', lambda tab: tab.on('pageerror', lambda error: errors.append(str(error))))
                    return context.new_page()

                def login(tab, username):
                    tab.goto(base + '/login')
                    tab.get_by_label('帳號', exact=True).fill(username)
                    tab.get_by_label('密碼', exact=True).fill(password)
                    tab.get_by_role('button', name='登入', exact=True).click()
                    tab.wait_for_url('**/admin' if username.endswith('admin') else '**/staff')

                def save_settings(tab):
                    with tab.expect_response(lambda response:response.url==base+'/admin/event-settings' and response.request.method=='POST') as saved:
                        tab.get_by_role('button',name='儲存活動設定',exact=True).click()
                    assert saved.value.status == 302

                def layout(tab, label):
                    for width in [320, 390]:
                        tab.set_viewport_size({'width': width, 'height': 844})
                        assert tab.evaluate('document.documentElement.scrollWidth <= innerWidth'), (engine, label, width)
                    tab.screenshot(path=str(output / f'{engine}-campus-{label}.png'), full_page=True)

                admin = page()
                login(admin, 'campus-admin')
                admin.goto(base + '/admin/event-settings')
                admin.locator('#roster-profile').select_option('campus')
                admin.locator('#seating-mode').select_option('grade')
                admin.locator('[name="default_affiliation"]').fill('同系測試單位 / Test Department')
                save_settings(admin)
                layout(admin, 'settings')
                admin.set_viewport_size({'width': 1440, 'height': 1000})
                admin.goto(base + '/admin/import')
                admin.locator('[name="file"]').set_input_files(str(root / 'examples/attendees-campus.xlsx'))
                admin.get_by_role('button', name='上傳並預覽', exact=True).click()
                expect(admin.locator('.preview-counts strong').first).to_have_text('13')
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 0
                admin.get_by_role('button', name='確認匯入 13 筆', exact=True).click()
                expect(admin.locator('#table-overview-title')).to_have_text('現場區域概況')
                for grade, count in [('碩一', 5), ('碩二', 4), ('博一', 3), ('教職員', 1)]:
                    expect(admin.locator(f'[data-table-no="{grade}"] progress')).to_have_attribute('max', str(count))
                admin.screenshot(path=str(output / (engine + '-campus-overview.png')), full_page=True)
                with app.app_context():
                    token = get_db().execute('SELECT token FROM tickets WHERE attendee_pk=1').fetchone()[0]

                guest = page('en-US', mobile=True)
                guest.goto(base + '/ticket#' + token)
                expect(guest.locator('#ticket-table')).to_have_text('碩一區')
                expect(guest.locator('#ticket-seat-label')).to_have_text('Your seating area')
                expect(guest.locator('#ticket-seat-note')).to_have_text('Choose any seat within your area')
                assert 'guest01@example.org' not in guest.locator('body').inner_text()
                assert 'TEST-STU-001' not in guest.locator('body').inner_text()
                layout(guest, 'english-ticket')
                guest.locator('[data-ui-language="zh-TW"]').click()
                expect(guest.locator('#ticket-seat-label')).to_have_text('您的區域')
                layout(guest, 'chinese-ticket')

                staff = page(mobile=True)
                login(staff, 'campus-staff')
                staff.locator('#search-input').fill('TEST-STU-001')
                staff.get_by_role('button', name='搜尋', exact=True).click()
                staff.locator('.search-result').click()
                expect(staff.locator('#result-table')).to_have_text('碩一區')
                expect(staff.locator('#result-seat-label')).to_have_text('區域')
                staff.get_by_role('button', name='確認報到', exact=True).click()
                expect(staff.locator('#result-label')).to_have_text('✓ 報到成功')
                layout(staff, 'staff-checkin')
                admin.goto(base + '/bingo/host')
                admin.locator('#registration-button').click()
                expect(admin.locator('#registration-button')).to_have_text('關閉領卡')
                guest.locator('#refresh-ticket').click()
                guest.get_by_role('button', name='領取／開啟賓果卡', exact=True).click()
                guest.wait_for_url('**/bingo')
                expect(guest.locator('#player-seating')).to_contain_text('碩一區')
                guest.locator('.bingo-cell[data-index="0"]').click()
                expect(guest.locator('#save-status')).to_have_text('已保存')
                layout(guest, 'bingo-card')
                expect(admin.locator('#ticket-seat-heading')).to_have_text('區域')
                expect(admin.locator('#ticket-table')).to_contain_text('碩一區')

                admin.goto(base + '/raffle/host')
                admin.locator('#prize-name').fill('測試獎品')
                admin.locator('#prize-quantity').fill('1')
                admin.get_by_role('button', name='新增獎品', exact=True).click()
                admin.get_by_role('button', name='抽出得主', exact=True).click()
                expect(admin.locator('#raffle-winners')).to_contain_text('碩一區', timeout=10000)
                layout(admin, 'raffle-region')

                admin.goto(base + '/admin/event-settings')
                admin.locator('#seating-mode').select_option('table_grade')
                save_settings(admin)
                admin.goto(base+'/admin')
                expect(admin.locator('#table-overview-title')).to_have_text('現場桌次／區域概況')
                for key,count in [('table:11–13',4),('table:14–15 桌',3),('grade:碩一',5),('grade:教職員',1)]:
                    expect(admin.locator(f'[data-table-no="{key}"] progress')).to_have_attribute('max',str(count))
                layout(admin,'mixed-overview')
                with app.app_context():
                    table_token=get_db().execute('SELECT token FROM tickets WHERE attendee_pk=6').fetchone()[0]
                    range_token=get_db().execute('SELECT token FROM tickets WHERE attendee_pk=10').fetchone()[0]
                table_guest=page('en-US',mobile=True)
                table_guest.goto(base+'/ticket#'+table_token)
                expect(table_guest.locator('#ticket-seat-label')).to_have_text('Your table')
                expect(table_guest.locator('#ticket-table')).to_have_text('11–13')
                expect(table_guest.locator('#ticket-seat-note')).to_have_text('')
                layout(table_guest,'mixed-table-english')
                range_guest=page(mobile=True)
                range_guest.goto(base+'/ticket#'+range_token)
                expect(range_guest.locator('#ticket-seat-label')).to_have_text('您的桌次')
                expect(range_guest.locator('#ticket-table')).to_have_text('14–15 桌')
                layout(range_guest,'mixed-table-range')
                guest.goto(base+'/ticket#'+token)
                expect(guest.locator('#ticket-table')).to_have_text('碩一區')
                expect(guest.locator('#ticket-seat-label')).to_have_text('您的區域')
                staff.goto(base+'/staff')
                staff.locator('#search-input').fill('TEST-STU-010')
                staff.get_by_role('button',name='搜尋',exact=True).click()
                staff.locator('.search-result').click()
                expect(staff.locator('#result-table')).to_have_text('14–15 桌')
                expect(staff.locator('#result-seat-label')).to_have_text('桌次')
                staff.get_by_role('button',name='確認報到',exact=True).click()
                expect(staff.locator('#result-label')).to_have_text('✓ 報到成功')
                layout(staff,'mixed-staff-checkin')
                range_guest.locator('#refresh-ticket').click()
                range_guest.get_by_role('button',name='領取／開啟賓果卡',exact=True).click()
                range_guest.wait_for_url('**/bingo')
                expect(range_guest.locator('#player-seating')).to_contain_text('14–15 桌')
                admin.goto(base+'/bingo/host')
                expect(admin.locator('#ticket-seat-heading')).to_have_text('桌次／區域')
                expect(admin.locator('#ticket-table')).to_contain_text('14–15 桌')
                admin.goto(base+'/raffle/host')
                admin.locator('#prize-name').fill('混合座位測試獎')
                admin.locator('#prize-quantity').fill('1')
                admin.get_by_role('button',name='新增獎品',exact=True).click()
                admin.locator('.raffle-prize').filter(has_text='混合座位測試獎').get_by_role('button',name='抽出得主',exact=True).click()
                expect(admin.locator('#raffle-winners')).to_contain_text('14–15 桌',timeout=10000)
                assert '14–15 桌 桌' not in admin.locator('#raffle-winners').inner_text()
                layout(admin,'mixed-raffle')
                admin.goto(base+'/admin/import')
                admin.locator('[name="file"]').set_input_files(str(root/'examples/attendees-campus.csv'))
                admin.get_by_role('button',name='上傳並預覽',exact=True).click()
                expect(admin.locator('.preview-counts strong').nth(1)).to_have_text('13')
                expect(admin.locator('th').nth(4)).to_have_text('桌次')
                layout(admin,'mixed-import-preview')
                admin.get_by_role('button',name='確認匯入 13 筆',exact=True).click()
                expect(admin.locator('[data-table-no="table:14–15 桌"] progress')).to_have_attribute('value','1')
                with admin.expect_download() as downloaded:
                    admin.get_by_role('button',name='匯出桌次／區域統計',exact=True).click()
                report=list(csv.reader(io.StringIO(Path(downloaded.value.path()).read_text(encoding='utf-8-sig'))))
                assert report[0] == ['seating_type','seating','total','checked_in','pending']
                assert ['table','14–15 桌','3','1','2'] in report
                admin.goto(base + '/admin/event-settings')
                admin.locator('#roster-profile').select_option('standard')
                admin.locator('#seating-mode').select_option('table')
                save_settings(admin)
                guest.goto(base + '/ticket#' + token)
                # Revisiting the same fragment can keep the existing document.
                # Use the attendee's refresh button to fetch the saved settings.
                guest.locator('#refresh-ticket').click()
                expect(guest.locator('#ticket-seat-label')).to_have_text('您的桌次')
                expect(guest.locator('#ticket-status')).to_have_text('已報到')
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM attendees').fetchone()[0] == 13
                    assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 2
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 2
                    assert get_db().execute('SELECT grade FROM attendees WHERE id=1').fetchone()[0] == '碩一'
                assert all(secret not in url for secret in [token,table_token,range_token] for url in urls) and not errors, errors
                results.append({'engine': engine, 'normal_login_and_template_settings': 'PASS', 'xlsx_preview_and_import': 'PASS',
                                'grade_counts_5_4_3_1': 'PASS', 'bilingual_ticket_and_contact_privacy': 'PASS',
                                'staff_checkin_and_original_ticket_bingo': 'PASS', 'raffle_region_and_template_switch': 'PASS',
                                'six_column_csv_reimport_retains_checkins_and_cards':'PASS','mixed_table_ranges_and_grade_fallback':'PASS',
                                'mixed_actual_group_denominators_and_report':'PASS','mixed_bingo_and_raffle_seating':'PASS',
                                'mobile_layout': 'PASS', 'page_errors': errors, 'fixture_only': True})
            finally:
                if browser:
                    browser.close()
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
    destination = output / 'campus-browser-results.json'
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
