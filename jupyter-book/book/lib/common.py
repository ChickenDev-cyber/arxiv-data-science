"""
book/lib/common.py
------------------
Thư viện dùng chung do Hoàng phụ trách.
Cung cấp các hàm chuẩn hóa để đọc:
  - scope.json
  - accepted_results.jsonl
  - events.jsonl / *-timing.json
  - benchmarks/*.json
  - statistics/*.json

Hỗ trợ chế độ mock (is_mock=True) khi pipeline chưa hoàn tất crawl thật,
giúp tất cả thành viên trong nhóm build sách và dựng đồ thị ngay lập tức.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def get_mock_accepted_results() -> List[Dict[str, Any]]:
    """Tạo bộ accepted_results mẫu hợp lệ theo schema với is_mock=True."""
    mock_papers = [
        {"arxiv_id": "2301.00001", "owner": "Trong", "v_count": 2, "before_b": 520000, "retained_b": 95000, "s2_cnt": 25, "arxiv_cnt": 18, "status": "success"},
        {"arxiv_id": "2301.00002", "owner": "Khang", "v_count": 1, "before_b": 310000, "retained_b": 62000, "s2_cnt": 14, "arxiv_cnt": 12, "status": "success"},
        {"arxiv_id": "2301.00003", "owner": "HuynhNhi", "v_count": 3, "before_b": 840000, "retained_b": 145000, "s2_cnt": 38, "arxiv_cnt": 30, "status": "success"},
        {"arxiv_id": "2301.00004", "owner": "ManhNhi", "v_count": 1, "before_b": 280000, "retained_b": 54000, "s2_cnt": 10, "arxiv_cnt": 8, "status": "success"},
        {"arxiv_id": "2301.00005", "owner": "Trong", "v_count": 2, "before_b": 610000, "retained_b": 110000, "s2_cnt": 22, "arxiv_cnt": 19, "status": "success"},
        {"arxiv_id": "2301.00006", "owner": "Khang", "v_count": 1, "before_b": 420000, "retained_b": 78000, "s2_cnt": 19, "arxiv_cnt": 15, "status": "success"},
        {"arxiv_id": "2301.00007", "owner": "HuynhNhi", "v_count": 2, "before_b": 490000, "retained_b": 88000, "s2_cnt": 28, "arxiv_cnt": 21, "status": "success"},
        {"arxiv_id": "2301.00008", "owner": "ManhNhi", "v_count": 1, "before_b": 150000, "retained_b": 35000, "s2_cnt": 8, "arxiv_cnt": 6, "status": "success"},
        {"arxiv_id": "2301.00009", "owner": "Trong", "v_count": 1, "before_b": 390000, "retained_b": 72000, "s2_cnt": 16, "arxiv_cnt": 14, "status": "success"},
        {"arxiv_id": "2301.00010", "owner": "Khang", "v_count": 2, "before_b": 730000, "retained_b": 130000, "s2_cnt": 32, "arxiv_cnt": 26, "status": "success"},
    ]
    records: List[Dict[str, Any]] = []
    for p in mock_papers:
        records.append({
            "task_type": "discovery",
            "arxiv_id": p["arxiv_id"],
            "status": "success",
            "owner": p["owner"],
            "is_mock": True,
        })
        records.append({
            "task_type": "metadata",
            "arxiv_id": p["arxiv_id"],
            "status": "success",
            "versions_complete": True,
            "versions": [f"v{i+1}" for i in range(p["v_count"])],
            "owner": p["owner"],
            "is_mock": True,
        })
        records.append({
            "task_type": "sources",
            "arxiv_id": p["arxiv_id"],
            "status": "success",
            "download_bytes": int(p["retained_b"] * 1.2),
            "extracted_bytes_before_filter": p["before_b"],
            "retained_source_bytes": p["retained_b"],
            "removed_figure_bytes": int(p["before_b"] * 0.7),
            "removed_other_bytes": p["before_b"] - p["retained_b"] - int(p["before_b"] * 0.7),
            "owner": p["owner"],
            "is_mock": True,
        })
        records.append({
            "task_type": "references",
            "arxiv_id": p["arxiv_id"],
            "status": "success",
            "complete": True,
            "s2_reference_count": p["s2_cnt"],
            "arxiv_reference_count": p["arxiv_cnt"],
            "complete_reference_count": p["arxiv_cnt"],
            "owner": p["owner"],
            "is_mock": True,
        })
    return records


def get_mock_benchmark() -> Dict[str, Any]:
    """Tạo benchmark mẫu hợp lệ mô phỏng môi trường Colab CPU."""
    return {
        "schema_version": "1.2",
        "is_mock": True,
        "run_id": "colab-cpu-sample-001",
        "environment": {
            "os": "Linux 6.1.85+",
            "os_version": "Ubuntu 22.04 LTS",
            "architecture": "x86_64",
            "processor": "Intel(R) Xeon(R) CPU @ 2.20GHz",
            "cpu_count": "2",
            "python_version": "3.10.12",
            "python_implementation": "CPython",
            "total_ram_bytes": 13612879872,
        },
        "cache_mode": "cold",
        "wall_seconds": 48.72,
        "discovery_seconds": 3.42,
        "mean_paper_seconds": 4.53,
        "min_paper_seconds": 2.85,
        "max_paper_seconds": 7.12,
        "total_paper_seconds": 45.30,
        "peak_ram_bytes": 78643200,      # ~75 MB
        "mean_ram_bytes": 52428800,      # ~50 MB
        "peak_disk_bytes": 24576000,     # ~23.4 MB
        "final_output_bytes": 18432000,  # ~17.5 MB
        "method": "process_tree_rss (psutil)",
        "sampling_interval_seconds": 0.1,
        "exit_code": 0,
        "stage_timings": {
            "discovery": 3.42,
            "metadata": 8.75,
            "sources": 21.40,
            "references": 15.15,
        },
    }


def load_accepted_results(filepath: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """Đọc accepted_results.jsonl, nếu không có hoặc rỗng sẽ fallback về mock data."""
    if filepath and Path(filepath).exists():
        results = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    results.append(json.loads(line))
        if results:
            return results
    return get_mock_accepted_results()


def load_benchmark(filepath: Optional[str | Path] = None) -> Dict[str, Any]:
    """Đọc file benchmark JSON, nếu không có thì trả về mock data."""
    if filepath and Path(filepath).exists():
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return get_mock_benchmark()


def compute_m1_statistics(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Tính toán 5 chỉ số bắt buộc theo yêu cầu Milestone 1.
    Đặc biệt tuân thủ Lưu ý 1 & 2 mục 4:
      - Kích thước tính trung bình THEO MỖI BÀI (cộng dồn các version của bài).
      - Thể hiện cả s2_reference_count và arxiv_reference_count.
    """
    is_mock = any(r.get("is_mock", False) for r in records)

    # Gom nhóm theo paper
    papers: Dict[str, Dict[str, Any]] = {}
    for r in records:
        aid = r.get("arxiv_id")
        if not aid:
            continue
        if aid not in papers:
            papers[aid] = {
                "arxiv_id": aid,
                "owner": r.get("owner", "unknown"),
                "has_discovery": False,
                "has_metadata": False,
                "has_sources": False,
                "has_references": False,
                "extracted_bytes": 0,
                "retained_bytes": 0,
                "s2_ref_count": 0,
                "arxiv_ref_count": 0,
                "complete_ref_count": 0,
            }

        ttype = r.get("task_type")
        if ttype == "discovery" and r.get("status") == "success":
            papers[aid]["has_discovery"] = True
        elif ttype == "metadata" and r.get("status") == "success":
            papers[aid]["has_metadata"] = True
        elif ttype == "sources" and r.get("status") == "success":
            papers[aid]["has_sources"] = True
            papers[aid]["extracted_bytes"] += r.get("extracted_bytes_before_filter", 0)
            papers[aid]["retained_bytes"] += r.get("retained_source_bytes", 0)
        elif ttype == "references" and r.get("status") == "success":
            papers[aid]["has_references"] = True
            papers[aid]["s2_ref_count"] += r.get("s2_reference_count", 0)
            papers[aid]["arxiv_ref_count"] += r.get("arxiv_reference_count", 0)
            papers[aid]["complete_ref_count"] += r.get("complete_reference_count", 0)

    total_papers = len(papers)
    complete_papers = [
        p for p in papers.values()
        if p["has_discovery"] and p["has_metadata"] and p["has_sources"] and p["has_references"]
    ]
    papers_complete_count = len(complete_papers)
    paper_success_rate = (papers_complete_count / total_papers * 100.0) if total_papers > 0 else 0.0

    # Kích thước trung bình mỗi bài (tính trên các bài có nguồn)
    sources_papers = [p for p in papers.values() if p["has_sources"]]
    n_sources = len(sources_papers)
    mean_extracted = (sum(p["extracted_bytes"] for p in sources_papers) / n_sources) if n_sources > 0 else 0.0
    mean_retained = (sum(p["retained_bytes"] for p in sources_papers) / n_sources) if n_sources > 0 else 0.0

    # References trung bình mỗi bài
    ref_papers = [p for p in papers.values() if p["has_references"]]
    n_refs = len(ref_papers)
    mean_s2_refs = (sum(p["s2_ref_count"] for p in ref_papers) / n_refs) if n_refs > 0 else 0.0
    mean_arxiv_refs = (sum(p["arxiv_ref_count"] for p in ref_papers) / n_refs) if n_refs > 0 else 0.0

    total_s2_refs = sum(p["s2_ref_count"] for p in ref_papers)
    total_arxiv_refs = sum(p["arxiv_ref_count"] for p in ref_papers)
    total_complete_refs = sum(p["complete_ref_count"] for p in ref_papers)
    ref_metadata_success_rate = (total_complete_refs / total_arxiv_refs * 100.0) if total_arxiv_refs > 0 else 100.0

    return {
        "is_mock": is_mock,
        "total_papers_discovered": total_papers,
        "papers_complete": papers_complete_count,
        "paper_success_rate": paper_success_rate,
        "mean_extracted_bytes_per_paper": mean_extracted,
        "mean_retained_bytes_per_paper": mean_retained,
        "mean_s2_references_per_paper": mean_s2_refs,
        "mean_arxiv_references_per_paper": mean_arxiv_refs,
        "total_s2_references": total_s2_refs,
        "total_arxiv_references": total_arxiv_refs,
        "reference_metadata_success_rate": ref_metadata_success_rate,
        "papers_list": list(papers.values()),
    }
