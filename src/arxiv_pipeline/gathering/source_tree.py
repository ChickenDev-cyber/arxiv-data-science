"""Read-only view of one extracted source directory (one paper version)."""

import os
import posixpath
from pathlib import Path

from .latex_scan import mask_comments

TEX_SUFFIX = ".tex"
BBL_SUFFIX = ".bbl"
BIB_SUFFIX = ".bib"


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class SourceTree:
    """Lists the files of source_dir and resolves \\input targets inside it.

    Paths are relative to source_dir and use "/" on every OS. Symlinks are
    skipped, and no target may resolve outside source_dir.
    """

    def __init__(self, source_dir: Path):
        self.source_dir = Path(source_dir)
        self._files = self._scan()
        self._file_set = frozenset(self._files)
        self._by_lower: dict[str, str] = {}
        for path in self._files:
            self._by_lower.setdefault(path.lower(), path)
        self._text_cache: dict[str, tuple[str, str]] = {}

    def _scan(self) -> list[str]:
        if not self.source_dir.is_dir():
            return []
        found = []
        for directory, subdirs, names in os.walk(self.source_dir, followlinks=False):
            subdirs.sort()
            for name in names:
                full = Path(directory, name)
                if full.is_symlink() or not full.is_file():
                    continue
                found.append(full.relative_to(self.source_dir).as_posix())
        return sorted(found)

    @property
    def tex_files(self) -> list[str]:
        return [p for p in self._files if p.lower().endswith(TEX_SUFFIX)]

    @property
    def bbl_files(self) -> list[str]:
        return [p for p in self._files if p.lower().endswith(BBL_SUFFIX)]

    def read(self, path: str) -> tuple[str, str]:
        """Return (text, masked_text) of a file, decoded once and cached."""
        if path not in self._text_cache:
            text = _decode((self.source_dir / path).read_bytes())
            self._text_cache[path] = (text, mask_comments(text))
        return self._text_cache[path]

    def resolve(self, target: str, base_dirs: list[str]) -> str | None:
        r"""Find the .tex file that \input{target} loads, or None.

        LaTeX resolves names from the root file's folder, so base_dirs should
        start with it; the including file's folder is a fallback that many
        Overleaf projects rely on. "intro" means "intro.tex" first, then
        "intro" as written. Exact case wins over a case-insensitive match.
        """
        return self._resolve(target, base_dirs, TEX_SUFFIX)

    def resolve_bib(self, target: str, base_dirs: list[str]) -> str | None:
        r"""Same lookup for \bibliography names: "refs" means "refs.bib"."""
        return self._resolve(target, base_dirs, BIB_SUFFIX)

    def _resolve(self, target: str, base_dirs: list[str], suffix: str) -> str | None:
        name = target.replace('"', "").strip().replace("\\", "/")
        if not name or "#" in name or name.startswith("/") or ":" in name:
            return None
        names = [name] if name.lower().endswith(suffix) else [name + suffix, name]
        for base in dict.fromkeys(base_dirs):
            for candidate in names:
                joined = posixpath.normpath(posixpath.join(base, candidate))
                if joined == ".." or joined.startswith("../"):
                    continue
                found = self._lookup(joined, suffix)
                if found is not None:
                    return found
        return None

    def _lookup(self, path: str, suffix: str) -> str | None:
        found = path if path in self._file_set else self._by_lower.get(path.lower())
        return found if found is not None and found.lower().endswith(suffix) else None
