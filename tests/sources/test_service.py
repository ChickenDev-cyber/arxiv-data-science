"""Tests for T3 SourcesModule service and Result schema-1.1 contracts."""

import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock

from arxiv_pipeline.contracts import Context, PaperTask, SCHEMA_VERSION, paper_folder
from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.sources import SourcesModule
from arxiv_pipeline.sources.extractor import ExtractionLimits, SourceExtractor


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


if __name__ == "__main__":
    unittest.main()
