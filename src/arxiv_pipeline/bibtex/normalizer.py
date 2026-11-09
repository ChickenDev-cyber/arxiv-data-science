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


def reference_identity(entry: dict[str, object]) -> str:
    doi = normalize_doi(str(entry.get("doi", "")))
    if doi:
        return f"doi:{doi}"
    title = normalize_title(str(entry.get("title", "")))
    if title:
        return f"title:{title}"
    # No reliable identity: keep separate by citation key rather than false merge.
    return f"key:{entry.get('citation_key', '')}"
