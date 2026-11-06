"""Sync and complete supplementary crawls for all project members sequentially.

Runs metadata and source crawling across all member corpora one by one, respecting
arXiv rate limits and skipping stages that are already 100% complete.
"""

import argparse
import sys
import time
from pathlib import Path

from ..contracts import paper_folder
from .local_source_runner import (
    DEFAULT_MAX_DOWNLOAD_BYTES,
    detect_run_id,
    run_metadata_stage,
    run_sources_stage,
)

DEFAULT_MEMBERS = ["hoang", "huynh-nhi", "man-nhi", "khang", "trong"]


def get_member_corpus(member: str, base_dir: Path = Path("data")) -> tuple[Path, str]:
    corpus_dir = base_dir / f"m1-colab-{member}-01" / "corpus"
    run_id = f"m1-colab-{member}-01"
    return corpus_dir, run_id


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Chạy cào bổ sung tự động lần lượt cho toàn bộ 5 thành viên (One-command Multi-Member Runner)."
    )
    parser.add_argument(
        "--members",
        default=",".join(DEFAULT_MEMBERS),
        help=f"Danh sách thành viên cần chạy, cách nhau bởi dấu phẩy (mặc định: {','.join(DEFAULT_MEMBERS)})",
    )
    parser.add_argument(
        "--stage",
        choices=["all", "metadata", "sources"],
        default="all",
        help="Công đoạn cần chạy cho mỗi người: all (mặc định), metadata, hoặc sources",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.5,
        help="Thời gian giãn cách giữa các lượt gọi arXiv (giây, mặc định 3.5s an toàn)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Thời gian chờ tối đa cho mỗi request mạng (giây, mặc định 30.0s)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Số lần thử lại khi gặp lỗi mạng/timeout (mặc định 2 lần)",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="Giới hạn dung lượng tải mỗi gói nguồn (bytes, mặc định 500 MiB)",
    )
    parser.add_argument("--pilot", action="store_true", help="Chạy thử nghiệm 5 bài mỗi người")
    parser.add_argument("--retry-not-found", action="store_true", help="Thử tải lại cả các bài từng bị 404")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn tối đa N bài mỗi người")

    args = parser.parse_args()
    member_list = [m.strip().lower() for m in args.members.split(",") if m.strip()]

    print("\n" + "#" * 70)
    print("### ALL-IN-ONE MULTI-MEMBER SUPPLEMENTARY RUNNER")
    print(f"### Danh sách thành viên: {', '.join(member_list)}")
    print(f"### Công đoạn (stage): {args.stage}")
    print(f"### Giãn cách: {args.delay}s | Timeout: {args.timeout}s | Giới hạn gói nguồn: {args.max_download_bytes / (1024 * 1024):.0f} MiB")
    print("#" * 70 + "\n")

    start_all = time.time()
    results = {}

    for idx, member in enumerate(member_list, 1):
        corpus_dir, run_id = get_member_corpus(member)
        db_path = corpus_dir / "state.sqlite"

        print("\n" + "=" * 70)
        print(f">>> [{idx}/{len(member_list)}] BẮT ĐẦU XỬ LÝ THÀNH VIÊN: {member.upper()}")
        print(f">>> Thư mục: {corpus_dir}")
        print("=" * 70)

        if not db_path.is_file():
            print(f"[!] Bỏ qua: Không tìm thấy database tại {db_path}\n")
            results[member] = "Không tìm thấy thư mục/DB"
            continue

        actual_run_id = detect_run_id(db_path)
        member_start = time.time()

        try:
            # Stage 1: Metadata
            if args.stage in ("all", "metadata"):
                run_metadata_stage(corpus_dir, db_path, actual_run_id, args)

            # Stage 2: Sources
            if args.stage in ("all", "sources"):
                run_sources_stage(corpus_dir, db_path, actual_run_id, args)

            elapsed_member = (time.time() - member_start) / 60
            results[member] = f"Hoàn tất ({elapsed_member:.1f} phút)"
            print(f"\n[+] ĐÃ HOÀN TẤT BỔ SUNG CHO: {member.upper()} ({elapsed_member:.1f} phút)\n")

        except KeyboardInterrupt:
            print(f"\n[!] Bị ngắt bởi người dùng khi đang xử lý {member}. Dừng toàn bộ tiến trình.")
            sys.exit(0)
        except Exception as exc:
            print(f"\n[!] Lỗi khi xử lý {member}: {exc}. Chuyển sang thành viên tiếp theo...")
            results[member] = f"Lỗi: {exc}"

    total_time = (time.time() - start_all) / 60
    print("\n" + "#" * 70)
    print(f"### TỔNG KẾT TIẾN ĐỘ TOÀN BỘ ({total_time:.1f} PHÚT)")
    for member in member_list:
        print(f"  * {member.upper()}: {results.get(member, 'Chưa chạy')}")
    print("#" * 70 + "\n")


if __name__ == "__main__":
    main()
