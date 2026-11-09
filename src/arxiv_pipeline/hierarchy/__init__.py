"""Module 3: Hierarchy Tree Construction.

Builds structured hierarchical trees (document -> sections -> leaves) from raw
LaTeX source streams for each publication version.
"""

from arxiv_pipeline.hierarchy.builder import HierarchyBuilder, build_hierarchy
from arxiv_pipeline.hierarchy.contracts import (
    ElementKind,
    GatheredSource,
    GatherStatus,
    LocalNode,
    VersionHierarchy,
)
from arxiv_pipeline.hierarchy.leaf_parser import (
    clean_latex_formatting,
    normalize_block_formula,
    normalize_inline_math,
    process_prose_block,
    tokenize_sentences,
)
from arxiv_pipeline.hierarchy.stack import HierarchyStack
from arxiv_pipeline.hierarchy.validation import (
    HierarchyValidationError,
    validate_version_hierarchy,
)

__all__ = [
    "ElementKind",
    "GatheredSource",
    "GatherStatus",
    "LocalNode",
    "VersionHierarchy",
    "HierarchyValidationError",
    "validate_version_hierarchy",
    "HierarchyStack",
    "HierarchyBuilder",
    "build_hierarchy",
    "clean_latex_formatting",
    "normalize_block_formula",
    "normalize_inline_math",
    "process_prose_block",
    "tokenize_sentences",
]
