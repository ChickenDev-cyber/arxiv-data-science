#!/usr/bin/env python3
"""
scripts/prepare_huynhnhi.py  (chủ file: Huỳnh Nhi)
--------------------------------------------------
Đọc file handoff của pipeline (schema 1.2) và ghi các file tổng hợp NHỎ vào
data/huynhnhi/ cho các mục 1.3, 1.4, 1.7 của sách.

Đầu vào: một hoặc nhiều thư mục run, mỗi thư mục có
    scope.json, accepted_results.jsonl, validation.json, quality.json
(ví dụ  <Drive arXiv-data>/runs/m1-colab-huynh-nhi-01/ ).
Chọn ĐÚNG MỘT run cho mỗi owner (quy tắc mục 5 của kế hoạch).

Đầu ra (data/huynhnhi/):
    metadata_per_paper.csv    mỗi dòng = 1 Result step=metadata
    sources_per_version.csv   mỗi dòng = 1 Result step=sources (1 phiên bản)
    quality_issues.csv        mỗi dòng = 1 issue trong validation.json/quality.json
    papers_sample.csv         vài dòng bảng `papers` của đề (ánh xạ từ Result)
    references_sample.csv     vài dòng bảng `references` của đề
    manifest.json             run đã đọc, số dòng, cờ is_mock

Không ghi corpus thô (.tex, .bib, raw_references) vào repo sách.

Cách chạy:
    python scripts/prepare_huynhnhi.py --run-dir D:/data/runs/m1-colab-huynh-nhi-01
    python scripts/prepare_huynhnhi.py --run-dir RUN_A --run-dir RUN_B   # nhiều owner
    # hoặc đặt biến môi trường HUYNHNHI_RUN_DIRS="RUN_A;RUN_B"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "huynhnhi"
SAMPLE_ROWS = 5  # số dòng mẫu cho bảng papers/references


# ----------------------------------------------------------------- đọc file
def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_jsonl(path: Path) -> Iterable[dict]:
    """Đọc từng dòng để không nạp cả file lớn (raw_references) vào RAM một lúc."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def as_json(value: Any) -> str:
    """Danh sách (authors, revised_dates...) lưu trong CSV dưới dạng chuỗi JSON."""
    return json.dumps(value, ensure_ascii=False) if value is not None else ""


# ------------------------------------------------------------ từng loại Result
def metadata_row(r: dict) -> dict:
    data = r.get("data") or {}
    versions = data.get("versions") or []
    dates = [v.get("submitted_at") for v in versions]
    known = [d for d in dates if d]
    return {
        "run_id": r["run_id"],
        "owner": r["owner"],
        "arxiv_id": r["arxiv_id"],
        "status": r["status"],
        "version_count": len(versions),
        "versions_complete": bool(data.get("versions_complete", False)),
        # bất thường thứ tự ngày: ngày phiên bản sau sớm hơn phiên bản trước
        "date_order_issue": any(b < a for a, b in zip(known, known[1:])),
        "has_venue": bool(data.get("venue")),
        "missing_fields": ";".join(r.get("missing_fields") or []),
        "error_code": (r.get("error") or {}).get("code", ""),
        "is_mock": r["is_mock"],
    }


def sources_row(r: dict) -> dict:
    data = r.get("data") or {}
    m = r.get("metrics") or {}
    files = data.get("files") or []
    before = m.get("extracted_bytes_before_filter")
    parts = [m.get("retained_source_bytes"), m.get("removed_figure_bytes"), m.get("removed_other_bytes")]
    invariant = "" if before is None or None in parts else (before == sum(parts))
    return {
        "run_id": r["run_id"],
        "owner": r["owner"],
        "arxiv_id": r["arxiv_id"],
        "version": r["version"],
        "status": r["status"],
        "source_kind": data.get("source_kind", ""),
        "download_bytes": m.get("download_bytes"),
        "extracted_bytes_before_filter": before,
        "retained_source_bytes": m.get("retained_source_bytes"),
        "removed_figure_bytes": m.get("removed_figure_bytes"),
        "removed_other_bytes": m.get("removed_other_bytes"),
        "invariant_ok": invariant,  # before = retained + removed_figure + removed_other
        "n_files": len(files),
        "n_tex": sum(f["path"].lower().endswith(".tex") for f in files),
        "n_bib": sum(f["path"].lower().endswith(".bib") for f in files),
        "error_code": (r.get("error") or {}).get("code", ""),
        "is_mock": r["is_mock"],
    }


def paper_table_row(r: dict) -> dict:
    """Result(step=metadata) -> 1 dòng bảng `papers` của đề."""
    d = r["data"]
    return {
        "arxiv_id": d["arxiv_id"],
        "title": d.get("title"),
        "authors": as_json(d.get("authors")),
        "submission_date": d.get("submission_date"),
        "revised_dates": as_json(d.get("revised_dates")),
        "venue": d.get("venue"),
    }


