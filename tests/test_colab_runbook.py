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
        self.ns = {'REPO_DIR': REPO, 'PIPELINE_ENV': {**os.environ, 'PYTHONPATH': str(REPO / 'src')}, 'SNAPSHOT_MAX_FILES': 500_000}
        exec(''.join(helper['source']), self.ns)
        automation = next(c for c in notebook['cells'] if c.get('id') == 'automation-worker')
        exec(''.join(automation['source']), self.ns)

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
        worker = self.ns['NotebookBatchWorker'](
            repo_dir=REPO, env=self.ns['PIPELINE_ENV'], work_root=self.root,
            output_root=self.root / 'corpus', drive_root=self.root,
            owner='trong', run_id=rid, pipeline_commit='f' * 40,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'],
            stop_after_tasks=50, max_files=1, expected_mock=True, cli=self.ns['run_cli'])
        batch = worker.make_batch('pilot', 'cold-01')
        with redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
            with self.assertRaises(RuntimeError):
                worker.backup_batch(batch, report, {'batches': 1})
        self.assertFalse(Path(batch['handoff_dir']).exists())
        worker.max_files = 500_000
        with redirect_stdout(io.StringIO()):
            worker.backup_batch(batch, report, {'batches': 1})
        bundle = self.root / 'handoffs' / rid / f'{rid}-cold-01.zip'
        checksum = bundle.with_suffix('.zip.sha256')
        self.assertEqual(checksum.read_text().split()[0], hashlib.sha256(bundle.read_bytes()).hexdigest())
        copied = self.root / 'trong' / rid / 'cold-01' / bundle.name
        self.assertEqual(copied.read_bytes(), bundle.read_bytes())
        with zipfile.ZipFile(bundle) as archive:
            metadata = json.loads(archive.read('run.json'))
            self.assertTrue(metadata['is_mock'])
            self.assertEqual(metadata['scope']['paper_count'], 1)
            self.assertEqual(metadata['snapshot_limits'], {'max_files': 500_000})
            self.assertEqual(metadata['automation'], {'batches': 1})
            self.assertIn('snapshot.tar.gz', archive.namelist())
            self.assertIn(f'benchmark/{rid}-timing.json', archive.namelist())
        snapshot = Path(batch['handoff_dir']) / 'snapshot.tar.gz'
        restored = self.root / 'restored'
        worker.max_files = 1
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                worker._snapshot(['verify', '--archive', str(snapshot)])
            with self.assertRaises(RuntimeError):
                worker._snapshot(['restore', '--archive', str(snapshot), '--destination', str(restored)])
        self.assertFalse(restored.exists())
        worker.max_files = 500_000
        with redirect_stdout(io.StringIO()):
            self.assertEqual(worker._snapshot(['verify', '--archive', str(snapshot)]), 0)
            self.assertEqual(worker._snapshot(['restore', '--archive', str(snapshot), '--destination', str(restored)]), 0)
        self.assertTrue((restored / 'restore_receipt.json').is_file())

    def test_snapshot_file_limit_rejects_invalid_configuration(self):
        snapshot_cli = self.ns.get('run_snapshot_cli')
        self.assertIsNotNone(snapshot_cli, 'Notebook needs a shared snapshot CLI adapter')
        for value in (True, False, 0, -1, 1.5, '500000', None):
            with self.subTest(value=value):
                self.ns['SNAPSHOT_MAX_FILES'] = value
                with self.assertRaises(ValueError):
                    snapshot_cli(['verify', '--archive', str(self.root / 'missing.tar.gz')])

    def test_real_runbook_rejects_mock_restore_bundle(self):
        bundle = self.root / 'mock.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('run.json', json.dumps({'owner': 'trong', 'run_id': 'test', 'is_mock': True}))
        bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.ns.update(RESTORE_BUNDLE=str(bundle), RUN_MODE='resume', TEAM_PATH=self.root, OWNER='trong', RUN_ID='test', MEASUREMENT_ID='resume-01', WORK_ROOT=self.root, COMMIT_SHA='f' * 40, json=json, re=re)
        with self.assertRaisesRegex(ValueError, 'mock'):
            self.execute_cell('restore-bundle')

    def test_restore_continuation_survives_kernel_restart_before_auto_run(self):
        continuation = {'owner': 'trong', 'run_id': 'restore-policy', 'pipeline_commit': 'f' * 40,
                        'mode': 'resume', 'next_measurement': 'resume-04', 'retry_passes': 2,
                        'batches': 4, 'terminal': False}
        bundle = self.root / 'real-identity.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('run.json', json.dumps({'owner': 'trong', 'run_id': 'restore-policy',
                'is_mock': False, 'pipeline_commit': 'f' * 40, 'automation': continuation}))
            archive.writestr('snapshot.tar.gz', b'fixture-for-restore-port')
        bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest())
        def restore_port(args):
            if args[0] == 'restore':
                corpus = Path(args[args.index('--destination') + 1]) / 'corpus'
                corpus.mkdir(parents=True)
                (corpus / 'state.sqlite').write_bytes(b'fixture-sqlite')
        self.ns.update(RESTORE_BUNDLE=str(bundle), RUN_MODE='resume', TEAM_PATH=self.root,
            OWNER='trong', RUN_ID='restore-policy', MEASUREMENT_ID='cold-01', WORK_ROOT=self.root,
            COMMIT_SHA='f' * 40, RESTORED_AUTOMATION=None, run_snapshot_cli=restore_port)
        with redirect_stdout(io.StringIO()):
            self.execute_cell('restore-bundle')
        pointer = json.loads((self.root / 'automation/restore-policy/corpus.json').read_text())
        self.assertEqual(pointer.get('automation'), continuation)
        # Restarting the kernel loses globals, while the VM disk still has restored corpus.
        self.ns['RESTORED_AUTOMATION'] = None
        with redirect_stdout(io.StringIO()):
            self.execute_cell('restore-bundle')
        self.assertEqual(self.ns['RESTORED_AUTOMATION'], continuation)
        self.assertFalse((self.root / 'automation/restore-policy/session.json').exists())

    def test_explicit_pipeline_upgrade_keeps_verified_data_and_records_source_revision(self):
        old, new = 'a' * 40, 'b' * 40
        continuation = {'owner': 'trong', 'run_id': 'upgrade', 'pipeline_commit': old,
                        'mode': 'resume', 'next_measurement': 'resume-04', 'retry_passes': 0}
        bundle = self.root / 'older-core.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('run.json', json.dumps({'owner': 'trong', 'run_id': 'upgrade',
                'is_mock': False, 'pipeline_commit': old, 'automation': continuation}))
            archive.writestr('snapshot.tar.gz', b'verified-by-restore-port')
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        bundle.with_suffix('.zip.sha256').write_text(digest)

        def restore_port(args):
            if args[0] == 'restore':
                corpus = Path(args[args.index('--destination') + 1]) / 'corpus'
                corpus.mkdir(parents=True)
                (corpus / 'state.sqlite').write_bytes(b'original-corpus')

        self.ns.update(RESTORE_BUNDLE=str(bundle), RUN_MODE='resume', TEAM_PATH=self.root,
            OWNER='trong', RUN_ID='upgrade', MEASUREMENT_ID='resume-04', WORK_ROOT=self.root,
            COMMIT_SHA=new, PREVIOUS_PIPELINE_COMMIT=old, RESTORED_AUTOMATION=None,
            run_snapshot_cli=restore_port)
        with redirect_stdout(io.StringIO()):
            self.execute_cell('restore-bundle')
        restored = self.ns['RESTORED_AUTOMATION']
        self.assertEqual(restored['pipeline_commit'], new)
        self.assertEqual(restored['retry_before_attempt'], 0)
        self.assertEqual(restored['retry_passes'], 0)
        self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), digest)
        root = Path(self.ns['CORPUS_PATH']).parent
        receipt = json.loads((root / 'pipeline-upgrade.json').read_text())
        self.assertEqual(receipt['from_commit'], old)
        self.assertEqual(receipt['to_commit'], new)
        self.assertEqual((root / 'corpus/state.sqlite').read_bytes(), b'original-corpus')

    def test_empty_source_revision_cannot_be_accepted_as_default_previous_commit(self):
        bundle = self.root / 'empty-revision.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            archive.writestr('run.json', json.dumps({'owner': 'trong', 'run_id': 'test',
                'is_mock': False, 'pipeline_commit': '', 'automation': {'owner': 'trong',
                'run_id': 'test', 'pipeline_commit': '', 'retry_passes': 0}}))
        bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.ns.update(RESTORE_BUNDLE=str(bundle), RUN_MODE='resume', TEAM_PATH=self.root,
            OWNER='trong', RUN_ID='test', MEASUREMENT_ID='resume-01', WORK_ROOT=self.root,
            COMMIT_SHA='f' * 40, PREVIOUS_PIPELINE_COMMIT='', RESTORED_AUTOMATION=None)
        with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            self.execute_cell('restore-bundle')
        self.assertFalse((self.root / 'restored').exists())

    def test_drive_link_selects_matching_id_even_with_different_shortcut_name(self):
        resolve = self.ns.get('resolve_team_drive')
        self.assertIsNotNone(resolve, 'Notebook must resolve the configured shared folder link')
        mount = self.root / 'drive'
        target = mount / '.shortcut-targets-by-id/team-folder-id/Renamed team folder'
        target.mkdir(parents=True)
        unrelated = mount / 'MyDrive/arXiv-data'
        unrelated.mkdir(parents=True)
        selected = resolve('https://drive.google.com/drive/folders/team-folder-id?usp=drive_link',
                           str(unrelated), mount_root=mount)
        self.assertEqual(selected, target.resolve())

    def test_drive_link_keeps_existing_explicit_path_for_folder_owner(self):
        resolve = self.ns.get('resolve_team_drive')
        self.assertIsNotNone(resolve)
        mount = self.root / 'drive'
        direct = mount / 'MyDrive/arXiv-data'
        direct.mkdir(parents=True)
        self.assertEqual(resolve('https://drive.google.com/drive/folders/team-folder-id',
                                 str(direct), mount_root=mount), direct.resolve())

    def test_missing_shared_folder_reports_link_without_creating_duplicate(self):
        resolve = self.ns.get('resolve_team_drive')
        self.assertIsNotNone(resolve)
        mount = self.root / 'drive'
        (mount / 'MyDrive').mkdir(parents=True)
        link = 'https://drive.google.com/drive/folders/team-folder-id'
        direct = mount / 'MyDrive/arXiv-data'
        with self.assertRaisesRegex(FileNotFoundError, 'team-folder-id'):
            resolve(link, str(direct), mount_root=mount)
        self.assertFalse(direct.exists())

    def test_drive_link_rejects_outside_mount_and_wrong_shortcut_target(self):
        resolve = self.ns.get('resolve_team_drive')
        self.assertIsNotNone(resolve)
        mount = self.root / 'drive'
        wrong = mount / '.shortcut-targets-by-id/wrong-id/arXiv-data'
        wrong.mkdir(parents=True)
        link = 'https://drive.google.com/drive/folders/team-folder-id'
        for path in (self.root, wrong):
            with self.subTest(path=path), self.assertRaises(ValueError):
                resolve(link, str(path), mount_root=mount)

    def test_drive_cell_uses_shared_link_and_checks_write_access(self):
        from types import ModuleType, SimpleNamespace
        from unittest.mock import Mock
        mount = self.root / 'mounted-drive'
        target = mount / '.shortcut-targets-by-id/team-folder-id/Shared data'
        target.mkdir(parents=True)
        link = 'https://drive.google.com/drive/folders/team-folder-id'
        resolver = self.ns['resolve_team_drive']
        self.ns.update(TEAM_DRIVE_FOLDER_URL=link, TEAM_DRIVE_DIR=str(mount / 'MyDrive/missing'), OWNER='trong')
        self.ns['resolve_team_drive'] = lambda url, path: resolver(url, path, mount_root=mount)
        google = ModuleType('google')
        colab = ModuleType('google.colab')
        colab.drive = SimpleNamespace(mount=Mock())
        google.colab = colab
        with patch.dict(sys.modules, {'google': google, 'google.colab': colab}), redirect_stdout(io.StringIO()):
            self.execute_cell('drive-setup')
        colab.drive.mount.assert_called_once_with('/content/drive')
        self.assertEqual(self.ns['TEAM_PATH'], target.resolve())
        self.assertEqual(self.ns['TEAM_DRIVE_DIR'], str(target.resolve()))
        self.assertEqual(list(target.iterdir()), [])  # Write probe is removed after testing access.

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
