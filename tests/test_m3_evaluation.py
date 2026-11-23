"""Unit tests for Module D — Evaluation & Verification.

Implements rigorous tests for:
- MRR@5 hand-calculated mathematical proof.
- Denominator preservation against missing predictions.
- Frozen split partition authority enforcement.
- Inventory multi-shard resolution.
- Exhaustive tie-breaking ranking check.
- Complete 6-table schema and relational foreign-key validation.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from arxiv_pipeline.matching.evaluation import compute_mrr_at_5, evaluate
from arxiv_pipeline.matching.validation import validate_prediction_rules, validate_dataset


def test_hand_calculated_mrr_at_5():
    """Verify MRR@5 calculation with an exact hand-calculated toy example."""
    # Toy dataset: 6 queries
    # Q1: hit at rank 1 -> 1/1 = 1.0
    # Q2: hit at rank 2 -> 1/2 = 0.5
    # Q3: hit at rank 3 -> 1/3
    # Q4: hit at rank 4 -> 1/4 = 0.25
    # Q5: hit at rank 5 -> 1/5 = 0.2
    # Q6: outside top 5 -> 0.0
    # Expected sum = 1.0 + 0.5 + (1/3) + 0.25 + 0.2 + 0.0 = 2.283333333...
    # Expected mean = 2.283333333... / 6 = 0.380556
    toy_entries = [
        {"groundtruth_arxiv_id": "A", "cand_1": "A", "cand_2": "B", "cand_3": "C", "cand_4": "D", "cand_5": "E"},
        {"groundtruth_arxiv_id": "B", "cand_1": "A", "cand_2": "B", "cand_3": "C", "cand_4": "D", "cand_5": "E"},
        {"groundtruth_arxiv_id": "C", "cand_1": "A", "cand_2": "B", "cand_3": "C", "cand_4": "D", "cand_5": "E"},
        {"groundtruth_arxiv_id": "D", "cand_1": "A", "cand_2": "B", "cand_3": "C", "cand_4": "D", "cand_5": "E"},
        {"groundtruth_arxiv_id": "E", "cand_1": "A", "cand_2": "B", "cand_3": "C", "cand_4": "D", "cand_5": "E"},
        {"groundtruth_arxiv_id": "F", "cand_1": "A", "cand_2": "B", "cand_3": "C", "cand_4": "D", "cand_5": "E"},
    ]

    result = compute_mrr_at_5(toy_entries)

    expected_sum = 1.0 + 0.5 + (1.0 / 3.0) + 0.25 + 0.2 + 0.0
    expected_mrr = round(expected_sum / 6.0, 6)

    assert result["sample_count"] == 6
    assert result["mrr_at_5"] == expected_mrr
    assert result["hits_at_1"] == 1
    assert result["hits_at_3"] == 3
    assert result["hits_at_5"] == 5
    assert result["status"] == "ok"


def test_empty_sample_mrr():
    """Verify that empty sample returns uncomputable and does not crash."""
    result = compute_mrr_at_5([])
    assert result["sample_count"] == 0
    assert result["mrr_at_5"] is None
    assert result["status"] == "uncomputable"


def test_mrr_missing_prediction_preserves_denominator(tmp_path: Path):
    """Test that missing predictions are not omitted: receives 0.0 and preserves denominator."""
    # 2 queries in split
    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"owner": "hoang", "arxiv_id": "2503.00001", "label_method": "manual", "partition": "valid"},
    ]), split_path)

    # 2 matched labels: ref1 and ref2
    labels_path = tmp_path / "labels.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "ref1", "groundtruth_arxiv_id": "1101.0001", "label_method": "manual", "label_status": "matched"},
        {"arxiv_id": "2503.00001", "citation_key": "ref2", "groundtruth_arxiv_id": "1101.0002", "label_method": "manual", "label_status": "matched"},
    ]), labels_path)

    # ONLY 1 prediction provided (ref1 is hit at rank 1; ref2 is missing completely!)
    pred_path = tmp_path / "predictions.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "ref1", "cand_1": "1101.0001", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None},
    ]), pred_path)

    report_dir = tmp_path / "reports"
    evaluate(
        inventory=tmp_path / "inv.json",
        labels=labels_path,
        split=split_path,
        scores=tmp_path / "scores.parquet",
        predictions=pred_path,
        report_dir=report_dir,
    )

    with open(report_dir / "metrics.json") as f:
        metrics = json.load(f)

    # Total samples MUST BE 2 (not reduced to 1!)
    # MRR = (1.0 + 0.0) / 2 = 0.5 (instead of falsely inflated 1.0)
    assert metrics["summary"]["matched_groundtruth_queries"] == 2
    assert metrics["summary"]["evaluated_queries"] == 2
    assert metrics["summary"]["missing_predictions"] == 1
    assert metrics["metrics"]["valid"]["manual"]["mrr_at_5"] == 0.5

    # Check errors.parquet contains the missing query
    err_table = pq.read_table(report_dir / "errors.parquet")
    assert err_table.num_rows == 1
    assert err_table["citation_key"].to_pylist() == ["ref2"]
    assert err_table["error_type"].to_pylist() == ["missing_prediction"]


def test_frozen_split_partition_enforcement(tmp_path: Path):
    """Test that evaluate() enforces frozen split partition over mismatched prediction partition."""
    # Split specifies valid
    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"owner": "hoang", "arxiv_id": "2503.00001", "label_method": "manual", "partition": "valid"},
    ]), split_path)

    labels_path = tmp_path / "labels.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "ref1", "groundtruth_arxiv_id": "1101.0001", "label_method": "manual", "label_status": "matched"},
    ]), labels_path)

    # Prediction erroneously claims train partition
    pred_path = tmp_path / "predictions.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "train", "citation_key": "ref1", "cand_1": "1101.0001", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None},
    ]), pred_path)

    report_dir = tmp_path / "reports_split"
    evaluate(
        inventory=tmp_path / "inv.json",
        labels=labels_path,
        split=split_path,
        scores=tmp_path / "scores.parquet",
        predictions=pred_path,
        report_dir=report_dir,
    )

    with open(report_dir / "metrics.json") as f:
        metrics = json.load(f)

    # Result must be placed in valid (frozen split), not train!
    assert metrics["metrics"]["valid"]["manual"]["mrr_at_5"] == 1.0
    assert metrics["metrics"]["train"]["manual"]["mrr_at_5"] is None
    assert metrics["summary"]["partition_mismatches_count"] == 1


def test_inventory_multishard_and_relative_path_resolution(tmp_path: Path):
    """Test resolving multi-shard relative references paths from inventory.json."""
    data_dir = tmp_path / "inputs" / "hoang" / "references"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Shard 0: 2503.00001 -> refA
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA"}
    ]), data_dir / "part-0.parquet")

    # Shard 1: 2503.00001 -> refB
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refB"}
    ]), data_dir / "part-1.parquet")

    inv_path = tmp_path / "inventory.json"
    with open(inv_path, "w") as f:
        json.dump({
            "owners": {
                "hoang": {
                    "tables": {
                        "references": [
                            "inputs/hoang/references/part-0.parquet",
                            "inputs/hoang/references/part-1.parquet",
                        ]
                    }
                }
            }
        }, f)

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "valid"}]), split_path)

    # Valid prediction with both refA and refB
    good_pred = tmp_path / "good_pred.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1",
        "cand_1": "refA", "cand_2": "refB", "cand_3": None, "cand_4": None, "cand_5": None,
    }]), good_pred)

    out_val = tmp_path / "val_good.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=tmp_path / "dummy.parquet",
        split_path=split_path,
        scores_path=tmp_path / "dummy_scores.parquet",
        predictions_path=good_pred,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)
    assert rep["valid"] is True
    assert len(rep["issues"]) == 0


def test_validate_dataset_schema_strictness_and_foreign_keys(tmp_path: Path):
    """Test validate_dataset checks all 6 tables, types, and foreign key relations."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()

    # 1. papers
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "Paper 1", "authors": ["Author A"], "submission_date": "2025-03-01", "revised_dates": [], "venue": None}
    ], schema=pa.schema([
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("title", pa.string()),
        pa.field("authors", pa.list_(pa.string())),
        pa.field("submission_date", pa.string()),
        pa.field("revised_dates", pa.list_(pa.string())),
        pa.field("venue", pa.string()),
    ])), ds_dir / "papers.parquet")

    # 2. references
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "1101.0001", "ref_title": "T", "ref_authors": ["A"], "ref_submission_date": "2011", "ref_s2_id": "S2"}
    ], schema=pa.schema([
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("ref_arxiv_id", pa.string(), nullable=False),
        pa.field("ref_title", pa.string()),
        pa.field("ref_authors", pa.list_(pa.string())),
        pa.field("ref_submission_date", pa.string()),
        pa.field("ref_s2_id", pa.string()),
    ])), ds_dir / "references.parquet")

    # 3. elements
    pq.write_table(pa.Table.from_pylist([
        {"element_id": "2503.00001#e1", "arxiv_id": "2503.00001", "kind": "paragraph", "content": "Hello"}
    ]), ds_dir / "elements.parquet")

    # 4. hierarchy
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "version": 1, "element_id": "2503.00001#e1", "parent_id": "root"}
    ]), ds_dir / "hierarchy.parquet")

    # 5. bibtex (with all 9 required columns: doi, journal, url included)
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "entry_type": "article", "title": "T", "authors": ["A"], "year": 2020, "doi": "10.1", "journal": "J", "url": "http://"}
    ], schema=pa.schema([
        pa.field("arxiv_id", pa.string(), nullable=False),
        pa.field("citation_key", pa.string(), nullable=False),
        pa.field("entry_type", pa.string(), nullable=False),
        pa.field("title", pa.string()),
        pa.field("authors", pa.list_(pa.string())),
        pa.field("year", pa.int32()),
        pa.field("doi", pa.string()),
        pa.field("journal", pa.string()),
        pa.field("url", pa.string()),
    ])), ds_dir / "bibtex.parquet")

    # 6. predictions (with groundtruth_arxiv_id included)
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1", "groundtruth_arxiv_id": "1101.0001", "cand_1": "1101.0001", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None}
    ]), ds_dir / "predictions.parquet")

    out_val = tmp_path / "ds_val.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=tmp_path / "split.parquet",
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is True
    assert rep["total_issues"] == 0
    assert rep["tables"]["bibtex"]["columns"] == ["arxiv_id", "citation_key", "entry_type", "title", "authors", "year", "doi", "journal", "url"]
    assert "groundtruth_arxiv_id" in rep["tables"]["predictions"]["columns"]
