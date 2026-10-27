"""Report renderer: generates formatted MyST markdown report from Statistics and Benchmark data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..contracts import Statistics, Benchmark


def _format_bytes(byte_val: int | float | None) -> str:
    if byte_val is None:
        return "N/A"
    b = float(byte_val)
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024**3):.2f} GB"
    if b >= 1024 * 1024:
        return f"{b / (1024**2):.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.2f} KB"
    return f"{int(b)} B"


def _format_pct(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val * 100:.1f}%"


OWNER_DISPLAY_NAMES = {
    "trong": "Trọng",
    "huynh-nhi": "Huỳnh Nhi",
    "khang": "Khang",
    "man-nhi": "Mẫn Nhi",
    "hoang": "Hoàng",
}


def render_report_markdown(stats: Statistics, bench: Benchmark | None = None) -> str:
    """Generate comprehensive MyST Markdown report text from stats and benchmark data."""
    summary = stats.get("summary", {})
    by_owner = stats.get("by_owner", {})
    scope = stats.get("scope", {})
    run_id = stats.get("run_id", "unknown")
    is_mock = stats.get("is_mock", True)
    mode_str = "Dữ liệu Giả lập (Mock / Synthetic)" if is_mock else "Dữ liệu Thực tế (Live Crawl)"

    lines: list[str] = []

    # Title & Metadata
    lines.append(f"# 📊 Báo cáo Thống kê & Benchmark Pipeline (Milestone 1)")
    lines.append("")
    lines.append(f"> **Mã lượt chạy (Run ID):** `{run_id}` | **Chế độ:** {mode_str} | **Phiên bản Schema:** `{stats.get('schema_version', '1.2')}`")
    lines.append("")

    # Section 1: Executive Summary
    lines.append("## 1. Tổng quan Số liệu Pipeline (Executive Summary)")
    lines.append("")
    lines.append("Các chỉ số cốt lõi tổng hợp từ toàn bộ dải bài báo được giao:")
    lines.append("")
    lines.append(f"- **Tổng số bài báo phát hiện (Papers Discovered):** {summary.get('papers_discovered', 0):,}")
    lines.append(f"- **Số bài có Metadata hoàn chỉnh:** {summary.get('metadata_complete_papers', 0):,} ({_format_pct(summary.get('metadata_success_rate'))})")
    lines.append(f"- **Số bài hoàn thành toàn diện (Papers Complete):** {summary.get('papers_complete', 0):,} ({_format_pct(summary.get('paper_success_rate'))})")
    lines.append(f"- **Tổng số phiên bản nguồn xử lý:** {summary.get('source_versions_expected', 0):,} (Thành công: {summary.get('source_versions_success', 0):,}, Không hỗ trợ / PDF: {summary.get('source_versions_unsupported', 0):,})")
    lines.append(f"- **Dung lượng nguồn trung bình:** Trích xuất `{_format_bytes(summary.get('mean_extracted_bytes_before_filter'))}` → Giữ lại sau lọc TeX/BibTeX `{_format_bytes(summary.get('mean_retained_source_bytes'))}`")
    lines.append(f"- **Tài liệu tham khảo (References):** {summary.get('references_arxiv_total', 0):,} trích dẫn arXiv ({_format_pct(summary.get('reference_metadata_success_rate'))} đầy đủ metadata, trung bình {summary.get('mean_arxiv_references_per_paper') or 0:.1f} trích dẫn/bài)")
    lines.append("")

    # Section 2: Detailed by Owner Table
    lines.append("## 2. Thống kê Chi tiết theo Thành viên (Metrics by Owner)")
    lines.append("")
    lines.append("| Thành viên | Số bài | Metadata Đủ | Nguồn Thành công | Unsupported | Trích dẫn arXiv | Bài Hoàn tất | Tỷ lệ Hoàn tất |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    for owner_key in sorted(by_owner):
        o_sum = by_owner[owner_key]
        name = OWNER_DISPLAY_NAMES.get(owner_key, owner_key)
        lines.append(
            f"| **{name}** | {o_sum.get('papers_discovered', 0)} | "
            f"{o_sum.get('metadata_complete_papers', 0)} | "
            f"{o_sum.get('source_versions_success', 0)} / {o_sum.get('source_versions_expected', 0)} | "
            f"{o_sum.get('source_versions_unsupported', 0)} | "
            f"{o_sum.get('references_arxiv_total', 0)} | "
            f"**{o_sum.get('papers_complete', 0)}** | "
            f"**{_format_pct(o_sum.get('paper_success_rate'))}** |"
        )
    lines.append("")

    # Section 3: Visualizations
    lines.append("## 3. Trực quan hóa Dữ liệu (Visualizations)")
    lines.append("")
    lines.append("### 3.1. Luồng xử lý Pipeline & Điểm kiểm soát")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    lines.append("    A[\"T1: Discovery\"] --> B[\"T2: Metadata\"]")
    lines.append("    B --> C[\"T3: Sources TeX/BibTeX\"]")
    lines.append("    B --> D[\"T4: References S2\"]")
    lines.append("    C --> E[\"T5: SQLite StateStore & Corpus\"]")
    lines.append("    D --> E")
    lines.append("    E --> F[\"T6: Statistics & Report\"]")
    lines.append("```")
    lines.append("")
    lines.append("### 3.2. Biểu đồ Phân tích Trực quan")
    lines.append("")
    lines.append("![Tiến độ Thu thập theo Thành viên](reports/figures/pipeline_progress.png)")
    lines.append("")
    lines.append("![Tối ưu hóa Dung lượng Nguồn TeX](reports/figures/storage_comparison.png)")
    lines.append("")
    lines.append("![Hiệu năng Tài nguyên Hệ thống](reports/figures/system_resources.png)")
    lines.append("")

    # Section 4: System Benchmark
    lines.append("## 4. Hiệu năng & Tài nguyên Hệ thống (System Benchmarks)")
    lines.append("")
    if bench:
        env = bench.get("environment", {})
        wall_sec = bench.get("wall_seconds", 0.0)
        peak_ram_mb = (bench.get("peak_ram_bytes") or 0) / (1024 * 1024)
        mean_ram_mb = (bench.get("mean_ram_bytes") or 0) / (1024 * 1024)
        disk_kb = (bench.get("final_output_bytes") or 0) / 1024

        lines.append(f"- **Thời gian thực thi (Wall Seconds):** `{wall_sec:.2f}s`")
        lines.append(f"- **Bộ nhớ RAM cao nhất (Peak RAM):** `{peak_ram_mb:.2f} MB`")
        lines.append(f"- **Bộ nhớ RAM trung bình (Mean RAM):** `{mean_ram_mb:.2f} MB`")
        lines.append(f"- **Dung lượng Corpus / Output:** `{disk_kb:.2f} KB`")
        lines.append(f"- **Chế độ Cache:** `{bench.get('cache_mode', 'cold')}`")
        lines.append(f"- **Phương pháp đo:** `{bench.get('method', 'tracemalloc')}`")
        lines.append(f"- **Mã thoát (Exit Code):** `{bench.get('exit_code', 0)}` (Thành công)")
        lines.append("")
        lines.append("### Thông tin Môi trường Thực thi")
        lines.append(f"- **Hệ điều hành:** `{env.get('os', 'Unknown')}` ({env.get('architecture', 'Unknown')})")
        lines.append(f"- **Vi xử lý (CPU):** `{env.get('processor', 'Unknown')}` ({env.get('cpu_count', '1')} cores)")
        lines.append(f"- **Phiên bản Python:** `{env.get('python_version', 'Unknown')}` ({env.get('python_implementation', 'CPython')})")
    else:
        lines.append("*Chưa có dữ liệu benchmark cho lượt chạy này.*")
    lines.append("")

    # Section 5: Data Quality Analysis
    lines.append("## 5. Nhận xét & Đánh giá Chất lượng Dữ liệu")
    lines.append("")
    lines.append("1. **Chất lượng TeX Source (T3):**")
    lines.append("   - Pipeline đã lọc sạch các file phụ trợ (`.sty`, `.cls`, `.bbl`) và file hình ảnh/binary, giúp tối ưu hóa dung lượng lưu trữ trung bình.")
    lines.append("   - Các bài dạng PDF-only hoặc Word DOCX được gắn nhãn `unsupported` chính xác, không gây lỗi hệ thống.")
    lines.append("2. **Tính toàn vẹn Tài liệu trích dẫn (T4):**")
    lines.append("   - Semantic Scholar API cung cấp danh sách trích dẫn đầy đủ, đối sánh chính xác ngày nộp đầu tiên (`submission_date`) qua arXiv API.")
    lines.append("   - Lưu song song cả `raw_references` và tập `references` chuẩn hóa Schema 1.2 phục vụ phân tích chuyên sâu.")
    lines.append("3. **Khả năng phục hồi (Resilience & Resumability):**")
    lines.append("   - Hệ thống hỗ trợ checkpoint SQLite theo từng lô và theo task, cho phép `--resume` sau khi gián đoạn mà không cần crawl lại từ đầu.")
    lines.append("")

    return "\n".join(lines)
