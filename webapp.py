#!/usr/bin/env python3
"""Authenticated WSGI production app interface; local server is dev-only."""
import base64,hashlib,hmac,html,json,os,secrets,sqlite3,sys,uuid
from datetime import datetime,timezone,timedelta
from email.parser import BytesParser
from email.policy import default
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server,WSGIRequestHandler
import app,migrations,database
from logging_utils import emit
ROOT=Path(__file__).resolve().parent
LOCAL_ENV=ROOT/'.env.local'
if LOCAL_ENV.exists():
 for line in LOCAL_ENV.read_text().splitlines():
  if '=' in line and not line.lstrip().startswith('#'):
   k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip())
DB=Path(os.getenv('DATABASE_PATH',str(app.DB)));SESSION_KEY=os.getenv('WEB_SESSION_KEY');USER=os.getenv('WEB_USER','operator');PASSWORD_HASH=os.getenv('WEB_PASSWORD_HASH','');COOKIE='tml_session';MAX_BODY=12*1024*1024
ACTIVE_NONCE=''

def db():
 url=os.getenv('DATABASE_URL','')
 if url.startswith(('postgres://','postgresql://')):
  c=database.connect(url);migrations_pg_apply(c);c.commit();return c
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');app.init_db(c);migrations.apply(c);return c

def migrations_pg_apply(c):
 import migrations_pg
 migrations_pg.apply(c)

def password_hash(password,salt=None):
 salt=salt or secrets.token_hex(16)
 return salt+'$'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()
def check_password(password,stored):
 try:salt,want=stored.split('$',1);got=password_hash(password,salt).split('$',1)[1];return hmac.compare_digest(got,want)
 except Exception:return False
def token(user,expiry,nonce):
 payload=f'{user}|{expiry}|{nonce}'.encode();sig=hmac.new(SESSION_KEY.encode(),payload,hashlib.sha256).digest()
 return base64.urlsafe_b64encode(payload).decode().rstrip('=')+'.'+base64.urlsafe_b64encode(sig).decode().rstrip('=')
def decode_cookie(value):
 try:
  left,right=value.split('.',1);payload=base64.urlsafe_b64decode(left+'='*((4-len(left)%4)%4));sig=base64.urlsafe_b64decode(right+'='*((4-len(right)%4)%4))
  user,expiry,nonce=payload.decode().split('|');want=hmac.new(SESSION_KEY.encode(),payload,hashlib.sha256).digest()
  if not hmac.compare_digest(sig,want) or int(expiry)<int(datetime.now(timezone.utc).timestamp()):return None
  return user,nonce
 except Exception:return None
