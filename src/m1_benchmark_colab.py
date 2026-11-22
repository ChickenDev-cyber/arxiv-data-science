#!/usr/bin/env python3
"""Milestone 1 — Crawling Benchmark & Metrics Evaluation Runner.

Executable both locally and on Google Colab CPU via Colab CLI:
    /home/trong/.local/bin/colab run --timeout 3600 src/m1_benchmark_colab.py
"""
import argparse
import base64
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

BUNDLE_B64 = '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'


def safe_fmt(v, fmt=".1f", scale=1.0, suffix="", na="N/A"):
    if v is None: return na
    try: return f"{v * scale:{fmt}}{suffix}"
    except Exception: return str(v)


def ensure_deps():
    for pkg in ['psutil', 'matplotlib']:
        try:
            __import__(pkg)
        except ImportError:
            print(f'Installing {pkg}...')
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', pkg], check=True)


def inspect_hardware():
    import psutil
    print('=' * 75)
    print('1. KIEM TRA MOI TRUONG PHAN CUNG (COLAB CPU)')
    print('=' * 75)
    gpu_detected = False
    try:
        res = subprocess.run(['nvidia-smi'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            gpu_detected = True
    except Exception:
        pass

    if gpu_detected:
        print("CANH BAO: Phat hien GPU! De bai yeu cau benchmark tren CPU-only instance.")
    else:
        print("Moi truong hop chuan: CPU-only (None) - Dat chuan Rubric de bai.")

    ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    print(f'CPU Cores: {os.cpu_count()}')
    print(f'RAM Tong: {ram_gb:.2f} GB')
    print(f'OS / Kernel: {platform.platform()}')
    print(f'Python Version: {sys.version.split()[0]}')


def resolve_s2_key():
    print('\n' + '=' * 75)
    print('2. XAC THUC SEMANTIC SCHOLAR API KEY')
    print('=' * 75)
    key = os.environ.get('S2_API_KEY')
    if not key:
        try:
            from google.colab import userdata
            key = userdata.get('S2_API_KEY')
        except Exception:
            pass

    if not key and Path('.env').exists():
        for line in Path('.env').read_text().splitlines():
            if line.startswith('S2_API_KEY='):
                key = line.split('=', 1)[1].strip().strip('"').strip("'")
                break

    if key:
        os.environ['S2_API_KEY'] = key
        try:
            headers = {'x-api-key': key, 'User-Agent': 'arxiv-data-science/T4 (academic course project)', 'Content-Type': 'application/json'}
            req = Request('https://api.semanticscholar.org/graph/v1/paper/batch?fields=paperId',
                          data=json.dumps({'ids': ['ARXIV:2106.15928']}).encode(), headers=headers)
            with urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    print("S2_API_KEY: Hop le va dang hoat dong (HTTP 200 OK)!")
                else:
                    print(f'S2 API phan hoi HTTP {resp.status}')
        except Exception as e:
            print(f'Canh bao preflight check: {e}')
    else:
        print("Chay khong co S2_API_KEY. S2 API co the bi nghen toc do (HTTP 429).")
    return key


def setup_code_and_tools():
    print('\n' + '=' * 75)
    print('3. THIET LAP MA NGUON PIPELINE & ADAPTERS')
    print('=' * 75)
    current_dir = Path.cwd()
    if (current_dir / 'src/arxiv_pipeline').exists():
        code_root = current_dir
    elif Path('/content/arxiv-repo/src/arxiv_pipeline').exists():
        code_root = Path('/content/arxiv-repo')
    else:
        print("Dang clone ma nguon tu GitHub...")
        subprocess.run(['git', 'clone', 'https://github.com/ChickenDev-cyber/arxiv-data-science.git', '/content/arxiv-repo'], check=True)
        code_root = Path('/content/arxiv-repo')

    print(f'Thu muc ma nguon: {code_root}')

    req_file = code_root / 'requirements.txt'
    if not req_file.exists():
        req_file = code_root / 'src/requirements.txt'
    if req_file.exists():
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(req_file)], check=True)

    tools_dir = code_root / 'tools'
    tools_dir.mkdir(exist_ok=True)
    if not (tools_dir / 'm1_batch_experiment.py').exists() or not (tools_dir / 'm1_batch_support.py').exists():
        print("Dang dong bo module batch adapter vao tools/...")
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(BUNDLE_B64)))
        z.extractall(tools_dir)
        print("Da cap nhat day du cac cong cu batch benchmark vao tools/!")

    sys.path.insert(0, str(code_root / 'src'))
    sys.path.insert(0, str(code_root / 'tools'))
    os.environ['PYTHONPATH'] = f"{code_root / 'src'}:{code_root / 'tools'}:{os.environ.get('PYTHONPATH', '')}"

    from arxiv_pipeline.contracts import SCHEMA_VERSION
    print(f'Pipeline Contracts Schema Version: {SCHEMA_VERSION}')
    return code_root


