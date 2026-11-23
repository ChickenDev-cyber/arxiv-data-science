import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from tests.matching.fixtures import Corpus, write
from arxiv_pipeline.matching.splitting import make_pool, make_split
from arxiv_pipeline.matching.export import build_predictions


class PredictionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = Corpus(self.root)
        self.pool = make_pool(self.c.inventory, self.c.manual, self.root/'pool.parquet')
        self.labels = self.c.labels(self.pool)
        self.split = make_split(self.pool, self.labels, self.root/'split.parquet')
        self.scores = self.c.scores(self.split)

    def build(self, output='predictions.parquet'):
        return build_predictions(self.c.inventory, self.labels, self.split, self.scores, self.root/output)

    def test_rank_ties_do_not_promote_ground_truth(self):
        out = self.build()
        rows = pq.read_table(out).to_pylist()
        self.assertEqual(len(rows), 140)
        self.assertTrue(pa.types.is_dictionary(pq.read_schema(out).field('partition').type))
        key19 = next(r for r in rows if r['citation_key']=='key19')
        self.assertEqual(key19['groundtruth_arxiv_id'], '2401.00020')
        self.assertEqual([key19[f'cand_{i}'] for i in range(1,6)], ['2401.00001','2401.00002','2401.00003','2401.00004','2401.00005'])
        lookup = {r['arxiv_id']:r['partition'] for r in pq.read_table(self.split).to_pylist()}
        self.assertTrue(all(r['partition']==lookup[r['arxiv_id']] for r in rows))

    def test_null_labels_short_and_empty_candidate_lists(self):
        paper = pq.read_table(self.split).to_pylist()[0]['arxiv_id']
        labels = pq.read_table(self.labels).to_pylist()
        for i, row in enumerate(r for r in labels if r['arxiv_id']==paper):
            row.update(label_status='no_match' if i%2 else 'unreviewed', groundtruth_arxiv_id=None)
        write(self.labels, labels)
        original = self.c.rows('references')
        for count in [2, 0]:
            self.c.replace('references', [r for r in original if r['arxiv_id']!=paper or r['ref_arxiv_id'] in ['2401.00001','2401.00002'][:count]])
            self.scores = self.c.scores(self.split)
            out = self.build('short'+str(count)+'.parquet')
            row = next(r for r in pq.read_table(out).to_pylist() if r['arxiv_id']==paper)
            self.assertIsNone(row['groundtruth_arxiv_id'])
            self.assertEqual([row[f'cand_{i}'] for i in range(1,6)], ['2401.00001','2401.00002',None,None,None] if count else [None]*5)

    def test_bad_scores_are_rejected_without_output(self):
        original = pq.read_table(self.scores).to_pylist()
        for case in ['duplicate','nan','inf','foreign','unknown_entry','unknown_paper','missing']:
            with self.subTest(case=case):
                rows = [dict(r) for r in original]
                if case=='duplicate': rows.append(dict(rows[0]))
                if case=='nan': rows[0]['score']=float('nan')
                if case=='inf': rows[0]['score']=float('inf')
                if case=='foreign': rows[0]['ref_arxiv_id']='hep-th/9901001'
                if case=='unknown_entry': rows[0]['citation_key']='unknown'
                if case=='unknown_paper': rows[0]['arxiv_id']='2501.99999'
                if case=='missing': rows.pop()
                write(self.scores, rows)
                with self.assertRaises(ValueError): self.build(case+'.parquet')
                self.assertFalse((self.root/(case+'.parquet')).exists())

    def test_missing_unknown_or_foreign_ground_truth_labels_rejected(self):
        original = pq.read_table(self.labels).to_pylist()
        for case in ['missing','unknown','foreign','nonmatched_gt']:
            with self.subTest(case=case):
                rows = [dict(r) for r in original]
                if case=='missing': rows.pop()
                if case=='unknown': rows[0]['citation_key']='unknown'
                if case=='foreign': rows[0]['groundtruth_arxiv_id']='hep-th/9901001'
                if case=='nonmatched_gt': rows[0]['label_status']='no_match'
                write(self.labels, rows)
                with self.assertRaises(ValueError): self.build(case+'.parquet')

if __name__=='__main__': unittest.main()
