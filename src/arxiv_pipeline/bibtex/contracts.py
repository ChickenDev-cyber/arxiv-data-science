"""Milestone 2 Module 4 data contracts for references extracted from LaTeX."""
from typing import TypedDict


class GatheredSource(TypedDict):
    arxiv_id: str
    version: int
    root_file: str | None
    compilation_path: list[str]
    merged_tex: str
    bbl_files: list[str]
    status: str


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
