"""Tests for leaf parsing, tokenization, and tree integration (Bước 3)."""

import pytest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource
from arxiv_pipeline.hierarchy.leaf_parser import (
    clean_latex_formatting,
    normalize_block_formula,
    normalize_inline_math,
    tokenize_sentences,
)
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


def test_clean_latex_formatting():
    """Verify non-semantic commands are removed and styles are unwrapped."""
    raw = r"\centering \textbf{Bold Title} and \emph{Emphasized Text} with \vspace{10pt} and \midrule."
    cleaned = clean_latex_formatting(raw)
    assert r"\centering" not in cleaned
    assert r"\vspace" not in cleaned
    assert r"\midrule" not in cleaned
    assert "Bold Title" in cleaned
    assert "Emphasized Text" in cleaned


def test_normalize_inline_math():
    r"""\(...\) and \begin{math}...\end{math} are normalized to $...$."""
    raw = r"Let \( x > 0 \) and \begin{math} y < 1 \end{math} be variables."
    normalized = normalize_inline_math(raw)
    assert "$x > 0$" in normalized
    assert "$y < 1$" in normalized
    assert r"\(" not in normalized
    assert r"\begin{math}" not in normalized


def test_normalize_block_formula():
    """Verify $$...$$, \\[...\\], and align environments become \\begin{equation}...\\end{equation}."""
    eq_dollar = "$$ E = mc^2 $$"
    norm_dollar = normalize_block_formula(eq_dollar)
    assert norm_dollar.startswith(r"\begin{equation}")
    assert norm_dollar.endswith(r"\end{equation}")
    assert "E = mc^2" in norm_dollar

    eq_bracket = r"\[ a + b = c \]"
    norm_bracket = normalize_block_formula(eq_bracket)
    assert norm_bracket.startswith(r"\begin{equation}")
    assert "a + b = c" in norm_bracket

    eq_align = r"\begin{align} x &= 1 \\ y &= 2 \end{align}"
    norm_align = normalize_block_formula(eq_align)
    assert norm_align.startswith(r"\begin{equation}")
    assert "x &= 1" in norm_align


def test_sentence_tokenizer_negative_rules():
    """Tokenizer MUST NOT split inside math, citations, abbreviations, decimals, or URLs."""
    text = (
        r"According to Vaswani et al. in \cite{vaswani2017}, the accuracy reached 98.5\% at $p < 0.05$. "
        r"See Fig. 1 and Eq. 2 for details (i.e. the architecture). "
        r"The code is available at https://github.com/example/repo. "
        r"This concludes our introduction."
    )
    sentences = tokenize_sentences(text)
    # Expected exactly 4 sentences:
    # 1. According to Vaswani et al. in \cite{vaswani2017}, the accuracy reached 98.5\% at $p < 0.05$.
    # 2. See Fig. 1 and Eq. 2 for details (i.e. the architecture).
    # 3. The code is available at https://github.com/example/repo.
    # 4. This concludes our introduction.
    assert len(sentences) == 4
    assert "Vaswani et al." in sentences[0]
    assert r"\cite{vaswani2017}" in sentences[0]
    assert "98.5" in sentences[0]
    assert "$p < 0.05$" in sentences[0]

    assert "Fig. 1" in sentences[1]
    assert "Eq. 2" in sentences[1]
    assert "i.e." in sentences[1]

    assert "https://github.com/example/repo" in sentences[2]
    assert "This concludes our introduction." in sentences[3]


def test_sentence_tokenizer_extended_cite_commands():
    r"""Tokenizer MUST NOT split inside extended citation commands like \citep, \citet with pre/post notes."""
    text = (
        r"Recent advances in transformers \citep[see][p. 15]{vaswani2017} demonstrate strong scaling laws. "
        r"As argued by \citet[Theorem 2.1]{devlin2019}, pretraining provides good initialization. "
        r"This completes our analysis."
    )
    sentences = tokenize_sentences(text)
    assert len(sentences) == 3
    assert r"\citep[see][p. 15]{vaswani2017}" in sentences[0]
    assert r"\citet[Theorem 2.1]{devlin2019}" in sentences[1]
    assert "This completes our analysis." in sentences[2]


