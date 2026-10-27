"""Choose one unpacked snapshot per owner and describe them as a collection."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from ..contracts import SCHEMA_VERSION
from ..pipeline_contracts import CollectionManifest, CollectionMember
from ..validation.artifacts import safe_path
from ..validation.pipeline_contracts import validate_pipeline_document
from ..validation.results import utc
from .reader import COLLECTION_MANIFEST, read_document, verify_files

SNAPSHOT_MANIFEST = "manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Assembly:
    manifest: CollectionManifest
    skipped: list[str]  # snapshot IDs whose owners were already provided by a newer snapshot


class CollectionAssembler:
    def __init__(self, *, clock=utc_now, snapshot_manifest: str = SNAPSHOT_MANIFEST):
        self.clock, self.snapshot_manifest = clock, snapshot_manifest

    def assemble(self, collection_root: Path, collection_id: str, member_roots: list[str]) -> Assembly:
        """``member_roots`` are unpacked snapshot folders relative to collection_root."""
        root = Path(collection_root)
        candidates = []
        for relative in member_roots:
            manifest_path = f"{relative}/{self.snapshot_manifest}"
            snapshot = read_document(root, manifest_path, "snapshot")
            verify_files(safe_path(root, relative), snapshot["files"])
            candidates.append((snapshot, relative, manifest_path))
        if not candidates:
            raise ValueError("A collection needs at least one snapshot")
        ids = [s["snapshot_id"] for s, _, _ in candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("The same snapshot was given twice")
        if len({s["is_mock"] for s, _, _ in candidates}) != 1:
            raise ValueError("Cannot mix mock and real snapshots in one collection")
        members: list[CollectionMember] = []
        skipped, taken = [], set()
        # Newest handoff wins for an owner; a snapshot is kept whole or not at all,
        # so each selected run stays intact.
        for snapshot, relative, manifest_path in sorted(
                candidates, key=lambda c: (utc(c[0]["created_at"]), c[0]["snapshot_id"]), reverse=True):
            if taken & set(snapshot["owners"]):
                skipped.append(snapshot["snapshot_id"])
                continue
            taken |= set(snapshot["owners"])
            members.append({"snapshot_id": snapshot["snapshot_id"], "run_id": snapshot["run_id"],
                            "owners": snapshot["owners"], "exporter_commit": snapshot["exporter_commit"],
                            "root": relative, "manifest_path": manifest_path})
        manifest: CollectionManifest = {
            "format_version": 1, "kind": "arxiv-collection", "collection_id": collection_id,
            "schema_version": SCHEMA_VERSION, "is_mock": candidates[0][0]["is_mock"],
            "created_at": self.clock(), "members": sorted(members, key=lambda m: m["snapshot_id"])}
        problems = validate_pipeline_document(manifest, "collection")
        if problems:
            raise ValueError("Assembled collection is invalid: " + problems[0])
        return Assembly(manifest, sorted(skipped))

    @staticmethod
    def save(collection_root: Path, manifest: CollectionManifest) -> Path:
        """Write collection.json atomically so a reader never sees half a manifest."""
        if validate_pipeline_document(manifest, "collection"):
            raise ValueError("Refusing to save an invalid collection manifest")
        target = safe_path(collection_root, COLLECTION_MANIFEST)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                         delete=False, prefix=".collection-") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(stream.name, target)
        return target
