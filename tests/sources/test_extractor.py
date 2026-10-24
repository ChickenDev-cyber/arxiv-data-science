import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path, PurePosixPath

from arxiv_pipeline.sources.extractor import (
    ExtractionLimits,
    GzipTexHandler,
    SourceExtractor,
    SourceProcessingError,
    TarHandler,
    TexHandler,
    classify_file,
    detect_source_kind,
    to_relative_path,
)


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
                self.assertEqual(to_relative_path(raw), expected)

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
                    to_relative_path(path)

    def test_safe_relative_path_rejects_empty_and_dot_only(self):
        invalid = ["", ".", "./", "./.", "./..", "..", "   ", "a/./b.tex"]
        for path in invalid:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    to_relative_path(path)

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


class TarHandlerArchiveRealWorldTests(unittest.TestCase):
    def setUp(self):
        self.handler = TarHandler()
        self.limits = ExtractionLimits()

    def _create_tar(self, members: list[tuple[str, bytes | None, bool]]) -> Path:
        """Create a temporary TAR archive from a list of (name, content_or_none, is_dir)."""
        temp_file = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
        temp_file.close()
        path = Path(temp_file.name)

        with tarfile.open(path, mode="w") as tar:
            for name, content, is_dir in members:
                ti = tarfile.TarInfo(name=name)
                if is_dir:
                    ti.type = tarfile.DIRTYPE
                    tar.addfile(ti)
                else:
                    assert content is not None
                    ti.type = tarfile.REGTYPE
                    ti.size = len(content)
                    tar.addfile(ti, io.BytesIO(content))
        return path

    def test_extract_archive_with_root_dot_and_nested_directories_and_dot_slash(self):
        """Standard archives with root '.', nested 'chapters/', and './main.tex' extract safely."""
        members = [
            (".", None, True),                        # Root directory '.'
            ("./", None, True),                       # Root directory './'
            ("chapters/", None, True),                # Nested directory 'chapters/'
            ("figures/", None, True),                 # Nested directory 'figures/'
            ("./main.tex", b"\\documentclass{article}\n", False),   # Leading './'
            ("chapters/intro.tex", b"\\section{Intro}\n", False),   # Nested .tex
            ("refs.bib", b"@article{demo,}\n", False),              # .bib file
            ("figures/plot.png", b"\x89PNG\r\n\x1a\n" + b"0" * 40, False),  # Figure
            ("custom.sty", b"\\ProvidesPackage{custom}\n", False),  # Other (.sty)
        ]
        tar_path = self._create_tar(members)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                files, metrics = self.handler.extract(tar_path, dest, limits=self.limits)

                # Only .tex and .bib are retained
                expected_paths = {"main.tex", "chapters/intro.tex", "refs.bib"}
                retained_paths = {f["path"] for f in files}
                self.assertEqual(retained_paths, expected_paths)

                # Verify files physically exist on disk with correct content
                self.assertEqual((dest / "main.tex").read_bytes(), b"\\documentclass{article}\n")
                self.assertEqual((dest / "chapters/intro.tex").read_bytes(), b"\\section{Intro}\n")
                self.assertEqual((dest / "refs.bib").read_bytes(), b"@article{demo,}\n")

                # Verify figures and .sty were NOT extracted to disk
                self.assertFalse((dest / "figures/plot.png").exists())
                self.assertFalse((dest / "custom.sty").exists())

                # Directory members are excluded from byte calculations
                tex_bytes = len(b"\\documentclass{article}\n") + len(b"\\section{Intro}\n") + len(b"@article{demo,}\n")
                fig_bytes = len(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
                sty_bytes = len(b"\\ProvidesPackage{custom}\n")
                total_regular_bytes = tex_bytes + fig_bytes + sty_bytes

                self.assertEqual(metrics["extracted_bytes_before_filter"], total_regular_bytes)
                self.assertEqual(metrics["retained_source_bytes"], tex_bytes)
                self.assertEqual(metrics["removed_figure_bytes"], fig_bytes)
                self.assertEqual(metrics["removed_other_bytes"], sty_bytes)
                self.assertIsNone(metrics["download_bytes"])

                # Invariant holds
                self.assertEqual(
                    metrics["extracted_bytes_before_filter"],
                    metrics["retained_source_bytes"] + metrics["removed_figure_bytes"] + metrics["removed_other_bytes"],
                )
        finally:
            tar_path.unlink(missing_ok=True)

    def test_extract_archive_rejects_duplicate_members_exact_path(self):
        """Archive with duplicate member names must be rejected."""
        members = [
            ("main.tex", b"Version 1", False),
            ("main.tex", b"Version 2", False),
        ]
        tar_path = self._create_tar(members)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
                self.assertIn("Duplicate member path", str(ctx.exception))
        finally:
            tar_path.unlink(missing_ok=True)

    def test_extract_archive_rejects_duplicate_members_after_normalization(self):
        """Archive with './main.tex' and 'main.tex' must be detected as duplicate and rejected."""
        members = [
            ("./main.tex", b"Version 1", False),
            ("main.tex", b"Version 2", False),
        ]
        tar_path = self._create_tar(members)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
                self.assertIn("Duplicate member path", str(ctx.exception))
        finally:
            tar_path.unlink(missing_ok=True)


class TarSafetyAndLimitsTests(unittest.TestCase):
    def setUp(self):
        self.handler = TarHandler()
        self.limits = ExtractionLimits()

    def _create_tar_file(self, populate_fn) -> Path:
        temp_file = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
        temp_file.close()
        path = Path(temp_file.name)
        with tarfile.open(path, mode="w") as tar:
            populate_fn(tar)
        return path

    def test_reject_parent_traversal_outside(self):
        def populate(tar):
            ti = tarfile.TarInfo(name="../outside.tex")
            content = b"evil"
            ti.size = len(content)
            tar.addfile(ti, io.BytesIO(content))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_absolute_path(self):
        def populate(tar):
            ti = tarfile.TarInfo(name="/tmp/x.tex")
            content = b"evil"
            ti.size = len(content)
            tar.addfile(ti, io.BytesIO(content))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_symlink(self):
        def populate(tar):
            ti = tarfile.TarInfo(name="link.tex")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            tar.addfile(ti)

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_hardlink(self):
        def populate(tar):
            ti1 = tarfile.TarInfo(name="main.tex")
            ti1.size = 4
            tar.addfile(ti1, io.BytesIO(b"data"))
            ti2 = tarfile.TarInfo(name="hardlink.tex")
            ti2.type = tarfile.LNKTYPE
            ti2.linkname = "main.tex"
            tar.addfile(ti2)

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_special_member_fifo_and_devices(self):
        for member_type, name in [(tarfile.FIFOTYPE, "fifo_pipe"), (tarfile.CHRTYPE, "char_dev")]:
            with self.subTest(member_type=member_type):
                def populate(tar):
                    ti = tarfile.TarInfo(name=name)
                    ti.type = member_type
                    tar.addfile(ti)

                tar_path = self._create_tar_file(populate)
                try:
                    with tempfile.TemporaryDirectory() as dest_dir:
                        with self.assertRaises(SourceProcessingError) as ctx:
                            self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                        self.assertEqual(ctx.exception.code, "unsafe_archive")
                finally:
                    tar_path.unlink(missing_ok=True)

    def test_reject_unsafe_directory_traversal(self):
        def populate(tar):
            ti = tarfile.TarInfo(name="../escape_dir")
            ti.type = tarfile.DIRTYPE
            tar.addfile(ti)

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_oversized_member(self):
        custom_limits = ExtractionLimits(max_file_bytes=50)

        def populate(tar):
            ti = tarfile.TarInfo(name="main.tex")
            content = b"x" * 60
            ti.size = len(content)
            tar.addfile(ti, io.BytesIO(content))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=custom_limits)
                self.assertEqual(ctx.exception.code, "extract_limit")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_too_many_members(self):
        custom_limits = ExtractionLimits(max_members=2)

        def populate(tar):
            for i in range(3):
                ti = tarfile.TarInfo(name=f"file{i}.tex")
                ti.size = 1
                tar.addfile(ti, io.BytesIO(b"x"))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=custom_limits)
                self.assertEqual(ctx.exception.code, "extract_limit")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_cumulative_extracted_limit(self):
        custom_limits = ExtractionLimits(max_extracted_bytes=100)

        def populate(tar):
            for i in range(3):
                ti = tarfile.TarInfo(name=f"part{i}.tex")
                content = b"x" * 40
                ti.size = len(content)
                tar.addfile(ti, io.BytesIO(content))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=custom_limits)
                self.assertEqual(ctx.exception.code, "extract_limit")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_reject_truncated_or_corrupt_tar(self):
        temp_file = tempfile.NamedTemporaryFile(suffix=".tar", delete=False)
        temp_file.write(b"NOT A VALID TAR FILE AT ALL 1234567890")
        temp_file.close()
        tar_path = Path(temp_file.name)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "invalid_source")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_bib_only_archive_retains_no_files_and_shifts_to_removed_other(self):
        def populate(tar):
            ti_bib = tarfile.TarInfo(name="references.bib")
            content_bib = b"@article{test, author={A}}\n"
            ti_bib.size = len(content_bib)
            tar.addfile(ti_bib, io.BytesIO(content_bib))

            ti_sty = tarfile.TarInfo(name="custom.sty")
            content_sty = b"\\ProvidesPackage{custom}\n"
            ti_sty.size = len(content_sty)
            tar.addfile(ti_sty, io.BytesIO(content_sty))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                files, metrics = self.handler.extract(tar_path, dest, limits=self.limits)

                # No .tex was present, so no files are accepted/retained
                self.assertEqual(files, [])
                self.assertEqual(metrics["retained_source_bytes"], 0)
                expected_total = len(b"@article{test, author={A}}\n") + len(b"\\ProvidesPackage{custom}\n")
                self.assertEqual(metrics["extracted_bytes_before_filter"], expected_total)
                self.assertEqual(metrics["removed_other_bytes"], expected_total)
                self.assertEqual(metrics["removed_figure_bytes"], 0)

                # Destination has no retained files on disk
                disk_files = [p for p in dest.rglob("*") if p.is_file()]
                self.assertEqual(disk_files, [])

                # Invariant holds
                self.assertEqual(
                    metrics["extracted_bytes_before_filter"],
                    metrics["retained_source_bytes"] + metrics["removed_figure_bytes"] + metrics["removed_other_bytes"],
                )
        finally:
            tar_path.unlink(missing_ok=True)

    def test_figure_only_archive_retains_no_files(self):
        def populate(tar):
            ti_fig = tarfile.TarInfo(name="figures/plot.png")
            content_fig = b"\x89PNG\r\n\x1a\n" + b"0" * 50
            ti_fig.size = len(content_fig)
            tar.addfile(ti_fig, io.BytesIO(content_fig))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                files, metrics = self.handler.extract(tar_path, dest, limits=self.limits)

                self.assertEqual(files, [])
                self.assertEqual(metrics["retained_source_bytes"], 0)
                self.assertEqual(metrics["removed_figure_bytes"], len(b"\x89PNG\r\n\x1a\n" + b"0" * 50))
                self.assertEqual(metrics["removed_other_bytes"], 0)
                self.assertEqual(metrics["extracted_bytes_before_filter"], metrics["removed_figure_bytes"])

                # Invariant holds
                self.assertEqual(
                    metrics["extracted_bytes_before_filter"],
                    metrics["retained_source_bytes"] + metrics["removed_figure_bytes"] + metrics["removed_other_bytes"],
                )
        finally:
            tar_path.unlink(missing_ok=True)

    def test_valid_member_followed_by_unsafe_rolls_back_destination(self):
        def populate(tar):
            # Member 1: Valid .tex file
            ti1 = tarfile.TarInfo(name="main.tex")
            content1 = b"\\documentclass{article}\n"
            ti1.size = len(content1)
            tar.addfile(ti1, io.BytesIO(content1))

            # Member 2: Unsafe member
            ti2 = tarfile.TarInfo(name="../escape.tex")
            content2 = b"malicious"
            ti2.size = len(content2)
            tar.addfile(ti2, io.BytesIO(content2))

        tar_path = self._create_tar_file(populate)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.handler.extract(tar_path, dest, limits=self.limits)
                self.assertEqual(ctx.exception.code, "unsafe_archive")

                # Verify rollback: no leftover files in destination
                disk_files = [p for p in dest.rglob("*") if p.is_file()]
                self.assertEqual(disk_files, [], "Destination must be completely cleaned on failure")
        finally:
            tar_path.unlink(missing_ok=True)

    def test_source_processing_error_info_representation(self):
        err = SourceProcessingError("extract_limit", "Exceeded size limit")
        self.assertEqual(err.code, "extract_limit")
        self.assertEqual(err.info["code"], "extract_limit")
        self.assertEqual(err.info["message"], "Exceeded size limit")
        self.assertFalse(err.info["retryable"])
        self.assertIsNone(err.info["http_status"])


