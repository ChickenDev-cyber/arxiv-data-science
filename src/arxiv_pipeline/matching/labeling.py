"""Labeling and Heuristics Module for Milestone 3 (Module B - Mẫn Nhi).

This module implements:
1. Manual labeling templates and validation.
2. Independent, rule-based heuristic automatic reference matcher (frozen rules/thresholds).
3. Label merging, quota verification, consistency checks, and reporting.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ==============================================================================
# Constants & Contract Specifications
# ==============================================================================

HEURISTIC_VERSION = "heuristic-v1.0.0"

VALID_LABEL_METHODS = {"manual", "automatic"}
VALID_LABEL_STATUSES = {"matched", "no_match", "ambiguous", "unreviewed"}
VALID_OWNERS = {"trong", "huynh-nhi", "khang", "man-nhi", "hoang"}

# Explicit PyArrow schema for labels.parquet according to Milestone 3 contract
LABELS_ARROW_SCHEMA = pa.schema([
    ("arxiv_id", pa.string()),
    ("citation_key", pa.string()),
    ("groundtruth_arxiv_id", pa.string()),  # Nullable
    ("label_method", pa.string()),          # 'manual' | 'automatic'
    ("labeler", pa.string()),               # owner name or 'heuristic'
    ("label_status", pa.string()),          # 'matched' | 'no_match' | 'ambiguous' | 'unreviewed'
    ("evidence", pa.string()),              # Explanation / rule matched
    ("heuristic_version", pa.string()),    # Nullable ('heuristic-v1.0.0' or null)
])


# ==============================================================================
# String Normalization & Feature Helpers for Heuristic Matching
# ==============================================================================

_LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+(?:\[[^\]]*\])?\{([^}]*)\}")
_BRACES_RE = re.compile(r"[{}]")
_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")

# arXiv ID patterns:
# New style: e.g. 2106.09685, 2501.13787, 0704.1234
# Old style: e.g. hep-th/0608012, math/0501234, cs.CV/0102003
_ARXIV_NEW_RE = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b", re.IGNORECASE)
_ARXIV_OLD_RE = re.compile(r"\b([a-zA-Z\-]+(?:\.[a-zA-Z]{2})?/\d{7})(?:v\d+)?\b", re.IGNORECASE)

_ROMAN_MAP = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10"
}


def normalize_title(title: Optional[str]) -> str:
    """Normalize a paper/reference title for robust matching.
    
    Removes LaTeX macros, punctuation, extra spaces, and converts to lowercase.
    """
    if not title or not isinstance(title, str):
        return ""
    # Strip common LaTeX markup
    t = _LATEX_CMD_RE.sub(r"\1", title)
    t = _BRACES_RE.sub("", t)
    t = t.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WHITESPACE_RE.sub(" ", t)
    return t.strip()


def extract_arxiv_id(text: Optional[str]) -> Optional[str]:
    """Extract and normalize an arXiv ID from text (URL, DOI, journal, or title).
    
    Returns clean arXiv ID without version suffix (e.g. '2106.09685', 'hep-th/0608012').
    """
    if not text or not isinstance(text, str):
        return None
    m_new = _ARXIV_NEW_RE.search(text)
    if m_new:
        return m_new.group(1)
    m_old = _ARXIV_OLD_RE.search(text)
    if m_old:
        return m_old.group(1)
    return None


def extract_part_spec(title: Optional[str]) -> Optional[str]:
    """Extract multi-part paper numeral ('Part I', 'Part II', 'Part 1', 'Part 2', or trailing 'I', 'II', etc.).
    
    Returns normalized arabic numeral string (e.g. '1', '2') or None.
    Handles trailing roman numerals and digits directly at end of title (e.g. 'black holes II' -> '2').
    """
    if not title or not isinstance(title, str):
        return None
    clean = title.strip()
    # 1. Explicit keyword: Part/Paper/Pt/Section/Chapter
    m = re.search(r"\b(?:part|paper|pt\.?|section|chapter)\s*([ivx]+|\d+)\b", clean, re.IGNORECASE)
    if m:
        val = m.group(1).lower()
        return _ROMAN_MAP.get(val, val)
    # 2. In parentheses e.g. "(I)", "(II)", "(1)", "(2)"
    m = re.search(r"\(([ivx]+|\d+)\)\s*[\.\?!]?\s*$", clean, re.IGNORECASE)
    if m:
        val = m.group(1).lower()
        return _ROMAN_MAP.get(val, val)
    # 3. After colon/dash/comma e.g. ": I", "- II", ", 1"
    m = re.search(r"[:\-,]\s*([ivx]+|\d+)\s*[\.\?!]?\s*$", clean, re.IGNORECASE)
    if m:
        val = m.group(1).lower()
        return _ROMAN_MAP.get(val, val)
    # 4. Standalone Roman numeral or digit at the very end of title!
    # e.g. "Linear waves in the interior of extremal black holes I" -> "1"
    # "Linear waves in the interior of extremal black holes II" -> "2"
    m = re.search(r"\b(i{1,3}|iv|v|vi{0,3}|ix|x|\d{1,2})\s*[\.\?!]?\s*$", clean, re.IGNORECASE)
    if m:
        val = m.group(1).lower()
        return _ROMAN_MAP.get(val, val)
    return None


def get_title_tokens(title: str) -> Set[str]:
    """Extract set of informative title tokens (preserving hyphenated compound tokens)."""
    if not title:
        return set()
    # Extract words, preserving hyphenated terms like lora-fa, gpt-4
    words = re.findall(r"\b[a-z0-9]+(?:-[a-z0-9]+)*\b", title.lower())
    stopwords = {
        "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with",
        "by", "from", "via", "is", "are", "was", "were", "using", "based", "towards"
    }
    return {w for w in words if len(w) > 2 and w not in stopwords}


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Compute Jaccard similarity between two sets of tokens."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def token_containment(set_sub: Set[str], set_super: Set[str]) -> float:
    """Compute token containment ratio: |set_sub & set_super| / |set_sub|."""
    if not set_sub:
        return 0.0
    return len(set_sub & set_super) / len(set_sub)


def extract_author_lastnames(authors: Any) -> List[str]:
    """Extract lowercased last names safely from any authors representation.
    
    Handles: list, tuple, np.ndarray, pd.Series, JSON string, or string separated by 'and'/comma.
    """
    if authors is None:
        return []
    if isinstance(authors, float) and pd.isna(authors):
        return []

    # If it's a JSON array string e.g. '["Emparan, Roberto", "Reall, Harvey S"]'
    if isinstance(authors, str):
        stripped = authors.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                authors = json.loads(stripped)
            except Exception:
                pass

    if isinstance(authors, str):
        raw_list = re.split(r"\s+and\s+|,|;", authors)
    elif isinstance(authors, (list, tuple, np.ndarray, pd.Series)):
        raw_list = list(authors)
    elif hasattr(authors, "__iter__") and not isinstance(authors, (bytes, dict)):
        raw_list = list(authors)
    else:
        return []

    names: List[str] = []
    for item in raw_list:
        if item is None or (isinstance(item, float) and pd.isna(item)):
            continue
        if isinstance(item, dict):
            name = str(item.get("name", ""))
        elif isinstance(item, str):
            name = item
        else:
            name = str(item)

        clean = _BRACES_RE.sub("", name).strip()
        if not clean:
            continue
        if "," in clean:
            last = clean.split(",")[0].strip().lower()
        else:
            parts = clean.split()
            last = parts[-1].strip().lower() if parts else ""
        last_clean = _PUNCT_RE.sub("", last)
        if last_clean:
            names.append(last_clean)
    return names


def parse_year(val: Any) -> Optional[int]:
    """Extract integer year from string, int, or ISO date."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    m = re.search(r"\b(19\d\d|20\d\d)\b", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


