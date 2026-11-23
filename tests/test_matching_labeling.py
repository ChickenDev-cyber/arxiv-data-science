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

    def test_extract_part_spec_including_trailing_numeral(self):
        assert extract_part_spec("Nonlinear Theory of Black Rings. Part I") == "1"
        assert extract_part_spec("Nonlinear Theory of Black Rings. Part II") == "2"
        assert extract_part_spec("Statistical Physics: IV") == "4"
        assert extract_part_spec("Deep Learning (II)") == "2"
        assert extract_part_spec("General Relativity") is None

        # Real test case: Dejanlinintext trailing I and II
        assert extract_part_spec("Linear waves in the interior of extremal black holes I") == "1"
        assert extract_part_spec("Linear waves in the interior of extremal black holes II") == "2"
        assert extract_part_spec("Linear waves in the interior of extremal black holes. III") == "3"

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

    def test_dejanlinintext_part_ii_does_not_match_part_i(self):
        # Real case: DejanlinintextII vs 1509.06568 (Part I)
        bib = {
            "title": "Linear waves in the interior of extremal black holes II",
            "authors": ["Dejan Gajic"],
            "year": 2017,
        }
        cands = [
            {
                "ref_arxiv_id": "1509.06568",
                "ref_title": "Linear Waves in the Interior of Extremal Black Holes I",
                "ref_authors": ["D. Gajic"],
                "ref_submission_date": "2015-09-22T12:29:24Z",
            }
        ]
        status, gt, ev = match_bibtex_entry_to_candidates(bib, cands)
        assert status == "no_match"
        assert gt is None

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
    """Test validation rules, N key quota reading, BibTeX verification, and conflict detection."""

    def test_validate_labels_success(self):
        pool_data = [
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 6)
        ] + [
            {"owner": "man-nhi", "arxiv_id": f"paper_auto_{i}", "label_method": "automatic"}
            for i in range(1, 4)
        ]
        pool_df = pd.DataFrame(pool_data)

        bib_data = []
        refs_data = []
        for i in range(1, 6):
            for k in range(1, 25):
                bib_data.append({
                    "arxiv_id": f"paper_{i}",
                    "citation_key": f"key_{i}_{k}",
                })
                refs_data.append({
                    "arxiv_id": f"paper_{i}",
                    "ref_arxiv_id": f"ref_{i}_{k}",
                })
        for i in range(1, 4):
            bib_data.append({
                "arxiv_id": f"paper_auto_{i}",
                "citation_key": f"auto_key_{i}",
            })
            refs_data.append({
                "arxiv_id": f"paper_auto_{i}",
                "ref_arxiv_id": f"ref_auto_{i}",
            })
        bib_df = pd.DataFrame(bib_data)
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
            bibtex_df=bib_df,
            official_counts={"man-nhi": 20},
        )
        assert report["passed"] is True
        assert len(report["errors"]) == 0

    def test_non_existent_bibtex_key_fails_validation_and_quota(self):
        pool_df = pd.DataFrame([
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 6)
        ] + [
            {"owner": "man-nhi", "arxiv_id": "paper_auto_1", "label_method": "automatic"},
            {"owner": "man-nhi", "arxiv_id": "paper_auto_2", "label_method": "automatic"},
        ])

        # BibTeX only has 10 valid keys per paper
        bib_data = []
        for i in range(1, 6):
            for k in range(1, 11):
                bib_data.append({"arxiv_id": f"paper_{i}", "citation_key": f"key_{i}_{k}"})
        bib_data.append({"arxiv_id": "paper_auto_1", "citation_key": "auto_k1"})
        bib_data.append({"arxiv_id": "paper_auto_2", "citation_key": "auto_k2"})
        bib_df = pd.DataFrame(bib_data)

        # Labels include phantom keys (key_i_11 to key_i_22) not in bibtex
        label_rows = []
        refs_data = []
        for i in range(1, 6):
            for k in range(1, 23):
                refs_data.append({"arxiv_id": f"paper_{i}", "ref_arxiv_id": f"ref_{i}_{k}"})
                label_rows.append({
                    "arxiv_id": f"paper_{i}",
                    "citation_key": f"key_{i}_{k}",  # 11-22 are NOT in bibtex!
                    "groundtruth_arxiv_id": f"ref_{i}_{k}",
                    "label_method": "manual",
                    "labeler": "man-nhi",
                    "label_status": "matched",
                    "evidence": "manual",
                    "heuristic_version": None,
                })
        labels_df = pd.DataFrame(label_rows)
        refs_df = pd.DataFrame(refs_data)

        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            bibtex_df=bib_df,
            official_counts={"man-nhi": 20},
        )
        assert report["passed"] is False
        assert any("DO NOT exist in bibtex table" in err for err in report["errors"])
        # And matched pairs should NOT count the phantom keys (only 10 per paper < 20 quota)
        assert report["quota_report"]["manual"]["by_owner"]["man-nhi"]["passed"] is False

    def test_missing_automatic_quota_fails_validation(self):
        pool_df = pd.DataFrame([
            {"owner": "man-nhi", "arxiv_id": f"paper_{i}", "label_method": "manual"}
            for i in range(1, 6)
        ] + [
            {"owner": "man-nhi", "arxiv_id": "paper_auto_1", "label_method": "automatic"}
        ])

        refs_df = pd.DataFrame([{"arxiv_id": "paper_auto_1", "ref_arxiv_id": "ref_1"}])
        labels_df = pd.DataFrame([{
            "arxiv_id": "paper_auto_1", "citation_key": "k1", "groundtruth_arxiv_id": "ref_1",
            "label_method": "automatic", "labeler": "heuristic", "label_status": "matched",
            "evidence": "ev", "heuristic_version": HEURISTIC_VERSION,
        }])

        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            official_counts={"man-nhi": 100},
        )
        assert report["passed"] is False
        assert any("FAILED QUOTA" in err for err in report["errors"])


