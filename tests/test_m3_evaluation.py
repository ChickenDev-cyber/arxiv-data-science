"""Unit tests for Module D — Evaluation & Verification.

Implements rigorous tests for MRR@5 hand-calculated examples,
partition slicing, edge cases, error analysis, and dataset validation.
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


def test_end_to_end_evaluation_pipeline(tmp_path: Path):
    """Test full evaluate() function on synthetic Parquet tables."""
    # 1. Create synthetic split
    split_data = [
        {"owner": "hoang", "arxiv_id": "2503.00001", "label_method": "manual", "partition": "train"},
        {"owner": "hoang", "arxiv_id": "2503.00002", "label_method": "automatic", "partition": "valid"},
    ]
    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist(split_data), split_path)

    # 2. Create synthetic labels
    labels_data = [
        {
            "arxiv_id": "2503.00001",
            "citation_key": "ref1",
            "groundtruth_arxiv_id": "1101.0001",
            "label_method": "manual",
            "labeler": "hoang",
            "label_status": "matched",
            "evidence": "verified",
            "heuristic_version": None,
        },
        {
            "arxiv_id": "2503.00002",
            "citation_key": "ref2",
            "groundtruth_arxiv_id": "1101.0002",
            "label_method": "automatic",
            "labeler": "system",
            "label_status": "matched",
            "evidence": "heuristic",
            "heuristic_version": "1.0",
        },
    ]
    labels_path = tmp_path / "labels.parquet"
    pq.write_table(pa.Table.from_pylist(labels_data), labels_path)

    # 3. Create synthetic predictions
    pred_data = [
        {
            "arxiv_id": "2503.00001",
            "partition": "train",
            "citation_key": "ref1",
            "groundtruth_arxiv_id": "1101.0001",
            "cand_1": "1101.0001",  # Rank 1 -> RR = 1.0
            "cand_2": "1101.9999",
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
        {
            "arxiv_id": "2503.00002",
            "partition": "valid",
            "citation_key": "ref2",
            "groundtruth_arxiv_id": "1101.0002",
            "cand_1": "1101.9999",
            "cand_2": "1101.0002",  # Rank 2 -> RR = 0.5
            "cand_3": None,
            "cand_4": None,
            "cand_5": None,
        },
    ]
    pred_path = tmp_path / "predictions.parquet"
    pq.write_table(pa.Table.from_pylist(pred_data), pred_path)

    # 4. Dummy inventory and scores
    inv_path = tmp_path / "inventory.json"
    with open(inv_path, "w") as f:
        json.dump({"owners": {}}, f)

    scores_path = tmp_path / "scores.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "citation_key": "ref1", "ref_arxiv_id": "1101.0001", "score": 0.99}]), scores_path)

    # 5. Run evaluate()
    report_dir = tmp_path / "reports"
    metrics_file = evaluate(
        inventory=inv_path,
        labels=labels_path,
        split=split_path,
        scores=scores_path,
        predictions=pred_path,
        report_dir=report_dir,
    )

    assert metrics_file.exists()
    with open(metrics_file, "r") as f:
        metrics = json.load(f)

    # Train (manual): 1 sample, hit rank 1 -> MRR = 1.0
    assert metrics["metrics"]["train"]["manual"]["mrr_at_5"] == 1.0
    assert metrics["metrics"]["train"]["manual"]["sample_count"] == 1

    # Valid (automatic): 1 sample, hit rank 2 -> MRR = 0.5
    assert metrics["metrics"]["valid"]["automatic"]["mrr_at_5"] == 0.5
    assert metrics["metrics"]["valid"]["automatic"]["sample_count"] == 1

    # Overall: 2 samples, MRR = (1.0 + 0.5) / 2 = 0.75
    assert metrics["metrics"]["overall"]["overall"]["mrr_at_5"] == 0.75
    assert metrics["metrics"]["overall"]["overall"]["sample_count"] == 2

    # Check errors.parquet
    errors_file = report_dir / "errors.parquet"
    assert errors_file.exists()
    err_table = pq.read_table(errors_file)
    # The valid query was hit at rank 2 (not rank 1), so it must be captured in error_records
    assert err_table.num_rows == 1
    assert err_table["citation_key"].to_pylist() == ["ref2"]


def test_validate_prediction_rules(tmp_path: Path):
    """Test prediction rules validator detects duplicates and foreign candidates."""
    # Reference pool for paper 2503.00001: only ["refA", "refB"]
    ref_path = tmp_path / "references.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA"},
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refB"},
    ]), ref_path)

    inv_path = tmp_path / "inventory.json"
    with open(inv_path, "w") as f:
        json.dump({"owners": {"hoang": {"tables": {"references": str(ref_path)}}}}, f)

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "train"},
    ]), split_path)

    # Predictions with invalid candidate "ref_alien" and duplicate candidate "refA"
    bad_pred_path = tmp_path / "bad_predictions.parquet"
    pq.write_table(pa.Table.from_pylist([
        {
            "arxiv_id": "2503.00001",
            "partition": "train",
            "citation_key": "k1",
            "cand_1": "refA",
            "cand_2": "refA",  # Duplicate!
            "cand_3": "ref_alien",  # Alien!
            "cand_4": None,
            "cand_5": None,
        }
    ]), bad_pred_path)

    out_val = tmp_path / "validation.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=tmp_path / "dummy.parquet",
        split_path=split_path,
        scores_path=tmp_path / "dummy_scores.parquet",
        predictions_path=bad_pred_path,
        output_path=out_val,
    )

    with open(out_val, "r") as f:
        val_rep = json.load(f)

    assert val_rep["valid"] is False
    assert any("Duplicate candidates" in iss for iss in val_rep["issues"])
    assert any("ref_alien" in iss for iss in val_rep["issues"])
