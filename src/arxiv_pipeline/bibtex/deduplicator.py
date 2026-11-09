"""Cross-version BibTeX deduplication and field union."""
from __future__ import annotations

from copy import deepcopy
from .normalizer import normalize_doi, normalize_title

_FIELDS = ("entry_type", "title", "authors", "year", "doi", "journal", "url")


def _identities(entry: dict[str, object]) -> list[str]:
    identities: list[str] = []
    doi = normalize_doi(str(entry.get("doi", "")))
    title = normalize_title(str(entry.get("title", "")))
    if doi:
        identities.append(f"doi:{doi}")
    if title:
        identities.append(f"title:{title}")
    if not identities:
        identities.append(f"key:{entry.get('citation_key', '')}")
    return identities


def _merge_entry(target: dict[str, object], incoming: dict[str, object]) -> None:
    """Fill missing fields; preserve all distinct author names and known metadata."""
    for field in _FIELDS:
        old, new = target.get(field), incoming.get(field)
        if field == "authors":
            merged = list(old or [])
            for author in new or []:
                if author not in merged:
                    merged.append(author)
            target[field] = merged
        elif field == "year":
            if old is None and new is not None:
                target[field] = new
        elif not old and new:
            target[field] = new


def unify_entries(entries_by_version: list[tuple[int, dict[str, object]]]) -> tuple[list[dict[str, object]], dict[int, dict[str, str]]]:
    """Deduplicate by DOI OR normalized title and build per-version key maps."""
    canonical_by_identity: dict[str, dict[str, object]] = {}
    unique: list[dict[str, object]] = []
    remap: dict[int, dict[str, str]] = {}
    for version, entry in sorted(entries_by_version, key=lambda pair: pair[0]):
        identities = _identities(entry)
        canonical = next((canonical_by_identity[i] for i in identities if i in canonical_by_identity), None)
        if canonical is None:
            canonical = deepcopy(entry)
            unique.append(canonical)
        else:
            _merge_entry(canonical, entry)
        for identity in identities:
            canonical_by_identity[identity] = canonical
        # Register aliases after merging, including newly acquired DOI/title fields.
        for identity in _identities(canonical):
            canonical_by_identity[identity] = canonical
        key = str(entry.get("citation_key", ""))
        remap.setdefault(version, {})[key] = str(canonical.get("citation_key", key))
    return unique, remap
