"""Restoration and relocation of verified snapshot archives."""
import json
import os
from pathlib import Path
import re
from typing import Callable

from ..pipeline_contracts import RunReader, SnapshotManifest, StateRelocator
from .archive import SnapshotArchive
from .common import utc_now


def _extract_owners(scope: dict, accepted_results: list[dict]) -> list[str]:
    ranges = scope.get("scope", {}).get("ranges", [])
    owners: list[str] = []
    for r in ranges:
        o = r.get("owner")
        if o and o not in owners:
            owners.append(o)
    if not owners:
        for p in scope.get("scope", {}).get("paper_tasks", []):
            o = p.get("owner")
            if o and o not in owners:
                owners.append(o)
    if not owners:
        for res in accepted_results:
            o = res.get("owner")
            if o and o not in owners:
                owners.append(o)
    if not owners and "owners" in scope:
        for o in scope["owners"]:
            if o and o not in owners:
                owners.append(o)
    return owners


def _extract_provenance(accepted_results: list[dict]) -> tuple[list[str], int]:
    producer_commits: list[str] = []
    unknown_provenance_tasks = 0
    for res in accepted_results:
        prov = res.get("provenance") or {}
        commit = prov.get("producer_commit")
        if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
            if commit not in producer_commits:
                producer_commits.append(commit)
        else:
            unknown_provenance_tasks += 1
    return producer_commits, unknown_provenance_tasks


class SnapshotRestorer:
    def __init__(self, archive: SnapshotArchive, reader: RunReader,
                 relocator: StateRelocator, *,
                 clock: Callable[[], str] = utc_now):
        self.archive = archive
        self.reader = reader
        self.relocator = relocator
        self.clock = clock

    def restore(self, archive: Path, destination: Path) -> SnapshotManifest:
        dest_path = Path(destination)
        manifest = self.archive.unpack(Path(archive), dest_path)

        corpus_root = dest_path / "corpus"

        # 1. Open RunReader before relocation and match manifest against VerifiedRun
        with self.reader.open(corpus_root, manifest["run_id"]) as before_run:
            if before_run.scope.get("run_id") != manifest["run_id"]:
                raise ValueError(
                    f"Run ID mismatch: database has {before_run.scope.get('run_id')!r}, "
                    f"manifest has {manifest['run_id']!r}"
                )
            if bool(before_run.scope.get("is_mock", False)) != bool(manifest["is_mock"]):
                raise ValueError(
                    f"is_mock mismatch: database has {before_run.scope.get('is_mock')}, "
                    f"manifest has {manifest['is_mock']}"
                )
            owners = _extract_owners(before_run.scope, before_run.accepted_results)
            if owners and set(owners) != set(manifest["owners"]):
                raise ValueError(
                    f"Owners mismatch: database has {owners}, manifest has {manifest['owners']}"
                )
            if before_run.config_fingerprint != manifest["config_fingerprint"]:
                raise ValueError(
                    f"Fingerprint mismatch: database has {before_run.config_fingerprint!r}, "
                    f"manifest has {manifest['config_fingerprint']!r}"
                )
            if str(before_run.recorded_output_root) != str(manifest["origin_output_root"]):
                raise ValueError(
                    f"Origin root mismatch: database has {before_run.recorded_output_root!r}, "
                    f"manifest has {manifest['origin_output_root']!r}"
                )
            db_files = {f["path"]: (f["size_bytes"], f["sha256"]) for f in before_run.files}
            manifest_files = {f["path"]: (f["size_bytes"], f["sha256"]) for f in manifest.get("files", [])}
            if db_files != manifest_files:
                raise ValueError("Corpus database file inventory does not match manifest files")

            p_commits, unk_tasks = _extract_provenance(before_run.accepted_results)
            if sorted(p_commits) != sorted(manifest.get("producer_commits", [])):
                raise ValueError(
                    f"Producer commits mismatch: database has {p_commits}, "
                    f"manifest has {manifest.get('producer_commits')}"
                )
            if unk_tasks != manifest.get("unknown_provenance_tasks", 0):
                raise ValueError(
                    f"Unknown provenance task count mismatch: database has {unk_tasks}, "
                    f"manifest has {manifest.get('unknown_provenance_tasks')}"
                )

            saved_scope = before_run.scope
            saved_accepted = before_run.accepted_results
            saved_fingerprint = before_run.config_fingerprint

        # 2. Context is released, avoiding nested locks. Now relocate database.
        self.relocator.relocate(
            corpus_root,
            manifest["run_id"],
            manifest["origin_output_root"],
            snapshot_id=manifest["snapshot_id"],
        )

        # 3. Open RunReader after relocation to verify recorded_output_root and preserved identity/scope/results
        with self.reader.open(corpus_root, manifest["run_id"]) as after_run:
            expected_new_root = str(corpus_root.resolve())
            if str(after_run.recorded_output_root) != expected_new_root:
                raise ValueError(
                    f"Restored run recorded_output_root {after_run.recorded_output_root!r} "
                    f"does not match expected {expected_new_root!r}"
                )
            if after_run.scope.get("run_id") != manifest["run_id"]:
                raise ValueError("Run ID was modified after relocation")
            if bool(after_run.scope.get("is_mock", False)) != bool(manifest["is_mock"]):
                raise ValueError("is_mock was modified after relocation")
            if after_run.config_fingerprint != saved_fingerprint:
                raise ValueError("Config fingerprint was modified after relocation")
            if after_run.scope != saved_scope:
                raise ValueError("Run scope was modified after relocation")
            if after_run.accepted_results != saved_accepted:
                raise ValueError("Accepted results were modified after relocation")

            actual_recorded_root = after_run.recorded_output_root

        # 4. Atomic receipt write
        receipt = {
            "snapshot_id": manifest["snapshot_id"],
            "run_id": manifest["run_id"],
            "old_output_root": manifest["origin_output_root"],
            "new_output_root": actual_recorded_root,
            "restored_at": self.clock(),
        }
        receipt_path = dest_path / "restore_receipt.json"
        temp_receipt = dest_path / f".tmp_receipt_{receipt_path.name}"
        temp_receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        os.replace(temp_receipt, receipt_path)

        return manifest
