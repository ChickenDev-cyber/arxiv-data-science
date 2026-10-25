"""Sequential, task-level resumable orchestration of the four modules."""
from pathlib import Path
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


class _Pause(Exception):pass


def _reusable(r):
    if r['status'] in ('success','empty','unsupported'):return True
    return (r['step']=='discovery' and r['status']=='partial' and r['error'] is None
            and not r['data']['unresolved_ids'] and r['provenance'].get('scope_limited')=='true')


def run_pipeline(options,modules,*,stop_after_tasks=None):
    if stop_after_tasks is not None and (type(stop_after_tasks) is not int or stop_after_tasks<1):
        raise ValueError('stop_after_tasks must be a positive integer')
    root=Path(options.output_root);context=options.context
    executed=reused=0
    with OutputLock(root),StateStore(root/'state.sqlite',options.run_id) as store:
        store.open_run(options);pub=Publisher(context,store);export=Exporter(context,store,pub)
        store.set_run_status('running')
        try:
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
                if previous is not None and _reusable(previous):reused+=1;return previous
                if stop_after_tasks is not None and executed>=stop_after_tasks:raise _Pause()
                if spec.step=='sources':
                    stage=safe_path(root,f'staging/{options.run_id}/{spec.owner}/{paper_folder(spec.arxiv_id)}/v{spec.version}')
                    if stage.exists():shutil.rmtree(stage)
                attempt=store.mark_running(spec.key)
                result=call();executed+=1
                issues=validate_for_task(result,spec,context)
                store.record(attempt,result,issues)
                if issues:
                    export.extra_issues=issues
                    raise ValueError('Module returned an invalid Result; see validation report')
                export.append_event(result)
                pub.publish(attempt,result)
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

            for task in scope['scope']['paper_tasks']:
                metadata=perform(paper_spec(task,'metadata'),lambda task=task:modules.metadata.fetch_metadata(task,context))
                scope=update_scope(scope,[],list(store.iter_results(accepted_only=True)));store.update_scope(scope)
                for spec in source_specs(task,metadata):
                    perform(spec,lambda task=task,version=spec.version:modules.sources.fetch_sources(task,version,context))
                perform(paper_spec(task,'references'),lambda task=task:modules.references.fetch_references(task,context))
            store.set_run_status('finished')
            export.checkpoint()
            code=1 if not export.validation['valid'] else 0 if export.quality['valid'] else 2
        except (_Pause,KeyboardInterrupt) as exc:
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
