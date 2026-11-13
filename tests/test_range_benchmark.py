"""Mẫu benchmark phải đúng dải và không giải nén đường dẫn ngoài corpus."""

import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("benchmark_m2_range", Path(__file__).resolve().parents[1] / "tools/benchmark_m2_range.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_candidates_are_unique_and_in_khang_range():
    ids = benchmark.candidates()
    assert len(ids) == len(set(ids)) == 100
    assert sum(p.startswith("2506.") for p in ids) == 70
    assert sum(p.startswith("2507.") for p in ids) == 30
    assert all((p.startswith("2506.") and int(p[5:]) >= 20788)
               or (p.startswith("2507.") and 1 <= int(p[5:]) <= 1658) for p in ids)


def test_extract_only_keeps_tex_and_bib(tmp_path):
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
    assert sorted(p.name for p in dest.iterdir()) == ["main.tex", "refs.bib"]


def test_extract_rejects_parent_path(tmp_path):
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("../escape.tex")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    dest = tmp_path / "output"
    dest.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        benchmark.extract(archive, dest)
    assert not (tmp_path / "escape.tex").exists()
