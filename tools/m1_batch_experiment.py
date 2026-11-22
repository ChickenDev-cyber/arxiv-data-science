"""Opt-in full-M1 baseline/batch benchmark. Fresh roots; no changes to main.

Example: PYTHONPATH=src python tools/m1_batch_experiment.py --variant batch
 --root /content/m1-batch/b --run-id batch-100 --owner trong --count100
"""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import argparse,hashlib,json,os,subprocess,sys,time
from urllib.request import urlopen
from urllib.error import HTTPError

from arxiv_pipeline.benchmark.profiler import get_dir_size
from arxiv_pipeline.benchmark.runner import BenchmarkService,extract_run_timings
from arxiv_pipeline.runner.models import RunOptions,ModuleSet
from arxiv_pipeline.runner.service import PipelineRunner
from arxiv_pipeline.runner.cli import checkpoint_validator
from arxiv_pipeline.statistics.service import StatisticsService
from m1_batch_support import BatchPostClient,BatchReferencesModule,BatchPipelineRunner,post_transport
from m1_batch_metrics import summarize_assignment
from m1_experiment_scheduler import ExperimentRequestScheduler


def write_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    os.replace(temporary,path)


class HttpAudit:
    def __init__(self,path):self.path=Path(path);self.events=[]
    def record(self,label,method,status,started,error=None):
        event={'service':label,'method':method,'status':status,
               'elapsed_seconds':time.monotonic()-started,
               'finished_at':datetime.now(timezone.utc).isoformat(),'error_type':error}
        self.events.append(event)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a') as stream:stream.write(json.dumps(event)+'\n')
    def wrap(self,label,method,transport):
        def call(*args,**kwargs):
            started=time.monotonic()
            try:response=transport(*args,**kwargs)
            except Exception as exc:
                self.record(label,method,None,started,type(exc).__name__);raise
            self.record(label,method,response.status_code,started)
            return response
        return call
    def source_open(self,request,timeout):
        started=time.monotonic()
        try:response=urlopen(request,timeout=timeout)
        except Exception as exc:
            self.record('arxiv_source','GET',getattr(exc,'code',None),started,type(exc).__name__);raise
        self.record('arxiv_source','GET',getattr(response,'status',200),started)
        return response


class DiskObserver:
    def __init__(self,root):self.root=Path(root);self.peak=0;self.events=[]
    def sample(self,label):
        size=get_dir_size(self.root);self.peak=max(size,self.peak)
        self.events.append({'event':label,'bytes':size,'elapsed_clock':time.monotonic()})


class ObservedSources:
    def __init__(self,client,observer):self.client=client;self.observer=observer
    @property
    def attempts(self):return self.client.attempts
    def fetch_budget(self):return self.client.fetch_budget()
    def provenance(self,*args):return self.client.provenance(*args)
    def download(self,*args):
        try:
            result=self.client.download(*args)
            if self.client.scheduler is not None:
                self.client.scheduler.request_succeeded('arxiv')
            return result
        finally:self.observer.sample('download_finished_archive_present')


class ObservedExtractor:
    def __init__(self,extractor,observer):self.extractor=extractor;self.observer=observer
    def extract(self,*args,**kwargs):
        try:return self.extractor.extract(*args,**kwargs)
        finally:self.observer.sample('extraction_finished_archive_and_retained_files_present')


