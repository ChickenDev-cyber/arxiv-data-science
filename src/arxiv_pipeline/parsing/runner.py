"""Pipeline orchestrator for hierarchical parsing, standardization, and storage."""

from dataclasses import dataclass
import json
import inspect
import logging
import re
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from arxiv_pipeline.parsing.contracts import (
    BibtexEntry,
    GatheredSource,
    LocalNode,
    PaperReferences,
    PublicationParsingResult,
    VersionHierarchy,
)
from arxiv_pipeline.parsing.dedup import deduplicate_publication_elements
from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.parsing.storage import ParsingDatabase

logger = logging.getLogger(__name__)

_BIB_CMD_RE = re.compile(r"\\(?:bibliography|addbibresource)\s*\{([^}]+)\}")
_CITE_KEY_RE = re.compile(
    r"(\\(?:cite[a-z]*|autocite[a-z]*|parencite[a-z]*|textcite[a-z]*)\*?\s*(?:\[[^]]*\]\s*){0,2}\{)([^{}]*)(\})",
    re.IGNORECASE,
)
_NOCITE_ALL_RE = re.compile(r"\\nocite\s*\{\s*\*\s*\}")


def _strip_tex_comments(tex: str) -> str:
    """Remove unescaped LaTeX % comments line-by-line."""
    return re.sub(r"(?<!\\)%.*$", "", tex, flags=re.MULTILINE)


@dataclass
class PipelineComponents:
    """Pluggable module hooks implementing M1 -> M4 -> Rewrite -> M2/M3 -> M5."""

    gather_fn: Optional[Callable[[Path, str, int], GatheredSource]] = None
    refs_fn: Optional[Callable[..., PaperReferences]] = None
    rewrite_fn: Optional[Callable[[str, Dict[str, str]], str]] = None
    hierarchy_fn: Optional[Callable[[GatheredSource], VersionHierarchy]] = None

    def resolve(
        self,
    ) -> Tuple[
        Callable[[Path, str, int], GatheredSource],
        Callable[..., PaperReferences],
        Callable[[str, Dict[str, str]], str],
        Callable[[GatheredSource], VersionHierarchy],
    ]:
        return (
            self.gather_fn or default_gather_source,
            self.refs_fn or default_extract_references,
            self.rewrite_fn or default_rewrite_citations,
            self.hierarchy_fn or default_build_hierarchy,
        )


def _resolve_bib_paths(source_dir: Path, root_dir: Path, merged_nocomment: str) -> List[str]:
    """Find actual .bib files declared via \\bibliography or \\addbibresource."""
    resolved: List[str] = []
    seen: Set[str] = set()
    all_bibs: Optional[List[Path]] = None

    for group in _BIB_CMD_RE.findall(merged_nocomment):
        for raw_item in group.split(","):
            ref = raw_item.strip()
            if not ref:
                continue
            name = ref if ref.lower().endswith(".bib") else f"{ref}.bib"

            candidates = [root_dir / name, source_dir / name]
            matched_path = next((c for c in candidates if c.is_file()), None)
            if matched_path is None:
                if all_bibs is None:
                    all_bibs = sorted(source_dir.rglob("*.bib"))
                target_name = Path(name).name
                matched_path = next((b for b in all_bibs if b.name == target_name), None)

            if matched_path is not None:
                resolved_str = str(matched_path)
                if resolved_str not in seen:
                    seen.add(resolved_str)
                    resolved.append(resolved_str)

    return resolved


