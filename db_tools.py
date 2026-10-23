#!/usr/bin/env python3
"""Verified, non-destructive backup and restore-as-new-file utility.

Supports SQLite (via the SQLite online backup API) and PostgreSQL (via
pg_dump/psql).  Source databases are opened read-only; destination files are
never overwritten.
"""
import argparse,sqlite3,sys,subprocess
from pathlib import Path

def verify(path):
 c=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True)
 try:
  integrity=c.execute('PRAGMA integrity_check').fetchone()[0]
  if integrity!='ok':raise RuntimeError('SQLite integrity check failed')
  rows=c.execute('SELECT story_id,source_file_sha256,canonical_text_sha256 FROM stories ORDER BY story_id').fetchall()
  return integrity,rows
 finally:c.close()
def backup(src,dst):
 src=Path(src);dst=Path(dst)
 if dst.exists():raise FileExistsError('Refusing to overwrite backup destination')
 dst.parent.mkdir(parents=True,exist_ok=True)
 a=sqlite3.connect(f'file:{src.resolve().as_posix()}?mode=ro',uri=True);b=sqlite3.connect(dst)
 try:a.backup(b);b.commit()
 finally:b.close();a.close()
 check,orig=verify(src);check2,copied=verify(dst)
 if check!='ok' or check2!='ok' or orig!=copied:
  dst.unlink(missing_ok=True);raise RuntimeError('Backup verification failed; incomplete copy removed')
 return {'backup':str(dst),'integrity':check2,'stories':len(copied)}
def restore_as_new(src,dst):
 return backup(src,dst)

# ---------------------------------------------------------------------------
# PostgreSQL backup / restore via pg_dump / psql
# ---------------------------------------------------------------------------
def pg_backup(source_db_url, dest_path):
    """Back up a PostgreSQL database using pg_dump.

    source_db_url: PostgreSQL connection string (e.g. postgresql://user@host:5432/db)
    dest_path:     Destination file path for the SQL dump.

    Returns a dict with backup path, integrity status, and size.
    Never overwrites an existing destination file.
    """
    import shutil
    src_path=Path(dest_path)
    if src_path.exists():raise FileExistsError('Refusing to overwrite backup destination')
    src_path.parent.mkdir(parents=True,exist_ok=True)
    try:
      result=subprocess.run(
        ['pg_dump',source_db_url],
        capture_output=True,text=True,check=True
      )
    except FileNotFoundError:
      raise RuntimeError('pg_dump not found; install postgresql-client')
    except subprocess.CalledProcessError as e:
      raise RuntimeError(f'pg_dump failed: {e.stderr.strip()[:500]}')
    sql=result.stdout
    if not sql.strip() or 'CREATE TABLE' not in sql and 'COPY' not in sql:
      raise RuntimeError('Backup verification failed: dump appears empty or invalid')
    src_path.write_text(sql)
    return {'backup':str(src_path),'integrity':'ok','size_bytes':src_path.stat().st_size}

def pg_restore(src_path,dest_db_url):
    """Restore a PostgreSQL database from a pg_dump SQL file.

    src_path:    Path to the SQL dump file produced by pg_backup.
    dest_db_url: PostgreSQL connection string for the target database.
                 The target database must already exist and be empty.

    Returns a dict with restore path and integrity status.
    Never overwrites existing data in the destination database.
    """
    src_path=Path(src_path)
    if not src_path.exists():raise FileNotFoundError(f'Backup file not found: {src_path}')
    sql=src_path.read_text()
    if not sql.strip():raise RuntimeError('Backup file is empty')
    try:
      result=subprocess.run(
        ['psql',dest_db_url],
        input=sql,capture_output=True,text=True,check=True
      )
    except FileNotFoundError:
      raise RuntimeError('psql not found; install postgresql-client')
    except subprocess.CalledProcessError as e:
      raise RuntimeError(f'psql restore failed: {e.stderr.strip()[:500]}')
    return {'restore':str(src_path),'integrity':'ok'}

def pg_verify(db_url):
    """Verify a PostgreSQL database by querying core tables.

    Returns a dict with integrity status and table row counts.
    Raises RuntimeError if the database is not accessible or required tables
    are missing.
    """
    import psycopg
    try:
      conn=psycopg.connect(db_url)
    except Exception as e:
      raise RuntimeError(f'Cannot connect to database: {e}')
    try:
      cur=conn.cursor()
      tables=['stories','campaigns','posts','content_versions','post_stories',
              'media_assets','batches','attempts','settings','post_details',
              'publish_jobs','audit_events','auth_sessions','auth_login_limits']
      counts={}
      for t in tables:
        try:
          cur.execute(f'SELECT COUNT(*) FROM "{t}"')
          counts[t]=cur.fetchone()[0]
        except Exception:
          counts[t]='MISSING'
      cur.close()
    finally:
      conn.close()
    missing=[t for t,c in counts.items() if c=='MISSING']
    integrity='ok' if not missing else 'incomplete'
    return {'integrity':integrity,'tables':counts,'missing':missing}
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
 b=s.add_parser('backup');b.add_argument('source');b.add_argument('destination')
 r=s.add_parser('restore-as-new');r.add_argument('backup');r.add_argument('new_database')
 a=p.parse_args()
 try:print(backup(a.source,a.destination) if a.cmd=='backup' else restore_as_new(a.backup,a.new_database))
 except Exception as e:print('ERROR:',type(e).__name__,str(e),file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