def run_benchmark(code_root, owner, count, variant, batch_size, run_id, work_root):
    print('\n' + '=' * 75)
    print(f'4. THUC THI BENCHMARK M1 — OWNER: {owner.upper()} | VARIANT: {variant.upper()}')
    print('=' * 75)
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    corpus_dir = work_root / 'corpus'

    benchmark_script = code_root / 'tools/m1_batch_experiment.py'
    cmd = [
        sys.executable, str(benchmark_script),
        '--variant', variant,
        '--owner', owner,
        '--count', str(count),
        '--batch-size', str(batch_size),
        '--run-id', run_id,
        '--root', str(corpus_dir)
    ]
    print(f'Lenh thuc thi: {" ".join(cmd)}')
    print('-' * 75)

    start_clock = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=code_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in proc.stdout:
        l = line.strip()
        if any(k in l for k in ['Bai', 'Bài', 'bat dau', 'bắt đầu', 'success', 'POST batch', 'References', 'Tong ket', 'Tổng kết', 'metrics', 'cohort_complete']):
            print(l, flush=True)

    proc.wait()
    elapsed = time.monotonic() - start_clock
    print('-' * 75)
    print(f'Qua trinh thu thap hoan tat trong {elapsed:.1f}s (~{elapsed/60:.2f} phut)!')
    print(f'Ma thoat (Exit code): {proc.returncode}')
    return corpus_dir


