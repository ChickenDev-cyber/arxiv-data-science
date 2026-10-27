"""Verify stopped runs and assemble one checked collection from team snapshots.

Import RunInspector from .inspector explicitly: it needs the runner's SQLite state.
"""
from .assembler import Assembly, CollectionAssembler
from .reader import CollectionDirectoryReader
from .statistics import CollectionStatisticsBuilder, OwnerSummarizer, combine_summaries
