"""Additional tests for db_tools.py backup/restore reliability using synthetic data only."""
import os, sqlite3, tempfile, unittest
from pathlib import Path
from db_tools import backup, verify, pg_backup, pg_restore, pg_verify
import psycopg


def make_synthetic_db(path):
    """Create a synthetic SQLite database mimicking the Marvel Library schema."""
    c = sqlite3.connect(path)
    c.execute('''CREATE TABLE stories(
        story_id TEXT PRIMARY KEY, title TEXT NOT NULL,
        source_file_sha256 TEXT NOT NULL, canonical_text_sha256 TEXT NOT NULL,
        extractor_version TEXT NOT NULL, extracted_text TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE posts(
        post_id TEXT PRIMARY KEY, promotion_type TEXT NOT NULL, status TEXT NOT NULL,
        current_version_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE content_versions(
        version_id TEXT PRIMARY KEY, post_id TEXT NOT NULL,
        version_number INTEGER NOT NULL, caption TEXT NOT NULL,
        content_hash TEXT NOT NULL, created_at TEXT NOT NULL)''')
    for sid, raw_hash, canon_hash in SYNTHETIC_STORIES:
        c.execute('INSERT INTO stories VALUES(?,?,?,?,?,?,?,?)',
                  (sid, f'Story {sid}', raw_hash, canon_hash, 'v1',
                   f'Extracted text for {sid}', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
    c.commit()
    c.close()


SYNTHETIC_STORIES = [
    ('tml-synth-1', 'raw-hash-abc', 'canonical-hash-def'),
    ('tml-synth-2', 'raw-hash-def', 'canonical-hash-ghi'),
    ('tml-synth-3', 'raw-hash-ghi', 'canonical-hash-jkl'),
]


class BackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_backup_and_restore_roundtrip_preserves_all_data(self):
        """Backup -> restore-as-new cycle preserves schema, row counts, and values."""
        src = self.root / 'source.sqlite'
        intermediate = self.root / 'intermediate.sqlite'
        final = self.root / 'final.sqlite'
        make_synthetic_db(src)

        # Backup source -> intermediate
        result = backup(src, intermediate)
        self.assertEqual(result['integrity'], 'ok')
        self.assertEqual(result['stories'], 3)

        # Restore intermediate -> final (distinct paths)
        result2 = backup(intermediate, final)
        self.assertEqual(result2['integrity'], 'ok')
        self.assertEqual(result2['stories'], 3)

        # Verify all three stories match in restored DB
        c = sqlite3.connect(final)
        try:
            rows = c.execute('SELECT story_id, source_file_sha256, canonical_text_sha256 FROM stories ORDER BY story_id').fetchall()
            self.assertEqual(len(rows), 3)
            for i, (sid, raw_hash, canon_hash) in enumerate(SYNTHETIC_STORIES):
                self.assertEqual(rows[i][0], sid)
                self.assertEqual(rows[i][1], raw_hash)
                self.assertEqual(rows[i][2], canon_hash)
        finally:
            c.close()

    def test_backup_refuses_to_overwrite_existing_destination(self):
        """Backup to an existing destination must raise FileExistsError."""
        src = self.root / 'source.sqlite'
        dst = self.root / 'dest.sqlite'
        make_synthetic_db(src)
        # Create destination file
        dst.touch()
        with self.assertRaises(FileExistsError):
            backup(src, dst)
        # Destination must remain unchanged (empty file)
        self.assertEqual(dst.stat().st_size, 0)

    def test_restore_as_new_to_existing_destination_raises(self):
        """restore-as-new to an existing destination must also raise FileExistsError."""
        src = self.root / 'source.sqlite'
        dst = self.root / 'dest.sqlite'
        make_synthetic_db(src)
        dst.touch()
        with self.assertRaises(FileExistsError):
            backup(src, dst)  # restore-as_new calls backup internally

    def test_backup_nonexistent_source_raises(self):
        """Backing up a non-existent source must raise an error."""
        src = self.root / 'nonexistent.sqlite'
        dst = self.root / 'dest.sqlite'
        with self.assertRaises(Exception):
            backup(src, dst)

    def test_backup_from_invalid_sqlite_raises(self):
        """Backing up a non-SQLite file must raise an error."""
        src = self.root / 'not_sqlite.txt'
        dst = self.root / 'dest.sqlite'
        src.write_text('this is not a sqlite database')
        with self.assertRaises(Exception):
            backup(src, dst)

    def test_backup_does_not_modify_source(self):
        """Backup must not alter the source database file."""
        src = self.root / 'source.sqlite'
        dst = self.root / 'backup.sqlite'
        make_synthetic_db(src)
        src_hash_before = src.read_bytes()
        result = backup(src, dst)
        src_hash_after = src.read_bytes()
        self.assertEqual(src_hash_before, src_hash_after)
        self.assertEqual(result['integrity'], 'ok')

    def test_backup_captures_full_schema(self):
        """Backup preserves all tables, not just stories."""
        src = self.root / 'source.sqlite'
        dst = self.root / 'backup.sqlite'
        c = sqlite3.connect(src)
        c.execute('CREATE TABLE stories(story_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_file_sha256 TEXT NOT NULL, canonical_text_sha256 TEXT NOT NULL)')
        c.execute('CREATE TABLE posts(post_id TEXT PRIMARY KEY, post_id_val TEXT NOT NULL)')
        c.execute('CREATE TABLE content_versions(version_id TEXT PRIMARY KEY, caption TEXT NOT NULL)')
        c.execute('INSERT INTO stories VALUES("s1","Story 1","raw1","canon1")')
        c.execute('INSERT INTO posts VALUES("p1","post-1")')
        c.execute('INSERT INTO content_versions VALUES("v1","caption-1")')
        c.commit()
        c.close()

        result = backup(src, dst)
        self.assertEqual(result['integrity'], 'ok')

        c = sqlite3.connect(dst)
        try:
            tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            table_names = [r[0] for r in tables]
            self.assertIn('stories', table_names)
            self.assertIn('posts', table_names)
            self.assertIn('content_versions', table_names)
            self.assertEqual(len(table_names), 3)
        finally:
            c.close()


class PostgreSQLBackupRestoreTests(unittest.TestCase):
    """Tests for PostgreSQL backup/restore using a real PostgreSQL server.

    Skipped unless TEST_POSTGRES_URL is set (provided by CI environment).
    All test data is synthetic; no real story data is used.
    """

    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get('TEST_POSTGRES_URL')
        if not cls.dsn:
            raise unittest.SkipTest('TEST_POSTGRES_URL not set')
        import psycopg
        cls.conn = psycopg.connect(cls.dsn)
        cls.conn.autocommit = True
        cur = cls.conn.cursor()
        # Create a disposable test database
        cur.execute('DROP DATABASE IF EXISTS tml_backup_test_src')
        cur.execute('DROP DATABASE IF EXISTS tml_backup_test_dst')
        cur.execute('CREATE DATABASE tml_backup_test_src')
        cur.execute('CREATE DATABASE tml_backup_test_dst')
        cur.close()
        cls.conn.close()

        # Create schema and insert synthetic data in source DB
        src_dsn = cls.dsn.replace('/tml_integration', '/tml_backup_test_src')
        c2 = psycopg.connect(src_dsn)
        cur2 = c2.cursor()
        cur2.execute('''CREATE TABLE stories(
            story_id TEXT PRIMARY KEY, title TEXT NOT NULL,
            source_file_sha256 TEXT NOT NULL, canonical_text_sha256 TEXT NOT NULL,
            extractor_version TEXT NOT NULL, extracted_text TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        c2.execute('''CREATE TABLE posts(
            post_id TEXT PRIMARY KEY, promotion_type TEXT NOT NULL, status TEXT NOT NULL,
            current_version_id TEXT, scheduled_at TEXT, approval_batch_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        c2.execute('''CREATE TABLE content_versions(
            version_id TEXT PRIMARY KEY, post_id TEXT NOT NULL,
            version_number INTEGER NOT NULL, caption TEXT NOT NULL,
            content_hash TEXT NOT NULL, created_at TEXT NOT NULL)''')
        stamp = '2026-01-01T00:00:00Z'
        for sid, raw_hash, canon_hash in SYNTHETIC_STORIES:
            c2.execute('INSERT INTO stories VALUES(?,?,?,?,?,?,?,?)',
                       (sid, f'Story {sid}', raw_hash, canon_hash, 'v1',
                        f'Extracted text for {sid}', stamp, stamp))
        c2.commit()
        c2.close()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'conn') and cls.conn:
            cur = cls.conn.cursor()
            cur.execute('DROP DATABASE IF EXISTS tml_backup_test_src')
            cur.execute('DROP DATABASE IF EXISTS tml_backup_test_dst')
            cur.close()
            cls.conn.close()

    def test_pg_backup_and_restore_preserves_all_data(self):
        """Full backup -> restore cycle preserves all data in PostgreSQL."""
        src_dsn = self.dsn.replace('/tml_integration', '/tml_backup_test_src')
        dst_dsn = self.dsn.replace('/tml_integration', '/tml_backup_test_dst')

        # Backup source database
        backup_path = self.root / 'pg_backup.sql'
        result = pg_backup(src_dsn, backup_path)
        self.assertEqual(result['integrity'], 'ok')
        self.assertTrue(Path(result['backup']).exists())
        self.assertGreater(result['size_bytes'], 0)

        # Restore to destination database
        result2 = pg_restore(backup_path, dst_dsn)
        self.assertEqual(result2['integrity'], 'ok')

        # Verify all data matches in restored DB
        c = psycopg.connect(dst_dsn)
        try:
            rows = c.execute('SELECT story_id, source_file_sha256, canonical_text_sha256 FROM stories ORDER BY story_id').fetchall()
            self.assertEqual(len(rows), 3)
            for i, (sid, raw_hash, canon_hash) in enumerate(SYNTHETIC_STORIES):
                self.assertEqual(rows[i][0], sid)
                self.assertEqual(rows[i][1], raw_hash)
                self.assertEqual(rows[i][2], canon_hash)
        finally:
            c.close()

    def test_backup_refuses_to_overwrite_existing_destination(self):
        """Backup to an existing destination file must raise FileExistsError."""
        src_dsn = self.dsn.replace('/tml_integration', '/tml_backup_test_src')
        dst_path = self.root / 'existing_backup.sql'
        dst_path.write_text('existing content')
        with self.assertRaises(FileExistsError):
            pg_backup(src_dsn, dst_path)
        # Destination must remain unchanged
        self.assertEqual(dst_path.read_text(), 'existing content')

    def test_restore_to_nonexistent_database_raises(self):
        """Restore to a non-existent database must raise an error."""
        backup_path = self.root / 'pg_backup.sql'
        # First create a valid backup
        src_dsn = self.dsn.replace('/tml_integration', '/tml_backup_test_src')
        result = pg_backup(src_dsn, backup_path)
        self.assertEqual(result['integrity'], 'ok')

        # Try to restore to a non-existent database
        bad_dsn = self.dsn.replace('/tml_integration', '/nonexistent_db')
        with self.assertRaises(Exception):
            pg_restore(backup_path, bad_dsn)

    def test_backup_invalid_source_raises(self):
        """Backing up from a non-existent database must raise an error."""
        bad_dsn = self.dsn.replace('/tml_integration', '/nonexistent_db')
        dst_path = self.root / 'backup.sql'
        with self.assertRaises(Exception):
            pg_backup(bad_dsn, dst_path)

    def test_pg_verify_confirms_database_health(self):
        """pg_verify returns integrity='ok' for a healthy database."""
        src_dsn = self.dsn.replace('/tml_integration', '/tml_backup_test_src')
        result = pg_verify(src_dsn)
        self.assertEqual(result['integrity'], 'ok')
        self.assertIn('stories', result['tables'])
        self.assertEqual(result['tables']['stories'], 3)

if __name__ == '__main__':
    unittest.main(verbosity=2)
