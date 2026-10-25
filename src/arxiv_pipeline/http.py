"""Shared pacing for actual requests, including retries in different clients."""
from collections import deque
from contextlib import contextmanager
import math
import threading
import time


def rate_limit_delay(streak,retry_after):
    """Only the fallback is capped; a server cooldown can be arbitrarily longer."""
    return max(30. * (2 ** min(max(streak-1,0),2)),retry_after)


class RequestScheduler:
    def __init__(self,*,monotonic=time.monotonic,sleep=time.sleep):
        self.monotonic,self.sleep=monotonic,sleep
        self._locks={g:threading.RLock() for g in ("arxiv","semantic_scholar")}
        self._next={g:0. for g in self._locks}
        self._s2=deque()
        self._s2_429=0

    @contextmanager
    def request(self,group):
        with self._locks[group]:
            while True:
                now=self.monotonic()
                while self._s2 and self._s2[0]<=now-300:self._s2.popleft()
                at=self._next[group]
                if group=="semantic_scholar" and len(self._s2)>=100:at=max(at,self._s2[0]+300)
                if at<=now:break
                self.sleep(at-now)
            if group=="semantic_scholar":self._s2.append(self.monotonic())
            try:yield
            finally:self._next[group]=max(self._next[group],self.monotonic()+(3 if group=="arxiv" else 1))

    def defer(self,group,seconds):
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0:
            raise ValueError("Cooldown must be finite and nonnegative")
        with self._locks[group]:self._next[group]=max(self._next[group],self.monotonic()+seconds)

    def rate_limited(self,group,retry_after):
        with self._locks[group]:
            if group=='semantic_scholar':
                self._s2_429+=1
                retry_after=rate_limit_delay(self._s2_429,retry_after)
            self.defer(group,retry_after)
            return retry_after

    def request_succeeded(self,group):
        with self._locks[group]:
            if group=='semantic_scholar':self._s2_429=0


def scheduled_transport(transport,scheduler,group,client):
    """Buffered transports consume and close their response before returning."""
    def send(url,timeout,headers):
        with scheduler.request(group):
            client.attempts+=1
            response=transport(url,timeout,headers)
            if response.status_code==429:
                scheduler.rate_limited(group,client._retry_after(response.headers))
            elif response.status_code==200:
                scheduler.request_succeeded(group)
            elif response.status_code in (500,502,503,504):
                scheduler.defer(group,client._retry_after(response.headers))
            return response
    return send
