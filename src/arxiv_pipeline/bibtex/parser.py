"""Extract references from BibTeX files and LaTeX ``thebibliography`` blocks.

Paths supplied by Module 1 are relative to ``source_dir``.  The public service
accepts a per-version source directory mapping so the GatheredSource contract
can remain exactly as specified by Module 1.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Iterable

_BIBLIOGRAPHY_RE = re.compile(
    r"\\begin\s*\{thebibliography\}\s*(.*?)\\end\s*\{thebibliography\}",
    re.IGNORECASE | re.DOTALL,
)
_BIBITEM_START_RE = re.compile(r"\\bibitem\s*(?:\[[^\]]*\]\s*)?\{([^{}]+)\}")
_DOI_RE = re.compile(r"\b(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.I)
_URL_RE = re.compile(r"https?://[^\s{}<>]+", re.I)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2}|2100)\b")
_CITE_COMMANDS = (
    "cite", "citep", "citet", "citealt", "citealp", "autocite",
    "parencite", "textcite", "nocite", "Citet", "Citep", "Cite",
    "citeauthor", "citeyear",
)
_CITE_RE = re.compile(
    r"\\(?:cite[a-z]*|autocite[a-z]*|parencite[a-z]*|textcite[a-z]*|nocite)\*?"
    r"(?:\s*\[[^]]*\]){0,2}\s*\{([^{}]*)\}",
    re.IGNORECASE,
)
_BIB_ENTRY_START_RE = re.compile(r"@([a-zA-Z]+)\s*([({])")


def _strip_tex_comments(text: str) -> str:
    """Remove TeX comments while respecting escaped percent signs."""
    result = []
    for line in (text or "").splitlines():
        out = []
        i = 0
        while i < len(line):
            if line[i] == "%":
                backslashes = 0
                j = i - 1
                while j >= 0 and line[j] == "\\":
                    backslashes += 1
                    j -= 1
                if backslashes % 2 == 0:
                    break
            out.append(line[i])
            i += 1
        result.append("".join(out))
    return "\n".join(result)


def extract_cited_keys(merged_tex: str) -> tuple[set[str], bool]:
    """Return citation keys and whether ``\\nocite{*}`` requests every entry."""
    keys: set[str] = set()
    include_all = False
    for match in _CITE_RE.finditer(_strip_tex_comments(merged_tex or "")):
        command = match.group(0).lstrip("\\").split("{", 1)[0].split("[", 1)[0].rstrip("*")
        for key in match.group(1).split(","):
            key = key.strip()
            if command == "nocite" and key == "*":
                include_all = True
            elif key and not re.fullmatch(r"#\d+", key):
                keys.add(key)
    return keys, include_all


def _balanced_bibitems(body: str) -> list[tuple[str, str]]:
    """Return (citation key, raw entry) pairs without splitting nested braces."""
    clean_body = _strip_tex_comments(body)
    matches = list(_BIBITEM_START_RE.finditer(clean_body))
    items: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(clean_body)
        raw = clean_body[start:end].strip()
        if raw:
            items.append((match.group(1).strip(), raw))
    return items


def extract_bibitems(text: str) -> list[tuple[str, str]]:
    """Extract citation-key/raw-entry pairs from every bibliography block."""
    found: list[tuple[str, str]] = []
    clean_text = _strip_tex_comments(text or "")
    for block in _BIBLIOGRAPHY_RE.findall(clean_text):
        found.extend(_balanced_bibitems(block))
    return found


def _replace_href(value: str) -> str:
    """Replace \\href{url}{visible text} with only its visible text."""
    pattern = re.compile(r"\\href\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    previous = None
    while previous != value:
        previous = value
        value = pattern.sub(r"\2", value)
    return value


def _clean_latex(value: str) -> str:
    value = _replace_href(value)
    value = re.sub(r"\\(?:emph|textit|textbf|textrm|mathrm|url)\s*\{([^{}]*)\}", r"\1", value)
    # Decode common TeX accents and special letters instead of dropping them.
    accent_marks = {
        "'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302",
        "~": chr(0x0303), "=": chr(0x0304), ".": chr(0x0307),
        "u": chr(0x0306), "v": chr(0x030C), "H": chr(0x030B), "c": chr(0x0327),
        "d": "\u0323", "b": "\u0331",
    }
    def decode_accent(match: re.Match[str]) -> str:
        command, letter = match.group(1), match.group(2)
        mark = accent_marks.get(command)
        return unicodedata.normalize("NFC", letter + mark) if mark else letter
    value = re.sub(r"\\(['`\"^~=\.uvHcdb])\s*\{?([A-Za-z])\}?", decode_accent, value)
    special_letters = {"ss": "ß", "aa": "å", "o": "ø", "O": "Ø", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ"}
    value = re.sub(r"\\(ss|aa|ae|AE|oe|OE|o|O|l|L|i|j)\b", lambda m: special_letters[m.group(1)], value)
    value = re.sub(r"\\\\(?:[,;:!]|\\)", " ", value)
    value = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^]]*\])?", " ", value)
    value = value.replace(r"\~", " ").replace("~", " ").replace("\n", " ")
    value = value.replace("{", "").replace("}", "")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" \t\n.,;:")


def _split_blocks(raw: str) -> list[str]:
    return [block.strip() for block in re.split(r"\\newblock\b", raw, flags=re.I) if block.strip()]


def _quoted_title(raw: str) -> str:
    for pattern in (r"``\s*(.*?)\s*''", r"[“\"]\s*(.*?)\s*[”\"]"):
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            return _clean_latex(match.group(1))
    return ""


def _author_list_prefix(raw: str) -> tuple[list[str], str]:
    """Recognize a conventional author list at the start of an unstructured entry.

    Supports both ``M. Erhard, M. Krenn, and A. Zeilinger`` and
    ``Wang, W., Zhang, J., and Li, Q.`` without treating initials as sentence
    boundaries. Returns the parsed authors and the remaining bibliography text.
    """
    text = _replace_href(raw).replace("~", " ").replace("\n", " ").strip()
    person = (
        r"(?:"
        r"(?:[A-Z]\.(?:\s*-\s*[A-Z]\.)*\s*)+[A-Z][A-Za-z'’\-]+"
        r"|[A-Z][A-Za-z'’\-]+,\s*(?:[A-Z](?:\.[\s]*)+)(?:[A-Z](?:\.[\s]*)*)?"
        r"|(?:[A-Z][a-z'’\-]+\s+){1,2}[A-Z][a-z'’\-]+"
        r")"
    )
    pattern = re.compile(
        rf"^\s*(?P<authors>{person}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){person})*)\s*,\s*(?P<rest>.+)$",
        re.DOTALL,
    )
    match = pattern.match(text)
    if not match:
        return [], text
    author_text = match.group("authors")
    person_re = re.compile(person)
    authors = [item.group(0).strip(" ,;") for item in person_re.finditer(author_text)]
    return authors, match.group("rest").strip()


def _extract_title(raw: str, authors: list[str]) -> str:
    quoted = _quoted_title(raw)
    if quoted:
        return quoted
    blocks = _split_blocks(raw)
    if len(blocks) >= 2:
        candidate = _clean_latex(blocks[1])
        candidate = re.sub(r"\b(?:19\d{2}|20\d{2}|2100)\b.*$", "", candidate).strip(" .,:;")
        if candidate:
            return candidate

    # Unstructured ``authors, title, journal volume, pages (year)`` entries.
    parsed_authors, remainder = _author_list_prefix(raw)
    if parsed_authors and remainder:
        remainder = _clean_latex(remainder)
        # A journal tail commonly ends in volume/pages followed by a year.
        # Remove that tail first, then use the preceding comma as the title
        # boundary. This avoids splitting initials such as ``W.`` or ``J.``.
        journal_tail = re.search(
            r",\s*[^,]+?\s+\d+\s*,\s*\d+(?:[-–]\d+)?\s*\(?(?:19\d{2}|20\d{2}|2100)\)?\.?$",
            remainder,
            re.I,
        )
        if journal_tail:
            candidate = remainder[:journal_tail.start()].strip(" .,:;")
            if candidate:
                return candidate
        candidate = re.split(r",\s*(?=[A-Z][A-Za-z .&-]*(?:\s+\d|\s*\())", remainder, maxsplit=1)[0]
        if candidate.strip(" .,:;"):
            return candidate.strip(" .,:;")

    cleaned = _clean_latex(raw)
    if authors:
        pos = cleaned.lower().find(authors[0].lower())
        if pos >= 0:
            remainder = cleaned[pos + len(authors[0]):].lstrip(" .,:;-")
            pieces = [p.strip() for p in re.split(r"\.\s+(?=[A-Z“`])", remainder) if p.strip()]
            if pieces and not _YEAR_RE.fullmatch(pieces[0]):
                return pieces[0].rstrip(".")
    pieces = [p.strip() for p in re.split(r"\.\s+(?=[A-Z“`])", cleaned) if p.strip()]
    return pieces[1] if len(pieces) > 1 else (pieces[0] if pieces else "")


def _extract_authors(raw: str) -> list[str]:
    blocks = _split_blocks(raw)
    author_block = _clean_latex(blocks[0] if blocks else raw).strip(" .,:;")
    if len(blocks) == 1:
        parsed, _ = _author_list_prefix(raw)
        if parsed:
            return parsed
    if not author_block or len(author_block) > 300:
        return []
    # With explicit ``and`` the list separator is unambiguous. Preserve
    # surname/initial comma pairs (e.g. ``Wang, W.``).
    authors = re.split(r"\s+and\s+", author_block, flags=re.I)
    expanded: list[str] = []
    for group in authors:
        expanded.extend(re.split(r",\s+(?=[A-Z][a-z]+,\s)", group))
    return [author.strip(" ,;") for author in expanded if author.strip(" ,;")]

def _entry_type_from_text(raw: str) -> str:
    lower = raw.lower()
    if any(term in lower for term in ("proceedings", "conference", "workshop", "symposium", "in proc.")):
        return "inproceedings"
    if any(term in lower for term in ("journal", "transactions", "vol.", "volume")):
        return "article"
    if any(term in lower for term in ("book", "publisher", "press")):
        return "book"
    return "misc"


def parse_bibitem(arxiv_id: str, citation_key: str, raw_entry: str) -> dict[str, object]:
    raw = raw_entry.strip()
    doi_match = _DOI_RE.search(raw)
    url_match = _URL_RE.search(raw)
    year_source = _URL_RE.sub(" ", raw)
    year_source = _DOI_RE.sub(" ", year_source)
    year_matches = list(_YEAR_RE.finditer(year_source))
    year_match = year_matches[-1] if year_matches else None
    authors = _extract_authors(raw)
    blocks = _split_blocks(raw)
    title = _extract_title(raw, authors)
    entry_type = _entry_type_from_text(raw)
    journal = _clean_latex(blocks[2]) if len(blocks) >= 3 else ""
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


def _find_entry_end(text: str, opening_index: int, opening: str) -> int:
    closing = "}" if opening == "{" else ")"
    depth = 1
    in_quote = False
    escaped = False
    for index in range(opening_index + 1, len(text)):
        char = text[index]
        if char == '"' and not escaped and depth == 1:
            in_quote = not in_quote
        elif not in_quote:
            if char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    return index
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    return len(text)


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    parts, start = [], 0
    brace_depth = 0
    in_quote = False
    escaped = False
    for index, char in enumerate(value):
        if char == '"' and not escaped and brace_depth == 0:
            in_quote = not in_quote
        elif not in_quote:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)
            elif char == delimiter and brace_depth == 0:
                parts.append(value[start:index].strip())
                start = index + 1
        if char == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
    tail = value[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_bib_fields(body: str) -> dict[str, str]:
    parts = _split_top_level(body)
    fields: dict[str, str] = {}
    for part in parts[1:]:  # first item is citation key
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        fields[name.strip().lower()] = value.strip().rstrip(",")
    return fields


def _bib_value(value: str, strings: dict[str, str], *, clean: bool = True) -> str:
    pieces = _split_top_level(value, "#")
    output = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if (piece.startswith("{") and piece.endswith("}")) or (piece.startswith('"') and piece.endswith('"')):
            output.append(piece[1:-1])
        elif re.fullmatch(r"\d+", piece):
            output.append(piece)
        else:
            output.append(strings.get(piece.lower(), piece))
    combined = "".join(output)
    return _clean_latex(combined) if clean else combined.strip()


def _strip_bib_comments(text: str) -> str:
    """Strip % comments only between BibTeX entries, never inside an entry."""
    result: list[str] = []
    pos = 0
    while pos < len(text):
        match = _BIB_ENTRY_START_RE.search(text, pos)
        segment_end = match.start() if match else len(text)
        segment = text[pos:segment_end]
        result.append(_strip_tex_comments(segment))
        if not match:
            break
        end = _find_entry_end(text, match.end() - 1, match.group(2))
        result.append(text[match.start():min(end + 1, len(text))])
        pos = min(end + 1, len(text))
    return "".join(result)


def _parse_bibtex_text(text: str) -> tuple[list[tuple[str, str, dict[str, str]]], dict[str, str]]:
    """Parse common BibTeX entries, @String macros, and skip comments/preambles."""
    clean = _strip_bib_comments(text or "")
    entries: list[tuple[str, str, dict[str, str]]] = []
    strings: dict[str, str] = {}
    pos = 0
    while True:
        match = _BIB_ENTRY_START_RE.search(clean, pos)
        if not match:
            break
        kind = match.group(1).lower()
        opening = match.group(2)
        end = _find_entry_end(clean, match.end() - 1, opening)
        body = clean[match.end():end].strip()
        pos = max(end + 1, match.end())
        if kind in ("comment", "preamble"):
            continue
        if kind == "string":
            if "=" in body:
                name, value = body.split("=", 1)
                strings[name.strip().lower()] = _bib_value(value, strings, clean=False)
            continue
        fields = _parse_bib_fields(body)
        if not fields:
            continue
        citation_key = _split_top_level(body)[0].strip()
        entries.append((kind, citation_key, fields))
    return entries, strings


def _parse_bibtex_entry(arxiv_id: str, key: str, kind: str, fields: dict[str, str],
                        strings: dict[str, str]) -> dict[str, object]:
    values = {name: _bib_value(value, strings, clean=name != "url") for name, value in fields.items()}
    author_text = values.get("author", "")
    authors = [name.strip() for name in re.split(r"\s+and\s+", author_text, flags=re.I) if name.strip()]
    title = values.get("title", "")
    doi = values.get("doi", "")
    doi_match = _DOI_RE.search(doi)
    url = values.get("url", "")
    url_match = _URL_RE.search(url)
    year_text = values.get("year", "")
    year_match = _YEAR_RE.search(year_text)
    journal_field = next((fields[name] for name in ("journal", "booktitle", "publisher") if name in fields), "")
    journal_expanded = _bib_value(journal_field, strings, clean=False)
    journal = values.get("journal", values.get("booktitle", values.get("publisher", "")))
    if journal_expanded.rstrip().endswith(".") and journal and not journal.endswith("."):
        journal += "."
    return {
        "arxiv_id": arxiv_id,
        "citation_key": key,
        "entry_type": kind.lower(),
        "title": title,
        "authors": authors,
        "year": int(year_match.group(1)) if year_match else None,
        "doi": doi_match.group(1).rstrip(".,;)") if doi_match else doi.strip(),
        "journal": journal,
        "url": url_match.group(0).rstrip(".,;)") if url_match else url,
    }


def _resolve_source_path(filename: str | Path, base_dir: Path | None) -> Path | None:
    """Safely resolve a bib or bbl path against base_dir without path doubling."""
    path = Path(filename)
    if path.is_absolute():
        return path if path.is_file() else None
    if base_dir is None:
        return None
    cand = base_dir / path
    if cand.is_file():
        return cand
    try:
        cand_rel = base_dir / path.relative_to(base_dir)
        if cand_rel.is_file():
            return cand_rel
    except ValueError:
        pass
    return None


def extract_from_gathered_source(
    source: dict[str, object], source_dir: str | Path | None = None
) -> list[dict[str, object]]:
    """Read cited .bib entries plus .bibitem entries from merged TeX and .bbl."""
    arxiv_id = str(source.get("arxiv_id", ""))
    merged_tex = str(source.get("merged_tex", "") or "")
    cited_keys, include_all = extract_cited_keys(merged_tex)
    base_dir_value = source_dir or source.get("source_dir")
    base_dir = Path(base_dir_value) if base_dir_value else None

    results: list[dict[str, object]] = []
    seen_keys: dict[str, dict[str, object]] = {}

    # Read only .bib files explicitly discovered by Module 1.
    parsed_bibs: list[tuple[str, str, dict[str, str], dict[str, str]]] = []
    for filename in source.get("bib_files", []) or []:
        try:
            path = _resolve_source_path(filename, base_dir)
            if path is not None and path.is_file():
                entries, strings = _parse_bibtex_text(path.read_text(encoding="utf-8", errors="replace"))
                parsed_bibs.extend((kind, key, fields, strings) for kind, key, fields in entries)
        except (OSError, ValueError, TypeError):
            continue

    for kind, key, fields, strings in parsed_bibs:
        if not (include_all or key in cited_keys):
            continue
        entry = _parse_bibtex_entry(arxiv_id, key, kind, fields, strings)
        previous = seen_keys.get(key)
        if previous is None:
            results.append(entry)
            seen_keys[key] = entry
        elif not _same_reference(previous, entry):
            # Preserve conflicting entries; downstream remapping must treat
            # this key as ambiguous rather than silently discarding one.
            results.append(entry)

    texts: list[str] = [merged_tex]
    for filename in source.get("bbl_files", []) or []:
        try:
            path = _resolve_source_path(filename, base_dir)
            if path is not None and path.is_file():
                texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError, TypeError):
            continue

    for text in texts:
        for key, raw in extract_bibitems(text):
            entry = parse_bibitem(arxiv_id, key, raw)
            previous = seen_keys.get(key)
            if previous is None:
                results.append(entry)
                seen_keys[key] = entry
            elif not _same_reference(previous, entry):
                results.append(entry)
    return results


def _same_reference(left: dict[str, object], right: dict[str, object]) -> bool:
    """Compare duplicate-key records conservatively, preferring DOI when present."""
    left_doi = str(left.get("doi", "")).strip().lower()
    right_doi = str(right.get("doi", "")).strip().lower()
    if left_doi and right_doi:
        return left_doi == right_doi
    left_title = re.sub(r"\W+", "", str(left.get("title", "")).lower())
    right_title = re.sub(r"\W+", "", str(right.get("title", "")).lower())
    return bool(left_title and right_title and left_title == right_title)
