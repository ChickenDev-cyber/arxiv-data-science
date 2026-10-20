"""Generate synthetic interface examples offline, outside the repository by default."""

import argparse
import io
import json
import tarfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ASSIGNED_RANGES
from .contracts import SCHEMA_VERSION, paper_folder


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def result(owner, step, arxiv_id, data, status="success", version=None):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {
        "schema_version": SCHEMA_VERSION, "run_id": "mock-demo", "owner": owner,
        "is_mock": True, "step": step, "arxiv_id": arxiv_id, "version": version,
        "status": status, "data": data, "missing_fields": [], "error": None,
        "provenance": {"source": "synthetic", "date_source": "synthetic"},
        "metrics": {}, "started_at": start.isoformat(),
        "finished_at": (start + timedelta(seconds=1)).isoformat(),
        "duration_seconds": 1.0, "attempts": 0,
    }


def generate(root):
    if root.exists() and any(root.iterdir()):
        raise SystemExit("Output directory must be empty; choose a new path.")
    root.mkdir(parents=True, exist_ok=True)
    events = []
    tex = b"% SYNTHETIC EXAMPLE ONLY\n\\documentclass{article}\n\\begin{document}\nExample sentence.\\cite{demo}\n\\begin{figure}\\includegraphics{figure.png}\\end{figure}\n\\bibliography{refs}\n\\end{document}\n"
    bib = b"@article{demo, title={Synthetic reference}, author={Example Author}, year={2020}}\n"
    image = b"SYNTHETIC BINARY PLACEHOLDER; NOT A REAL PNG"
    for config in ASSIGNED_RANGES:
        owner = config["owner"]
        month = config["start_month"][2:].replace("-", "")
        arxiv_id = f"{month}.{config['start_id']:05d}"
        task = {"owner": owner, "arxiv_id": arxiv_id}
        discovery = result(owner, "discovery", None, {
            "papers": [task], "unresolved_ids": [], "excluded_ids": [], "complete": False,
        }, "partial")
        discovery["provenance"]["scope_limited"] = "true"
        events.append(discovery)
        write_json(root / "examples" / owner / "discovery.result.json", discovery)
        # Dates, version counts, authors and references are deliberately invented.
        date = config["start_month"] + "-01"
        metadata = {
            "arxiv_id": arxiv_id, "title": "SYNTHETIC example paper",
            "authors": ["Example Author"], "submission_date": date,
            "revised_dates": [config["start_month"] + "-02"], "venue": None,
            "versions": [{"version": 1, "submitted_at": date},
                         {"version": 2, "submitted_at": config["start_month"] + "-02"}],
            "versions_complete": True,
        }
        base = Path("raw") / owner / paper_folder(arxiv_id)
        write_json(root / base / "metadata.json", metadata)
        event = result(owner, "metadata", arxiv_id, metadata)
        events.append(event)
        write_json(root / "examples" / owner / "metadata.result.json", event)
        references = {
            "2001.00001": {"arxiv_id": "2001.00001", "title": "SYNTHETIC reference",
                           "authors": ["Example Author"], "submission_date": "2020-01-01",
                           "semantic_scholar_id": "SYNTHETIC-S2-ID"},
        }
        if owner == "hoang":
            references = {}
        write_json(root / base / "references.json", references)
        ref_event = result(owner, "references", arxiv_id, {
            "references": references, "complete": True,
        }, "empty" if not references else "success")
        ref_event["metrics"] = {
            "s2_reference_count": len(references), "arxiv_reference_count": len(references),
            "complete_reference_count": len(references),
        }
        events.append(ref_event)
        write_json(root / "examples" / owner / "references.result.json", ref_event)
        for version in (1, 2):
            folder = root / base / f"v{version}"
            folder.mkdir(parents=True, exist_ok=True)
            staging = root / "staging" / "mock-demo" / owner / paper_folder(arxiv_id) / f"v{version}"
            staging.mkdir(parents=True, exist_ok=True)
            unsupported = owner == "khang" and version == 2
            manifest = []
            if not unsupported:
                for name, content in (("main.tex", tex), ("refs.bib", bib)):
                    (folder / name).write_bytes(content)
                    (staging / name).write_bytes(content)
                    manifest.append({"path": name, "size_bytes": len(content)})
            event = result(owner, "sources", arxiv_id, {
                "source_kind": "pdf_only" if unsupported else "tar_gzip",
                "source_dir": (base / f"v{version}").as_posix(), "files": manifest,
            }, "unsupported" if unsupported else "success", version)
            event["metrics"] = {
                "download_bytes": None,
                "extracted_bytes_before_filter": None if unsupported else len(tex) + len(bib) + len(image),
                "retained_source_bytes": 0 if unsupported else len(tex) + len(bib),
                "removed_figure_bytes": None if unsupported else len(image),
                "removed_other_bytes": None if unsupported else 0,
            }
            write_json(folder / "source_manifest.json", event)
            events.append(event)
        archive_path = root / "inputs" / owner / "source_with_image.tar.gz"
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, content in (("main.tex", tex), ("refs.bib", bib), ("figure.png", image)):
                entry = tarfile.TarInfo(name)
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
        # This measures the locally generated archive, not an HTTP download.
        for event in events:
            if event["owner"] == owner and event["step"] == "sources" and event["status"] == "success":
                event["metrics"]["download_bytes"] = archive_path.stat().st_size
                write_json(root / event["data"]["source_dir"] / "source_manifest.json", event)
    partial = deepcopy(next(e for e in events if e["step"] == "metadata"))
    partial["status"] = "partial"
    partial["data"]["submission_date"] = None
    partial["data"]["versions"][0]["submitted_at"] = None
    partial["data"]["versions_complete"] = False
    partial["missing_fields"] = ["/data/submission_date", "/data/versions/0/submitted_at"]
    failed = result("man-nhi", "references", "2501.12642", None, "failed")
    failed["error"] = {"code": "rate_limited", "message": "Synthetic HTTP 429",
                       "retryable": True, "http_status": 429}
    not_found = result("trong", "metadata", "2504.17523", None, "not_found")
    not_found["error"] = {"code": "not_found", "message": "Synthetic verified absence",
                          "retryable": False, "http_status": 404}
    write_json(root / "examples" / "edge_cases.json", [partial, failed, not_found])
    write_json(root / "range_configs.json", ASSIGNED_RANGES)
    logs = root / "logs" / "mock-demo" / "events.jsonl"
    logs.parent.mkdir(parents=True, exist_ok=True)
    logs.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    tasks = [task for e in events if e["step"] == "discovery" for task in e["data"]["papers"]]
    scope = {
        "ranges": ASSIGNED_RANGES, "paper_tasks": tasks, "discovery_complete": False,
        "versions_complete": True, "owner_count": len({t["owner"] for t in tasks}),
        "paper_count": len(tasks),
    }
    write_json(root / "runs/mock-demo/scope.json", {
        "schema_version": SCHEMA_VERSION, "run_id": "mock-demo", "is_mock": True, "scope": scope,
    })
    (root / "runs/mock-demo/accepted_results.jsonl").write_text(logs.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "README.txt").write_text(
        "All records, versions, dates and source contents are synthetic. No network was used.\n"
        "Never include this directory in real corpus statistics or submissions.\n"
        "edge_cases.json contains separate scenarios, not extra events for the demo run.\n"
        "Staging and raw folders simulate both module output and an accepted corpus.\n"
        "runs/mock-demo is a synthetic handoff for statistics; no runner was executed.\n",
        encoding="utf-8",
    )
    return events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/arxiv-m1-examples"))
    args = parser.parse_args()
    events = generate(args.output_dir)
    print(f"Generated {len(events)} synthetic events in {args.output_dir}")


if __name__ == "__main__":
    main()
