"""Experiment regressions: real T4 contracts, fake HTTP, no external requests."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from arxiv_pipeline.discovery.api import ApiResponse, RemoteError
from arxiv_pipeline.http import RequestScheduler
from arxiv_pipeline.references.checkpoint import MemoryCheckpointStore
from arxiv_pipeline.references.examples import FakeReader, FakeDates, cited, page
from arxiv_pipeline.runner.models import paper_spec
from arxiv_pipeline.validation import validate_for_task
from m1_batch_support import BatchPostClient, BatchReferencesModule

TASKS=[{'owner':'trong','arxiv_id':'2504.17523'}, {'owner':'trong','arxiv_id':'2504.17524'}]
DATE='2020-01-01T00:00:00Z'
CTX={'run_id':'offline-batch','is_mock':True,'output_root':Path('/tmp/not-used')}


def paper(aid, refs=None, count=None):
    refs=[cited('2001.00001')['citedPaper']] if refs is None else refs
    return {'paperId':'citing-'+aid,'externalIds':{'ArXiv':aid},
            'referenceCount':len(refs) if count is None else count,'references':refs}


class Clock:
    def __init__(self):self.now=0.
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class BatchExperimentTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.calls=[]
        self.scheduler=RequestScheduler(monotonic=self.clock.monotonic,sleep=self.clock.sleep)

    def client(self, payload, status=200):
        def transport(url,timeout,headers,body):
            self.calls.append({'ids':json.loads(body)['ids'],'at':self.clock.now,
                               'fields':url.split('fields=',1)[1]})
            return ApiResponse(status,{'Retry-After':'60'} if status==429 else {},json.dumps(payload).encode())
        return BatchPostClient(scheduler=self.scheduler,transport=transport,delay=1,
            monotonic=self.clock.monotonic,sleep=self.clock.sleep,max_attempts=1)

    def module(self, payload, fallback=None, store=None, status=200):
        self.fallback=fallback or FakeReader([])
        self.dates=FakeDates({'2001.00001':DATE,'2001.00002':DATE})
        return BatchReferencesModule(self.fallback,self.dates,self.client(payload,status),checkpoints=store)

    def assert_valid(self, results):
        for task,result in zip(TASKS,results):
            self.assertEqual(validate_for_task(result,paper_spec(task,'references'),CTX),[])

    def test_one_post_serves_two_papers_and_counts_shared_request_once(self):
        module=self.module([paper(t['arxiv_id']) for t in TASKS])
        results=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual([r['status'] for r in results],['success','success'])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0]['ids'],['ARXIV:2504.17523','ARXIV:2504.17524'])
        self.assertEqual(self.fallback.calls,[])
        self.assertEqual(sum(r['attempts'] for r in results),2) #1 POST +1 shared date GET
        self.assert_valid(results)

    def test_truncated_batch_reference_list_falls_back_without_duplicate_items(self):
        fallback=FakeReader([page([cited('2001.00001'),cited('2001.00002')])])
        module=self.module([paper(TASKS[0]['arxiv_id'],count=2),paper(TASKS[1]['arxiv_id'])],fallback)
        results=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual(len(self.fallback.calls),1)
        self.assertEqual(len(results[0]['data']['raw_references']),2)
        self.assertTrue(all(r['data']['complete'] for r in results))
        self.assertEqual(sum(r['attempts'] for r in results),3)
        self.assert_valid(results)

    def test_null_paper_is_not_claimed_as_empty_references(self):
        fallback=FakeReader([RemoteError('not_found','No indexed paper',False,404)])
        module=self.module([None,paper(TASKS[1]['arxiv_id'])],fallback)
        results=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual(results[0]['status'],'not_found')
        self.assertIsNone(results[0]['data'])
        self.assertEqual(results[1]['status'],'success')
        self.assert_valid(results)

    def test_swapped_ids_cannot_publish_another_papers_references(self):
        fallback=FakeReader([page([cited('2001.00002')]),page([cited('2001.00001')])])
        module=self.module([paper(TASKS[1]['arxiv_id']),paper(TASKS[0]['arxiv_id'])],fallback)
        results=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual(len(self.fallback.calls),2)
        self.assertEqual(set(results[0]['data']['references']),{'2001.00002'})
        self.assert_valid(results)

    def test_429_never_escapes_through_get_and_shared_cooldown_survives(self):
        module=self.module({},status=429)
        results=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['status'],'failed')
        self.assertEqual(results[0]['error']['http_status'],429)
        self.assertEqual(self.fallback.calls,[])
        self.assertEqual(results[0]['attempts'],1)
        with self.scheduler.request('semantic_scholar'):
            self.assertGreaterEqual(self.clock.now,60)
        self.assert_valid(results)

    def test_adaptive_split_stops_all_remaining_halves_after_429(self):
        tasks=TASKS+[{'owner':'trong','arxiv_id':'2504.17525'},{'owner':'trong','arxiv_id':'2504.17526'}]
        calls=[]
        def transport(url,timeout,headers,body):
            ids=json.loads(body)['ids'];calls.append(ids)
            if len(calls)==1:return ApiResponse(413,{},b'')
            if len(calls)==2:return ApiResponse(429,{},b'')
            return ApiResponse(200,{},json.dumps([paper(a.removeprefix('ARXIV:')) for a in ids]).encode())
        client=BatchPostClient(scheduler=self.scheduler,transport=transport,delay=1,
                monotonic=self.clock.monotonic,sleep=self.clock.sleep,max_attempts=1)
        fallback=FakeReader([])
        module=BatchReferencesModule(fallback,FakeDates({}),client)
        results=module.fetch_references_batch(tasks,CTX)
        self.assertEqual(len(calls),2)
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['error']['http_status'],429)
        self.assertEqual(results[0]['attempts'],2)
        self.assertEqual(fallback.calls,[])

    def test_429_from_later_half_blocks_earlier_unresolved_get_fallback(self):
        tasks=TASKS+[{'owner':'trong','arxiv_id':'2504.17525'},{'owner':'trong','arxiv_id':'2504.17526'}]
        calls=[]
        def transport(url,timeout,headers,body):
            ids=json.loads(body)['ids'];calls.append(ids)
            if len(calls)==1:return ApiResponse(413,{},b'')
            if len(calls)==2:return ApiResponse(200,{},json.dumps([None,None]).encode())
            return ApiResponse(429,{},b'')
        client=BatchPostClient(scheduler=self.scheduler,transport=transport,delay=1,
                monotonic=self.clock.monotonic,sleep=self.clock.sleep,max_attempts=1)
        fallback=FakeReader([page([]),page([])])
        module=BatchReferencesModule(fallback,FakeDates({}),client)
        results=module.fetch_references_batch(tasks,CTX)
        self.assertEqual(fallback.calls,[])
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['error']['http_status'],429)
        self.assertEqual(results[0]['attempts'],3)

    def test_post_429_blocks_resumed_partial_page_get_without_losing_saved_refs(self):
        store=MemoryCheckpointStore()
        module=self.module({},store=store,status=429)
        module.page_checkpoint.save('trong',TASKS[0]['arxiv_id'],100,module.fields,
            [{'offset':0,'next_offset':1,'raw_items':[cited('2001.00001')],'fetched_at':'2026-11-21T00:00:00Z'}])
        results=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual(self.fallback.calls,[])
        self.assertEqual(len(self.calls),1)
        self.assertEqual(len(results),1)
        self.assertEqual(results[0]['status'],'partial')
        self.assertFalse(results[0]['data']['complete'])
        self.assertEqual(len(results[0]['data']['raw_references']),1)
        self.assertEqual(results[0]['error']['http_status'],429)
        self.assert_valid(results)

    def test_complete_page_and_date_checkpoints_avoid_second_post(self):
        store=MemoryCheckpointStore()
        module=self.module([paper(t['arxiv_id']) for t in TASKS],store=store)
        module.fetch_references_batch(TASKS,CTX)
        second=module.fetch_references_batch(TASKS,CTX)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(sum(r['attempts'] for r in second),0)
        self.assert_valid(second)

    def test_bad_response_cardinality_falls_back_for_every_requested_id(self):
        fallback=FakeReader([page([]),page([])])
        results=self.module([paper(TASKS[0]['arxiv_id'])],fallback).fetch_references_batch(TASKS,CTX)
        self.assertEqual(len(self.fallback.calls),2)
        self.assertEqual([r['status'] for r in results],['empty','empty'])
        self.assert_valid(results)


if __name__=='__main__':unittest.main()
