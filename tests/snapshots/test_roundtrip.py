"""Independent roundtrip, tamper/collision, and restore/resume integration tests.

These tests exercise SnapshotService through its public contract:
- Baseline source corpus validation (genuine mock state, authoritative files)
- Snapshot roundtrip (export -> verify -> unpack)
- Tamper and collision protection (existing destination, inside corpus, member corruption)
- Restore and resume (StateRelocator, restore_receipt.json, pipeline resumption)
"""
import hashlib
import importlib.util
import io
import json
from contextlib import contextmanager
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.pipeline_contracts import FileDigest, SnapshotManifest, VerifiedRun
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.restore import RunStateRelocator
from arxiv_pipeline.runner.service import run_pipeline
from arxiv_pipeline.runner.state import OutputLock, StateStore, load_options
from arxiv_pipeline.snapshots.common import ManifestValidator, SnapshotLimits
from arxiv_pipeline.snapshots.service import SnapshotService
from tests.runner.helpers import make_v1_corpus


def fake_manifest_validator(document: object) -> list[str]:
    """Clearly labelled fake ManifestValidator for independent testing."""
    if not isinstance(document, dict):
        return ["Manifest must be a JSON object"]
    issues = []
    if document.get("format_version") != 1:
        issues.append("/format_version: must be 1")
    if document.get("kind") != "arxiv-snapshot":
        issues.append("/kind: must be 'arxiv-snapshot'")
    if document.get("schema_version") != "1.2":
        issues.append("/schema_version: must be '1.2'")
    for field in (
        "snapshot_id",
        "run_id",
        "is_mock",
        "owners",
        "created_at",
        "exporter_commit",
        "producer_commits",
        "unknown_provenance_tasks",
        "config_fingerprint",
        "origin_output_root",
        "files",
    ):
        if field not in document:
            issues.append(f"/{field}: required field missing")
    if "files" in document:
        if not isinstance(document["files"], list) or not document["files"]:
            issues.append("/files: must be a non-empty list of FileDigest")
        else:
            for idx, entry in enumerate(document["files"]):
                if not isinstance(entry, dict):
                    issues.append(f"/files/{idx}: must be an object")
                elif any(k not in entry for k in ("path", "size_bytes", "sha256")):
                    issues.append(f"/files/{idx}: missing path, size_bytes or sha256")
    return issues


def get_test_validator() -> ManifestValidator:
    """Resolve lazy common validator if available, else test fake."""
    try:
        from arxiv_pipeline.snapshots.common import load_manifest_validator
        return load_manifest_validator()
    except Exception:
        return fake_manifest_validator


