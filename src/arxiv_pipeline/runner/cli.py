"""CLI for stage selection, the baseline adapters and same-run resume."""

import argparse
from functools import partial
from importlib import import_module, util
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import get_args

from ..config import OWNERS, ASSIGNED_RANGES
from ..http import RequestScheduler
from ..pipeline_contracts import Stage
from .checkpoints import CheckpointValidator, InMemoryCheckpointStore
from .factory import LiveModuleFactory, MockModuleFactory
from .models import ModuleSet, RunOptions
from .progress import TerminalProgress
from .service import PipelineRunner
from .state import load_options


def make_live_modules(options: RunOptions, scheduler: RequestScheduler) -> ModuleSet:
    """Legacy builder for callers supplying a scheduler; CLI uses a durable factory."""
    return LiveModuleFactory(scheduler=scheduler).build(options, InMemoryCheckpointStore())


def checkpoint_validator() -> CheckpointValidator | None:
    """Use the member-owned semantic validator when it is explicitly available."""
    name = 'arxiv_pipeline.validation.pipeline_contracts'
    if util.find_spec(name) is None:
        return None
    module = import_module(name)
    return partial(module.validate_pipeline_document, kind='checkpoint')


def producer_revision() -> str | None:
    """Only label clean, committed pipeline sources; never label working edits as HEAD."""
    root = Path(__file__).resolve().parents[3]
    try:
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=root, text=True,
                                           stderr=subprocess.DEVNULL).strip()
        if Path(git('rev-parse', '--show-toplevel')).resolve() != root:
            return None
        if git('status', '--porcelain', '--untracked-files=all', '--',
               'src/arxiv_pipeline', 'src/requirements.txt'):
            return None
        revision = git('rev-parse', 'HEAD')
        return revision if re.fullmatch(r'[0-9a-f]{40}', revision) else None
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--owner', action='append', choices=sorted(OWNERS))
    group.add_argument('--all-owners', action='store_true')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--stage', choices=get_args(Stage), default='all', help='Processing stage (default: all)')
    parser.add_argument('--mock', action='store_true', help='Synthetic adapters; no network; separate output root')
    parser.add_argument('--resume', action='store_true', help='Reuse the saved run configuration')
    parser.add_argument('--max-candidates', type=int, help='Candidate IDs examined per owner (default 10)')
    parser.add_argument('--max-papers', type=int, help='Verified papers selected per owner (default 5)')
    parser.add_argument('--full-range', action='store_true', help='Explicitly remove both sample limits')
    parser.add_argument('--stop-after-tasks', type=int, help='Pause at a task boundary for a resume check')
    parser.add_argument('--quiet', action='store_true', help='Hide terminal progress; keep JSON summary and file logs')
    args = parser.parse_args(argv)
    if args.resume and (args.owner or args.all_owners or args.mock or args.full_range
                        or args.max_candidates is not None or args.max_papers is not None):
        parser.error('--resume reads owners/mode/limits from state; do not supply them again')
    if not args.resume and not (args.owner or args.all_owners):
        parser.error('Choose --owner or --all-owners')
    if args.full_range and (args.max_candidates is not None or args.max_papers is not None):
        parser.error('--full-range excludes sample limit flags')
    try:
        if args.resume:
            options = load_options(args.output_root / 'state.sqlite', args.run_id)
            if options.output_root.resolve() != args.output_root.resolve():
                raise ValueError('Saved run belongs to another output root; use a verified restore')
        else:
            owners = tuple(c['owner'] for c in ASSIGNED_RANGES) if args.all_owners else tuple(args.owner)
            options = RunOptions(args.run_id, args.output_root, owners, args.mock,
                                 None if args.full_range else 10 if args.max_candidates is None else args.max_candidates,
                                 None if args.full_range else 5 if args.max_papers is None else args.max_papers)
        factory = MockModuleFactory() if options.is_mock else LiveModuleFactory()
        runner = PipelineRunner(factory, producer_commit=producer_revision(), validate_record=checkpoint_validator())
        with TerminalProgress(enabled=not args.quiet) as progress:
            progress(f"Bắt đầu run {options.run_id} | {'mock' if options.is_mock else 'dữ liệu thật'} | "
                     f'{len(options.owners)} dải | công đoạn {args.stage}')
            result = runner.run(options, stage=args.stage, stop_after_tasks=args.stop_after_tasks, progress=progress)
            progress(f'Kết thúc | {result.executed_tasks} task mới | {result.reused_tasks} task dùng lại | '
                     f'mã thoát {result.exit_code}')
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'T5: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'run_id': options.run_id, 'is_mock': options.is_mock, 'stage': args.stage,
                      'papers': result.scope['scope']['paper_count'],
                      'discovery_complete': result.scope['scope']['discovery_complete'],
                      'executed_tasks': result.executed_tasks, 'reused_tasks': result.reused_tasks,
                      'validation_valid': result.validation['valid'], 'quality_valid': result.quality['valid'],
                      'reports': str(options.output_root / 'runs' / options.run_id)}, ensure_ascii=False))
    return result.exit_code
