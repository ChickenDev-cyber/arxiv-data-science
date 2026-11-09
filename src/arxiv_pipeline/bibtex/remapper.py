"""Rewrite citation keys while preserving optional citation arguments."""
from __future__ import annotations

import re

_CITE_RE = re.compile(r"(\\cite\s*(?:\[[^\]]*\]\s*){0,2}\{)([^{}]*)(\})")


def rewrite_citation_keys(text: str, remap: dict[str, str]) -> str:
    """Rewrite keys in ``\\cite{a,b}``; leave unknown keys and other text intact."""
    def replace(match: re.Match[str]) -> str:
        keys = [key.strip() for key in match.group(2).split(",")]
        rewritten = [remap.get(key, key) for key in keys]
        return match.group(1) + ",".join(rewritten) + match.group(3)
    return _CITE_RE.sub(replace, text)
