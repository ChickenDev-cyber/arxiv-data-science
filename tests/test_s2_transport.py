"""Exercise emitted HTTP bytes using real HTTPConnection and an offline socket."""
import http.client
import io
import os
import socket
import ssl
import unittest
from unittest.mock import patch

from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.references.api import SemanticScholarClient, ArxivDateClient
from arxiv_pipeline.references.preflight import check_access
from arxiv_pipeline.http import RequestScheduler

REAL_HTTP_CONNECTION = http.client.HTTPConnection


class RecordingIO(io.BytesIO):
    def __init__(self, value, *, fail_read=False):
        super().__init__(value)
        self.read_sizes = []
        self.fail_read = fail_read

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self.fail_read:
            raise TimeoutError('offline fixture')
        return super().read(size)


class WireFixture:
    def __init__(self, responses, *, fail_read=False):
        self.responses = iter(responses)
        self.connections = []
        self.sockets = []
        fixture = self

        class Socket:
            def __init__(self, response):
                self.wire = bytearray()
                self.closed = False
                self.file = RecordingIO(response, fail_read=fail_read)

            def sendall(self, data):
                self.wire.extend(data)

            def makefile(self, *args, **kwargs):
                return self.file

            def close(self):
                self.closed = True

        class Connection(REAL_HTTP_CONNECTION):
            default_port = 443
            def __init__(self, host, **kwargs):
                kwargs.pop('context', None)
                kwargs.pop('check_hostname', None)
                super().__init__(host, **kwargs)
                fixture.connections.append(self)

            def connect(self):
                self.sock = Socket(next(fixture.responses))
                fixture.sockets.append(self.sock)

        self.connection_type = Connection

    def patch(self):
        return patch('http.client.HTTPSConnection', self.connection_type)


def response(body=b'{"data": []}', status=200, headers=b''):
    return (b'HTTP/1.1 ' + str(status).encode() + b' Fixture\r\nContent-Length: '
            + str(len(body)).encode() + b'\r\n' + headers + b'\r\n' + body)