def csrf(user,nonce):return hmac.new(SESSION_KEY.encode(),f'csrf|{user}|{nonce}'.encode(),hashlib.sha256).hexdigest()
def identity_hash(value):return hmac.new(SESSION_KEY.encode(),('identity|'+value.lower()).encode(),hashlib.sha256).hexdigest()
def nonce_hash(value):return hmac.new(SESSION_KEY.encode(),('session|'+value).encode(),hashlib.sha256).hexdigest()
def login_csrf(bucket=None):
 bucket=int(datetime.now(timezone.utc).timestamp()//300) if bucket is None else bucket
 return f'{bucket}.'+hmac.new(SESSION_KEY.encode(),f'login-csrf|{bucket}'.encode(),hashlib.sha256).hexdigest()
def valid_login_csrf(value):
 try:
  bucket,sig=value.split('.',1);bucket=int(bucket);cur=int(datetime.now(timezone.utc).timestamp()//300)
  return bucket in (cur,cur-1) and hmac.compare_digest(sig,login_csrf(bucket).split('.',1)[1])
 except Exception:return False
def blocked(c,username):
 r=c.execute('SELECT locked_until FROM auth_login_limits WHERE username_hash=?',(identity_hash(username),)).fetchone()
 return bool(r and r['locked_until'] and r['locked_until']>app.now())
def failed_login(c,username):
 key=identity_hash(username);stamp=datetime.now(timezone.utc);window=(stamp-timedelta(minutes=15)).isoformat(timespec='seconds');locked=(stamp+timedelta(minutes=15)).isoformat(timespec='seconds')
 r=c.execute('SELECT failed_count,window_started FROM auth_login_limits WHERE username_hash=?',(key,)).fetchone();count=(r['failed_count']+1 if r and r['window_started']>window else 1);lock=locked if count>=8 else None
 c.execute('INSERT INTO auth_login_limits(username_hash,failed_count,window_started,locked_until) VALUES(?,?,?,?) ON CONFLICT(username_hash) DO UPDATE SET failed_count=excluded.failed_count,window_started=excluded.window_started,locked_until=excluded.locked_until',(key,count,stamp.isoformat(timespec='seconds'),lock))
def successful_login(c,user,nonce,expiry):
 stamp=app.now();expires=datetime.fromtimestamp(expiry,timezone.utc).isoformat(timespec='seconds');key=identity_hash(user)
 c.execute('DELETE FROM auth_sessions WHERE expires_at<=?',(stamp,));c.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',(nonce_hash(nonce),user,expires,stamp))
 c.execute('INSERT INTO auth_login_limits(username_hash,failed_count,window_started,locked_until) VALUES(?,?,?,NULL) ON CONFLICT(username_hash) DO UPDATE SET failed_count=0,window_started=excluded.window_started,locked_until=NULL',(key,0,stamp))
def valid_session(c,user,nonce):return bool(c.execute('SELECT 1 FROM auth_sessions WHERE session_hash=? AND username=? AND expires_at>?',(nonce_hash(nonce),user,app.now())).fetchone())
def parse_form(env,body):
 if env.get('CONTENT_TYPE','').startswith('multipart/form-data'):
  msg=BytesParser(policy=default).parsebytes(b'Content-Type: '+env['CONTENT_TYPE'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+body);out={}
  for part in msg.iter_parts():
   name=part.get_param('name',header='content-disposition')
   if not name:continue
   raw=part.get_payload(decode=True) or b'';fname=part.get_filename()
   val={'filename':fname,'bytes':raw} if fname is not None else raw.decode(part.get_content_charset() or 'utf-8')
   if name in out:
    if not isinstance(out[name],list):out[name]=[out[name]]
    out[name].append(val)
   else:out[name]=val
  return out
 vals=parse_qs(body.decode('utf-8'),keep_blank_values=True)
 return {k:(v if len(v)>1 else v[0]) for k,v in vals.items()}
def esc(v):return html.escape(str(v or ''),quote=True)
def page(title,body,user=None):
 nav=(f'<nav>{esc(user)} · <a href="/">Stories</a> · <a href="/ideas">New idea</a> · <a href="/review">Review</a> · <a href="/calendar">Calendar</a> · <a href="/history">History</a> · <a href="/health">Health</a><form method="post" action="/logout" style="display:inline"><input type="hidden" name="csrf" value="{csrf(user,ACTIVE_NONCE)}"><button>Log out</button></form></nav>') if user else ''
 return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>body{{font:16px system-ui;max-width:1100px;margin:auto;padding:1rem;background:#f5f6f8;color:#202124}}nav,.card{{background:#fff;padding:1rem;margin:1rem 0;border-radius:10px}}input,textarea,select,button{{font:inherit;padding:.6rem;max-width:100%}}textarea{{width:95%;min-height:6rem}}table{{width:100%;border-collapse:collapse;overflow-wrap:anywhere}}td,th{{text-align:left;border-bottom:1px solid #ddd;padding:.55rem}}a{{color:#174a82}}button{{cursor:pointer}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style></head><body>{nav}<h1>{esc(title)}</h1>{body}</body></html>'''
def response(start,status,body,headers=None):
 b=body.encode();h=[('Content-Type','text/html; charset=utf-8'),('Content-Length',str(len(b))),('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")];h.extend(headers or []);start(status,h);return [b]
def audit(c,kind,actor,obj,details):c.execute('INSERT INTO audit_events VALUES(?,?,?,?,?,?)',(str(uuid.uuid4()),kind,actor,obj,json.dumps(details,ensure_ascii=False),app.now()))

def app_wsgi(env,start):
 global ACTIVE_NONCE
 method=env['REQUEST_METHOD'];path=env.get('PATH_INFO','/')
 if method=='GET' and path=='/healthz':
  try:
   c=database.connect();c.execute('SELECT 1').fetchone();c.close();return response(start,'200 OK','healthy')
  except Exception:emit('error','health_check_failed');return response(start,'503 Service Unavailable','unhealthy')
 if not SESSION_KEY:return response(start,'503 Service Unavailable','WEB_SESSION_KEY is not configured')
 length=int(env.get('CONTENT_LENGTH') or 0)
 if length>MAX_BODY:return response(start,'413 Payload Too Large','Request too large')
 body=env['wsgi.input'].read(length) if length else b'';data=parse_form(env,body)
 jar=cookies.SimpleCookie();jar.load(env.get('HTTP_COOKIE',''));sess=decode_cookie(jar[COOKIE].value) if COOKIE in jar else None
 user,nonce=sess if sess else (None,None);ACTIVE_NONCE=nonce or ''
 if method=='GET' and path=='/login':
  form=f'<form method="post"><input type="hidden" name="login_csrf" value="{login_csrf()}"><label>Username <input name="username" required></label><br><label>Password <input name="password" type="password" required></label><br><button>Sign in</button></form>'
  return response(start,'200 OK',page('Sign in',form))
 if method=='POST' and path=='/login':
  c=None
  try:
   if not valid_login_csrf(data.get('login_csrf','')):return response(start,'403 Forbidden',page('Sign in','Request token invalid'))
   c=db();username=str(data.get('username',''))[:120]
   if blocked(c,USER):return response(start,'429 Too Many Requests',page('Sign in','Too many attempts; try later'))
   if username==USER and PASSWORD_HASH and check_password(data.get('password',''),PASSWORD_HASH):
    expiry=int(datetime.now(timezone.utc).timestamp())+8*3600;nonce=secrets.token_urlsafe(24);successful_login(c,USER,nonce,expiry);audit(c,'login_success',USER,USER,{});c.commit();val=token(USER,expiry,nonce)
    secure='; Secure' if os.getenv('COOKIE_SECURE','0')=='1' else ''
    return response(start,'303 See Other','',[('Location','/'),('Set-Cookie',f'{COOKIE}={val}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800{secure}')])
   failed_login(c,USER);audit(c,'login_failure',USER,USER,{});c.commit();return response(start,'401 Unauthorized',page('Sign in','Invalid credentials'))
  except Exception as e:
   if c:c.rollback()
   emit('error','login_handler_failed',error_type=type(e).__name__);return response(start,'503 Service Unavailable',page('Sign in','Authentication service unavailable'))
  finally:
   if c:c.close()
 if not user:return response(start,'303 See Other','',[('Location','/login')])
 c=None
 try:
  c=db()
  if not valid_session(c,user,nonce):c.close();return response(start,'303 See Other','',[('Location','/login'),('Set-Cookie',f'{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')])
  if method=='POST' and not hmac.compare_digest(str(data.get('csrf','')),csrf(user,nonce)):return response(start,'403 Forbidden',page('Request rejected','Invalid CSRF token',user))
  if path=='/logout' and method=='POST':
   c.execute('DELETE FROM auth_sessions WHERE session_hash=?',(nonce_hash(nonce),));audit(c,'logout',user,user,{});c.commit()
   return response(start,'303 See Other','',[('Location','/login'),('Set-Cookie',f'{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')])
  if method=='GET' and path in ('/','/stories'):
   query=parse_qs(env.get('QUERY_STRING','')).get('q',[''])[0].lower();pat='%'+query+'%'
   rows=c.execute('SELECT story_id,title,status,source_path,source_review_required FROM stories WHERE lower(title) LIKE ? OR lower(extracted_text) LIKE ? ORDER BY story_id',(pat,pat)).fetchall()
   trs=''.join(f'<tr><td><a href="/story/{esc(r["story_id"])}">{esc(r["story_id"])} — {esc(r["title"])}</a></td><td>{esc(r["status"])}</td><td>{"Review required" if r["source_review_required"] else "Check source"}</td></tr>' for r in rows)
   return response(start,'200 OK',page('Story catalogue',f'<form><input name="q" value="{esc(query)}" placeholder="Search stories"><button>Search</button></form><table>{trs}</table>',user))
  if method=='GET' and path.startswith('/story/'):
   sid=path.rsplit('/',1)[-1];r=c.execute('SELECT * FROM stories WHERE story_id=?',(sid,)).fetchone()
   if not r:return response(start,'404 Not Found',page('Not found','Story not found',user))
   try:
    f=Path(r['vault_root'])/r['source_path'];integrity='ok' if f.is_file() and app.sha(f.read_bytes())==r['source_file_sha256'] and app.sha(app.docx_text(f).encode())==r['canonical_text_sha256'] else 'changed'
   except Exception:integrity='unavailable/review required'
   posts=c.execute('SELECT p.post_id,p.status FROM post_stories s JOIN posts p ON p.post_id=s.post_id WHERE s.story_id=?',(sid,)).fetchall()
   related=''.join(f'<li><a href="/post/{esc(p["post_id"])}">{esc(p["post_id"])} — {esc(p["status"])}</a></li>' for p in posts)
   body_html=f'<section class="card"><p>ID {esc(sid)} · source integrity <b>{esc(integrity)}</b></p><p>Source: {esc(r["source_path"])}</p><p>Characters: {esc(r["characters_json"])} · perspective: {esc(r["perspective"] or "unknown")} · continuity: {esc(r["continuity"] or "unknown")}</p><ul>{related}</ul><details><summary>Extracted source text</summary><pre>{esc(r["extracted_text"])}</pre></details></section>'
   return response(start,'200 OK',page(r['title'],body_html,user))
  if method=='GET' and path=='/ideas':
   stories=c.execute('SELECT story_id,title FROM stories ORDER BY story_id').fetchall();opts=''.join(f'<option value="{esc(s["story_id"])}">{esc(s["title"])}</option>' for s in stories)
   form=f'<form method="post"><input type="hidden" name="csrf" value="{csrf(user,nonce)}"><label>Caption proposal<textarea name="caption"></textarea></label><br><select name="story_id"><option value="">Library-wide promotion</option>{opts}</select><input name="purpose" placeholder="Purpose"><input name="campaign" placeholder="Campaign"><button>Create idea</button></form>'
   return response(start,'200 OK',page('New idea',form,user))
  if method=='POST' and path=='/ideas':
   pid=app.next_id(c,'posts','ig');vid=str(uuid.uuid4());stamp=app.now();caption=data.get('caption','');payload={'caption':caption,'media':[],'format':'image','alt_text':None,'disclosures':[]};ch=app.canonical_version(payload)
   c.execute('INSERT INTO posts(post_id,promotion_type,status,current_version_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',(pid,'story' if data.get('story_id') else 'library','idea',vid,stamp,stamp))
   c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,pid,1,caption,'[]','image',None,'[]',ch,stamp))
   if data.get('story_id'):
    sr=c.execute('SELECT canonical_text_sha256 FROM stories WHERE story_id=?',(data['story_id'],)).fetchone()
    if not sr:raise ValueError('Unknown story ID')
    c.execute('INSERT INTO post_stories VALUES(?,?,?)',(pid,data['story_id'],sr[0]))
   campaign=None
   if data.get('campaign'):
    old=c.execute('SELECT campaign_id FROM campaigns WHERE title=?',(data['campaign'],)).fetchone();campaign=old[0] if old else str(uuid.uuid4())
    if not old:c.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',(campaign,data['campaign'],'','active',stamp,stamp))
   c.execute('INSERT INTO post_details VALUES(?,?,?,?,?)',(pid,campaign,data.get('purpose',''),'','0'));audit(c,'idea_created',user,pid,{'version_id':vid,'content_hash':ch});c.commit()
   return response(start,'303 See Other','',[('Location','/post/'+pid)])
  if method=='GET' and path.startswith('/post/'):
   pid=path.rsplit('/',1)[-1];p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
   if not p:return response(start,'404 Not Found',page('Not found','Post not found',user))
   v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone();d=c.execute('SELECT * FROM post_details WHERE post_id=?',(pid,)).fetchone()
   edit=f'<form method="post" action="/post/{esc(pid)}/edit"><input type="hidden" name="csrf" value="{csrf(user,nonce)}"><textarea name="caption">{esc(v["caption"])}</textarea><button>Save new version (invalidates prior approval)</button></form>'
   upload=f'<form method="post" enctype="multipart/form-data" action="/post/{esc(pid)}/media"><input type="hidden" name="csrf" value="{csrf(user,nonce)}"><input type="file" name="media" accept="image/jpeg" required><button>Attach JPEG</button></form>'
   scheduled=''
   if p['status']=='batch_approved':scheduled=f'<form method="post" action="/schedule/{esc(pid)}"><input type="hidden" name="csrf" value="{csrf(user,nonce)}"><input name="scheduled_at" placeholder="2027-01-01T09:00:00+10:00" required><button>Schedule approved version</button></form>'
   return response(start,'200 OK',page('Post '+pid,f'<div class="card"><p>{esc(p["status"])} · version {esc(v["version_id"])}</p><p>Hash {esc(v["content_hash"])}</p><p>Campaign: {esc(d["campaign_id"] if d else "")}; purpose: {esc(d["purpose"] if d else "")}</p><p>Media refs: {esc(v["media_json"])}</p>{edit}{upload}{scheduled}</div>',user))
  if method=='POST' and path.startswith('/post/') and path.endswith('/edit'):
   pid=path.split('/')[2];p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
   if not p:raise ValueError('Unknown post')
   old=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone();caption=data.get('caption','');media=json.loads(old['media_json']);payload={'caption':caption,'media':media,'format':old['format'],'alt_text':old['alt_text'],'disclosures':json.loads(old['disclosures_json'])};ch=app.canonical_version(payload);vid=str(uuid.uuid4());stamp=app.now()
   c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,pid,old['version_number']+1,caption,json.dumps(media),old['format'],old['alt_text'],old['disclosures_json'],ch,stamp));c.execute("UPDATE posts SET current_version_id=?,status='draft',approval_batch_id=NULL,scheduled_at=NULL,updated_at=? WHERE post_id=?",(vid,stamp,pid));audit(c,'content_edited',user,pid,{'version_id':vid,'content_hash':ch,'approval_invalidated':True});c.commit()
   return response(start,'303 See Other','',[('Location','/post/'+pid)])
  if method=='POST' and path.startswith('/post/') and path.endswith('/media'):
   pid=path.split('/')[2];p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone();item=data.get('media')
   if not p or not isinstance(item,dict):raise ValueError('Unknown post or missing media upload')
   import media_store
   stored=media_store.from_env().put(item.get('bytes',b''),Path(item.get('filename') or 'image.jpg').name);ref={**stored,'media_type':'image/jpeg'}
   asset_path=stored.get('path') or 's3://'+stored['bucket']+'/'+stored['key'];digest=stored['sha256']
   old=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone();media=json.loads(old['media_json']);media.append(ref);payload={'caption':old['caption'],'media':media,'format':old['format'],'alt_text':old['alt_text'],'disclosures':json.loads(old['disclosures_json'])};ch=app.canonical_version(payload);vid=str(uuid.uuid4());stamp=app.now()
   c.execute('INSERT INTO content_versions VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,pid,old['version_number']+1,old['caption'],json.dumps(media),old['format'],old['alt_text'],old['disclosures_json'],ch,stamp))
   existing=c.execute('SELECT 1 FROM media_assets WHERE asset_id=?',(digest,)).fetchone()
   if existing:c.execute('UPDATE media_assets SET path=?,sha256=?,media_type=? WHERE asset_id=?',(asset_path,digest,'image/jpeg',digest))
   else:c.execute('INSERT INTO media_assets VALUES(?,?,?,?,?)',(digest,asset_path,digest,'image/jpeg',stamp))
   c.execute("UPDATE posts SET current_version_id=?,status='draft',approval_batch_id=NULL,scheduled_at=NULL,updated_at=? WHERE post_id=?",(vid,stamp,pid));audit(c,'media_attached',user,pid,{'sha256':digest,'version_id':vid});c.commit()
   return response(start,'303 See Other','',[('Location','/post/'+pid)])
  if method=='GET' and path=='/review':
   rows=c.execute("SELECT p.post_id,p.status,v.version_id,v.content_hash,v.caption,v.media_json FROM posts p JOIN content_versions v ON v.version_id=p.current_version_id WHERE p.status IN ('idea','draft','ready_for_review','needs_review','batch_approved','scheduled') ORDER BY p.created_at").fetchall()
   cards=''.join(f'<div class="card"><label><input type="checkbox" name="post_id" value="{esc(r["post_id"])}"><a href="/post/{esc(r["post_id"])}">{esc(r["post_id"])}</a> {esc(r["status"])} · {esc(r["version_id"])}</label><p>{esc(r["caption"])}</p><p>Hash: {esc(r["content_hash"])}</p><p>Media: {esc(r["media_json"])}</p></div>' for r in rows)
   body=f'<form method="post" action="/review-batch"><input type="hidden" name="csrf" value="{csrf(user,nonce)}">{cards}<button>Approve selected exact versions</button></form>' if rows else 'No pending posts.'
   return response(start,'200 OK',page('Review queue',body,user))
  if method=='POST' and path=='/review-batch':
   ids=data.get('post_id',[]);ids=[ids] if isinstance(ids,str) else ids
   if not ids:raise ValueError('Select one or more posts')
   entries=[];import media_store;store=media_store.from_env()
   for pid in ids:
    p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
    if not p or p['status'] not in ('draft','idea','ready_for_review','needs_review'):raise ValueError('Selected post not eligible')
    v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone();media=json.loads(v['media_json'])
    if not media or any(not store.verify(x) for x in media):raise ValueError('Media missing or integrity check failed')
    for src in c.execute('SELECT ps.source_hash,s.canonical_text_sha256,s.source_review_required FROM post_stories ps JOIN stories s ON s.story_id=ps.story_id WHERE ps.post_id=?',(pid,)):
     if src['source_review_required'] or src['source_hash']!=src['canonical_text_sha256']:raise ValueError('Story source needs review')
    entries.append({'post_id':pid,'version_id':v['version_id'],'content_hash':v['content_hash']})
   batch=str(uuid.uuid4());stamp=app.now();c.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?,?)',(batch,'approved',json.dumps(entries),user,stamp,None,None,stamp))
   for e in entries:c.execute("UPDATE posts SET status='batch_approved',approval_batch_id=?,updated_at=? WHERE post_id=?",(batch,stamp,e['post_id']))
   audit(c,'batch_approved',user,batch,{'entries':entries});c.commit();return response(start,'303 See Other','',[('Location','/calendar')])
  if method=='POST' and path.startswith('/schedule/'):
   pid=path.rsplit('/',1)[-1];p=c.execute('SELECT * FROM posts WHERE post_id=?',(pid,)).fetchone()
   if not p or p['status']!='batch_approved':raise ValueError('Only approved posts can be scheduled')
   dt=datetime.fromisoformat(str(data.get('scheduled_at','')).replace('Z','+00:00'))
   if not dt.tzinfo:raise ValueError('Schedule requires an explicit timezone')
   b=c.execute('SELECT * FROM batches WHERE batch_id=?',(p['approval_batch_id'],)).fetchone();v=c.execute('SELECT * FROM content_versions WHERE version_id=?',(p['current_version_id'],)).fetchone()
   entry=next((x for x in json.loads(b['entries_json']) if x['post_id']==pid),None)
   if b['status']!='approved' or not entry or entry['version_id']!=v['version_id'] or entry['content_hash']!=v['content_hash']:raise ValueError('Approval version mismatch')
   utc=dt.astimezone(timezone.utc).isoformat();c.execute("UPDATE posts SET status='scheduled',scheduled_at=?,updated_at=? WHERE post_id=?",(utc,app.now(),pid))
   c.execute('INSERT INTO publish_jobs(job_id,post_id,version_id,batch_id,run_at,status,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(post_id,version_id) DO NOTHING',(str(uuid.uuid4()),pid,v['version_id'],b['batch_id'],utc,'queued',app.now()))
   audit(c,'scheduled',user,pid,{'version_id':v['version_id'],'scheduled_at_utc':utc});c.commit();return response(start,'303 See Other','',[('Location','/calendar')])
  if method=='GET' and path=='/calendar':
   rows=c.execute('SELECT post_id,status,scheduled_at,current_version_id FROM posts ORDER BY COALESCE(scheduled_at,created_at)').fetchall();table=''.join(f'<tr><td><a href="/post/{esc(r["post_id"])}">{esc(r["post_id"])}</a></td><td>{esc(r["status"])}</td><td>{esc(r["scheduled_at"] or "—")}</td><td>{esc(r["current_version_id"])}</td></tr>' for r in rows)
   return response(start,'200 OK',page('Calendar',f'<table><tr><th>Post</th><th>Status</th><th>Time (UTC)</th><th>Version</th></tr>{table}</table>',user))
  if method=='GET' and path=='/history':
   attempts=c.execute('SELECT attempt_id,post_id,version_id,attempt_number,phase,outcome,started_at,media_id,safe_error FROM attempts ORDER BY started_at DESC LIMIT 200').fetchall();jobs=c.execute('SELECT job_id,post_id,status,attempts,run_at,last_error FROM publish_jobs ORDER BY created_at DESC LIMIT 200').fetchall()
   return response(start,'200 OK',page('Publishing history','<h2>Attempts</h2><pre>'+esc(json.dumps([dict(x) for x in attempts],indent=2))+'</pre><h2>Jobs</h2><pre>'+esc(json.dumps([dict(x) for x in jobs],indent=2))+'</pre>',user))
  if method=='POST' and path in ('/pause','/resume'):
   val='1' if path=='/pause' else '0';c.execute("UPDATE settings SET value=? WHERE key='global_pause'",(val,));audit(c,'global_pause',user,'global',{'paused':val=='1'});c.commit();return response(start,'303 See Other','',[('Location','/health')])
  if method=='GET' and path=='/health':
   pause=c.execute("SELECT value FROM settings WHERE key='global_pause'").fetchone()[0];dbcheck='ok' if c.execute('SELECT 1').fetchone() else 'failed'
   return response(start,'200 OK',page('System health',f'<section class="card"><p>Database: {dbcheck}</p><p>Global pause: {"ON" if pause=="1" else "OFF"}</p><p>Meta/live publishing: disabled</p><form method="post" action="/pause"><input type="hidden" name="csrf" value="{csrf(user,nonce)}"><button>Pause all jobs</button></form><form method="post" action="/resume"><input type="hidden" name="csrf" value="{csrf(user,nonce)}"><button>Resume jobs</button></form></section>',user))
  return response(start,'404 Not Found',page('Not found','Route unavailable',user))
 except Exception as e:
  c.rollback();emit('error','web_operation_failed',path=path,method=method,error_type=type(e).__name__,actor=user)
  msg=str(e) if isinstance(e,ValueError) else 'Operation failed; check service logs.'
  return response(start,'400 Bad Request',page('Operation rejected',esc(msg),user))
 finally:
  if c:c.close()

def main():
 if not SESSION_KEY or not PASSWORD_HASH:print('Run auth_setup.py or configure WEB_SESSION_KEY and WEB_PASSWORD_HASH through the host secret manager.',file=sys.stderr);raise SystemExit(2)
 c=db();c.close()
 class Quiet(WSGIRequestHandler):
  def log_message(self,fmt,*args):emit('info','http_access',request=fmt%args)
 host=os.getenv('WEB_HOST','127.0.0.1');port=int(os.getenv('WEB_PORT','8080'))
 print(f'Local review interface at http://{host}:{port}; Instagram publishing disabled')
 make_server(host,port,app_wsgi,handler_class=Quiet).serve_forever()
if __name__=='__main__':main()
