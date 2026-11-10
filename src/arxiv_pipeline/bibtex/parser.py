"""Extract bibliography items from LaTeX thebibliography environments."""
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
        raw = re.sub(r"(?m)%[^\n]*$", "", body[start:end]).strip()
        if raw:
            items.append((match.group(1).strip(), raw))
    return items


def extract_bibitems(text: str) -> list[tuple[str, str]]:
    """Extract citation-key/raw-entry pairs from every bibliography block."""
    found: list[tuple[str, str]] = []
    for block in _BIBLIOGRAPHY_RE.findall(text or ""):
        found.extend(_balanced_bibitems(block))
    return found


def _command_argument(value: str, command: str) -> str:
    """Return the first braced argument of a TeX command, supporting nested braces."""
    match = re.search(r"\\" + re.escape(command) + r"\s*\{", value)
    if not match:
        return ""
    start = match.end()
    depth = 1
    for index in range(start, len(value)):
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return value[start:index]
    return ""


def _replace_href(value: str) -> str:
    """Replace \\href{url}{visible text} with only its visible text."""
    pattern = re.compile(r"\\href\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    previous = None
    while previous != value:
        previous = value
        value = pattern.sub(r"\2", value)
    return value


def _clean_latex(value: str) -> str:
    # Keep visible text, not the URL, for hyperlinks.
    value = _replace_href(value)
    value = re.sub(r"\\(?:emph|textit|textbf|textrm|mathrm|url)\s*\{([^{}]*)\}", r"\1", value)
    value = re.sub(r"\\\\(?:[,;:!]|\\)", " ", value)
    value = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^]]*\])?", " ", value)
    value = value.replace("~", " ").replace("\n", " ")
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\n.,;:")


def _split_blocks(raw: str) -> list[str]:
    """Split a bibitem on structural \\newblock markers before cleaning commands."""
    blocks = re.split(r"\\newblock\b", raw, flags=re.IGNORECASE)
    return [block.strip() for block in blocks if block.strip()]


def _quoted_title(raw: str) -> str:
    # BibTeX style commonly emits ``title''; support straight/curly quotes too.
    patterns = (
        r"``\s*(.*?)\s*''",
        r"[“\"]\s*(.*?)\s*[”\"]",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            return _clean_latex(match.group(1))
    return ""


def _extract_title(raw: str, authors: list[str]) -> str:
    quoted = _quoted_title(raw)
    if quoted:
        return quoted

    blocks = _split_blocks(raw)
    # In standard .bbl output, block 0 is authors and block 1 is the title.
    if len(blocks) >= 2:
        candidate = _clean_latex(blocks[1])
        candidate = re.sub(r"\b(?:19\d{2}|20\d{2}|2100)\b.*$", "", candidate).strip(" .,:;")
        if candidate:
            return candidate

    cleaned = _clean_latex(raw)
    if authors:
        author_text = authors[0]
        pos = cleaned.lower().find(author_text.lower())
        if pos >= 0:
            remainder = cleaned[pos + len(author_text):].lstrip(" .,:;-")
            # Do not split at initials such as W. Zhang or J. Smith.
            pieces = [p.strip() for p in re.split(r"\.\s+(?=[A-Z“`])", remainder) if p.strip()]
            if pieces:
                candidate = pieces[0]
                if not _YEAR_RE.fullmatch(candidate):
                    return candidate.rstrip(".")
    pieces = [p.strip() for p in re.split(r"\.\s+(?=[A-Z“`])", cleaned) if p.strip()]
    return pieces[1] if len(pieces) > 1 else (pieces[0] if pieces else "")


def _extract_authors(raw: str) -> list[str]:
    blocks = _split_blocks(raw)
    author_block = _clean_latex(blocks[0] if blocks else raw)
    # Remove common leading/trailing punctuation and avoid splitting "Wang, Z.".
    author_block = re.sub(r"^\s*(?:\\bibinfo\s*\{[^}]+\}\s*)", "", author_block)
    author_block = author_block.strip(" .,:;")
    if not author_block or len(author_block) > 300:
        return []
    authors = re.split(r"\s+and\s+", author_block, flags=re.IGNORECASE)
    expanded: list[str] = []
    for author_group in authors:
        # Handle "Surname, I., Surname, J." without splitting surname/initial pairs.
        expanded.extend(re.split(r",\s+(?=[A-Z][a-z]+,\s)", author_group))
    return [author.strip(" ,;") for author in expanded if author.strip(" ,;")]


def parse_bibitem(arxiv_id: str, citation_key: str, raw_entry: str) -> dict[str, object]:
    """Convert one free-form ``\\bibitem`` entry to the agreed BibtexEntry shape."""
    raw = raw_entry.strip()
    doi_match = _DOI_RE.search(raw)
    url_match = _URL_RE.search(raw)
    year_match = _YEAR_RE.search(raw)

    authors = _extract_authors(raw)
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
    blocks = _split_blocks(raw)
    if len(blocks) >= 3:
        journal = _clean_latex(blocks[2])
    elif entry_type == "article":
        segments = [s.strip(" .,:;") for s in re.split(r"\.\s+", _clean_latex(raw)) if s.strip()]
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
    """Extract references from merged TeX and the .bbl files supplied by Module 1."""
    arxiv_id = str(source.get("arxiv_id", ""))
    texts: list[str] = [str(source.get("merged_tex", "") or "")]
    source_dir_value = source.get("source_dir")
    source_dir = Path(str(source_dir_value)) if source_dir_value else None

    for filename in source.get("bbl_files", []) or []:
        try:
            path = Path(str(filename))
            if not path.is_absolute() and source_dir is not None:
                path = source_dir / path
            if path.is_file():
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError, TypeError):
            continue

    results: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for text in texts:
        for key, raw in extract_bibitems(text):
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(parse_bibitem(arxiv_id, key, raw))
    return results
