"""Human-readable terminal progress, separate from JSON results and durable logs."""
import sys
import threading
import time


class TerminalProgress:
    def __init__(self, *, enabled=True, stream=None, heartbeat_interval=15.0):
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.interval = heartbeat_interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._message = None
        self._since = time.monotonic()
        self._interactive = False
        if self.enabled:
            try:
                self._interactive = bool(getattr(self.stream, 'isatty', lambda: False)())
            except (OSError, ValueError):
                pass
        self._bar = None
        self._bar_state = None
        self._bar_visible = False

    def __enter__(self):
        if self.enabled:
            self._thread = threading.Thread(target=self._heartbeat, name='arxiv-progress', daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self._lock:
            if self._bar_visible:
                self._emit('\n')
                self._bar_visible = False

    def __call__(self, message):
        with self._lock:
            self._message = message
            self._since = time.monotonic()
            self._write(message)

    def update(self, stage: str, completed: int, total: int, *, unit: str) -> None:
        """Count processed work, including reuse and incomplete outcomes."""
        if not self.enabled:
            return
        if not 0 <= completed <= total:
            raise ValueError('Progress count must be between zero and total')
        state = stage, completed, total, unit
        with self._lock:
            if state == self._bar_state:
                return
            self._bar_state = state
            filled = completed * 20 // total if total else 0
            percentage = f'{completed * 100 // total}%' if total else '--'
            self._bar = (f'{stage} [{"#" * filled}{"-" * (20 - filled)}] '
                         f'{percentage} | {completed}/{total} {unit}')
            self._since = time.monotonic()
            self._draw_bar()

    def _emit(self, text):
        if self.enabled:
            try:
                self.stream.write(text)
                self.stream.flush()
            except (OSError, ValueError):
                self.enabled = False
                self._bar_visible = False

    def _draw_bar(self):
        if self._bar is not None:
            self._emit(('\r\x1b[2K' if self._interactive else '') + self._bar
                       + ('' if self._interactive else '\n'))
            self._bar_visible = self.enabled and self._interactive

    def _write(self, message):
        if self._bar_visible:
            self._emit('\r\x1b[2K')
        self._emit(f'[{time.strftime("%H:%M:%S")}] {message}\n')
        if self._interactive:
            self._draw_bar()

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                elapsed = time.monotonic() - self._since
                if self.enabled and self._message and elapsed >= self.interval:
                    self._write(f'Vẫn đang xử lý ({elapsed:.0f}s): {self._message}')
