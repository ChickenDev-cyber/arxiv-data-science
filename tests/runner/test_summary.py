from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.examples import result
from .helpers import PAPER, metadata_result, references_result, source_result


class PaperSummaryTests(unittest.TestCase):
    def setUp(self):
        try:
            from arxiv_pipeline.runner.summary import paper_outcome
        except ModuleNotFoundError:
            self.fail('Chưa có phần phân loại tổng kết từng bài')
        self.classify = paper_outcome
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        source = source_result(Path(self.tmp.name))
        second = deepcopy(source)
        second['version'] = 2
        second['data']['source_dir'] = 'raw/trong/2504-17523/v2'
        self.rows = [metadata_result(), source, second, references_result()]

    def test_complete_requires_all_versions_and_accepts_empty_references(self):
        self.assertEqual(self.classify(PAPER, self.rows), 'success')

    def test_missing_version_is_partial(self):
        self.assertEqual(self.classify(PAPER, [self.rows[0], self.rows[1], self.rows[3]]), 'partial')

    def test_unsupported_source_is_partial(self):
        source = self.rows[2]
        source['status'] = 'unsupported'
        source['data'].update(source_kind='pdf_only', files=[])
        source['metrics'].update(extracted_bytes_before_filter=None, retained_source_bytes=0,
                                 removed_figure_bytes=None, removed_other_bytes=None)
        self.assertEqual(self.classify(PAPER, self.rows), 'partial')

    def test_metadata_with_incomplete_history_is_partial(self):
        self.rows[0] = metadata_result(missing_v2_date=True)
        self.assertEqual(self.classify(PAPER, self.rows), 'partial')

    def test_failure_requires_no_accepted_paper_result(self):
        failure = result('trong', 'metadata', PAPER['arxiv_id'], None, 'failed')
        failure['error'] = {'code':'network_error','message':'synthetic timeout','retryable':True,'http_status':None}
        self.assertEqual(self.classify(PAPER, [failure]), 'failed')
        self.assertEqual(self.classify(PAPER, [failure, references_result()]), 'partial')

    def test_no_finished_results_is_pending(self):
        self.assertEqual(self.classify(PAPER, []), 'pending')

    def test_pipeline_counts_three_categories_and_updates_after_resume(self):
        from unittest.mock import patch
        from arxiv_pipeline.discovery.api import RemoteError
        from arxiv_pipeline.runner.mock import make_mock_modules
        from arxiv_pipeline.runner.models import RunOptions
        from arxiv_pipeline.runner.service import run_pipeline
        options = RunOptions('counts', Path(self.tmp.name)/'corpus', ('trong','hoang','man-nhi'), True)
        modules = make_mock_modules(options)
        original_record = modules.metadata.client.record
        original_page = modules.references.reader.references_page
        original_download = modules.sources.client.download
        phase = [0]

        def record(aid):
            if aid == '2501.12642' and phase[0] == 0:
                raise RemoteError('network_error','synthetic timeout',True)
            return original_record(aid)

        def page(aid,offset,limit):
            if aid == '2501.12642' and phase[0] == 0:
                raise RemoteError('rate_limited','synthetic busy',True,429)
            return original_page(aid,offset,limit)

        def download(aid,version,path):
            if aid == '2504.17523' and version == 2:
                path.parent.mkdir(parents=True,exist_ok=True)
                body = b'%PDF-1.7\nsynthetic PDF';path.write_bytes(body)
                return len(body)
            return original_download(aid,version,path)

        with patch.object(modules.metadata.client,'record',side_effect=record), \
             patch.object(modules.references.reader,'references_page',side_effect=page), \
             patch.object(modules.sources.client,'download',side_effect=download):
            messages = []
            first = run_pipeline(options,modules,progress=messages.append)
            self.assertTrue(first.validation['valid'])
            self.assertIn('Tổng kết Mẫn Nhi | 1 bài | thành công: 0 | thiếu: 0 | thất bại: 1',messages)
            self.assertIn('Tổng kết Trọng | 1 bài | thành công: 0 | thiếu: 1 | thất bại: 0',messages)
            self.assertIn('Tổng kết toàn bộ mẫu | 3 bài | thành công: 1 | thiếu: 1 | thất bại: 1',messages)
            phase[0] = 1;messages.clear()
            second = run_pipeline(options,modules,progress=messages.append)
            self.assertTrue(second.validation['valid'])
            self.assertIn('Tổng kết Mẫn Nhi | 1 bài | thành công: 1 | thiếu: 0 | thất bại: 0',messages)
            self.assertIn('Tổng kết toàn bộ mẫu | 3 bài | thành công: 2 | thiếu: 1 | thất bại: 0',messages)
