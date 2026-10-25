import io
import threading
import unittest

from arxiv_pipeline.runner.progress import TerminalProgress


class TerminalProgressTests(unittest.TestCase):
    def test_heartbeat_is_visible_during_work_and_stops_on_exit(self):
        heartbeat = threading.Event()

        class Stream(io.StringIO):
            def write(self, text):
                result = super().write(text)
                if 'Vẫn đang xử lý' in text:heartbeat.set()
                return result

        stream = Stream()
        try:
            with TerminalProgress(stream=stream, heartbeat_interval=0.01) as progress:
                progress('trong | references | 2504.17526 | bắt đầu')
                self.assertTrue(heartbeat.wait(2), 'Phải in trạng thái trong lúc task chưa trả về')
        except TypeError as exc:
            self.fail(f'Chưa hỗ trợ heartbeat: {exc}')
        self.assertIn('2504.17526', stream.getvalue())
        heartbeat.clear()
        self.assertFalse(heartbeat.wait(0.04), 'Heartbeat phải dừng khi thoát context')

    def test_terminal_write_failure_does_not_abort_work(self):
        class BrokenStream:
            def write(self, text):raise BrokenPipeError('terminal closed')
            def flush(self):pass

        progress = TerminalProgress(stream=BrokenStream())
        try:
            progress('trong | metadata | bắt đầu')
            progress('trong | metadata | success')
        except OSError as exc:
            self.fail(f'Lỗi hiển thị không được làm dừng crawl: {exc}')
