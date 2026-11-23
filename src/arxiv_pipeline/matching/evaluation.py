"""Module D — Evaluation & Metrics Scorer.

Computes MRR@5, enforces frozen split partitions, preserves ground-truth sample
denominators against missing/duplicate predictions, and exports error analysis tables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pyarrow as pa
import pyarrow.parquet as pq


def compute_mrr_at_5(
    entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute MRR@5 and hit rates on a list of evaluated entries.

    Each entry must contain:
    - 'groundtruth_arxiv_id': string (the true referenced arxiv_id)
    - 'cand_1' .. 'cand_5': optional strings (the top-5 predicted candidates)

    MRR@5 is 1/rank if ground truth appears in top 5 (rank 1..5), else 0.0.
    Returns uncomputable status if entries is empty.
    """
    total = len(entries)
    if total == 0:
        return {
            "mrr_at_5": None,
            "sample_count": 0,
            "hits_at_1": 0,
            "hits_at_3": 0,
            "hits_at_5": 0,
            "status": "uncomputable",
        }

    reciprocal_ranks: list[float] = []
    hits_1 = 0
    hits_3 = 0
    hits_5 = 0

    for item in entries:
        gt = item.get("groundtruth_arxiv_id")
        if not gt:
            continue

        cands = [item.get(f"cand_{i}") for i in range(1, 6)]
        rank = None
        for idx, cand in enumerate(cands, start=1):
            if cand == gt:
                rank = idx
                break

        if rank is not None:
            rr = 1.0 / rank
            reciprocal_ranks.append(rr)
            if rank == 1:
                hits_1 += 1
            if rank <= 3:
                hits_3 += 1
            if rank <= 5:
                hits_5 += 1
        else:
            reciprocal_ranks.append(0.0)

    evaluated_count = len(reciprocal_ranks)
    if evaluated_count == 0:
        return {
            "mrr_at_5": None,
            "sample_count": 0,
            "hits_at_1": 0,
            "hits_at_3": 0,
            "hits_at_5": 0,
            "status": "uncomputable",
        }

    mrr = sum(reciprocal_ranks) / evaluated_count
    return {
        "mrr_at_5": round(mrr, 6),
        "sample_count": evaluated_count,
        "hits_at_1": hits_1,
        "hits_at_3": hits_3,
        "hits_at_5": hits_5,
        "hit_rate_at_1": round(hits_1 / evaluated_count, 6),
        "hit_rate_at_3": round(hits_3 / evaluated_count, 6),
        "hit_rate_at_5": round(hits_5 / evaluated_count, 6),
        "status": "ok",
    }