def print_metrics_table(corpus_dir, owner, variant):
    print('\n' + '=' * 75)
    print(f'5. BANG TONG HOP CHI SO BENCHMARK — THANH VIEN: {owner.upper()} ({variant.upper()})')
    print('=' * 75)
    metrics_file = corpus_dir / 'experiment-metrics.json'
    assert metrics_file.exists(), f'Khong tim thay file {metrics_file}!'

    metrics = json.loads(metrics_file.read_text(encoding='utf-8'))
    bench = metrics.get('benchmark', {})
    timing = metrics.get('timing', {})
    assign = metrics.get('assignment', {})
    refs = assign.get('references', {})
    sources = assign.get('source_sizes', {})
    versions = assign.get('versions', {})

    def fmt_row(cat, name, val, desc):
        print(f"  [{cat:15s}] {name:42s} : {val:22s} | {desc}")

    print("\n--- 1. RUNNING TIME (Thoi gian thuc thi) ---")
    wall_s = safe_fmt(bench.get("wall_seconds"), ".2f", suffix="s")
    wall_m = safe_fmt(bench.get("wall_seconds"), ".2f", scale=1/60, suffix="m")
    fmt_row("Running Time", "Tong thoi gian toan trinh (Wall Time)", f"{wall_s} (~{wall_m})", "End-to-end tu discovery den export")
    fmt_row("Running Time", "Thoi gian truy van danh muc (Discovery)", safe_fmt(timing.get("discovery_seconds"), ".3f", suffix="s"), "arXiv Atom search API")
    fmt_row("Running Time", "Thoi gian trung binh / 1 bai (Mean Latency)", safe_fmt(timing.get("mean_paper_seconds"), ".2f", suffix="s/bai"), "Trung binh metadata + source + refs")
    fmt_row("Running Time", "Bai nhanh nhat / lau nhat (Min / Max)", f"{safe_fmt(timing.get('min_paper_seconds'), '.1f', suffix='s')} / {safe_fmt(timing.get('max_paper_seconds'), '.1f', suffix='s')}", "Do tre don le tung bai")

    print("\n--- 2. MEMORY FOOTPRINT (Bo nho & Luu tru) ---")
    fmt_row("Memory Footprint", "Peak RAM (Maximum RSS)", safe_fmt(bench.get("peak_ram_bytes"), ".2f", scale=1/(1024**2), suffix=" MiB"), "Lay mau qua psutil (chu ky 50ms)")
    fmt_row("Memory Footprint", "Mean RAM Consumption", safe_fmt(bench.get("mean_ram_bytes"), ".2f", scale=1/(1024**2), suffix=" MiB"), "Muc tieu thu RAM trung binh on dinh")
    fmt_row("Memory Footprint", "Peak Disk Storage", safe_fmt(bench.get("peak_disk_bytes"), ".2f", scale=1/(1024**2), suffix=" MiB"), "Ghi nhan tai cac moc tai & giai nen")
    fmt_row("Memory Footprint", "Dung luong Corpus cuoi (Final Output)", safe_fmt(bench.get("final_output_bytes"), ".2f", scale=1/(1024**2), suffix=" MiB"), "Du lieu sach sau khi loc bo rac")

    print("\n--- 3. DATA & CRAWLING STATISTICS (Chat luong Du lieu) ---")
    completed = assign.get("papers_complete", 0)
    total_pub = assign.get("publications", 0)
    fmt_row("Data Statistics", "So bai yeu cau / Hoan tat", f"{completed} / {total_pub} bai", f"Ty le thanh cong: {safe_fmt(assign.get('paper_success_rate'), '.1f', scale=100, suffix='%')}")
    fmt_row("Data Statistics", "Do bao phu phien ban (Versions)", f"{versions.get('covered', 0)} / {versions.get('expected', 0)} phien ban", f"Unsupported: {versions.get('unsupported', 0)}, Unresolved: {versions.get('unresolved', 0)}")
    b_val = sources.get("mean_before_filter_bytes")
    a_val = sources.get("mean_retained_bytes")
    reduc_str = f"{(1 - (a_val / max(1, b_val)))*100:.1f}%" if b_val and a_val else "N/A"
    before_str = safe_fmt(b_val, ".1f", scale=1/1024, suffix=" KiB")
    after_str = safe_fmt(a_val, ".1f", scale=1/1024, suffix=" KiB")
    fmt_row("Data Statistics", "Dung luong TeX Truoc / Sau loc", f"{before_str} -> {after_str}", f"Giam {reduc_str} dung luong nho loc anh")
    fmt_row("Data Statistics", "So bai lay References thanh cong", f"{refs.get('fully_fetched_papers', 0)} / {total_pub} bai", f"Ty le query S2: {safe_fmt(refs.get('query_success_rate'), '.1f', scale=100, suffix='%')}")
    fmt_row("Data Statistics", "Tong so trich dan thu thap", safe_fmt(refs.get("total_raw_references_complete_lists"), ",d", suffix=" refs"), f"Trung binh: {safe_fmt(refs.get('mean_all_references_per_fully_fetched_paper'), '.1f', suffix=' refs/bai')}")
    fmt_row("Data Statistics", "Trich dan arXiv (Du metadata)", safe_fmt(refs.get("metadata_complete_arxiv_entries"), ",d", suffix=" bai"), f"Macro/Micro: {safe_fmt(refs.get('metadata_macro_success_rate'), '.1f', scale=100, suffix='%')} / {safe_fmt(refs.get('metadata_micro_success_rate'), '.1f', scale=100, suffix='%')}")

    print(f"\nHTTP Audit: {metrics.get('http_attempts_actual', 0)} requests. Phan phoi: {metrics.get('http_counts')}")
    return metrics


