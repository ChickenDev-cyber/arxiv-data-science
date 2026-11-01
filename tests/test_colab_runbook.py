"""Execute notebook helpers offline; do not mount Drive, clone or contact arXiv."""
from contextlib import redirect_stdout
import io, json, os, shlex, sys, tempfile, unittest
import hashlib
import importlib.metadata
import re
import zipfile
from pathlib import Path
from unittest.mock import patch
REPO = Path(__file__).resolve().parents[1]

class ColabRunbookTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        notebook = json.loads((REPO / 'src/colab_baseline_smoke.ipynb').read_text())
        helper = next((c for c in notebook['cells'] if c.get('id') == 'runbook-helpers'), None)
        self.assertIsNotNone(helper, 'Notebook must provide its real runbook helpers for offline execution')
        self.ns = {'REPO_DIR': REPO, 'PIPELINE_ENV': {**os.environ, 'PYTHONPATH': str(REPO / 'src')}}
        exec(''.join(helper['source']), self.ns)

    def tearDown(self):
        self.tmp.cleanup()

    def test_runner_accepts_partial_exit_without_hiding_real_errors(self):
        out = io.StringIO()
        with redirect_stdout(out):
            result = self.ns['run_cli']([sys.executable, '-c', 'print("partial-run"); raise SystemExit(2)'], allowed=(0, 2))
        self.assertEqual(result, 2)
        self.assertIn('partial-run', out.getvalue())
        with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            self.ns['run_cli']([sys.executable, '-c', 'raise SystemExit(3)'], allowed=(0, 2))

    def test_notebook_helper_runs_actual_measured_mock_pipeline(self):
        root = self.root / 'corpus'
        bench = self.root / 'bench'
        rid = 'notebook-smoke'
        runner = [sys.executable, '-m', 'arxiv_pipeline.runner', '--mock', '--owner', 'trong', '--run-id', rid, '--output-root', str(root), '--max-candidates', '1', '--max-papers', '1']
        command = [sys.executable, '-m', 'arxiv_pipeline.benchmark', '--run-id', rid, '--output-root', str(root), '--output-dir', str(bench), '--cmd', shlex.join(runner)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(self.ns['run_cli'](command, allowed=(0, 2)), 0)
        data = json.loads((bench / (rid + '.json')).read_text())
        self.assertTrue(data['is_mock'])
        self.assertEqual(data['scope']['paper_count'], 1)
        self.assertEqual(data['exit_code'], 0)
        self.assertIsNotNone(data['wall_seconds'])

    def test_restore_checks_expected_checksum_before_publishing_copy(self):
        source = self.root / 'source.zip'
        source.write_bytes(b'changed-after-checksum')
        dest = self.root / 'restore/source.zip'
        with self.assertRaises(ValueError):
            self.ns['copy_verified'](source, dest, expected_sha256='0' * 64)
        self.assertFalse(dest.exists())

    def test_verified_copy_refuses_overwrite_and_accepts_identical_retry(self):
        source = self.root / 'source.zip'
        source.write_bytes(b'archive-payload' * 1000)
        dest = self.root / 'backup/source.zip'
        copy = self.ns['copy_verified']
        copy(source, dest)
        self.assertEqual(dest.read_bytes(), source.read_bytes())
        copy(source, dest)
        source.write_bytes(b'different-archive')
        with self.assertRaises(FileExistsError):
            copy(source, dest)
        self.assertEqual(dest.read_bytes(), b'archive-payload' * 1000)

    def test_notebook_export_packages_scope_and_keeps_mock_identity(self):
        rid = 'notebook-export'
        corpus, benchdir, statdir = (self.root / x for x in ('corpus', 'benchmark', 'statistics'))
        runner = [sys.executable, '-m', 'arxiv_pipeline.runner', '--mock', '--owner', 'trong', '--run-id', rid, '--output-root', str(corpus), '--max-candidates', '1', '--max-papers', '1']
        command = [sys.executable, '-m', 'arxiv_pipeline.benchmark', '--run-id', rid, '--output-root', str(corpus), '--output-dir', str(benchdir), '--cmd', shlex.join(runner)]
        with redirect_stdout(io.StringIO()):
            self.ns['run_cli'](command)
            self.ns['run_cli']([sys.executable, '-m', 'arxiv_pipeline.statistics', '--run-id', rid, '--output-root', str(corpus), '--output-dir', str(statdir)])
        self.ns.update(sys=sys, json=json, importlib=importlib, OUTPUT_ROOT=corpus, RUN_ID=rid, OWNER='trong', MEASUREMENT_ID='cold-01', BENCHMARK_DIR=benchdir, STATISTICS_DIR=statdir, HANDOFF_DIR=self.root / 'handoffs' / rid / 'cold-01', TEAM_PATH=self.root / 'team', stats=json.loads((statdir / (rid + '.json')).read_text()), bench=json.loads((benchdir / (rid + '.json')).read_text()), command=command, CACHE_MODE='cold', ensure_release=lambda: 'f' * 40)
        with redirect_stdout(io.StringIO()):
            self.execute_cell('export-handoff')
        bundle, checksum = (self.ns['BUNDLE'], self.ns['CHECKSUM'])
        self.assertEqual(checksum.read_text().split()[0], hashlib.sha256(bundle.read_bytes()).hexdigest())
        copied = self.root / 'team' / 'trong' / rid / 'cold-01' / bundle.name
        self.assertEqual(copied.read_bytes(), bundle.read_bytes())
        with zipfile.ZipFile(bundle) as archive:
            metadata = json.loads(archive.read('run.json'))
            self.assertTrue(metadata['is_mock'])
            self.assertIn('scope', metadata)
            self.assertEqual(metadata['scope']['paper_count'], 1)
            self.assertIn('snapshot.tar.gz', archive.namelist())
            self.assertIn(f'benchmark/{rid}-timing.json', archive.namelist())

    def test_real_runbook_rejects_mock_restore_bundle(self):
        bundle = self.root / 'mock.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('run.json', json.dumps({'owner': 'trong', 'run_id': 'test', 'is_mock': True}))
        bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.ns.update(RESTORE_BUNDLE=str(bundle), RUN_MODE='resume', TEAM_PATH=self.root, OWNER='trong', RUN_ID='test', MEASUREMENT_ID='resume-01', WORK_ROOT=self.root, json=json, re=re)
        with self.assertRaisesRegex(ValueError, 'mock'):
            self.execute_cell('restore-bundle')

    def execute_cell(self, cell_id):
        notebook = json.loads((REPO / 'src/colab_baseline_smoke.ipynb').read_text())
        cell = next((c for c in notebook['cells'] if c.get('id') == cell_id))
        exec(''.join(cell['source']), self.ns)

    def test_corrupted_copy_never_publishes_as_complete_backup(self):
        source = self.root / 'source.zip'
        source.write_bytes(b'complete-archive')
        dest = self.root / 'backup/source.zip'

        def corrupt(src, dst):
            Path(dst).write_bytes(b'incomplete')
        with patch.object(self.ns['shutil'], 'copyfile', side_effect=corrupt), self.assertRaises(ValueError):
            self.ns['copy_verified'](source, dest)
        self.assertFalse(dest.exists())
        self.assertFalse(list(dest.parent.glob('*.part')))
if __name__ == '__main__':
    unittest.main()