# ==============================================================================
# Heuristic Matcher Core Logic
# ==============================================================================

def match_bibtex_entry_to_candidates(
    bib_row: Dict[str, Any],
    candidate_refs: List[Dict[str, Any]],
    version: str = HEURISTIC_VERSION,
) -> Tuple[str, Optional[str], str]:
    """Match a single BibTeX entry against candidate references of the paper.
    
    Returns:
        (label_status, groundtruth_arxiv_id, evidence)
        where:
        - label_status is one of: 'matched', 'no_match', 'ambiguous'
        - groundtruth_arxiv_id is non-null only if status == 'matched'
        - evidence is a descriptive explanation of the decision
    """
    if not candidate_refs:
        return "no_match", None, "no_candidates_available"

    # Step 1: Direct arXiv ID extraction from BibTeX metadata (url, doi, journal, title)
    extracted_arxiv_id = (
        extract_arxiv_id(bib_row.get("url"))
        or extract_arxiv_id(bib_row.get("doi"))
        or extract_arxiv_id(bib_row.get("journal"))
        or extract_arxiv_id(bib_row.get("title"))
    )

    if extracted_arxiv_id:
        matched_by_id = [
            r for r in candidate_refs
            if str(r.get("ref_arxiv_id")) == str(extracted_arxiv_id)
        ]
        if len(matched_by_id) == 1:
            return (
                "matched",
                matched_by_id[0]["ref_arxiv_id"],
                f"direct_arxiv_id_match:{extracted_arxiv_id}"
            )
        elif len(matched_by_id) > 1:
            return (
                "matched",
                matched_by_id[0]["ref_arxiv_id"],
                f"direct_arxiv_id_match_dedup:{extracted_arxiv_id}"
            )
        else:
            # CRITICAL RULE: When BibTeX explicitly cites an arXiv ID, but that ID
            # does not exist in the crawled references of this paper, DO NOT fall back
            # to title matching to pick a different paper!
            return (
                "no_match",
                None,
                f"explicit_arxiv_id_not_in_references:{extracted_arxiv_id}"
            )

    # Step 2: Title-based & Metadata Matching
    raw_bib_title = str(bib_row.get("title", "")) if bib_row.get("title") else ""
    norm_bib_title = normalize_title(raw_bib_title)
    bib_tokens = get_title_tokens(raw_bib_title)
    bib_authors = extract_author_lastnames(bib_row.get("authors"))
    bib_year = parse_year(bib_row.get("year"))
    bib_part = extract_part_spec(raw_bib_title)

    if not norm_bib_title:
        return "no_match", None, "missing_title"

    # Step 2.1: Check exact normalized title match
    exact_title_matches = []
    for ref in candidate_refs:
        ref_title_raw = str(ref.get("ref_title", "")) if ref.get("ref_title") else ""
        norm_ref_title = normalize_title(ref_title_raw)
        if norm_ref_title and norm_ref_title == norm_bib_title:
            # Check part numeral consistency
            ref_part = extract_part_spec(ref_title_raw)
            if (bib_part or ref_part) and (bib_part != ref_part):
                continue
            exact_title_matches.append(ref)

    if len(exact_title_matches) == 1:
        chosen = exact_title_matches[0]
        return "matched", chosen["ref_arxiv_id"], "exact_normalized_title_match"
    elif len(exact_title_matches) > 1:
        # Multiple refs have the exact same title (e.g. different versions or duplicates)
        # Disambiguate using author or year if possible
        if bib_authors:
            author_filtered = [
                r for r in exact_title_matches
                if set(bib_authors) & set(extract_author_lastnames(r.get("ref_authors")))
            ]
            if len(author_filtered) == 1:
                return "matched", author_filtered[0]["ref_arxiv_id"], "exact_title_author_disambiguated"
        return "ambiguous", None, "multiple_exact_title_matches"

    if len(norm_bib_title) < 8 or not bib_tokens:
        # Title is too short or has no informative tokens for reliable fuzzy matching
        return "no_match", None, "insufficient_title_tokens_for_fuzzy"

    # Step 3: Fuzzy / Token Similarity with Author & Year validation
    # Extract hyphenated compound tokens (e.g. lora-fa, resnet-50) from bib title
    bib_compounds = {w for w in bib_tokens if "-" in w}

    scored_candidates = []
    for ref in candidate_refs:
        ref_title_raw = str(ref.get("ref_title", "")) if ref.get("ref_title") else ""
        ref_part = extract_part_spec(ref_title_raw)

        # Multi-part veto: Part I vs Part II or presence/absence mismatch must NEVER match!
        if (bib_part or ref_part) and (bib_part != ref_part):
            continue

        ref_tokens = get_title_tokens(ref_title_raw)
        if not ref_tokens:
            continue

        # Compound token veto: e.g. LoRA-FA vs LoRA
        ref_compounds = {w for w in ref_tokens if "-" in w}
        if bib_compounds != ref_compounds:
            # If one title has a compound model variant like 'lora-fa' that differs from ref
            mismatch_compound = False
            for c in bib_compounds:
                base = c.split("-")[0]
                if base in ref_tokens and c not in ref_tokens:
                    mismatch_compound = True
                    break
            for c in ref_compounds:
                base = c.split("-")[0]
                if base in bib_tokens and c not in bib_tokens:
                    mismatch_compound = True
                    break
            if mismatch_compound:
                continue

        jaccard = jaccard_similarity(bib_tokens, ref_tokens)
        contain_bib_in_ref = token_containment(bib_tokens, ref_tokens)
        contain_ref_in_bib = token_containment(ref_tokens, bib_tokens)
        min_contain = min(contain_bib_in_ref, contain_ref_in_bib)

        # Both titles must contain a substantial fraction of each other
        if min_contain < 0.65 or jaccard < 0.65:
            continue

        title_score = (jaccard + min_contain) / 2.0

        # Check author agreement
        ref_authors = extract_author_lastnames(ref.get("ref_authors"))
        author_match = False
        if bib_authors and ref_authors:
            if bib_authors[0] in ref_authors or ref_authors[0] in bib_authors:
                author_match = True
            elif set(bib_authors) & set(ref_authors):
                author_match = True

        # Check year agreement
        ref_year = parse_year(ref.get("ref_submission_date"))
        year_diff = abs(bib_year - ref_year) if (bib_year and ref_year) else None
        year_compatible = (year_diff is None) or (year_diff <= 1)

        # Boost / penalty
        final_score = title_score
        if author_match:
            final_score += 0.08
        if year_compatible and (bib_year is not None and ref_year is not None):
            final_score += 0.04
        elif year_diff is not None and year_diff > 3:
            final_score -= 0.15

        scored_candidates.append({
            "ref": ref,
            "title_score": title_score,
            "final_score": final_score,
            "author_match": author_match,
            "year_compatible": year_compatible,
        })

    if not scored_candidates:
        return "no_match", None, "no_valid_candidate_scores"

    # Sort descending by final_score
    scored_candidates.sort(key=lambda x: x["final_score"], reverse=True)
    best = scored_candidates[0]

    # Matching thresholds (conservative to prevent false positives)
    MATCH_THRESHOLD = 0.82
    AMBIGUITY_MARGIN = 0.10

    if best["final_score"] >= MATCH_THRESHOLD and best["title_score"] >= 0.70:
        # Check for ambiguity with the second-best candidate
        if len(scored_candidates) > 1:
            second = scored_candidates[1]
            if (
                second["final_score"] >= (MATCH_THRESHOLD - 0.05)
                and (best["final_score"] - second["final_score"]) < AMBIGUITY_MARGIN
            ):
                return (
                    "ambiguous",
                    None,
                    f"ambiguous_candidates:best={best['final_score']:.3f},second={second['final_score']:.3f}"
                )

        return (
            "matched",
            best["ref"]["ref_arxiv_id"],
            f"high_similarity_match:score={best['final_score']:.3f},title_jaccard={best['title_score']:.3f}"
        )

    # If best score is below threshold
    return "no_match", None, f"low_similarity:best_score={best['final_score']:.3f}"


