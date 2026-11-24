"""Module C model/contract tests (spec T5-T11) on a seeded, realistic synthetic corpus."""
import json
import random
import shutil
import sys
import tempfile
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyarrow.parquet as pq

from tests.matching.fixtures import Corpus
from arxiv_pipeline.matching import modeling
from arxiv_pipeline.matching.contracts import SCHEMAS, write_rows
from arxiv_pipeline.matching.modeling import mrr_at_5, rank_top5, score, train
from arxiv_pipeline.matching.pipeline import run_pipeline
from arxiv_pipeline.matching.splitting import make_pool, make_split


WORDS = ('neural quantum graph learning network deep field theory black hole entropy gauge string inflation '
         'dark matter galaxy cluster lensing spectral sparse convex optimization stochastic gradient bayesian '
         'inference markov chain monte carlo transformer attention language model vision image segmentation '
         'reinforcement policy robust adversarial kernel manifold topology lattice spin glass phase transition '
         'superconductivity magnetic plasma turbulence fluid wave scattering amplitude symmetry boundary').split()
FIRST = 'Alice Bob Carol David Erin Frank Grace Heidi Ivan Judy Mallory Niaj Olivia Peggy Rupert Sybil'.split()
LAST = 'Nguyen Tran Smith Muller Garcia Rossi Kim Chen Novak Silva Dubois Kowalski Tanaka Ivanov Haddad Okafor'.split()
SMALL_GRID = dict(LR_GRID=({'C': 1.0},), RANKER_GRID=({'num_leaves': 7, 'learning_rate': 0.1, 'n_estimators': 40, 'min_data_in_leaf': 5},), CV_FOLDS=3)


def realistic(root, n=45, refs_per_paper=24, seed=7):
    """Corpus fixture whose BibTeX entries are noisy views of the paper's references."""
    c = Corpus(root, n=n, entries=1)
    rng = random.Random(seed)
    catalogue = []
    for k in range(160):
        year = rng.randint(2005, 2023)
        catalogue.append(dict(ref_arxiv_id=f'{year % 100:02d}{rng.randint(1, 12):02d}.{k + 1:05d}',
                              ref_title=' '.join(rng.sample(WORDS, rng.randint(4, 8))).capitalize(),
                              ref_authors=[f'{rng.choice(FIRST)} {rng.choice(LAST)}' for _ in range(rng.randint(1, 4))],
                              ref_submission_date=f'{year}-03-01', ref_s2_id=f's2-{k}'))
    bibtex, references, truth = [], [], {}
    for paper in c.ids['trong']:
        for j, ref in enumerate(rng.sample(catalogue, refs_per_paper)):
            references.append(dict(ref, arxiv_id=paper))
            words = ref['ref_title'].split()
            if rng.random() < 0.3:
                words.pop(rng.randrange(len(words)))
            title = None if rng.random() < 0.1 else ' '.join(words)
            if title and rng.random() < 0.3:
                title = '{' + title.lower() + '}'
            authors = [f"{a.split()[-1]}, {a.split()[0][0]}." for a in ref['ref_authors']]
            if len(authors) > 2 and rng.random() < 0.5:
                authors = authors[:2] + ['others']
            url = f"https://arxiv.org/abs/{ref['ref_arxiv_id']}v2" if rng.random() < 0.4 else ''
            key = f'ref{j:02d}'
            bibtex.append(dict(arxiv_id=paper, citation_key=key, entry_type='article', title=title, authors=authors,
                               year=int(ref['ref_submission_date'][:4]) + rng.choice((0, 0, 1)), doi='', journal='', url=url))
            truth[(paper, key)] = ref['ref_arxiv_id']
        for j in range(3):
            bibtex.append(dict(arxiv_id=paper, citation_key=f'zz{j}', entry_type='book', title=f'Textbook of {rng.choice(WORDS)} volume {j}',
                               authors=['Unknown, U.'], year=1990, doi='', journal='', url=''))
    c.replace('bibtex', bibtex)
    c.replace('references', references)
    c.truth = truth
    return c


