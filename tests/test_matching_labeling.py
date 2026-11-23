"""Unit tests for Milestone 3 Reference Matching Labeling Module (Module B - Mẫn Nhi)."""

import json
from pathlib import Path
import numpy as np
import pytest
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from arxiv_pipeline.matching.labeling import (
    normalize_title,
    extract_arxiv_id,
    extract_part_spec,
    extract_author_lastnames,
    match_bibtex_entry_to_candidates,
    run_heuristic_matcher,
    validate_labels,
    build_labels,
    generate_manual_template,
    load_table_from_inventory,
    HEURISTIC_VERSION,
    LABELS_ARROW_SCHEMA,
)


class TestNormalizationAndExtraction:
    """Test string normalization, part spec, and regex arXiv ID extraction."""

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
        assert extract_arxiv_id("hep-th/0608012") == "hep-th/0608012"
        assert extract_arxiv_id("http://arxiv.org/abs/cs.CV/0102003") == "cs.CV/0102003"

        # None / invalid
        assert extract_arxiv_id("Nature 2020 paper") is None
        assert extract_arxiv_id(None) is None

    def test_extract_part_spec(self):
        assert extract_part_spec("Nonlinear Theory of Black Rings. Part I") == "1"
        assert extract_part_spec("Nonlinear Theory of Black Rings. Part II") == "2"
        assert extract_part_spec("Statistical Physics: IV") == "4"
        assert extract_part_spec("Deep Learning (II)") == "2"
        assert extract_part_spec("General Relativity") is None

    def test_extract_author_lastnames_ndarray_and_json(self):
        # Test numpy ndarray
        arr = np.array(["Emparan, Roberto", "Reall, Harvey S"])
        assert extract_author_lastnames(arr) == ["emparan", "reall"]

        # Test JSON string
        json_str = '["Emparan, Roberto", "Reall, Harvey S"]'
        assert extract_author_lastnames(json_str) == ["emparan", "reall"]

        # Test list of dicts (Semantic scholar style)
        dicts = [{"name": "Roberto Emparan"}, {"name": "Harvey S. Reall"}]
        assert extract_author_lastnames(dicts) == ["emparan", "reall"]

        # Test None / NaN
        assert extract_author_lastnames(None) == []
        assert extract_author_lastnames(float("nan")) == []


