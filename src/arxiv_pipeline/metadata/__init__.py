"""T2 metadata: verify each paper's metadata and version history."""

from .service import MetadataModule, missing_fields, validate_task

__all__ = ["MetadataModule", "missing_fields", "validate_task"]