class ExperimentFactory:
    def __init__(self,root,variant,audit,observer):
        self.root,self.variant,self.audit,self.observer=root,variant,audit,observer
    def build(self,options,checkpoints):
        from arxiv_pipeline.discovery.api import ApiClient,_default_transport
        from arxiv_pipeline.discovery.service import DiscoveryModule
        from arxiv_pipeline.metadata.api import OaiClient
        from arxiv_pipeline.metadata.service import MetadataModule
        from arxiv_pipeline.references.api import SemanticScholarClient,ArxivDateClient,_semantic_scholar_transport
        from arxiv_pipeline.sources.api import ArxivSourceClient
        from arxiv_pipeline.sources.extractor import SourceExtractor
        from arxiv_pipeline.sources.service import SourcesModule
        from arxiv_pipeline.references.service import ReferencesModule
        scheduler=ExperimentRequestScheduler(checkpoints=checkpoints)
        query=ApiClient(scheduler=scheduler,transport=self.audit.wrap('arxiv_discovery','GET',_default_transport))
        oai=OaiClient(scheduler=scheduler,transport=self.audit.wrap('arxiv_metadata','GET',_default_transport))
        fallback=SemanticScholarClient(scheduler=scheduler,delay=1.1,
                    transport=self.audit.wrap('semantic_scholar','GET',_semantic_scholar_transport))
        dates=ArxivDateClient(scheduler=scheduler,transport=self.audit.wrap('arxiv_dates','GET',_default_transport))
        if self.variant=='batch':
            batch=BatchPostClient(scheduler=scheduler,delay=1.1,
                                 transport=self.audit.wrap('semantic_scholar','POST',post_transport))
            references=BatchReferencesModule(fallback,dates,batch,checkpoints=checkpoints)
        else:references=ReferencesModule(fallback,dates,checkpoints=checkpoints)
        audit=self.audit
        class Opener:
            def open(self,request,timeout):
                try:return audit.source_open(request,timeout)
                except HTTPError as exc:
                    if exc.code==429:
                        try:scheduler.rate_limited('arxiv',client._retry_after(exc.headers))
                        except BaseException:
                            exc.close()
                            raise
                    raise
        client=ArxivSourceClient(scheduler=scheduler,opener=Opener())
        return ModuleSet(DiscoveryModule(query,max_candidates=options.max_candidates,checkpoints=checkpoints),
                         MetadataModule(oai),SourcesModule(ObservedSources(client,self.observer),
                         ObservedExtractor(SourceExtractor(),self.observer)),references)


def run_worker(root,run_id,owner,count,variant,batch_size,*,factory=None,is_mock=False,stop_after_seconds=86400):
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    audit=HttpAudit(root/'http-audit.jsonl');observer=DiskObserver(root)
    max_cand = None if count >= 5000 else count
    options=RunOptions(run_id,root,(owner,),is_mock,max_cand,count)
    selected=factory or ExperimentFactory(root,variant,audit,observer)
    runner=(BatchPipelineRunner(selected,batch_size=batch_size,validate_record=checkpoint_validator())
            if variant=='batch' else PipelineRunner(selected,validate_record=checkpoint_validator()))
    def progress(message):
        print(datetime.now(timezone.utc).isoformat(),message,flush=True)
        write_json(root/'progress.json',{'message':message,'updated_at':datetime.now(timezone.utc).isoformat()})
    outcome=runner.run(options,stage='all',progress=progress,references_rate_limit_threshold=20,
                       stop_after_seconds=stop_after_seconds)
    events=root/'logs'/run_id/'events.jsonl'
    results=[json.loads(line) for line in events.read_text().splitlines() if line.strip()] if events.is_file() else []
    assignment=summarize_assignment(outcome.scope['scope']['paper_tasks'],results)
    statistics_error=None
    try:
        stats=StatisticsService().build_statistics(root,run_id)
        write_json(root/'assignment-statistics-schema12.json',stats)
    except Exception as exc:statistics_error=type(exc).__name__
    observer.sample('all_pipeline_and_statistics_outputs_finished')
    counts=Counter((r['service'],r['method'],str(r['status'])) for r in audit.events)
    data={'variant':variant,'requested_publications':count,'assignment':assignment,
          'standard_statistics_error':statistics_error,'http_attempts_actual':len(audit.events),
          'http_counts':{'|'.join(k):v for k,v in counts.items()},
          'disk_peak_observed_bytes':observer.peak,'disk_observations':observer.events,
          'pause_reason':outcome.pause_reason,'validation_valid':outcome.validation['valid'],
          'quality_valid':outcome.quality['valid'],'is_mock':is_mock,
          'selected_manifest':[t['arxiv_id'] for t in outcome.scope['scope']['paper_tasks']],
          'limits':['100-paper pilot is not a completed assigned range.',
                    'Source downloads are per version; metadata history still OAI GetRecord.',
                    'Peak disk is sampled and observed at source lifecycle boundaries; transient peaks may be missed.',
                    'Per-paper latency includes batch/shared waits; sum of paper latency is not system wall.',
                    'Source size averages sum all versions per fully measured publication; no compressed/uncompressed comparison.',
                    'Fresh distinct corpus roots; OS and server caches are uncontrolled.',
                    'Archive/Drive handoff after measurement is separate from crawler wall.']}
    write_json(root/'experiment-data.json',data)
    return outcome,data


