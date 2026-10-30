"""Tests for snapshot restoration."""
from contextlib import contextmanager
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.pipeline_contracts import VerifiedRun
from arxiv_pipeline.snapshots.archive import SnapshotArchive
from arxiv_pipeline.snapshots.restorer import SnapshotRestorer


def fake_validator(doc):
    return []


def make_manifest(
    snapshot_id="snap-restore-01",
    run_id="run-restore-01",
    origin_root="/old/path/corpus",
    files=None,
    is_mock=True,
    owners=None,
    fingerprint=None,
    producer_commits=None,
    unknown_provenance_tasks=0,
):
    return {
        "format_version": 1,
        "kind": "arxiv-snapshot",
        "snapshot_id": snapshot_id,
        "schema_version": "1.2",
        "run_id": run_id,
        "is_mock": is_mock,
        "owners": owners or ["owner-r"],
        "created_at": "2026-10-30T08:00:00Z",
        "exporter_commit": "1" * 40,
        "producer_commits": producer_commits if producer_commits is not None else ["2" * 40],
        "unknown_provenance_tasks": unknown_provenance_tasks,
        "config_fingerprint": fingerprint or ("3" * 64),
        "origin_output_root": origin_root,
        "files": files or [],
    }


def build_archive(path, manifest, corpus_files):
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    with tarfile.open(path, "w:gz") as tar:
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(manifest_bytes)
        ti.mode = 0o644
        tar.addfile(ti, io.BytesIO(manifest_bytes))

        for name, data in corpus_files.items():
            ti = tarfile.TarInfo(f"corpus/{name}")
            ti.size = len(data)
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))


class FakeRelocator:
    def __init__(self, reader=None, fail=False):
        self.calls = []
        self.reader = reader
        self.fail = fail

    def relocate(self, output_root: Path, run_id: str, expected_old_root: str, *, snapshot_id: str) -> None:
        if self.reader and self.reader.is_open:
            raise RuntimeError("Relocation invoked while RunReader context is still open (nested lock error)")
        self.calls.append({
            "output_root": output_root,
            "run_id": run_id,
            "expected_old_root": expected_old_root,
            "snapshot_id": snapshot_id,
        })
        if self.fail:
            raise ValueError("Relocation rejected by FakeRelocator")
        if self.reader:
            self.reader.recorded_output_root = str(Path(output_root).resolve())


class FakeRunReader:
    def __init__(
        self,
        *,
        run_id="run-42",
        is_mock=True,
        owners=None,
        recorded_output_root="/original/runs/corpus",
        config_fingerprint=None,
        files=None,
        accepted_results=None,
        fail_on_call=None,
        tamper_after_relocate=None,
    ):
        self.calls = []
        self.is_open = False
        self.run_id = run_id
        self.is_mock = is_mock
        self.owners = owners or ["owner-r"]
        self.recorded_output_root = recorded_output_root
        self.config_fingerprint = config_fingerprint or ("3" * 64)
        self.files = files or []
        self.accepted_results = (
            accepted_results
            if accepted_results is not None
            else [{"owner": "owner-r", "provenance": {"producer_commit": "2" * 40}}]
        )
        self.fail_on_call = fail_on_call  # e.g. 1 (first call) or 2 (second call)
        self.tamper_after_relocate = tamper_after_relocate or {}

    @contextmanager
    def open(self, output_root: Path, run_id: str):
        call_num = len(self.calls) + 1
        self.calls.append({"output_root": output_root, "run_id": run_id, "call_num": call_num})
        if self.fail_on_call == call_num:
            raise ValueError(f"RunReader check failed on call #{call_num}")

        self.is_open = True
        try:
            scope = {
                "schema_version": "1.2",
                "run_id": self.run_id,
                "is_mock": self.is_mock,
                "scope": {
                    "ranges": [{"owner": o} for o in self.owners],
                    "paper_tasks": [],
                    "discovery_complete": True,
                    "versions_complete": True,
                    "owner_count": len(self.owners),
                    "paper_count": len(self.accepted_results),
                },
            }
            results = list(self.accepted_results)
            rec_root = self.recorded_output_root
            fingerprint = self.config_fingerprint

            if call_num >= 2 and self.tamper_after_relocate:
                if "recorded_output_root" in self.tamper_after_relocate:
                    rec_root = self.tamper_after_relocate["recorded_output_root"]
                if "fingerprint" in self.tamper_after_relocate:
                    fingerprint = self.tamper_after_relocate["fingerprint"]
                if "scope" in self.tamper_after_relocate:
                    scope = self.tamper_after_relocate["scope"]
                if "accepted_results" in self.tamper_after_relocate:
                    results = self.tamper_after_relocate["accepted_results"]

            yield VerifiedRun(
                output_root=Path(output_root),
                scope=scope,
                accepted_results=results,
                validation={"valid": True, "issues": []},
                quality={"valid": True, "issues": []},
                files=list(self.files),
                run_state="finished",
                config_fingerprint=fingerprint,
                recorded_output_root=rec_root,
            )
        finally:
            self.is_open = False


class SnapshotRestorerTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.archive_path = self.work_dir / "test_snapshot.tar.gz"

        file_bytes = b"sqlite database placeholder"
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        self.file_entry = {"path": "state.sqlite", "size_bytes": len(file_bytes), "sha256": file_hash}
        self.manifest = make_manifest(
            snapshot_id="snap-42",
            run_id="run-42",
            origin_root="/original/runs/corpus",
            files=[self.file_entry],
        )
        build_archive(self.archive_path, self.manifest, {"state.sqlite": file_bytes})
        self.archive = SnapshotArchive(fake_validator)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_restore_successful_workflow(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            owners=["owner-r"],
            recorded_output_root="/original/runs/corpus",
            config_fingerprint="3" * 64,
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader)
        fixed_time = "2026-10-30T10:00:00Z"

        restorer = SnapshotRestorer(
            self.archive, reader, relocator, clock=lambda: fixed_time
        )
        dest = self.work_dir / "restored"

        result_manifest = restorer.restore(self.archive_path, dest)

        # Returned manifest
        self.assertEqual(result_manifest["snapshot_id"], "snap-42")

        # Verified 2 calls to reader (call 1: before relocate, call 2: after relocate)
        self.assertEqual(len(reader.calls), 2)
        expected_corpus = dest / "corpus"
        self.assertEqual(reader.calls[0]["output_root"], expected_corpus)
        self.assertEqual(reader.calls[1]["output_root"], expected_corpus)

        # Relocator called once, with lock released
        self.assertEqual(len(relocator.calls), 1)
        reloc_call = relocator.calls[0]
        self.assertEqual(reloc_call["output_root"], expected_corpus)
        self.assertEqual(reloc_call["run_id"], "run-42")
        self.assertEqual(reloc_call["expected_old_root"], "/original/runs/corpus")
        self.assertEqual(reloc_call["snapshot_id"], "snap-42")

        # Receipt written atomically outside corpus
        receipt_file = dest / "restore_receipt.json"
        self.assertTrue(receipt_file.is_file())
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(receipt["snapshot_id"], "snap-42")
        self.assertEqual(receipt["run_id"], "run-42")
        self.assertEqual(receipt["old_output_root"], "/original/runs/corpus")
        self.assertEqual(receipt["new_output_root"], str(expected_corpus.resolve()))
        self.assertEqual(receipt["restored_at"], fixed_time)

        # Original manifest preserved beside corpus
        manifest_file = dest / "manifest.json"
        self.assertTrue(manifest_file.is_file())
        unpacked_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(unpacked_manifest["snapshot_id"], "snap-42")

    def test_restore_relocation_failure_leaves_unpacked_copy_without_receipt(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader, fail=True)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "restored_reloc_fail"

        with self.assertRaisesRegex(ValueError, "Relocation rejected"):
            restorer.restore(self.archive_path, dest)

        # Unpacked directory remains for inspection
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "manifest.json").is_file())
        self.assertTrue((dest / "corpus" / "state.sqlite").is_file())

        # No receipt written!
        self.assertFalse((dest / "restore_receipt.json").exists())
        # Reader only called once (before relocation)
        self.assertEqual(len(reader.calls), 1)

    def test_restore_reader_post_check_failure_leaves_unpacked_without_receipt(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
            fail_on_call=2,
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "restored_reader_fail"

        with self.assertRaisesRegex(ValueError, "RunReader check failed on call #2"):
            restorer.restore(self.archive_path, dest)

        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "corpus" / "state.sqlite").is_file())
        self.assertEqual(len(relocator.calls), 1)
        self.assertFalse((dest / "restore_receipt.json").exists())

    def test_restore_refuses_existing_destination(self):
        reader = FakeRunReader()
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "already_here"
        dest.mkdir()

        with self.assertRaises(FileExistsError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0)
        self.assertEqual(len(reader.calls), 0)

    def test_manifest_is_mock_mismatch_blocks_relocation(self):
        # DB has is_mock=False, but manifest says is_mock=True
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=False,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_mock"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when is_mock mismatches")

    def test_manifest_fingerprint_mismatch_blocks_relocation(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            config_fingerprint="f" * 64,  # different from manifest "3" * 64
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_fingerprint"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when fingerprint mismatches")

    def test_manifest_origin_root_mismatch_blocks_relocation(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/different/origin/root",
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_origin"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when origin root mismatches")

    def test_manifest_run_id_mismatch_blocks_relocation(self):
        reader = FakeRunReader(
            run_id="different-run-id",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_run_id"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when run_id mismatches")

    def test_manifest_owners_mismatch_blocks_relocation(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            owners=["different-owner"],
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_owners"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when owners mismatch")

    def test_manifest_files_mismatch_blocks_relocation(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[{"path": "extra.txt", "size_bytes": 10, "sha256": "0" * 64}],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_files"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when files mismatch")

    def test_manifest_provenance_mismatch_blocks_relocation(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
            accepted_results=[{"owner": "owner-r", "provenance": {"producer_commit": "e" * 40}}],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_mismatch_provenance"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 0, "Relocator must not be called when provenance mismatches")

    def test_post_relocation_recorded_output_root_mismatch_fails_without_receipt(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
            tamper_after_relocate={"recorded_output_root": "/wrong/after/root"},
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_post_rec_root_mismatch"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 1)
        self.assertFalse((dest / "restore_receipt.json").exists())

    def test_post_relocation_tampered_fingerprint_or_scope_fails_without_receipt(self):
        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
            tamper_after_relocate={"fingerprint": "9" * 64},
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_post_fingerprint_tampered"

        with self.assertRaises(ValueError):
            restorer.restore(self.archive_path, dest)

        self.assertEqual(len(relocator.calls), 1)
        self.assertFalse((dest / "restore_receipt.json").exists())

    def test_restore_accepts_producer_commits_in_different_order(self):
        commit_a = "a" * 40
        commit_b = "b" * 40
        manifest = make_manifest(
            snapshot_id="snap-order",
            run_id="run-42",
            origin_root="/original/runs/corpus",
            files=[self.file_entry],
            producer_commits=[commit_a, commit_b],
        )
        archive_path = self.work_dir / "order_test.tar.gz"
        build_archive(archive_path, manifest, {"state.sqlite": b"sqlite database placeholder"})

        reader = FakeRunReader(
            run_id="run-42",
            is_mock=True,
            recorded_output_root="/original/runs/corpus",
            files=[self.file_entry],
            accepted_results=[
                {"owner": "owner-r", "provenance": {"producer_commit": commit_b}},
                {"owner": "owner-r", "provenance": {"producer_commit": commit_a}},
            ],
        )
        relocator = FakeRelocator(reader=reader)

        restorer = SnapshotRestorer(self.archive, reader, relocator)
        dest = self.work_dir / "dest_order_ok"

        res_manifest = restorer.restore(archive_path, dest)
        self.assertEqual(res_manifest["producer_commits"], [commit_a, commit_b])
        self.assertTrue((dest / "restore_receipt.json").is_file())


if __name__ == "__main__":
    unittest.main()
