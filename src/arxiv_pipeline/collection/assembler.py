"""Describe unpacked snapshots, one per owner, as a collection."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from ..contracts import SCHEMA_VERSION
from ..pipeline_contracts import CollectionManifest, CollectionMember, RunReader
from ..validation.artifacts import safe_path
from ..validation.pipeline_contracts import snapshot_paths, validate_pipeline_document
from .reader import COLLECTION_MANIFEST, CollectionDirectoryReader, read_document, verify_files


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class CollectionAssembler:
    def __init__(self, runs: RunReader, *, clock=utc_now):
        self.runs, self.clock = runs, clock

    def assemble(self, collection_root: Path, collection_id: str,
                 snapshot_folders: list[str]) -> CollectionManifest:
        """Verify every snapshot and its run, then write collection.json once.

        Each folder (relative to collection_root) holds manifest.json next to corpus/.
        The runs stay open, and locked, until the manifest is written.
        """
        root = Path(collection_root)
        if safe_path(root, COLLECTION_MANIFEST).exists():
            raise ValueError(f"{root / COLLECTION_MANIFEST} already exists; assemble into a new collection folder")
        manifest = self.describe(root, collection_id, snapshot_folders)
        with CollectionDirectoryReader(self.runs).verify(root, manifest):
            self._save(root, manifest)
        return manifest

    def describe(self, collection_root: Path, collection_id: str,
                 snapshot_folders: list[str]) -> CollectionManifest:
        """The manifest for these snapshots; checks their files but not their runs, writes nothing."""
        root = Path(collection_root)
        members: list[CollectionMember] = []
        modes = set()
        for folder in snapshot_folders:
            corpus, manifest_path = snapshot_paths(Path(folder).as_posix())
            snapshot = read_document(root, manifest_path, "snapshot")
            verify_files(safe_path(root, corpus), snapshot["files"])
            modes.add(snapshot["is_mock"])
            members.append({"snapshot_id": snapshot["snapshot_id"], "run_id": snapshot["run_id"],
                            "owners": snapshot["owners"], "exporter_commit": snapshot["exporter_commit"],
                            "root": corpus, "manifest_path": manifest_path})
        if not members:
            raise ValueError("A collection needs at least one snapshot")
        ids = [m["snapshot_id"] for m in members]
        if len(set(ids)) != len(ids):
            raise ValueError("The same snapshot was given twice")
        # Choosing between two handoffs of one owner is the team's decision, not ours:
        # report it instead of silently dropping a whole snapshot.
        owners = [o for m in members for o in m["owners"]]
        repeated = sorted({o for o in owners if owners.count(o) > 1})
        if repeated:
            raise ValueError("More than one snapshot for owner(s): " + ", ".join(repeated))
        if len(modes) != 1:
            raise ValueError("Cannot mix mock and real snapshots in one collection")
        manifest: CollectionManifest = {
            "format_version": 1, "kind": "arxiv-collection", "collection_id": collection_id,
            "schema_version": SCHEMA_VERSION, "is_mock": modes.pop(),
            "created_at": self.clock(), "members": sorted(members, key=lambda m: m["snapshot_id"])}
        problems = validate_pipeline_document(manifest, "collection")
        if problems:
            raise ValueError("Assembled collection is invalid: " + problems[0])
        return manifest

    @staticmethod
    def _save(collection_root: Path, manifest: CollectionManifest) -> Path:
        """Write collection.json atomically; never replace a collection that already exists."""
        target = safe_path(collection_root, COLLECTION_MANIFEST)
        if target.exists():
            raise ValueError(f"{target} already exists; assemble into a new collection folder")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                         delete=False, prefix=".collection-") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        try:
            # A hard link fails if the target appeared meanwhile, unlike os.replace.
            os.link(stream.name, target)
        except FileExistsError:
            raise ValueError(f"{target} already exists; assemble into a new collection folder") from None
        finally:
            os.unlink(stream.name)
        return target
