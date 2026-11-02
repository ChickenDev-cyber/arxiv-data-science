"""Offline tests for the real notebook batch worker; no Colab auth or live requests."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parents[1]


def notebook_namespace():
    notebook = json.loads((REPO / 'src/colab_baseline_smoke.ipynb').read_text())
    ns = {'__name__': '__main__', 'REPO_DIR': REPO,
          'PIPELINE_ENV': {**os.environ, 'PYTHONPATH': str(REPO / 'src')}}
    for cell_id in ('runbook-helpers', 'automation-worker'):
        cell = next((c for c in notebook['cells'] if c.get('id') == cell_id), None)
        if cell is None:
            return ns
        exec(''.join(cell['source']), ns)
    return ns


class NotebookAutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ns = notebook_namespace()

    def tearDown(self):
        self.temp.cleanup()

    def worker(self, *, max_files=500_000, drive=True):
        worker_type = self.ns.get('NotebookBatchWorker')
        self.assertIsNotNone(worker_type, 'Notebook needs its actual automated batch worker')
        drive_root = self.root / 'team' if drive else None
        if drive_root is not None:
            drive_root.mkdir(exist_ok=True)
        return worker_type(
            repo_dir=REPO, env=self.ns['PIPELINE_ENV'], work_root=self.root,
            output_root=self.root / 'corpus', drive_root=drive_root,
            owner='trong', run_id='auto-mock', pipeline_commit='f' * 40,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'],
            stop_after_tasks=2, max_files=max_files, expected_mock=True,
            cli=self.ns['run_cli'])

    def test_worker_command_carries_fixed_pass_boundary_and_reports_attempt_coverage(self):
        import shlex
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01', retry_before_attempt=0)
        runner = shlex.split(batch['command'][-1])
        self.assertEqual(runner[runner.index('--retry-before-attempt') + 1], '0')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            first = worker.inspect_batch(batch)
        self.assertEqual(first['processed_in_pass'], 2)
        self.assertEqual(first['max_attempt_id'], 2)
        second = worker.make_batch('resume', 'resume-01', retry_before_attempt=0)
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(second)
            report = worker.inspect_batch(second)
        self.assertEqual(report['processed_in_pass'], 4)
        self.assertNotEqual(first['progress_key'], report['progress_key'])

    def test_worker_refuses_core_without_pass_boundary_capability_before_measuring(self):
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01')
        worker.env = {**worker.env, 'PYTHONPATH': str(self.root / 'old-core')}
        package = self.root / 'old-core/arxiv_pipeline/runner'
        package.mkdir(parents=True)
        (package.parent / '__init__.py').touch()
        (package / '__init__.py').touch()
        (package / '__main__.py').write_text('print("old runner help without pass support")')
        # Avoid resolving the repository's real core ahead of the old fixture package.
        worker.repo_dir = self.root
        with self.assertRaisesRegex(RuntimeError, 'COMMIT_SHA'):
            worker.execute_batch(batch)
        self.assertFalse(Path(batch['measurement_root']).exists())

    def test_failed_source_is_retried_only_after_first_traversal_with_verified_backups(self):
        import shlex
        worker = self.worker()
        # Run the real CLI in its real measured subprocess with one offline failing adapter.
        adapter = '''import sys
from arxiv_pipeline.runner import cli
from arxiv_pipeline.runner.factory import MockModuleFactory
from arxiv_pipeline.runner.models import ModuleSet
from arxiv_pipeline.examples import result
class Factory(MockModuleFactory):
 def build(self, options, checkpoints):
  modules=super().build(options, checkpoints)
  original=modules.sources
  class Sources:
   def fetch_sources(self, task, version, context):
    if version != 2: return original.fetch_sources(task, version, context)
    r=result(task['owner'],'sources',task['arxiv_id'],None,'failed',version)
    r['run_id']=options.run_id
    r['error']={'code':'network_error','message':'synthetic timeout','retryable':True,'http_status':None}
    r['metrics']={k:None for k in ('download_bytes','extracted_bytes_before_filter','retained_source_bytes','removed_figure_bytes','removed_other_bytes')}
    return r
  return ModuleSet(modules.discovery,modules.metadata,Sources(),modules.references)
cli.MockModuleFactory=Factory
raise SystemExit(cli.main(sys.argv[1:]))
'''
        real_cli = worker.cli

        def measured_with_failure(args, **kwargs):
            args = list(args)
            if 'arxiv_pipeline.benchmark' in args:
                index = args.index('--cmd') + 1
                runner = shlex.split(args[index])
                args[index] = shlex.join([runner[0], '-c', adapter, *runner[3:]])
            return real_cli(args, **kwargs)

        worker.cli = measured_with_failure
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
                pipeline_commit=worker.pipeline_commit, journal_path=self.root / 'session.json',
                initial_mode='pilot', max_batches=10).run()
        self.assertEqual(result['stop_reason'], 'retry_exhausted')
        self.assertEqual(result['retry_passes'], 2)
        events = [json.loads(line) for line in
                  (worker.output_root / 'logs/auto-mock/events.jsonl').read_text().splitlines()]
        operations = [(event['step'], event.get('version')) for event in events]
        self.assertEqual(operations, [('discovery', None), ('metadata', None), ('sources', 1),
                                     ('sources', 2), ('references', None), ('sources', 2), ('sources', 2)])
        folders = sorted((worker.drive_root / 'trong/auto-mock').iterdir())
        for folder in folders:
            metadata = json.loads((folder / 'run.json').read_text())
            self.assertIn('retry_before_attempt', metadata['automation'])
            self.assertTrue((folder / 'benchmark/auto-mock.json').is_file())
            for bundle in folder.glob('*.zip'):
                self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(),
                                 bundle.with_suffix('.zip.sha256').read_text().split()[0])
        self.assertEqual(len(list((worker.drive_root / 'trong/auto-mock').glob('*/*.zip'))), 2)

    def test_pipeline_upgrade_preserves_previous_revision_metrics_during_retention(self):
        worker = self.worker()
        first = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(first)
            report = worker.inspect_batch(first)
            worker.backup_batch(first, report, {'retry_passes': 0, 'retry_before_attempt': 0})
        old_json = worker.drive_root / 'trong/auto-mock/cold-01/run.json'
        expected = old_json.read_bytes()
        upgraded = type(worker)(repo_dir=worker.repo_dir, env=worker.env, work_root=worker.work_root,
            output_root=worker.output_root, drive_root=worker.drive_root, owner=worker.owner,
            run_id=worker.run_id, pipeline_commit='e' * 40, initial_args=worker.initial_args,
            expected_mock=True, stop_after_tasks=2, cli=worker.cli,
            historical_pipeline_commit=worker.pipeline_commit)
        second = upgraded.make_batch('resume', 'resume-01')
        with contextlib.redirect_stdout(io.StringIO()):
            upgraded.execute_batch(second)
            report = upgraded.inspect_batch(second)
            upgraded.backup_batch(second, report, {'retry_passes': 0, 'retry_before_attempt': 0})
        self.assertEqual(old_json.read_bytes(), expected)
        self.assertEqual(len(list((worker.drive_root / 'trong/auto-mock').glob('*/*.zip'))), 2)

    def test_real_mock_batches_backup_before_resume_and_preserve_metrics(self):
        worker = self.worker()
        session_type = self.ns.get('AutoCrawlSession')
        self.assertIsNotNone(session_type, 'Notebook needs its actual automatic session controller')
        with contextlib.redirect_stdout(io.StringIO()):
            result = session_type(worker, owner='trong', run_id='auto-mock',
                pipeline_commit='f' * 40, journal_path=self.root / 'session.json',
                initial_mode='pilot').run()
        self.assertTrue(result['terminal'])
        folders = sorted((self.root / 'team/trong/auto-mock').iterdir())
        self.assertGreaterEqual(len(folders), 2)
        self.assertEqual(folders[0].name, 'cold-01')
        for folder in folders:
            for bundle in folder.glob('*.zip'):
                expected = bundle.with_suffix('.zip.sha256').read_text().split()[0]
                self.assertEqual(expected, hashlib.sha256(bundle.read_bytes()).hexdigest())
            self.assertTrue((folder / 'benchmark/auto-mock.json').is_file())
        self.assertEqual(len(list((self.root / 'team/trong/auto-mock').glob('*/*.zip'))), 2)
        cold = self.root / 'measurements/auto-mock/cold-01/benchmark/auto-mock.json'
        resume = self.root / 'measurements/auto-mock/resume-01/benchmark/auto-mock.json'
        self.assertEqual(json.loads(cold.read_text())['cache_mode'], 'cold')
        self.assertEqual(json.loads(resume.read_text())['cache_mode'], 'mixed')
        self.assertTrue(json.loads(resume.read_text())['is_mock'])
        last_batch = worker.make_batch('resume', folders[-1].name)
        with contextlib.redirect_stdout(io.StringIO()):
            report = worker.inspect_batch(last_batch)
        self.assertEqual(report['summary']['papers_complete'], 1)
        refs = json.loads(next((self.root / 'corpus/raw').rglob('raw_references.json')).read_text())
        self.assertEqual(worker._run_state().get('raw_reference_entries'), len(refs))
        # Restore a retained ZIP exactly as a new VM would, including terminal policy.
        latest = folders[-1]
        with zipfile.ZipFile(next(latest.glob('*.zip'))) as archive:
            continuation = json.loads(archive.read('run.json'))['automation']
            snapshot = self.root / 'incoming.tar.gz'
            snapshot.write_bytes(archive.read('snapshot.tar.gz'))
        with contextlib.redirect_stdout(io.StringIO()):
            worker._snapshot(['restore', '--archive', str(snapshot), '--destination', str(self.root / 'restored')])
        fresh = type(worker)(repo_dir=REPO, env=worker.env, work_root=self.root / 'fresh-vm',
            output_root=self.root / 'restored/corpus', drive_root=worker.drive_root,
            owner=worker.owner, run_id=worker.run_id, pipeline_commit=worker.pipeline_commit,
            initial_args=worker.initial_args, expected_mock=True, cli=worker.cli, restored_state=continuation)
        with contextlib.redirect_stdout(io.StringIO()):
            restored_result = session_type(fresh, owner=worker.owner, run_id=worker.run_id,
                pipeline_commit=worker.pipeline_commit, journal_path=self.root / 'fresh-vm/session.json',
                initial_mode='resume', resume_state=continuation).run()
        self.assertEqual(restored_result['retry_passes'], result['retry_passes'])
        self.assertEqual(restored_result['summary'], result['summary'])
        self.assertTrue(restored_result['terminal'])
        self.assertFalse((self.root / 'fresh-vm/measurements').exists())

    def test_verified_local_package_is_reused_when_drive_copy_is_retried(self):
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
        after = {'retry_passes': 0, 'batches': 1}
        original_copy = self.ns['copy_verified']
        def unavailable(*args, **kwargs):
            raise OSError('Drive unavailable')
        self.ns['copy_verified'] = unavailable
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(OSError):
            worker.backup_batch(batch, report, after)
        bundle = self.root / 'handoffs/auto-mock/auto-mock-cold-01.zip'
        original_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
        events = (self.root / 'corpus/logs/auto-mock/events.jsonl').read_bytes()
        self.ns['copy_verified'] = original_copy
        with contextlib.redirect_stdout(io.StringIO()):
            worker.backup_batch(batch, report, after)
        self.assertEqual(hashlib.sha256(bundle.read_bytes()).hexdigest(), original_hash)
        self.assertEqual((self.root / 'corpus/logs/auto-mock/events.jsonl').read_bytes(), events)
        self.assertTrue((self.root / 'team/trong/auto-mock/cold-01'/bundle.name).is_file())

    def test_structural_failure_stops_before_backup(self):
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
        # First batch pauses before sources, so damage the accepted metadata instead.
        metadata = next((self.root / 'corpus/raw').rglob('metadata.json'))
        metadata.write_text('{}')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises((RuntimeError, ValueError)):
            worker.inspect_batch(batch)
        self.assertFalse((self.root / 'team/trong/auto-mock').exists())

    def test_auto_worker_requires_backup_destination(self):
        worker_type = self.ns.get('NotebookBatchWorker')
        self.assertIsNotNone(worker_type)
        with self.assertRaises(ValueError):
            self.worker(drive=False)

    def test_new_vm_requires_restore_when_drive_already_has_this_run(self):
        worker = self.worker()
        remote = self.root / 'team/trong/auto-mock/cold-01'
        remote.mkdir(parents=True)
        (remote / 'auto-mock-cold-01.zip').write_bytes(b'previous-backup')
        (remote / 'auto-mock-cold-01.zip.sha256').write_text('0' * 64)
        with self.assertRaisesRegex(ValueError, 'restore|khôi phục|Khôi phục'):
            worker.adopt_existing()
        self.assertFalse((self.root / 'corpus').exists())

    def test_existing_manual_batch_adopts_a_new_measurement_without_rerunning(self):
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
            worker.backup_batch(batch, report, {})
            state = worker.adopt_existing()
        self.assertEqual(state['mode'], 'resume')
        self.assertEqual(state.get('next_measurement'), 'resume-01')

    def test_restored_manual_snapshot_uses_resume_id_in_new_vm(self):
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
            worker.backup_batch(batch, report, {})
            archive = Path(batch['handoff_dir']) / 'snapshot.tar.gz'
            restored = self.root / 'new-vm/restored'
            worker._snapshot(['restore', '--archive', str(archive), '--destination', str(restored)])
        resumed = self.ns['NotebookBatchWorker'](
            repo_dir=REPO, env=self.ns['PIPELINE_ENV'], work_root=self.root / 'new-vm',
            output_root=restored / 'corpus', drive_root=self.root / 'team',
            owner='trong', run_id='auto-mock', pipeline_commit='f' * 40,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'],
            stop_after_tasks=2, expected_mock=True, cli=self.ns['run_cli'])
        with contextlib.redirect_stdout(io.StringIO()):
            state = resumed.adopt_existing()
        self.assertEqual(state.get('next_measurement'), 'resume-01')

    def test_only_two_snapshots_remain_but_every_measurement_json_is_preserved(self):
        worker = self.worker()
        with contextlib.redirect_stdout(io.StringIO()):
            for index in range(4):
                measurement = 'cold-01' if index == 0 else f'resume-{index:02d}'
                batch = worker.make_batch('pilot' if index == 0 else 'resume', measurement)
                worker.execute_batch(batch)
                report = worker.inspect_batch(batch)
                worker.backup_batch(batch, report, {'batches': index + 1})
        remote = self.root / 'team/trong/auto-mock'
        self.assertEqual(sorted(p.parent.name for p in remote.glob('*/*.zip')), ['resume-02', 'resume-03'])
        for measurement in ('cold-01', 'resume-01', 'resume-02', 'resume-03'):
            folder = remote / measurement
            for name in ('run.json', 'benchmark/auto-mock.json', 'benchmark/auto-mock-timing.json', 'statistics/auto-mock.json'):
                self.assertTrue((folder / name).is_file(), (measurement, name))
        self.assertEqual(len(list((self.root / 'handoffs/auto-mock').glob('*.zip'))), 2)
        self.assertEqual(len(list((self.root / 'handoffs/auto-mock').glob('*/snapshot.tar.gz'))), 2)
        self.assertTrue((self.root / 'corpus/state.sqlite').is_file())

    def test_stale_local_measurement_cannot_replace_newer_drive_progress(self):
        worker = self.worker()
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
            worker.backup_batch(batch, report, {})
        remote = self.root / 'team/trong/auto-mock/resume-05'
        remote.mkdir()
        (remote / 'auto-mock-resume-05.zip').write_bytes(b'newer-backup')
        (remote / 'auto-mock-resume-05.zip.sha256').write_text('0' * 64)
        with self.assertRaisesRegex(ValueError, 'mới hơn|stale'):
            worker.execute_batch(worker.make_batch('resume', 'resume-06'))
        self.assertFalse((self.root / 'measurements/auto-mock/resume-06').exists())

    def test_corrupt_recent_snapshot_prevents_deleting_an_older_good_backup(self):
        worker = self.worker()
        with contextlib.redirect_stdout(io.StringIO()):
            for index in range(2):
                measurement = 'cold-01' if index == 0 else 'resume-01'
                batch = worker.make_batch('pilot' if index == 0 else 'resume', measurement)
                worker.execute_batch(batch)
                worker.backup_batch(batch, worker.inspect_batch(batch), {'batches': index + 1})
        remote = self.root / 'team/trong/auto-mock'
        (remote / 'resume-01/auto-mock-resume-01.zip').write_bytes(b'corrupt recent backup')
        batch = worker.make_batch('resume', 'resume-02')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            report = worker.inspect_batch(batch)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                worker.backup_batch(batch, report, {'batches': 3})
        self.assertTrue((remote / 'cold-01/auto-mock-cold-01.zip').is_file())

    def test_old_measurement_cannot_be_recomputed_from_an_advanced_corpus(self):
        worker = self.worker()
        cold = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(cold)
            worker.inspect_batch(cold)
        stats = Path(cold['statistics_dir']) / 'auto-mock.json'
        previous = stats.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(worker.make_batch('resume', 'resume-01'))
            with self.assertRaises(ValueError):
                worker.inspect_batch(worker.make_batch('pilot', 'cold-01'))
        self.assertEqual(stats.read_bytes(), previous)


    def test_leftover_handoff_reserves_measurement_and_blocks_execution(self):
        worker = self.worker()
        handoff = self.root / 'handoffs/auto-mock/cold-01'
        handoff.mkdir(parents=True)
        (handoff / 'run.json').write_text('{}')
        self.assertTrue(worker.measurement_exists('cold-01'))
        with self.assertRaises(FileExistsError):
            worker.execute_batch(worker.make_batch('pilot', 'cold-01'))
        self.assertFalse((self.root / 'corpus').exists())

    def test_restored_continuation_is_not_discarded_by_backend_adoption(self):
        worker = self.worker()
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(worker.make_batch('pilot', 'cold-01'))
        expected = {'owner': 'trong', 'run_id': 'auto-mock', 'pipeline_commit': 'f' * 40,
                    'mode': 'resume', 'next_measurement': 'resume-09', 'retry_passes': 2,
                    'batches': 8, 'terminal': True, 'stop_reason': 'retry_exhausted'}
        worker.restored_state = expected
        self.assertEqual(worker.adopt_existing(), expected)

    def test_restored_manual_finished_run_does_not_schedule_another_crawl(self):
        worker = self.worker()
        worker.stop_after_tasks = 50
        batch = worker.make_batch('pilot', 'cold-01')
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(batch)
            worker.backup_batch(batch, worker.inspect_batch(batch), {})
            restored = self.root / 'restored-complete'
            worker._snapshot(['restore', '--archive', str(self.root / 'handoffs/auto-mock/cold-01/snapshot.tar.gz'),
                              '--destination', str(restored)])
        resumed = type(worker)(repo_dir=REPO, env=worker.env, work_root=self.root / 'fresh-vm',
            output_root=restored / 'corpus', drive_root=worker.drive_root,
            owner=worker.owner, run_id=worker.run_id, pipeline_commit=worker.pipeline_commit,
            initial_args=worker.initial_args, expected_mock=True, cli=worker.cli)
        with contextlib.redirect_stdout(io.StringIO()):
            state = resumed.adopt_existing()
        self.assertTrue(state.get('terminal'), state)
        self.assertEqual(state['stop_reason'], 'finished_with_issues')  # Limited pilot scope remains limited.
        self.assertFalse((self.root / 'fresh-vm/measurements').exists())

    def test_unrelated_measurement_subdirectory_does_not_break_adoption(self):
        worker = self.worker()
        with contextlib.redirect_stdout(io.StringIO()):
            worker.execute_batch(worker.make_batch('pilot', 'cold-01'))
        import shutil
        shutil.rmtree(self.root / 'measurements/auto-mock/cold-01')
        (self.root / 'measurements/auto-mock/.ipynb_checkpoints').mkdir()
        state = worker.adopt_existing()
        self.assertEqual(state['next_measurement'], 'resume-01')


if __name__ == '__main__':
    unittest.main()
