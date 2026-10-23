from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from flask import Flask

from checkin import db as database


def test_simultaneous_new_database_initialization_enables_wal_and_preserves_schema(tmp_path,monkeypatch):
    real_connect=database.connect
    for run in range(20):
        path=tmp_path/f'parallel-{run}.sqlite3'
        gate=Barrier(4)
        def synchronized_connect(path):
            connection=real_connect(path)
            gate.wait(timeout=5)
            return connection
        monkeypatch.setattr(database,'connect',synchronized_connect)
        def initialize(index):
            app=Flask(f'initialization-{run}-{index}')
            app.config['DATABASE']=str(path)
            database.init_db(app)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(initialize,range(4)))
        with real_connect(path) as db:
            assert db.execute('PRAGMA journal_mode').fetchone()[0]=='wal'
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {'users','attendees','checkins','bingo_games','raffle_rounds','raffle_winners'}.issubset(tables)
            assert db.execute("SELECT COUNT(*) FROM settings WHERE key='roster_version'").fetchone()[0]==1
