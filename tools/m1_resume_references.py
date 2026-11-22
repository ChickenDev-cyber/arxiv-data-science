"""Resume references on a verified restored corpus; never recrawl its inputs."""
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import argparse,hashlib,json,os,time
from urllib.parse import urlsplit,parse_qs

from arxiv_pipeline.benchmark.runner import BenchmarkService
from arxiv_pipeline.runner.cli import checkpoint_validator
from arxiv_pipeline.runner.state import load_options,StateStore
from arxiv_pipeline.runner.writer import Publisher
from m1_batch_experiment import ExperimentFactory,HttpAudit,DiskObserver,write_json
from m1_batch_support import BatchPipelineRunner
from m1_batch_metrics import summarize_assignment

SAFE_HEADERS={'retry-after','date','server','via','x-cache','x-amzn-requestid',
              'x-amzn-errortype','x-ratelimit-limit','x-ratelimit-remaining','x-ratelimit-reset'}


class ReferenceHttpAudit(HttpAudit):
    def __init__(self,directory):
        self.directory=Path(directory)
        super().__init__(self.directory/'http-audit.jsonl')

    def wrap(self,label,method,transport):
        audited=super().wrap(label,method,transport)
        if label!='semantic_scholar':return audited
        def call(url,timeout,headers,*extra):
            lower={k.lower():v for k,v in headers.items()}
            key=lower.get('x-api-key','')
            public_headers={k:v for k,v in lower.items() if k in ('user-agent','accept','content-type')}
            body=extra[0] if extra else None
            signature={'method':method,'url':url,'headers':public_headers,
                       'body':body.decode() if body is not None else None}
            record={'method':method,'path':urlsplit(url).path,
                    'fields':parse_qs(urlsplit(url).query).get('fields',[]),
                    'request_signature':hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest(),
                    'user_agent':lower.get('user-agent'),'key_present':bool(key),
                    'key_matches_environment':bool(key) and key==os.environ.get('S2_API_KEY'),
                    'timeout_seconds':timeout,'started_at':datetime.now(timezone.utc).isoformat()}
            started=time.monotonic()
            try:
                response=audited(url,timeout,headers,*extra)
                record.update(status=response.status_code,response_headers={
                    k.lower():str(v).replace(key,'[REDACTED]') if key else str(v)
                    for k,v in response.headers.items() if k.lower() in SAFE_HEADERS})
                return response
            except Exception as exc:
                record['error_type']=type(exc).__name__
                raise
            finally:
                record['http_seconds']=time.monotonic()-started
                record['finished_at']=datetime.now(timezone.utc).isoformat()
                self.directory.mkdir(parents=True,exist_ok=True)
                with (self.directory/'request-trace.jsonl').open('a') as stream:
                    stream.write(json.dumps(record)+'\n')
        return call


class ReferenceResumeFactory(ExperimentFactory):
    def build(self,options,checkpoints):
        modules=super().build(options,checkpoints)
        for client in (modules.references.batch_client,modules.references.prefetch.fallback):
            client.max_attempts=1
            client.delay=3.
        return modules


class _CachedInputsOnly:
    def discover(self,*args,**kwargs):raise ValueError('Resume requires verified cached discovery; recrawl blocked')
    def fetch_metadata(self,*args,**kwargs):raise ValueError('Resume requires cached metadata; recrawl blocked')
    def fetch_sources(self,*args,**kwargs):raise ValueError('Source download blocked during references-only resume')


class _ReferencesOnlyFactory:
    def __init__(self,factory):self.factory=factory
    def build(self,options,checkpoints):
        modules=self.factory.build(options,checkpoints)
        guard=_CachedInputsOnly()
        return replace(modules,discovery=guard,metadata=guard,sources=guard)


def _protected_inputs(root):
    paths=list((root/'discovery').rglob('*'))
    paths+=list((root/'raw').glob('*/*/metadata.json'))
    for directory in (root/'raw').glob('*/*/v[0-9]*'):
        if directory.is_dir():paths.extend(directory.rglob('*'))
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file()}


