"""Interface Contracts for Module 3: Hierarchy Tree Construction.

Defined by the team's Milestone 2 specification (milestone-2-assignment.md).
Adheres strictly to the shared data contracts across all pipeline modules.
"""

from typing import Literal, TypedDict

# --- Module 1 Input Types (for reference and typing compatibility) ---
GatherStatus = Literal["ok", "empty_source", "no_root_file"]


class GatheredSource(TypedDict):
    """Input payload provided by Module 1 (Gathering & Path Resolver)."""

    arxiv_id: str
    version: int
    root_file: str | None  # Primary .tex file containing \documentclass
    compilation_path: list[str]  # Order of included .tex files (\input, \include)
    merged_tex: str  # Concatenated LaTeX text in compilation order
    bib_files: list[str]  # Actual accessible .bib files called via \bibliography
    bbl_files: list[str]  # Accompanying .bbl files (if any, typically [] due to M1 filter)
    status: GatherStatus


# --- Module 3 Output Types ---
ElementKind = Literal[
    "document",
    "section",
    "subsection",
    "subsubsection",
    "paragraph",
    "itemize",
    "enumerate",
    "item",
    "sentence",
    "formula",
    "figure",  # 'figure' covers both figures and tables as required by parsing.md
]


class LocalNode(TypedDict):
    """A single node in the version-level hierarchical tree."""

    temp_id: str  # Local temporary ID within version (e.g. 'root', 'h1', 's1')
    kind: ElementKind
    content: str  # Cleaned content (heading title, sentence, block formula, caption)
    parent_temp_id: str | None  # None only for the root 'document' node


class VersionHierarchy(TypedDict):
    """Full hierarchical representation of one version of a paper."""

    arxiv_id: str
    version: int
    nodes: list[LocalNode]
