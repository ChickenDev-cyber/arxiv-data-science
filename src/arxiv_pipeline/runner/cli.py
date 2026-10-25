"""CLI for the T5 baseline, mock integration and same-run resume."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from ..config import OWNERS,ASSIGNED_RANGES
from ..http import RequestScheduler
from .models import RunOptions,ModuleSet
from .state import load_options
from .mock import make_mock_modules
from .service import run_pipeline


def make_live_modules(options,scheduler):
    from ..discovery.api import ApiClient
    from ..discovery.service import DiscoveryModule
    from ..metadata.api import OaiClient
    from ..metadata.service import MetadataModule
    from ..sources.api import ArxivSourceClient
    from ..sources.service import SourcesModule
    from ..sources.extractor import SourceExtractor
    from ..references.api import SemanticScholarClient,ArxivDateClient
    from ..references.service import ReferencesModule
    return ModuleSet(DiscoveryModule(ApiClient(scheduler=scheduler),max_candidates=options.max_candidates),
                     MetadataModule(OaiClient(scheduler=scheduler)),
                     SourcesModule(ArxivSourceClient(scheduler=scheduler),SourceExtractor()),
                     ReferencesModule(SemanticScholarClient(scheduler=scheduler),ArxivDateClient(scheduler=scheduler)))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--owner',action='append',choices=sorted(OWNERS))
    group.add_argument('--all-owners',action='store_true')
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--mock',action='store_true',help='Synthetic adapters; no network; separate output root')
    parser.add_argument('--resume',action='store_true',help='Reuse the saved run configuration')
    parser.add_argument('--max-candidates',type=int,help='Candidate IDs examined per owner (default 10)')
    parser.add_argument('--max-papers',type=int,help='Verified papers selected per owner (default 5)')
    parser.add_argument('--full-range',action='store_true',help='Explicitly remove both sample limits')
    parser.add_argument('--stop-after-tasks',type=int,help='Pause at a task boundary for a resume check')
    args=parser.parse_args(argv)
    if args.resume and (args.owner or args.all_owners or args.mock or args.full_range or args.max_candidates is not None or args.max_papers is not None):
        parser.error('--resume reads owners/mode/limits from state; do not supply them again')
    if not args.resume and not (args.owner or args.all_owners):parser.error('Choose --owner or --all-owners')
    if args.full_range and (args.max_candidates is not None or args.max_papers is not None):parser.error('--full-range excludes sample limit flags')
    try:
        if args.resume:
            options=load_options(args.output_root/'state.sqlite',args.run_id)
            if options.output_root.resolve()!=args.output_root.resolve():raise ValueError('Saved run belongs to another output root')
        else:
            owners=tuple(c['owner'] for c in ASSIGNED_RANGES) if args.all_owners else tuple(args.owner)
            options=RunOptions(args.run_id,args.output_root,owners,args.mock,
                None if args.full_range else 10 if args.max_candidates is None else args.max_candidates,
                None if args.full_range else 5 if args.max_papers is None else args.max_papers)
        modules=make_mock_modules(options) if options.is_mock else make_live_modules(options,RequestScheduler())
        result=run_pipeline(options,modules,stop_after_tasks=args.stop_after_tasks)
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(f'T5: {exc}',file=sys.stderr);return 1
    print(json.dumps({'run_id':options.run_id,'is_mock':options.is_mock,'papers':result.scope['scope']['paper_count'],
                      'discovery_complete':result.scope['scope']['discovery_complete'],
                      'executed_tasks':result.executed_tasks,'reused_tasks':result.reused_tasks,
                      'validation_valid':result.validation['valid'],'quality_valid':result.quality['valid'],
                      'reports':str(options.output_root/'runs'/options.run_id)},ensure_ascii=False))
    return result.exit_code