class S2TransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        no_network = patch('socket.create_connection', side_effect=AssertionError('Live network forbidden'))
        no_network.start()
        self.addCleanup(no_network.stop)

    def test_default_transport_emits_exact_lowercase_key_including_explicit_none(self):
        for kwargs in ({}, {'transport': None}):
            with self.subTest(kwargs=kwargs):
                fixture = WireFixture([response()])
                with fixture.patch():
                    page = SemanticScholarClient(api_key='offline-secret', max_attempts=1,
                                                 **kwargs).references_page('2504.17523', 0, 100)
                self.assertEqual(page, {'data': []})
                wire = bytes(fixture.sockets[0].wire)
                self.assertIn(b'\r\nx-api-key: offline-secret\r\n', wire)
                self.assertNotIn(b'\r\nX-Api-Key:', wire)
                self.assertIn(b'limit=100', wire)
                self.assertTrue(fixture.sockets[0].closed)
                self.assertTrue(fixture.sockets[0].file.closed)

    def test_preflight_uses_same_authentication_transport(self):
        fixture = WireFixture([response()])
        with fixture.patch():
            report = check_access('2504.17523', api_key='offline-secret')
        self.assertTrue(report['ok'])
        self.assertIn(b'\r\nx-api-key: offline-secret\r\n', fixture.sockets[0].wire)
        self.assertNotIn('offline-secret', str(report))

    def test_off_origin_urls_fail_before_opening_connection(self):
        for url in ('http://api.semanticscholar.org/a', 'https://example.org/a',
                    'https://api.semanticscholar.org:444/a',
                    'https://user:password@api.semanticscholar.org/a'):
            with self.subTest(url=url):
                fixture = WireFixture([response()])
                with fixture.patch(), self.assertRaises(ValueError):
                    SemanticScholarClient(api_key='offline-secret', max_attempts=1)._get(url)
                self.assertEqual(len(fixture.connections), 0)

    def test_redirect_does_not_forward_key(self):
        fixture = WireFixture([response(status=302, headers=b'Location: https://example.org/a\r\n'), response()])
        with fixture.patch(), self.assertRaises(RemoteError) as caught:
            SemanticScholarClient(api_key='offline-secret', max_attempts=1).references_page('2504.17523', 0, 100)
        self.assertEqual(caught.exception.info['http_status'], 302)
        self.assertEqual(len(fixture.connections), 1)
        self.assertTrue(fixture.sockets[0].closed)

    def test_429_keeps_retry_after_and_does_not_read_error_body(self):
        now = [0.0]
        scheduler = RequestScheduler(monotonic=lambda: now[0], sleep=lambda n: now.__setitem__(0, now[0]+n))
        fixture = WireFixture([response(b'offline-secret upstream body', 429, b'Retry-After: 600\r\n')])
        with fixture.patch(), self.assertRaises(RemoteError) as caught:
            SemanticScholarClient(api_key='offline-secret', scheduler=scheduler,
                max_attempts=1, monotonic=lambda: now[0], sleep=lambda n: None).references_page('2504.17523', 0, 100)
        self.assertEqual(caught.exception.info['http_status'], 429)
        self.assertNotIn('offline-secret', str(caught.exception))
        self.assertEqual(fixture.sockets[0].file.read_sizes, [])
        self.assertTrue(fixture.sockets[0].closed)
        with scheduler.request('semantic_scholar'):
            self.assertEqual(now[0], 600.0)

    def test_body_limit_is_enforced_before_parsing_and_connection_closes(self):
        fixture = WireFixture([response(b'0123456789')])
        with fixture.patch(), patch('arxiv_pipeline.references.api.MAX_RESPONSE_BYTES', 4), self.assertRaises(RemoteError):
            SemanticScholarClient(api_key='', max_attempts=1)._get('https://api.semanticscholar.org/a')
        self.assertEqual(fixture.sockets[0].file.read_sizes, [5])
        self.assertTrue(fixture.sockets[0].closed)
        self.assertTrue(fixture.sockets[0].file.closed)

    def test_read_failure_closes_response_and_connection(self):
        fixture = WireFixture([response()], fail_read=True)
        with fixture.patch(), self.assertRaises(RemoteError) as caught:
            SemanticScholarClient(api_key='offline-secret', max_attempts=1).references_page('2504.17523', 0, 100)
        self.assertEqual(caught.exception.info['code'], 'network_error')
        self.assertNotIn('offline-secret', str(caught.exception))
        self.assertTrue(fixture.sockets[0].closed)
        self.assertTrue(fixture.sockets[0].file.closed)

    def test_dns_tls_and_socket_failures_remain_retryable_and_redacted(self):
        for error_type in (socket.gaierror, ssl.SSLError, OSError):
            closed = []
            class Connection:
                def __init__(self, *args, **kwargs):
                    pass
                def request(self, *args, **kwargs):
                    raise error_type('offline-secret')
                def close(self):
                    closed.append(True)
            with self.subTest(error_type=error_type.__name__):
                with patch('http.client.HTTPSConnection', Connection), self.assertRaises(RemoteError) as caught:
                    SemanticScholarClient(api_key='offline-secret', max_attempts=1).references_page(
                        '2504.17523', 0, 100)
                self.assertEqual(caught.exception.info['code'], 'network_error')
                self.assertTrue(caught.exception.info['retryable'])
                self.assertNotIn('offline-secret', str(caught.exception))
                self.assertEqual(closed, [True])

    def test_malformed_key_is_rejected_without_echoing_value(self):
        for key in ('offline-secret\r\ninjected', ' offline-secret ', 'offline-secret-\u2603', 123):
            with self.subTest(key_type=type(key).__name__):
                with self.assertRaises(ValueError) as caught:
                    SemanticScholarClient(api_key=key)
                self.assertNotIn('offline-secret', str(caught.exception))

    def test_custom_transports_and_arxiv_client_remain_supported(self):
        sent = []
        def transport(*args):
            sent.append(args)
            return ApiResponse(200, {}, b'{"data": []}')
        self.assertEqual(SemanticScholarClient(api_key='offline-secret', transport=transport).references_page(
            '2504.17523', 0, 100), {'data': []})
        self.assertEqual(len(sent), 1)
        fixture = WireFixture([response(b'dates')])
        with fixture.patch():
            value = ArxivDateClient(max_attempts=1)._get('https://export.arxiv.org/api/query')
        self.assertEqual(value, b'dates')
        self.assertNotIn(b'api-key', fixture.sockets[0].wire)
