import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.bibtex import extract_and_unify_references, rewrite_citation_keys
from arxiv_pipeline.bibtex.parser import extract_bibitems, parse_bibitem
from arxiv_pipeline.bibtex.normalizer import is_reliable_title
from arxiv_pipeline.bibtex.deduplicator import unify_entries


class TestModule4(unittest.TestCase):

    def test_bibtex_percent_inside_url_and_preamble_does_not_truncate_file(self):
        from arxiv_pipeline.bibtex.parser import _parse_bibtex_text
        bib = r"""@Preamble{\"prefix%inside\"}
@article{first, title={First Paper}, url={https://example.org/a%26b}, year={2020}}
% a real comment outside entries
@article{second, title={Second Paper}, year={2021}}
"""
        entries, _ = _parse_bibtex_text(bib)
        self.assertEqual([entry[1] for entry in entries], ["first", "second"])
        self.assertIn("a%26b", entries[0][2]["url"])

    def test_bibitem_year_ignores_numbers_inside_doi(self):
        raw = r"""Nature 2020. https://doi.org/10.1038/s41586-020-2058-6"""
        entry = parse_bibitem("2501.12643", "k", raw)
        self.assertEqual(entry["year"], 2020)

    def test_bibitem_keeps_hyphenated_initials_as_separate_authors(self):
        raw = "H.-S. Sim, J. Lee, and M. Kim, Kondo effect in a quantum dot, Nature 586, 2020."
        entry = parse_bibitem("2501.12643", "Kondo_effect_quantum_dot3", raw)
        self.assertGreaterEqual(len(entry["authors"]), 3)
        self.assertEqual(entry["authors"][0], "H.-S. Sim")

    def test_clean_latex_preserves_tex_accent_and_special_letter_commands(self):
        from arxiv_pipeline.bibtex.parser import _clean_latex
        self.assertEqual(_clean_latex(r"\v{c} \c{c} \o \ss \aa"), "č ç ø ß å")

    def test_bib_value_does_not_rewrite_tilde_in_url(self):
        from arxiv_pipeline.bibtex.parser import _bib_value
        self.assertEqual(_bib_value(r"{https://example.org/~user/file}", {}, clean=False), "https://example.org/~user/file")

    def test_same_citation_key_across_versions_falls_back_when_title_is_unreliable(self):
        entries = [
            (1, {"citation_key": "armstrong1995canudas", "title": "J", "doi": "", "authors": []}),
            (2, {"citation_key": "armstrong1995canudas", "title": "J.", "doi": "", "authors": []}),
        ]
        unique, remap = unify_entries(entries)
        self.assertEqual(len(unique), 1)
        self.assertEqual(remap[2]["armstrong1995canudas"], "armstrong1995canudas")

    def test_cross_version_same_key_with_conflicting_dois_gets_distinct_remaps(self):
        entries = [
            (1, {"citation_key": "shared", "title": "First Distinct Paper", "doi": "10.1234/one", "authors": []}),
            (2, {"citation_key": "shared", "title": "Second Distinct Paper", "doi": "10.1234/two", "authors": []}),
        ]
        unique, remap = unify_entries(entries)
        self.assertEqual([entry["citation_key"] for entry in unique], ["shared", "shared__2"])
        self.assertEqual(remap[1]["shared"], "shared")
        self.assertEqual(remap[2]["shared"], "shared__2")

    def test_conflicting_same_key_is_disambiguated_in_unified_output(self):
        entries = [
            (1, {"citation_key": "same", "title": "First Distinct Paper", "doi": "10.1234/one", "authors": []}),
            (1, {"citation_key": "same", "title": "Second Distinct Paper", "doi": "10.1234/two", "authors": []}),
        ]
        unique, _ = unify_entries(entries)
        keys = [entry["citation_key"] for entry in unique]
        self.assertEqual(keys, ["same", "same__2"])

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


    def test_custom_cite_wrappers_and_macro_params_are_handled(self):
        from arxiv_pipeline.bibtex.parser import extract_cited_keys
        tex = r"\newcommand{\foo}[2]{\citeauthor{#2}} \citepbjps{key1} \citetbjps{key2} \citealtbjps{key3}"
        keys, include_all = extract_cited_keys(tex)
        self.assertFalse(include_all)
        self.assertEqual(keys, {"key1", "key2", "key3"})
        self.assertEqual(
            rewrite_citation_keys(r"\citepbjps{key1} \citetbjps{key2}", {"key1": "k1"}),
            r"\citepbjps{k1} \citetbjps{key2}",
        )

    def test_uppercase_and_author_year_citation_commands_are_supported(self):
        text = r"\Citet{a} \Citep{b} \Cite{c} \citeauthor{d} \citeyear{e}"
        expected = r"\Citet{A} \Citep{B} \Cite{C} \citeauthor{D} \citeyear{E}"
        self.assertEqual(rewrite_citation_keys(text, {"a": "A", "b": "B", "c": "C", "d": "D", "e": "E"}), expected)

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



    def test_bibitem_keeps_authors_without_oxford_comma_and_with_tilde(self):
        raw = r"H.-S. Sim, J.~Lee and M. Kim, Kondo effect in a quantum dot, Nature 586, 2020."
        entry = parse_bibitem("2501.12643", "Kondo_effect_quantum_dot3", raw)
        self.assertEqual(entry["authors"], ["H.-S. Sim", "J. Lee", "M. Kim"])
        self.assertEqual(entry["title"], "Kondo effect in a quantum dot")
        self.assertEqual(entry["year"], 2020)

    def test_later_version_not_remapped_to_ambiguous_key_after_disambiguation(self):
        entries = [
            (1, {"citation_key": "same", "title": "First Distinct Paper", "doi": "10.1234/one", "authors": []}),
            (1, {"citation_key": "same", "title": "Second Distinct Paper", "doi": "10.1234/two", "authors": []}),
            (2, {"citation_key": "alias", "title": "First Distinct Paper", "doi": "10.1234/one", "authors": []}),
        ]
        _, remap = unify_entries(entries)
        self.assertNotIn("alias", remap.get(2, {}))


