"""Sequential, task-level resumable orchestration of the four modules."""
from pathlib import Path
from collections import Counter
import shutil
from ..contracts import paper_folder
from ..validation import validate_for_task
from ..validation.results import issue
from ..validation.artifacts import safe_path
from .models import RunOutcome,discovery_spec,paper_spec
from .scope import select_papers,source_specs,update_scope
from .state import OutputLock,StateStore
from .writer import Publisher,atomic_json
from .exports import Exporter
from .summary import paper_outcome,summary_line


class _Pause(Exception):pass


def _reusable(r):
    if r['status'] in ('success','empty','unsupported'):return True
    return (r['step']=='discovery' and r['status']=='partial' and r['error'] is None
            and not r['data']['unresolved_ids'] and r['provenance'].get('scope_limited')=='true')


def run_pipeline(options,modules,*,stop_after_tasks=None,progress=None):
    if stop_after_tasks is not None and (type(stop_after_tasks) is not int or stop_after_tasks<1):
        raise ValueError('stop_after_tasks must be a positive integer')
    root=Path(options.output_root);context=options.context
    executed=reused=0
    def report(message):
        if progress is not None:progress(message)
    with OutputLock(root),StateStore(root/'state.sqlite',options.run_id) as store:
        store.open_run(options);pub=Publisher(context,store);export=Exporter(context,store,pub)
        store.set_run_status('running')
        try:
            report('Đang phục hồi và kiểm tra dữ liệu đã lưu')
            export.mark_unavailable()
            pub.recover()
            for row in store.tasks():
                if pub.verify_task(row['task_key']):store.invalidate(row['task_key'])
            export.rebuild_events()
            scope=update_scope(store.read_scope(),[],list(store.iter_results(accepted_only=True)))
            store.update_scope(scope)
            atomic_json(safe_path(root,f'runs/{options.run_id}/scope.json'),scope)

            def perform(spec,call):
                nonlocal executed,reused
                store.ensure_task(spec)
                row=store.get_task(spec.key);previous=row['accepted_result']
                identity=spec.arxiv_id or 'dải ID'
                if spec.version is not None:identity+=f'v{spec.version}'
                label=f'{spec.owner} | {spec.step} | {identity}'
                if previous is not None and _reusable(previous):
                    reused+=1;report(f"{label} | dùng lại ({previous['status']})");return previous
                if stop_after_tasks is not None and executed>=stop_after_tasks:raise _Pause()
                if spec.step=='sources':
                    stage=safe_path(root,f'staging/{options.run_id}/{spec.owner}/{paper_folder(spec.arxiv_id)}/v{spec.version}')
                    if stage.exists():shutil.rmtree(stage)
                attempt=store.mark_running(spec.key)
                report(f'{label} | bắt đầu')
                result=call();executed+=1
                issues=validate_for_task(result,spec,context)
                store.record(attempt,result,issues)
                if issues:
                    export.extra_issues=issues
                    raise ValueError('Module returned an invalid Result; see validation report')
                export.append_event(result)
                pub.publish(attempt,result)
                report(f"{label} | {result['status']} | {result['duration_seconds']:.1f}s")
                return store.get_task(spec.key)['accepted_result'] or result

            discoveries={}
            for config in options.ranges:
                spec=discovery_spec(config)
                d=perform(spec,lambda config=config:modules.discovery.discover(config,context))
                discoveries[config['owner']]=d
                chosen=select_papers(d,options)
                existing=[p for p in scope['scope']['paper_tasks'] if p['owner']==config['owner']]
                chosen=existing+[p for p in chosen if p not in existing]
                if options.max_papers is not None:chosen=chosen[:options.max_papers]
                scope=update_scope(scope,chosen,list(store.iter_results(accepted_only=True)))
                scope['scope']['discovery_complete']=(len(discoveries)==len(options.ranges) and all(
                    r['data'] is not None and r['data']['complete'] and
                    (options.max_papers is None or len(r['data']['papers'])<=options.max_papers)
                    for r in discoveries.values()))
                store.update_scope(scope)
                atomic_json(safe_path(root,f'runs/{options.run_id}/scope.json'),scope)

            papers=scope['scope']['paper_tasks']
            totals=Counter(task['owner'] for task in papers)
            remaining=totals.copy()
            owner_counts={owner:Counter() for owner in options.owners}
            for index,task in enumerate(papers,1):
                report(f"Bài {index}/{len(papers)} | {task['owner']} | {task['arxiv_id']}")
                metadata=perform(paper_spec(task,'metadata'),lambda task=task:modules.metadata.fetch_metadata(task,context))
                results=[metadata]
                scope=update_scope(scope,[],list(store.iter_results(accepted_only=True)));store.update_scope(scope)
                for spec in source_specs(task,metadata):
                    results.append(perform(spec,lambda task=task,version=spec.version:modules.sources.fetch_sources(task,version,context)))
                results.append(perform(paper_spec(task,'references'),lambda task=task:modules.references.fetch_references(task,context)))
                owner=task['owner']
                owner_counts[owner][paper_outcome(task,results)]+=1
                remaining[owner]-=1
                if remaining[owner]==0:report(summary_line(owner,totals[owner],owner_counts[owner]))
            for owner in options.owners:
                if not totals[owner]:report(summary_line(owner,0,owner_counts[owner]))
            store.set_run_status('finished')
            report('Đang kiểm tra và xuất báo cáo')
            export.checkpoint()
            if export.validation['valid']:
                overall=Counter()
                for counts in owner_counts.values():overall.update(counts)
                report(summary_line(None,len(papers),overall))
            code=1 if not export.validation['valid'] else 0 if export.quality['valid'] else 2
        except (_Pause,KeyboardInterrupt) as exc:
            report('Đang phục hồi và lưu trạng thái để chạy tiếp')
            # Resolve prepared files before comparing them with the previous accepted Result.
            pub.recover()
            scope=update_scope(store.read_scope(),[],list(store.iter_results(accepted_only=True)))
            store.update_scope(scope)
            store.set_run_status('paused');export.checkpoint()
            code=130 if isinstance(exc,KeyboardInterrupt) else 2
        except BaseException:
            store.set_run_status('error')
            # Preserve pending journals on storage errors; recovery owns their disposition.
            if export.extra_issues:export.checkpoint()
            raise
        return RunOutcome(code,store.read_scope(),executed,reused,export.validation,export.quality)
