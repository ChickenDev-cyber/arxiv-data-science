"""Milestone 2 Module 4: LaTeX bibliography extraction and citation remapping."""
from .contracts import BibtexEntry, GatheredSource, PaperReferences
from .remapper import rewrite_citation_keys
from .service import extract_and_unify_references

__all__ = [
    "BibtexEntry", "GatheredSource", "PaperReferences",
    "extract_and_unify_references", "rewrite_citation_keys",
]
