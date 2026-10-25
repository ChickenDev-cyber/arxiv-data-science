import gzip
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest

from arxiv_pipeline.sources.api import ArxivSourceClient
from arxiv_pipeline.sources.extractor import SourceExtractor
from arxiv_pipeline.sources.extractor import ExtractionLimits
from arxiv_pipeline.sources.service import SourcesModule
from .test_download_integrity import Response
from .test_service import make_tar_gz


TEX = b'\\documentclass{article}\n\\begin{document}source\\end{document}\n'


class FetchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.good = make_tar_gz([('main.tex', TEX)])
        large = make_tar_gz([('main.tex', TEX + random.Random(42).randbytes(100_000))])
        self.truncated = large[:len(large) // 2]

    def module(self, responses, **kwargs):
        self.calls = []

        def open_response(request, timeout):
            self.calls.append(request.full_url)
            item = responses[len(self.calls) - 1]
            if isinstance(item, Exception):
                raise item
            return Response(item)

        self.client = ArxivSourceClient(
            opener=SimpleNamespace(open=open_response), delay=0, sleep=lambda _: None,
            **kwargs,
        )
        return SourcesModule(self.client, SourceExtractor())

    def fetch(self, module, root, version=1):
        return module.fetch_sources(
            {'owner': 'trong', 'arxiv_id': '2504.17526'}, version,
            {'run_id': 'recovery', 'is_mock': False, 'output_root': root},
        )

    def test_truncated_gzip_then_valid_retries(self):
        module = self.module([self.truncated, self.good])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.fetch(module, root)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['attempts'], 2)
            self.assertEqual((root/'staging/recovery/trong/2504-17526/v1/main.tex').read_bytes(), TEX)

    def test_missing_gzip_trailer_is_not_success(self):
        module = self.module([self.good[:-4]] * 3)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.fetch(module, root)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['attempts'], 3)
            self.assertFalse((root/'staging').exists())

    def test_transport_and_extraction_share_three_attempts(self):
        module = self.module([TimeoutError('timeout'), self.truncated, self.good])
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual((result['status'], result['attempts']), ('success', 3))

    def test_budget_does_not_multiply_with_transport_retries(self):
        module = self.module([TimeoutError('first'), self.truncated, TimeoutError('last'), self.good])
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['attempts'], 3)
        self.assertEqual(len(self.calls), 3)

    def test_corrupt_exhaustion_cleans_staging(self):
        module = self.module([self.truncated] * 3)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.fetch(module, root)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['attempts'], 3)
            self.assertIsNone(result['data'])
            self.assertFalse((root/'staging').exists())
            self.assertFalse(any(p.is_file() for p in (root/'scratch').rglob('*')))

    def test_unsafe_archive_not_retried(self):
        module = self.module([make_tar_gz([('../evil.tex', TEX)])])
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual(result['error']['code'], 'unsafe_archive')
            self.assertEqual(result['attempts'], 1)

    def test_download_limit_not_retried(self):
        module = self.module([self.good], max_download_bytes=10)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['attempts'], 1)

    def test_budget_resets_for_next_version(self):
        module = self.module([TimeoutError('timeout'), self.truncated, self.good, self.good])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second = self.fetch(module, root), self.fetch(module, root, 2)
            self.assertEqual((first['status'], first['attempts']), ('success', 3))
            self.assertEqual((second['status'], second['attempts']), ('success', 1))

    def test_single_gzip_tex_eof_is_retryable(self):
        module = self.module([gzip.compress(TEX)[:-4], gzip.compress(TEX)])
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual((result['status'], result['attempts']), ('success', 2))

    def test_late_deflate_error_returns_failed_without_retry(self):
        import io
        import tarfile
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            data = TEX + random.Random(17).randbytes(1_000_000)
            member = tarfile.TarInfo('main.tex');member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        damaged = bytearray(gzip.compress(buffer.getvalue(), compresslevel=0))
        position = 10;blocks = []
        while position < len(damaged) - 8:
            blocks.append(position)
            length = int.from_bytes(damaged[position + 1:position + 3], 'little')
            position += 5 + length
        for position in (blocks[0], blocks[-2]):
            with self.subTest(block_offset=position):
                corrupt = damaged.copy()
                corrupt[position] = 7  # Reserved DEFLATE type, early or after valid members.
                module = self.module([bytes(corrupt)])
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    result = self.fetch(module, root)
                    self.assertEqual(result['status'], 'failed')
                    self.assertEqual(result['error']['code'], 'invalid_source')
                    self.assertFalse(result['error']['retryable'])
                    self.assertEqual(result['attempts'], 1)
                    self.assertFalse((root/'staging').exists())

    def test_valid_large_record_padding_is_supported(self):
        raw = gzip.decompress(self.good)
        body = gzip.compress(raw + b'\0' * (65536 - len(raw)))
        module = self.module([body])
        module.limits = ExtractionLimits(max_members=1, max_file_bytes=128, max_extracted_bytes=128)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual((result['status'], result['attempts']), ('success', 1))
            self.assertEqual(result['metrics']['retained_source_bytes'], len(TEX))

    def test_excessive_gzip_padding_is_bounded(self):
        raw = gzip.decompress(self.good)
        body = gzip.compress(raw + b'\0' * (1024 * 1024))
        module = self.module([body])
        module.limits = ExtractionLimits(max_members=1, max_file_bytes=128, max_extracted_bytes=128)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.fetch(module, Path(tmp))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error']['code'], 'extract_limit')
            self.assertEqual(result['attempts'], 1)
