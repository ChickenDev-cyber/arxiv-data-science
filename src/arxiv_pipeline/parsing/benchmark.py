"""Measure a fresh parsing command including imports, SQLite and Parquet export.

The sampler measures supervisor + parser process tree, not notebook/system RAM.
Setup, source hashing and report serialization happen outside the timed command.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

from arxiv_pipeline.benchmark.profiler import ResourceSampler, get_environment_info


def source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for file in sorted(root.rglob('*.py')):
        digest.update(file.relative_to(root).as_posix().encode() + b'\0')
        digest.update(file.read_bytes())
    return digest.hexdigest()


def run_measured(corpus: Path, output: Path, *, backend: str = 'rule',
                 parquet: bool = False, interval: float = 0.05,
                 checkpoint: bool = False, resume_from: Path | None = None,
                 backup_db: Path | None = None, backup_every: int = 100,
                 single_parquet: bool = False) -> dict:
    """Never reuse an existing output directory or call a partial run complete."""
    output.mkdir(parents=True, exist_ok=False)
    if resume_from:
        from .resume import restore_database
        restore_database(resume_from,output/'parsing.sqlite')
    environment = get_environment_info()
    packages = {}
    for name in ('psutil', 'pyarrow', 'spacy', 'wtpsplit'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    fingerprint = source_fingerprint()
    command = [sys.executable, '-m', 'arxiv_pipeline.parsing.cli',
               '--corpus', str(corpus.resolve()), '--db', str(output/'parsing.sqlite'),
               '--benchmark', str(output/'pipeline.json'), '--no-python-heap-tracing']
    if parquet:
        command += ['--parquet-dir', str(output/'parquet')]
        if single_parquet:
            command += ['--single-parquet']
    if checkpoint or resume_from:
        command += ['--resume']
    if backup_db:
        command += ['--backup-db',str(backup_db),'--backup-every',str(backup_every)]
    env = dict(os.environ, ARXIV_SENTENCE_BACKEND=backend)
    sampler = ResourceSampler(interval_seconds=interval)
    sampler.start()
    started = time.perf_counter()
    process = None
    interrupted = False
    try:
        with (output/'stdout.log').open('w') as stdout, (output/'stderr.log').open('w') as stderr:
            process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr)
            try:
                exit_code = process.wait()
            except KeyboardInterrupt:
                interrupted = True
                if os.name=='posix':process.send_signal(signal.SIGINT)
                else:process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                exit_code = 130
    finally:
        wall = time.perf_counter() - started
        peak, mean, _, method = sampler.stop()
    pipeline_path = output/'pipeline.json'
    pipeline = json.loads(pipeline_path.read_text()) if pipeline_path.is_file() else {}
    attempted = pipeline.get('total_publications', 0)
    processed = pipeline.get('processed_publications', 0)
    skipped = pipeline.get('skipped_publications', 0)
    statistics = None
    statistics_error = None
    if attempted and (output/'parsing.sqlite').is_file():
        # This is report preparation after the timed parser command, not an
        # extra parsing run. Keep all per-publication denominators inspectable.
        from .statistics import export_statistics
        try:
            statistics = export_statistics(corpus, output/'parsing.sqlite', output/'statistics')
        except Exception as exc:
            statistics_error = str(exc)
    complete = (exit_code == 0 and attempted > 0 and attempted == processed+skipped
                and peak is not None and (not parquet or pipeline.get('parquet_export') is not None)
                and statistics is not None and statistics['version_accounted_rate'] == 1.0)
    artifacts = [output/'parsing.sqlite', *sorted((output/'parquet').rglob('*.parquet'))]
    sizes = {str(p.relative_to(output)): p.stat().st_size for p in artifacts if p.is_file()}
    report = {
        'schema_version': 'm2-benchmark-1', 'status': 'complete' if complete else 'incomplete',
        'run_kind': 'resume' if resume_from else 'fresh',
        'restored_from': str(resume_from) if resume_from else None,
        'interrupted': interrupted, 'exit_code': exit_code,
        'wall_seconds': wall,
        'timing_scope': 'subprocess spawn through exit: imports, parsing, checkpoints/backups if enabled, SQLite close, optional Parquet export',
        'excluded': ['installation', 'corpus staging', 'supervisor source hashing', 'snapshot restore', 'post-run artifact transfer',
                     'post-run statistics export', 'report serialization'],
        'peak_rss_bytes': peak, 'mean_rss_bytes': mean, 'rss_method': method,
        'rss_scope': 'supervisor and parser process tree; sampled peak',
        'sampling_interval_seconds': sampler.interval_seconds,
        'mean_seconds_per_attempted_publication': wall/attempted if attempted else None,
        'mean_seconds_per_processed_publication': wall/processed if processed else None,
        'backend': backend, 'source_sha256': fingerprint,
        'environment': environment, 'packages': packages, 'command': command,
        'pipeline': pipeline, 'output_bytes': sum(sizes.values()), 'artifact_bytes': sizes,
        'statistics': statistics, 'statistics_error': statistics_error,
        'checkpoint_backup_seconds': pipeline.get('checkpoint_backup_seconds',0.0),
        'wall_seconds_excluding_checkpoint_backups': max(0,wall-pipeline.get('checkpoint_backup_seconds',0.0)),
    }
    (output/'benchmark.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--backend', choices=('rule', 'spacy', 'sat', 'punkt'), default='rule')
    parser.add_argument('--parquet', action='store_true')
    parser.add_argument('--single-parquet', action='store_true',
                        help='Export 3 single Parquet files instead of sharded directories.')
    parser.add_argument('--sample-interval', type=float, default=0.05)
    parser.add_argument('--checkpoint',action='store_true')
    parser.add_argument('--resume-from',type=Path)
    parser.add_argument('--backup-db',type=Path)
    parser.add_argument('--backup-every',type=int,default=100)
    args = parser.parse_args(argv)
    if args.sample_interval <= 0:
        parser.error('--sample-interval must be positive')
    if args.backup_every < 1:parser.error('--backup-every must be positive')
    if args.backup_db and not (args.checkpoint or args.resume_from):
        parser.error('--backup-db requires --checkpoint or --resume-from')
    report = run_measured(args.corpus, args.output_dir, backend=args.backend,
                          parquet=args.parquet, interval=args.sample_interval,
                          checkpoint=args.checkpoint,resume_from=args.resume_from,
                          backup_db=args.backup_db,backup_every=args.backup_every,
                          single_parquet=args.single_parquet)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