def run_heuristic_matcher(
    bibtex_df: pd.DataFrame,
    references_df: pd.DataFrame,
    target_paper_ids: Optional[Set[str]] = None,
    version: str = HEURISTIC_VERSION,
) -> pd.DataFrame:
    """Run the automatic heuristic matcher on a set of papers.
    
    Generates labels for EVERY BibTeX entry of the target papers.
    """
    if target_paper_ids is not None:
        bibtex_df = bibtex_df[bibtex_df["arxiv_id"].isin(target_paper_ids)].copy()
        references_df = references_df[references_df["arxiv_id"].isin(target_paper_ids)].copy()

    # Pre-group references by arxiv_id
    refs_by_paper: Dict[str, List[Dict[str, Any]]] = {}
    for row in references_df.to_dict(orient="records"):
        paper_id = str(row["arxiv_id"])
        refs_by_paper.setdefault(paper_id, []).append(row)

    rows = []
    for bib in bibtex_df.to_dict(orient="records"):
        paper_id = str(bib["arxiv_id"])
        citation_key = str(bib["citation_key"])
        cands = refs_by_paper.get(paper_id, [])

        status, gt_id, evidence = match_bibtex_entry_to_candidates(
            bib, cands, version=version
        )

        rows.append({
            "arxiv_id": paper_id,
            "citation_key": citation_key,
            "groundtruth_arxiv_id": gt_id if status == "matched" else None,
            "label_method": "automatic",
            "labeler": "heuristic",
            "label_status": status,
            "evidence": evidence,
            "heuristic_version": version,
        })

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        return pd.DataFrame(columns=[
            "arxiv_id", "citation_key", "groundtruth_arxiv_id",
            "label_method", "labeler", "label_status", "evidence", "heuristic_version"
        ])
    return result_df


