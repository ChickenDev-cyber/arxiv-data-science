"""Validation utilities for hierarchical tree structures."""

from typing import get_args

from arxiv_pipeline.hierarchy.contracts import ElementKind, LocalNode, VersionHierarchy

VALID_ELEMENT_KINDS = set(get_args(ElementKind))


class HierarchyValidationError(ValueError):
    """Raised when a VersionHierarchy violates structural integrity rules."""


def validate_version_hierarchy(hierarchy: VersionHierarchy) -> None:
    """Validate that a VersionHierarchy satisfies all structural rules.

    Rules enforced:
    1. If `nodes` is empty, it represents an empty source (e.g. PDF-only / unsupported).
    2. If `nodes` is non-empty, exactly one node must have `parent_temp_id is None`
       and its kind must be 'document'.
    3. All other nodes must have a `parent_temp_id` pointing to a preceding or existing node.
    4. All node `temp_id`s must be unique within the version.
    5. All node `kind` values must belong to the valid ElementKind set.
    6. No cycles in the parent-child graph.
    """
    nodes = hierarchy.get("nodes", [])
    if not nodes:
        # Valid empty tree representation for papers without TeX source
        return

    seen_ids: set[str] = set()
    root_nodes: list[LocalNode] = []

    for idx, node in enumerate(nodes):
        temp_id = node.get("temp_id")
        kind = node.get("kind")
        parent_id = node.get("parent_temp_id")

        if not temp_id or not isinstance(temp_id, str):
            raise HierarchyValidationError(f"Node at index {idx} has invalid temp_id: {temp_id!r}")

        if temp_id in seen_ids:
            raise HierarchyValidationError(f"Duplicate temp_id found: {temp_id!r}")
        seen_ids.add(temp_id)

        if kind not in VALID_ELEMENT_KINDS:
            raise HierarchyValidationError(
                f"Node {temp_id!r} has invalid kind {kind!r}. Expected one of {sorted(VALID_ELEMENT_KINDS)}"
            )

        if parent_id is None:
            root_nodes.append(node)
            if kind != "document":
                raise HierarchyValidationError(
                    f"Root node {temp_id!r} must have kind 'document', got {kind!r}"
                )

    if len(root_nodes) != 1:
        raise HierarchyValidationError(
            f"Expected exactly 1 root node with parent_temp_id=None, found {len(root_nodes)}"
        )

    # Check parent references and cycle detection
    for node in nodes:
        parent_id = node.get("parent_temp_id")
        if parent_id is not None:
            if parent_id not in seen_ids:
                raise HierarchyValidationError(
                    f"Node {node['temp_id']!r} references non-existent parent_temp_id {parent_id!r}"
                )

    # Cycle detection via depth traversal
    parent_map = {node["temp_id"]: node["parent_temp_id"] for node in nodes}
    for start_id in seen_ids:
        curr = start_id
        visited_in_path: set[str] = set()
        while curr is not None:
            if curr in visited_in_path:
                raise HierarchyValidationError(f"Cycle detected in hierarchy graph at node {curr!r}")
            visited_in_path.add(curr)
            curr = parent_map.get(curr)
