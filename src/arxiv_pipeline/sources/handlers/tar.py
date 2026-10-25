"""TAR and TAR-GZIP source archive extraction handler."""

import gzip
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import zlib

from arxiv_pipeline.contracts import SourceFile, SourceMetrics
from arxiv_pipeline.sources.handlers.base import (
    ExtractionLimits,
    SourceProcessingError,
    classify_file,
    to_relative_path,
)


def _verify_gzip_end(source_path, members, limits):
    """tarfile can stop at TAR padding before GZIP verifies its trailer."""
    with source_path.open("rb") as source:
        if source.read(2) != b"\x1f\x8b":
            return
    # Account for actual metadata/block overhead and reserve 64 KiB for record padding.
    # The configured payload budget still bounds the full decompression pass.
    end = max((m.offset_data + ((m.size + 511) // 512) * 512 for m in members), default=0)
    overhead = max(0, end - sum(m.size for m in members))
    limit = limits.max_extracted_bytes + overhead + 65536
    try:
        with gzip.open(source_path, "rb") as source:
            total = 0
            while chunk := source.read(65536):
                total += len(chunk)
                if total > limit:
                    raise SourceProcessingError("extract_limit", "GZIP data exceeds TAR extent and padding")
    except (EOFError, gzip.BadGzipFile, zlib.error, OSError) as exc:
        raise SourceProcessingError(
            "invalid_source", f"Corrupt gzip TAR stream: {exc}", retryable=isinstance(exc, EOFError),
        ) from exc


class TarHandler:
    """Extracts only .tex and .bib members from TAR archives with exact byte accounting."""

    def extract(
        self,
        source_path: Path,
        destination: Path,
        *,
        limits: ExtractionLimits,
    ) -> tuple[list[SourceFile], SourceMetrics]:
        if not source_path.is_file():
            raise SourceProcessingError(
                "invalid_source", f"Source file does not exist: {source_path}"
            )

        try:
            tar = tarfile.open(source_path, mode="r:*")
        except (tarfile.TarError, EOFError, OSError, zlib.error) as e:
            raise SourceProcessingError(
                "invalid_source", f"Cannot open TAR archive: {e}", retryable=isinstance(e, EOFError),
            ) from e

        try:
            with tar:
                try:
                    members = tar.getmembers()
                except (tarfile.TarError, EOFError, OSError, zlib.error) as e:
                    raise SourceProcessingError(
                        "invalid_source", f"Failed to read TAR members: {e}", retryable=isinstance(e, EOFError),
                    ) from e

                if len(members) > limits.max_members:
                    raise SourceProcessingError(
                        "extract_limit",
                        f"Archive member count {len(members)} exceeds limit {limits.max_members}",
                    )

                destination.mkdir(parents=True, exist_ok=True)
                retained_files: list[SourceFile] = []
                seen_paths: set[PurePosixPath] = set()
                directory_paths: set[PurePosixPath] = set()
                bib_entries: list[tuple[Path, SourceFile]] = []
                has_tex = False

                before_bytes = 0
                retained_bytes = 0
                removed_figure_bytes = 0
                removed_other_bytes = 0

                for member in members:
                    # Directory members are container entries (e.g. '.', './', 'chapters/')
                    if member.isdir():
                        if member.name in (".", "./"):
                            continue
                        try:
                            directory = to_relative_path(member.name.rstrip("/"))
                        except ValueError as e:
                            raise SourceProcessingError(
                                "unsafe_archive", f"Unsafe directory path {member.name}: {e}",
                            ) from e
                        if directory in seen_paths or any(parent in seen_paths for parent in directory.parents):
                            raise SourceProcessingError(
                                "unsafe_archive",
                                f"File/directory path conflict: {member.name}",
                            )
                        directory_paths.add(directory)
                        directory_paths.update(directory.parents)
                        continue

                    if not member.isreg():
                        raise SourceProcessingError(
                            "unsafe_archive",
                            f"Unsupported archive member type: {member.name} ({member.type})",
                        )

                    if member.size > limits.max_file_bytes:
                        raise SourceProcessingError(
                            "extract_limit",
                            f"Member {member.name} size {member.size} exceeds file limit {limits.max_file_bytes}",
                        )

                    try:
                        rel_path = to_relative_path(member.name)
                    except ValueError as e:
                        raise SourceProcessingError(
                            "unsafe_archive", f"Unsafe member path {member.name}: {e}"
                        ) from e

                    if rel_path in seen_paths:
                        raise SourceProcessingError(
                            "unsafe_archive",
                            f"Duplicate member path detected: {rel_path}",
                        )
                    if rel_path in directory_paths or any(parent in seen_paths for parent in rel_path.parents):
                        raise SourceProcessingError(
                            "unsafe_archive", f"File/directory path conflict: {rel_path}",
                        )
                    seen_paths.add(rel_path)
                    directory_paths.update(rel_path.parents)

                    before_bytes += member.size
                    if before_bytes > limits.max_extracted_bytes:
                        raise SourceProcessingError(
                            "extract_limit",
                            f"Cumulative extracted bytes {before_bytes} exceeds limit {limits.max_extracted_bytes}",
                        )

                    classification = classify_file(rel_path)
                    if classification == "retain":
                        is_tex = rel_path.suffix.lower() == ".tex"
                        if is_tex:
                            has_tex = True

                        dest_file = destination / rel_path
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                        f_in = tar.extractfile(member)
                        if f_in is None:
                            raise SourceProcessingError(
                                "invalid_source",
                                f"Could not read content of {member.name}",
                            )
                        with open(dest_file, "wb") as f_out:
                            written = 0
                            while chunk := f_in.read(65536):
                                written += len(chunk)
                                f_out.write(chunk)
                        if written != member.size:
                            raise SourceProcessingError(
                                "invalid_source",
                                f"Size mismatch for {member.name}: read {written}, expected {member.size}",
                            )
                        retained_bytes += written
                        file_entry: SourceFile = {
                            "path": str(rel_path),
                            "size_bytes": written,
                        }
                        retained_files.append(file_entry)
                        if not is_tex:
                            bib_entries.append((dest_file, file_entry))
                    elif classification == "figure":
                        removed_figure_bytes += member.size
                    else:
                        removed_other_bytes += member.size

                # If no .tex file is present in the archive, no files may be retained.
                # Any extracted .bib files are deleted, accounted under removed_other_bytes,
                # and empty destination directories are purged. SourceExtractor will
                # explicitly classify this result as status='unsupported'.
                if not has_tex:
                    for dest_file, entry in bib_entries:
                        dest_file.unlink(missing_ok=True)
                        retained_bytes -= entry["size_bytes"]
                        removed_other_bytes += entry["size_bytes"]
                    retained_files.clear()
                    if destination.exists():
                        for item in list(destination.iterdir()):
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)

            _verify_gzip_end(source_path, members, limits)
            metrics: SourceMetrics = {
                "download_bytes": None,
                "extracted_bytes_before_filter": before_bytes,
                "retained_source_bytes": retained_bytes,
                "removed_figure_bytes": removed_figure_bytes,
                "removed_other_bytes": removed_other_bytes,
            }
            return retained_files, metrics

        except Exception as exc:
            # Rollback: Clean up any files/directories created during this extraction
            if destination.exists():
                for item in destination.iterdir():
                    if item.is_file() or item.is_symlink():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
            if isinstance(exc, (tarfile.TarError, EOFError, gzip.BadGzipFile, zlib.error)):
                raise SourceProcessingError(
                    "invalid_source", f"Corrupt TAR content: {exc}", retryable=isinstance(exc, EOFError),
                ) from exc
            raise
