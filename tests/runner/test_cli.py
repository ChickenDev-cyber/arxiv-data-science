from contextlib import redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arxiv_pipeline.runner.cli import main


class CLITests(unittest.TestCase):
    def test_member_summary_is_printed_after_owner_and_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()) as out,redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--mock','--all-owners','--run-id','owners','--output-root',tmp]),0)
            text=err.getvalue()
            for name in ('Khang','Trọng','Hoàng','Mẫn Nhi','Huỳnh Nhi'):
                self.assertIn(f'Tổng kết {name} | 1 bài | thành công: 1 | thiếu: 0 | thất bại: 0',text)
            self.assertLess(text.index('Tổng kết Hoàng'),text.index('Bài 2/5'))
            self.assertIn('Tổng kết toàn bộ mẫu | 5 bài | thành công: 5 | thiếu: 0 | thất bại: 0',text)
            out.seek(0);out.truncate(0);err.seek(0);err.truncate(0)
            self.assertEqual(main(['--resume','--run-id','owners','--output-root',tmp]),0)
            self.assertEqual(json.loads(out.getvalue())['executed_tasks'],0)
            self.assertIn('Tổng kết Trọng | 1 bài | thành công: 1',err.getvalue())

    def test_progress_is_on_stderr_and_stdout_stays_json(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()) as out,redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--mock','--owner','trong','--run-id','progress','--output-root',tmp]),0)
            self.assertEqual(json.loads(out.getvalue())['papers'],1)
            self.assertEqual(len(out.getvalue().splitlines()),1)
            self.assertIn('Bài 1/1',err.getvalue())
            self.assertIn('trong | sources | 2504.17523v2',err.getvalue())
            self.assertIn('bắt đầu',err.getvalue())
            self.assertIn('success',err.getvalue())
            log=Path(tmp)/'logs/progress/events.jsonl'
            self.assertEqual(len([json.loads(line) for line in log.read_text().splitlines()]),5)

    def test_resume_prints_reused_tasks(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--mock','--owner','trong','--run-id','reuse','--output-root',tmp]),0)
            err.seek(0);err.truncate(0)
            self.assertEqual(main(['--resume','--run-id','reuse','--output-root',tmp]),0)
            self.assertIn('dùng lại',err.getvalue())

    def test_quiet_suppresses_progress(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()) as out,redirect_stderr(io.StringIO()) as err:
            try:
                code=main(['--mock','--owner','trong','--run-id','quiet','--output-root',tmp,'--quiet'])
            except SystemExit as exc:
                self.fail(f'CLI phải hỗ trợ --quiet: {exc}')
            self.assertEqual(code,0)
            self.assertEqual(err.getvalue(),'')
            self.assertEqual(json.loads(out.getvalue())['papers'],1)

    def test_mock_all_owners_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=['--mock','--all-owners','--run-id','mock-cli','--output-root',tmp]
            with patch('urllib.request.urlopen',side_effect=AssertionError('network forbidden')), \
                 patch('arxiv_pipeline.discovery.api.urlopen',side_effect=AssertionError('network forbidden')), \
                 patch('arxiv_pipeline.sources.api.urlopen',side_effect=AssertionError('network forbidden')),redirect_stdout(io.StringIO()) as out,redirect_stderr(io.StringIO()):
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
