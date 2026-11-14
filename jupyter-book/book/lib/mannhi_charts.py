"""Build SVG charts for Mẫn Nhi's report from generated CSV files."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_charts(data_dir: Path, output_dir: Path) -> list[Path]:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Cần cài matplotlib từ reqs/mannhi.txt để sinh biểu đồ.") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    refs = read_rows(data_dir / "references_per_paper.csv")

    # Keep only numeric values actually present in the source run.
    counts = []
    for row in refs:
        value = row.get("arxiv_reference_count", "")
        try:
            counts.append(int(value))
        except (TypeError, ValueError):
            continue

    if counts:
        fig, ax = plt.subplots()
        ax.hist(counts, bins="auto")
        ax.set_title("Số reference arXiv trên mỗi bài")
        ax.set_xlabel("Số arXiv ID duy nhất")
        ax.set_ylabel("Số bài")
        fig.tight_layout()
        target = output_dir / "references-count.svg"
        fig.savefig(target, format="svg")
        plt.close(fig)
        created.append(target)

    statuses = Counter(row.get("status", "unknown") or "unknown" for row in refs)
    if statuses:
        fig, ax = plt.subplots()
        labels = list(statuses)
        values = [statuses[label] for label in labels]
        ax.bar(labels, values)
        ax.set_title("Trạng thái thu thập references")
        ax.set_xlabel("Status")
        ax.set_ylabel("Số bài")
        fig.tight_layout()
        target = output_dir / "references-status.svg"
        fig.savefig(target, format="svg")
        plt.close(fig)
        created.append(target)

    discovery = read_rows(data_dir / "discovery.csv")
    checked = []
    found = []
    for row in discovery:
        try:
            checked.append(int(row.get("candidates_checked", "")))
            found.append(int(row.get("found_count", "")))
        except (TypeError, ValueError):
            continue
    if checked and found:
        fig, ax = plt.subplots()
        ax.bar(["Candidates checked", "Papers found"], [sum(checked), sum(found)])
        ax.set_title("Tổng hợp Discovery trong run")
        ax.set_ylabel("Số lượng")
        fig.tight_layout()
        target = output_dir / "discovery-summary.svg"
        fig.savefig(target, format="svg")
        plt.close(fig)
        created.append(target)

    return created


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/mannhi"))
    parser.add_argument("--output-dir", type=Path, default=Path("book/_static/generated"))
    args = parser.parse_args()
    for path in build_charts(args.data_dir, args.output_dir):
        print(path)
