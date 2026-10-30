"""Archive verification and unpacking for snapshots."""
import gzip
import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

from .common import ManifestValidator, SnapshotLimits, check_manifest


def _check_name_security(name: str) -> None:
    if not name or not isinstance(name, str):
        raise ValueError("Archive member name must be a non-empty string")
    if "\\" in name:
        raise ValueError(f"Archive member name contains backslash: {name!r}")
    if name.startswith("/") or name.startswith("\\"):
        raise ValueError(f"Archive member has absolute path: {name!r}")
    if re.match(r"^[a-zA-Z]:", name):
        raise ValueError(f"Archive member contains Windows drive prefix: {name!r}")
    if "\0" in name:
        raise ValueError(f"Archive member contains null byte: {name!r}")

    stripped = name.rstrip("/")
    if not stripped:
        raise ValueError(f"Invalid archive member name: {name!r}")

    segments = stripped.split("/")
    for segment in segments:
        if segment in ("..", ".", ""):
            raise ValueError(f"Invalid path segment {segment!r} in member {name!r}")

    normalized = posixpath.normpath(stripped)
    if normalized != stripped:
        raise ValueError(f"Non-canonical path in member {name!r}")


class SnapshotArchive:
    def __init__(self, validator: ManifestValidator, *, limits: SnapshotLimits | None = None):
        self.validator = validator
        self.limits = limits if limits is not None else SnapshotLimits()

    def verify(self, archive: Path) -> dict:
        return self._process_archive(Path(archive), destination=None)

    def unpack(self, archive: Path, destination: Path) -> dict:
        return self._process_archive(Path(archive), destination=Path(destination))

    def _process_archive(self, archive: Path, destination: Path | None = None) -> dict:
        if not archive.is_file():
            raise ValueError(f"Archive does not exist or is not a file: {archive}")

        dest_path: Path | None = None
        if destination is not None:
            dest_path = Path(destination)
            if dest_path.exists() or dest_path.is_symlink():
                raise FileExistsError(f"Destination already exists: {dest_path}")

        try:
            tar = tarfile.open(archive, "r:gz")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise ValueError(f"Corrupt or invalid archive: {exc}") from exc

        try:
            # Pass 1: Member inspection, security validation, manifest verification
            expected_members: dict[str, tuple[str, int]] = {}
            seen_names: set[str] = set()
            manifest_member: tarfile.TarInfo | None = None
            regular_file_count = 0

            for member in tar:
                _check_name_security(member.name)
                norm_name = member.name.rstrip("/")
                if norm_name in seen_names:
                    raise ValueError(f"Duplicate member in archive: {member.name}")
                seen_names.add(norm_name)

                if not (member.isreg() or member.isdir()):
                    raise ValueError(
                        f"Unsupported member type (links/special entries forbidden): {member.name}"
                    )

                mtype = "dir" if member.isdir() else "reg"
                expected_members[norm_name] = (mtype, member.size)

                if member.isreg():
                    regular_file_count += 1
                    if regular_file_count > self.limits.max_files:
                        raise ValueError(
                            f"Archive exceeds maximum file limit ({self.limits.max_files})"
                        )
                    if member.size > self.limits.max_file_bytes:
                        raise ValueError(
                            f"Member {member.name} header size ({member.size}) exceeds limit ({self.limits.max_file_bytes})"
                        )
                    if norm_name == "manifest.json":
                        manifest_member = member

            if manifest_member is None:
                raise ValueError("Archive is missing manifest.json")

            manifest_file = tar.extractfile(manifest_member)
            if manifest_file is None:
                raise ValueError("Could not extract manifest.json from archive")
            manifest_raw = manifest_file.read()
            manifest_raw_hash = hashlib.sha256(manifest_raw).hexdigest()
            manifest_raw_len = len(manifest_raw)

            if manifest_raw_len > self.limits.max_file_bytes:
                raise ValueError("manifest.json exceeds max_file_bytes limit")

            try:
                manifest_dict = json.loads(manifest_raw.decode("utf-8"))
            except Exception as exc:
                raise ValueError(f"manifest.json is not valid UTF-8 JSON: {exc}") from exc

            manifest = check_manifest(manifest_dict, self.validator)

            declared_files: dict[str, dict] = {}
            for fentry in manifest.get("files", []):
                fpath = fentry.get("path")
                if not isinstance(fpath, str):
                    raise ValueError(f"Invalid path in manifest files: {fpath!r}")
                _check_name_security(fpath)
                if fpath in declared_files:
                    raise ValueError(f"Duplicate file path declared in manifest: {fpath}")
                declared_files[fpath] = fentry

            allowed_dirs = {"corpus"}
            for fpath in declared_files:
                parts = fpath.split("/")
                for i in range(1, len(parts)):
                    allowed_dirs.add("corpus/" + "/".join(parts[:i]))

            corpus_files_seen: set[str] = set()
            total_declared_bytes = manifest_raw_len

            for norm_name, (mtype, msize) in expected_members.items():
                if mtype == "dir":
                    if norm_name not in allowed_dirs:
                        raise ValueError(
                            f"Directory {norm_name!r} is not a canonical parent directory of declared corpus files"
                        )
                elif mtype == "reg":
                    if norm_name == "manifest.json":
                        continue
                    if not norm_name.startswith("corpus/"):
                        raise ValueError(
                            f"Archive member {norm_name!r} is outside corpus/ and not manifest.json"
                        )
                    corpus_rel = norm_name[len("corpus/"):]
                    if corpus_rel not in declared_files:
                        raise ValueError(f"Unrecognized file in archive not in manifest: {norm_name}")
                    declared_info = declared_files[corpus_rel]
                    if msize != declared_info["size_bytes"]:
                        raise ValueError(
                            f"TarInfo size {msize} does not match manifest size {declared_info['size_bytes']} for {norm_name}"
                        )
                    total_declared_bytes += declared_info["size_bytes"]
                    corpus_files_seen.add(corpus_rel)

            missing_files = set(declared_files.keys()) - corpus_files_seen
            if missing_files:
                raise ValueError(f"Archive is missing declared corpus files: {sorted(missing_files)}")

            if total_declared_bytes > self.limits.max_total_bytes:
                raise ValueError(
                    f"Total uncompressed bytes ({total_declared_bytes}) exceeds limit ({self.limits.max_total_bytes})"
                )

        finally:
            tar.close()

        # Pass 2: Stream content, verify actual bytes and SHA-256 hashes, unpack if requested
        temp_dir_path: Path | None = None
        if dest_path is not None:
            if dest_path.exists() or dest_path.is_symlink():
                raise FileExistsError(f"Destination already exists: {dest_path}")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            temp_dir = tempfile.mkdtemp(prefix=f".tmp_unpack_{dest_path.name}_", dir=dest_path.parent)
            temp_dir_path = Path(temp_dir)

        try:
            tar = tarfile.open(archive, "r:gz")
        except (tarfile.TarError, OSError, EOFError) as exc:
            if temp_dir_path and temp_dir_path.exists():
                shutil.rmtree(temp_dir_path, ignore_errors=True)
            raise ValueError(f"Corrupt or invalid archive: {exc}") from exc

        try:
            pass2_seen_names: set[str] = set()
            total_streamed_bytes = 0

            for member in tar:
                # 1. Immediate security validation on member header before touching disk
                _check_name_security(member.name)
                norm_name = member.name.rstrip("/")

                # 2. Verify member matches exact expected inventory from pass 1
                if norm_name not in expected_members:
                    raise ValueError(
                        f"Archive member set changed between passes: unexpected member {member.name!r}"
                    )
                if norm_name in pass2_seen_names:
                    raise ValueError(f"Duplicate archive member in pass 2: {member.name!r}")
                pass2_seen_names.add(norm_name)

                exp_type, exp_size = expected_members[norm_name]
                curr_type = "dir" if member.isdir() else ("reg" if member.isreg() else "other")
                if curr_type != exp_type:
                    raise ValueError(f"Archive member {member.name!r} type changed between passes")
                if member.size != exp_size:
                    raise ValueError(f"Archive member {member.name!r} size changed between passes")

                if member.isdir():
                    if temp_dir_path:
                        target_dir = temp_dir_path / norm_name
                        try:
                            target_dir.resolve().relative_to(temp_dir_path.resolve())
                        except ValueError:
                            raise ValueError(f"Path traversal detected in directory: {member.name!r}")
                        target_dir.mkdir(parents=True, exist_ok=True)
                    continue

                if not member.isreg():
                    raise ValueError(f"Forbidden member type in pass 2: {member.name!r}")

                f = tar.extractfile(member)
                if f is None:
                    raise ValueError(f"Could not read member: {member.name}")

                hasher = hashlib.sha256()
                bytes_read = 0

                out_file = None
                if temp_dir_path:
                    target_file = temp_dir_path / norm_name
                    try:
                        target_file.resolve().relative_to(temp_dir_path.resolve())
                    except ValueError:
                        raise ValueError(f"Path traversal detected in file: {member.name!r}")
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    out_file = open(target_file, "wb")

                try:
                    while True:
                        chunk = f.read(64 * 1024)
                        if not chunk:
                            break
                        bytes_read += len(chunk)
                        total_streamed_bytes += len(chunk)
                        if bytes_read > self.limits.max_file_bytes:
                            raise ValueError(
                                f"Member {member.name} exceeded max_file_bytes during extraction"
                            )
                        if total_streamed_bytes > self.limits.max_total_bytes:
                            raise ValueError(
                                f"Archive exceeded max_total_bytes during extraction"
                            )
                        hasher.update(chunk)
                        if out_file:
                            out_file.write(chunk)
                finally:
                    if out_file:
                        out_file.close()

                if norm_name == "manifest.json":
                    if bytes_read != manifest_raw_len or hasher.hexdigest() != manifest_raw_hash:
                        raise ValueError("manifest.json content changed between pass 1 and pass 2")
                else:
                    corpus_rel = norm_name[len("corpus/"):]
                    expected = declared_files[corpus_rel]
                    if bytes_read != expected["size_bytes"]:
                        raise ValueError(
                            f"File {member.name} actual bytes ({bytes_read}) != declared ({expected['size_bytes']})"
                        )
                    if hasher.hexdigest().lower() != expected["sha256"].lower():
                        raise ValueError(
                            f"File {member.name} sha256 ({hasher.hexdigest()}) != declared ({expected['sha256']})"
                        )

            if pass2_seen_names != set(expected_members.keys()):
                raise ValueError("Archive member set changed between passes (missing members)")

            # Drain underlying gzip stream to EOF to force verification of the gzip trailer (CRC-32 and ISIZE)
            # without loading entire payload into RAM, and enforce tail bounds and zero padding.
            if hasattr(tar, "fileobj") and tar.fileobj:
                max_allowed_total = self.limits.max_total_bytes + tarfile.RECORDSIZE
                try:
                    while True:
                        tail = tar.fileobj.read(64 * 1024)
                        if not tail:
                            break
                        if any(b != 0 for b in tail):
                            raise ValueError("Non-zero trailing data found after end of tar archive")
                        total_streamed_bytes += len(tail)
                        if total_streamed_bytes > max_allowed_total:
                            raise ValueError("Archive uncompressed size exceeded limit")
                except (gzip.BadGzipFile, EOFError, OSError) as exc:
                    raise ValueError(
                        f"Corrupt gzip archive (trailer CRC/ISIZE check failed): {exc}"
                    ) from exc

            # Atomic publication if destination was requested
            if temp_dir_path and dest_path:
                if dest_path.exists() or dest_path.is_symlink():
                    raise FileExistsError(f"Destination already exists: {dest_path}")
                os.replace(temp_dir_path, dest_path)
                temp_dir_path = None  # Replaced successfully

        except Exception:
            if temp_dir_path and temp_dir_path.exists():
                shutil.rmtree(temp_dir_path, ignore_errors=True)
            raise
        finally:
            tar.close()

        return manifest
