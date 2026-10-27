from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from arxiv_pipeline.runner.models import RunOptions,ModuleSet
from arxiv_pipeline.runner.service import run_pipeline
from .helpers import metadata_result,discovery_result,references_result,source_result


class Modules:
    def __init__(self):self.calls=[];self.fail_v2=True;self.metadata_failure=False;self.bug=False
    def discover(self,config,context):
        self.calls.append('discovery');r=discovery_result();r['provenance']['scope_limited']='true';return r
    def fetch_metadata(self,task,context):
        self.calls.append('metadata')
        if self.bug:raise RuntimeError('module programming bug')
        r=metadata_result()
        if self.metadata_failure:r.update(status='failed',data=None,error={'code':'network_error','message':'offline','retryable':True,'http_status':None})
        return r
    def fetch_sources(self,task,version,context):
        self.calls.append(('sources',version))
        if version==2 and self.fail_v2:
            from arxiv_pipeline.examples import result
            r=result('trong','sources','2504.17523',None,'failed',2)
            r['error']={'code':'network_error','message':'offline','retryable':True,'http_status':None}
            r['metrics']={k:None for k in ('download_bytes','extracted_bytes_before_filter','retained_source_bytes','removed_figure_bytes','removed_other_bytes')}
            return r
        r=source_result(context['output_root'])
        if version==2:
            old=context['output_root']/'staging/mock-demo/trong/2504-17523/v1'
            old.rename(old.parent/'v2');r['version']=2;r['data']['source_dir']='raw/trong/2504-17523/v2'
        return r
    def fetch_references(self,task,context):self.calls.append('references');return references_result()
    @property
    def bundle(self):return ModuleSet(self,self,self,self)


class RunnerTests(unittest.TestCase):
    def test_resume_only_failed_version_and_repair_changed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            o=RunOptions('mock-demo',Path(tmp),('trong',),True);m=Modules()
            a=run_pipeline(o,m.bundle)
            self.assertEqual(a.exit_code,2);self.assertEqual(a.executed_tasks,5)
            m.fail_v2=False
            b=run_pipeline(o,m.bundle)
            self.assertEqual(b.exit_code,0);self.assertEqual(b.executed_tasks,1)
            self.assertEqual(m.calls.count(('sources',1)),1)
            (Path(tmp)/'raw/trong/2504-17523/v1/main.tex').write_bytes(b'changed')
            c=run_pipeline(o,m.bundle)
            self.assertEqual(c.exit_code,0);self.assertEqual(c.executed_tasks,1)
            self.assertEqual(m.calls.count(('sources',1)),2)

    def test_stop_and_metadata_failure_dont_block_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            o=RunOptions('mock-demo',Path(tmp),('trong',),True);m=Modules()
            a=run_pipeline(o,m.bundle,stop_after_tasks=2)
            self.assertEqual(a.executed_tasks,2)
            self.assertEqual(a.scope['scope']['paper_count'],1)
            m.fail_v2=False
            b=run_pipeline(o,m.bundle)
            self.assertEqual(b.exit_code,0);self.assertEqual(b.executed_tasks,3)
        with tempfile.TemporaryDirectory() as tmp:
            m=Modules();m.metadata_failure=True
            r=run_pipeline(RunOptions('mock-demo',Path(tmp),('trong',),True),m.bundle)
            self.assertEqual(r.exit_code,2);self.assertIn('references',m.calls)
            self.assertFalse(any(isinstance(c,tuple) for c in m.calls))

    def test_programming_bug_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Modules();m.bug=True
            with self.assertRaisesRegex(RuntimeError,'programming bug'):
                run_pipeline(RunOptions('mock-demo',Path(tmp),('trong',),True),m.bundle)

    def test_keyboard_interrupt_returns_130_and_can_resume(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            o=RunOptions('mock-demo',Path(tmp),('trong',),True);m=Modules();m.fail_v2=False
            with patch.object(m,'fetch_sources',side_effect=KeyboardInterrupt):
                stopped=run_pipeline(o,m.bundle)
            self.assertEqual(stopped.exit_code,130)
            resumed=run_pipeline(o,m.bundle)
            self.assertEqual(resumed.exit_code,0)
            self.assertEqual(m.calls.count('metadata'),1)
