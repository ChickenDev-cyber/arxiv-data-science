"""Milestone 2, Module 1: multi-file gathering and compilation path."""

from .contracts import GATHERED_SOURCE_KEYS, GatheredSource, GatherStatus
from .service import GatherReport, SourceGatherer, gather_source

__all__ = [
    "GATHERED_SOURCE_KEYS",
    "GatherReport",
    "GatherStatus",
    "GatheredSource",
    "SourceGatherer",
    "gather_source",
]
