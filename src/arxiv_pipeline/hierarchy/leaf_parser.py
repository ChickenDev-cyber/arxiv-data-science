r"""Leaf parsing, normalization, and tokenization utilities (Module 3).

Adheres strictly to the granularity and exclusion rules in parsing.md:
- Sentence tokenization: Leaf nodes for body prose, split on periods.
  Never splits inside mathematics ($...$), commands (\cite, \ref), abbreviations
  (i.e., et al., Fig. 1), decimal numbers, or URLs.
- Math normalization:
  Inline math converted to $ ... $
  Block formulas converted to \begin{equation} ... \end{equation}
- Figure/Table leaf handling:
  Treated as kind='figure' with non-semantic formatting stripped.
"""

import re
from typing import Callable

# Abbreviation patterns that contain dots but do not terminate a sentence
ABBREVIATIONS = [
    r"\bi\.e\.",
    r"\be\.g\.",
    r"\bet al\.",
    r"\bet seq\.",
    r"\bFig\.",
    r"\bFigs\.",
    r"\bEq\.",
    r"\bEqs\.",
    r"\bDr\.",
    r"\bProf\.",
    r"\bvs\.",
    r"\bcf\.",
    r"\bvol\.",
    r"\bno\.",
    r"\bsec\.",
    r"\bapprox\.",
    r"\bviz\.",
    r"\bca\.",
    r"\bdept\.",
    r"\bal\.",
]

ABBR_PATTERN = re.compile("|".join(ABBREVIATIONS), re.IGNORECASE)

# Non-semantic formatting commands to remove
FORMATTING_CLEANUP_RES = [
    re.compile(r"\\(?:centering|raggedright|raggedleft|clearpage|newpage)\b"),
    re.compile(r"\\(?:vspace|hspace)\*?\{[^}]*\}"),
    re.compile(r"\[\s*[htpb!]+\s*\]"),  # [htpb] placement option
    re.compile(r"\\(?:midrule|toprule|bottomrule|hline)\b"),
    re.compile(r"\\(?:small|footnotesize|scriptsize|large|Large|huge|Huge|normalsize)\b"),
]

# Text unwrapping regexes (\textbf{abc} -> abc)
UNWRAP_RES = [
    re.compile(r"\\(?:textbf|textit|emph|text|textsc|texttt|underline)\{([^}]+)\}")
]


def clean_latex_formatting(text: str) -> str:
    """Remove non-semantic formatting commands and unwrap font styles."""
    cleaned = text
    for pattern in FORMATTING_CLEANUP_RES:
        cleaned = pattern.sub("", cleaned)
    for pattern in UNWRAP_RES:
        # Loop to handle nested unwrapping if any
        prev = ""
        while prev != cleaned:
            prev = cleaned
            cleaned = pattern.sub(r"\1", cleaned)
    return cleaned


def normalize_inline_math(text: str) -> str:
    """Normalize all inline mathematics to standard $...$ form.

    Converts \\( ... \\) and \\begin{math} ... \\end{math} to $ ... $.
    """
    # Convert \( ... \) -> $ ... $
    normalized = re.sub(r"\\\(\s*(.*?)\s*\\\)", r"$\1$", text, flags=re.DOTALL)
    # Convert \begin{math} ... \end{math} -> $ ... $
    normalized = re.sub(r"\\begin\{math\}\s*(.*?)\s*\\end\{math\}", r"$\1$", normalized, flags=re.DOTALL)
    return normalized


