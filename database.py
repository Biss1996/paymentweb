"""SQLite locally; PostgreSQL for durable serverless deployments."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
import psycopg
from psycopg.rows import dict_row

INTEGRITY_ERRORS=(sqlite3.IntegrityError,psycopg.IntegrityError)

class PostgresConnection:
    def __init__(self,url):
        self.db=psycopg.connect(url,connect_timeout=10,row_factory=dict_row,prepare_threshold=None)
    def execute(self,sql,params=()):
        if sql=='BEGIN IMMEDIATE':
            # Serializes short financial write transactions across serverless instances.
            return self.db.execute('SELECT pg_advisory_xact_lock(76421109)')
        return self.db.execute(sql.replace('?','%s'),params)
    def commit(self): self.db.commit()
    def rollback(self): self.db.rollback()
    def close(self): self.db.close()

@contextmanager
def connect(app):
    url=app.config.get('DATABASE_URL')
    if url: db=PostgresConnection(url)
    else:
        db=sqlite3.connect(app.config['DB'],timeout=30)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback(); raise
    finally: db.close()

def migrate(app):
    root=Path(__file__).parent
    if app.config.get('DATABASE_URL'):
        with connect(app) as db:
            for sql in (root/'schema.postgres.sql').read_text().split(';'):
                if sql.strip(): db.execute(sql)
    else:
        Path(app.config['DB']).parent.mkdir(parents=True,exist_ok=True)
        with connect(app) as db:
            db.executescript((root/'schema.sql').read_text())
            cols={x['name'] for x in db.execute('PRAGMA table_info(events)')}
            for col,kind in [('lease_token','TEXT'),('lease_until','INTEGER NOT NULL DEFAULT 0'),('created','INTEGER NOT NULL DEFAULT 0')]:
                if col not in cols: db.execute(f'ALTER TABLE events ADD COLUMN {col} {kind}')
