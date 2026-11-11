"""LaTeX stream utilities for structural parsing."""

import re


def strip_comments(tex: str) -> str:
    """Remove LaTeX comments (% ...) while preserving escaped percent signs (\\%).

    Correctly handles comments preceded by an even number of backslashes (e.g. \\\\% comment).
    """
    output_lines: list[str] = []
    for line in tex.splitlines(keepends=True):
        idx = 0
        comment_pos = -1
        while True:
            pos = line.find("%", idx)
            if pos == -1:
                break
            # Count consecutive backslashes preceding pos
            bs_count = 0
            i = pos - 1
            while i >= 0 and line[i] == "\\":
                bs_count += 1
                i -= 1
            if bs_count % 2 == 0:
                # Even number of backslashes: this % is an active comment delimiter
                comment_pos = pos
                break
            # Odd number of backslashes: escaped percent (e.g. \%)
            idx = pos + 1

        if comment_pos != -1:
            newline = "\n" if line.endswith("\n") else ""
            output_lines.append(line[:comment_pos].rstrip(" \t") + newline)
        else:
            output_lines.append(line)

    return "".join(output_lines)


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


def unwrap_styling_balanced(text: str) -> str:
    """Unwrap font formatting commands (\\textbf, \\textit, etc.) handling nested braces."""
    macro_pattern = re.compile(
        r"\\(?:textbf|textit|emph|text|textsc|texttt|underline|mathbf|mathit)\s*\{"
    )
    result = text
    while True:
        match = macro_pattern.search(result)
        if not match:
            break
        start_macro = match.start()
        open_brace = match.end() - 1
        inner, close_brace = extract_balanced_braces(result, open_brace)
        # Replace the entire \macro{...} with inner content
        result = result[:start_macro] + inner + result[close_brace + 1 :]
    return result


def extract_title(tex: str) -> str | None:
    """Extract paper title from \\title[short]{long} command, supporting nested braces."""
    match = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{", tex)
    if not match:
        return None
    open_pos = match.end() - 1
    content, _ = extract_balanced_braces(tex, open_pos)
    # Basic cleanup of title text
    cleaned = unwrap_styling_balanced(content)
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
