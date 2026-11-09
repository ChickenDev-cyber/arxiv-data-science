"""Extract bibliography items from LaTeX thebibliography environments.

The parser deliberately uses only the standard library. Bibitems are free-form
prose, so field extraction is heuristic; raw source is retained internally
until all fields have been inspected.
"""
from __future__ import annotations

import re
from pathlib import Path

_BIBLIOGRAPHY_RE = re.compile(
    r"\\begin\s*\{thebibliography\}\s*(.*?)\\end\s*\{thebibliography\}",
    re.IGNORECASE | re.DOTALL,
)
_BIBITEM_START_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
_DOI_RE = re.compile(r"\b(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)
_URL_RE = re.compile(r"https?://[^\s{}<>]+", re.I)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2}|2100)\b")


def _balanced_bibitems(body: str) -> list[tuple[str, str]]:
    """Return (citation key, raw entry) pairs without splitting nested braces."""
    matches = list(_BIBITEM_START_RE.finditer(body))
    items: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        raw = body[start:end].strip()
        # Remove trailing whitespace and harmless TeX comments.
        raw = re.sub(r"(?m)%[^\n]*$", "", raw).strip()
        if raw:
            items.append((match.group(1).strip(), raw))
    return items


def extract_bibitems(text: str) -> list[tuple[str, str]]:
    """Extract citation-key/raw-entry pairs from every bibliography block."""
    found: list[tuple[str, str]] = []
    for block in _BIBLIOGRAPHY_RE.findall(text or ""):
        found.extend(_balanced_bibitems(block))
    return found


def _clean_latex(value: str) -> str:
    value = re.sub(r"\\(?:emph|textit|textbf|textrm|mathrm|url)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^]]*\])?", " ", value)
    value = value.replace("~", " ").replace("\n", " ")
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\n.,;:")


def _extract_title(raw: str, authors: list[str]) -> str:
    # Prefer an explicitly quoted title, common in hand-written bibitems.
    quoted = re.search(r"[\"“`](.+?)[\"”`]", raw)
    if quoted:
        return _clean_latex(quoted.group(1))
    cleaned = _clean_latex(raw)
    if authors:
        author_text = authors[0]
        # Common format: Authors. Title. Venue, year.
        pos = cleaned.lower().find(author_text.lower())
        if pos >= 0:
            remainder = cleaned[pos + len(author_text):].lstrip(" .,:;-")
            pieces = [p.strip() for p in re.split(r"\.\s+", remainder) if p.strip()]
            if pieces:
                candidate = pieces[0]
                if not _YEAR_RE.fullmatch(candidate):
                    return candidate.rstrip(".")
    # Conservative fallback: first sentence-like segment.
    pieces = [p.strip() for p in re.split(r"\.\s+", cleaned) if p.strip()]
    return pieces[1] if len(pieces) > 1 else (pieces[0] if pieces else "")


def parse_bibitem(arxiv_id: str, citation_key: str, raw_entry: str) -> dict[str, object]:
    """Convert one free-form ``\\bibitem`` entry to the agreed BibtexEntry shape."""
    raw = raw_entry.strip()
    doi_match = _DOI_RE.search(raw)
    url_match = _URL_RE.search(raw)
    year_match = _YEAR_RE.search(raw)

    cleaned = _clean_latex(raw)
    # Author lists in common bibitem prose precede the first sentence boundary.
    first_segment = re.split(r"\.\s+", cleaned, maxsplit=1)[0].strip(" .,:;")
    authors = [a.strip() for a in re.split(r"\s+and\s+|\s*,\s*(?=[A-Z])", first_segment) if a.strip()]
    if not authors or len(first_segment) > 220:
        authors = []

    title = _extract_title(raw, authors)
    lower = raw.lower()
    if any(term in lower for term in ("proceedings", "conference", "workshop", "symposium", "in proc.")):
        entry_type = "inproceedings"
    elif any(term in lower for term in ("journal", "transactions", "vol.", "volume")):
        entry_type = "article"
    elif any(term in lower for term in ("book", "publisher", "press")):
        entry_type = "book"
    else:
        entry_type = "misc"

    journal = ""
    if entry_type == "article":
        # Keep a compact venue guess; do not mistake the title itself for a venue.
        segments = [s.strip(" .,:;") for s in re.split(r"\.\s+", cleaned) if s.strip()]
        if len(segments) >= 3:
            journal = segments[2]

    return {
        "arxiv_id": arxiv_id,
        "citation_key": citation_key.strip(),
        "entry_type": entry_type,
        "title": title,
        "authors": authors,
        "year": int(year_match.group(1)) if year_match else None,
        "doi": doi_match.group(1).rstrip(".,;)") if doi_match else "",
        "journal": journal,
        "url": url_match.group(0).rstrip(".,;)") if url_match else "",
    }


def extract_from_gathered_source(source: dict[str, object]) -> list[dict[str, object]]:
    """Read bibliography blocks from merged TeX and any available .bbl files."""
    arxiv_id = str(source.get("arxiv_id", ""))
    texts: list[str] = [str(source.get("merged_tex", "") or "")]
    for filename in source.get("bbl_files", []) or []:
        try:
            path = Path(str(filename))
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
    results: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for text in texts:
        for key, raw in extract_bibitems(text):
            # .tex and .bbl often contain the same bibliography.
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(parse_bibitem(arxiv_id, key, raw))
    return results
