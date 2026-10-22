#!/usr/bin/env python3
"""Copy a verified SQLite source to an EMPTY PostgreSQL target transactionally."""
import argparse,os,sys
from database import migrate_sqlite_to_postgres
p=argparse.ArgumentParser();p.add_argument('--sqlite',required=True);p.add_argument('--database-url-env',default='DATABASE_URL');a=p.parse_args()
url=os.getenv(a.database_url_env)
if not url or not url.startswith(('postgres://','postgresql://')):raise SystemExit(f'Set {a.database_url_env} in the process environment; do not pass credentials as a CLI argument.')
try:
 print(migrate_sqlite_to_postgres(a.sqlite,url))
except Exception as e:
 print('Migration failed; source SQLite was opened read-only and remains unchanged. Error type:',type(e).__name__,file=sys.stderr)
 raise SystemExit(1)
