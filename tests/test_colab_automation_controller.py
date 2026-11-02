"""controller_tests.py - Comprehensive tests for AutoCrawlSession controller.
Pure controller unit tests using FakeBackend.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.test_colab_automation import notebook_namespace
AutoCrawlSession = notebook_namespace().get('AutoCrawlSession')

class FakeBackend:
    def __init__(self, *, step_responses=None, measurement_exists_set=None, adopt_data=None):
        self.calls = []
        self.step_responses = list(step_responses or [])
        self.measurement_exists_set = set(measurement_exists_set or [])
        self.adopt_data = adopt_data
        self.completed_batches = set()
        self.fail_backup = False
        self.fail_execute = False
        self.interrupt_on_execute = False
        self.inspect_exception = None
        self.interrupt_on_inspect = 0
        self.interrupt_on_backup = 0
        self.batch_boundaries = []

    def adopt_existing(self):
        self.calls.append(("adopt_existing",))
        return self.adopt_data

    def make_batch(self, mode, measurement_id, *, retry_before_attempt=0):
        self.calls.append(("make_batch", mode, measurement_id))
        return {
            "mode": mode,
            "measurement_id": measurement_id,
            "interrupted": False,
            "retry_before_attempt": retry_before_attempt,
        }

    def execute_batch(self, batch):
        self.calls.append(("execute_batch", batch["measurement_id"]))
        self.batch_boundaries.append(batch.get('retry_before_attempt'))
        if self.fail_execute:
            raise RuntimeError("Execute failure simulated")
        b = dict(batch)
        if self.interrupt_on_execute:
            b["interrupted"] = True
        self.completed_batches.add(b["measurement_id"])
        return b

    def batch_completed(self, batch):
        self.calls.append(("batch_completed", batch["measurement_id"]))
        return batch["measurement_id"] in self.completed_batches

    def inspect_batch(self, batch):
        self.calls.append(("inspect_batch", batch["measurement_id"]))
        if self.interrupt_on_inspect > 0:
            self.interrupt_on_inspect -= 1
            raise KeyboardInterrupt("Simulated Ctrl+C during inspect")
        if self.inspect_exception:
            raise self.inspect_exception
        if self.step_responses:
            return self.step_responses.pop(0)
        return {
            "run_state": "finished",
            "retryable_tasks": 0,
            "progress_key": "default_key",
            "summary": {"records": 500},
            "issues": [],
            "scope_complete": True,
        }

    def backup_batch(self, batch, report, continuation):
        self.calls.append(("backup_batch", batch["measurement_id"], report, continuation))
        if self.interrupt_on_backup > 0:
            self.interrupt_on_backup -= 1
            raise KeyboardInterrupt("Simulated Ctrl+C during backup")
        if self.fail_backup:
            raise RuntimeError("Backup failed: Drive SHA verification failure")

    def measurement_exists(self, mid):
        self.calls.append(("measurement_exists", mid))
        return mid in self.measurement_exists_set


class TestAutoCrawlSession(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="autocrawl_test_")
        self.journal_path = Path(self.test_dir) / "journal.json"
        self.owner = "test-owner"
        self.run_id = "run-001"
        self.pipeline_commit = "af99d11"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_session(self, backend, **kwargs):
        if AutoCrawlSession is None:
            self.fail("AutoCrawlSession could not be imported from controller")
        params = {
            "owner": self.owner,
            "run_id": self.run_id,
            "pipeline_commit": self.pipeline_commit,
            "journal_path": self.journal_path,
        }
        params.update(kwargs)
        return AutoCrawlSession(backend, **params)

    def test_pass_boundary_stays_fixed_through_pauses_and_advances_only_after_traversal(self):
        def report(state, count, failures):
            return {'run_state': state, 'max_attempt_id': count, 'retryable_tasks': failures,
                    'progress_key': ['covered', count], 'scope_complete': True, 'issues': []}
        backend = FakeBackend(step_responses=[report('paused', 5, 1), report('finished', 10, 1),
                                               report('paused', 11, 1), report('finished', 12, 0)])
        result = self._make_session(backend).run()
        self.assertEqual(backend.batch_boundaries, [0, 0, 10, 10])
        self.assertEqual(result['retry_passes'], 1)
        self.assertEqual(result['retry_before_attempt'], 10)
        self.assertEqual(json.loads(self.journal_path.read_text())['retry_before_attempt'], 10)

    def test_backup_retry_preserves_next_pass_boundary_without_reexecuting_measured_batch(self):
        report = {'run_state': 'finished', 'max_attempt_id': 8, 'retryable_tasks': 1,
                  'progress_key': ['coverage', 8], 'scope_complete': True, 'issues': []}
        backend = FakeBackend(step_responses=[report])
        backend.fail_backup = True
        with self.assertRaises(RuntimeError):
            self._make_session(backend).run()
        pending = json.loads(self.journal_path.read_text())['pending']
        self.assertEqual(pending['after']['retry_before_attempt'], 8)
        backend.fail_backup = False
        result = self._make_session(backend).run()
        self.assertEqual(backend.batch_boundaries, [0, 8])
        self.assertEqual(result['retry_passes'], 1)

    def test_snapshot_continuation_restores_active_retry_boundary(self):
        backend = FakeBackend()
        saved = {'owner': self.owner, 'run_id': self.run_id, 'pipeline_commit': self.pipeline_commit,
                 'mode': 'resume', 'retry_passes': 1, 'retry_before_attempt': 12,
                 'next_measurement': 'resume-04'}
        self._make_session(backend, initial_mode='resume', resume_state=saved).run()
        self.assertEqual(backend.batch_boundaries, [12])

    def test_paused_to_backup_to_resume_to_finished(self):
        """Batch 1 paused -> backup -> Batch 2 resume -> finished."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "page-1",
                    "summary": {"crawled": 500},
                    "issues": [],
                    "scope_complete": False,
                },
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "page-2",
                    "summary": {"crawled": 1000},
                    "issues": [],
                    "scope_complete": True,
                },
            ]
        )
        session = self._make_session(backend)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")
        self.assertEqual(result["batches"], 2)

        # Check call sequence:
        # batch 1: make(full, cold-01) -> execute(cold-01) -> inspect(cold-01) -> backup(cold-01)
        # batch 2: make(resume, resume-01) -> execute(resume-01) -> inspect(resume-01) -> backup(resume-01)
        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        backup_calls = [c for c in backend.calls if c[0] == "backup_batch"]
        self.assertEqual(len(exec_calls), 2)
        self.assertEqual(len(backup_calls), 2)
        self.assertEqual(exec_calls[0][1], "cold-01")
        self.assertEqual(exec_calls[1][1], "resume-01")

        # Journal on disk reflects terminal=True, pending=None
        self.assertTrue(self.journal_path.exists())
        with open(self.journal_path) as f:
            j = json.load(f)
        self.assertTrue(j["terminal"])
        self.assertEqual(j["stop_reason"], "finished")
        self.assertIsNone(j["pending"])
        self.assertEqual(j["batches"], 2)

    def test_retry_passes_capped_at_max2(self):
        """2 retry passes = at most 3 finished measured attempts."""
        backend = FakeBackend(
            step_responses=[
                # Pass 0 finishes with retryables -> retry_passes becomes 1
                {
                    "run_state": "finished",
                    "retryable_tasks": 3,
                    "progress_key": "k1",
                    "summary": {"batch": 1},
                    "issues": [],
                    "scope_complete": False,
                },
                # Pass 1 finishes with retryables -> retry_passes becomes 2
                {
                    "run_state": "finished",
                    "retryable_tasks": 2,
                    "progress_key": "k2",
                    "summary": {"batch": 2},
                    "issues": [],
                    "scope_complete": False,
                },
                # Pass 2 finishes with retryables -> retry_passes was 2 (>= max_retry_passes 2) -> exhausted
                {
                    "run_state": "finished",
                    "retryable_tasks": 1,
                    "progress_key": "k3",
                    "summary": {"batch": 3},
                    "issues": [],
                    "scope_complete": False,
                },
            ]
        )
        session = self._make_session(backend, max_retry_passes=2)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "retry_exhausted")
        self.assertEqual(result["batches"], 3)
        self.assertEqual(result["retry_passes"], 2)

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 3)

    def test_permanent_issues_no_retry(self):
        """PDF-only/withdrawn permanent issues must never trigger retries."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "k1",
                    "summary": {"papers": 500},
                    "issues": ["2301.00001: PDF unavailable / withdrawn"],
                    "scope_complete": False,
                }
            ]
        )
        session = self._make_session(backend, max_retry_passes=2)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished_with_issues")
        self.assertEqual(result["batches"], 1)
        self.assertEqual(result["retry_passes"], 0)

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 1)

    def test_backup_failure_prevents_advancing_and_preserves_pending(self):
        """Backup failure stops run and preserves candidate continuation in pending.after."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {"papers": 500},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.fail_backup = True
        session = self._make_session(backend)

        with self.assertRaises(RuntimeError) as ctx:
            session.run()
        self.assertIn("Backup failed", str(ctx.exception))

        # Journal must preserve pending with executed=True and candidate continuation
        with open(self.journal_path) as f:
            j = json.load(f)
        self.assertIsNotNone(j["pending"])
        self.assertTrue(j["pending"]["executed"])
        self.assertIsNotNone(j["pending"]["after"])
        self.assertEqual(j["batches"], 0)  # journal not advanced yet!
        self.assertEqual(j["pending"]["after"]["batches"], 1)

    def test_retry_invocation_reuses_measured_batch_and_advances(self):
        """Retry invocation reuses existing measured batch and backup then advances (not rerun)."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {"papers": 500},
                    "issues": [],
                    "scope_complete": False,
                },
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "p2",
                    "summary": {"papers": 1000},
                    "issues": [],
                    "scope_complete": True,
                },
            ]
        )
        backend.fail_backup = True
        session1 = self._make_session(backend)
        with self.assertRaises(RuntimeError):
            session1.run()

        # Now backend backup recovers
        backend.fail_backup = False
        backend.calls.clear()

        # New session instance on same journal
        session2 = self._make_session(backend)
        result = session2.run()

        # execute_batch must NOT have been called for cold-01 again!
        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        # Exactly 1 new execute call: for the subsequent resume-01 batch!
        self.assertEqual(len(exec_calls), 1)
        self.assertEqual(exec_calls[0][1], "resume-01")

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")
        self.assertEqual(result["batches"], 2)

    def test_interrupted_backs_up_and_stops(self):
        """Interrupted batch backs up and run returns with terminal=False, stop_reason='interrupted'."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "partial-1",
                    "summary": {"papers": 250},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_execute = True
        session = self._make_session(backend)
        result = session.run()

        self.assertFalse(result["terminal"])
        self.assertEqual(result["stop_reason"], "interrupted")
        self.assertEqual(result["batches"], 1)

        # Journal persisted with pending=None and stop_reason='interrupted', terminal=False
        with open(self.journal_path) as f:
            j = json.load(f)
        self.assertFalse(j["terminal"])
        self.assertEqual(j["stop_reason"], "interrupted")
        self.assertIsNone(j["pending"])
        self.assertEqual(j["batches"], 1)

        # A later run can continue
        backend.interrupt_on_execute = False
        backend.step_responses.append(
            {
                "run_state": "finished",
                "retryable_tasks": 0,
                "progress_key": "partial-2",
                "summary": {"papers": 500},
                "issues": [],
                "scope_complete": True,
            }
        )
        session2 = self._make_session(backend)
        res2 = session2.run()
        self.assertTrue(res2["terminal"])
        self.assertEqual(res2["stop_reason"], "finished")
        self.assertEqual(res2["batches"], 2)

    def test_stale_journal_identity_rejected(self):
        """Stale journal identity mismatch must fail on load."""
        with open(self.journal_path, "w") as f:
            json.dump(
                {
                    "owner": "intruder",
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "mode": "full",
                    "next_measurement": "cold-01",
                    "retry_passes": 0,
                    "stall_retries": 0,
                    "previous_progress": None,
                    "batches": 0,
                    "terminal": False,
                    "stop_reason": None,
                    "pending": None,
                },
                f,
            )

        backend = FakeBackend()
        with self.assertRaises(ValueError):
            self._make_session(backend)

    def test_unique_ids_skip_existing(self):
        """Fresh resume-N measurement IDs skip any existing ones."""
        backend = FakeBackend(
            measurement_exists_set={"resume-01", "resume-02"},
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "p2",
                    "summary": {},
                    "issues": [],
                    "scope_complete": True,
                },
            ],
        )
        session = self._make_session(backend)
        session.run()

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 2)
        self.assertEqual(exec_calls[0][1], "cold-01")
        self.assertEqual(exec_calls[1][1], "resume-03")

    def test_noprogress_capped_stalls(self):
        """Stop terminal reason='stalled' only after first no-progress batch plus 2 additional retries."""
        # 1 initial progress + 1 no-progress + 2 retries = 4 batches total
        backend = FakeBackend(
            step_responses=[
                # batch 1: initial progress
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p_same",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                # batch 2: same progress (stall 1)
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p_same",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                # batch 3: same progress (stall 2)
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p_same",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                # batch 4: same progress (stall 3 > max_stall_retries 2) -> stop
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p_same",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
            ]
        )
        session = self._make_session(backend, max_stall_retries=2)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "stalled")
        self.assertEqual(result["batches"], 4)

    def test_restored_terminal_and_counters_preserved(self):
        """Saved terminal state returns without new work; counters preserved."""
        resume_state = {
            "owner": self.owner,
            "run_id": self.run_id,
            "pipeline_commit": self.pipeline_commit,
            "mode": "resume",
            "next_measurement": "resume-05",
            "retry_passes": 1,
            "stall_retries": 0,
            "previous_progress": "prog-5",
            "batches": 5,
            "terminal": True,
            "stop_reason": "finished",
            "pending": None,
        }
        backend = FakeBackend()
        session = self._make_session(backend, resume_state=resume_state)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")
        self.assertEqual(result["batches"], 5)
        self.assertEqual(result["retry_passes"], 1)

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 0)

    def test_recovered_prepared_batch_not_completed_stops_with_error(self):
        """Missing benchmark in recovered prepared batch must stop rather than repeat."""
        with open(self.journal_path, "w") as f:
            json.dump(
                {
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "mode": "full",
                    "next_measurement": "cold-01",
                    "retry_passes": 0,
                    "stall_retries": 0,
                    "previous_progress": None,
                    "batches": 0,
                    "terminal": False,
                    "stop_reason": None,
                    "pending": {
                        "batch": {"mode": "full", "measurement_id": "cold-01"},
                        "executed": False,
                        "after": None,
                    },
                },
                f,
            )

        backend = FakeBackend()
        # batch_completed("cold-01") is False by default
        session = self._make_session(backend)
        with self.assertRaises(RuntimeError) as ctx:
            session.run()

        self.assertIn("cannot be blindly repeated", str(ctx.exception).lower())
        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 0)

    def test_recovered_prepared_batch_completed_continues(self):
        """Recovered prepared batch with batch_completed True proceeds without re-executing."""
        with open(self.journal_path, "w") as f:
            json.dump(
                {
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "mode": "full",
                    "next_measurement": "cold-01",
                    "retry_passes": 0,
                    "stall_retries": 0,
                    "previous_progress": None,
                    "batches": 0,
                    "terminal": False,
                    "stop_reason": None,
                    "pending": {
                        "batch": {"mode": "full", "measurement_id": "cold-01"},
                        "executed": False,
                        "after": None,
                    },
                },
                f,
            )

        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "done",
                    "summary": {},
                    "issues": [],
                    "scope_complete": True,
                }
            ]
        )
        backend.completed_batches.add("cold-01")
        session = self._make_session(backend)
        result = session.run()

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 0)  # NOT executed again!
        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")
        self.assertEqual(result["batches"], 1)

    def test_positive_limits_validated(self):
        """Positive limits must be strictly validated."""
        backend = FakeBackend()
        with self.assertRaises(ValueError):
            self._make_session(backend, max_batches=0)
        with self.assertRaises(ValueError):
            self._make_session(backend, max_batches=-1)
        with self.assertRaises(ValueError):
            self._make_session(backend, max_retry_passes=0)
        with self.assertRaises(ValueError):
            self._make_session(backend, max_retry_passes=-1)
        with self.assertRaises(ValueError):
            self._make_session(backend, max_stall_retries=0)
        with self.assertRaises(ValueError):
            self._make_session(backend, max_stall_retries=-1)

    def test_measurement_id_format_validated(self):
        """Measurement IDs must match cold-N / resume-N."""
        backend = FakeBackend()
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="invalid_id")
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="cold-")
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="resume")

    def test_batch_limit_stops_terminal(self):
        """When next batches >= max_batches, stop terminal reason='batch_limit'."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        session = self._make_session(backend, max_batches=1)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "batch_limit")
        self.assertEqual(result["batches"], 1)

    def test_file_lock_blocks_concurrent_session(self):
        """Nonblocking fcntl lock prevents concurrent session from running on same journal."""
        backend = FakeBackend()
        session1 = self._make_session(backend)
        session2 = self._make_session(backend)

        # Acquire lock in session1 manually to simulate active run
        session1._acquire_lock()
        try:
            with self.assertRaises(RuntimeError) as ctx:
                session2.run()
            self.assertIn("lock", str(ctx.exception).lower())
        finally:
            session1._release_lock()

    def test_adopt_existing_merges_known_fields_and_forbids_identity_changes(self):
        """backend.adopt_existing overrides default fields under lock, forbids identity changes."""
        # Mismatch owner raises under run() lock
        backend_mismatch = FakeBackend(adopt_data={"owner": "other_owner"})
        session_mismatch = self._make_session(backend_mismatch)
        self.assertEqual(len(backend_mismatch.calls), 0)  # constructor does not call backend
        with self.assertRaises(ValueError):
            session_mismatch.run()

        # Valid overrides applied during run()
        backend_valid = FakeBackend(
            adopt_data={
                "mode": "pilot",
                "next_measurement": "cold-02",
                "previous_progress": "prog-adopt",
            },
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "done",
                    "summary": {"crawled": 10},
                    "issues": [],
                    "scope_complete": True,
                }
            ],
        )
        session = self._make_session(backend_valid)
        self.assertEqual(len(backend_valid.calls), 0)  # constructor does not call backend
        result = session.run()
        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")
        # Ensure adopted batch used pilot and cold-02
        exec_calls = [c for c in backend_valid.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 1)
        self.assertEqual(exec_calls[0][1], "cold-02")

    def test_constructor_performs_no_backend_calls_and_no_writes(self):
        """Constructing a session performs no backend calls and does not write to disk."""
        backend = FakeBackend(
            adopt_data={"mode": "pilot", "next_measurement": "cold-02"}
        )
        session = self._make_session(backend)
        self.assertEqual(len(backend.calls), 0)
        self.assertFalse(self.journal_path.exists())

    def test_session_constructed_before_another_finishes_rereads_terminal_journal(self):
        """Session constructed before another finishes re-reads terminal journal under lock with 0 new executes."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "done",
                    "summary": {"indexed": 500},
                    "issues": [],
                    "scope_complete": True,
                }
            ]
        )
        # Construct session 1
        session1 = self._make_session(backend)
        # Construct session 2 BEFORE session 1 runs
        session2 = self._make_session(backend)
        self.assertEqual(len(backend.calls), 0)

        # Run session 1 to completion
        res1 = session1.run()
        self.assertTrue(res1["terminal"])
        self.assertEqual(res1["stop_reason"], "finished")
        self.assertEqual(res1["batches"], 1)

        exec_calls_before = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls_before), 1)

        # Now run session 2: must re-read journal under lock and execute 0 new batches
        res2 = session2.run()
        self.assertTrue(res2["terminal"])
        self.assertEqual(res2["stop_reason"], "finished")
        self.assertEqual(res2["batches"], 1)
        self.assertEqual(res2["summary"], {"indexed": 500})

        exec_calls_after = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls_after), 1)  # No new execute calls!

    def test_measurement_ordinal_must_be_positive(self):
        """Measurement ordinal must be positive; cold-00 and resume-0 are invalid."""
        backend = FakeBackend()
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="cold-00")
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="cold-0")
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="resume-00")
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_measurement="resume-0")

        # cold-01 and resume-1 are valid
        s1 = self._make_session(backend, initial_measurement="cold-01")
        self.assertEqual(s1.next_measurement, "cold-01")
        s2 = self._make_session(backend, initial_measurement="resume-1")
        self.assertEqual(s2.next_measurement, "resume-1")

    def test_validate_mode(self):
        """Mode must be one of full, pilot, resume."""
        backend = FakeBackend()
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_mode="invalid_mode")
        with self.assertRaises(ValueError):
            self._make_session(backend, initial_mode="")

        # Test invalid mode in resume_state
        with self.assertRaises(ValueError):
            self._make_session(
                backend,
                resume_state={
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "mode": "bogus_mode",
                },
            )

    def test_validate_counters_reject_bool_and_negative(self):
        """Nonnegative integer counters must reject boolean and negative values."""
        backend = FakeBackend()
        # Test in resume_state
        for bad_val in (True, False, -1, 1.5, "1"):
            with self.assertRaises(ValueError):
                self._make_session(
                    backend,
                    resume_state={
                        "owner": self.owner,
                        "run_id": self.run_id,
                        "pipeline_commit": self.pipeline_commit,
                        "batches": bad_val,
                    },
                )
            with self.assertRaises(ValueError):
                self._make_session(
                    backend,
                    resume_state={
                        "owner": self.owner,
                        "run_id": self.run_id,
                        "pipeline_commit": self.pipeline_commit,
                        "retry_passes": bad_val,
                    },
                )
            with self.assertRaises(ValueError):
                self._make_session(
                    backend,
                    resume_state={
                        "owner": self.owner,
                        "run_id": self.run_id,
                        "pipeline_commit": self.pipeline_commit,
                        "stall_retries": bad_val,
                    },
                )

    def test_validate_terminal_must_be_bool(self):
        """Terminal flag must strictly be a boolean."""
        backend = FakeBackend()
        for bad_val in (1, 0, "true", "false", None, []):
            with self.assertRaises(ValueError):
                self._make_session(
                    backend,
                    resume_state={
                        "owner": self.owner,
                        "run_id": self.run_id,
                        "pipeline_commit": self.pipeline_commit,
                        "terminal": bad_val,
                    },
                )

    def test_validate_basic_pending_shape(self):
        """Pending state must conform to basic pending shape."""
        backend = FakeBackend()
        # Not a dict
        with self.assertRaises(ValueError):
            self._make_session(
                backend,
                resume_state={
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "pending": "not_a_dict",
                },
            )
        # Missing batch
        with self.assertRaises(ValueError):
            self._make_session(
                backend,
                resume_state={
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "pending": {"executed": False, "after": None},
                },
            )
        # Executed not bool
        with self.assertRaises(ValueError):
            self._make_session(
                backend,
                resume_state={
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "pipeline_commit": self.pipeline_commit,
                    "pending": {
                        "batch": {"mode": "full", "measurement_id": "cold-01"},
                        "executed": "not_bool",
                        "after": None,
                    },
                },
            )

    def test_preserve_terminal_summary_across_journal_reload(self):
        """Terminal summary is persisted in journal and preserved across session reload."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "done",
                    "summary": {"records": 500, "dataset_size_mb": 12.4},
                    "issues": [],
                    "scope_complete": True,
                }
            ]
        )
        session1 = self._make_session(backend)
        res1 = session1.run()
        expected_summary = {"records": 500, "dataset_size_mb": 12.4}
        self.assertEqual(res1["summary"], expected_summary)

        # Check journal JSON on disk explicitly
        with open(self.journal_path, "r", encoding="utf-8") as f:
            j = json.load(f)
        self.assertIn("summary", j)
        self.assertEqual(j["summary"], expected_summary)

        # New session reloads journal
        session2 = self._make_session(backend)
        res2 = session2.run()
        self.assertEqual(res2["summary"], expected_summary)
        self.assertEqual(session2.last_summary, expected_summary)


    def test_resume_state_identity_mismatch_fails(self):
        """Mismatched identity in resume_state must fail."""
        backend = FakeBackend()
        bad_resume = {
            "owner": "wrong_owner",
            "run_id": self.run_id,
            "pipeline_commit": self.pipeline_commit,
        }
        with self.assertRaises(ValueError):
            self._make_session(backend, resume_state=bad_resume)

    def test_pilot_initial_mode_advances_to_resume(self):
        """Initial mode pilot is permitted, subsequent mode becomes resume."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "p2",
                    "summary": {},
                    "issues": [],
                    "scope_complete": True,
                },
            ]
        )
        session = self._make_session(backend, initial_mode="pilot")
        result = session.run()

        make_calls = [c for c in backend.calls if c[0] == "make_batch"]
        self.assertEqual(len(make_calls), 2)
        self.assertEqual(make_calls[0][1], "pilot")
        self.assertEqual(make_calls[1][1], "resume")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")


    def test_one_shot_keyboard_interrupt_from_inspect_backs_up_and_stops(self):
        """One-shot Ctrl+C during inspect: computes state, overlays interrupted, backs up, stops."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {"papers": 100},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_inspect = 1
        session = self._make_session(backend)
        result = session.run()

        self.assertFalse(result["terminal"])
        self.assertEqual(result["stop_reason"], "interrupted")
        self.assertEqual(result["batches"], 1)

        # Proves: exactly one execution, verified backup, journal has no pending
        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        backup_calls = [c for c in backend.calls if c[0] == "backup_batch"]
        self.assertEqual(len(exec_calls), 1)
        self.assertEqual(len(backup_calls), 1)

        with open(self.journal_path, "r", encoding="utf-8") as f:
            j = json.load(f)
        self.assertIsNone(j["pending"])
        self.assertEqual(j["stop_reason"], "interrupted")
        self.assertEqual(j["batches"], 1)

    def test_one_shot_keyboard_interrupt_from_backup_backs_up_and_stops(self):
        """One-shot Ctrl+C during backup preserves precomputed continuation (overlay stop_reason), backs up."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {"papers": 100},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_backup = 1
        session = self._make_session(backend)
        result = session.run()

        self.assertFalse(result["terminal"])
        self.assertEqual(result["stop_reason"], "interrupted")
        self.assertEqual(result["batches"], 1)

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        backup_calls = [c for c in backend.calls if c[0] == "backup_batch"]
        self.assertEqual(len(exec_calls), 1)
        self.assertEqual(len(backup_calls), 2)  # 1 interrupted attempt + 1 successful emergency backup

        with open(self.journal_path, "r", encoding="utf-8") as f:
            j = json.load(f)
        self.assertIsNone(j["pending"])
        self.assertEqual(j["stop_reason"], "interrupted")
        self.assertEqual(j["batches"], 1)

    def test_finished_with_retryable_interrupted_preserves_first_retry_counter(self):
        """finished+retryable interrupted must still count planned first retry, preventing 3rd retry pass."""
        backend = FakeBackend(
            step_responses=[
                # Pass 0: finishes with retryables
                {
                    "run_state": "finished",
                    "retryable_tasks": 2,
                    "progress_key": "k1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                # Pass 1: finishes with retryables
                {
                    "run_state": "finished",
                    "retryable_tasks": 1,
                    "progress_key": "k2",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
                # Pass 2: finishes with retryables
                {
                    "run_state": "finished",
                    "retryable_tasks": 1,
                    "progress_key": "k3",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                },
            ]
        )
        backend.interrupt_on_backup = 1
        session1 = self._make_session(backend, max_retry_passes=2)
        res1 = session1.run()

        # Interrupted stop overlaid, but retry_passes counted!
        self.assertFalse(res1["terminal"])
        self.assertEqual(res1["stop_reason"], "interrupted")
        self.assertEqual(res1["retry_passes"], 1)
        self.assertEqual(res1["batches"], 1)

        # Resume with session 2
        session2 = self._make_session(backend, max_retry_passes=2)
        res2 = session2.run()

        # Should finish pass 1 (retry_passes=2) and pass 2 (retry_passes was 2 -> retry_exhausted)
        self.assertTrue(res2["terminal"])
        self.assertEqual(res2["stop_reason"], "retry_exhausted")
        self.assertEqual(res2["batches"], 3)
        self.assertEqual(res2["retry_passes"], 2)

        exec_calls = [c for c in backend.calls if c[0] == "execute_batch"]
        self.assertEqual(len(exec_calls), 3)  # Exactly 3 finished measured attempts!

    def test_terminal_state_interrupted_preserves_terminal_not_reopening(self):
        """Terminal finished/retry_exhausted/stalled states preserved on Ctrl+C rather than reopening."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "k0",
                    "summary": {"records": 500},
                    "issues": [],
                    "scope_complete": True,
                }
            ]
        )
        backend.interrupt_on_backup = 1
        session = self._make_session(backend)
        result = session.run()

        # Terminal state must NOT be reopened to interrupted/nonterminal
        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")
        self.assertEqual(result["batches"], 1)

        with open(self.journal_path, "r", encoding="utf-8") as f:
            j = json.load(f)
        self.assertTrue(j["terminal"])
        self.assertEqual(j["stop_reason"], "finished")
        self.assertIsNone(j["pending"])

    def test_repeat_interrupt_propagates(self):
        """Repeat interrupt during emergency backup propagates out of run()."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_backup = 2  # Both backup attempts raise KeyboardInterrupt
        session = self._make_session(backend)

        with self.assertRaises(KeyboardInterrupt):
            session.run()

        # Lock was released in finally
        session2 = self._make_session(backend)
        session2._acquire_lock()
        session2._release_lock()

    def test_batch_interrupted_finished_with_retryable_plans_retry_passes(self):
        """When batch[interrupted]=True and inspect is finished with retryables, plan retry_passes=1."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 3,
                    "progress_key": "k1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_execute = True
        session = self._make_session(backend, max_retry_passes=2)
        result = session.run()

        self.assertFalse(result["terminal"])
        self.assertEqual(result["stop_reason"], "interrupted")
        self.assertEqual(result["retry_passes"], 1)

    def test_batch_interrupted_terminal_finished_preserved(self):
        """When batch[interrupted]=True but runner finalized finished, terminal finished is preserved."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 0,
                    "progress_key": "k1",
                    "summary": {"records": 500},
                    "issues": [],
                    "scope_complete": True,
                }
            ]
        )
        backend.interrupt_on_execute = True
        session = self._make_session(backend)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "finished")

    def test_batch_interrupted_terminal_retry_exhausted_preserved(self):
        """When batch[interrupted]=True but retry passes exhausted, terminal retry_exhausted is preserved."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "finished",
                    "retryable_tasks": 1,
                    "progress_key": "k1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_execute = True
        # resume_state where retry_passes is already 2 (max_retry_passes=2)
        resume_state = {
            "owner": self.owner,
            "run_id": self.run_id,
            "pipeline_commit": self.pipeline_commit,
            "mode": "resume",
            "next_measurement": "resume-03",
            "retry_passes": 2,
            "stall_retries": 0,
            "previous_progress": "k0",
            "batches": 2,
            "terminal": False,
            "stop_reason": None,
            "pending": None,
        }
        session = self._make_session(backend, max_retry_passes=2, resume_state=resume_state)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "retry_exhausted")

    def test_batch_interrupted_batch_limit_not_bypassed(self):
        """When batch[interrupted]=True and next batches >= max_batches, stop terminal batch_limit."""
        backend = FakeBackend(
            step_responses=[
                {
                    "run_state": "paused",
                    "retryable_tasks": 0,
                    "progress_key": "p1",
                    "summary": {},
                    "issues": [],
                    "scope_complete": False,
                }
            ]
        )
        backend.interrupt_on_execute = True
        session = self._make_session(backend, max_batches=1)
        result = session.run()

        self.assertTrue(result["terminal"])
        self.assertEqual(result["stop_reason"], "batch_limit")


if __name__ == "__main__":
    unittest.main()