class FakeRunReader:
    """Fake RunReader test adapter reading stopped genuine SQLite corpus."""

    @contextmanager
    def open(self, output_root: Path, run_id: str):
        root = Path(output_root)
        db_path = root / "state.sqlite"
        if not db_path.is_file():
            raise ValueError(f"Missing state database at: {db_path}")

        with OutputLock(root):
            with StateStore.readonly(db_path, run_id) as store:
                record = store.read_run_record()
                if record.status not in ("paused", "finished"):
                    raise ValueError(f"Run {run_id} is not stopped (status={record.status})")
                if store.pending_publications():
                    raise ValueError(f"Run {run_id} has pending publications")

                scope = store.read_scope()
                accepted_results = list(store.iter_results(accepted_only=True))

                reports = root / f"runs/{run_id}"
                val_path = reports / "validation.json"
                qual_path = reports / "quality.json"
                validation = json.loads(val_path.read_text()) if val_path.is_file() else {"valid": True, "issues": []}
                quality = json.loads(qual_path.read_text()) if qual_path.is_file() else {"valid": True, "issues": []}

                required_paths = [
                    "state.sqlite",
                    ".runner.lock",
                    f"runs/{run_id}/scope.json",
                    f"runs/{run_id}/accepted_results.jsonl",
                    f"runs/{run_id}/validation.json",
                    f"runs/{run_id}/quality.json",
                    f"logs/{run_id}/events.jsonl",
                ]

                files: list[FileDigest] = []
                seen_paths: set[str] = set()

                for rel in required_paths:
                    p = root / rel
                    if p.is_file():
                        data = p.read_bytes()
                        files.append({
                            "path": rel,
                            "size_bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        })
                        seen_paths.add(rel)

                for artifact in store.artifact_inventory():
                    rel = artifact.path
                    if rel not in seen_paths:
                        p = root / rel
                        if p.is_file():
                            data = p.read_bytes()
                            files.append({
                                "path": rel,
                                "size_bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            })
                            seen_paths.add(rel)

                files.sort(key=lambda item: item["path"])

                yield VerifiedRun(
                    output_root=root,
                    scope=scope,
                    accepted_results=accepted_results,
                    validation=validation,
                    quality=quality,
                    files=files,
                    run_state=record.status,
                    config_fingerprint=record.fingerprint,
                    recorded_output_root=record.config["output_root"],
                )


def corpus_fingerprint(root: Path) -> dict[str, str]:
    """Calculate sha256 checksums for all regular files under a directory."""
    result = {}
    for p in root.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(root))
            result[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


class SnapshotRoundtripTests(unittest.TestCase):
    """End-to-end contract validation for snapshots through SnapshotService."""

    def test_01_baseline_corpus_integrity(self):
        """Verify the test corpus baseline conforms to authoritative requirements."""
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp) / "origin"
            make_v1_corpus(origin)

            self.assertTrue((origin / "state.sqlite").is_file())
            self.assertTrue((origin / ".runner.lock").is_file())
            self.assertTrue((origin / "runs/legacy/scope.json").is_file())
            self.assertTrue((origin / "runs/legacy/accepted_results.jsonl").is_file())
            self.assertTrue((origin / "runs/legacy/validation.json").is_file())
            self.assertTrue((origin / "runs/legacy/quality.json").is_file())
            self.assertTrue((origin / "logs/legacy/events.jsonl").is_file())

            tex_files = list(origin.glob("raw/**/*.tex"))
            bib_files = list(origin.glob("raw/**/*.bib"))
            self.assertTrue(len(tex_files) > 0, "Corpus must contain .tex source files")
            self.assertTrue(len(bib_files) > 0, "Corpus must contain .bib source files")
            for tf in tex_files:
                self.assertGreater(tf.stat().st_size, 0)
            for bf in bib_files:
                self.assertGreater(bf.stat().st_size, 0)

            reader = FakeRunReader()
            with reader.open(origin, "legacy") as run:
                self.assertEqual(run.run_state, "finished")
                self.assertTrue(run.validation["valid"])
                self.assertTrue(run.quality["valid"])
                self.assertEqual(run.recorded_output_root, str(origin))
                self.assertGreater(len(run.files), 0)
                file_map = {f["path"]: f for f in run.files}
                self.assertIn("state.sqlite", file_map)
                self.assertIn("raw/trong/2504-17523/v1/main.tex", file_map)
                self.assertIn("raw/trong/2504-17523/v1/refs.bib", file_map)

    def test_02_snapshot_roundtrip_export_verify_unpack(self):
        """Export a genuine corpus, verify archive layout/manifest, and unpack cleanly."""
        if not is_module_available("arxiv_pipeline.snapshots.exporter"):
            self.skipTest("SnapshotExporter implementation pending from worker khang-export")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            origin = tmp_path / "origin"
            make_v1_corpus(origin)
            before_fingerprint = corpus_fingerprint(origin)

            service = SnapshotService(
                reader=FakeRunReader(),
                relocator=RunStateRelocator(),
                validator=get_test_validator(),
            )
            archive_path = tmp_path / "bundle.tar.gz"
            snapshot_id = "roundtrip-snap-01"
            exporter_commit = "0123456789abcdef0123456789abcdef01234567"

            exported_path = service.export(
                origin,
                "legacy",
                archive_path,
                snapshot_id=snapshot_id,
                exporter_commit=exporter_commit,
            )
            self.assertEqual(exported_path, archive_path)
            self.assertTrue(archive_path.is_file())

            # Original corpus must remain completely untouched
            self.assertEqual(
                corpus_fingerprint(origin),
                before_fingerprint,
                "Export must never mutate the origin corpus",
            )

            # Verification
            manifest = service.verify(archive_path)
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["kind"], "arxiv-snapshot")
            self.assertEqual(manifest["schema_version"], "1.2")
            self.assertEqual(manifest["snapshot_id"], snapshot_id)
            self.assertEqual(manifest["run_id"], "legacy")
            self.assertEqual(manifest["exporter_commit"], exporter_commit)
            self.assertEqual(manifest["origin_output_root"], str(origin))
            self.assertTrue(len(manifest["files"]) > 0)

            # Archive member layout inspection
            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()
                self.assertIn("manifest.json", names)
                for member in tar.getmembers():
                    self.assertFalse(member.name.startswith("/"), "Archive must not have absolute member paths")
                    self.assertNotIn("..", member.name.split("/"), "Archive must not contain traversal paths")
                    if member.name != "manifest.json":
                        self.assertTrue(
                            member.name.startswith("corpus/"),
                            f"Corpus files must be nested under corpus/, got: {member.name}",
                        )
                    # Forbid staging/scratch/journals
                    self.assertNotIn("staging", member.name)
                    self.assertNotIn("scratch", member.name)
                    self.assertFalse(member.name.endswith("-journal"))

            # Unpack
            unpack_dest = tmp_path / "unpacked"
            unpacked_manifest = service.unpack(archive_path, unpack_dest)
            self.assertEqual(unpacked_manifest, manifest)
            self.assertTrue((unpack_dest / "manifest.json").is_file())
            self.assertTrue((unpack_dest / "corpus").is_dir())

            # Verify every declared file in manifest is present with correct bytes
            unpacked_corpus = unpack_dest / "corpus"
            for fentry in manifest["files"]:
                target_file = unpacked_corpus / fentry["path"]
                self.assertTrue(target_file.is_file(), f"Missing unpacked file: {fentry['path']}")
                content = target_file.read_bytes()
                self.assertEqual(len(content), fentry["size_bytes"])
                self.assertEqual(hashlib.sha256(content).hexdigest(), fentry["sha256"])

            # Verify .tex and .bib bytes are identical to source
            for tex_p in origin.glob("raw/**/*.tex"):
                rel = tex_p.relative_to(origin)
                self.assertEqual(
                    (unpacked_corpus / rel).read_bytes(),
                    tex_p.read_bytes(),
                    f"TeX bytes changed during export/unpack for {rel}",
                )
            for bib_p in origin.glob("raw/**/*.bib"):
                rel = bib_p.relative_to(origin)
                self.assertEqual(
                    (unpacked_corpus / rel).read_bytes(),
                    bib_p.read_bytes(),
                    f"Bib bytes changed during export/unpack for {rel}",
                )

    def test_03_tamper_and_collision_protection(self):
        """Verify rejection of collisions, in-corpus export, and tampered archives."""
        if not is_module_available("arxiv_pipeline.snapshots.exporter"):
            self.skipTest("SnapshotExporter implementation pending from worker khang-export")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            origin = tmp_path / "origin"
            make_v1_corpus(origin)
            before_fingerprint = corpus_fingerprint(origin)

            service = SnapshotService(
                reader=FakeRunReader(),
                relocator=RunStateRelocator(),
                validator=get_test_validator(),
            )
            snapshot_id = "collision-snap-01"
            commit = "0123456789abcdef0123456789abcdef01234567"

            # 1. Export collision: target archive exists
            existing_archive = tmp_path / "existing.tar.gz"
            existing_archive.write_bytes(b"existing-archive-payload")
            with self.assertRaises((FileExistsError, ValueError, OSError)):
                service.export(origin, "legacy", existing_archive, snapshot_id=snapshot_id, exporter_commit=commit)
            self.assertEqual(existing_archive.read_bytes(), b"existing-archive-payload")

            # 2. Export inside corpus forbidden
            inside_corpus = origin / "backup.tar.gz"
            with self.assertRaises(ValueError):
                service.export(origin, "legacy", inside_corpus, snapshot_id=snapshot_id, exporter_commit=commit)
            self.assertFalse(inside_corpus.exists())

            # Generate valid archive for downstream tamper/collision tests
            valid_archive = tmp_path / "valid.tar.gz"
            service.export(origin, "legacy", valid_archive, snapshot_id=snapshot_id, exporter_commit=commit)
            valid_archive_bytes = valid_archive.read_bytes()

            # 3. Unpack collision: destination exists
            existing_dest = tmp_path / "existing_dir"
            existing_dest.mkdir()
            (existing_dest / "sentinel.txt").write_text("keep_me")
            with self.assertRaises((FileExistsError, ValueError, OSError)):
                service.unpack(valid_archive, existing_dest)
            self.assertEqual((existing_dest / "sentinel.txt").read_text(), "keep_me")

            # 4. Restore collision: destination exists
            with self.assertRaises((FileExistsError, ValueError, OSError)):
                service.restore(valid_archive, existing_dest)
            self.assertEqual((existing_dest / "sentinel.txt").read_text(), "keep_me")

            # 5. Tampered member bytes: alter a file inside the archive
            tampered_archive = tmp_path / "tampered.tar.gz"
            with tarfile.open(valid_archive, "r:gz") as reader, tarfile.open(tampered_archive, "w:gz") as writer:
                for member in reader.getmembers():
                    extracted = reader.extractfile(member)
                    if member.name.endswith(".tex"):
                        corrupted = b"corrupted tex content!"
                        ti = tarfile.TarInfo(name=member.name)
                        ti.size = len(corrupted)
                        ti.mode = member.mode
                        writer.addfile(ti, io.BytesIO(corrupted))
                    else:
                        writer.addfile(member, extracted)

            with self.assertRaises(ValueError):
                service.verify(tampered_archive)

            tampered_dest = tmp_path / "dest_tampered_unpack"
            with self.assertRaises(ValueError):
                service.unpack(tampered_archive, tampered_dest)
            self.assertFalse(tampered_dest.exists(), "Failed unpack must clean up temporary destination")

            # 6. Tampered manifest: invalid schema
            corrupt_manifest_archive = tmp_path / "corrupt_manifest.tar.gz"
            with tarfile.open(valid_archive, "r:gz") as reader, tarfile.open(corrupt_manifest_archive, "w:gz") as writer:
                for member in reader.getmembers():
                    extracted = reader.extractfile(member)
                    if member.name == "manifest.json":
                        raw = json.loads(extracted.read().decode("utf-8"))
                        raw["format_version"] = 999  # Invalid format version
                        corrupted = json.dumps(raw).encode("utf-8")
                        ti = tarfile.TarInfo(name=member.name)
                        ti.size = len(corrupted)
                        writer.addfile(ti, io.BytesIO(corrupted))
                    else:
                        writer.addfile(member, extracted)

            with self.assertRaises(ValueError):
                service.verify(corrupt_manifest_archive)

            # 7. Corrupt gzip truncated footer (missing CRC-32 / ISIZE trailer)
            truncated_archive = tmp_path / "truncated_footer.tar.gz"
            truncated_archive.write_bytes(valid_archive_bytes[:-4])
            with self.assertRaisesRegex(ValueError, "Corrupt gzip|corrupt|trailer|footer"):
                service.verify(truncated_archive)
            with self.assertRaisesRegex(ValueError, "Corrupt gzip|corrupt|trailer|footer"):
                service.unpack(truncated_archive, tmp_path / "dest_truncated")
            self.assertFalse((tmp_path / "dest_truncated").exists())

            # Ensure valid archive and origin remained intact throughout
            self.assertEqual(valid_archive.read_bytes(), valid_archive_bytes)
            self.assertEqual(corpus_fingerprint(origin), before_fingerprint)

    def test_04_restore_and_resume_pipeline(self):
        """Restore archive, verify receipt and relocation, and resume mock pipeline."""
        if not is_module_available("arxiv_pipeline.snapshots.exporter"):
            self.skipTest("SnapshotExporter implementation pending from worker khang-export")
        if not is_module_available("arxiv_pipeline.snapshots.restorer"):
            self.skipTest("SnapshotRestorer implementation pending from worker khang-restore")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            origin = tmp_path / "origin"
            make_v1_corpus(origin)
            before_origin_fingerprint = corpus_fingerprint(origin)

            service = SnapshotService(
                reader=FakeRunReader(),
                relocator=RunStateRelocator(),
                validator=get_test_validator(),
            )
            snapshot_id = "restore-resume-01"
            commit = "0123456789abcdef0123456789abcdef01234567"
            archive_path = tmp_path / "export.tar.gz"

            service.export(origin, "legacy", archive_path, snapshot_id=snapshot_id, exporter_commit=commit)
            archive_bytes = archive_path.read_bytes()

            # Restore
            restore_dest = tmp_path / "restored"
            restored_manifest = service.restore(archive_path, restore_dest)
            self.assertEqual(restored_manifest["snapshot_id"], snapshot_id)

            # Archive and origin immutability
            self.assertEqual(archive_path.read_bytes(), archive_bytes)
            self.assertEqual(corpus_fingerprint(origin), before_origin_fingerprint)

            # Destination layout verification
            self.assertTrue((restore_dest / "manifest.json").is_file())
            self.assertTrue((restore_dest / "corpus").is_dir())
            receipt_file = restore_dest / "restore_receipt.json"
            self.assertTrue(receipt_file.is_file(), "restore_receipt.json must exist outside corpus/")

            receipt = json.loads(receipt_file.read_text())
            self.assertEqual(receipt["snapshot_id"], snapshot_id)
            self.assertEqual(receipt["run_id"], "legacy")
            self.assertEqual(receipt["old_output_root"], str(origin))
            expected_new_root = str((restore_dest / "corpus").resolve())
            self.assertIn(receipt["new_output_root"], (expected_new_root, str(restore_dest / "corpus")))
            self.assertIn("restored_at", receipt)

            # Database relocation verification
            restored_corpus = restore_dest / "corpus"
            with StateStore.readonly(restored_corpus / "state.sqlite", "legacy") as store:
                rec = store.read_run_record()
                self.assertEqual(rec.config["output_root"], str(restored_corpus))

            # Resume the pipeline from the restored corpus
            options = load_options(restored_corpus / "state.sqlite", "legacy")
            modules = make_mock_modules(options)

            # Ensure good sources are reused and never redownloaded
            with patch.object(
                modules.sources,
                "fetch_sources",
                side_effect=AssertionError("fetch_sources should not be called when resuming complete run"),
            ):
                outcome = run_pipeline(options, modules)

            self.assertEqual(outcome.executed_tasks, 0, "No tasks should be re-executed on completed run")
            self.assertEqual(outcome.reused_tasks, 5, "All 5 paper tasks should be reused")
            self.assertTrue(outcome.validation["valid"])
            self.assertTrue(outcome.quality["valid"])

    def test_05_restore_relocation_failure_leaves_unpacked_without_receipt(self):
        """Relocation failure leaves unpacked copy for inspection but writes no receipt."""
        if not is_module_available("arxiv_pipeline.snapshots.exporter"):
            self.skipTest("SnapshotExporter implementation pending from worker khang-export")
        if not is_module_available("arxiv_pipeline.snapshots.restorer"):
            self.skipTest("SnapshotRestorer implementation pending from worker khang-restore")

        class FailingRelocator:
            def relocate(self, output_root: Path, run_id: str, expected_old_root: str, *, snapshot_id: str) -> None:
                raise ValueError("Simulated relocation failure for test")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            origin = tmp_path / "origin"
            make_v1_corpus(origin)

            normal_service = SnapshotService(
                reader=FakeRunReader(),
                relocator=RunStateRelocator(),
                validator=get_test_validator(),
            )
            archive_path = tmp_path / "snap.tar.gz"
            normal_service.export(
                origin,
                "legacy",
                archive_path,
                snapshot_id="fail-reloc-01",
                exporter_commit="0123456789abcdef0123456789abcdef01234567",
            )

            failing_service = SnapshotService(
                reader=FakeRunReader(),
                relocator=FailingRelocator(),
                validator=get_test_validator(),
            )
            restore_dest = tmp_path / "fail_dest"

            with self.assertRaisesRegex(ValueError, "Simulated relocation failure"):
                failing_service.restore(archive_path, restore_dest)

            # Spec: Leave verified unpacked copy without receipt for inspection
            self.assertTrue((restore_dest / "manifest.json").is_file())
            self.assertTrue((restore_dest / "corpus").is_dir())
            self.assertFalse(
                (restore_dest / "restore_receipt.json").exists(),
                "Must never write receipt if relocation failed",
            )

    def test_06_restore_rejects_tampered_manifest_identity(self):
        """Restore must reject archives where manifest claims false is_mock or fingerprint."""
        if not is_module_available("arxiv_pipeline.snapshots.exporter"):
            self.skipTest("SnapshotExporter implementation pending from worker khang-export")
        if not is_module_available("arxiv_pipeline.snapshots.restorer"):
            self.skipTest("SnapshotRestorer implementation pending from worker khang-restore")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            origin = tmp_path / "origin"
            make_v1_corpus(origin)

            service = SnapshotService(
                reader=FakeRunReader(),
                relocator=RunStateRelocator(),
                validator=get_test_validator(),
            )
            good_archive = tmp_path / "good.tar.gz"
            service.export(
                origin,
                "legacy",
                good_archive,
                snapshot_id="identity-guard-01",
                exporter_commit="0123456789abcdef0123456789abcdef01234567",
            )

            # 1. Tamper manifest is_mock
            bad_mock_archive = tmp_path / "bad_mock.tar.gz"
            with tarfile.open(good_archive, "r:gz") as src, tarfile.open(bad_mock_archive, "w:gz") as tgt:
                for member in src:
                    f = src.extractfile(member)
                    if member.name == "manifest.json":
                        doc = json.loads(f.read().decode("utf-8"))
                        doc["is_mock"] = not doc["is_mock"]
                        data = json.dumps(doc).encode("utf-8")
                        ti = tarfile.TarInfo(name=member.name)
                        ti.size = len(data)
                        tgt.addfile(ti, io.BytesIO(data))
                    else:
                        tgt.addfile(member, f)

            with self.assertRaisesRegex(ValueError, "is_mock mismatch"):
                service.restore(bad_mock_archive, tmp_path / "dest_bad_mock")
            self.assertFalse((tmp_path / "dest_bad_mock" / "restore_receipt.json").exists())

            # 2. Tamper manifest config_fingerprint
            bad_fp_archive = tmp_path / "bad_fp.tar.gz"
            with tarfile.open(good_archive, "r:gz") as src, tarfile.open(bad_fp_archive, "w:gz") as tgt:
                for member in src:
                    f = src.extractfile(member)
                    if member.name == "manifest.json":
                        doc = json.loads(f.read().decode("utf-8"))
                        doc["config_fingerprint"] = "f" * 64
                        data = json.dumps(doc).encode("utf-8")
                        ti = tarfile.TarInfo(name=member.name)
                        ti.size = len(data)
                        tgt.addfile(ti, io.BytesIO(data))
                    else:
                        tgt.addfile(member, f)

            with self.assertRaisesRegex(ValueError, "Fingerprint mismatch"):
                service.restore(bad_fp_archive, tmp_path / "dest_bad_fp")
            self.assertFalse((tmp_path / "dest_bad_fp" / "restore_receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
