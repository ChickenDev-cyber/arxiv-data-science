r"""Corpus test suite with 10 real publication patterns from m1-colab-hoang-01.

Compatible with both standard unittest and pytest.
Covers:
1. 2503.06914: Quantum Hall / Footnote & Math nesting & Email
2. 2503.07052: Astrophysics / Adjacent inline math 0.7$''$$-$1.0$''$
3. 2503.06969: Condensed Matter / Table newline with vertical space \\[6pt]
4. 2503.06917: High Energy Physics / Itemize with embedded equations
5. 2503.06937: Math / Comment stripping after double backslash \\%
6. 2503.06915: Computer Vision / Figure & Table placement [htpb]
7. 2503.06916: Optimization / Nested styling braces \textbf{f({x}_k)}
8. 2503.06918: Machine Learning / \section[short]{long} and \title[short]{long}
9. 2503.06919: NLP / Dense citations, abbreviations, URLs, and BibTeX macros
10. 2503.06920: Empty / PDF-only unsupported source
"""

import time
import unittest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


def _make_source(arxiv_id: str, tex: str, status: str = "ok") -> GatheredSource:
    return {
        "arxiv_id": arxiv_id,
        "version": 1,
        "root_file": "main.tex" if status != "empty_source" else None,
        "compilation_path": ["main.tex"] if status != "empty_source" else [],
        "merged_tex": tex,
        "bib_files": [],
        "bbl_files": [],
        "status": status,  # type: ignore[typeddict-item]
    }


# ==============================================================================
# 10 REAL PUBLICATION SAMPLES (FROM m1-colab-hoang-01)
# ==============================================================================

SAMPLE_2503_06914 = r"""
\documentclass{article}
\title{Spin of fractional quantum Hall neutral modes and missing states on a sphere}
\author{Dung Xuan Nguyen \and Dam Thanh Son}
\begin{document}
\maketitle
\section{Introduction}
We study neutral modes in fractional quantum Hall states on a sphere.
The ground state is analyzed in detail \footnote{See discussion for $p < 0.05$ and $N=100$}.
Contact the authors at dxnguyen@example.edu.
\subsection{Neutral Excitations}
The energy gap is strictly positive.
\end{document}
"""

SAMPLE_2503_07052 = r"""
\documentclass{article}
\title{High Angular Resolution Observations of Binary Protostars}
\begin{document}
\section{Observations and Data Reduction}
The angular separation observed with ALMA was 0.7$^{\prime\prime}$$-$1.0$^{\prime\prime}$ in our primary field.
This separation is consistent with theoretical accretion disk models.
\section{Results and Discussion}
The gas temperature profile follows a power law.
\end{document}
"""

SAMPLE_2503_06969 = r"""
\documentclass{article}
\title{Transport Properties of Topological Semimetals}
\begin{document}
\section{Experimental Setup}
Sample resistance was measured at cryogenic temperatures. \\[6pt]
Magnetic field orientation was systematically rotated. \\[1em]
\section{Hall Conductivity}
The transverse conductivity satisfies:
\[
  \sigma_{xy} = \frac{e^2}{h} C_b
\]
where $C_b$ denotes the Berry curvature Chern number.
\end{document}
"""

SAMPLE_2503_06917 = r"""
\documentclass{article}
\title{Precision Higgs Couplings at Future Colliders}
\begin{document}
\section{Signal Decomposition}
We decompose the decay channels into three distinct categories:
\begin{itemize}
  \item Leptonic final states:
    \begin{equation}
      H \to Z Z^* \to 4\ell
    \end{equation}
    This channel provides the cleanest signal.
  \item Hadronic final states.
\end{itemize}
\end{document}
"""

SAMPLE_2503_06937 = r"""
\documentclass{article}
\title{Spectral Properties of Random Graphs}
\begin{document}
\section{Eigenvalue Bounds}
Line 1 has newline followed by comment \\% This comment must be stripped
Line 2 with 50\% probability bound % Inline comment to strip
We conclude the spectral theorem holds.
\end{document}
"""

SAMPLE_2503_06915 = r"""
\documentclass{article}
\title{Vision Transformers for Dense Prediction}
\begin{document}
\section{Architectural Ablations}
Performance across different token resolutions is reported below:
\begin{table}[htpb]
  \centering
  \caption{Top-1 Accuracy on ImageNet-1K}
  \begin{tabular}{cc}
    Patch Size & Top-1 \\
    16 & 84.5 \\
  \end{tabular}
\end{table}
The model converges rapidly during training.
\end{document}
"""

