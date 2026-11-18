"""Check real Arrow output, including empty tables and multiple row groups."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from arxiv_pipeline.parsing.storage import ParsingDatabase


@unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'requires real pyarrow')
class SingleParquetRealTests(unittest.TestCase):
    def test_empty_database_exports_three_typed_readable_files(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with ParsingDatabase(root/'db.sqlite') as db:
                self.assertEqual(db.export_to_single_parquet(root/'parquet'),
                                 {'elements':0,'hierarchy':0,'bibtex':0})
            for name in ['elements','hierarchy','bibtex']:
                table=pq.read_table(root/'parquet'/f'{name}.parquet')
                self.assertEqual(table.num_rows,0)
                self.assertIn('arxiv_id',table.column_names)

    def test_multiple_chunks_are_preserved_in_single_file(self):
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with ParsingDatabase(root/'db.sqlite') as db:
                db.conn.executemany('INSERT INTO elements VALUES (?,?,?,?)',
                    [(f'2501.00001#s{i}','2501.00001','sentence',f'Text {i}.') for i in range(5)])
                counts=db.export_to_single_parquet(root/'parquet',chunk_size=2)
            self.assertEqual(counts['elements'],5)
            table=pq.read_table(root/'parquet/elements.parquet')
            self.assertEqual(table['content'].to_pylist(),[f'Text {i}.' for i in range(5)])
            self.assertEqual(pq.ParquetFile(root/'parquet/elements.parquet').metadata.num_row_groups,3)
