"""Cross-version BibTeX deduplication and field union."""
from __future__ import annotations

from copy import deepcopy
from .normalizer import normalize_doi, normalize_title, is_reliable_title

_FIELDS = ("entry_type", "title", "authors", "year", "doi", "journal", "url")


def _identities(entry: dict[str, object]) -> list[str]:
    identities: list[str] = []
    doi = normalize_doi(str(entry.get("doi", "")))
    title = str(entry.get("title", ""))
    if doi:
        identities.append(f"doi:{doi}")
    if is_reliable_title(title):
        identities.append(f"title:{normalize_title(title)}")
    # Fall back to a citation key only when stronger identity data is absent.
    # This is intentionally cross-version: the same key commonly survives a
    # revision even when its title punctuation/metadata changes slightly.
    if not identities and entry.get("citation_key"):
        identities.append(f"key:{str(entry['citation_key']).strip().casefold()}")
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


def unify_entries(
    entries_by_version: list[tuple[int, dict[str, object]]],
    conflicts: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], dict[int, dict[str, str]]]:
    """Deduplicate by DOI/title and omit remaps for ambiguous same-version keys.

    ``conflicts`` is an optional collector to preserve the public two-value
    return contract while exposing citation-key collisions to the service.
    """
    canonical_by_identity: dict[str, dict[str, object]] = {}
    unique: list[dict[str, object]] = []
    remap: dict[int, dict[str, str]] = {}
    keys_seen: dict[tuple[int, str], dict[str, object]] = {}
    canonical_for_key_slot: dict[tuple[int, str], dict[str, object]] = {}
    ambiguous: set[tuple[int, str]] = set()
    for version, entry in sorted(entries_by_version, key=lambda pair: pair[0]):
        key = str(entry.get("citation_key", ""))
        key_slot = (version, key)
        previous_key_entry = keys_seen.get(key_slot)
        if previous_key_entry is not None:
            previous_doi = normalize_doi(str(previous_key_entry.get("doi", "")))
            current_doi = normalize_doi(str(entry.get("doi", "")))
            previous_title = normalize_title(str(previous_key_entry.get("title", "")))
            current_title = normalize_title(str(entry.get("title", "")))
            same = (previous_doi and current_doi and previous_doi == current_doi) or (
                not (previous_doi and current_doi) and previous_title and current_title
                and previous_title == current_title
            )
            if not same:
                ambiguous.add(key_slot)
                if conflicts is not None:
                    conflicts.append({
                        "version": version,
                        "citation_key": key,
                        "reason": "duplicate_key_different_reference",
                        "references": [deepcopy(previous_key_entry), deepcopy(entry)],
                    })
        else:
            keys_seen[key_slot] = deepcopy(entry)

        identities = _identities(entry)
        # A repeated key inside one version may represent two distinct works.
        # Do not let the key-only fallback collapse that genuine collision.
        if previous_key_entry is not None and not (
            (normalize_doi(str(previous_key_entry.get("doi", ""))) and
             normalize_doi(str(entry.get("doi", ""))) and
             normalize_doi(str(previous_key_entry.get("doi", ""))) == normalize_doi(str(entry.get("doi", "")))) or
            (normalize_title(str(previous_key_entry.get("title", ""))) and
             normalize_title(str(previous_key_entry.get("title", ""))) == normalize_title(str(entry.get("title", ""))))
        ):
            identities = [identity for identity in identities if not identity.startswith("key:")]
        canonical = next((canonical_by_identity[i] for i in identities if i in canonical_by_identity), None)
        if canonical is None:
            canonical = deepcopy(entry)
            unique.append(canonical)
        else:
            _merge_entry(canonical, entry)
        for identity in identities:
            canonical_by_identity[identity] = canonical
        for identity in _identities(canonical):
            canonical_by_identity[identity] = canonical
        canonical_for_key_slot[key_slot] = canonical
        if key_slot not in ambiguous:
            remap.setdefault(version, {})[key] = str(canonical.get("citation_key", key))
        else:
            remap.setdefault(version, {}).pop(key, None)

    ambiguous_names = {key for _version, key in ambiguous}
    for version, key in ambiguous:
        remap.setdefault(version, {}).pop(key, None)
    # A later version must not be remapped to a canonical key that is itself
    # ambiguous in the version where that canonical entry originated.
    for version, mappings in remap.items():
        for source_key, target_key in list(mappings.items()):
            if target_key in ambiguous_names:
                mappings.pop(source_key, None)

    # A unified bibliography is a flat collection: make every output key
    # unique even when two genuinely different works used the same local key.
    used_keys: set[str] = set()
    for entry in unique:
        original = str(entry.get("citation_key", "reference")) or "reference"
        candidate = original
        suffix = 2
        while candidate in used_keys:
            candidate = f"{original}__{suffix}"
            suffix += 1
        entry["citation_key"] = candidate
        used_keys.add(candidate)

    # Reflect any disambiguated canonical key in each version's citation map.
    for (version, source_key), canonical in canonical_for_key_slot.items():
        if (version, source_key) not in ambiguous and source_key in remap.get(version, {}):
            remap[version][source_key] = str(canonical.get("citation_key", source_key))
    return unique, remap