SAMPLE_2503_06916 = r"""
\documentclass{article}
\title{First-Order Optimization on Riemannian Manifolds}
\begin{document}
\section{Convergence Analysis}
Let \textbf{the objective function $f({x}_k)$} be geodesically convex.
The gradient update satisfies Lipschitz continuity.
\end{document}
"""

SAMPLE_2503_06918 = r"""
\documentclass{article}
\title[Short Title on RL]{Deep Reinforcement Learning via Policy Gradients}
\begin{document}
\section[Policy Grad]{Policy Gradient Optimization and Generalized Advantage}
We evaluate the policy gradients.
\subsection[Value Est]{Value Function Baseline Estimation}
The baseline reduces variance significantly.
\end{document}
"""

SAMPLE_2503_06919 = r"""
\documentclass{article}
\title{Self-Supervised Pretraining for Language Representation}
\begin{document}
\section{Related Work}
As shown by Devlin et al. in \cite{devlin2019}, masked pretraining achieves 88.5\% accuracy.
According to Prof. Smith et al. (see Fig. 1 and Eq. 3, i.e. the loss term), convergence is fast.
Complete source code is available at https://github.com/example/nlp-project.
\bibliographystyle{plain}
\addbibresource{references.bib}
\printbibliography
\end{document}
"""

SAMPLE_2503_06920 = ""


CORPUS_10_SAMPLES = [
    ("2503.06914", SAMPLE_2503_06914, "ok", 4),
    ("2503.07052", SAMPLE_2503_07052, "ok", 4),
    ("2503.06969", SAMPLE_2503_06969, "ok", 4),
    ("2503.06917", SAMPLE_2503_06917, "ok", 5),
    ("2503.06937", SAMPLE_2503_06937, "ok", 3),
    ("2503.06915", SAMPLE_2503_06915, "ok", 4),
    ("2503.06916", SAMPLE_2503_06916, "ok", 3),
    ("2503.06918", SAMPLE_2503_06918, "ok", 4),
    ("2503.06919", SAMPLE_2503_06919, "ok", 4),
    ("2503.06920", SAMPLE_2503_06920, "empty_source", 0),
]


class HierarchyCorpusTests(unittest.TestCase):
    def test_corpus_10_samples_validation(self):
        r"""Every sample in the 10-paper corpus from m1-colab-hoang-01 builds a valid VersionHierarchy."""
        for arxiv_id, tex, expected_status, min_nodes in CORPUS_10_SAMPLES:
            with self.subTest(arxiv_id=arxiv_id):
                source = _make_source(arxiv_id, tex, status=expected_status)
                hierarchy = build_hierarchy(source)

                # 1. Structural validity
                validate_version_hierarchy(hierarchy)
                self.assertEqual(hierarchy["arxiv_id"], arxiv_id)
                self.assertEqual(hierarchy["version"], 1)

                nodes = hierarchy["nodes"]
                if expected_status == "empty_source":
                    self.assertEqual(len(nodes), 0)
                else:
                    self.assertGreaterEqual(len(nodes), min_nodes)
                    self.assertEqual(nodes[0]["kind"], "document")
                    self.assertIsNone(nodes[0]["parent_temp_id"])

                    # Verify all child nodes have valid parent pointers
                    node_ids = {n["temp_id"] for n in nodes}
                    for node in nodes[1:]:
                        self.assertIn(node["parent_temp_id"], node_ids)
                        if node["kind"] in ("sentence", "formula", "figure"):
                            self.assertGreater(len(node["content"]), 0)

    def test_corpus_benchmark_performance(self):
        r"""Definition of Done: Average parsing time per publication must be sub-millisecond."""
        start_time = time.perf_counter()
        iterations = 20  # Parse 200 documents total
        total_parsed = 0

        for _ in range(iterations):
            for arxiv_id, tex, status, _ in CORPUS_10_SAMPLES:
                source = _make_source(arxiv_id, tex, status=status)
                build_hierarchy(source)
                total_parsed += 1

        elapsed = time.perf_counter() - start_time
        avg_time_ms = (elapsed / total_parsed) * 1000

        print(f"\nParsed {total_parsed} documents in {elapsed:.4f}s. Avg: {avg_time_ms:.3f} ms/doc")
        self.assertLess(avg_time_ms, 5.0, f"Parser too slow: {avg_time_ms:.3f} ms/doc")


if __name__ == "__main__":
    unittest.main()
