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

    def test_bibitem_without_newblock_parses_initial_authors_and_title(self):
        raw = (
            "M. Erhard, M. Krenn, and A. Zeilinger, "
            "Advances in high-dimensional quantum entanglement, "
            "Nat. Rev. Phys. 2, 365 (2020)."
        )
        entry = parse_bibitem("2503.00001", "1", raw)
        self.assertEqual(entry["authors"], ["M. Erhard", "M. Krenn", "A. Zeilinger"])
        self.assertEqual(entry["title"], "Advances in high-dimensional quantum entanglement")

    def test_bibitem_without_newblock_preserves_surname_initials(self):
        raw = "Wang, W., Zhang, J., and Li, Q., Learning with Sparse Signals, Proc. 2, 10 (2025)."
        entry = parse_bibitem("2503.00002", "2", raw)
        self.assertEqual(entry["authors"], ["Wang, W.", "Zhang, J.", "Li, Q."])
        self.assertEqual(entry["title"], "Learning with Sparse Signals")

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

    def test_duplicate_key_with_different_references_is_not_remapped(self):
        entries = [
            (1, {"citation_key": "same", "title": "First Distinct Paper", "doi": "10.1234/one", "authors": []}),
            (1, {"citation_key": "same", "title": "Second Distinct Paper", "doi": "10.1234/two", "authors": []}),
        ]
        conflicts = []
        unique, remap = unify_entries(entries, conflicts=conflicts)
        self.assertEqual(len(unique), 2)
        self.assertNotIn("same", remap[1])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["reason"], "duplicate_key_different_reference")

    def test_service_reports_duplicate_key_conflict_and_omits_ambiguous_remap(self):
        tex = r"""\begin{thebibliography}{9}
\bibitem{same} Alice Smith. First Distinct Paper. Journal A, 2020. DOI: 10.1234/one.
\bibitem{same} Bob Jones. Second Distinct Paper. Journal B, 2021. DOI: 10.1234/two.
\end{thebibliography}"""
        result = extract_and_unify_references([{
            "arxiv_id": "2409.00003", "version": 1, "merged_tex": tex,
            "bib_files": [], "bbl_files": [],
        }])
        self.assertEqual(len(result["unified_bibtex"]), 2)
        self.assertNotIn("same", result["citation_remap"][1])
        self.assertEqual(len(result["citation_key_conflicts"]), 1)

    def test_duplicate_key_with_same_reference_is_not_a_conflict(self):
        entries = [
            (1, {"citation_key": "same", "title": "A Distinctive Paper Title", "doi": "10.1234/one", "authors": []}),
            (1, {"citation_key": "same", "title": "A Distinctive Paper Title", "doi": "10.1234/one", "authors": []}),
        ]
        conflicts = []
        unique, remap = unify_entries(entries, conflicts=conflicts)
        self.assertEqual(len(unique), 1)
        self.assertEqual(remap[1]["same"], "same")
        self.assertEqual(conflicts, [])

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
                "root_file": "main.tex", "compilation_path": ["main.tex"],
                "bib_files": [], "bbl_files": ["refs.bbl"], "status": "ok",
            }], source_dirs={1: source_dir})
            self.assertEqual(len(result["unified_bibtex"]), 1)


    def test_reads_only_cited_entries_from_bib_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / "refs.bib").write_text(r"""
@article{cited,
  author = {Alice Smith and Bob Jones},
  title = {A Useful Paper},
  journal = {Journal of Tests},
  year = {2024},
  doi = {10.1234/abc}
}
@book{unused,
  author = {Nobody},
  title = {An Unused Book},
  year = {2020}
}
""", encoding="utf-8")
            source = {
                "arxiv_id": "2409.00001", "version": 1,
                "root_file": "main.tex", "compilation_path": ["main.tex"],
                "merged_tex": r"Text cites \cite{cited}.",
                "bib_files": ["refs.bib"], "bbl_files": [], "status": "ok",
            }
            result = extract_and_unify_references([source], source_dirs={1: source_dir})
            self.assertEqual(len(result["unified_bibtex"]), 1)
            entry = result["unified_bibtex"][0]
            self.assertEqual(entry["citation_key"], "cited")
            self.assertEqual(entry["entry_type"], "article")
            self.assertEqual(entry["authors"], ["Alice Smith", "Bob Jones"])
            self.assertEqual(entry["year"], 2024)
            self.assertEqual(entry["doi"], "10.1234/abc")
            self.assertEqual(entry["journal"], "Journal of Tests")

    def test_bibtex_string_macro_and_nocite_star(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp)
            (source_dir / "refs.bib").write_text(r"""
@String{jtest = {Journal of Tests}}
@inproceedings{paper,
 author = "Jane Doe and John Roe",
 title = {A Conference Paper},
 booktitle = jtest # { Annual Meeting},
 year = 2025
}
@misc{extra, title = {Uncited but included}, author = {Someone}}
""", encoding="utf-8")
            source = {
                "arxiv_id": "2409.00001", "version": 1,
                "root_file": "main.tex", "compilation_path": ["main.tex"],
                "merged_tex": r"\nocite{*}",
                "bib_files": ["refs.bib"], "bbl_files": [], "status": "ok",
            }
            result = extract_and_unify_references([source], source_dirs={1: source_dir})
            by_key = {e["citation_key"]: e for e in result["unified_bibtex"]}
            self.assertEqual(set(by_key), {"paper", "extra"})
            self.assertEqual(by_key["paper"]["entry_type"], "inproceedings")
            self.assertEqual(by_key["paper"]["journal"], "Journal of Tests Annual Meeting")

    def test_bibitem_in_tex_works_without_bbl(self):
        tex = r"""\begin{thebibliography}{9}
\bibitem{x} Alice Smith\newblock ``A Useful Paper''\newblock Journal of Tests, 2024.
\end{thebibliography}"""
        result = extract_and_unify_references([{
            "arxiv_id": "2409.00001", "version": 1,
            "root_file": "main.tex", "compilation_path": ["main.tex"],
            "merged_tex": tex, "bib_files": [], "bbl_files": [], "status": "ok",
        }])
        self.assertEqual(result["unified_bibtex"][0]["citation_key"], "x")

    def test_bib_files_without_source_dir_are_not_resolved_from_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "refs.bib").write_text("@misc{x,title={X}}", encoding="utf-8")
            source = {
                "arxiv_id": "2409.00001", "version": 1,
                "root_file": "main.tex", "compilation_path": ["main.tex"],
                "merged_tex": r"\nocite{*}", "bib_files": ["refs.bib"],
                "bbl_files": [], "status": "ok",
            }
            result = extract_and_unify_references([source])
            self.assertEqual(result["unified_bibtex"], [])

    def test_empty_input_returns_empty_contract(self):
        self.assertEqual(extract_and_unify_references([]), {
            "arxiv_id": "", "unified_bibtex": [], "citation_remap": {},
            "citation_key_conflicts": []
        })

    def test_rewrite_preserves_key_spacing_and_starred_commands(self):
        text = r"\citep*[see][p. 4]{oldA, oldB} and \textcite{oldA ,oldB}"
        expected = r"\citep*[see][p. 4]{newA, newB} and \textcite{newA ,newB}"
        self.assertEqual(
            rewrite_citation_keys(text, {"oldA": "newA", "oldB": "newB"}),
            expected,
        )

    def test_version_without_bibliography_is_kept_in_remap(self):
        result = extract_and_unify_references([
            {"arxiv_id": "2409.00001", "version": 1, "merged_tex": "", "bbl_files": []},
            {"arxiv_id": "2409.00001", "version": 2, "merged_tex": "", "bbl_files": [], "status": "empty_source"},
        ])
        self.assertEqual(result["citation_remap"], {1: {}, 2: {}})


if __name__ == "__main__":
    unittest.main()
