#!/usr/bin/env python3
"""Verified, non-destructive SQLite backup and restore-as-new-file utility."""
import argparse,sqlite3,sys
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
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
 b=s.add_parser('backup');b.add_argument('source');b.add_argument('destination')
 r=s.add_parser('restore-as-new');r.add_argument('backup');r.add_argument('new_database')
 a=p.parse_args()
 try:print(backup(a.source,a.destination) if a.cmd=='backup' else restore_as_new(a.backup,a.new_database))
 except Exception as e:print('ERROR:',type(e).__name__,str(e),file=sys.stderr);return 1
 return 0
if __name__=='__main__':raise SystemExit(main())
