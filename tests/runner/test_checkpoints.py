from copy import deepcopy
from contextlib import closing
from importlib import import_module, util
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.runner.models import RunOptions
from arxiv_pipeline.runner.state import StateStore, load_options
from .helpers import date_checkpoint, make_v1_corpus


class CheckpointTests(unittest.TestCase):
    def adapters(self):
        self.assertIsNotNone(util.find_spec("arxiv_pipeline.runner.checkpoints"),
                             "Checkpoint storage adapters are not implemented")
        return import_module("arxiv_pipeline.runner.checkpoints")

    def test_v1_migration_preserves_results_and_hashes(self):
        self.adapters()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = make_v1_corpus(root)
            before_hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (root / "raw").rglob("*") if p.is_file()}
            with closing(sqlite3.connect(root / "state.sqlite")) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='run_checkpoints'").fetchone())
                before_results = connection.execute("SELECT result_json FROM attempts ORDER BY attempt_id").fetchall()
            with StateStore(root / "state.sqlite", options.run_id) as store:
                store.open_run(options)
                self.assertEqual(store.connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual([tuple(r) for r in store.connection.execute(
                    "SELECT result_json FROM attempts ORDER BY attempt_id")], before_results)
                self.assertEqual(len(list(store.iter_results(accepted_only=True))), 5)
            self.assertEqual(load_options(root / "state.sqlite", "legacy"), options)
            self.assertEqual({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (root / "raw").rglob("*") if p.is_file()}, before_hashes)

    def test_migration_failure_rolls_back(self):
        self.adapters()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_v1_corpus(root)
            real_connect = sqlite3.connect

            def restricted_connect(*args, **kwargs):
                connection = real_connect(*args, **kwargs)
                connection.set_authorizer(lambda action, arg1, arg2, *_:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA
                    and arg1 == "user_version" and arg2 == "2" else sqlite3.SQLITE_OK)
                return connection

            with patch("arxiv_pipeline.runner.state.sqlite3.connect", side_effect=restricted_connect):
                with self.assertRaises(sqlite3.DatabaseError):
                    StateStore(root / "state.sqlite", "legacy")
            with closing(real_connect(root / "state.sqlite")) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='run_checkpoints'").fetchone())

    def test_checkpoint_roundtrip_and_delete(self):
        adapters = self.adapters()
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("records", Path(tmp), ("trong",), True)
            with StateStore(Path(tmp) / "state.sqlite", options.run_id) as store:
                store.open_run(options)
                sql = adapters.SQLiteCheckpointStore(store)
                self.exercise_store(sql)
            with StateStore(Path(tmp) / "state.sqlite", options.run_id) as store:
                self.assertEqual(adapters.SQLiteCheckpointStore(store).load(
                    "arxiv_dates", '["2504.17523"]'), date_checkpoint())
        self.exercise_store(adapters.InMemoryCheckpointStore())

    def exercise_store(self, store):
        record = date_checkpoint()
        store.save(record)
        record["payload"]["submission_date"] = "1900-01-01"
        loaded = store.load("arxiv_dates", '["2504.17523"]')
        self.assertEqual(loaded["payload"]["submission_date"], "2025-04-24")
        loaded["payload"]["submission_date"] = "1800-01-01"
        self.assertEqual(store.load("arxiv_dates", '["2504.17523"]'), date_checkpoint())
        store.delete("arxiv_dates", '["2504.17523"]')
        self.assertIsNone(store.load("arxiv_dates", '["2504.17523"]'))
        store.save(date_checkpoint())

    def test_checkpoints_do_not_leak_between_runs(self):
        adapters = self.adapters()
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("primary", Path(tmp), ("trong",), True)
            with StateStore(Path(tmp) / "state.sqlite", "primary") as store:
                store.open_run(options)
                # A legacy database can contain another run; exercise query scoping.
                store.connection.execute("INSERT INTO runs SELECT 'foreign',schema_version,fingerprint,"
                                         "config_json,is_mock,scope_json,status,created_at,updated_at FROM runs")
                foreign = date_checkpoint()
                foreign["payload"]["submission_date"] = "1999-01-01"
                store.connection.execute("INSERT INTO run_checkpoints VALUES(?,?,?,?,?,?)", (
                    "foreign", foreign["namespace"], foreign["key"], 1,
                    json.dumps(foreign["payload"]), foreign["updated_at"]))
                adapter = adapters.SQLiteCheckpointStore(store)
                self.assertIsNone(adapter.load("arxiv_dates", foreign["key"]))
                adapter.save(date_checkpoint())
                self.assertEqual(adapter.load("arxiv_dates", foreign["key"]), date_checkpoint())
                adapter.delete("arxiv_dates", foreign["key"])
                self.assertEqual(store.connection.execute(
                    "SELECT COUNT(*) FROM run_checkpoints WHERE run_id='foreign'").fetchone()[0], 1)

    def test_invalid_or_corrupt_records_are_not_silently_missing(self):
        adapters = self.adapters()
        for invalid in (True, 2, "1"):
            record = date_checkpoint()
            record["format_version"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                adapters.InMemoryCheckpointStore().save(record)
        record = date_checkpoint()
        record["payload"]["value"] = float("nan")
        with self.assertRaises(ValueError):
            adapters.InMemoryCheckpointStore().save(record)
        with tempfile.TemporaryDirectory() as tmp:
            options = RunOptions("corrupt", Path(tmp), ("trong",), True)
            with StateStore(Path(tmp) / "state.sqlite", options.run_id) as store:
                store.open_run(options)
                adapter = adapters.SQLiteCheckpointStore(store)
                adapter.save(date_checkpoint())
                store.connection.execute("UPDATE run_checkpoints SET payload_json='invalid json'")
                with self.assertRaises(ValueError):
                    adapter.load("arxiv_dates", '["2504.17523"]')

    def test_optional_payload_validator_runs_at_both_boundaries(self):
        adapters = self.adapters()
        seen = []

        def validator(record):
            seen.append(deepcopy(record))
            return [] if record["payload"]["submission_date"] == "2025-04-24" else ["bad date"]

        adapter = adapters.InMemoryCheckpointStore(validate_record=validator)
        adapter.save(date_checkpoint())
        adapter.load("arxiv_dates", '["2504.17523"]')
        self.assertEqual(len(seen), 2)
        bad = date_checkpoint()
        bad["payload"]["submission_date"] = "invalid"
        with self.assertRaisesRegex(ValueError, "bad date"):
            adapter.save(bad)
        self.assertEqual(adapter.load("arxiv_dates", bad["key"]), date_checkpoint())

    def test_validator_cannot_retain_mutable_storage_record(self):
        adapters = self.adapters()
        retained = []

        def validator(record):
            retained.append(record)
            return []

        adapter = adapters.InMemoryCheckpointStore(validate_record=validator)
        adapter.save(date_checkpoint())
        retained[0]["payload"]["submission_date"] = "wrong"
        self.assertEqual(adapter.load("arxiv_dates", '["2504.17523"]'), date_checkpoint())
