"""Unit tests for pipeline orchestrator and multi-version runner."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.parsing.runner import (
    discover_paper_dirs,
    extract_fallback_references,
    parse_fallback_hierarchy,
    process_single_publication,
    run_parsing_pipeline,
)
from arxiv_pipeline.parsing.storage import ParsingDatabase


class TestParsingRunner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.corpus_path = self.base_path / "corpus"
        self.db_path = self.base_path / "parsing.sqlite"

        # Create mock publication with v1 (has TeX) and v2 (empty/PDF-only)
        paper_dir = self.corpus_path / "2409.03594"
        v1_src = paper_dir / "sources" / "v1"
        v1_src.mkdir(parents=True, exist_ok=True)
        (v1_src / "main.tex").write_text(
            r"""
        \documentclass{article}
        \begin{document}
        \section{Intro}
        First sentence. Second sentence.
        \begin{thebibliography}{1}
        \bibitem{vaswani2017} Vaswani et al. Attention is all you need. 2017.
        \end{thebibliography}
        \end{document}
        """,
            encoding="utf-8",
        )

        v2_src = paper_dir / "sources" / "v2"
        v2_src.mkdir(parents=True, exist_ok=True)  # empty v2 directory

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_single_publication_flow(self):
        """Review Focus 1 & 5: Process publication with mixed valid and empty versions."""
        res = process_single_publication("2409.03594", self.corpus_path / "2409.03594")
        self.assertEqual(res["arxiv_id"], "2409.03594")
        self.assertEqual(res["version_count"], 2)
        self.assertIn(2, res["empty_versions"])
        self.assertTrue(len(res["elements"]) > 0)

        # Exclusion rule: thebibliography should not appear as an element in elements
        element_contents = [e["content"] for e in res["elements"]]
        for content in element_contents:
            self.assertNotIn(r"\begin{thebibliography}", content)
            self.assertNotIn("vaswani2017", content)

        # Unified BibTeX extraction should contain the citation
        self.assertEqual(len(res["bibtex"]), 1)
        self.assertEqual(res["bibtex"][0]["citation_key"], "vaswani2017")

    def test_end_to_end_pipeline_runner(self):
        """Review Focus 5: Full pipeline runs over directory and commits to database."""
        stats = run_parsing_pipeline(self.corpus_path, self.db_path)
        self.assertEqual(stats["total_publications"], 1)
        self.assertEqual(stats["processed_publications"], 1)
        self.assertIn("peak_ram_mb", stats)
        self.assertIn("total_wall_time_sec", stats)
        self.assertIn("avg_time_per_paper_sec", stats)

        db = ParsingDatabase(self.db_path)
        elements = db.get_elements("2409.03594")
        self.assertTrue(len(elements) > 0)

        hierarchy = db.get_hierarchy("2409.03594")
        self.assertTrue(len(hierarchy) > 0)

        bibtex = db.get_bibtex("2409.03594")
        self.assertEqual(len(bibtex), 1)
        self.assertEqual(bibtex[0]["citation_key"], "vaswani2017")

        meta = db.get_publication_meta("2409.03594")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["version_count"], 2)
        self.assertEqual(meta["empty_versions"], [2])
        db.close()

    def test_pipeline_with_limit_and_batch_commit(self):
        """Test pipeline limit parameter and batching behavior."""
        # Create second paper
        p2_dir = self.corpus_path / "2409.03595"
        v1_src = p2_dir / "sources" / "v1"
        v1_src.mkdir(parents=True, exist_ok=True)
        (v1_src / "paper.tex").write_text(
            r"\section{Methods} We evaluate models.", encoding="utf-8"
        )

        # Run with limit=1, batch_size=1
        stats = run_parsing_pipeline(self.corpus_path, self.db_path, limit=1, batch_size=1)
        self.assertEqual(stats["total_publications"], 1)
        self.assertEqual(stats["processed_publications"], 1)

    def test_publication_without_sources_dir(self):
        """Test fallback when sources directory is absent."""
        p_no_src = self.corpus_path / "2409.99999"
        p_no_src.mkdir(parents=True, exist_ok=True)

        res = process_single_publication("2409.99999", p_no_src)
        self.assertEqual(res["arxiv_id"], "2409.99999")
        self.assertEqual(res["version_count"], 1)
        self.assertEqual(res["empty_versions"], [1])
        self.assertEqual(len(res["elements"]), 0)

    def test_publication_with_flat_sources_dir(self):
        """Test fallback when sources directory contains .tex files directly without version subdirs."""
        p_flat = self.corpus_path / "2409.11111"
        src_dir = p_flat / "sources"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "intro.tex").write_text(
            r"\section{Overview} This is a flat paper.", encoding="utf-8"
        )

        res = process_single_publication("2409.11111", p_flat)
        self.assertEqual(res["arxiv_id"], "2409.11111")
        self.assertEqual(res["version_count"], 1)
        self.assertEqual(len(res["empty_versions"]), 0)
        self.assertTrue(len(res["elements"]) > 0)

    def test_real_m1_layout_with_metadata(self):
        """Test real Milestone 1 layout where v1/ is directly under paper dir and metadata.json is present."""
        p_m1 = self.corpus_path / "2503-11904"
        v1_dir = p_m1 / "v1"
        v1_dir.mkdir(parents=True, exist_ok=True)
        (v1_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\section{Intro} Real M1 layout test.\end{document}", encoding="utf-8")
        (p_m1 / "v2").mkdir(parents=True, exist_ok=True)  # empty v2

        (p_m1 / "metadata.json").write_text(
            json.dumps({
                "arxiv_id": "2503.11904",
                "versions": [{"version": 1}, {"version": 2}, {"version": 3}],
            }),
            encoding="utf-8",
        )

        res = process_single_publication("2503-11904", p_m1)
        self.assertEqual(res["arxiv_id"], "2503.11904")
        self.assertEqual(res["version_count"], 3)
        self.assertIn(2, res["empty_versions"])
        self.assertIn(3, res["empty_versions"])  # v3 was declared in metadata but missing on disk
        self.assertTrue(len(res["elements"]) > 0)

    def test_discover_paper_dirs_nested_raw(self):
        """Test discovering paper directories in nested raw/<member>/<paper_id> layout."""
        raw_corpus = self.base_path / "m1_corpus"
        paper_in_raw = raw_corpus / "raw" / "hoang" / "2503-11892"
        (paper_in_raw / "v1").mkdir(parents=True, exist_ok=True)
        (paper_in_raw / "v1" / "main.tex").write_text(r"Testing nested.", encoding="utf-8")

        discovered = discover_paper_dirs(raw_corpus)
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].name, "2503-11892")


if __name__ == "__main__":
    unittest.main()
