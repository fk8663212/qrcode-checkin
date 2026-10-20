import os

# Protect SQLite WAL, shared memory files and any future private files.
os.umask(0o077)

from checkin import create_app

app = create_app()
