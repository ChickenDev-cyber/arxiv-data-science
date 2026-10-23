from dataclasses import dataclass
import gzip
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Literal, NamedTuple
import zlib

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


class ExtractionResult(NamedTuple):
    """Result of payload extraction and classification by SourceExtractor."""
    status: Literal["success", "unsupported"]
    source_kind: str
    files: list[SourceFile]
    metrics: SourceMetrics
    reason: str | None = None

    @property
    def kind(self) -> str:
        """Backwards compatibility alias for source_kind."""
        return self.source_kind


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

def to_relative_path(name: str) -> PurePosixPath:
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


safe_relative_path = to_relative_path

def is_text_data(data: bytes) -> bool:
    """Check if byte snippet represents plain text source rather than binary data.

    T3 distinguishes package container formats and preserves source files.
    Full TeX syntax parsing/validation belongs to Milestone 2.
    A payload or sample is recognized as text source if it is non-empty, contains
    no NUL bytes, and decodes cleanly as UTF-8 or standard 8-bit text.
    """
    if not data or b"\x00" in data:
        return False

    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass

    try:
        text = data.decode("latin-1")
        # Ensure control characters (excluding \t, \n, \r, \f) are negligible (<2%)
        control_chars = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r\f")
        return (control_chars / len(text)) < 0.02
    except Exception:
        return False


def is_tex_content(data: bytes) -> bool:
    """Backwards-compatibility alias for is_text_data."""
    return is_text_data(data)


def detect_source_kind(source_path: Path) -> str:
    """Detect arXiv source package format by inspecting magic bytes and structure."""
    if not source_path.is_file():
        raise SourceProcessingError(
            "invalid_source", f"Source file does not exist: {source_path}"
        )

    try:
        with open(source_path, "rb") as f:
            header = f.read(1024)
    except OSError as e:
        raise SourceProcessingError(
            "invalid_source", f"Cannot read source file: {e}"
        ) from e

    if not header:
        raise SourceProcessingError("invalid_source", "Source file is empty")

    if header.startswith(b"%PDF-"):
        return "pdf_only"

    if header.startswith(b"\x1f\x8b"):
        # gzip compressed: could be tar_gzip, gzip_tex, or invalid/other
        try:
            if tarfile.is_tarfile(source_path):
                return "tar_gzip"
        except (tarfile.TarError, EOFError, OSError):
            pass

        try:
            with gzip.open(source_path, "rb") as gz:
                sample = gz.read(4096)
                if is_text_data(sample):
                    return "gzip_tex"
                return "other"
        except (gzip.BadGzipFile, zlib.error, EOFError, OSError):
            return "other"

    # Uncompressed TAR check
    try:
        if tarfile.is_tarfile(source_path):
            return "tar"
    except (tarfile.TarError, EOFError, OSError):
        pass

    # Direct uncompressed text source check
    if is_text_data(header):
        return "tex"

    return "other"


class GzipTexHandler:
    """Extracts a single TeX source wrapped in a GZIP container as main.tex."""

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

        destination.mkdir(parents=True, exist_ok=True)
        dest_file = destination / "main.tex"

        try:
            with gzip.open(source_path, "rb") as gz, open(dest_file, "wb") as f_out:
                written = 0
                sample = b""
                while chunk := gz.read(65536):
                    if len(sample) < 4096:
                        sample += chunk[: 4096 - len(sample)]
                    written += len(chunk)
                    if written > limits.max_file_bytes:
                        raise SourceProcessingError(
                            "extract_limit",
                            f"Decompressed TeX size {written} exceeds limit {limits.max_file_bytes}",
                        )
                    if written > limits.max_extracted_bytes:
                        raise SourceProcessingError(
                            "extract_limit",
                            f"Cumulative extracted bytes {written} exceeds limit {limits.max_extracted_bytes}",
                        )
                    f_out.write(chunk)

            if not is_text_data(sample):
                raise SourceProcessingError(
                    "invalid_source", "Decompressed gzip content is not text source"
                )

        except SourceProcessingError:
            dest_file.unlink(missing_ok=True)
            if destination.exists():
                for item in destination.iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
            raise
        except (gzip.BadGzipFile, zlib.error, EOFError, OSError) as e:
            dest_file.unlink(missing_ok=True)
            if destination.exists():
                for item in destination.iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
            raise SourceProcessingError("invalid_source", f"Corrupt gzip source: {e}") from e

        retained_files: list[SourceFile] = [
            {"path": "main.tex", "size_bytes": written}
        ]
        metrics: SourceMetrics = {
            "download_bytes": None,
            "extracted_bytes_before_filter": written,
            "retained_source_bytes": written,
            "removed_figure_bytes": 0,
            "removed_other_bytes": 0,
        }
        return retained_files, metrics


class TexHandler:
    """Extracts a direct uncompressed TeX source file as main.tex."""

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

        file_size = source_path.stat().st_size
        if file_size > limits.max_file_bytes:
            raise SourceProcessingError(
                "extract_limit",
                f"TeX file size {file_size} exceeds limit {limits.max_file_bytes}",
            )
        if file_size > limits.max_extracted_bytes:
            raise SourceProcessingError(
                "extract_limit",
                f"TeX file size {file_size} exceeds limit {limits.max_extracted_bytes}",
            )

        with open(source_path, "rb") as f_in:
            sample = f_in.read(4096)
        if not is_text_data(sample):
            raise SourceProcessingError(
                "invalid_source", "Source file content is not text source"
            )

        destination.mkdir(parents=True, exist_ok=True)
        dest_file = destination / "main.tex"

        try:
            with open(source_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        except Exception:
            dest_file.unlink(missing_ok=True)
            raise

        retained_files: list[SourceFile] = [
            {"path": "main.tex", "size_bytes": file_size}
        ]
        metrics: SourceMetrics = {
            "download_bytes": None,
            "extracted_bytes_before_filter": file_size,
            "retained_source_bytes": file_size,
            "removed_figure_bytes": 0,
            "removed_other_bytes": 0,
        }
        return retained_files, metrics


class SourceExtractor:
    """Dispatches extraction based on detected arXiv source kind."""

    def __init__(self) -> None:
        tar_handler = TarHandler()
        self._handlers = {
            "tar_gzip": tar_handler,
            "tar": tar_handler,
            "gzip_tex": GzipTexHandler(),
            "tex": TexHandler(),
        }

    def extract(
        self,
        source_path: Path,
        destination: Path,
        *,
        limits: ExtractionLimits,
    ) -> ExtractionResult:
        kind = detect_source_kind(source_path)

        if kind == "pdf_only":
            metrics: SourceMetrics = {
                "download_bytes": None,
                "extracted_bytes_before_filter": None,
                "retained_source_bytes": 0,
                "removed_figure_bytes": None,
                "removed_other_bytes": None,
            }
            return ExtractionResult(
                status="unsupported",
                source_kind="pdf_only",
                files=[],
                metrics=metrics,
                reason="PDF-only payload without TeX source",
            )

        handler = self._handlers.get(kind)
        if handler is None:
            raise SourceProcessingError(
                "invalid_source", f"Unsupported or unknown source kind: {kind}"
            )

        files, metrics = handler.extract(source_path, destination, limits=limits)
        if not files:
            return ExtractionResult(
                status="unsupported",
                source_kind=kind,
                files=[],
                metrics=metrics,
                reason="Archive contains no .tex source files",
            )

        return ExtractionResult(
            status="success",
            source_kind=kind,
            files=files,
            metrics=metrics,
            reason=None,
        )