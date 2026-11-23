import errno
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from tests.matching.fixtures import Corpus, write, COLUMNS
from arxiv_pipeline.matching.contracts import SCHEMAS
from arxiv_pipeline.matching.splitting import make_pool, make_split
from arxiv_pipeline.matching.export import build_predictions, export_owner


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.c = Corpus(self.root, owners=('trong', 'hoang'))
        # A referenced ID may be old-style or in somebody else's assigned range.
        refs = self.c.rows('references')
        refs[0]['ref_arxiv_id']='hep-th/9901001'
        refs[1]['ref_arxiv_id']='2502.00001'
        self.c.replace('references',refs)
        # Mixed-owner reference shards must filter by citing paper.
        self.c.replace('references',refs+self.c.rows('references','hoang'))
        # No matching pool needed for testing a valid typed empty predictions table.
        self.pred = write(self.root/'pred.parquet', [], SCHEMAS['predictions'])

    def test_exports_all_six_configs_and_filters_mixed_owner_shards(self):
        old_hash = hashlib.sha256(self.c.path('references').read_bytes()).hexdigest()
        out = export_owner(self.c.inventory, self.pred, 'trong', self.root/'export')
        config = yaml.safe_load((out/'README.md').read_text().split('---')[1])
        self.assertEqual({v['config_name'] for v in config['configs']}, {'papers','references','elements','hierarchy','bibtex','predictions'})
        for name in ['papers','references','elements','hierarchy','bibtex','predictions']:
            paths = list((out/'data'/name).glob('*.parquet'))
            self.assertTrue(paths)
            for path in paths:
                table = pq.read_table(path)
                self.assertEqual(table.schema, SCHEMAS[name])
                self.assertTrue(set(table['arxiv_id'].to_pylist()) <= set(self.c.ids['trong']))
        refs = pq.read_table(out/'data/references').to_pylist()
        self.assertIn('hep-th/9901001', {r['ref_arxiv_id'] for r in refs})
        self.assertIn('2502.00001', {r['ref_arxiv_id'] for r in refs})
        self.assertEqual(pq.read_table(out/'data/papers').num_rows, 25)
        self.assertEqual(pq.read_table(out/'data/predictions').num_rows, 0)
        self.assertEqual(hashlib.sha256(self.c.path('references').read_bytes()).hexdigest(), old_hash)

    def test_counts_use_owner_rows_inside_mixed_shards(self):
        self.c.info['owners']['trong']['row_counts']={'references':500,'papers':25}
        self.c.save()
        out=export_owner(self.c.inventory,self.pred,'trong',self.root/'counted')
        self.assertEqual(pq.read_table(out/'data/references').num_rows,500)
        self.c.info['owners']['trong']['row_counts']['references']=501
        self.c.save()
        with self.assertRaisesRegex(ValueError,'row_counts.*references'):
            export_owner(self.c.inventory,self.pred,'trong',self.root/'wrong-count')
        self.assertFalse((self.root/'wrong-count').exists())

    def test_existing_destination_and_bad_input_never_publish(self):
        destination = self.root/'existing'
        destination.mkdir()
        (destination/'keep').write_text('original')
        with self.assertRaises(FileExistsError): export_owner(self.c.inventory,self.pred,'trong',destination)
        self.assertEqual((destination/'keep').read_text(),'original')
        with self.assertRaises(ValueError): export_owner(self.c.inventory,self.pred,'unknown',self.root/'unknown')
        self.c.path('elements').unlink()
        with self.assertRaises(ValueError): export_owner(self.c.inventory,self.pred,'trong',self.root/'bad')
        self.assertFalse((self.root/'bad').exists())

    def test_write_failure_is_atomic(self):
        with patch('arxiv_pipeline.matching.export.write_rows', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): export_owner(self.c.inventory,self.pred,'trong',self.root/'broken')
        self.assertFalse((self.root/'broken').exists())

    def test_filesystem_without_hardlinks_can_export_atomically(self):
        with patch('arxiv_pipeline.matching.contracts.os.link', side_effect=OSError(errno.EOPNOTSUPP, 'links unavailable')):
            out=export_owner(self.c.inventory,self.pred,'trong',self.root/'portable')
        self.assertEqual(pq.read_table(out/'data/papers').num_rows,25)

    def test_small_shards_and_typed_empty_tables(self):
        self.c.replace('hierarchy',[])
        with patch('arxiv_pipeline.matching.export.SHARD_ROWS', 10):
            out = export_owner(self.c.inventory,self.pred,'trong',self.root/'shards')
        self.assertEqual(len(list((out/'data/papers').glob('*.parquet'))),3)
        self.assertEqual(pq.read_table(out/'data/hierarchy').num_rows,0)
        self.assertEqual(pq.read_schema(next((out/'data/hierarchy').glob('*.parquet'))),SCHEMAS['hierarchy'])

if __name__=='__main__': unittest.main()
