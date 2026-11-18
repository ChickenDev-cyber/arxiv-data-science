"""Small reproductions of failures observed in the 25-publication M2 pilot."""

import tempfile
import unittest
from pathlib import Path

from arxiv_pipeline.parsing.runner import process_single_publication
from arxiv_pipeline.standardization import clean_latex


class PilotRegressions(unittest.TestCase):
    def parse(self, body, title='Pilot'):
        with tempfile.TemporaryDirectory() as tmp:
            version = Path(tmp) / 'v1'
            version.mkdir()
            (version / 'main.tex').write_text(
                '\\documentclass{article}\n\\title{' + title + '}\n'
                '\\begin{document}\n' + body + '\n\\end{document}', encoding='utf-8')
            return process_single_publication('2503.06927', Path(tmp))

    def test_spaced_bibliography_is_extracted_but_excluded_from_hierarchy(self):
        for begin, end in [(r'\begin {thebibliography}', r'\end {thebibliography}'),
                           ('\\begin\n{thebibliography}', '\\end\n{thebibliography}'),
                           (r'\begin{thebibliography}', r'\end {thebibliography}')]:
            with self.subTest(begin=begin, end=end):
                result = self.parse(
                    'Body sentence. ' + begin + r'''{99}
\bibitem{friedland} B. Friedland, ``Control system design'', Dover, 2005.
''' + end + r'''
\section*{Acknowledgements}Thanks to our colleagues.
\appendix\section{Proofs}The result follows.
''')
                sentences = [e['content'] for e in result['elements'] if e['kind'] == 'sentence']
                self.assertEqual(sentences, ['Body sentence.', 'Thanks to our colleagues.', 'The result follows.'])
                self.assertEqual([e['citation_key'] for e in result['bibtex']], ['friedland'])

    def test_spaced_bibliography_commands_do_not_become_sentences(self):
        result = self.parse(r'''Before.
\bibliographystyle {plain}
\bibliography {refs}
\addbibresource [location=local] {extra.bib}
\section*{Acknowledgements}After.
''')
        self.assertEqual([e['content'] for e in result['elements'] if e['kind'] == 'sentence'],
                         ['Before.', 'After.'])

    def test_layout_definitions_are_not_scanned_as_document_structure(self):
        result = self.parse(r'''
\IEEEoverridecommandlockouts
\setlength{\abovedisplayskip}{3pt}\pagestyle{plain}\setcounter{page}{1}
\def\thepage{}
\renewcommand{\KwSty}[1]{\textbf{#1}\section{Not a section}}
\newcommand\ArgSty[1][default]{\emph{#1}\begin{itemize}\item Not an item\end{itemize}}
\flushbottom\IEEEpeerreviewmaketitle
\section{Results}Visible prose uses $\mathbf{x}$ and \cite{key}.
''')
        sections = [e['content'] for e in result['elements'] if e['kind'] == 'section']
        self.assertEqual(sections, ['Results'])
        self.assertEqual([e['content'] for e in result['elements'] if e['kind'] == 'sentence'],
                         [r'Visible prose uses $\mathbf{x}$ and \cite{key}.'])
        self.assertFalse(any(e['kind'] == 'itemize' for e in result['elements']))

    def test_frontmatter_wrappers_and_metadata_leave_abstract_and_body(self):
        result = self.parse(r'''
\begin{frontmatter}
\author{A. Author}, \author{B. Author} and \author{C. Author}
\address[A]{University of Somewhere}\email{a@example.org}
\affiliation[1]{organization={Some lab},country={Somewhere}}
\titlerunning{Short title}\authorrunning{A. Author}
\begin{abstract}An abstract sentence.\end{abstract}
\end{frontmatter}
\section{Main}Main sentence.
\section*{Acknowledgements}We thank the reviewers.
\appendix\section{Proof}A proof sentence.
''')
        self.assertEqual([e['content'] for e in result['elements'] if e['kind'] == 'sentence'],
                         ['An abstract sentence.', 'Main sentence.', 'We thank the reviewers.', 'A proof sentence.'])

    def test_title_layout_is_removed_without_losing_title_words(self):
        result = self.parse('Text.', r'\fontsize{21.9}{26.28}\selectfont HEPPO \LARGE Architecture\vspace{-30pt}')
        self.assertEqual(result['elements'][0]['content'], 'HEPPO Architecture')

    def test_layout_cleaner_respects_argument_count_and_protected_content(self):
        text = (r'\setlength{\parskip}{2pt}{Keep this text.} '
                r'\fontsize{10}{12}\selectfont Text $\setlength{\x}{2pt}$ '
                r'\cite{setlength.key}\ref{layout.1}\custom{\setlength{\x}{2pt} opaque.}')
        self.assertEqual(clean_latex(text),
                         r'{Keep this text.} Text $\setlength{\x}{2pt}$ '
                         r'\cite{setlength.key}\ref{layout.1}\custom{\setlength{\x}{2pt} opaque.}')

    def test_algorithm_style_declarations_and_tex_indent_are_not_body_prose(self):
        result = self.parse(r'''
\bstctlcite{IEEEetal:BSTcontrol}\bstctlcite{IEEEdash:BSTcontrol}
\SetKwComment{Comment}{\color{green}// }{}
\SetKwProg{Function}{function}{}{}\SetKwInOut{KwIn}{Input}
\parindent=1.25 em
Actual text with \cite{real_key}.
''')
        self.assertEqual([e['content'] for e in result['elements'] if e['kind'] == 'sentence'],
                         [r'Actual text with \cite{real_key}.'])

    def test_nested_identical_heading_names_do_not_create_self_loop_hierarchy_edge(self):
        result = self.parse(r'''
\chapter{Elliptic Pre-Complexes}
Some text in chapter.
\section{Elliptic Pre-Complexes}
Some text in section.
''')
        self_loops = [e for e in result['hierarchy'] if e['element_id'] == e['parent_id']]
        self.assertEqual(self_loops, [])


if __name__ == '__main__':
    unittest.main()
