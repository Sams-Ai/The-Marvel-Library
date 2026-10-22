#!/usr/bin/env python3
"""Local Marvel Library catalogue and simulated Instagram workflow.

No Meta credentials or network calls. DOCX inputs are read-only; SQLite lives
in this project directory. Python standard library only.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sqlite3, sys, unicodedata, uuid, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'marvel_library.sqlite3'
EXTRACTOR = 'tml-docx-text-v1'


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def init_db(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS stories (
      story_id TEXT PRIMARY KEY, title TEXT NOT NULL, characters_json TEXT NOT NULL DEFAULT '[]',
      perspective TEXT, continuity TEXT, status TEXT NOT NULL DEFAULT 'draft', source_path TEXT NOT NULL,
      vault_root TEXT NOT NULL,
      source_file_sha256 TEXT NOT NULL, canonical_text_sha256 TEXT NOT NULL,
      extractor_version TEXT NOT NULL, extracted_text TEXT NOT NULL, medium_url TEXT,
      source_review_required INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS posts (
      post_id TEXT PRIMARY KEY, promotion_type TEXT NOT NULL, status TEXT NOT NULL,
      current_version_id TEXT, scheduled_at TEXT, approval_batch_id TEXT,
      published_media_id TEXT, published_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS post_stories (
      post_id TEXT NOT NULL REFERENCES posts(post_id), story_id TEXT NOT NULL REFERENCES stories(story_id),
      source_hash TEXT NOT NULL, PRIMARY KEY(post_id,story_id));
    CREATE TABLE IF NOT EXISTS content_versions (
      version_id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(post_id), version_number INTEGER NOT NULL,
      caption TEXT NOT NULL, media_json TEXT NOT NULL, format TEXT NOT NULL, alt_text TEXT,
      disclosures_json TEXT NOT NULL, content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(post_id,version_number));
    CREATE TABLE IF NOT EXISTS media_assets (asset_id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, media_type TEXT NOT NULL, added_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS batches (
      batch_id TEXT PRIMARY KEY, status TEXT NOT NULL, entries_json TEXT NOT NULL, approved_by TEXT,
      approved_at TEXT, revoked_at TEXT, revoke_reason TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS attempts (
      attempt_id TEXT PRIMARY KEY, post_id TEXT NOT NULL, version_id TEXT NOT NULL, batch_id TEXT,
      attempt_number INTEGER NOT NULL, phase TEXT NOT NULL, outcome TEXT NOT NULL,
      started_at TEXT NOT NULL, finished_at TEXT NOT NULL, container_id TEXT,
      media_id TEXT, safe_error TEXT);
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT OR IGNORE INTO settings(key,value) VALUES('global_pause','0');
    ''')
    cols={r[1] for r in c.execute('PRAGMA table_info(stories)')}
    if 'vault_root' not in cols:
      c.execute("ALTER TABLE stories ADD COLUMN vault_root TEXT NOT NULL DEFAULT ''")
    c.commit()

def docx_text(path: Path) -> str:
    if not zipfile.is_zipfile(path): raise ValueError('Not a valid DOCX/ZIP package')
    try:
      with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        if 'word/document.xml' not in names: raise ValueError('Missing word/document.xml')
        if any(n.endswith('comments.xml') for n in names): raise ValueError('Comments present; manual review required')
        if any(n.startswith('word/media/') for n in names): raise ValueError('Embedded media present; manual review required')
        from xml.etree import ElementTree as ET
        root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        for tag in ('ins','del','moveFrom','moveTo'):
          if root.findall('.//w:'+tag, ns): raise ValueError('Tracked changes present; manual review required')
        body=root.find('w:body',ns)
        out=[]
        for child in list(body):
          kind=child.tag.rsplit('}',1)[-1]
          if kind=='tbl': raise ValueError('Tables present; manual review required')
          if kind!='p': continue
          # Exclude deleted text and preserve order of visible text runs.
          chunks=[]
          for node in child.iter():
            tag=node.tag.rsplit('}',1)[-1]
            if tag=='t' and node.text: chunks.append(node.text)
            elif tag=='tab': chunks.append('\t')
            elif tag in ('br','cr'): chunks.append('\n')
          p=unicodedata.normalize('NFC',''.join(chunks).replace('\r\n','\n').replace('\r','\n'))
          out.append(p.strip())
        return '\n'.join(out)
    except (zipfile.BadZipFile, KeyError, OSError) as e: raise ValueError('Malformed/unreadable DOCX') from e

def import_stories(c, vault: Path):
    note=vault/'The Marvel Library.md'
    if not note.is_file(): raise ValueError(f'Library index not found: {note}')
    embeds=re.findall(r'!\[\[([^\]]+\.docx)(?:\|[^\]]*)?\]\]',note.read_text(encoding='utf-8'))
    # Preserve existing embedded source files; import only those explicitly embedded.
    for idx, name in enumerate(embeds,1):
      p=(vault/name).resolve()
      if vault.resolve() not in p.parents or not p.is_file(): raise ValueError(f'Embed missing or escapes vault: {name}')
      text=docx_text(p); raw=p.read_bytes(); paragraphs=text.split('\n')
      title=paragraphs[0].strip() if paragraphs else p.stem
      # Existing IDs follow source path, never embed order or title.
      existing=c.execute('SELECT * FROM stories WHERE source_path=? AND vault_root=?',(str(p.relative_to(vault)),str(vault.resolve()))).fetchone()
      if existing:
        if existing['source_file_sha256'] != sha(raw) or existing['canonical_text_sha256'] != sha(text.encode()):
          c.execute('UPDATE stories SET source_review_required=1,updated_at=? WHERE story_id=?',(now(),existing['story_id'])); c.commit()
        continue
      sid=next_id(c,'stories','tml')
      c.execute('''INSERT OR IGNORE INTO stories(story_id,title,source_path,vault_root,source_file_sha256,canonical_text_sha256,extractor_version,extracted_text,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?)''',(sid,title,str(p.relative_to(vault)),str(vault.resolve()),sha(raw),sha(text.encode()),EXTRACTOR,text,now(),now()))
    c.commit()

def canonical_version(payload):
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return sha(raw)

def next_id(c, table, prefix):
    col={'stories':'story_id','posts':'post_id','batches':'batch_id'}[table]
    rows=c.execute(f'SELECT {col} FROM {table} WHERE {col} LIKE ?',(prefix+'-%',)).fetchall()
    n=max([int(r[0].split('-')[-1]) for r in rows] or [0])+1
    return f'{prefix}-{n:04d}'

def add_post(c,args):
    if args.promotion not in ('story','library'): raise ValueError('promotion must be story or library')
    refs=[]
    for sid in args.story:
      row=c.execute('SELECT story_id,canonical_text_sha256 FROM stories WHERE story_id=?',(sid,)).fetchone()
      if not row: raise ValueError(f'Unknown story ID: {sid}')
      refs.append((sid,row['canonical_text_sha256']))
    if args.promotion=='story' and not refs: raise ValueError('Story promotion requires at least one story reference')
    media=[]
    for value in args.media:
      f=Path(value).resolve()
      if not f.is_file(): raise ValueError(f'Media not found: {value}')
      media.append({'path':str(f),'sha256':sha(f.read_bytes()),'media_type':f.suffix.lower()})
    pid=next_id(c,'posts','ig'); vid=str(uuid.uuid4()); timestamp=now()
    payload={'caption':args.caption,'media':media,'format':args.format,'alt_text':args.alt_text,'disclosures':[]}
    ch=canonical_version(payload)
    c.execute('INSERT INTO posts VALUES(?,?,?, ?,NULL,NULL,NULL,NULL,?,?)',(pid,args.promotion,'draft',vid,timestamp,timestamp))
    c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,pid,1,args.caption,json.dumps(media),args.format,args.alt_text,'[]',ch,timestamp))
    for sid,h in refs: c.execute('INSERT INTO post_stories VALUES(?,?,?)',(pid,sid,h))
    c.commit(); print(json.dumps({'post_id':pid,'version_id':vid,'content_hash':ch,'status':'draft'}))

def set_review(c,pid):
    p=c.execute('SELECT status FROM posts WHERE post_id=?',(pid,)).fetchone()
    if not p or p['status'] not in ('draft','idea','needs_review'): raise ValueError('Post cannot enter review from its current state')
    v=c.execute('SELECT * FROM content_versions WHERE version_id=(SELECT current_version_id FROM posts WHERE post_id=?)',(pid,)).fetchone()
    if not v: raise ValueError('Missing version')
    if v['format'] in ('image','carousel','reel') and not json.loads(v['media_json']): raise ValueError('Media required for this format')
    import media_store
    store=media_store.from_env()
    for m in json.loads(v['media_json']):
      if not store.verify(m): raise ValueError('Media absent or hash mismatch')
    if c.execute('SELECT 1 FROM posts p JOIN post_stories ps ON p.post_id=ps.post_id JOIN stories s ON s.story_id=ps.story_id WHERE p.post_id=? AND s.source_review_required=1',(pid,)).fetchone():
      raise ValueError('Story source requires review')
    c.execute("UPDATE posts SET status='ready_for_review',updated_at=? WHERE post_id=?",(now(),pid))
    c.commit()

def create_batch(c,ids,approver):
    if not ids: raise ValueError('Provide one or more post IDs')
    entries=[]
    for pid in ids:
      p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
      if not p or p['status']!='ready_for_review': raise ValueError(f'{pid} is not ready_for_review')
      v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone()
      entries.append({'post_id':pid,'version_id':v['version_id'],'content_hash':v['content_hash']})
    bid=next_id(c,'batches','batch'); stamp=now()
    c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',(bid,'approved',json.dumps(entries),approver,stamp,None,None,stamp))
    for e in entries: c.execute("UPDATE posts SET status='batch_approved',approval_batch_id=?,updated_at=? WHERE post_id=?",(bid,stamp,e['post_id']))
    c.commit(); print(json.dumps({'batch_id':bid,'approved_by':approver,'approved_at':stamp,'entries':entries}))

def schedule(c,pid,when):
    p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
    if not p or p['status']!='batch_approved': raise ValueError('Post is not batch_approved')
    b=c.execute('SELECT * FROM batches WHERE batch_id=?',(p['approval_batch_id'],)).fetchone()
    e=next((x for x in json.loads(b['entries_json']) if x['post_id']==pid),None)
    v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone()
    if b['status']!='approved' or not e or e['version_id']!=v['version_id'] or e['content_hash']!=v['content_hash']: raise ValueError('Approval is stale or mismatched')
    if c.execute('SELECT 1 FROM post_stories ps JOIN stories s ON s.story_id=ps.story_id WHERE ps.post_id=? AND s.source_review_required=1',(pid,)).fetchone(): raise ValueError('Story source requires review')
    dt=datetime.fromisoformat(when.replace('Z','+00:00'))
    if not dt.tzinfo: raise ValueError('Scheduled time must include timezone')
    c.execute("UPDATE posts SET status='scheduled',scheduled_at=?,updated_at=? WHERE post_id=?",(dt.astimezone(timezone.utc).isoformat(),now(),pid))
    # Enqueue only when the versioned queue schema has been applied.
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publish_jobs'").fetchone():
      c.execute('INSERT OR IGNORE INTO publish_jobs(job_id,post_id,version_id,batch_id,run_at,status,created_at) VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid4()),pid,v['version_id'],p['approval_batch_id'],dt.astimezone(timezone.utc).isoformat(),'queued',now()))
    c.commit()

def fake_publish(c,pid):
    paused=c.execute("SELECT value FROM settings WHERE key='global_pause'").fetchone()[0]=='1'
    if paused: raise ValueError('Global pause is enabled')
    p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
    if not p or p['status']!='scheduled': raise ValueError('Post is not scheduled')
    b=c.execute('SELECT * FROM batches WHERE batch_id=?',(p['approval_batch_id'],)).fetchone()
    ent=next((x for x in json.loads(b['entries_json']) if x['post_id']==pid),None)
    v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone()
    if b['status']!='approved' or not ent or ent['version_id']!=v['version_id'] or ent['content_hash']!=v['content_hash']: raise ValueError('Approval invalid')
    if datetime.fromisoformat(p['scheduled_at'])>datetime.now(timezone.utc): raise ValueError('Not due yet')
    import media_store
    store=media_store.from_env()
    for m in json.loads(v['media_json']):
      if not store.verify(m): raise ValueError('Media absent or hash mismatch')
    for r in c.execute('SELECT ps.story_id,ps.source_hash,s.canonical_text_sha256,s.source_review_required FROM post_stories ps JOIN stories s ON s.story_id=ps.story_id WHERE ps.post_id=?',(pid,)):
      if r['source_review_required'] or r['source_hash']!=r['canonical_text_sha256']: raise ValueError('Story source stale')
    stamp=now(); aid=str(uuid.uuid4()); num=c.execute('SELECT COUNT(*) FROM attempts WHERE post_id=? AND version_id=?',(pid,v['version_id'])).fetchone()[0]+1
    # Simulated result only; no Meta request.
    media_id='SIM-'+uuid.uuid4().hex[:12]
    c.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(aid,pid,v['version_id'],p['approval_batch_id'],num,'fake_publish','succeeded',stamp,stamp,'SIM-CONTAINER-'+uuid.uuid4().hex[:8],media_id,None))
    c.execute("UPDATE posts SET status='published',published_media_id=?,published_at=?,updated_at=? WHERE post_id=?",(media_id,stamp,stamp,pid)); c.commit()
    print(json.dumps({'mode':'SIMULATION','post_id':pid,'simulated_media_id':media_id,'attempt_id':aid}))

def main():
 ap=argparse.ArgumentParser(description=__doc__); sub=ap.add_subparsers(dest='cmd',required=True)
 sub.add_parser('init'); i=sub.add_parser('import-vault'); i.add_argument('--vault',default='/var/minis/mounts/Spiderweb')
 sub.add_parser('stories'); p=sub.add_parser('add-post'); p.add_argument('--promotion',required=True); p.add_argument('--story',action='append',default=[]); p.add_argument('--caption',required=True); p.add_argument('--format',choices=['image','carousel','reel'],default='image'); p.add_argument('--media',action='append',default=[]); p.add_argument('--alt-text'); r=sub.add_parser('ready'); r.add_argument('post_id')
 b=sub.add_parser('approve-batch'); b.add_argument('post_ids',nargs='+'); b.add_argument('--approver',required=True)
 s=sub.add_parser('schedule'); s.add_argument('post_id'); s.add_argument('--at',required=True)
 f=sub.add_parser('fake-publish'); f.add_argument('post_id')
 pu=sub.add_parser('pause'); pu.add_argument('value',choices=['on','off'])
 ex=sub.add_parser('export'); ex.add_argument('story_ids',nargs='+'); ex.add_argument('--out',required=True)
 args=ap.parse_args(); c=connect(); init_db(c)
 try:
  if args.cmd=='init': print(DB)
  elif args.cmd=='import-vault': import_stories(c,Path(args.vault).resolve()); print(json.dumps([dict(x) for x in c.execute('SELECT story_id,title,source_path,source_file_sha256,canonical_text_sha256,extractor_version,status FROM stories ORDER BY story_id')],ensure_ascii=False,indent=2))
  elif args.cmd=='stories': print(json.dumps([dict(x) for x in c.execute('SELECT story_id,title,characters_json,perspective,continuity,status,source_path,source_file_sha256,canonical_text_sha256,extractor_version,medium_url,source_review_required FROM stories ORDER BY story_id')],ensure_ascii=False,indent=2))
  elif args.cmd=='add-post': add_post(c,args)
  elif args.cmd=='ready': set_review(c,args.post_id); print('ready_for_review')
  elif args.cmd=='approve-batch': create_batch(c,args.post_ids,args.approver)
  elif args.cmd=='schedule': schedule(c,args.post_id,args.at); print('scheduled')
  elif args.cmd=='fake-publish': fake_publish(c,args.post_id)
  elif args.cmd=='pause': c.execute("UPDATE settings SET value=? WHERE key='global_pause'",('1' if args.value=='on' else '0',)); c.commit(); print('global_pause='+args.value)
  elif args.cmd=='export': export(c,args.story_ids,Path(args.out))
 except Exception as e: print('ERROR:',str(e),file=sys.stderr); return 1
 finally: c.close()
 return 0

def export(c,ids,out):
 import tempfile, zipfile as zf
 rows=[]
 for sid in ids:
  row=c.execute('SELECT * FROM stories WHERE story_id=?',(sid,)).fetchone()
  if not row: raise ValueError(f'Unknown story ID: {sid}')
  src=Path(row['vault_root'])/row['source_path']
  if not src.is_file() or sha(src.read_bytes())!=row['source_file_sha256']: raise ValueError(f'Source changed or missing: {sid}')
  rows.append((row,src))
 manifest={'schema_version':'tml-export-v1','export_id':str(uuid.uuid4()),'created_at':now(),'extractor_version':EXTRACTOR,'stories':[]}
 for r,src in rows: manifest['stories'].append({'story_id':r['story_id'],'title':r['title'],'source_path':r['source_path'],'source_file_sha256':r['source_file_sha256'],'canonical_text_sha256':r['canonical_text_sha256'],'extractor_version':r['extractor_version'],'export_path':f"stories/{r['story_id']}/story.docx",'size_bytes':src.stat().st_size})
 out.parent.mkdir(parents=True,exist_ok=True)
 with zf.ZipFile(out,'w',compression=zf.ZIP_DEFLATED) as z:
  z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2))
  for (r,src),entry in zip(rows,manifest['stories']): z.write(src,entry['export_path'])
 print(json.dumps({'archive':str(out),'manifest':manifest},ensure_ascii=False,indent=2))

if __name__=='__main__': raise SystemExit(main())
