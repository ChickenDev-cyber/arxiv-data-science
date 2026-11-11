"""Module 1 output contract (Milestone 2, section 3 of the team plan)."""

from typing import Literal, TypedDict

GatherStatus = Literal["ok", "empty_source", "no_root_file"]

GATHERED_SOURCE_KEYS = (
    "arxiv_id",
    "version",
    "root_file",
    "compilation_path",
    "merged_tex",
    "bib_files",
    "bbl_files",
    "status",
)


class GatheredSource(TypedDict):
    arxiv_id: str
    version: int
    root_file: str | None            # .tex file with \documentclass, relative to source_dir
    compilation_path: list[str]      # .tex files in the order they are first loaded
    merged_tex: str                  # LaTeX text with every \input/\include inlined
    bib_files: list[str]             # .bib files the text really cites (\bibliography, \addbibresource)
    bbl_files: list[str]             # .bbl files found in source_dir (usually [] because M1 drops them)
    status: GatherStatus
