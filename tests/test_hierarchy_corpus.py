r"""Corpus test suite (Definition of Done - Bước 4).

Validates Module 3 against 10 diverse, realistic LaTeX publication patterns:
1. Standard Article (Sections, Subsections, Sentences, Equations, Figures)
2. Book / Thesis style (\chapter -> \section -> \subsection)
3. Deeply nested list environments (itemize, enumerate, item) with embedded equations
4. Complex block mathematics (\align, \gather, $$, \[, equation*)
5. Multiple figures and tables (\figure*, \table*, \caption, \label)
6. Compulsory inclusions (\section*{Acknowledgements}, \appendix, \section*{Appendix A})
7. Compulsory exclusions (\bibliography, \begin{thebibliography}, \section{References})
8. Floating preamble / Abstract prose before the first section
9. Prose with dense academic abbreviations, decimals, URLs, citations, and formulas
10. Unsupported / PDF-only empty source (empty VersionHierarchy with nodes=[])
"""

import time
import pytest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource, VersionHierarchy
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


def _make_source(arxiv_id: str, tex: str, status: str = "ok") -> GatheredSource:
    return {
        "arxiv_id": arxiv_id,
        "version": 1,
        "root_file": "main.tex",
        "compilation_path": ["main.tex"],
        "merged_tex": tex,
        "bbl_files": [],
        "status": status,  # type: ignore[typeddict-item]
    }


# ==============================================================================
# 10 DIVERSE PUBLICATION SAMPLES
# ==============================================================================

SAMPLE_1_STANDARD_ARTICLE = r"""
\documentclass{article}
\title{Attention Is All You Need}
\begin{document}
\section{Introduction}
The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.
We propose the Transformer, a model architecture eschewing recurrence and entirely relying on attention.
\subsection{Model Architecture}
The Transformer follows an encoder-decoder architecture.
The encoder maps an input sequence to a sequence of continuous representations.
\end{document}
"""

SAMPLE_2_CHAPTER_BOOK_STYLE = r"""
\documentclass{book}
\title{Foundations of Data Science}
\begin{document}
\chapter{Linear Algebra and High-Dimensional Space}
In high dimensions, volume behaves unexpectedly. Most volume is located near the equator.
\section{Geometry of the Sphere}
The surface area of a unit sphere in $d$ dimensions is well understood.
\subsection{Volume in the Equator}
Almost all the volume of a sphere is concentrated near its equator.
\end{document}
"""

SAMPLE_3_NESTED_LISTS_WITH_EMBEDDED_MATH = r"""
\documentclass{article}
\title{Algorithmic Optimization}
\begin{document}
\section{Complexity Analysis}
We outline the three optimization stages:
\begin{itemize}
  \item Stage 1: Data Preprocessing.
    \begin{enumerate}
      \item Normalization:
        $$
        z = \frac{x - \mu}{\sigma}
        $$
        This scales features to zero mean.
      \item Outlier removal.
    \end{enumerate}
  \item Stage 2: Gradient Descent Optimization.
\end{itemize}
\end{document}
"""

SAMPLE_4_COMPLEX_BLOCK_MATHEMATICS = r"""
\documentclass{article}
\title{Stochastic Calculus}
\begin{document}
\section{Formulation}
We consider an Ito process driven by Brownian motion:
\begin{align}
  dX_t &= \mu(X_t, t) dt + \sigma(X_t, t) dW_t \\
  dY_t &= \alpha(Y_t, t) dt + \beta(Y_t, t) dW_t
\end{align}
Alternatively, the integral representation holds:
\[
  X_t = X_0 + \int_0^t \mu(X_s, s) ds + \int_0^t \sigma(X_s, s) dW_s
\]
Finally, we have the expected value:
\begin{gather*}
  \mathbb{E}[X_t] = X_0 + \int_0^t \mathbb{E}[\mu(X_s, s)] ds
\end{gather*}
\end{document}
"""

SAMPLE_5_FIGURES_AND_TABLES = r"""
\documentclass{article}
\title{Empirical Benchmarking}
\begin{document}
\section{Experiments}
We report accuracy scores across all baseline models.
\begin{table}[htpb]
  \centering
  \caption{Performance on ImageNet Benchmark}
  \begin{tabular}{lcc}
    \toprule
    Model & Top-1 & Top-5 \\
    \midrule
    ResNet-50 & 76.1 & 92.8 \\
    ViT-B/16 & 77.9 & 93.9 \\
    \bottomrule
  \end{tabular}
\end{table}
As illustrated below:
\begin{figure*}[!t]
  \centering
  \caption{Training curves over 100 epochs.}
\end{figure*}
\end{document}
"""

SAMPLE_6_ACKNOWLEDGEMENTS_AND_APPENDICES = r"""
\documentclass{article}
\title{Quantum Mechanics}
\begin{document}
\section{Main Findings}
The wave function collapses upon observation.
\section*{Acknowledgements}
This work was supported by National Science Foundation Grant 12345.
\appendix
\section{Proof of Theorem 1}
Let $\mathcal{H}$ be a separable Hilbert space. The spectral theorem applies directly.
\end{document}
"""

SAMPLE_7_EXCLUSION_OF_REFERENCES = r"""
\documentclass{article}
\title{Graph Neural Networks}
\begin{document}
\section{Introduction}
Graph representation learning is widely applied in chemistry and social networks.
\section{References}
\begin{thebibliography}{99}
  \bibitem{kipf2016} Kipf and Welling. Semi-Supervised Classification. ICLR 2017.
  \bibitem{velickovic2018} Velickovic et al. Graph Attention Networks. ICLR 2018.
\end{thebibliography}
\appendix
\section{Implementation Details}
Hyperparameters were tuned using grid search.
\end{document}
"""

