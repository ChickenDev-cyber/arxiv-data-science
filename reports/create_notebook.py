import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()

cells = [
    nbf.v4.new_markdown_cell(
        "# 📊 Báo cáo Khoa học & Benchmark Pipeline (Milestone 1)\n\n"
        "Notebook này phân tích dữ liệu trực tiếp từ hệ thống pipeline thu thập bài báo arXiv theo chuẩn Schema 1.2.\n\n"
        "> **Lưu ý quy chuẩn:** Benchmark chính thức chạy trên môi trường **Google Colab CPU** (Linux). Các số liệu chưa đo được biểu diễn là `N/A`, không quy đổi về `0`."
    ),
    nbf.v4.new_code_cell(
        "import json\n"
        "from pathlib import Path\n"
        "import matplotlib.pyplot as plt\n"
        "import sys\n\n"
        "sys.path.insert(0, 'src')\n\n"
        "stats_path = Path('statistics/baseline-001.json')\n"
        "bench_path = Path('benchmarks/baseline-001.json')\n"
        "timing_path = Path('benchmarks/baseline-001-timing.json')\n\n"
        "with open(stats_path, 'r', encoding='utf-8') as f:\n"
        "    stats = json.load(f)\n"
        "with open(bench_path, 'r', encoding='utf-8') as f:\n"
        "    bench = json.load(f)\n"
        "timing = {}\n"
        "if timing_path.exists():\n"
        "    with open(timing_path, 'r', encoding='utf-8') as f:\n"
        "        timing = json.load(f)\n\n"
        "summary = stats.get('summary', {})\n"
        "paper_sr = summary.get('paper_success_rate')\n"
        "paper_sr_str = f\"{paper_sr:.1%}\" if paper_sr is not None else \"N/A\"\n"
        "source_sr = summary.get('source_success_rate')\n"
        "source_sr_str = f\"{source_sr:.1%}\" if source_sr is not None else \"N/A\"\n"
        "ref_sr = summary.get('reference_metadata_success_rate')\n"
        "ref_sr_str = f\"{ref_sr:.1%}\" if ref_sr is not None else \"N/A\"\n"
        "mean_refs = summary.get('mean_arxiv_references_per_paper')\n"
        "mean_refs_str = f\"{mean_refs:.1f}\" if mean_refs is not None else \"N/A\"\n\n"
        "print(f\"Tổng số bài phát hiện: {summary.get('papers_discovered', 0):,}\")\n"
        "print(f\"Số bài hoàn thành toàn diện: {summary.get('papers_complete', 0):,} ({paper_sr_str})\")\n"
        "print(f\"Tỷ lệ thành công thu thập nguồn: {source_sr_str}\")\n"
        "print(f\"Tỷ lệ hoàn thành metadata references: {ref_sr_str}\")\n"
        "print(f\"Số reference arXiv trung bình mỗi bài: {mean_refs_str}\")"
    ),
    nbf.v4.new_markdown_cell(
        "## 1. Phân phối Trạng thái Thu thập theo Thành viên"
    ),
    nbf.v4.new_code_cell(
        "by_owner = stats.get('by_owner', {})\n"
        "owners = list(by_owner.keys())\n"
        "complete = [by_owner[o].get('papers_complete', 0) for o in owners]\n"
        "discovered = [by_owner[o].get('papers_discovered', 0) for o in owners]\n"
        "incomplete = [discovered[i] - complete[i] for i in range(len(owners))]\n\n"
        "fig, ax = plt.subplots(figsize=(9, 4.5))\n"
        "p1 = ax.bar(owners, complete, label='Hoàn thành đầy đủ', color='#2ecc71')\n"
        "p2 = ax.bar(owners, incomplete, bottom=complete, label='Chưa hoàn thành', color='#e74c3c')\n\n"
        "ax.set_ylabel('Số lượng bài báo')\n"
        "ax.set_title('Trạng thái Hoàn thành Thu thập Dữ liệu theo Thành viên', fontweight='bold')\n"
        "ax.legend()\n"
        "plt.tight_layout()\n"
        "plt.show()"
    ),
    nbf.v4.new_markdown_cell(
        "## 2. Phân tích Dung lượng File Nguồn TeX (Trước & Sau khi Lọc)"
    ),
    nbf.v4.new_code_cell(
        "owner_labels = []\n"
        "before_kb = []\n"
        "after_kb = []\n\n"
        "for o, s in by_owner.items():\n"
        "    cnt = s.get('source_size_sample_count', 0)\n"
        "    b = s.get('mean_extracted_bytes_before_filter')\n"
        "    a = s.get('mean_retained_source_bytes')\n"
        "    owner_labels.append(o)\n"
        "    before_kb.append(b / 1024 if (cnt > 0 and b is not None) else None)\n"
        "    after_kb.append(a / 1024 if (cnt > 0 and a is not None) else None)\n\n"
        "x = range(len(owner_labels))\n"
        "width = 0.35\n"
        "fig, ax = plt.subplots(figsize=(10, 4.5))\n\n"
        "b_vals = [v if v is not None else 0 for v in before_kb]\n"
        "a_vals = [v if v is not None else 0 for v in after_kb]\n\n"
        "rects1 = ax.bar([p - width/2 for p in x], b_vals, width, label='Trước khi lọc', color='#3498db')\n"
        "rects2 = ax.bar([p + width/2 for p in x], a_vals, width, label='Sau khi giữ lại', color='#9b59b6')\n\n"
        "for i, (b_val, a_val) in enumerate(zip(before_kb, after_kb)):\n"
        "    if b_val is None or a_val is None:\n"
        "        ax.text(i, 5, 'N/A', ha='center', va='bottom', fontsize=9, color='#888888', fontweight='bold')\n\n"
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
        "peak_ram = bench.get('peak_ram_bytes')\n"
        "mean_ram = bench.get('mean_ram_bytes')\n"
        "peak_disk = bench.get('peak_disk_bytes')\n"
        "final_output = bench.get('final_output_bytes')\n"
        "wall_sec = bench.get('wall_seconds')\n"
        "disc_sec = timing.get('discovery_seconds')\n"
        "mean_p_sec = timing.get('mean_paper_seconds')\n\n"
        "peak_ram_str = f\"{peak_ram / (1024 * 1024):.2f} MB\" if peak_ram is not None else \"N/A\"\n"
        "mean_ram_str = f\"{mean_ram / (1024 * 1024):.2f} MB\" if mean_ram is not None else \"N/A\"\n"
        "peak_disk_str = f\"{peak_disk / 1024:.2f} KB\" if peak_disk is not None else \"N/A\"\n"
        "final_output_str = f\"{final_output / 1024:.2f} KB\" if final_output is not None else \"N/A\"\n"
        "wall_str = f\"{wall_sec:.2f}s\" if wall_sec is not None else \"N/A\"\n"
        "disc_str = f\"{disc_sec:.2f}s\" if disc_sec is not None else \"N/A\"\n"
        "mean_p_str = f\"{mean_p_sec:.2f}s\" if mean_p_sec is not None else \"N/A\"\n\n"
        "print(f\"Thời gian thực thi toàn lệnh (Wall time): {wall_str}\")\n"
        "print(f\"Thời gian giai đoạn discovery: {disc_str}\")\n"
        "print(f\"Thời gian trung bình mỗi bài (Elapsed time): {mean_p_str}\")\n"
        "print(f\"Bộ nhớ RAM cao nhất (Peak RSS): {peak_ram_str}\")\n"
        "print(f\"Bộ nhớ RAM trung bình: {mean_ram_str}\")\n"
        "print(f\"Phương pháp đo RAM: {bench.get('method', 'N/A')}\")\n"
        "print(f\"Dung lượng đầu ra hoàn chỉnh: {final_output_str}\")\n"
        "print(f\"Dung lượng đĩa đỉnh (Peak disk): {peak_disk_str}\")\n"
        "print(f\"Mã thoát (Exit code): {bench.get('exit_code', 'N/A')}\")\n"
        "print(f\"Hệ điều hành: {bench.get('env', {}).get('os', bench.get('environment', {}).get('os', 'Unknown'))}\")\n"
        "print(f\"CPU: {bench.get('env', {}).get('processor', bench.get('environment', {}).get('processor', 'Unknown'))}\")"
    ),
    nbf.v4.new_code_cell(
        "per_paper = timing.get('per_paper_seconds', {})\n"
        "if per_paper:\n"
        "    p_ids = list(per_paper.keys())\n"
        "    p_times = list(per_paper.values())\n"
        "    fig, ax = plt.subplots(figsize=(8, 4))\n"
        "    bars = ax.bar(p_ids, p_times, color='#3498db')\n"
        "    ax.set_ylabel('Thời gian xử lý thực (giây)')\n"
        "    ax.set_title('Thời gian Xử lý Từng Bài báo (Elapsed Time)', fontweight='bold')\n"
        "    for b in bars:\n"
        "        ax.text(b.get_x() + b.get_width()/2, b.get_height(), f\"{b.get_height():.2f}s\", ha='center', va='bottom', fontsize=9)\n"
        "    plt.tight_layout()\n"
        "    plt.show()\n"
        "else:\n"
        "    print('Thời gian xử lý từng bài: Chưa đo (N/A)')"
    ),
]

nb.cells = cells

with open("report.ipynb", "w", encoding="utf-8") as f:
    nbf.write(nb, f)

print("Generated report.ipynb successfully.")
