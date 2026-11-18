"""Reduced source cases found in the frozen 1,000-publication quality audit."""
import unittest

from arxiv_pipeline.hierarchy.builder import build_hierarchy
from arxiv_pipeline.standardization import clean_latex


def nodes(body):
    return build_hierarchy({'arxiv_id': '2503.07826', 'version': 1,
        'status': 'success', 'merged_tex':
        r'\documentclass{article}\begin{document}' + body + r'\end{document}'})['nodes']


def leaves(body, kind):
    return [n['content'] for n in nodes(body) if n['kind'] == kind]


class ReleaseCleanupTests(unittest.TestCase):
    def test_bare_environment_end_is_not_a_sentence(self):
        self.assertEqual(leaves(r'\begin{remark}A useful observation. \end{remark}', 'sentence'),
                         [r'\begin{remark}A useful observation.'])

    def test_bare_environment_begin_between_sections_is_not_a_sentence(self):
        self.assertEqual(leaves(r'\begin{appendix}\section{Proof}The result follows. \end{appendix}',
                                'sentence'), ['The result follows.'])

    def test_marker_in_unknown_argument_is_preserved(self):
        self.assertEqual(leaves(r'\custom{\end{remark}}', 'sentence'),
                         [r'\custom{\end{remark}}'])

    def test_unbraced_length_argument_consumes_only_its_two_arguments(self):
        self.assertEqual(clean_latex(r'\setlength\itemsep{-2pt}{Keep.} '
                                    r'\addtolength \tabcolsep {1pt} Next.'), '{Keep.} Next.')

    def test_length_in_math_or_unknown_command_is_preserved(self):
        text = r'$\setlength\x{2pt}$ \custom{\setlength\x{2pt}}'
        self.assertEqual(clean_latex(text), text)

    def test_twocolumn_optional_body_is_processed_without_losing_abstract(self):
        self.assertEqual(leaves(r'\twocolumn[{\renewcommand\twocolumn[1][]{#1}'
             r'\maketitle\begin{abstract}An abstract with $x$.\end{abstract}}] Body.',
             'sentence'), ['An abstract with $x$.', 'Body.'])

    def test_malformed_layout_wrapper_is_preserved(self):
        for text in [r'\twocolumn[Unclosed', r'\resizebox{1pt}{!}{Unclosed',
                     r'\scalebox{.9}[.8]{Unclosed']:
            with self.subTest(text=text):
                self.assertEqual(clean_latex(text), text)

    def test_bbl_input_is_excluded_but_body_and_appendix_remain(self):
        for command in [r'\input{bibliography.bbl}', r'\input references.bbl']:
            with self.subTest(command=command):
                self.assertEqual(leaves('Body. ' + command +
                    r'\appendix\section{Proof}Proof text.', 'sentence'),
                    ['Body.', 'Proof text.'])

    def test_unresolved_tex_input_is_retained(self):
        self.assertIn(r'\input{annex}', ' '.join(leaves(r'Before. \input{annex}', 'sentence')))

    def test_spaced_list_delimiters_create_list_and_items(self):
        result = nodes(r'\begin {itemize}\item First.\item Second.\end {itemize}After.')
        self.assertEqual(sum(n['kind'] == 'itemize' for n in result), 1)
        self.assertEqual(sum(n['kind'] == 'item' for n in result), 2)
        self.assertEqual([n['content'] for n in result if n['kind'] == 'sentence'],
                         ['First.', 'Second.', 'After.'])

    def test_wrap_floats_preserve_caption_and_contents_in_one_leaf(self):
        for env, content in [('wraptable', r'\begin{tabular}{cc}A & $x$\\ B & 2\end{tabular}'),
                             ('wrapfigure', r'\includegraphics{plot.pdf}')]:
            body = (r'Before. \begin{' + env + r'}[11]{C}{0.44\textwidth}'
                    r'\setlength\tabcolsep{3pt}' + content +
                    r'\caption{Our result.}\end{' + env + '} After.')
            with self.subTest(env=env):
                figs = leaves(body, 'figure')
                self.assertEqual(len(figs), 1)
                self.assertIn(content, figs[0])
                self.assertIn(r'\caption{Our result.}', figs[0])
                self.assertNotIn('setlength', figs[0])
                self.assertEqual(leaves(body, 'sentence'), ['Before.', 'After.'])

    def test_standalone_tables_inside_known_size_wrappers_are_figure_leaves(self):
        for env in ['tabular', 'tabular*', 'longtable']:
            table = r'\begin{' + env + r'}{cc}A & $x$\\B & 2\end{' + env + '}'
            for wrapper in [table, r'\resizebox{0.91\textwidth}{!}{' + table + '}',
                            r'\scalebox{0.95}[0.8]{' + table + '}']:
                with self.subTest(env=env, wrapper=wrapper):
                    body = r'Before. \begin{center}' + wrapper + r'\end{center} After.'
                    self.assertEqual(leaves(body, 'figure'), [table])
                    self.assertEqual(leaves(body, 'sentence'), ['Before.', 'After.'])

    def test_float_ends_at_matching_environment(self):
        body = r'\begin{figure}\begin{tabular}{c}A\end{tabular}\caption{C.}\end{figure} Tail.'
        self.assertEqual(leaves(body, 'figure'), [body.removesuffix(' Tail.')])
        self.assertEqual(leaves(body, 'sentence'), ['Tail.'])


if __name__ == '__main__':
    unittest.main()
