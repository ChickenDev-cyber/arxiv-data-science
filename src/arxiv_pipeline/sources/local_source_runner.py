"""Local Sources Recovery Runner to fetch and extract missing arXiv LaTeX sources.

Fetches only tasks where sources are missing or incomplete (accepted_attempt_id IS NULL),
extracts .tex and .bib files (filtering images), produces Schema 1.2 Results,
and publishes atomically to StateStore and disk.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

from ..contracts import SCHEMA_VERSION, paper_folder
from ..discovery.api import RemoteError
from ..runner.models import paper_spec
from ..runner.state import StateStore, OutputLock
from ..runner.writer import Publisher
from ..sources.api import ArxivSourceClient
from ..sources.extractor import SourceExtractor
from ..sources.service import SourcesModule


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def detect_run_id(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT run_id FROM runs LIMIT 1")
        row = c.fetchone()
        if row and row[0]:
            return row[0]
    finally:
        conn.close()
    return "m1-run"


def get_pending_source_tasks(db_path: Path, run_id: str, skip_not_found: bool = True) -> list[dict]:
    """Find all source tasks where accepted_attempt_id IS NULL."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT t.task_key, t.input_json, a.result_json FROM run_tasks t "
            "LEFT JOIN attempts a ON a.attempt_id = t.latest_attempt_id "
            "WHERE t.run_id = ? AND t.task_key LIKE '%sources%' AND t.accepted_attempt_id IS NULL "
            "ORDER BY t.task_key",
            (run_id,)
        )
        tasks = []
        for task_key, input_json, result_json in c.fetchall():
            if skip_not_found and result_json:
                try:
                    res = json.loads(result_json)
                    if res.get("status") == "not_found":
                        continue
                except Exception:
                    pass
            task_info = json.loads(input_json)
            tasks.append(task_info)
        return tasks
    finally:
        conn.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="Tải bù mã nguồn TeX/LaTeX (Sources) cho các bài bị thiếu.")
    parser.add_argument("--corpus-dir", default="data/m1-colab-trong-01/corpus",
                        help="Đường dẫn thư mục corpus local")
    parser.add_argument("--run-id", default=None, help="Mã run_id (tự động phát hiện nếu để trống)")
    parser.add_argument("--delay", type=float, default=2.5,
                        help="Thời gian giãn cách giữa các lần tải từ arXiv (giây, mặc định 2.5s)")
    parser.add_argument("--limit", type=int, default=None, help="Chỉ chạy tối đa N bài")
    parser.add_argument("--pilot", action="store_true", help="Chạy thử nghiệm 5 bài đầu tiên")

    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir).resolve()
    db_path = corpus_dir / "state.sqlite"
    if not db_path.is_file():
        print(f"[!] Lỗi: Không tìm thấy database tại {db_path}")
        sys.exit(1)

    run_id = args.run_id or detect_run_id(db_path)
    context = {"run_id": run_id, "output_root": corpus_dir, "is_mock": False}

    pending_tasks = get_pending_source_tasks(db_path, run_id)
    owner = pending_tasks[0].get("owner", "trong") if pending_tasks else "unknown"

    print("\n" + "=" * 65)
    print(f"[*] Thư mục corpus: {corpus_dir}")
    print(f"[*] Run ID: {run_id} | Người phụ trách: {owner}")
    print(f"[*] Số phiên bản sources CẦN TẢI BÙ: {len(pending_tasks)}")
    print("=" * 65 + "\n")

    if not pending_tasks:
        print("[+] Tuyệt vời! Toàn bộ các phiên bản sources đều đã đầy đủ.")
        sys.exit(0)

    if args.pilot:
        pending_tasks = pending_tasks[:5]
        print(f"[*] Chế độ PILOT: Chỉ thử nghiệm {len(pending_tasks)} phiên bản đầu tiên.")
    elif args.limit:
        pending_tasks = pending_tasks[:args.limit]
        print(f"[*] Giới hạn lượt này: Chạy {len(pending_tasks)} phiên bản.")

    # Setup sources module
    sources_module = SourcesModule(
        client=ArxivSourceClient(delay=args.delay),
        extractor=SourceExtractor()
    )

    acc_path = corpus_dir / "runs" / run_id / "accepted_results.jsonl"
    acc_path.parent.mkdir(parents=True, exist_ok=True)

    total_tasks = len(pending_tasks)
    succeeded = 0
    start_time = time.time()

    with OutputLock(corpus_dir):
        with StateStore(db_path, run_id) as store:
            publisher = Publisher(context, store)

            for i, task_info in enumerate(pending_tasks, 1):
                aid = task_info["arxiv_id"]
                version = task_info.get("version", 1)
                task_dict = {"owner": task_info["owner"], "arxiv_id": aid}
                spec = paper_spec(task_dict, "sources", version)

                label = f"{aid}v{version}"
                print(f"[{i}/{total_tasks}] Đang tải & trích xuất mã nguồn {label}...", end=" ", flush=True)

                consecutive_errors = 0
                while True:
                    try:
                        attempt = store.mark_running(spec.key)
                        result = sources_module.fetch_sources(task_dict, version, context)
                        store.record(attempt, result, issues=[])

                        if result["status"] in ("success", "unsupported"):
                            publisher.publish(attempt, result)
                            with open(acc_path, "a", encoding="utf-8") as f:
                                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                            succeeded += 1

                            # Print summary
                            m = result.get("metrics", {})
                            dl_mb = (m.get("download_bytes") or 0) / (1024 * 1024)
                            files_count = len(result.get("data", {}).get("files", []))
                            kind = result.get("data", {}).get("source_kind", "unknown")
                            print(f"OK! ({result['status']}: {kind}, {dl_mb:.1f} MB, {files_count} files TeX/bib)")
                        elif result["status"] == "not_found":
                            store.complete(attempt, accepted=False)
                            with open(acc_path, "a", encoding="utf-8") as f:
                                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                            print("[!] 404 Not Found trên arXiv (Đã ghi nhận, bỏ qua)")
                        else:
                            store.complete(attempt, accepted=False)
                            print(f"[!] Bỏ qua (status: {result['status']}, lỗi: {result.get('error')})")

                        break

                    except KeyboardInterrupt:
                        print("\n[!] Dừng bởi người dùng (Ctrl+C). Tiến trình đã lưu an toàn.")
                        sys.exit(0)
                    except Exception as exc:
                        consecutive_errors += 1
                        cooldown = min(60, 10 * consecutive_errors)
                        print(f"\n    [!] Lỗi tải {label}: {exc}. Chờ {cooldown}s rồi thử lại...", flush=True)
                        time.sleep(cooldown)

    elapsed = time.time() - start_time
    print("\n" + "=" * 65)
    print(f"[+] Hoàn tất đợt tải bù Sources: Đã xử lý {succeeded}/{total_tasks} phiên bản.")
    print(f"[*] Tổng thời gian: {elapsed / 60:.1f} phút.")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