def normalize_block_formula(raw_formula: str) -> str:
    """Normalize block mathematics to standard \\begin{equation}...\\end{equation} form."""
    cleaned = raw_formula.strip()
    # Check $$ ... $$
    if cleaned.startswith("$$") and cleaned.endswith("$$"):
        inner = cleaned[2:-2].strip()
        return f"\\begin{{equation}}\n{inner}\n\\end{{equation}}"
    # Check \[ ... \]
    if cleaned.startswith(r"\[") and cleaned.endswith(r"\]"):
        inner = cleaned[2:-2].strip()
        return f"\\begin{{equation}}\n{inner}\n\\end{{equation}}"
    # Check \begin{equation*} ... \end{equation*}
    if cleaned.startswith(r"\begin{equation*}") and cleaned.endswith(r"\end{equation*}"):
        inner = cleaned[len(r"\begin{equation*}") : -len(r"\end{equation*}")].strip()
        return f"\\begin{{equation}}\n{inner}\n\\end{{equation}}"
    # Check \begin{align...} or \begin{gather...}
    match = re.match(r"\\begin\{(?:align|gather)\*?\}(.*?)\\end\{(?:align|gather)\*?\}", cleaned, re.DOTALL)
    if match:
        inner = match.group(1).strip()
        return f"\\begin{{equation}}\n{inner}\n\\end{{equation}}"
    # If already \begin{equation} ... \end{equation}
    return cleaned


def tokenize_sentences(text: str) -> list[str]:
    r"""Split prose text into sentence leaves using periods while respecting negative rules.

    DOES NOT split on:
    - Mathematics inside $ ... $
    - LaTeX commands (\cite{...}, \ref{...})
    - Abbreviations (i.e., et al., Fig. 1, etc.)
    - Decimal numbers (3.14, 0.05)
    - URLs and emails
    """
    if not text or not text.strip():
        return []

    # Temporary mask storage
    masks: dict[str, str] = {}
    mask_counter = 0

    def create_mask(content: str) -> str:
        nonlocal mask_counter
        placeholder = f"__TOKEN_MASK_{mask_counter}__"
        mask_counter += 1
        masks[placeholder] = content
        return placeholder

    working_text = text

    # 1. Mask inline math $...$
    def mask_math(m: re.Match[str]) -> str:
        return create_mask(m.group(0))

    working_text = re.sub(r"(?<!\\)\$.*?(?<!\\)\$", mask_math, working_text, flags=re.DOTALL)

    # 2. Mask LaTeX commands with braces like \cite{...}, \citep[see][p. 15]{...}, \ref{...}, \url{...}
    def mask_cmd(m: re.Match[str]) -> str:
        return create_mask(m.group(0))

    working_text = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])*(?:\{[^}]*\})+", mask_cmd, working_text)

    # 3. Mask URLs and emails (excluding trailing sentence-ending punctuation)
    def mask_url(m: re.Match[str]) -> str:
        return create_mask(m.group(0))

    url_email_pattern = re.compile(
        r"(?:https?://|www\.)[^\s]+?(?=[.,;:!?]?(?:\s|$))|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )
    working_text = url_email_pattern.sub(mask_url, working_text)

    # 4. Mask decimal numbers (e.g. 3.14, 0.005)
    def mask_decimal(m: re.Match[str]) -> str:
        return create_mask(m.group(0))

    working_text = re.sub(r"\b\d+\.\d+\b", mask_decimal, working_text)

    # 5. Mask abbreviations (e.g. i.e., et al., Fig.)
    def mask_abbr(m: re.Match[str]) -> str:
        return create_mask(m.group(0))

    working_text = ABBR_PATTERN.sub(mask_abbr, working_text)

    # Now split on sentence-ending punctuation: period, question mark, exclamation
    # followed by whitespace or end of string
    raw_sentences = re.split(r"(?<=[.!?])(?:\s+|\n+)", working_text)

    # Restore masks in each sentence
    restored_sentences: list[str] = []
    for raw in raw_sentences:
        s = raw.strip()
        if not s:
            continue
        # Restore masks in reverse order
        for placeholder, original in masks.items():
            if placeholder in s:
                s = s.replace(placeholder, original)
        # Final cleanup of excessive internal spaces
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            restored_sentences.append(s)

    return restored_sentences


def process_prose_block(
    text: str,
    custom_tokenizer: Callable[[str], list[str]] | None = None,
) -> list[str]:
    """Clean, normalize inline math, and split prose block into sentence leaves."""
    cleaned = clean_latex_formatting(text)
    normalized = normalize_inline_math(cleaned)
    tokenizer = custom_tokenizer or tokenize_sentences
    return tokenizer(normalized)
