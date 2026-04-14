"""
benchmark_monitor.py

Wraps `codebase-insights` as a subprocess and continuously monitors its
resource usage, then writes a structured JSON report to
benchmark_results/full_rebuild.json.

Usage:
    python scripts/benchmark_monitor.py "G:\\SyntaxSenpai\\" [extra cli flags...]

Extra CLI flags are forwarded verbatim to codebase-insights.  The most
common invocation for a full benchmark run:

    python scripts/benchmark_monitor.py "G:\\SyntaxSenpai\\" --rebuild-index --rebuild-semantic

Requirements:
    pip install psutil
    (psutil is not a declared dependency of the main package)

Output:
    benchmark_results/full_rebuild.json   – machine-readable summary
    benchmark_results/full_rebuild.log    – full stdout+stderr transcript
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:
    print("ERROR: psutil is not installed.  Run:  pip install psutil")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

POLL_INTERVAL_S = 0.5          # resource-sampling cadence (seconds)
OUTPUT_DIR = Path(__file__).parent.parent / "benchmark_results"
_BENCH_PREFIX = "[BENCHMARK:"  # prefix used to detect structured output lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_process_memory_mb(proc: psutil.Process) -> float:
    """Return total RSS (MiB) for a process tree (process + all children)."""
    try:
        total = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total / (1024 * 1024)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _get_process_cpu_pct(proc: psutil.Process) -> float:
    """Return combined CPU % for a process tree."""
    try:
        total = proc.cpu_percent(interval=None)
        for child in proc.children(recursive=True):
            try:
                total += child.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0


def _tee_reader(
    stream,
    lines: list[str],
    label: str,
    log_fh=None,
    log_lock: threading.Lock | None = None,
) -> None:
    """Read *stream* line-by-line, print each line, append to *lines*, and
    optionally stream each line to *log_fh* immediately."""
    for raw in stream:
        line = raw.rstrip("\n")
        print(f"{label}{line}" if label else line)
        lines.append(line)
        if log_fh is not None:
            with (log_lock or threading.Lock()):
                log_fh.write(f"{label}{line}\n")
                log_fh.flush()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <project_root> [--rebuild-index] [--rebuild-semantic] ...")
        sys.exit(1)

    project_root = sys.argv[1]
    extra_flags = sys.argv[2:]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / "full_rebuild.log"
    json_path = OUTPUT_DIR / "full_rebuild.json"

    cmd = ["codebase-insights", project_root] + extra_flags
    # On Windows, subprocess.Popen with a list doesn't search PATH for scripts.
    # Prefer the executable from the local uv venv, then fall back to PATH.
    _script_dir = Path(__file__).parent
    _venv_exe = _script_dir.parent / ".venv" / "Scripts" / "codebase-insights.exe"
    exe = (
        str(_venv_exe) if _venv_exe.exists()
        else shutil.which("codebase-insights") or shutil.which("codebase-insights.exe")
    )
    if exe is None:
        print("ERROR: codebase-insights executable not found in .venv/Scripts/ or PATH")
        sys.exit(1)
    cmd = [exe, project_root] + extra_flags
    print(f"[BenchmarkMonitor] Command: {' '.join(cmd)}")
    print(f"[BenchmarkMonitor] Log:     {log_path}")
    print(f"[BenchmarkMonitor] Report:  {json_path}")
    print()

    # ------------------------------------------------------------------
    # Launch subprocess
    # ------------------------------------------------------------------
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    t_wall_start = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    psutil_proc = psutil.Process(proc.pid)
    # Initialise CPU measurement baseline
    psutil_proc.cpu_percent(interval=None)

    # Open log file for streaming writes; both threads share handle + lock.
    log_fh = open(log_path, "w", encoding="utf-8")
    log_lock = threading.Lock()

    # Read stdout/stderr in background threads so we don't deadlock
    t_stdout = threading.Thread(
        target=_tee_reader, args=(proc.stdout, stdout_lines, "", log_fh, log_lock), daemon=True
    )
    t_stderr = threading.Thread(
        target=_tee_reader, args=(proc.stderr, stderr_lines, "[STDERR] ", log_fh, log_lock), daemon=True
    )
    t_stdout.start()
    t_stderr.start()

    # ------------------------------------------------------------------
    # Resource polling loop
    # ------------------------------------------------------------------
    mem_samples: list[float] = []
    cpu_samples: list[float] = []

    # The process may exit quickly (e.g. on error).  We poll until it exits
    # OR until the MCP server starts (it blocks indefinitely).  For the
    # benchmark we stop monitoring after the indexing is done by detecting
    # the [BENCHMARK:SIZES] line in stdout.
    indexing_done = False
    indexing_done_t: float | None = None

    while True:
        # Check if process is still alive
        ret = proc.poll()

        # Sample resource usage
        mem_mb = _get_process_memory_mb(psutil_proc)
        cpu_pct = _get_process_cpu_pct(psutil_proc)
        if mem_mb > 0:
            mem_samples.append(mem_mb)
        if cpu_pct >= 0:
            cpu_samples.append(cpu_pct)

        # Detect end of indexing phase
        if not indexing_done and any(_BENCH_PREFIX + "SIZES]" in l for l in stdout_lines):
            indexing_done = True
            indexing_done_t = time.perf_counter()
            print(f"\n[BenchmarkMonitor] Indexing phase complete at "
                  f"{indexing_done_t - t_wall_start:.1f}s wall-clock. "
                  f"(Process still running as MCP server — stopping resource monitor.)")
            break

        if ret is not None:
            break  # process exited

        time.sleep(POLL_INTERVAL_S)

    t_wall_end = time.perf_counter()
    wall_clock_s = t_wall_end - t_wall_start

    # Wait for I/O threads to drain any buffered data (give them a moment)
    t_stdout.join(timeout=5)
    t_stderr.join(timeout=5)

    # If process exited, capture return code
    return_code = proc.poll()

    # ------------------------------------------------------------------
    # Parse structured benchmark lines from stdout
    # ------------------------------------------------------------------
    benchmark_data: dict[str, str] = {}
    for line in stdout_lines:
        if line.startswith(_BENCH_PREFIX):
            # e.g. [BENCHMARK:INDEXER] wall_time=12.34s files_total=87 ...
            tag_end = line.index("]")
            tag = line[len(_BENCH_PREFIX): tag_end]  # e.g. "INDEXER"
            rest = line[tag_end + 1:].strip()         # key=value pairs
            if tag not in benchmark_data:
                benchmark_data[tag] = {}
            for kv in rest.split():
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    benchmark_data[tag][k] = v

    # ------------------------------------------------------------------
    # Build report
    # ------------------------------------------------------------------
    peak_rss_mb = max(mem_samples) if mem_samples else 0.0
    avg_cpu_pct = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0

    report = {
        "run_info": {
            "started_at": started_at,
            "command": " ".join(cmd),
            "project_root": project_root,
        },
        "timing": {
            "wall_clock_s": round(wall_clock_s, 2),
            "indexed_until_s": round(indexing_done_t - t_wall_start, 2) if indexing_done_t else None,
        },
        "resources": {
            "peak_rss_mb": round(peak_rss_mb, 1),
            "avg_cpu_pct": round(avg_cpu_pct, 1),
            "mem_sample_count": len(mem_samples),
        },
        "process": {
            "return_code": return_code,
            "exited_cleanly": return_code == 0 if return_code is not None else None,
        },
        "phases": benchmark_data,
    }

    # ------------------------------------------------------------------
    # Write outputs
    # ------------------------------------------------------------------
    log_fh.close()
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    # ------------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("BENCHMARK MONITOR SUMMARY")
    print("=" * 70)
    print(f"  Wall-clock (until indexing done) : {wall_clock_s:.1f}s")
    print(f"  Peak RSS (process tree)          : {peak_rss_mb:.0f} MiB")
    print(f"  Avg CPU (process tree)           : {avg_cpu_pct:.0f}%")
    print(f"  Process exit code                : {return_code}")
    print()
    for phase, fields in benchmark_data.items():
        print(f"  [{phase}]")
        for k, v in fields.items():
            print(f"    {k} = {v}")
    print()
    print(f"  Full log  → {log_path}")
    print(f"  JSON data → {json_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
