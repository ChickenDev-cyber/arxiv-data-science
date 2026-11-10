"""Shared data contracts and types for Milestone 2: Hierarchical Parsing & Standardization."""

from typing import Literal, TypedDict

GatherStatus = Literal["ok", "empty_source", "no_root_file"]


class GatheredSource(TypedDict):
    arxiv_id: str
    version: int
    root_file: str | None
    compilation_path: list[str]
    merged_tex: str
    bbl_files: list[str]
    status: GatherStatus


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
    "figure",
]


class LocalNode(TypedDict):
    temp_id: str
    kind: ElementKind
    content: str
    parent_temp_id: str | None


class VersionHierarchy(TypedDict):
    arxiv_id: str
    version: int
    nodes: list[LocalNode]


class BibtexEntry(TypedDict):
    arxiv_id: str
    citation_key: str
    entry_type: str
    title: str
    authors: list[str]
    year: int | None
    doi: str
    journal: str
    url: str


class PaperReferences(TypedDict):
    arxiv_id: str
    unified_bibtex: list[BibtexEntry]
    citation_remap: dict[int, dict[str, str]]


class ElementRecord(TypedDict):
    element_id: str
    arxiv_id: str
    kind: str
    content: str


class HierarchyEdgeRecord(TypedDict):
    arxiv_id: str
    version: int
    element_id: str
    parent_id: str


class BibtexRecord(TypedDict):
    arxiv_id: str
    citation_key: str
    entry_type: str
    title: str
    authors: list[str]
    year: int | None
    doi: str
    journal: str
    url: str


class PublicationParsingResult(TypedDict):
    arxiv_id: str
    elements: list[ElementRecord]
    hierarchy: list[HierarchyEdgeRecord]
    bibtex: list[BibtexRecord]
    version_count: int
    empty_versions: list[int]
