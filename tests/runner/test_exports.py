from pathlib import Path
import json
import tempfile
import unittest
from arxiv_pipeline.runner.models import RunOptions,paper_spec,discovery_spec
from arxiv_pipeline.runner.scope import initial_scope,update_scope
from arxiv_pipeline.runner.state import StateStore
from arxiv_pipeline.runner.writer import Publisher
from arxiv_pipeline.runner.exports import Exporter
from .helpers import PAPER,metadata_result,discovery_result


class ExportTests(unittest.TestCase):
    def test_scope_preserves_failed_paper_and_log_rebuild_is_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);o=RunOptions('mock-demo',root,('trong',),True)
            with StateStore(root/'state.sqlite',o.run_id) as s:
                s.open_run(o);p=Publisher(o.context,s);e=Exporter(o.context,s,p)
                for r in (discovery_result(2),metadata_result()):
                    spec=discovery_spec(o.ranges[0]) if r['step']=='discovery' else paper_spec(PAPER,'metadata')
                    s.ensure_task(spec);a=s.mark_running(spec.key);s.record(a,r,[]);p.publish(a,r)
                scope=update_scope(initial_scope(o),[PAPER,{'owner':'trong','arxiv_id':'2504.17524'}],list(s.iter_results(accepted_only=True)))
                s.update_scope(scope);e.rebuild_events();e.checkpoint()
                wrapper=json.loads((root/'runs/mock-demo/scope.json').read_text())
                self.assertEqual(wrapper['scope']['paper_count'],2)
                self.assertTrue(e.validation['valid']);self.assertFalse(e.quality['valid'])
                log=root/'logs/mock-demo/events.jsonl'
                with log.open('a') as f:f.write('{broken')
                e.rebuild_events();e.checkpoint();e.checkpoint()
                self.assertEqual(len([json.loads(line) for line in log.read_text().splitlines()]),2)
                self.assertEqual(len((root/'runs/mock-demo/accepted_results.jsonl').read_text().splitlines()),2)

    def test_wrong_run_and_duplicate_are_rejected(self):
        from arxiv_pipeline.validation.corpus import validate_handoff
        o=RunOptions('mock-demo',Path('/tmp/example'),('trong',),True)
        scope=update_scope(initial_scope(o),[PAPER],[metadata_result()])
        r=metadata_result()
        self.assertTrue(validate_handoff(scope,[r,r]))
        r['is_mock']=False
        self.assertTrue(validate_handoff(scope,[r]))
