import io, os, sqlite3, tempfile, unittest
from pathlib import Path
import app, migrations, webapp, worker

class WebProductionPrepTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.old_db=webapp.DB;self.old_key=webapp.SESSION_KEY;self.old_pass=webapp.PASSWORD_HASH;self.old_appdb=app.DB;self.old_dbpath=os.environ.get('DATABASE_PATH')
  webapp.DB=Path(self.t.name)/'web.sqlite';os.environ['DATABASE_PATH']=str(webapp.DB);webapp.SESSION_KEY='test-session-secret-not-real';webapp.USER='tester';webapp.PASSWORD_HASH=webapp.password_hash('test-password')
  c=webapp.db();
  c.execute("INSERT INTO stories(story_id,title,source_path,vault_root,source_file_sha256,canonical_text_sha256,extractor_version,extracted_text,created_at,updated_at) VALUES('tml-0001','Test Story','x.docx','/tmp','x','x','v1','A story',?,?)",(app.now(),app.now()));c.commit();c.close()
 def tearDown(self):
  webapp.DB=self.old_db;os.environ.pop('DATABASE_URL',None); (os.environ.__setitem__('DATABASE_PATH',self.old_dbpath) if self.old_dbpath is not None else os.environ.pop('DATABASE_PATH',None));webapp.SESSION_KEY=self.old_key;webapp.PASSWORD_HASH=self.old_pass;app.DB=self.old_appdb;self.t.cleanup()
 def jpeg(self):
  import io
  from PIL import Image
  b=io.BytesIO();Image.new('RGB',(2,2),(12,22,32)).save(b,format='JPEG');return b.getvalue()
 def login_body(self):return f'username=tester&password=test-password&login_csrf={webapp.login_csrf()}'.encode()
 def request(self,path='/',method='GET',body=b'',cookie=''):
  environ={'REQUEST_METHOD':method,'PATH_INFO':path,'QUERY_STRING':'','CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':'application/x-www-form-urlencoded','HTTP_COOKIE':cookie,'wsgi.input':io.BytesIO(body)};capt={}
  def start(status,headers):capt['status']=status;capt['headers']=headers
  out=b''.join(webapp.app_wsgi(environ,start)).decode();return capt,out
 def test_password_hash_and_signed_session(self):
  h=webapp.password_hash('p');self.assertTrue(webapp.check_password('p',h));self.assertFalse(webapp.check_password('bad',h))
  v=webapp.token('user',9999999999,'n');self.assertEqual(webapp.decode_cookie(v),('user','n'));self.assertIsNone(webapp.decode_cookie(v+'x'))
 def test_authentication_required_and_login(self):
  r,_=self.request('/stories');self.assertEqual(r['status'],'303 See Other')
  r,_=self.request('/login','POST',self.login_body());self.assertTrue(r['status'].startswith('303'))
  cookie=[v.split(';')[0] for k,v in r['headers'] if k=='Set-Cookie'][0]
  r,body=self.request('/stories',cookie=cookie);self.assertIn('Test Story',body)
 def test_session_is_persisted_and_revoked_on_logout(self):
  r,_=self.request('/login','POST',self.login_body());cookie=[v.split(';')[0] for k,v in r['headers'] if k=='Set-Cookie'][0];val=cookie.split('=',1)[1];user,nonce=webapp.decode_cookie(val)
  c=webapp.db();self.assertTrue(webapp.valid_session(c,user,nonce));c.close()
  r,_=self.request('/logout','POST',f'csrf={webapp.csrf(user,nonce)}'.encode(),cookie);self.assertTrue(r['status'].startswith('303'))
  c=webapp.db();self.assertFalse(webapp.valid_session(c,user,nonce));c.close()
 def test_failed_login_lockout_is_persisted(self):
  for _ in range(8):self.request('/login','POST',f'username=bad&password=bad&login_csrf={webapp.login_csrf()}'.encode())
  r,_=self.request('/login','POST',self.login_body());self.assertEqual(r['status'],'429 Too Many Requests')
 def test_healthz_checks_database_without_auth(self):
  r,body=self.request('/healthz');self.assertEqual(r['status'],'200 OK');self.assertEqual(body,'healthy')
  import database
  original=database.connect;database.connect=lambda:(_ for _ in ()).throw(OSError('synthetic database outage'))
  try:r,_=self.request('/healthz');self.assertEqual(r['status'],'503 Service Unavailable')
  finally:database.connect=original
 def test_csrf_required_for_mutations(self):
  r,_=self.request('/login','POST',self.login_body());cookie=[v.split(';')[0] for k,v in r['headers'] if k=='Set-Cookie'][0]
  r,_=self.request('/pause','POST',b'',cookie);self.assertEqual(r['status'],'403 Forbidden')
 def test_authenticated_batch_approval_covers_exact_posts(self):
  from argparse import Namespace
  media=Path(self.t.name)/'batch.jpg';media.write_bytes(self.jpeg())
  for caption in ('one','two'):
   c=webapp.db();app.add_post(c,Namespace(promotion='story',story=['tml-0001'],caption=caption,format='image',media=[str(media)],alt_text=None));c.close()
  r,_=self.request('/login','POST',self.login_body());cookie=[v.split(';')[0] for k,v in r['headers'] if k=='Set-Cookie'][0];val=cookie.split('=',1)[1];usr,nonce=webapp.decode_cookie(val);csrf=webapp.csrf(usr,nonce)
  r,_=self.request('/review-batch','POST',f'csrf={csrf}&post_id=ig-0001&post_id=ig-0002'.encode(),cookie)
  self.assertTrue(r['status'].startswith('303'))
  c=webapp.db();self.assertEqual(c.execute("select count(*) from posts where status='batch_approved'").fetchone()[0],2);self.assertEqual(c.execute('select count(*) from batches').fetchone()[0],1);c.close()
 def test_migrations_idempotent_and_preserve_story(self):
  c=webapp.db();before=c.execute('select source_file_sha256,canonical_text_sha256 from stories').fetchone();migrations.apply(c);after=c.execute('select source_file_sha256,canonical_text_sha256 from stories').fetchone()
  self.assertEqual(tuple(before),tuple(after));self.assertEqual(c.execute('select count(*) from schema_migrations').fetchone()[0],2);c.close()
 def test_global_pause_leaves_due_job_queued(self):
  from argparse import Namespace
  media=Path(self.t.name)/'paused.jpg';media.write_bytes(self.jpeg());c=webapp.db()
  app.add_post(c,Namespace(promotion='story',story=['tml-0001'],caption='pause',format='image',media=[str(media)],alt_text=None));pid=c.execute('select post_id from posts').fetchone()[0];app.set_review(c,pid);app.create_batch(c,[pid],'test');p=c.execute('select * from posts where post_id=?',(pid,)).fetchone();v=c.execute('select * from content_versions where version_id=?',(p['current_version_id'],)).fetchone();b=c.execute('select * from batches where batch_id=?',(p['approval_batch_id'],)).fetchone();c.execute("update posts set status='scheduled',scheduled_at='2000-01-01T00:00:00+00:00' where post_id=?",(pid,));c.execute("update settings set value='1' where key='global_pause'");c.execute('insert into publish_jobs(job_id,post_id,version_id,batch_id,run_at,status,created_at) values(?,?,?,?,?,?,?)',('pausedjob',pid,v['version_id'],b['batch_id'],'2000-01-01T00:00:00+00:00','queued',app.now()));c.commit();c.close()
  self.assertFalse(worker.tick());c=webapp.db();self.assertEqual(c.execute("select status from publish_jobs where job_id='pausedjob'").fetchone()[0],'queued');c.execute("update settings set value='0' where key='global_pause'");c.commit();c.close()
  self.assertTrue(worker.tick())
 def test_worker_retries_only_transient_errors_then_completes(self):
  from argparse import Namespace
  media=Path(self.t.name)/'retry.jpg';media.write_bytes(self.jpeg())
  c=webapp.db();app.add_post(c,Namespace(promotion='story',story=['tml-0001'],caption='retry',format='image',media=[str(media)],alt_text=None));pid=c.execute('select post_id from posts order by created_at desc').fetchone()[0];app.set_review(c,pid);app.create_batch(c,[pid],'test');p=c.execute('select * from posts where post_id=?',(pid,)).fetchone();v=c.execute('select * from content_versions where version_id=?',(p['current_version_id'],)).fetchone();b=c.execute('select * from batches where batch_id=?',(p['approval_batch_id'],)).fetchone();run='2000-01-01T00:00:00+00:00';c.execute("update posts set status='scheduled',scheduled_at=? where post_id=?",(run,pid));c.execute('insert into publish_jobs(job_id,post_id,version_id,batch_id,run_at,status,created_at) values(?,?,?,?,?,?,?)',('retryjob',pid,v['version_id'],b['batch_id'],run,'queued',app.now()));c.commit();c.close()
  original=worker.app.fake_publish
  def temporary_failure(c,p):raise TimeoutError('synthetic transient')
  worker.app.fake_publish=temporary_failure
  try:self.assertTrue(worker.tick())
  finally:worker.app.fake_publish=original
  c=webapp.db();self.assertEqual(c.execute("select status from publish_jobs where job_id='retryjob'").fetchone()[0],'queued');c.execute("update publish_jobs set run_at='2000-01-01T00:00:00+00:00' where job_id='retryjob'");c.commit();c.close()
  self.assertTrue(worker.tick());c=webapp.db();self.assertEqual(c.execute("select status from publish_jobs where job_id='retryjob'").fetchone()[0],'done');c.close()
 def test_worker_claim_publishes_once_and_marks_job_done(self):
  from argparse import Namespace
  media=Path(self.t.name)/'x.jpg';media.write_bytes(self.jpeg())
  args=Namespace(promotion='story',story=['tml-0001'],caption='test',format='image',media=[str(media)],alt_text=None);app.add_post(webapp.db(),args)
  c=webapp.db();pid=c.execute('select post_id from posts').fetchone()[0];app.set_review(c,pid);app.create_batch(c,[pid],'test');p=c.execute('select * from posts where post_id=?',(pid,)).fetchone();b=c.execute('select * from batches where batch_id=?',(p['approval_batch_id'],)).fetchone();v=c.execute('select * from content_versions where version_id=?',(p['current_version_id'],)).fetchone();run='2000-01-01T00:00:00+00:00';c.execute("update posts set status='scheduled',scheduled_at=? where post_id=?",(run,pid));c.execute('insert into publish_jobs(job_id,post_id,version_id,batch_id,run_at,status,created_at) values(?,?,?,?,?,?,?)',('job1',pid,v['version_id'],b['batch_id'],run,'queued',app.now()));c.commit();c.close()
  self.assertTrue(worker.tick());self.assertFalse(worker.tick())
  c=webapp.db();self.assertEqual(c.execute("select status from publish_jobs where job_id='job1'").fetchone()[0],'done');self.assertEqual(c.execute('select count(*) from attempts').fetchone()[0],1);c.close()

if __name__=='__main__':unittest.main(verbosity=2)
