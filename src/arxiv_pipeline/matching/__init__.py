"""Milestone 3 matching contracts, orchestration, labeling, evaluation and dataset delivery.

This package contains:
- labeling.py: Label management, manual templates, automatic heuristic matcher, quota and integrity validation (Module B - Mẫn Nhi).
- evaluation.py, validation.py: Module D evaluation and validation pipeline (Huy Hoàng).
- contracts.py, export.py, pipeline.py, splitting.py: Matching orchestration and dataset delivery (Module E - Trọng).
"""

from .labeling import (
    build_labels,
    generate_manual_template,
    validate_labels,
    run_heuristic_matcher,
    HEURISTIC_VERSION,
)
from .evaluation import compute_mrr_at_5, evaluate
from .validation import validate_dataset, validate_prediction_rules

__all__ = [
    "build_labels",
    "generate_manual_template",
    "validate_labels",
    "run_heuristic_matcher",
    "HEURISTIC_VERSION",
    "compute_mrr_at_5",
    "evaluate",
    "validate_dataset",
    "validate_prediction_rules",
]
