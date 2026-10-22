"""JSON application logs with conservative key-based secret redaction."""
import json,logging,re
LOG=logging.getLogger('marvel_library')
if not LOG.handlers:
 h=logging.StreamHandler();h.setFormatter(logging.Formatter('%(message)s'));LOG.addHandler(h);LOG.setLevel(logging.INFO);LOG.propagate=False
SENSITIVE=re.compile(r'(token|secret|password|credential|authorization|cookie|session|key)',re.I)
def scrub(value,key=''):
 if SENSITIVE.search(key):return '[REDACTED]'
 if isinstance(value,dict):return {str(k):scrub(v,str(k)) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [scrub(x) for x in value]
 if isinstance(value,(str,int,float,bool)) or value is None:return value
 return str(value)
def emit(level,event,**fields):
 payload={'service':'marvel-library','event':event,**scrub(fields)}
 getattr(LOG,level if level in ('debug','info','warning','error','critical') else 'info')(json.dumps(payload,separators=(',',':'),ensure_ascii=False))
