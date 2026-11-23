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

    # Scores table covering all references (refA, refB)
    scores_path = tmp_path / "scores.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "ref_arxiv_id": "refA", "score": 2.0},
        {"arxiv_id": "2503.00001", "citation_key": "k1", "ref_arxiv_id": "refB", "score": 1.0},
    ]), scores_path)

    out_val = tmp_path / "val_good.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=tmp_path / "dummy.parquet",
        split_path=split_path,
        scores_path=scores_path,
        predictions_path=good_pred,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)
    assert rep["valid"] is True
    assert len(rep["issues"]) == 0


def test_paper_without_references_must_reject_non_null_candidates(tmp_path: Path):
    """Test that a citing paper without references must have all candidates as None."""
    data_dir = tmp_path / "inputs" / "hoang" / "references"
    data_dir.mkdir(parents=True, exist_ok=True)
    # Write references only for 2503.00001
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA"}
    ]), data_dir / "part-0.parquet")

    inv_path = tmp_path / "inventory.json"
    with open(inv_path, "w") as f:
        json.dump({
            "owners": {
                "hoang": {
                    "tables": {
                        "references": ["inputs/hoang/references/part-0.parquet"]
                    }
                }
            }
        }, f)

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid"},
        {"arxiv_id": "2503.99999", "partition": "valid"}  # Paper without any references
    ]), split_path)

    # Bad prediction: 2503.99999 has non-null candidate "cand_alien"
    bad_pred = tmp_path / "bad_pred.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "arxiv_id": "2503.99999", "partition": "valid", "citation_key": "k1",
        "cand_1": "cand_alien", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None,
    }]), bad_pred)

    out_val = tmp_path / "val_bad.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=tmp_path / "dummy.parquet",
        split_path=split_path,
        scores_path=tmp_path / "dummy_scores.parquet",
        predictions_path=bad_pred,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)
    assert rep["valid"] is False
    assert any("has no valid references" in iss for iss in rep["issues"])


def test_validate_prediction_rules_scores_coverage_and_missing_file(tmp_path: Path):
    """Test scores must cover all references of citing paper and missing scores file is fatal."""
    inv_path = tmp_path / "inventory.json"
    with open(inv_path, "w") as f:
        json.dump({
            "owners": {
                "hoang": {
                    "tables": {
                        "references": ["refs.parquet"]
                    }
                }
            }
        }, f)

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA"},
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refB"},
    ]), tmp_path / "refs.parquet")

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "valid"}]), split_path)

    pred_path = tmp_path / "pred.parquet"
    pq.write_table(pa.Table.from_pylist([{
        "arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1",
        "cand_1": "refA", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None
    }]), pred_path)

    # Case 1: Missing scores file entirely
    out_missing = tmp_path / "out_missing.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=tmp_path / "dummy.parquet",
        split_path=split_path,
        scores_path=tmp_path / "non_existent_scores.parquet",
        predictions_path=pred_path,
        output_path=out_missing,
    )
    with open(out_missing) as f:
        rep_missing = json.load(f)
    assert rep_missing["valid"] is False
    assert any("Missing required scores file" in iss for iss in rep_missing["issues"])

    # Case 2: Scores file only covers refA, missing refB
    partial_scores = tmp_path / "partial_scores.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "ref_arxiv_id": "refA", "score": 1.5}
    ]), partial_scores)

    out_partial = tmp_path / "out_partial.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=tmp_path / "dummy.parquet",
        split_path=split_path,
        scores_path=partial_scores,
        predictions_path=pred_path,
        output_path=out_partial,
    )
    with open(out_partial) as f:
        rep_partial = json.load(f)
    assert rep_partial["valid"] is False
    assert any("Scores incomplete" in iss and "refB" in iss for iss in rep_partial["issues"])


