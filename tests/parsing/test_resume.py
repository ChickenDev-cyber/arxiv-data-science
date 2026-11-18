"""Resume must verify inputs/code/output and retain a restorable SQLite backup."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from arxiv_pipeline.parsing.runner import run_parsing_pipeline

class ParsingResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.corpus=self.root/'corpus';self.db=self.root/'data.sqlite'
        for aid in ['2501.00001','2501.00002']:
            p=self.corpus/aid/'v1';p.mkdir(parents=True)
            (p/'main.tex').write_text(r'\documentclass{article}\begin{document}Original sentence.\end{document}')

    def run_pipeline(self, **kwargs):
        return run_parsing_pipeline(self.corpus,self.db,resume=True,track_python_memory=False,**kwargs)

    def test_second_run_skips_verified_results(self):
        self.run_pipeline()
        from arxiv_pipeline.parsing.runner import process_single_publication
        with patch('arxiv_pipeline.parsing.runner.process_single_publication',wraps=process_single_publication) as parse:
            summary=self.run_pipeline()
        self.assertEqual(parse.call_count,0)
        self.assertEqual(summary['skipped_publications'],2)
        self.assertEqual(summary['processed_publications'],0)
        self.assertEqual(summary['failed_publications'],0)

    def test_modified_input_reprocesses_only_affected_paper(self):
        self.run_pipeline()
        (self.corpus/'2501.00002'/'v1'/'main.tex').write_text(r'\documentclass{article}\begin{document}Changed sentence.\end{document}')
        summary=self.run_pipeline()
        self.assertEqual(summary['processed_publications'],1)
        self.assertEqual(summary['skipped_publications'],1)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM elements WHERE content='Changed sentence.'").fetchone()[0],1)

    def test_changed_code_cannot_reuse_old_checkpoint(self):
        self.run_pipeline()
        with patch('arxiv_pipeline.parsing.resume.source_fingerprint',return_value='changed'):
            summary=self.run_pipeline()
        self.assertEqual(summary['processed_publications'],2)
        self.assertEqual(summary['skipped_publications'],0)

    def test_modified_output_is_repaired_instead_of_skipped(self):
        self.run_pipeline()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("DELETE FROM elements WHERE arxiv_id='2501.00001' AND kind='sentence'")
        summary=self.run_pipeline()
        self.assertEqual(summary['processed_publications'],1)
        self.assertEqual(summary['skipped_publications'],1)

    def test_backup_restores_results_and_their_checkpoints(self):
        backup=self.root/'persistent'/'checkpoint.sqlite'
        self.run_pipeline(backup_path=backup,backup_every=1)
        self.assertTrue(backup.is_file())
        self.assertTrue(backup.with_suffix('.sqlite.sha256').is_file())
        restored=self.root/'restored.sqlite'
        with closing(sqlite3.connect(backup)) as src,closing(sqlite3.connect(restored)) as dst:src.backup(dst)
        self.db=restored;summary=self.run_pipeline()
        self.assertEqual(summary['skipped_publications'],2)

    def test_legacy_results_without_checkpoints_are_reprocessed(self):
        run_parsing_pipeline(self.corpus,self.db,track_python_memory=False)
        self.assertEqual(self.run_pipeline()['processed_publications'],2)

    def test_backup_cannot_overwrite_primary_or_unrelated_file(self):
        with self.assertRaises(ValueError):self.run_pipeline(backup_path=self.db)
        unrelated=self.root/'important.txt';unrelated.write_text('keep')
        with self.assertRaises(ValueError):self.run_pipeline(backup_path=unrelated)
        self.assertEqual(unrelated.read_text(),'keep')

    def test_concurrent_writer_is_rejected_before_opening_database(self):
        from arxiv_pipeline.runner.state import OutputLock
        with OutputLock(self.db.parent):
            with self.assertRaises(ValueError):self.run_pipeline()
        self.assertFalse(self.db.exists())
