"""PostgreSQL schema migrations. DDL is transactional and additive."""
from datetime import datetime,timezone

MIGRATIONS=[(1,[
"""CREATE TABLE IF NOT EXISTS stories(story_id TEXT PRIMARY KEY,title TEXT NOT NULL,characters_json TEXT NOT NULL DEFAULT '[]',perspective TEXT,continuity TEXT,status TEXT NOT NULL DEFAULT 'draft',source_path TEXT NOT NULL,vault_root TEXT NOT NULL,source_file_sha256 TEXT NOT NULL,canonical_text_sha256 TEXT NOT NULL,extractor_version TEXT NOT NULL,extracted_text TEXT NOT NULL,medium_url TEXT,source_review_required INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS campaigns(campaign_id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS posts(post_id TEXT PRIMARY KEY,promotion_type TEXT NOT NULL,status TEXT NOT NULL,current_version_id TEXT,scheduled_at TEXT,approval_batch_id TEXT,published_media_id TEXT,published_at TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS content_versions(version_id TEXT PRIMARY KEY,post_id TEXT NOT NULL REFERENCES posts(post_id),version_number INTEGER NOT NULL,caption TEXT NOT NULL,media_json TEXT NOT NULL,format TEXT NOT NULL,alt_text TEXT,disclosures_json TEXT NOT NULL,content_hash TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(post_id,version_number))""",
"""CREATE TABLE IF NOT EXISTS post_stories(post_id TEXT NOT NULL REFERENCES posts(post_id),story_id TEXT NOT NULL REFERENCES stories(story_id),source_hash TEXT NOT NULL,PRIMARY KEY(post_id,story_id))""",
"""CREATE TABLE IF NOT EXISTS media_assets(asset_id TEXT PRIMARY KEY,path TEXT NOT NULL,sha256 TEXT NOT NULL,media_type TEXT NOT NULL,added_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS batches(batch_id TEXT PRIMARY KEY,status TEXT NOT NULL,entries_json TEXT NOT NULL,approved_by TEXT,approved_at TEXT,revoked_at TEXT,revoke_reason TEXT,created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS attempts(attempt_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,version_id TEXT NOT NULL,batch_id TEXT,attempt_number INTEGER NOT NULL,phase TEXT NOT NULL,outcome TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT NOT NULL,container_id TEXT,media_id TEXT,safe_error TEXT)""",
"""CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS post_details(post_id TEXT PRIMARY KEY REFERENCES posts(post_id),campaign_id TEXT REFERENCES campaigns(campaign_id),purpose TEXT NOT NULL DEFAULT '',audience TEXT NOT NULL DEFAULT '',ai_proposal INTEGER NOT NULL DEFAULT 0)""",
"""CREATE TABLE IF NOT EXISTS publish_jobs(job_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,version_id TEXT NOT NULL,batch_id TEXT NOT NULL,run_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,lease_owner TEXT,lease_until TEXT,last_error TEXT,created_at TEXT NOT NULL,UNIQUE(post_id,version_id))""",
"CREATE INDEX IF NOT EXISTS ix_publish_jobs_due ON publish_jobs(status,run_at)",
"""CREATE TABLE IF NOT EXISTS audit_events(event_id TEXT PRIMARY KEY,event_type TEXT NOT NULL,actor TEXT NOT NULL,object_id TEXT NOT NULL,details_json TEXT NOT NULL,created_at TEXT NOT NULL)""",
"""CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)""",
"INSERT INTO settings(key,value) VALUES('global_pause','0') ON CONFLICT(key) DO NOTHING",
]),(2,[
"CREATE TABLE IF NOT EXISTS auth_sessions(session_hash TEXT PRIMARY KEY,username TEXT NOT NULL,expires_at TEXT NOT NULL,created_at TEXT NOT NULL)",
"CREATE INDEX IF NOT EXISTS ix_auth_sessions_expiry ON auth_sessions(expires_at)",
"CREATE TABLE IF NOT EXISTS auth_login_limits(username_hash TEXT PRIMARY KEY,failed_count INTEGER NOT NULL,window_started TEXT NOT NULL,locked_until TEXT)"
])]

def apply(conn, fail_after=None):
    """Apply all migrations in caller-controlled transaction; safe to rerun."""
    conn.execute('SELECT pg_advisory_xact_lock(731904221)')
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)')
    rows=conn.execute('SELECT version FROM schema_migrations').fetchall()
    done={r[0] for r in rows}
    executed=0
    for version, statements in MIGRATIONS:
        if version in done: continue
        for sql in statements:
            conn.execute(sql); executed+=1
            if fail_after is not None and executed>=fail_after:raise RuntimeError('simulated migration interruption')
        conn.execute('INSERT INTO schema_migrations(version,applied_at) VALUES(%s,%s)',(version,datetime.now(timezone.utc).isoformat()))
    return executed
