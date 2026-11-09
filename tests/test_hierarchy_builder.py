"""Unit tests for HierarchyBuilder and stack scoping engine (Bước 2)."""

import pytest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.hierarchy.contracts import GatheredSource
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy


def test_empty_source():
    """Empty source returns an empty VersionHierarchy."""
    source: GatheredSource = {
        "arxiv_id": "2409.01234",
        "version": 1,
        "root_file": None,
        "compilation_path": [],
        "merged_tex": "",
        "bbl_files": [],
        "status": "empty_source",
    }
    result = build_hierarchy(source)
    assert result["arxiv_id"] == "2409.01234"
    assert result["version"] == 1
    assert result["nodes"] == []
    validate_version_hierarchy(result)


def test_section_scoping_and_unwinding():
    """Nested sections unwind properly when a higher-level section appears."""
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
        "bbl_files": [],
        "status": "ok",
    }
    hierarchy = build_hierarchy(source)
    validate_version_hierarchy(hierarchy)

    nodes = hierarchy["nodes"]
    # Node 0: Document root
    assert nodes[0]["kind"] == "document"
    assert "Sample Paper on AI" in nodes[0]["content"]

    # Section 1: Intro (child of root)
    sec_intro = nodes[1]
    assert sec_intro["kind"] == "section"
    assert sec_intro["content"] == "Introduction"
    assert sec_intro["parent_temp_id"] == "root"

    # Subsection: Background (child of Intro)
    sub_bg = nodes[2]
    assert sub_bg["kind"] == "subsection"
    assert sub_bg["content"] == "Background"
    assert sub_bg["parent_temp_id"] == sec_intro["temp_id"]

    # Subsubsection: Overview (child of Background)
    subsub = nodes[3]
    assert subsub["kind"] == "subsubsection"
    assert subsub["content"] == "Historical Overview"
    assert subsub["parent_temp_id"] == sub_bg["temp_id"]

    # Paragraph: Early Years (child of Overview)
    para = nodes[4]
    assert para["kind"] == "paragraph"
    assert para["content"] == "Early Years"
    assert para["parent_temp_id"] == subsub["temp_id"]

    # Section 2: Methodology (must unwind back to root)
    sec_method = nodes[5]
    assert sec_method["kind"] == "section"
    assert sec_method["content"] == "Methodology"
    assert sec_method["parent_temp_id"] == "root"

    # Subsection: Proposed Model (child of Methodology)
    sub_model = nodes[6]
    assert sub_model["kind"] == "subsection"
    assert sub_model["content"] == "Proposed Model"
    assert sub_model["parent_temp_id"] == sec_method["temp_id"]


def test_list_environments_and_items():
    """Itemize and enumerate lists properly nest items and unwind."""
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
        "bbl_files": [],
        "status": "ok",
    }
    hierarchy = build_hierarchy(source)
    validate_version_hierarchy(hierarchy)

    nodes = hierarchy["nodes"]
    node_map = {n["temp_id"]: n for n in nodes}

    sec_contrib = nodes[1]
    assert sec_contrib["content"] == "Contributions"

    list_node = [n for n in nodes if n["kind"] == "itemize"][0]
    assert list_node["parent_temp_id"] == sec_contrib["temp_id"]

    items = [n for n in nodes if n["kind"] == "item"]
    assert len(items) == 2
    assert items[0]["parent_temp_id"] == list_node["temp_id"]
    # Sibling: parent is the itemize container, NOT items[0]
    assert items[1]["parent_temp_id"] == list_node["temp_id"]

    sec_conclusion = [n for n in nodes if n["kind"] == "section" and n["content"] == "Conclusion"][0]
    # Unwound back to document root
    assert sec_conclusion["parent_temp_id"] == "root"


def test_nested_lists():
    """Enumerate nested inside itemize attaches to parent item."""
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
        "bbl_files": [],
        "status": "ok",
    }
    hierarchy = build_hierarchy(source)
    validate_version_hierarchy(hierarchy)

    nodes = hierarchy["nodes"]
    list_node = [n for n in nodes if n["kind"] == "itemize"][0]
    items = [n for n in nodes if n["kind"] == "item"]
    assert len(items) == 4
    item_dataset = items[0]

    enum_nodes = [n for n in nodes if n["kind"] == "enumerate"]
    assert len(enum_nodes) == 1
    enum_node = enum_nodes[0]
    assert enum_node["parent_temp_id"] == item_dataset["temp_id"]  # enum is child of item(Dataset)

    item_train, item_test, item_metric = items[1], items[2], items[3]
    assert item_train["parent_temp_id"] == enum_node["temp_id"]
    assert item_test["parent_temp_id"] == enum_node["temp_id"]
    assert item_metric["parent_temp_id"] == list_node["temp_id"]  # back to outer itemize


def test_inclusions_acknowledgements_and_appendices():
    """Acknowledgements and Appendices must be included in the hierarchy."""
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
        "bbl_files": [],
        "status": "ok",
    }
    hierarchy = build_hierarchy(source)
    validate_version_hierarchy(hierarchy)

    titles = [n["content"] for n in hierarchy["nodes"] if n["kind"] == "section"]
    assert "Main Text" in titles
    assert "Acknowledgements" in titles
    assert "Mathematical Proofs" in titles


def test_exclusions_references_and_comments():
    """References sections and LaTeX comments must be excluded from the hierarchy."""
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
        "bbl_files": [],
        "status": "ok",
    }
    hierarchy = build_hierarchy(source)
    validate_version_hierarchy(hierarchy)

    titles = [n["content"] for n in hierarchy["nodes"]]
    assert not any("Commented Section" in t for t in titles)
    assert "References" not in titles
    assert not any("vaswani2017" in t for t in titles)
    assert "Supplementary Proofs" in titles
