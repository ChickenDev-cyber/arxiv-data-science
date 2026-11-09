"""LaTeX stream utilities for structural parsing."""

import re


def strip_comments(tex: str) -> str:
    """Remove LaTeX comments (% ...) while preserving escaped percent signs (\\%)."""
    # Pattern matches unescaped % until the end of the line
    pattern = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
    return pattern.sub("", tex)


def extract_balanced_braces(tex: str, open_pos: int) -> tuple[str, int]:
    """Given index of '{', extract balanced contents inside braces and ending index.

    Returns (inner_content, closing_pos).
    """
    if open_pos >= len(tex) or tex[open_pos] != "{":
        raise ValueError(f"Expected '{{' at position {open_pos}")

    depth = 0
    start_content = open_pos + 1
    for i in range(open_pos, len(tex)):
        char = tex[i]
        # Ignore escaped braces \{ and \}
        if char == "{" and (i == 0 or tex[i - 1] != "\\"):
            depth += 1
        elif char == "}" and (i == 0 or tex[i - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return tex[start_content:i], i

    # Unbalanced: take remainder of line or remainder of text
    end_line = tex.find("\n", start_content)
    if end_line == -1:
        end_line = len(tex)
    return tex[start_content:end_line], end_line


def extract_title(tex: str) -> str | None:
    """Extract paper title from \\title{...} command, supporting nested braces."""
    match = re.search(r"\\title\s*\{", tex)
    if not match:
        return None
    open_pos = match.end() - 1
    content, _ = extract_balanced_braces(tex, open_pos)
    # Basic cleanup of title text
    cleaned = re.sub(r"\\(?:textbf|textit|emph|text|sc|rm)\{([^}]+)\}", r"\1", content)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else None


def extract_document_body(tex: str) -> str:
    """Extract content between \\begin{document} and \\end{document}.

    If document environment is missing, returns the stripped TeX stream.
    """
    begin_match = re.search(r"\\begin\{document\}", tex)
    if begin_match:
        start_idx = begin_match.end()
    else:
        start_idx = 0

    end_match = re.search(r"\\end\{document\}", tex[start_idx:])
    if end_match:
        end_idx = start_idx + end_match.start()
    else:
        end_idx = len(tex)

    return tex[start_idx:end_idx]
