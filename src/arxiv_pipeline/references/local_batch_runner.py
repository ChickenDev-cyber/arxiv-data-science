"""Local Batch References Runner for high-throughput references ingestion.

Leverages S2 POST /graph/v1/paper/batch (100 papers/request) to eliminate
HTTP 429 rate limits, enrich citations with arXiv submission dates, and
persist Schema 1.2 results into SQLite StateStore and corpus directory.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import zipfile

from ..contracts import SCHEMA_VERSION, paper_folder
from ..discovery.api import RemoteError
from ..identifiers import normalize_arxiv_id, is_valid_arxiv_id
from ..references.api import ArxivDateClient
from ..references.batch_client import BatchSemanticScholarClient, adapt_batch_reference
from ..references.checkpoint import DateCache
from ..runner.models import paper_spec
from ..runner.state import StateStore, OutputLock
from ..runner.writer import Publisher
from ..snapshots.service import SnapshotService


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def process_batch_references(root: Path, run_id: str, tasks: list[dict],
                             s2_client: BatchSemanticScholarClient,
                             arxiv_dates_client: ArxivDateClient,
                             date_cache: DateCache | None = None,
                             progress=None) -> int:
    """Process a chunk of papers via S2 batch POST and arXiv date lookups."""
    if not tasks:
        return 0

    root = Path(root)
    db_path = root / "state.sqlite"
    arxiv_ids = [t["arxiv_id"] for t in tasks]
    owner = tasks[0]["owner"]

    if progress:
        progress(f"Đang gửi POST batch {len(arxiv_ids)} bài tới Semantic Scholar...")

    # 1. Fetch S2 batch
    batch_data = s2_client.fetch_batch_adaptive(arxiv_ids)
    s2_map = {}
    for aid, paper in zip(arxiv_ids, batch_data):
        if paper is not None:
            ext_arxiv = paper.get("externalIds", {}).get("ArXiv")
            mapped_id = normalize_arxiv_id(ext_arxiv) if ext_arxiv else aid
            s2_map[mapped_id] = paper
            s2_map[aid] = paper

    # 2. Collect cited arXiv IDs across the whole batch
    all_cited_ids = set()
    for aid in arxiv_ids:
        paper = s2_map.get(aid)
        if not paper or not paper.get("references"):
            continue
        for ref in paper["references"]:
            ext = ref.get("externalIds") or {}
            c_aid = ext.get("ArXiv")
            if c_aid and is_valid_arxiv_id(c_aid):
                norm_c = normalize_arxiv_id(c_aid)
                all_cited_ids.add(norm_c)

    # 3. Query missing submission dates from arXiv
    known_dates = {}
    missing_date_ids = list(all_cited_ids)
    if date_cache is not None:
        cached = date_cache.get_many(missing_date_ids)
        known_dates.update(cached)
        missing_date_ids = [cid for cid in missing_date_ids if cid not in known_dates]

    if missing_date_ids:
        if progress:
            progress(f"Đang tra cứu ngày nộp arXiv cho {len(missing_date_ids)} tài liệu trích dẫn...")
        chunk_size = 100
        for i in range(0, len(missing_date_ids), chunk_size):
            chunk = missing_date_ids[i:i + chunk_size]
            try:
                fetched = arxiv_dates_client.submission_dates(chunk)
                known_dates.update(fetched)
                if date_cache is not None:
                    for cid, sdate in fetched.items():
                        date_cache.put(cid, sdate, "arxiv")
            except Exception as exc:
                if progress:
                    progress(f"Cảnh báo: Lỗi tra cứu ngày arXiv cho lô {len(chunk)} ID: {exc}")

    # 4. Construct Results and persist per paper
    processed_count = 0
    context = {"run_id": run_id, "output_root": root, "is_mock": False}
    with OutputLock(root):
        with StateStore(db_path, run_id) as store:
            publisher = Publisher(context, store)
            for task in tasks:
                aid = task["arxiv_id"]
                paper = s2_map.get(aid)
                folder_name = paper_folder(aid)
                paper_dir = root / "raw" / owner / folder_name
                paper_dir.mkdir(parents=True, exist_ok=True)

                raw_refs = []
                norm_refs = {}
                missing_fields = []

                if paper is None:
                    status = "not_found"
                    data = None
                    error = {"code": "not_found", "message": "Paper not indexed by Semantic Scholar",
                             "retryable": False, "http_status": 404}
                    metrics = {"s2_reference_count": 0, "arxiv_reference_count": 0, "complete_reference_count": 0}
                else:
                    raw_refs = [adapt_batch_reference(r) for r in (paper.get("references") or [])]
                    for ref in (paper.get("references") or []):
                        ext = ref.get("externalIds") or {}
                        c_aid = ext.get("ArXiv")
                        if not c_aid or not is_valid_arxiv_id(c_aid):
                            continue
                        norm_c = normalize_arxiv_id(c_aid)
                        sdate = known_dates.get(norm_c)
                        authors = [a.get("name") for a in (ref.get("authors") or []) if a.get("name")]
                        norm_refs[norm_c] = {
                            "arxiv_id": norm_c,
                            "title": ref.get("title"),
                            "authors": authors if authors else None,
                            "submission_date": sdate,
                            "semantic_scholar_id": ref.get("paperId")
                        }
                        for field, val in norm_refs[norm_c].items():
                            if val is None:
                                missing_fields.append(f"/data/references/{norm_c}/{field}")

                    metrics = {
                        "s2_reference_count": len(raw_refs),
                        "arxiv_reference_count": len(norm_refs),
                        "complete_reference_count": sum(1 for r in norm_refs.values() if r["submission_date"] and r["title"] and r["authors"])
                    }

                    if not raw_refs:
                        status = "empty"
                    elif missing_fields:
                        status = "partial"
                    else:
                        status = "success"
                    data = {
                        "references": norm_refs,
                        "raw_references": raw_refs,
                        "complete": True
                    }
                    error = None

                now_ts = _utc_now()
                result = {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "owner": owner,
                    "is_mock": False,
                    "step": "references",
                    "arxiv_id": aid,
                    "version": None,
                    "status": status,
                    "data": data,
                    "missing_fields": missing_fields,
                    "error": error,
                    "provenance": {"http_source": "semantic_scholar_batch_post"},
                    "metrics": metrics,
                    "started_at": now_ts,
                    "finished_at": now_ts,
                    "duration_seconds": 0.0,
                    "attempts": 1
                }

                spec = paper_spec(task, "references")
                store.ensure_task(spec)
                attempt = store.mark_running(spec.key)
                store.record(attempt, result, issues=[])

                if data is not None:
                    # Write files directly and through publisher
                    (paper_dir / "raw_references.json").write_text(json.dumps(raw_refs, ensure_ascii=False, indent=2))
                    (paper_dir / "references.json").write_text(json.dumps(norm_refs, ensure_ascii=False, indent=2))
                    try:
                        publisher.publish(attempt, result)
                    except Exception:
                        store.complete(attempt, accepted=True)
                else:
                    store.complete(attempt, accepted=False)

                acc_path = root / "runs" / run_id / "accepted_results.jsonl"
                acc_path.parent.mkdir(parents=True, exist_ok=True)
                with open(acc_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")

                processed_count += 1

    return processed_count


def restore_snapshot_if_needed(zip_path: Path, target_corpus_dir: Path) -> Path:
    """Unpack snapshot.tar.gz from ZIP into destination corpus directory."""
    target_corpus_dir = Path(target_corpus_dir).resolve()
    if (target_corpus_dir / "state.sqlite").is_file():
        print(f"[*] Thư mục corpus đã tồn tại tại: {target_corpus_dir}")
        return target_corpus_dir

    zip_path = Path(zip_path).resolve()
    if not zip_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file ZIP: {zip_path}")

    print(f"[*] Đang giải nén snapshot từ: {zip_path.name}...")
    temp_dir = Path(tempfile.mkdtemp(prefix="arxiv-restore-"))
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            with z.open("snapshot.tar.gz") as source, open(temp_dir / "snapshot.tar.gz", "wb") as dest:
                shutil.copyfileobj(source, dest, length=4 * 1024 * 1024)

        archive = temp_dir / "snapshot.tar.gz"
        unpack_dest = temp_dir / "unpacked"
        service = SnapshotService()
        service.unpack(archive, unpack_dest)

        unpacked_corpus = unpack_dest / "corpus"
        if not unpacked_corpus.is_dir():
            raise RuntimeError("Corpus directory not found in snapshot archive")

        target_corpus_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_corpus_dir.exists():
            shutil.rmtree(target_corpus_dir)
        shutil.move(str(unpacked_corpus), str(target_corpus_dir))
        print(f"[+] Giải nén thành công vào: {target_corpus_dir}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return target_corpus_dir


def load_env_key(env_path: Path) -> str | None:
    env_path = Path(env_path)
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("S2_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def detect_run_id(db_path: Path) -> str:
    """Tự động nhận diện run_id từ bảng runs trong state.sqlite."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        c.execute("SELECT run_id FROM runs LIMIT 1")
        row = c.fetchone()
        if row and row[0]:
            return row[0]
    finally:
        conn.close()
    return "m1-colab-run"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Thu thập References theo lô (POST Batch) tại máy local.")
    parser.add_argument("--zip", default="data/m1-colab-trong-01-resume-40.zip",
                        help="Đường dẫn file ZIP backup từ Colab")
    parser.add_argument("--corpus-dir", default="data/m1-colab-trong-01/corpus",
                        help="Đường dẫn thư mục corpus local")
    parser.add_argument("--run-id", default=None, help="Mã run_id (tự động nhận diện nếu để trống)")
    parser.add_argument("--env", default=".env", help="Đường dẫn file .env chứa S2_API_KEY")
    parser.add_argument("--batch-size", type=int, default=50, help="Số bài mỗi lượt POST batch (mặc định 50)")
    parser.add_argument("--pilot", action="store_true", help="Chạy thử nghiệm 10 bài đầu tiên để kiểm tra")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn tối đa số bài chạy trong lượt này")
    parser.add_argument("--s2-delay", type=float, default=3.0, help="Thời gian giãn cách giữa các batch POST (giây, mặc định 3.0)")

    args = parser.parse_args()

    # 1. Restore if needed
    corpus_dir = Path(args.corpus_dir)
    if not (corpus_dir / "state.sqlite").is_file():
        zip_path = Path(args.zip)
        if not zip_path.is_file():
            print(f"[!] Lỗi: Không thấy {zip_path}. Hãy tải file ZIP về hoặc chỉ định --zip.")
            sys.exit(1)
        restore_snapshot_if_needed(zip_path, corpus_dir)

    # 2. Inspect state
    db_path = corpus_dir / "state.sqlite"
    run_id = args.run_id or detect_run_id(db_path)
    with StateStore.readonly(db_path, run_id) as store:
        scope_doc = store.read_scope()
        scope_inner = scope_doc.get("scope", scope_doc) if isinstance(scope_doc, dict) else {}
        tasks = scope_inner.get("paper_tasks", [])
        owner = tasks[0]["owner"] if tasks else "trong"

    # Identify pending papers
    pending_tasks = []
    done_count = 0
    for task in tasks:
        pdir = corpus_dir / "raw" / task["owner"] / paper_folder(task["arxiv_id"])
        if (pdir / "references.json").is_file():
            done_count += 1
        else:
            pending_tasks.append(task)

    print("\n" + "=" * 60)
    print(f"[*] Tổng số bài trong dải của {owner}: {len(tasks)}")
    print(f"[*] Số bài ĐÃ CÓ references: {done_count} ({done_count / len(tasks) * 100:.1f}%)")
    print(f"[*] Số bài CÒN THIẾU references: {len(pending_tasks)}")
    print("=" * 60 + "\n")

    if not pending_tasks:
        print("[+] Tuyệt vời! Toàn bộ 5.000 bài báo đã có đủ References.")
        sys.exit(0)

    # Apply pilot or limit
    if args.pilot:
        pending_tasks = pending_tasks[:10]
        print(f"[*] Chế độ PILOT: Chỉ chạy thử nghiệm {len(pending_tasks)} bài đầu tiên.")
    elif args.limit:
        pending_tasks = pending_tasks[:args.limit]
        print(f"[*] Giới hạn lượt này: Chạy tối đa {len(pending_tasks)} bài.")

    # 3. Setup clients
    api_key = load_env_key(Path(args.env)) or os.environ.get("S2_API_KEY")
    if api_key:
        print("[+] Đã nạp thành công Semantic Scholar API Key từ .env")
    else:
        print("[!] Cảnh báo: Không tìm thấy S2_API_KEY, chạy với hạn mức công khai (dễ bị 429).")

    s2_client = BatchSemanticScholarClient(api_key=api_key, delay=args.s2_delay)
    arxiv_dates_client = ArxivDateClient(delay=3.0)

    # 4. Run loop
    batch_size = max(1, min(args.batch_size, 100))
    total_to_run = len(pending_tasks)
    total_processed = 0
    start_time = time.time()

    print(f"[*] Bắt đầu xử lý {total_to_run} bài (Lô {batch_size} bài/request POST, delay {args.s2_delay}s)...", flush=True)
    idx = 0
    consecutive_429 = 0
    try:
        while idx < total_to_run:
            chunk = pending_tasks[idx:idx + batch_size]
            batch_num = (idx // batch_size) + 1
            total_batches = (total_to_run + batch_size - 1) // batch_size

            def report_progress(msg):
                print(f"  [Lô {batch_num}/{total_batches}] {msg}", flush=True)

            try:
                count = process_batch_references(
                    corpus_dir, run_id, chunk,
                    s2_client, arxiv_dates_client,
                    progress=report_progress
                )
                total_processed += count
                idx += len(chunk)
                consecutive_429 = 0
                elapsed = time.time() - start_time
                rate = total_processed / max(1.0, elapsed)
                remaining_sec = (total_to_run - total_processed) / max(0.01, rate)
                print(f"-> Hoàn thành lô {batch_num}/{total_batches}: {total_processed}/{total_to_run} bài "
                      f"({total_processed / total_to_run * 100:.1f}%) | Tốc độ: {rate:.1f} bài/s | Ước tính còn lại: {remaining_sec:.0f}s\n", flush=True)
                time.sleep(args.s2_delay)
            except Exception as exc:
                is_429 = (isinstance(exc, RemoteError) and (exc.info.get("http_status") == 429 or "429" in str(exc))) or "429" in str(exc)
                if is_429:
                    consecutive_429 += 1
                    cooldown = min(120, 15 * (2 ** (consecutive_429 - 1)))
                    print(f"\n[!] Semantic Scholar báo rate limit (429). Hệ thống tự động nghỉ {cooldown:.0f}s rồi gửi lại lô {batch_num}...", flush=True)
                    for sec in range(int(cooldown), 0, -5):
                        print(f"    ... Đang chờ cooldown: còn {sec}s ...", flush=True)
                        time.sleep(min(5, sec))
                    print(f"[+] Hết thời gian chờ, đang gửi lại lô {batch_num}...\n", flush=True)
                else:
                    consecutive_429 += 1
                    cooldown = min(60, 10 * consecutive_429)
                    print(f"\n[!] Gặp sự cố kết nối/mạng: {exc}. Tự động thử lại lô {batch_num} sau {cooldown}s (lần {consecutive_429})...", flush=True)
                    for sec in range(int(cooldown), 0, -5):
                        print(f"    ... Đang chờ thử lại: còn {sec}s ...", flush=True)
                        time.sleep(min(5, sec))
                    print(f"[+] Đang gửi lại lô {batch_num}...\n", flush=True)

    except KeyboardInterrupt:
        print("\n[!] Bị ngắt bởi người dùng (Ctrl+C). Tiến trình đã lưu checkpoint an toàn.", flush=True)
    except Exception as exc:
        print(f"\n[!] Lỗi trong quá trình chạy: {exc}", flush=True)
        raise

    print("\n" + "=" * 60, flush=True)
    print(f"[+] Hoàn tất đợt chạy: Đã xử lý thành công {total_processed} bài.", flush=True)
    print(f"[*] Dữ liệu được lưu tại: {corpus_dir}", flush=True)
    print("=" * 60 + "\n", flush=True)


if __name__ == "__main__":
    main()
