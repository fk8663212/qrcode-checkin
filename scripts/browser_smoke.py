"""Real HTTP + Chromium/WebKit mobile emulation; isolated temporary SQLite only.

Run from the project root: python -m scripts.browser_smoke
Camera test uses generated video frames, not a physical phone or lens.
"""
import json
import tempfile
import threading
from pathlib import Path

from PIL import Image
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server, WSGIRequestHandler

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee
from checkin.views import qr_png


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def main():
    output = Path('test-results')
    output.mkdir(exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='qrcode-browser-') as temp:
        app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'SECRET_KEY': 'test-browser-secret-000000000000000000',
                          'DATABASE': str(Path(temp) / 'browser.sqlite3'), 'PUBLIC_BASE_URL': 'http://127.0.0.1',
                          'EVENT_NAME': '校友交流活動', 'EVENT_STARTS_AT': '2026-11-20 18:00',
                          'EVENT_LOCATION': '第一會館', 'EVENT_ORGANIZER': '測試主辦單位',
                          'EVENT_CONTACT': 'host@example.org', 'EVENT_NOTICE': '請出示本人的電子票券。',
                          'SESSION_COOKIE_SECURE': False})
        with app.app_context(), transaction() as db:
            hashed = hash_password('browser-test-password')
            for name, role in [('browser-admin', 'admin'), ('browser-staff', 'staff')]:
                db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)', (name, hashed, role, now()))
            for index in range(1, 12):
                table_no = '01' if index <= 3 else '02' if index <= 8 else '03' if index <= 10 else ''
                insert_attendee(db, {'attendee_id': f'B{index:03}', 'name': f'測試來賓 {index:02}', 'affiliation': '資訊工程學系', 'table_no': table_no})
            tokens = {row['attendee_pk']: row['token'] for row in db.execute('SELECT * FROM tickets')}
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        base = f'http://127.0.0.1:{server.server_port}'
        app.config['PUBLIC_BASE_URL'] = base
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def count(pk):
            with app.app_context():
                return get_db().execute('SELECT COUNT(*) FROM checkins WHERE attendee_pk=? AND undone_at IS NULL', (pk,)).fetchone()[0]

        def login(page, admin=False):
            page.goto(base + '/login')
            page.get_by_label('帳號', exact=True).fill('browser-admin' if admin else 'browser-staff')
            page.get_by_label('密碼', exact=True).fill('browser-test-password')
            page.get_by_role('button', name='登入', exact=True).click()
            page.wait_for_url('**/admin' if admin else '**/staff')
            page.wait_for_load_state('networkidle')

        try:
            with sync_playwright() as playwright:
                for browser_name, device_name, offset in [('chromium', 'Pixel 7', 0), ('webkit', 'iPhone 13', 4)]:
                    browser = getattr(playwright, browser_name).launch()
                    context = browser.new_context(**playwright.devices[device_name])
                    errors = []
                    context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
                    page = context.new_page()
                    login(page)
                    # Inject rejection at the scanner boundary. WebKit can bypass
                    # JS overrides of native mediaDevices on subsequent calls.
                    # A separate generated-video test exercises real getUserMedia.
                    page.evaluate("""() => {
                        window.cameraDeniedCalls = 0;
                        Html5Qrcode.prototype.start = async () => {
                            window.cameraDeniedCalls += 1;
                            throw new DOMException('test-denied', 'NotAllowedError');
                        };
                    }""")
                    page.get_by_role('button', name='開啟後鏡頭').click()
                    try:
                        expect(page.locator('#staff-message')).to_contain_text('相機未能開啟')
                    except AssertionError:
                        page.screenshot(path=str(output / f'{browser_name}-failure.png'), full_page=True)
                        print(browser_name, page.evaluate("({calls: window.cameraDeniedCalls, cameraDisabled: document.getElementById('start-camera').disabled})"), errors)
                        raise
                    assert page.evaluate('window.cameraDeniedCalls') >= 1
                    pk = offset + 1
                    page.locator('#search-input').fill(f'B{pk:03}')
                    page.get_by_role('button', name='搜尋', exact=True).click()
                    page.locator('.search-result').click()
                    expect(page.locator('#result-status')).to_have_text('尚未報到')
                    assert count(pk) == 0
                    page.screenshot(path=str(output / f'{browser_name}-staff-confirm.png'), full_page=True)
                    page.get_by_role('button', name='確認報到', exact=True).click()
                    expect(page.locator('#result-label')).to_have_text('✓ 報到成功')
                    assert count(pk) == 1
                    page.get_by_role('button', name='繼續下一位').click()
                    page.get_by_text('貼上票券連結', exact=True).click()
                    page.locator('#ticket-input').fill(base + '/ticket#' + tokens[pk])
                    page.get_by_role('button', name='讀取並核對').click()
                    expect(page.locator('#result-history')).to_contain_text('browser-staff')
                    expect(page.locator('#confirm-checkin')).to_be_hidden()
                    assert count(pk) == 1
                    page.get_by_role('button', name='繼續下一位').click()

                    # Connection fails before server receives POST: no success state.
                    pk += 1
                    page.locator('#search-input').fill(f'B{pk:03}')
                    page.get_by_role('button', name='搜尋', exact=True).click()
                    page.locator('.search-result').click()
                    page.route('**/api/staff/checkin', lambda route: route.abort())
                    page.locator('#confirm-checkin').click()
                    expect(page.locator('#staff-message')).to_contain_text('尚未確認報到結果')
                    expect(page.locator('#result-label')).to_have_text('請核對來賓')
                    assert count(pk) == 0
                    page.unroute('**/api/staff/checkin')
                    page.locator('#confirm-checkin').click()
                    expect(page.locator('#result-label')).to_have_text('✓ 報到成功')
                    assert count(pk) == 1
                    page.get_by_role('button', name='繼續下一位').click()

                    # Commit succeeds but response is lost: same request safely retries.
                    pk += 1
                    page.locator('#search-input').fill(f'B{pk:03}')
                    page.get_by_role('button', name='搜尋', exact=True).click()
                    page.locator('.search-result').click()
                    def lost_response(route):
                        response = route.fetch()
                        assert response.status == 201
                        route.abort()
                    page.route('**/api/staff/checkin', lost_response)
                    page.locator('#confirm-checkin').click()
                    expect(page.locator('#staff-message')).to_contain_text('尚未確認報到結果')
                    expect(page.locator('#result-label')).to_have_text('請核對來賓')
                    assert count(pk) == 1
                    page.screenshot(path=str(output / f'{browser_name}-network-unconfirmed.png'), full_page=True)
                    page.unroute('**/api/staff/checkin')
                    page.locator('#confirm-checkin').click()
                    expect(page.locator('#result-label')).to_have_text('已報到 · 請勿重複計數')
                    assert count(pk) == 1

                    public_context = browser.new_context(**playwright.devices[device_name])
                    public = public_context.new_page()
                    urls = []
                    public.on('request', lambda request: urls.append(request.url))
                    public.goto(base + '/')
                    expect(public.get_by_role('heading', name='歡迎蒞臨 校友交流活動')).to_be_visible()
                    expect(public.get_by_label('活動資訊')).to_contain_text('第一會館')
                    assert public.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    public.screenshot(path=str(output / f'{browser_name}-event-home.png'), full_page=True)
                    public.goto(base + '/ticket#' + tokens[offset + 1])
                    expect(public.locator('#ticket-name')).to_have_text(f'測試來賓 {offset+1:02}')
                    expect(public.locator('#ticket-status')).to_have_text('已報到')
                    expect(public.get_by_label('活動資訊')).to_contain_text('2026/11/20 18:00:00')
                    expect(public.get_by_label('活動資訊')).to_contain_text('請出示本人的電子票券。')
                    assert all(tokens[offset + 1] not in url for url in urls)
                    assert public.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    public.screenshot(path=str(output / f'{browser_name}-ticket.png'), full_page=True)
                    public.goto(base + '/ticket#' + 'A' * 43)
                    expect(public.locator('#ticket-message')).to_contain_text('失效')
                    expect(public.locator('#ticket-card')).to_be_hidden()
                    public_context.close()

                    admin = context.new_page()
                    login(admin, admin=True)
                    overview = admin.get_by_role('region', name='現場桌次概況')
                    expect(overview.locator('.table-attendance-card')).to_have_count(4)
                    for table_no, total in [('01', 3), ('02', 5), ('03', 2), ('', 1)]:
                        card = overview.locator(f'[data-table-no="{table_no}"]')
                        assert card.locator('progress').evaluate('(p) => p.max') == total
                        positions = card.evaluate('''(card) => {
                            const label = card.querySelector('h3').getBoundingClientRect();
                            const count = card.querySelector('.table-attendance-count').getBoundingClientRect();
                            return {separated: count.top >= label.bottom};
                        }''')
                        assert positions['separated'], 'Table label and attendance count overlap'
                    expect(overview.locator('[data-table-no="01"]')).to_have_class('table-attendance-card is-complete')
                    assert len(admin.locator('link[rel="stylesheet"]').get_attribute('href').split('?')) == 2
                    overview.screenshot(path=str(output / f'{browser_name}-uneven-tables-mobile.png'))
                    admin.screenshot(path=str(output / f'{browser_name}-admin.png'), full_page=True)
                    assert admin.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    admin.set_viewport_size({'width': 1522, 'height': 1000})
                    overview.screenshot(path=str(output / f'{browser_name}-uneven-tables-desktop.png'))
                    assert not errors, errors
                    results.append({'browser': browser_name, 'device_emulation': device_name,
                                    'manual_checkin': 'PASS', 'duplicate': 'PASS', 'camera_denied': 'PASS',
                                    'network_before_commit': 'PASS', 'lost_response_retry': 'PASS',
                                    'ticket_and_no_token_in_url': 'PASS', 'mobile_layout': 'PASS',
                                    'configured_event_information': 'PASS',
                                    'uneven_table_denominators_and_separate_labels': 'PASS',
                                    'javascript_errors': errors})
                    context.close()
                    browser.close()

                # A real getUserMedia video stream of generated QR frames exercises
                # the vendored software decoder without native BarcodeDetector.
                video = Path(temp) / 'qr.y4m'
                with app.app_context():
                    qr = Image.open(qr_png(tokens[11])).convert('RGB').resize((280, 280), Image.Resampling.NEAREST)
                canvas = Image.new('RGB', (640, 480), 'white')
                canvas.paste(qr, (180, 100))
                y, cb, cr = canvas.convert('YCbCr').split()
                frame = y.tobytes() + cb.resize((320, 240)).tobytes() + cr.resize((320, 240)).tobytes()
                video.write_bytes(b'YUV4MPEG2 W640 H480 F5:1 Ip A0:0 C420\n' + (b'FRAME\n' + frame) * 20)
                browser = playwright.chromium.launch(args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                                                           '--use-file-for-fake-video-capture=' + str(video)])
                context = browser.new_context(permissions=['camera'], viewport={'width': 390, 'height': 844})
                page = context.new_page()
                login(page)
                page.evaluate('window.BarcodeDetector = undefined')
                page.get_by_role('button', name='開啟後鏡頭').click()
                expect(page.locator('#result-name')).to_have_text('測試來賓 11', timeout=20000)
                assert count(11) == 0
                page.locator('#confirm-checkin').click()
                expect(page.locator('#result-label')).to_have_text('✓ 報到成功')
                assert count(11) == 1
                page.screenshot(path=str(output / 'chromium-camera-decoder.png'), full_page=True)
                results.append({'browser': 'chromium', 'generated_camera_video_software_decoder': 'PASS', 'physical_camera': 'NOT TESTED'})
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
        (output / 'browser-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