def generate_figures(corpus_dir, metrics, owner, variant):
    print('\n' + '=' * 75)
    print('6. XUAT BIEU DO KHOA HOC (JUPYTER BOOK FIGURES)')
    print('=' * 75)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("Matplotlib/Numpy chua duoc cai dat, bo qua tao anh.")
        return

    timing = metrics.get('timing', {})
    sources = metrics.get('assignment', {}).get('source_sizes', {})
    refs = metrics.get('assignment', {}).get('references', {})

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'MILESTONE 1 PIPELINE BENCHMARK ANALYSIS — {owner.upper()} ({variant.upper()} MODE)', fontsize=16, fontweight='bold', y=0.98)

    latencies = [v for v in timing.get('per_paper_seconds', {}).values() if v is not None]
    ax1 = axes[0, 0]
    if latencies:
        ax1.hist(latencies, bins=min(20, max(5, len(latencies))), color='#3498db', edgecolor='black', alpha=0.8)
        ax1.axvline(np.mean(latencies), color='red', linestyle='--', linewidth=2, label=f'Trung binh: {np.mean(latencies):.1f}s')
        ax1.axvline(np.median(latencies), color='green', linestyle=':', linewidth=2, label=f'Trung vi: {np.median(latencies):.1f}s')
        ax1.set_title('Phan phoi Thoi gian Xu ly Tung Bai (Paper Latency)', fontsize=12, fontweight='bold')
        ax1.set_xlabel('Thoi gian (giay)')
        ax1.set_ylabel('So luong bai bao')
        ax1.legend()
    else:
        ax1.text(0.5, 0.5, 'Chua co du lieu latency', ha='center')

    ax2 = axes[0, 1]
    before_kb = (sources.get('mean_before_filter_bytes') or 0) / 1024
    after_kb = (sources.get('mean_retained_bytes') or 0) / 1024
    reduc = (1 - (after_kb / max(1, before_kb))) * 100 if before_kb else 0
    bars = ax2.bar(['Truoc khi loc\n(Raw Source)', 'Sau khi loc\n(TeX/Bib Retained)'], [before_kb, after_kb], color=['#e74c3c', '#2ecc71'], width=0.45, edgecolor='black')
    for b in bars:
        y = b.get_height()
        ax2.text(b.get_x() + b.get_width()/2.0, y + (max(1, before_kb)*0.02), f"{y:.1f} KiB", ha="center", va="bottom", fontweight="bold")
    ax2.set_title(f'Dung luong Trung binh Moi Bai (Giam {reduc:.1f}%)', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Dung luong trung binh (KiB)')
    ax2.set_ylim(0, max(10, before_kb * 1.2))

    ax3 = axes[1, 0]
    total_raw = refs.get('total_raw_references_complete_lists') or 0
    arxiv_entries = refs.get('metadata_complete_arxiv_entries') or 0
    non_arxiv = max(0, total_raw - arxiv_entries)
    if total_raw > 0:
        ax3.pie([arxiv_entries, non_arxiv], labels=['Trich dan arXiv\n(Du metadata)', 'Trich dan Non-arXiv\n(Khac)'],
                autopct='%1.1f%%', colors=['#9b59b6', '#95a5a6'], startangle=140, explode=(0.05, 0), shadow=True)
        ax3.set_title(f'Co cau Trich dan ({total_raw:,} tong trich dan)', fontsize=12, fontweight='bold')
    else:
        ax3.text(0.5, 0.5, 'Chua co du lieu trich dan', ha='center')

    ax4 = axes[1, 1]
    http_c = metrics.get('http_counts', {})
    if http_c:
        labels = [k.replace('|', ' ') for k in http_c.keys()]
        vals = list(http_c.values())
        y_pos = np.arange(len(labels))
        bars4 = ax4.barh(y_pos, vals, color='#f39c12', edgecolor='black')
        ax4.set_yticks(y_pos)
        ax4.set_yticklabels(labels, fontsize=9)
        ax4.invert_yaxis()
        for bar in bars4:
            xval = bar.get_width()
            ax4.text(xval + max(vals)*0.01, bar.get_y() + bar.get_height()/2.0, f'{int(xval)}', ha='left', va='center', fontweight='bold')
        ax4.set_title(f'Phan phoi HTTP Requests ({metrics.get("http_attempts_actual", 0)} requests)', fontsize=12, fontweight='bold')
        ax4.set_xlabel('So luong yeu cau (Requests)')
    else:
        ax4.text(0.5, 0.5, 'Chua co du lieu HTTP', ha='center')

    plt.tight_layout()
    fig_out = corpus_dir / 'benchmark_figures.png'
    plt.savefig(fig_out, dpi=300, bbox_inches='tight')
    plt.close()
    print(f'Da xuat bieu do khoa hoc: {fig_out}')


def package_and_save(work_root, corpus_dir, run_id, save_to_drive, drive_output_dir):
    print('\n' + '=' * 75)
    print('7. DONG GOI ARTIFACTS & DONG BO')
    print('=' * 75)
    import hashlib
    archive_path = work_root / f'{run_id}-result.zip'
    print(f'Dang dong goi ket qua vao {archive_path.name}...')

    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in corpus_dir.rglob('*'):
            if f.is_file():
                z.write(f, arcname=Path('corpus') / f.relative_to(corpus_dir))

    with zipfile.ZipFile(archive_path, 'r') as z:
        assert z.testzip() is None, "Loi tinh toan ven file zip!"

    sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (work_root / f"{archive_path.name}.sha256").write_text(sha256 + "\n")
    zip_size_mb = archive_path.stat().st_size / (1024 ** 2)
    print(f'Da tao file ket qua: {archive_path} ({zip_size_mb:.2f} MiB)')
    print(f'SHA-256: {sha256}')

    if save_to_drive:
        try:
            drive_mount = Path('/content/drive')
            if not drive_mount.exists() or not os.path.ismount(str(drive_mount)):
                from google.colab import drive
                drive.mount(str(drive_mount))

            drive_dir = Path(drive_output_dir)
            drive_dir.mkdir(parents=True, exist_ok=True)
            print(f'Dang sao luu len Google Drive: {drive_dir}...')
            shutil.copy2(archive_path, drive_dir / archive_path.name)
            shutil.copy2(work_root / f"{archive_path.name}.sha256", drive_dir / f"{archive_path.name}.sha256")
            shutil.copy2(corpus_dir / 'experiment-metrics.json', drive_dir / 'experiment-metrics.json')
            fig_file = corpus_dir / 'benchmark_figures.png'
            if fig_file.exists():
                shutil.copy2(fig_file, drive_dir / 'benchmark_figures.png')
            print(f'DA LUU TRU TOAN BO KET QUA VAO GOOGLE DRIVE: {drive_dir}')
        except Exception as e:
            print(f'Khong the tu dong sao luu len Drive: {e}')


def main():
    parser = argparse.ArgumentParser(description='Milestone 1 Benchmark Runner')
    parser.add_argument('--owner', default='trong', choices=['trong', 'khang', 'hoang', 'man-nhi', 'huynh-nhi'])
    parser.add_argument('--count', type=int, default=100, help='So bai bao can crawl & benchmark')
    parser.add_argument('--variant', default='batch', choices=['batch', 'baseline'], help='batch (S2 POST) hoac baseline (GET)')
    parser.add_argument('--batch-size', type=int, default=100)
    parser.add_argument('--save-to-drive', action='store_true', default=False)
    parser.add_argument('--drive-dir', default=None)
    args = parser.parse_args()

    ensure_deps()
    run_id = f'm1-bench-{args.owner}-{args.variant}-{args.count}'
    work_root = Path(f'/content/m1-work-{args.owner}') if Path('/content').exists() else Path(f'/tmp/m1-work-{args.owner}')
    drive_dir = args.drive_dir or f'/content/drive/MyDrive/arXiv-data/milestone 1/benchmarks/{args.owner}/{run_id}'

    inspect_hardware()
    resolve_s2_key()
    code_root = setup_code_and_tools()
    corpus_dir = run_benchmark(code_root, args.owner, args.count, args.variant, args.batch_size, run_id, work_root)
    metrics = print_metrics_table(corpus_dir, args.owner, args.variant)
    generate_figures(corpus_dir, metrics, args.owner, args.variant)
    package_and_save(work_root, corpus_dir, run_id, args.save_to_drive, drive_dir)
    print("\nTAT CA CAC BUOC DA HOAN TAT THANH CONG!")


if __name__ == '__main__':
    main()
