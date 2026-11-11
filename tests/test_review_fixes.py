r"""Unit tests for all issues identified in the team leader's PR #27 review.

Compatible with both standard unittest and pytest.
Covers:
1. Zero __TOKEN_MASK_*__ leakage when math is nested inside commands/footnotes (paper 2503.06914)
2. No false-positive display math ($$) on adjacent inline math (paper 2503.07052)
3. No false-positive bracket math (\[) on newline with vertical space (paper 2503.06969)
4. Preservation of array notation like x[t], dp[p] in body prose while stripping [htpb] on figures
5. Parsing of \section[short]{long} and \title[short]{long}
6. Filtering of metadata and BibTeX commands (\maketitle, \bibliographystyle, \addbibresource)
7. Normalization of extended math environments (multline, eqnarray, flalign, displaymath)
8. Correct comment stripping after even backslashes (\\% is a comment, \% is escaped, paper 2503.06937)
9. Unwrapping of font styles with nested braces (\textbf{f({x}_k)})
10. Stack resilience against orphaned \end{itemize}
"""

import unittest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource
from arxiv_pipeline.hierarchy.latex_utils import (
    extract_title,
    strip_comments,
    unwrap_styling_balanced,
)
from arxiv_pipeline.hierarchy.leaf_parser import (
    clean_figure_formatting,
    clean_latex_formatting,
    normalize_block_formula,
    tokenize_sentences,
)
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


def _make_source(arxiv_id: str, tex: str) -> GatheredSource:
    return {
        "arxiv_id": arxiv_id,
        "version": 1,
        "root_file": "main.tex",
        "compilation_path": ["main.tex"],
        "merged_tex": tex,
        "bib_files": [],
        "bbl_files": [],
        "status": "ok",
    }


