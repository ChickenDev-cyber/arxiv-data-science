"""Tái hiện lỗi abstract, caption, proof/label và vùng comment trên corpus."""

import unittest
from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.hierarchy.latex_utils import strip_comment_environments, strip_prose_markup
from arxiv_pipeline.standardization import clean_latex


def parse(body):
    return build_hierarchy({
        "arxiv_id": "2506.20794", "version": 1, "status": "ok",
        "root_file": "main.tex", "compilation_path": ["main.tex"],
        "bib_files": [], "bbl_files": [],
        "merged_tex": r"\documentclass{article}\begin{document}" + body + r"\end{document}",
    })["nodes"]


def prose(nodes):
    return [n["content"] for n in nodes if n["kind"] == "sentence"]


class TestProseStructureFixes(unittest.TestCase):
    def test_comment_removed_before_structure_scan(self):
        nodes = parse(r"Before.\begin{comment}\section{Hidden}\item Secret.\end{comment}After.")
        self.assertEqual(prose(nodes), ["Before.", "After."])
        self.assertFalse(any(n["kind"] in {"section", "item"} for n in nodes))

    def test_repeated_visible_text_survives_comment_removal(self):
        nodes = parse(r"Visible text.\begin{comment}Visible text.\[hidden=1\]\end{comment}")
        self.assertEqual(prose(nodes), ["Visible text."])
        self.assertFalse(any(n["kind"] == "formula" for n in nodes))

    def test_unclosed_comment_reports_and_omits_tail(self):
        with self.assertLogs("arxiv_pipeline.hierarchy.latex_utils", level="WARNING") as cm:
            self.assertEqual(prose(parse(r"Before.\begin{comment}Secret.")), ["Before."])
            self.assertTrue(any("Unclosed comment" in msg for msg in cm.output))

    def test_comment_examples_inside_literal_are_preserved(self):
        raw = r"\verb|\begin{comment}| \begin{verbatim}\begin{comment}\end{verbatim}"
        self.assertEqual(strip_comment_environments(raw), raw)
        self.assertEqual(strip_comment_environments(r"A\\begin{comment}B"), r"A\\begin{comment}B")

    def test_commented_out_comment_opener_is_not_active(self):
        self.assertEqual(prose(parse("Before.\n% \\begin{comment}\nAfter.")), ["Before.", "After."])

    def test_abstract_prose_is_split(self):
        bodies = [
            r"\abstract{First. \textbf{Second.}}",
            r"\abstract{First.}{Second.}{}{}{Third.}",
            r"\begin{abstract}First. Second.\end{abstract}",
        ]
        for body in bodies:
            with self.subTest(body=body):
                expected = ["First.", "Second."]
                if "Third." in body:
                    expected.append("Third.")
                self.assertEqual(prose(parse(body)), expected)

    def test_abstract_preserves_math_structure_and_citations(self):
        nodes = parse(r"\abstract{Use $\mathbf{x}$ and \cite{k}.\[x=1\]Next.}\section{Body}Done.")
        self.assertEqual(prose(nodes), [r"Use $\mathbf{x}$ and \cite{k}.", "Next.", "Done."])
        self.assertEqual(sum(n["kind"] == "formula" for n in nodes), 1)
        self.assertEqual(sum(n["kind"] == "section" for n in nodes), 1)

    def test_caption_cleaned_without_splitting_figure(self):
        raw = r"\begin{figure}\caption*[\emph{Short}]{\textbf{Main \emph{result}.} Use $\mathbf{x}$ and \cite{k}.}\end{figure}"
        cleaned = clean_latex(raw)
        self.assertIn(r"\caption*[Short]{Main result. Use $\mathbf{x}$ and \cite{k}.}", cleaned)
        self.assertEqual(clean_latex(cleaned), cleaned)
        nodes = parse(raw)
        self.assertFalse(prose(nodes))
        self.assertEqual(sum(n["kind"] == "figure" for n in nodes), 1)

    def test_unknown_command_and_malformed_caption_preserved(self):
        for raw in [r"\unknown{\textbf{Keep}}", r"\caption[unfinished", r"\caption{unfinished"]:
            with self.subTest(raw=raw):
                self.assertEqual(clean_latex(raw), raw)

    def test_proof_and_label_removed_without_breaking_sentence(self):
        nodes = parse(r"\begin{proof}[Proof of Lemma 2]The\label{p} result follows.\end{proof} Next.")
        self.assertEqual(prose(nodes), ["The result follows.", "Next."])

    def test_semantic_commands_and_math_labels_preserved(self):
        raw = r"Use $x\label{eq}$ and \ref{eq} and \cite{k}. \unknown{\label{keep}}"
        self.assertEqual(strip_prose_markup(raw), raw)

    def test_algorithm_and_unknown_environments_are_not_deleted(self):
        raw = r"\begin{algorithm}\State Work.\end{algorithm}\begin{custom}Text.\end{custom}"
        self.assertEqual(strip_prose_markup(raw), raw)


if __name__ == "__main__":
    unittest.main()