def test_validate_prediction_rules_cross_check_labels_and_scores(tmp_path: Path):
    """Test cross-checking predictions with labels (missing entry, GT mismatch) and missing scores."""
    inv_path = tmp_path / "inventory.json"
    with open(inv_path, "w") as f:
        json.dump({"owners": {}}, f)

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid"},
    ]), split_path)

    # Labels has 2 entries: k1 (gt: refA) and k2 (gt: refB)
    labels_path = tmp_path / "labels.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "groundtruth_arxiv_id": "refA", "label_status": "matched"},
        {"arxiv_id": "2503.00001", "citation_key": "k2", "groundtruth_arxiv_id": "refB", "label_status": "matched"},
    ]), labels_path)

    # Predictions only has k1, but with wrong groundtruth_arxiv_id "wrong_ref", and missing k2
    pred_path = tmp_path / "pred.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1", "groundtruth_arxiv_id": "wrong_ref",
         "cand_1": "refA", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None}
    ]), pred_path)

    # Scores has no entry for k1
    scores_path = tmp_path / "scores.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "other_key", "ref_arxiv_id": "refA", "score": 1.0}
    ]), scores_path)

    out_val = tmp_path / "val_cross.json"
    validate_prediction_rules(
        inventory_path=inv_path,
        labels_path=labels_path,
        split_path=split_path,
        scores_path=scores_path,
        predictions_path=pred_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is False
    # Check 1: missing prediction for k2
    assert any("Missing prediction" in iss and "k2" in iss for iss in rep["issues"])
    # Check 2: GT mismatch for k1
    assert any("Groundtruth mismatch" in iss and "k1" in iss for iss in rep["issues"])
    # Check 3: missing scores for k1
    assert any("Missing scores" in iss and "k1" in iss for iss in rep["issues"])


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

    # split.parquet
    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid"}
    ]), split_path)

    out_val = tmp_path / "ds_val.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=split_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is True
    assert rep["total_issues"] == 0
    assert rep["tables"]["bibtex"]["columns"] == ["arxiv_id", "citation_key", "entry_type", "title", "authors", "year", "doi", "journal", "url"]
    assert "groundtruth_arxiv_id" in rep["tables"]["predictions"]["columns"]


def test_validate_dataset_cross_shard_duplicate_and_hierarchy_parent_id(tmp_path: Path):
    """Test cross-shard duplicate primary keys and missing hierarchy parent_id."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()

    # papers table with 2 shards containing duplicate arxiv_id
    papers_dir = ds_dir / "papers"
    papers_dir.mkdir()
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "P1", "authors": ["A"], "submission_date": "2025", "revised_dates": [], "venue": None}
    ]), papers_dir / "part-0.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "P1 duplicate", "authors": ["A"], "submission_date": "2025", "revised_dates": [], "venue": None}
    ]), papers_dir / "part-1.parquet")

    # elements
    pq.write_table(pa.Table.from_pylist([
        {"element_id": "2503.00001#e1", "arxiv_id": "2503.00001", "kind": "p", "content": "c"}
    ]), ds_dir / "elements.parquet")

    # hierarchy with parent_id not existing in elements
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "version": 1, "element_id": "2503.00001#e1", "parent_id": "non_existent_elem"}
    ]), ds_dir / "hierarchy.parquet")

    # other minimal tables
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA", "ref_title": "T", "ref_authors": ["A"], "ref_submission_date": "2020", "ref_s2_id": "s2"}
    ]), ds_dir / "references.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "entry_type": "article", "title": "T", "authors": ["A"], "year": 2020, "doi": "d", "journal": "j", "url": "u"}
    ]), ds_dir / "bibtex.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1", "groundtruth_arxiv_id": "refA", "cand_1": "refA", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None}
    ]), ds_dir / "predictions.parquet")

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "valid"}]), split_path)

    out_val = tmp_path / "ds_val_issues.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=split_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is False
    assert any("Duplicate primary key '2503.00001'" in iss for iss in rep["issues"])
    assert any("parent_id 'non_existent_elem'" in iss for iss in rep["issues"])


def test_validate_dataset_candidate_not_in_references_and_partition_mismatch(tmp_path: Path):
    """Test candidate origin in references and partition mismatch against frozen split."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "P1", "authors": ["A"], "submission_date": "2025", "revised_dates": [], "venue": None}
    ]), ds_dir / "papers.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "valid_ref", "ref_title": "T", "ref_authors": ["A"], "ref_submission_date": "2020", "ref_s2_id": "s2"}
    ]), ds_dir / "references.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"element_id": "2503.00001#e1", "arxiv_id": "2503.00001", "kind": "p", "content": "c"}
    ]), ds_dir / "elements.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "version": 1, "element_id": "2503.00001#e1", "parent_id": "root"}
    ]), ds_dir / "hierarchy.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "entry_type": "article", "title": "T", "authors": ["A"], "year": 2020, "doi": "d", "journal": "j", "url": "u"}
    ]), ds_dir / "bibtex.parquet")

    # Predictions has wrong partition ("test" instead of "train") and alien candidate ("alien_ref")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "test", "citation_key": "k1", "groundtruth_arxiv_id": "valid_ref",
         "cand_1": "alien_ref", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None}
    ]), ds_dir / "predictions.parquet")

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "train"}]), split_path)

    out_val = tmp_path / "ds_val_cands.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=split_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is False
    assert any("Partition mismatch" in iss for iss in rep["issues"])
    assert any("does not belong to references" in iss for iss in rep["issues"])


