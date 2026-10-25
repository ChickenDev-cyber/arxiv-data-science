from email.message import Message
from http.client import HTTPResponse
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.sources.api import ArxivSourceClient


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
