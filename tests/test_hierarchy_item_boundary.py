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
        self.assertEqual(sentences[0]["content"], r"\setlength{\itemsep}{0pt}")
        self.assertEqual(
            sentences[0]["parent_temp_id"],
            next(n["temp_id"] for n in nodes if n["kind"] == "itemize"),
        )
        for item, expected in zip(items, ["First sentence.", "Second sentence.", "{Third sentence.}"]):
            with self.subTest(expected=expected):
                self.assertEqual(
                    [n["content"] for n in sentences if n["parent_temp_id"] == item["temp_id"]],
                    [expected],
                )


if __name__ == "__main__":
    unittest.main()
