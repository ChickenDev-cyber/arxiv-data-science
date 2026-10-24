"""Base classes, protocols, types, and validation helpers for source handlers."""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Literal, NamedTuple, Protocol

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


class SourceHandler(Protocol):
    """Shared extraction protocol implemented by all format handlers."""

    def extract(
        self,
        source_path: Path,
        destination: Path,
        *,
        limits: ExtractionLimits,
    ) -> tuple[list[SourceFile], SourceMetrics]:
        ...


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
    """Accept text sources, excluding recognizable HTML document responses.

    Do not require a TeX command in the prefix: submissions can start with
    long comments, and parsing TeX syntax belongs to Milestone 2.
    """
    if not is_text_data(data):
        return False
    prefix = data.removeprefix(b"\xef\xbb\xbf").lstrip()
    if re.match(rb"<\?xml\b", prefix, re.IGNORECASE):
        end = prefix.find(b"?>")
        if end >= 0:
            prefix = prefix[end + 2:].lstrip()
    # Consume comments once, without a repeated wildcard regex that can backtrack.
    while prefix.startswith(b"<!--"):
        end = prefix.find(b"-->")
        if end < 0:
            break
        prefix = prefix[end + 3:].lstrip()
    html = re.match(
        rb"<(?:!doctype\s+html\b|html\b|head\b|body\b)",
        prefix, re.IGNORECASE,
    )
    return html is None
