"""Reference Matching Module for Milestone 3 (M3).

This package contains:
- labeling.py: Label management, manual templates, automatic heuristic matcher, quota and integrity validation (Module B - Mẫn Nhi).
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
