"""Payload extraction handlers for arXiv source formats."""

from arxiv_pipeline.sources.handlers.base import (
    ExtractionLimits,
    ExtractionResult,
    FIGURE_EXTENSIONS,
    RETAIN_EXTENSIONS,
    SourceHandler,
    SourceProcessingError,
    classify_file,
    is_tex_content,
    is_text_data,
    to_relative_path,
)
from arxiv_pipeline.sources.handlers.gzip import GzipTexHandler
from arxiv_pipeline.sources.handlers.tar import TarHandler
from arxiv_pipeline.sources.handlers.tex import TexHandler

__all__ = [
    "ExtractionLimits",
    "ExtractionResult",
    "FIGURE_EXTENSIONS",
    "GzipTexHandler",
    "RETAIN_EXTENSIONS",
    "SourceHandler",
    "SourceProcessingError",
    "TarHandler",
    "TexHandler",
    "classify_file",
    "is_tex_content",
    "is_text_data",
    "to_relative_path",
]
