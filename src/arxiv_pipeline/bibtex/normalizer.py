"""Normalization helpers for cross-version bibliography matching."""
from __future__ import annotations

import re
import unicodedata


def normalize_doi(doi: str) -> str:
    value = (doi or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value)
    return value.rstrip(".,;)")


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def is_reliable_title(title: str) -> bool:
    """A very short title is too weak to identify a reference safely."""
    normalized = normalize_title(title)
    words = re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii").lower())
    return len(normalized) >= 8 and len(words) >= 2


def reference_identity(entry: dict[str, object]) -> str:
    doi = normalize_doi(str(entry.get("doi", "")))
    if doi:
        return f"doi:{doi}"
    title = str(entry.get("title", ""))
    if is_reliable_title(title):
        return f"title:{normalize_title(title)}"
    return f"key:{entry.get('citation_key', '')}"
