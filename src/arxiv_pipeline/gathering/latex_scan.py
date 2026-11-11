r"""Small LaTeX scanners: comments, \input/\include, \documentclass.

Every scanner runs on a *masked* copy of the text where comments are replaced
by spaces. The masked copy has the same length as the original, so match
offsets can be used to slice the original text, comments included.
"""

import re
from dataclasses import dataclass

_INCLUDE_RE = re.compile(
    r"\\(?P<cmd>input|include|subfile)(?![A-Za-z@])\s*\{(?P<braced>[^{}]*)\}"
    r"|\\input(?![A-Za-z@])[ \t]+(?P<bare>[^\s{}%\\]+)"
)
_DOCUMENTCLASS_RE = re.compile(
    r"\\document(?:class|style)(?![A-Za-z@])\s*(?:\[[^\]]*\])?\s*\{(?P<name>[^{}]*)\}"
)
_BEGIN_DOCUMENT_RE = re.compile(r"\\begin\s*\{document\}")
_END_DOCUMENT_RE = re.compile(r"\\end\s*\{document\}")
_BIB_RE = re.compile(
    r"\\(?P<cmd>bibliography|addbibresource|addglobalbib|addsectionbib)(?![A-Za-z@])"
    r"\s*(?:\[[^\]]*\])?\s*\{(?P<names>[^{}]*)\}"
)
_SIMPLE_MACRO_RE = re.compile(
    r"\\(?:re)?newcommand\*?\s*\{?\s*\\(?P<a>[A-Za-z@]+)\s*\}?\s*\{(?P<av>[^{}#\\]*)\}"
    r"|\\def\s*\\(?P<b>[A-Za-z@]+)\s*\{(?P<bv>[^{}#\\]*)\}"
)
_ENDINPUT_RE = re.compile(r"\\endinput(?![A-Za-z@])")


def comment_start(line: str) -> int:
    r"""Index of the % that starts a comment, or -1.

    A % escaped by an odd number of backslashes (\%) is text; after an even
    number (\\%) it still starts a comment.
    """
    index = line.find("%")
    while index != -1:
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and line[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            return index
        index = line.find("%", index + 1)
    return -1


def mask_comments(text: str) -> str:
    """Same-length copy of text with every comment replaced by spaces."""
    lines = text.split("\n")
    for number, line in enumerate(lines):
        start = comment_start(line)
        if start != -1:
            lines[number] = line[:start] + " " * (len(line) - start)
    return "\n".join(lines)


@dataclass(frozen=True)
class IncludeCommand:
    command: str   # "input", "include" or "subfile"
    target: str    # argument as written, e.g. "./sub/intro"
    start: int     # offsets of the whole command in the text
    end: int


def find_includes(masked: str) -> list[IncludeCommand]:
    found = []
    for match in _INCLUDE_RE.finditer(masked):
        if match["bare"] is not None:
            command, target = "input", match["bare"]
        else:
            command, target = match["cmd"], match["braced"]
        found.append(IncludeCommand(command, target, match.start(), match.end()))
    return found


def document_class(masked: str) -> str | None:
    r"""Class name of the first uncommented \documentclass (or \documentstyle)."""
    match = _DOCUMENTCLASS_RE.search(masked)
    return None if match is None else match["name"].strip()


def has_begin_document(masked: str) -> bool:
    return _BEGIN_DOCUMENT_RE.search(masked) is not None


def endinput_cut(masked: str) -> int | None:
    r"""Offset where reading stops because of \endinput, or None.

    TeX finishes the line that holds \endinput, then stops reading the file.
    """
    match = _ENDINPUT_RE.search(masked)
    if match is None:
        return None
    line_end = masked.find("\n", match.end())
    return len(masked) if line_end == -1 else line_end + 1


def find_bib_names(masked: str) -> list[str]:
    r"""Names listed by \bibliography{a, b} and \addbibresource[opts]{c.bib}.

    Order of appearance, duplicates kept (the caller removes them). Remote
    resources (http..., with a colon) are skipped, they cannot be read from disk.
    """
    names = []
    for match in _BIB_RE.finditer(masked):
        for part in match["names"].split(","):
            name = part.strip()
            if name and ":" not in name:
                names.append(name)
    return names


def simple_macros(masked: str) -> dict[str, str]:
    r"""Macros without arguments whose body is plain text, e.g.

    \newcommand{\texpath}{sections} or \def\figdir{figs/}. Used only to expand
    paths such as \input{\texpath/intro}; anything fancier is ignored.
    """
    found = {}
    for match in _SIMPLE_MACRO_RE.finditer(masked):
        name = match["a"] or match["b"]
        value = match["av"] if match["a"] else match["bv"]
        found[name] = value.strip()
    return found


def expand_simple_macros(target: str, macros: dict[str, str]) -> str:
    if "\\" not in target or not macros:
        return target
    return re.sub(r"\\([A-Za-z@]+)",
                  lambda m: macros.get(m[1], m[0]), target)


def document_body_span(masked: str) -> tuple[int, int] | None:
    r"""(start, end) of the text between \begin{document} and \end{document}."""
    begin = _BEGIN_DOCUMENT_RE.search(masked)
    if begin is None:
        return None
    end = _END_DOCUMENT_RE.search(masked, begin.end())
    return begin.end(), (len(masked) if end is None else end.start())
