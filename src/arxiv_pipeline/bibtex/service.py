"""Public Module 4 service API."""
from __future__ import annotations

from pathlib import Path

from .contracts import BibtexEntry, PaperReferences
from .deduplicator import unify_entries
from .parser import extract_from_gathered_source


def extract_and_unify_references(
    versions_sources: list[dict[str, object]],
    source_dirs: dict[int, str | Path] | None = None,
) -> PaperReferences:
    """Extract references for all versions of one paper.

    ``source_dirs`` maps each version number to its source directory. It is
    needed because Module 1 deliberately keeps paths in ``bib_files`` and
    ``bbl_files`` relative to ``source_dir`` and its 8-key GatheredSource does
    not include the directory itself.
    """
    if not versions_sources:
        return {"arxiv_id": "", "unified_bibtex": [], "citation_remap": {}, "citation_key_conflicts": []}

    arxiv_id = str(versions_sources[0].get("arxiv_id", ""))
    entries: list[tuple[int, dict[str, object]]] = []
    for source in sorted(versions_sources, key=lambda item: int(item.get("version", 0))):
        source_id = str(source.get("arxiv_id", ""))
        if arxiv_id and source_id and source_id != arxiv_id:
            raise ValueError(f"Mixed arXiv IDs: expected {arxiv_id}, got {source_id}")
        version = int(source.get("version", 1))
        source_dir = (source_dirs or {}).get(version)
        entries.extend(
            (version, entry)
            for entry in extract_from_gathered_source(source, source_dir=source_dir)
        )

    conflicts: list[dict[str, object]] = []
    unified, remap = unify_entries(entries, conflicts=conflicts)
    for source in versions_sources:
        remap.setdefault(int(source.get("version", 1)), {})
    return {
        "arxiv_id": arxiv_id,
        "unified_bibtex": unified,  # type: ignore[typeddict-item]
        "citation_remap": remap,
        "citation_key_conflicts": conflicts,
    }
