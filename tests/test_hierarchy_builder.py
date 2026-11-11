r"""Unit tests for HierarchyBuilder and stack scoping engine (Bước 2).

Compatible with both standard unittest and pytest.
"""

import unittest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


class HierarchyBuilderTests(unittest.TestCase):
    def test_empty_source(self):
        r"""Empty source returns an empty VersionHierarchy."""
        source: GatheredSource = {
            "arxiv_id": "2409.01234",
            "version": 1,
            "root_file": None,
            "compilation_path": [],
            "merged_tex": "",
            "bib_files": [],
            "bbl_files": [],
            "status": "empty_source",
        }
        result = build_hierarchy(source)
        self.assertEqual(result["arxiv_id"], "2409.01234")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["nodes"], [])
        validate_version_hierarchy(result)

    def test_section_scoping_and_unwinding(self):
        r"""Nested sections unwind properly when a higher-level section appears."""
        tex = r"""
        \documentclass{article}
        \title{Sample Paper on AI}
        \begin{document}
        \section{Introduction}
        \subsection{Background}
        \subsubsection{Historical Overview}
        \paragraph{Early Years}
        \section{Methodology}
        \subsection{Proposed Model}
        \end{document}
        """
        source: GatheredSource = {
            "arxiv_id": "2409.00001",
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
        # Node 0: Document root
        self.assertEqual(nodes[0]["kind"], "document")
        self.assertIn("Sample Paper on AI", nodes[0]["content"])

        # Section 1: Intro (child of root)
        sec_intro = nodes[1]
        self.assertEqual(sec_intro["kind"], "section")
        self.assertEqual(sec_intro["content"], "Introduction")
        self.assertEqual(sec_intro["parent_temp_id"], "root")

        # Subsection: Background (child of Intro)
        sub_bg = nodes[2]
        self.assertEqual(sub_bg["kind"], "subsection")
        self.assertEqual(sub_bg["content"], "Background")
        self.assertEqual(sub_bg["parent_temp_id"], sec_intro["temp_id"])

        # Subsubsection: Overview (child of Background)
        subsub = nodes[3]
        self.assertEqual(subsub["kind"], "subsubsection")
        self.assertEqual(subsub["content"], "Historical Overview")
        self.assertEqual(subsub["parent_temp_id"], sub_bg["temp_id"])

        # Paragraph: Early Years (child of Overview)
        para = nodes[4]
        self.assertEqual(para["kind"], "paragraph")
        self.assertEqual(para["content"], "Early Years")
        self.assertEqual(para["parent_temp_id"], subsub["temp_id"])

        # Section 2: Methodology (must unwind back to root)
        sec_method = nodes[5]
        self.assertEqual(sec_method["kind"], "section")
        self.assertEqual(sec_method["content"], "Methodology")
        self.assertEqual(sec_method["parent_temp_id"], "root")

        # Subsection: Proposed Model (child of Methodology)
        sub_model = nodes[6]
        self.assertEqual(sub_model["kind"], "subsection")
        self.assertEqual(sub_model["content"], "Proposed Model")
        self.assertEqual(sub_model["parent_temp_id"], sec_method["temp_id"])

    def test_list_environments_and_items(self):
        r"""Itemize and enumerate lists properly nest items and unwind."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Contributions}
        \begin{itemize}
          \item First contribution
          \item Second contribution
        \end{itemize}
        \section{Conclusion}
        \end{document}
        """
        source: GatheredSource = {
            "arxiv_id": "2409.00002",
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
        sec_contrib = nodes[1]
        self.assertEqual(sec_contrib["content"], "Contributions")

        list_node = [n for n in nodes if n["kind"] == "itemize"][0]
        self.assertEqual(list_node["parent_temp_id"], sec_contrib["temp_id"])

        items = [n for n in nodes if n["kind"] == "item"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["parent_temp_id"], list_node["temp_id"])
        # Sibling: parent is the itemize container, NOT items[0]
        self.assertEqual(items[1]["parent_temp_id"], list_node["temp_id"])

        sec_conclusion = [n for n in nodes if n["kind"] == "section" and n["content"] == "Conclusion"][0]
        # Unwound back to document root
        self.assertEqual(sec_conclusion["parent_temp_id"], "root")

    def test_nested_lists(self):
        r"""Enumerate nested inside itemize attaches to parent item."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Experiment}
        \begin{itemize}
          \item Dataset
            \begin{enumerate}
              \item Training split
              \item Testing split
            \end{enumerate}
          \item Evaluation Metric
        \end{itemize}
        \end{document}
        """
        source: GatheredSource = {
            "arxiv_id": "2409.00003",
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
        list_node = [n for n in nodes if n["kind"] == "itemize"][0]
        items = [n for n in nodes if n["kind"] == "item"]
        self.assertEqual(len(items), 4)
        item_dataset = items[0]

        enum_nodes = [n for n in nodes if n["kind"] == "enumerate"]
        self.assertEqual(len(enum_nodes), 1)
        enum_node = enum_nodes[0]
        self.assertEqual(enum_node["parent_temp_id"], item_dataset["temp_id"])

        item_train, item_test, item_metric = items[1], items[2], items[3]
        self.assertEqual(item_train["parent_temp_id"], enum_node["temp_id"])
        self.assertEqual(item_test["parent_temp_id"], enum_node["temp_id"])
        self.assertEqual(item_metric["parent_temp_id"], list_node["temp_id"])

    def test_inclusions_acknowledgements_and_appendices(self):
        r"""Acknowledgements and Appendices must be included in the hierarchy."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Main Text}
        \section*{Acknowledgements}
        \appendix
        \section{Mathematical Proofs}
        \end{document}
        """
        source: GatheredSource = {
            "arxiv_id": "2409.00004",
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

        titles = [n["content"] for n in hierarchy["nodes"] if n["kind"] == "section"]
        self.assertIn("Main Text", titles)
        self.assertIn("Acknowledgements", titles)
        self.assertIn("Mathematical Proofs", titles)

    def test_exclusions_references_and_comments(self):
        r"""References sections and LaTeX comments must be excluded from the hierarchy."""
        tex = r"""
        \documentclass{article}
        \begin{document}
        \section{Introduction}
        % \section{Commented Section That Should Not Exist}
        \section{References}
        \begin{thebibliography}{99}
          \bibitem{vaswani2017} Vaswani et al.
        \end{thebibliography}
        \appendix
        \section{Supplementary Proofs}
        \end{document}
        """
        source: GatheredSource = {
            "arxiv_id": "2409.00005",
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

        titles = [n["content"] for n in hierarchy["nodes"]]
        self.assertFalse(any("Commented Section" in t for t in titles))
        self.assertNotIn("References", titles)
        self.assertFalse(any("vaswani2017" in t for t in titles))
        self.assertIn("Supplementary Proofs", titles)


if __name__ == "__main__":
    unittest.main()
