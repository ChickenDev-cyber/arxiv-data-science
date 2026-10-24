"""Format detection and extraction orchestration for arXiv source packages."""

import gzip
from pathlib import Path
import tarfile
from xml.etree import ElementTree as ET
import zipfile
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
            header = f.read(4096)
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
                if is_tex_content(sample):
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
    if is_tex_content(header):
        return "tex"

    return "other"


def _is_docx_without_tex(source_path: Path, limits: ExtractionLimits) -> bool:
    """Recognize a conventional OOXML Word package without extracting it.

    Check all members' CRCs, its content type, relationship and main XML;
    ZIP signatures alone do not prove a payload has no TeX source.
    https://learn.microsoft.com/en-us/office/open-xml/word/structure-of-a-wordprocessingml-document
    """
    if not zipfile.is_zipfile(source_path):
        return False

    required = ("[Content_Types].xml", "_rels/.rels", "word/document.xml")
    try:
        with zipfile.ZipFile(source_path) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if not set(required).issubset(names):
                return False
            if len(members) != len(names) or any(name.lower().endswith(".tex") for name in names):
                return False
            if len(members) > limits.max_members:
                raise SourceProcessingError("extract_limit", "DOCX member count exceeds limit")
            if any(member.file_size > limits.max_file_bytes for member in members):
                raise SourceProcessingError("extract_limit", "DOCX member size exceeds limit")
            if sum(member.file_size for member in members) > limits.max_extracted_bytes:
                raise SourceProcessingError("extract_limit", "DOCX cumulative size exceeds limit")

            roots = {}
            inspected_bytes = 0
            for member in members:
                content = bytearray() if member.filename in required else None
                part_bytes = 0
                with archive.open(member) as stream:
                    # Reading to EOF validates CRC; discard auxiliary bytes in bounded chunks.
                    while chunk := stream.read(65536):
                        part_bytes += len(chunk)
                        inspected_bytes += len(chunk)
                        if (part_bytes > limits.max_file_bytes
                                or inspected_bytes > limits.max_extracted_bytes):
                            raise SourceProcessingError("extract_limit", "DOCX inspection size exceeds limit")
                        if content is not None:
                            content.extend(chunk)
                if content is not None:
                    try:
                        roots[member.filename] = ET.fromstring(content)
                    except (LookupError, ValueError):
                        # Expat also reports unsupported XML encodings outside ParseError.
                        return False

            types, relationships, document = (roots[name] for name in required)
            types_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
            rels_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
            word_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            return (
                types.tag == types_ns + "Types"
                and any(
                    part.get("PartName") == "/word/document.xml"
                    and part.get("ContentType") == (
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                    )
                    for part in types.findall(types_ns + "Override")
                )
                and relationships.tag == rels_ns + "Relationships"
                and any(
                    rel.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
                    and rel.get("Target") in ("word/document.xml", "/word/document.xml")
                    and rel.get("TargetMode", "Internal") == "Internal"
                    for rel in relationships.findall(rels_ns + "Relationship")
                )
                and document.tag == word_ns + "document"
                and document.find(word_ns + "body") is not None
            )
    except (zipfile.BadZipFile, ET.ParseError, OSError, EOFError, RuntimeError, NotImplementedError, zlib.error):
        return False


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

        if kind == "pdf_only" or (kind == "other" and _is_docx_without_tex(source_path, limits)):
            metrics: SourceMetrics = {
                "download_bytes": None,
                "extracted_bytes_before_filter": None,
                "retained_source_bytes": 0,
                "removed_figure_bytes": None,
                "removed_other_bytes": None,
            }
            return ExtractionResult(
                status="unsupported",
                source_kind=kind,
                files=[],
                metrics=metrics,
                reason="PDF-only payload without TeX source" if kind == "pdf_only" else "DOCX package without TeX source",
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