def test_validate_dataset_detects_empty_or_missing_predictions_for_split(tmp_path: Path):
    """Test validate_dataset detects empty predictions table or missing prediction entries for split papers."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "P1", "authors": ["A"], "submission_date": "2025", "revised_dates": [], "venue": None}
    ]), ds_dir / "papers.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA", "ref_title": "T", "ref_authors": ["A"], "ref_submission_date": "2020", "ref_s2_id": "s2"}
    ]), ds_dir / "references.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"element_id": "2503.00001#e1", "arxiv_id": "2503.00001", "kind": "p", "content": "c"}
    ]), ds_dir / "elements.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "version": 1, "element_id": "2503.00001#e1", "parent_id": "root"}
    ]), ds_dir / "hierarchy.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "entry_type": "article", "title": "T", "authors": ["A"], "year": 2020, "doi": "d", "journal": "j", "url": "u"}
    ]), ds_dir / "bibtex.parquet")

    # Case 1: predictions table is EMPTY (0 rows)
    empty_schema = pa.schema([
        ("arxiv_id", pa.string()), ("partition", pa.string()), ("citation_key", pa.string()),
        ("groundtruth_arxiv_id", pa.string()), ("cand_1", pa.string()), ("cand_2", pa.string()),
        ("cand_3", pa.string()), ("cand_4", pa.string()), ("cand_5", pa.string())
    ])
    pq.write_table(pa.Table.from_pylist([], schema=empty_schema), ds_dir / "predictions.parquet")

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "valid"}]), split_path)

    out_val = tmp_path / "ds_val_empty.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=split_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is False
    assert any("Missing predictions in dataset" in iss and "k1" in iss for iss in rep["issues"])


def test_validate_dataset_strict_contract_schema_types(tmp_path: Path):
    """Test validate_dataset enforces strict schema types (e.g. authors must be list<string>, not list<int/null>)."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()

    # Bad papers: authors is list<int64> instead of list<string>
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "P1", "authors": [123, 456], "submission_date": "2025", "revised_dates": ["2025"], "venue": "V"}
    ], schema=pa.schema([
        ("arxiv_id", pa.string()), ("title", pa.string()), ("authors", pa.list_(pa.int64())),
        ("submission_date", pa.string()), ("revised_dates", pa.list_(pa.string())), ("venue", pa.string())
    ])), ds_dir / "papers.parquet")

    # Bad bibtex: year is string instead of int
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "entry_type": "article", "title": "T",
         "authors": ["A"], "year": "2020", "doi": "d", "journal": "j", "url": "u"}
    ], schema=pa.schema([
        ("arxiv_id", pa.string()), ("citation_key", pa.string()), ("entry_type", pa.string()),
        ("title", pa.string()), ("authors", pa.list_(pa.string())), ("year", pa.string()),
        ("doi", pa.string()), ("journal", pa.string()), ("url", pa.string())
    ])), ds_dir / "bibtex.parquet")

    # Other valid tables
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA", "ref_title": "T", "ref_authors": ["A"], "ref_submission_date": "2020", "ref_s2_id": "s2"}
    ], schema=pa.schema([
        ("arxiv_id", pa.string()), ("ref_arxiv_id", pa.string()), ("ref_title", pa.string()),
        ("ref_authors", pa.list_(pa.string())), ("ref_submission_date", pa.string()), ("ref_s2_id", pa.string())
    ])), ds_dir / "references.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"element_id": "2503.00001#e1", "arxiv_id": "2503.00001", "kind": "p", "content": "c"}
    ]), ds_dir / "elements.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "version": 1, "element_id": "2503.00001#e1", "parent_id": "root"}
    ]), ds_dir / "hierarchy.parquet")
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1", "groundtruth_arxiv_id": "refA",
         "cand_1": "refA", "cand_2": None, "cand_3": None, "cand_4": None, "cand_5": None}
    ]), ds_dir / "predictions.parquet")

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "valid"}]), split_path)

    out_val = tmp_path / "ds_val_types.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=split_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is False
    assert any("must be list<string>, but items are int64" in iss for iss in rep["issues"])
    assert any("Column 'year' in table bibtex" in iss and "must be integer type, got string" in iss for iss in rep["issues"])


