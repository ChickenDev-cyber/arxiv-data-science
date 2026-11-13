"""Exercise Module 2 cleanup through the default publication workflow."""

import tempfile
import unittest
from pathlib import Path
from arxiv_pipeline.parsing.runner import process_single_publication


class TestCleanerWorkflow(unittest.TestCase):
    def test_default_workflow_cleans_prose_and_figures(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            source_dir = tmp_path / "sources" / "v1"
            source_dir.mkdir(parents=True)
            (source_dir / "main.tex").write_text(
                r"""\documentclass{article}
\begin{document}
\section{Introduction}
\textbf{Our \emph{method}} uses $\mathbf{x}$ and x[t].
\newpage See Fig. 2 for details.
\begin{figure}[htpb]
\centering\caption{Keep x[t].}
\end{figure}
\[\mathbf{x} = 1\]
\end{document}
""",
                encoding="utf-8",
            )

            result = process_single_publication("2409.00001", tmp_path)
            elements = result["elements"]
            sentences = [e["content"] for e in elements if e["kind"] == "sentence"]
            self.assertEqual(
                sentences,
                [
                    r"Our method uses $\mathbf{x}$ and x[t].",
                    "See Fig. 2 for details.",
                ],
            )
            figure = next(e["content"] for e in elements if e["kind"] == "figure")
            self.assertNotIn("[htpb]", figure)
            self.assertNotIn(r"\centering", figure)
            self.assertIn("x[t]", figure)
            formula = next(e["content"] for e in elements if e["kind"] == "formula")
            self.assertIn(r"\mathbf{x}", formula)
            self.assertTrue(formula.startswith(r"\begin{equation}"))


if __name__ == "__main__":
    unittest.main()
