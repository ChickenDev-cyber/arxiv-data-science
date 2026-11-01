from email.message import Message
from http.client import HTTPResponse
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.sources.api import ArxivSourceClient
from .helpers import wire_http_response


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = str(value)


class DownloadIntegrityTests(unittest.TestCase):
    def client(self, responses, **kwargs):
        sent = []

        def open_response(request, timeout):
            sent.append(request)
            return responses[len(sent) - 1]

        client = ArxivSourceClient(
            opener=SimpleNamespace(open=open_response), delay=0, sleep=lambda _: None,
            **kwargs,
        )
        return client, sent

    def test_short_content_length_retries(self):
        bad = Response(b'abc', headers={'content-length': '6'})
        good = Response(b'abcdef', headers={'Content-Length': '6'})
        client, sent = self.client([bad, good])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source'
            self.assertEqual(client.download('2504.17526', 1, path), 6)
            self.assertEqual(path.read_bytes(), b'abcdef')
        self.assertEqual(len(sent), 2)
        self.assertTrue(bad.closed and good.closed)
        provenance = client.provenance('2504.17526', 1)
        self.assertEqual(provenance['http_status'], '200')
        self.assertEqual(provenance['content_length'], '6')
        self.assertEqual(provenance['received_bytes'], '6')

    def test_unsolicited_partial_response_retries(self):
        for status in (200, 206):
            with self.subTest(status=status):
                bad = Response(b'abc', status=status, headers={
                    'Content-Length': '3', 'Content-Range': 'bytes 0-2/6',
                })
                client, sent = self.client([bad, Response(b'abcdef')])
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'source'
                    self.assertEqual(client.download('2504.17526', 1, path), 6)
                    self.assertEqual(path.read_bytes(), b'abcdef')
                self.assertEqual(len(sent), 2)
                self.assertTrue(bad.closed)

    def test_missing_length_valid_body(self):
        client, _ = self.client([Response(b'abcdef')])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(client.download('2504.17526', 1, Path(tmp) / 'source'), 6)

    def test_empty_and_invalid_length(self):
        for length in ('0', '-1', 'abc', '1.5'):
            with self.subTest(length=length):
                client, _ = self.client([Response(b'', headers={'Content-Length': length})], max_attempts=1)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'source'
                    with self.assertRaises(RemoteError):
                        client.download('2504.17526', 1, path)
                    self.assertFalse(path.exists())

    def test_final_failure_removes_partial_file(self):
        responses = [Response(b'abc', headers={'Content-Length': '6'}) for _ in range(3)]
        client, sent = self.client(responses)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source'
            with self.assertRaises(RemoteError):
                client.download('2504.17526', 1, path)
            self.assertFalse(path.exists())
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(client.provenance('2504.17526', 1)['received_bytes'], '3')

    def test_non_success_status_is_not_a_download(self):
        client, _ = self.client([Response(b'body', status=204)], max_attempts=1)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RemoteError):
                client.download('2504.17526', 1, Path(tmp) / 'source')

    def test_conflicting_lengths_do_not_accept_a_truncated_real_http_body(self):
        from arxiv_pipeline.sources.extractor import SourceExtractor
        from arxiv_pipeline.sources.service import SourcesModule
        prefix = b'\\documentclass{article}\n\\begin{document}prefix\n'
        body = prefix + b'suffix\\end{document}\n'
        wire = (f'HTTP/1.1 200 OK\r\nContent-Length: {len(prefix)}\r\n'
                f'Content-Length: {len(body)}\r\n\r\n').encode() + body
        class Socket:
            def makefile(self, *args, **kwargs):return io.BytesIO(wire)
        response = HTTPResponse(Socket());response.begin()
        client, _ = self.client([response], max_attempts=1)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = SourcesModule(client, SourceExtractor()).fetch_sources(
                {'owner': 'trong', 'arxiv_id': '2504.17526'}, 1,
                {'run_id': 'lengths', 'is_mock': False, 'output_root': root},
            )
            self.assertEqual(result['status'], 'failed')
            self.assertFalse((root/'staging').exists())
            self.assertTrue(response.isclosed())

    def test_huge_or_blank_length_is_a_remote_error(self):
        for value in ('9' * 5000, ' ', '0' * 5000):
            with self.subTest(value_length=len(value)):
                client, _ = self.client([Response(b'abc', headers={'Content-Length': value})], max_attempts=1)
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp)/'source'
                    with self.assertRaises(RemoteError):
                        client.download('2504.17526', 1, path)
                    self.assertFalse(path.exists())

    def test_agreeing_duplicate_and_combined_lengths_are_supported(self):
        duplicate = Response(b'abc', headers={'Content-Length': '3'})
        duplicate.headers['Content-Length'] = '03'
        combined = Response(b'abc', headers={'Content-Length': '3, 03'})
        for response in (duplicate, combined):
            with self.subTest(headers=str(response.headers)):
                client, _ = self.client([response])
                with tempfile.TemporaryDirectory() as tmp:
                    self.assertEqual(client.download('2504.17526', 1, Path(tmp)/'source'), 3)

    def test_real_http_response_short_content_length_retries_and_discards_partial(self):
        """Native HTTPResponse EOF semantics: truncated wire body retries, cleans partial, and gets exact payload."""
        bad = wire_http_response(b'short', content_length=12)
        good = wire_http_response(b'exact_twelve', content_length=12)
        client, sent = self.client([bad, good])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source'
            self.assertEqual(client.download('2504.17526', 1, path), 12)
            self.assertEqual(path.read_bytes(), b'exact_twelve')
        self.assertEqual(len(sent), 2)
        self.assertTrue(bad.isclosed())
        self.assertTrue(good.isclosed())
        provenance = client.provenance('2504.17526', 1)
        self.assertEqual(provenance['http_status'], '200')
        self.assertEqual(provenance['content_length'], '12')
        self.assertEqual(provenance['received_bytes'], '12')

    def test_real_http_response_repeated_truncation_exhausts_budget_and_cleans_destination(self):
        """Native HTTPResponse EOF semantics: repeated wire truncation exhausts attempts and deletes destination."""
        responses = [
            wire_http_response(b'part1', content_length=15),
            wire_http_response(b'part22', content_length=15),
            wire_http_response(b'part333', content_length=15),
        ]
        client, sent = self.client(responses, max_attempts=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'source'
            with self.assertRaises(RemoteError) as ctx:
                client.download('2504.17526', 1, path)
            self.assertEqual(ctx.exception.info['code'], 'network_error')
            self.assertFalse(path.exists())
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(r.isclosed() for r in responses))
        self.assertEqual(client.provenance('2504.17526', 1)['received_bytes'], '7')

    def test_real_http_response_truncation_exhaustion_in_service_never_publishes_to_staging(self):
        """End-to-end service: repeated native HTTPResponse truncation fails and leaves no staged or raw artifact."""
        from arxiv_pipeline.sources.extractor import SourceExtractor
        from arxiv_pipeline.sources.service import SourcesModule

        responses = [
            wire_http_response(b'prefix1', content_length=20),
            wire_http_response(b'prefix22', content_length=20),
            wire_http_response(b'prefix333', content_length=20),
        ]
        client, sent = self.client(responses, max_attempts=3)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = SourcesModule(client, SourceExtractor()).fetch_sources(
                {'owner': 'trong', 'arxiv_id': '2504.17526'}, 1,
                {'run_id': 'truncation-run', 'is_mock': False, 'output_root': root},
            )
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'network_error')
            self.assertFalse((root / 'staging').exists())
            self.assertFalse((root / 'raw').exists())
            self.assertEqual(len(sent), 3)
            self.assertTrue(all(r.isclosed() for r in responses))