class ReviewFixesTests(unittest.TestCase):
    def test_no_token_mask_leakage_nested(self):
        r"""Issue 1: Nested masks (math inside commands/footnotes/styles) must unnest completely."""
        text = (
            r"We study the model \footnote{See details for $p < 0.05$ and $N=100$}. "
            r"The accuracy in \textbf{Table \ref{tab:1} ($d=5$)} reaches 95%. "
            r"Contact author at user@example.com."
        )
        sentences = tokenize_sentences(text)
        self.assertGreaterEqual(len(sentences), 2)
        for s in sentences:
            self.assertNotIn("__TOKEN_MASK_", s, f"Leaked token mask found in: {s}")
        self.assertIn("$p < 0.05$", sentences[0])
        self.assertIn("$d=5$", sentences[1])

    def test_adjacent_inline_math_not_swallowed(self):
        r"""Issue 2a: Adjacent inline math like 0.7$''$$-$1.0$''$ must not trigger $$ formula swallowing sections."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Measurements}
        Observed angular separation was 0.7$^{\prime\prime}$$-$1.0$^{\prime\prime}$ in our field.
        This was confirmed by subsequent observations.
        \section{Conclusion}
        We summarize our findings.
        \end{document}
        """
        hierarchy = build_hierarchy(_make_source("2503.07052", tex))
        validate_version_hierarchy(hierarchy)

        sections = [n for n in hierarchy["nodes"] if n["kind"] == "section"]
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["content"], "Measurements")
        self.assertEqual(sections[1]["content"], "Conclusion")

        formulas = [n for n in hierarchy["nodes"] if n["kind"] == "formula"]
        self.assertEqual(len(formulas), 0)

    def test_newline_with_space_not_swallowed(self):
        r"""Issue 2b: Newline with vertical space \\[6pt] must not trigger display math \\[...\\]."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Table Formatting}
        First row of table content. \\[6pt]
        Second row of table content. \\[1em]
        \section{Mathematical Analysis}
        Here is an actual display equation:
        \[
          x + y = z
        \]
        This concludes the proof.
        \end{document}
        """
        hierarchy = build_hierarchy(_make_source("2503.06969", tex))
        validate_version_hierarchy(hierarchy)

        sections = [n for n in hierarchy["nodes"] if n["kind"] == "section"]
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]["content"], "Table Formatting")
        self.assertEqual(sections[1]["content"], "Mathematical Analysis")

        formulas = [n for n in hierarchy["nodes"] if n["kind"] == "formula"]
        self.assertEqual(len(formulas), 1)
        self.assertIn("x + y = z", formulas[0]["content"])

    def test_array_notation_preserved_in_prose(self):
        r"""Issue 3: Array indexing like x[t], dp[p], state[h], v[b] must be preserved in prose."""
        prose = "The state vector x[t] and dynamic table dp[p] satisfy recurrence for v[b]."
        cleaned = clean_latex_formatting(prose)
        self.assertIn("x[t]", cleaned)
        self.assertIn("dp[p]", cleaned)
        self.assertIn("v[b]", cleaned)

        # Figure formatting STILL removes placement option [htpb]
        fig_text = r"\begin{figure}[htpb] \centering \caption{Test} \end{figure}"
        cleaned_fig = clean_figure_formatting(fig_text)
        self.assertNotIn("[htpb]", cleaned_fig)
        self.assertNotIn(r"\centering", cleaned_fig)

    def test_section_and_title_with_optional_brackets(self):
        r"""Issue 4: \section[short]{long} and \title[short]{long} must be recognized."""
        tex = r"""
        \documentclass{article}
        \title[Short AI]{A Comprehensive Long Title on Artificial Intelligence}
        \begin{document}
        \section[Intro]{Introduction and Problem Statement}
        We begin our exploration.
        \subsection[Model]{Detailed Mathematical Model Architecture}
        Here is the architecture.
        \end{document}
        """
        hierarchy = build_hierarchy(_make_source("2503.99999", tex))
        validate_version_hierarchy(hierarchy)

        nodes = hierarchy["nodes"]
        self.assertIn("A Comprehensive Long Title on Artificial Intelligence", nodes[0]["content"])

        sections = [n for n in nodes if n["kind"] == "section"]
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["content"], "Introduction and Problem Statement")

        subsections = [n for n in nodes if n["kind"] == "subsection"]
        self.assertEqual(len(subsections), 1)
        self.assertEqual(subsections[0]["content"], "Detailed Mathematical Model Architecture")

    def test_bib_and_metadata_not_leaked_into_sentences(self):
        r"""Issue 5a: \maketitle, \bibliographystyle, \addbibresource must not leak as sentences."""
        tex = r"""
        \documentclass{article}
        \title{Sample Paper}
        \author{Author One}
        \date{\today}
        \begin{document}
        \maketitle
        \section{Introduction}
        This is body text.
        \bibliographystyle{plain}
        \addbibresource{sample.bib}
        \printbibliography
        \end{document}
        """
        hierarchy = build_hierarchy(_make_source("2503.88888", tex))
        validate_version_hierarchy(hierarchy)

        sentences = [n["content"] for n in hierarchy["nodes"] if n["kind"] == "sentence"]
        for s in sentences:
            self.assertNotIn(r"\maketitle", s)
            self.assertNotIn(r"\bibliographystyle", s)
            self.assertNotIn(r"\addbibresource", s)
            self.assertNotIn(r"\printbibliography", s)
            self.assertNotIn(r"\author", s)

    def test_extended_block_math_environments(self):
        r"""Issue 5b: multline, eqnarray, flalign, displaymath must normalize to equation."""
        for env in ["multline", "eqnarray", "flalign", "alignat", "displaymath"]:
            raw = f"\\begin{{{env}}}\n a = b + c \n\\end{{{env}}}"
            norm = normalize_block_formula(raw)
            self.assertTrue(norm.startswith(r"\begin{equation}"), f"Failed on env: {env}")
            self.assertIn("a = b + c", norm)

    def test_comment_after_even_backslashes(self):
        r"""Issue 6a: \\% must be treated as newline + comment, while \% is escaped percent."""
        tex = (
            "Line 1 \\\\% This is a real comment\n"
            "Line 2 with 50\\% discount % Inline comment\n"
        )
        cleaned = strip_comments(tex)
        self.assertIn("Line 1 \\\\", cleaned)
        self.assertNotIn("This is a real comment", cleaned)
        self.assertIn("50\\% discount", cleaned)
        self.assertNotIn("Inline comment", cleaned)

    def test_nested_braces_in_styling(self):
        r"""Issue 6b: \textbf{f({x}_k)} with inner braces must unwrap completely without truncation."""
        text = r"Let \textbf{the function $f({x}_k)$} be strictly convex."
        unwrapped = unwrap_styling_balanced(text)
        self.assertNotIn(r"\textbf", unwrapped)
        self.assertIn("the function $f({x}_k)$", unwrapped)

    def test_orphan_end_itemize_does_not_collapse_stack(self):
        r"""Issue 6c: An orphan \end{itemize} must not pop active sections back to root."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Section One}
        Some text.
        \end{itemize}
        \subsection{Subsection One}
        Subsection text.
        \end{document}
        """
        hierarchy = build_hierarchy(_make_source("2503.77777", tex))
        validate_version_hierarchy(hierarchy)

        subsections = [n for n in hierarchy["nodes"] if n["kind"] == "subsection"]
        self.assertEqual(len(subsections), 1)
        sec_one = [n for n in hierarchy["nodes"] if n["kind"] == "section"][0]
        self.assertEqual(subsections[0]["parent_temp_id"], sec_one["temp_id"])


if __name__ == "__main__":
    unittest.main()