def test_validate_dataset_candidate_duplicates_null_padding_and_gt_references(tmp_path: Path):
    """Test validate_dataset checks duplicate candidates, null padding, and non-null GT belongs to references."""
    ds_dir = tmp_path / "dataset"
    ds_dir.mkdir()

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "title": "P1", "authors": ["A"], "submission_date": "2025", "revised_dates": ["2025"], "venue": "V"}
    ], schema=pa.schema([
        ("arxiv_id", pa.string()), ("title", pa.string()), ("authors", pa.list_(pa.string())),
        ("submission_date", pa.string()), ("revised_dates", pa.list_(pa.string())), ("venue", pa.string())
    ])), ds_dir / "papers.parquet")

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "ref_arxiv_id": "refA", "ref_title": "T", "ref_authors": ["A"], "ref_submission_date": "2020", "ref_s2_id": "s2"}
    ], schema=pa.schema([
        ("arxiv_id", pa.string()), ("ref_arxiv_id", pa.string()), ("ref_title", pa.string()),
        ("ref_authors", pa.list_(pa.string())), ("ref_submission_date", pa.string()), ("ref_s2_id", pa.string())
    ])), ds_dir / "references.parquet")

    pq.write_table(pa.Table.from_pylist([
        {"element_id": "2503.00001#e1", "arxiv_id": "2503.00001", "kind": "p", "content": "c"}
    ]), ds_dir / "elements.parquet")

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "version": 1, "element_id": "2503.00001#e1", "parent_id": "root"}
    ]), ds_dir / "hierarchy.parquet")

    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "citation_key": "k1", "entry_type": "article", "title": "T",
         "authors": ["A"], "year": 2020, "doi": "d", "journal": "j", "url": "u"},
        {"arxiv_id": "2503.00001", "citation_key": "k2", "entry_type": "article", "title": "T2",
         "authors": ["A"], "year": 2020, "doi": "d", "journal": "j", "url": "u"}
    ], schema=pa.schema([
        ("arxiv_id", pa.string()), ("citation_key", pa.string()), ("entry_type", pa.string()),
        ("title", pa.string()), ("authors", pa.list_(pa.string())), ("year", pa.int32()),
        ("doi", pa.string()), ("journal", pa.string()), ("url", pa.string())
    ])), ds_dir / "bibtex.parquet")

    # k1 has:
    # 1. Duplicate candidates: cand_1="refA", cand_2="refA"
    # 2. GT violation: "alien_gt" not in references
    # k2 has:
    # 3. Invalid null padding: cand_1=None, cand_2="refA"
    pq.write_table(pa.Table.from_pylist([
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k1", "groundtruth_arxiv_id": "alien_gt",
         "cand_1": "refA", "cand_2": "refA", "cand_3": None, "cand_4": None, "cand_5": None},
        {"arxiv_id": "2503.00001", "partition": "valid", "citation_key": "k2", "groundtruth_arxiv_id": "refA",
         "cand_1": None, "cand_2": "refA", "cand_3": None, "cand_4": None, "cand_5": None}
    ]), ds_dir / "predictions.parquet")

    split_path = tmp_path / "split.parquet"
    pq.write_table(pa.Table.from_pylist([{"arxiv_id": "2503.00001", "partition": "valid"}]), split_path)

    out_val = tmp_path / "ds_val_cands_gt.json"
    validate_dataset(
        dataset_dir=ds_dir,
        inventory=tmp_path / "inv.json",
        split=split_path,
        output_path=out_val,
    )

    with open(out_val) as f:
        rep = json.load(f)

    assert rep["valid"] is False
    assert any("Duplicate candidates in top 5" in iss for iss in rep["issues"])
    assert any("Invalid null padding in top 5" in iss for iss in rep["issues"])
    assert any("groundtruth_arxiv_id 'alien_gt' does not belong to references" in iss for iss in rep["issues"])
