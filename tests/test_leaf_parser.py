r"""Tests for leaf parsing, tokenization, and tree integration (Bước 3).

Compatible with both standard unittest and pytest.
"""

import unittest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource
from arxiv_pipeline.hierarchy.leaf_parser import (
    clean_latex_formatting,
    normalize_block_formula,
    normalize_inline_math,
    tokenize_sentences,
)
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


class LeafParserTests(unittest.TestCase):
    def test_clean_latex_formatting(self):
        r"""Verify non-semantic commands are removed and styles are unwrapped."""
        raw = r"\centering \textbf{Bold Title} and \emph{Emphasized Text} with \vspace{10pt} and \midrule."
        cleaned = clean_latex_formatting(raw)
        self.assertNotIn(r"\centering", cleaned)
        self.assertNotIn(r"\vspace", cleaned)
        self.assertNotIn(r"\midrule", cleaned)
        self.assertIn("Bold Title", cleaned)
        self.assertIn("Emphasized Text", cleaned)

    def test_normalize_inline_math(self):
        r"""\(...\) and \begin{math}...\end{math} are normalized to $...$."""
        raw = r"Let \( x > 0 \) and \begin{math} y < 1 \end{math} be variables."
        normalized = normalize_inline_math(raw)
        self.assertIn("$x > 0$", normalized)
        self.assertIn("$y < 1$", normalized)
        self.assertNotIn(r"\(", normalized)
        self.assertNotIn(r"\begin{math}", normalized)

    def test_normalize_block_formula(self):
        r"""Verify $$...$$, \\[...\\], and align environments become \begin{equation}...\end{equation}."""
        eq_dollar = "$$ E = mc^2 $$"
        norm_dollar = normalize_block_formula(eq_dollar)
        self.assertTrue(norm_dollar.startswith(r"\begin{equation}"))
        self.assertTrue(norm_dollar.endswith(r"\end{equation}"))
        self.assertIn("E = mc^2", norm_dollar)

        eq_bracket = r"\[ a + b = c \]"
        norm_bracket = normalize_block_formula(eq_bracket)
        self.assertTrue(norm_bracket.startswith(r"\begin{equation}"))
        self.assertIn("a + b = c", norm_bracket)

        eq_align = r"\begin{align} x &= 1 \\ y &= 2 \end{align}"
        norm_align = normalize_block_formula(eq_align)
        self.assertTrue(norm_align.startswith(r"\begin{equation}"))
        self.assertIn("x &= 1", norm_align)

    def test_sentence_tokenizer_negative_rules(self):
        r"""Tokenizer MUST NOT split inside math, citations, abbreviations, decimals, or URLs."""
        text = (
            r"According to Vaswani et al. in \cite{vaswani2017}, the accuracy reached 98.5\% at $p < 0.05$. "
            r"See Fig. 1 and Eq. 2 for details (i.e. the architecture). "
            r"The code is available at https://github.com/example/repo. "
            r"This concludes our introduction."
        )
        sentences = tokenize_sentences(text)
        self.assertEqual(len(sentences), 4)
        self.assertIn("Vaswani et al.", sentences[0])
        self.assertIn(r"\cite{vaswani2017}", sentences[0])
        self.assertIn("98.5", sentences[0])
        self.assertIn("$p < 0.05$", sentences[0])

        self.assertIn("Fig. 1", sentences[1])
        self.assertIn("Eq. 2", sentences[1])
        self.assertIn("i.e.", sentences[1])

        self.assertIn("https://github.com/example/repo", sentences[2])
        self.assertIn("This concludes our introduction.", sentences[3])

    def test_sentence_tokenizer_extended_cite_commands(self):
        r"""Tokenizer MUST NOT split inside extended citation commands like \citep, \citet with pre/post notes."""
        text = (
            r"Recent advances in transformers \citep[see][p. 15]{vaswani2017} demonstrate strong scaling laws. "
            r"As argued by \citet[Theorem 2.1]{devlin2019}, pretraining provides good initialization. "
            r"This completes our analysis."
        )
        sentences = tokenize_sentences(text)
        self.assertEqual(len(sentences), 3)
        self.assertIn(r"\citep[see][p. 15]{vaswani2017}", sentences[0])
        self.assertIn(r"\citet[Theorem 2.1]{devlin2019}", sentences[1])
        self.assertIn("This completes our analysis.", sentences[2])

    def test_full_tree_with_leaves_and_parent_relations(self):
        r"""Verify end-to-end integration: sections, itemize, sentences, formulas, and figures."""
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

        # 1. Preamble sentence attaches to root
        preamble_sents = [
            n for n in nodes if n["kind"] == "sentence" and n["parent_temp_id"] == "root"
        ]
        self.assertEqual(len(preamble_sents), 1)
        self.assertIn("abstract preamble sentence", preamble_sents[0]["content"])

        # 2. Section: Introduction
        intro_nodes = [n for n in nodes if n["kind"] == "section" and n["content"] == "Introduction"]
        self.assertEqual(len(intro_nodes), 1)
        intro_id = intro_nodes[0]["temp_id"]

        # Sentences directly under Introduction
        intro_sents = [
            n for n in nodes if n["kind"] == "sentence" and n["parent_temp_id"] == intro_id
        ]
        self.assertTrue(any("Deep learning has revolutionized AI" in s["content"] for s in intro_sents))

        # 3. Itemize and items
        items = [n for n in nodes if n["kind"] == "item"]
        self.assertEqual(len(items), 2)

        # Sentences under item 1
        item1_sents = [
            n for n in nodes if n["kind"] == "sentence" and n["parent_temp_id"] == items[0]["temp_id"]
        ]
        self.assertEqual(len(item1_sents), 2)
        self.assertIn("analyze transformer architectures", item1_sents[0]["content"])
        self.assertIn("state-of-the-art", item1_sents[1]["content"])

        # 4. Block formula under Section 2
        sec2_nodes = [
            n for n in nodes if n["kind"] == "section" and "Theoretical Formulation" in n["content"]
        ]
        self.assertEqual(len(sec2_nodes), 1)
        sec2_id = sec2_nodes[0]["temp_id"]

        formulas = [n for n in nodes if n["kind"] == "formula"]
        self.assertEqual(len(formulas), 1)
        self.assertEqual(formulas[0]["parent_temp_id"], sec2_id)
        self.assertIn(r"\begin{equation}", formulas[0]["content"])
        self.assertIn(r"\mathcal{L}", formulas[0]["content"])

        # 5. Figure leaf under Section 2
        figures = [n for n in nodes if n["kind"] == "figure"]
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0]["parent_temp_id"], sec2_id)
        self.assertIn("System Diagram", figures[0]["content"])
        self.assertNotIn(r"\centering", figures[0]["content"])  # Cleaned
        self.assertNotIn("[htpb]", figures[0]["content"])  # Cleaned


if __name__ == "__main__":
    unittest.main()
