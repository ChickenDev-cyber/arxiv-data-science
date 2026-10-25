from pathlib import Path
import tempfile
import unittest
from arxiv_pipeline.validation.artifacts import validate_source_files
from .helpers import source_result


class ArtifactTests(unittest.TestCase):
    def test_files_match_manifest_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); r = source_result(root)
            ctx = {"output_root": root, "run_id": "mock-demo", "is_mock": True}
            self.assertEqual(validate_source_files(r, ctx), [])
            (root / "staging/mock-demo/trong/2504-17523/v1/main.tex").write_bytes(b"four")
            self.assertTrue(validate_source_files(r, ctx))

    def test_extra_files_symlinks_and_unsafe_paths(self):
        for mutation in ("extra", "symlink", "parent", "duplicate", "bytes", "directory"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); r = source_result(root)
                stage = root / "staging/mock-demo/trong/2504-17523/v1"
                if mutation == "extra": (stage / "plot.png").write_bytes(b"png")
                if mutation == "symlink":
                    (stage / "main.tex").unlink(); (stage / "main.tex").symlink_to(stage / "refs.bib")
                if mutation == "parent": r["data"]["files"][0]["path"] = "../main.tex"
                if mutation == "duplicate": r["data"]["files"].append(dict(r["data"]["files"][0]))
                if mutation == "bytes": r["metrics"]["retained_source_bytes"] = 20
                if mutation == "directory": r["data"]["source_dir"] = "raw/elsewhere"
                self.assertTrue(validate_source_files(r, {"output_root": root, "run_id": "mock-demo", "is_mock": True}))
