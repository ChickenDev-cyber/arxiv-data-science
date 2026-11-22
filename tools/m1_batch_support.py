"""Opt-in M1 experiment adapters; the committed pipeline package is unchanged."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime
import http.client
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode,urlsplit

from arxiv_pipeline.discovery.api import ApiResponse,RemoteError
from arxiv_pipeline.references.api import SemanticScholarClient,S2_FIELDS,_PacedClient
from arxiv_pipeline.references.service import ReferencesModule,_utc_now
from arxiv_pipeline.identifiers import normalize_arxiv_id,is_valid_arxiv_id
from arxiv_pipeline.runner.service import PipelineRunner
from arxiv_pipeline.runner.models import paper_spec

BATCH_URL='https://api.semanticscholar.org/graph/v1/paper/batch'
BATCH_FIELDS='paperId,externalIds,referenceCount,references.title,references.authors,references.year,references.publicationDate,references.venue,references.externalIds'
MAX_BODY=16*1024*1024


def post_transport(url,timeout,headers,body):
    target=urlsplit(url)
    if (target.scheme!='https' or target.hostname!='api.semanticscholar.org'
        or target.port not in (None,443) or target.username or target.password):
        raise ValueError('S2 credentials must remain on the HTTPS API origin')
    connection=http.client.HTTPSConnection(target.hostname,timeout=timeout)
    try:
        connection.request('POST',target.path+'?'+target.query,body=body,headers=headers)
        with connection.getresponse() as response:
            payload=response.read(MAX_BODY+1) if response.status==200 else b''
            if len(payload)>MAX_BODY:raise RemoteError('invalid_payload','Batch size limit exceeded',True)
            return ApiResponse(response.status,dict(response.getheaders()),payload)
    except OSError:
        # Never include a request/header dump in exception text.
        from urllib.error import URLError
        raise URLError('S2 POST connection failed') from None
    finally:
        connection.close()


class BatchPostClient(_PacedClient):
    """Use precisely the existing S2 pacing, retry and shared scheduler for POST."""
    def __init__(self,*,api_key=None,transport=None,delay=1,**kwargs):
        # Reuse the existing validated API-key/header construction.
        auth=SemanticScholarClient(api_key=api_key)
        headers={**auth.headers,'Content-Type':'application/json'}
        self._post=transport or post_transport
        self._body=b''
        self._batch_lock=threading.Lock()
        def send(url,timeout,sent_headers):
            return self._post(url,timeout,sent_headers,self._body)
        super().__init__(delay=delay,headers=headers,transport=send,
                         group='semantic_scholar',**kwargs)

    def fetch(self,ids):
        if not ids or len(ids)>100 or not all(is_valid_arxiv_id(a) for a in ids):
            raise ValueError('Batch requires1..100 canonical IDs')
        with self._batch_lock:
            self._body=json.dumps({'ids':['ARXIV:'+a for a in ids]}).encode()
            raw=self._get(BATCH_URL+'?'+urlencode({'fields':BATCH_FIELDS}))
        try:payload=json.loads(raw)
        except (ValueError,UnicodeDecodeError):raise RemoteError('invalid_payload','Invalid batch JSON') from None
        if not isinstance(payload,list) or len(payload)!=len(ids):
            raise RemoteError('invalid_payload','Batch response cardinality mismatch')
        return payload


class PrefetchedReader:
    def __init__(self,fallback):
        self.fallback=fallback
        self.ready={}
        self.errors={}
        self.blocked=None
    @property
    def attempts(self):return self.fallback.attempts
    def references_page(self,aid,offset,limit):
        if offset==0 and aid in self.ready:return {'data':deepcopy(self.ready.pop(aid))}
        if self.blocked is not None:raise self.blocked
        if offset==0 and aid in self.errors:raise self.errors[aid]
        return self.fallback.references_page(aid,offset,limit)


class BatchReferencesModule(ReferencesModule):
    def __init__(self,reader,dates,batch_client,**kwargs):
        self.prefetch=PrefetchedReader(reader)
        self.batch_client=batch_client
        super().__init__(self.prefetch,dates,**kwargs)

    def _prefetch(self,tasks):
        if not tasks:return
        ids=[task['arxiv_id'] for task in tasks]
        if self.prefetch.errors:
            # Abort every unvisited sibling after an adaptive child hit429.
            error=next(iter(self.prefetch.errors.values()))
            for aid in ids:self.prefetch.errors[aid]=error
            return
        try:papers=self.batch_client.fetch(ids)
        except RemoteError as exc:
            if (len(tasks)>1 and (exc.info.get('http_status') in (400,413)
                    or 'size limit' in exc.info.get('message','').lower())):
                middle=len(tasks)//2
                self._prefetch(tasks[:middle]);self._prefetch(tasks[middle:]);return
            if exc.info.get('http_status')==429:
                self.prefetch.blocked=exc
                # Surface the S2 failure through normal runner Results. Never
                # bypass this cooldown with a GET or smaller POST batches.
                for aid in ids:self.prefetch.errors[aid]=exc
            # Other errors use the established per-paper GET path.
            return
        for task,paper in zip(tasks,papers):
            if not isinstance(paper,dict):continue
            external=paper.get('externalIds')
            if not isinstance(external,dict) or normalize_arxiv_id(external.get('ArXiv'))!=task['arxiv_id']:
                continue
            count=paper.get('referenceCount');refs=paper.get('references')
            if type(count) is not int or count<0 or not isinstance(refs,list) or len(refs)!=count:
                continue
            if any(not isinstance(ref,dict) for ref in refs):continue
            wrapped=[{'citedPaper':deepcopy(ref)} for ref in refs]
            if self.page_checkpoint:
                self.page_checkpoint.save(task['owner'],task['arxiv_id'],self.page_size,self.fields,
                    [{'offset':0,'next_offset':None,'raw_items':wrapped,'fetched_at':_utc_now()}])
            else:self.prefetch.ready[task['arxiv_id']]=wrapped

    def fetch_references_batch(self,tasks,context,*,progress=None):
        started=_utc_now();before=self.batch_client.attempts
        todo=[]
        for task in tasks:
            saved=self.page_checkpoint.load(task['owner'],task['arxiv_id'],self.page_size,self.fields) if self.page_checkpoint else None
            # Preserve partial page chains; continue them through normal GET.
            if saved is None:todo.append(task)
        self._prefetch(todo)
        post_attempts=self.batch_client.attempts-before
        if post_attempts>0 and any(t['arxiv_id'] not in self.prefetch.ready for t in tasks):
            # Enforce polite spacing after POST batch before issuing fallback GETs
            time.sleep(3.5)
        try:
            results=super().fetch_references_batch(tasks,context,progress=progress)
            if results:results[0]['attempts']+=post_attempts
            for result in results:
                result['started_at']=started
                result['duration_seconds']=max(0.,(datetime.fromisoformat(result['finished_at'].replace('Z','+00:00'))-
                                                    datetime.fromisoformat(started.replace('Z','+00:00'))).total_seconds())
                result['provenance']['experiment_s2_post_batch']='true'
                result['provenance']['shared_post_attempts_charged_to_first_result']=str(post_attempts)
                result['provenance']['timing_policy']='shared batch latency; do not sum paper durations as wall'
            return results
        finally:
            self.prefetch.ready.clear();self.prefetch.errors.clear();self.prefetch.blocked=None


class BatchPipelineRunner(PipelineRunner):
    def __init__(self,*args,batch_size=100,**kwargs):
        if type(batch_size) is not int or not 1<=batch_size<=100:raise ValueError('batch_size1..100')
        self.batch_size=batch_size
        super().__init__(*args,**kwargs)

    def _references_pass(self):
        remaining=self.totals.copy();prefetched={}
        self._bar('References',0,len(self.papers),unit='bài')
        for index,task in enumerate(self.papers,1):
            key=(task['owner'],task['arxiv_id']);spec=paper_spec(task,'references')
            if (key not in prefetched and self.retry_before_attempt is None
                    and self._batch_fetch_allowed() and self._references_pending(spec)):
                maximum=self.batch_size
                if self.stop_after_tasks is not None:maximum=min(maximum,self.stop_after_tasks-self.executed)
                group=[]
                for candidate in self.papers[index-1:]:
                    if len(group)>=maximum:break
                    if self._references_pending(paper_spec(candidate,'references')):group.append(candidate)
                if group:
                    self._report(f'Experimental POST batch: {len(group)} papers')
                    for result in self.modules.references.fetch_references_batch(group,self.context,progress=self._report):
                        prefetched[(result['owner'],result['arxiv_id'])]=result
            def load(task=task,key=key):
                prepared=prefetched.pop(key,None)
                return prepared if prepared is not None else self.modules.references.fetch_references(task,self.context)
            result=self._perform(spec,load)
            self._bar('References',index,len(self.papers),unit='bài')
            self._count_paper(task,references=result)
            self._owner_finished(task['owner'],remaining)
