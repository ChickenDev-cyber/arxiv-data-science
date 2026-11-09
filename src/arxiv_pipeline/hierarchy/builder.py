r"""Hierarchy Tree Builder (Module 3).

Constructs structured VersionHierarchy trees from GatheredSource payloads:
- Root 'document' container with title metadata
- Hierarchical sectioning scopes (chapter, section, subsection, subsubsection, paragraph)
- List environments (itemize, enumerate) and list items (\item)
- Three leaf node kinds strictly adhering to parsing.md:
  1. 'sentence': Prose text split by periods, preserving math/citations/abbreviations.
  2. 'formula': Block formulas normalized to \begin{equation} ... \end{equation}.
  3. 'figure': Figures and tables with non-semantic formatting stripped.
- Strict inclusion of Acknowledgements and Appendices
- Strict exclusion of References (\bibliography, \begin{thebibliography})
- Resilient handling of empty sources (PDF-only / unsupported versions)
"""

import re
from typing import Callable

from arxiv_pipeline.hierarchy.contracts import (
    GatheredSource,
    LocalNode,
    VersionHierarchy,
)
from arxiv_pipeline.hierarchy.latex_utils import (
    extract_balanced_braces,
    extract_document_body,
    extract_title,
    strip_comments,
)
from arxiv_pipeline.hierarchy.leaf_parser import (
    clean_latex_formatting,
    normalize_block_formula,
    process_prose_block,
)
from arxiv_pipeline.hierarchy.stack import HierarchyStack
from arxiv_pipeline.hierarchy.validation import validate_version_hierarchy

# Comprehensive delimiter scanner across structural commands, math blocks, and figures
STREAM_DELIMITER_RE = re.compile(
    # 1. Structural sectioning commands
    r"(?P<section>\\(?:chapter|section|subsection|subsubsection|paragraph)\*?\s*\{)"
    # 2. List environments
    r"|(?P<list_begin>\\begin\{(?:itemize|enumerate)\})"
    r"|(?P<list_end>\\end\{(?:itemize|enumerate)\})"
    r"|(?P<item>\\item(?:\s*\[[^\]]*\])?)"
    # 3. Special boundaries
    r"|(?P<appendix>\\appendix\b)"
    r"|(?P<bib_begin>\\begin\{thebibliography\})"
    r"|(?P<bib_cmd>\\bibliography\{)"
    # 4. Block Formulas
    r"|(?P<block_math_env>\\begin\{(?:equation|align|gather)\*?\}.*?\\end\{(?:equation|align|gather)\*?\})"
    r"|(?P<block_math_dollar>\$\$.*?\$\$)"
    r"|(?P<block_math_bracket>\\\[.*?\\\])"
    # 5. Figures & Tables
    r"|(?P<figure_env>\\begin\{(?:figure|table)\*?\}(?:\[[^\]]*\])?.*?\\end\{(?:figure|table)\*?\})",
    re.DOTALL,
)

REFERENCE_TITLES = {
    "references",
    "reference",
    "bibliography",
    "works cited",
    "literature cited",
}


def _is_reference_title(title: str) -> bool:
    """Check if heading title indicates a references section."""
    cleaned = re.sub(r"[^a-zA-Z\s]", "", title).strip().lower()
    return cleaned in REFERENCE_TITLES


