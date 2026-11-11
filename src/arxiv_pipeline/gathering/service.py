"""Module 1 entry point: turn one source directory into a GatheredSource."""

from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path

from ..identifiers import is_valid_arxiv_id
from .bib_collector import BibCollector
from .contracts import GatheredSource
from .expander import DEFAULT_MAX_DEPTH, IncludeExpander
from .root_finder import RootCandidate, RootFinder
from .source_tree import SourceTree


@dataclass
class GatherReport:
    """GatheredSource plus diagnostics that are not part of the contract."""

    source: GatheredSource
    root_candidates: list[RootCandidate] = field(default_factory=list)
    missing_includes: list[str] = field(default_factory=list)
    cycles: list[str] = field(default_factory=list)
    missing_bib: list[str] = field(default_factory=list)


def validate_request(source_dir: object, arxiv_id: object, version: object) -> None:
    if not isinstance(source_dir, (str, PathLike)):
        raise ValueError("source_dir must be a path")
    if Path(source_dir).exists() and not Path(source_dir).is_dir():
        raise ValueError(f"source_dir is not a directory: {source_dir}")
    if not is_valid_arxiv_id(arxiv_id):
        raise ValueError(f"invalid arXiv ID: {arxiv_id!r}")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"version must be an int >= 1, got {version!r}")


class SourceGatherer:
    """Find the root file, follow \\input/\\include, merge the text.

    Invalid arguments raise ValueError. A missing or .tex-less directory
    (PDF-only versions) gives status "empty_source"; .tex files without any
    \\documentclass give "no_root_file". Both have merged_tex "" and empty
    compilation_path and bib_files.
    """

    def __init__(self, root_finder: RootFinder | None = None, *,
                 max_depth: int = DEFAULT_MAX_DEPTH):
        self.root_finder = root_finder or RootFinder()
        self.max_depth = max_depth

    def gather(self, source_dir: str | PathLike, arxiv_id: str, version: int) -> GatheredSource:
        return self.inspect(source_dir, arxiv_id, version).source

    def inspect(self, source_dir: str | PathLike, arxiv_id: str, version: int) -> GatherReport:
        validate_request(source_dir, arxiv_id, version)
        tree = SourceTree(Path(source_dir))
        bbl_files = tree.bbl_files
        if not tree.tex_files:
            return GatherReport(self._source(arxiv_id, version, None, [], "", [], bbl_files, "empty_source"))

        candidates = self.root_finder.candidates(tree)
        if not candidates:
            return GatherReport(
                self._source(arxiv_id, version, None, [], "", [], bbl_files, "no_root_file"))

        root_file = candidates[0].path
        expansion = IncludeExpander(tree, max_depth=self.max_depth).expand(root_file)
        bib_files, missing_bib = BibCollector(tree).collect(
            expansion.merged_tex, root_file, expansion.compilation_path)
        source = self._source(arxiv_id, version, root_file, expansion.compilation_path,
                              expansion.merged_tex, bib_files, bbl_files, "ok")
        return GatherReport(source, candidates, expansion.missing, expansion.cycles, missing_bib)

    @staticmethod
    def _source(arxiv_id, version, root_file, compilation_path, merged_tex,
                bib_files, bbl_files, status) -> GatheredSource:
        return {
            "arxiv_id": arxiv_id,
            "version": version,
            "root_file": root_file,
            "compilation_path": list(compilation_path),
            "merged_tex": merged_tex,
            "bib_files": list(bib_files),
            "bbl_files": list(bbl_files),
            "status": status,
        }


def gather_source(source_dir: str | PathLike, arxiv_id: str, version: int) -> GatheredSource:
    """Shortcut for SourceGatherer().gather(...)."""
    return SourceGatherer().gather(source_dir, arxiv_id, version)
