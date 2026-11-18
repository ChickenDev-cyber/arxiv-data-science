"""Measured runs must produce data and report failures honestly."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MeasuredParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.corpus = self.root/'corpus'
        version = self.corpus/'2501.00001'/'v1'
        version.mkdir(parents=True)
        (version/'main.tex').write_text(r'\documentclass{article}\begin{document}A real sentence.\end{document}')
        self.out = self.root/'measured'

    def run_command(self, *extra, **env_overrides):
        env = dict(os.environ, PYTHONPATH='src', ARXIV_SENTENCE_BACKEND='invalid_inherited_backend',
                   ARXIV_SAT_MODEL=str(self.root/'missing-model'),
                   ARXIV_SAT_TOKENIZER=str(self.root/'missing-tokenizer'))
        env.update(env_overrides)
        return subprocess.run([sys.executable, '-m', 'arxiv_pipeline.parsing.benchmark',
            '--corpus', str(self.corpus), '--output-dir', str(self.out), *extra],
            text=True, capture_output=True, env=env)

    def test_run_writes_sqlite_and_measures_whole_process(self):
        result = self.run_command()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.out/'benchmark.json').read_text())
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['backend'], 'rule')
        self.assertGreater(report['wall_seconds'], 0)
        self.assertGreater(report['peak_rss_bytes'], 0)
        self.assertIn('rss', report['rss_method'])
        self.assertEqual(report['pipeline']['processed_publications'], 1)
        self.assertEqual(report['statistics']['version_accounted_rate'], 1.0)
        self.assertTrue((self.out/'statistics/publications.csv').is_file())
        self.assertIsNone(report['pipeline']['python_heap_peak_mb'])
        self.assertEqual(report['output_bytes'], (self.out/'parsing.sqlite').stat().st_size)
        self.assertEqual(len(report['source_sha256']), 64)

    def test_existing_output_is_not_overwritten(self):
        self.out.mkdir(); marker=self.out/'keep'; marker.write_text('original')
        result=self.run_command()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), 'original')

    def test_no_publications_cannot_be_complete(self):
        (self.corpus/'2501.00001'/'v1'/'main.tex').unlink()
        # A missing input path, unlike a known PDF-only record, is an invalid run.
        result=self.run_command('--corpus',str(self.root/'missing'))
        self.assertNotEqual(result.returncode, 0)
        report=json.loads((self.out/'benchmark.json').read_text())
        self.assertNotEqual(report['status'], 'complete')

    def test_failed_paper_is_reported_even_when_legacy_cli_exits_zero(self):
        result=self.run_command('--backend','sat')
        # Use an unavailable explicit model to make failure deterministic offline.
        # Backend/model resolution is exercised in a separate child process.
        self.assertNotEqual(result.returncode, 0)
        report=json.loads((self.out/'benchmark.json').read_text())
        self.assertNotEqual(report['status'], 'complete')

    def test_requested_parquet_cannot_be_complete_when_dependency_is_missing(self):
        stub = self.root/'without-pyarrow'
        stub.mkdir()
        (stub/'pyarrow.py').write_text('raise ImportError("deliberately unavailable")\n')
        result = self.run_command('--parquet',
                                  PYTHONPATH=str(stub)+os.pathsep+str(Path('src').resolve()))
        self.assertNotEqual(result.returncode, 0)
        report = json.loads((self.out/'benchmark.json').read_text())
        self.assertEqual(report['pipeline']['processed_publications'], 1)
        self.assertEqual(report['exit_code'], 0)  # legacy CLI tolerates export skips
        self.assertEqual(report['status'], 'incomplete')

    def test_measured_resume_restores_backup_and_skips_completed_papers(self):
        backup=self.root/'persistent'/'checkpoint.sqlite'
        result=self.run_command('--checkpoint','--backup-db',str(backup),'--backup-every','1')
        self.assertEqual(result.returncode,0,result.stderr)
        self.out=self.root/'resumed'
        result=self.run_command('--resume-from',str(backup))
        self.assertEqual(result.returncode,0,result.stderr)
        report=json.loads((self.out/'benchmark.json').read_text())
        self.assertEqual(report['run_kind'],'resume')
        self.assertEqual(report['pipeline']['skipped_publications'],1)
        self.assertEqual(report['pipeline']['processed_publications'],0)
        self.assertIsNone(report['mean_seconds_per_processed_publication'])

    def test_corrupted_backup_checksum_is_rejected(self):
        from arxiv_pipeline.parsing.resume import restore_database
        backup=self.root/'bad.sqlite';backup.write_bytes(b'wrong')
        backup.with_suffix('.sqlite.sha256').write_text('0'*64)
        with self.assertRaises(ValueError):restore_database(backup,self.root/'target.sqlite')
        self.assertFalse((self.root/'target.sqlite').exists())
