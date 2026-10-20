"""Desktop localhost camera context and canonical LAN ticket URL regression.

Uses a temporary database, a signed fixture session and no real camera.
Run: python -m scripts.local_camera_smoke
"""
import json
import secrets
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from checkin import create_app
from checkin.db import now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee
from scripts.browser_smoke import QuietHandler


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='qrcode-local-camera-') as temp:
        canonical = 'http://192.0.2.10:5001'
        app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'SECRET_KEY': secrets.token_hex(32),
                          'DATABASE': str(Path(temp) / 'event.sqlite3'), 'PUBLIC_BASE_URL': canonical,
                          'SESSION_COOKIE_SECURE': False})
        with app.app_context(), transaction() as db:
            db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                       ('fixture-staff', hash_password(secrets.token_urlsafe(20)), 'staff', now()))
            pk = insert_attendee(db, {'attendee_id': 'LOCAL-001', 'name': '內網票券測試', 'affiliation': '測試', 'table_no': '01'})
            token = db.execute('SELECT token FROM tickets WHERE attendee_pk=?', (pk,)).fetchone()[0]
        cookie = app.session_interface.get_signing_serializer(app).dumps(
            {'user_id': 1, 'auth_version': 1, 'csrf': secrets.token_urlsafe(32)})
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        base = f'http://localhost:{server.server_port}'
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                for engine in ('webkit', 'chromium'):
                    browser = getattr(playwright, engine).launch()
                    context = browser.new_context()
                    context.add_cookies([{'name': app.config['SESSION_COOKIE_NAME'], 'value': cookie,
                                         'url': base, 'httpOnly': True, 'sameSite': 'Lax'}])
                    page = context.new_page()
                    page.goto(base + '/staff')
                    probe = page.evaluate('({secure: isSecureContext, cameraAPI: !!navigator.mediaDevices?.getUserMedia})')
                    print(engine, json.dumps(probe))
                    assert probe == {'secure': True, 'cameraAPI': True}
                    page.get_by_text('貼上票券連結', exact=True).click()
                    page.locator('#ticket-input').fill(canonical + '/ticket#' + token)
                    page.get_by_role('button', name='讀取並核對').click()
                    try:
                        expect(page.locator('#result-name')).to_have_text('內網票券測試', timeout=3000)
                    except AssertionError:
                        print('Canonical LAN ticket lookup:', page.locator('#staff-message').inner_text())
                        raise
                    page.locator('#next-guest').click()
                    page.locator('#ticket-input').fill('https://unrelated.example/ticket#' + token)
                    page.get_by_role('button', name='讀取並核對').click()
                    expect(page.locator('#staff-message')).to_contain_text('這不是本活動')
                    expect(page.locator('#confirmation')).to_be_hidden()
                    with app.app_context(), transaction() as db:
                        assert db.execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 0
                    results.append({'engine': engine, 'localhost_secure_context': 'PASS',
                                    'camera_api_available': 'PASS', 'canonical_lan_ticket': 'PASS',
                                    'unrelated_origin_rejected': 'PASS', 'no_automatic_checkin': 'PASS',
                                    'physical_camera': 'NOT TESTED'})
                    context.close()
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
    output = Path('test-results')
    output.mkdir(exist_ok=True)
    (output / 'local-camera-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
