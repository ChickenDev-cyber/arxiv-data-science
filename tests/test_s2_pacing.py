"""Runtime pacing must reach real clients and survive the measured handoff."""
import contextlib
from functools import partial
import io
import json
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from arxiv_pipeline.discovery.api import ApiResponse
from arxiv_pipeline.http import RequestScheduler
from arxiv_pipeline.references.api import SemanticScholarClient
from arxiv_pipeline.runner import cli
from arxiv_pipeline.runner.checkpoints import InMemoryCheckpointStore
from arxiv_pipeline.runner.factory import LiveModuleFactory
from arxiv_pipeline.runner.models import RunOptions
from tests.test_colab_automation import REPO, notebook_namespace
from tests.test_request_scheduler import Clock


class S2PacingTests(unittest.TestCase):
    def clients(self, *, replies=None, request_duration=0, **factory_options):
        clock, starts = Clock(), []
        statuses = iter(replies or [(200, {})] * 3)

        def transport(url, timeout, headers):
            starts.append(clock.now)
            clock.sleep(request_duration)
            status, response_headers = next(statuses)
            return ApiResponse(status, response_headers, b'{"data": []}')

        scheduler = RequestScheduler(monotonic=clock.monotonic, sleep=clock.sleep)
        client = partial(SemanticScholarClient, api_key='', transport=transport,
                         monotonic=clock.monotonic, sleep=clock.sleep,
                         wall_clock=lambda: clock.now)
        with patch('arxiv_pipeline.references.api.SemanticScholarClient', client):
            modules = LiveModuleFactory(scheduler=scheduler, **factory_options).build(
                RunOptions('pacing', Path('/tmp/pacing-unused'), ('trong',)), InMemoryCheckpointStore())
        return modules, clock, starts

    def test_factory_delay_is_after_response_completion_and_default_stays_one_second(self):
        for options, expected in [({}, [0, 1.25]), ({'s2_delay_seconds': 2}, [0, 2.25])]:
            with self.subTest(options=options):
                modules, clock, starts = self.clients(request_duration=.25, **options)
                modules.references.reader.references_page('2504.17523', 0, 100)
                modules.references.reader.references_page('2504.17524', 0, 100)
                self.assertEqual(starts, expected)
                # The same arXiv scheduler still reserves starts at least three seconds apart.
                arxiv_starts = []
                for _ in range(2):
                    with modules.references.dates.scheduler.request('arxiv'):
                        arxiv_starts.append(clock.now)
                self.assertEqual(arxiv_starts[1] - arxiv_starts[0], 3)

    def test_new_delay_does_not_shorten_retry_after_or_s2_cooldown(self):
        modules, clock, starts = self.clients(s2_delay_seconds=2,
            replies=[(429, {'Retry-After': '180'}), (200, {}), (200, {})])
        reader = modules.references.reader
        reader.references_page('2504.17523', 0, 100)
        reader.references_page('2504.17524', 0, 100)
        self.assertEqual(starts, [0, 180, 182])
        self.assertEqual(reader.attempts, 3)

    def test_factory_rejects_unsafe_or_non_numeric_delays(self):
        for invalid in (True, False, None, '2', 0, .9, -1, float('inf'), float('nan')):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, 's2_delay_seconds'):
                LiveModuleFactory(s2_delay_seconds=invalid)

    def test_cli_setting_controls_requests_in_the_composed_live_factory(self):
        # Keep the real CLI and factory; replace only runner execution to avoid unrelated live APIs.
        clock, starts = Clock(), []
        def transport(*args):
            starts.append(clock.now)
            return ApiResponse(200, {}, b'{"data": []}')
        real_client = partial(SemanticScholarClient, api_key='', transport=transport,
                              monotonic=clock.monotonic, sleep=clock.sleep)
        def run(runner, options, **kwargs):
            modules = runner.factory.build(options, InMemoryCheckpointStore())
            reader = modules.references.reader
            reader.scheduler = None
            reader.transport = transport
            reader.references_page('2504.17523', 0, 100)
            reader.references_page('2504.17524', 0, 100)
            return SimpleNamespace(exit_code=2, executed_tasks=2, reused_tasks=0, deferred_tasks=0,
                pause_reason='task_limit', scope={'scope': {'paper_count': 1, 'discovery_complete': True}},
                validation={'valid': True}, quality={'valid': False})
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()), \
             patch('arxiv_pipeline.references.api.SemanticScholarClient', real_client), \
             patch.object(cli.PipelineRunner, 'run', run):
            self.assertEqual(cli.main(['--owner', 'trong', '--run-id', 'cli-pacing', '--output-root', tmp,
                                       '--s2-delay-seconds', '2.5']), 2)
            self.assertEqual(starts, [0, 2.5])
            self.assertEqual(len(out.getvalue().splitlines()), 1)
            self.assertTrue(json.loads(out.getvalue())['validation_valid'])

    def test_invalid_cli_delay_stops_before_creating_corpus(self):
        for invalid in ('0', '.9', '-1', 'inf', 'nan'):
            with self.subTest(value=invalid), tempfile.TemporaryDirectory() as tmp, \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                root = Path(tmp)/'corpus'
                self.assertEqual(cli.main(['--mock', '--owner', 'trong', '--run-id', 'invalid-delay',
                    '--output-root', str(root), '--s2-delay-seconds', invalid]), 1)
                self.assertFalse(root.exists())


class NotebookPacingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ns = notebook_namespace()

    def worker(self, **kwargs):
        return self.ns['NotebookBatchWorker'](repo_dir=REPO, env=self.ns['PIPELINE_ENV'],
            work_root=self.root, output_root=self.root/'corpus', drive_root=self.root,
            owner='trong', run_id='mock-pacing', pipeline_commit='f'*40, expected_mock=True,
            initial_args=['--mock', '--owner', 'trong', '--max-candidates', '1', '--max-papers', '1'],
            **kwargs)

    def test_explicit_pacing_survives_execution_and_verified_drive_handoff(self):
        worker = self.worker(s2_delay_seconds=2.0, stop_after_tasks=2, stop_after_seconds=600,
                             references_rate_limit_threshold=2)
        session = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
            pipeline_commit=worker.pipeline_commit, journal_path=self.root/'session.json', initial_mode='pilot')
        with contextlib.redirect_stdout(io.StringIO()):
            result = session.run(max_batches_this_call=1)
        self.assertEqual((result['batches'], result['retry_passes']), (1, 0))
        local = json.loads((self.root/'measurements/mock-pacing/cold-01/invocation.json').read_text())
        remote = self.root/'trong/mock-pacing/cold-01'
        saved = json.loads((remote/'run.json').read_text())
        self.assertTrue((remote/'mock-pacing-cold-01.zip.sha256').is_file())
        self.assertEqual(saved['command'], local['command'])
        args = shlex.split(saved['command'][-1])
        for flag, value in [('--s2-delay-seconds', '2.0'), ('--stop-after-tasks', '2'),
                            ('--stop-after-seconds', '600'), ('--retry-before-attempt', '0')]:
            self.assertEqual(args[args.index(flag)+1], value)

    def test_probe_is_separated_from_measured_requests_by_configured_delay(self):
        worker = self.worker(s2_delay_seconds=2.5, require_s2_key=True)
        worker.expected_mock = False
        worker.env['S2_API_KEY'] = 'offline-test-key'
        # The subprocess boundary supplies a successful single probe; no real key or HTTP.
        with patch.object(worker, '_capture', return_value={'ok': True, 'http_status': 200}), \
             patch('time.sleep') as sleep, contextlib.redirect_stdout(io.StringIO()):
            worker.preflight()
        self.assertEqual([call.args for call in sleep.call_args_list], [(2.5,)])
        self.assertFalse((self.root/'measurements').exists())

    def test_old_core_is_rejected_before_pending_batch_when_delay_is_configured(self):
        worker = self.worker(s2_delay_seconds=2)
        package = self.root/'old-core/arxiv_pipeline/runner'
        package.mkdir(parents=True)
        (package.parent/'__init__.py').touch()
        (package/'__init__.py').touch()
        (package/'__main__.py').write_text('print("--retry-before-attempt")')
        worker.repo_dir = self.root
        worker.env['PYTHONPATH'] = str(package.parent.parent)
        session = self.ns['AutoCrawlSession'](worker, owner=worker.owner, run_id=worker.run_id,
            pipeline_commit=worker.pipeline_commit, journal_path=self.root/'session.json', initial_mode='pilot')
        with self.assertRaisesRegex(RuntimeError, 'COMMIT_SHA'), contextlib.redirect_stdout(io.StringIO()):
            session.run(max_batches_this_call=1)
        self.assertFalse((self.root/'measurements').exists())
        if (self.root/'session.json').exists():
            self.assertIsNone(json.loads((self.root/'session.json').read_text())['pending'])

    def test_invalid_notebook_delay_is_rejected_before_measurements(self):
        for invalid in (True, '2', 0, .9, float('nan'), float('inf')):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, 's2_delay_seconds'):
                self.worker(s2_delay_seconds=invalid)
        self.assertFalse((self.root/'measurements').exists())
