import sqlite3,tempfile,unittest
from pathlib import Path
from db_tools import backup,verify
class BackupTests(unittest.TestCase):
 def test_backup_verifies_hashes_without_changing_source(self):
  with tempfile.TemporaryDirectory() as d:
   src=Path(d)/'src.sqlite';dst=Path(d)/'backups'/'copy.sqlite'
   c=sqlite3.connect(src);c.execute('create table stories(story_id text,source_file_sha256 text,canonical_text_sha256 text)');c.execute('insert into stories values(?,?,?)',('tml-0099','abc','def'));c.commit();c.close()
   before=verify(src);result=backup(src,dst);self.assertEqual(result['stories'],1);self.assertEqual(before,verify(src));self.assertEqual(verify(src),verify(dst))
   with self.assertRaises(FileExistsError):backup(src,dst)
if __name__=='__main__':unittest.main(verbosity=2)
