"""Shared pacing for actual requests, including retries in different clients."""
from collections import deque
from contextlib import contextmanager
import math
import threading
import time


class RequestScheduler:
    def __init__(self,*,monotonic=time.monotonic,sleep=time.sleep):
        self.monotonic,self.sleep=monotonic,sleep
        self._locks={g:threading.RLock() for g in ("arxiv","semantic_scholar")}
        self._next={g:0. for g in self._locks}
        self._s2=deque()

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


def scheduled_transport(transport,scheduler,group,client):
    """Buffered transports consume and close their response before returning."""
    def send(url,timeout,headers):
        with scheduler.request(group):
            client.attempts+=1
            response=transport(url,timeout,headers)
            if response.status_code in (429,500,502,503,504):
                scheduler.defer(group,client._retry_after(response.headers))
            return response
    return send