def default_gather_source(source_dir: Path, arxiv_id: str, version: int) -> GatheredSource:
    """Default baseline Module 1 gatherer supporting .tex, active .bib, and .bbl files."""
    if not source_dir.exists() or not source_dir.is_dir():
        return {
            "arxiv_id": arxiv_id,
            "version": version,
            "root_file": None,
            "compilation_path": [],
            "merged_tex": "",
            "bib_files": [],
            "bbl_files": [],
            "status": "empty_source",
        }

    tex_files = sorted(source_dir.rglob("*.tex"))
    if not tex_files or sum(f.stat().st_size for f in tex_files) == 0:
        return {
            "arxiv_id": arxiv_id,
            "version": version,
            "root_file": None,
            "compilation_path": [],
            "merged_tex": "",
            "bib_files": [],
            "bbl_files": [],
            "status": "empty_source",
        }

    root_path = tex_files[0]
    parts: List[str] = []
    comp_path: List[str] = []
    for f in tex_files:
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if r"\documentclass" in _strip_tex_comments(txt):
            root_path = f
        parts.append(txt)
        comp_path.append(str(f.relative_to(source_dir)))

    merged_tex = "\n".join(parts)
    merged_nocomment = _strip_tex_comments(merged_tex)
    bib_files = _resolve_bib_paths(source_dir, root_path.parent, merged_nocomment)
    bbl_files = sorted(str(f) for f in source_dir.rglob("*.bbl"))

    return {
        "arxiv_id": arxiv_id,
        "version": version,
        "root_file": str(root_path.relative_to(source_dir)),
        "compilation_path": comp_path,
        "merged_tex": merged_tex,
        "bib_files": bib_files,
        "bbl_files": bbl_files,
        "status": "ok",
    }


def _extract_balanced_block(text: str, open_idx: int) -> Tuple[str, int]:
    """Extract content inside balanced {...} or (...) starting at open_idx."""
    open_ch = text[open_idx]
    close_ch = "}" if open_ch == "{" else ")"
    depth = 0
    in_quotes = False
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == '"' and open_ch == "{" and (i == 0 or text[i - 1] != "\\"):
            in_quotes = not in_quotes
        elif not in_quotes or open_ch == "(":
            if ch == open_ch and (i == 0 or text[i - 1] != "\\"):
                depth += 1
            elif ch == close_ch and (i == 0 or text[i - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    return text[open_idx + 1 : i], i
        i += 1
    return text[open_idx + 1 :], len(text) - 1


def _strip_bib_wrappers(val: str) -> str:
    val = re.sub(r"\s+", " ", val).strip()
    if len(val) >= 2 and ((val[0] == "{" and val[-1] == "}") or (val[0] == '"' and val[-1] == '"')):
        val = val[1:-1].strip()
    return val.replace("{", "").replace("}", "")


def _parse_bib_fields(body: str, macros: Dict[str, str]) -> Dict[str, str]:
    """Parse key = value pairs from the body of a .bib entry."""
    fields: Dict[str, str] = {}
    i = 0
    n = len(body)
    while i < n:
        match = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_-]*)\s*=\s*", body[i:])
        if not match:
            i += 1
            continue
        field_name = match.group(1).lower()
        val_start = i + match.end()
        if val_start >= n:
            break

        ch = body[val_start]
        if ch == "{":
            raw_val, end_idx = _extract_balanced_block(body, val_start)
            val = _strip_bib_wrappers("{" + raw_val + "}")
            i = end_idx + 1
        elif ch == '"':
            j = val_start + 1
            while j < n:
                if body[j] == '"' and body[j - 1] != "\\":
                    break
                j += 1
            val = _strip_bib_wrappers(body[val_start : j + 1])
            i = j + 1
        else:
            comma_idx = body.find(",", val_start)
            end_idx = comma_idx if comma_idx != -1 else n
            token = body[val_start:end_idx].strip()
            val = macros.get(token.lower(), token)
            i = end_idx + 1

        fields[field_name] = val
        while i < n and body[i] in " ,\t\r\n":
            i += 1
    return fields


