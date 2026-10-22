#!/usr/bin/env python3
"""Day-to-day catalogue and planning commands; uses app.py's SQLite schema."""
import argparse, hashlib, json, sqlite3, sys, uuid, tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import app
DB=app.DB

def con():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); app.init_db(c); return c

def j(x): print(json.dumps(x,ensure_ascii=False,indent=2))
def verify_story(c,r):
 p=Path(r['vault_root'])/r['source_path']
 if not p.exists(): return 'missing'
 if app.sha(p.read_bytes())!=r['source_file_sha256']: return 'raw_changed'
 try:
  text=app.docx_text(p)
  if app.sha(text.encode())!=r['canonical_text_sha256']: return 'text_changed'
 except Exception as e:return 'needs_review:'+str(e)
 return 'ok'
def search(c,q):
 pat='%'+q.lower()+'%'
 rows=c.execute('SELECT * FROM stories WHERE lower(title) LIKE ? OR lower(extracted_text) LIKE ? OR lower(characters_json) LIKE ? OR lower(coalesce(perspective,\'\')) LIKE ? OR lower(coalesce(continuity,\'\')) LIKE ? ORDER BY story_id',(pat,pat,pat,pat,pat)).fetchall()
 j([{'story_id':r['story_id'],'title':r['title'],'source_path':r['source_path'],'integrity':verify_story(c,r),'status':r['status']} for r in rows])
def story(c,sid):
 r=c.execute('SELECT * FROM stories WHERE story_id=?',(sid,)).fetchone()
 if not r: raise ValueError('Unknown story ID')
 d=dict(r); d['source_integrity']=verify_story(c,r)
 d['related_posts']=[dict(x) for x in c.execute('SELECT p.post_id,p.status,p.promotion_type,p.current_version_id FROM post_stories ps JOIN posts p ON p.post_id=ps.post_id WHERE ps.story_id=?',(sid,))]
 j(d)
def check_sources(c):
 changed=[]
 for r in c.execute('SELECT * FROM stories'):
  state=verify_story(c,r)
  if state!='ok':
   c.execute('UPDATE stories SET source_review_required=1,updated_at=? WHERE story_id=?',(app.now(),r['story_id']))
   c.execute("UPDATE posts SET status='needs_review',updated_at=? WHERE post_id IN (SELECT post_id FROM post_stories WHERE story_id=?) AND status IN ('draft','ready_for_review','batch_approved','scheduled')",(app.now(),r['story_id']))
   changed.append({'story_id':r['story_id'],'integrity':state})
 c.commit();j(changed)
def metadata(c,a):
 if not c.execute('SELECT 1 FROM stories WHERE story_id=?',(a.story_id,)).fetchone():raise ValueError('Unknown story')
 current=c.execute('SELECT * FROM stories WHERE story_id=?',(a.story_id,)).fetchone()
 fields={'characters':json.dumps(a.characters if a.characters is not None else json.loads(current['characters_json']),ensure_ascii=False),'perspective':a.perspective if a.perspective is not None else current['perspective'],'continuity':a.continuity if a.continuity is not None else current['continuity'],'status':a.status if a.status is not None else current['status'],'medium_url':a.medium_url if a.medium_url is not None else current['medium_url']}
 c.execute('UPDATE stories SET characters_json=?,perspective=?,continuity=?,status=?,medium_url=?,updated_at=? WHERE story_id=?',(*fields.values(),app.now(),a.story_id));c.commit();print('updated metadata; canonical hashes unchanged')
def idea(c,a):
 pid=app.next_id(c,'posts','ig'); vid=str(uuid.uuid4()); stamp=app.now();payload={'caption':a.caption or '', 'media':[], 'format':a.format, 'alt_text':a.alt_text, 'disclosures':[]}; h=app.canonical_version(payload)
 c.execute('INSERT INTO posts(post_id,promotion_type,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',(pid,a.promotion,'idea',vid,stamp,stamp))
 c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,pid,1,payload['caption'],'[]',a.format,a.alt_text,'[]',h,stamp))
 for sid in a.story:
  r=c.execute('SELECT canonical_text_sha256 FROM stories WHERE story_id=?',(sid,)).fetchone()
  if not r:raise ValueError('Unknown story '+sid)
  c.execute('INSERT INTO post_stories VALUES(?,?,?)',(pid,sid,r['canonical_text_sha256']))
 c.commit();j({'post_id':pid,'version_id':vid,'status':'idea','content_hash':h})
def edit(c,a):
 p=c.execute('SELECT * FROM posts WHERE post_id=?',(a.post_id,)).fetchone()
 if not p:raise ValueError('Unknown post')
 old=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone()
 media=json.loads(old['media_json']) if a.media is None else []
 if a.media is not None:
  for mp in a.media:
   f=Path(mp).resolve()
   if not f.is_file():raise ValueError('Media file missing')
   media.append({'path':str(f),'sha256':app.sha(f.read_bytes()),'media_type':f.suffix.lower()})
 caption=old['caption'] if a.caption is None else a.caption
 fmt=old['format'] if a.format is None else a.format
 alt=old['alt_text'] if a.alt_text is None else a.alt_text
 payload={'caption':caption,'media':media,'format':fmt,'alt_text':alt,'disclosures':json.loads(old['disclosures_json'])}; h=app.canonical_version(payload);vid=str(uuid.uuid4());num=old['version_number']+1;stamp=app.now()
 c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,a.post_id,num,caption,json.dumps(media),fmt,alt,old['disclosures_json'],h,stamp))
 c.execute("UPDATE posts SET current_version_id=?,status='draft',approval_batch_id=NULL,scheduled_at=NULL,updated_at=? WHERE post_id=?",(vid,stamp,a.post_id));c.commit();j({'post_id':a.post_id,'version_id':vid,'content_hash':h,'approval':'invalidated','status':'draft'})
