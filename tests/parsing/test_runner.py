"""Unit tests for pipeline orchestrator and multi-version runner."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.parsing.runner import (
    PipelineComponents,
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

    def test_pluggable_components_pipeline_flow_and_cite_rewrite(self):
        """Verify M1 -> M4 -> Rewrite -> M3 -> M5 orchestration deduplicates rewritten citations."""
        p_hook = self.corpus_path / "2503.77777"
        (p_hook / "v1").mkdir(parents=True, exist_ok=True)
        (p_hook / "v2").mkdir(parents=True, exist_ok=True)
        (p_hook / "v1" / "main.tex").write_text("dummy v1", encoding="utf-8")
        (p_hook / "v2" / "main.tex").write_text("dummy v2", encoding="utf-8")

        def mock_gather(src_dir, arxiv_id, ver):
            tex = r"We cite \cite{oldA}." if ver == 1 else r"We cite \cite{newB}."
            return {
                "arxiv_id": arxiv_id,
                "version": ver,
                "root_file": "main.tex",
                "compilation_path": ["main.tex"],
                "merged_tex": tex,
                "bib_files": [],
                "bbl_files": [],
                "status": "ok",
            }

        def mock_refs(sources):
            return {
                "arxiv_id": sources[0]["arxiv_id"],
                "unified_bibtex": [
                    {
                        "arxiv_id": sources[0]["arxiv_id"],
                        "citation_key": "canonA",
                        "entry_type": "article",
                        "title": "Unified Paper",
                        "authors": ["Alice"],
                        "year": 2024,
                        "doi": "",
                        "journal": "",
                        "url": "",
                    }
                ],
                "citation_remap": {1: {"oldA": "canonA"}, 2: {"newB": "canonA"}},
            }

        def mock_rewrite(text, remap):
            for old, new in remap.items():
                text = text.replace(old, new)
            return text

        def mock_hierarchy(source):
            return {
                "arxiv_id": source["arxiv_id"],
                "version": source["version"],
                "nodes": [
                    {"temp_id": "root", "kind": "document", "content": "Doc", "parent_temp_id": None},
                    {"temp_id": "s1", "kind": "sentence", "content": source["merged_tex"], "parent_temp_id": "root"},
                ],
            }

        components = PipelineComponents(
            gather_fn=mock_gather,
            refs_fn=mock_refs,
            rewrite_fn=mock_rewrite,
            hierarchy_fn=mock_hierarchy,
        )
        res = process_single_publication("2503.77777", p_hook, components=components)
        sent_elements = [e for e in res["elements"] if e["kind"] == "sentence"]
        self.assertEqual(len(sent_elements), 1)
        self.assertEqual(sent_elements[0]["content"], r"We cite \cite{canonA}.")

    def test_bib_file_discovery_ignores_unused_template_and_uncited_dump(self):
        """Verify fallback ignores unused template .bib and uncited Zotero dump entries."""
        p_bib = self.corpus_path / "2503.88888"
        v1 = p_bib / "v1"
        v1.mkdir(parents=True, exist_ok=True)
        (v1 / "main.tex").write_text(
            r"""
            \documentclass{article}
            \begin{document}
            \section{Intro}
            We build upon \citep{cited_key}.
            \bibliography{refs}
            \end{document}
            """,
            encoding="utf-8",
        )
        (v1 / "refs.bib").write_text(
            """
            @String(ICLR = {Int. Conf. Learn. Represent.})
            @article{cited_key,
              title={Targeted Paper Title},
              author={Doe, John and Smith, Jane},
              journal=ICLR,
              year={2024}
            }
            @misc{uncited_dump_key,
              title={Unused Zotero Entry},
              author={Nobody},
              year={2020}
            }
            """,
            encoding="utf-8",
        )
        (v1 / "sample-bibliography.bib").write_text(
            """
            @article{template_key,
              title={Overleaf Template Junk},
              year={1999}
            }
            """,
            encoding="utf-8",
        )

        res = process_single_publication("2503.88888", p_bib)
        self.assertEqual(res["bib_source_kind"], "bib_file")
        keys = [b["citation_key"] for b in res["bibtex"]]
        self.assertEqual(keys, ["cited_key"])
        self.assertEqual(res["bibtex"][0]["title"], "Targeted Paper Title")
        self.assertEqual(res["bibtex"][0]["authors"], ["Doe, John", "Smith, Jane"])
        self.assertEqual(res["bibtex"][0]["journal"], "Int. Conf. Learn. Represent.")
        self.assertEqual(res["bibtex"][0]["year"], 2024)

    def test_bbl_missing_classification(self):
        """Papers with \\bibliography command but missing .bib and no thebibliography record bbl_missing."""
        p_bbl = self.corpus_path / "2503.66666"
        v1 = p_bbl / "v1"
        v1.mkdir(parents=True, exist_ok=True)
        (v1 / "main.tex").write_text(
            r"\documentclass{article}\begin{document}\section{Intro} See \cite{x}.\bibliography{missing_bib}\end{document}",
            encoding="utf-8",
        )
        res = process_single_publication("2503.66666", p_bbl)
        self.assertEqual(res["bib_source_kind"], "bbl_missing")
        self.assertEqual(res["bibtex"], [])
        self.assertTrue(len(res["elements"]) > 0)

    def test_pipeline_telemetry_includes_corpus_stats_and_db_size(self):
        """Verify run_parsing_pipeline exposes pre/post dedup counts, coverage, and db_size_mb."""
        stats = run_parsing_pipeline(self.corpus_path, self.db_path)
        self.assertIn("elements_before_dedup", stats)
        self.assertIn("elements_after_dedup", stats)
        self.assertIn("bibtex_before_dedup", stats)
        self.assertIn("bibtex_after_dedup", stats)
        self.assertIn("total_versions", stats)
        self.assertIn("empty_versions_count", stats)
        self.assertIn("bib_source_counts", stats)
        self.assertIn("db_size_mb", stats)
        self.assertGreater(stats["db_size_mb"], 0.0)

    def test_gather_expands_input_files_in_order(self):
        r"""Issue 1 reproduction: verify \input files are expanded and sentences are not lost."""
        p_input = self.corpus_path / "2409.01111"
        v1 = p_input / "v1"
        v1.mkdir(parents=True, exist_ok=True)
        (v1 / "main.tex").write_text(
            r"\documentclass{article}\begin{document}\section{Intro}Main sent. \input{body.tex}\end{document}",
            encoding="utf-8",
        )
        (v1 / "body.tex").write_text(
            r"Body sentence one. Body sentence two.",
            encoding="utf-8",
        )
        res = process_single_publication("2409.01111", p_input)
        contents = [e["content"] for e in res["elements"]]
        self.assertIn("Body sentence one.", contents)
        self.assertIn("Body sentence two.", contents)
        for c in contents:
            self.assertNotIn(r"\input{body.tex}", c)

    def test_relative_corpus_path_extracts_bibtex(self):
        """Issue 3 reproduction: verify relative corpus paths do not drop bibtex due to path doubling."""
        import shutil
        rel_dir = Path("tmp_test_rel_runner_corpus")
        try:
            p_rel = rel_dir / "2409.02222"
            v1 = p_rel / "v1"
            v1.mkdir(parents=True, exist_ok=True)
            (v1 / "main.tex").write_text(
                r"\documentclass{article}\begin{document}We cite \cite{k1}.\bibliography{refs}\end{document}",
                encoding="utf-8",
            )
            (v1 / "refs.bib").write_text(
                r"@article{k1, title={Relative Paper}, author={Author One}, year={2021}}",
                encoding="utf-8",
            )
            res = process_single_publication("2409.02222", p_rel)
            self.assertEqual(len(res["bibtex"]), 1)
            self.assertEqual(res["bibtex"][0]["citation_key"], "k1")
        finally:
            shutil.rmtree(rel_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
