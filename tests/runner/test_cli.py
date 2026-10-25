from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arxiv_pipeline.runner.cli import main


class CLITests(unittest.TestCase):
    def test_mock_all_owners_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=['--mock','--all-owners','--run-id','mock-cli','--output-root',tmp]
            with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')), \
                 patch('arxiv_pipeline.discovery.api.urlopen',side_effect=AssertionError('network forbidden')), \
                 patch('arxiv_pipeline.sources.api.urlopen',side_effect=AssertionError('network forbidden')),redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(args),0)
                summary=json.loads(out.getvalue());self.assertEqual(summary['papers'],5)
                out.seek(0);out.truncate(0)
                self.assertEqual(main(['--resume','--run-id','mock-cli','--output-root',tmp]),0)
                self.assertEqual(json.loads(out.getvalue())['executed_tasks'],0)
            accepted=[json.loads(line) for line in (Path(tmp)/'runs/mock-cli/accepted_results.jsonl').read_text().splitlines()]
            self.assertEqual(len(accepted),25)
            self.assertEqual(sum(r['step']=='sources' for r in accepted),10)
            refs=[r for r in accepted if r['step']=='references']
            self.assertEqual(sum(len(r['data']['raw_references']) for r in refs),9)
            self.assertEqual(sum(len(r['data']['references']) for r in refs),4)
            self.assertTrue(all(r['is_mock'] and r['attempts']==0 for r in accepted))

    def test_resume_rejects_changed_options(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--mock','--owner','trong','--run-id','x','--output-root',tmp]),0)
            with self.assertRaises(SystemExit):
                main(['--resume','--mock','--run-id','x','--output-root',tmp])
