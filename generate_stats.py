"""Backward-compatible wrapper for arxiv_pipeline.statistics."""

from pathlib import Path
import sys

# Ensure src is on PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "src"))

from arxiv_pipeline.statistics.service import StatisticsService


def generate_statistics(run_id: str = "baseline-001", output_root: str = "."):
    service = StatisticsService(check_validation=False)
    stats = service.build_statistics(Path(output_root), run_id)
    out_file = Path("statistics") / f"{run_id}.json"
    service.save_statistics(stats, out_file)
    print(f"[SUCCESS] Generated Schema 1.2 statistics file at: {out_file}")
    return stats


if __name__ == "__main__":
    generate_statistics()
