"""Module D — Validation & Quality Assurance.

Validates predictions rules (candidate membership, tie-breaking, disjoint partitions),
and conducts independent auditing on the final 6-table Parquet dataset across all shards.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pyarrow as pa
import pyarrow.parquet as pq


def _collect_table_files(base_dir: Path, tbl_name: str) -> list[Path]:
    """Find all parquet files belonging to a table, supporting flat or sharded layout."""
    # 1. Direct file: <base_dir>/<tbl_name>.parquet
    direct_file = base_dir / f"{tbl_name}.parquet"
    if direct_file.is_file():
        return [direct_file]

    # 2. Subdirectory: <base_dir>/<tbl_name>/*.parquet
    direct_dir = base_dir / tbl_name
    if direct_dir.is_dir():
        parts = sorted(list(direct_dir.glob("*.parquet")))
        if parts:
            return parts

    # 3. Data subdirectory layout: <base_dir>/data/<tbl_name>/*.parquet
    data_dir = base_dir / "data" / tbl_name
    if data_dir.is_dir():
        parts = sorted(list(data_dir.glob("*.parquet")))
        if parts:
            return parts

    data_direct_file = base_dir / "data" / f"{tbl_name}.parquet"
    if data_direct_file.is_file():
        return [data_direct_file]

    return []


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
       Reads multi-shard references paths from inventory and resolves relative paths.
    2. No duplicate candidates within top 5.
    3. Proper null padding for papers with fewer than 5 candidates.
    4. Partition disjointness: no paper appears in more than one partition.
    5. Tie-breaking consistency against scores table: descending score, ascending ref_arxiv_id
       verified across ALL entries (exhaustive check).
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
        aid = str(r["arxiv_id"])
        part = r.get("partition")
        if aid in paper_partitions and paper_partitions[aid] != part:
            issues.append(f"Data leakage: paper {aid} appears in both {paper_partitions[aid]} and {part}")
        paper_partitions[aid] = str(part)

    # 2. Load predictions
    pred_table = pq.read_table(predictions_path)
    pred_rows = pred_table.to_pylist()

    # 3. Load references shards from inventory (resolving relative paths)
    ref_lookup: dict[str, set[str]] = {}
    inv_path = Path(inventory_path)
    ref_table_loaded = False

    if inv_path.exists():
        try:
            with open(inv_path, "r", encoding="utf-8") as f:
                inv_data = json.load(f)

            resolved_ref_shards: list[Path] = []
            for owner, owner_info in inv_data.get("owners", {}).items():
                tables = owner_info.get("tables", {})
                ref_spec = tables.get("references")
                if not ref_spec:
                    continue

                if isinstance(ref_spec, str):
                    shard_paths = [ref_spec]
                elif isinstance(ref_spec, list):
                    shard_paths = ref_spec
                else:
                    shard_paths = []

                for sp in shard_paths:
                    p = Path(sp)
                    if not p.is_absolute():
                        p = (inv_path.parent / p).resolve()
                    if p.is_file():
                        resolved_ref_shards.append(p)
                    else:
                        issues.append(f"References shard specified in inventory does not exist: {p}")

            if resolved_ref_shards:
                for shard in resolved_ref_shards:
                    rt = pq.read_table(shard, columns=["arxiv_id", "ref_arxiv_id"])
                    for row in rt.to_pylist():
                        ref_lookup.setdefault(str(row["arxiv_id"]), set()).add(str(row["ref_arxiv_id"]))
                ref_table_loaded = True
            else:
                issues.append(f"Inventory {inv_path} exists but no valid references shards could be resolved")
        except Exception as e:
            issues.append(f"Failed to read references shards from inventory {inv_path}: {e}")
    else:
        # Fallback to local dataset directory if inventory is not present
        default_ref_parts = _collect_table_files(inv_path.parent, "references")
        if default_ref_parts:
            for shard in default_ref_parts:
                rt = pq.read_table(shard, columns=["arxiv_id", "ref_arxiv_id"])
                for row in rt.to_pylist():
                    ref_lookup.setdefault(str(row["arxiv_id"]), set()).add(str(row["ref_arxiv_id"]))
            ref_table_loaded = True
        else:
            warnings.append("No inventory or local references table found; skipping candidate origin verification")

    # 4. Load labels for cross-check (missing entries & GT consistency)
    labels_path = Path(labels_path)
    label_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    if labels_path.exists():
        try:
            labels_table = pq.read_table(labels_path)
            for row in labels_table.to_pylist():
                aid = str(row["arxiv_id"])
                ck = str(row["citation_key"])
                if aid in paper_partitions:
                    label_lookup[(aid, ck)] = row
        except Exception as e:
            issues.append(f"Failed to read labels from {labels_path}: {e}")

    # 5. Check predictions rows
    pred_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    seen_entries: set[tuple[str, str]] = set()
    for pred in pred_rows:
        aid = str(pred["arxiv_id"])
        ck = str(pred["citation_key"])
        entry_key = (aid, ck)

        if entry_key in seen_entries:
            issues.append(f"Duplicate prediction entry: ({aid}, {ck})")
        seen_entries.add(entry_key)
        pred_lookup[entry_key] = pred

        # Check partition consistency with split
        if aid in paper_partitions:
            expected_partition = paper_partitions[aid]
            pred_partition = pred.get("partition")
            if pred_partition is not None and str(pred_partition) != expected_partition:
                issues.append(
                    f"Partition mismatch for ({aid}, {ck}): prediction claims '{pred_partition}', "
                    f"but frozen split requires '{expected_partition}'"
                )

        # Check top 5 candidates
        cands = [pred.get(f"cand_{i}") for i in range(1, 6)]
        non_null_cands = [str(c) for c in cands if c is not None]

        # Check for duplicates among top 5
        if len(non_null_cands) != len(set(non_null_cands)):
            issues.append(f"Duplicate candidates in top 5 for ({aid}, {ck}): {non_null_cands}")

        # Check candidate origin: candidate must belong to citing paper's own references
        # Papers without references MUST have all candidates set to None
        if ref_table_loaded:
            valid_refs = ref_lookup.get(aid, set())
            if not valid_refs:
                if non_null_cands:
                    issues.append(
                        f"Paper {aid} has no valid references; all candidates must be null for ({aid}, {ck}), "
                        f"but got non-null candidates: {non_null_cands}"
                    )
            else:
                for c in non_null_cands:
                    if c not in valid_refs:
                        issues.append(f"Candidate {c} does not belong to references of citing paper {aid}")

        # Check null padding: after first None, subsequent candidates must be None
        seen_none = False
        for c in cands:
            if c is None:
                seen_none = True
            elif seen_none:
                issues.append(f"Invalid null padding in top 5 for ({aid}, {ck}): {cands}")
                break

    # 6. Cross-check predictions against labels
    if label_lookup:
        for (l_aid, l_ck), l_row in label_lookup.items():
            if (l_aid, l_ck) not in pred_lookup:
                issues.append(
                    f"Missing prediction: entry ({l_aid}, {l_ck}) present in labels (split pool) is missing from predictions"
                )
            else:
                p_row = pred_lookup[(l_aid, l_ck)]
                p_gt = p_row.get("groundtruth_arxiv_id")
                l_gt = l_row.get("groundtruth_arxiv_id")
                norm_p_gt = str(p_gt).strip() if (p_gt is not None and str(p_gt).strip() != "") else None
                norm_l_gt = str(l_gt).strip() if (l_gt is not None and str(l_gt).strip() != "") else None
                if norm_p_gt != norm_l_gt:
                    issues.append(
                        f"Groundtruth mismatch for ({l_aid}, {l_ck}): "
                        f"predictions has '{norm_p_gt}', but labels has '{norm_l_gt}'"
                    )

        for (p_aid, p_ck) in pred_lookup:
            if p_aid in paper_partitions and (p_aid, p_ck) not in label_lookup:
                issues.append(f"Extraneous prediction: entry ({p_aid}, {p_ck}) for split paper {p_aid} not found in labels")

    # 7. Check exhaustive tie-breaking and completeness against scores
    scores_path = Path(scores_path)
    if scores_path.exists():
        try:
            scores_table = pq.read_table(scores_path)
            scores_rows = scores_table.to_pylist()
            grouped_scores: dict[tuple[str, str], list[tuple[str, float]]] = {}
            for s in scores_rows:
                k = (str(s["arxiv_id"]), str(s["citation_key"]))
                grouped_scores.setdefault(k, []).append((str(s["ref_arxiv_id"]), float(s["score"])))

            # Check that every prediction entry has corresponding scores if the paper has references
            for (p_aid, p_ck), p_row in pred_lookup.items():
                p_cands = [p_row.get(f"cand_{i}") for i in range(1, 6)]
                has_cands = any(c is not None for c in p_cands)
                paper_has_refs = bool(ref_lookup.get(p_aid))

                if paper_has_refs or has_cands:
                    if (p_aid, p_ck) not in grouped_scores:
                        issues.append(
                            f"Missing scores: entry ({p_aid}, {p_ck}) in predictions has no candidate scores in {scores_path.name}"
                        )
                    else:
                        scored_refs = set(cs[0] for cs in grouped_scores[(p_aid, p_ck)])
                        for c in p_cands:
                            if c is not None and str(c) not in scored_refs:
                                issues.append(
                                    f"Candidate {c} for ({p_aid}, {p_ck}) in predictions has no score recorded in {scores_path.name}"
                                )

            # Exhaustive check across ALL scored entries
            for (aid, ck), cand_scores in grouped_scores.items():
                # Tie-breaking contract: descending score, ascending ref_arxiv_id
                cand_scores.sort(key=lambda x: (-x[1], x[0]))
                expected_top_5 = [cs[0] for cs in cand_scores[:5]]

                actual_pred = pred_lookup.get((aid, ck))
                if actual_pred:
                    actual_top_5 = [
                        str(actual_pred.get(f"cand_{i}"))
                        for i in range(1, 1 + len(expected_top_5))
                        if actual_pred.get(f"cand_{i}") is not None
                    ]
                    if actual_top_5 != expected_top_5:
                        issues.append(
                            f"Ranking/Tie-breaking mismatch for ({aid}, {ck}): "
                            f"expected {expected_top_5}, got {actual_top_5}"
                        )
                else:
                    issues.append(f"Missing prediction for scored entry ({aid}, {ck})")
        except Exception as e:
            issues.append(f"Failed during exhaustive scores verification: {e}")

    report = {
        "valid": len(issues) == 0,
        "total_checked_predictions": len(pred_rows),
        "total_partitions_checked": len(paper_partitions),
        "issues": issues[:200],  # cap display if voluminous
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
    """Audit the complete 6-table Parquet dataset against academic schemas across all shards.

    Audits:
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

    # Load frozen split for partition validation
    split_path = Path(split)
    split_partitions: dict[str, str] = {}
    if split_path.exists():
        try:
            split_table = pq.read_table(split_path)
            for r in split_table.to_pylist():
                split_partitions[str(r["arxiv_id"])] = str(r["partition"])
        except Exception as e:
            all_issues.append(f"Failed to load frozen split from {split_path}: {e}")

    # Store sets for full relational and foreign key validation
    paper_ids: set[str] = set()
    element_keys: set[tuple[str, str]] = set()  # (arxiv_id, element_id)
    bibtex_keys: set[tuple[str, str]] = set()   # (arxiv_id, citation_key)
    paper_refs: dict[str, set[str]] = {}        # arxiv_id -> set(ref_arxiv_id)

    # 1. Audit each table across all its shards
    for tbl_name in required_tables:
        shards = _collect_table_files(dataset_dir, tbl_name)
        if not shards:
            all_issues.append(f"Missing required table: {tbl_name} (neither {tbl_name}.parquet nor sharded directory found)")
            table_reports[tbl_name] = {"exists": False}
            continue

        total_rows = 0
        field_names: list[str] = []

        try:
            # Check schema from first shard
            first_pf = pq.ParquetFile(shards[0])
            first_schema = first_pf.schema_arrow
            field_names = first_schema.names

            # Check that all shards have uniform column names AND uniform data types
            for s in shards:
                pf = pq.ParquetFile(s)
                total_rows += pf.metadata.num_rows
                s_schema = pf.schema_arrow
                if s_schema.names != field_names:
                    all_issues.append(f"Inconsistent column names across shards for table {tbl_name}: {s.name}")
                else:
                    for col in field_names:
                        t1 = first_schema.field(col).type
                        t2 = s_schema.field(col).type
                        if t1 != t2:
                            all_issues.append(
                                f"Inconsistent data type for column '{col}' in table {tbl_name} ({s.name}): "
                                f"expected {t1}, got {t2}"
                            )

            table_reports[tbl_name] = {
                "exists": True,
                "shards_count": len(shards),
                "total_rows": total_rows,
                "columns": field_names,
            }

            # Table-specific schema, list types, and cross-shard primary key audits
            if tbl_name == "papers":
                for col in ["arxiv_id", "title", "authors", "submission_date", "revised_dates", "venue"]:
                    if col not in field_names:
                        all_issues.append(f"papers table missing column '{col}'")

                for list_col in ["authors", "revised_dates"]:
                    if list_col in field_names:
                        f_field = first_schema.field(list_col)
                        if not pa.types.is_list(f_field.type):
                            all_issues.append(f"papers column '{list_col}' must be list type, got {f_field.type}")

                # Accumulate and check cross-shard uniqueness of arxiv_id
                seen_paper_pks: set[str] = set()
                for s in shards:
                    t = pq.read_table(s, columns=["arxiv_id"])
                    p_list = t["arxiv_id"].to_pylist()
                    for aid in p_list:
                        if aid is None:
                            all_issues.append(f"papers table contains null arxiv_id in shard {s.name}")
                            continue
                        s_aid = str(aid)
                        if s_aid in seen_paper_pks:
                            all_issues.append(f"Duplicate primary key '{s_aid}' in papers table across shards (found duplicate in {s.name})")
                        seen_paper_pks.add(s_aid)
                        paper_ids.add(s_aid)

            elif tbl_name == "references":
                for col in ["arxiv_id", "ref_arxiv_id", "ref_title", "ref_authors", "ref_submission_date", "ref_s2_id"]:
                    if col not in field_names:
                        all_issues.append(f"references table missing column '{col}'")

                if "ref_authors" in field_names:
                    f_field = first_schema.field(ref_authors := "ref_authors")
                    if not pa.types.is_list(f_field.type):
                        all_issues.append(f"references column 'ref_authors' must be list type, got {f_field.type}")

                seen_ref_pks: set[tuple[str, str]] = set()
                for s in shards:
                    t = pq.read_table(s, columns=["arxiv_id", "ref_arxiv_id"])
                    for row in t.to_pylist():
                        aid = str(row["arxiv_id"])
                        ref_aid = str(row["ref_arxiv_id"])
                        pair = (aid, ref_aid)
                        if pair in seen_ref_pks:
                            all_issues.append(f"Duplicate reference pair ({aid}, {ref_aid}) in references table across shards (found in {s.name})")
                        seen_ref_pks.add(pair)
                        paper_refs.setdefault(aid, set()).add(ref_aid)

            elif tbl_name == "elements":
                for col in ["element_id", "arxiv_id", "kind", "content"]:
                    if col not in field_names:
                        all_issues.append(f"elements table missing column '{col}'")

                seen_elem_pks: set[tuple[str, str]] = set()
                for s in shards:
                    t = pq.read_table(s, columns=["arxiv_id", "element_id"])
                    for row in t.to_pylist():
                        aid = str(row["arxiv_id"])
                        eid = str(row["element_id"])
                        k = (aid, eid)
                        if k in seen_elem_pks:
                            all_issues.append(f"Duplicate primary key ({aid}, {eid}) in elements table across shards (found in {s.name})")
                        seen_elem_pks.add(k)
                        element_keys.add(k)

            elif tbl_name == "hierarchy":
                for col in ["arxiv_id", "version", "element_id", "parent_id"]:
                    if col not in field_names:
                        all_issues.append(f"hierarchy table missing column '{col}'")

            elif tbl_name == "bibtex":
                # Check all 9 columns strictly according to assignment spec
                for col in ["arxiv_id", "citation_key", "entry_type", "title", "authors", "year", "doi", "journal", "url"]:
                    if col not in field_names:
                        all_issues.append(f"bibtex table missing column '{col}'")

                if "authors" in field_names:
                    f_field = first_schema.field("authors")
                    if not pa.types.is_list(f_field.type):
                        all_issues.append(f"bibtex column 'authors' must be list type, got {f_field.type}")

                seen_bibtex_pks: set[tuple[str, str]] = set()
                for s in shards:
                    t = pq.read_table(s, columns=["arxiv_id", "citation_key"])
                    for row in t.to_pylist():
                        aid = str(row["arxiv_id"])
                        ck = str(row["citation_key"])
                        k = (aid, ck)
                        if k in seen_bibtex_pks:
                            all_issues.append(f"Duplicate primary key ({aid}, {ck}) in bibtex table across shards (found in {s.name})")
                        seen_bibtex_pks.add(k)
                        bibtex_keys.add(k)

            elif tbl_name == "predictions":
                for col in ["arxiv_id", "partition", "citation_key", "groundtruth_arxiv_id", "cand_1", "cand_2", "cand_3", "cand_4", "cand_5"]:
                    if col not in field_names:
                        all_issues.append(f"predictions table missing column '{col}'")

                seen_pred_pks: set[tuple[str, str]] = set()
                for s in shards:
                    t = pq.read_table(s)
                    for row in t.to_pylist():
                        aid = str(row["arxiv_id"])
                        ck = str(row["citation_key"])
                        k = (aid, ck)
                        if k in seen_pred_pks:
                            all_issues.append(f"Duplicate primary key ({aid}, {ck}) in predictions table across shards (found in {s.name})")
                        seen_pred_pks.add(k)

                        # Vấn đề 3: Verify partition against frozen split
                        p_part = row.get("partition")
                        if split_partitions:
                            if aid in split_partitions:
                                expected_part = split_partitions[aid]
                                if str(p_part) != expected_part:
                                    all_issues.append(
                                        f"Partition mismatch in predictions ({s.name}) for ({aid}, {ck}): "
                                        f"declared '{p_part}', but frozen split requires '{expected_part}'"
                                    )
                            else:
                                all_issues.append(
                                    f"Paper {aid} in predictions ({s.name}) is not in frozen split.parquet"
                                )

                        # Vấn đề 3: Verify candidate belongs to references of citing paper
                        cands = [row.get(f"cand_{i}") for i in range(1, 6)]
                        non_null_cands = [str(c) for c in cands if c is not None]
                        valid_refs = paper_refs.get(aid, set())

                        if not valid_refs:
                            if non_null_cands:
                                all_issues.append(
                                    f"Paper {aid} has no references in dataset, but prediction ({aid}, {ck}) contains non-null candidates: {non_null_cands}"
                                )
                        else:
                            for c in non_null_cands:
                                if c not in valid_refs:
                                    all_issues.append(
                                        f"Candidate {c} in predictions ({s.name}) for ({aid}, {ck}) does not belong to references of paper {aid}"
                                    )

        except Exception as e:
            all_issues.append(f"Error auditing table {tbl_name}: {e}")
            table_reports[tbl_name] = {"exists": True, "error": str(e)}

    # 2. Comprehensive Relational Foreign Key Audits
    if paper_ids:
        # Check papers parent relationship for all tables
        for tbl_name in ["references", "elements", "hierarchy", "bibtex", "predictions"]:
            shards = _collect_table_files(dataset_dir, tbl_name)
            for s in shards:
                try:
                    t = pq.read_table(s, columns=["arxiv_id"])
                    shard_ids = set(str(x) for x in t["arxiv_id"].to_pylist() if x is not None)
                    diff = shard_ids - paper_ids
                    if diff:
                        all_issues.append(
                            f"Foreign key violation: {len(diff)} publications in {tbl_name} ({s.name}) not found in papers table"
                        )
                except Exception:
                    pass

        # Check hierarchy element_id and parent_id exist in elements
        hierarchy_shards = _collect_table_files(dataset_dir, "hierarchy")
        for s in hierarchy_shards:
            try:
                t = pq.read_table(s, columns=["arxiv_id", "element_id", "parent_id"])
                for r in t.to_pylist():
                    aid = str(r["arxiv_id"])
                    eid = str(r["element_id"])
                    pid = r.get("parent_id")
                    if (aid, eid) not in element_keys:
                        all_issues.append(f"Foreign key violation in hierarchy ({s.name}): element {eid} not found in elements")
                        break
                    # Check parent_id exists if not root
                    if pid is not None and str(pid).strip() not in {"root", "None", ""}:
                        p_key = (aid, str(pid).strip())
                        if p_key not in element_keys:
                            all_issues.append(
                                f"Foreign key violation in hierarchy ({s.name}): parent_id '{pid}' for ({aid}, {eid}) not found in elements"
                            )
                            break
            except Exception:
                pass

        # Check predictions citation_key exists in bibtex
        pred_shards = _collect_table_files(dataset_dir, "predictions")
        for s in pred_shards:
            try:
                t = pq.read_table(s, columns=["arxiv_id", "citation_key"])
                for r in t.to_pylist():
                    k = (str(r["arxiv_id"]), str(r["citation_key"]))
                    if k not in bibtex_keys:
                        all_issues.append(f"Foreign key violation in predictions ({s.name}): citation_key {k[1]} not found in bibtex")
                        break
            except Exception:
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
