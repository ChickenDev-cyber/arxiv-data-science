"""Corpus export to verified snapshot tar.gz archives."""
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
import time
from typing import Callable

from ..contracts import Owner
from ..pipeline_contracts import RunReader, SnapshotManifest
from ..validation.artifacts import safe_path
from .common import ManifestValidator, SnapshotLimits, check_manifest, utc_now


class StreamVerifier(io.RawIOBase):
    """Wraps an open file to stream bytes, compute sha256 and enforce size limits."""

    def __init__(self, raw_file, expected_size: int, expected_sha256: str, max_file_bytes: int):
        self._raw_file = raw_file
        self._expected_size = expected_size
        self._expected_sha256 = expected_sha256
        self._max_file_bytes = max_file_bytes
        self._bytes_read = 0
        self._hasher = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        chunk = self._raw_file.read(size)
        if chunk:
            self._bytes_read += len(chunk)
            if self._bytes_read > self._max_file_bytes:
                raise ValueError(f"File exceeded max_file_bytes limit of {self._max_file_bytes}")
            self._hasher.update(chunk)
        return chunk

    def finish_and_verify(self) -> None:
        extra = self._raw_file.read(1)
        if extra:
            raise ValueError("File grew during export")
        if self._bytes_read != self._expected_size:
            raise ValueError(f"File size mismatch: expected {self._expected_size}, read {self._bytes_read}")
        actual_sha256 = self._hasher.hexdigest()
        if actual_sha256 != self._expected_sha256:
            raise ValueError(f"File hash mismatch: expected {self._expected_sha256}, got {actual_sha256}")


