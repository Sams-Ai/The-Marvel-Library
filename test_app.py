import hashlib, json, tempfile, unittest, zipfile
from pathlib import Path
from app import docx_text, sha, canonical_version, connect, init_db, import_stories, set_review, create_batch, schedule, fake_publish

class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
  import app
  self.old=app.DB; app.DB=self.root/'test.sqlite'
  self.c=connect(); init_db(self.c)
  self.vault=self.root/'vault'; self.vault.mkdir()
  self.make_docx(self.vault/'one.docx',['Story One','Line A','Line B'])
  (self.vault/'The Marvel Library.md').write_text('![[one.docx]]')
  import_stories(self.c,self.vault)
  self.media=self.root/'image.jpg'
  import io
  from PIL import Image
  buffer=io.BytesIO();Image.new('RGB',(2,2),(10,20,30)).save(buffer,format='JPEG');self.media.write_bytes(buffer.getvalue())
 def tearDown(self):
  import app
  self.c.close(); app.DB=self.old; self.tmp.cleanup()
 def make_docx(self,path,paras):
  xml='<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+''.join('<w:p><w:r><w:t>'+s+'</w:t></w:r></w:p>' for s in paras)+'</w:body></w:document>'
  with zipfile.ZipFile(path,'w') as z: z.writestr('word/document.xml',xml)
 def post(self):
  from argparse import Namespace
  import app
  a=Namespace(promotion='story',story=['tml-0001'],caption='caption',format='image',media=[str(self.media)],alt_text=None)
  app.add_post(self.c,a)
  return self.c.execute('SELECT post_id FROM posts').fetchone()[0]
 def test_import_and_canonical_hash(self):
  t=docx_text(self.vault/'one.docx'); self.assertEqual(t,'Story One\nLine A\nLine B')
  self.assertEqual(sha(t.encode()),sha(docx_text(self.vault/'one.docx').encode()))
  self.assertEqual(canonical_version({'b':2,'a':1}),canonical_version({'a':1,'b':2}))
 def test_tracked_changes_rejected(self):
  p=self.root/'rev.docx'; xml='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:ins/></w:body></w:document>'
  with zipfile.ZipFile(p,'w') as z:z.writestr('word/document.xml',xml)
  with self.assertRaises(ValueError):docx_text(p)
 def test_approval_and_fake_publish_gates(self):
  import app
  pid=self.post()
  with self.assertRaises(ValueError):schedule(self.c,pid,'2020-01-01T00:00:00+00:00')
  set_review(self.c,pid);create_batch(self.c,[pid],'tester')
  schedule(self.c,pid,'2020-01-01T00:00:00+00:00')
  fake_publish(self.c,pid)
  self.assertEqual(self.c.execute('SELECT status FROM posts WHERE post_id=?',(pid,)).fetchone()[0],'published')
  self.assertTrue(self.c.execute('SELECT published_media_id FROM posts WHERE post_id=?',(pid,)).fetchone()[0].startswith('SIM-'))
 def test_pause_blocks_publish(self):
  from app import add_post
  from argparse import Namespace
  pid=self.post();set_review(self.c,pid);create_batch(self.c,[pid],'tester');schedule(self.c,pid,'2020-01-01T00:00:00+00:00')
  self.c.execute("UPDATE settings SET value='1' WHERE key='global_pause'");self.c.commit()
  with self.assertRaises(ValueError):fake_publish(self.c,pid)
 def test_duplicate_fake_publish_blocked(self):
  pid=self.post();set_review(self.c,pid);create_batch(self.c,[pid],'tester');schedule(self.c,pid,'2020-01-01T00:00:00+00:00')
  fake_publish(self.c,pid)
  with self.assertRaises(ValueError):fake_publish(self.c,pid)
 def test_media_change_blocks_publish(self):
  import app
  pid=self.post();set_review(self.c,pid);create_batch(self.c,[pid],'tester');schedule(self.c,pid,'2020-01-01T00:00:00+00:00')
  self.media.write_bytes(b'changed')
  with self.assertRaises(ValueError):fake_publish(self.c,pid)
 def test_export_contains_selected_story_only(self):
  import app
  out=self.root/'export.zip'; app.export(self.c,['tml-0001'],out)
  with zipfile.ZipFile(out) as z:
   self.assertIn('manifest.json',z.namelist()); self.assertIn('stories/tml-0001/story.docx',z.namelist())
   m=json.loads(z.read('manifest.json')); self.assertEqual(len(m['stories']),1)

if __name__=='__main__':unittest.main(verbosity=2)
