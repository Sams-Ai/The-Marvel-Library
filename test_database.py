import hashlib,os,sqlite3,tempfile,unittest
from pathlib import Path
from database import PostgresConnection,Row
from migrations_pg import apply

class FakeCursor:
 def __init__(self,rows=()):self.rows=list(rows);self.rowcount=0
 def fetchall(self):return self.rows
 def fetchone(self):return self.rows.pop(0) if self.rows else None
class FakePG:
 def __init__(self):self.versions=set();self.statements=[]
 def execute(self,sql,params=()):
  self.statements.append(sql)
  if sql.startswith('SELECT version'):return FakeCursor([(x,) for x in self.versions])
  if sql.startswith('INSERT INTO schema_migrations'):self.versions.add(params[0])
  return FakeCursor()
class DatabaseTests(unittest.TestCase):
 def test_row_supports_mapping_and_tuple_access(self):
  r=Row(['story_id','title'],['tml-1','Story']);self.assertEqual(r['story_id'],'tml-1');self.assertEqual(r[1],'Story')
 def test_postgres_adapter_converts_placeholders(self):
  class C:
   def execute(self,q,p):self.q=q;self.p=p;return FakeCursor()
  c=C();db=PostgresConnection(c);db.execute('SELECT * FROM x WHERE id=? AND z=?',(1,2));self.assertEqual(c.q,'SELECT * FROM x WHERE id=%s AND z=%s')
 def test_pg_migrations_idempotent_and_interruption_is_retryable(self):
  c=FakePG()
  with self.assertRaises(RuntimeError):apply(c,fail_after=3)
  self.assertFalse(c.versions)
  c.statements=[];apply(c);self.assertEqual(c.versions,{1,2})
  n=len(c.statements);apply(c);self.assertEqual(c.versions,{1,2});self.assertEqual(len(c.statements),n+3)

