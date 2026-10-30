"""Unit tests for SnapshotExporter."""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.contracts import RangeConfig, Result, RunScope, Scope, ValidationReport
from arxiv_pipeline.pipeline_contracts import FileDigest, RunReader, SnapshotManifest, VerifiedRun
from arxiv_pipeline.snapshots.common import SnapshotLimits
from arxiv_pipeline.snapshots.exporter import SnapshotExporter


class FakeReader:
    def __init__(self, verified_run: VerifiedRun):
        self.verified_run = verified_run
        self.lock_held = False
        self.opened_count = 0
        self.during_export_checks = []

    @contextmanager
    def open(self, output_root: Path, run_id: str):
        self.opened_count += 1
        self.lock_held = True
        try:
            yield self.verified_run
            for check in self.during_export_checks:
                check()
        finally:
            self.lock_held = False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SnapshotExporterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.corpus_dir = self.root / "corpus"
        self.corpus_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir = self.root / "exports"
        self.export_dir.mkdir(parents=True, exist_ok=True)

        # Create corpus files
        self.files_data = {
            "state.sqlite": b"sqlite database bytes",
            ".runner.lock": b"",
            "runs/run_01/scope.json": b'{"run_id": "run_01"}',
            "runs/run_01/accepted_results.jsonl": b'{"step": "discovery"}\n',
            "runs/run_01/validation.json": b'{"valid": true, "issues": []}',
            "runs/run_01/quality.json": b'{"valid": true, "issues": []}',
            "logs/run_01/events.jsonl": b'{"event": "start"}\n',
            "raw/khang/2501.00001/paper.tex": b"\\documentclass{article}\n\\begin{document}Hello\\end{document}\n",
            "raw/khang/2501.00001/refs.bib": b"@article{author2025,\n  title={Paper}\n}\n",
        }

        self.inventory: list[FileDigest] = []
        for rel_path, data in self.files_data.items():
            file_path = self.corpus_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
            self.inventory.append({
                "path": rel_path,
                "size_bytes": len(data),
                "sha256": sha256_bytes(data),
            })

        # Create untrusted / excluded files in corpus directory
        (self.corpus_dir / "scratch").mkdir(parents=True, exist_ok=True)
        (self.corpus_dir / "scratch" / "temp.log").write_bytes(b"scratch log")
        (self.corpus_dir / "staging").mkdir(parents=True, exist_ok=True)
        (self.corpus_dir / "staging" / "file.tmp").write_bytes(b"staging file")

        # Range config
        self.ranges: list[RangeConfig] = [{
            "owner": "khang",
            "start_month": "2501",
            "start_id": 1,
            "end_month": "2501",
            "end_id": 100,
            "inclusive": True,
        }]

        self.scope: RunScope = {
            "schema_version": "1.2",
            "run_id": "run_01",
            "is_mock": False,
            "scope": {
                "ranges": self.ranges,
                "paper_tasks": [{"owner": "khang", "arxiv_id": "2501.00001"}],
                "discovery_complete": True,
                "versions_complete": True,
                "owner_count": 1,
                "paper_count": 1,
            },
        }

        self.producer_commit = "1" * 40
        self.exporter_commit = "2" * 40
        self.config_fingerprint = "f" * 64

        self.accepted_results: list[Result] = [
            {
                "schema_version": "1.2",
                "run_id": "run_01",
                "owner": "khang",
                "is_mock": False,
                "step": "discovery",
                "arxiv_id": None,
                "version": None,
                "status": "success",
                "data": None,
                "missing_fields": [],
                "error": None,
                "provenance": {"producer_commit": self.producer_commit},
                "metrics": {},
                "started_at": "2026-10-30T00:00:00Z",
                "finished_at": "2026-10-30T00:01:00Z",
                "duration_seconds": 60.0,
                "attempts": 1,
            },
            {
                "schema_version": "1.2",
                "run_id": "run_01",
                "owner": "khang",
                "is_mock": False,
                "step": "metadata",
                "arxiv_id": "2501.00001",
                "version": None,
                "status": "success",
                "data": None,
                "missing_fields": [],
                "error": None,
                "provenance": {},  # No producer commit -> unknown provenance
                "metrics": {},
                "started_at": "2026-10-30T00:01:00Z",
                "finished_at": "2026-10-30T00:02:00Z",
                "duration_seconds": 60.0,
                "attempts": 1,
            },
        ]

        self.verified_run = VerifiedRun(
            output_root=self.corpus_dir,
            scope=self.scope,
            accepted_results=self.accepted_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=list(self.inventory),
            run_state="paused",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        self.reader = FakeReader(self.verified_run)
        self.validator = lambda val: []
        self.clock = lambda: "2026-10-30T08:00:00Z"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inventory_only_tar_layout_and_manifest_fields(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "snapshot_01.tar.gz"

        out_path = exporter.export(
            self.corpus_dir,
            "run_01",
            destination,
            snapshot_id="snap_20261030",
            exporter_commit=self.exporter_commit,
        )

        self.assertEqual(out_path, destination)
        self.assertTrue(destination.is_file())

        with tarfile.open(destination, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("manifest.json", names)
            for item in self.inventory:
                self.assertIn(f"corpus/{item['path']}", names)

            # Untrusted scratch and staging must not be present
            self.assertNotIn("corpus/scratch/temp.log", names)
            self.assertNotIn("corpus/staging/file.tmp", names)
            self.assertEqual(len(names), 1 + len(self.inventory))

            # Verify manifest content
            manifest_file = tar.extractfile("manifest.json")
            self.assertIsNotNone(manifest_file)
            manifest: SnapshotManifest = json.load(manifest_file)
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["kind"], "arxiv-snapshot")
            self.assertEqual(manifest["snapshot_id"], "snap_20261030")
            self.assertEqual(manifest["schema_version"], "1.2")
            self.assertEqual(manifest["run_id"], "run_01")
            self.assertFalse(manifest["is_mock"])
            self.assertEqual(manifest["owners"], ["khang"])
            self.assertEqual(manifest["created_at"], "2026-10-30T08:00:00Z")
            self.assertEqual(manifest["exporter_commit"], self.exporter_commit)
            self.assertEqual(manifest["producer_commits"], [self.producer_commit])
            self.assertEqual(manifest["unknown_provenance_tasks"], 1)
            self.assertEqual(manifest["config_fingerprint"], self.config_fingerprint)
            self.assertEqual(manifest["origin_output_root"], "/original/corpus/root")
            self.assertEqual(manifest["files"], self.inventory)

            # Check preserved bytes
            tex_member = tar.extractfile("corpus/raw/khang/2501.00001/paper.tex")
            self.assertIsNotNone(tex_member)
            self.assertEqual(tex_member.read(), self.files_data["raw/khang/2501.00001/paper.tex"])

    def test_unknown_provenance_tasks_counted_honestly(self):
        # 1 valid commit, 2 unknown
        results = [
            dict(self.accepted_results[0]),
            dict(self.accepted_results[1]),
            {
                **self.accepted_results[0],
                "provenance": {"producer_commit": "not_40_hex"},
            },
        ]
        verified = VerifiedRun(
            output_root=self.corpus_dir,
            scope=self.scope,
            accepted_results=results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=list(self.inventory),
            run_state="finished",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        reader = FakeReader(verified)
        exporter = SnapshotExporter(reader, self.validator, clock=self.clock)
        destination = self.export_dir / "unknown_prov.tar.gz"

        exporter.export(
            self.corpus_dir,
            "run_01",
            destination,
            snapshot_id="snap_prov",
            exporter_commit=self.exporter_commit,
        )

        with tarfile.open(destination, "r:gz") as tar:
            manifest_file = tar.extractfile("manifest.json")
            manifest = json.load(manifest_file)
            self.assertEqual(manifest["producer_commits"], [self.producer_commit])
            self.assertEqual(manifest["unknown_provenance_tasks"], 2)

    def test_quality_false_allowed_for_stopped_run(self):
        verified = VerifiedRun(
            output_root=self.corpus_dir,
            scope=self.scope,
            accepted_results=self.accepted_results,
            validation={"valid": True, "issues": []},
            quality={"valid": False, "issues": [{"message": "coverage low"}]},
            files=list(self.inventory),
            run_state="paused",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        reader = FakeReader(verified)
        exporter = SnapshotExporter(reader, self.validator, clock=self.clock)
        destination = self.export_dir / "quality_false.tar.gz"

        out_path = exporter.export(
            self.corpus_dir,
            "run_01",
            destination,
            snapshot_id="snap_qfalse",
            exporter_commit=self.exporter_commit,
        )
        self.assertTrue(out_path.is_file())

    def test_lock_context_held_until_export_finishes(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "lock_check.tar.gz"

        # Verify that lock is held while inside reader.open
        def assert_locked():
            self.assertTrue(self.reader.lock_held)

        self.reader.during_export_checks.append(assert_locked)

        self.assertFalse(self.reader.lock_held)
        exporter.export(
            self.corpus_dir,
            "run_01",
            destination,
            snapshot_id="snap_lock",
            exporter_commit=self.exporter_commit,
        )
        # Lock must be released after export finishes
        self.assertFalse(self.reader.lock_held)
        self.assertEqual(self.reader.opened_count, 1)

    def test_invalid_ids_and_commit_rejected(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "invalid_ids.tar.gz"

        for bad_snapshot_id in ["bad/slash", "bad space", "", "bad@sym", None]:
            with self.subTest(bad_snapshot_id=bad_snapshot_id), self.assertRaises(ValueError):
                exporter.export(
                    self.corpus_dir, "run_01", destination,
                    snapshot_id=bad_snapshot_id, exporter_commit=self.exporter_commit
                )

        for bad_commit in ["3" * 39, "3" * 41, "G" * 40, "", 12345]:
            with self.subTest(bad_commit=bad_commit), self.assertRaises(ValueError):
                exporter.export(
                    self.corpus_dir, "run_01", destination,
                    snapshot_id="snap_01", exporter_commit=bad_commit
                )

        for bad_run_id in ["run/01", "run 01", "", "mismatch_run"]:
            with self.subTest(bad_run_id=bad_run_id), self.assertRaises(ValueError):
                exporter.export(
                    self.corpus_dir, bad_run_id, destination,
                    snapshot_id="snap_01", exporter_commit=self.exporter_commit
                )

    def test_output_inside_corpus_rejected(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)

        inside_destination = self.corpus_dir / "inside.tar.gz"
        with self.assertRaises(ValueError):
            exporter.export(
                self.corpus_dir, "run_01", inside_destination,
                snapshot_id="snap_01", exporter_commit=self.exporter_commit
            )
        self.assertFalse(inside_destination.exists())

        # Exact corpus dir as destination
        with self.assertRaises(ValueError):
            exporter.export(
                self.corpus_dir, "run_01", self.corpus_dir,
                snapshot_id="snap_01", exporter_commit=self.exporter_commit
            )

    def test_existing_file_rejected_without_modification(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "pre_existing.tar.gz"
        destination.write_bytes(b"initial destination content")

        with self.assertRaises(FileExistsError):
            exporter.export(
                self.corpus_dir, "run_01", destination,
                snapshot_id="snap_01", exporter_commit=self.exporter_commit
            )

        # Content must remain intact
        self.assertEqual(destination.read_bytes(), b"initial destination content")
        # No temporary files left behind
        sibling_files = list(self.export_dir.iterdir())
        self.assertEqual(sibling_files, [destination])

    def test_existing_and_dangling_symlink_destination_rejected(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)

        # 1. Destination is an existing valid symlink
        target_file = self.root / "some_target.tar.gz"
        target_file.write_bytes(b"target file content")
        symlink_dest = self.export_dir / "symlink_dest.tar.gz"
        symlink_dest.symlink_to(target_file)

        with self.assertRaises(FileExistsError):
            exporter.export(
                self.corpus_dir, "run_01", symlink_dest,
                snapshot_id="snap_01", exporter_commit=self.exporter_commit
            )
        self.assertTrue(symlink_dest.is_symlink())
        self.assertEqual(target_file.read_bytes(), b"target file content")

        # 2. Destination is a dangling symlink (target does not exist)
        dangling_dest = self.export_dir / "dangling_dest.tar.gz"
        dangling_dest.symlink_to(self.root / "non_existent_file.tar.gz")
        self.assertTrue(dangling_dest.is_symlink())
        self.assertFalse(dangling_dest.exists())

        with self.assertRaises(FileExistsError):
            exporter.export(
                self.corpus_dir, "run_01", dangling_dest,
                snapshot_id="snap_01", exporter_commit=self.exporter_commit
            )
        # Dangling symlink must NOT be overwritten or unlinked
        self.assertTrue(dangling_dest.is_symlink())
        self.assertFalse(dangling_dest.exists())
        self.assertEqual(
            sorted(p.name for p in self.export_dir.iterdir()),
            sorted(["symlink_dest.tar.gz", "dangling_dest.tar.gz"])
        )

    def test_mock_os_link_error_cleans_up_and_does_not_replace(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "mock_link_err.tar.gz"

        with patch("os.link", side_effect=OSError("Disk full or link failure")):
            with self.assertRaises(OSError):
                exporter.export(
                    self.corpus_dir, "run_01", destination,
                    snapshot_id="snap_01", exporter_commit=self.exporter_commit
                )

        self.assertFalse(destination.exists())
        self.assertEqual(list(self.export_dir.iterdir()), [])

    def test_parent_directory_symlink_in_inventory_rejected(self):
        # Create a real directory, put a file in it, then create a parent symlink
        real_parent = self.corpus_dir / "raw" / "khang" / "real_dir"
        real_parent.mkdir(parents=True, exist_ok=True)
        sample_file = real_parent / "paper.tex"
        sample_file.write_bytes(b"content")

        sym_parent = self.corpus_dir / "raw" / "khang" / "sym_dir"
        sym_parent.symlink_to(real_parent)

        bad_inventory = list(self.inventory) + [{
            "path": "raw/khang/sym_dir/paper.tex",
            "size_bytes": len(b"content"),
            "sha256": sha256_bytes(b"content"),
        }]

        verified = VerifiedRun(
            output_root=self.corpus_dir,
            scope=self.scope,
            accepted_results=self.accepted_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=bad_inventory,
            run_state="paused",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        reader = FakeReader(verified)
        exporter = SnapshotExporter(reader, self.validator, clock=self.clock)
        destination = self.export_dir / "parent_sym_fail.tar.gz"

        with self.assertRaisesRegex(ValueError, "Symlinks are not accepted"):
            exporter.export(
                self.corpus_dir, "run_01", destination,
                snapshot_id="snap_sym_parent", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.export_dir.iterdir()), [])

    def test_symlink_in_inventory_rejected(self):
        # Create a symlink in corpus and add to inventory
        symlink_target = self.corpus_dir / "state.sqlite"
        symlink_file = self.corpus_dir / "link_to_state.sqlite"
        symlink_file.symlink_to(symlink_target)

        bad_inventory = list(self.inventory) + [{
            "path": "link_to_state.sqlite",
            "size_bytes": symlink_target.stat().st_size,
            "sha256": sha256_bytes(symlink_target.read_bytes()),
        }]

        verified = VerifiedRun(
            output_root=self.corpus_dir,
            scope=self.scope,
            accepted_results=self.accepted_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=bad_inventory,
            run_state="paused",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        reader = FakeReader(verified)
        exporter = SnapshotExporter(reader, self.validator, clock=self.clock)
        destination = self.export_dir / "symlink_fail.tar.gz"

        with self.assertRaises(ValueError):
            exporter.export(
                self.corpus_dir, "run_01", destination,
                snapshot_id="snap_sym", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.export_dir.iterdir()), [])

    def test_mutated_inventory_file_hash_rejected(self):
        # Corrupt the file content after inventory was built
        target_file = self.corpus_dir / "state.sqlite"
        target_file.write_bytes(b"corrupted sqlite content")

        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "mutated_fail.tar.gz"

        with self.assertRaises(ValueError):
            exporter.export(
                self.corpus_dir, "run_01", destination,
                snapshot_id="snap_mut", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.export_dir.iterdir()), [])

    def test_size_limits_enforced(self):
        # 1. max_files limit: we have 9 files in inventory + 1 manifest = 10 files
        limits_files = SnapshotLimits(max_files=5)
        exporter_files = SnapshotExporter(self.reader, self.validator, limits=limits_files, clock=self.clock)
        destination_files = self.export_dir / "limit_files.tar.gz"

        with self.assertRaisesRegex(ValueError, "max_files"):
            exporter_files.export(
                self.corpus_dir, "run_01", destination_files,
                snapshot_id="snap_lim1", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination_files.exists())

        # 2. max_file_bytes limit
        limits_file_bytes = SnapshotLimits(max_file_bytes=10)
        exporter_fbytes = SnapshotExporter(self.reader, self.validator, limits=limits_file_bytes, clock=self.clock)
        destination_fbytes = self.export_dir / "limit_fbytes.tar.gz"

        with self.assertRaisesRegex(ValueError, "max_file_bytes"):
            exporter_fbytes.export(
                self.corpus_dir, "run_01", destination_fbytes,
                snapshot_id="snap_lim2", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination_fbytes.exists())

        # 3. max_total_bytes limit
        limits_total = SnapshotLimits(max_total_bytes=50)
        exporter_total = SnapshotExporter(self.reader, self.validator, limits=limits_total, clock=self.clock)
        destination_total = self.export_dir / "limit_total.tar.gz"

        with self.assertRaisesRegex(ValueError, "max_total_bytes"):
            exporter_total.export(
                self.corpus_dir, "run_01", destination_total,
                snapshot_id="snap_lim3", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination_total.exists())

    def test_empty_scope_stopped_run_preserves_scope(self):
        empty_scope: RunScope = {
            "schema_version": "1.2",
            "run_id": "run_empty",
            "is_mock": True,
            "scope": {
                "ranges": self.ranges,
                "paper_tasks": [],
                "discovery_complete": False,
                "versions_complete": False,
                "owner_count": 0,
                "paper_count": 0,
            },
        }
        verified = VerifiedRun(
            output_root=self.corpus_dir,
            scope=empty_scope,
            accepted_results=[],
            validation={"valid": True, "issues": []},
            quality={"valid": False, "issues": []},
            files=list(self.inventory),
            run_state="paused",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        reader = FakeReader(verified)
        exporter = SnapshotExporter(reader, self.validator, clock=self.clock)
        destination = self.export_dir / "empty_scope.tar.gz"

        exporter.export(
            self.corpus_dir,
            "run_empty",
            destination,
            snapshot_id="snap_empty",
            exporter_commit=self.exporter_commit,
        )

        with tarfile.open(destination, "r:gz") as tar:
            manifest = json.load(tar.extractfile("manifest.json"))
            self.assertEqual(manifest["run_id"], "run_empty")
            self.assertTrue(manifest["is_mock"])
            self.assertEqual(manifest["owners"], ["khang"])
            self.assertEqual(manifest["producer_commits"], [])
            self.assertEqual(manifest["unknown_provenance_tasks"], 0)

    def test_non_stopped_run_state_rejected(self):
        verified = VerifiedRun(
            output_root=self.corpus_dir,
            scope=self.scope,
            accepted_results=self.accepted_results,
            validation={"valid": True, "issues": []},
            quality={"valid": True, "issues": []},
            files=list(self.inventory),
            run_state="running",
            config_fingerprint=self.config_fingerprint,
            recorded_output_root="/original/corpus/root",
        )
        reader = FakeReader(verified)
        exporter = SnapshotExporter(reader, self.validator, clock=self.clock)
        destination = self.export_dir / "running.tar.gz"

        with self.assertRaises(ValueError):
            exporter.export(
                self.corpus_dir, "run_01", destination,
                snapshot_id="snap_run", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination.exists())

    def test_file_size_changed_before_read_rejected(self):
        # Truncate a file on disk so its size doesn't match inventory
        target_file = self.corpus_dir / "state.sqlite"
        target_file.write_bytes(b"short")

        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        destination = self.export_dir / "size_mismatch.tar.gz"

        with self.assertRaisesRegex(ValueError, "size mismatch"):
            exporter.export(
                self.corpus_dir, "run_01", destination,
                snapshot_id="snap_size", exporter_commit=self.exporter_commit
            )
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.export_dir.iterdir()), [])

    def test_destination_parent_dirs_created_automatically(self):
        exporter = SnapshotExporter(self.reader, self.validator, clock=self.clock)
        nested_destination = self.export_dir / "nested" / "folder" / "snap.tar.gz"
        self.assertFalse(nested_destination.parent.exists())

        out_path = exporter.export(
            self.corpus_dir, "run_01", nested_destination,
            snapshot_id="snap_nested", exporter_commit=self.exporter_commit
        )
        self.assertEqual(out_path, nested_destination)
        self.assertTrue(nested_destination.is_file())

    def test_service_delegates_to_exporter(self):
        from arxiv_pipeline.snapshots.service import SnapshotService
        service = SnapshotService(reader=self.reader, validator=self.validator, clock=self.clock)
        destination = self.export_dir / "via_service.tar.gz"

        out_path = service.export(
            self.corpus_dir, "run_01", destination,
            snapshot_id="snap_service", exporter_commit=self.exporter_commit
        )
        self.assertEqual(out_path, destination)
        self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
