from datetime import datetime,timezone
from email.utils import format_datetime
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.error import HTTPError
from arxiv_pipeline.http import RequestScheduler
from arxiv_pipeline.discovery.api import ApiClient,ApiResponse,RemoteError
from arxiv_pipeline.metadata.api import OaiClient
from arxiv_pipeline.references.api import ArxivDateClient,SemanticScholarClient
from arxiv_pipeline.sources.api import ArxivSourceClient


class Clock:
    def __init__(self):self.now=0.
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class SchedulerTests(unittest.TestCase):
    def test_shared_arxiv_clients_and_s2_rolling_window(self):
        c=Clock();s=RequestScheduler(monotonic=c.monotonic,sleep=c.sleep);starts=[]
        def transport(*args):starts.append(c.now);return ApiResponse(200,{},b"body")
        kw=dict(scheduler=s,transport=transport,monotonic=c.monotonic,sleep=c.sleep)
        ApiClient(**kw).query(["2504.17523"])
        OaiClient(**kw).record("2504.17523")
        ArxivDateClient(**kw)._get("https://export.arxiv.org/api/query")
        self.assertEqual(starts,[0.,3.,6.])
        c=Clock();s=RequestScheduler(monotonic=c.monotonic,sleep=c.sleep);starts=[]
        for _ in range(101):
            with s.request("semantic_scholar"):starts.append(c.now)
        self.assertEqual(starts[:3],[0.,1.,2.]);self.assertEqual(starts[-1],300.)

    def test_stream_cooldown_survives_to_another_client(self):
        for status in (429,503):
            for date in (False,True):
                with self.subTest(status=status,date=date),tempfile.TemporaryDirectory() as tmp:
                    c=Clock();s=RequestScheduler(monotonic=c.monotonic,sleep=c.sleep)
                    header=format_datetime(datetime.fromtimestamp(120,timezone.utc),usegmt=True) if date else "120"
                    def opener(req,timeout):raise HTTPError(req.full_url,status,"busy",{"Retry-After":header},io.BytesIO())
                    client=ArxivSourceClient(opener=SimpleNamespace(open=opener),max_attempts=1,
                        scheduler=s,monotonic=c.monotonic,wall_clock=lambda:c.now,sleep=c.sleep)
                    with self.assertRaises(RemoteError):client.download("2504.17523",1,Path(tmp)/"x")
                    calls=[]
                    def transport(*args):calls.append(c.now);return ApiResponse(200,{},b"ok")
                    OaiClient(scheduler=s,transport=transport,monotonic=c.monotonic,sleep=c.sleep).record("2504.17523")
                    self.assertEqual(calls,[120.])

    def test_wait_interrupt_does_not_count_unsent_request(self):
        c=Clock();s=RequestScheduler(monotonic=c.monotonic,sleep=c.sleep)
        with s.request("arxiv"):pass
        def interrupt(_):raise KeyboardInterrupt()
        s.sleep=interrupt
        client=ApiClient(scheduler=s,transport=lambda *args:ApiResponse(200,{},b"ok"))
        with self.assertRaises(KeyboardInterrupt):client.query(["2504.17523"])
        self.assertEqual(client.attempts,0)
