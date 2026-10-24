"""Single TeX source wrapped in a GZIP container extraction handler."""

import gzip
from pathlib import Path
import shutil
import zlib

from arxiv_pipeline.contracts import SourceFile, SourceMetrics
from arxiv_pipeline.sources.handlers.base import (
    ExtractionLimits,
    SourceProcessingError,
    is_text_data,
)


class GzipTexHandler:
    """Extracts a single TeX source wrapped in a GZIP container as main.tex."""

    def extract(
        self,
        source_path: Path,
        destination: Path,
        *,
        limits: ExtractionLimits,
    ) -> tuple[list[SourceFile], SourceMetrics]:
        if not source_path.is_file():
            raise SourceProcessingError(
                "invalid_source", f"Source file does not exist: {source_path}"
            )

        destination.mkdir(parents=True, exist_ok=True)
        dest_file = destination / "main.tex"

        try:
            with gzip.open(source_path, "rb") as gz, open(dest_file, "wb") as f_out:
                written = 0
                sample = b""
                while chunk := gz.read(65536):
                    if len(sample) < 4096:
                        sample += chunk[: 4096 - len(sample)]
                    written += len(chunk)
                    if written > limits.max_file_bytes:
                        raise SourceProcessingError(
                            "extract_limit",
                            f"Decompressed TeX size {written} exceeds limit {limits.max_file_bytes}",
                        )
                    if written > limits.max_extracted_bytes:
                        raise SourceProcessingError(
                            "extract_limit",
                            f"Cumulative extracted bytes {written} exceeds limit {limits.max_extracted_bytes}",
                        )
                    f_out.write(chunk)

            if not is_text_data(sample):
                raise SourceProcessingError(
                    "invalid_source", "Decompressed gzip content is not text source"
                )

        except SourceProcessingError:
            dest_file.unlink(missing_ok=True)
            if destination.exists():
                for item in destination.iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
            raise
        except (gzip.BadGzipFile, zlib.error, EOFError, OSError) as e:
            dest_file.unlink(missing_ok=True)
            if destination.exists():
                for item in destination.iterdir():
                    if item.is_file():
                        item.unlink(missing_ok=True)
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
            raise SourceProcessingError("invalid_source", f"Corrupt gzip source: {e}") from e

        retained_files: list[SourceFile] = [
            {"path": "main.tex", "size_bytes": written}
        ]
        metrics: SourceMetrics = {
            "download_bytes": None,
            "extracted_bytes_before_filter": written,
            "retained_source_bytes": written,
            "removed_figure_bytes": 0,
            "removed_other_bytes": 0,
        }
        return retained_files, metrics
