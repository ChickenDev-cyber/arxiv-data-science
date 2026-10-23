"""Direct uncompressed TeX source file extraction handler."""

from pathlib import Path
import shutil

from arxiv_pipeline.contracts import SourceFile, SourceMetrics
from arxiv_pipeline.sources.handlers.base import (
    ExtractionLimits,
    SourceProcessingError,
    is_text_data,
)


class TexHandler:
    """Extracts a direct uncompressed TeX source file as main.tex."""

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

        file_size = source_path.stat().st_size
        if file_size > limits.max_file_bytes:
            raise SourceProcessingError(
                "extract_limit",
                f"TeX file size {file_size} exceeds limit {limits.max_file_bytes}",
            )
        if file_size > limits.max_extracted_bytes:
            raise SourceProcessingError(
                "extract_limit",
                f"TeX file size {file_size} exceeds limit {limits.max_extracted_bytes}",
            )

        with open(source_path, "rb") as f_in:
            sample = f_in.read(4096)
        if not is_text_data(sample):
            raise SourceProcessingError(
                "invalid_source", "Source file content is not text source"
            )

        destination.mkdir(parents=True, exist_ok=True)
        dest_file = destination / "main.tex"

        try:
            with open(source_path, "rb") as f_in, open(dest_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        except Exception:
            dest_file.unlink(missing_ok=True)
            raise

        retained_files: list[SourceFile] = [
            {"path": "main.tex", "size_bytes": file_size}
        ]
        metrics: SourceMetrics = {
            "download_bytes": None,
            "extracted_bytes_before_filter": file_size,
            "retained_source_bytes": file_size,
            "removed_figure_bytes": 0,
            "removed_other_bytes": 0,
        }
        return retained_files, metrics
