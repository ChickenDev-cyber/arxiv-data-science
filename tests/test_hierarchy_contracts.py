"""Tests for Module 3 hierarchy contracts and structural validation."""

import pytest

from arxiv_pipeline.hierarchy.contracts import (
    ElementKind,
    GatheredSource,
    LocalNode,
    VersionHierarchy,
)
from arxiv_pipeline.hierarchy.validation import (
    HierarchyValidationError,
    validate_version_hierarchy,
)


def test_empty_version_hierarchy_valid():
    """An empty version hierarchy (e.g. for unsupported/PDF-only versions) is valid."""
    empty_tree: VersionHierarchy = {
        "arxiv_id": "2409.00001",
        "version": 1,
        "nodes": [],
    }
    # Should not raise
    validate_version_hierarchy(empty_tree)


def test_valid_basic_hierarchy():
    """A valid hierarchy with document root, sections, and leaves."""
    valid_tree: VersionHierarchy = {
        "arxiv_id": "2409.00001",
        "version": 1,
        "nodes": [
            {
                "temp_id": "root",
                "kind": "document",
                "content": "Paper Title",
                "parent_temp_id": None,
            },
            {
                "temp_id": "sec_1",
                "kind": "section",
                "content": "Introduction",
                "parent_temp_id": "root",
            },
            {
                "temp_id": "sent_1",
                "kind": "sentence",
                "content": "We propose a novel parsing framework.",
                "parent_temp_id": "sec_1",
            },
            {
                "temp_id": "eq_1",
                "kind": "formula",
                "content": "\\begin{equation} E = mc^2 \\end{equation}",
                "parent_temp_id": "sec_1",
            },
            {
                "temp_id": "fig_1",
                "kind": "figure",
                "content": "\\begin{figure} System Architecture \\end{figure}",
                "parent_temp_id": "sec_1",
            },
        ],
    }
    validate_version_hierarchy(valid_tree)


def test_invalid_no_root():
    """Hierarchy with nodes but missing root (no node with parent=None)."""
    invalid_tree: VersionHierarchy = {
        "arxiv_id": "2409.00001",
        "version": 1,
        "nodes": [
            {
                "temp_id": "sec_1",
                "kind": "section",
                "content": "Introduction",
                "parent_temp_id": "root",
            }
        ],
    }
    with pytest.raises(HierarchyValidationError, match="Expected exactly 1 root node"):
        validate_version_hierarchy(invalid_tree)


def test_invalid_duplicate_temp_id():
    """Nodes cannot share the same temp_id."""
    invalid_tree: VersionHierarchy = {
        "arxiv_id": "2409.00001",
        "version": 1,
        "nodes": [
            {
                "temp_id": "root",
                "kind": "document",
                "content": "Doc",
                "parent_temp_id": None,
            },
            {
                "temp_id": "root",
                "kind": "section",
                "content": "Duplicate",
                "parent_temp_id": "root",
            },
        ],
    }
    with pytest.raises(HierarchyValidationError, match="Duplicate temp_id"):
        validate_version_hierarchy(invalid_tree)


def test_invalid_kind():
    """Node with unknown element kind must fail validation."""
    invalid_tree = {
        "arxiv_id": "2409.00001",
        "version": 1,
        "nodes": [
            {
                "temp_id": "root",
                "kind": "unknown_kind",  # type: ignore
                "content": "Doc",
                "parent_temp_id": None,
            }
        ],
    }
    with pytest.raises(HierarchyValidationError, match="invalid kind"):
        validate_version_hierarchy(invalid_tree)  # type: ignore


def test_invalid_non_existent_parent():
    """Child referencing a parent_id that does not exist."""
    invalid_tree: VersionHierarchy = {
        "arxiv_id": "2409.00001",
        "version": 1,
        "nodes": [
            {
                "temp_id": "root",
                "kind": "document",
                "content": "Doc",
                "parent_temp_id": None,
            },
            {
                "temp_id": "sec_1",
                "kind": "section",
                "content": "Intro",
                "parent_temp_id": "ghost_parent",
            },
        ],
    }
    with pytest.raises(HierarchyValidationError, match="non-existent parent_temp_id"):
        validate_version_hierarchy(invalid_tree)
