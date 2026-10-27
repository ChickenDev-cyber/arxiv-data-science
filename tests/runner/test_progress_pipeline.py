"""Exercise progress through real orchestration and temporary publications."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from arxiv_pipeline.config import ASSIGNED_RANGES
from arxiv_pipeline.examples import result
from arxiv_pipeline.runner.cli import main
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.service import run_pipeline


class RecordingProgress:
    def __init__(self):
        self.messages = []
        self.updates = []

    def __call__(self, message):
        self.messages.append(message)

    def update(self, stage, completed, total, *, unit):
        self.updates.append((stage, completed, total, unit))

    def counts(self, stage):
        return [(n, total, unit) for name, n, total, unit in self.updates if name == stage]


class PipelineProgressTests(unittest.TestCase):
    def test_all_owners_count_ids_and_papers_instead_of_source_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            owners = tuple(c['owner'] for c in ASSIGNED_RANGES)
            options = RunOptions('bars', Path(tmp), owners, True)
            progress = RecordingProgress()
            outcome = run_pipeline(options, make_mock_modules(options), progress=progress)
            self.assertEqual(outcome.exit_code, 0)
            self.assertIn((50, 50, 'ID'), progress.counts('Discovery'))
            self.assertEqual(progress.counts('Sources'), [(n, 5, 'bài') for n in range(6)])
            self.assertEqual(progress.counts('References'), [(n, 5, 'bài') for n in range(6)])
            self.assertEqual(len(list(Path(tmp).glob('raw/*/*/v*/main.tex'))), 10)

    def test_resume_counts_reused_papers_without_executing_new_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions('reuse-bars', Path(tmp), ('trong',), True)
            run_pipeline(options, make_mock_modules(options))
            progress = RecordingProgress()
            outcome = run_pipeline(options, make_mock_modules(options), progress=progress)
            self.assertEqual((outcome.executed_tasks, outcome.reused_tasks), (0, 5))
            self.assertIn((10, 10, 'ID'), progress.counts('Discovery'))
            self.assertEqual(progress.counts('Sources'), [(0, 1, 'bài'), (1, 1, 'bài')])
            self.assertEqual(progress.counts('References'), [(0, 1, 'bài'), (1, 1, 'bài')])

    def test_only_selected_stage_is_displayed(self):
        for stage, shown, hidden in [('sources', 'Sources', 'References'),
                                      ('references', 'References', 'Sources')]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                options = RunOptions('selected-bars', Path(tmp), ('trong',), True)
                progress = RecordingProgress()
                run_pipeline(options, make_mock_modules(options), stage=stage, progress=progress)
                self.assertEqual(progress.counts(shown), [(0, 1, 'bài'), (1, 1, 'bài')])
                self.assertEqual(progress.counts(hidden), [])

    def test_pause_between_versions_does_not_complete_the_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions('pause-bars', Path(tmp), ('trong',), True)
            progress = RecordingProgress()
            outcome = run_pipeline(options, make_mock_modules(options), progress=progress, stop_after_tasks=3)
            self.assertEqual(outcome.exit_code, 2)
            self.assertEqual(progress.counts('Sources'), [(0, 1, 'bài')])
            self.assertEqual(progress.counts('References'), [])
            self.assertTrue((Path(tmp)/'raw/trong/2504-17523/v1/main.tex').exists())
            self.assertFalse((Path(tmp)/'raw/trong/2504-17523/v2/main.tex').exists())

    def test_failed_or_unsupported_sources_finish_progress_without_passing_quality(self):
        for status in ('failed', 'unsupported'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                options = RunOptions('incomplete-bars', Path(tmp), ('trong',), True)
                modules = make_mock_modules(options)

                def sources(task, version, context):
                    data = None if status == 'failed' else {
                        'source_kind': 'pdf_only', 'source_dir': f'raw/trong/2504-17523/v{version}', 'files': []}
                    r = result('trong', 'sources', task['arxiv_id'], data, status, version)
                    r['run_id'] = context['run_id']
                    r['metrics'] = {k: None for k in ('download_bytes', 'extracted_bytes_before_filter',
                        'retained_source_bytes', 'removed_figure_bytes', 'removed_other_bytes')}
                    if status == 'failed':
                        r['error'] = {'code': 'network_error', 'message': 'offline',
                                      'retryable': True, 'http_status': None}
                    else:
                        r['metrics'].update(download_bytes=100, retained_source_bytes=0)
                    return r

                modules = replace(modules, sources=SimpleNamespace(fetch_sources=sources))
                progress = RecordingProgress()
                outcome = run_pipeline(options, modules, progress=progress)
                self.assertEqual(outcome.exit_code, 2)
                self.assertTrue(outcome.validation['valid'])
                self.assertFalse(outcome.quality['valid'])
                self.assertEqual(progress.counts('Sources'), [(0, 1, 'bài'), (1, 1, 'bài')])
                self.assertEqual(progress.counts('References'), [(0, 1, 'bài'), (1, 1, 'bài')])
                self.assertIn('thiếu: 1', progress.messages[-1])

    def test_legacy_discovery_adapter_uses_range_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions('legacy-bars', Path(tmp), ('trong',), True)
            modules = make_mock_modules(options)
            original = modules.discovery
            legacy = SimpleNamespace(discover=lambda config, context: original.discover(config, context))
            progress = RecordingProgress()
            outcome = run_pipeline(options, replace(modules, discovery=legacy), progress=progress)
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(progress.counts('Discovery'), [(0, 1, 'dải'), (1, 1, 'dải')])

    def test_plain_callable_progress_remains_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions('callable-bars', Path(tmp), ('trong',), True)
            messages = []
            outcome = run_pipeline(options, make_mock_modules(options), progress=messages.append)
            self.assertEqual(outcome.exit_code, 0)
            self.assertTrue(any('Tổng kết toàn bộ mẫu' in text for text in messages))

    def test_cli_outputs_bars_to_stderr_and_keeps_json_stdout(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out, \
             redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--mock', '--owner', 'trong', '--run-id', 'cli-bars', '--output-root', tmp]), 0)
            self.assertEqual(len(out.getvalue().splitlines()), 1)
            self.assertEqual(json.loads(out.getvalue())['papers'], 1)
            text = err.getvalue()
            for stage, counts in [('Discovery', '10/10 ID'), ('Sources', '1/1 bài'), ('References', '1/1 bài')]:
                self.assertRegex(text, stage + r' \[[#-]+\] 100% \| ' + counts)
            self.assertNotIn('\r', text)
            self.assertNotIn('\x1b', text)
            out.seek(0); out.truncate(0); err.seek(0); err.truncate(0)
            self.assertEqual(main(['--resume', '--run-id', 'cli-bars', '--output-root', tmp, '--quiet']), 0)
            self.assertEqual(err.getvalue(), '')
            self.assertEqual(json.loads(out.getvalue())['executed_tasks'], 0)

    def test_closed_stderr_does_not_abort_crawl_or_quiet_mode(self):
        for flags in ([], ['--quiet']):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as tmp:
                closed = io.StringIO()
                closed.close()
                with redirect_stderr(closed), redirect_stdout(io.StringIO()) as out:
                    code = main(['--mock', '--owner', 'trong', '--run-id', 'closed-stream',
                                 '--output-root', tmp] + flags)
                self.assertEqual(code, 0)
                summary = json.loads(out.getvalue())
                self.assertEqual(summary['papers'], 1)
                self.assertTrue(summary['validation_valid'])