def test_full_tree_with_leaves_and_parent_relations():
    """Verify end-to-end integration: sections, itemize, sentences, formulas, and figures."""
    tex = r"""
    \documentclass{article}
    \title{Deep Learning Survey}
    \begin{document}
    This is an abstract preamble sentence before section one.

    \section{Introduction}
    Deep learning has revolutionized AI. We present three key contributions:
    \begin{itemize}
      \item First, we analyze transformer architectures. The model achieves state-of-the-art results.
      \item Second, we evaluate efficiency.
    \end{itemize}

    \section{Theoretical Formulation}
    We define the loss function below:
    $$
    \mathcal{L} = -\sum y_i \log(\hat{y}_i)
    $$
    This formulation guarantees convergence.

    \begin{figure}[htpb]
      \centering
      \caption{System Diagram}
    \end{figure}

    \end{document}
    """
    source: GatheredSource = {
        "arxiv_id": "2409.00010",
        "version": 1,
        "root_file": "main.tex",
        "compilation_path": ["main.tex"],
        "merged_tex": tex,
        "bib_files": [],
        "bbl_files": [],
        "status": "ok",
    }
    hierarchy = build_hierarchy(source)
    validate_version_hierarchy(hierarchy)

    nodes = hierarchy["nodes"]
    node_map = {n["temp_id"]: n for n in nodes}

    # 1. Preamble sentence attaches to root
    preamble_sents = [
        n for n in nodes if n["kind"] == "sentence" and n["parent_temp_id"] == "root"
    ]
    assert len(preamble_sents) == 1
    assert "abstract preamble sentence" in preamble_sents[0]["content"]

    # 2. Section: Introduction
    intro_nodes = [n for n in nodes if n["kind"] == "section" and n["content"] == "Introduction"]
    assert len(intro_nodes) == 1
    intro_id = intro_nodes[0]["temp_id"]

    # Sentences directly under Introduction
    intro_sents = [
        n for n in nodes if n["kind"] == "sentence" and n["parent_temp_id"] == intro_id
    ]
    assert any("Deep learning has revolutionized AI" in s["content"] for s in intro_sents)

    # 3. Itemize and items
    items = [n for n in nodes if n["kind"] == "item"]
    assert len(items) == 2

    # Sentences under item 1
    item1_sents = [
        n for n in nodes if n["kind"] == "sentence" and n["parent_temp_id"] == items[0]["temp_id"]
    ]
    assert len(item1_sents) == 2
    assert "analyze transformer architectures" in item1_sents[0]["content"]
    assert "state-of-the-art" in item1_sents[1]["content"]

    # 4. Block formula under Section 2
    sec2_nodes = [
        n for n in nodes if n["kind"] == "section" and "Theoretical Formulation" in n["content"]
    ]
    assert len(sec2_nodes) == 1
    sec2_id = sec2_nodes[0]["temp_id"]

    formulas = [n for n in nodes if n["kind"] == "formula"]
    assert len(formulas) == 1
    assert formulas[0]["parent_temp_id"] == sec2_id
    assert r"\begin{equation}" in formulas[0]["content"]
    assert r"\mathcal{L}" in formulas[0]["content"]

    # 5. Figure leaf under Section 2
    figures = [n for n in nodes if n["kind"] == "figure"]
    assert len(figures) == 1
    assert figures[0]["parent_temp_id"] == sec2_id
    assert "System Diagram" in figures[0]["content"]
    assert r"\centering" not in figures[0]["content"]  # Cleaned
    assert "[htpb]" not in figures[0]["content"]  # Cleaned
