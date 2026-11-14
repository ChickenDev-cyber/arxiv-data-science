"""
book/lib/hoang_stats.py
-----------------------
Module do Hoàng phụ trách, phục vụ vẽ biểu đồ và xuất bảng thống kê cho mục 1.6 (Crawling Statistics).
Chỉ import từ book.lib.common, tuân thủ nguyên tắc không import chéo file người khác.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import matplotlib.pyplot as plt
import pandas as pd

from book.lib.common import (
    DEFAULT_DATA_DIR,
    compute_m1_statistics,
    load_accepted_results,
)


def get_stats_data() -> Dict[str, Any]:
    """Tải dữ liệu thống kê từ cache data/hoang/ hoặc tính toán trực tiếp."""
    cached_file = DEFAULT_DATA_DIR / "hoang" / "statistics_summary.json"
    if cached_file.exists():
        with open(cached_file, "r", encoding="utf-8") as f:
            return json.load(f)
    records = load_accepted_results()
    return compute_m1_statistics(records)


def render_m1_summary_table_md() -> str:
    """Sinh bảng Markdown 5 chỉ số bắt buộc theo yêu cầu đề bài Milestone 1."""
    stats = get_stats_data()
    is_mock = stats.get("is_mock", False)
    data_badge = "🔴 Mock Data (Dữ liệu mẫu thử nghiệm)" if is_mock else "🟢 Dữ liệu Crawl Thật"

    mean_before_kb = stats["mean_extracted_bytes_per_paper"] / 1024.0
    mean_after_kb = stats["mean_retained_bytes_per_paper"] / 1024.0
    saved_ratio = (
        (1.0 - stats["mean_retained_bytes_per_paper"] / stats["mean_extracted_bytes_per_paper"]) * 100.0
        if stats["mean_extracted_bytes_per_paper"] > 0
        else 0.0
    )

    md = f"""
> [!NOTE]
> **Trạng thái dữ liệu:** {data_badge}

| STT | Chỉ số bắt buộc theo đề | Giá trị tính toán | Ghi chú & Phương pháp tính |
|:---:|:------------------------|:-----------------:|:---------------------------|
| **1** | **Số bài crawl thành công** | **{stats['papers_complete']} / {stats['total_papers_discovered']} bài** | Đạt **{stats['paper_success_rate']:.1f}%** (thành công cả 4 công đoạn: Discovery, Metadata, Sources, References) |
| **2** | **Kích thước trung bình mỗi bài (Trước lọc)** | **{mean_before_kb:.1f} KB** ({stats['mean_extracted_bytes_per_paper']:,.0f} bytes) | Tổng dung lượng nguồn giải nén của tất cả phiên bản trong mỗi bài |
| | **Kích thước trung bình mỗi bài (Sau lọc)** | **{mean_after_kb:.1f} KB** ({stats['mean_retained_bytes_per_paper']:,.0f} bytes) | Giảm **{saved_ratio:.1f}%** sau khi loại bỏ hình ảnh, chỉ giữ lại file TeX & BibTeX |
| **3** | **Số reference trung bình mỗi bài** | **{stats['mean_arxiv_references_per_paper']:.1f} refs/bài** (có arXiv ID) | Trung bình thô từ Semantic Scholar: **{stats['mean_s2_references_per_paper']:.1f} refs/bài** |
| **4** | **Tỷ lệ thành công crawl Reference Metadata** | **{stats['reference_metadata_success_rate']:.1f}%** | Tỷ lệ trích xuất thành công ngày nộp từ arXiv API trên tổng số arXiv references ({stats['total_arxiv_references']} refs) |
"""
    return md.strip()


def generate_size_comparison_chart(output_path: str | Path) -> str:
    """Sinh biểu đồ cột so sánh kích thước trước và sau khi lọc hình ảnh."""
    stats = get_stats_data()
    papers = stats.get("papers_list", [])
    if not papers:
        return ""

    df = pd.DataFrame(papers)
    df["before_kb"] = df["extracted_bytes"] / 1024.0
    df["retained_kb"] = df["retained_bytes"] / 1024.0

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    x = range(len(df))
    width = 0.38

    ax.bar([i - width/2 for i in x], df["before_kb"], width=width, label="Trước lọc (Full TeX + Figures)", color="#e06666", edgecolor="#333", alpha=0.9)
    ax.bar([i + width/2 for i in x], df["retained_kb"], width=width, label="Sau lọc (Chỉ giữ TeX & BibTeX)", color="#6aa84f", edgecolor="#333", alpha=0.9)

    ax.set_title("So sánh Kích thước Trung bình Mỗi Bài Trước & Sau Lọc Hình Ảnh", fontsize=12, fontweight="bold", pad=12)
    ax.set_xlabel("Mã bài báo (arXiv ID)", fontsize=10)
    ax.set_ylabel("Dung lượng (KB)", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(df["arxiv_id"], rotation=45, ha="right", fontsize=9)
    ax.legend(frameon=True)
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    plt.tight_layout()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return str(output_path)
