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
