"""TAR and TAR-GZIP source archive extraction handler."""

from pathlib import Path, PurePosixPath
import shutil
import tarfile

from arxiv_pipeline.contracts import SourceFile, SourceMetrics
from arxiv_pipeline.sources.handlers.base import (
    ExtractionLimits,
    SourceProcessingError,
    classify_file,
    to_relative_path,
)


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
        except (tarfile.TarError, EOFError, OSError) as e:
            raise SourceProcessingError(
                "invalid_source", f"Cannot open TAR archive: {e}"
            ) from e

        try:
            with tar:
                try:
                    members = tar.getmembers()
                except (tarfile.TarError, EOFError, OSError) as e:
                    raise SourceProcessingError(
                        "invalid_source", f"Failed to read TAR members: {e}"
                    ) from e

                if len(members) > limits.max_members:
                    raise SourceProcessingError(
                        "extract_limit",
                        f"Archive member count {len(members)} exceeds limit {limits.max_members}",
                    )

                destination.mkdir(parents=True, exist_ok=True)
                retained_files: list[SourceFile] = []
                seen_paths: set[PurePosixPath] = set()
                bib_entries: list[tuple[Path, SourceFile]] = []
                has_tex = False

                before_bytes = 0
                retained_bytes = 0
                removed_figure_bytes = 0
                removed_other_bytes = 0

                for member in members:
                    # Directory members are container entries (e.g. '.', './', 'chapters/')
                    if member.isdir():
                        norm_name = member.name.replace("\\", "/")
                        if norm_name.startswith("/") or ".." in norm_name.split("/"):
                            raise SourceProcessingError(
                                "unsafe_archive",
                                f"Unsafe directory traversal: {member.name}",
                            )
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
                    seen_paths.add(rel_path)

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

            metrics: SourceMetrics = {
                "download_bytes": None,
                "extracted_bytes_before_filter": before_bytes,
                "retained_source_bytes": retained_bytes,
                "removed_figure_bytes": removed_figure_bytes,
                "removed_other_bytes": removed_other_bytes,
            }
            return retained_files, metrics

        except Exception:
            # Rollback: Clean up any files/directories created during this extraction
            if destination.exists():
                for item in destination.iterdir():
                    if item.is_file() or item.is_symlink():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
            raise
