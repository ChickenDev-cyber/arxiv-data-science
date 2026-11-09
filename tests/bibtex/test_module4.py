from arxiv_pipeline.bibtex import extract_and_unify_references, rewrite_citation_keys
from arxiv_pipeline.bibtex.parser import extract_bibitems


def test_extract_bibitems_handles_multiple_entries():
    tex = r'''\begin{thebibliography}{9}
\bibitem{a} Alice Smith. A Useful Paper. Journal of Tests, 2020. DOI: 10.1234/abc.
\bibitem[b-label]{b} Bob Jones. Another Paper. Proceedings of Tests, 2021.
\end{thebibliography}'''
    items = extract_bibitems(tex)
    assert [key for key, _ in items] == ["a", "b"]
    assert "A Useful Paper" in items[0][1]


def test_unifies_versions_by_doi_and_unions_fields():
    v1 = r'''\begin{thebibliography}{9}
\bibitem{oldA} Alice Smith. A Useful Paper. Journal of Tests, 2020.
\end{thebibliography}'''
    v2 = r'''\begin{thebibliography}{9}
\bibitem{newB} Alice Smith. A Useful Paper. Journal of Tests, 2021. DOI: 10.1234/abc.
\end{thebibliography}'''
    result = extract_and_unify_references([
        {"arxiv_id": "2409.00001", "version": 1, "merged_tex": v1, "bbl_files": []},
        {"arxiv_id": "2409.00001", "version": 2, "merged_tex": v2, "bbl_files": []},
    ])
    assert len(result["unified_bibtex"]) == 1
    entry = result["unified_bibtex"][0]
    assert entry["citation_key"] == "oldA"
    assert entry["year"] == 2020
    assert entry["doi"] == "10.1234/abc"
    assert result["citation_remap"] == {1: {"oldA": "oldA"}, 2: {"newB": "oldA"}}


def test_rewrite_citation_keys_multiple_keys_and_optional_args():
    text = r"See \cite{oldA,newB} and \cite[p. 4]{newB,unknown}."
    assert rewrite_citation_keys(text, {"oldA": "oldA", "newB": "oldA"}) == (
        r"See \cite{oldA,oldA} and \cite[p. 4]{oldA,unknown}."
    )


def test_empty_input_returns_empty_contract():
    assert extract_and_unify_references([]) == {
        "arxiv_id": "", "unified_bibtex": [], "citation_remap": {}
    }


def test_version_without_bibliography_is_kept_in_remap():
    result = extract_and_unify_references([
        {"arxiv_id": "2409.00001", "version": 1, "merged_tex": "", "bbl_files": []},
        {"arxiv_id": "2409.00001", "version": 2, "merged_tex": "", "bbl_files": [], "status": "empty_source"},
    ])
    assert result["citation_remap"] == {1: {}, 2: {}}
