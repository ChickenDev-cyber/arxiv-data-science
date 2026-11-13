"""Mẫu benchmark phải đúng dải và không giải nén đường dẫn ngoài corpus."""

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "benchmark_m2_range",
    Path(__file__).resolve().parents[1] / "tools/benchmark_m2_range.py",
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class TestRangeBenchmark(unittest.TestCase):
    def test_candidates_are_unique_and_in_khang_range(self):
        ids = benchmark.candidates()
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(p.startswith("2506.") for p in ids), 70)
        self.assertEqual(sum(p.startswith("2507.") for p in ids), 30)
        self.assertTrue(all(
            (p.startswith("2506.") and int(p[5:]) >= 20788)
            or (p.startswith("2507.") and 1 <= int(p[5:]) <= 1658)
            for p in ids
        ))

    def test_extract_only_keeps_tex_and_bib(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            archive = tmp_path / "source.tar"
            with tarfile.open(archive, "w") as tar:
                for name in ["main.tex", "refs.bib", "image.png"]:
                    data = b"content"
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            dest = tmp_path / "output"
            dest.mkdir()
            benchmark.extract(archive, dest)
            self.assertEqual(sorted(p.name for p in dest.iterdir()), ["main.tex", "refs.bib"])

    def test_extract_rejects_parent_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            archive = tmp_path / "source.tar"
            with tarfile.open(archive, "w") as tar:
                info = tarfile.TarInfo("../escape.tex")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            dest = tmp_path / "output"
            dest.mkdir()
            with self.assertRaisesRegex(ValueError, "unsafe"):
                benchmark.extract(archive, dest)
            self.assertFalse((tmp_path / "escape.tex").exists())


if __name__ == "__main__":
    unittest.main()
