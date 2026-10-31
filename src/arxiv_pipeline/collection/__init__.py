"""Verify stopped runs and assemble one checked collection from team snapshots.

Import RunInspector from .inspector explicitly: it needs the runner's SQLite state.
"""
from .assembler import CollectionAssembler
from .reader import CollectionDirectoryReader
from .service import CollectionService
from .statistics import CollectionStatisticsBuilder, OwnerSummarizer, combine_summaries
