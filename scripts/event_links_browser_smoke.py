"""Verify editable ticket resources in isolated Chromium/WebKit sessions."""
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
    with tempfile.TemporaryDirectory(prefix='qrcode-event-links-browser-') as directory, sync_playwright() as playwright:
        for engine, device in [('chromium', 'Pixel 7'), ('webkit', 'iPhone 13')]:
            app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'DATABASE': str(Path(directory) / (engine + '.sqlite3')),
                              'SECRET_KEY': 'event-links-browser-fixture-secret-0000', 'PUBLIC_BASE_URL': 'http://127.0.0.1',
                              'EVENT_NAME': '導生晚宴 / Dinner', 'INITIAL_ADMIN_USERNAME': '', 'INITIAL_ADMIN_PASSWORD_HASH': ''})
            password = 'event-links-fixture-password!'
            with app.app_context(), transaction() as db:
                db.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,'admin',?)",
                           ('links-admin', hash_password(password), now()))
                insert_attendee(db, {'attendee_id': 'LINK001', 'name': '測試來賓 / Test Guest', 'affiliation': '測試單位 / Test', 'table_no': '01'})
                token = db.execute('SELECT token FROM tickets').fetchone()[0]
            server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
            base = f'http://127.0.0.1:{server.server_port}'
            app.config['PUBLIC_BASE_URL'] = base
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            browser = None
            errors, resource_requests = [], []
            try:
                browser = getattr(playwright, engine).launch()
                guest_context = browser.new_context(locale='en-US', **playwright.devices[device])
                guest_context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))

                def resource(route):
                    resource_requests.append(route.request.headers)
                    route.fulfill(status=200, content_type='text/html', body='<h1>Fixture event document</h1>')

                guest_context.route('https://resources.example.test/**', resource)
                guest = guest_context.new_page()
                guest.goto(base + '/ticket#' + token)
                expect(guest.locator('#ticket-name')).to_have_text('測試來賓 / Test Guest')
                for key in ['menu', 'schedule']:
                    expect(guest.locator('#ticket-' + key + '-pending')).to_be_visible()
                    expect(guest.locator('#ticket-' + key + '-pending')).to_be_disabled()
                    expect(guest.locator('#ticket-' + key)).to_be_hidden()
                expect(guest.locator('#ticket-menu-pending')).to_contain_text('Dinner menu')
                expect(guest.locator('#ticket-schedule-pending')).to_contain_text('Coming soon')
                guest.screenshot(path=str(output / (engine + '-event-links-pending.png')), full_page=True)

                admin_context = browser.new_context(viewport={'width': 1280, 'height': 900}, locale='en-US')
                admin = admin_context.new_page()
                admin.on('pageerror', lambda error: errors.append(str(error)))
                admin.goto(base + '/login')
                admin.get_by_label('帳號', exact=True).fill('links-admin')
                admin.get_by_label('密碼', exact=True).fill(password)
                admin.get_by_role('button', name='登入', exact=True).click()
                admin.wait_for_url('**/admin')
                admin.locator('nav').get_by_role('link', name='活動連結', exact=True).click()
                expect(admin.locator('html')).to_have_attribute('lang', 'zh-Hant')

                def save(menu, schedule):
                    admin.locator('#menu_url').fill(menu)
                    admin.locator('#schedule_url').fill(schedule)
                    admin.get_by_role('button', name='儲存活動連結', exact=True).click()
                    expect(admin.get_by_role('status')).to_contain_text('活動連結已儲存')
                    guest.locator('#refresh-ticket').click()

                menu = 'https://resources.example.test/menu.pdf?lang=en#page=1'
                schedule = 'https://resources.example.test/schedule?view=public'
                save(menu, '')
                expect(guest.locator('#ticket-menu')).to_have_attribute('href', menu)
                expect(guest.locator('#ticket-menu-pending')).to_be_hidden()
                expect(guest.locator('#ticket-schedule-pending')).to_be_visible()
                with guest.expect_popup() as popup:
                    guest.locator('#ticket-menu').click()
                document = popup.value
                expect(document.locator('h1')).to_have_text('Fixture event document')
                assert document.evaluate('window.opener === null')
                document.close()

                save(menu + '-v2', schedule)
                expect(guest.locator('#ticket-menu')).to_have_attribute('href', menu + '-v2')
                expect(guest.locator('#ticket-schedule')).to_have_attribute('href', schedule)
                expect(guest.locator('#ticket-schedule')).to_contain_text('Event schedule')
                guest.reload()
                expect(guest.locator('#ticket-schedule')).to_be_visible()
                for language in ['en', 'zh-TW']:
                    guest.locator('[data-ui-language="' + language + '"]').click()
                    expect(guest.locator('#ticket-schedule')).to_be_visible()
                    expect(guest.locator('#ticket-menu')).to_contain_text('Dinner menu' if language == 'en' else '晚宴菜單')
                    assert guest.evaluate('location.hash.slice(1)') == token
                    for width in [320, 390]:
                        guest.set_viewport_size({'width': width, 'height': 844})
                        assert guest.evaluate('document.documentElement.scrollWidth <= innerWidth'), (engine, language, width)
                        guest.screenshot(path=str(output / f'{engine}-event-links-ticket-{width}-{language}.png'), full_page=True)

                for width in [320, 390, 1280]:
                    admin.set_viewport_size({'width': width, 'height': 900})
                    assert admin.evaluate('document.documentElement.scrollWidth <= innerWidth'), (engine, 'admin', width)
                admin.screenshot(path=str(output / (engine + '-event-links-admin.png')), full_page=True)
                save('', schedule)
                expect(guest.locator('#ticket-menu-pending')).to_be_visible()
                expect(guest.locator('#ticket-menu')).to_be_hidden()
                assert guest.locator('#ticket-menu').get_attribute('href') is None
                expect(guest.locator('#ticket-schedule')).to_be_visible()
                assert resource_requests and all('referer' not in headers and 'cookie' not in headers for headers in resource_requests)
                assert not errors, errors
                with app.app_context():
                    db = get_db()
                    assert db.execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0
                    assert db.execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 0
                    assert db.execute('SELECT token FROM tickets WHERE revoked_at IS NULL').fetchone()[0] == token
                    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='event_links_update'").fetchone()[0] == 3
                results.append({'engine': engine, 'bilingual_pending_buttons': 'PASS', 'normal_admin_login_and_edit': 'PASS',
                                'refresh_update_and_clear': 'PASS', 'external_document_opened_without_referrer_or_cookie': 'PASS',
                                'mobile_320_390_and_admin_layout': 'PASS', 'original_ticket_and_attendance_preserved': 'PASS',
                                'page_errors': errors, 'fixture_only': True})
            finally:
                if browser:
                    browser.close()
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()
    destination = output / 'event-links-browser-results.json'
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
