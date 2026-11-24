"""Module C feature tests (spec T1-T4)."""
import json
import unittest

import numpy as np

from arxiv_pipeline.matching.features import (FEATURE_GROUPS, FEATURE_ORDER, INDEX, TitleVectorizer, author_keys,
                                              build_features, extract_arxiv_id, masked, normalize_text, parse_year,
                                              title_tokens)


ENTRIES = [
    dict(citation_key='vaswani', title=r'Attention Is All You {N}eed', authors=['Vaswani, A.', 'Shazeer, N.', 'others'],
         year=2017, url='https://arxiv.org/abs/1706.03762v5', doi='', journal=''),
    dict(citation_key='he', title='Deep residual learning for image recognition', authors=['Kaiming He'], year=2016,
         url='', doi='', journal='CVPR'),
    dict(citation_key='blank', title=None, authors=None, year=None, url=None, doi=None, journal=None),
]
REFS = [
    dict(ref_arxiv_id='1512.03385', ref_title='Deep Residual Learning for Image Recognition', ref_authors=['Kaiming He', 'Xiangyu Zhang'], ref_submission_date='2015-12-10'),
    dict(ref_arxiv_id='1706.03762', ref_title='Attention Is All You Need', ref_authors=['Ashish Vaswani', 'Noam Shazeer'], ref_submission_date='2017-06-12'),
    dict(ref_arxiv_id='1810.04805', ref_title='BERT: Pre-training of Deep Bidirectional Transformers', ref_authors=['Jacob Devlin'], ref_submission_date='2018-10-11'),
]
CORPUS = [e['title'] for e in ENTRIES if e['title']] + [r['ref_title'] for r in REFS] + [
    'Neural machine translation by jointly learning to align and translate', 'Adam: a method for stochastic optimization']


class NormalizationTests(unittest.TestCase):  # T1
    def test_latex_unicode_and_case(self):
        self.assertEqual(normalize_text(r'Schr{\"o}dinger \emph{Equation} for $\alpha$-Decay~in Na\"ive \v{C}ech'),
                         'schrodinger equation for alpha decay in naive cech')
        self.assertEqual(normalize_text('Ångström–Résumé  ÉCOLE'), 'angstrom resume ecole')
        self.assertEqual(normalize_text(r'\textbf{Bold} and \textit{italic}'), 'bold and italic')
        self.assertEqual(normalize_text(None), '')
        self.assertEqual(title_tokens('The theory of the x-ray in d dimensions'), ['theory', 'x', 'ray', 'd', 'dimensions'])

    def test_author_formats(self):
        self.assertEqual(author_keys(['Emparan, Roberto', 'Harvey S. Reall', 'et al.']), [('emparan', 'r'), ('reall', 'h')])
        self.assertEqual(author_keys('A. Einstein and B. Podolsky and others'), [('einstein', 'a'), ('podolsky', 'b')])
        self.assertEqual(author_keys([r'G{\"o}del, K.', 'van der Berg, J.', 'John Smith Jr.']), [('godel', 'k'), ('berg', 'j'), ('smith', 'j')])
        self.assertEqual(author_keys(['ATLAS Collaboration']), [('atlas collaboration', '')])
        self.assertEqual(author_keys(np.array(['Last, First'])), [('last', 'f')])
        self.assertEqual(author_keys(None), [])

    def test_year(self):
        self.assertEqual(parse_year('2017-06-12'), 2017)
        self.assertEqual(parse_year(np.int32(2016)), 2016)
        self.assertIsNone(parse_year('n.d.'))
        self.assertIsNone(parse_year(None))


