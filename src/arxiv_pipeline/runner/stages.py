"""Invocation-level stage policy, deliberately separate from run identity."""

from typing import cast, get_args

from ..pipeline_contracts import Stage


def validate_stage(stage: str) -> Stage:
    if stage not in get_args(Stage):
        raise ValueError("stage must be discovery, sources, references or all")
    return cast(Stage, stage)
