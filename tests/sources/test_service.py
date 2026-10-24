"""Tests for T3 SourcesModule service and Result schema-1.1 contracts."""

import io
import gzip
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from unittest.mock import Mock

from arxiv_pipeline.contracts import Context, PaperTask, SCHEMA_VERSION, paper_folder
from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.sources import SourcesModule
from arxiv_pipeline.sources.api import ArxivSourceClient
from arxiv_pipeline.sources.extractor import ExtractionLimits, SourceExtractor
from .helpers import make_docx, make_zip, truncated_chunked_response


class FakeClient:
    def __init__(self, payload: bytes | Exception):
        self.payload = payload
        self.attempts = 0

    def download(self, arxiv_id: str, version: int, destination: Path) -> int:
        self.attempts += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)
        return len(self.payload)

    def provenance(self, arxiv_id: str, version: int) -> dict[str, str]:
        return {
            "url": f"https://export.arxiv.org/src/{arxiv_id}v{version}",
            "method": "GET",
        }


def make_tar_gz(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members:
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class SourcesModuleTests(unittest.TestCase):
    def setUp(self):
        self.extractor = SourceExtractor()
        self.limits = ExtractionLimits()

    def _context(self, root: Path, run_id: str = "run_2026") -> Context:
        return {
            "run_id": run_id,
            "output_root": root,
            "is_mock": False,
        }

    def test_fetch_sources_success_schema_and_staging(self):
        payload = make_tar_gz([
            ("main.tex", b"\\documentclass{article}\n\\input{sections/part}\n"),
            ("sections/part.tex", b"\\section{Part}\n"),
            ("refs.bib", b"@article{demo, title={T}}\n"),
            ("figures/fig.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50),
            ("custom.sty", b"\\ProvidesPackage{custom}\n"),
        ])
        client = FakeClient(payload)
        module = SourcesModule(client, self.extractor, limits=self.limits)

        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            context = self._context(root)
            result = module.fetch_sources(task, 1, context)

            # 1. Result schema-1.1 key checks
            required_keys = {
                "schema_version", "run_id", "owner", "is_mock", "step", "arxiv_id",
                "version", "status", "data", "missing_fields", "error", "provenance",
                "metrics", "started_at", "finished_at", "duration_seconds", "attempts",
            }
            self.assertEqual(set(result.keys()), required_keys)
            self.assertEqual(result["schema_version"], SCHEMA_VERSION)
            self.assertEqual(result["run_id"], "run_2026")
            self.assertEqual(result["owner"], "khang")
            self.assertEqual(result["step"], "sources")
            self.assertEqual(result["arxiv_id"], "2501.12642")
            self.assertEqual(result["version"], 1)
            self.assertEqual(result["status"], "success")
            self.assertIsNone(result["error"])
            self.assertEqual(result["missing_fields"], [])
            self.assertEqual(result["attempts"], 1)
            self.assertGreaterEqual(result["duration_seconds"], 0.0)

            # 2. Metrics check
            metrics = result["metrics"]
            self.assertEqual(metrics["download_bytes"], len(payload))
            self.assertIsNotNone(metrics["retained_source_bytes"])
            self.assertEqual(
                metrics["extracted_bytes_before_filter"],
                metrics["retained_source_bytes"] + metrics["removed_figure_bytes"] + metrics["removed_other_bytes"]
            )

            # 3. Source directory invariant and staging location
            data = result["data"]
            self.assertIsNotNone(data)
            self.assertEqual(data["source_kind"], "tar_gzip")
            folder = paper_folder("2501.12642")
            expected_rel_source_dir = f"raw/khang/{folder}/v1"
            self.assertEqual(data["source_dir"], expected_rel_source_dir)

            # Verify physical staging location
            staging_dir = root / "staging" / "run_2026" / "khang" / folder / "v1"
            self.assertTrue(staging_dir.exists())
            self.assertTrue((staging_dir / "main.tex").exists())
            self.assertTrue((staging_dir / "sections" / "part.tex").exists())
            self.assertTrue((staging_dir / "refs.bib").exists())
            # Non-retained files must NOT be in staging
            self.assertFalse((staging_dir / "figures" / "fig.png").exists())
            self.assertFalse((staging_dir / "custom.sty").exists())

            # Verify no writes to raw/
            self.assertFalse((root / "raw").exists())

            # Verify content bytes exactly preserved
            self.assertEqual(
                (staging_dir / "main.tex").read_bytes(),
                b"\\documentclass{article}\n\\input{sections/part}\n"
            )

    def test_fetch_sources_pdf_only_returns_unsupported(self):
        client = FakeClient(b"%PDF-1.5\n1 0 obj\n<<>>\nendobj")
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "hoang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(task, 2, self._context(root))

            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["owner"], "hoang")
            self.assertIsNone(result["error"])
            self.assertEqual(result["data"]["source_kind"], "pdf_only")
            self.assertEqual(result["data"]["files"], [])
            self.assertEqual(result["metrics"]["retained_source_bytes"], 0)

            # Staging must NOT have been created for unsupported
            folder = paper_folder("2501.12642")
            staging_dir = root / "staging" / "run_2026" / "hoang" / folder / "v2"
            self.assertFalse(staging_dir.exists())

    def test_fetch_sources_tar_without_tex_returns_unsupported(self):
        payload = make_tar_gz([
            ("refs.bib", b"@article{a,}\n"),
            ("notes.txt", b"Notes\n"),
        ])
        client = FakeClient(payload)
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(task, 1, self._context(root))

            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["data"]["files"], [])
            self.assertEqual(result["metrics"]["retained_source_bytes"], 0)
            self.assertIsNone(result["error"])

    def test_html_response_is_not_staged_as_tex(self):
        documents = [
            b"<!DOCTYPE html><html><body>Service unavailable</body></html>",
            b"\xef\xbb\xbf \n\t<HTML><HEAD><TITLE>Error</TITLE></HEAD></HTML>",
            b'<?xml version="1.0"?>\n<html xmlns="http://www.w3.org/1999/xhtml"/>',
            b"<!-- proxy error -->\n<html><body>Error</body></html>",
        ]
        for html in documents:
            for compressed in (False, True):
                with self.subTest(html=html, compressed=compressed):
                    payload = gzip.compress(html) if compressed else html
                    module = SourcesModule(FakeClient(payload), self.extractor)
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        root = Path(tmp_dir)
                        result = module.fetch_sources(
                            {"owner": "khang", "arxiv_id": "2506.20788"},
                            1, self._context(root),
                        )
                        self.assertEqual(result["status"], "failed")
                        self.assertEqual(result["error"]["code"], "invalid_source")
                        self.assertIsNone(result["data"])
                        self.assertFalse((root / "staging").exists())

    def test_tex_long_preamble_and_embedded_html_preserve_original_bytes(self):
        tex = b"% source comment\n" * 400 + (
            b"\\documentclass{article}\n\\begin{document}\n"
            b"\\begin{verbatim}\n<html>Example only</html>\n\\end{verbatim}\n"
            b"\\includegraphics{figure}\n\\end{document}\n"
        )
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                module = SourcesModule(FakeClient(gzip.compress(tex) if compressed else tex),
                                       self.extractor)
                with tempfile.TemporaryDirectory() as tmp_dir:
                    root = Path(tmp_dir)
                    result = module.fetch_sources(
                        {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                    )
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(result["data"]["source_kind"], "gzip_tex" if compressed else "tex")
                    staged = root / "staging/run_2026/khang/2506-20788/v1/main.tex"
                    self.assertEqual(staged.read_bytes(), tex)
                    self.assertEqual(result["metrics"]["retained_source_bytes"], len(tex))

    def test_docx_without_tex_returns_unsupported(self):
        payload = make_docx()
        module = SourcesModule(FakeClient(payload), self.extractor)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(
                {"owner": "khang", "arxiv_id": "2506.20788"}, 2, self._context(root),
            )
            self.assertEqual(result["status"], "unsupported")
            self.assertIsNone(result["error"])
            self.assertEqual(result["version"], 2)
            self.assertEqual(result["data"], {
                "source_kind": "other", "source_dir": "raw/khang/2506-20788/v2", "files": [],
            })
            self.assertEqual(result["metrics"]["download_bytes"], len(payload))
            self.assertEqual(result["metrics"]["retained_source_bytes"], 0)
            self.assertIsNone(result["metrics"]["extracted_bytes_before_filter"])
            self.assertFalse((root / "staging").exists())

    def test_unknown_or_broken_zip_is_not_declared_without_tex(self):
        corrupt_crc = bytearray(make_docx(compression=zipfile.ZIP_STORED))
        corrupt_crc[corrupt_crc.index(b"An example document")] ^= 1
        payloads = {
            "ordinary_zip": make_zip({"readme.txt": b"Unknown source"}),
            "malformed_xml": make_docx(document=b"<w:document"),
            "wrong_document_type": make_docx(document=b"<anything/>"),
            "corrupt_crc": bytes(corrupt_crc),
            "also_contains_tex": make_docx(extra_members={"source.tex": b"\\documentclass{article}"}),
            "not_a_zip": b"PK\x03\x04\x00broken",
        }
        for label, payload in payloads.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                result = SourcesModule(FakeClient(payload), self.extractor).fetch_sources(
                    {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "invalid_source")
                self.assertIsNone(result["data"])
                self.assertFalse((root / "staging").exists())

    def test_docx_inspection_respects_size_limits(self):
        for limits in (ExtractionLimits(max_file_bytes=10),
                       ExtractionLimits(max_extracted_bytes=100)):
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                result = SourcesModule(FakeClient(make_docx()), self.extractor, limits=limits).fetch_sources(
                    {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "extract_limit")
                self.assertIsNone(result["data"])

    def test_docx_unknown_or_unsupported_xml_encoding_returns_failed(self):
        for encoding in ("nonexistent", "utf-7", "utf-32"):
            document = (
                f'<?xml version="1.0" encoding="{encoding}"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body/></w:document>'
            ).encode("ascii")
            with self.subTest(encoding=encoding), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                module = SourcesModule(FakeClient(make_docx(document=document)), self.extractor)
                try:
                    result = module.fetch_sources(
                        {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                    )
                except Exception as exc:
                    self.fail(f"Malformed XML must return Result, got {type(exc).__name__}: {exc}")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "invalid_source")
                self.assertIsNone(result["data"])
                self.assertFalse((root / "staging").exists())

    def test_docx_corrupt_auxiliary_member_returns_failed(self):
        valid = make_docx(extra_members={"word/styles.xml": b"<styles>extra part</styles>"},
                          compression=zipfile.ZIP_STORED)
        corrupt = bytearray(valid)
        corrupt[corrupt.index(b"extra part")] ^= 1
        for label, payload, expected in (("valid", valid, "unsupported"),
                                         ("corrupt", bytes(corrupt), "failed")):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                result = SourcesModule(FakeClient(payload), self.extractor).fetch_sources(
                    {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                )
                self.assertEqual(result["status"], expected)
                if expected == "failed":
                    self.assertEqual(result["error"]["code"], "invalid_source")
                    self.assertIsNone(result["data"])
                else:
                    self.assertIsNone(result["error"])
                self.assertFalse((root / "staging").exists())

    def test_docx_auxiliary_inspection_respects_size_limits(self):
        payload = make_docx(extra_members={"word/large.bin": b"x" * 5000})
        for limits in (ExtractionLimits(max_file_bytes=4096),
                       ExtractionLimits(max_extracted_bytes=4096)):
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                result = SourcesModule(FakeClient(payload), self.extractor, limits=limits).fetch_sources(
                    {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "extract_limit")
                self.assertIsNone(result["data"])

    def test_fetch_sources_404_returns_not_found(self):
        err = RemoteError("not_found", "HTTP 404", retryable=False, http_status=404)
        client = FakeClient(err)
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(task, 1, self._context(root))

            self.assertEqual(result["status"], "not_found")
            self.assertIsNone(result["data"])
            self.assertEqual(result["error"]["code"], "not_found")
            self.assertEqual(result["error"]["http_status"], 404)

    def test_fetch_sources_network_error_returns_failed(self):
        err = RemoteError("network_error", "Connection timed out", retryable=False)
        client = FakeClient(err)
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(task, 1, self._context(root))

            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["data"])
            self.assertEqual(result["error"]["code"], "network_error")

    def test_truncated_stream_exhaustion_returns_failed_and_cleans_scratch(self):
        responses = []

        def opener(req, timeout):
            response = truncated_chunked_response()
            responses.append(response)
            return response

        client = ArxivSourceClient(opener=Mock(open=opener), max_attempts=2, delay=0)
        module = SourcesModule(client, self.extractor)
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            try:
                result = module.fetch_sources(
                    {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                )
            except Exception as exc:
                self.fail(f"Stream failure must return Result, got {type(exc).__name__}: {exc}")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"]["code"], "network_error")
            self.assertIsNone(result["data"])
            self.assertEqual(result["attempts"], 2)
            self.assertFalse((root / "staging").exists())
            self.assertFalse(any(p.is_file() for p in (root / "scratch").rglob("*")))
            self.assertTrue(all(r.isclosed() for r in responses))

    def test_fetch_sources_unsafe_archive_returns_failed(self):
        payload = make_tar_gz([("../evil.tex", b"bad")])
        client = FakeClient(payload)
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(task, 1, self._context(root))

            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["data"])
            self.assertEqual(result["error"]["code"], "unsafe_archive")

    def test_archive_prefix_conflict_returns_failed_and_preserves_raw(self):
        for members in (
            [("main.tex", b"source"), ("main.tex/part.tex", b"part")],
            [("main.tex/part.tex", b"part"), ("main.tex", b"source")],
        ):
            with self.subTest(members=members), tempfile.TemporaryDirectory() as tmp_dir:
                root = Path(tmp_dir)
                accepted = root / "raw/khang/2506-20788/v1/main.tex"
                accepted.parent.mkdir(parents=True)
                accepted.write_bytes(b"previously accepted source")
                module = SourcesModule(FakeClient(make_tar_gz(members)), self.extractor)
                try:
                    result = module.fetch_sources(
                        {"owner": "khang", "arxiv_id": "2506.20788"}, 1, self._context(root),
                    )
                except Exception as exc:
                    self.fail(f"Invalid archive must return Result, got {type(exc).__name__}: {exc}")
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], "unsafe_archive")
                self.assertIsNone(result["data"])
                self.assertFalse((root / "staging").exists())
                self.assertEqual(accepted.read_bytes(), b"previously accepted source")
                self.assertFalse(any(p.is_file() for p in (root / "scratch").rglob("*")))

    def test_occupied_staging_raises_file_exists_error(self):
        payload = make_tar_gz([("main.tex", b"\\documentclass{article}\n")])
        client = FakeClient(payload)
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            context = self._context(root)
            folder = paper_folder("2501.12642")
            existing_staging = root / "staging" / "run_2026" / "khang" / folder / "v1"
            existing_staging.mkdir(parents=True, exist_ok=True)
            (existing_staging / "preexisting.txt").write_text("do not touch")

            with self.assertRaises(FileExistsError):
                module.fetch_sources(task, 1, context)

            # Existing files must remain untouched
            self.assertEqual((existing_staging / "preexisting.txt").read_text(), "do not touch")

    def test_bad_input_validation_raises_value_error(self):
        client = FakeClient(b"")
        module = SourcesModule(client, self.extractor, limits=self.limits)
        context = self._context(Path("/tmp"))

        bad_tasks = [
            {"owner": "unknown_person", "arxiv_id": "2501.12642"},
            {"owner": "khang", "arxiv_id": "http://arxiv.org/abs/2501.12642"},
            {"owner": "khang", "arxiv_id": "2501.12642v1"},
            {"owner": "khang"},
            "not a dict",
        ]
        for bad_task in bad_tasks:
            with self.subTest(task=bad_task):
                with self.assertRaises(ValueError):
                    module.fetch_sources(bad_task, 1, context)

        # Bad versions
        valid_task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}
        for bad_version in [0, -1, True, False, "1", None]:
            with self.subTest(version=bad_version):
                with self.assertRaises(ValueError):
                    module.fetch_sources(valid_task, bad_version, context)

        # Bad context run_id
        bad_context: Context = {"run_id": "invalid run id with spaces!", "output_root": Path("/tmp"), "is_mock": False}
        with self.assertRaises(ValueError):
            module.fetch_sources(valid_task, 1, bad_context)

    def test_end_to_end_tex_commands_and_relative_paths_unchanged(self):
        r"""Commands like \input{...} and \includegraphics{...} must remain bit-exact."""
        raw_main = (
            b"\\documentclass{article}\n"
            b"\\usepackage{graphicx}\n"
            b"\\begin{document}\n"
            b"\\input{sections/methods}\n"
            b"\\includegraphics{figures/loss.png}\n"
            b"\\bibliography{refs}\n"
            b"\\end{document}\n"
        )
        raw_methods = b"\\section{Methods}\nDetailed text here.\n"
        raw_bib = b"@article{good2026, title={Great Paper}}\n"
        raw_fig = b"\x89PNG\r\n\x1a\n" + b"\x00" * 80

        payload = make_tar_gz([
            ("main.tex", raw_main),
            ("sections/methods.tex", raw_methods),
            ("refs.bib", raw_bib),
            ("figures/loss.png", raw_fig),
        ])
        client = FakeClient(payload)
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            result = module.fetch_sources(task, 1, self._context(root))

            self.assertEqual(result["status"], "success")
            data = result["data"]
            self.assertEqual(len(data["files"]), 3)

            folder = paper_folder("2501.12642")
            staging = root / "staging" / "run_2026" / "khang" / folder / "v1"

            # 1. Exact bytes preserved
            self.assertEqual((staging / "main.tex").read_bytes(), raw_main)
            self.assertEqual((staging / "sections" / "methods.tex").read_bytes(), raw_methods)
            self.assertEqual((staging / "refs.bib").read_bytes(), raw_bib)

            # 2. Manifest matches physical files
            for file_entry in data["files"]:
                staged_file = staging / file_entry["path"]
                self.assertTrue(staged_file.exists())
                self.assertEqual(staged_file.stat().st_size, file_entry["size_bytes"])

            # 3. No manifest file or figure was staged
            self.assertFalse((staging / "figures" / "loss.png").exists())
            self.assertFalse((staging / "manifest.json").exists())

    def test_repeated_failure_does_not_mutate_existing_raw(self):
        """Repeated failure must not overwrite an existing accepted raw/ or contaminate staging."""
        client = FakeClient(RemoteError("remote_error", "HTTP 500 error", retryable=False, http_status=500))
        module = SourcesModule(client, self.extractor, limits=self.limits)
        task: PaperTask = {"owner": "khang", "arxiv_id": "2501.12642"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            folder = paper_folder("2501.12642")
            raw_target = root / "raw" / "khang" / folder / "v1"
            raw_target.mkdir(parents=True, exist_ok=True)
            marker_file = raw_target / "accepted_content.tex"
            marker_file.write_text("pre-existing accepted version")

            # Run failing fetch
            result = module.fetch_sources(task, 1, self._context(root))
            self.assertEqual(result["status"], "failed")

            # Verify raw marker is intact
            self.assertTrue(marker_file.exists())
            self.assertEqual(marker_file.read_text(), "pre-existing accepted version")

            # Verify staging is not left over
            staging_dir = root / "staging" / "run_2026" / "khang" / folder / "v1"
            self.assertFalse(staging_dir.exists())


if __name__ == "__main__":
    unittest.main()
