"""Public Module 4 service API."""
from __future__ import annotations

from .contracts import BibtexEntry, PaperReferences
from .deduplicator import unify_entries
from .parser import extract_from_gathered_source


def extract_and_unify_references(versions_sources: list[dict[str, object]]) -> PaperReferences:
    """Extract and deduplicate references for all versions of one arXiv paper."""
    if not versions_sources:
        return {"arxiv_id": "", "unified_bibtex": [], "citation_remap": {}}

    arxiv_id = str(versions_sources[0].get("arxiv_id", ""))
    entries: list[tuple[int, dict[str, object]]] = []
    for source in sorted(versions_sources, key=lambda item: int(item.get("version", 0))):
        source_id = str(source.get("arxiv_id", ""))
        if arxiv_id and source_id and source_id != arxiv_id:
            raise ValueError(f"Mixed arXiv IDs: expected {arxiv_id}, got {source_id}")
        version = int(source.get("version", 1))
        entries.extend((version, entry) for entry in extract_from_gathered_source(source))

    unified, remap = unify_entries(entries)
    # Preserve every input version in the contract, including PDF-only/no-bib versions.
    for source in versions_sources:
        remap.setdefault(int(source.get("version", 1)), {})
    return {
        "arxiv_id": arxiv_id,
        "unified_bibtex": unified,  # type: ignore[typeddict-item]
        "citation_remap": remap,
    }
