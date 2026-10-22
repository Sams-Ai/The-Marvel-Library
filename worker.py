#!/usr/bin/env python3
"""Persistent local SQLite job runner. Fake publisher only; no Meta/network."""
import argparse,os,socket,time,uuid,json
from datetime import datetime,timezone,timedelta
import app,migrations,database
from logging_utils import emit

def open_db():
 c=database.connect()
 if os.getenv('DATABASE_URL','').startswith(('postgres://','postgresql://')):
  import migrations_pg;migrations_pg.apply(c);c.commit()
 else:app.init_db(c);migrations.apply(c)
 return c

def audit(c,event,job,details):
 c.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),event,'worker',job,json.dumps(details),app.now()))

def tick():
 c=open_db()
 now=datetime.now(timezone.utc).isoformat();owner=socket.gethostname()+':'+str(os.getpid())
 try:
  is_pg=os.getenv('DATABASE_URL','').startswith(('postgres://','postgresql://'))
  c.execute('BEGIN' if is_pg else 'BEGIN IMMEDIATE')
  pause=c.execute("SELECT value FROM settings WHERE key='global_pause'").fetchone()
  if pause and pause[0]=='1':c.rollback();return False
  query="SELECT * FROM publish_jobs WHERE (status='queued' OR (status='claimed' AND lease_until<?)) AND run_at<=? ORDER BY run_at LIMIT 1"
  if is_pg:query=query.replace('?','%s')+' FOR UPDATE SKIP LOCKED'
  row=c.execute(query,(now,now)).fetchone()
  if not row:c.rollback();return False
  c.execute("UPDATE publish_jobs SET status='claimed',lease_owner=?,lease_until=?,attempts=attempts+1 WHERE job_id=?",(owner,(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),row['job_id']))
  audit(c,'worker_claimed',row['job_id'],{'post_id':row['post_id'],'version_id':row['version_id'],'attempt':row['attempts']+1});c.commit()
  emit('info','job_claimed',job_id=row['job_id'],post_id=row['post_id'],version_id=row['version_id'],attempt=row['attempts']+1)
  p=c.execute('SELECT * FROM posts WHERE post_id=?',(row['post_id'],)).fetchone()
  if p and p['status']=='published':
   c.execute("UPDATE publish_jobs SET status='done',lease_owner=NULL,lease_until=NULL WHERE job_id=?",(row['job_id'],));c.commit();return True
  if not p or p['current_version_id']!=row['version_id'] or p['approval_batch_id']!=row['batch_id']:
   c.execute("UPDATE publish_jobs SET status='blocked',last_error='stale approved version',lease_owner=NULL,lease_until=NULL WHERE job_id=?",(row['job_id'],));audit(c,'worker_blocked',row['job_id'],{'reason':'stale approved version'});c.commit();return True
  try:
   # Explicitly fake path. app.fake_publish performs pause, approval, source and media checks.
   app.fake_publish(c,row['post_id'])
   c.execute("UPDATE publish_jobs SET status='done',lease_owner=NULL,lease_until=NULL,last_error=NULL WHERE job_id=?",(row['job_id'],));audit(c,'worker_completed',row['job_id'],{'post_id':row['post_id'],'version_id':row['version_id']});c.commit()
   emit('info','simulation_completed',job_id=row['job_id'],post_id=row['post_id'])
  except Exception as e:
   if isinstance(e,ValueError) and str(e)=='Global pause is enabled':
    retry_at=(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()
    c.execute("UPDATE publish_jobs SET status='queued',run_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?",(retry_at,row['job_id']));audit(c,'job_released_for_pause',row['job_id'],{});c.commit();return True
   transient=isinstance(e,(TimeoutError,ConnectionError,OSError)) or type(e).__name__ in ('OperationalError','InterfaceError','SerializationFailure','DeadlockDetected')
   retries_left=row['attempts']<4
   if transient and retries_left:
    delay=min(3600,30*(2**row['attempts']));retry_at=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat()
    c.execute("UPDATE publish_jobs SET status='queued',run_at=?,last_error=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?",(retry_at,type(e).__name__,row['job_id']))
    audit(c,'worker_retry_scheduled',row['job_id'],{'error_type':type(e).__name__,'retry_in_seconds':delay})
    emit('warning','job_retry_scheduled',job_id=row['job_id'],retry_in_seconds=delay,error_type=type(e).__name__)
   else:
    safe= str(e)[:200] if isinstance(e,ValueError) else type(e).__name__
    c.execute("UPDATE publish_jobs SET status='failed',last_error=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?",(safe,row['job_id']))
    audit(c,'worker_failed',row['job_id'],{'error_type':type(e).__name__,'safe_reason':safe})
    emit('error','simulation_failed',job_id=row['job_id'],post_id=row['post_id'],error_type=type(e).__name__)
   c.commit()
  return True
 finally:c.close()
def main():
 p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');p.add_argument('--poll-seconds',type=int,default=15);a=p.parse_args()
 if a.once:tick();return
 while True:
  try:did=tick()
  except Exception as e:
   emit('error','worker_iteration_failed',error_type=type(e).__name__);did=False
  if not did:time.sleep(max(2,a.poll_seconds))
if __name__=='__main__':main()
