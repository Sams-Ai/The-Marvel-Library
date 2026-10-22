import json, tempfile, unittest, zipfile
from pathlib import Path
import app, ops

class OpsTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); root=Path(self.t.name); self.old=app.DB; app.DB=root/'db.sqlite'; ops.DB=app.DB
  self.c=ops.con(); self.v=root/'vault';self.v.mkdir()
  x='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Alpha Story</w:t></w:r></w:p><w:p><w:r><w:t>Original text.</w:t></w:r></w:p></w:body></w:document>'
  with zipfile.ZipFile(self.v/'alpha.docx','w') as z:z.writestr('word/document.xml',x)
  (self.v/'The Marvel Library.md').write_text('![[alpha.docx]]')
  app.import_stories(self.c,self.v)
 def tearDown(self):self.c.close();app.DB=self.old;ops.DB=self.old;self.t.cleanup()
 def create_post(self):
  from argparse import Namespace
  media=Path(self.t.name)/'m.jpg';media.write_bytes(b'fake')
  args=Namespace(promotion='story',story=['tml-0001'],caption='sample',format='image',media=[str(media)],alt_text=None)
  app.add_post(self.c,args)
  return self.c.execute('select post_id from posts').fetchone()[0]
 def test_idempotent_import(self):
  app.import_stories(self.c,self.v);self.assertEqual(self.c.execute('select count(*) from stories').fetchone()[0],1)
 def test_search_finds_story_text(self):
  r=self.c.execute("select count(*) from stories where lower(extracted_text) like '%original text%' ").fetchone()[0];self.assertEqual(r,1)
 def test_metadata_preserves_unknowns(self):
  from argparse import Namespace
  ops.metadata(self.c,Namespace(story_id='tml-0001',characters=None,perspective=None,continuity=None,status=None,medium_url=None))
  r=self.c.execute('select characters_json,perspective,continuity,medium_url from stories').fetchone();self.assertEqual(tuple(r),('[]',None,None,None))
 def test_changed_source_flags_post(self):
  pid=self.create_post();self.c.execute("update posts set status='scheduled' where post_id=?",(pid,));self.c.commit()
  x='<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Alpha Story</w:t></w:r></w:p><w:p><w:r><w:t>Revised.</w:t></w:r></w:p></w:body></w:document>'
  with zipfile.ZipFile(self.v/'alpha.docx','w') as z:z.writestr('word/document.xml',x)
  ops.check_sources(self.c)
  self.assertEqual(self.c.execute('select status from posts where post_id=?',(pid,)).fetchone()[0],'needs_review')
  self.assertEqual(self.c.execute('select source_review_required from stories').fetchone()[0],1)
 def test_edit_creates_version_and_resets_status(self):
  from argparse import Namespace
  pid=self.create_post();old=self.c.execute('select current_version_id from posts').fetchone()[0]
  ops.edit(self.c,Namespace(post_id=pid,caption='edited',media=None,format=None,alt_text=None))
  p=self.c.execute('select current_version_id,status,approval_batch_id from posts').fetchone()
  self.assertNotEqual(old,p[0]);self.assertEqual((p[1],p[2]),('draft',None))
  self.assertEqual(self.c.execute('select count(*) from content_versions').fetchone()[0],2)
 def test_disabled_meta_client_never_calls(self):
  import meta_adapter
  with self.assertRaises(meta_adapter.MetaUnavailable):meta_adapter.MetaClient(live_enabled=True).publish_container('x')

if __name__=='__main__':unittest.main(verbosity=2)