def _parse_bib_file_entries(
    bib_text: str,
    arxiv_id: str,
    cited_keys: Optional[Set[str]] = None,
) -> List[BibtexEntry]:
    """Parse @entry{key, ...} blocks from a .bib file, expanding @String macros."""
    macros: Dict[str, str] = {}
    entries: List[BibtexEntry] = []
    pos = 0
    n = len(bib_text)

    while pos < n:
        at_idx = bib_text.find("@", pos)
        if at_idx == -1:
            break
        hdr = re.match(r"@([a-zA-Z]+)\s*([{(])", bib_text[at_idx:])
        if not hdr:
            pos = at_idx + 1
            continue

        entry_type = hdr.group(1).lower()
        open_idx = at_idx + hdr.start(2)
        block_content, close_idx = _extract_balanced_block(bib_text, open_idx)
        pos = close_idx + 1

        if entry_type in ("comment", "preamble"):
            continue
        if entry_type == "string":
            kv = re.match(r"\s*([a-zA-Z0-9_-]+)\s*=\s*(.+)", block_content, re.DOTALL)
            if kv:
                macros[kv.group(1).strip().lower()] = _strip_bib_wrappers(kv.group(2))
            continue

        comma_idx = block_content.find(",")
        if comma_idx == -1:
            continue
        citation_key = block_content[:comma_idx].strip()
        if cited_keys is not None and citation_key not in cited_keys:
            continue

        fields = _parse_bib_fields(block_content[comma_idx + 1 :], macros)
        authors_raw = fields.get("author", "")
        authors = [a.strip() for a in re.split(r"\s+and\s+", authors_raw) if a.strip()] if authors_raw else []
        year_match = re.search(r"\b(19\d{2}|20\d{2}|2100)\b", fields.get("year", ""))

        entries.append({
            "arxiv_id": arxiv_id,
            "citation_key": citation_key,
            "entry_type": entry_type,
            "title": fields.get("title", ""),
            "authors": authors,
            "year": int(year_match.group(1)) if year_match else None,
            "doi": fields.get("doi", ""),
            "journal": fields.get("journal", fields.get("booktitle", "")),
            "url": fields.get("url", ""),
        })

    return entries


def _cited_keys_in_tex(tex: str) -> Optional[Set[str]]:
    """Return cited keys in tex, or None if \\nocite{*} keeps all .bib entries."""
    nocomment = _strip_tex_comments(tex)
    if _NOCITE_ALL_RE.search(nocomment):
        return None
    keys: Set[str] = set()
    for match in _CITE_KEY_RE.finditer(nocomment):
        for k in match.group(2).split(","):
            cleaned = k.strip()
            if cleaned:
                keys.add(cleaned)
    return keys


def parse_fallback_hierarchy(raw_tex: str, arxiv_id: str, version: int) -> VersionHierarchy:
    """Standard baseline parser for converting raw TeX stream into VersionHierarchy."""
    nodes: List[LocalNode] = []
    root_id = "root"
    nodes.append({
        "temp_id": root_id,
        "kind": "document",
        "content": f"Version {version} root",
        "parent_temp_id": None,
    })

    clean_tex = re.sub(
        r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", raw_tex, flags=re.DOTALL
    )
    section_pattern = re.compile(r"\\(section\*?|subsection\*?)\{([^}]+)\}")
    current_parent = root_id
    sec_idx = 1
    sent_idx = 1

    matches = list(section_pattern.finditer(clean_tex))
    if not matches:
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


def default_build_hierarchy(source: GatheredSource) -> VersionHierarchy:
    """Build with Module 2 standardization and the production hierarchy parser."""
    if source.get("status") != "ok" or not source.get("merged_tex", "").strip():
        return {"arxiv_id": source["arxiv_id"], "version": source["version"], "nodes": []}
    return build_hierarchy(source)


def extract_fallback_references(raw_tex: str, arxiv_id: str) -> PaperReferences:
    """Extract references from thebibliography environment as baseline."""
    bib_pattern = re.compile(
        r"\\bibitem(?:\[.*?\])?\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\}|\Z)",
        re.DOTALL,
    )
    entries: List[BibtexEntry] = []
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
    return {"arxiv_id": arxiv_id, "unified_bibtex": entries, "citation_remap": {}}


def default_extract_references(
    sources: List[GatheredSource],
    source_dirs: Optional[Dict[int, Path]] = None,
) -> PaperReferences:
    """Use the shared Module 4 parser with explicit per-version source roots."""
    from arxiv_pipeline.bibtex.service import extract_and_unify_references

    return extract_and_unify_references(sources, source_dirs=source_dirs)


