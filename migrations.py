"""Versioned local SQLite migrations. Production PostgreSQL migrations separate/not enabled."""
import sqlite3

MIGRATIONS=[
(1, '''
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaigns(campaign_id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS post_details(post_id TEXT PRIMARY KEY REFERENCES posts(post_id),campaign_id TEXT REFERENCES campaigns(campaign_id),purpose TEXT NOT NULL DEFAULT '',audience TEXT NOT NULL DEFAULT '',ai_proposal INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS publish_jobs(job_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,version_id TEXT NOT NULL,batch_id TEXT NOT NULL,run_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until TEXT,last_error TEXT,created_at TEXT NOT NULL,UNIQUE(post_id,version_id));
CREATE INDEX IF NOT EXISTS ix_publish_jobs_due ON publish_jobs(status,run_at);
CREATE TABLE IF NOT EXISTS audit_events(event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,actor TEXT NOT NULL,object_id TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL);
'''),
(2, '''
CREATE TABLE IF NOT EXISTS auth_sessions(session_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_auth_sessions_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_login_limits(username_hash TEXT PRIMARY KEY,failed_count INTEGER NOT NULL,window_started TEXT NOT NULL,locked_until TEXT);
''')]

def apply(c):
 c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
 done={r[0] for r in c.execute('SELECT version FROM schema_migrations')}
 from datetime import datetime,timezone
 for version,sql in MIGRATIONS:
  if version not in done:
   c.executescript(sql)
   c.execute('INSERT INTO schema_migrations VALUES(?,?)',(version,datetime.now(timezone.utc).isoformat()))
 c.commit()

def migrate_sqlite_to_postgres(*args,**kwargs):
 raise RuntimeError('PostgreSQL migration is not implemented: no PostgreSQL driver or server is configured. SQLite records remain authoritative locally.')
