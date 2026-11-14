#!/usr/bin/env python3
"""
scripts/prepare_hoang.py
------------------------
Script do Hoàng phụ trách.
Đọc dữ liệu pipeline (scope.json, accepted_results.jsonl, benchmarks/*.json)
hoặc sinh dữ liệu mock nếu chưa có dữ liệu thật.
Ghi file tổng hợp vào data/hoang/ để phục vụ build sách.
"""

import json
import sys
from pathlib import Path

# Đảm bảo in tiếng Việt trên console Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Đảm bảo import được common.py
CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from book.lib.common import (
    load_accepted_results,
    load_benchmark,
    compute_m1_statistics,
    DEFAULT_DATA_DIR,
)
from book.lib.hoang_stats import generate_size_comparison_chart
from book.lib.hoang_benchmark import generate_stage_timing_chart


def main():
    output_dir = DEFAULT_DATA_DIR / "hoang"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("==> [Hoang] Đang xử lý số liệu thống kê và benchmark...")

    # 1. Tính toán thống kê M1
    records = load_accepted_results()
    stats = compute_m1_statistics(records)
    stats_file = output_dir / "statistics_summary.json"
    with open(stats_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f" -> Đã lưu thống kê vào: {stats_file}")

    # 2. Xử lý benchmark M1
    bench = load_benchmark()
    bench_file = output_dir / "benchmark_summary.json"
    with open(bench_file, "w", encoding="utf-8") as f:
        json.dump(bench, f, indent=2, ensure_ascii=False)
    print(f" -> Đã lưu benchmark vào: {bench_file}")

    # 3. Sinh đồ thị trực quan phục vụ báo cáo
    figures_dir = REPO_ROOT / "book" / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    chart1 = figures_dir / "fig-1-6-size-comparison.png"
    generate_size_comparison_chart(chart1)
    print(f" -> Đã tạo biểu đồ thống kê: {chart1}")

    chart2 = figures_dir / "fig-1-8-stage-timing.png"
    generate_stage_timing_chart(chart2)
    print(f" -> Đã tạo biểu đồ benchmark: {chart2}")

    print("==> Hoàn tất chuẩn bị dữ liệu cho Hoàng.")


if __name__ == "__main__":
    main()
