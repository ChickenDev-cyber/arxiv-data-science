"""Cross-version full-text deduplication engine."""

import re
from arxiv_pipeline.parsing.contracts import (
    BibtexRecord,
    ElementKind,
    ElementRecord,
    HierarchyEdgeRecord,
    PaperReferences,
    PublicationParsingResult,
    VersionHierarchy,
)


def normalize_content(text: str) -> str:
    """Normalize whitespace and newlines for exact matching."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    joined = " ".join(part for part in lines if part)
    return re.sub(r"[ \t]+", " ", joined).strip()


def kind_prefix(kind: ElementKind | str) -> str:
    """Short human-readable prefix for element_id matching the spec example."""
    mapping = {
        "document": "root",
        "section": "h",
        "subsection": "h",
        "subsubsection": "h",
        "paragraph": "p",
        "itemize": "list",
        "enumerate": "list",
        "item": "item",
        "sentence": "s",
        "formula": "eq",
        "figure": "fig",
    }
    return mapping.get(kind, "elem")


_GENERIC_LIST_CONTENT = frozenset({"", r"\begin{itemize}", r"\begin{enumerate}"})


def _is_non_dedup_container(kind: str, norm_content: str) -> bool:
    """Return True if a structural node lacks semantic content and must stay distinct."""
    if kind in ("itemize", "enumerate", "item") and norm_content in _GENERIC_LIST_CONTENT:
        return True
    return norm_content == ""


def deduplicate_publication_elements(
    arxiv_id: str,
    hierarchies: list[VersionHierarchy],
    references: PaperReferences | None = None,
    empty_versions: list[int] | None = None,
) -> PublicationParsingResult:
    """Deduplicate elements across versions and build final relational table records."""
    if empty_versions is None:
        empty_versions = []

    content_to_id: dict[tuple[str, str], str] = {}  # (kind, normalized_content) -> element_id
    elements: list[ElementRecord] = []
    hierarchy_edges: list[HierarchyEdgeRecord] = []
    bibtex_records: list[BibtexRecord] = []

    counters: dict[str, int] = {}
    raw_element_count = 0

    def next_id(kind: str) -> str:
        prefix = kind_prefix(kind)
        count = counters.get(prefix, 0) + 1
        counters[prefix] = count
        return f"{arxiv_id}#{prefix}{count}"

    for v_tree in hierarchies:
        version_num = v_tree["version"]
        temp_to_canonical: dict[str, str] = {}

        # First pass: map nodes to canonical element_id
        for node in v_tree["nodes"]:
            raw_element_count += 1
            raw_content = node["content"]
            norm_content = normalize_content(raw_content)
            kind = node["kind"]
            temp_id = node["temp_id"]

            # Document roots are distinct per version (e.g. root1, root2 in spec)
            if kind == "document":
                canonical_id = f"{arxiv_id}#root{version_num}"
                temp_to_canonical[temp_id] = canonical_id
                elements.append({
                    "element_id": canonical_id,
                    "arxiv_id": arxiv_id,
                    "kind": kind,
                    "content": norm_content if norm_content else f"Version {version_num} root",
                })
                continue

            if _is_non_dedup_container(kind, norm_content):
                canonical_id = next_id(kind)
                elements.append({
                    "element_id": canonical_id,
                    "arxiv_id": arxiv_id,
                    "kind": kind,
                    "content": norm_content,
                })
            else:
                cache_key = (kind, norm_content)
                if cache_key in content_to_id:
                    canonical_id = content_to_id[cache_key]
                else:
                    canonical_id = next_id(kind)
                    content_to_id[cache_key] = canonical_id
                    elements.append({
                        "element_id": canonical_id,
                        "arxiv_id": arxiv_id,
                        "kind": kind,
                        "content": norm_content,
                    })

            temp_to_canonical[temp_id] = canonical_id

        # Second pass: construct hierarchy edges with canonical IDs
        for node in v_tree["nodes"]:
            temp_id = node["temp_id"]
            parent_temp_id = node["parent_temp_id"]
            if parent_temp_id is None:
                continue

            child_canon_id = temp_to_canonical[temp_id]
            parent_canon_id = temp_to_canonical.get(parent_temp_id)
            if parent_canon_id is not None:
                hierarchy_edges.append({
                    "arxiv_id": arxiv_id,
                    "version": version_num,
                    "element_id": child_canon_id,
                    "parent_id": parent_canon_id,
                })

    # Process bibtex entries from PaperReferences
    if references and references.get("unified_bibtex"):
        for b in references["unified_bibtex"]:
            bibtex_records.append({
                "arxiv_id": arxiv_id,
                "citation_key": b["citation_key"],
                "entry_type": b["entry_type"],
                "title": b.get("title", ""),
                "authors": b.get("authors", []),
                "year": b.get("year"),
                "doi": b.get("doi", ""),
                "journal": b.get("journal", ""),
                "url": b.get("url", ""),
            })

    remap_dict = references.get("citation_remap", {}) if references else {}
    raw_bib_from_remap = sum(len(v_map) for v_map in remap_dict.values()) if remap_dict else 0
    raw_bibtex_count = max(raw_bib_from_remap, len(bibtex_records))

    all_versions = {h["version"] for h in hierarchies}.union(empty_versions)

    return {
        "arxiv_id": arxiv_id,
        "elements": elements,
        "hierarchy": hierarchy_edges,
        "bibtex": bibtex_records,
        "version_count": len(all_versions),
        "empty_versions": sorted(list(set(empty_versions))),
        "raw_element_count": raw_element_count,
        "raw_bibtex_count": raw_bibtex_count,
    }
