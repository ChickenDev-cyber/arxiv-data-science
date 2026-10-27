"""Open an assembled collection and verify every member; implements ``CollectionReader``."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path

from ..pipeline_contracts import RunReader, VerifiedCollection
from ..validation.artifacts import safe_path
from ..validation.pipeline_contracts import validate_pipeline_document

COLLECTION_MANIFEST = "collection.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_document(root: Path, relative: str, kind: str) -> dict:
    """Load a JSON document inside root and reject it unless the validator accepts it."""
    try:
        document = json.loads(safe_path(root, relative).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {kind} manifest {relative}") from exc
    problems = validate_pipeline_document(document, kind)
    if problems:
        raise ValueError(f"Invalid {kind} manifest {relative}: {problems[0]}")
    return document


def verify_files(root: Path, files) -> None:
    for f in files:
        path = safe_path(root, f["path"])
        if not path.is_file() or path.stat().st_size != f["size_bytes"] or sha256_file(path) != f["sha256"]:
            raise ValueError("Snapshot file missing or changed: " + f["path"])


def run_owners(run) -> list[str]:
    return sorted(c["owner"] for c in run.scope["scope"]["ranges"])


class CollectionDirectoryReader:
    def __init__(self, runs: RunReader):
        self.runs = runs

    @contextmanager
    def open(self, collection_root: Path):
        root = Path(collection_root)
        manifest = read_document(root, COLLECTION_MANIFEST, "collection")
        with ExitStack() as stack:
            verified = []
            for member in manifest["members"]:
                snapshot = read_document(root, member["manifest_path"], "snapshot")
                if any(snapshot[k] != member[k] for k in ("snapshot_id", "run_id", "owners", "exporter_commit")):
                    raise ValueError("Collection member differs from its snapshot: " + member["snapshot_id"])
                if snapshot["is_mock"] != manifest["is_mock"]:
                    raise ValueError("Collection mixes mock and real data: " + member["snapshot_id"])
                member_root = safe_path(root, member["root"])
                verify_files(member_root, snapshot["files"])
                run = stack.enter_context(self.runs.open(member_root, member["run_id"]))
                if (run.scope["run_id"] != member["run_id"] or run.scope["is_mock"] != manifest["is_mock"]
                        or run.config_fingerprint != snapshot["config_fingerprint"]
                        or run_owners(run) != sorted(member["owners"])):
                    raise ValueError("Snapshot does not match its verified run: " + member["snapshot_id"])
                verified.append(run)
            yield VerifiedCollection(manifest=manifest, runs=verified)