def default_rewrite_citations(text: str, remap: Dict[str, str]) -> str:
    """Rewrite citation keys in \\cite, \\citep, \\citet, and related commands."""
    if not remap:
        return text

    def replace(match: re.Match[str]) -> str:
        keys = [k.strip() for k in match.group(2).split(",")]
        rewritten = [remap.get(k, k) for k in keys]
        return match.group(1) + ",".join(rewritten) + match.group(3)

    return _CITE_KEY_RE.sub(replace, text)


def _classify_bib_source(sources: List[GatheredSource]) -> str:
    """Classify bibliography source coverage across non-empty versions."""
    active = [s for s in sources if s.get("status") == "ok" and s.get("merged_tex", "").strip()]
    if not active:
        return "none"

    has_bib_files = any(bool(s.get("bib_files")) for s in active)
    has_bibitem = any(
        ("thebibliography" in _strip_tex_comments(s.get("merged_tex", ""))) or bool(s.get("bbl_files"))
        for s in active
    )
    if has_bib_files and has_bibitem:
        return "both"
    if has_bib_files:
        return "bib_file"
    if has_bibitem:
        return "bibitem"

    has_bib_cmd = any(bool(_BIB_CMD_RE.search(_strip_tex_comments(s.get("merged_tex", "")))) for s in active)
    return "bbl_missing" if has_bib_cmd else "none"


