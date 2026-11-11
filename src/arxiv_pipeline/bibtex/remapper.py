"""Rewrite citation keys while preserving optional citation arguments."""
from __future__ import annotations

import re

_CITE_COMMANDS = (
    "cite", "citep", "citet", "citealt", "citealp", "autocite",
    "parencite", "textcite",
)
_CITE_RE = re.compile(
    r"(\\(?:" + "|".join(_CITE_COMMANDS) + r")\*?(?:\s*\[[^\]]*\]){0,2}\s*\{)([^{}]*)(\})"
)


def rewrite_citation_keys(text: str, remap: dict[str, str]) -> str:
    """Rewrite keys for supported natbib/biblatex citation commands."""
    def replace(match: re.Match[str]) -> str:
        # Rewrite each key independently while preserving whitespace and empty
        # segments. This keeps the user's original TeX formatting intact.
        rewritten: list[str] = []
        for segment in match.group(2).split(","):
            key_match = re.fullmatch(r"(\s*)(.*?)(\s*)", segment, re.DOTALL)
            assert key_match is not None
            leading, key, trailing = key_match.groups()
            rewritten.append(leading + remap.get(key, key) + trailing)
        return match.group(1) + ",".join(rewritten) + match.group(3)
    return _CITE_RE.sub(replace, text)