def reference_table_rows(r: dict) -> list[dict]:
    """Result(step=references) -> các dòng bảng `references` (chỉ ref có arXiv ID)."""
    rows = []
    for ref_id, ref in (r["data"].get("references") or {}).items():
        rows.append({
            "arxiv_id": r["arxiv_id"],             # bài trích dẫn
            "ref_arxiv_id": ref.get("arxiv_id", ref_id),
            "ref_title": ref.get("title"),
            "ref_authors": as_json(ref.get("authors")),
            "ref_submission_date": ref.get("submission_date"),
            "ref_s2_id": ref.get("semantic_scholar_id"),
        })
    return rows


def issue_rows(run_id: str, kind: str, report: dict) -> list[dict]:
    return [{
        "run_id": run_id,
        "report": kind,  # validation (đúng cấu trúc) | quality (đủ dữ liệu chưa)
        "owner": i.get("owner"),
        "step": i.get("step"),
        "field": i.get("field") or "",
        "arxiv_id": i.get("arxiv_id") or "",
        "version": i.get("version") if i.get("version") is not None else "",
        "message": i.get("message"),
    } for i in report.get("issues", [])]


# ----------------------------------------------------------------------- main
def collect(run_dirs: list[Path]) -> dict:
    meta, src, issues, papers, refs, runs = [], [], [], [], [], []
    for run_dir in run_dirs:
        scope = read_json(run_dir / "scope.json")
        run_id, is_mock = scope["run_id"], scope["is_mock"]
        counts = {"metadata": 0, "sources": 0, "references": 0, "discovery": 0}
        for r in read_jsonl(run_dir / "accepted_results.jsonl"):
            if r.get("schema_version") != "1.2" or "step" not in r:
                raise SystemExit(f"{run_dir}: dòng không theo schema 1.2 (thiếu 'step'); "
                                 "đây có thể là file mẫu cũ dạng 'task_type'.")
            if r["is_mock"] != is_mock:
                raise SystemExit(f"{run_dir}: run trộn mock và thật -> lỗi, không tự lọc.")
            counts[r["step"]] += 1
            if r["step"] == "metadata":
                meta.append(metadata_row(r))
                if r["status"] == "success" and len(papers) < SAMPLE_ROWS:
                    papers.append(paper_table_row(r))
            elif r["step"] == "sources":
                src.append(sources_row(r))
            elif r["step"] == "references" and r.get("data") and len(refs) < SAMPLE_ROWS:
                refs.extend(reference_table_rows(r)[: SAMPLE_ROWS - len(refs)])
        for kind in ("validation", "quality"):
            path = run_dir / f"{kind}.json"
            if path.exists():
                issues.extend(issue_rows(run_id, kind, read_json(path)))
        # Chỉ ghi run_id, KHÔNG ghi đường dẫn máy cá nhân vào repo.
        runs.append({"run_id": run_id, "is_mock": is_mock,
                     "paper_count": scope["scope"]["paper_count"],
                     "discovery_complete": scope["scope"]["discovery_complete"],
                     "accepted_by_step": counts})
    return {"meta": meta, "src": src, "issues": issues, "papers": papers,
            "refs": refs, "runs": runs}


def main() -> int:
    ap = argparse.ArgumentParser(description="Tổng hợp dữ liệu cho mục 1.3, 1.4, 1.7.")
    ap.add_argument("--run-dir", action="append", default=[],
                    help="Thư mục runs/<run_id> (lặp lại cho nhiều owner).")
    args = ap.parse_args()
    dirs = args.run_dir or [p for p in os.environ.get("HUYNHNHI_RUN_DIRS", "").split(";") if p]
    if not dirs:
        print("Chưa chỉ --run-dir; giữ nguyên dữ liệu đang có trong data/huynhnhi/.")
        return 0
    out = collect([Path(d) for d in dirs])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tables = [
        ("metadata_per_paper.csv", out["meta"]),
        ("sources_per_version.csv", out["src"]),
        ("quality_issues.csv", out["issues"]),
        ("papers_sample.csv", out["papers"]),
        ("references_sample.csv", out["refs"]),
    ]
    headers = {
        "quality_issues.csv": ["run_id", "report", "owner", "step", "field", "arxiv_id", "version", "message"],
    }
    for name, rows in tables:
        fields = list(rows[0]) if rows else headers.get(name, ["empty"])
        write_csv(OUT_DIR / name, fields, rows)
        print(f" -> {name}: {len(rows)} dòng")
    manifest = {"is_mock": any(r["is_mock"] for r in out["runs"]), "runs": out["runs"],
                "rows": {name: len(rows) for name, rows in tables}}
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    print(" -> manifest.json; is_mock =", manifest["is_mock"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
