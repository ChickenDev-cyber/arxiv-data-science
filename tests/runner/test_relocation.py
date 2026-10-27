from importlib import import_module, util
from contextlib import closing
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.service import run_pipeline
from arxiv_pipeline.runner.state import OutputLock, StateStore, load_options
from .helpers import make_v1_corpus


class RelocationTests(unittest.TestCase):
    def test_relocate_rejects_scope_that_disagrees_with_saved_config(self):
        relocator = self.relocator()
        for damage in ('range', 'paper_limit', 'candidate_limit'):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                origin, restored = Path(tmp) / 'origin', Path(tmp) / 'copy'
                make_v1_corpus(origin)
                shutil.copytree(origin, restored)
                report = restored / 'runs/legacy/scope.json'
                scope = json.loads(report.read_text())
                if damage == 'range':
                    scope['scope']['ranges'][0]['start_id'] = 1
                else:
                    ids = [17523, 17550] if damage == 'candidate_limit' else list(range(17523, 17529))
                    scope['scope'].update(paper_tasks=[{'owner': 'trong', 'arxiv_id': f'2504.{n:05d}'} for n in ids],
                                          paper_count=len(ids), versions_complete=False)
                report.write_text(json.dumps(scope))
                import sqlite3
                with closing(sqlite3.connect(restored / 'state.sqlite')) as connection:
                    connection.execute('UPDATE runs SET scope_json=?', (json.dumps(scope),))
                    connection.commit()
                before = (restored / 'state.sqlite').read_bytes()
                with self.assertRaisesRegex(ValueError, 'scope|Scope|configuration|limit'):
                    relocator.relocate(restored, 'legacy', str(origin), snapshot_id='invalid-scope')
                self.assertEqual((restored / 'state.sqlite').read_bytes(), before)

    def relocator(self):
        self.assertIsNotNone(util.find_spec("arxiv_pipeline.runner.restore"),
                             "The state relocation service is missing")
        return import_module("arxiv_pipeline.runner.restore").RunStateRelocator()

    def test_relocate_preserves_run_scope_results_and_fingerprint(self):
        relocator = self.relocator()
        with tempfile.TemporaryDirectory() as tmp:
            origin, restored = Path(tmp) / "origin", Path(tmp) / "restored"
            make_v1_corpus(origin)
            original_database = (origin / "state.sqlite").read_bytes()
            shutil.copytree(origin, restored)
            with StateStore.readonly(restored / "state.sqlite", "legacy") as before:
                record = before.read_run_record()
                scope = before.read_scope()
                results = list(before.iter_results(accepted_only=True))
                hashes = before.artifact_inventory()
            relocator.relocate(restored, "legacy", str(origin), snapshot_id="snapshot-1")
            with StateStore.readonly(restored / "state.sqlite", "legacy") as after:
                changed = after.read_run_record()
                self.assertEqual(changed.fingerprint, record.fingerprint)
                self.assertEqual(changed.config, {**record.config, "output_root": str(restored)})
                self.assertEqual(after.read_scope(), scope)
                self.assertEqual(list(after.iter_results(accepted_only=True)), results)
                self.assertEqual(after.artifact_inventory(), hashes)
            self.assertEqual((origin / "state.sqlite").read_bytes(), original_database)
            options = load_options(restored / "state.sqlite", "legacy")
            resumed = run_pipeline(options, make_mock_modules(options))
            self.assertEqual(resumed.executed_tasks, 0)
            self.assertTrue(resumed.validation["valid"])

    def test_relocate_rejects_wrong_origin_or_identity(self):
        relocator = self.relocator()
        with tempfile.TemporaryDirectory() as tmp:
            origin, restored = Path(tmp) / "origin", Path(tmp) / "restored"
            make_v1_corpus(origin)
            shutil.copytree(origin, restored)
            before = (restored / "state.sqlite").read_bytes()
            for run_id, old_root, snapshot in (("wrong-run", str(origin), "snap"),
                                                 ("legacy", "/wrong", "snap"),
                                                 ("legacy", str(origin), "../bad")):
                with self.subTest(run_id=run_id, old_root=old_root, snapshot=snapshot):
                    with self.assertRaises(ValueError):
                        relocator.relocate(restored, run_id, old_root, snapshot_id=snapshot)
                    self.assertEqual((restored / "state.sqlite").read_bytes(), before)

    def test_relocate_rejects_active_writer(self):
        relocator = self.relocator()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_v1_corpus(root)
            with OutputLock(root), self.assertRaises(ValueError):
                relocator.relocate(root, "legacy", str(root), snapshot_id="snapshot")

    def test_relocate_rejects_damaged_or_unready_corpus(self):
        relocator = self.relocator()
        for damage in ("source", "validation", "scope", "prepared"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                origin, restored = Path(tmp) / "origin", Path(tmp) / "restored"
                make_v1_corpus(origin)
                shutil.copytree(origin, restored)
                if damage == "source":
                    (restored / "raw/trong/2504-17523/v1/main.tex").write_bytes(b"corrupt")
                elif damage == "validation":
                    (restored / "runs/legacy/validation.json").write_text('{"valid":false,"issues":[]}')
                elif damage == "scope":
                    (restored / "runs/legacy/scope.json").write_text('{}')
                else:
                    import sqlite3
                    with closing(sqlite3.connect(restored / "state.sqlite")) as connection:
                        connection.execute("UPDATE publications SET state='prepared' WHERE attempt_id=1")
                        connection.commit()
                before = (restored / "state.sqlite").read_bytes()
                with self.assertRaises(ValueError):
                    relocator.relocate(restored, "legacy", str(origin), snapshot_id="snap")
                self.assertEqual((restored / "state.sqlite").read_bytes(), before)