# ==============================================================================
# Manual Template Generation
# ==============================================================================

def generate_manual_template(
    inventory_path: Path,
    pool_path: Path,
    owner: str,
    output_path: Path,
) -> Path:
    """Generate a manual labeling template for a specific owner.
    
    Pre-populates all BibTeX entries for the owner's manual papers in the pool,
    along with candidate references. Does NOT run heuristic matching or suggest
    labels, preserving unbiased manual labeling.
    """
    inventory_path = Path(inventory_path)
    inventory_data = load_inventory(inventory_path)
    pool_df = pd.read_parquet(pool_path)

    owner_manual_papers = set(
        pool_df[
            (pool_df["owner"] == owner) & (pool_df["label_method"] == "manual")
        ]["arxiv_id"].unique()
    )

    if not owner_manual_papers:
        raise ValueError(f"No manual papers found for owner '{owner}' in pool '{pool_path}'")

    bibtex_df = load_table_from_inventory(
        inventory_data, "bibtex", inventory_dir=inventory_path.parent, owner=owner
    )
    owner_bib = bibtex_df[bibtex_df["arxiv_id"].isin(owner_manual_papers)].copy()

    refs_df = load_table_from_inventory(
        inventory_data, "references", inventory_dir=inventory_path.parent, owner=owner
    )
    owner_refs = refs_df[refs_df["arxiv_id"].isin(owner_manual_papers)].copy()

    # Create candidate ref list map
    refs_by_paper: Dict[str, List[Dict[str, Any]]] = {}
    for r in owner_refs.to_dict(orient="records"):
        refs_by_paper.setdefault(str(r["arxiv_id"]), []).append(r)

    template_rows = []
    for bib in owner_bib.to_dict(orient="records"):
        paper_id = str(bib["arxiv_id"])
        ckey = str(bib["citation_key"])
        cands = refs_by_paper.get(paper_id, [])

        # Default template row: unreviewed, NO heuristic suggestions
        template_rows.append({
            "arxiv_id": paper_id,
            "citation_key": ckey,
            "groundtruth_arxiv_id": None,
            "label_method": "manual",
            "labeler": owner,
            "label_status": "unreviewed",
            "evidence": "",
            "heuristic_version": None,
            # Auxiliary columns to guide human reviewer (stripped on final save)
            "_bib_title": bib.get("title", ""),
            "_bib_authors": str(bib.get("authors", "")),
            "_bib_year": str(bib.get("year", "")),
            "_candidate_ref_ids": ",".join([str(r.get("ref_arxiv_id", "")) for r in cands]),
        })

    res_df = pd.DataFrame(template_rows)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.suffix.lower() == ".csv":
        res_df.to_csv(output_path, index=False, encoding="utf-8")
    else:
        table = pa.Table.from_pandas(res_df)
        pq.write_table(table, output_path)

    return output_path


# ==============================================================================
# Validation & Quota Checking
# ==============================================================================