def _verify_cached_inputs(root,options):
    with StateStore.readonly(root/'state.sqlite',options.run_id) as store:
        if store.read_run_record().status not in ('paused','finished') or store.pending_publications():
            raise ValueError('Resume requires a stopped published checkpoint')
        scope=store.read_scope()
        papers=scope['scope']['paper_tasks']
        if not papers:raise ValueError('Resume requires selected papers with cached metadata')
        accepted=list(store.iter_results(accepted_only=True))
        indexed={(r['owner'],r['arxiv_id'],r['step'],r['version']):r for r in accepted}
        for paper in papers:
            key=(paper['owner'],paper['arxiv_id'])
            metadata=indexed.get((*key,'metadata',None))
            if (metadata is None or metadata['status']!='success' or not metadata.get('data')
                    or not metadata['data']['versions_complete'] or not metadata['data']['versions']):
                raise ValueError('Resume requires complete cached metadata and version history')
            for version in metadata['data']['versions']:
                source=indexed.get((*key,'sources',version['version']))
                if source is None or source['status'] not in ('success','unsupported'):
                    raise ValueError('Resume requires every cached source or an explicit unsupported result')
        publisher=Publisher(options.context,store)
        for row in store.tasks():
            if json.loads(row['input_json'])['step'] in ('discovery','metadata','sources'):
                if publisher.verify_task(row['task_key']):
                    raise ValueError('Cached input artifact failed integrity verification')


def resume_references(root,run_id,report_dir,*,factory=None):
    root,report_dir=Path(root),Path(report_dir)
    if root.is_symlink() or not (root/'state.sqlite').is_file() or (root/'state.sqlite').is_symlink():
        raise ValueError('Resume requires an existing verified state database')
    options=load_options(root/'state.sqlite',run_id)
    if options.output_root.resolve()!=root.resolve():
        raise ValueError('Saved root differs; use verified restore before resume')
    if options.is_mock and factory is None:
        raise ValueError('Mock state requires an explicitly supplied synthetic factory')
    if report_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError('Resume report must be outside the corpus root')
    if report_dir.exists():raise ValueError('Use a fresh report directory for each invocation')
    _verify_cached_inputs(root,options)
    before=_protected_inputs(root)
    report_dir.mkdir(parents=True)
    audit=ReferenceHttpAudit(report_dir);observer=DiskObserver(root)
    selected=factory or ReferenceResumeFactory(root,'batch',audit,observer)
    runner=BatchPipelineRunner(_ReferencesOnlyFactory(selected),batch_size=100,
                               validate_record=checkpoint_validator())
    captured={}
    def progress(message):
        write_json(report_dir/'progress.json',{'message':message,'updated_at':datetime.now(timezone.utc).isoformat()})
        print(message,flush=True)
    def target():
        outcome=runner.run(options,stage='references',references_rate_limit_threshold=1,
                           stop_after_seconds=600,progress=progress)
        captured['outcome']=outcome
        return outcome
    _,benchmark=BenchmarkService(default_interval=.05).benchmark_callable(
        target,run_id,output_root=root,output_dir=report_dir/'benchmarks',cache_mode='warm')
    outcome=captured['outcome']
    events=[json.loads(line) for line in (root/'logs'/run_id/'events.jsonl').read_text().splitlines() if line.strip()]
    assignment=summarize_assignment(outcome.scope['scope']['paper_tasks'],events)
    count=assignment['publications'];fetched=assignment['references']['fully_fetched_papers']
    report={'measurement_scope':'references-only resume','original_benchmark_recomputed':False,
            'run_id':run_id,'exit_code':outcome.exit_code,'pause_reason':outcome.pause_reason,
            'executed_tasks':outcome.executed_tasks,'reused_tasks':outcome.reused_tasks,
            'references_complete':count>0 and fetched==count,'assignment':assignment,
            'validation_valid':outcome.validation['valid'],'quality_valid':outcome.quality['valid'],
            'protected_inputs_unchanged':before==_protected_inputs(root),'protected_inputs_before':before,
            'benchmark':benchmark,'http_attempts_this_invocation':len(audit.events),
            'limits':['This measurement excludes the original discovery/source crawl.',
                      'References completion does not make PDF-only sources available.',
                      'S2 uses one attempt per request, 3-second client delay, and the saved scheduler cooldown.']}
    write_json(report_dir/'resume-report.json',report)
    if not report['protected_inputs_unchanged']:raise ValueError('Protected discovery/metadata/source files changed')
    return outcome,report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--run-id',required=True)
    p.add_argument('--report-dir',type=Path,required=True)
    args=p.parse_args(argv)
    outcome,report=resume_references(args.root,args.run_id,args.report_dir)
    print(json.dumps({k:report[k] for k in ('references_complete','protected_inputs_unchanged','exit_code','pause_reason')}))
    return outcome.exit_code


if __name__=='__main__':raise SystemExit(main())
