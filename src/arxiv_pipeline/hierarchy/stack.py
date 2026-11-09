r"""Hierarchy Stack Engine for managing nested document scopes in LaTeX.

Maintains the active parent-child context during LaTeX parsing, supporting:
- Multi-level sectioning (chapter -> section -> subsection -> subsubsection -> paragraph)
- List environments (itemize, enumerate) and list items (\item)
- Automatic unwinding of unclosed section blocks
- Clean generation of local temporary node IDs
"""

from dataclasses import dataclass
from typing import Literal

from arxiv_pipeline.hierarchy.contracts import ElementKind, LocalNode

# Hierarchy depth mapping for structural container elements
SECTION_LEVELS: dict[str, int] = {
    "chapter": 1,
    "section": 2,
    "subsection": 3,
    "subsubsection": 4,
    "paragraph": 5,
}


@dataclass
class StackFrame:
    """An active scope frame on the hierarchy stack."""

    temp_id: str
    kind: ElementKind
    level: int  # Lower number = higher in hierarchy (0 = document root)


class HierarchyStack:
    """Manages active container nodes and resolves parent pointers."""

    def __init__(self, root_title: str = "Document") -> None:
        self.nodes: list[LocalNode] = []
        self._counter: dict[str, int] = {}
        self._stack: list[StackFrame] = []

        # Initialize root node (level 0)
        root_id = self._next_id("root")
        root_node: LocalNode = {
            "temp_id": root_id,
            "kind": "document",
            "content": root_title,
            "parent_temp_id": None,
        }
        self.nodes.append(root_node)
        self._stack.append(StackFrame(temp_id=root_id, kind="document", level=0))

    def _next_id(self, prefix: str) -> str:
        count = self._counter.get(prefix, 0) + 1
        self._counter[prefix] = count
        if prefix == "root":
            return "root"
        return f"{prefix}_{count}"

    @property
    def current_parent_id(self) -> str:
        """Return the temp_id of the currently active container node."""
        if not self._stack:
            raise RuntimeError("Hierarchy stack is unexpectedly empty")
        return self._stack[-1].temp_id

    def push_section(self, section_cmd: str, title: str) -> LocalNode:
        """Open a new sectioning block (chapter, section, subsection, etc.).

        Pops any active sections or items at equal or deeper hierarchy levels.
        """
        level = SECTION_LEVELS.get(section_cmd, 2)
        prefix_map = {
            "chapter": "chap",
            "section": "sec",
            "subsection": "subsec",
            "subsubsection": "subsub",
            "paragraph": "para",
        }
        prefix = prefix_map.get(section_cmd, "sec")

        # Unwind any deeper or equal containers, stopping before document root (level 0)
        while len(self._stack) > 1 and self._stack[-1].level >= level:
            self._stack.pop()

        parent_id = self.current_parent_id
        temp_id = self._next_id(prefix)
        # Map chapter to section to strictly conform to ElementKind contract
        node_kind: ElementKind = "section" if section_cmd in ("chapter", "section") else section_cmd  # type: ignore[assignment]

        node: LocalNode = {
            "temp_id": temp_id,
            "kind": node_kind,
            "content": title.strip(),
            "parent_temp_id": parent_id,
        }
        self.nodes.append(node)
        self._stack.append(StackFrame(temp_id=temp_id, kind=node_kind, level=level))
        return node

    def push_list_container(self, list_type: Literal["itemize", "enumerate"]) -> LocalNode:
        """Open a list environment block (itemize or enumerate)."""
        parent_id = self.current_parent_id
        temp_id = self._next_id("list")

        # List containers have depth relative to their parent
        parent_level = self._stack[-1].level
        container_level = parent_level + 10  # Distinguish list nesting from sections

        node: LocalNode = {
            "temp_id": temp_id,
            "kind": list_type,
            "content": f"\\begin{{{list_type}}}",
            "parent_temp_id": parent_id,
        }
        self.nodes.append(node)
        self._stack.append(StackFrame(temp_id=temp_id, kind=list_type, level=container_level))
        return node

    def push_item(self, initial_content: str = "") -> LocalNode:
        r"""Open a list item (\item).

        If another item is currently active at this list level, closes (pops) it first.
        """
        # If previous element on stack was an item, pop it to make sibling
        if self._stack and self._stack[-1].kind == "item":
            self._stack.pop()

        parent_id = self.current_parent_id
        temp_id = self._next_id("item")
        item_level = self._stack[-1].level + 1

        node: LocalNode = {
            "temp_id": temp_id,
            "kind": "item",
            "content": initial_content.strip(),
            "parent_temp_id": parent_id,
        }
        self.nodes.append(node)
        self._stack.append(StackFrame(temp_id=temp_id, kind="item", level=item_level))
        return node

    def pop_list_container(self, list_type: Literal["itemize", "enumerate"]) -> None:
        """Close a list environment (\\end{itemize} or \\end{enumerate})."""
        # Pop any active items or inner structures until we pop the matching list container
        while len(self._stack) > 1:
            top = self._stack.pop()
            if top.kind == list_type:
                break

    def add_leaf(self, kind: Literal["sentence", "formula", "figure"], content: str) -> LocalNode:
        """Add a leaf node under the currently active container.

        Leaves are terminal elements and are never pushed to the stack.
        """
        prefix_map = {
            "sentence": "s",
            "formula": "eq",
            "figure": "fig",
        }
        temp_id = self._next_id(prefix_map[kind])
        node: LocalNode = {
            "temp_id": temp_id,
            "kind": kind,
            "content": content.strip(),
            "parent_temp_id": self.current_parent_id,
        }
        self.nodes.append(node)
        return node