def review(c,bid,out):
 b=c.execute('SELECT * FROM batches WHERE batch_id=?',(bid,)).fetchone()
 if not b:raise ValueError('Unknown batch')
 entries=json.loads(b['entries_json']); result={'batch_id':bid,'status':b['status'],'approved_by':b['approved_by'],'approved_at':b['approved_at'],'posts':[]}
 for e in entries:
  p=c.execute('SELECT * FROM posts WHERE post_id=?',(e['post_id'],)).fetchone();v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(e['version_id'],)).fetchone()
  result['posts'].append({'post_id':p['post_id'],'status':p['status'],'version_id':v['version_id'],'content_hash':v['content_hash'],'caption':v['caption'],'media':json.loads(v['media_json']),'format':v['format'],'alt_text':v['alt_text'],'story_refs':[dict(x) for x in c.execute('SELECT s.story_id,s.title,ps.source_hash,s.canonical_text_sha256,s.source_review_required FROM post_stories ps JOIN stories s ON s.story_id=ps.story_id WHERE ps.post_id=?',(p['post_id'],))]})
 path=Path(out);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(path)
def calendar(c,start,end):
 rows=c.execute('SELECT post_id,status,promotion_type,scheduled_at,current_version_id FROM posts WHERE scheduled_at IS NOT NULL AND scheduled_at>=? AND scheduled_at<=? ORDER BY scheduled_at',(start,end)).fetchall();j([dict(x) for x in rows])

def verify_export(path):
 import zipfile
 zpath=Path(path)
 with zipfile.ZipFile(zpath) as z:
  names=set(z.namelist())
  if 'manifest.json' not in names:raise ValueError('manifest missing')
  m=json.loads(z.read('manifest.json'))
  if m.get('schema_version')!='tml-export-v1':raise ValueError('unsupported manifest schema')
  checked=[]
  for item in m.get('stories',[]):
   rel=PurePosixPath(item['export_path'])
   if rel.is_absolute() or '..' in rel.parts or item['export_path'] not in names:raise ValueError('unsafe or missing export path')
   data=z.read(item['export_path'])
   if len(data)!=item['size_bytes'] or app.sha(data)!=item['source_file_sha256']:raise ValueError('export bytes/hash mismatch: '+item['story_id'])
   with tempfile.NamedTemporaryFile(suffix='.docx') as tmp:
    tmp.write(data);tmp.flush(); text=app.docx_text(Path(tmp.name))
   if app.sha(text.encode())!=item['canonical_text_sha256']:raise ValueError('canonical text hash mismatch: '+item['story_id'])
   checked.append(item['story_id'])
  unexpected=names-{'manifest.json'}-{x['export_path'] for x in m.get('stories',[])}
  if unexpected:raise ValueError('unlisted archive entries: '+','.join(sorted(unexpected)))
  return {'valid':True,'schema_version':m['schema_version'],'story_ids':checked}
def main():
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='cmd',required=True)
 q=s.add_parser('search');q.add_argument('query')
 q=s.add_parser('show');q.add_argument('story_id')
 s.add_parser('check-sources')
 q=s.add_parser('metadata');q.add_argument('story_id');q.add_argument('--characters',nargs='*');q.add_argument('--perspective');q.add_argument('--continuity');q.add_argument('--status');q.add_argument('--medium-url')
 q=s.add_parser('idea');q.add_argument('--promotion',choices=['story','library'],default='story');q.add_argument('--story',action='append',default=[]);q.add_argument('--caption',default='');q.add_argument('--format',choices=['image','carousel','reel'],default='image');q.add_argument('--alt-text')
 q=s.add_parser('edit');q.add_argument('post_id');q.add_argument('--caption');q.add_argument('--format',choices=['image','carousel','reel']);q.add_argument('--media',action='append');q.add_argument('--alt-text')
 q=s.add_parser('batch-review');q.add_argument('batch_id');q.add_argument('--out',required=True)
 q=s.add_parser('calendar');q.add_argument('--start',required=True);q.add_argument('--end',required=True)
 q=s.add_parser('verify-export');q.add_argument('archive')
 a=p.parse_args();c=con()
 try:
  if a.cmd=='search':search(c,a.query)
  elif a.cmd=='show':story(c,a.story_id)
  elif a.cmd=='check-sources':check_sources(c)
  elif a.cmd=='metadata':metadata(c,a)
  elif a.cmd=='idea':idea(c,a)
  elif a.cmd=='edit':edit(c,a)
  elif a.cmd=='batch-review':review(c,a.batch_id,a.out)
  elif a.cmd=='calendar':calendar(c,a.start,a.end)
  elif a.cmd=='verify-export':j(verify_export(a.archive))
 except Exception as e:print('ERROR:',e,file=sys.stderr);return 1
 finally:c.close()
 return 0
if __name__=='__main__':raise SystemExit(main())