def write_labels(c, pool, path, drop=()):
    rows = []
    for p in pq.read_table(pool).to_pylist():
        if p['arxiv_id'] in drop:
            continue
        for entry in c.rows('bibtex'):
            if entry['arxiv_id'] != p['arxiv_id']:
                continue
            gt = c.truth.get((entry['arxiv_id'], entry['citation_key']))
            rows.append(dict(arxiv_id=entry['arxiv_id'], citation_key=entry['citation_key'], groundtruth_arxiv_id=gt,
                             label_method=p['label_method'], labeler='fixture', label_status='matched' if gt else 'no_match',
                             evidence='synthetic', heuristic_version='fixture' if p['label_method'] == 'automatic' else None))
    return write_rows(path, 'labels', rows)


class ModelingBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.c = realistic(cls.root / 'corpus')
        cls.pool = make_pool(cls.c.inventory, cls.c.manual, cls.root / 'pool.parquet')
        cls.labels = write_labels(cls.c, cls.pool, cls.root / 'labels.parquet')
        cls.split = make_split(cls.pool, cls.labels, cls.root / 'split.parquet')
        cls.patcher = patch.multiple(modeling, **SMALL_GRID)
        cls.patcher.start()
        cls.model = train(cls.c.inventory, cls.labels, cls.split, cls.root / 'model')

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()
        cls.temp.cleanup()

    def out(self, name):
        return self.root / 'runs' / f'{self._testMethodName}-{name}.parquet'


class RankingTests(unittest.TestCase):  # T5
    def test_mrr_ranks_and_ties(self):
        refs = ['a', 'b', 'c', 'd', 'e', 'f']
        self.assertEqual(mrr_at_5([(refs, [6, 5, 4, 3, 2, 1], 'a')]), 1.0)
        self.assertAlmostEqual(mrr_at_5([(refs, [6, 5, 4, 3, 2, 1], 'e')]), 0.2)
        self.assertEqual(mrr_at_5([(refs, [6, 5, 4, 3, 2, 1], 'f')]), 0.0)
        self.assertEqual(rank_top5(['c', 'a', 'b'], [1, 1, 1]), ['a', 'b', 'c'])
        self.assertAlmostEqual(mrr_at_5([(['c', 'a', 'b'], [1, 1, 1], 'b')]), 0.5)
        self.assertAlmostEqual(mrr_at_5([(refs, [6, 5, 4, 3, 2, 1], 'a'), (refs, [6, 5, 4, 3, 2, 1], 'b')]), 0.75)
        self.assertIsNone(mrr_at_5([]))


