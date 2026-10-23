"""Extraction and validation policies for arXiv source archives."""

from pathlib import PurePosixPath
from typing import Literal

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
        if part in ("", ".", ".."):
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
