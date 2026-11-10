"""Pipeline orchestrator for hierarchical parsing, standardization, and storage."""

import logging
import os
import re
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Optional

from arxiv_pipeline.parsing.contracts import (
    GatheredSource,
    LocalNode,
    PaperReferences,
    PublicationParsingResult,
    VersionHierarchy,
)
from arxiv_pipeline.parsing.dedup import deduplicate_publication_elements
from arxiv_pipeline.parsing.storage import ParsingDatabase

logger = logging.getLogger(__name__)


def parse_fallback_hierarchy(raw_tex: str, arxiv_id: str, version: int) -> VersionHierarchy:
    """Standard baseline parser for converting raw TeX stream into VersionHierarchy.

    Acts as the standard pipeline parser; can be substituted with Module 3 implementation.
    """
    nodes: List[LocalNode] = []
    root_id = "root"
    nodes.append({
        "temp_id": root_id,
        "kind": "document",
        "content": f"Version {version} root",
        "parent_temp_id": None,
    })

    # Strip thebibliography environment from hierarchy (exclusion rule)
    clean_tex = re.sub(
        r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", raw_tex, flags=re.DOTALL
    )

    # Basic sections split
    section_pattern = re.compile(r"\\(section\*?|subsection\*?)\{([^}]+)\}")
    current_parent = root_id
    sec_idx = 1
    sent_idx = 1

    matches = list(section_pattern.finditer(clean_tex))
    if not matches:
        # Fallback single block
        sentences = [s.strip() for s in clean_tex.split(".") if len(s.strip()) > 3]
        for s in sentences:
            clean_s = re.sub(r"\\(?:documentclass|begin|end)\{[^}]*\}", "", s).strip()
            if len(clean_s) > 3:
                nodes.append({
                    "temp_id": f"s_{sent_idx}",
                    "kind": "sentence",
                    "content": clean_s,
                    "parent_temp_id": root_id,
                })
                sent_idx += 1
        return {"arxiv_id": arxiv_id, "version": version, "nodes": nodes}

    for i, match in enumerate(matches):
        sec_type = "section" if "subsection" not in match.group(1) else "subsection"
        sec_title = match.group(2).strip()
        sec_id = f"h_{sec_idx}"
        sec_idx += 1

        nodes.append({
            "temp_id": sec_id,
            "kind": sec_type,
            "content": sec_title,
            "parent_temp_id": root_id if sec_type == "section" else current_parent,
        })
        current_parent = sec_id

        # Text between this section and next
        start_body = match.end()
        end_body = matches[i + 1].start() if i + 1 < len(matches) else len(clean_tex)
        body = clean_tex[start_body:end_body]

        sentences = [s.strip() for s in body.split(".") if len(s.strip()) > 3]
        for s in sentences:
            clean_s = re.sub(r"\\end\{document\}", "", s).strip()
            if len(clean_s) > 3:
                nodes.append({
                    "temp_id": f"s_{sent_idx}",
                    "kind": "sentence",
                    "content": clean_s,
                    "parent_temp_id": current_parent,
                })
                sent_idx += 1

    return {"arxiv_id": arxiv_id, "version": version, "nodes": nodes}


def extract_fallback_references(raw_tex: str, arxiv_id: str) -> PaperReferences:
    """Extract references from thebibliography environment as baseline."""
    bib_pattern = re.compile(
        r"\\bibitem(?:\[.*?\])?\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\}|\Z)",
        re.DOTALL,
    )
    entries = []
    remap: Dict[int, Dict[str, str]] = {}

    for match in bib_pattern.finditer(raw_tex):
        key = match.group(1).strip()
        content = re.sub(r"\s+", " ", match.group(2)).strip()
        entries.append({
            "arxiv_id": arxiv_id,
            "citation_key": key,
            "entry_type": "article",
            "title": content[:120],
            "authors": [],
            "year": None,
            "doi": "",
            "journal": "",
            "url": "",
        })

    return {
        "arxiv_id": arxiv_id,
        "unified_bibtex": entries,
        "citation_remap": remap,
    }