def validate_labels(
    labels_df: pd.DataFrame,
    pool_df: pd.DataFrame,
    references_df: pd.DataFrame,
    bibtex_df: Optional[pd.DataFrame] = None,
    official_counts: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Perform rigorous validation and quota checking on the unified labels DataFrame.
    
    Verifies:
    1. Schema compliance (required columns, non-null fields, allowed values).
    2. Key uniqueness and conflict check:
       (arxiv_id, citation_key) must be unique. Duplicate keys with conflicting GT
       or different methods are strictly flagged as fatal errors.
    3. BibTeX existence check:
       (arxiv_id, citation_key) must exist in bibtex table. Non-existent keys trigger
       errors and are NOT counted as matched towards quota.
    4. Isolation & Provenance:
       manual papers must have label_method == 'manual', heuristic_version is null,
       and correct labeler matching owner.
    5. Ground truth integrity:
       - Every 'matched' row must have groundtruth_arxiv_id existing in references of that paper.
       - Every non-'matched' row must have null groundtruth_arxiv_id.
    6. Quotas:
       - Manual quota: each owner >= 5 manual papers, each paper >= 20 matched pairs.
       - Group manual quota: >= 25 papers, >= 500 matched pairs.
       - Automatic quota: each owner >= max(2, ceil(0.10 * (N - M))) papers (read from key N).
         Failing automatic quota marks validation as NOT PASSED.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. Column and Type check
    required_cols = [
        "arxiv_id", "citation_key", "groundtruth_arxiv_id",
        "label_method", "labeler", "label_status", "evidence", "heuristic_version"
    ]
    missing_cols = set(required_cols) - set(labels_df.columns)
    if missing_cols:
        errors.append(f"Missing required columns in labels: {missing_cols}")
        return {"passed": False, "errors": errors, "warnings": warnings}

    # Non-null checks
    for col in ["arxiv_id", "citation_key", "label_method", "labeler", "label_status", "evidence"]:
        null_count = labels_df[col].isna().sum()
        if null_count > 0:
            errors.append(f"Column '{col}' contains {null_count} null values (non-null required)")

    # Value checks
    invalid_methods = set(labels_df["label_method"].unique()) - VALID_LABEL_METHODS
    if invalid_methods:
        errors.append(f"Invalid label_method values: {invalid_methods}")

    invalid_statuses = set(labels_df["label_status"].unique()) - VALID_LABEL_STATUSES
    if invalid_statuses:
        errors.append(f"Invalid label_status values: {invalid_statuses}")

    # 2. Key uniqueness and Conflict check
    dup_mask = labels_df.duplicated(subset=["arxiv_id", "citation_key"], keep=False)
    if dup_mask.any():
        conflicts = []
        for (pid, ckey), group in labels_df[dup_mask].groupby(["arxiv_id", "citation_key"]):
            gts = set(group["groundtruth_arxiv_id"].dropna().unique())
            methods = set(group["label_method"].unique())
            statuses = set(group["label_status"].unique())
            conflicts.append(
                f"({pid}, {ckey}): methods={methods}, statuses={statuses}, groundtruths={gts}"
            )
        errors.append(
            f"Found {len(conflicts)} duplicate/conflicting (arxiv_id, citation_key) entries: "
            + "; ".join(conflicts[:5])
        )

    # 3. Check (arxiv_id, citation_key) existence in BibTeX
    valid_bibtex_keys: Optional[Set[Tuple[str, str]]] = None
    if bibtex_df is not None and not bibtex_df.empty:
        valid_bibtex_keys = set(
            zip(bibtex_df["arxiv_id"].astype(str), bibtex_df["citation_key"].astype(str))
        )
        non_existent_keys = []
        for row in labels_df.to_dict(orient="records"):
            key = (str(row["arxiv_id"]), str(row["citation_key"]))
            if key not in valid_bibtex_keys:
                non_existent_keys.append(key)
        if non_existent_keys:
            errors.append(
                f"Found {len(non_existent_keys)} entries in labels that DO NOT exist in bibtex table: "
                + "; ".join([f"({p}, {k})" for p, k in non_existent_keys[:5]])
            )

    # Build reference lookup: (arxiv_id, ref_arxiv_id)
    ref_lookup: Set[Tuple[str, str]] = set()
    for row in references_df[["arxiv_id", "ref_arxiv_id"]].to_dict(orient="records"):
        ref_lookup.add((str(row["arxiv_id"]), str(row["ref_arxiv_id"])))

    # 4. Ground truth consistency
    matched_mask = labels_df["label_status"] == "matched"
    non_matched_mask = ~matched_mask

    # 'matched' must have non-null groundtruth_arxiv_id
    null_gt_in_matched = labels_df[matched_mask]["groundtruth_arxiv_id"].isna().sum()
    if null_gt_in_matched > 0:
        errors.append(f"Found {null_gt_in_matched} 'matched' rows with null groundtruth_arxiv_id")

    # non-'matched' must have null groundtruth_arxiv_id
    non_null_gt_in_unmatched = labels_df[non_matched_mask]["groundtruth_arxiv_id"].notna().sum()
    if non_null_gt_in_unmatched > 0:
        errors.append(f"Found {non_null_gt_in_unmatched} non-matched rows with non-null groundtruth_arxiv_id")

    # Ground truth must exist in references of the citing paper
    invalid_gt_refs = 0
    for row in labels_df[matched_mask].to_dict(orient="records"):
        key = (str(row["arxiv_id"]), str(row["groundtruth_arxiv_id"]))
        if key not in ref_lookup:
            invalid_gt_refs += 1
    if invalid_gt_refs > 0:
        errors.append(
            f"Found {invalid_gt_refs} 'matched' entries where groundtruth_arxiv_id does NOT exist in candidate references of that paper"
        )

    # 5. Isolation: manual papers must not be processed by automatic heuristic
    pool_methods = dict(zip(pool_df["arxiv_id"], pool_df["label_method"]))

    manual_papers_in_pool = {p for p, m in pool_methods.items() if m == "manual"}

    # Check if any manual paper was labeled as automatic
    auto_on_manual = labels_df[
        (labels_df["arxiv_id"].isin(manual_papers_in_pool)) & (labels_df["label_method"] == "automatic")
    ]
    if len(auto_on_manual) > 0:
        errors.append(f"CRITICAL: Found {len(auto_on_manual)} entries of manual papers processed with automatic heuristic!")

    # Check heuristic_version for manual rows (must be null)
    manual_with_heuristic_ver = labels_df[
        (labels_df["label_method"] == "manual") & (labels_df["heuristic_version"].notna())
    ]
    if len(manual_with_heuristic_ver) > 0:
        errors.append(f"Found {len(manual_with_heuristic_ver)} manual entries with non-null heuristic_version")

    # 6. Quota Verification
    quota_report: Dict[str, Any] = {
        "manual": {"passed": True, "by_owner": {}},
        "automatic": {"passed": True, "by_owner": {}},
    }

    # Manual Quotas
    total_manual_papers = 0
    total_manual_matches = 0

    owners_in_pool = pool_df["owner"].unique()
    for owner in owners_in_pool:
        owner_pool_manual = pool_df[
            (pool_df["owner"] == owner) & (pool_df["label_method"] == "manual")
        ]["arxiv_id"].unique()

        owner_labels = labels_df[labels_df["arxiv_id"].isin(owner_pool_manual)]

        # Valid matched entries MUST exist in bibtex (if bibtex provided)
        valid_matched_mask = (owner_labels["label_status"] == "matched")
        if valid_bibtex_keys is not None:
            in_bib = owner_labels.apply(
                lambda r: (str(r["arxiv_id"]), str(r["citation_key"])) in valid_bibtex_keys,
                axis=1
            )
            valid_matched_mask = valid_matched_mask & in_bib

        matched_counts_per_paper = (
            owner_labels[valid_matched_mask]
            .groupby("arxiv_id")
            .size()
            .to_dict()
        )

        owner_n_papers = len(owner_pool_manual)
        owner_matched_total = sum(matched_counts_per_paper.values())
        total_manual_papers += owner_n_papers
        total_manual_matches += owner_matched_total

        papers_below_20 = [
            pid for pid in owner_pool_manual
            if matched_counts_per_paper.get(pid, 0) < 20
        ]

        owner_manual_passed = (owner_n_papers >= 5) and (len(papers_below_20) == 0)
        if not owner_manual_passed:
            quota_report["manual"]["passed"] = False
            if owner_n_papers < 5:
                errors.append(f"Owner '{owner}' manual papers count = {owner_n_papers} (< 5 required)")
            if papers_below_20:
                errors.append(
                    f"Owner '{owner}' has {len(papers_below_20)} manual papers with < 20 matched pairs: {papers_below_20}"
                )

        quota_report["manual"]["by_owner"][owner] = {
            "n_manual_papers": owner_n_papers,
            "total_matched_pairs": owner_matched_total,
            "passed": owner_manual_passed,
            "papers_below_20_matches": papers_below_20,
            "matched_per_paper": matched_counts_per_paper,
        }

    # Automatic Quotas (strict check reading N)
    for owner in owners_in_pool:
        owner_pool_auto = pool_df[
            (pool_df["owner"] == owner) & (pool_df["label_method"] == "automatic")
        ]["arxiv_id"].unique()
        auto_count = len(owner_pool_auto)

        # Read N from official_counts dict or default 5000
        n_official = official_counts.get(owner, 5000) if official_counts else 5000
        m_manual = len(pool_df[(pool_df["owner"] == owner) & (pool_df["label_method"] == "manual")])
        required_auto = max(2, math.ceil(0.10 * (n_official - m_manual)))

        auto_passed = auto_count >= required_auto
        if not auto_passed:
            quota_report["automatic"]["passed"] = False
            errors.append(
                f"Owner '{owner}' automatic papers count = {auto_count} "
                f"(FAILED QUOTA: required at least {required_auto} from formula max(2, ceil(0.10 * (N - M))) with N={n_official}, M={m_manual})"
            )

        quota_report["automatic"]["by_owner"][owner] = {
            "n_automatic_papers": auto_count,
            "required_papers": required_auto,
            "n_official_N": n_official,
            "passed": auto_passed,
        }

    # Summary statistics
    status_counts = labels_df["label_status"].value_counts().to_dict()
    method_counts = labels_df["label_method"].value_counts().to_dict()

    passed = len(errors) == 0

    return {
        "passed": passed,
        "total_rows": len(labels_df),
        "status_distribution": status_counts,
        "method_distribution": method_counts,
        "quota_report": quota_report,
        "errors": errors,
        "warnings": warnings,
        "heuristic_version": HEURISTIC_VERSION,
    }


# ==============================================================================
# I/O & Contract Runner (build_labels)
# ==============================================================================

def load_inventory(inventory_input: Path | dict) -> dict:
    """Load inventory configuration from path or return dict directly."""
    if isinstance(inventory_input, dict):
        return inventory_input
    with open(inventory_input, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_shard_path(raw_path: str | Path, base_dir: Optional[Path] = None) -> Path:
    """Resolve a shard path relative to base directory (inventory dir) first."""
    p = Path(raw_path)
    if p.is_absolute():
        return p
    # Relative path: ALWAYS resolve relative to base_dir (inventory directory) first!
    if base_dir is not None:
        cand = (base_dir / p).resolve()
        return cand
    return p.resolve()


def load_table_from_inventory(
    inventory_data: dict,
    table_name: str,
    inventory_dir: Optional[Path] = None,
    owner: Optional[str] = None,
) -> pd.DataFrame:
    """Load a table DataFrame from inventory paths.
    
    Resolves relative paths according to inventory_dir (without prioritizing CWD).
    Reads shared shards declared across multiple owners only once to avoid duplication.
    """
    dfs = []
    raw_shard_paths: List[str] = []

    # Structure 1: inventory["owners"][owner]["tables"][table_name]
    if "owners" in inventory_data:
        owners_dict = inventory_data["owners"]
        target_owners = [owner] if owner else list(owners_dict.keys())
        for o in target_owners:
            if o in owners_dict and "tables" in owners_dict[o]:
                paths = owners_dict[o]["tables"].get(table_name, [])
                if isinstance(paths, str):
                    paths = [paths]
                raw_shard_paths.extend(paths)

    # Structure 2: inventory["tables"][table_name]
    elif "tables" in inventory_data:
        paths = inventory_data["tables"].get(table_name, [])
        if isinstance(paths, str):
            paths = [paths]
        raw_shard_paths.extend(paths)

    # Structure 3: direct key
    elif table_name in inventory_data:
        paths = inventory_data[table_name]
        if isinstance(paths, str):
            paths = [paths]
        raw_shard_paths.extend(paths)

    if raw_shard_paths:
        # Read each unique resolved shard path ONLY ONCE
        unique_shards: List[Tuple[str, Path]] = []
        seen_canonical_paths: Set[str] = set()

        for raw_p in raw_shard_paths:
            resolved = _resolve_shard_path(raw_p, base_dir=inventory_dir)
            canon_str = str(resolved.resolve()) if resolved.exists() else str(resolved)
            if canon_str not in seen_canonical_paths:
                seen_canonical_paths.add(canon_str)
                unique_shards.append((raw_p, resolved))

        for raw_p, resolved in unique_shards:
            if not resolved.exists():
                raise FileNotFoundError(
                    f"Missing required shard: '{raw_p}' (resolved to '{resolved}') for table '{table_name}'. "
                    "All declared shards must exist."
                )
            dfs.append(pd.read_parquet(resolved))

        if dfs:
            merged = pd.concat(dfs, ignore_index=True)
            # Deduplicate rows by primary key
            if table_name == "bibtex" and "arxiv_id" in merged.columns and "citation_key" in merged.columns:
                merged = merged.drop_duplicates(subset=["arxiv_id", "citation_key"], keep="first")
            elif table_name == "references" and "arxiv_id" in merged.columns and "ref_arxiv_id" in merged.columns:
                merged = merged.drop_duplicates(subset=["arxiv_id", "ref_arxiv_id"], keep="first")
            elif table_name == "papers" and "arxiv_id" in merged.columns:
                merged = merged.drop_duplicates(subset=["arxiv_id"], keep="first")
            return merged

    # Fallback to direct check on inputs/<owner>/<table_name>
    inputs_dir = (inventory_dir / "inputs") if inventory_dir else Path("inputs")
    if inputs_dir.exists():
        found = list(inputs_dir.glob(f"*/{table_name}/*.parquet"))
        if found:
            dfs = [pd.read_parquet(f) for f in found]
            merged = pd.concat(dfs, ignore_index=True)
            if table_name == "bibtex" and "arxiv_id" in merged.columns and "citation_key" in merged.columns:
                merged = merged.drop_duplicates(subset=["arxiv_id", "citation_key"], keep="first")
            return merged

    raise FileNotFoundError(
        f"Could not load table '{table_name}' from inventory. Verify inventory.json paths."
    )


def build_labels(
    inventory: Path,
    pool: Path,
    manual_dir: Path,
    output_dir: Path,
) -> Path:
    """Milestone 3 Contract Function: Build unified labels.parquet and label_report.json.
    
    Halts export if validation fails or conflicting duplicate labels exist.
    Validates provenance strictly without overwriting label_method, labeler, or heuristic_version.
    """
    inventory_path = Path(inventory)
    pool_path = Path(pool)
    manual_dir_path = Path(manual_dir)
    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    inventory_data = load_inventory(inventory_path)
    pool_df = pd.read_parquet(pool_path)

    # 1. Load bibtex and references tables (resolving shards via inventory parent dir)
    bibtex_df = load_table_from_inventory(
        inventory_data, "bibtex", inventory_dir=inventory_path.parent
    )
    references_df = load_table_from_inventory(
        inventory_data, "references", inventory_dir=inventory_path.parent
    )

    # 2. Separate pool into manual and automatic
    manual_pool = pool_df[pool_df["label_method"] == "manual"]
    auto_pool = pool_df[pool_df["label_method"] == "automatic"]

    manual_paper_ids = set(manual_pool["arxiv_id"].unique())
    auto_paper_ids = set(auto_pool["arxiv_id"].unique())

    # 3. Process Manual Labels
    manual_dfs = []
    # Search for <owner>.parquet or <owner>.csv in manual_dir
    for owner in pool_df["owner"].unique():
        candidates = [
            manual_dir_path / f"{owner}.parquet",
            manual_dir_path / f"manual_{owner}.parquet",
            manual_dir_path / f"{owner}.csv",
            manual_dir_path / f"manual_{owner}.csv",
        ]
        found_file = next((f for f in candidates if f.exists()), None)
        if found_file:
            if found_file.suffix.lower() == ".parquet":
                m_df = pd.read_parquet(found_file)
            else:
                # Force string types to avoid loss of ID formatting (e.g. leading zeros)
                m_df = pd.read_csv(found_file, dtype=str, keep_default_na=False)

            # Filter out auxiliary columns starting with '_'
            clean_cols = [c for c in m_df.columns if not c.startswith("_")]
            m_df = m_df[clean_cols].copy()

            # Ensure ID columns are string
            m_df["arxiv_id"] = m_df["arxiv_id"].astype(str)
            m_df["citation_key"] = m_df["citation_key"].astype(str)

            # STRICT PROVENANCE VALIDATION (DO NOT OVERWRITE INPUT VALUES)
            if "label_method" in m_df.columns:
                invalid_method_mask = m_df["label_method"] != "manual"
                if invalid_method_mask.any():
                    raise ValueError(
                        f"Provenance error in manual file for '{owner}': found {invalid_method_mask.sum()} rows "
                        f"with label_method != 'manual' (values: {m_df.loc[invalid_method_mask, 'label_method'].unique()}). "
                        "Manual input files must declare label_method='manual' and will not be overwritten."
                    )
            else:
                raise ValueError(f"Manual file for '{owner}' is missing required column 'label_method'.")

            if "heuristic_version" in m_df.columns:
                non_null_h = m_df[
                    m_df["heuristic_version"].notna()
                    & (m_df["heuristic_version"] != "")
                    & (m_df["heuristic_version"] != "nan")
                ]
                if len(non_null_h) > 0:
                    raise ValueError(
                        f"Provenance error in manual file for '{owner}': found {len(non_null_h)} rows with non-null "
                        f"heuristic_version='{non_null_h['heuristic_version'].unique()}'. "
                        "Manual input labels must have null heuristic_version and will not be overwritten."
                    )

            if "labeler" in m_df.columns:
                invalid_labeler_mask = m_df["labeler"] != owner
                if invalid_labeler_mask.any():
                    raise ValueError(
                        f"Provenance error in manual file for '{owner}': found {invalid_labeler_mask.sum()} rows "
                        f"with labeler != '{owner}' (found: {m_df.loc[invalid_labeler_mask, 'labeler'].unique()}). "
                        "Provenance labeler must match the declared owner and will not be overwritten."
                    )
            else:
                raise ValueError(f"Manual file for '{owner}' is missing required column 'labeler'.")

            # Filter to manual papers of this owner in the pool
            owner_manual_ids = set(
                manual_pool[manual_pool["owner"] == owner]["arxiv_id"].unique()
            )
            m_df = m_df[m_df["arxiv_id"].isin(owner_manual_ids)].copy()

            # Clean empty strings to None
            m_df["groundtruth_arxiv_id"] = m_df["groundtruth_arxiv_id"].where(
                m_df["groundtruth_arxiv_id"].notna() & (m_df["groundtruth_arxiv_id"] != "") & (m_df["groundtruth_arxiv_id"] != "nan"),
                None
            )
            m_df["heuristic_version"] = None

            manual_dfs.append(m_df)

    if manual_dfs:
        manual_labels_df = pd.concat(manual_dfs, ignore_index=True)
    else:
        manual_labels_df = pd.DataFrame(columns=[
            "arxiv_id", "citation_key", "groundtruth_arxiv_id",
            "label_method", "labeler", "label_status", "evidence", "heuristic_version"
        ])

    # Ensure every entry of manual papers is represented (fill missing as unreviewed)
    manual_bib = bibtex_df[bibtex_df["arxiv_id"].isin(manual_paper_ids)].copy()
    existing_manual_keys = set(
        zip(manual_labels_df["arxiv_id"].astype(str), manual_labels_df["citation_key"].astype(str))
    ) if not manual_labels_df.empty else set()

    missing_manual_rows = []
    paper_owner_map = dict(zip(pool_df["arxiv_id"], pool_df["owner"]))
    for bib in manual_bib.to_dict(orient="records"):
        pid = str(bib["arxiv_id"])
        ckey = str(bib["citation_key"])
        if (pid, ckey) not in existing_manual_keys:
            missing_manual_rows.append({
                "arxiv_id": pid,
                "citation_key": ckey,
                "groundtruth_arxiv_id": None,
                "label_method": "manual",
                "labeler": paper_owner_map.get(pid, "unreviewed"),
                "label_status": "unreviewed",
                "evidence": "unreviewed_entry",
                "heuristic_version": None,
            })

    if missing_manual_rows:
        missing_df = pd.DataFrame(missing_manual_rows)
        manual_labels_df = pd.concat([manual_labels_df, missing_df], ignore_index=True)

    # 4. Process Automatic Labels with Frozen Heuristics
    # NOTE: Never run heuristic on manual_paper_ids!
    auto_labels_df = run_heuristic_matcher(
        bibtex_df=bibtex_df,
        references_df=references_df,
        target_paper_ids=auto_paper_ids,
        version=HEURISTIC_VERSION,
    )

    # 5. Merge all labels
    unified_df = pd.concat([manual_labels_df, auto_labels_df], ignore_index=True)

    # Normalize types
    unified_df["arxiv_id"] = unified_df["arxiv_id"].astype(str)
    unified_df["citation_key"] = unified_df["citation_key"].astype(str)
    unified_df["label_method"] = unified_df["label_method"].astype(str)
    unified_df["labeler"] = unified_df["labeler"].astype(str)
    unified_df["label_status"] = unified_df["label_status"].astype(str)
    unified_df["evidence"] = unified_df["evidence"].fillna("").astype(str)

    # Nullable string handling
    unified_df["groundtruth_arxiv_id"] = unified_df["groundtruth_arxiv_id"].where(
        unified_df["groundtruth_arxiv_id"].notna() & (unified_df["groundtruth_arxiv_id"] != "") & (unified_df["groundtruth_arxiv_id"] != "nan"),
        None
    )
    unified_df["heuristic_version"] = unified_df["heuristic_version"].where(
        unified_df["heuristic_version"].notna() & (unified_df["heuristic_version"] != "") & (unified_df["heuristic_version"] != "nan"),
        None
    )

    # Reorder columns to match canonical contract
    ordered_cols = [
        "arxiv_id", "citation_key", "groundtruth_arxiv_id",
        "label_method", "labeler", "label_status", "evidence", "heuristic_version"
    ]
    unified_df = unified_df[ordered_cols].copy()

    # 6. Extract official counts N from inventory
    official_counts: Dict[str, int] = {}
    if "owners" in inventory_data:
        for o, info in inventory_data["owners"].items():
            if isinstance(info, dict):
                n_val = info.get("N", info.get("n_official", info.get("n_papers", None)))
                if n_val is not None:
                    official_counts[o] = int(n_val)

    # 7. Run full validation & quota checking (including bibtex existence check)
    report = validate_labels(
        labels_df=unified_df,
        pool_df=pool_df,
        references_df=references_df,
        bibtex_df=bibtex_df,
        official_counts=official_counts,
    )

    # Always write label_report.json
    label_report_path = output_dir_path / "label_report.json"
    with open(label_report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # 8. HALT IF VALIDATION FAILS (Never export labels.parquet if validation has errors)
    if not report["passed"]:
        error_msg = f"Validation failed with {len(report['errors'])} error(s). labels.parquet export was ABORTED.\n" + "\n".join(report["errors"])
        raise ValueError(error_msg)

    # 9. Write outputs if validation passed
    labels_parquet_path = output_dir_path / "labels.parquet"
    arrow_table = pa.Table.from_pandas(unified_df, schema=LABELS_ARROW_SCHEMA, preserve_index=False)
    pq.write_table(arrow_table, labels_parquet_path)

    return labels_parquet_path


# ==============================================================================
# CLI Interface
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Milestone 3 Labeling & Heuristics CLI (Module B)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: build
    build_parser = subparsers.add_parser("build", help="Build unified labels.parquet from pool and manual files")
    build_parser.add_argument("--inventory", type=Path, required=True, help="Path to inventory.json")
    build_parser.add_argument("--pool", type=Path, required=True, help="Path to pool.parquet")
    build_parser.add_argument("--manual-dir", type=Path, required=True, help="Directory containing manual/<owner>.parquet")
    build_parser.add_argument("--output-dir", type=Path, required=True, help="Directory to save labels.parquet and label_report.json")

    # Command: template
    template_parser = subparsers.add_parser("template", help="Generate manual labeling template for an owner")
    template_parser.add_argument("--inventory", type=Path, required=True, help="Path to inventory.json")
    template_parser.add_argument("--pool", type=Path, required=True, help="Path to pool.parquet")
    template_parser.add_argument("--owner", type=str, required=True, help="Owner name (e.g. man-nhi, trong)")
    template_parser.add_argument("--output", type=Path, required=True, help="Output path (e.g. manual/man-nhi.parquet or .csv)")

    args = parser.parse_args()

    if args.command == "build":
        out = build_labels(args.inventory, args.pool, args.manual_dir, args.output_dir)
        print(f"Successfully generated labels at: {out}")
    elif args.command == "template":
        out = generate_manual_template(args.inventory, args.pool, args.owner, args.output)
        print(f"Successfully generated manual template at: {out}")


if __name__ == "__main__":
    main()
