"""Historical producers must survive repeated code upgrades without blocking backup."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import runpy
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests.test_colab_automation import REPO, notebook_namespace


class ArchiveRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = notebook_namespace()

    def worker(self, commit='c', previous='b'):
        return self.ns['NotebookBatchWorker'](repo_dir=REPO, env=self.ns['PIPELINE_ENV'],
            work_root=self.root/'work', output_root=self.root/'corpus', drive_root=self.root,
            owner='trong', run_id='archive-mock', pipeline_commit=commit*40,
            historical_pipeline_commit=previous*40, expected_mock=True, stop_after_tasks=2,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'])

    def archive(self, worker, measurement, commit, **overrides):
        folder = self.root/'trong/archive-mock'/measurement
        folder.mkdir(parents=True, exist_ok=True)
        meta = {'owner': 'trong', 'run_id': worker.run_id, 'pipeline_commit': commit,
                'is_mock': True, 'measurement_id': measurement, **overrides}
        documents = {'run.json': json.dumps(meta).encode(),
                     'benchmark/archive-mock.json': b'{"wall_seconds": 7}',
                     'benchmark/archive-mock-timing.json': b'{"wall_seconds": 7}',
                     'statistics/archive-mock.json': b'{"schema_version": "1.2"}'}
        bundle = folder/f'{worker.run_id}-{measurement}.zip'
        with zipfile.ZipFile(bundle, 'w') as archive:
            for name, content in documents.items():
                archive.writestr(name, content)
        bundle.with_suffix('.zip.sha256').write_text(hashlib.sha256(bundle.read_bytes()).hexdigest())
        return folder, bundle, documents

    def test_three_producer_generations_keep_two_zip_and_all_original_measurements(self):
        worker = self.worker()
        archives = [self.archive(worker, name, commit*40) for name, commit in
                    [('resume-29', 'a'), ('resume-30', 'b'), ('resume-31', 'c')]]
        worker._prune_snapshots()
        self.assertFalse(archives[0][1].exists())
        self.assertTrue(all(bundle.exists() for _, bundle, _ in archives[1:]))
        for folder, _, documents in archives:
            for name, content in documents.items():
                self.assertEqual((folder/name).read_bytes(), content)
        # Archival validation never authorizes this producer as a new active batch.
        with self.assertRaises(ValueError):
            worker._identity(json.loads(archives[0][2]['run.json']))

    def test_archive_identity_rejects_wrong_run_mode_measurement_and_malformed_commit(self):
        mutations = [{'owner': 'khang'}, {'run_id': 'another-run'}, {'is_mock': False},
                     {'is_mock': 1}, {'measurement_id': 'resume-99'}, {'pipeline_commit': 'invalid'}]
        for changes in mutations:
            with self.subTest(changes=changes):
                worker = self.worker()
                folder, bundle, _ = self.archive(worker, 'resume-29', 'c'*40, **changes)
                try:
                    with self.assertRaises(ValueError):
                        worker._prune_snapshots()
                    self.assertTrue(bundle.exists())
                finally:
                    shutil.rmtree(folder)

    def test_local_archive_cleanup_accepts_older_producers_and_preserves_corpus(self):
        worker = self.worker()
        for name, commit in [('resume-29', 'a'), ('resume-30', 'b'), ('resume-31', 'c')]:
            folder = worker.work_root/'handoffs'/worker.run_id/name
            folder.mkdir(parents=True)
            (folder/'run.json').write_text(json.dumps({'owner': 'trong', 'run_id': worker.run_id,
                'pipeline_commit': commit*40, 'measurement_id': name, 'is_mock': True}))
            (folder/'snapshot.tar.gz').write_bytes(b'local snapshot')
        worker.output_root.mkdir()
        (worker.output_root/'state.sqlite').write_bytes(b'corpus marker')
        worker._prune_snapshots()
        self.assertFalse((worker.work_root/'handoffs'/worker.run_id/'resume-29/snapshot.tar.gz').exists())
        self.assertTrue((worker.work_root/'handoffs'/worker.run_id/'resume-30/snapshot.tar.gz').exists())
        self.assertEqual((worker.output_root/'state.sqlite').read_bytes(), b'corpus marker')

    def test_real_backup_after_two_upgrades_preserves_first_producer_metrics(self):
        first, second, third = self.worker('a', 'a'), self.worker('b', 'a'), self.worker('c', 'b')
        original = None
        with contextlib.redirect_stdout(io.StringIO()):
            for worker, mode, measurement in [(first, 'pilot', 'cold-01'),
                                               (second, 'resume', 'resume-01'),
                                               (third, 'resume', 'resume-02')]:
                batch = worker.make_batch(mode, measurement)
                worker.execute_batch(batch)
                worker.backup_batch(batch, worker.inspect_batch(batch), {'retry_passes': 0})
                if original is None:
                    original = (self.root/'trong/archive-mock/cold-01/run.json').read_bytes()
        self.assertEqual((self.root/'trong/archive-mock/cold-01/run.json').read_bytes(), original)
        self.assertFalse((self.root/'trong/archive-mock/cold-01/archive-mock-cold-01.zip').exists())
        for name in ['resume-01', 'resume-02']:
            path = self.root/'trong/archive-mock'/name/f'archive-mock-{name}.zip'
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             path.with_suffix('.zip.sha256').read_text().split()[0])

    def test_pending_backup_rescue_keeps_old_zip_without_recrawling_or_duplicate_counters(self):
        script = REPO/'src/colab_pending_backup_recovery.py'
        self.assertTrue(script.is_file(), 'Pending-backup recovery cell has not been prepared')
        recover = runpy.run_path(str(script), run_name='recovery_test')['recover_pending_backup']
        with contextlib.redirect_stdout(io.StringIO()):
            for worker, mode, name in [(self.worker('a', 'a'), 'pilot', 'cold-01'),
                                      (self.worker('b', 'a'), 'resume', 'resume-01')]:
                batch = worker.make_batch(mode, name)
                worker.execute_batch(batch)
                worker.backup_batch(batch, worker.inspect_batch(batch), {'retry_passes': 0})
        worker = self.worker('c', 'b')
        session = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
            pipeline_commit=worker.pipeline_commit, journal_path=self.root/'session.json',
            resume_state={'owner': worker.owner, 'run_id': worker.run_id,
                'pipeline_commit': worker.pipeline_commit, 'mode': 'resume', 'next_measurement': 'resume-02',
                'batches': 2, 'retry_passes': 0, 'retry_before_attempt': 0, 'terminal': False})
        with contextlib.redirect_stdout(io.StringIO()), patch.object(worker, '_prune_snapshots',
             side_effect=ValueError('old producer identity')):
            with self.assertRaisesRegex(ValueError, 'old producer'):
                session.run(max_batches_this_call=1)
        before = (worker.output_root/f'logs/{worker.run_id}/events.jsonl').read_bytes()
        expected_after = json.loads(session.journal_path.read_text())['pending']['after']
        with contextlib.redirect_stdout(io.StringIO()), \
             patch.object(worker, 'execute_batch', side_effect=AssertionError('Must not crawl again')), \
             patch.object(worker, 'preflight', side_effect=AssertionError('Must not probe')):
            result = recover({'session': session, 'worker': worker}, expected_measurement='resume-02')
        self.assertEqual(result['batches'], 3)
        self.assertEqual(result['retry_passes'], expected_after['retry_passes'])
        self.assertEqual(result['retry_before_attempt'], expected_after['retry_before_attempt'])
        saved = json.loads(session.journal_path.read_text())
        self.assertIsNone(saved['pending'])
        self.assertEqual((worker.output_root/f'logs/{worker.run_id}/events.jsonl').read_bytes(), before)
        remote = worker.drive_root/'trong/archive-mock'
        self.assertEqual(len(list(remote.glob('*/*.zip'))), 3)
        self.assertTrue((remote/'resume-02/archive-mock-resume-02.zip.sha256').is_file())
        with self.assertRaisesRegex(RuntimeError, 'pending|chờ'), contextlib.redirect_stdout(io.StringIO()):
            recover({'session': session, 'worker': worker}, expected_measurement='resume-02')
