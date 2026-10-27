from contextlib import redirect_stdout
from importlib import util
import io
from pathlib import Path
import json
import shutil
import tempfile
import unittest

from arxiv_pipeline.collection.cli import main

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
        self.run_mock = lambda root, run_id, **kw: run_pipeline(
            RunOptions(run_id, root, ("huynh-nhi",), True),
            make_mock_modules(RunOptions(run_id, root, ("huynh-nhi",), True)), **kw)
        self.run_mock(self.root, "nhi-mock")
        self.inspector = RunInspector()

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(list(argv))
        return code, (json.loads(out.getvalue()) if out.getvalue() else None)

    def snapshot_of(self, run_root, run_id, collection_root, folder, snapshot_id):
        """Copy a run like an unpacked snapshot; the manifest stands in for Khang's exporter."""
        target = collection_root / folder
        shutil.copytree(run_root, target)
        with self.inspector.open(target, run_id) as run:
            manifest = {"format_version": 1, "kind": "arxiv-snapshot", "snapshot_id": snapshot_id,
                        "schema_version": "1.2", "run_id": run_id, "is_mock": True,
                        "owners": sorted(c["owner"] for c in run.scope["scope"]["ranges"]),
                        "created_at": "2026-10-27T00:00:00Z", "exporter_commit": "a" * 40,
                        "producer_commits": [], "unknown_provenance_tasks": 0,
                        "config_fingerprint": run.config_fingerprint,
                        "origin_output_root": run.recorded_output_root, "files": run.files}
        (target / "manifest.json").write_text(json.dumps(manifest))

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

    def test_paused_run_can_be_inspected_and_resumed(self):
        paused = Path(self.tmp.name) / "paused"
        self.run_mock(paused, "nhi-paused", stop_after_tasks=3)
        with self.inspector.open(paused, "nhi-paused") as run:
            self.assertEqual(run.run_state, "paused")
            self.assertTrue(run.validation["valid"])
            self.assertFalse(run.quality["valid"])  # work is left; listed as issues, not an error
        before = (paused / "state.sqlite").read_bytes()
        self.run_mock(paused, "nhi-paused")  # resume after inspection still works
        self.assertNotEqual((paused / "state.sqlite").read_bytes(), before)
        with self.inspector.open(paused, "nhi-paused") as run:
            self.assertEqual(run.run_state, "finished")

    def test_cli_inspect_lists_issues_and_rejects_bad_runs(self):
        code, report = self.cli("inspect", "--output-root", str(self.root), "--run-id", "nhi-mock")
        self.assertIn(code, (0, 2))
        self.assertEqual(report["run_id"], "nhi-mock")
        self.assertEqual(code == 0, report["quality_valid"] and not report["issues"])
        code, report = self.cli("inspect", "--output-root", str(self.root), "--run-id", "other")
        self.assertEqual((code, report), (1, None))

    def test_end_to_end_assemble_and_check(self):
        collection = Path(self.tmp.name) / "collection"
        self.snapshot_of(self.root, "nhi-mock", collection, "s/nhi", "nhi-1")
        code, report = self.cli("assemble", "--root", str(collection), "--collection-id", "demo", "s/nhi")
        self.assertEqual(code, 0)
        self.assertEqual(report["members"], [{"snapshot_id": "nhi-1", "owners": ["huynh-nhi"]}])
        code, report = self.cli("check", "--root", str(collection))
        self.assertEqual(code, 2)  # four owners have no snapshot yet
        self.assertEqual(report["missing_owners"], ["khang", "trong", "hoang", "man-nhi"])
        self.assertEqual(report["members"][0]["run_id"], "nhi-mock")
        (collection / "s/nhi/runs/nhi-mock/scope.json").write_text("{}")
        code, _ = self.cli("check", "--root", str(collection))
        self.assertEqual(code, 1)  # a changed file fails the snapshot hash check


if __name__ == "__main__":
    unittest.main()
