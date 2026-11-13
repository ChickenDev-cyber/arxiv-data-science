r"""Leaf parsing, normalization, and tokenization utilities (Module 3).

Adheres strictly to the granularity and exclusion rules in parsing.md:
- Sentence tokenization: Leaf nodes for body prose, split on periods.
  Never splits inside mathematics ($...$), commands (\cite, \ref), abbreviations
  (i.e., et al., Fig. 1), decimal numbers, or URLs.
  Uses the shared Module 2 tokenizer without placeholder substitution.
- Math normalization:
  Inline math converted to $ ... $
  Block formulas (equation, align, gather, multline, eqnarray, flalign, alignat, displaymath)
  converted to \begin{equation} ... \end{equation}
- Figure/Table leaf handling:
  Treated as kind='figure' with non-semantic formatting ([htpb], \centering) stripped.
  Array notation like x[t] in prose is strictly preserved.
"""

from typing import Callable

from arxiv_pipeline.standardization import clean_latex, normalize_math, process_prose, tokenize_sentences

def clean_latex_formatting(text: str) -> str:
    """Clean prose using the shared Module 2 cleaner."""
    return clean_latex(text)


def clean_figure_formatting(text: str) -> str:
    """Clean figure/table layout without removing arbitrary bracketed text."""
    return clean_latex(text)


def normalize_inline_math(text: str) -> str:
    """Normalize math in a prose block using Module 2."""
    return normalize_math(text)


def normalize_block_formula(raw_formula: str) -> str:
    """Normalize a display formula using Module 2."""
    return normalize_math(raw_formula.strip())


def process_prose_block(
    text: str,
    custom_tokenizer: Callable[[str], list[str]] | None = None,
) -> list[str]:
    """Clean, normalize inline math, and split prose block into sentence leaves."""
    return process_prose(text, custom_tokenizer=custom_tokenizer)