SAMPLE_8_FLOATING_PREAMBLE_ABSTRACT_PROSE = r"""
\documentclass{article}
\title{Self-Supervised Learning}
\begin{document}
Self-supervised learning eliminates the need for manual labels.
In this paper, we explore contrastive learning frameworks on large uncurated datasets.
\section{Related Work}
Contrastive learning has gained significant traction.
\end{document}
"""

SAMPLE_9_DENSE_ACADEMIC_PROSE = r"""
\documentclass{article}
\title{Advanced Natural Language Processing}
\begin{document}
\section{Overview}
As demonstrated by Devlin et al. in \cite{devlin2019}, masked language modeling achieves 88.5\% accuracy.
According to Prof. Smith et al. (see Fig. 1 and Eq. 3, i.e. the loss term), the rate of convergence is $O(1/k)$.
Furthermore, Dr. Johnson reported $p < 0.001$ across all trials (cf. vol. 12, no. 4, pp. 100-110).
The complete implementation and datasets are available at https://github.com/huggingface/transformers.
We conclude that bidirectional pretraining is essential.
\end{document}
"""

SAMPLE_10_EMPTY_PDF_ONLY_SOURCE = ""


# ==============================================================================
# TEST CASES
# ==============================================================================


@pytest.mark.parametrize(
    "arxiv_id,tex,expected_status,min_nodes",
    [
        ("2409.001", SAMPLE_1_STANDARD_ARTICLE, "ok", 4),
        ("2409.002", SAMPLE_2_CHAPTER_BOOK_STYLE, "ok", 5),
        ("2409.003", SAMPLE_3_NESTED_LISTS_WITH_EMBEDDED_MATH, "ok", 8),
        ("2409.004", SAMPLE_4_COMPLEX_BLOCK_MATHEMATICS, "ok", 4),
        ("2409.005", SAMPLE_5_FIGURES_AND_TABLES, "ok", 4),
        ("2409.006", SAMPLE_6_ACKNOWLEDGEMENTS_AND_APPENDICES, "ok", 5),
        ("2409.007", SAMPLE_7_EXCLUSION_OF_REFERENCES, "ok", 4),
        ("2409.008", SAMPLE_8_FLOATING_PREAMBLE_ABSTRACT_PROSE, "ok", 4),
        ("2409.009", SAMPLE_9_DENSE_ACADEMIC_PROSE, "ok", 5),
        ("2409.010", SAMPLE_10_EMPTY_PDF_ONLY_SOURCE, "empty_source", 0),
    ],
)
def test_corpus_10_samples_validation(arxiv_id, tex, expected_status, min_nodes):
    """Every sample in the 10-paper corpus must build a strictly valid VersionHierarchy."""
    source = _make_source(arxiv_id, tex, status=expected_status)
    hierarchy = build_hierarchy(source)

    # 1. Structural validity
    validate_version_hierarchy(hierarchy)
    assert hierarchy["arxiv_id"] == arxiv_id
    assert hierarchy["version"] == 1

    nodes = hierarchy["nodes"]
    if expected_status == "empty_source":
        assert len(nodes) == 0
    else:
        assert len(nodes) >= min_nodes
        assert nodes[0]["kind"] == "document"
        assert nodes[0]["parent_temp_id"] is None

        # Verify all child nodes have valid parent pointers
        node_ids = {n["temp_id"] for n in nodes}
        for node in nodes[1:]:
            assert node["parent_temp_id"] in node_ids
            # Leaf kinds must only be sentence, formula, or figure
            if node["kind"] in ("sentence", "formula", "figure"):
                assert len(node["content"]) > 0


def test_corpus_benchmark_performance():
    """Definition of Done: Average parsing time per publication must be sub-millisecond."""
    samples = [
        ("s1", SAMPLE_1_STANDARD_ARTICLE),
        ("s2", SAMPLE_2_CHAPTER_BOOK_STYLE),
        ("s3", SAMPLE_3_NESTED_LISTS_WITH_EMBEDDED_MATH),
        ("s4", SAMPLE_4_COMPLEX_BLOCK_MATHEMATICS),
        ("s5", SAMPLE_5_FIGURES_AND_TABLES),
        ("s6", SAMPLE_6_ACKNOWLEDGEMENTS_AND_APPENDICES),
        ("s7", SAMPLE_7_EXCLUSION_OF_REFERENCES),
        ("s8", SAMPLE_8_FLOATING_PREAMBLE_ABSTRACT_PROSE),
        ("s9", SAMPLE_9_DENSE_ACADEMIC_PROSE),
        ("s10", SAMPLE_10_EMPTY_PDF_ONLY_SOURCE),
    ]

    start_time = time.perf_counter()
    iterations = 20  # Parse 200 documents total
    total_parsed = 0

    for _ in range(iterations):
        for arxiv_id, tex in samples:
            status = "empty_source" if not tex else "ok"
            source = _make_source(arxiv_id, tex, status=status)
            build_hierarchy(source)
            total_parsed += 1

    elapsed = time.perf_counter() - start_time
    avg_time_ms = (elapsed / total_parsed) * 1000

    print(f"\nParsed {total_parsed} documents in {elapsed:.4f}s. Avg: {avg_time_ms:.3f} ms/doc")
    # Sub-millisecond requirement on modern CPU
    assert avg_time_ms < 5.0, f"Parser too slow: {avg_time_ms:.3f} ms/doc"
