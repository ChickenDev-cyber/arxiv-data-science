"""Local Pipeline Runner to fetch arXiv Metadata and TeX/LaTeX Sources.

Supports:
- Metadata recovery (arXiv OAI-PMH): discovers versions and generates Schema 1.2 metadata.json
- Sources recovery (arXiv /src/): downloads, filters images, extracts .tex/.bib, generates source_manifest.json
- Bounded retries, automatic cooldown on rate limits / network blips, atomic publication.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from ..contracts import SCHEMA_VERSION, paper_folder
from ..discovery.api import RemoteError
from ..metadata.api import OaiClient
from ..metadata.service import MetadataModule
from ..runner.models import paper_spec
from ..runner.state import StateStore, OutputLock
from ..runner.writer import Publisher
from ..sources.api import ArxivSourceClient, DEFAULT_MAX_DOWNLOAD_BYTES
from ..sources.extractor import SourceExtractor, ExtractionLimits
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


def get_assigned_papers(corpus_dir: Path, db_path: Path, run_id: str) -> list[dict]:
    """Retrieve full list of assigned papers for this corpus from discovery or tasks."""
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        # 1. Try discovery attempt result_json
        c.execute(
            "SELECT result_json FROM attempts WHERE run_id = ? AND task_key LIKE '%discovery%' AND result_valid = 1 "
            "ORDER BY attempt_id DESC LIMIT 1",
            (run_id,),
        )
        row = c.fetchone()
        if row and row[0]:
            try:
                data = json.loads(row[0]).get("data", {})
                papers = data.get("papers", [])
                if papers:
                    return papers
            except Exception:
                pass

        # 2. Try run_tasks metadata / sources
        c.execute(
            "SELECT input_json FROM run_tasks WHERE run_id = ? AND (task_key LIKE '%metadata%' OR task_key LIKE '%sources%')",
            (run_id,),
        )
        seen = {}
        for (input_str,) in c.fetchall():
            try:
                task = json.loads(input_str)
                aid = task.get("arxiv_id")
                owner = task.get("owner")
                if aid and owner and aid not in seen:
                    seen[aid] = {"owner": owner, "arxiv_id": aid}
            except Exception:
                pass
        if seen:
            return [seen[k] for k in sorted(seen)]

        # 3. Try runs initial_scope
        c.execute("SELECT initial_scope FROM runs WHERE run_id = ? LIMIT 1", (run_id,))
        row = c.fetchone()
        if row and row[0]:
            try:
                scope = json.loads(row[0])
                papers = scope.get("paper_tasks", [])
                if papers:
                    return papers
            except Exception:
                pass
    finally:
        conn.close()

    return []


def get_pending_metadata_tasks(corpus_dir: Path, db_path: Path, run_id: str, retry_not_found: bool = False) -> list[dict]:
    """Find papers where metadata.json is missing or unaccepted in DB."""
    assigned = get_assigned_papers(corpus_dir, db_path, run_id)
    if not assigned:
        return []

    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute(
            "SELECT t.task_key, t.accepted_attempt_id, a.result_json "
            "FROM run_tasks t "
            "LEFT JOIN attempts a ON a.attempt_id = t.latest_attempt_id "
            "WHERE t.run_id = ? AND t.task_key LIKE '%metadata%'",
            (run_id,),
        )
        db_tasks = {}
        for key, acc_id, res_json in c.fetchall():
            try:
                parts = json.loads(key)
                aid = parts[2]
                status = None
                if res_json:
                    try:
                        status = json.loads(res_json).get("status")
                    except Exception:
                        pass
                db_tasks[aid] = {"accepted": acc_id is not None, "status": status}
            except Exception:
                pass
    finally:
        conn.close()

    pending = []
    for paper in assigned:
        aid = paper["arxiv_id"]
        owner = paper.get("owner", "unknown")
        meta_file = corpus_dir / "raw" / owner / paper_folder(aid) / "metadata.json"
        alt_meta_file = corpus_dir / "papers" / paper_folder(aid) / "metadata.json"
        file_exists = meta_file.is_file() or alt_meta_file.is_file()
        db_info = db_tasks.get(aid)

        # If already accepted and file exists, not pending
        if file_exists and db_info and db_info["accepted"]:
            continue

        # If not_found on arXiv and not retrying
        if not retry_not_found and db_info and db_info["status"] == "not_found":
            continue

        pending.append(paper)

    return pending


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
            (run_id,),
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


def run_metadata_stage(corpus_dir: Path, db_path: Path, run_id: str, args):
    """Crawl and publish missing metadata via OAI-PMH."""
    pending = get_pending_metadata_tasks(corpus_dir, db_path, run_id, retry_not_found=args.retry_not_found)
    owner = pending[0].get("owner", "unknown") if pending else "unknown"

    print("\n" + "=" * 65)
    print(f"[*] [STAGE 1: METADATA] Thư mục: {corpus_dir}")
    print(f"[*] Run ID: {run_id} | Phụ trách: {owner}")
    print(f"[*] Số bài CẦN CÀO METADATA: {len(pending)}")
    print("=" * 65 + "\n")

    if not pending:
        print("[+] Metadata đã hoàn tất 100%, không có bài nào cần cào thêm.")
        return 0

    if args.pilot:
        pending = pending[:5]
        print(f"[*] Chế độ PILOT: Chạy {len(pending)} bài đầu tiên.")
    elif args.limit:
        pending = pending[:args.limit]
        print(f"[*] Giới hạn lượt này: Chạy {len(pending)} bài.")

    context = {"run_id": run_id, "output_root": corpus_dir, "is_mock": False}
    metadata_module = MetadataModule(client=OaiClient(delay=args.delay))
    acc_path = corpus_dir / "runs" / run_id / "accepted_results.jsonl"
    acc_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(pending)
    succeeded = 0
    start_time = time.time()

    with OutputLock(corpus_dir):
        with StateStore(db_path, run_id) as store:
            publisher = Publisher(context, store)

            for i, task_info in enumerate(pending, 1):
                aid = task_info["arxiv_id"]
                owner = task_info.get("owner", "unknown")
                task_dict = {"owner": owner, "arxiv_id": aid}
                spec = paper_spec(task_dict, "metadata")

                store.ensure_task(spec)
                label = f"{aid} [metadata]"
                print(f"[{i}/{total}] Đang lấy metadata {label}...", end=" ", flush=True)

                consecutive_errors = 0
                while True:
                    try:
                        attempt = store.mark_running(spec.key)
                        result = metadata_module.fetch_metadata(task_dict, context)
                        store.record(attempt, result, issues=[])

                        if result["status"] in ("success", "partial"):
                            publisher.publish(attempt, result)
                            with open(acc_path, "a", encoding="utf-8") as f:
                                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                            succeeded += 1

                            # Register newly discovered versions into run_tasks for sources stage
                            versions = (result.get("data") or {}).get("versions") or []
                            for v_info in versions:
                                v_num = v_info.get("version", 1)
                                v_spec = paper_spec(task_dict, "sources", v_num)
                                store.ensure_task(v_spec)

                            title = (result.get("data") or {}).get("title") or "No title"
                            if len(title) > 35:
                                title = title[:32] + "..."
                            print(f"OK! ({result['status']}: {len(versions)} phiên bản - {title})")

                        elif result["status"] == "not_found":
                            store.complete(attempt, accepted=False)
                            with open(acc_path, "a", encoding="utf-8") as f:
                                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                            print("[!] 404 Not Found trên OAI-PMH (Bỏ qua)")
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
                        print(f"\n    [!] Lỗi lấy metadata {label}: {exc}. Chờ {cooldown}s rồi thử lại...", flush=True)
                        time.sleep(cooldown)

    elapsed = time.time() - start_time
    print(f"\n[+] Kết thúc stage Metadata: Đã lưu {succeeded}/{total} bài ({elapsed / 60:.1f} phút).\n")
    return succeeded


def run_sources_stage(corpus_dir: Path, db_path: Path, run_id: str, args):
    """Crawl and publish missing LaTeX/TeX sources."""
    pending = get_pending_source_tasks(db_path, run_id, skip_not_found=not args.retry_not_found)
    owner = pending[0].get("owner", "unknown") if pending else "unknown"

    print("\n" + "=" * 65)
    print(f"[*] [STAGE 2: SOURCES] Thư mục: {corpus_dir}")
    print(f"[*] Run ID: {run_id} | Phụ trách: {owner}")
    print(f"[*] Giới hạn dung lượng tải: {args.max_download_bytes / (1024 * 1024):.0f} MiB")
    print(f"[*] Số phiên bản sources CẦN TẢI BÙ: {len(pending)}")
    print("=" * 65 + "\n")

    if not pending:
        print("[+] Toàn bộ sources đã đầy đủ, không có phiên bản nào cần tải thêm.")
        return 0

    if args.pilot:
        pending = pending[:5]
        print(f"[*] Chế độ PILOT: Chạy {len(pending)} phiên bản đầu tiên.")
    elif args.limit:
        pending = pending[:args.limit]
        print(f"[*] Giới hạn lượt này: Chạy {len(pending)} phiên bản.")

    context = {"run_id": run_id, "output_root": corpus_dir, "is_mock": False}
    limits = ExtractionLimits(
        max_file_bytes=args.max_download_bytes,
        max_extracted_bytes=max(1024 * 1024 * 1024, 2 * args.max_download_bytes),
    )
    sources_module = SourcesModule(
        client=ArxivSourceClient(delay=args.delay, max_download_bytes=args.max_download_bytes),
        extractor=SourceExtractor(),
        limits=limits,
    )

    acc_path = corpus_dir / "runs" / run_id / "accepted_results.jsonl"
    acc_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(pending)
    succeeded = 0
    start_time = time.time()

    with OutputLock(corpus_dir):
        with StateStore(db_path, run_id) as store:
            publisher = Publisher(context, store)

            for i, task_info in enumerate(pending, 1):
                aid = task_info["arxiv_id"]
                version = task_info.get("version", 1)
                task_dict = {"owner": task_info["owner"], "arxiv_id": aid}
                spec = paper_spec(task_dict, "sources", version)

                label = f"{aid}v{version}"
                print(f"[{i}/{total}] Đang tải & trích xuất mã nguồn {label}...", end=" ", flush=True)

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
    print(f"\n[+] Kết thúc stage Sources: Đã xử lý {succeeded}/{total} phiên bản ({elapsed / 60:.1f} phút).\n")
    return succeeded


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(
        description="Thu thập Metadata và Mã nguồn TeX/LaTeX (Sources) cục bộ cho tập dữ liệu arXiv."
    )
    parser.add_argument(
        "--corpus-dir",
        default="data/m1-colab-trong-01/corpus",
        help="Đường dẫn thư mục corpus local (mặc định: data/m1-colab-trong-01/corpus)",
    )
    parser.add_argument("--run-id", default=None, help="Mã run_id (tự động phát hiện nếu để trống)")
    parser.add_argument(
        "--step",
        choices=["all", "metadata", "sources"],
        default="all",
        help="Công đoạn cần chạy: all (cả metadata lẫn sources, mặc định), metadata, hoặc sources",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Thời gian giãn cách giữa các lượt gọi arXiv (giây, mặc định 3.0s)",
    )
    parser.add_argument(
        "--max-download-bytes",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES,
        help="Giới hạn dung lượng tải mỗi gói nguồn (bytes, mặc định 500 MiB)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Chỉ chạy tối đa N bài")
    parser.add_argument("--pilot", action="store_true", help="Chạy thử nghiệm 5 bài đầu tiên")
    parser.add_argument("--retry-not-found", action="store_true", help="Thử tải lại cả các bài từng bị 404")

    args = parser.parse_args()

    corpus_dir = Path(args.corpus_dir).resolve()
    db_path = corpus_dir / "state.sqlite"
    if not db_path.is_file():
        print(f"[!] Lỗi: Không tìm thấy database tại {db_path}")
        sys.exit(1)

    run_id = args.run_id or detect_run_id(db_path)

    print("\n" + "#" * 65)
    print(f"### ARXIV LOCAL METADATA & SOURCES RUNNER")
    print(f"### Thư mục: {corpus_dir}")
    print(f"### Run ID: {run_id}")
    print(f"### Công đoạn (step): {args.step}")
    print(f"### Giới hạn tải: {args.max_download_bytes / (1024 * 1024):.0f} MiB | Giãn cách: {args.delay}s")
    print("#" * 65)

    if args.step in ("all", "metadata"):
        run_metadata_stage(corpus_dir, db_path, run_id, args)

    if args.step in ("all", "sources"):
        run_sources_stage(corpus_dir, db_path, run_id, args)


if __name__ == "__main__":
    main()
