"""Task 1 tests: safe relative path policy and file classification."""

import unittest
from pathlib import PurePosixPath

from arxiv_pipeline.sources.extractor import classify_file, safe_relative_path


class SafePathAndClassifierTests(unittest.TestCase):
    def test_safe_relative_path_accepts_main_nested_and_leading_dot_slash(self):
        cases = {
            "main.tex": PurePosixPath("main.tex"),
            "chapters/intro.tex": PurePosixPath("chapters/intro.tex"),
            "./chapters/intro.tex": PurePosixPath("chapters/intro.tex"),
            "./main.tex": PurePosixPath("main.tex"),
            "nested/dir/deep/file.bib": PurePosixPath("nested/dir/deep/file.bib"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(safe_relative_path(raw), expected)

    def test_safe_relative_path_rejects_parent_absolute_windows_backslash_nul(self):
        invalid = [
            "../outside.tex",
            "a/../b.tex",
            "/tmp/x.tex",
            "C:\\tmp\\x.tex",
            "C:/tmp/x.tex",
            "\\\\server\\share\\x.tex",
            "a\\b.tex",
            "safe/path\x00corrupt.tex",
        ]
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    safe_relative_path(path)

    def test_safe_relative_path_rejects_empty_and_dot_only(self):
        invalid = ["", ".", "./", "./.", "./..", "..", "   ", "a/./b.tex"]
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    safe_relative_path(path)

    def test_classify_file_keeps_only_tex_bib_case_insensitive(self):
        retained = [
            PurePosixPath("main.tex"),
            PurePosixPath("Refs.BIB"),
            PurePosixPath("DOCUMENT.TEX"),
            PurePosixPath("sub/dir/biblio.bib"),
            PurePosixPath("PAPER.TeX"),
            PurePosixPath("data.Bib"),
        ]
        for path in retained:
            with self.subTest(path=path):
                self.assertEqual(classify_file(path), "retain")

    def test_classify_file_distinguishes_figure_from_other(self):
        figures = [
            PurePosixPath("plot.png"),
            PurePosixPath("plot.pdf"),
            PurePosixPath("figures/diagram.EPS"),
            PurePosixPath("chart.jpg"),
            PurePosixPath("photo.jpeg"),
            PurePosixPath("icon.svg"),
        ]
        for path in figures:
            with self.subTest(path=path):
                self.assertEqual(classify_file(path), "figure")

        others = [
            PurePosixPath("style.sty"),
            PurePosixPath("README"),
            PurePosixPath("document.bbl"),
            PurePosixPath("article.cls"),
            PurePosixPath("notes.txt"),
            PurePosixPath("Makefile"),
        ]
        for path in others:
            with self.subTest(path=path):
                self.assertEqual(classify_file(path), "other")


if __name__ == "__main__":
    unittest.main()