def discover_paper_dirs(corpus_path: Path) -> List[Path]:
    """Discover publication directories within corpus, handling flat, nested, and M1 raw layouts."""
    if not corpus_path.is_dir():
        return []

    direct_papers = [
        d for d in corpus_path.iterdir()
        if d.is_dir() and not d.name.startswith(".")
        and ((d / "metadata.json").exists() or (d / "v1").is_dir() or (d / "sources").is_dir())
    ]
    if direct_papers:
        return sorted(direct_papers, key=lambda d: d.name)

    raw_dir = corpus_path / "raw"
    if raw_dir.is_dir():
        raw_sub = [d for d in raw_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        papers = []
        for sub in raw_sub:
            if (sub / "metadata.json").exists() or (sub / "v1").is_dir() or (sub / "sources").is_dir():
                papers.append(sub)
            else:
                for p in sub.iterdir():
                    if p.is_dir() and not p.name.startswith(".") and (
                        (p / "metadata.json").exists() or (p / "v1").is_dir() or (p / "sources").is_dir()
                    ):
                        papers.append(p)
        if papers:
            return sorted(papers, key=lambda d: d.name)

    return sorted(
        [d for d in corpus_path.iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.name,
    )


def process_single_publication(
    arxiv_id: str,
    paper_dir: Path | str,
    components: Optional[PipelineComponents] = None,
) -> PublicationParsingResult:
    """Process all versions of a publication through M1 -> M4 -> Rewrite -> M2/M3 -> M5."""
    gather_fn, refs_fn, rewrite_fn, hierarchy_fn = (components or PipelineComponents()).resolve()
    paper_path = Path(paper_dir)

    meta_file = paper_path / "metadata.json"
    declared_versions: List[int] = []
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("arxiv_id"):
                arxiv_id = meta["arxiv_id"]
            if meta.get("versions"):
                declared_versions = [v["version"] for v in meta["versions"] if "version" in v]
        except Exception as e:
            logger.warning("Could not parse metadata.json in %s: %s", paper_dir, e)

    sources_dir = paper_path / "sources" if (paper_path / "sources").is_dir() else paper_path
    version_subdirs = sorted(
        [d for d in sources_dir.iterdir() if d.is_dir() and d.name.startswith("v")],
        key=lambda d: int(d.name[1:]) if d.name[1:].isdigit() else 999,
    )

    gathered_sources: List[GatheredSource] = []
    empty_versions: List[int] = []

    if not version_subdirs:
        src = gather_fn(sources_dir, arxiv_id, 1)
        if src.get("status") != "ok" or not src.get("merged_tex", "").strip():
            empty_versions.extend(declared_versions if declared_versions else [1])
        else:
            gathered_sources.append(src)
    else:
        found_version_nums: Set[int] = set()
        for v_dir in version_subdirs:
            v_num = int(v_dir.name[1:]) if v_dir.name[1:].isdigit() else 1
            found_version_nums.add(v_num)
            src = gather_fn(v_dir, arxiv_id, v_num)
            if src.get("status") != "ok" or not src.get("merged_tex", "").strip():
                empty_versions.append(v_num)
            else:
                gathered_sources.append(src)

        for d_ver in declared_versions:
            if d_ver not in found_version_nums and d_ver not in empty_versions:
                empty_versions.append(d_ver)

    bib_source_kind = _classify_bib_source(gathered_sources)
    source_dirs: Dict[int, Path] = {}
    if version_subdirs:
        source_dirs = {
            int(directory.name[1:]): directory
            for directory in version_subdirs
            if directory.name[1:].isdigit()
        }
    else:
        source_dirs = {1: sources_dir}
    if gathered_sources:
        try:
            accepts_source_dirs = "source_dirs" in inspect.signature(refs_fn).parameters
        except (TypeError, ValueError):
            accepts_source_dirs = False
        refs = refs_fn(gathered_sources, source_dirs=source_dirs) if accepts_source_dirs else refs_fn(gathered_sources)
    else:
        refs = None
    remap_by_ver = refs.get("citation_remap", {}) if refs else {}

    hierarchies: List[VersionHierarchy] = []
    for src in gathered_sources:
        v_remap = remap_by_ver.get(src["version"], {})
        rewritten_tex = rewrite_fn(src.get("merged_tex", ""), v_remap) if v_remap else src.get("merged_tex", "")
        rewritten_src = dict(src)
        rewritten_src["merged_tex"] = rewritten_tex
        tree = hierarchy_fn(rewritten_src)  # type: ignore[arg-type]
        hierarchies.append(tree)

    result = deduplicate_publication_elements(
        arxiv_id,
        hierarchies,
        refs,
        empty_versions=sorted(list(set(empty_versions))),
    )
    result["bib_source_kind"] = bib_source_kind
    return result


def run_parsing_pipeline(
    corpus_dir: Path | str,
    db_path: Path | str,
    limit: Optional[int] = None,
    batch_size: int = 100,
    components: Optional[PipelineComponents] = None,
) -> Dict[str, Any]:
    """Execute the end-to-end parsing pipeline with streaming commits and memory tracking."""
    corpus_path = Path(corpus_dir)
    paper_dirs = discover_paper_dirs(corpus_path)
    if limit is not None:
        paper_dirs = paper_dirs[:limit]

    total = len(paper_dirs)
    processed = 0
    t0 = time.time()
    already_tracing = tracemalloc.is_tracing()
    if not already_tracing:
        tracemalloc.start()

    logger.info("Starting pipeline on %d publications...", total)

    with ParsingDatabase(db_path) as db:
        for i, p_dir in enumerate(paper_dirs, start=1):
            arxiv_id = p_dir.name
            try:
                res = process_single_publication(arxiv_id, p_dir, components=components)
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
        corpus_stats = db.get_corpus_statistics()

    total_time = time.time() - t0
    avg_time = (total_time / processed) if processed > 0 else 0.0
    db_file = Path(db_path)
    db_size_mb = db_file.stat().st_size / (1024 * 1024) if db_file.exists() else 0.0

    if not already_tracing:
        tracemalloc.stop()

    summary: Dict[str, Any] = {
        "total_publications": total,
        "processed_publications": processed,
        "total_wall_time_sec": total_time,
        "avg_time_per_paper_sec": avg_time,
        "peak_ram_mb": peak / (1024 * 1024),
        "db_size_mb": db_size_mb,
        "db_path": str(db_path),
    }
    summary.update(corpus_stats)
    return summary
