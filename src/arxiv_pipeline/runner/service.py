"""Stage-based orchestration with one writer and task-level recovery."""

from collections import Counter
from copy import deepcopy
from inspect import signature
import json
from pathlib import Path
import re
import shutil
from typing import Callable

from ..contracts import Result, paper_folder
from ..discovery import candidate_count
from ..pipeline_contracts import Stage
from ..validation import validate_for_task
from ..validation.artifacts import safe_path
from .checkpoints import CheckpointValidator, SQLiteCheckpointStore
from .exports import Exporter
from .factory import ModuleFactory, StaticModuleFactory
from .models import ModuleSet, RunOptions, RunOutcome, TaskSpec, discovery_spec, paper_spec
from .scope import select_papers, source_specs, update_scope, validate_scope_configuration
from .stages import validate_stage
from .state import OutputLock, StateStore
from .summary import paper_outcome, summary_line
from .writer import Publisher, atomic_json


class _Pause(Exception):
    pass


def _reusable(result: Result) -> bool:
    if result['status'] in ('success', 'empty', 'unsupported'):
        return True
    return (result['step'] == 'discovery' and result['status'] == 'partial'
            and result['error'] is None and not result['data']['unresolved_ids']
            and result['provenance'].get('scope_limited') == 'true')


class PipelineRunner:
    """Own one synchronous run session; modules only return domain Results."""

    def __init__(self, factory: ModuleFactory, *, producer_commit: str | None = None,
                 validate_record: CheckpointValidator | None = None):
        if producer_commit is not None and not re.fullmatch(r'[0-9a-f]{40}', producer_commit):
            raise ValueError('producer_commit must be a 40-character lowercase Git SHA')
        self.factory = factory
        self.producer_commit = producer_commit
        self.validate_record = validate_record

    def run(self, options: RunOptions, *, stage: Stage = 'all', stop_after_tasks: int | None = None,
            progress: Callable[[str], None] | None = None) -> RunOutcome:
        self.stage = validate_stage(stage)
        if stop_after_tasks is not None and (type(stop_after_tasks) is not int or stop_after_tasks < 1):
            raise ValueError('stop_after_tasks must be a positive integer')
        self.options, self.context = options, options.context
        self.root = Path(options.output_root)
        self.progress, self.stop_after_tasks = progress, stop_after_tasks
        self.executed = self.reused = 0
        self.failures: dict[str, Result] = {}
        self.metadata: dict[tuple[str, str], Result] = {}
        self.owner_counts = {owner: Counter() for owner in options.owners}
        with OutputLock(self.root), StateStore(self.root / 'state.sqlite', options.run_id) as store:
            self.store = store
            store.open_run(options)
            self.publisher = Publisher(self.context, store)
            self.exporter = Exporter(self.context, store, self.publisher)
            store.set_run_status('running')
            try:
                self._recover()
                checkpoints = SQLiteCheckpointStore(store, validate_record=self.validate_record)
                self.modules = self.factory.build(options, checkpoints)
                self._discover()
                if self.stage in ('sources', 'all'):
                    self._sources_pass()
                if self.stage in ('references', 'all'):
                    self._references_pass()
                store.set_run_status('finished')
                self._report('Đang kiểm tra và xuất báo cáo')
                self.exporter.checkpoint()
                if self.exporter.validation['valid']:
                    self._final_summary()
                code = (1 if not self.exporter.validation['valid'] else
                        0 if self.exporter.quality['valid'] else 2)
            except (_Pause, KeyboardInterrupt) as exc:
                self._report('Đang phục hồi và lưu trạng thái để chạy tiếp')
                self.publisher.recover()
                self._load_metadata()
                self.scope = update_scope(store.read_scope(), [], list(self.metadata.values()))
                store.update_scope(self.scope)
                store.set_run_status('paused')
                self.exporter.checkpoint()
                code = 130 if isinstance(exc, KeyboardInterrupt) else 2
            except BaseException:
                store.set_run_status('error')
                if self.exporter.extra_issues:
                    self.exporter.checkpoint()
                raise
            return RunOutcome(code, store.read_scope(), self.executed, self.reused,
                              self.exporter.validation, self.exporter.quality)

    def _report(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def _bar(self, stage: str, completed: int, total: int, *, unit: str) -> None:
        update = getattr(self.progress, 'update', None)
        if callable(update):
            update(stage, completed, total, unit=unit)

    def _load_metadata(self) -> None:
        # Load only metadata payloads, not every paper's potentially large raw references.
        self.metadata.clear()
        for row in self.store.tasks():
            if json.loads(row['input_json'])['step'] == 'metadata':
                result = self.store.get_task(row['task_key'])['accepted_result']
                if result is not None:
                    self.metadata[result['owner'], result['arxiv_id']] = result

    def _recover(self) -> None:
        self._report('Đang phục hồi và kiểm tra dữ liệu đã lưu')
        self.exporter.mark_unavailable()
        validate_scope_configuration(self.store.read_scope(), self.options)
        self.publisher.recover()
        for row in self.store.tasks():
            if self.publisher.verify_task(row['task_key']):
                self.store.invalidate(row['task_key'])
        self.exporter.rebuild_events()
        self._load_metadata()
        self.scope = update_scope(self.store.read_scope(), [], list(self.metadata.values()))
        self.store.update_scope(self.scope)
        self._write_scope()

    def _write_scope(self) -> None:
        atomic_json(safe_path(self.root, f'runs/{self.options.run_id}/scope.json'), self.scope)

    def _perform(self, spec: TaskSpec, call: Callable[[], Result]) -> Result:
        self.store.ensure_task(spec)
        previous = self.store.get_task(spec.key)['accepted_result']
        identity = spec.arxiv_id or 'dải ID'
        if spec.version is not None:
            identity += f'v{spec.version}'
        label = f'{spec.owner} | {spec.step} | {identity}'
        if previous is not None and _reusable(previous):
            self.reused += 1
            self._report(f"{label} | dùng lại ({previous['status']})")
            return previous
        if self.stop_after_tasks is not None and self.executed >= self.stop_after_tasks:
            raise _Pause()
        if spec.step == 'sources':
            stage = safe_path(self.root, f'staging/{self.options.run_id}/{spec.owner}/'
                                        f'{paper_folder(spec.arxiv_id)}/v{spec.version}')
            if stage.exists():
                shutil.rmtree(stage)
        attempt = self.store.mark_running(spec.key)
        self._report(f'{label} | bắt đầu')
        result = call()
        self.executed += 1
        if self.producer_commit is not None:
            result = deepcopy(result)
            if isinstance(result, dict) and isinstance(result.get('provenance'), dict):
                result['provenance']['producer_commit'] = self.producer_commit
        issues = validate_for_task(result, spec, self.context)
        self.store.record(attempt, result, issues)
        if issues:
            self.exporter.extra_issues = issues
            raise ValueError('Module returned an invalid Result; see validation report')
        self.exporter.append_event(result)
        self.publisher.publish(attempt, result)
        self._report(f"{label} | {result['status']} | {result['duration_seconds']:.1f}s")
        if result['status'] in ('failed', 'not_found'):
            self.failures[spec.key] = result
        return self.store.get_task(spec.key)['accepted_result'] or result

    def _discover(self) -> None:
        discoveries = {}
        ranges = self.options.ranges
        discover = self.modules.discovery.discover
        reports_ids = 'progress' in signature(discover).parameters
        limits = [min(candidate_count(config), self.options.max_candidates)
                  if self.options.max_candidates is not None else candidate_count(config)
                  for config in ranges] if reports_ids else [1] * len(ranges)
        total, completed = sum(limits), 0
        unit = 'ID' if reports_ids else 'dải'
        self._bar('Discovery', 0, total, unit=unit)
        for config, limit in zip(ranges, limits):
            spec = discovery_spec(config)

            def call(config=config, offset=completed):
                if reports_ids:
                    return discover(config, self.context, progress=lambda n, _: self._bar(
                        'Discovery', offset + n, total, unit=unit))
                return discover(config, self.context)

            result = self._perform(spec, call)
            completed += limit
            self._bar('Discovery', completed, total, unit=unit)
            discoveries[config['owner']] = result
            chosen = select_papers(result, self.options)
            existing = [p for p in self.scope['scope']['paper_tasks'] if p['owner'] == config['owner']]
            chosen = existing + [p for p in chosen if p not in existing]
            if self.options.max_papers is not None:
                chosen = chosen[:self.options.max_papers]
            self.scope = update_scope(self.scope, chosen, list(self.metadata.values()))
            self.scope['scope']['discovery_complete'] = (
                len(discoveries) == len(self.options.ranges) and all(
                    r['data'] is not None and r['data']['complete'] and
                    (self.options.max_papers is None or len(r['data']['papers']) <= self.options.max_papers)
                    for r in discoveries.values()))
            self.store.update_scope(self.scope)
            self._write_scope()
        self.papers = self.scope['scope']['paper_tasks']
        self.unknown_history = {(p['owner'], p['arxiv_id']) for p in self.papers
                                if (p['owner'], p['arxiv_id']) not in self.metadata or not
                                self.metadata[p['owner'], p['arxiv_id']]['data']['versions_complete']}
        self.totals = Counter(task['owner'] for task in self.papers)

    def _remember_metadata(self, task, result: Result) -> None:
        key = task['owner'], task['arxiv_id']
        if result['data'] is not None:
            self.metadata[key] = result
            if result['data']['versions_complete']:
                self.unknown_history.discard(key)
            else:
                self.unknown_history.add(key)
        complete = bool(self.papers) and not self.unknown_history
        if self.scope['scope']['versions_complete'] != complete:
            self.scope['scope']['versions_complete'] = complete
            self.store.update_scope(self.scope)

    def _sources_pass(self) -> None:
        remaining = self.totals.copy()
        self._bar('Sources', 0, len(self.papers), unit='bài')
        for index, task in enumerate(self.papers, 1):
            self._report(f"Bài {index}/{len(self.papers)} | {task['owner']} | {task['arxiv_id']} | nguồn")
            metadata = self._perform(paper_spec(task, 'metadata'),
                                     lambda task=task: self.modules.metadata.fetch_metadata(task, self.context))
            self._remember_metadata(task, metadata)
            for spec in source_specs(task, metadata):
                self._perform(spec, lambda task=task, version=spec.version:
                              self.modules.sources.fetch_sources(task, version, self.context))
            self._bar('Sources', index, len(self.papers), unit='bài')
            if self.stage == 'sources':
                self._count_paper(task)
                self._owner_finished(task['owner'], remaining)

    def _references_pass(self) -> None:
        remaining = self.totals.copy()
        self._bar('References', 0, len(self.papers), unit='bài')
        for index, task in enumerate(self.papers, 1):
            self._report(f"Bài {index}/{len(self.papers)} | {task['owner']} | {task['arxiv_id']} | references")
            result = self._perform(paper_spec(task, 'references'),
                                   lambda task=task: self.modules.references.fetch_references(task, self.context))
            self._bar('References', index, len(self.papers), unit='bài')
            self._count_paper(task, references=result)
            self._owner_finished(task['owner'], remaining)

    def _accepted_or_failed(self, spec: TaskSpec) -> Result | None:
        row = self.store.get_task(spec.key)
        return (row['accepted_result'] if row else None) or self.failures.get(spec.key)

    def _count_paper(self, task, *, references: Result | None = None) -> None:
        if self.progress is None:
            return
        results = []
        metadata = self.metadata.get((task['owner'], task['arxiv_id']))
        if metadata is None:
            metadata = self._accepted_or_failed(paper_spec(task, 'metadata'))
        if metadata is not None:
            results.append(metadata)
            for spec in source_specs(task, metadata):
                source = self._accepted_or_failed(spec)
                if source is not None:
                    results.append(source)
        references = references or self._accepted_or_failed(paper_spec(task, 'references'))
        if references is not None:
            results.append(references)
        self.owner_counts[task['owner']][paper_outcome(task, results)] += 1

    def _owner_finished(self, owner, remaining) -> None:
        remaining[owner] -= 1
        if remaining[owner] == 0:
            self._report(summary_line(owner, self.totals[owner], self.owner_counts[owner]))

    def _final_summary(self) -> None:
        if self.progress is None:
            return
        if self.stage == 'discovery':
            for task in self.papers:
                self._count_paper(task)
            for owner in self.options.owners:
                if self.totals[owner]:
                    self._report(summary_line(owner, self.totals[owner], self.owner_counts[owner]))
        for owner in self.options.owners:
            if not self.totals[owner]:
                self._report(summary_line(owner, 0, self.owner_counts[owner]))
        overall = Counter()
        for counts in self.owner_counts.values():
            overall.update(counts)
        self._report(summary_line(None, len(self.papers), overall))
        if overall['pending']:
            self._report(f"Còn {overall['pending']} bài chưa được xử lý trong phạm vi đã chọn")


def run_pipeline(options: RunOptions, modules: ModuleSet, *, stage: Stage = 'all',
                 stop_after_tasks: int | None = None,
                 progress: Callable[[str], None] | None = None) -> RunOutcome:
    """Keep the baseline library entry point for callers supplying module instances."""
    return PipelineRunner(StaticModuleFactory(modules)).run(
        options, stage=stage, stop_after_tasks=stop_after_tasks, progress=progress)
