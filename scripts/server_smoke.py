"""Multi-process Gunicorn, concurrent HTTP POST, real process restart and log check."""
import concurrent.futures
import http.cookiejar
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from threading import Barrier

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee


def main():
    with tempfile.TemporaryDirectory(prefix='qrcode-server-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        key = secrets.token_hex(32)
        database = str(Path(temp) / 'http.sqlite3')
        app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'SECRET_KEY': key,
                          'DATABASE': database, 'PUBLIC_BASE_URL': base})
        with app.app_context(), transaction() as db:
            db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',
                       ('http-staff', hash_password('http-test-password'), 'staff', now()))
            insert_attendee(db, {'attendee_id': 'HTTP001', 'name': '重啟測試來賓', 'affiliation': '測試', 'table_no': '01'})
            token = db.execute('SELECT token FROM tickets').fetchone()[0]
        env = dict(os.environ, CHECKIN_ENV='test', DATABASE_PATH=database, SECRET_KEY=key,
                   PUBLIC_BASE_URL=base, TRUST_PROXY='0')
        logpath = Path(temp) / 'server.log'
        process = None
        log = logpath.open('w')

        def start():
            process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--config', 'gunicorn.conf.py',
                                        '--bind', f'127.0.0.1:{port}', 'wsgi:app'], env=env, stdout=log, stderr=log)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(logpath.read_text())
                try:
                    with urllib.request.urlopen(base + '/healthz', timeout=1) as response:
                        if response.status == 200:
                            return process
                except OSError:
                    time.sleep(0.1)
            process.terminate()
            raise RuntimeError('Gunicorn startup timed out')

        def browser():
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            html = opener.open(base + '/login').read().decode()
            csrf = re.search('name="csrf-token" content="([^"]+)"', html).group(1)
            from urllib.parse import urlencode
            opener.open(urllib.request.Request(base + '/login', data=urlencode({'csrf_token': csrf, 'username': 'http-staff',
                                                                              'password': 'http-test-password'}).encode())).read()
            html = opener.open(base + '/staff').read().decode()
            csrf = re.search('name="csrf-token" content="([^"]+)"', html).group(1)
            return opener, csrf

        def post(opener, csrf, path, data):
            response = opener.open(urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                   headers={'Content-Type': 'application/json', 'X-CSRF-Token': csrf}))
            return response.status, json.load(response)

        try:
            process = start()
            clients = [browser() for _ in range(10)]
            barrier = Barrier(10)
            def work(client):
                barrier.wait(timeout=15)
                return post(*client, '/api/staff/checkin', {'token': token, 'request_id': secrets.token_hex(24)})
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                responses = list(pool.map(work, clients))
            assert sum(status == 201 for status, _ in responses) == 1
            assert all(status in (200, 201) for status, _ in responses)
            original = responses[0][1]['attendee']['checked_at']
            process.terminate()
            process.wait(timeout=15)
            process = start()
            anonymous = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            html = anonymous.open(base + '/ticket').read().decode()
            csrf = re.search('name="csrf-token" content="([^"]+)"', html).group(1)
            status, data = post(anonymous, csrf, '/api/ticket', {'token': token})
            assert status == 200 and data['checked_in'] and data['checked_at'] == original
            with app.app_context():
                assert get_db().execute('SELECT COUNT(*) FROM checkins').fetchone()[0] == 1
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            log.close()
        assert token not in logpath.read_text()
        result = {'gunicorn_workers': 2, 'parallel_http_requests': 10, 'first_success_count': 1,
                  'real_process_restart': 'PASS', 'ticket_and_checkin_retained': 'PASS', 'no_full_token_in_access_log': 'PASS'}
        output = Path('test-results')
        output.mkdir(exist_ok=True)
        (output / 'server-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
