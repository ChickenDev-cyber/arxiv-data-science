"""Optional cooldown recovery around the notebook's existing batch controller.

Data retry budgets and snapshots remain owned by AutoCrawlSession. This layer
only retries a typed preflight rate pause or a fully backed-up rate-limit pause.
"""
import math
import time

from .state import OutputLock


class RateLimitPause(RuntimeError):
    """Safe structured signal; never accepts remote messages, headers or keys."""

    def __init__(self, *, code='rate_limited', http_status=429, wait_seconds=0):
        if (code, http_status) not in (('rate_limited', 429), ('cooldown', None)):
            raise ValueError('Not a recoverable Semantic Scholar rate pause')
        _number(wait_seconds, 'wait_seconds', minimum=0)
        self.code, self.http_status, self.wait_seconds = code, http_status, wait_seconds
        super().__init__('Semantic Scholar rate limit; wait before checking again')


def _number(value, name, *, minimum):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')


def run_continuously(session, *, enabled=False, max_recoveries=48,
                     max_wait_seconds=21600, max_batches_this_call=None,
                     record_event=None, monotonic=time.monotonic, sleep=time.sleep):
    """Continue after 429, within per-call operational limits, with the same journal.

    The wait budget covers actual cooldown waiting, not crawl runtime. Extra
    waits are outside runner benchmarks; optional events record this outer scope.
    The existing controller's _call_start_batches counter keeps a one-batch cap
    valid across recoveries, including restored or adopted cumulative counters.
    """
    if type(enabled) is not bool:
        raise ValueError('enabled must be bool')
    if type(max_recoveries) is not int or max_recoveries < 1:
        raise ValueError('max_recoveries must be a positive integer')
    _number(max_wait_seconds, 'max_wait_seconds', minimum=1)
    if max_batches_this_call is not None and (
            type(max_batches_this_call) is not int or max_batches_this_call < 1):
        raise ValueError('max_batches_this_call must be a positive integer or None')
    if not enabled:
        return session.run(max_batches_this_call=max_batches_this_call)

    # Hold a separate lock while sleeping too; the inner controller keeps its own lock.
    lock = OutputLock(session.journal_path.parent / '.continuous')
    try:
        lock.__enter__()
    except ValueError:
        raise RuntimeError('Another continuous controller is already running') from None
    try:
        return _run(session, max_recoveries, max_wait_seconds, max_batches_this_call,
                    record_event, monotonic, sleep)
    finally:
        lock.__exit__(None, None, None)


def _run(session, max_recoveries, max_wait_seconds, batch_cap, record_event, monotonic, sleep):
    started = monotonic()
    recoveries, waited, remaining = 0, 0.0, batch_cap
    result, failure, audit_failed = None, None, False

    def emit(kind, **fields):
        nonlocal audit_failed
        if record_event is not None:
            try:
                record_event({'event': kind, 'elapsed_seconds': monotonic() - started, **fields})
            except BaseException:
                audit_failed = True
                raise

    def stop(reason):
        return {**session._result(), 'stop_reason': reason, 'terminal': False,
                'recovery': {'recoveries': recoveries, 'wait_seconds': waited}}

    try:
        emit('auto_run_started', max_recoveries=max_recoveries,
             max_wait_seconds=max_wait_seconds, max_batches_this_call=batch_cap)
        while True:
            wait_hint, code, status = 0, 'rate_limited', 429
            try:
                result = session.run(max_batches_this_call=remaining)
            except RateLimitPause as pause:
                # A failure during backup must stay pending, even if mislabelled as 429.
                if session.pending is not None:
                    raise
                code, status, wait_hint = pause.code, pause.http_status, pause.wait_seconds
                result = session._result()
            else:
                if result.get('terminal') or result.get('stop_reason') != 'rate_limited':
                    return result
                if session.pending is not None:
                    raise RuntimeError('Rate-limited batch still has a pending backup')

            if remaining is not None:
                remaining -= session.batches - session._call_start_batches
                if remaining <= 0:
                    return result
            cooldown = getattr(session.backend, 'cooldown_seconds', lambda: 0)()
            _number(cooldown, 'saved cooldown', minimum=0)
            delay = max(min(60 * 2 ** min(recoveries, 4), 900), wait_hint, cooldown)
            # Persist this deadline even if this call cannot afford the wait.
            emit('rate_limit_pause_observed', code=code, http_status=status,
                 requested_wait_seconds=delay, batches=session.batches,
                 next_measurement=session.next_measurement)
            if recoveries >= max_recoveries:
                result = stop('rate_limit_recovery_exhausted')
                return result
            if delay > max_wait_seconds - waited:
                result = stop('rate_limit_wait_budget_exhausted')
                return result

            recoveries += 1
            print(f'[AutoRecovery] S2 {code}: chờ {delay:g}s rồi kiểm tra lại '
                  f'(lượt {recoveries}/{max_recoveries}); dữ liệu đã lưu.', flush=True)
            emit('rate_limit_wait_started', code=code, http_status=status,
                 recovery=recoveries, requested_wait_seconds=delay,
                 batches=session.batches, next_measurement=session.next_measurement)
            wait_start = monotonic()
            interrupted = False
            heartbeat = 0.0
            try:
                while monotonic() - wait_start < delay:
                    sleep(min(30, delay - (monotonic() - wait_start)))
                    elapsed = monotonic() - wait_start
                    if elapsed - heartbeat >= 60:
                        heartbeat = elapsed
                        print(f'[AutoRecovery] Đã chờ {elapsed:.0f}/{delay:g}s; '
                              'chưa gửi request mới.', flush=True)
                        emit('rate_limit_wait_heartbeat', recovery=recoveries,
                             actual_wait_seconds=elapsed)
            except KeyboardInterrupt:
                interrupted = True
            finally:
                actual = monotonic() - wait_start
                waited += actual
                emit('rate_limit_wait_finished', recovery=recoveries,
                     actual_wait_seconds=actual, interrupted=interrupted)
            if interrupted:
                result = stop('interrupted')
                return result
            if waited > max_wait_seconds:
                result = stop('rate_limit_wait_budget_exhausted')
                return result
    except BaseException as exc:
        failure = type(exc).__name__
        raise
    finally:
        if not audit_failed:
            emit('auto_run_finished', stop_reason=result.get('stop_reason') if result else None,
                 error_type=failure, recoveries=recoveries, actual_wait_seconds=waited,
                 wall_seconds=monotonic() - started)
