from contextlib import redirect_stderr, redirect_stdout
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

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = main(list(argv))
        return code, (json.loads(out.getvalue()) if out.getvalue() else None)

    def rejects(self, root, run_id="nhi-mock", message=""):
        with self.assertRaisesRegex(ValueError, message):
            with self.inspector.open(root, run_id):
                pass

    def copy_inventory(self, source, run_id, target):
        """Copy only the files the inspector lists, nothing else from the output root."""
        with self.inspector.open(source, run_id) as run:
            files = run.files
        for f in files:
            (target / f["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / f["path"], target / f["path"])
        return files

    def snapshot_of(self, run_root, run_id, collection_root, folder, snapshot_id, **changes):
        """Unpacked snapshot in the agreed layout; the manifest stands in for Khang's exporter."""
        target = collection_root / folder
        (target / "corpus").mkdir(parents=True)
        self.copy_inventory(run_root, run_id, target / "corpus")
        with self.inspector.open(target / "corpus", run_id) as run:
            manifest = {"format_version": 1, "kind": "arxiv-snapshot", "snapshot_id": snapshot_id,
                        "schema_version": "1.2", "run_id": run_id, "is_mock": True,
                        "owners": sorted(c["owner"] for c in run.scope["scope"]["ranges"]),
                        "created_at": "2026-10-27T00:00:00Z", "exporter_commit": "a" * 40,
                        "producer_commits": [], "unknown_provenance_tasks": 0,
                        "config_fingerprint": run.config_fingerprint,
                        "origin_output_root": run.recorded_output_root, "files": run.files, **changes}
        (target / "manifest.json").write_text(json.dumps(manifest))

    def test_verified_run_lists_checked_files(self):
        with self.inspector.open(self.root, "nhi-mock") as run:
            self.assertEqual(run.scope["run_id"], "nhi-mock")
            self.assertTrue(run.validation["valid"])
            self.assertIn(run.run_state, ("paused", "finished"))
            self.assertTrue(run.accepted_results)
            paths = [f["path"] for f in run.files]
            self.assertEqual(paths, sorted(paths))
            for path in ("state.sqlite", ".runner.lock", "logs/nhi-mock/events.jsonl",
                         "runs/nhi-mock/accepted_results.jsonl", "runs/nhi-mock/quality.json"):
                self.assertIn(path, paths)
            self.assertTrue(any(p.startswith("raw/huynh-nhi/") for p in paths))
            self.assertEqual(run.recorded_output_root, str(self.root.resolve()))

    def test_copy_of_exact_inventory_reopens_with_same_files(self):
        copy = Path(self.tmp.name) / "inventory-only"
        copy.mkdir()
        files = self.copy_inventory(self.root, "nhi-mock", copy)
        with self.inspector.open(copy, "nhi-mock") as run:
            self.assertEqual(run.files, files)
            self.assertEqual(run.recorded_output_root, str(self.root.resolve()))
        # Every listed file is needed: without one of them the copy no longer opens.
        for name in ("state.sqlite", ".runner.lock", "logs/nhi-mock/events.jsonl", "runs/nhi-mock/quality.json"):
            with self.subTest(missing=name):
                partial = Path(self.tmp.name) / ("without-" + name.replace("/", "-"))
                shutil.copytree(copy, partial)
                (partial / name).unlink()
                self.rejects(partial)

    def test_reads_a_copy_without_changing_it(self):
        copy = Path(self.tmp.name) / "copy"
        shutil.copytree(self.root, copy)
        before = {p: p.read_bytes() for p in copy.rglob("*") if p.is_file()}
        with self.inspector.open(copy, "nhi-mock") as run:
            self.assertEqual(run.output_root, copy)
            self.assertEqual(run.recorded_output_root, str(self.root.resolve()))
        self.assertEqual({p: p.read_bytes() for p in copy.rglob("*") if p.is_file()}, before)

    def test_rejects_changed_artifact_or_export(self):
        with self.inspector.open(self.root, "nhi-mock") as run:
            artifact = next(f["path"] for f in run.files if f["path"].startswith("raw/"))
        target = self.root / artifact
        original = target.read_bytes()
        target.write_bytes(original + b" ")
        self.rejects(self.root)
        target.write_bytes(original)
        report = self.root / "runs/nhi-mock/validation.json"
        report.write_text(json.dumps({"valid": False, "issues": []}))
        self.rejects(self.root)

    def test_events_must_match_sqlite(self):
        events = self.root / "logs/nhi-mock/events.jsonl"
        lines = events.read_text().splitlines(keepends=True)
        for changed in (lines[:-1], lines + lines[:1], lines[1:] + lines[:1]):  # lost, repeated, reordered
            with self.subTest(lines=len(changed)):
                events.write_text("".join(changed))
                self.rejects(self.root, message="Event log")
        events.write_text("".join(lines))
        with self.inspector.open(self.root, "nhi-mock"):
            pass

    def test_quality_report_must_be_well_formed_and_complete(self):
        report = self.root / "runs/nhi-mock/quality.json"
        issue = {"owner": "huynh-nhi", "arxiv_id": None, "version": None, "step": "discovery",
                 "field": None, "message": "x"}
        for bad in ({"valid": True}, {"valid": "yes", "issues": []}, {"valid": True, "issues": [issue]},
                    {"valid": False, "issues": []}, {"valid": False, "issues": [{"message": "x"}]}, []):
            with self.subTest(bad=bad):
                report.write_text(json.dumps(bad))
                self.rejects(self.root, message="Quality report")
        report.write_text(json.dumps({"valid": False, "issues": [issue]}))  # extra issues are allowed
        with self.inspector.open(self.root, "nhi-mock") as run:
            self.assertFalse(run.quality["valid"])

    def test_holds_the_output_root_lock(self):
        from arxiv_pipeline.runner.state import OutputLock
        with OutputLock(self.root):
            self.rejects(self.root, message="Another runner")
        with self.inspector.open(self.root, "nhi-mock"):
            with self.assertRaisesRegex(ValueError, "Another runner"):
                self.run_mock(self.root, "nhi-mock")
        self.run_mock(self.root, "nhi-mock")  # the lock is released after reading

    def test_rejects_unknown_run_or_missing_database(self):
        self.rejects(self.root, "other-run")
        nowhere = Path(self.tmp.name) / "nowhere"
        self.rejects(nowhere, message="lock file")
        self.assertFalse(nowhere.exists())  # inspecting never creates the output root

    def test_paused_run_can_be_inspected_and_resumed(self):
        paused = Path(self.tmp.name) / "paused"
        self.run_mock(paused, "nhi-paused", stop_after_tasks=3)
        with self.inspector.open(paused, "nhi-paused") as run:
            self.assertEqual(run.run_state, "paused")
            self.assertTrue(run.validation["valid"])
            self.assertFalse(run.quality["valid"])  # work is left; listed as issues, not an error
            self.assertTrue(run.quality["issues"])
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

    def test_end_to_end_snapshot_in_agreed_layout(self):
        collection = Path(self.tmp.name) / "collection"
        self.snapshot_of(self.root, "nhi-mock", collection, "snapshots/nhi", "nhi-1")
        code, report = self.cli("assemble", "--root", str(collection), "--collection-id", "demo", "snapshots/nhi")
        self.assertEqual(code, 0)
        self.assertEqual(report["members"], [{"snapshot_id": "nhi-1", "owners": ["huynh-nhi"]}])
        member = json.loads((collection / "collection.json").read_text())["members"][0]
        self.assertEqual((member["root"], member["manifest_path"]),
                         ("snapshots/nhi/corpus", "snapshots/nhi/manifest.json"))
        code, report = self.cli("check", "--root", str(collection))
        self.assertEqual(code, 2)  # four owners have no snapshot yet
        self.assertEqual(report["missing_owners"], ["khang", "trong", "hoang", "man-nhi"])
        self.assertEqual(report["members"][0]["run_id"], "nhi-mock")
        (collection / "snapshots/nhi/corpus/runs/nhi-mock/scope.json").write_text("{}")
        code, _ = self.cli("check", "--root", str(collection))
        self.assertEqual(code, 1)  # a changed file fails the snapshot hash check

    def test_check_rejects_manifest_that_differs_from_run(self):
        with self.inspector.open(self.root, "nhi-mock") as run:
            files = run.files
        cases = {"origin": dict(origin_output_root="/somewhere/else"),
                 "inventory": dict(files=[f for f in files if f["path"] != "state.sqlite"])}
        for name, changes in cases.items():
            with self.subTest(name):
                collection = Path(self.tmp.name) / ("collection-" + name)
                self.snapshot_of(self.root, "nhi-mock", collection, "snapshots/nhi", "nhi-1", **changes)
                self.assertEqual(self.cli("assemble", "--root", str(collection), "--collection-id", "demo",
                                          "snapshots/nhi")[0], 0)
                self.assertEqual(self.cli("check", "--root", str(collection)), (1, None))


if __name__ == "__main__":
    unittest.main()
