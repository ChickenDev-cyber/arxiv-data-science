import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from arxiv_pipeline.runner.state import StateStore
from .helpers import make_v1_corpus


class ReadonlyStateTests(unittest.TestCase):
    def test_readonly_wal_never_creates_sidecar_or_ignores_wal_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_v1_corpus(root)
            with closing(sqlite3.connect(root / 'state.sqlite')) as connection:
                self.assertEqual(connection.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            before_files = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
            before = (root / 'state.sqlite').read_bytes()
            # WAL is explicitly unsupported for immutable handoff inspection;
            # refusal must happen before SQLite creates -wal/-shm files.
            with self.assertRaisesRegex(ValueError, 'WAL'):
                self.readonly(root / 'state.sqlite', 'legacy')
            self.assertEqual(sorted(str(p.relative_to(root)) for p in root.rglob('*')), before_files)
            self.assertEqual((root / 'state.sqlite').read_bytes(), before)

    def test_readonly_refuses_uncheckpointed_wal_instead_of_reading_stale_main_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_v1_corpus(root)
            with closing(sqlite3.connect(root / 'state.sqlite')) as writer:
                writer.execute('PRAGMA journal_mode=WAL')
                writer.execute("UPDATE runs SET status='paused'")
                writer.commit()
                wal = root / 'state.sqlite-wal'
                self.assertGreater(wal.stat().st_size, 0)
                before = wal.read_bytes()
                with self.assertRaisesRegex(ValueError, 'WAL'):
                    self.readonly(root / 'state.sqlite', 'legacy')
                self.assertEqual(wal.read_bytes(), before)


    def readonly(self, path, run_id):
        self.assertTrue(hasattr(StateStore, "readonly"), "Read-only state API is missing")
        return StateStore.readonly(path, run_id)

    def test_readonly_does_not_create_missing_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "missing/state.sqlite"
            with self.assertRaises(ValueError):
                self.readonly(target, "missing")
            self.assertFalse(target.parent.exists())

    def test_readonly_v1_does_not_migrate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_v1_corpus(root)
            before = hashlib.sha256((root / "state.sqlite").read_bytes()).hexdigest()
            before_files = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
            with self.readonly(root / "state.sqlite", "legacy") as store:
                self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(len(list(store.iter_results(accepted_only=True))), 5)
            self.assertEqual(hashlib.sha256((root / "state.sqlite").read_bytes()).hexdigest(), before)
            self.assertEqual(sorted(str(p.relative_to(root)) for p in root.rglob("*")), before_files)

    def test_readonly_exposes_run_config_and_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = make_v1_corpus(root)
            with self.readonly(root / "state.sqlite", "legacy") as store:
                record = store.read_run_record()
                self.assertEqual(record.run_id, "legacy")
                self.assertEqual(record.status, "finished")
                self.assertEqual(record.config, options.to_dict())
                inventory = store.artifact_inventory()
                names = {artifact.path for artifact in inventory}
                self.assertIn("raw/trong/2504-17523/v1/main.tex", names)
                self.assertIn("raw/trong/2504-17523/v2/main.tex", names)
                self.assertTrue(all(hashlib.sha256((root / a.path).read_bytes()).hexdigest() == a.sha256
                                    for a in inventory))

    def test_readonly_rejects_writes_and_unknown_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_v1_corpus(root)
            with self.readonly(root / "state.sqlite", "legacy") as store:
                with self.assertRaises(sqlite3.Error):
                    store.set_run_status("error")
            with self.assertRaises(ValueError):
                self.readonly(root / "state.sqlite", "another-run")
