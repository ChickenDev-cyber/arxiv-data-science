from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Literal

from arxiv_pipeline.contracts import ErrorInfo, SourceFile, SourceMetrics

RETAIN_EXTENSIONS = frozenset({".tex", ".bib"})
FIGURE_EXTENSIONS = frozenset({
    ".png",
    ".pdf",
    ".jpg",
    ".jpeg",
    ".eps",
    ".ps",
    ".svg",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
})


class SourceProcessingError(Exception):
    """Extraction and validation errors for arXiv source payloads."""

    def __init__(
        self,
        code: Literal["invalid_source", "unsafe_archive", "extract_limit"],
        message: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.info: ErrorInfo = {
            "code": code,
            "message": message,
            "retryable": False,
            "http_status": None,
        }


@dataclass(frozen=True, slots=True)
class ExtractionLimits:
    max_members: int = 10_000
    max_file_bytes: int = 200 * 1024 * 1024  # Max 200Mb file size
    max_extracted_bytes: int = 1024 * 1024 * 1024  # Max 1Gb extracted bytes


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

        created_paths: list[Path] = []
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
                        f"Member count {len(members)} exceeds limit {limits.max_members}",
                    )

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
                        rel_path = safe_relative_path(member.name)
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
                        created_paths.append(dest_file)
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

                # If no .tex file is present in the archive, no files may be retained
                # Any extracted .bib files are deleted and accounted under removed_other_bytes
                if not has_tex:
                    for dest_file, entry in bib_entries:
                        dest_file.unlink(missing_ok=True)
                        retained_bytes -= entry["size_bytes"]
                        removed_other_bytes += entry["size_bytes"]
                    retained_files.clear()

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

def classify_file(path: PurePosixPath) -> Literal["retain", "figure", "other"]:
    """Classify an archive member file as 'retain', 'figure', or 'other'.

    - 'retain': .tex and .bib files (case-insensitive)
    - 'figure': raster and vector figure formats (.png, .pdf, .eps, etc.)
    - 'other': all other files (.sty, .cls, .bbl, README, etc.)
    """
    suffix = path.suffix.lower()
    if suffix in RETAIN_EXTENSIONS:
        return "retain"
    if suffix in FIGURE_EXTENSIONS:
        return "figure"
    return "other"

def safe_relative_path(name: str) -> PurePosixPath:
    """Validate and normalize a POSIX relative path for an archive member.

    Rejects absolute paths, parent directory traversals ('..'), backslashes,
    Windows drive/UNC patterns, NUL bytes, empty components, and '.' components.
    Only a single leading './' prefix is accepted and stripped as a documented exception.
    """
    if not isinstance(name, str):
        raise TypeError("Path must be a string")
    if not name or not name.strip():
        raise ValueError("Path must not be empty or whitespace only")
    if "\x00" in name:
        raise ValueError("Path must not contain NUL bytes")
    if "\\" in name:
        raise ValueError("Path must not contain backslashes")

    # Documented exception: allow a single leading './'
    if name.startswith("./"):
        raw = name[2:]
    else:
        raw = name

    if not raw or raw == ".":
        raise ValueError("Path must not resolve to empty or '.'")

    # Split into raw POSIX path components BEFORE any normalization
    parts = raw.split("/")
    for part in parts:
        if part in (".", ".."):
            raise ValueError(
                f"Path component '{part}' is invalid (empty, '.', or '..' not allowed)"
            )

    # Reject Windows drive specifiers (e.g. 'C:', 'D:')
    if len(parts[0]) >= 2 and parts[0][1] == ":":
        raise ValueError("Path must not contain Windows drive specifiers")

    path = PurePosixPath(raw)
    if path.is_absolute():
        raise ValueError("Path must not be absolute")

    return path