class TestBuildLabelsAndInventoryShards:
    """Test build_labels contract, provenance enforcement, and shared shard deduplication."""

    def test_provenance_error_on_invalid_manual_input(self, tmp_path):
        # Create input where manual file has label_method == 'automatic'
        manual_dir = tmp_path / "manual"
        manual_dir.mkdir()
        pd.DataFrame([{
            "arxiv_id": "paper_1", "citation_key": "k1", "groundtruth_arxiv_id": "r1",
            "label_method": "automatic",  # WRONG! Must be 'manual'
            "labeler": "man-nhi", "label_status": "matched",
            "evidence": "ev", "heuristic_version": None
        }]).to_parquet(manual_dir / "man-nhi.parquet")

        inputs_dir = tmp_path / "inputs"
        inputs_dir.mkdir()
        bib_path = inputs_dir / "bib.parquet"
        ref_path = inputs_dir / "ref.parquet"
        pd.DataFrame([{"arxiv_id": "paper_1", "citation_key": "k1"}]).to_parquet(bib_path)
        pd.DataFrame([{"arxiv_id": "paper_1", "ref_arxiv_id": "r1"}]).to_parquet(ref_path)

        inv_path = tmp_path / "inventory.json"
        with open(inv_path, "w") as f:
            json.dump({
                "tables": {
                    "bibtex": [str(bib_path)],
                    "references": [str(ref_path)],
                }
            }, f)

        pool_path = tmp_path / "pool.parquet"
        pd.DataFrame([{"owner": "man-nhi", "arxiv_id": "paper_1", "label_method": "manual"}]).to_parquet(pool_path)

        with pytest.raises(ValueError, match="Provenance error in manual file"):
            build_labels(inv_path, pool_path, manual_dir, tmp_path / "out")

    def test_shared_shard_deduplication(self, tmp_path):
        # Shard file
        shard_path = tmp_path / "shared_bib.parquet"
        pd.DataFrame([
            {"arxiv_id": "p1", "citation_key": "k1", "entry_type": "article"},
            {"arxiv_id": "p2", "citation_key": "k2", "entry_type": "article"},
        ]).to_parquet(shard_path)

        # Inventory where two owners declare the EXACT SAME shared shard
        inv_data = {
            "owners": {
                "man-nhi": {"tables": {"bibtex": [str(shard_path)]}},
                "trong": {"tables": {"bibtex": [str(shard_path)]}},
            }
        }
        loaded = load_table_from_inventory(inv_data, "bibtex", inventory_dir=tmp_path)
        # Must be exactly 2 rows, NOT 4 rows!
        assert len(loaded) == 2

    def test_relative_path_resolved_via_inventory_dir(self, tmp_path):
        sub_dir = tmp_path / "inventory_sub"
        sub_dir.mkdir()
        data_file = sub_dir / "bib.parquet"
        pd.DataFrame([{"arxiv_id": "p1", "citation_key": "k1"}]).to_parquet(data_file)

        # Relative path "bib.parquet" relative to sub_dir
        inv_data = {"tables": {"bibtex": ["bib.parquet"]}}
        loaded = load_table_from_inventory(inv_data, "bibtex", inventory_dir=sub_dir)
        assert len(loaded) == 1

    def test_empty_bibtex_still_validates_citation_keys_and_fails_quota(self):
        pool_df = pd.DataFrame([
            {"arxiv_id": f"2501.0000{i}", "owner": "man-nhi", "label_method": "manual"}
            for i in range(1, 6)
        ])
        labels_rows = []
        for i in range(1, 6):
            for j in range(1, 25):
                labels_rows.append({
                    "arxiv_id": f"2501.0000{i}",
                    "citation_key": f"key_{j}",
                    "groundtruth_arxiv_id": "2401.99999",
                    "label_method": "manual",
                    "labeler": "man-nhi",
                    "label_status": "matched",
                    "evidence": "manual_verified",
                    "heuristic_version": None,
                })
        labels_df = pd.DataFrame(labels_rows)
        refs_df = pd.DataFrame([
            {"arxiv_id": f"2501.0000{i}", "ref_arxiv_id": "2401.99999"}
            for i in range(1, 6)
        ])

        # Empty bibtex table!
        empty_bibtex = pd.DataFrame(columns=["arxiv_id", "citation_key"])

        report = validate_labels(
            labels_df=labels_df,
            pool_df=pool_df,
            references_df=refs_df,
            bibtex_df=empty_bibtex,
        )

        # Must NOT pass validation: all keys must be reported missing from bibtex
        assert not report["passed"]
        assert any("DO NOT exist in bibtex table" in e for e in report["errors"])
        # Quota must fail because entries not existing in bibtex cannot count as matched
        assert not report["quota_report"]["manual"]["passed"]

    def test_conflicting_metadata_on_same_pk_raises_conflict_error(self, tmp_path):
        # Shard 1
        s1 = tmp_path / "shard1.parquet"
        pd.DataFrame([
            {"arxiv_id": "p1", "citation_key": "k1", "title": "Paper One Version A", "year": 2021}
        ]).to_parquet(s1)

        # Shard 2 with same PK ('p1', 'k1') but DIFFERENT metadata
        s2 = tmp_path / "shard2.parquet"
        pd.DataFrame([
            {"arxiv_id": "p1", "citation_key": "k1", "title": "Paper One Version B", "year": 2022}
        ]).to_parquet(s2)

        inv_data = {
            "tables": {"bibtex": [str(s1), str(s2)]}
        }

        # Must raise ValueError reporting metadata conflict, NOT keep="first" silently!
        with pytest.raises(ValueError, match="Conflict in table 'bibtex'"):
            load_table_from_inventory(inv_data, "bibtex", inventory_dir=tmp_path)

        # But if metadata is 100% identical, it merges cleanly into 1 row
        s2_identical = tmp_path / "shard2_identical.parquet"
        pd.DataFrame([
            {"arxiv_id": "p1", "citation_key": "k1", "title": "Paper One Version A", "year": 2021}
        ]).to_parquet(s2_identical)

        inv_data_clean = {
            "tables": {"bibtex": [str(s1), str(s2_identical)]}
        }
        clean_loaded = load_table_from_inventory(inv_data_clean, "bibtex", inventory_dir=tmp_path)
        assert len(clean_loaded) == 1
        assert clean_loaded.iloc[0]["title"] == "Paper One Version A"
