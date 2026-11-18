"""Không nhầm tiền tố tên command với mục danh sách thật."""

import unittest
from arxiv_pipeline.hierarchy.builder import build_hierarchy


class TestHierarchyItemBoundary(unittest.TestCase):
    def test_itemsep_does_not_split_prose_or_create_list_items(self):
        source = {
            "arxiv_id": "2506.20793", "version": 1, "status": "ok",
            "root_file": "main.tex", "compilation_path": ["main.tex"],
            "bib_files": [], "bbl_files": [],
            "merged_tex": r"""\documentclass{article}
\begin{document}
\begin{itemize}
\setlength{\itemsep}{0pt}
\item First sentence.
\item[label] Second sentence.
\item{Third sentence.}
\end{itemize}
\end{document}""",
        }
        nodes = build_hierarchy(source)["nodes"]
        items = [n for n in nodes if n["kind"] == "item"]
        self.assertEqual(len(items), 3)
        sentences = [n for n in nodes if n["kind"] == "sentence"]
        # Layout declarations must not become sentences or fake list items.
        self.assertEqual(len(sentences), 3)
        self.assertFalse(any(r"\setlength" in n["content"] for n in sentences))
        for item, expected in zip(items, ["First sentence.", "Second sentence.", "{Third sentence.}"]):
            with self.subTest(expected=expected):
                self.assertEqual(
                    [n["content"] for n in sentences if n["parent_temp_id"] == item["temp_id"]],
                    [expected],
                )


if __name__ == "__main__":
    unittest.main()