class HierarchyBuilder:
    """Builds a VersionHierarchy from raw LaTeX source."""

    def __init__(
        self,
        arxiv_id: str,
        version: int,
        prose_processor: Callable[[str], list[str]] | None = None,
    ) -> None:
        self.arxiv_id = arxiv_id
        self.version = version
        self.prose_processor = prose_processor or process_prose_block

    def build_from_source(self, source: GatheredSource) -> VersionHierarchy:
        """Parse GatheredSource into a structured VersionHierarchy."""
        # Handle empty source (e.g. PDF-only from M1)
        if source.get("status") == "empty_source" or not source.get("merged_tex", "").strip():
            empty_hierarchy: VersionHierarchy = {
                "arxiv_id": self.arxiv_id,
                "version": self.version,
                "nodes": [],
            }
            validate_version_hierarchy(empty_hierarchy)
            return empty_hierarchy

        raw_tex = source.get("merged_tex", "")
        clean_tex = strip_comments(raw_tex)

        doc_title = extract_title(clean_tex) or f"Paper {self.arxiv_id}v{self.version}"
        body_tex = extract_document_body(clean_tex)

        stack = HierarchyStack(root_title=doc_title)
        self._parse_stream(body_tex, stack)

        hierarchy: VersionHierarchy = {
            "arxiv_id": self.arxiv_id,
            "version": self.version,
            "nodes": stack.nodes,
        }
        validate_version_hierarchy(hierarchy)
        return hierarchy

    def _emit_prose(self, prose_text: str, stack: HierarchyStack) -> None:
        """Process prose segment and attach sentence leaves to the active container."""
        if not prose_text or not prose_text.strip():
            return
        sentences = self.prose_processor(prose_text)
        for sentence in sentences:
            if sentence.strip():
                stack.add_leaf("sentence", sentence)

    def _parse_stream(self, body_tex: str, stack: HierarchyStack) -> None:
        """Scan body for structural commands, block formulas, figures, and prose."""
        pos = 0
        in_references = False

        for match in STREAM_DELIMITER_RE.finditer(body_tex):
            match_start = match.start()
            match_end = match.end()

            # Ignore overlapping matches if pos has been advanced past match_start
            if match_start < pos:
                continue

            # Text preceding the delimiter is prose content under the active container
            preceding_prose = body_tex[pos:match_start]
            if not in_references:
                self._emit_prose(preceding_prose, stack)

            pos = match_end

            # --- 1. References Handling (Exclusion) ---
            if match.group("bib_begin"):
                in_references = True
                end_bib = body_tex.find(r"\end{thebibliography}", match_end)
                if end_bib != -1:
                    pos = end_bib + len(r"\end{thebibliography}")
                    in_references = False
                continue

            if match.group("bib_cmd"):
                open_pos = match_end - 1
                _, close_pos = extract_balanced_braces(body_tex, open_pos)
                pos = close_pos + 1
                continue

            if match.group("appendix"):
                in_references = False
                continue

            # --- 2. Structural Section Commands ---
            if match.group("section"):
                cmd_match = re.match(
                    r"\\(chapter|section|subsection|subsubsection|paragraph)\*?\s*\{",
                    match.group("section"),
                )
                if not cmd_match:
                    continue
                cmd_name = cmd_match.group(1)
                open_brace_idx = match_end - 1
                title_content, close_brace_idx = extract_balanced_braces(body_tex, open_brace_idx)
                pos = close_brace_idx + 1

                title_text = re.sub(r"\s+", " ", title_content).strip()

                if _is_reference_title(title_text):
                    in_references = True
                    continue
                else:
                    in_references = False

                stack.push_section(section_cmd=cmd_name, title=title_text)
                continue

            # If inside references section, skip all internal content
            if in_references:
                continue

            # --- 3. List Environments ---
            if match.group("list_begin"):
                list_match = re.search(r"\\begin\{(itemize|enumerate)\}", match.group("list_begin"))
                if list_match:
                    stack.push_list_container(list_match.group(1))  # type: ignore[arg-type]
                continue

            if match.group("list_end"):
                list_match = re.search(r"\\end\{(itemize|enumerate)\}", match.group("list_end"))
                if list_match:
                    stack.pop_list_container(list_match.group(1))  # type: ignore[arg-type]
                continue

            if match.group("item"):
                stack.push_item()
                continue

            # --- 4. Block Formulas (Leaf kind='formula') ---
            if (
                match.group("block_math_env")
                or match.group("block_math_dollar")
                or match.group("block_math_bracket")
            ):
                raw_formula = match.group(0)
                norm_formula = normalize_block_formula(raw_formula)
                stack.add_leaf("formula", norm_formula)
                continue

            # --- 5. Figures and Tables (Leaf kind='figure') ---
            if match.group("figure_env"):
                raw_figure = match.group(0)
                cleaned_figure = clean_latex_formatting(raw_figure)
                stack.add_leaf("figure", cleaned_figure)
                continue

        # Emit any trailing prose after the last delimiter
        if not in_references and pos < len(body_tex):
            remaining_prose = body_tex[pos:]
            self._emit_prose(remaining_prose, stack)


def build_hierarchy(
    source: GatheredSource,
    prose_processor: Callable[[str], list[str]] | None = None,
) -> VersionHierarchy:
    """Convenience entry point to build a VersionHierarchy from GatheredSource."""
    builder = HierarchyBuilder(
        arxiv_id=source.get("arxiv_id", "unknown"),
        version=source.get("version", 1),
        prose_processor=prose_processor,
    )
    return builder.build_from_source(source)
