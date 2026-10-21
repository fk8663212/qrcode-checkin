"""Two real Gunicorn workers, 200 players, concurrent draws and process restart."""
import concurrent.futures
import http.cookiejar
import json
import os
import re
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Barrier

from checkin import create_app
from checkin.db import get_db, now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee


def main():
    with tempfile.TemporaryDirectory(prefix='qrcode-bingo-workers-') as directory:
        folder = Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        key = secrets.token_hex(32)
        database = str(folder/'game.sqlite3')
        app = create_app({'TESTING':True,'CHECKIN_ENV':'test','DATABASE':database,'SECRET_KEY':key,'PUBLIC_BASE_URL':base,
                          'EVENT_NAME':'賓果多程序測試','INITIAL_ADMIN_USERNAME':'','INITIAL_ADMIN_PASSWORD_HASH':''})
        password = 'bingo-server-fixture-password!'
        with app.app_context(), transaction() as db:
            hashed = hash_password(password)
            for username, role in [('load-admin','admin'),('load-staff','staff')]:
                db.execute('INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)',(username,hashed,role,now()))
            for index in range(1,201):
                pk = insert_attendee(db,{'attendee_id':f'LOAD{index:03}','name':f'負載測試{index:03}','affiliation':'','table_no':str((index-1)//10+1)})
                db.execute('''INSERT INTO checkins(attendee_pk,ticket_id,checked_at,operator_id,operator_name,method,request_id)
                              VALUES (?,?,?,2,'load-staff','manual',?)''',
                           (pk,db.execute('SELECT id FROM tickets WHERE attendee_pk=?',(pk,)).fetchone()[0],now(),f'fixture-checkin-{index:04}'))
            tokens = [row[0] for row in db.execute('SELECT token FROM tickets ORDER BY attendee_pk')]
        environment = dict(os.environ,CHECKIN_ENV='test',DATABASE_PATH=database,SECRET_KEY=key,PUBLIC_BASE_URL=base,TRUST_PROXY='0',
                           EVENT_NAME='賓果多程序測試',INITIAL_ADMIN_USERNAME='',INITIAL_ADMIN_PASSWORD_HASH='',
                           EVENT_STARTS_AT='',EVENT_LOCATION='',EVENT_ORGANIZER='',EVENT_CONTACT='',EVENT_NOTICE='')
        logpath = folder/'gunicorn.log'
        log = logpath.open('w')
        process = None

        def start():
            process = subprocess.Popen([sys.executable,'-m','gunicorn','--config','gunicorn.conf.py','--bind',f'127.0.0.1:{port}','wsgi:app'],
                                       env=environment,stdout=log,stderr=log)
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError('Test Gunicorn exited during startup; private temporary log retained only for this test')
                try:
                    with urllib.request.urlopen(base+'/healthz',timeout=1) as response:
                        if response.status == 200:
                            return process
                except OSError:
                    time.sleep(.1)
            process.terminate(); process.wait(timeout=10)
            raise RuntimeError('Test Gunicorn startup timed out')

        def browser(admin=False):
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            if admin:
                with opener.open(base+'/login',timeout=15) as response:
                    page = response.read().decode()
                csrf = re.search(r'name="csrf-token" content="([^"]+)"',page)[1]
                data = urllib.parse.urlencode({'username':'load-admin','password':password,'csrf_token':csrf}).encode()
                with opener.open(base+'/login',data=data,timeout=15) as response:
                    assert response.geturl() == base+'/admin'
                    page = response.read().decode()
                csrf = re.search(r'name="csrf-token" content="([^"]+)"',page)[1]
            else:
                with opener.open(base+'/api/bingo/state',timeout=15) as response:
                    csrf = json.load(response)['csrf']
            return opener, csrf

        def post(client,path,data):
            opener,csrf = client
            request = urllib.request.Request(base+path,data=json.dumps(data).encode(),
                                             headers={'Content-Type':'application/json','X-CSRF-Token':csrf})
            try:
                response = opener.open(request,timeout=30)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.status,json.load(response)

        try:
            process = start()
            admin = browser(admin=True)
            assert post(admin,'/api/bingo/admin/registration',{'game_id':1,'open':True})[0] == 200
            def claim(token):
                client = browser()
                status,data = post(client,'/api/bingo/claim',{'token':token})
                assert status == 200
                return client,data['card']
            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
                participants = list(pool.map(claim,tokens))
            with app.app_context():
                assert get_db().execute('SELECT COUNT(*) FROM bingo_cards').fetchone()[0] == 200
            gate = Barrier(10)
            def draw(_):
                gate.wait(timeout=15)
                return post(admin,'/api/bingo/admin/draw',{'game_id':1,'expected_round':0})[0]
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                statuses = list(pool.map(draw,range(10)))
            assert statuses.count(200) == 1 and statuses.count(409) == 9
            def read_state(participant):
                (opener,_),before = participant
                started = time.monotonic()
                with opener.open(base+'/api/bingo/state',timeout=30) as response:
                    state = json.load(response)
                elapsed = (time.monotonic()-started)*1000
                assert len(state['draws']) == 1 and state['player_count'] == 200
                assert state['card']['id'] == before['id'] and state['card']['numbers'] == before['numbers']
                return elapsed
            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
                timings = list(pool.map(read_state,participants))
            process.terminate(); process.wait(timeout=15)
            process = start()
            with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
                list(pool.map(read_state,participants))
            with urllib.request.urlopen(base+'/api/bingo/display-state',timeout=15) as response:
                public = json.load(response)
            assert all(field not in public for field in ('card','players','tickets','games','round_results'))
        finally:
            if process and process.poll() is None:
                process.terminate(); process.wait(timeout=15)
            log.close()
        captured = logpath.read_text()
        assert all(token not in captured for token in tokens)
        result = {'workers':2,'threads_per_worker':4,'players':200,'concurrent_client_tasks':24,'shared_ip_claims':'PASS',
                  'parallel_draw_requests':10,'successful_draws':1,'stale_draw_rejections':9,'cross_worker_state':'PASS',
                  'real_process_restart_preserves_200_cards':'PASS','public_state_privacy':'PASS','no_ticket_token_in_logs':'PASS',
                  'state_mean_ms':round(statistics.mean(timings),2),'state_p95_ms':round(sorted(timings)[189],2)}
        output = Path('test-results'); output.mkdir(exist_ok=True)
        (output/'bingo-server-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
