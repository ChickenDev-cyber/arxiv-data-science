"""Benchmark local theo mẫu cố định trong dải Khang; không đổi backend production."""

import argparse
import gzip
import hashlib
import json
import os
import resource
import shutil
import subprocess
import tarfile
import threading
import time
from collections import Counter
from pathlib import Path

from arxiv_pipeline.gathering import SourceGatherer
from arxiv_pipeline.hierarchy import build_hierarchy
from arxiv_pipeline.standardization import clean_latex, normalize_math

ROOT = Path("data/module2-range-benchmark-2026-11-13")


def candidates():
    # Trải theo ID, không chọn theo độ dễ parse hoặc số câu; chỉ xét bản v1.
    return ([f"2506.{20788 + round(i * (24000 - 20788) / 69):05d}" for i in range(70)]
            + [f"2507.{1 + round(i * (1658 - 1) / 29):05d}" for i in range(30)])


def download():
    archive_dir = ROOT / "archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for paper in candidates():
        destination = archive_dir / f"{paper}v1.source"
        previous = Path(f"/tmp/khang-{paper}v1.source")
        start = time.perf_counter()
        if destination.exists():
            status = "cached"
        elif previous.exists():
            shutil.copyfile(previous, destination)
            status = "reused"
        else:
            temp = destination.with_suffix(".part")
            result = subprocess.run([
                "curl", "-sS", "-L", "--max-time", "60", "--max-filesize", "67108864",
                "-A", "arxiv-data-science coursework milestone2 CPU benchmark",
                "-o", str(temp), "-w", "%{http_code}",
                f"https://export.arxiv.org/src/{paper}v1",
            ], capture_output=True, text=True)
            status = "downloaded" if result.returncode == 0 and result.stdout == "200" else f"download_error:{result.returncode}:{result.stdout}"
            if status == "downloaded":
                temp.rename(destination)
            else:
                temp.unlink(missing_ok=True)
            time.sleep(3)  # Request tuần tự, không tạo tải đồng thời lên arXiv.
        row = dict(paper=paper, status=status, seconds=time.perf_counter()-start)
        if destination.exists():
            row.update(bytes=destination.stat().st_size,
                       sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
        rows.append(row)
        (ROOT / "downloads.json").write_text(json.dumps(rows, indent=2))
        print(json.dumps(row), flush=True)


def extract(archive, dest):
    try:
        with tarfile.open(archive) as source:
            total = 0
            for member in source:
                if not member.isfile() or Path(member.name).suffix.lower() not in {".tex", ".bib"}:
                    continue
                target = (dest / member.name).resolve()
                total += member.size
                if not target.is_relative_to(dest.resolve()) or member.size > 20_000_000 or total > 100_000_000:
                    raise ValueError("unsafe or oversized source")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
    except tarfile.ReadError:
        raw = archive.read_bytes()
        if raw[:2] == b"\x1f\x8b":
            with gzip.open(archive, "rb") as stream:
                raw = stream.read(100_000_001)
            if len(raw) > 100_000_000:
                raise ValueError("oversized decompressed source")
        if raw.startswith(b"%PDF-"):
            return
        if b"\\documentclass" not in raw:
            raise ValueError("unsupported source")
        (dest / "main.tex").write_bytes(raw)


def prepare():
    papers, blocks = [], []
    start = time.perf_counter()
    for paper in candidates():
        archive = ROOT / "archives" / f"{paper}v1.source"
        if not archive.exists():
            papers.append(dict(paper=paper, status="download_missing"))
            continue
        dest = ROOT / "corpus" / paper / "v1"
        dest.mkdir(parents=True, exist_ok=True)
        clock = time.perf_counter()
        first = len(blocks)
        try:
            extract(archive, dest)
            report = SourceGatherer().inspect(dest, paper, 1)
            def capture(raw):
                text = normalize_math(clean_latex(raw))
                if text.strip():
                    blocks.append(dict(paper=paper, text=text))
                # Chỉ thu thập prose; chưa chạy NLP trong giai đoạn chuẩn bị.
                return []
            tree = build_hierarchy(report.source, prose_processor=capture)
            papers.append(dict(paper=paper, status=report.source["status"],
                blocks=len(blocks)-first, seconds=time.perf_counter()-clock,
                missing_includes=report.missing_includes,
                non_sentence_nodes=dict(Counter(n['kind'] for n in tree['nodes']))))
        except Exception as exc:
            del blocks[first:]  # Không đưa phần parse dang dở vào workload.
            papers.append(dict(paper=paper, status="prepare_error", error=f"{type(exc).__name__}: {exc}"))
        print(json.dumps(papers[-1]), flush=True)
    (ROOT / "workload.json").write_text(json.dumps(blocks, ensure_ascii=False))
    (ROOT / "preparation.json").write_text(json.dumps(dict(
        papers=papers, seconds=time.perf_counter()-start, blocks=len(blocks),
        characters=sum(len(b['text']) for b in blocks)), indent=2))


def run(args):
    import psutil
    from arxiv_pipeline.standardization.segmentation_utils import masked_view, split_at_ends
    from arxiv_pipeline.standardization.neural_adapter import sat_model, spacy_model

    ROOT.mkdir(parents=True, exist_ok=True)
    blocks = json.loads((ROOT / "workload.json").read_text())
    papers = list(dict.fromkeys(b['paper'] for b in blocks))
    if args.limit_papers and len(papers) > args.limit_papers:
        # Pilot cũng trải trên toàn dải, không chỉ lấy các bài đầu tiên.
        selected = {papers[round(i*(len(papers)-1)/(args.limit_papers-1))] for i in range(args.limit_papers)} if args.limit_papers > 1 else {papers[0]}
        blocks = [b for b in blocks if b['paper'] in selected]
        papers = [p for p in papers if p in selected]
    process = psutil.Process()
    peak = [process.memory_info().rss]
    stop = threading.Event()
    def sample():
        while not stop.wait(.05):
            peak[0] = max(peak[0], process.memory_info().rss)
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    baseline_rss = peak[0]
    load_start = time.perf_counter()
    model = sat_model() if args.backend == "sat" else spacy_model()
    load_seconds = time.perf_counter()-load_start
    # Warmup tách riêng khỏi đo inference và không chỉnh threshold/model.
    if args.backend == 'sat':
        model.split("This is a warmup. Another sentence follows.")
    else:
        model("This is a warmup. Another sentence follows.")
    if args.bucket:
        blocks.sort(key=lambda b: len(b['text']))
    digest = hashlib.sha256()
    sentences = 0
    inference_start = time.perf_counter()
    for index in range(0, len(blocks), args.batch):
        batch = blocks[index:index+args.batch]
        views_regions = [masked_view(b['text']) for b in batch]
        views = [v for v, _ in views_regions]
        if args.backend == 'sat':
            outputs = list(model.split(views, batch_size=args.internal_batch or args.batch))
            ends_batch = []
            for view, segments in zip(views, outputs):
                offset, ends = 0, []
                for segment in segments:
                    begin = view.find(segment, offset)
                    if begin == -1 or view[offset:begin].strip():
                        raise ValueError('SaT alignment failed')
                    offset = begin + len(segment)
                    end = offset
                    while end > begin and view[end-1].isspace():
                        end -= 1
                    ends.append(end)
                if view[offset:].strip():
                    raise ValueError('SaT omitted content')
                ends_batch.append(ends)
        else:
            ends_batch = [[s.end_char for s in doc.sents] for doc in model.pipe(views, batch_size=args.batch)]
        assert len(ends_batch) == len(batch)
        for block, (_, regions), ends in zip(batch, views_regions, ends_batch):
            output = split_at_ends(block['text'], ends, regions)
            assert ''.join(''.join(output).split()) == ''.join(block['text'].split())
            sentences += len(output)
            digest.update(json.dumps([block['paper'], block['text'], output], ensure_ascii=False).encode())
        if index % (args.batch*20) == 0:
            print(f"{args.backend} batch={args.batch}: {min(index+args.batch,len(blocks))}/{len(blocks)} blocks; {time.perf_counter()-inference_start:.1f}s", flush=True)
    elapsed = time.perf_counter()-inference_start
    stop.set()
    thread.join()
    peak_bytes = max(peak[0], resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024)
    result = dict(backend=args.backend, batch=args.batch, bucket=args.bucket,
        internal_batch=(args.internal_batch or args.batch) if args.backend == 'sat' else None,
        papers=len(papers), paper_ids=papers, blocks=len(blocks),
        characters=sum(len(b['text']) for b in blocks), sentences=sentences,
        model_load_seconds=load_seconds, inference_seconds=elapsed,
        seconds_per_paper=elapsed/len(papers), hours_for_40000_versions=elapsed/len(papers)*40000/3600,
        peak_rss_mib=peak_bytes/2**20, baseline_rss_mib=baseline_rss/2**20,
        output_sha256=digest.hexdigest(), cpu_count=os.cpu_count())
    suffix = f"{args.backend}-b{args.batch}-{'bucket' if args.bucket else 'ordered'}-n{len(papers)}"
    if args.internal_batch:
        suffix += f"-ib{args.internal_batch}"
    (ROOT / f"{suffix}.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['download', 'prepare', 'run'])
    parser.add_argument('--backend', choices=['sat', 'spacy'], default='spacy')
    parser.add_argument('--batch', type=int, default=32)
    parser.add_argument('--bucket', action='store_true')
    parser.add_argument('--internal-batch', type=int, default=0)
    parser.add_argument('--limit-papers', type=int, default=0)
    parser.add_argument('--root', default=str(ROOT))
    args = parser.parse_args()
    ROOT = Path(args.root)
    {'download': download, 'prepare': prepare, 'run': lambda: run(args)}[args.stage]()