_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_BASE_DIR = _REPO_ROOT / "data" / "m1-colab-man-nhi-01"
_REAL_DATA_DIR = _REAL_BASE_DIR / "corpus" if (_REAL_BASE_DIR / "corpus").is_dir() else _REAL_BASE_DIR


@unittest.skipUnless(_REAL_DATA_DIR.is_dir(), "Real corpus data/m1-colab-man-nhi-01 not present")
class TestModule4RealCorpus(unittest.TestCase):
    def _gather_paper(self, paper_folder: str):
        import json
        from arxiv_pipeline.gathering.service import gather_source
        from arxiv_pipeline.bibtex.service import extract_and_unify_references
        from arxiv_pipeline.parsing.runner import discover_paper_dirs

        paper_dir = next((d for d in discover_paper_dirs(_REAL_DATA_DIR) if d.name == paper_folder), None)
        if paper_dir is None or not paper_dir.is_dir():
            self.skipTest(f"Missing paper folder {paper_folder} under {_REAL_DATA_DIR}")
        arxiv_id = paper_folder.replace("-", ".")
        meta_file = paper_dir / "metadata.json"
        if meta_file.is_file():
            try:
                arxiv_id = json.loads(meta_file.read_text(encoding="utf-8")).get("arxiv_id") or arxiv_id
            except Exception:
                pass
        sources_root = paper_dir / "sources" if (paper_dir / "sources").is_dir() else paper_dir
        v_dirs = sorted(
            [d for d in sources_root.iterdir() if d.is_dir() and d.name.startswith("v") and d.name[1:].isdigit()],
            key=lambda d: int(d.name[1:]),
        )
        if not v_dirs:
            v_dirs = [sources_root]
        gathered = []
        source_dirs = {}
        for vd in v_dirs:
            v_num = int(vd.name[1:]) if vd.name.startswith("v") and vd.name[1:].isdigit() else 1
            source_dirs[v_num] = vd
            gathered.append(gather_source(vd, arxiv_id, v_num))
        return extract_and_unify_references(gathered, source_dirs=source_dirs)

    def test_real_papers_bib_not_truncated_and_no_duplicate_keys(self):
        expected_min_counts = {
            "2501-12643": 1,
            "2501-12654": 1,
            "2501-12673": 1,
            "2501-12681": 1,
            "2501-12705": 27,
            "2501-12754": 1,
            "2501-12765": 1,
            "2501-12767": 6,
            "2501-12870": 1,
            "2501-12887": 2,
        }
        for folder, min_count in expected_min_counts.items():
            refs = self._gather_paper(folder)
            unified = refs["unified_bibtex"]
            self.assertGreaterEqual(len(unified), min_count, f"{folder} produced too few unified_bibtex entries")
            keys = [e["citation_key"] for e in unified]
            self.assertEqual(len(keys), len(set(keys)), f"{folder} has duplicate citation_keys")

    def test_real_2501_12643_kondo_effect_quantum_dot3(self):
        refs = self._gather_paper("2501-12643")
        matches = [e for e in refs["unified_bibtex"] if e["citation_key"] == "Kondo_effect_quantum_dot3"]
        self.assertEqual(len(matches), 1)
        entry = matches[0]
        self.assertEqual(entry["year"], 2020)
        self.assertIn("H.-S. Sim", entry["authors"])
        self.assertGreaterEqual(len(entry["authors"]), 8)
        self.assertEqual(entry["title"], "Observation of the Kondo Screening Cloud of Micron Lengths")
        self.assertEqual(entry["url"], "https://www.nature.com/articles/s41586-020-2058-6")


if __name__ == "__main__":
    unittest.main()
