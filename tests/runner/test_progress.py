import io
import threading
import unittest

from arxiv_pipeline.runner.progress import TerminalProgress


class TerminalProgressTests(unittest.TestCase):
    def update(self, progress, *args, **kwargs):
        self.assertTrue(callable(getattr(progress, 'update', None)), 'Progress bar update is missing')
        progress.update(*args, **kwargs)

    def test_redirected_progress_shows_counts_without_terminal_escape_codes(self):
        stream = io.StringIO()
        with TerminalProgress(stream=stream) as progress:
            self.update(progress, 'Sources', 0, 2, unit='bài')
            self.update(progress, 'Sources', 1, 2, unit='bài')
            self.update(progress, 'Sources', 1, 2, unit='bài')
            self.update(progress, 'Sources', 2, 2, unit='bài')
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 3, 'An unchanged count should not add another log line')
        self.assertRegex(lines[0], r'Sources.*0%.*0/2 bài')
        self.assertRegex(lines[1], r'Sources.*50%.*1/2 bài')
        self.assertRegex(lines[2], r'Sources.*100%.*2/2 bài')
        self.assertNotIn('\r', stream.getvalue())
        self.assertNotIn('\x1b', stream.getvalue())

    def test_interactive_bar_is_redrawn_after_logs_and_ends_with_newline(self):
        class Terminal(io.StringIO):
            def isatty(self): return True

        stream = Terminal()
        with TerminalProgress(stream=stream) as progress:
            self.update(progress, 'Sources', 1, 2, unit='bài')
            progress('metadata complete')
            self.assertRegex(stream.getvalue().split('metadata complete')[-1], r'Sources.*1/2 bài')
            self.update(progress, 'Sources', 2, 2, unit='bài')
        self.assertIn('\r', stream.getvalue())
        self.assertIn('\x1b[2K', stream.getvalue())
        self.assertTrue(stream.getvalue().endswith('\n'))

    def test_exit_during_incomplete_stage_does_not_show_one_hundred_percent(self):
        stream = io.StringIO()
        with TerminalProgress(stream=stream) as progress:
            self.update(progress, 'References', 1, 5, unit='bài')
        self.assertRegex(stream.getvalue(), r'References.*20%.*1/5 bài')
        self.assertNotIn('100%', stream.getvalue())

    def test_empty_stage_does_not_claim_processed_papers(self):
        stream = io.StringIO()
        with TerminalProgress(stream=stream) as progress:
            self.update(progress, 'Sources', 0, 0, unit='bài')
        self.assertIn('0/0 bài', stream.getvalue())
        self.assertNotIn('100%', stream.getvalue())

    def test_quiet_progress_does_not_write_bars_or_logs(self):
        stream = io.StringIO()
        with TerminalProgress(stream=stream, enabled=False) as progress:
            self.update(progress, 'Sources', 1, 2, unit='bài')
            progress('message')
        self.assertEqual(stream.getvalue(), '')

    def test_terminal_write_failure_does_not_abort_bar_updates(self):
        class BrokenStream:
            def write(self, text): raise BrokenPipeError('terminal closed')
            def flush(self): pass

        with TerminalProgress(stream=BrokenStream()) as progress:
            self.update(progress, 'Sources', 1, 2, unit='bài')
            self.update(progress, 'Sources', 2, 2, unit='bài')

    def test_unavailable_terminal_probe_falls_back_and_quiet_skips_probing(self):
        class UnavailableTerminal(io.StringIO):
            probes = 0

            def isatty(self):
                self.probes += 1
                raise OSError('terminal unavailable')

        stream = UnavailableTerminal()
        with TerminalProgress(stream=stream) as progress:
            self.update(progress, 'Sources', 1, 2, unit='bài')
        self.assertIn('1/2 bài', stream.getvalue())
        self.assertNotIn('\x1b', stream.getvalue())
        quiet = UnavailableTerminal()
        with TerminalProgress(stream=quiet, enabled=False) as progress:
            self.update(progress, 'Sources', 1, 2, unit='bài')
        self.assertEqual(quiet.probes, 0)
        self.assertEqual(quiet.getvalue(), '')

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