class SnapshotExporter:
    """Exports a stopped verified corpus into a portable tar.gz snapshot."""

    def __init__(self, reader: RunReader, validator: ManifestValidator, *,
                 limits: SnapshotLimits | None = None,
                 clock: Callable[[], str] = utc_now):
        self.reader = reader
        self.validator = validator
        self.limits = limits if limits is not None else SnapshotLimits()
        self.clock = clock

    def export(self, output_root: Path, run_id: str, destination: Path, *,
               snapshot_id: str, exporter_commit: str) -> Path:
        output_root = Path(output_root)
        destination = Path(destination)

        if not isinstance(snapshot_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", snapshot_id):
            raise ValueError(f"Invalid snapshot_id: {snapshot_id!r}")
        if not isinstance(exporter_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", exporter_commit):
            raise ValueError(f"Invalid exporter_commit; must be 40 lowercase hex characters: {exporter_commit!r}")
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError(f"Invalid run_id: {run_id!r}")

        # The reader context is held through inventory copy, hashing, and publication
        with self.reader.open(output_root, run_id) as run:
            dest_resolved = destination.resolve()
            root_resolved = output_root.resolve()

            try:
                dest_resolved.relative_to(root_resolved)
                is_inside = True
            except ValueError:
                is_inside = False

            if is_inside or dest_resolved == root_resolved:
                raise ValueError(f"Destination archive cannot be inside output root: {destination}")

            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"Destination archive already exists: {destination}")

            if run.run_state not in ("paused", "finished"):
                raise ValueError(f"Run state must be paused or finished, got {run.run_state!r}")

            if not run.validation.get("valid", False):
                raise ValueError("Run validation report is not valid")

            if run.scope.get("run_id") != run_id:
                raise ValueError(f"Run ID mismatch: expected {run_id!r}, got {run.scope.get('run_id')!r}")

            if not isinstance(run.config_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", run.config_fingerprint):
                raise ValueError(f"Invalid config_fingerprint in verified run: {run.config_fingerprint!r}")

            # Collect owners from ranges configuration, preserving order without duplicates
            ranges = run.scope.get("scope", {}).get("ranges", [])
            owners: list[Owner] = []
            for r in ranges:
                o = r.get("owner")
                if o and o not in owners:
                    owners.append(o)
            if not owners:
                for p in run.scope.get("scope", {}).get("paper_tasks", []):
                    o = p.get("owner")
                    if o and o not in owners:
                        owners.append(o)
            if not owners:
                for res in run.accepted_results:
                    o = res.get("owner")
                    if o and o not in owners:
                        owners.append(o)
            if not owners:
                raise ValueError("Cannot determine owners for snapshot: ranges configuration is empty")

            # Collect producer commits and count missing provenance
            producer_commits: list[str] = []
            unknown_provenance_tasks = 0
            for res in run.accepted_results:
                prov = res.get("provenance") or {}
                commit = prov.get("producer_commit")
                if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
                    if commit not in producer_commits:
                        producer_commits.append(commit)
                else:
                    unknown_provenance_tasks += 1

            created_at = self.clock()
            manifest_dict: SnapshotManifest = {
                "format_version": 1,
                "kind": "arxiv-snapshot",
                "snapshot_id": snapshot_id,
                "schema_version": "1.2",
                "run_id": run_id,
                "is_mock": bool(run.scope.get("is_mock", False)),
                "owners": owners,
                "created_at": created_at,
                "exporter_commit": exporter_commit,
                "producer_commits": producer_commits,
                "unknown_provenance_tasks": unknown_provenance_tasks,
                "config_fingerprint": run.config_fingerprint,
                "origin_output_root": str(run.recorded_output_root),
                "files": list(run.files),
            }

            validated_manifest = check_manifest(manifest_dict, self.validator)

            # File count limit includes manifest.json
            total_file_count = 1 + len(run.files)
            if total_file_count > self.limits.max_files:
                raise ValueError(f"Exceeded max_files limit: {total_file_count} > {self.limits.max_files}")

            manifest_bytes = json.dumps(validated_manifest, ensure_ascii=False, indent=2).encode("utf-8")
            if len(manifest_bytes) > self.limits.max_file_bytes:
                raise ValueError(f"manifest.json size ({len(manifest_bytes)}) exceeds max_file_bytes ({self.limits.max_file_bytes})")

            total_uncompressed_bytes = len(manifest_bytes)
            seen_paths: set[str] = set()
            for f in run.files:
                p = f["path"]
                if p in seen_paths:
                    raise ValueError(f"Duplicate path in file inventory: {p}")
                seen_paths.add(p)

                # Validate path safety and ensure no ancestor or leaf is a symlink
                safe_path(output_root, p)

                if f["size_bytes"] > self.limits.max_file_bytes:
                    raise ValueError(f"File {p} size {f['size_bytes']} exceeds max_file_bytes ({self.limits.max_file_bytes})")

                total_uncompressed_bytes += f["size_bytes"]
                if total_uncompressed_bytes > self.limits.max_total_bytes:
                    raise ValueError(f"Total size {total_uncompressed_bytes} exceeds max_total_bytes ({self.limits.max_total_bytes})")

            destination.parent.mkdir(parents=True, exist_ok=True)
            temp_fd, temp_path_str = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".tmp_{destination.name}_",
                suffix=".tar.gz"
            )
            os.close(temp_fd)
            temp_path = Path(temp_path_str)

            try:
                with tarfile.open(temp_path, "w:gz") as tar:
                    manifest_info = tarfile.TarInfo(name="manifest.json")
                    manifest_info.size = len(manifest_bytes)
                    try:
                        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                        manifest_info.mtime = int(dt.timestamp())
                    except Exception:
                        manifest_info.mtime = int(time.time())
                    manifest_info.mode = 0o644
                    manifest_info.type = tarfile.REGTYPE
                    tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

                    for f in run.files:
                        rel_path = f["path"]
                        source_path = safe_path(output_root, rel_path)
                        if not source_path.is_file():
                            raise ValueError(f"Inventory path is not a regular file: {rel_path}")

                        st = source_path.stat()
                        if st.st_size != f["size_bytes"]:
                            raise ValueError(f"File size mismatch before reading for {rel_path}: expected {f['size_bytes']}, got {st.st_size}")

                        member_info = tarfile.TarInfo(name=f"corpus/{rel_path}")
                        member_info.size = f["size_bytes"]
                        member_info.mtime = int(st.st_mtime)
                        member_info.mode = 0o644
                        member_info.type = tarfile.REGTYPE

                        with open(source_path, "rb") as raw_f:
                            verifier = StreamVerifier(
                                raw_f,
                                expected_size=f["size_bytes"],
                                expected_sha256=f["sha256"],
                                max_file_bytes=self.limits.max_file_bytes
                            )
                            tar.addfile(member_info, verifier)
                            verifier.finish_and_verify()

                if destination.exists() or destination.is_symlink():
                    raise FileExistsError(f"Destination archive already exists: {destination}")

                os.link(temp_path, destination)
                temp_path.unlink(missing_ok=True)

            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        return destination
