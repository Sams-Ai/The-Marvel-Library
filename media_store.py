"""Private media store interface with local dev and S3-compatible production backends."""
import hashlib,os,io
from pathlib import Path
from PIL import Image,UnidentifiedImageError
MAX_BYTES=10*1024*1024

def validate_jpeg(data,filename='image.jpg'):
 if not data or len(data)>MAX_BYTES:raise ValueError('JPEG empty or larger than 10 MiB')
 if Path(filename).suffix.lower() not in ('.jpg','.jpeg') or not data.startswith(b'\xff\xd8\xff') or not data.endswith(b'\xff\xd9'):
  raise ValueError('Only JPEG files with valid boundary markers are accepted')
 try:
  with Image.open(io.BytesIO(data)) as im:
   if im.format!='JPEG' or im.width<1 or im.height<1 or im.width*im.height>40_000_000:raise ValueError('JPEG dimensions or format are invalid')
   im.verify()
  with Image.open(io.BytesIO(data)) as im:im.load()
 except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as e:raise ValueError('JPEG decode validation failed') from e
 return hashlib.sha256(data).hexdigest()

class LocalMediaStore:
 def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
 def put(self,data,filename='image.jpg'):
  digest=validate_jpeg(data,filename);p=self.root/(digest+'.jpg')
  if not p.exists():p.write_bytes(data)
  if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise IOError('Local media integrity check failed')
  return {'backend':'local','key':p.name,'sha256':digest,'size':len(data),'path':str(p)}
 def signed_url(self,key,expires=300):raise RuntimeError('Local media is not publicly retrievable and cannot be used for live publishing')
 def verify(self,ref):
  p=Path(ref.get('path',''))
  return p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==ref.get('sha256')

class S3MediaStore:
 def __init__(self,bucket,client=None):
  if not bucket:raise ValueError('OBJECT_STORAGE_BUCKET is required')
  self.bucket=bucket
  if client is None:
   import boto3
   client=boto3.client('s3',endpoint_url=os.getenv('OBJECT_STORAGE_ENDPOINT') or None,region_name=os.getenv('OBJECT_STORAGE_REGION') or None)
  self.client=client
 def put(self,data,filename='image.jpg'):
  digest=validate_jpeg(data,filename);key='instagram-assets/'+digest+'.jpg'
  self.client.put_object(Bucket=self.bucket,Key=key,Body=data,ContentType='image/jpeg',Metadata={'sha256':digest},ServerSideEncryption=os.getenv('OBJECT_STORAGE_SSE','AES256'))
  head=self.client.head_object(Bucket=self.bucket,Key=key)
  if head.get('ContentLength')!=len(data) or head.get('Metadata',{}).get('sha256')!=digest:raise IOError('Stored media failed integrity verification')
  return {'backend':'s3','bucket':self.bucket,'key':key,'sha256':digest,'size':len(data)}
 def signed_url(self,key,expires=300):
  if not 60<=expires<=900:raise ValueError('Signed URL lifetime must be between 60 and 900 seconds')
  return self.client.generate_presigned_url('get_object',Params={'Bucket':self.bucket,'Key':key},ExpiresIn=expires)
 def verify(self,ref):
  try:
   h=self.client.head_object(Bucket=ref['bucket'],Key=ref['key'])
   return h.get('ContentLength')==ref['size'] and h.get('Metadata',{}).get('sha256')==ref['sha256']
  except Exception:return False

def from_env():
 bucket=os.getenv('OBJECT_STORAGE_BUCKET')
 if bucket:return S3MediaStore(bucket)
 return LocalMediaStore(os.getenv('LOCAL_MEDIA_DIR','media'))
