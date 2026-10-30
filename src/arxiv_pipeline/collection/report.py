"""Pure functions that turn verified runs and collections into JSON reports."""
from ..config import ASSIGNED_RANGES
from ..pipeline_contracts import CollectionManifest, VerifiedCollection, VerifiedRun

ISSUE_FIELDS = ("owner", "arxiv_id", "version", "step", "message")


def missing_owners(manifest: CollectionManifest) -> list[str]:
    """Assigned owners without a snapshot, in the order of ASSIGNED_RANGES."""
    present = {o for m in manifest["members"] for o in m["owners"]}
    return [c["owner"] for c in ASSIGNED_RANGES if c["owner"] not in present]


def issues(run: VerifiedRun, snapshot_id: str | None = None) -> list[dict]:
    extra = {"snapshot_id": snapshot_id} if snapshot_id else {}
    return [{**extra, **{k: i[k] for k in ISSUE_FIELDS}} for i in run.quality["issues"]]


def run_summary(run: VerifiedRun) -> dict:
    return {"run_id": run.scope["run_id"], "run_state": run.run_state, "is_mock": run.scope["is_mock"],
            "owners": sorted(c["owner"] for c in run.scope["scope"]["ranges"]),
            "papers": run.scope["scope"]["paper_count"], "accepted_results": len(run.accepted_results),
            "files": len(run.files), "quality_valid": run.quality["valid"]}


def run_report(run: VerifiedRun) -> tuple[dict, int]:
    """Report and exit code: 0 complete, 2 valid but work is left."""
    return {**run_summary(run), "issues": issues(run)}, 0 if run.quality["valid"] else 2


def collection_report(collection: VerifiedCollection) -> tuple[dict, int]:
    """Report and exit code: 0 complete for all five owners, 2 issues or owners missing."""
    manifest, members, found = collection.manifest, [], []
    for member, run in zip(manifest["members"], collection.runs):
        members.append({"snapshot_id": member["snapshot_id"], **run_summary(run)})
        found += issues(run, member["snapshot_id"])
    missing = missing_owners(manifest)
    report = {"collection_id": manifest["collection_id"], "is_mock": manifest["is_mock"],
              "members": members, "missing_owners": missing, "issues": found}
    return report, 0 if not found and not missing else 2


def assembly_report(manifest: CollectionManifest, path) -> tuple[dict, int]:
    return {"collection_id": manifest["collection_id"], "is_mock": manifest["is_mock"],
            "members": [{"snapshot_id": m["snapshot_id"], "owners": m["owners"]} for m in manifest["members"]],
            "manifest": str(path)}, 0
