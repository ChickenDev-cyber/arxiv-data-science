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

    def __enter__(self):
        if self.enabled:
            self._thread = threading.Thread(target=self._heartbeat, name='arxiv-progress', daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def __call__(self, message):
        with self._lock:
            self._message = message
            self._since = time.monotonic()
            self._write(message)

    def _write(self, message):
        if self.enabled:
            try:
                print(f'[{time.strftime("%H:%M:%S")}] {message}', file=self.stream, flush=True)
            except (OSError, ValueError):
                self.enabled = False

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                elapsed = time.monotonic() - self._since
                if self.enabled and self._message and elapsed >= self.interval:
                    self._write(f'Vẫn đang xử lý ({elapsed:.0f}s): {self._message}')
