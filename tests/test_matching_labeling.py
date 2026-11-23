"""Unit tests for Milestone 3 Reference Matching Labeling Module (Module B - Mẫn Nhi)."""

import json
from pathlib import Path
import pytest
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from arxiv_pipeline.matching.labeling import (
    normalize_title,
    extract_arxiv_id,
    match_bibtex_entry_to_candidates,
    run_heuristic_matcher,
    validate_labels,
    build_labels,
    generate_manual_template,
    HEURISTIC_VERSION,
    LABELS_ARROW_SCHEMA,
)


class TestNormalizationAndExtraction:
    """Test string normalization and regex arXiv ID extraction."""

    def test_normalize_title(self):
        raw = r"Attention Is All \emph{You} Need: A {Comprehensive} Survey!"
        expected = "attention is all you need a comprehensive survey"
        assert normalize_title(raw) == expected

        assert normalize_title("") == ""
        assert normalize_title(None) == ""

    def test_extract_arxiv_id(self):
        # New style
        assert extract_arxiv_id("https://arxiv.org/abs/2106.09685v2") == "2106.09685"
        assert extract_arxiv_id("arXiv:2501.13787") == "2501.13787"
        assert extract_arxiv_id("10.48550/arXiv.2401.00123") == "2401.00123"

        # Old style
        assert extract_arxiv_id("math/0501234v1") == "math/0501234"
        assert extract_arxiv_id("http://arxiv.org/abs/cs.CV/0102003") == "cs.CV/0102003"

        # None / invalid
        assert extract_arxiv_id("Nature 2020 paper") is None
        assert extract_arxiv_id(None) is None


