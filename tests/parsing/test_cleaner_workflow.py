"""Exercise Module 2 cleanup through the default publication workflow."""

from arxiv_pipeline.parsing.runner import process_single_publication


def test_default_workflow_cleans_prose_and_figures(tmp_path):
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
    assert sentences == [
        r"Our method uses $\mathbf{x}$ and x[t].",
        "See Fig. 2 for details.",
    ]
    figure = next(e["content"] for e in elements if e["kind"] == "figure")
    assert "[htpb]" not in figure
    assert r"\centering" not in figure
    assert "x[t]" in figure
    formula = next(e["content"] for e in elements if e["kind"] == "formula")
    assert r"\mathbf{x}" in formula
    assert formula.startswith(r"\begin{equation}")
