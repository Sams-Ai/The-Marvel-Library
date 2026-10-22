import tempfile
from pathlib import Path
import unittest
from media_store import LocalMediaStore,S3MediaStore,validate_jpeg
class FakeS3:
 def __init__(self):self.obj={}
 def put_object(self,**kw):self.obj[(kw['Bucket'],kw['Key'])]=kw
 def head_object(self,Bucket,Key):
  x=self.obj[(Bucket,Key)];return {'ContentLength':len(x['Body']),'Metadata':x['Metadata']}
 def generate_presigned_url(self,*a,**kw):return 'https://private.example/'+kw['Params']['Key']
class MediaStoreTests(unittest.TestCase):
 def setUp(self):
  import io
  from PIL import Image
  out=io.BytesIO();Image.new('RGB',(2,2),(20,30,40)).save(out,format='JPEG');self.jpg=out.getvalue()
 def test_jpeg_validation_and_local_integrity(self):
  with tempfile.TemporaryDirectory() as d:
   s=LocalMediaStore(Path(d));r=s.put(self.jpg,'x.jpg');self.assertTrue(s.verify(r));self.assertEqual(validate_jpeg(self.jpg,'x.jpeg'),r['sha256'])
  with self.assertRaises(ValueError):validate_jpeg(b'nope','x.png')
 def test_s3_private_object_integrity_and_signed_expiry(self):
  client=FakeS3();s=S3MediaStore('private-bucket',client);r=s.put(self.jpg,'a.jpg');self.assertTrue(s.verify(r));self.assertTrue(s.signed_url(r['key'],60).startswith('https://'))
  with self.assertRaises(ValueError):s.signed_url(r['key'],3600)
if __name__=='__main__':unittest.main(verbosity=2)