class TestHeuristicMatcher:
    """Test the rule-based heuristic matcher."""

    def test_direct_arxiv_id_match(self):
        bib = {
            "title": "Some Paper Title",
            "url": "https://arxiv.org/abs/2106.09685",
            "authors": "Author A",
            "year": 2021,
        }
        cands = [
            {"ref_arxiv_id": "2106.09685", "ref_title": "Paper Title", "ref_authors": ["Author A"]},
            {"ref_arxiv_id": "2001.00001", "ref_title": "Other Paper", "ref_authors": ["Author B"]},
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "matched"
        assert gt == "2106.09685"
        assert "direct_arxiv_id_match" in ev

    def test_exact_normalized_title_match(self):
        bib = {
            "title": "Convolutional LSTM Network: A Machine Learning Approach for Precipitation Nowcasting",
            "url": None,
            "doi": None,
            "journal": None,
            "authors": "Shi et al.",
            "year": 2015,
        }
        cands = [
            {
                "ref_arxiv_id": "1506.04214",
                "ref_title": "Convolutional LSTM Network: A Machine Learning Approach for Precipitation Nowcasting",
                "ref_authors": ["Xingjian Shi"],
                "ref_submission_date": "2015-06-13T03:19:24Z",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "matched"
        assert gt == "1506.04214"
        assert "exact_normalized_title_match" in ev

    def test_fuzzy_title_and_author_match(self):
        bib = {
            "title": "Language Models are Few-Shot Learners",
            "authors": "Tom Brown and Benjamin Mann",
            "year": 2020,
        }
        cands = [
            {
                "ref_arxiv_id": "2005.14165",
                "ref_title": "Language Models are Few-Shot Learners (NeurIPS 2020)",
                "ref_authors": ["Tom B. Brown", "Benjamin Mann"],
                "ref_submission_date": "2020-05-28T00:00:00Z",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "matched"
        assert gt == "2005.14165"

    def test_ambiguity_detection(self):
        bib = {
            "title": "Graph Neural Networks for Molecule Generation",
            "authors": "Wang et al.",
            "year": 2021,
        }
        cands = [
            {
                "ref_arxiv_id": "2101.00001",
                "ref_title": "Graph Neural Networks for Molecule Generation",
                "ref_authors": ["Wang"],
                "ref_submission_date": "2021-01-01",
            },
            {
                "ref_arxiv_id": "2101.00002",
                "ref_title": "Graph Neural Networks for Molecule Generation",
                "ref_authors": ["Wang"],
                "ref_submission_date": "2021-01-01",
            },
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "ambiguous"
        assert gt is None

    def test_no_match(self):
        bib = {
            "title": "Introduction to Algorithms 3rd Edition",
            "authors": "Cormen, Leiserson, Rivest, Stein",
            "year": 2009,
        }
        cands = [
            {"ref_arxiv_id": "2101.00001", "ref_title": "Deep Learning with Transformers", "ref_authors": ["Smith"]}
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "no_match"
        assert gt is None


class TestLabelValidationAndQuotas:
    """Test validation rules, schema compliance, and quota verification."""

    def test_validate_labels_success(self):
        # Create pool with 5 manual papers for man-nhi
        pool_data = [
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 6)
        ] + [
            {"owner": "man-nhi", "arxiv_id": "paper_auto_1", "label_method": "automatic"},
            {"owner": "man-nhi", "arxiv_id": "paper_auto_2", "label_method": "automatic"},
        ]
        pool_df = pd.DataFrame(pool_data)

        # References
        refs_data = []
        for i in range(1, 6):
            for k in range(1, 25):
                refs_data.append({
                    "arxiv_id": f"paper_{i}",
                    "ref_arxiv_id": f"ref_{i}_{k}",
                    "ref_title": f"Ref Title {k}",
                    "ref_authors": ["Author"],
                    "ref_submission_date": "2021-01-01",
                    "ref_s2_id": "s2_1",
                })
        refs_data.append({
            "arxiv_id": "paper_auto_1",
            "ref_arxiv_id": "ref_auto_1",
            "ref_title": "Auto Ref 1",
            "ref_authors": ["Author"],
            "ref_submission_date": "2021-01-01",
            "ref_s2_id": "s2_2",
        })
        refs_df = pd.DataFrame(refs_data)

        # Labels: exactly 22 matched per manual paper
        label_rows = []
        for i in range(1, 6):
            for k in range(1, 23):
                label_rows.append({
                    "arxiv_id": f"paper_{i}",
                    "citation_key": f"key_{i}_{k}",
                    "groundtruth_arxiv_id": f"ref_{i}_{k}",
                    "label_method": "manual",
                    "labeler": "man-nhi",
                    "label_status": "matched",
                    "evidence": "manual_verified",
                    "heuristic_version": None,
                })
        # Add automatic row
        label_rows.append({
            "arxiv_id": "paper_auto_1",
            "citation_key": "auto_key_1",
            "groundtruth_arxiv_id": "ref_auto_1",
            "label_method": "automatic",
            "labeler": "heuristic",
            "label_status": "matched",
            "evidence": "exact_title",
            "heuristic_version": HEURISTIC_VERSION,
        })
        labels_df = pd.DataFrame(label_rows)

        # Validate with mock official count
        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            official_counts={"man-nhi": 20},
        )
        assert report["passed"] is True
        assert len(report["errors"]) == 0
        assert report["quota_report"]["manual"]["by_owner"]["man-nhi"]["passed"] is True

    def test_validate_labels_fails_when_quota_not_met(self):
        # Only 3 manual papers (need >= 5)
        pool_data = [
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 4)
        ]
        pool_df = pd.DataFrame(pool_data)

        refs_df = pd.DataFrame([
            {"arxiv_id": "paper_1", "ref_arxiv_id": "ref_1_1"}
        ])

        labels_df = pd.DataFrame([
            {
                "arxiv_id": "paper_1",
                "citation_key": "key_1",
                "groundtruth_arxiv_id": "ref_1_1",
                "label_method": "manual",
                "labeler": "man-nhi",
                "label_status": "matched",
                "evidence": "manual",
                "heuristic_version": None,
            }
        ])

        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            official_counts={"man-nhi": 5000},
        )
        assert report["passed"] is False
        assert any("manual papers count = 3 (< 5 required)" in err for err in report["errors"])

    def test_validate_labels_fails_on_invalid_groundtruth(self):
        pool_df = pd.DataFrame([
            {"owner": "man-nhi", "arxiv_id": "paper_1", "label_method": "manual"}
        ])
        refs_df = pd.DataFrame([
            {"arxiv_id": "paper_1", "ref_arxiv_id": "real_ref_1"}
        ])
        # groundtruth_arxiv_id is "fake_ref" which does not exist in refs_df
        labels_df = pd.DataFrame([
            {
                "arxiv_id": "paper_1",
                "citation_key": "key_1",
                "groundtruth_arxiv_id": "fake_ref",
                "label_method": "manual",
                "labeler": "man-nhi",
                "label_status": "matched",
                "evidence": "manual",
                "heuristic_version": None,
            }
        ])
        report = validate_labels(labels_df, pool_df, refs_df)
        assert report["passed"] is False
        assert any("does NOT exist in candidate references" in err for err in report["errors"])


class TestBuildLabelsContract:
    """End-to-end integration test of build_labels contract."""

    def test_build_labels_pipeline(self, tmp_path):
        # 1. Prepare dummy inputs directory
        inputs_dir = tmp_path / "inputs" / "man-nhi"
        bib_dir = inputs_dir / "bibtex"
        ref_dir = inputs_dir / "references"
        bib_dir.mkdir(parents=True)
        ref_dir.mkdir(parents=True)

        bib_data = [
            {
                "arxiv_id": "paper_1",
                "citation_key": "key_1_1",
                "entry_type": "article",
                "title": "Title One",
                "authors": "Author A",
                "year": 2021,
                "doi": None,
                "journal": None,
                "url": "https://arxiv.org/abs/2101.00001",
            },
            {
                "arxiv_id": "paper_2",
                "citation_key": "key_2_1",
                "entry_type": "inproceedings",
                "title": "Title Two",
                "authors": "Author B",
                "year": 2022,
                "doi": None,
                "journal": None,
                "url": None,
            },
        ]
        ref_data = [
            {
                "arxiv_id": "paper_1",
                "ref_arxiv_id": "2101.00001",
                "ref_title": "Title One",
                "ref_authors": ["Author A"],
                "ref_submission_date": "2021-01-01",
                "ref_s2_id": "s2_1",
            },
            {
                "arxiv_id": "paper_2",
                "ref_arxiv_id": "2201.00002",
                "ref_title": "Title Two",
                "ref_authors": ["Author B"],
                "ref_submission_date": "2022-01-01",
                "ref_s2_id": "s2_2",
            },
        ]
        pd.DataFrame(bib_data).to_parquet(bib_dir / "part-0.parquet")
        pd.DataFrame(ref_data).to_parquet(ref_dir / "part-0.parquet")

        # 2. Inventory json
        inventory = {
            "owners": {
                "man-nhi": {
                    "n_official": 10,
                    "tables": {
                        "bibtex": [str(bib_dir / "part-0.parquet")],
                        "references": [str(ref_dir / "part-0.parquet")],
                    },
                }
            }
        }
        inv_path = tmp_path / "inventory.json"
        with open(inv_path, "w") as f:
            json.dump(inventory, f)

        # 3. Pool: paper_1 is manual, paper_2 is automatic
        pool_data = [
            {"owner": "man-nhi", "arxiv_id": "paper_1", "label_method": "manual"},
            {"owner": "man-nhi", "arxiv_id": "paper_2", "label_method": "automatic"},
        ]
        pool_path = tmp_path / "pool.parquet"
        pd.DataFrame(pool_data).to_parquet(pool_path)

        # 4. Manual file
        manual_dir = tmp_path / "manual"
        manual_dir.mkdir()
        manual_df = pd.DataFrame([
            {
                "arxiv_id": "paper_1",
                "citation_key": "key_1_1",
                "groundtruth_arxiv_id": "2101.00001",
                "label_method": "manual",
                "labeler": "man-nhi",
                "label_status": "matched",
                "evidence": "manual_verified",
                "heuristic_version": None,
            }
        ])
        manual_df.to_parquet(manual_dir / "man-nhi.parquet")

        # 5. Run build_labels
        output_dir = tmp_path / "output"
        out_labels = build_labels(
            inventory=inv_path,
            pool=pool_path,
            manual_dir=manual_dir,
            output_dir=output_dir,
        )

        assert out_labels.exists()
        assert (output_dir / "label_report.json").exists()

        result_df = pd.read_parquet(out_labels)
        assert len(result_df) == 2

        # Check manual row
        row_man = result_df[result_df["arxiv_id"] == "paper_1"].iloc[0]
        assert row_man["label_method"] == "manual"
        assert row_man["groundtruth_arxiv_id"] == "2101.00001"
        assert pd.isna(row_man["heuristic_version"]) or row_man["heuristic_version"] is None

        # Verify PyArrow table directly
        pq_table = pq.read_table(out_labels)
        col_names = pq_table.column_names
        assert col_names == [
            "arxiv_id", "citation_key", "groundtruth_arxiv_id",
            "label_method", "labeler", "label_status", "evidence", "heuristic_version"
        ]
        # First row is manual
        assert pq_table.column("heuristic_version")[0].as_py() is None
        # Second row is automatic
        assert pq_table.column("heuristic_version")[1].as_py() == HEURISTIC_VERSION

        # Check automatic row
        row_auto = result_df[result_df["arxiv_id"] == "paper_2"].iloc[0]
        assert row_auto["label_method"] == "automatic"
        assert row_auto["groundtruth_arxiv_id"] == "2201.00002"
        assert row_auto["heuristic_version"] == HEURISTIC_VERSION

    def test_generate_manual_template(self, tmp_path):
        # Prepare inputs
        inputs_dir = tmp_path / "inputs" / "man-nhi"
        bib_dir = inputs_dir / "bibtex"
        ref_dir = inputs_dir / "references"
        bib_dir.mkdir(parents=True)
        ref_dir.mkdir(parents=True)

        bib_data = [{
            "arxiv_id": "paper_m1",
            "citation_key": "key_m1",
            "entry_type": "article",
            "title": "Machine Learning",
            "authors": "Tom Mitchell",
            "year": 1997,
            "doi": None,
            "journal": None,
            "url": None,
        }]
        ref_data = [{
            "arxiv_id": "paper_m1",
            "ref_arxiv_id": "9701.00001",
            "ref_title": "Machine Learning",
            "ref_authors": ["Tom Mitchell"],
            "ref_submission_date": "1997-01-01",
            "ref_s2_id": "s2_m",
        }]
        pd.DataFrame(bib_data).to_parquet(bib_dir / "part-0.parquet")
        pd.DataFrame(ref_data).to_parquet(ref_dir / "part-0.parquet")

        inv_path = tmp_path / "inventory.json"
        with open(inv_path, "w") as f:
            json.dump({
                "owners": {
                    "man-nhi": {
                        "tables": {
                            "bibtex": [str(bib_dir / "part-0.parquet")],
                            "references": [str(ref_dir / "part-0.parquet")],
                        }
                    }
                }
            }, f)

        pool_path = tmp_path / "pool.parquet"
        pd.DataFrame([{"owner": "man-nhi", "arxiv_id": "paper_m1", "label_method": "manual"}]).to_parquet(pool_path)

        out_template = tmp_path / "man-nhi_template.parquet"
        res_path = generate_manual_template(inv_path, pool_path, "man-nhi", out_template)

        assert res_path.exists()
        tmpl_df = pd.read_parquet(res_path)
        assert len(tmpl_df) == 1
        assert tmpl_df.iloc[0]["citation_key"] == "key_m1"
        assert tmpl_df.iloc[0]["label_status"] == "unreviewed"
        assert tmpl_df.iloc[0]["_suggested_arxiv_id"] == "9701.00001"
