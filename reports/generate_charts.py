"""Generate scientific charts from Statistics and Benchmark data for Jupyter Book / Report."""

import json
from pathlib import Path
import matplotlib.pyplot as plt

plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial"]
plt.rcParams["axes.edgecolor"] = "#cccccc"
plt.rcParams["axes.linewidth"] = 0.8


def generate_all_charts(
    stats_file: Path = Path("statistics/baseline-001.json"),
    bench_file: Path = Path("benchmarks/baseline-001.json"),
    output_dir: Path = Path("reports/figures"),
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    if not stats_file.exists():
        print(f"Stats file {stats_file} not found.")
        return generated

    with open(stats_file, "r", encoding="utf-8") as f:
        stats = json.load(f)

    bench = {}
    if bench_file.exists():
        with open(bench_file, "r", encoding="utf-8") as f:
            bench = json.load(f)

    by_owner = stats.get("by_owner", {})
    owners = sorted(by_owner.keys())
    owner_labels = [o.capitalize() for o in owners]

    # Chart 1: Paper Status by Member
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    discovered = [by_owner[o].get("papers_discovered", 0) for o in owners]
    complete = [by_owner[o].get("papers_complete", 0) for o in owners]
    meta_ok = [by_owner[o].get("metadata_complete_papers", 0) for o in owners]

    x = range(len(owners))
    width = 0.25

    ax.bar([i - width for i in x], discovered, width, label="Phát hiện (Discovered)", color="#3498db")
    ax.bar([i for i in x], meta_ok, width, label="Metadata hoàn chỉnh", color="#f39c12")
    ax.bar([i + width for i in x], complete, width, label="Hoàn tất toàn diện", color="#2ecc71")

    ax.set_ylabel("Số lượng bài báo", fontsize=11)
    ax.set_title("Tiến độ Thu thập & Hoàn thiện theo Thành viên", fontsize=13, fontweight="bold", pad=12)
    ax.set_xticks(list(x))
    ax.set_xticklabels(owner_labels, fontsize=10)
    ax.legend(frameon=True, facecolor="#ffffff", edgecolor="#e0e0e0")
    plt.tight_layout()

    out1 = output_dir / "pipeline_progress.png"
    plt.savefig(out1)
    plt.close()
    generated.append(out1)

    # Chart 2: Storage Optimization (Extracted vs Retained)
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    extracted = [by_owner[o].get("mean_extracted_bytes_before_filter") or 0 for o in owners]
    retained = [by_owner[o].get("mean_retained_source_bytes") or 0 for o in owners]

    # Convert to KB
    extracted_kb = [e / 1024 for e in extracted]
    retained_kb = [r / 1024 for r in retained]

    ax.bar([i - 0.15 for i in x], extracted_kb, 0.3, label="Trước lọc (Tổng giải nén)", color="#e74c3c")
    ax.bar([i + 0.15 for i in x], retained_kb, 0.3, label="Sau lọc (Chỉ TeX/BibTeX)", color="#27ae60")

    ax.set_ylabel("Dung lượng trung bình (KB)", fontsize=11)
    ax.set_title("Tối ưu hóa Dung lượng Nguồn TeX qua Bộ lọc T3", fontsize=13, fontweight="bold", pad=12)
    ax.set_xticks(list(x))
    ax.set_xticklabels(owner_labels, fontsize=10)
    ax.legend(frameon=True, facecolor="#ffffff", edgecolor="#e0e0e0")
    plt.tight_layout()

    out2 = output_dir / "storage_comparison.png"
    plt.savefig(out2)
    plt.close()
    generated.append(out2)

    # Chart 3: System Performance Benchmark
    if bench:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4), dpi=150)
        
        peak_ram_mb = (bench.get("peak_ram_bytes") or 0) / (1024 * 1024)
        mean_ram_mb = (bench.get("mean_ram_bytes") or 0) / (1024 * 1024)
        wall_sec = bench.get("wall_seconds", 0.0)

        # RAM comparison
        ram_labels = ["Mean RAM", "Peak RAM"]
        ram_values = [mean_ram_mb, peak_ram_mb]
        bars1 = ax1.bar(ram_labels, ram_values, color=["#9b59b6", "#8e44ad"], width=0.45)
        ax1.set_ylabel("RAM (MB)", fontsize=11)
        ax1.set_title("Bộ nhớ RAM Sử dụng", fontsize=12, fontweight="bold")
        for bar in bars1:
            yval = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2, yval + 0.5, f"{yval:.1f} MB", ha="center", va="bottom", fontsize=9)

        # Wall time
        bars2 = ax2.bar(["Wall Time"], [wall_sec], color=["#1abc9c"], width=0.3)
        ax2.set_ylabel("Thời gian (giây)", fontsize=11)
        ax2.set_title("Thời gian Thực thi Pipeline", fontsize=12, fontweight="bold")
        for bar in bars2:
            yval = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2, yval + 0.1, f"{yval:.2f}s", ha="center", va="bottom", fontsize=9)

        plt.tight_layout()
        out3 = output_dir / "system_resources.png"
        plt.savefig(out3)
        plt.close()
        generated.append(out3)

    print(f"Generated {len(generated)} charts in {output_dir}")
    return generated


if __name__ == "__main__":
    generate_all_charts()