class FormatSniffingAndSingleFileStrategyTests(unittest.TestCase):
    def setUp(self):
        self.extractor = SourceExtractor()
        self.limits = ExtractionLimits()

    def _write_temp_file(self, content: bytes, suffix: str = ".bin") -> Path:
        tf = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tf.write(content)
        tf.close()
        return Path(tf.name)

    def test_detect_genuine_tar_gzip_even_with_misleading_suffix(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            ti = tarfile.TarInfo(name="main.tex")
            data = b"\\documentclass{article}"
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        path = self._write_temp_file(buf.getvalue(), suffix=".pdf")  # Misleading suffix
        try:
            self.assertEqual(detect_source_kind(path), "tar_gzip")
        finally:
            path.unlink(missing_ok=True)

    def test_detect_plain_tar_even_with_misleading_suffix(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:") as tar:
            ti = tarfile.TarInfo(name="main.tex")
            data = b"\\documentclass{article}"
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

        path = self._write_temp_file(buf.getvalue(), suffix=".txt")  # Misleading suffix
        try:
            self.assertEqual(detect_source_kind(path), "tar")
        finally:
            path.unlink(missing_ok=True)

    def test_detect_gzip_single_tex_even_with_misleading_suffix(self):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(b"\\documentclass{article}\n\\begin{document}\nHello World\n\\end{document}")

        path = self._write_temp_file(buf.getvalue(), suffix=".tar.gz")  # Misleading suffix
        try:
            self.assertEqual(detect_source_kind(path), "gzip_tex")
        finally:
            path.unlink(missing_ok=True)

    def test_detect_direct_tex_even_with_misleading_suffix(self):
        content = b"\\documentclass{article}\n\\title{Sample Paper}\n\\begin{document}\nText\n\\end{document}"
        path = self._write_temp_file(content, suffix=".dat")  # Misleading suffix
        try:
            self.assertEqual(detect_source_kind(path), "tex")
        finally:
            path.unlink(missing_ok=True)

    def test_detect_pdf_only_payload(self):
        content = b"%PDF-1.5\n1 0 obj\n<<>>\nendobj"
        path = self._write_temp_file(content, suffix=".tar")  # Misleading suffix
        try:
            self.assertEqual(detect_source_kind(path), "pdf_only")
        finally:
            path.unlink(missing_ok=True)

    def test_detect_gzip_arbitrary_binary_returns_other(self):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(b"\x00\x01\x02\x03\x04\xff\xfe\xfd" * 10)

        path = self._write_temp_file(buf.getvalue())
        try:
            self.assertEqual(detect_source_kind(path), "other")
        finally:
            path.unlink(missing_ok=True)

    def test_detect_corrupted_gzip_returns_other(self):
        # Starts with gzip magic bytes \x1f\x8b but corrupted payload
        corrupt_data = b"\x1f\x8b\x08\x00truncatedgarbage"
        path = self._write_temp_file(corrupt_data)
        try:
            self.assertEqual(detect_source_kind(path), "other")
        finally:
            path.unlink(missing_ok=True)

    def test_gzip_tex_handler_preserves_non_ascii_and_produces_main_tex(self):
        raw_tex = "\\documentclass{article}\n\\section{Hà Nội & ĐHQG: α, β, γ}\n\\begin{document}\nText\n\\end{document}\n".encode("utf-8")
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(raw_tex)

        path = self._write_temp_file(buf.getvalue(), suffix=".gz")
        handler = GzipTexHandler()
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                files, metrics = handler.extract(path, dest, limits=self.limits)

                self.assertEqual(len(files), 1)
                self.assertEqual(files[0]["path"], "main.tex")
                self.assertEqual(files[0]["size_bytes"], len(raw_tex))

                extracted_path = dest / "main.tex"
                self.assertTrue(extracted_path.exists())
                self.assertEqual(extracted_path.read_bytes(), raw_tex)

                self.assertEqual(metrics["extracted_bytes_before_filter"], len(raw_tex))
                self.assertEqual(metrics["retained_source_bytes"], len(raw_tex))
                self.assertEqual(metrics["removed_figure_bytes"], 0)
                self.assertEqual(metrics["removed_other_bytes"], 0)
                self.assertIsNone(metrics["download_bytes"])
        finally:
            path.unlink(missing_ok=True)

    def test_tex_handler_preserves_non_ascii_and_produces_main_tex(self):
        raw_tex = "\\documentclass{article}\n\\title{Tiếng Việt: Thử nghiệm}\n\\begin{document}\nContent\n\\end{document}\n".encode("utf-8")
        path = self._write_temp_file(raw_tex, suffix=".tex")
        handler = TexHandler()
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                files, metrics = handler.extract(path, dest, limits=self.limits)

                self.assertEqual(len(files), 1)
                self.assertEqual(files[0]["path"], "main.tex")
                self.assertEqual(files[0]["size_bytes"], len(raw_tex))

                extracted_path = dest / "main.tex"
                self.assertTrue(extracted_path.exists())
                self.assertEqual(extracted_path.read_bytes(), raw_tex)

                self.assertEqual(metrics["extracted_bytes_before_filter"], len(raw_tex))
                self.assertEqual(metrics["retained_source_bytes"], len(raw_tex))
                self.assertEqual(metrics["removed_figure_bytes"], 0)
                self.assertEqual(metrics["removed_other_bytes"], 0)
        finally:
            path.unlink(missing_ok=True)

    def test_gzip_tex_handler_rejects_expansion_over_limit(self):
        raw_tex = b"\\documentclass{article}\n" + (b"% comment\n" * 100)
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(raw_tex)

        path = self._write_temp_file(buf.getvalue(), suffix=".gz")
        handler = GzipTexHandler()
        custom_limits = ExtractionLimits(max_file_bytes=50)
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                with self.assertRaises(SourceProcessingError) as ctx:
                    handler.extract(path, dest, limits=custom_limits)
                self.assertEqual(ctx.exception.code, "extract_limit")
                # Destination must have no leftover files
                self.assertFalse((dest / "main.tex").exists())
        finally:
            path.unlink(missing_ok=True)

    def test_source_extractor_dispatches_correctly_and_handles_pdf_only(self):
        # 1. PDF-only returns explicit unsupported status and reason
        pdf_path = self._write_temp_file(b"%PDF-1.4\n1 0 obj\n")
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                res = self.extractor.extract(pdf_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(res.status, "unsupported")
                self.assertEqual(res.source_kind, "pdf_only")
                self.assertEqual(res.kind, "pdf_only")
                self.assertEqual(res.reason, "PDF-only payload without TeX source")
                self.assertEqual(res.files, [])
                self.assertEqual(res.metrics["retained_source_bytes"], 0)
                self.assertIsNone(res.metrics["extracted_bytes_before_filter"])
                self.assertIsNone(res.metrics["removed_figure_bytes"])
                self.assertIsNone(res.metrics["removed_other_bytes"])
        finally:
            pdf_path.unlink(missing_ok=True)

        # 2. Direct TeX returns success status
        tex_path = self._write_temp_file(b"\\documentclass{article}\n\\begin{document}\nOk\n\\end{document}")
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                res = self.extractor.extract(tex_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(res.status, "success")
                self.assertEqual(res.source_kind, "tex")
                self.assertEqual(res.kind, "tex")
                self.assertIsNone(res.reason)
                self.assertEqual(len(res.files), 1)
                self.assertEqual(res.files[0]["path"], "main.tex")
        finally:
            tex_path.unlink(missing_ok=True)

        # 3. Unknown binary raises invalid_source
        bin_path = self._write_temp_file(b"\x00\x00\x00\x00NOT_VALID")
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                with self.assertRaises(SourceProcessingError) as ctx:
                    self.extractor.extract(bin_path, Path(dest_dir), limits=self.limits)
                self.assertEqual(ctx.exception.code, "invalid_source")
        finally:
            bin_path.unlink(missing_ok=True)

    def test_source_extractor_tar_without_tex_returns_explicit_unsupported(self):
        # Archive containing only a .bib file and documentation
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            ti = tarfile.TarInfo(name="refs.bib")
            data = b"@article{key, title={T}}\n"
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

            ti_doc = tarfile.TarInfo(name="README.md")
            data_doc = b"# Documentation\n"
            ti_doc.size = len(data_doc)
            tar.addfile(ti_doc, io.BytesIO(data_doc))

        path = self._write_temp_file(buf.getvalue(), suffix=".tar.gz")
        try:
            with tempfile.TemporaryDirectory() as dest_dir:
                dest = Path(dest_dir)
                res = self.extractor.extract(path, dest, limits=self.limits)

                self.assertEqual(res.status, "unsupported")
                self.assertEqual(res.source_kind, "tar_gzip")
                self.assertEqual(res.files, [])
                self.assertEqual(res.reason, "Archive contains no .tex source files")
                self.assertEqual(res.metrics["retained_source_bytes"], 0)
                self.assertEqual(
                    res.metrics["extracted_bytes_before_filter"],
                    res.metrics["removed_other_bytes"],
                )
                # Verify destination is clean on disk
                disk_files = [p for p in dest.rglob("*") if p.is_file()]
                self.assertEqual(disk_files, [])
        finally:
            path.unlink(missing_ok=True)

    def test_detect_direct_tex_with_long_comments_before_commands(self):
        # A TeX file with >4KB of comment lines before any LaTeX commands
        long_preamble = b"% This is a comment line explaining copyright and usage.\n" * 100
        self.assertGreater(len(long_preamble), 5000)
        content = long_preamble + b"\\documentclass{article}\n\\begin{document}\nHello\n\\end{document}\n"

        path = self._write_temp_file(content, suffix=".tex")
        try:
            # Must detect as "tex" without false negative from keyword check
            self.assertEqual(detect_source_kind(path), "tex")

            with tempfile.TemporaryDirectory() as dest_dir:
                res = self.extractor.extract(path, Path(dest_dir), limits=self.limits)
                self.assertEqual(res.status, "success")
                self.assertEqual(res.source_kind, "tex")
                self.assertEqual(len(res.files), 1)
                self.assertEqual(res.files[0]["path"], "main.tex")
                self.assertEqual(res.files[0]["size_bytes"], len(content))
        finally:
            path.unlink(missing_ok=True)

    def test_detect_gzip_tex_with_long_comments_before_commands(self):
        # Gzip compressed single TeX file with >4KB of comments
        long_preamble = b"% Comment header line.\n" * 250
        self.assertGreater(len(long_preamble), 5000)
        content = long_preamble + b"\\documentclass{article}\n\\begin{document}\nBody\n\\end{document}\n"

        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(content)

        path = self._write_temp_file(buf.getvalue(), suffix=".gz")
        try:
            self.assertEqual(detect_source_kind(path), "gzip_tex")

            with tempfile.TemporaryDirectory() as dest_dir:
                res = self.extractor.extract(path, Path(dest_dir), limits=self.limits)
                self.assertEqual(res.status, "success")
                self.assertEqual(res.source_kind, "gzip_tex")
                self.assertEqual(len(res.files), 1)
                self.assertEqual(res.files[0]["path"], "main.tex")
                self.assertEqual(res.files[0]["size_bytes"], len(content))
        finally:
            path.unlink(missing_ok=True)


class HandlersPackageModularStructureTests(unittest.TestCase):
    def test_import_handlers_directly_from_submodules(self):
        from arxiv_pipeline.sources.handlers.base import SourceHandler, ExtractionLimits, ExtractionResult
        from arxiv_pipeline.sources.handlers.tar import TarHandler
        from arxiv_pipeline.sources.handlers.gzip import GzipTexHandler
        from arxiv_pipeline.sources.handlers.tex import TexHandler

        self.assertTrue(issubclass(TarHandler, object))
        self.assertTrue(issubclass(GzipTexHandler, object))
        self.assertTrue(issubclass(TexHandler, object))

    def test_import_from_sources_handler_alias(self):
        from arxiv_pipeline.sources import handler, handlers
        self.assertIs(handler, handlers)
        self.assertTrue(hasattr(handler, "TarHandler"))
        self.assertTrue(hasattr(handler, "GzipTexHandler"))
        self.assertTrue(hasattr(handler, "TexHandler"))


if __name__ == "__main__":
    unittest.main()
