"""Real bilingual participant flows; normal operator login, isolated SQLite only."""
import json
import re
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

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
    with tempfile.TemporaryDirectory(prefix='qrcode-language-browser-') as directory, sync_playwright() as playwright:
        for engine, device in [('chromium', 'Pixel 7'), ('webkit', 'iPhone 13')]:
            app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'DATABASE': str(Path(directory) / (engine + '.sqlite3')),
                              'SECRET_KEY': 'language-browser-fixture-secret-00000000',
                              'EVENT_NAME': 'International Dinner / 導生晚宴', 'EVENT_LOCATION': 'Main Hall / 大廳',
                              'EVENT_STARTS_AT': '2026-10-23T18:00:00+08:00',
                              'PUBLIC_BASE_URL': 'http://127.0.0.1', 'INITIAL_ADMIN_USERNAME': '', 'INITIAL_ADMIN_PASSWORD_HASH': ''})
            password = 'language-browser-fixture-password!'
            with app.app_context(), transaction() as db:
                hashed = hash_password(password)
                for username, role in [('language-admin', 'admin'), ('language-staff', 'staff')]:
                    db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)', (username, hashed, role, now()))
                insert_attendee(db, {'attendee_id': 'INT001', 'name': 'Alice Ng', 'affiliation': 'International Students', 'table_no': '03'})
                insert_attendee(db, {'attendee_id': 'PRIVATE002', 'name': 'Private Guest Two', 'affiliation': 'Private Dept', 'table_no': '04'})
                token = db.execute('SELECT token FROM tickets WHERE attendee_pk=1').fetchone()[0]
            server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
            base = f'http://127.0.0.1:{server.server_port}'
            app.config['PUBLIC_BASE_URL'] = base
            threading.Thread(target=server.serve_forever, daemon=True).start()
            browser = getattr(playwright, engine).launch()
            errors, urls = [], []

            def context(mobile=False, locale='en-US'):
                result = browser.new_context(locale=locale, **(playwright.devices[device] if mobile else {'viewport': {'width': 1522, 'height': 1000}}))
                result.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
                result.on('request', lambda request: urls.append(request.url))
                return result

            def language(page, code):
                page.locator('[data-ui-language="' + code + '"]').click()
                expect(page.locator('html')).to_have_attribute('lang', 'en' if code == 'en' else 'zh-Hant')

            def login(page, role):
                page.goto(base + '/login?lang=en')
                expect(page.locator('html')).to_have_attribute('lang', 'zh-Hant')
                page.get_by_label('帳號', exact=True).fill('language-' + role)
                page.get_by_label('密碼', exact=True).fill(password)
                page.get_by_role('button', name='登入', exact=True).click()
                page.wait_for_url('**/admin' if role == 'admin' else '**/staff')

            def mobile_layout(page, label):
                for width in (320, 390):
                    page.set_viewport_size({'width': width, 'height': 844})
                    if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'):
                        page.screenshot(path=str(output / (engine + '-english-layout-failure.png')), full_page=True)
                        overflowing = page.locator('body *').evaluate_all("elements => elements.map(e => ({tag:e.tagName, cls:e.className, left:e.getBoundingClientRect().left, right:e.getBoundingClientRect().right})).filter(e => e.left < -1 || e.right > innerWidth + 1)")
                        raise AssertionError((engine, label, width, overflowing))
                page.screenshot(path=str(output / (engine + '-english-' + label + '.png')), full_page=True)

            try:
                guest_context = context(mobile=True)
                guest = guest_context.new_page()
                guest.goto(base)
                expect(guest.locator('html')).to_have_attribute('lang', 'en')
                expect(guest.locator('h1')).to_have_text('A warm welcome awaits.')
                expect(guest.locator('.event-info')).to_contain_text('Main Hall / 大廳')
                mobile_layout(guest, 'home')
                guest.goto(base + '/ticket#' + token)
                expect(guest.locator('#ticket-status')).to_have_text('Not checked in')
                expect(guest.locator('#join-bingo')).to_be_hidden()
                language(guest, 'zh-TW')
                expect(guest.locator('#ticket-status')).to_have_text('待報到')
                assert guest.evaluate('location.hash.slice(1)') == token
                language(guest, 'en')
                expect(guest.locator('#ticket-name')).to_have_text('Alice Ng')
                assert guest.evaluate('location.hash.slice(1)') == token
                mobile_layout(guest, 'ticket-before-checkin')
                guest_context.set_offline(True)
                guest.locator('#refresh-ticket').click()
                expect(guest.locator('#ticket-message')).to_contain_text('Check your internet connection')
                expect(guest.locator('#ticket-card')).to_be_hidden()
                guest_context.set_offline(False)
                guest.locator('#retry-ticket').click()
                expect(guest.locator('#ticket-status')).to_have_text('Not checked in')
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 0

                premature = context(mobile=True).new_page()
                premature.goto(base + '/bingo')
                premature.get_by_label('Personal ticket link', exact=True).fill(base + '/ticket?lang=en#' + token)
                premature.get_by_role('button', name='Get / restore my card').click()
                expect(premature.locator('#page-error')).to_contain_text('check you in first')
                premature.close()

                staff = context(mobile=True).new_page()
                login(staff, 'staff')
                staff.locator('#search-input').fill('INT001')
                staff.get_by_role('button', name='搜尋', exact=True).click()
                staff.locator('.search-result').click()
                staff.get_by_role('button', name='確認報到', exact=True).click()
                expect(staff.locator('#result-label')).to_have_text('✓ 報到成功')
                host = context().new_page()
                login(host, 'admin')
                host.goto(base + '/bingo/host?lang=en')
                expect(host.locator('html')).to_have_attribute('lang', 'zh-Hant')
                host.locator('#prize-count').fill('1')
                host.get_by_role('button', name='儲存獎品份數', exact=True).click()
                expect(host.locator('#stat-winners')).to_have_text('0 / 1')
                host.locator('#registration-button').click()
                expect(host.locator('#registration-button')).to_have_text('關閉領卡')

                guest.bring_to_front()
                guest.locator('#refresh-ticket').click()
                expect(guest.locator('#ticket-status')).to_have_text('Checked in')
                expect(guest.locator('#ticket-time')).to_contain_text('(Taipei)')
                mobile_layout(guest, 'ticket')
                guest.get_by_role('button', name='Get / open your bingo card', exact=True).click()
                guest.wait_for_url('**/bingo')
                expect(guest.locator('.bingo-cell')).to_have_count(25)
                expect(guest.locator('#player-greeting')).to_have_text('Alice Ng’s bingo card')
                expect(guest.locator('#game-badge')).to_have_text('Game 1')
                expect(guest.locator('#prize-rule')).to_have_text('Prizes this game: 1. Each guest can win once per game.')
                guest.locator('.bingo-cell[data-index="0"]').click()
                expect(guest.locator('#save-status')).to_have_text('Saved')
                with app.app_context():
                    original = dict(get_db().execute('SELECT * FROM bingo_cards').fetchone())
                language(guest, 'zh-TW')
                expect(guest.locator('#player-greeting')).to_have_text('Alice Ng的賓果卡')
                expect(guest.locator('.bingo-cell[data-index="0"]')).to_have_attribute('aria-pressed', 'true')
                language(guest, 'en')
                expect(guest.locator('#game-badge')).to_have_text('Game 1')
                expect(guest.locator('.bingo-cell[data-index="0"]')).to_have_attribute('aria-pressed', 'true')
                mobile_layout(guest, 'bingo-card')

                # The explicit display language must override a Chinese browser,
                # including subsequent API requests and dynamic animation text.
                display = context(locale='zh-TW').new_page()
                display.goto(base + '/bingo/display?lang=en')
                expect(display.locator('#display-winners')).to_contain_text('The first winner is coming soon.')
                mobile_layout(display, 'bingo-display-mobile')
                display.set_viewport_size({'width': 1522, 'height': 1000})
                first_row = json.loads(original['numbers'])[:5]
                host.bring_to_front()
                with patch('checkin.bingo.service.secrets.choice', return_value=first_row[0]):
                    host.locator('#draw-button').click()
                    expect(host.locator('#draw-stage')).to_have_class(re.compile(r'\bis-rolling\b'))
                display.bring_to_front()
                display.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(display.locator('#draw-stage')).to_have_class(re.compile(r'\bis-rolling\b'))
                expect(display.locator('#display-round')).to_have_text('Draw 1 · The lucky number is coming')
                expect(display.locator('#display-latest')).to_have_text(str(first_row[0]), timeout=10000)
                expect(display.locator('#draw-history .history-ball')).to_have_count(1)
                for page in (guest, host, display):
                    page.emulate_media(reduced_motion='reduce')
                for index, number in enumerate(first_row[1:], start=2):
                    host.bring_to_front()
                    with patch('checkin.bingo.service.secrets.choice', return_value=number):
                        host.locator('#draw-button').click()
                        expect(host.locator('#stat-draws')).to_have_text(str(index))
                guest.bring_to_front()
                expect(guest.locator('#player-status')).to_contain_text('BINGO! You’re a lucky winner', timeout=10000)
                expect(guest.locator('#player-status')).to_contain_text('Show P001 and this screen')
                display.bring_to_front()
                expect(display.locator('#display-winners')).to_contain_text('Lucky guest', timeout=10000)
                expect(display.locator('#display-winners')).not_to_contain_text('Alice Ng')
                display.screenshot(path=str(output / (engine + '-english-bingo-display.png')), full_page=True)
                host.bring_to_front()
                host.locator('#host-winners button').click()
                guest.bring_to_front()
                expect(guest.locator('#player-status')).to_contain_text('Your prize has been collected.', timeout=10000)
                mobile_layout(guest, 'bingo-winner')

                host.goto(base + '/raffle/host?lang=en')
                expect(host.locator('html')).to_have_attribute('lang', 'zh-Hant')
                expect(host.locator('#eligible-count')).to_have_text('1')
                host.locator('#allow-repeat').check()
                host.get_by_role('button', name='儲存本輪規則', exact=True).click()
                expect(host.locator('#rule-hint')).to_contain_text('規則已保存')
                host.locator('#prize-name').fill('Gift voucher / 禮券')
                host.locator('#prize-quantity').fill('2')
                host.get_by_role('button', name='新增獎品', exact=True).click()
                expect(host.locator('.raffle-prize h3')).to_have_text('Gift voucher / 禮券')
                raffle = context().new_page()
                raffle.goto(base + '/raffle/display')
                expect(raffle.locator('#display-round-title')).to_have_text('Lucky draw')
                expect(raffle.locator('#raffle-display-winners')).to_contain_text('Please wait for the host')
                mobile_layout(raffle, 'raffle-display-mobile')
                raffle.set_viewport_size({'width': 1522, 'height': 1000})
                host.bring_to_front()
                host.emulate_media(reduced_motion='no-preference')
                host.get_by_role('button', name='抽出得主', exact=True).click()
                expect(host.locator('#raffle-animation')).to_have_class('raffle-animation is-rolling')
                raffle.bring_to_front()
                raffle.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(raffle.locator('#raffle-animation')).to_have_class('raffle-animation is-rolling')
                expect(raffle.locator('#animation-title')).to_have_text('Our lucky winners are coming')
                expect(raffle.locator('#animation-hint')).to_have_text('Winners to reveal: 1 · A little luck is coming')
                expect(raffle.locator('.raffle-display-winner')).to_have_count(0)
                expect(raffle.locator('#animation-title')).to_have_text('Congratulations to our lucky winners!', timeout=10000)
                expect(raffle.locator('.raffle-display-winner')).to_have_count(1)
                expect(raffle.locator('#animation-winners')).to_have_text('P001')
                expect(raffle.locator('#raffle-display-prizes')).to_have_text('Gift voucher / 禮券 · 1 / 2 awarded')
                assert 'Alice Ng' not in raffle.locator('#raffle-app').inner_text()
                raffle.screenshot(path=str(output / (engine + '-english-raffle-reveal.png')), full_page=True)
                raffle.emulate_media(reduced_motion='reduce')
                host.bring_to_front()
                host.emulate_media(reduced_motion='reduce')
                host.get_by_role('button', name='抽出得主', exact=True).click()
                expect(host.locator('.raffle-winner')).to_have_count(2)
                raffle.bring_to_front()
                expect(raffle.locator('.raffle-display-winner')).to_have_count(2, timeout=10000)
                expect(raffle.locator('#raffle-animation')).to_have_class('raffle-animation without-motion is-revealed')
                raffle.reload()
                expect(raffle.locator('.raffle-display-winner')).to_have_count(2)
                expect(raffle.locator('#raffle-animation')).to_be_hidden()
                host.bring_to_front()
                host.locator('.raffle-winner').first.get_by_role('button', name='標記領獎', exact=True).click()
                raffle.bring_to_front()
                expect(raffle.locator('#raffle-display-winners')).to_contain_text('Prize collected ✓', timeout=10000)
                language(raffle, 'zh-TW')
                expect(raffle.locator('#raffle-display-winners')).to_contain_text('已領獎 ✓')
                language(raffle, 'en')
                expect(raffle.locator('#raffle-display-winners')).to_contain_text('Prize collected ✓')

                # Reissue through the existing authenticated admin form. Locale
                # choice must neither revive the old ticket nor create a card.
                host.bring_to_front()
                host.goto(base + '/admin/attendees/1')
                with app.app_context():
                    ticket_id = get_db().execute('SELECT id FROM tickets WHERE attendee_pk=1 AND revoked_at IS NULL').fetchone()[0]
                csrf = host.locator('meta[name="csrf-token"]').get_attribute('content')
                response = host.request.post(base + '/admin/attendees/1/reissue', form={
                    'csrf_token': csrf, 'reason': '雙語瀏覽器測試補發', 'confirm': 'yes', 'ticket_id': str(ticket_id)})
                assert response.ok
                with app.app_context():
                    replacement = get_db().execute('SELECT token FROM tickets WHERE attendee_pk=1 AND revoked_at IS NULL').fetchone()[0]
                invalid = context(mobile=True).new_page()
                invalid.goto(base + '/ticket?lang=en#' + token)
                expect(invalid.locator('#ticket-message')).to_contain_text('ask the organizer for your latest ticket')
                expect(invalid.locator('#ticket-card')).to_be_hidden()
                guest.bring_to_front()
                expect(guest.locator('#card-panel')).to_be_hidden(timeout=10000)
                guest.goto(base + '/ticket#' + replacement)
                guest.locator('#join-bingo').click()
                guest.wait_for_url('**/bingo')
                expect(guest.locator('#player-status')).to_contain_text('Your prize has been collected.')
                expect(guest.locator('.bingo-cell[data-index="0"]')).to_have_attribute('aria-pressed', 'true')
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 1
                    assert get_db().execute('SELECT numbers FROM bingo_cards').fetchone()[0] == original['numbers']
                    assert get_db().execute('SELECT COUNT(*) FROM checkins WHERE undone_at IS NULL').fetchone()[0] == 1
                assert all(token not in url and replacement not in url for url in urls)
                assert not errors, errors
                results.append({'browser': engine, 'device_emulation': device, 'automatic_english': 'PASS',
                                'manual_switch_preserves_ticket_fragment_and_identity': 'PASS', 'no_checkin_on_language_switch': 'PASS',
                                'staff_and_admin_remain_chinese': 'PASS', 'english_ticket_and_safe_network_retry': 'PASS',
                                'english_eligibility_and_reissue_errors': 'PASS', 'same_card_marks_and_wins_across_languages': 'PASS',
                                'english_bingo_animation_and_public_winners': 'PASS', 'english_raffle_animation_and_claims': 'PASS',
                                'raffle_reduced_motion_and_no_replay': 'PASS', '320_and_390_layout': 'PASS',
                                'no_ticket_token_in_http_urls': 'PASS', 'javascript_errors': errors})
            finally:
                browser.close()
                server.shutdown()
                server.server_close()
    (output / 'i18n-browser-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
