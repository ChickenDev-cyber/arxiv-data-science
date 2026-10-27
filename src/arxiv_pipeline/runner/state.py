"""Single-writer SQLite checkpoint state and prepared-publication journal."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sqlite3
from .models import RunOptions, Artifact, canonical, config_fingerprint
from .scope import initial_scope
from ..contracts import SCHEMA_VERSION
from ..validation.artifacts import safe_path
from ..pipeline_contracts import CheckpointRecord, RunRecord
from .checkpoints import normalize_checkpoint, validate_checkpoint_key


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class OutputLock:
    def __init__(self, output_root):
        self.root, self.stream = Path(output_root), None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        path = safe_path(self.root, ".runner.lock")
        self.stream = path.open("a+")
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close(); self.stream = None
            raise ValueError("Another runner holds this output root") from None
        return self

    def __exit__(self, *args):
        if self.stream is not None:
            self.stream.close(); self.stream = None


class StateStore:
    def __init__(self, path: Path, run_id: str, *, read_only: bool = False):
        self.path, self.run_id = Path(path), run_id
        if read_only:
            if not self.path.is_file() or self.path.is_symlink():
                raise ValueError("No saved run at output root")
            safe_path(self.path.parent, self.path.name)
            # mode=ro can still create WAL shared-memory files. Inspect the
            # SQLite header before opening it; handoffs use rollback journals.
            with self.path.open("rb") as stream:
                header = stream.read(20)
            if (2 in header[18:20]
                    or any(self.path.with_name(self.path.name + suffix).exists()
                           for suffix in ("-wal", "-shm"))):
                raise ValueError("WAL databases are not supported for read-only handoff inspection; "
                                 "use a stopped, verified rollback-journal checkpoint")
            self.connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro",
                                              uri=True, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            try:
                self.connection.execute("PRAGMA query_only=ON")
                if self.connection.execute("PRAGMA user_version").fetchone()[0] not in (1, 2):
                    raise ValueError("Unsupported state database version")
                self.read_run_record()
            except BaseException:
                self.connection.close()
                raise
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        safe_path(self.path.parent, self.path.name)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("Unsupported state database version")
            if version == 0:
                if self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("Database is not a T5 checkpoint")
                self.connection.executescript('''BEGIN IMMEDIATE;
                    CREATE TABLE runs (run_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                      fingerprint TEXT NOT NULL, config_json TEXT NOT NULL, is_mock INTEGER NOT NULL,
                      scope_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT, updated_at TEXT);
                    CREATE TABLE run_tasks (run_id TEXT NOT NULL REFERENCES runs(run_id), task_key TEXT NOT NULL,
                      input_json TEXT NOT NULL, state TEXT NOT NULL, latest_attempt_id INTEGER REFERENCES attempts(attempt_id),
                      accepted_attempt_id INTEGER REFERENCES attempts(attempt_id), PRIMARY KEY(run_id,task_key));
                    CREATE TABLE attempts (attempt_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, task_key TEXT NOT NULL,
                      result_json TEXT, issues_json TEXT NOT NULL DEFAULT '[]', result_valid INTEGER NOT NULL DEFAULT 0,
                      started_at TEXT, finished_at TEXT, FOREIGN KEY(run_id,task_key) REFERENCES run_tasks(run_id,task_key));
                    CREATE TABLE publications (attempt_id INTEGER PRIMARY KEY REFERENCES attempts(attempt_id),
                      state TEXT NOT NULL, manifest_json TEXT NOT NULL);
                    PRAGMA user_version=1;
                    COMMIT;''')
            if version in (0, 1):
                with self.transaction():
                    self.connection.execute('''CREATE TABLE run_checkpoints (
                        run_id TEXT NOT NULL REFERENCES runs(run_id),
                        namespace TEXT NOT NULL, key TEXT NOT NULL,
                        format_version INTEGER NOT NULL, payload_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(run_id, namespace, key))''')
                    self.connection.execute("PRAGMA user_version=2")
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self): return self

    def __exit__(self, *args): self.connection.close()

    @classmethod
    def readonly(cls, path: Path, run_id: str) -> "StateStore":
        """Open an existing v1/v2 database without creating or migrating it."""
        return cls(path, run_id, read_only=True)

    def read_run_record(self) -> RunRecord:
        row = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (self.run_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown run ID")
        config = json.loads(row["config_json"])
        options = options_from_config(config)
        if (options.run_id != self.run_id or row["schema_version"] != SCHEMA_VERSION
                or int(options.is_mock) != row["is_mock"]
                or config_fingerprint(options) != row["fingerprint"]
                or row["status"] not in ("created", "running", "paused", "finished", "error")):
            raise ValueError("Run identity, schema, configuration or fingerprint is inconsistent")
        return RunRecord(self.run_id, row["schema_version"], row["fingerprint"], config,
                         row["status"], row["created_at"], row["updated_at"])

    def artifact_inventory(self) -> list[Artifact]:
        """Return distinct artifacts attached to accepted task results only."""
        inventory: dict[str, Artifact] = {}
        for task in self.tasks():
            for artifact in self.get_task(task["task_key"])["artifacts"]:
                if artifact.path in inventory and inventory[artifact.path] != artifact:
                    raise ValueError("Conflicting accepted artifact digests")
                inventory[artifact.path] = artifact
        return [inventory[path] for path in sorted(inventory)]

    def relocate_output_root(self, expected_old_root: str, new_output_root: Path) -> None:
        """Update only a restored run's config root, after the caller verifies files."""
        root = Path(new_output_root).resolve()
        if root != self.path.parent.resolve():
            raise ValueError("New output root must contain this state database")
        with self.transaction():
            record = self.read_run_record()
            if record.config["output_root"] != expected_old_root:
                raise ValueError("Saved output root differs from the snapshot origin")
            if record.status not in ("paused", "finished") or self.pending_publications():
                raise ValueError("Relocation requires a stopped, published checkpoint")
            config = {**record.config, "output_root": str(root)}
            if config_fingerprint(options_from_config(config)) != record.fingerprint:
                raise ValueError("Relocation would change processing configuration")
            self.connection.execute("UPDATE runs SET config_json=? WHERE run_id=?",
                                    (canonical(config), self.run_id))

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def open_run(self, options):
        if options.run_id != self.run_id or Path(options.output_root).resolve() != self.path.parent.resolve():
            raise ValueError("Run identity/output root does not match database")
        config, fingerprint = canonical(options.to_dict()), config_fingerprint(options)
        with self.transaction():
            row = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (self.run_id,)).fetchone()
            if row:
                if row["schema_version"] != SCHEMA_VERSION or row["fingerprint"] != fingerprint or row["config_json"] != config:
                    raise ValueError("Resume configuration/schema differs from saved run")
            else:
                if self.connection.execute("SELECT run_id FROM runs").fetchone():
                    raise ValueError("Output root belongs to a different run")
                allowed = {".runner.lock", "state.sqlite", "state.sqlite-journal", "state.sqlite-wal", "state.sqlite-shm"}
                if any(p.name not in allowed for p in self.path.parent.iterdir()):
                    raise ValueError("Refusing to adopt an existing unowned corpus")
                self.connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (
                    self.run_id, SCHEMA_VERSION, fingerprint, config, int(options.is_mock),
                    canonical(initial_scope(options)), "created", now(), now()))

    def ensure_task(self, spec):
        with self.transaction():
            existing = self.connection.execute("SELECT input_json FROM run_tasks WHERE run_id=? AND task_key=?", (self.run_id, spec.key)).fetchone()
            value = canonical(asdict(spec))
            if existing and existing[0] != value:
                raise ValueError("Task identity changed")
            self.connection.execute("INSERT OR IGNORE INTO run_tasks(run_id,task_key,input_json,state) VALUES(?,?,?,'pending')",
                                    (self.run_id, spec.key, value))

    def mark_running(self, key):
        with self.transaction():
            cur = self.connection.execute("INSERT INTO attempts(run_id,task_key,started_at) VALUES(?,?,?)", (self.run_id, key, now()))
            attempt = cur.lastrowid
            self.connection.execute("UPDATE run_tasks SET state='running',latest_attempt_id=? WHERE run_id=? AND task_key=?", (attempt, self.run_id, key))
        return attempt

    def record(self, attempt, result, issues):
        with self.transaction():
            self.connection.execute("UPDATE attempts SET result_json=?,issues_json=?,result_valid=?,finished_at=? WHERE attempt_id=? AND run_id=?",
                                    (canonical(result), canonical(issues), int(not issues), now(), attempt, self.run_id))

    def prepare(self, attempt, manifest):
        with self.transaction():
            self.connection.execute("INSERT INTO publications VALUES(?,'prepared',?)", (attempt, canonical(manifest)))

    def complete(self, attempt, accepted):
        with self.transaction():
            row = self.connection.execute("SELECT * FROM attempts WHERE attempt_id=? AND run_id=?", (attempt, self.run_id)).fetchone()
            if row is None or (accepted and not row["result_valid"]):
                raise ValueError("Cannot accept missing or invalid result")
            if accepted:
                changed = self.connection.execute("UPDATE publications SET state='accepted' WHERE attempt_id=? AND state='prepared'", (attempt,)).rowcount
                if not changed: raise ValueError("Acceptance requires a prepared publication")
                self.connection.execute("UPDATE run_tasks SET accepted_attempt_id=? WHERE run_id=? AND task_key=?", (attempt, self.run_id, row["task_key"]))
            self.connection.execute("UPDATE run_tasks SET state='completed' WHERE run_id=? AND task_key=?", (self.run_id, row["task_key"]))

    def invalidate(self, key):
        with self.transaction():
            self.connection.execute("UPDATE run_tasks SET accepted_attempt_id=NULL,state='pending' WHERE run_id=? AND task_key=?", (self.run_id, key))

    def update_scope(self, scope):
        with self.transaction():
            self.connection.execute("UPDATE runs SET scope_json=?,updated_at=? WHERE run_id=?", (canonical(scope), now(), self.run_id))

    def set_run_status(self, status):
        if status not in ("created", "running", "paused", "finished", "error"):
            raise ValueError("Invalid run lifecycle state")
        with self.transaction():
            self.connection.execute("UPDATE runs SET status=?,updated_at=? WHERE run_id=?", (status, now(), self.run_id))

    def get_task(self, key):
        row = self.connection.execute('''SELECT t.*, a.result_json AS accepted_json,p.manifest_json
            FROM run_tasks t LEFT JOIN attempts a ON a.attempt_id=t.accepted_attempt_id
            LEFT JOIN publications p ON p.attempt_id=t.accepted_attempt_id
            WHERE t.run_id=? AND t.task_key=?''', (self.run_id, key)).fetchone()
        if row is None: return None
        value = dict(row)
        value["accepted_result"] = json.loads(value.pop("accepted_json")) if value["accepted_json"] else None
        manifest = json.loads(value.pop("manifest_json")) if value["manifest_json"] else {}
        value["artifacts"] = [Artifact(**a) for a in manifest.get("artifacts", [])]
        return value

    def pending_publications(self):
        return [dict(row) for row in self.connection.execute('''SELECT p.*,a.task_key,a.result_json FROM publications p
            JOIN attempts a USING(attempt_id) WHERE a.run_id=? AND p.state='prepared' ORDER BY attempt_id''', (self.run_id,))]

    def publication_records(self):
        return [dict(r) for r in self.connection.execute('''SELECT p.*,a.task_key,a.result_json FROM publications p
            JOIN attempts a USING(attempt_id) WHERE a.run_id=? ORDER BY attempt_id''', (self.run_id,))]

    def abort_publication(self, attempt):
        with self.transaction():
            row = self.connection.execute("SELECT task_key FROM attempts WHERE attempt_id=? AND run_id=?", (attempt, self.run_id)).fetchone()
            if row is None: raise ValueError("Unknown attempt")
            self.connection.execute("UPDATE publications SET state='aborted' WHERE attempt_id=?", (attempt,))
            self.connection.execute("UPDATE run_tasks SET state='pending' WHERE run_id=? AND task_key=?", (self.run_id, row[0]))

    def iter_results(self, *, accepted_only):
        query = ('''SELECT a.result_json FROM run_tasks t JOIN attempts a ON a.attempt_id=t.accepted_attempt_id
                    WHERE t.run_id=? AND a.result_valid=1 ORDER BY t.task_key''' if accepted_only else
                 "SELECT result_json FROM attempts WHERE run_id=? AND result_valid=1 ORDER BY attempt_id")
        for row in self.connection.execute(query, (self.run_id,)):
            yield json.loads(row[0])

    def read_scope(self):
        row = self.connection.execute("SELECT scope_json FROM runs WHERE run_id=?", (self.run_id,)).fetchone()
        if not row: raise ValueError("Unknown run")
        return json.loads(row[0])

    def tasks(self):
        return [dict(r) for r in self.connection.execute("SELECT * FROM run_tasks WHERE run_id=? ORDER BY task_key", (self.run_id,))]

    def load_checkpoint(self, namespace: str, key: str) -> CheckpointRecord | None:
        validate_checkpoint_key(namespace, key)
        row = self.connection.execute(
            "SELECT format_version,payload_json,updated_at FROM run_checkpoints "
            "WHERE run_id=? AND namespace=? AND key=?", (self.run_id, namespace, key)).fetchone()
        if row is None:
            return None
        return normalize_checkpoint({
            "format_version": row["format_version"], "namespace": namespace, "key": key,
            "payload": json.loads(row["payload_json"]), "updated_at": row["updated_at"]})

    def save_checkpoint(self, record: CheckpointRecord) -> None:
        record = normalize_checkpoint(record)
        with self.transaction():
            self.connection.execute(
                "INSERT INTO run_checkpoints VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(run_id,namespace,key) DO UPDATE SET "
                "format_version=excluded.format_version,payload_json=excluded.payload_json,"
                "updated_at=excluded.updated_at",
                (self.run_id, record["namespace"], record["key"], record["format_version"],
                 canonical(record["payload"]), record["updated_at"]))

    def delete_checkpoint(self, namespace: str, key: str) -> None:
        validate_checkpoint_key(namespace, key)
        with self.transaction():
            self.connection.execute("DELETE FROM run_checkpoints WHERE run_id=? AND namespace=? AND key=?",
                                    (self.run_id, namespace, key))


def options_from_config(value: dict) -> RunOptions:
    fields = {"run_id", "output_root", "owners", "is_mock", "max_candidates", "max_papers"}
    if (not isinstance(value, dict) or set(value) != fields
            or not isinstance(value["output_root"], str) or not isinstance(value["owners"], list)):
        raise ValueError("Malformed saved run configuration")
    options = RunOptions(**{**value, "output_root": Path(value["output_root"]),
                           "owners": tuple(value["owners"])})
    if options.to_dict() != value:
        raise ValueError("Saved run configuration is not canonical")
    return options


def load_options(path: Path, run_id: str) -> RunOptions:
    with StateStore.readonly(path, run_id) as store:
        return options_from_config(store.read_run_record().config)
