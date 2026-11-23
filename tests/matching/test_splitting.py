import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from tests.matching.fixtures import Corpus, write
from arxiv_pipeline.matching.splitting import make_pool, make_split


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = Corpus(self.root)

    def pool(self):
        return make_pool(self.c.inventory, self.c.manual, self.root/'pool.parquet')

    def test_quota_and_paper_level_partitions_are_reproducible(self):
        pool = self.pool()
        labels = self.c.labels(pool)
        out = make_split(pool, labels, self.root/'split.parquet')
        rows = pq.read_table(out).to_pylist()
        self.assertEqual(Counter((r['label_method'], r['partition']) for r in rows), {('manual','train'):3, ('manual','valid'):1, ('manual','test'):1, ('automatic','valid'):1, ('automatic','test'):1})
        self.assertEqual(len({r['arxiv_id'] for r in rows}), 7)
        write(self.root/'reversed.parquet', list(reversed(pq.read_table(pool).to_pylist())))
        again = make_split(self.root/'reversed.parquet', labels, self.root/'again.parquet')
        self.assertEqual(rows, pq.read_table(again).to_pylist())
        for name in ['papers', 'bibtex', 'references']:
            self.c.replace(name, list(reversed(self.c.rows(name))))
        pool2 = make_pool(self.c.inventory, self.c.manual, self.root/'pool2.parquet')
        self.assertEqual(pq.read_table(pool).to_pylist(), pq.read_table(pool2).to_pylist())

    def test_quota_rounds_up_against_full_corpus(self):
        c = Corpus(self.root/'boundary', n=56)
        pool = make_pool(c.inventory, c.manual, c.root/'pool.parquet')
        self.assertEqual(Counter(r['label_method'] for r in pq.read_table(pool).to_pylist()), {'manual':5, 'automatic':6})

    def test_invalid_inventory_and_manual_list_are_rejected(self):
        cases = ['missing_file', 'wrong_count', 'duplicate_paper', 'unknown_owner', 'few_manual', 'duplicate_manual']
        for case in cases:
            with self.subTest(case=case):
                c = Corpus(self.root/case)
                if case == 'missing_file': c.path('references').unlink()
                if case == 'wrong_count': c.info['owners']['trong']['N']=24; c.save()
                if case == 'duplicate_paper': c.replace('papers', c.rows('papers')[:1]+c.rows('papers'))
                if case == 'unknown_owner': write(c.manual, [{'owner':'unknown', 'arxiv_id':'2501.00001'}])
                if case == 'few_manual': write(c.manual, pq.read_table(c.manual).to_pylist()[:4])
                if case == 'duplicate_manual': write(c.manual, pq.read_table(c.manual).to_pylist()*2)
                with self.assertRaises(ValueError): make_pool(c.inventory, c.manual, c.root/'bad.parquet')
                self.assertFalse((c.root/'bad.parquet').exists())

    def test_declared_row_counts_are_verified_for_every_input_table(self):
        for table in ['papers','references','elements','hierarchy','bibtex']:
            with self.subTest(table=table):
                self.c.info['owners']['trong']['row_counts']={table:len(self.c.rows(table))+1}
                self.c.save()
                with self.assertRaisesRegex(ValueError, 'row_counts.*'+table):
                    make_pool(self.c.inventory,self.c.manual,self.root/('bad-count-'+table+'.parquet'))

    def test_not_enough_automatic_papers_is_rejected(self):
        c = Corpus(self.root/'small', n=6)
        with self.assertRaises(ValueError): make_pool(c.inventory, c.manual, c.root/'pool.parquet')

    def test_invalid_labels_do_not_publish_split(self):
        pool = self.pool()
        original = pq.read_table(self.c.labels(pool)).to_pylist()
        for case in ['few_manual_matches', 'no_auto_matches', 'duplicate', 'unknown_paper', 'method', 'null_matched', 'status']:
            with self.subTest(case=case):
                rows = [dict(r) for r in original]
                if case == 'few_manual_matches': rows[0].update(label_status='unreviewed', groundtruth_arxiv_id=None)
                if case == 'no_auto_matches':
                    for r in rows:
                        if r['label_method']=='automatic': r.update(label_status='no_match', groundtruth_arxiv_id=None)
                if case == 'duplicate': rows.append(rows[0])
                if case == 'unknown_paper': rows[0]['arxiv_id']='2501.99999'
                if case == 'method': rows[0]['label_method']='automatic'
                if case == 'null_matched': rows[0]['groundtruth_arxiv_id']=None
                if case == 'status': rows[0]['label_status']='invented'
                bad = write(self.root/(case+'.parquet'), rows)
                with self.assertRaises(ValueError): make_split(pool, bad, self.root/('split-'+case+'.parquet'))

if __name__ == '__main__': unittest.main()
