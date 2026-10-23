"""Two real Gunicorn workers: idempotency, prize capacity, replacement and restart."""
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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from threading import Barrier

from checkin import create_app
from checkin.db import now, transaction
from checkin.security import hash_password
from checkin.services import insert_attendee


def main():
    with tempfile.TemporaryDirectory(prefix='qrcode-raffle-workers-') as directory:
        folder=Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        key=secrets.token_hex(32)
        database=str(folder/'event.sqlite3')
        app=create_app({'TESTING':True,'CHECKIN_ENV':'test','DATABASE':database,'SECRET_KEY':key,'PUBLIC_BASE_URL':base,
                        'INITIAL_ADMIN_USERNAME':'','INITIAL_ADMIN_PASSWORD_HASH':''})
        password='raffle-workers-fixture-password!'
        with app.app_context(),transaction() as db:
            db.execute("INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,'admin',?)",('raffle-admin',hash_password(password),now()))
            for index in range(1,201):
                pk=insert_attendee(db,{'attendee_id':f'RF{index:03}','name':f'多程序來賓{index}','affiliation':'','table_no':'01'})
                db.execute('''INSERT INTO checkins(attendee_pk,checked_at,operator_id,operator_name,method,request_id)
                    VALUES (?,?,1,'raffle-admin','manual',?)''',(pk,now(),f'fixture-checkin-{index:04}'))
            tokens=[row[0] for row in db.execute('SELECT token FROM tickets')]
        environment=dict(os.environ,CHECKIN_ENV='test',DATABASE_PATH=database,SECRET_KEY=key,PUBLIC_BASE_URL=base,TRUST_PROXY='0',
                         INITIAL_ADMIN_USERNAME='',INITIAL_ADMIN_PASSWORD_HASH='',EVENT_STARTS_AT='',EVENT_LOCATION='',
                         EVENT_ORGANIZER='',EVENT_CONTACT='',EVENT_NOTICE='')
        logpath=folder/'gunicorn.log'
        process=None
        with logpath.open('w') as log:
            def start():
                candidate=subprocess.Popen([sys.executable,'-m','gunicorn','--config','gunicorn.conf.py','--bind',f'127.0.0.1:{port}','wsgi:app'],env=environment,stdout=log,stderr=log)
                for _ in range(120):
                    if candidate.poll() is not None:
                        raise RuntimeError('Temporary raffle test server exited')
                    try:
                        with urllib.request.urlopen(base+'/healthz',timeout=1) as response:
                            if response.status==200:return candidate
                    except OSError:
                        time.sleep(.1)
                candidate.terminate();candidate.wait(timeout=15)
                raise RuntimeError('Temporary raffle test server startup timed out')

            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            csrf=None
            def post(action,**data):
                request=urllib.request.Request(base+'/api/raffle/admin/'+action,data=json.dumps({'round_id':1,**data}).encode(),
                                               headers={'Content-Type':'application/json','X-CSRF-Token':csrf})
                try:response=opener.open(request,timeout=30)
                except urllib.error.HTTPError as error:response=error
                with response:return response.status,json.load(response)

            def parallel(operation):
                gate=Barrier(10)
                def run(index):
                    gate.wait(timeout=15)
                    return operation(index)
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                    return list(pool.map(run,range(10)))

            try:
                process=start()
                with opener.open(base+'/login',timeout=15) as response:
                    csrf=re.search(r'name="csrf-token" content="([^"]+)"',response.read().decode())[1]
                login=urllib.parse.urlencode({'username':'raffle-admin','password':password,'csrf_token':csrf}).encode()
                with opener.open(base+'/login',data=login,timeout=15) as response:
                    assert response.geturl()==base+'/admin'
                    csrf=re.search(r'name="csrf-token" content="([^"]+)"',response.read().decode())[1]
                status,data=post('prize',name='第一份獎',quantity=1)
                assert status==200
                first=data['prizes'][-1]['id']
                request_id=secrets.token_hex(24)
                shared=parallel(lambda _:post('draw',prize_id=first,count=1,request_id=request_id))
                assert all(status==200 for status,_ in shared)
                assert len({data['result']['draw_id'] for _,data in shared})==1
                original=shared[0][1]['winners'][0]
                status,data=post('prize',name='第二份獎',quantity=1)
                second=data['prizes'][-1]['id']
                unique=parallel(lambda _:post('draw',prize_id=second,count=1,request_id=secrets.token_hex(24))[0])
                assert unique.count(200)==1 and unique.count(409)==9
                replacements=parallel(lambda _:post('redraw',prize_id=first,count=1,winner_id=original['id'],reason='測試放棄',confirm=True,request_id=secrets.token_hex(24))[0])
                assert replacements.count(200)==1 and replacements.count(409)==9
                status,data=post('prize',name='全場獎',quantity=198)
                final=data['prizes'][-1]['id']
                status,data=post('draw',prize_id=final,count=198,request_id=secrets.token_hex(24))
                assert status==200
                active=[w for w in data['winners'] if not w['voided_at']]
                assert len(active)==len({w['attendee_pk'] for w in active})==200
                before=data['winners']
                process.terminate();process.wait(timeout=15)
                process=start()
                with opener.open(base+'/api/raffle/admin/state',timeout=30) as response:
                    after=json.load(response)
                assert after['winners']==before and len(after['draws'])==4
                assert len(after['rounds'])==1
                assert post('draw',prize_id=first,count=1,request_id=request_id)[1]['result']['replayed'] is True
                with urllib.request.urlopen(base+'/api/raffle/display-state',timeout=30) as response:
                    public=json.load(response)
                assert len(public['winners'])==200 and 'draws' not in public
                assert '多程序來賓' not in json.dumps(public,ensure_ascii=False)
            finally:
                if process and process.poll() is None:
                    process.terminate();process.wait(timeout=15)
        captured=logpath.read_text()
        assert all(token not in captured for token in tokens)
        result={'workers':2,'threads_per_worker':4,'checked_in_guests':200,'parallel_requests_per_case':10,
                'same_request_awards_once':'PASS','distinct_requests_respect_prize_capacity':'PASS',
                'parallel_redraw_replaces_once':'PASS','two_hundred_unique_active_winners':'PASS',
                'real_restart_preserves_awards_and_retry_identity':'PASS','public_privacy':'PASS','no_ticket_tokens_in_logs':'PASS'}
        output=Path('test-results');output.mkdir(exist_ok=True)
        (output/'raffle-server-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