@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'),'no isolated TEST_POSTGRES_URL configured; PostgreSQL integration skipped')
class PostgresMigrationIntegration(unittest.TestCase):
 def test_schema_copy_rollback_repeat_and_populated_target_rejection(self):
  import app,migrations,database,psycopg
  d=tempfile.TemporaryDirectory();src=Path(d.name)/'source.sqlite';c=sqlite3.connect(src);app.init_db(c);migrations.apply(c);stamp=app.now()
  c.execute('INSERT INTO stories(story_id,title,source_path,vault_root,source_file_sha256,canonical_text_sha256,extractor_version,extracted_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',('tml-9001','Synthetic','a.docx','/read-only','raw-hash','text-hash','v1','synthetic story',stamp,stamp))
  c.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',('camp-1','Launch','','active',stamp,stamp))
  c.execute('INSERT INTO posts(post_id,promotion_type,status,current_version_id,scheduled_at,approval_batch_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',('ig-9001','story','scheduled','ver-1','2030-01-01T00:00:00+00:00','batch-1',stamp,stamp))
  c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',('ver-1','ig-9001',1,'Approved caption','[]','image',None,'[]','content-hash',stamp))
  c.execute('INSERT INTO post_stories VALUES(?,?,?)',('ig-9001','tml-9001','text-hash'))
  c.execute('INSERT INTO media_assets VALUES(?,?,?,?,?)',('asset-1','s3://private/key','asset-hash','image/jpeg',stamp))
  c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',('batch-1','approved','[{"post_id":"ig-9001","version_id":"ver-1","content_hash":"content-hash"}]','operator',stamp,None,None,stamp))
  c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',('attempt-1','ig-8001','ver-old','batch-old',1,'fake','failed',stamp,stamp,None,None,'synthetic'))
  c.execute("UPDATE settings SET value='1' WHERE key='global_pause'")
  c.execute('INSERT INTO post_details VALUES(?,?,?,?,?)',('ig-9001','camp-1','launch','readers',0))
  c.execute('INSERT INTO publish_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?)',('job-1','ig-9001','ver-1','batch-1','2030-01-01T00:00:00+00:00','queued',0,None,None,None,stamp))
  c.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?)',('event-1','approved','operator','batch-1','{}',stamp))
  c.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',('session-hash','operator','2031-01-01T00:00:00+00:00',stamp))
  c.execute('INSERT INTO auth_login_limits VALUES(?,?,?,?)',('user-hash',2,stamp,None));c.commit();c.close()
  source_hash=hashlib.sha256(src.read_bytes()).hexdigest();dsn=os.environ['TEST_POSTGRES_URL']
  # The injected failure occurs after the first table's data insert, inside the transaction.
  with self.assertRaises(RuntimeError):database.migrate_sqlite_to_postgres(str(src),dsn,fail_after=1)
  pg=psycopg.connect(dsn)
  try:
   exists=pg.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name IN ('stories','schema_migrations','publish_jobs') ORDER BY table_name").fetchall()
   self.assertEqual(exists,[],'transactional DDL and copied rows should all roll back')
  finally:pg.close()
  result=database.migrate_sqlite_to_postgres(str(src),dsn);self.assertEqual(result['status'],'verified')
  pg=psycopg.connect(dsn)
  try:
   self.assertEqual(pg.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall(),[(1,),(2,)])
   tables=['stories','campaigns','posts','content_versions','post_stories','media_assets','batches','attempts','settings','post_details','publish_jobs','audit_events','auth_sessions','auth_login_limits']
   for table in tables:
    self.assertEqual(pg.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],c_count(src,table),table)
   row=pg.execute("SELECT story_id,source_file_sha256,canonical_text_sha256 FROM stories WHERE story_id='tml-9001'").fetchone();self.assertEqual(row,('tml-9001','raw-hash','text-hash'))
   self.assertEqual(pg.execute('SELECT title,status FROM campaigns').fetchone(),('Launch','active'))
   self.assertEqual(pg.execute('SELECT post_id,status,current_version_id FROM posts').fetchone(),('ig-9001','scheduled','ver-1'))
   self.assertEqual(pg.execute('SELECT post_id,story_id,source_hash FROM post_stories').fetchone(),('ig-9001','tml-9001','text-hash'))
   self.assertEqual(pg.execute('SELECT version_id,content_hash FROM content_versions').fetchone(),('ver-1','content-hash'))
   self.assertEqual(pg.execute('SELECT asset_id,sha256 FROM media_assets').fetchone(),('asset-1','asset-hash'))
   batch=pg.execute('SELECT batch_id,status,approved_by,entries_json FROM batches').fetchone();self.assertEqual(batch[:3],('batch-1','approved','operator'));self.assertEqual(__import__('json').loads(batch[3]),[{"post_id":"ig-9001","version_id":"ver-1","content_hash":"content-hash"}])
   self.assertEqual(pg.execute('SELECT attempt_id,outcome,media_id FROM attempts').fetchone(),('attempt-1','failed',None))
   self.assertEqual(pg.execute('SELECT campaign_id,purpose,audience FROM post_details').fetchone(),('camp-1','launch','readers'))
   self.assertEqual(pg.execute('SELECT job_id,version_id,status FROM publish_jobs').fetchone(),('job-1','ver-1','queued'))
   self.assertEqual(pg.execute('SELECT event_id,event_type,actor FROM audit_events').fetchone(),('event-1','approved','operator'))
   self.assertEqual(pg.execute("SELECT value FROM settings WHERE key='global_pause'").fetchone(),('1',))
   self.assertEqual(pg.execute('SELECT session_hash,username FROM auth_sessions').fetchone(),('session-hash','operator'))
   self.assertEqual(pg.execute('SELECT username_hash,failed_count FROM auth_login_limits').fetchone(),('user-hash',2))
   before={t:pg.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}
   with self.assertRaises(RuntimeError):database.migrate_sqlite_to_postgres(str(src),dsn)
   after={t:pg.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables};self.assertEqual(before,after)
   apply(pg);pg.commit();self.assertEqual(pg.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall(),[(1,),(2,)])
  finally:pg.close()
  self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(),source_hash,'SQLite source must remain byte-identical')
  d.cleanup()

def c_count(path,table):
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True)
 try:return c.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
 finally:c.close()
if __name__=='__main__':unittest.main(verbosity=2)