def evaluate(
    inventory: Path,
    labels: Path,
    split: Path,
    scores: Path,
    predictions: Path,
    report_dir: Path,
) -> Path:
    """Evaluate predictions against ground-truth labels and frozen split specifications.

    Enforces:
    1. Frozen split authority: predictions declaring mismatched partitions are rejected/overridden.
    2. Denominator preservation: missing predictions for matched queries receive 0.0 score.
    3. Duplicate detection: duplicate prediction entries are identified and reported.
    4. Sliced MRR@5 reporting and error collection.

    Generates:
    - metrics.json
    - validation.json
    - errors.parquet

    Returns Path to metrics.json.
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load frozen split
    split_table = pq.read_table(split)
    split_rows = split_table.to_pylist()

    # Map: arxiv_id -> {partition, label_method, owner}
    split_map: dict[str, dict[str, Any]] = {}
    split_papers: set[str] = set()
    for row in split_rows:
        aid = row["arxiv_id"]
        split_papers.add(aid)
        split_map[aid] = {
            "partition": row.get("partition"),
            "label_method": row.get("label_method"),
            "owner": row.get("owner"),
        }

    # 2. Load ground-truth labels
    labels_table = pq.read_table(labels)
    labels_rows = labels_table.to_pylist()

    # Filter only 'matched' ground-truth labels for publications belonging to split
    matched_queries: dict[tuple[str, str], dict[str, Any]] = {}
    for row in labels_rows:
        aid = row["arxiv_id"]
        ck = row["citation_key"]
        status = row.get("label_status")
        gt = row.get("groundtruth_arxiv_id")

        if aid in split_papers and status == "matched" and gt:
            matched_queries[(aid, ck)] = row

    # 3. Load predictions & index by (arxiv_id, citation_key)
    pred_table = pq.read_table(predictions)
    pred_rows = pred_table.to_pylist()

    pred_map: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_pred_keys: set[tuple[str, str]] = set()
    partition_mismatches: list[str] = []

    for pred in pred_rows:
        aid = pred["arxiv_id"]
        ck = pred["citation_key"]
        key = (aid, ck)

        if key in pred_map:
            duplicate_pred_keys.add(key)
        else:
            pred_map[key] = pred

        # Check partition consistency against frozen split
        pred_part = pred.get("partition")
        if aid in split_map:
            expected_part = split_map[aid]["partition"]
            if pred_part is not None and pred_part != expected_part:
                partition_mismatches.append(
                    f"Paper {aid} entry {ck}: prediction declared partition '{pred_part}', "
                    f"expected frozen split '{expected_part}'"
                )

    # 4. Evaluate every ground-truth query (ensures denominator is never deflated)
    evaluated_records: list[dict[str, Any]] = []
    error_records: list[dict[str, Any]] = []
    missing_pred_count = 0

    for (aid, ck), lbl in matched_queries.items():
        sp = split_map.get(aid, {})
        # Enforce frozen split partition
        frozen_partition = str(sp.get("partition") or "unknown")
        label_method = str(lbl.get("label_method") or sp.get("label_method") or "unknown")
        gt = str(lbl["groundtruth_arxiv_id"])

        pred = pred_map.get((aid, ck))

        if pred is None:
            # Query was omitted in predictions: receives rank None, score 0.0
            missing_pred_count += 1
            rec = {
                "arxiv_id": aid,
                "citation_key": ck,
                "partition": frozen_partition,
                "label_method": label_method,
                "groundtruth_arxiv_id": gt,
                "cand_1": None,
                "cand_2": None,
                "cand_3": None,
                "cand_4": None,
                "cand_5": None,
                "rank": None,
                "reciprocal_rank": 0.0,
                "error_type": "missing_prediction",
            }
            evaluated_records.append(rec)
            error_records.append(rec)
        else:
            cands = [pred.get(f"cand_{i}") for i in range(1, 6)]
            rank = None
            for idx, cand in enumerate(cands, start=1):
                if cand == gt:
                    rank = idx
                    break

            rr = (1.0 / rank) if rank is not None else 0.0

            error_type = None
            if rank is None:
                error_type = "unranked"
            elif rank > 1:
                error_type = f"rank_{rank}"

            rec = {
                "arxiv_id": aid,
                "citation_key": ck,
                "partition": frozen_partition,
                "label_method": label_method,
                "groundtruth_arxiv_id": gt,
                "cand_1": cands[0],
                "cand_2": cands[1],
                "cand_3": cands[2],
                "cand_4": cands[3],
                "cand_5": cands[4],
                "rank": rank,
                "reciprocal_rank": rr,
                "error_type": error_type or "none",
            }
            evaluated_records.append(rec)

            if rank != 1:
                error_records.append(rec)

    # 5. Compute slice metrics
    partitions = ["train", "valid", "test", "overall"]
    methods = ["manual", "automatic", "overall"]

    metrics_matrix: dict[str, dict[str, Any]] = {}
    for part in partitions:
        metrics_matrix[part] = {}
        for meth in methods:
            subset = [
                r for r in evaluated_records
                if (part == "overall" or r["partition"] == part)
                and (meth == "overall" or r["label_method"] == meth)
            ]
            metrics_matrix[part][meth] = compute_mrr_at_5(subset)

    metrics_output = {
        "summary": {
            "total_split_publications": len(split_papers),
            "matched_groundtruth_queries": len(matched_queries),
            "total_predictions_provided": len(pred_rows),
            "evaluated_queries": len(evaluated_records),
            "missing_predictions": missing_pred_count,
            "duplicate_prediction_keys": len(duplicate_pred_keys),
            "partition_mismatches_count": len(partition_mismatches),
            "total_error_queries": len(error_records),
        },
        "metrics": metrics_matrix,
    }

    metrics_path = report_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_output, f, indent=2, ensure_ascii=False)

    # 6. Export errors.parquet
    error_schema = pa.schema([
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("citation_key", pa.string(), nullable=False),
        pa.field("partition", pa.string(), nullable=False),
        pa.field("label_method", pa.string(), nullable=False),
        pa.field("groundtruth_arxiv_id", pa.string(), nullable=False),
        pa.field("cand_1", pa.string(), nullable=True),
        pa.field("cand_2", pa.string(), nullable=True),
        pa.field("cand_3", pa.string(), nullable=True),
        pa.field("cand_4", pa.string(), nullable=True),
        pa.field("cand_5", pa.string(), nullable=True),
        pa.field("rank", pa.int32(), nullable=True),
        pa.field("reciprocal_rank", pa.float64(), nullable=False),
        pa.field("error_type", pa.string(), nullable=False),
    ])

    errors_path = report_dir / "errors.parquet"
    if error_records:
        error_table = pa.Table.from_pylist(error_records, schema=error_schema)
    else:
        error_table = pa.Table.from_pylist([], schema=error_schema)
    pq.write_table(error_table, errors_path, compression="snappy")

    # 7. Perform prediction rules validation
    from .validation import validate_prediction_rules
    validation_path = report_dir / "validation.json"
    validate_prediction_rules(
        inventory_path=inventory,
        labels_path=labels,
        split_path=split,
        scores_path=scores,
        predictions_path=predictions,
        output_path=validation_path,
    )

    return metrics_path
