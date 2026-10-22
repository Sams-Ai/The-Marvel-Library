"""DB-API connection facade: SQLite locally, PostgreSQL via psycopg in production."""
import os,sqlite3
from pathlib import Path

class Row(dict):
    def __init__(self,keys,values):super().__init__(zip(keys,values));self._values=tuple(values)
    def __getitem__(self,key):return self._values[key] if isinstance(key,int) else super().__getitem__(key)
class Cursor:
    def __init__(self,c):self.c=c
    def fetchone(self):
        r=self.c.fetchone()
        if r is None:return None
        if isinstance(r,dict):return Row(tuple(r.keys()),tuple(r.values()))
        return r
    def fetchall(self):return [self._row(r) for r in self.c.fetchall()]
    def _row(self,r):return Row(tuple(r.keys()),tuple(r.values())) if isinstance(r,dict) else r
    def __iter__(self):
        for r in self.c:yield self._row(r)
    @property
    def rowcount(self):return self.c.rowcount

class PostgresConnection:
    def __init__(self,conn):self.conn=conn
    def execute(self,sql,params=()):
        sql=sql.replace('?','%s')
        return Cursor(self.conn.execute(sql,params))
    def commit(self):self.conn.commit()
    def rollback(self):self.conn.rollback()
    def close(self):self.conn.close()

def connect(database_url=None):
    url=database_url or os.getenv('DATABASE_URL','')
    if url.startswith(('postgres://','postgresql://')):
        try:import psycopg
        except ImportError as e:raise RuntimeError('PostgreSQL requires psycopg; install requirements-production.txt') from e
        return PostgresConnection(psycopg.connect(url,row_factory=psycopg.rows.dict_row))
    path=url[7:] if url.startswith('sqlite:') else url or os.getenv('DATABASE_PATH','marvel_library.sqlite3')
    c=sqlite3.connect(path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def migrate_sqlite_to_postgres(sqlite_path,pg_url,fail_after=None):
    """Copy all known application rows to an EMPTY migrated PostgreSQL DB, transactionally.
    SQLite source is opened read-only and never altered. Destination must have no app data.
    """
    import psycopg
    from migrations_pg import apply
    srcpath=Path(sqlite_path).resolve()
    src=sqlite3.connect('file:'+srcpath.as_posix()+'?mode=ro',uri=True);src.row_factory=sqlite3.Row
    dst=psycopg.connect(pg_url)
    tables=['stories','campaigns','posts','content_versions','post_stories','media_assets','batches','attempts','settings','post_details','publish_jobs','audit_events','auth_sessions','auth_login_limits']
    try:
      with dst.transaction():
       apply(dst)
       copied=0
       for table in tables:
        count=dst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if table=='settings' and count==1:
         default=dst.execute("SELECT value FROM settings WHERE key='global_pause'").fetchone()
         if default and default[0]=='0':dst.execute("DELETE FROM settings WHERE key='global_pause'");count=0
        if count:raise RuntimeError(f'Destination table {table} is not empty; refusing merge')
       for table in tables:
        if table not in {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}:continue
        cols=[r[1] for r in src.execute(f'PRAGMA table_info("{table}")')]
        if not cols:continue
        quoted=','.join('"'+x.replace('"','""')+'"' for x in cols)
        vals=','.join(['%s']*len(cols))
        rows=src.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
        if rows:
         with dst.cursor() as cur:cur.executemany(f'INSERT INTO "{table}" ({quoted}) VALUES ({vals})',[tuple(x) for x in rows])
         copied+=len(rows)
         if fail_after is not None and copied>=fail_after:raise RuntimeError('simulated copy interruption')
       for table in tables:
        if table not in {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}:continue
        expected=src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        actual=dst.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if expected!=actual:raise RuntimeError(f'Row-count mismatch in {table}: {expected} vs {actual}')
       old=src.execute('SELECT story_id,source_file_sha256,canonical_text_sha256 FROM stories ORDER BY story_id').fetchall()
       new=dst.execute('SELECT story_id,source_file_sha256,canonical_text_sha256 FROM stories ORDER BY story_id').fetchall()
       if [tuple(r) for r in old]!=[tuple(r) for r in new]:raise RuntimeError('Story IDs/source hashes differ after migration')
      return {'status':'verified','tables':tables,'stories':len(old)}
    finally:src.close();dst.close()
