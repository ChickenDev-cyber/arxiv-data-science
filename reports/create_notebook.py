"""Generate report.ipynb notebook for Jupyter Book."""

import json
import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()

cells = [
    nbf.v4.new_markdown_cell(
        "# 📊 Báo cáo Khoa học & Benchmark Pipeline (Milestone 1)\n\n"
        "Notebook này phân tích dữ liệu trực tiếp từ hệ thống pipeline thu thập bài báo arXiv theo chuẩn Schema 1.2."
    ),
    nbf.v4.new_code_cell(
        "import json\n"
        "from pathlib import Path\n"
        "import matplotlib.pyplot as plt\n"
        "import sys\n\n"
        "sys.path.insert(0, 'src')\n"
        "from arxiv_pipeline.statistics.service import StatisticsService\n"
        "from arxiv_pipeline.benchmark.runner import BenchmarkService\n\n"
        "stats_path = Path('statistics/baseline-001.json')\n"
        "bench_path = Path('benchmarks/baseline-001.json')\n\n"
        "with open(stats_path, 'r', encoding='utf-8') as f:\n"
        "    stats = json.load(f)\n"
        "with open(bench_path, 'r', encoding='utf-8') as f:\n"
        "    bench = json.load(f)\n\n"
        "summary = stats['summary']\n"
        "print(f\"Tổng số bài phát hiện: {summary['papers_discovered']}\")\n"
        "print(f\"Số bài hoàn thành toàn diện: {summary['papers_complete']} ({summary['paper_success_rate'] or 0:.1%})\")\n"
        "print(f\"Trích dẫn arXiv: {summary['references_arxiv_total']} (Tỷ lệ đủ metadata: {summary['reference_metadata_success_rate'] or 0:.1%})\")"
    ),
    nbf.v4.new_markdown_cell(
        "## 1. Trực quan hóa Tiến độ Thu thập theo Thành viên"
    ),
    nbf.v4.new_code_cell(
        "by_owner = stats.get('by_owner', {})\n"
        "owners = sorted(by_owner.keys())\n"
        "owner_labels = [o.capitalize() for o in owners]\n\n"
        "fig, ax = plt.subplots(figsize=(8, 4.5), dpi=120)\n"
        "discovered = [by_owner[o].get('papers_discovered', 0) for o in owners]\n"
        "complete = [by_owner[o].get('papers_complete', 0) for o in owners]\n\n"
        "x = range(len(owners))\n"
        "width = 0.35\n"
        "ax.bar([i - width/2 for i in x], discovered, width, label='Phát hiện', color='#3498db')\n"
        "ax.bar([i + width/2 for i in x], complete, width, label='Hoàn tất', color='#2ecc71')\n\n"
        "ax.set_ylabel('Số lượng bài báo')\n"
        "ax.set_title('Tiến độ Thu thập & Hoàn thiện theo Thành viên', fontweight='bold')\n"
        "ax.set_xticks(list(x))\n"
        "ax.set_xticklabels(owner_labels)\n"
        "ax.legend()\n"
        "plt.tight_layout()\n"
        "plt.show()"
    ),
    nbf.v4.new_markdown_cell(
        "## 2. Tối ưu hóa Dung lượng Nguồn TeX/BibTeX"
    ),
    nbf.v4.new_code_cell(
        "extracted = [by_owner[o].get('mean_extracted_bytes_before_filter') or 0 for o in owners]\n"
        "retained = [by_owner[o].get('mean_retained_source_bytes') or 0 for o in owners]\n\n"
        "extracted_kb = [e / 1024 for e in extracted]\n"
        "retained_kb = [r / 1024 for r in retained]\n\n"
        "fig, ax = plt.subplots(figsize=(7, 4), dpi=120)\n"
        "ax.bar([i - 0.15 for i in x], extracted_kb, 0.3, label='Trước lọc (Tổng giải nén)', color='#e74c3c')\n"
        "ax.bar([i + 0.15 for i in x], retained_kb, 0.3, label='Sau lọc (Chỉ TeX/BibTeX)', color='#27ae60')\n\n"
        "ax.set_ylabel('Dung lượng trung bình (KB)')\n"
        "ax.set_title('Dung lượng Nguồn Trước vs. Sau khi Lọc File Phụ trợ', fontweight='bold')\n"
        "ax.set_xticks(list(x))\n"
        "ax.set_xticklabels(owner_labels)\n"
        "ax.legend()\n"
        "plt.tight_layout()\n"
        "plt.show()"
    ),
    nbf.v4.new_markdown_cell(
        "## 3. Hiệu năng & Tài nguyên Hệ thống"
    ),
    nbf.v4.new_code_cell(
        "peak_ram_mb = (bench.get('peak_ram_bytes') or 0) / (1024 * 1024)\n"
        "mean_ram_mb = (bench.get('mean_ram_bytes') or 0) / (1024 * 1024)\n"
        "wall_sec = bench.get('wall_seconds', 0.0)\n\n"
        "print(f\"Thời gian thực thi: {wall_sec:.2f}s\")\n"
        "print(f\"Bộ nhớ RAM đỉnh: {peak_ram_mb:.2f} MB\")\n"
        "print(f\"Bộ nhớ RAM trung bình: {mean_ram_mb:.2f} MB\")\n"
        "print(f\"Phương pháp đo: {bench.get('method', 'tracemalloc')}\")\n"
        "print(f\"Hệ điều hành: {bench.get('environment', {}).get('os', 'Unknown')}\")\n"
        "print(f\"CPU: {bench.get('environment', {}).get('processor', 'Unknown')}\")"
    ),
]

nb.cells = cells

with open("report.ipynb", "w", encoding="utf-8") as f:
    nbf.write(nb, f)

print("Generated report.ipynb successfully.")
