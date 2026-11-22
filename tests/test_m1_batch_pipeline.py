"""Full mocked-network M1 run through real services, publisher and profiler."""
from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from arxiv_pipeline.runner.models import ModuleSet
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.discovery.service import DiscoveryModule
from arxiv_pipeline.references.examples import FakeReader,FakeDates,cited,page
from arxiv_pipeline.references.service import ReferencesModule
from arxiv_pipeline.discovery.api import ApiResponse
from m1_batch_support import BatchReferencesModule,BatchPostClient
from m1_batch_experiment import profile_pipeline

IDS=['2504.17523','2504.17524']
class Query:
 attempts=0
 def query(self,ids):
  self.attempts+=1
  found=[a for a in IDS if a in ids]
  entries=''.join(f'<entry><id>https://arxiv.org/abs/{a}v2</id></entry>' for a in found)
  return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>{len(found)}</o:totalResults><o:startIndex>0</o:startIndex>{entries}</feed>').encode()
class Factory:
 def __init__(self,variant):self.variant=variant
 def build(self,options,checkpoints):
  modules=make_mock_modules(options,checkpoints=checkpoints)
  refs=[cited('2001.00001'),cited(None)]
  self.gets=FakeReader([page(refs),page(refs)])
  dates=FakeDates({'2001.00001':'2020-01-01'})
  if self.variant=='batch':
   def post(url,timeout,headers,body):
    ids=json.loads(body)['ids'];self.posts+=1
    return ApiResponse(200,{},json.dumps([{'externalIds':{'ArXiv':a.removeprefix('ARXIV:')},'referenceCount':2,'references':[r['citedPaper'] for r in refs]} for a in ids]).encode())
   self.posts=0
   reference=BatchReferencesModule(self.gets,dates,BatchPostClient(transport=post,delay=0),checkpoints=checkpoints)
  else:reference=ReferencesModule(self.gets,dates,checkpoints=checkpoints)
  return ModuleSet(DiscoveryModule(Query(),max_candidates=2,checkpoints=checkpoints),modules.metadata,modules.sources,reference)
class PipelineExperimentTests(unittest.TestCase):
 def test_full_baseline_and_batch_preserve_data_and_export_assignment_metrics(self):
  with tempfile.TemporaryDirectory() as tmp:
   reports=[]
   for variant in ['baseline','batch']:
    root=Path(tmp)/variant;factory=Factory(variant)
    report=profile_pipeline(root,'mock-batch-ab','trong',2,variant,2,factory=factory,is_mock=True)
    self.assertEqual(report['assignment']['publications'],2)
    self.assertEqual(report['assignment']['papers_complete'],2)
    self.assertEqual(report['assignment']['versions']['expected'],4)
    self.assertEqual(report['assignment']['references']['mean_all_references_per_fully_fetched_paper'],2)
    self.assertEqual(report['assignment']['source_sizes']['sample_publications'],2)
    self.assertGreater(report['benchmark']['wall_seconds'],0)
    self.assertGreater(report['benchmark']['peak_ram_bytes'],0)
    self.assertGreater(report['benchmark']['mean_ram_bytes'],0)
    self.assertGreater(report['benchmark']['peak_disk_bytes'],0)
    self.assertTrue((root/'experiment-metrics.json').is_file())
    self.assertEqual(set(report['timing']['per_paper_seconds']),set(IDS))
    if variant=='batch':
     self.assertEqual(factory.posts,1)
     self.assertEqual(factory.gets.calls,[])
    reports.append(report)
   self.assertEqual(reports[0]['assignment'],reports[1]['assignment'])
if __name__=='__main__':unittest.main()
