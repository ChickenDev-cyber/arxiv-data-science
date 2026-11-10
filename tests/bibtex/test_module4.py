import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.bibtex import extract_and_unify_references, rewrite_citation_keys
from arxiv_pipeline.bibtex.parser import extract_bibitems, parse_bibitem
from arxiv_pipeline.bibtex.normalizer import is_reliable_title
from arxiv_pipeline.bibtex.deduplicator import unify_entries


class TestModule4(unittest.TestCase):
    def test_extract_bibitems_handles_multiple_entries(self):
        tex = r"""\begin{thebibliography}{9}
\bibitem{a} Alice Smith. A Useful Paper. Journal of Tests, 2020. DOI: 10.1234/abc.
\bibitem[b-label]{b} Bob Jones. Another Paper. Proceedings of Tests, 2021.
\end{thebibliography}"""
        items = extract_bibitems(tex)
        self.assertEqual([key for key, _ in items], ["a", "b"])
        self.assertIn("A Useful Paper", items[0][1])

    def test_newblock_keeps_authors_and_title_separate(self):
        raw = r"""Stephen Lin and Michael S Brown\newblock ``A Robust Method for Testing Systems''\newblock Journal of Testing, 2025."""
        entry = parse_bibitem("2503.06966", "lin25", raw)
        self.assertEqual(entry["authors"], ["Stephen Lin", "Michael S Brown"])
        self.assertEqual(entry["title"], "A Robust Method for Testing Systems")

    def test_initials_do_not_become_title_or_author(self):
        raw = r"""Wang, W., Zhang, J., and Li, Q.\newblock ``Learning with Sparse Signals''\newblock Proceedings of Science, 2025."""
        entry = parse_bibitem("2503.06939", "wang25", raw)
        self.assertEqual(entry["title"], "Learning with Sparse Signals")
        self.assertIn("Wang, W.", entry["authors"])
        self.assertIn("Zhang, J.", entry["authors"])

    def test_href_keeps_visible_text_not_url(self):
        raw = r"""Lou, X.\newblock ``A Study of Data''\newblock \href{https://example.com/Lou00}{Journal of Data}, 2025."""
        entry = parse_bibitem("2503.06939", "Lou00", raw)
        self.assertEqual(entry["title"], "A Study of Data")
        self.assertNotIn("https://example.com/Lou00", entry["journal"])

    def test_short_titles_are_not_reliable_identity(self):
        self.assertFalse(is_reliable_title("J"))
        self.assertFalse(is_reliable_title("P"))
        self.assertFalse(is_reliable_title("Short"))
        self.assertTrue(is_reliable_title("A Robust Method"))

    def test_short_same_titles_do_not_merge_different_references(self):
        entries = [
            (1, {"citation_key": "a", "title": "J", "doi": "", "authors": []}),
            (1, {"citation_key": "b", "title": "J", "doi": "", "authors": []}),
        ]
        unique, remap = unify_entries(entries)
        self.assertEqual(len(unique), 2)
        self.assertEqual(remap[1], {"a": "a", "b": "b"})

    def test_doi_still_deduplicates_when_title_is_short(self):
        entries = [
            (1, {"citation_key": "a", "title": "J", "doi": "10.1234/ABC", "authors": []}),
            (2, {"citation_key": "b", "title": "J", "doi": "https://doi.org/10.1234/abc", "authors": []}),
        ]
        unique, remap = unify_entries(entries)
        self.assertEqual(len(unique), 1)
        self.assertEqual(remap[2]["b"], "a")

    def test_rewrite_supported_citation_commands_and_optional_args(self):
        text = (
            r"\cite{a} \citep[see][p. 4]{b,c} \citet{b} "
            r"\citealt{c} \citealp{a} \autocite{b} "
            r"\parencite[see]{c} \textcite{a}"
        )
        expected = (
            r"\cite{A} \citep[see][p. 4]{B,C} \citet{B} "
            r"\citealt{C} \citealp{A} \autocite{B} "
            r"\parencite[see]{C} \textcite{A}"
        )
        self.assertEqual(rewrite_citation_keys(text, {"a": "A", "b": "B", "c": "C"}), expected)

    def test_unifies_versions_by_doi_and_unions_fields(self):
        v1 = r"""\begin{thebibliography}{9}
\bibitem{oldA} Alice Smith\newblock ``A Useful Paper''\newblock Journal of Tests, 2020.
\end{thebibliography}"""
        v2 = r"""\begin{thebibliography}{9}
\bibitem{newB} Alice Smith\newblock ``A Useful Paper''\newblock Journal of Tests, 2021. DOI: 10.1234/abc.
\end{thebibliography}"""
        result = extract_and_unify_references([
            {"arxiv_id": "2409.00001", "version": 1, "merged_tex": v1, "bbl_files": []},
            {"arxiv_id": "2409.00001", "version": 2, "merged_tex": v2, "bbl_files": []},
        ])
        self.assertEqual(len(result["unified_bibtex"]), 1)
        entry = result["unified_bibtex"][0]
        self.assertEqual(entry["citation_key"], "oldA")
        self.assertEqual(entry["doi"], "10.1234/abc")
        self.assertEqual(result["citation_remap"], {1: {"oldA": "oldA"}, 2: {"newB": "oldA"}})

    def test_bbl_relative_path_resolves_from_source_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / "refs.bbl").write_text(
                r"""\begin{thebibliography}{9}\bibitem{x} A Author\newblock ``A Useful Paper''\end{thebibliography}""",
                encoding="utf-8",
            )
            result = extract_and_unify_references([{
                "arxiv_id": "2409.00001", "version": 1, "merged_tex": "",
                "source_dir": str(source_dir), "bbl_files": ["refs.bbl"],
            }])
            self.assertEqual(len(result["unified_bibtex"]), 1)

    def test_empty_input_returns_empty_contract(self):
        self.assertEqual(extract_and_unify_references([]), {
            "arxiv_id": "", "unified_bibtex": [], "citation_remap": {}
        })

    def test_version_without_bibliography_is_kept_in_remap(self):
        result = extract_and_unify_references([
            {"arxiv_id": "2409.00001", "version": 1, "merged_tex": "", "bbl_files": []},
            {"arxiv_id": "2409.00001", "version": 2, "merged_tex": "", "bbl_files": [], "status": "empty_source"},
        ])
        self.assertEqual(result["citation_remap"], {1: {}, 2: {}})


if __name__ == "__main__":
    unittest.main()
