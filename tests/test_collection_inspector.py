from importlib import util
from pathlib import Path
import json
import shutil
import tempfile
import unittest

# The runner's state store locks with fcntl, which Windows does not provide.
NEEDS_RUNNER = unittest.skipIf(util.find_spec("fcntl") is None, "runner state requires fcntl (Linux/macOS/Colab)")


@NEEDS_RUNNER
class RunInspectorTests(unittest.TestCase):
    def setUp(self):
        from arxiv_pipeline.collection.inspector import RunInspector
        from arxiv_pipeline.runner.mock import make_mock_modules
        from arxiv_pipeline.runner.models import RunOptions
        from arxiv_pipeline.runner.service import run_pipeline
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "origin"
        options = RunOptions("nhi-mock", self.root, ("huynh-nhi",), True)
        run_pipeline(options, make_mock_modules(options))
        self.inspector = RunInspector()

    def tearDown(self):
        self.tmp.cleanup()

    def test_verified_run_lists_checked_files(self):
        with self.inspector.open(self.root, "nhi-mock") as run:
            self.assertEqual(run.scope["run_id"], "nhi-mock")
            self.assertTrue(run.validation["valid"])
            self.assertIn(run.run_state, ("paused", "finished"))
            self.assertTrue(run.accepted_results)
            paths = [f["path"] for f in run.files]
            self.assertEqual(paths, sorted(paths))
            self.assertIn("runs/nhi-mock/accepted_results.jsonl", paths)
            self.assertTrue(any(p.startswith("raw/huynh-nhi/") for p in paths))
            self.assertEqual(run.recorded_output_root, str(self.root.resolve()))

    def test_reads_a_copy_without_changing_it(self):
        copy = Path(self.tmp.name) / "copy"
        shutil.copytree(self.root, copy)
        before = (copy / "state.sqlite").read_bytes()
        with self.inspector.open(copy, "nhi-mock") as run:
            self.assertEqual(run.output_root, copy)
            self.assertEqual(run.recorded_output_root, str(self.root.resolve()))
        self.assertEqual((copy / "state.sqlite").read_bytes(), before)
        self.assertFalse((copy / "state.sqlite-journal").exists())

    def test_rejects_changed_artifact_or_export(self):
        with self.inspector.open(self.root, "nhi-mock") as run:
            artifact = next(f["path"] for f in run.files if f["path"].startswith("raw/"))
        target = self.root / artifact
        original = target.read_bytes()
        target.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            with self.inspector.open(self.root, "nhi-mock"):
                pass
        target.write_bytes(original)
        report = self.root / "runs/nhi-mock/validation.json"
        report.write_text(json.dumps({"valid": False, "issues": []}))
        with self.assertRaises(ValueError):
            with self.inspector.open(self.root, "nhi-mock"):
                pass

    def test_rejects_unknown_run_or_missing_database(self):
        with self.assertRaises(ValueError):
            with self.inspector.open(self.root, "other-run"):
                pass
        with self.assertRaises(ValueError):
            with self.inspector.open(Path(self.tmp.name) / "nowhere", "nhi-mock"):
                pass


if __name__ == "__main__":
    unittest.main()
