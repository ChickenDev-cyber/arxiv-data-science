"""Real-client regressions for the experiment-only arXiv cooldown."""
from pathlib import Path
import io,sys,tempfile,unittest
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from m1_experiment_scheduler import ExperimentRequestScheduler
from arxiv_pipeline.discovery.api import ApiClient,ApiResponse,RemoteError
from arxiv_pipeline.metadata.api import OaiClient
from arxiv_pipeline.references.checkpoint import JsonFileCheckpointStore,MemoryCheckpointStore
from arxiv_pipeline.runner.cli import checkpoint_validator
from arxiv_pipeline.runner.models import RunOptions
from m1_batch_experiment import ExperimentFactory,HttpAudit,DiskObserver

class Clock:
    def __init__(self,now=0):self.now=float(now)
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds

class ExperimentSchedulerTests(unittest.TestCase):
    def scheduler(self,clock,store=None):
        return ExperimentRequestScheduler(monotonic=clock.monotonic,sleep=clock.sleep,
            wall_clock=clock.monotonic,checkpoints=store)

    def test_429_without_header_blocks_other_arxiv_client_for_one_minute(self):
        c=Clock();s=self.scheduler(c);starts=[]
        def busy(*args):starts.append(c.now);return ApiResponse(429,{},b'')
        kw=dict(scheduler=s,monotonic=c.monotonic,sleep=c.sleep,max_attempts=1)
        with self.assertRaises(RemoteError):ApiClient(transport=busy,**kw).query(['2504.17523'])
        def ok(*args):starts.append(c.now);return ApiResponse(200,{},b'ok')
        OaiClient(transport=ok,**kw).record('2504.17523')
        self.assertEqual(starts,[0.,60.])

    def test_repeated_429_escalates_and_caps_fallback(self):
        c=Clock();s=self.scheduler(c);starts=[]
        for _ in range(6):
            with s.request('arxiv'):starts.append(c.now)
            s.rate_limited('arxiv',0)
        self.assertEqual(starts,[0.,60.,180.,420.,720.,1020.])

    def test_long_server_deadline_is_never_capped(self):
        c=Clock();s=self.scheduler(c)
        s.rate_limited('arxiv',900)
        with s.request('arxiv'):self.assertEqual(c.now,900.)

    def test_disk_resume_restores_deadline_and_escalation(self):
        with tempfile.TemporaryDirectory() as root:
            c=Clock();s=self.scheduler(c,JsonFileCheckpointStore(root))
            with s.request('arxiv'):pass
            s.rate_limited('arxiv',0)
            record=JsonFileCheckpointStore(root).load('rate_limits','["arxiv"]')
            self.assertEqual(checkpoint_validator()(record),[])
            c2=Clock(20);resumed=self.scheduler(c2,JsonFileCheckpointStore(root))
            with resumed.request('arxiv'):self.assertEqual(c2.now,60.)
            resumed.rate_limited('arxiv',0)
            with resumed.request('arxiv'):self.assertEqual(c2.now,180.)

    def test_success_resets_streak_without_shortening_existing_deadline(self):
        c=Clock();s=self.scheduler(c)
        s.rate_limited('arxiv',0);s.rate_limited('arxiv',0)
        s.request_succeeded('arxiv')
        with s.request('arxiv'):self.assertEqual(c.now,120.)
        s.rate_limited('arxiv',0)
        with s.request('arxiv'):self.assertEqual(c.now,180.)

    def test_failed_persistence_retains_in_memory_cooldown(self):
        c=Clock();store=MemoryCheckpointStore();s=self.scheduler(c,store)
        store.fail_puts=True
        with self.assertRaises(OSError):s.rate_limited('arxiv',600)
        store.fail_puts=False
        with s.request('arxiv'):self.assertEqual(c.now,600.)
        s.rate_limited('arxiv',0)
        with s.request('arxiv'):self.assertEqual(c.now,720.)

    def test_s2_policy_and_independent_service_are_preserved(self):
        c=Clock();s=self.scheduler(c)
        s.rate_limited('arxiv',0)
        with s.request('semantic_scholar'):self.assertEqual(c.now,0.)
        s.rate_limited('semantic_scholar',0)
        with s.request('semantic_scholar'):self.assertEqual(c.now,30.)
        with s.request('arxiv'):self.assertEqual(c.now,60.)

    def test_factory_source_429_blocks_metadata_in_both_variants(self):
        for variant in ('baseline','batch'):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as root:
                root=Path(root);c=Clock();store=MemoryCheckpointStore()
                modules=ExperimentFactory(root,variant,HttpAudit(root/'audit.jsonl'),DiskObserver(root)).build(
                    RunOptions('source-cooldown',root,('trong',),False,5,5),store)
                source=modules.sources.client.client
                s=source.scheduler
                s.monotonic=c.monotonic;s.sleep=c.sleep;s.wall_clock=c.monotonic
                source.monotonic=c.monotonic;source.sleep=c.sleep;source.max_attempts=1
                def busy(req,timeout):raise HTTPError(req.full_url,429,'busy',{},io.BytesIO())
                with patch('m1_batch_experiment.urlopen',busy),self.assertRaises(RemoteError):
                    source.download('2504.17523',1,root/'source.tar')
                starts=[]
                def ok(*args):starts.append(c.now);return ApiResponse(200,{},b'ok')
                OaiClient(scheduler=s,transport=ok,monotonic=c.monotonic,sleep=c.sleep).record('2504.17523')
                self.assertEqual(starts,[60.])

    def test_source_error_stream_closes_when_cooldown_persistence_fails(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);c=Clock();store=MemoryCheckpointStore()
            modules=ExperimentFactory(root,'batch',HttpAudit(root/'audit.jsonl'),DiskObserver(root)).build(
                RunOptions('source-write-failure',root,('trong',),False,5,5),store)
            source=modules.sources.client.client;s=source.scheduler
            s.monotonic=c.monotonic;s.sleep=c.sleep;s.wall_clock=c.monotonic
            source.monotonic=c.monotonic;source.sleep=c.sleep;source.max_attempts=1
            body=io.BytesIO(b'busy')
            error=HTTPError('https://export.arxiv.org/src/2504.17523v1',429,'busy',{},body)
            def busy(req,timeout):
                store.fail_puts=True
                raise error
            with patch('m1_batch_experiment.urlopen',busy),self.assertRaises(RemoteError):
                source.download('2504.17523',1,root/'source.tar')
            self.assertTrue(body.closed)
            store.fail_puts=False
            with s.request('arxiv'):self.assertEqual(c.now,60.)

if __name__=='__main__':unittest.main()
