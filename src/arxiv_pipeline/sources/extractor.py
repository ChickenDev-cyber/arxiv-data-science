"""Format detection and extraction orchestration for arXiv source packages."""

import gzip
from pathlib import Path
import tarfile
import zlib

from arxiv_pipeline.contracts import SourceMetrics
from arxiv_pipeline.sources.handlers import (
    ExtractionLimits,
    ExtractionResult,
    FIGURE_EXTENSIONS,
    GzipTexHandler,
    RETAIN_EXTENSIONS,
    SourceHandler,
    SourceProcessingError,
    TarHandler,
    TexHandler,
    classify_file,
    is_tex_content,
    is_text_data,
    to_relative_path,
)


def detect_source_kind(source_path: Path) -> str:
    """Detect arXiv source package format by inspecting magic bytes and structure."""
    if not source_path.is_file():
        raise SourceProcessingError(
            "invalid_source", f"Source file does not exist: {source_path}"
        )

    try:
        with open(source_path, "rb") as f:
            header = f.read(1024)
    except OSError as e:
        raise SourceProcessingError(
            "invalid_source", f"Cannot read source file: {e}"
        ) from e

    if not header:
        raise SourceProcessingError("invalid_source", "Source file is empty")

    if header.startswith(b"%PDF-"):
        return "pdf_only"

    if header.startswith(b"\x1f\x8b"):
        # gzip compressed: could be tar_gzip, gzip_tex, or invalid/other
        try:
            if tarfile.is_tarfile(source_path):
                return "tar_gzip"
        except (tarfile.TarError, EOFError, OSError):
            pass

        try:
            with gzip.open(source_path, "rb") as gz:
                sample = gz.read(4096)
                if is_text_data(sample):
                    return "gzip_tex"
                return "other"
        except (gzip.BadGzipFile, zlib.error, EOFError, OSError):
            return "other"

    # Uncompressed TAR check
    try:
        if tarfile.is_tarfile(source_path):
            return "tar"
    except (tarfile.TarError, EOFError, OSError):
        pass

    # Direct uncompressed text source check
    if is_text_data(header):
        return "tex"

    return "other"


class SourceExtractor:
    """Dispatches extraction based on detected arXiv source kind."""

    def __init__(self) -> None:
        tar_handler = TarHandler()
        self._handlers: dict[str, SourceHandler] = {
            "tar_gzip": tar_handler,
            "tar": tar_handler,
            "gzip_tex": GzipTexHandler(),
            "tex": TexHandler(),
        }

    def extract(
        self,
        source_path: Path,
        destination: Path,
        *,
        limits: ExtractionLimits,
    ) -> ExtractionResult:
        kind = detect_source_kind(source_path)

        if kind == "pdf_only":
            metrics: SourceMetrics = {
                "download_bytes": None,
                "extracted_bytes_before_filter": None,
                "retained_source_bytes": 0,
                "removed_figure_bytes": None,
                "removed_other_bytes": None,
            }
            return ExtractionResult(
                status="unsupported",
                source_kind="pdf_only",
                files=[],
                metrics=metrics,
                reason="PDF-only payload without TeX source",
            )

        handler = self._handlers.get(kind)
        if handler is None:
            raise SourceProcessingError(
                "invalid_source", f"Unsupported or unknown source kind: {kind}"
            )

        files, metrics = handler.extract(source_path, destination, limits=limits)
        if not files:
            return ExtractionResult(
                status="unsupported",
                source_kind=kind,
                files=[],
                metrics=metrics,
                reason="Archive contains no .tex source files",
            )

        return ExtractionResult(
            status="success",
            source_kind=kind,
            files=files,
            metrics=metrics,
            reason=None,
        )


__all__ = [
    "ExtractionLimits",
    "ExtractionResult",
    "FIGURE_EXTENSIONS",
    "GzipTexHandler",
    "RETAIN_EXTENSIONS",
    "SourceExtractor",
    "SourceHandler",
    "SourceProcessingError",
    "TarHandler",
    "TexHandler",
    "classify_file",
    "detect_source_kind",
    "is_tex_content",
    "is_text_data",
    "to_relative_path",
]