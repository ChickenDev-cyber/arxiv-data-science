"""Milestone 3 matching contracts, orchestration, labeling and dataset delivery.

This package contains:
- labeling.py: Label management, manual templates, automatic heuristic matcher, quota and integrity validation (Module B - Mẫn Nhi).
- contracts.py, export.py, pipeline.py, splitting.py: Matching orchestration and dataset delivery (Module E - Trọng).
"""

from .labeling import (
    build_labels,
    generate_manual_template,
    validate_labels,
    run_heuristic_matcher,
    HEURISTIC_VERSION,
)

__all__ = [
    "build_labels",
    "generate_manual_template",
    "validate_labels",
    "run_heuristic_matcher",
    "HEURISTIC_VERSION",
]
