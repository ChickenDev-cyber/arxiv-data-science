"""Milestone 2 Module 4 data contracts."""
from typing import Literal, TypedDict


GatherStatus = Literal["ok", "empty_source", "no_root_file"]


class GatheredSource(TypedDict):
    arxiv_id: str
    version: int
    root_file: str | None
    compilation_path: list[str]
    merged_tex: str
    bib_files: list[str]
    bbl_files: list[str]
    status: GatherStatus


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
    citation_key_conflicts: list[dict[str, object]]
