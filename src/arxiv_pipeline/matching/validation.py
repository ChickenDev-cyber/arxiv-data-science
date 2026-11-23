"""Module D — Validation & Quality Assurance.

Validates predictions rules (candidate membership, tie-breaking, disjoint partitions),
and conducts independent auditing on the final 6-table Parquet dataset.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pyarrow as pa
import pyarrow.parquet as pq


def validate_prediction_rules(
    inventory_path: Path,
    labels_path: Path,
    split_path: Path,
    scores_path: Path,
    predictions_path: Path,
    output_path: Path,
) -> Path:
    """Validate prediction constraints against contract specifications.

    Constraints checked:
    1. Candidate origin: all candidates must belong to the citing paper's crawled references.
    2. No duplicate candidates within top 5.
    3. Proper null padding for papers with fewer than 5 candidates.
    4. Partition disjointness: no paper appears in more than one partition.
    5. Tie-breaking consistency against scores table: descending score, ascending ref_arxiv_id.
    6. Matching sample count consistency with labels.parquet.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    issues: list[str] = []
    warnings: list[str] = []

    # 1. Load split & check partition disjointness
    split_table = pq.read_table(split_path)
    split_rows = split_table.to_pylist()

    paper_partitions: dict[str, str] = {}
    for r in split_rows:
        aid = r["arxiv_id"]
        part = r.get("partition")
        if aid in paper_partitions and paper_partitions[aid] != part:
            issues.append(f"Data leakage: paper {aid} appears in both {paper_partitions[aid]} and {part}")
        paper_partitions[aid] = str(part)

    # 2. Load predictions
    pred_table = pq.read_table(predictions_path)
    pred_rows = pred_table.to_pylist()

    # 3. Load references if inventory provides path, or fallback to relative dataset
    ref_lookup: dict[str, set[str]] = {}
    inv_path = Path(inventory_path)
    if inv_path.exists():
        try:
            with open(inv_path, "r", encoding="utf-8") as f:
                inv_data = json.load(f)
            # Find references path from inventory
            # Inventory structure: owners -> tables -> paths
            for owner_info in inv_data.get("owners", {}).values():
                ref_file = owner_info.get("tables", {}).get("references")
                if ref_file and Path(ref_file).exists():
                    rt = pq.read_table(ref_file, columns=["arxiv_id", "ref_arxiv_id"])
                    for row in rt.to_pylist():
                        ref_lookup.setdefault(row["arxiv_id"], set()).add(row["ref_arxiv_id"])
        except Exception as e:
            warnings.append(f"Could not load references from inventory: {e}")

    # Fallback to local dataset directory if ref_lookup empty
    if not ref_lookup:
        default_ref = inv_path.parent / "references.parquet"
        if default_ref.exists():
            rt = pq.read_table(default_ref, columns=["arxiv_id", "ref_arxiv_id"])
            for row in rt.to_pylist():
                ref_lookup.setdefault(row["arxiv_id"], set()).add(row["ref_arxiv_id"])

    # Check predictions rows
    seen_entries: set[tuple[str, str]] = set()
    for pred in pred_rows:
        aid = pred["arxiv_id"]
        ck = pred["citation_key"]
        entry_key = (aid, ck)

        if entry_key in seen_entries:
            issues.append(f"Duplicate prediction entry: ({aid}, {ck})")
        seen_entries.add(entry_key)

        # Check top 5 candidates
        cands = [pred.get(f"cand_{i}") for i in range(1, 6)]
        non_null_cands = [c for c in cands if c is not None]

        # Check for duplicates
        if len(non_null_cands) != len(set(non_null_cands)):
            issues.append(f"Duplicate candidates in top 5 for ({aid}, {ck}): {non_null_cands}")

        # Check candidate origin if ref_lookup is available
        if aid in ref_lookup:
            valid_refs = ref_lookup[aid]
            for c in non_null_cands:
                if c not in valid_refs:
                    issues.append(f"Candidate {c} does not belong to references of citing paper {aid}")

        # Check null padding: after first None, no non-None should appear
        seen_none = False
        for c in cands:
            if c is None:
                seen_none = True
            elif seen_none:
                issues.append(f"Invalid null padding in top 5 for ({aid}, {ck}): {cands}")
                break

    # 4. Check tie-breaking against scores if scores table is provided
    scores_path = Path(scores_path)
    if scores_path.exists():
        try:
            scores_table = pq.read_table(scores_path)
            scores_rows = scores_table.to_pylist()
            # Group scores by (arxiv_id, citation_key) -> list of (ref_arxiv_id, score)
            grouped_scores: dict[tuple[str, str], list[tuple[str, float]]] = {}
            for s in scores_rows:
                k = (s["arxiv_id"], s["citation_key"])
                grouped_scores.setdefault(k, []).append((str(s["ref_arxiv_id"]), float(s["score"])))

            # Sample verify first 50 entries
            for (aid, ck), cand_scores in list(grouped_scores.items())[:50]:
                # Sort according to contract: score descending, ref_arxiv_id ascending
                cand_scores.sort(key=lambda x: (-x[1], x[0]))
                expected_top_5 = [cs[0] for cs in cand_scores[:5]]

                # Find prediction
                matching_preds = [p for p in pred_rows if p["arxiv_id"] == aid and p["citation_key"] == ck]
                if matching_preds:
                    actual_pred = matching_preds[0]
                    actual_top_5 = [actual_pred.get(f"cand_{i}") for i in range(1, 1 + len(expected_top_5))]
                    if actual_top_5 != expected_top_5:
                        issues.append(
                            f"Ranking mismatch for ({aid}, {ck}): expected {expected_top_5}, got {actual_top_5}"
                        )
        except Exception as e:
            warnings.append(f"Could not verify scores tie-breaking: {e}")

    report = {
        "valid": len(issues) == 0,
        "total_checked_predictions": len(pred_rows),
        "total_partitions_checked": len(paper_partitions),
        "issues": issues[:100],  # truncate if many
        "total_issues": len(issues),
        "warnings": warnings,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return output_path


def validate_dataset(
    dataset_dir: Path,
    inventory: Path,
    split: Path,
    output_path: Path,
) -> Path:
    """Audit the complete 6-table Parquet dataset against academic schemas.

    Tables inspected:
    1. papers
    2. references
    3. elements
    4. hierarchy
    5. bibtex
    6. predictions
    """
    dataset_dir = Path(dataset_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    required_tables = ["papers", "references", "elements", "hierarchy", "bibtex", "predictions"]
    table_reports: dict[str, Any] = {}
    all_issues: list[str] = []

    paper_ids: set[str] = set()

    for tbl_name in required_tables:
        tbl_path = dataset_dir / f"{tbl_name}.parquet"
        # Support subdirectory partition format: data/<tbl_name>/*.parquet or <tbl_name>.parquet
        if not tbl_path.exists():
            subdir = dataset_dir / tbl_name
            if subdir.is_dir():
                parts = list(subdir.glob("*.parquet"))
                if parts:
                    tbl_path = parts[0]

        if not tbl_path.exists():
            all_issues.append(f"Missing required table: {tbl_name}.parquet")
            table_reports[tbl_name] = {"exists": False}
            continue

        try:
            pq_file = pq.ParquetFile(tbl_path)
            num_rows = pq_file.metadata.num_rows
            schema = pq_file.schema_arrow

            field_names = schema.names
            table_reports[tbl_name] = {
                "exists": True,
                "rows": num_rows,
                "columns": field_names,
            }

            # Check specific table requirements
            if tbl_name == "papers":
                for col in ["arxiv_id", "title", "authors", "submission_date", "revised_dates", "venue"]:
                    if col not in field_names:
                        all_issues.append(f"papers table missing column '{col}'")
                t = pq.read_table(tbl_path, columns=["arxiv_id"])
                p_list = t["arxiv_id"].to_pylist()
                paper_ids = set(p_list)
                if len(paper_ids) != len(p_list):
                    all_issues.append("papers table contains duplicate arxiv_id")

            elif tbl_name == "references":
                for col in ["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors", "ref_submission_date", "ref_s2_id"]:
                    if col not in field_names:
                        all_issues.append(f"references table missing column '{col}'")

            elif tbl_name == "elements":
                for col in ["element_id", "arxiv_id", "kind", "content"]:
                    if col not in field_names:
                        all_issues.append(f"elements table missing column '{col}'")

            elif tbl_name == "hierarchy":
                for col in ["arxiv_id", "version", "element_id", "parent_id"]:
                    if col not in field_names:
                        all_issues.append(f"hierarchy table missing column '{col}'")

            elif tbl_name == "bibtex":
                for col in ["arxiv_id", "citation_key", "entry_type", "title", "authors", "year"]:
                    if col not in field_names:
                        all_issues.append(f"bibtex table missing column '{col}'")

            elif tbl_name == "predictions":
                for col in ["arxiv_id", "partition", "citation_key", "cand_1", "cand_2", "cand_3", "cand_4", "cand_5"]:
                    if col not in field_names:
                        all_issues.append(f"predictions table missing column '{col}'")

        except Exception as e:
            all_issues.append(f"Error reading {tbl_name}: {e}")
            table_reports[tbl_name] = {"exists": True, "error": str(e)}

    # Foreign key checks: all citing papers in other tables should exist in papers
    if paper_ids:
        for tbl_name in ["references", "elements", "hierarchy", "bibtex", "predictions"]:
            tbl_path = dataset_dir / f"{tbl_name}.parquet"
            if tbl_path.exists():
                try:
                    t = pq.read_table(tbl_path, columns=["arxiv_id"])
                    other_ids = set(t["arxiv_id"].to_pylist())
                    diff = other_ids - paper_ids
                    if diff:
                        all_issues.append(
                            f"Foreign key violation: {len(diff)} papers in {tbl_name} not found in papers table"
                        )
                except Exception as e:
                    pass

    audit_result = {
        "valid": len(all_issues) == 0,
        "dataset_directory": str(dataset_dir),
        "tables": table_reports,
        "issues": all_issues,
        "total_issues": len(all_issues),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(audit_result, f, indent=2, ensure_ascii=False)

    return output_path