def assemble(root,run_id,benchmark,data):
    timing=extract_run_timings(root,run_id)
    benchmark['peak_disk_bytes']=max(benchmark['peak_disk_bytes'] or 0,data['disk_peak_observed_bytes'])
    report={**data,'benchmark':benchmark,'timing':timing,
            'cohort_complete':(data['assignment']['publications']==data['requested_publications']
                               and data['assignment']['papers_complete']==data['requested_publications'])}
    write_json(root/'experiment-metrics.json',report)
    return report


def profile_pipeline(root,run_id,owner,count,variant,batch_size,*,factory=None,is_mock=False):
    captured={}
    def target():
        outcome,data=run_worker(root,run_id,owner,count,variant,batch_size,factory=factory,is_mock=is_mock)
        captured['data']=data
        return outcome
    _,benchmark=BenchmarkService(default_interval=.05).benchmark_callable(target,run_id,
                output_root=Path(root),output_dir=Path(root)/'benchmarks',cache_mode='mixed')
    return assemble(Path(root),run_id,benchmark,captured['data'])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant',choices=['baseline','batch'],required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--owner',default='trong')
    parser.add_argument('--count',type=int,default=100)
    parser.add_argument('--batch-size',type=int,default=100)
    parser.add_argument('--stop-after-seconds',type=int,default=86400)
    parser.add_argument('--worker',action='store_true')
    args=parser.parse_args(argv)
    if args.count<1 or not 1<=args.batch_size<=100:parser.error('count>=1,batch_size1..100')
    # Cho phép tiếp tục chạy nối tiếp từ checkpoint (resume)
    # if args.root.exists():parser.error('Fresh output root must not exist; never overwrite a previous attempt')
    if args.worker:
        outcome,_=run_worker(args.root,args.run_id,args.owner,args.count,args.variant,args.batch_size,stop_after_seconds=args.stop_after_seconds)
        return outcome.exit_code
    command=[sys.executable,str(Path(__file__).resolve()),'--worker','--variant',args.variant,
             '--root',str(args.root),'--run-id',args.run_id,'--owner',args.owner,
             '--count',str(args.count),'--batch-size',str(args.batch_size),
             '--stop-after-seconds',str(args.stop_after_seconds)]
    report_dir=args.root.parent/(args.root.name+'-measurement')
    report_dir.mkdir(parents=True,exist_ok=True)
    benchmark=BenchmarkService(default_interval=.05).benchmark_command(command,args.run_id,
                    output_root=args.root,output_dir=report_dir,cache_mode='mixed')
    data_path=args.root/'experiment-data.json'
    if data_path.is_file():
        report=assemble(args.root,args.run_id,benchmark,json.loads(data_path.read_text()))
        write_json(report_dir/'experiment-metrics.json',report)
        print(json.dumps({'cohort_complete':report['cohort_complete'],'metrics':str(args.root/'experiment-metrics.json')},indent=2))
    return benchmark['exit_code']


if __name__=='__main__':raise SystemExit(main())
