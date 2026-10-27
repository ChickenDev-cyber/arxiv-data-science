from datetime import datetime, timezone
from email.utils import format_datetime
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.sources.api import ArxivSourceClient
from .helpers import truncated_chunked_response


class FakeStreamResponse:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        if self.closed or not self.chunks:
            return b""
        return self.chunks.pop(0)

    def close(self):
        self.closed = True


class ArxivSourceClientTests(unittest.TestCase):
    def setUp(self):
        self.fake_time = 1000.0
        self.sleeps = []

    def _clock(self):
        return self.fake_time

    def _sleep(self, duration):
        self.sleeps.append(duration)
        self.fake_time += duration

    def test_download_happy_path_two_chunks(self):
        recorded_requests = []
        stream_resp = FakeStreamResponse([b"CHUNK1_", b"CHUNK2_DATA"])

        def fake_opener(req, timeout=30):
            recorded_requests.append(req)
            return stream_resp

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            delay=1.0,
            user_agent="CustomAgent/1.0",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            bytes_count = client.download("2506.20788", 1, dest)

            # Verification
            self.assertEqual(bytes_count, len(b"CHUNK1_CHUNK2_DATA"))
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_bytes(), b"CHUNK1_CHUNK2_DATA")
            self.assertTrue(stream_resp.closed)
            self.assertEqual(client.attempts, 1)

            # Request checks
            self.assertEqual(len(recorded_requests), 1)
            req = recorded_requests[0]
            self.assertEqual(req.full_url, "https://export.arxiv.org/src/2506.20788v1")
            self.assertEqual(req.get_header("User-agent"), "CustomAgent/1.0")

    def test_download_hard_limit_exceeded_cleans_destination(self):
        stream_resp = FakeStreamResponse([b"x" * 50, b"x" * 60])

        def fake_opener(req, timeout=30):
            return stream_resp

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            max_download_bytes=100,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            with self.assertRaises(RemoteError) as ctx:
                client.download("2506.20788", 1, dest)
            self.assertIn("exceeds limit", str(ctx.exception))
            self.assertFalse(dest.exists(), "Incomplete download must be cleaned on limit error")

    def test_download_404_raises_not_found_no_retry(self):
        def fake_opener(req, timeout=30):
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            max_attempts=3,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            with self.assertRaises(RemoteError) as ctx:
                client.download("2506.20788", 1, dest)
            self.assertEqual(ctx.exception.info["code"], "not_found")
            self.assertEqual(ctx.exception.info["http_status"], 404)
            self.assertFalse(ctx.exception.info["retryable"])
            self.assertEqual(client.attempts, 1, "404 must not be retried")
            self.assertFalse(dest.exists())

    def test_download_400_raises_remote_error_no_retry(self):
        def fake_opener(req, timeout=30):
            raise HTTPError(req.full_url, 400, "Bad Request", {}, None)

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            max_attempts=3,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            with self.assertRaises(RemoteError) as ctx:
                client.download("2506.20788", 1, dest)
            self.assertEqual(ctx.exception.info["code"], "remote_error")
            self.assertEqual(ctx.exception.info["http_status"], 400)
            self.assertEqual(client.attempts, 1, "400 must not be retried")

    def test_download_429_retries_and_honors_retry_after(self):
        call_count = 0

        def fake_opener(req, timeout=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "5"}, None)
            return FakeStreamResponse([b"SUCCESS"])

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            max_attempts=3,
            delay=1.0,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            bytes_count = client.download("2506.20788", 1, dest)
            self.assertEqual(bytes_count, len(b"SUCCESS"))
            self.assertEqual(client.attempts, 2)
            self.assertIn(5.0, self.sleeps)

    def test_download_timeout_retries_and_raises_network_error(self):
        def fake_opener(req, timeout=30):
            raise TimeoutError("timed out")

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            max_attempts=3,
            delay=1.0,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            with self.assertRaises(RemoteError) as ctx:
                client.download("2506.20788", 1, dest)
            self.assertEqual(ctx.exception.info["code"], "network_error")
            self.assertEqual(client.attempts, 3)

    def test_download_503_retries_and_raises_remote_error(self):
        def fake_opener(req, timeout=30):
            raise HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

        client = ArxivSourceClient(
            opener=Mock(open=fake_opener),
            monotonic=self._clock,
            wall_clock=self._clock,
            sleep=self._sleep,
            max_attempts=3,
            delay=1.0,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.tar.gz"
            with self.assertRaises(RemoteError) as ctx:
                client.download("2506.20788", 1, dest)
            self.assertEqual(ctx.exception.info["code"], "remote_error")
            self.assertEqual(ctx.exception.info["http_status"], 503)
            self.assertEqual(client.attempts, 3)

    def test_truncated_stream_retries_from_empty_file(self):
        broken = truncated_chunked_response()
        good = FakeStreamResponse([b"\\documentclass{article}\n"])
        with tempfile.TemporaryDirectory() as tmp_dir:
            dest = Path(tmp_dir) / "source.download"
            calls = []

            def opener(req, timeout):
                calls.append(req.full_url)
                if len(calls) == 1:
                    return broken
                self.assertFalse(dest.exists(), "Remove the partial file before retry")
                return good

            client = ArxivSourceClient(
                opener=Mock(open=opener), max_attempts=2,
                monotonic=self._clock, sleep=self._sleep,
            )
            try:
                size = client.download("2506.20788", 1, dest)
            except Exception as exc:
                self.fail(f"Truncated stream must retry, got {type(exc).__name__}: {exc}")
            self.assertEqual(dest.read_bytes(), b"\\documentclass{article}\n")
            self.assertEqual(size, 24)
            self.assertEqual(client.attempts, 2)
            self.assertTrue(broken.isclosed())
            self.assertTrue(good.closed)

    def test_final_retry_after_applies_to_next_download(self):
        for status in (429, 503):
            for header_kind in ("seconds", "http-date"):
                with self.subTest(status=status, header_kind=header_kind):
                    self.fake_time = 1000.0
                    request_times = []

                    def opener(req, timeout):
                        request_times.append(self.fake_time)
                        if len(request_times) <= 3:
                            header = "120"
                            if header_kind == "http-date":
                                header = format_datetime(datetime.fromtimestamp(
                                    1700000000 + self.fake_time + 120, timezone.utc,
                                ), usegmt=True)
                            raise HTTPError(req.full_url, status, "Unavailable",
                                            {"Retry-After": header}, io.BytesIO())
                        return FakeStreamResponse([b"good"])

                    client = ArxivSourceClient(
                        opener=Mock(open=opener), max_attempts=3, delay=3,
                        monotonic=self._clock, sleep=self._sleep,
                        wall_clock=lambda: 1700000000 + self.fake_time,
                    )
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        with self.assertRaises(RemoteError):
                            client.download("2506.20788", 1, Path(tmp_dir) / "first")
                        client.download("2506.20788", 2, Path(tmp_dir) / "second")
                    self.assertEqual(request_times, [1000.0, 1120.0, 1240.0, 1360.0])

    def test_provenance(self):
        client = ArxivSourceClient()
        prov = client.provenance("2501.12642", 2)
        self.assertEqual(prov, {
            "url": "https://export.arxiv.org/src/2501.12642v2",
            "method": "GET",
        })


if __name__ == "__main__":
    unittest.main()
