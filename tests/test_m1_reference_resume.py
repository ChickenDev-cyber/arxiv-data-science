"""Resume real runner state without recrawling existing inputs."""
from pathlib import Path
import hashlib,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
try:
    from m1_resume_references import resume_references,ReferenceHttpAudit
except ModuleNotFoundError as exc:
    if exc.name!='m1_resume_references':raise
    resume_references=ReferenceHttpAudit=None
from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.service import PipelineRunner
from arxiv_pipeline.discovery.api import ApiResponse
from test_m1_batch_pipeline import Factory

class ReferenceResumeTests(unittest.TestCase):
    def require_resume(self):self.assertTrue(callable(resume_references),'References-only resume is missing')

    def test_resume_reuses_metadata_sources_and_keeps_original_measurement(self):
        self.require_resume()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'corpus';options=RunOptions('resume-test',root,('trong',),True,2,2)
            PipelineRunner(Factory('batch')).run(options,stage='sources')
            files={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in (root/'raw').rglob('*') if f.is_file()}
            old=root/'experiment-metrics.json';old.write_text('{"original_measurement":true}\n')
            factory=Factory('batch')
            outcome,report=resume_references(root,'resume-test',Path(tmp)/'report',factory=factory)
            self.assertEqual(factory.posts,1)
            self.assertTrue(report['references_complete'])
            self.assertEqual(report['assignment']['references']['fully_fetched_papers'],2)
            self.assertEqual(report['measurement_scope'],'references-only resume')
            self.assertTrue(report['protected_inputs_unchanged'])
            self.assertEqual(old.read_text(),'{"original_measurement":true}\n')
            for name,digest in files.items():self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
            self.assertEqual(outcome.exit_code,0)
            # A second invocation must reuse accepted references, not POST again.
            again=Factory('batch')
            _,second=resume_references(root,'resume-test',Path(tmp)/'report2',factory=again)
            self.assertTrue(second['references_complete']);self.assertEqual(again.posts,0)

    def test_missing_state_is_rejected_before_creating_report_or_corpus(self):
        self.require_resume()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'missing';report=Path(tmp)/'report'
            with self.assertRaises((FileNotFoundError,ValueError)):resume_references(root,'missing-run',report)
            self.assertFalse(root.exists());self.assertFalse(report.exists())

    def test_unverified_moved_state_is_rejected_before_writing(self):
        self.require_resume()
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            root=Path(tmp)/'original';options=RunOptions('move-test',root,('trong',),True,2,2)
            PipelineRunner(Factory('batch')).run(options,stage='sources')
            moved=Path(tmp)/'moved';shutil.copytree(root,moved)
            before=hashlib.sha256((moved/'state.sqlite').read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError,'restore|root'):resume_references(moved,'move-test',Path(tmp)/'report')
            self.assertEqual(hashlib.sha256((moved/'state.sqlite').read_bytes()).hexdigest(),before)
            self.assertFalse((Path(tmp)/'report').exists())

    def test_http_trace_proves_auth_presence_without_exposing_key(self):
        self.assertTrue(callable(ReferenceHttpAudit),'Safe request audit is missing')
        with tempfile.TemporaryDirectory() as tmp:
            from unittest.mock import patch
            key='SYNTHETIC-PRIVATE-KEY'
            with patch.dict('os.environ',{'S2_API_KEY':key}):
                audit=ReferenceHttpAudit(Path(tmp))
                call=audit.wrap('semantic_scholar','POST',lambda *args:ApiResponse(429,{'Retry-After':'60','x-amzn-requestid':'support-id','Set-Cookie':'secret-cookie'},b''))
                call('https://api.semanticscholar.org/graph/v1/paper/batch?fields=paperId',20,{'x-api-key':key,'User-Agent':'fixture'},b'{"ids":["ARXIV:2504.17523"]}')
            text=(Path(tmp)/'request-trace.jsonl').read_text();record=json.loads(text)
            self.assertTrue(record['key_present']);self.assertTrue(record['key_matches_environment'])
            self.assertEqual(record['status'],429);self.assertEqual(record['response_headers']['retry-after'],'60')
            self.assertNotIn(key,text);self.assertNotIn('secret-cookie',text)

    def test_missing_or_incomplete_metadata_is_rejected_without_state_change(self):
        self.require_resume()
        for incomplete in (False,True):
            with self.subTest(incomplete=incomplete),tempfile.TemporaryDirectory() as tmp:
                class IncompleteFactory(Factory):
                    def build(self,options,checkpoints):
                        modules=super().build(options,checkpoints)
                        original=modules.metadata.fetch_metadata
                        def partial(*args):
                            result=original(*args);result['status']='partial'
                            result['data']['versions_complete']=False
                            result['missing_fields']=['/data/versions']
                            return result
                        modules.metadata.fetch_metadata=partial
                        return modules
                root=Path(tmp)/'corpus';options=RunOptions('missing-meta',root,('trong',),True,2,2)
                initial=IncompleteFactory('batch') if incomplete else Factory('batch')
                PipelineRunner(initial).run(options,stage='sources' if incomplete else 'discovery')
                before=hashlib.sha256((root/'state.sqlite').read_bytes()).hexdigest()
                with self.assertRaisesRegex(ValueError,'metadata|history'):
                    resume_references(root,'missing-meta',Path(tmp)/'report',factory=Factory('batch'))
                self.assertEqual(hashlib.sha256((root/'state.sqlite').read_bytes()).hexdigest(),before)
                self.assertFalse((Path(tmp)/'report').exists())

    def test_corrupted_cached_source_is_rejected_before_requests(self):
        self.require_resume()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'corpus';options=RunOptions('bad-source',root,('trong',),True,2,2)
            PipelineRunner(Factory('batch')).run(options,stage='sources')
            next((root/'raw').rglob('main.tex')).write_bytes(b'corrupted')
            before=hashlib.sha256((root/'state.sqlite').read_bytes()).hexdigest()
            with self.assertRaises(ValueError):resume_references(root,'bad-source',Path(tmp)/'report',factory=Factory('batch'))
            self.assertEqual(hashlib.sha256((root/'state.sqlite').read_bytes()).hexdigest(),before)
            self.assertFalse((Path(tmp)/'report').exists())

if __name__=='__main__':unittest.main()
