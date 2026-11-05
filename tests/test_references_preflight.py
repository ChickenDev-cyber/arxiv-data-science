"""Probe the real references protocol through offline transports, without crawl writes."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.references.preflight import check_access, main


class PreflightTests(unittest.TestCase):
    def test_cold_start_cli_preserves_retry_after_without_creating_a_corpus(self):
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        retry_date = format_datetime(datetime.now(timezone.utc)+timedelta(seconds=3600), usegmt=True)
        for retry_after in ('3600', retry_date):
            with self.subTest(retry_after=retry_after), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)/'corpus'
                def limited(*args):
                    return ApiResponse(429, {'Retry-After': retry_after}, b'')
                with patch.dict(os.environ, {'S2_API_KEY': 'test-secret'}), \
                     patch('arxiv_pipeline.references.api._semantic_scholar_transport', limited), \
                     patch('socket.create_connection', side_effect=AssertionError('No real network')), \
                     contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(main(['--arxiv-id', '2504.17523', '--output-root', str(root),
                                           '--run-id', 'cold-probe']), 2)
                result = json.loads(out.getvalue())
                self.assertEqual(result['http_status'], 429)
                self.assertGreaterEqual(result.get('wait_seconds', 0), 3590)
                self.assertFalse(root.exists())
                self.assertNotIn('test-secret', out.getvalue())

    def test_remaining_cooldown_is_readonly_keyless_and_includes_rolling_window(self):
        from arxiv_pipeline.references import preflight
        self.assertTrue(hasattr(preflight, 'remaining_cooldown_seconds'),
                        'Continuous control needs a read-only cooldown accessor')
        from arxiv_pipeline.http import checkpoint_record, canonical_checkpoint_key, utc_iso
        from arxiv_pipeline.runner.models import RunOptions
        from arxiv_pipeline.runner.service import PipelineRunner
        from arxiv_pipeline.runner.factory import MockModuleFactory
        from arxiv_pipeline.runner.state import StateStore
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            PipelineRunner(MockModuleFactory()).run(RunOptions('saved-window', root, ('trong',), True))
            with StateStore(root/'state.sqlite', 'saved-window') as store:
                store.save_checkpoint(checkpoint_record('rate_limits', canonical_checkpoint_key('semantic_scholar'),
                    {'group': 'semantic_scholar', 'next_allowed_at': utc_iso(1001),
                     'recent_requests_at': [utc_iso(999)] * 100, 'consecutive_429': 0}))
            original = (root/'state.sqlite').read_bytes()
            with patch('arxiv_pipeline.references.preflight.time.time', return_value=1000), \
                 patch.dict(os.environ, {}, clear=True), patch('socket.create_connection',
                 side_effect=AssertionError('Cooldown inspection must not send HTTP')):
                self.assertEqual(preflight.remaining_cooldown_seconds(root, 'saved-window'), 299)
            self.assertEqual((root/'state.sqlite').read_bytes(), original)
            self.assertFalse((root/'never-created').exists())
            self.assertEqual(preflight.remaining_cooldown_seconds(root/'never-created', 'saved-window'), 0)

    def test_rate_limited_cli_reports_saved_retry_after_for_outer_recovery(self):
        from arxiv_pipeline.runner.models import RunOptions
        from arxiv_pipeline.runner.service import PipelineRunner
        from arxiv_pipeline.runner.factory import MockModuleFactory
        from arxiv_pipeline.runner.state import StateStore
        sent = []
        def limited(*args):
            sent.append(args)
            return ApiResponse(429, {'Retry-After': '3600'}, b'')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            PipelineRunner(MockModuleFactory()).run(RunOptions('probe-429', root, ('trong',), True))
            with StateStore.readonly(root/'state.sqlite', 'probe-429') as store:
                before = store.connection.execute('SELECT count(*) FROM attempts').fetchone()[0]
            with patch.dict(os.environ, {'S2_API_KEY': 'test-secret'}), \
                 patch('arxiv_pipeline.references.api._semantic_scholar_transport', limited), \
                 patch('socket.create_connection', side_effect=AssertionError('No real network')), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(['--arxiv-id', '2504.17523', '--output-root', str(root),
                                       '--run-id', 'probe-429']), 2)
            result = json.loads(out.getvalue())
            self.assertEqual(result['http_status'], 429)
            self.assertGreaterEqual(result.get('wait_seconds', 0), 3590)
            self.assertEqual(len(sent), 1)
            self.assertNotIn('test-secret', out.getvalue())
            with StateStore.readonly(root/'state.sqlite', 'probe-429') as store:
                self.assertEqual(store.connection.execute('SELECT count(*) FROM attempts').fetchone()[0], before)

    def test_authenticated_probe_requests_one_reference_using_production_fields(self):
        sent = []
        def transport(url, timeout, headers):
            sent.append((url, timeout, headers))
            return ApiResponse(200, {}, b'{"data": []}')
        result = check_access('2504.17523', api_key='test-secret', transport=transport)
        self.assertEqual(result, {'ok': True, 'http_status': 200, 'returned_count': 0})
        self.assertEqual(len(sent), 1)
        self.assertIn('/paper/ARXIV:2504.17523/references?', sent[0][0])
        self.assertIn('fields=title%2Cauthors%2CexternalIds', sent[0][0])
        self.assertIn('limit=1', sent[0][0])
        self.assertEqual(sent[0][2]['x-api-key'], 'test-secret')
        self.assertLessEqual(sent[0][1], 20)
        self.assertNotIn('test-secret', json.dumps(result))

    def test_missing_key_never_sends_http(self):
        def forbidden(*args):
            self.fail('Missing key must fail before HTTP')
        with self.assertRaisesRegex(ValueError, 'S2_API_KEY'):
            check_access('2504.17523', api_key='', transport=forbidden)

    def test_http_errors_and_malformed_payload_do_not_retry(self):
        for status, body in [(429, b''), (401, b''), (403, b''), (503, b''), (200, b'not json')]:
            sent = []
            def transport(*args):
                sent.append(args)
                return ApiResponse(status, {'Retry-After': '3600'}, body)
            with self.subTest(status=status), self.assertRaises(RemoteError):
                check_access('2504.17523', api_key='test-secret', transport=transport)
            self.assertEqual(len(sent), 1)

    def test_cli_redacts_untrusted_error_text(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'S2_API_KEY': 'test-secret'}), patch(
            'arxiv_pipeline.references.preflight.check_access',
            side_effect=RemoteError('network_error', 'test-secret in an upstream error')):
            with contextlib.redirect_stdout(output):
                code = main(['--arxiv-id', '2504.17523'])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())['ok'])
        self.assertNotIn('test-secret', output.getvalue())

    def test_saved_cooldown_blocks_probe_without_changing_attempts(self):
        from arxiv_pipeline.http import checkpoint_record, canonical_checkpoint_key, utc_iso
        from arxiv_pipeline.runner.models import RunOptions
        from arxiv_pipeline.runner.service import PipelineRunner
        from arxiv_pipeline.runner.factory import MockModuleFactory
        from arxiv_pipeline.runner.state import StateStore
        import time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            PipelineRunner(MockModuleFactory()).run(RunOptions('mock-probe', root, ('trong',), True))
            with StateStore(root / 'state.sqlite', 'mock-probe') as store:
                before = store.connection.execute('SELECT count(*) FROM attempts').fetchone()[0]
                store.save_checkpoint(checkpoint_record('rate_limits', canonical_checkpoint_key('semantic_scholar'),
                    {'group': 'semantic_scholar', 'next_allowed_at': utc_iso(time.time() + 3600),
                     'recent_requests_at': [], 'consecutive_429': 1}))
            def forbidden(*args):
                self.fail('Active cooldown must block HTTP')
            with self.assertRaisesRegex(ValueError, 'cooldown'):
                check_access('2504.17523', api_key='test-secret', transport=forbidden,
                             output_root=root, run_id='mock-probe')
            output = io.StringIO()
            with patch.dict(os.environ, {'S2_API_KEY': 'test-secret'}), contextlib.redirect_stdout(output):
                self.assertEqual(main(['--arxiv-id', '2504.17523', '--output-root', str(root),
                                       '--run-id', 'mock-probe']), 2)
            blocked = json.loads(output.getvalue())
            self.assertEqual(blocked['code'], 'cooldown')
            self.assertGreater(blocked['wait_seconds'], 3500)
            with StateStore.readonly(root / 'state.sqlite', 'mock-probe') as store:
                self.assertEqual(store.connection.execute('SELECT count(*) FROM attempts').fetchone()[0], before)

    def test_cli_missing_key_names_the_actionable_reason_without_http(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
            code = main(['--arxiv-id', '2504.17523'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())['code'], 'missing_key')
