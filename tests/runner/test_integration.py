from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from arxiv_pipeline.config import ASSIGNED_RANGES
from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.service import run_pipeline
from arxiv_pipeline.runner.state import StateStore


class IntegrationTests(unittest.TestCase):
    def test_live_clients_recover_sources_and_s2_with_task_resume(self):
        import io
        import re
        from collections import Counter
        from types import SimpleNamespace
        from arxiv_pipeline.discovery.api import ApiResponse
        from arxiv_pipeline.http import RequestScheduler
        from arxiv_pipeline.references.api import SemanticScholarClient
        from arxiv_pipeline.references.service import ReferencesModule
        from arxiv_pipeline.runner.models import ModuleSet
        from arxiv_pipeline.sources.api import ArxivSourceClient
        from arxiv_pipeline.sources.extractor import SourceExtractor
        from arxiv_pipeline.sources.service import SourcesModule

        class Clock:
            now=0.
            def monotonic(self):return self.now
            def sleep(self,seconds):self.now+=seconds

        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('stability',root/'corpus',('trong','hoang'),True)
            mock=make_mock_modules(o);clock=Clock()
            scheduler=RequestScheduler(monotonic=clock.monotonic,sleep=clock.sleep)
            phase=[0];source_calls=Counter();s2_calls=[]

            def opener(request,timeout):
                aid,version=re.search(r'/src/(.+)v(\d+)$',request.full_url).groups()
                version=int(version);source_calls[aid,version]+=1
                if aid=='2504.17523' and version==2 and phase[0]==0:raise TimeoutError('synthetic timeout')
                if aid=='2503.06914' and version==1:return io.BytesIO(b'%PDF-1.7\nsynthetic PDF')
                payload=root/'payload'
                mock.sources.client.download(aid,version,payload)
                body=payload.read_bytes()
                if aid=='2504.17523' and version==1 and source_calls[aid,version]==1:body=body[:-4]
                return io.BytesIO(body)

            def transport(url,timeout,headers):
                aid=re.search(r'ARXIV:([^/]+)/references',url).group(1)
                s2_calls.append((aid,clock.now))
                if aid=='2503.06914' and phase[0]==0:return ApiResponse(429,{},b'')
                return ApiResponse(200,{},json.dumps(mock.references.reader.references_page(aid,0,100)).encode())

            client=ArxivSourceClient(opener=SimpleNamespace(open=opener),scheduler=scheduler,
                monotonic=clock.monotonic,sleep=clock.sleep,delay=0)
            reader=SemanticScholarClient(transport=transport,scheduler=scheduler,
                monotonic=clock.monotonic,sleep=clock.sleep,api_key='')
            modules=ModuleSet(mock.discovery,mock.metadata,SourcesModule(client,SourceExtractor()),
                ReferencesModule(reader,mock.references.dates))
            first=run_pipeline(o,modules)
            self.assertEqual(first.exit_code,2);self.assertTrue(first.validation['valid'])
            self.assertEqual(source_calls['2504.17523',1],2)
            self.assertEqual(source_calls['2504.17523',2],3)
            self.assertEqual([s2_calls[i+1][1]-s2_calls[i][1] for i in range(2)],[30.,60.])
            self.assertGreaterEqual(s2_calls[3][1]-s2_calls[2][1],120.)
            source=o.output_root/'raw/trong/2504-17523/v1/main.tex';before=source.read_bytes()
            phase[0]=1;second=run_pipeline(o,modules)
            self.assertEqual(second.executed_tasks,2)
            self.assertEqual(second.exit_code,2)  # The PDF-only record remains incomplete for TeX.
            self.assertTrue(second.validation['valid'])
            self.assertEqual(len(second.quality['issues']),1)
            self.assertEqual(second.quality['issues'][0]['arxiv_id'],'2503.06914')
            self.assertEqual(source.read_bytes(),before)
            self.assertEqual(source_calls['2504.17523',1],2)
            self.assertEqual(source_calls['2503.06914',1],1)
            self.assertEqual(source_calls['2504.17523',2],4)
            accepted=[json.loads(line) for line in (o.output_root/'runs/stability/accepted_results.jsonl').read_text().splitlines()]
            self.assertEqual(len(accepted),10)
            self.assertEqual(len({(r['owner'],r['step'],r['arxiv_id'],r['version']) for r in accepted}),10)
            self.assertEqual(run_pipeline(o,modules).executed_tasks,0)

    def test_first_page_reference_failure_is_recorded_and_later_papers_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            o=RunOptions('first-page',Path(tmp),tuple(c['owner'] for c in ASSIGNED_RANGES),True)
            modules=make_mock_modules(o);original=modules.references.reader.references_page
            def page(aid,offset,limit):
                if aid=='2504.17523':raise RemoteError('rate_limited','synthetic busy',True,429)
                return original(aid,offset,limit)
            with patch.object(modules.references.reader,'references_page',side_effect=page):
                outcome=run_pipeline(o,modules)
            self.assertEqual(outcome.exit_code,2);self.assertTrue(outcome.validation['valid'])
            accepted=[json.loads(line) for line in (Path(tmp)/'runs/first-page/accepted_results.jsonl').read_text().splitlines()]
            self.assertEqual(len(accepted),24)
            self.assertEqual(sum(r['step']=='references' for r in accepted),4)
            events=[json.loads(line) for line in (Path(tmp)/'logs/first-page/events.jsonl').read_text().splitlines()]
            failed=next(r for r in events if r['step']=='references' and r['owner']=='trong')
            self.assertEqual(failed['status'],'failed');self.assertEqual(failed['metrics'],{})

    def test_missing_title_keeps_complete_history_and_known_source_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('no-title',root,('trong',),True)
            modules=make_mock_modules(o);original=modules.metadata.client.record
            def record(aid):return original(aid).replace(b'<title>SYNTHETIC T5 example</title>',b'')
            with patch.object(modules.metadata.client,'record',side_effect=record):
                outcome=run_pipeline(o,modules)
            self.assertEqual(outcome.exit_code,2);self.assertTrue(outcome.validation['valid'])
            self.assertTrue(outcome.scope['scope']['versions_complete'])
            self.assertTrue((root/'raw/trong/2504-17523/v1/main.tex').is_file())
            self.assertTrue((root/'raw/trong/2504-17523/v2/main.tex').is_file())
            self.assertTrue((root/'raw/trong/2504-17523/raw_references.json').is_file())

    def test_interrupt_during_reference_pair_restores_previous_association(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('pair-interrupt',root,('trong',),True)
            modules=make_mock_modules(o);original=modules.references.reader.references_page
            def partial(aid,offset,limit):
                if offset:raise RemoteError('network_error','synthetic page failure',True)
                return {'data':original(aid,0,limit)['data'][:1],'next':1}
            with patch.object(modules.references.reader,'references_page',side_effect=partial):
                first=run_pipeline(o,modules)
            self.assertEqual(first.exit_code,2)
            from arxiv_pipeline.runner.models import paper_spec
            from arxiv_pipeline.runner.writer import Publisher
            key=paper_spec({'owner':'trong','arxiv_id':'2504.17523'},'references').key
            with StateStore(root/'state.sqlite',o.run_id) as s:before=s.get_task(key)['accepted_result']
            import os
            replace=os.replace
            def interrupt(source,dest):
                if '/new/' in str(source) and str(dest).endswith('/raw_references.json'):raise KeyboardInterrupt()
                return replace(source,dest)
            with patch('arxiv_pipeline.runner.writer.os.replace',side_effect=interrupt):
                stopped=run_pipeline(o,modules)
            self.assertEqual(stopped.exit_code,130)
            with StateStore(root/'state.sqlite',o.run_id) as s:
                pub=Publisher(o.context,s);pub.recover();pub.recover()
                self.assertEqual(s.get_task(key)['accepted_result'],before)
                self.assertEqual(pub.verify_task(key),[])

    def test_failed_handoff_replace_cannot_leave_a_valid_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('handoff',root,('trong',),True)
            modules=make_mock_modules(o);original=modules.metadata.client.record
            def missing(aid):
                body=original(aid);start=body.index(b'<version version="v2">');end=body.index(b'</version>',start)+10
                return body[:start]+b'<version version="v2"/>'+body[end:]
            with patch.object(modules.metadata.client,'record',side_effect=missing):self.assertEqual(run_pipeline(o,modules).exit_code,2)
            import os
            replace=os.replace
            def fail(source,dest):
                if str(dest).endswith('/accepted_results.jsonl'):raise OSError('interrupted handoff')
                return replace(source,dest)
            with patch('arxiv_pipeline.runner.exports.os.replace',side_effect=fail):
                with self.assertRaises(OSError):run_pipeline(o,modules)
            self.assertFalse(json.loads((root/'runs/handoff/validation.json').read_text())['valid'])
            self.assertEqual(run_pipeline(o,modules).exit_code,0)

    def test_interrupt_during_source_replacement_preserves_previous_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('source-interrupt',root,('trong',),True)
            modules=make_mock_modules(o);original=modules.sources.fetch_sources
            def partial(task,version,ctx):
                r=original(task,version,ctx)
                if version==1:r['status']='partial';r['metrics']['download_bytes']=None;r['missing_fields']=['/metrics/download_bytes']
                return r
            with patch.object(modules.sources,'fetch_sources',side_effect=partial):self.assertEqual(run_pipeline(o,modules).exit_code,2)
            from arxiv_pipeline.runner.models import paper_spec
            from arxiv_pipeline.runner.writer import Publisher
            key=paper_spec({'owner':'trong','arxiv_id':'2504.17523'},'sources',1).key
            with StateStore(root/'state.sqlite',o.run_id) as s:before=s.get_task(key)['accepted_result']
            import os
            replace=os.replace
            def interrupt(source,dest):
                if '/new/' in str(source) and str(dest).endswith('/2504-17523/v1'):raise KeyboardInterrupt()
                return replace(source,dest)
            with patch('arxiv_pipeline.runner.writer.os.replace',side_effect=interrupt):self.assertEqual(run_pipeline(o,modules).exit_code,130)
            with StateStore(root/'state.sqlite',o.run_id) as s:
                p=Publisher(o.context,s);p.recover();p.recover()
                self.assertEqual(s.get_task(key)['accepted_result'],before);self.assertEqual(p.verify_task(key),[])

    def test_resume_programming_error_invalidates_old_handoff_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('marker',root,('trong',),True);modules=make_mock_modules(o)
            self.assertEqual(run_pipeline(o,modules).exit_code,0)
            (root/'raw/trong/2504-17523/v1/main.tex').unlink()
            with patch.object(modules.sources,'fetch_sources',side_effect=RuntimeError('synthetic bug')):
                with self.assertRaises(RuntimeError):run_pipeline(o,modules)
            self.assertFalse(json.loads((root/'runs/marker/validation.json').read_text())['valid'])

    def test_mixed_outcomes_preserve_scope_and_repair_only_missing_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('mixed',root,tuple(c['owner'] for c in ASSIGNED_RANGES),True)
            modules=make_mock_modules(o);phase=[0];downloads=[]
            original_download=modules.sources.client.download
            def download(aid,version,path):
                downloads.append((aid,version))
                if aid=='2503.06914' and version==1:
                    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'%PDF-1.7\nPDF only');return 17
                if aid=='2504.17523' and version==2 and phase[0]==0:
                    raise RemoteError('network_error','synthetic timeout',True)
                return original_download(aid,version,path)
            original_record=modules.metadata.client.record
            def record(aid):
                body=original_record(aid)
                if aid=='2409.03594' and phase[0]==0:
                    start=body.index(b'<version version="v2">');end=body.index(b'</version>',start)+len(b'</version>')
                    body=body[:start]+b'<version version="v2"/>'+body[end:]
                return body
            original_page=modules.references.reader.references_page
            def page(aid,offset,limit):
                if aid=='2504.17523' and phase[0]==0:
                    if offset:raise RemoteError('network_error','synthetic page failure',True)
                    return {'data':original_page(aid,0,limit)['data'][:1],'next':1}
                return original_page(aid,offset,limit)
            with patch.object(modules.sources.client,'download',side_effect=download), \
                 patch.object(modules.metadata.client,'record',side_effect=record), \
                 patch.object(modules.references.reader,'references_page',side_effect=page):
                first=run_pipeline(o,modules)
                self.assertEqual(first.exit_code,2);self.assertTrue(first.validation['valid'])
                self.assertEqual(first.scope['scope']['paper_count'],5)
                self.assertFalse(first.scope['scope']['versions_complete'])
                phase[0]=1;second=run_pipeline(o,modules)
                self.assertEqual(second.exit_code,2) # PDF remains unsupported, not a retry failure.
                self.assertEqual(second.executed_tasks,3)
                self.assertTrue(second.validation['valid']);self.assertTrue(second.scope['scope']['versions_complete'])
            self.assertEqual(downloads.count(('2503.06914',1)),1)
            self.assertEqual(downloads.count(('2504.17523',2)),2)
            accepted=[json.loads(s) for s in (root/'runs/mixed/accepted_results.jsonl').read_text().splitlines()]
            self.assertEqual(len(accepted),25)
            self.assertEqual(sum(r['status']=='unsupported' for r in accepted),1)
            self.assertFalse(any(p.suffix.lower() in ('.png','.sty','.cls','.bbl') for p in (root/'raw').rglob('*')))
            self.assertIn(b'\\includegraphics{figure.png}',(root/'raw/trong/2504-17523/v1/main.tex').read_bytes())

    def test_resume_recovers_published_source_without_redownload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('crash',root,('trong',),True);modules=make_mock_modules(o)
            complete=StateStore.complete
            def crash(store,attempt,accepted):
                row=store.connection.execute('SELECT result_json FROM attempts WHERE attempt_id=?',(attempt,)).fetchone()
                r=json.loads(row[0])
                if accepted and r['step']=='sources' and r['version']==1:raise OSError('simulated process failure before DB commit')
                return complete(store,attempt,accepted)
            with patch.object(StateStore,'complete',new=crash):
                with self.assertRaises(OSError):run_pipeline(o,modules)
            path=root/'raw/trong/2504-17523/v1/main.tex';before=path.read_bytes()
            original=modules.sources.client.download;calls=[]
            def download(aid,version,path):calls.append(version);return original(aid,version,path)
            with patch.object(modules.sources.client,'download',side_effect=download):
                resumed=run_pipeline(o,modules)
            self.assertEqual(resumed.exit_code,0);self.assertEqual(calls,[2]);self.assertEqual(path.read_bytes(),before)