class ScoreContractTests(ModelingBase):
    def test_model_dir_contents(self):
        names = {p.name for p in self.model.iterdir()}
        config = json.loads((self.model / 'config.json').read_text())
        self.assertTrue({'config.json', 'tfidf.json', 'baseline.json', 'training_report.json', config['model']['file']} <= names)
        self.assertFalse([n for n in names if n.endswith(('.pkl', '.joblib'))])
        report = json.loads((self.model / 'training_report.json').read_text())
        self.assertTrue(report['cv'])
        self.assertEqual(report['counts']['train_papers'], sum(r['partition'] == 'train' for r in pq.read_table(self.split).to_pylist()))
        self.assertGreater(report['peak_rss_mb'], 0)

    def test_score_covers_every_pair_with_finite_values(self):  # T6
        split = pq.read_table(self.split).to_pylist()
        papers = {r['arxiv_id'] for r in split}
        entries = Counter(r['arxiv_id'] for r in self.c.rows('bibtex') if r['arxiv_id'] in papers)
        refs = Counter(r['arxiv_id'] for r in self.c.rows('references') if r['arxiv_id'] in papers)
        for method in modeling.METHODS:
            with self.subTest(method=method):
                path = score(self.c.inventory, self.split, self.model, self.out(method), method)
                table = pq.read_table(path)
                self.assertTrue(table.schema.equals(SCHEMAS['scores'], check_metadata=False))
                rows = table.to_pylist()
                self.assertEqual(Counter(r['arxiv_id'] for r in rows), Counter({p: entries[p] * refs[p] for p in papers}))
                self.assertEqual(len({(r['arxiv_id'], r['citation_key'], r['ref_arxiv_id']) for r in rows}), len(rows))
                self.assertTrue(np.isfinite([r['score'] for r in rows]).all())

    def test_model_beats_baseline_on_held_out_papers(self):
        split = {r['arxiv_id']: r for r in pq.read_table(self.split).to_pylist()}
        held = {p for p, r in split.items() if r['partition'] != 'train'}
        result = {}
        for method in modeling.METHODS:
            grouped = {}
            for r in pq.read_table(score(self.c.inventory, self.split, self.model, self.out(method), method)).to_pylist():
                if r['arxiv_id'] in held:
                    ids, scores = grouped.setdefault((r['arxiv_id'], r['citation_key']), ([], []))
                    ids.append(r['ref_arxiv_id'])
                    scores.append(r['score'])
            result[method] = mrr_at_5([(ids, s, self.c.truth[k]) for k, (ids, s) in grouped.items() if k in self.c.truth])
        self.assertGreater(result['model'], result['baseline'])
        self.assertGreater(result['model'], 0.9)

    def test_score_never_reads_labels(self):  # T7
        before = pq.read_table(score(self.c.inventory, self.split, self.model, self.out('before'), 'model'))
        moved = self.labels.with_suffix('.moved')
        self.labels.rename(moved)
        try:
            with patch.object(modeling, 'load_matched', side_effect=AssertionError('labels read')):
                after = pq.read_table(score(self.c.inventory, self.split, self.model, self.out('after'), 'model'))
        finally:
            moved.rename(self.labels)
        self.assertTrue(before.equals(after))

    def test_invalid_method_and_no_overwrite(self):  # T9
        with self.assertRaises(ValueError):
            score(self.c.inventory, self.split, self.model, self.out('bad'), 'oracle')
        self.assertFalse(self.out('bad').exists())
        path = score(self.c.inventory, self.split, self.model, self.out('once'), 'baseline')
        with self.assertRaises(FileExistsError):
            score(self.c.inventory, self.split, self.model, path, 'baseline')
        snapshot = {p.name: p.read_bytes() for p in self.model.iterdir()}
        with self.assertRaises(FileExistsError):
            train(self.c.inventory, self.labels, self.split, self.model)
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.model.iterdir()})

    def test_feature_version_mismatch_is_rejected(self):
        copy = self.root / 'stale-model'
        shutil.copytree(self.model, copy)
        config = json.loads((copy / 'config.json').read_text())
        config['feature_version'] = 'features-v0'
        (copy / 'config.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'feature_version'):
            score(self.c.inventory, self.split, copy, self.out('stale'), 'model')


MODEL_FILES = ('config.json', 'tfidf.json', 'baseline.json', 'logistic.json', 'ranker.txt')


def model_files(path):
    return {name: (path / name).read_bytes() for name in MODEL_FILES if (path / name).exists()}


class TrainingIsolationTests(ModelingBase):
    def test_same_seed_gives_identical_model_files(self):  # T10
        again = train(self.c.inventory, self.labels, self.split, self.root / 'model-again')
        self.assertEqual(model_files(self.model), model_files(again))

    def test_valid_and_test_data_do_not_change_the_model(self):  # T8
        split = pq.read_table(self.split).to_pylist()
        held = {r['arxiv_id'] for r in split if r['partition'] != 'train'}
        other = realistic(self.root / 'perturbed')
        bibtex = [dict(r, title=(r['title'] or 'x')[::-1], url='') if r['arxiv_id'] in held else r for r in other.rows('bibtex')]
        other.replace('bibtex', bibtex)
        labels = write_labels(other, self.pool, self.root / 'labels-without-held.parquet', drop=held)
        perturbed = train(other.inventory, labels, self.split, self.root / 'model-perturbed')
        self.assertEqual(model_files(self.model), model_files(perturbed))
        report = json.loads((perturbed / 'training_report.json').read_text())
        self.assertEqual(report['counts']['valid_queries'], 0)


class ExperimentTests(ModelingBase):
    def test_experiments_write_all_sections_without_touching_model(self):
        from arxiv_pipeline.matching.experiments import run_experiments
        before = model_files(self.model)
        out = run_experiments(self.c.inventory, self.labels, self.split, self.model, self.root / 'experiments')
        results = json.loads((out / 'experiments.json').read_text())
        self.assertEqual(set(results['E1_E2']), {'valid', 'test'})
        self.assertTrue({'baseline', 'model', 'logistic', 'ranker'} <= set(results['E1_E2']['valid']))
        self.assertEqual(set(results['E3_ablation']), {'full'} | {'-' + g for g in modeling.FEATURE_GROUPS})
        self.assertEqual(results['E4_heuristic']['papers'], 5)
        self.assertEqual(set(results['E6_features']), set(modeling.FEATURE_ORDER))
        self.assertIn('E6 Features', (out / 'feature_analysis.md').read_text())
        self.assertEqual(before, model_files(self.model))
        with self.assertRaises(FileExistsError):
            run_experiments(self.c.inventory, self.labels, self.split, self.model, out)


class PipelineIntegrationTests(unittest.TestCase):  # T11
    def test_full_run_with_real_module_c(self):
        with tempfile.TemporaryDirectory() as temp, patch.multiple(modeling, **SMALL_GRID):
            root = Path(temp)
            c = realistic(root / 'corpus')
            labels = write_labels(c, make_pool(c.inventory, c.manual, root / 'pool.parquet'), root / 'labels.parquet')
            stubs = {}
            for name in ('labeling', 'evaluation', 'validation'):
                stubs['arxiv_pipeline.matching.' + name] = types.ModuleType('arxiv_pipeline.matching.' + name)
            stubs['arxiv_pipeline.matching.modeling'] = modeling
            stubs['arxiv_pipeline.matching.labeling'].build_labels = lambda inventory, pool, manual_dir, output_dir: labels

            def evaluate(inventory, labels, split, scores, predictions, report_dir):
                report_dir.mkdir(parents=True)
                (report_dir / 'metrics.json').write_text('{}')
                return report_dir / 'metrics.json'

            def validate(dataset_dir, inventory, split, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(json.dumps({'valid': True}))
                return output_path
            stubs['arxiv_pipeline.matching.evaluation'].evaluate = evaluate
            stubs['arxiv_pipeline.matching.validation'].validate_dataset = validate
            config = root / 'config.json'
            config.write_text(json.dumps(dict(mode='full', inventory=str(c.inventory), manual_papers=str(c.manual),
                                              manual_dir=str(root / 'manual'), output_dir=str(root / 'run'), owners=['trong'], seed=42)))
            with patch.dict(sys.modules, stubs):
                out = run_pipeline(config)
            state = json.loads((out / 'run_config.json').read_text())
            self.assertEqual(state['status'], 'complete')
            self.assertTrue((out / 'model' / 'config.json').is_file())
            rows = pq.read_table(out / 'export/trong/data/predictions').to_pylist()
            hits = [r['cand_1'] == c.truth[(r['arxiv_id'], r['citation_key'])] for r in rows if (r['arxiv_id'], r['citation_key']) in c.truth]
            self.assertGreater(np.mean(hits), 0.9)


if __name__ == '__main__':
    unittest.main()