class ArxivIdTests(unittest.TestCase):  # T2
    def test_forms(self):
        cases = {
            'https://arxiv.org/abs/2106.09685v2': '2106.09685',
            'https://arxiv.org/pdf/2106.09685.pdf': '2106.09685',
            'arXiv:2501.13787': '2501.13787',
            'arXiv preprint arXiv:1312.6114': '1312.6114',
            '10.48550/arXiv.2401.00123': '2401.00123',
            'arXiv:hep-th/0608012v3': 'hep-th/0608012',
            'http://arxiv.org/abs/cs.CV/0102003': 'cs/0102003',
            'http://arxiv.org/abs/math/0501234v1': 'math/0501234',
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_arxiv_id(text), expected)

    def test_non_arxiv_fields_are_ignored(self):
        self.assertIsNone(extract_arxiv_id('10.1103/PhysRevD.95.123456'))
        self.assertIsNone(extract_arxiv_id('Proceedings 2019.12345'))
        self.assertIsNone(extract_arxiv_id(None, '', 'Nature'))
        self.assertEqual(extract_arxiv_id('', None, 'arXiv:1706.03762'), '1706.03762')


class VectorizerTests(unittest.TestCase):  # T3
    def test_json_round_trip_gives_identical_vectors(self):
        vec = TitleVectorizer.fit(CORPUS, min_df=1)
        again = TitleVectorizer.from_json(json.loads(json.dumps(vec.to_json())))
        a, b = vec.transform(CORPUS), again.transform(CORPUS)
        self.assertGreater(a.shape[1], 0)
        self.assertEqual((a != b).nnz, 0)
        norms = np.sqrt(np.asarray(a.multiply(a).sum(axis=1))).ravel()
        np.testing.assert_allclose(norms, 1.0)

    def test_empty_vectorizer_is_usable(self):
        vec = TitleVectorizer.fit([None, ''])
        self.assertEqual(vec.transform(['abc']).shape, (1, 0))
        self.assertEqual(build_features(ENTRIES, REFS, vec).shape, (9, len(FEATURE_ORDER)))


class FeatureTests(unittest.TestCase):  # T4
    def setUp(self):
        self.vec = TitleVectorizer.fit(CORPUS, min_df=1)

    def test_deterministic_finite_and_shaped(self):
        a = build_features(ENTRIES, REFS, self.vec)
        b = build_features(ENTRIES, REFS, TitleVectorizer.from_json(self.vec.to_json()))
        self.assertEqual(a.shape, (len(ENTRIES) * len(REFS), len(FEATURE_ORDER)))
        self.assertTrue(np.isfinite(a).all())
        np.testing.assert_array_equal(a, b)
        self.assertEqual(FEATURE_ORDER, [f for g in FEATURE_GROUPS.values() for f in g])

    def test_signals_point_to_the_right_candidate(self):
        X = build_features(ENTRIES, REFS, self.vec).reshape(3, 3, -1)
        self.assertEqual(X[0, 1, INDEX['id_exact']], 1.0)
        self.assertEqual(X[0, 0, INDEX['id_conflict']], 1.0)
        self.assertEqual(X[0, 1, INDEX['title_exact']], 1.0)
        self.assertEqual(X[0, 1, INDEX['first_author_match']], 1.0)
        self.assertEqual(X[0, 1, INDEX['mutual_best']], 1.0)
        self.assertEqual(X[1, 0, INDEX['title_rank_in_entry']], 1.0)
        self.assertEqual(X[1, 0, INDEX['year_delta']], 1.0)
        self.assertEqual(X[1, 0, INDEX['abs_year_delta_le1']], 1.0)
        for name in ('entry_title_missing', 'entry_authors_missing', 'year_missing'):
            self.assertTrue((X[2, :, INDEX[name]] == 1.0).all(), name)
        self.assertTrue((X[2, :, INDEX['token_set_ratio']] == 0).all())

    def test_masking_removes_only_identifier_signals(self):
        X = build_features(ENTRIES, REFS, self.vec)
        hidden = build_features(ENTRIES, REFS, self.vec, mask_ids=True)
        np.testing.assert_array_equal(masked(X), hidden)
        other = [INDEX[f] for f in FEATURE_ORDER if f not in FEATURE_GROUPS['id']]
        np.testing.assert_array_equal(X[:, other], hidden[:, other])

    def test_empty_inputs(self):
        self.assertEqual(build_features([], REFS, self.vec).shape, (0, len(FEATURE_ORDER)))
        self.assertEqual(build_features(ENTRIES, [], self.vec).shape, (0, len(FEATURE_ORDER)))


if __name__ == '__main__':
    unittest.main()
