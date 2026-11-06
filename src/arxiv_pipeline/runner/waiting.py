"""Interruptible request waits inside the measured batch, bounded by its deadline."""
import math
import time


class WaitBudgetExpired(Exception):
    """No further HTTP may be sent in this batch; checkpoint through the runner."""


class BudgetedWait:
    def __init__(self, *, monotonic=time.monotonic, sleep=time.sleep, report=None):
        self.monotonic, self.sleep, self.report = monotonic, sleep, report
        self.deadline = None

    def __call__(self, group, seconds):
        if group not in ('arxiv', 'semantic_scholar'):
            raise ValueError('Unknown request group')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Request wait must be finite and nonnegative')
        if self.deadline is None:
            raise ValueError('Request waiting is not started')
        started = self.monotonic()
        target = started + seconds
        heartbeat = started
        if seconds >= 10 and self.report is not None:
            self.report(f'Đang chờ {group} {seconds:.0f}s trong batch; chưa gửi request mới')
        while True:
            now = self.monotonic()
            if now >= self.deadline:
                raise WaitBudgetExpired()
            if now >= target:
                return
            self.sleep(min(30, target-now, self.deadline-now))
            now = self.monotonic()
            if now-heartbeat >= 60 and self.report is not None:
                self.report(f'Đã chờ {group} {now-started:.0f}/{seconds:.0f}s trong batch')
                heartbeat = now