class TestTightenedHeuristicMatcher:
    """Test tightened heuristic rules."""

    def test_explicit_arxiv_id_must_not_pick_different_ref(self):
        # BibTeX specifies LoRA-FA arXiv ID (2308.03303), but candidate only has LoRA (2106.09685)
        bib = {
            "title": "LoRA-FA: Memory-Efficient Low-Rank Adaptation of Large Language Models",
            "url": "https://arxiv.org/abs/2308.03303",
            "authors": "Zhang et al.",
            "year": 2023,
        }
        cands = [
            {
                "ref_arxiv_id": "2106.09685",
                "ref_title": "LoRA: Low-Rank Adaptation of Large Language Models",
                "ref_authors": ["Hu, Edward J."],
                "ref_submission_date": "2021-06-17",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "no_match"
        assert gt is None
        assert "explicit_arxiv_id_not_in_references" in ev

    def test_lora_fa_does_not_match_lora_without_arxiv_id(self):
        # Even without arXiv URL, LoRA-FA vs LoRA should not match
        bib = {
            "title": "LoRA-FA: Memory-Efficient Low-Rank Adaptation of Large Language Models",
            "authors": "Zhang et al.",
            "year": 2023,
        }
        cands = [
            {
                "ref_arxiv_id": "2106.09685",
                "ref_title": "LoRA: Low-Rank Adaptation of Large Language Models",
                "ref_authors": ["Hu, Edward J."],
                "ref_submission_date": "2021-06-17",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "no_match"
        assert gt is None

    def test_part_ii_does_not_match_part_i(self):
        bib = {
            "title": "Theory of Black Rings. Part II",
            "authors": ["Roberto Emparan", "Harvey S Reall"],
            "year": 2006,
        }
        cands = [
            {
                "ref_arxiv_id": "hep-th/0608012",
                "ref_title": "Theory of Black Rings. Part I",
                "ref_authors": ["Roberto Emparan", "Harvey S Reall"],
                "ref_submission_date": "2006-08-02",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "no_match"
        assert gt is None

    def test_blackrings_short_title_matches(self):
        # "Black rings" matches hep-th/0608012
        bib = {
            "title": "Black rings",
            "authors": np.array(["Emparan, Roberto", "Reall, Harvey S"]),
            "year": 2006,
        }
        cands = [
            {
                "ref_arxiv_id": "hep-th/0608012",
                "ref_title": "Black rings",
                "ref_authors": ["R. Emparan", "H. Reall"],
                "ref_submission_date": "2006-08-02",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "matched"
        assert gt == "hep-th/0608012"

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


class TestLabelValidationAndQuotas:
    """Test validation rules, N key quota reading, and conflict detection."""

    def test_validate_labels_success(self):
        pool_data = [
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 6)
        ] + [
            {"owner": "man-nhi", "arxiv_id": f"paper_auto_{i}", "label_method": "automatic"}
            for i in range(1, 4)
        ]
        pool_df = pd.DataFrame(pool_data)

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
        for i in range(1, 4):
            refs_data.append({
                "arxiv_id": f"paper_auto_{i}",
                "ref_arxiv_id": f"ref_auto_{i}",
                "ref_title": f"Auto Ref {i}",
                "ref_authors": ["Author"],
                "ref_submission_date": "2021-01-01",
                "ref_s2_id": "s2_2",
            })
        refs_df = pd.DataFrame(refs_data)

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
        for i in range(1, 4):
            label_rows.append({
                "arxiv_id": f"paper_auto_{i}",
                "citation_key": f"auto_key_{i}",
                "groundtruth_arxiv_id": f"ref_auto_{i}",
                "label_method": "automatic",
                "labeler": "heuristic",
                "label_status": "matched",
                "evidence": "exact_title",
                "heuristic_version": HEURISTIC_VERSION,
            })
        labels_df = pd.DataFrame(label_rows)

        # Official N = 20 -> required auto = max(2, ceil(0.10 * (20 - 5))) = 2
        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            official_counts={"man-nhi": 20},
        )
        assert report["passed"] is True
        assert len(report["errors"]) == 0

    def test_missing_automatic_quota_fails_validation(self):
        # Pool has 5 manual, but only 1 automatic paper
        pool_df = pd.DataFrame([
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 6)
        ] + [
            {"owner": "man-nhi", "arxiv_id": "paper_auto_1", "label_method": "automatic"}
        ])

        refs_df = pd.DataFrame([
            {"arxiv_id": "paper_auto_1", "ref_arxiv_id": "ref_1"}
        ])
        labels_df = pd.DataFrame([
            {
                "arxiv_id": "paper_auto_1",
                "citation_key": "k1",
                "groundtruth_arxiv_id": "ref_1",
                "label_method": "automatic",
                "labeler": "heuristic",
                "label_status": "matched",
                "evidence": "ev",
                "heuristic_version": HEURISTIC_VERSION,
            }
        ])

        # N = 100 -> required auto = ceil(0.10 * 95) = 10. We only have 1 -> MUST FAIL!
        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            official_counts={"man-nhi": 100},
        )
        assert report["passed"] is False
        assert any("FAILED QUOTA" in err for err in report["errors"])

    def test_conflicting_duplicate_labels_fails_validation(self):
        pool_df = pd.DataFrame([
            {"owner": "man-nhi", "arxiv_id": "p1", "label_method": "manual"}
        ])
        refs_df = pd.DataFrame([
            {"arxiv_id": "p1", "ref_arxiv_id": "r1"},
            {"arxiv_id": "p1", "ref_arxiv_id": "r2"},
        ])
        # Two rows for (p1, key1) with conflicting ground truths
        labels_df = pd.DataFrame([
            {
                "arxiv_id": "p1",
                "citation_key": "key1",
                "groundtruth_arxiv_id": "r1",
                "label_method": "manual",
                "labeler": "man-nhi",
                "label_status": "matched",
                "evidence": "ev1",
                "heuristic_version": None,
            },
            {
                "arxiv_id": "p1",
                "citation_key": "key1",
                "groundtruth_arxiv_id": "r2",
                "label_method": "manual",
                "labeler": "man-nhi",
                "label_status": "matched",
                "evidence": "ev2",
                "heuristic_version": None,
            },
        ])
        report = validate_labels(labels_df, pool_df, refs_df)
        assert report["passed"] is False
        assert any("duplicate/conflicting" in err for err in report["errors"])


class TestBuildLabelsAndInventoryShards:
    """Test build_labels contract, missing shard detection, and template generation."""

    def test_missing_shard_raises_filenotfound(self, tmp_path):
        inv_data = {
            "tables": {
                "bibtex": [str(tmp_path / "non_existent_shard.parquet")]
            }
        }
        with pytest.raises(FileNotFoundError, match="Missing required shard"):
            load_table_from_inventory(inv_data, "bibtex")

    def test_build_labels_halts_on_validation_failure(self, tmp_path):
        # Create input where quota fails
        inputs_dir = tmp_path / "inputs" / "man-nhi"
        bib_dir = inputs_dir / "bibtex"
        ref_dir = inputs_dir / "references"
        bib_dir.mkdir(parents=True)
        ref_dir.mkdir(parents=True)

        pd.DataFrame([{
            "arxiv_id": "paper_1", "citation_key": "k1", "entry_type": "article",
            "title": "T", "authors": ["A"], "year": 2020, "doi": None, "journal": None, "url": None
        }]).to_parquet(bib_dir / "part-0.parquet")

        pd.DataFrame([{
            "arxiv_id": "paper_1", "ref_arxiv_id": "r1", "ref_title": "T",
            "ref_authors": ["A"], "ref_submission_date": "2020-01-01", "ref_s2_id": "s1"
        }]).to_parquet(ref_dir / "part-0.parquet")

        inv_path = tmp_path / "inventory.json"
        with open(inv_path, "w") as f:
            json.dump({
                "owners": {
                    "man-nhi": {
                        "N": 5000,
                        "tables": {
                            "bibtex": [str(bib_dir / "part-0.parquet")],
                            "references": [str(ref_dir / "part-0.parquet")],
                        }
                    }
                }
            }, f)

        # Pool has 1 manual paper (< 5) and 0 auto papers -> validation must fail
        pool_path = tmp_path / "pool.parquet"
        pd.DataFrame([{"owner": "man-nhi", "arxiv_id": "paper_1", "label_method": "manual"}]).to_parquet(pool_path)

        manual_dir = tmp_path / "manual"
        manual_dir.mkdir()
        pd.DataFrame([{
            "arxiv_id": "paper_1", "citation_key": "k1", "groundtruth_arxiv_id": "r1",
            "label_method": "manual", "labeler": "man-nhi", "label_status": "matched",
            "evidence": "ev", "heuristic_version": None
        }]).to_parquet(manual_dir / "man-nhi.parquet")

        out_dir = tmp_path / "out"
        with pytest.raises(ValueError, match="Validation failed"):
            build_labels(inv_path, pool_path, manual_dir, out_dir)

        # labels.parquet must NOT exist!
        assert not (out_dir / "labels.parquet").exists()
        # label_report.json must exist with error details
        assert (out_dir / "label_report.json").exists()

    def test_generate_manual_template_has_no_heuristic_suggestions(self, tmp_path):
        inputs_dir = tmp_path / "inputs" / "man-nhi"
        bib_dir = inputs_dir / "bibtex"
        ref_dir = inputs_dir / "references"
        bib_dir.mkdir(parents=True)
        ref_dir.mkdir(parents=True)

        pd.DataFrame([{
            "arxiv_id": "paper_m1", "citation_key": "key_m1", "entry_type": "article",
            "title": "Machine Learning", "authors": ["Tom Mitchell"], "year": 1997,
            "doi": None, "journal": None, "url": None
        }]).to_parquet(bib_dir / "part-0.parquet")

        pd.DataFrame([{
            "arxiv_id": "paper_m1", "ref_arxiv_id": "9701.00001", "ref_title": "Machine Learning",
            "ref_authors": ["Tom Mitchell"], "ref_submission_date": "1997-01-01", "ref_s2_id": "s2_m"
        }]).to_parquet(ref_dir / "part-0.parquet")

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
        # Verify NO heuristic columns exist
        assert "_suggested_arxiv_id" not in tmpl_df.columns
        assert "_suggested_hint" not in tmpl_df.columns
        assert "_candidate_ref_ids" in tmpl_df.columns
