"""Module D — Evaluation & Metrics Scorer.

Computes MRR@5, breaks down metrics across partitions and labeling methods,
detects prediction anomalies, and exports error analysis tables.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """Evaluate predictions against ground-truth labels and split specifications.

    Generates:
    - metrics.json: MRR@5 and counts by partition and label_method
    - validation.json: prediction rule compliance report
    - errors.parquet: mispredicted entries for error analysis

    Returns Path to metrics.json.
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load inputs
    labels_table = pq.read_table(labels)
    split_table = pq.read_table(split)
    pred_table = pq.read_table(predictions)

    labels_data = labels_table.to_pylist()
    split_data = split_table.to_pylist()
    pred_data = pred_table.to_pylist()

    # Build lookup maps
    # split map: arxiv_id -> {partition, label_method, owner}
    split_map: dict[str, dict[str, Any]] = {}
    for row in split_data:
        split_map[row["arxiv_id"]] = {
            "partition": row.get("partition"),
            "label_method": row.get("label_method"),
            "owner": row.get("owner"),
        }

    # labels map: (arxiv_id, citation_key) -> label row
    # Filter only 'matched' entries per specification
    matched_labels: dict[tuple[str, str], dict[str, Any]] = {}
    for row in labels_data:
        aid = row["arxiv_id"]
        ck = row["citation_key"]
        status = row.get("label_status")
        gt = row.get("groundtruth_arxiv_id")
        if status == "matched" and gt:
            matched_labels[(aid, ck)] = row

    # 2. Match predictions with labels & split
    evaluated_records: list[dict[str, Any]] = []
    error_records: list[dict[str, Any]] = []

    for pred in pred_data:
        aid = pred["arxiv_id"]
        ck = pred["citation_key"]
        key = (aid, ck)

        if key not in matched_labels:
            continue

        lbl = matched_labels[key]
        sp = split_map.get(aid, {})
        partition = str(pred.get("partition") or sp.get("partition") or "unknown")
        label_method = str(lbl.get("label_method") or sp.get("label_method") or "unknown")
        gt = str(lbl["groundtruth_arxiv_id"])

        cands = [pred.get(f"cand_{i}") for i in range(1, 6)]
        rank = None
        for idx, cand in enumerate(cands, start=1):
            if cand == gt:
                rank = idx
                break

        rr = (1.0 / rank) if rank is not None else 0.0

        rec = {
            "arxiv_id": aid,
            "citation_key": ck,
            "partition": partition,
            "label_method": label_method,
            "groundtruth_arxiv_id": gt,
            "cand_1": cands[0],
            "cand_2": cands[1],
            "cand_3": cands[2],
            "cand_4": cands[3],
            "cand_5": cands[4],
            "rank": rank,
            "reciprocal_rank": rr,
        }
        evaluated_records.append(rec)

        # Collect error cases (rank is None or rank > 1)
        if rank != 1:
            error_records.append(rec)

    # 3. Compute slice metrics
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
            "total_predictions": len(pred_data),
            "matched_groundtruth_labels": len(matched_labels),
            "evaluated_samples": len(evaluated_records),
            "error_samples": len(error_records),
        },
        "metrics": metrics_matrix,
    }

    metrics_path = report_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_output, f, indent=2, ensure_ascii=False)

    # 4. Export errors.parquet
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
    ])

    errors_path = report_dir / "errors.parquet"
    if error_records:
        error_table = pa.Table.from_pylist(error_records, schema=error_schema)
    else:
        error_table = pa.Table.from_pylist([], schema=error_schema)
    pq.write_table(error_table, errors_path, compression="snappy")

    # 5. Prediction rules verification report (validation.json)
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
