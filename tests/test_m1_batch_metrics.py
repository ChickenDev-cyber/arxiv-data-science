"""Assignment denominators must survive batching and all-version downloads."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from m1_batch_metrics import summarize_assignment

TASKS=[{'owner':'trong','arxiv_id':'2504.17523'},{'owner':'trong','arxiv_id':'2504.17524'}]
def metadata(aid,versions):return {'owner':'trong','arxiv_id':aid,'step':'metadata','version':None,'status':'success','data':{'versions':[{'version':v} for v in versions],'versions_complete':True}}
def source(aid,version,before,after):return {'owner':'trong','arxiv_id':aid,'step':'sources','version':version,'status':'success','metrics':{'extracted_bytes_before_filter':before,'retained_source_bytes':after,'download_bytes':7}}
def references(aid,raw_count,complete_entries,total_entries):
 entries={str(i):{'title':'Title','authors':['A'],'submission_date':'2020-01-01','semantic_scholar_id':'s2'} for i in range(total_entries)}
 for i in range(complete_entries,total_entries):entries[str(i)]['authors']=None
 return {'owner':'trong','arxiv_id':aid,'step':'references','version':None,'status':'success' if complete_entries==total_entries else 'partial','data':{'complete':True,'raw_references':[{}]*raw_count,'references':entries}}

class AssignmentMetricsTests(unittest.TestCase):
 def records(self):
  a,b=[t['arxiv_id'] for t in TASKS]
  return [metadata(a,[1,2]),metadata(b,[1]),source(a,1,10,2),source(a,2,30,4),source(b,1,100,10),references(a,4,2,2),references(b,1,0,1)]
 def test_sizes_average_publications_after_summing_all_versions(self):
  r=summarize_assignment(TASKS,self.records())
  self.assertEqual(r['source_sizes']['sample_publications'],2)
  self.assertEqual(r['source_sizes']['mean_before_filter_bytes'],70)
  self.assertEqual(r['source_sizes']['mean_retained_bytes'],8)
  self.assertEqual(r['versions']['expected'],3)
 def test_all_raw_references_including_non_arxiv_are_in_mean(self):
  r=summarize_assignment(TASKS,self.records())['references']
  self.assertEqual(r['total_raw_references_complete_lists'],5)
  self.assertEqual(r['mean_all_references_per_fully_fetched_paper'],2.5)
  self.assertEqual(r['metadata_macro_success_rate'],0.5)
  self.assertAlmostEqual(r['metadata_micro_success_rate'],2/3)
 def test_unmeasured_source_size_is_not_zero_or_compressed_bytes(self):
  values=self.records();values[4]['metrics']['extracted_bytes_before_filter']=None
  r=summarize_assignment(TASKS,values)['source_sizes']
  self.assertEqual(r['sample_publications'],1)
  self.assertEqual(r['excluded_publications'],1)
  self.assertEqual(r['mean_before_filter_bytes'],40)
  self.assertEqual(r['mean_retained_bytes'],6)
 def test_failed_source_with_leftover_sizes_is_not_a_success_size_sample(self):
  values=self.records();values[4]['status']='failed'
  r=summarize_assignment(TASKS,values)['source_sizes']
  self.assertEqual(r['sample_publications'],1)
  self.assertEqual(r['mean_before_filter_bytes'],40)
 def test_incomplete_reference_list_excluded_with_visible_denominator(self):
  values=self.records();values[-1]['data']['complete']=False
  r=summarize_assignment(TASKS,values)['references']
  self.assertEqual(r['fully_fetched_papers'],1)
  self.assertEqual(r['unfetched_or_partial_papers'],1)
  self.assertEqual(r['mean_all_references_per_fully_fetched_paper'],4)
  self.assertEqual(r['query_success_rate'],0.5)

if __name__=='__main__':unittest.main()
