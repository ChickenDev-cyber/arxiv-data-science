"""Tests for snapshot archive verification and unpacking."""
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from arxiv_pipeline.snapshots.archive import SnapshotArchive
from arxiv_pipeline.snapshots.common import SnapshotLimits


def fake_validator(doc):
    """Clearly labelled test fake validator returning no issues."""
    if not isinstance(doc, dict):
        return ["manifest must be a dict"]
    if doc.get("kind") != "arxiv-snapshot":
        return ["kind must be arxiv-snapshot"]
    return []


def make_manifest(snapshot_id="snap-001", run_id="run-001", files=None):
    return {
        "format_version": 1,
        "kind": "arxiv-snapshot",
        "snapshot_id": snapshot_id,
        "schema_version": "1.2",
        "run_id": run_id,
        "is_mock": True,
        "owners": ["owner-a"],
        "created_at": "2026-10-30T08:00:00Z",
        "exporter_commit": "1" * 40,
        "producer_commits": ["2" * 40],
        "unknown_provenance_tasks": 0,
        "config_fingerprint": "3" * 64,
        "origin_output_root": "/data/orig/corpus",
        "files": files or [],
    }


def build_tar_gz(dest_path, file_entries, dir_entries=None, raw_members=None):
    """Build a hand-crafted tar.gz archive.

    file_entries: dict of member_name -> bytes
    dir_entries: list of directory names
    raw_members: list of custom TarInfo or callbacks
    """
    with tarfile.open(dest_path, "w:gz") as tar:
        if dir_entries:
            for d in dir_entries:
                ti = tarfile.TarInfo(d)
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tar.addfile(ti)
        for name, data in file_entries.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))
        if raw_members:
            for member_factory in raw_members:
                member_factory(tar)


class SnapshotArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.archive_path = self.work_dir / "snapshot.tar.gz"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_verify_and_unpack_valid_archive(self):
        file1_content = b"database-bytes-sqlite"
        file1_hash = hashlib.sha256(file1_content).hexdigest()
        file2_content = b"\\documentclass{article}\n\\begin{document}Hello\\end{document}\n"
        file2_hash = hashlib.sha256(file2_content).hexdigest()

        manifest = make_manifest(
            files=[
                {"path": "state.sqlite", "size_bytes": len(file1_content), "sha256": file1_hash},
                {"path": "runs/run-001/doc.tex", "size_bytes": len(file2_content), "sha256": file2_hash},
            ]
        )
        manifest_bytes = json.dumps(manifest).encode("utf-8")

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": manifest_bytes,
                "corpus/state.sqlite": file1_content,
                "corpus/runs/run-001/doc.tex": file2_content,
            },
            dir_entries=["corpus", "corpus/runs", "corpus/runs/run-001"],
        )

        archive = SnapshotArchive(fake_validator)

        # verify
        verified_manifest = archive.verify(self.archive_path)
        self.assertEqual(verified_manifest["snapshot_id"], "snap-001")
        self.assertEqual(len(verified_manifest["files"]), 2)
        # Verify does not unpack or create directories
        self.assertFalse((self.work_dir / "unpacked").exists())

        # unpack
        destination = self.work_dir / "unpacked"
        unpacked_manifest = archive.unpack(self.archive_path, destination)
        self.assertEqual(unpacked_manifest["snapshot_id"], "snap-001")
        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / "manifest.json").is_file())
        self.assertEqual((destination / "corpus" / "state.sqlite").read_bytes(), file1_content)
        self.assertEqual((destination / "corpus" / "runs" / "run-001" / "doc.tex").read_bytes(), file2_content)

    def test_path_traversal_dot_dot_rejected(self):
        content = b"evil"
        manifest = make_manifest(files=[{"path": "evil.txt", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/../escape.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_path_traversal_absolute_path_rejected(self):
        content = b"evil"
        manifest = make_manifest(files=[{"path": "evil.txt", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "/etc/evil.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_path_traversal_windows_drive_rejected(self):
        content = b"evil"
        manifest = make_manifest(files=[{"path": "evil.txt", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "C:evil.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_path_traversal_backslash_rejected(self):
        content = b"evil"
        manifest = make_manifest(files=[{"path": "evil.txt", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus\\evil.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_symlink_member_rejected(self):
        manifest = make_manifest(files=[])
        def add_symlink(tar):
            ti = tarfile.TarInfo("corpus/symlink")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "corpus/target"
            tar.addfile(ti)

        build_tar_gz(
            self.archive_path,
            file_entries={"manifest.json": json.dumps(manifest).encode("utf-8")},
            raw_members=[add_symlink],
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_hardlink_member_rejected(self):
        manifest = make_manifest(files=[])
        def add_hardlink(tar):
            ti = tarfile.TarInfo("corpus/hardlink")
            ti.type = tarfile.LNKTYPE
            ti.linkname = "manifest.json"
            tar.addfile(ti)

        build_tar_gz(
            self.archive_path,
            file_entries={"manifest.json": json.dumps(manifest).encode("utf-8")},
            raw_members=[add_hardlink],
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_duplicate_member_in_tar_rejected(self):
        content = b"data"
        h = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(files=[{"path": "file.txt", "size_bytes": len(content), "sha256": h}])

        def add_duplicate(tar):
            ti1 = tarfile.TarInfo("corpus/file.txt")
            ti1.size = len(content)
            tar.addfile(ti1, io.BytesIO(content))
            ti2 = tarfile.TarInfo("corpus/file.txt")
            ti2.size = len(content)
            tar.addfile(ti2, io.BytesIO(content))

        build_tar_gz(
            self.archive_path,
            file_entries={"manifest.json": json.dumps(manifest).encode("utf-8")},
            raw_members=[add_duplicate],
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_duplicate_declared_file_in_manifest_rejected(self):
        content = b"data"
        h = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(
            files=[
                {"path": "file.txt", "size_bytes": len(content), "sha256": h},
                {"path": "file.txt", "size_bytes": len(content), "sha256": h},
            ]
        )
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/file.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_missing_corpus_file_rejected(self):
        manifest = make_manifest(
            files=[{"path": "missing.txt", "size_bytes": 10, "sha256": "0" * 64}]
        )
        build_tar_gz(
            self.archive_path,
            file_entries={"manifest.json": json.dumps(manifest).encode("utf-8")},
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_extra_corpus_file_rejected(self):
        content = b"allowed"
        h = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(files=[{"path": "allowed.txt", "size_bytes": len(content), "sha256": h}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/allowed.txt": content,
                "corpus/extra.txt": b"surprise",
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_file_outside_corpus_rejected(self):
        manifest = make_manifest(files=[])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "scratch/random.txt": b"bad",
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_missing_manifest_rejected(self):
        content = b"file"
        build_tar_gz(
            self.archive_path,
            file_entries={"corpus/file.txt": content},
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_corrupt_tar_gzip_rejected(self):
        # Plain non-gzip garbage
        self.archive_path.write_bytes(b"NOT A GZIP OR TAR FILE")
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_checksum_mismatch_rejected(self):
        content = b"legit"
        wrong_hash = "f" * 64
        manifest = make_manifest(files=[{"path": "file.txt", "size_bytes": len(content), "sha256": wrong_hash}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/file.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_size_mismatch_rejected(self):
        content = b"short"
        manifest = make_manifest(files=[{"path": "file.txt", "size_bytes": 100, "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/file.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_limits_max_files_exceeded(self):
        c1, c2 = b"1", b"2"
        manifest = make_manifest(
            files=[
                {"path": "f1.txt", "size_bytes": 1, "sha256": hashlib.sha256(c1).hexdigest()},
                {"path": "f2.txt", "size_bytes": 1, "sha256": hashlib.sha256(c2).hexdigest()},
            ]
        )
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f1.txt": c1,
                "corpus/f2.txt": c2,
            },
        )
        # Limit max_files to 2 (manifest + 2 files = 3 > 2)
        limits = SnapshotLimits(max_files=2)
        archive = SnapshotArchive(fake_validator, limits=limits)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_limits_max_file_bytes_exceeded(self):
        c1 = b"a" * 150
        manifest = make_manifest(files=[{"path": "large.txt", "size_bytes": 150, "sha256": hashlib.sha256(c1).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/large.txt": c1,
            },
        )
        limits = SnapshotLimits(max_file_bytes=100)
        archive = SnapshotArchive(fake_validator, limits=limits)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_limits_max_total_bytes_exceeded(self):
        c1, c2 = b"a" * 80, b"b" * 80
        manifest = make_manifest(
            files=[
                {"path": "f1.txt", "size_bytes": 80, "sha256": hashlib.sha256(c1).hexdigest()},
                {"path": "f2.txt", "size_bytes": 80, "sha256": hashlib.sha256(c2).hexdigest()},
            ]
        )
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f1.txt": c1,
                "corpus/f2.txt": c2,
            },
        )
        # Total bytes: manifest (~300 bytes) + 80 + 80 > 400
        limits = SnapshotLimits(max_total_bytes=400)
        archive = SnapshotArchive(fake_validator, limits=limits)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_unpack_refuses_existing_destination_directory(self):
        content = b"content"
        manifest = make_manifest(files=[{"path": "f.txt", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        dest = self.work_dir / "already_exists_dir"
        dest.mkdir()
        (dest / "keep_me.txt").write_text("precious")

        with self.assertRaises(FileExistsError):
            archive.unpack(self.archive_path, dest)

        # Ensure destination is untouched
        self.assertEqual((dest / "keep_me.txt").read_text(), "precious")

    def test_unpack_refuses_existing_destination_file(self):
        content = b"content"
        manifest = make_manifest(files=[{"path": "f.txt", "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        dest = self.work_dir / "already_exists_file"
        dest.write_text("existing file")

        with self.assertRaises(FileExistsError):
            archive.unpack(self.archive_path, dest)

        self.assertEqual(dest.read_text(), "existing file")

    def test_cleanup_no_destination_on_invalid_archive(self):
        # Archive has bad checksum
        content = b"valid"
        manifest = make_manifest(files=[{"path": "f.txt", "size_bytes": len(content), "sha256": "0" * 64}])
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f.txt": content,
            },
        )
        archive = SnapshotArchive(fake_validator)
        dest = self.work_dir / "should_not_exist"

        with self.assertRaises(ValueError):
            archive.unpack(self.archive_path, dest)

        self.assertFalse(dest.exists())
        # Ensure no leftover temp directories
        remaining_tmp = list(self.work_dir.glob(".tmp_unpack_*"))
        self.assertEqual(remaining_tmp, [])

    def test_explicit_directory_only_canonical_parents(self):
        content = b"data"
        h = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(files=[{"path": "runs/run-01/f.txt", "size_bytes": len(content), "sha256": h}])

        # Non-parent directory entry in tar
        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/runs/run-01/f.txt": content,
            },
            dir_entries=["corpus", "corpus/runs", "corpus/runs/run-01", "corpus/unexpected_extra_dir"],
        )
        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(self.archive_path)

    def test_unpack_checksum_valid_archive_with_unsafe_name_fails_before_destination_publication(self):
        content = b"valid-data"
        h = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(files=[{"path": "runs/run-01/f.txt", "size_bytes": len(content), "sha256": h}])

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/runs/run-01/f.txt": content,
                "corpus/../escape.txt": b"outside",
            },
        )
        archive = SnapshotArchive(fake_validator)
        dest = self.work_dir / "target_should_not_exist"
        with self.assertRaises(ValueError):
            archive.unpack(self.archive_path, dest)

        self.assertFalse(dest.exists())
        self.assertEqual(list(self.work_dir.glob(".tmp_unpack_*")), [])

    def test_unpack_archive_with_duplicate_members_fails_before_destination_publication(self):
        content = b"valid-data"
        h = hashlib.sha256(content).hexdigest()
        manifest = make_manifest(files=[{"path": "runs/run-01/f.txt", "size_bytes": len(content), "sha256": h}])

        def add_duplicate(tar):
            ti1 = tarfile.TarInfo("corpus/runs/run-01/f.txt")
            ti1.size = len(content)
            tar.addfile(ti1, io.BytesIO(content))
            ti2 = tarfile.TarInfo("corpus/runs/run-01/f.txt")
            ti2.size = len(content)
            tar.addfile(ti2, io.BytesIO(content))

        build_tar_gz(
            self.archive_path,
            file_entries={"manifest.json": json.dumps(manifest).encode("utf-8")},
            raw_members=[add_duplicate],
        )
        archive = SnapshotArchive(fake_validator)
        dest = self.work_dir / "target_duplicate_should_not_exist"
        with self.assertRaises(ValueError):
            archive.unpack(self.archive_path, dest)

        self.assertFalse(dest.exists())
        self.assertEqual(list(self.work_dir.glob(".tmp_unpack_*")), [])

    def test_archive_changed_between_passes_rejected_without_escaping_or_publishing(self):
        file1 = b"original-database"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest = make_manifest(files=[{"path": "state.sqlite", "size_bytes": len(file1), "sha256": h1}])
        manifest_bytes = json.dumps(manifest).encode("utf-8")

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": manifest_bytes,
                "corpus/state.sqlite": file1,
            },
            dir_entries=["corpus"],
        )

        escaped_target = self.work_dir / "escaped.txt"
        real_tar_open = tarfile.open
        calls = [0]

        def open_side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                build_tar_gz(
                    self.archive_path,
                    file_entries={
                        "manifest.json": manifest_bytes,
                        "../escaped.txt": b"injected-content",
                    },
                )
            return real_tar_open(*args, **kwargs)

        archive = SnapshotArchive(fake_validator)
        destination = self.work_dir / "destination"

        with unittest.mock.patch("arxiv_pipeline.snapshots.archive.tarfile.open", side_effect=open_side_effect):
            with self.assertRaises(ValueError):
                archive.unpack(self.archive_path, destination)

        self.assertFalse(escaped_target.exists(), "File escaped unpack temporary directory!")
        self.assertFalse(destination.exists(), "Destination should not be published on failure")
        self.assertEqual(list(self.work_dir.glob(".tmp_unpack_*")), [])

    def test_manifest_bytes_changed_in_pass2_rejected(self):
        file1 = b"data"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest1 = make_manifest(files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}])
        manifest2 = make_manifest(files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}], snapshot_id="snap-tampered")

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest1).encode("utf-8"),
                "corpus/f.txt": file1,
            },
            dir_entries=["corpus"],
        )

        real_tar_open = tarfile.open
        calls = [0]

        def open_side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                build_tar_gz(
                    self.archive_path,
                    file_entries={
                        "manifest.json": json.dumps(manifest2).encode("utf-8"),
                        "corpus/f.txt": file1,
                    },
                    dir_entries=["corpus"],
                )
            return real_tar_open(*args, **kwargs)

        archive = SnapshotArchive(fake_validator)
        destination = self.work_dir / "destination_tampered_manifest"

        with unittest.mock.patch("arxiv_pipeline.snapshots.archive.tarfile.open", side_effect=open_side_effect):
            with self.assertRaises(ValueError):
                archive.unpack(self.archive_path, destination)

        self.assertFalse(destination.exists())
        self.assertEqual(list(self.work_dir.glob(".tmp_unpack_*")), [])

    def test_corrupt_gzip_truncated_footer_rejected(self):
        file1 = b"some-content"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest = make_manifest(
            files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}]
        )

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f.txt": file1,
            },
            dir_entries=["corpus"],
        )

        good_bytes = self.archive_path.read_bytes()
        archive = SnapshotArchive(fake_validator)

        for cut in (4, 8):
            bad_path = self.work_dir / f"bad_cut_{cut}.tar.gz"
            bad_path.write_bytes(good_bytes[:-cut])
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                archive.verify(bad_path)

    def test_corrupt_gzip_bad_crc_rejected(self):
        file1 = b"some-content"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest = make_manifest(
            files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}]
        )

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f.txt": file1,
            },
            dir_entries=["corpus"],
        )

        good_bytes = bytearray(self.archive_path.read_bytes())
        # Corrupt CRC: the 4 bytes from -8 to -4
        good_bytes[-8] ^= 0xFF

        bad_path = self.work_dir / "bad_crc.tar.gz"
        bad_path.write_bytes(good_bytes)

        archive = SnapshotArchive(fake_validator)
        with self.assertRaises(ValueError):
            archive.verify(bad_path)

    def test_member_size_changed_in_pass2_rejected(self):
        file1 = b"original-content"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest = make_manifest(files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}])

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f.txt": file1,
            },
            dir_entries=["corpus"],
        )

        real_tar_open = tarfile.open
        calls = [0]

        def open_side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                # Replace with different size
                build_tar_gz(
                    self.archive_path,
                    file_entries={
                        "manifest.json": json.dumps(manifest).encode("utf-8"),
                        "corpus/f.txt": file1 + b"-extra",
                    },
                    dir_entries=["corpus"],
                )
            return real_tar_open(*args, **kwargs)

        archive = SnapshotArchive(fake_validator)
        destination = self.work_dir / "destination_size_changed"

        with unittest.mock.patch("arxiv_pipeline.snapshots.archive.tarfile.open", side_effect=open_side_effect):
            with self.assertRaises(ValueError):
                archive.unpack(self.archive_path, destination)

        self.assertFalse(destination.exists())
        self.assertEqual(list(self.work_dir.glob(".tmp_unpack_*")), [])

    def test_member_missing_or_extra_in_pass2_rejected(self):
        file1 = b"content1"
        file2 = b"content2"
        h1 = hashlib.sha256(file1).hexdigest()
        h2 = hashlib.sha256(file2).hexdigest()
        manifest = make_manifest(
            files=[
                {"path": "f1.txt", "size_bytes": len(file1), "sha256": h1},
                {"path": "f2.txt", "size_bytes": len(file2), "sha256": h2},
            ]
        )

        build_tar_gz(
            self.archive_path,
            file_entries={
                "manifest.json": json.dumps(manifest).encode("utf-8"),
                "corpus/f1.txt": file1,
                "corpus/f2.txt": file2,
            },
            dir_entries=["corpus"],
        )

        real_tar_open = tarfile.open
        calls = [0]

        def open_side_effect(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                # Remove f2.txt in pass 2
                build_tar_gz(
                    self.archive_path,
                    file_entries={
                        "manifest.json": json.dumps(manifest).encode("utf-8"),
                        "corpus/f1.txt": file1,
                    },
                    dir_entries=["corpus"],
                )
            return real_tar_open(*args, **kwargs)

        archive = SnapshotArchive(fake_validator)
        destination = self.work_dir / "destination_missing_pass2"

        with unittest.mock.patch("arxiv_pipeline.snapshots.archive.tarfile.open", side_effect=open_side_effect):
            with self.assertRaises(ValueError):
                archive.unpack(self.archive_path, destination)

        self.assertFalse(destination.exists())
        self.assertEqual(list(self.work_dir.glob(".tmp_unpack_*")), [])

    def test_gzip_drain_rejects_non_zero_trailing_data_after_tar_eof(self):
        file1 = b"some-content"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest = make_manifest(
            files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}]
        )
        manifest_bytes = json.dumps(manifest).encode("utf-8")

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            ti_m = tarfile.TarInfo("manifest.json")
            ti_m.size = len(manifest_bytes)
            ti_m.mode = 0o644
            tar.addfile(ti_m, io.BytesIO(manifest_bytes))

            ti_f = tarfile.TarInfo("corpus/f.txt")
            ti_f.size = len(file1)
            ti_f.mode = 0o644
            tar.addfile(ti_f, io.BytesIO(file1))

        # Append non-zero data after end of tar archive
        uncompressed = tar_buf.getvalue() + b"MALICIOUS_TRAILING_NONZERO_DATA"
        bad_path = self.work_dir / "trailing_nonzero.tar.gz"
        with gzip.open(bad_path, "wb") as gz:
            gz.write(uncompressed)

        archive = SnapshotArchive(fake_validator)
        with self.assertRaisesRegex(ValueError, "Non-zero trailing data"):
            archive.verify(bad_path)

    def test_gzip_drain_enforces_tail_bound_with_small_limits(self):
        file1 = b"some-content"
        h1 = hashlib.sha256(file1).hexdigest()
        manifest = make_manifest(
            files=[{"path": "f.txt", "size_bytes": len(file1), "sha256": h1}]
        )
        manifest_bytes = json.dumps(manifest).encode("utf-8")

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            ti_m = tarfile.TarInfo("manifest.json")
            ti_m.size = len(manifest_bytes)
            ti_m.mode = 0o644
            tar.addfile(ti_m, io.BytesIO(manifest_bytes))

            ti_f = tarfile.TarInfo("corpus/f.txt")
            ti_f.size = len(file1)
            ti_f.mode = 0o644
            tar.addfile(ti_f, io.BytesIO(file1))

        # Small limit: total tar stream (headers + files + padding) exceeds 50 + RECORDSIZE
        limits = SnapshotLimits(max_total_bytes=50)
        archive = SnapshotArchive(fake_validator, limits=limits)

        archive_path = self.work_dir / "tail_bound.tar.gz"
        with gzip.open(archive_path, "wb") as gz:
            gz.write(tar_buf.getvalue())

        with self.assertRaises(ValueError):
            archive.verify(archive_path)


if __name__ == "__main__":
    unittest.main()
