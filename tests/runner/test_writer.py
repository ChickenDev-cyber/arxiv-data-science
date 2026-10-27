from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from arxiv_pipeline.runner.models import RunOptions, paper_spec
from arxiv_pipeline.runner.state import StateStore
from arxiv_pipeline.runner.writer import Publisher
from .helpers import PAPER, references_result, metadata_result, source_result


class WriterTests(unittest.TestCase):
    def setup_store(self, root):
        options = RunOptions("mock-demo", root, ("trong",), True)
        store = StateStore(root/"state.sqlite", "mock-demo"); self.addCleanup(store.__exit__)
        store.open_run(options)
        return store, Publisher(options.context, store)

    def attempt(self, store, result):
        spec = paper_spec(PAPER, result["step"], result["version"])
        store.ensure_task(spec); a = store.mark_running(spec.key); store.record(a, result, [])
        return a, spec.key

    def test_publishes_data_and_sources_and_verifies_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); store, pub = self.setup_store(root)
            for r in (metadata_result(), references_result(), source_result(root)):
                a, key = self.attempt(store, r)
                self.assertTrue(pub.publish(a, r)); self.assertEqual(pub.verify_task(key), [])
            base = root / "raw/trong/2504-17523"
            self.assertEqual(json.loads((base/"metadata.json").read_text()), metadata_result()["data"])
            self.assertEqual(json.loads((base/"raw_references.json").read_text()), references_result()["data"]["raw_references"])
            self.assertEqual((base/"v1/main.tex").read_bytes(), b"tex")
            (base/"v1/main.tex").write_bytes(b"bad bytes")
            self.assertTrue(pub.verify_task(paper_spec(PAPER, "sources", 1).key))

    def test_recovery_finishes_published_files_before_db_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store,pub=self.setup_store(root)
            r=references_result(); a,key=self.attempt(store,r)
            with patch.object(store,"complete",side_effect=OSError("crash before DB accept")):
                with self.assertRaises(OSError): pub.publish(a,r)
            self.assertIsNone(store.get_task(key)["accepted_result"])
            pub.recover(); pub.recover()
            self.assertEqual(store.get_task(key)["accepted_result"],r)
            self.assertEqual(pub.verify_task(key),[])

    def test_recovery_rolls_back_partial_reference_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); store,pub=self.setup_store(root)
            old=references_result(); a,key=self.attempt(store,old); pub.publish(a,old)
            newer=references_result(); newer["data"]["raw_references"].append({"citedPaper":None})
            newer["metrics"]["s2_reference_count"]=2
            b,_=self.attempt(store,newer)
            import os
            real=os.replace
            def fail_second(source,dest):
                if str(dest).endswith('/raw_references.json') and '/new/' in str(source):
                    raise OSError("crash in pair")
                return real(source,dest)
            with patch('arxiv_pipeline.runner.writer.os.replace',side_effect=fail_second):
                with self.assertRaises(OSError): pub.publish(b,newer)
            pub.recover();pub.recover()
            self.assertEqual(store.get_task(key)["accepted_result"],old)
            self.assertEqual(pub.verify_task(key),[])

    def test_failed_retry_and_unsupported_do_not_replace_source_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,pub=self.setup_store(root)
            old=source_result(root);a,key=self.attempt(store,old);pub.publish(a,old)
            failed=dict(old,status="failed",data=None,error={"code":"network_error","message":"offline","retryable":True,"http_status":None})
            b,_=self.attempt(store,failed);self.assertFalse(pub.publish(b,failed))
            self.assertEqual(store.get_task(key)["accepted_result"],old)

    def test_prepared_before_any_move_preserves_unchanged_pair_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,pub=self.setup_store(root)
            old=references_result();a,key=self.attempt(store,old);pub.publish(a,old)
            newer=references_result();newer["data"]["raw_references"].append({"citedPaper":None})
            newer["metrics"]["s2_reference_count"]=2
            b,_=self.attempt(store,newer);real=store.prepare
            def stop(*args):real(*args);raise OSError("after journal")
            with patch.object(store,"prepare",side_effect=stop):
                with self.assertRaises(OSError):pub.publish(b,newer)
            pub.recover();pub.recover()
            self.assertEqual(pub.verify_task(key),[])
            self.assertEqual(store.get_task(key)["accepted_result"],old)

    def test_malformed_journal_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store,pub=self.setup_store(root)
            r=metadata_result();a,key=self.attempt(store,r)
            store.prepare(a,{"transaction":"a"*32,"operations":[{"target":"../outside"}],"artifacts":[]})
            with self.assertRaises(ValueError):pub.recover()