def process_single_publication(arxiv_id: str, paper_dir: Path | str) -> PublicationParsingResult:
    """Process all versions of a single publication and return deduplicated relational tables."""
    paper_path = Path(paper_dir)
    sources_dir = paper_path / "sources"
    hierarchies: List[VersionHierarchy] = []
    empty_versions: List[int] = []
    combined_raw_tex = ""

    if not sources_dir.exists():
        return deduplicate_publication_elements(arxiv_id, [], None, empty_versions=[1])

    version_subdirs = sorted(
        [d for d in sources_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
        key=lambda d: int(d.name[1:]) if d.name[1:].isdigit() else 999,
    )

    if not version_subdirs:
        # Check if sources_dir itself contains tex files
        tex_files = sorted(list(sources_dir.glob("*.tex")))
        if tex_files and any(f.stat().st_size > 0 for f in tex_files):
            tex_content = "\n".join(
                f.read_text(encoding="utf-8", errors="ignore") for f in tex_files
            )
            tree = parse_fallback_hierarchy(tex_content, arxiv_id, 1)
            hierarchies.append(tree)
            combined_raw_tex += tex_content
        else:
            empty_versions.append(1)
    else:
        for v_dir in version_subdirs:
            v_num = int(v_dir.name[1:]) if v_dir.name[1:].isdigit() else 1
            tex_files = sorted(list(v_dir.rglob("*.tex")))
            if not tex_files or sum(f.stat().st_size for f in tex_files) == 0:
                empty_versions.append(v_num)
                continue

            # Concatenate TeX content (in production Module 1 gather_source provides merged TeX)
            raw_tex = "\n".join(f.read_text(encoding="utf-8", errors="ignore") for f in tex_files)
            combined_raw_tex += "\n" + raw_tex
            tree = parse_fallback_hierarchy(raw_tex, arxiv_id, v_num)
            hierarchies.append(tree)

    refs = extract_fallback_references(combined_raw_tex, arxiv_id) if combined_raw_tex else None
    return deduplicate_publication_elements(
        arxiv_id, hierarchies, refs, empty_versions=empty_versions
    )


def run_parsing_pipeline(
    corpus_dir: Path | str,
    db_path: Path | str,
    limit: Optional[int] = None,
    batch_size: int = 100,
) -> Dict[str, Any]:
    """Execute the end-to-end parsing pipeline with streaming commits and memory tracking."""
    corpus_path = Path(corpus_dir)
    db = ParsingDatabase(db_path)

    paper_dirs = sorted([d for d in corpus_path.iterdir() if d.is_dir() and not d.name.startswith(".")])
    if limit is not None:
        paper_dirs = paper_dirs[:limit]

    total = len(paper_dirs)
    processed = 0
    t0 = time.time()
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()

    logger.info("Starting pipeline on %d publications...", total)

    try:
        for i, p_dir in enumerate(paper_dirs, start=1):
            arxiv_id = p_dir.name
            try:
                res = process_single_publication(arxiv_id, p_dir)
                db.save_publication(res)
                processed += 1
            except Exception as e:
                logger.error("Failed processing %s: %s", arxiv_id, e)

            if i % batch_size == 0 or i == total:
                db.commit()
                current, peak = tracemalloc.get_traced_memory()
                logger.info(
                    "Progress: %d/%d (%.1f%%) | RAM Peak: %.2f MB",
                    i,
                    total,
                    (i / total) * 100 if total > 0 else 100.0,
                    peak / (1024 * 1024),
                )

        current, peak = tracemalloc.get_traced_memory()
        total_time = time.time() - t0
        avg_time = (total_time / processed) if processed > 0 else 0.0

        return {
            "total_publications": total,
            "processed_publications": processed,
            "total_wall_time_sec": total_time,
            "avg_time_per_paper_sec": avg_time,
            "peak_ram_mb": peak / (1024 * 1024),
            "db_path": str(db_path),
        }
    finally:
        if not already_tracing:
            tracemalloc.stop()
        db.close()
