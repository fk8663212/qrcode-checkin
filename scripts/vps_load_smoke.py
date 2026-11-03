"""Exercise the actual k6 launcher against isolated two-worker Gunicorn only."""
import argparse
import csv
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee
from scripts.vps_load_test import main as load_main


def main():
    parser = argparse.ArgumentParser(description='隔離資料庫的真實 k6／Gunicorn 回歸，不連線 VPS。')
    parser.add_argument('--k6-bin', default='k6')
    parser.add_argument('--users', type=int, default=120, choices=range(1, 201), metavar='1..200')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix='qrcode-vps-load-smoke-') as directory:
        folder = Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        key, password = secrets.token_hex(32), secrets.token_urlsafe(24)
        database = str(folder / 'fixture.sqlite3')
        app = create_app({'TESTING': True, 'CHECKIN_ENV': 'test', 'DATABASE': database, 'SECRET_KEY': key,
                          'PUBLIC_BASE_URL': base, 'EVENT_NAME': '隔離壓測回歸',
                          'INITIAL_ADMIN_USERNAME': '', 'INITIAL_ADMIN_PASSWORD_HASH': ''})
        with app.app_context(), transaction() as db:
            db.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,'staff',?)",
                       ('load-fixture-staff', hash_password(password), now()))
            for number in range(1, args.users + 1):
                insert_attendee(db, {'attendee_id': f'TEST120-{number:03}', 'name': f'假來賓 {number}（測試）',
                                     'affiliation': '隔離測試', 'table_no': f'{(number - 1) // 10 + 1:02}'})
            db.execute('UPDATE bingo_games SET registration_open=1 WHERE id=1')
            tokens = [row[0] for row in db.execute('SELECT token FROM tickets ORDER BY attendee_pk')]
        tickets = folder / 'tickets.csv'
        fd = os.open(str(tickets), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['attendee_id', 'name', 'ticket_url'])
            writer.writerows([[f'TEST120-{number:03}', f'假來賓 {number}（測試）', base + '/ticket#' + token]
                              for number, token in enumerate(tokens, 1)])
        environment = dict(os.environ, CHECKIN_ENV='test', DATABASE_PATH=database, SECRET_KEY=key,
                           PUBLIC_BASE_URL=base, TRUST_PROXY='0', INITIAL_ADMIN_USERNAME='', INITIAL_ADMIN_PASSWORD_HASH='',
                           EVENT_NAME='隔離壓測回歸', EVENT_STARTS_AT='', EVENT_LOCATION='', EVENT_ORGANIZER='',
                           EVENT_CONTACT='', EVENT_NOTICE='')
        logpath = folder / 'gunicorn.log'
        process = None
        reports = []
        with logpath.open('w') as log:
            try:
                process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--config', 'gunicorn.conf.py',
                                             '--bind', f'127.0.0.1:{port}', 'wsgi:app'],
                                            cwd=str(root), env=environment, stdout=log, stderr=log)
                last_error = 'no response'
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError('Temporary load-test Gunicorn exited during startup')
                    try:
                        with urlopen(base + '/healthz', timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, OSError) as error:
                        last_error = f'{type(error).__name__}: {error}'
                        time.sleep(.1)
                else:
                    captured = logpath.read_text()[-3000:]
                    for secret in [key, password, *tokens]:
                        captured = captured.replace(secret, '[redacted]')
                    raise RuntimeError('Temporary load-test server startup timed out: ' + last_error + '\n' + captured)

                def run(extra):
                    before = set((root / 'test-results').glob('vps-load-*/summary.json'))
                    result = load_main(['--url', base, '--allow-local-http', '--k6-bin', args.k6_bin, *extra])
                    after = set((root / 'test-results').glob('vps-load-*/summary.json'))
                    assert len(after - before) == 1
                    report = json.loads(next(iter(after - before)).read_text())
                    assert result == 0 and report['passed'], report['failed_thresholds']
                    reports.append(report)
                    return report

                run(['--mode', 'public', '--users', '10', '--duration', '6s'])
                with patch('scripts.vps_load_test.getpass.getpass', return_value=password):
                    report = run(['--mode', 'bingo', '--users', str(args.users), '--duration', '12s',
                                  '--tickets', str(tickets), '--check-in', '--staff-user', 'load-fixture-staff', '--mark-every', '2'])
                assert report['metrics']['guests_ready']['values']['count'] == args.users
                assert report['metrics']['marks_saved']['values']['count'] >= args.users
                with app.app_context():
                    assert get_db().execute('SELECT COUNT(*) FROM checkins WHERE undone_at IS NULL').fetchone()[0] == args.users
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == args.users
                    assert get_db().execute('SELECT COUNT(DISTINCT attendee_pk) FROM bingo_cards').fetchone()[0] == args.users
                    assert get_db().execute('SELECT COUNT(*) FROM bingo_draws').fetchone()[0] == 0
                    assert get_db().execute('SELECT COUNT(*) FROM raffle_draws').fetchone()[0] == 0
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=20)
        captured = logpath.read_text()
        assert all(token not in captured for token in tokens) and password not in captured
        result = {'workers': 2, 'threads_per_worker': 4, 'users': args.users, 'public_polling': 'PASS',
                  'normal_staff_login_and_csrf_checkin': 'PASS', 'independent_card_sessions': 'PASS',
                  'continuous_polling_and_confirmed_marks': 'PASS', 'no_host_draws_or_raffle_mutations': 'PASS',
                  'no_tokens_or_passwords_in_logs': 'PASS', 'local_http_fixture_only': True}
        destination = root / 'test-results' / 'vps-load-local-results.json'
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
