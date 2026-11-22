"""Opt-in arXiv 429 backoff; the baseline pipeline scheduler stays unchanged."""
from arxiv_pipeline.http import RequestScheduler, SCHEDULER_NAMESPACE


class ExperimentRequestScheduler(RequestScheduler):
    def __init__(self, **kwargs):
        self._arxiv_429 = 0
        super().__init__(**kwargs)

    def _snapshot(self, group):
        payload = super()._snapshot(group)
        if group == 'arxiv':
            payload['consecutive_429'] = self._arxiv_429
        return payload

    def _restore(self):
        super()._restore()
        record = self.checkpoints.load(SCHEDULER_NAMESPACE, self._key('arxiv'))
        if record is not None:
            streak = record['payload'].get('consecutive_429', 0)
            if type(streak) is not int or not 0 <= streak <= 1000:
                raise ValueError('Invalid arXiv 429 streak in experiment checkpoint')
            self._arxiv_429 = streak

    def rate_limited(self, group, retry_after):
        if group != 'arxiv':
            return super().rate_limited(group, retry_after)
        with self._locks[group]:
            self._arxiv_429 = min(self._arxiv_429 + 1, 1000)
            fallback = min(300., 60. * 2 ** min(self._arxiv_429 - 1, 3))
            delay = max(fallback, retry_after)
            # defer retains the deadline in memory even if persisting fails.
            self.defer(group, delay)
            return delay

    def request_succeeded(self, group):
        if group != 'arxiv':
            return super().request_succeeded(group)
        with self._locks[group]:
            if self._arxiv_429:
                previous = self._arxiv_429
                self._arxiv_429 = 0
                try:
                    self._persist(group)
                except Exception:
                    self._arxiv_429 = previous
                    raise
