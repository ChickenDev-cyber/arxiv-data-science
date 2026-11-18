"""Regressions reduced from the 1,000-paper pilot's bibliography failures."""
import json
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.bibtex.parser import extract_from_gathered_source
from arxiv_pipeline.gathering import SourceGatherer
from arxiv_pipeline.gathering.latex_scan import find_bib_names, mask_comments
from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.parsing.runner import default_gather_source, process_single_publication


class BibliographyVariantsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def source(self, tex, **extra):
        return {'arxiv_id': '2501.00001', 'version': 1, 'merged_tex': tex,
                'status': 'ok', 'bib_files': [], 'bbl_files': [], **extra}

    def test_putbib_discovers_only_files_requested_on_compilation_path(self):
        # Ignoring putbib loses a real source bibliography (2506.22989).
        self.write('main.tex', r'\documentclass{article}\begin{document}\input{sections/body}\end{document}')
        self.write('sections/body.tex', r'\cite{a}\putbib[refs, extra.bib] % \putbib[commented]')
        self.write('unused.tex', r'\putbib[unused]')
        for name in ['refs', 'extra', 'commented', 'unused']:
            self.write(name + '.bib', '@article{a,title={A real reference}}')
        gathered = SourceGatherer().gather(self.root, '2501.00001', 1)
        self.assertEqual(gathered['bib_files'], ['refs.bib', 'extra.bib'])
        self.assertEqual(gathered['compilation_path'], ['main.tex', 'sections/body.tex'])

    def test_bare_putbib_uses_current_default_without_loading_unused_defaults(self):
        text = r'''\defaultbibliography{unused}
\defaultbibliography{first, shared}\putbib
\putbibliography[not_a_command]\putbib[explicit]
\defaultbibliography{second}\putbib
% \putbib[commented]
'''
        self.assertEqual(find_bib_names(mask_comments(text)), ['first', 'shared', 'explicit', 'second'])

    def test_putbib_reports_missing_named_file(self):
        self.write('main.tex', r'\documentclass{article}\begin{document}Body.\putbib[absent]\end{document}')
        report = SourceGatherer().inspect(self.root, '2501.00001', 1)
        self.assertEqual(report.missing_bib, ['absent'])

    def test_fragment_fallback_resolves_putbib_too(self):
        self.write('fragment.tex', r'Body \cite{k}.\putbib[refs]')
        self.write('refs.bib', '@article{k,title={An actual reference}}')
        source = default_gather_source(self.root, '2501.00001', 1)
        self.assertEqual(source['bib_files'], ['refs.bib'])
        self.assertEqual([e['citation_key'] for e in extract_from_gathered_source(source, self.root)], ['k'])

    def test_amsrefs_preserves_repeated_authors_date_and_structured_title(self):
        tex = r'''\begin{bibdiv}\begin{biblist}
\bib{CW92}{article}{
author={Cazenave, Thierry}, author={Weissler, Fred~B.},
title={Rapidly decaying solutions of the nonlinear {S}chr\"{o}dinger equation},
date={1992-01-01}, journal={Comm. Math. Phys.},
url={https://doi.org/10.1007/BF02099531},}
\end{biblist}\end{bibdiv}'''
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['citation_key'], 'CW92')
        self.assertEqual(entries[0]['authors'], ['Cazenave, Thierry', 'Weissler, Fred B'])
        self.assertEqual(entries[0]['title'], 'Rapidly decaying solutions of the nonlinear Schrödinger equation')
        self.assertEqual(entries[0]['year'], 1992)
        self.assertEqual(entries[0]['journal'], 'Comm. Math. Phys.')
        self.assertEqual(entries[0]['doi'], '10.1007/BF02099531')

    def test_amsrefs_accepts_adjacent_braced_fields_without_commas(self):
        # This form occurs in Witt_vectors.tex, including repeated authors.
        tex = r'''\begin{biblist}\bib{AN}{article}{
title={Cartier modules} author={Benjamin Antieau}
author={Thomas Nikolaus} year={2021}
eprint={https://arxiv.org/abs/2104.03870} label={AN21}}
\end{biblist}'''
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['authors'], ['Benjamin Antieau', 'Thomas Nikolaus'])
        self.assertEqual(entries[0]['year'], 2021)
        self.assertEqual(entries[0]['url'], 'https://arxiv.org/abs/2104.03870')
        self.assertEqual(entries[0]['citation_key'], 'AN')  # label is not a citation key.

    def test_amsrefs_does_not_guess_metadata_from_key_or_arxiv_id(self):
        tex = r'\begin{biblist}\bib{Jones1906}{article}{author={Research and Development Group}, eprint={https://arxiv.org/abs/1906.11238}}\end{biblist}'
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['title'], '')
        self.assertEqual(entries[0]['year'], None)
        self.assertEqual(entries[0]['authors'], ['Research and Development Group'])

    def test_amsrefs_preserves_explicit_historical_date(self):
        tex = r'\begin{biblist}\bib{old}{book}{title={A historical work},date={1867-01-01}}\end{biblist}'
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual(entries[0]['year'], 1867)

    def test_amsrefs_group_scanner_does_not_swallow_entry_after_escaped_brace(self):
        tex = r'''\begin{biblist}
\bib{first}{article}{title={A set $\left\{x$ and {nested} text},year={2020}}
\bib{second}{book}{title={Another reference},year={2021}}
\end{biblist}'''
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual([e['citation_key'] for e in entries], ['first', 'second'])
        self.assertEqual(entries[1]['year'], 2021)

    def test_amsrefs_in_bbl_is_counted_before_duplicate_key_collapse(self):
        tex = r'\begin{biblist}\bib{same}{article}{title={A real reference},date={2020}}\end{biblist}'
        self.write('paper.bbl', tex)
        metrics = {}
        entries = extract_from_gathered_source(self.source(tex, bbl_files=['paper.bbl']), self.root, metrics=metrics)
        self.assertEqual(len(entries), 1)
        self.assertEqual(metrics['raw_entry_count'], 2)

    def test_amsrefs_duplicate_key_unions_fields_before_collapsing(self):
        tex = r'''\begin{biblist}
\bib{same}{article}{title={A real reference},author={Alice}}
\bib{same}{article}{title={A real reference},author={Bob},date={2020},doi={10.1234/real}}
\end{biblist}'''
        metrics = {}
        entries = extract_from_gathered_source(self.source(tex), metrics=metrics)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['authors'], ['Alice', 'Bob'])
        self.assertEqual(entries[0]['year'], 2020)
        self.assertEqual(entries[0]['doi'], '10.1234/real')
        self.assertEqual(metrics['raw_entry_count'], 2)

    def test_malformed_amsrefs_entry_does_not_hide_next_entry_or_appendix(self):
        tex = r'''\documentclass{article}\begin{document}Body.
\begin{biblist}
\bib{broken}{article}{title={Incomplete entry}
\bib{good}{article}{title={A complete reference},date={2021}}
\end{biblist}
\appendix\section{Proof}A valid appendix sentence.\end{document}'''
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual([entry['citation_key'] for entry in entries], ['good'])
        tree = build_hierarchy(self.source(tex))
        self.assertEqual([n['content'] for n in tree['nodes'] if n['kind'] == 'sentence'], ['Body.', 'A valid appendix sentence.'])

    def test_amsrefs_ignores_commented_and_outside_biblist_commands(self):
        tex = r'''\bib{outside}{article}{title={A code example}}
% \begin{biblist}\bib{commented}{book}{title={Not compiled}}\end{biblist}
\begin{biblist}
% \bib{hidden}{article}{title={A hidden entry}}
\bib{real}{book}{title={Actual entry},year={2022}}
\end{biblist}'''
        self.assertEqual([e['citation_key'] for e in extract_from_gathered_source(self.source(tex))], ['real'])

    def test_amsrefs_bibliography_cannot_create_body_sections_or_math(self):
        tex = r'''\documentclass{article}\begin{document}
\section{Body}Actual prose.
\begin {bibdiv}\begin {biblist}
\bib{k}{article}{title={\section{False section} $\alpha$ and \begin{equation}x=1\end{equation}}}
\end {biblist}\end {bibdiv}
\section*{Acknowledgements}Thanks to colleagues.
\appendix\section{Proof}A valid appendix sentence.
\end{document}'''
        tree = build_hierarchy(self.source(tex))
        self.assertEqual([n['content'] for n in tree['nodes'] if n['kind'] == 'sentence'],
                         ['Actual prose.', 'Thanks to colleagues.', 'A valid appendix sentence.'])
        self.assertEqual([n['content'] for n in tree['nodes'] if n['kind'] == 'section'], ['Body', 'Acknowledgements', 'Proof'])
        self.assertFalse(any(n['kind'] == 'formula' for n in tree['nodes']))

    def test_putbib_and_bibunit_markup_leave_body_and_appendix(self):
        tex = r'''\documentclass{article}\begin{document}
\defaultbibliography{refs}\begin{bibunit}[apecon]
Main text.\putbib[refs]\end{bibunit}
\appendix\section{Proof}Appendix text.\end{document}'''
        tree = build_hierarchy(self.source(tex))
        self.assertEqual([n['content'] for n in tree['nodes'] if n['kind'] == 'sentence'], ['Main text.', 'Appendix text.'])

    def test_cross_version_amsrefs_and_bibtex_union_fields_and_rewrite_cites(self):
        self.write('metadata.json', json.dumps({'arxiv_id': '2501.00001', 'versions': [{'version': 1}, {'version': 2}]}))
        self.write('v1/main.tex', r'''\documentclass{article}\begin{document}
Shared result \cite{old}.\begin{biblist}\bib{old}{article}{title={A shared reference title},author={Alice},date={2020}}\end{biblist}\end{document}''')
        self.write('v2/main.tex', r'\documentclass{article}\begin{document}Shared result \cite{new}.\putbib[refs]\end{document}')
        self.write('v2/refs.bib', '@article{new,title={A shared reference title},author={Bob},doi={10.1234/example}}')
        result = process_single_publication('2501.00001', self.root)
        self.assertEqual(len(result['bibtex']), 1)
        self.assertEqual(result['bibtex'][0]['citation_key'], 'old')
        self.assertEqual(result['bibtex'][0]['authors'], ['Alice', 'Bob'])
        self.assertEqual(result['bibtex'][0]['year'], 2020)
        self.assertEqual(result['bibtex'][0]['doi'], '10.1234/example')
        self.assertEqual(result['raw_bibtex_count'], 2)
        self.assertEqual(result['bib_source_kind'], 'both')
        self.assertEqual([e['content'] for e in result['elements'] if e['kind'] == 'sentence'], [r'Shared result \cite{old}.'])

    def test_direct_thebibliography_reads_entries_and_excludes_prose_until_document_end(self):
        self.write('main.tex', r'''\documentclass{article}\begin{document}
Body \cite{B}.\thebibliography{10}
\bibitem{B} A.~V.~Borovik.\newblock A real reference.\newblock Journal, 2020.
\end{document}Trailing unused material.''')
        result = process_single_publication('2501.00001', self.root)
        self.assertEqual([e['citation_key'] for e in result['bibtex']], ['B'])
        self.assertEqual(result['bib_source_kind'], 'bibitem')
        self.assertEqual([e['content'] for e in result['elements'] if e['kind'] == 'sentence'], [r'Body \cite{B}.'])

    def test_direct_thebibliography_stops_at_end_command_and_preserves_appendix(self):
        tex = r'''\documentclass{article}\begin{document}Body.
\thebibliography{9}\bibitem{k}Alice.\newblock A real reference.\newblock 2021.
\endthebibliography
\section*{Acknowledgements}Thanks.
\appendix\section{Proof}Proof prose.\end{document}'''
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual([e['citation_key'] for e in entries], ['k'])
        self.assertEqual(entries[0]['year'], 2021)
        tree = build_hierarchy(self.source(tex))
        self.assertEqual([n['content'] for n in tree['nodes'] if n['kind'] == 'sentence'], ['Body.', 'Thanks.', 'Proof prose.'])

    def test_direct_thebibliography_does_not_read_preamble_macro_as_a_reference(self):
        tex = r'''\documentclass{article}
\newcommand{\templatebib}{\thebibliography{9}\bibitem{fake}Alice.\newblock Fake reference.\endthebibliography}
\begin{document}Body.\thebibliography{9}\bibitem{real}Bob.\newblock Real reference.\endthebibliography\end{document}'''
        entries = extract_from_gathered_source(self.source(tex))
        self.assertEqual([e['citation_key'] for e in entries], ['real'])


if __name__ == '__main__':
    unittest.main()
