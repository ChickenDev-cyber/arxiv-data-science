"""Linux-only supervisor for a real terminal Ctrl+C regression test."""

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in ("Z", "X")
    except FileNotFoundError:
        return False


def main(root, detached_child=False):
    # Adopt and reap orphaned test descendants instead of leaving zombies to
    # the host's PID 1. This setting is confined to this supervisor subprocess.
    if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise RuntimeError("Cannot supervise test descendants")
    ready = root / "ready.json"
    child_code = """
import json, os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'group': os.getpgrp()}))
time.sleep(120)
"""
    runner = root / "runner.py"
    runner.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}, {str(ready)!r}], "
        f"start_new_session={detached_child!r})\n"
        "time.sleep(120)\n"
    )
    wrapper = root / "wrapper.py"
    wrapper.write_text(
        "import sys, subprocess\nfrom pathlib import Path\n"
        "from arxiv_pipeline.benchmark import runner as benchmark\n"
        f"root = Path({str(root)!r})\n"
        "class ReadyPopen(subprocess.Popen):\n"
        "    def wait(self, *args, **kwargs):\n"
        "        (root / 'waiting').touch()\n"
        "        return super().wait(*args, **kwargs)\n"
        "benchmark.subprocess.Popen = ReadyPopen\n"
        "try:\n"
        "    benchmark.BenchmarkService(default_interval=0.02).benchmark_command(\n"
        "        [sys.executable, str(root / 'runner.py')], run_id='interrupt',\n"
        "        output_root=root / 'corpus', output_dir=root / 'measurements')\n"
        "except KeyboardInterrupt:\n"
        "    sys.exit(130)\n"
    )
    output = (root / "wrapper.log").open("w")
    proc = subprocess.Popen(
        [sys.executable, str(wrapper)], start_new_session=True,
        stdout=output, stderr=subprocess.STDOUT,
    )
    child = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() or not (root / "waiting").exists():
            if proc.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Benchmark child did not start")
            time.sleep(0.01)
        child = json.loads(ready.read_text())
        os.killpg(proc.pid, signal.SIGINT)  # Like Ctrl+C on the foreground group.
        proc.wait(timeout=20)
        stderr = (root / "wrapper.log").read_text()
        bench = json.loads((root / "measurements/interrupt.json").read_text())
        result = {
            "wrapper_exit": proc.returncode,
            "benchmark_exit": bench["exit_code"],
            "grandchild_running": running(child["pid"]),
            "stderr": stderr,
        }
    finally:
        groups = {proc.pid}
        if child is not None:
            groups.add(child["group"])
        for group in groups:
            if group == os.getpgrp():
                raise RuntimeError("Refusing to signal supervisor's own group")
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=5)
        output.close()
        deadline = time.monotonic() + 3
        while True:
            try:
                reaped, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if not reaped:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Test descendants did not exit")
                time.sleep(0.01)
    print(json.dumps(result))


if __name__ == "__main__":
    main(Path(sys.argv[1]), detached_child=(len(sys.argv) > 2 and sys.argv[2] == "1"))
