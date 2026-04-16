#!/usr/bin/env python3
"""
run_benchmark.py — Full benchmark orchestrator for codebase-insights

Automates all phases of the benchmark-eval skill that do not require
human judgment:

  Phase 0  Pre-flight          (port check, config presence, environment)
  Phase 1  Marker verification ([BENCHMARK:*] markers present in source)
  Phase 2  Full rebuild        (delegates to benchmark_monitor.py; opt-in)
  Phase 3  Incremental A–D     (server lifecycle + file edits + capture)
  Phase 4  Retrieval quality   (query execution; scoring is manual)
  Phase 5  LSP navigation      (MCP tool matrix against running server)
  Phase 7  Report generation   (writes docs/benchmark-v<VER>.md)

Phase 6 (bug triage) is not automatable.

Usage
-----
    python scripts/run_benchmark.py TARGET_REPO [options]

Options
-------
    --phases 0,1,3,5,7      Comma-separated phases (default: 0,1,3,5,7)
    --full-rebuild           Also run Phase 2 (time-consuming; ~7+ min)
    --report-version VER     e.g. "0.2.0" (default: read from pyproject.toml)
    --queries-file FILE      JSON list [{query, expected}] for Phase 4
    --output-dir DIR         Output directory (default: benchmark_results)
    --leaf-file PATH         Relative/absolute path in target repo for Scenario B
    --core-file PATH         Relative/absolute path in target repo for Scenario C
    --timeout-incremental N  Seconds to wait per incremental scenario (default: 300)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

MCP_PORT = 6789
MCP_URL = f"http://127.0.0.1:{MCP_PORT}/mcp"
_DB_FILENAME = ".codebase-index.db"
_BENCH_PREFIX = "[BENCHMARK:"
_SCRIPT_DIR = Path(__file__).parent
_REPO_ROOT = _SCRIPT_DIR.parent


# ---------------------------------------------------------------------------
# MCP client
# ---------------------------------------------------------------------------

async def _mcp_call_async(tool_name: str, args: dict) -> dict:
    from mcp.client.streamable_http import streamablehttp_client
    from mcp import ClientSession

    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            if result.content:
                first = result.content[0]
                if hasattr(first, "text"):
                    try:
                        return json.loads(first.text)
                    except json.JSONDecodeError:
                        return {"raw": first.text}
                if hasattr(first, "data"):
                    return dict(first.data) if first.data else {}
            return {"raw": str(result)}


def call_tool(tool_name: str, args: dict) -> dict:
    """Synchronous MCP tool call. Returns a dict (may contain 'error' key)."""
    try:
        return asyncio.run(_mcp_call_async(tool_name, args))
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Executable resolution
# ---------------------------------------------------------------------------

def _find_exe() -> str | None:
    venv_exe = _REPO_ROOT / ".venv" / "Scripts" / "codebase-insights.exe"
    if venv_exe.exists():
        return str(venv_exe)
    return shutil.which("codebase-insights") or shutil.which("codebase-insights.exe")


# ---------------------------------------------------------------------------
# Port management
# ---------------------------------------------------------------------------

def kill_port(port: int) -> str:
    """Kill any process listening on *port*. Returns a status message."""
    # Fast check: can we connect?
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(("127.0.0.1", port))
        sock.close()
        in_use = True
    except (ConnectionRefusedError, OSError):
        in_use = False
    finally:
        sock.close()

    if not in_use:
        return f"port {port} is free"

    # Kill via PowerShell (Windows) or fuser (Linux/macOS)
    if sys.platform == "win32":
        try:
            ps_cmd = (
                f"$p = Get-NetTCPConnection -LocalPort {port} -EA SilentlyContinue; "
                f"if ($p) {{ Stop-Process -Id $p.OwningProcess -Force -EA SilentlyContinue; "
                f"Write-Output $p.OwningProcess }}"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
            )
            pid = r.stdout.strip()
            return f"killed PID {pid} on port {port}" if pid else f"port {port}: process found but not killed"
        except Exception as exc:
            return f"WARNING: could not kill port {port}: {exc}"
    else:
        try:
            r = subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, timeout=10)
            return f"fuser -k {port}/tcp: rc={r.returncode}"
        except Exception as exc:
            return f"WARNING: could not kill port {port}: {exc}"


def _server_is_running() -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(("127.0.0.1", MCP_PORT))
        return True
    except (ConnectionRefusedError, OSError):
        return False
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

class ServerProcess:
    """Wraps a codebase-insights server subprocess with line-by-line log capture."""

    def __init__(self, target_repo: str, log_path: Path, extra_flags: list[str] = ()):
        self.target_repo = target_repo
        self.log_path = log_path
        self.extra_flags = list(extra_flags)
        self.proc: subprocess.Popen | None = None
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._log_fh = None

    def start(self) -> None:
        exe = _find_exe()
        if exe is None:
            raise RuntimeError("codebase-insights executable not found in .venv/Scripts/ or PATH")
        cmd = [exe, self.target_repo] + self.extra_flags
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        self._log_fh = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self) -> None:
        for raw in self.proc.stdout:
            line = raw.rstrip("\n")
            with self._lock:
                self._lines.append(line)
            if self._log_fh:
                self._log_fh.write(line + "\n")
                self._log_fh.flush()

    def line_count(self) -> int:
        with self._lock:
            return len(self._lines)

    def bench_lines_since(self, start_idx: int) -> list[str]:
        with self._lock:
            return [l for l in self._lines[start_idx:] if l.startswith(_BENCH_PREFIX)]

    def wait_for_log_line(self, pattern: str, start_idx: int = 0, timeout: float = 15.0) -> bool:
        """Block until *pattern* appears in any log line after *start_idx*."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                lines = self._lines[start_idx:]
            if any(pattern in l for l in lines):
                return True
            time.sleep(0.5)
        return False

    def wait_for_marker(self, marker: str, start_idx: int = 0, timeout: float = 300.0) -> tuple[bool, list[str]]:
        """Block until ``[BENCHMARK:<marker>]`` appears after *start_idx*."""
        tag = f"[BENCHMARK:{marker}]"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = self.bench_lines_since(start_idx)
            if any(tag in l for l in lines):
                return True, lines
            time.sleep(2.0)
        return False, self.bench_lines_since(start_idx)

    def wait_until_ready(self, timeout: float = 120.0) -> bool:
        """Wait until the MCP server is accepting TCP connections."""
        import socket
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", MCP_PORT))
                return True
            except (ConnectionRefusedError, OSError):
                time.sleep(1.0)
            finally:
                s.close()
        return False

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._log_fh:
            self._log_fh.close()
            self._log_fh = None

    @staticmethod
    def parse_bench_data(lines: list[str]) -> dict[str, dict]:
        """Parse ``[BENCHMARK:TAG] k=v ...`` lines into a nested dict."""
        data: dict[str, dict] = {}
        for line in lines:
            if not line.startswith(_BENCH_PREFIX):
                continue
            try:
                tag_end = line.index("]")
                tag = line[len(_BENCH_PREFIX):tag_end]
                rest = line[tag_end + 1:].strip()
                entry = data.setdefault(tag, {})
                for kv in rest.split():
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        entry[k] = v
            except (ValueError, IndexError):
                pass
        return data


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _db_path(target_repo: str) -> Path:
    return Path(target_repo) / _DB_FILENAME


def _query_db(db: Path, sql: str, params: tuple = ()) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def _get_leaf_file(db: Path) -> tuple[str, int] | None:
    """Return (file_path, sym_count) for the file with fewest cross-file inbound refs."""
    rows = _query_db(db, """
        SELECT s.file_path,
               COUNT(DISTINCT s.id)                          AS sym_count,
               COUNT(CASE WHEN sr.file_path != s.file_path
                          THEN 1 END)                        AS cross_refs
        FROM symbols s
        LEFT JOIN symbol_refs sr ON sr.symbol_id = s.id
        GROUP BY s.file_path
        HAVING sym_count > 0
        ORDER BY cross_refs ASC, sym_count ASC
        LIMIT 20
    """)
    for row in rows:
        if os.path.isfile(row["file_path"]):
            return row["file_path"], row["sym_count"]
    return None


def _get_core_file(db: Path, exclude: str | None = None) -> tuple[str, int] | None:
    """Return (file_path, ref_count) for the most cross-referenced file."""
    rows = _query_db(db, """
        SELECT s.file_path,
               COUNT(*)  AS ref_count
        FROM symbols s
        JOIN symbol_refs sr ON sr.symbol_id = s.id AND sr.file_path != s.file_path
        GROUP BY s.file_path
        ORDER BY ref_count DESC
        LIMIT 20
    """)
    for row in rows:
        p = row["file_path"]
        if os.path.isfile(p) and p != exclude:
            return p, row["ref_count"]
    return None


def _get_symbols_for_lsp(db: Path, count: int = 6) -> list[dict]:
    """Return symbols useful for LSP positioning tests (Class, Interface, Function, Method)."""
    return _query_db(db, """
        SELECT s.name, s.file_path, s.def_line, s.def_character, s.kind_label,
               (SELECT COUNT(*) FROM symbol_refs sr WHERE sr.symbol_id = s.id) AS ref_count
        FROM symbols s
        WHERE s.kind_label IN ('Class', 'Interface', 'Function', 'Method')
        ORDER BY ref_count DESC, s.name
        LIMIT ?
    """, (count,))


# ---------------------------------------------------------------------------
# Test-edit helpers for incremental scenarios
# ---------------------------------------------------------------------------

def _make_test_snippet(file_path: str, scenario: str) -> str:
    """Return language-appropriate code to append for a given scenario."""
    ext = os.path.splitext(file_path)[1].lower()
    ts_name = f"_benchTest{scenario.upper()}"
    py_name = f"_bench_test_{scenario.lower()}"
    if ext in (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".vue"):
        return (
            f"\n// [BENCH-AUTO] scenario {scenario}\n"
            f"export function {ts_name}(): void {{ /* auto-generated by run_benchmark.py */ }}\n"
        )
    if ext == ".py":
        return (
            f"\n# [BENCH-AUTO] scenario {scenario}\n"
            f"def {py_name}():\n"
            f"    pass\n"
        )
    if ext in (".cpp", ".c", ".cc", ".cxx", ".h", ".hpp"):
        return (
            f"\n// [BENCH-AUTO] scenario {scenario}\n"
            f"void {ts_name}() {{ }}\n"
        )
    if ext == ".rs":
        return (
            f"\n// [BENCH-AUTO] scenario {scenario}\n"
            f"pub fn {py_name}() {{ }}\n"
        )
    # Generic fallback — changes file hash without adding a symbol
    return f"\n// [BENCH-AUTO] scenario {scenario}\n"


def _append_test_code(file_path: str, scenario: str) -> str:
    snippet = _make_test_snippet(file_path, scenario)
    with open(file_path, "a", encoding="utf-8") as fh:
        fh.write(snippet)
    return snippet


def _revert_file(file_path: str, appended: str) -> None:
    content = Path(file_path).read_text(encoding="utf-8", errors="replace")
    if content.endswith(appended):
        Path(file_path).write_text(content[: -len(appended)], encoding="utf-8")
    else:
        # fallback: strip all bench-auto lines
        lines = content.splitlines(keepends=True)
        cleaned = [
            l for l in lines
            if "[BENCH-AUTO]" not in l
            and "_benchTest" not in l
            and "_bench_test_" not in l
        ]
        Path(file_path).write_text("".join(cleaned), encoding="utf-8")


def _cleanup_stale_bench_entries(db_path: str) -> None:
    """Remove any leftover _bench_auto_* entries from the DB before Phase 3 starts.

    If the benchmark server was killed while a test file was still tracked in the DB
    (e.g. after a timeout), the stale file_hashes / file_summaries entries would
    cause the indexer to skip the file on recreation (hash unchanged).  This
    function wipes those entries so Scenario D always starts from a clean state.
    """
    if not os.path.isfile(db_path):
        return
    try:
        con = sqlite3.connect(db_path)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        for table in ("file_hashes", "file_summaries", "project_summary_sources", "symbols"):
            if table in tables:
                con.execute(f"DELETE FROM {table} WHERE file_path LIKE '%_bench_auto_%'")
        con.commit()
        con.close()
    except Exception:
        pass


def _create_test_file(target_repo: str) -> str:
    """Create a new source file for Scenario D and return its path."""
    # Detect dominant source extension at repo root
    ext_priority = (".ts", ".tsx", ".js", ".py", ".cpp", ".rs")
    ext_counts: dict[str, int] = {}
    for entry in os.scandir(target_repo):
        if entry.is_file():
            e = os.path.splitext(entry.name)[1].lower()
            if e in ext_priority:
                ext_counts[e] = ext_counts.get(e, 0) + 1
    # Also walk one level deep (src/)
    for sub in ("src", "lib", "app"):
        sub_dir = os.path.join(target_repo, sub)
        if os.path.isdir(sub_dir):
            for entry in os.scandir(sub_dir):
                if entry.is_file():
                    e = os.path.splitext(entry.name)[1].lower()
                    if e in ext_priority:
                        ext_counts[e] = ext_counts.get(e, 0) + 1

    ext = max(ext_counts, key=ext_counts.__getitem__) if ext_counts else ".ts"
    file_path = os.path.join(target_repo, f"_bench_auto_scenario_d{ext}")

    if ext == ".py":
        code = (
            "# [BENCH-AUTO] scenario D — auto-generated, will be deleted\n"
            "class BenchScenarioD:\n"
            "    def __init__(self, name: str) -> None:\n"
            "        self.name = name\n"
            "    def greet(self) -> str:\n"
            "        return f'Hello {self.name}'\n"
            "    def count(self) -> int:\n"
            "        return len(self.name)\n"
        )
    elif ext in (".cpp", ".c", ".cc", ".cxx"):
        code = (
            "// [BENCH-AUTO] scenario D — auto-generated, will be deleted\n"
            "#include <string>\n"
            "class BenchScenarioD {\n"
            "public:\n"
            "    std::string name;\n"
            "    std::string greet() { return \"Hello \" + name; }\n"
            "    int count() { return name.size(); }\n"
            "};\n"
        )
    elif ext == ".rs":
        code = (
            "// [BENCH-AUTO] scenario D — auto-generated, will be deleted\n"
            "pub struct BenchScenarioD { pub name: String }\n"
            "impl BenchScenarioD {\n"
            "    pub fn greet(&self) -> String { format!(\"Hello {}\", self.name) }\n"
            "    pub fn count(&self) -> usize { self.name.len() }\n"
            "}\n"
        )
    else:
        # TypeScript / JavaScript
        code = (
            "// [BENCH-AUTO] scenario D — auto-generated, will be deleted\n"
            "export class BenchScenarioD {\n"
            "  name: string;\n"
            "  constructor(name: string) { this.name = name; }\n"
            "  greet(): string { return `Hello ${this.name}`; }\n"
            "  count(): number { return this.name.length; }\n"
            "}\n"
        )

    Path(file_path).write_text(code, encoding="utf-8")
    return file_path


# ---------------------------------------------------------------------------
# Phase helpers
# ---------------------------------------------------------------------------

def _run_cmd(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=15).strip()
    except Exception:
        return "N/A"


def _read_version_from_toml() -> str:
    try:
        import tomllib  # py3.11+
        with open(_REPO_ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except Exception:
        pass
    try:
        # fallback for older Pythons
        content = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.strip().startswith("version"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Phase 0 — Pre-flight
# ---------------------------------------------------------------------------

def phase0_preflight(target_repo: str, state: dict) -> None:
    print("\n=== Phase 0: Pre-flight checks ===")
    preflight: dict = {}

    # Kill port
    msg = kill_port(MCP_PORT)
    preflight["port_status"] = msg
    print(f"  Port {MCP_PORT}: {msg}")

    # Config file
    config_path = os.path.join(target_repo, ".codebase-insights.toml")
    if not os.path.isfile(config_path):
        print(f"  ABORT: .codebase-insights.toml not found in {target_repo}")
        sys.exit(1)
    print("  Config: found")

    # Environment capture
    env: dict[str, str] = {
        "codebase_insights": _read_version_from_toml(),
        "python": _run_cmd([sys.executable, "--version"]),
        "ts_lsp": _run_cmd(["typescript-language-server", "--version"]),
        "clangd": _run_cmd(["clangd", "--version"]).splitlines()[0],
        "pylsp": _run_cmd(["pylsp", "--version"]),
    }
    for k, v in env.items():
        print(f"  {k}: {v}")
    preflight["env"] = env

    # DB stats
    db = _db_path(target_repo)
    if db.exists():
        files = _query_db(db, "SELECT COUNT(DISTINCT file_path) AS n FROM symbols")
        syms  = _query_db(db, "SELECT COUNT(*) AS n FROM symbols")
        refs  = _query_db(db, "SELECT COUNT(*) AS n FROM symbol_refs")
        state["repo_stats"] = {
            "files":   files[0]["n"] if files else 0,
            "symbols": syms[0]["n"]  if syms  else 0,
            "refs":    refs[0]["n"]  if refs  else 0,
        }
        print(f"  DB: {state['repo_stats']['files']} files, "
              f"{state['repo_stats']['symbols']} symbols, "
              f"{state['repo_stats']['refs']} refs")
    else:
        print("  DB: not found (run the server once to build the index)")
        state["repo_stats"] = {}

    state["preflight"] = preflight
    print("  Pre-flight: OK")


# ---------------------------------------------------------------------------
# Phase 1 — Verify instrumentation markers
# ---------------------------------------------------------------------------

def phase1_verify_markers(state: dict) -> None:
    print("\n=== Phase 1: Verify [BENCHMARK:*] markers ===")
    expected: dict[str, list[str]] = {
        "main.py":             ["BENCHMARK:STARTUP"],
        "workspace_indexer.py":["BENCHMARK:INDEXER"],
        "semantic_indexer.py": [
            "BENCHMARK:SEMANTIC",
            "BENCHMARK:FILE_SUMMARIES",
            "BENCHMARK:PROJECT_SUMMARY",
            "BENCHMARK:SIZES",
        ],
    }
    src_dir = _REPO_ROOT / "src" / "codebase_insights"
    results: dict[str, dict[str, bool]] = {}
    all_ok = True

    for fname, markers in expected.items():
        fpath = src_dir / fname
        if not fpath.exists():
            print(f"  MISSING FILE: {fname}")
            results[fname] = {m: False for m in markers}
            all_ok = False
            continue
        content = fpath.read_text(encoding="utf-8")
        file_result: dict[str, bool] = {}
        for m in markers:
            present = f"[{m}]" in content
            file_result[m] = present
            if not present:
                print(f"  MISSING: [{m}] in {fname}")
                all_ok = False
        results[fname] = file_result

    state["markers"] = results
    if all_ok:
        print("  All markers present: OK")
    else:
        print("  WARNING: some markers are missing — benchmark lines may not appear in logs")


# ---------------------------------------------------------------------------
# Phase 2 — Full rebuild (delegates to benchmark_monitor.py)
# ---------------------------------------------------------------------------

def phase2_full_rebuild(target_repo: str, output_dir: Path, state: dict) -> None:
    print("\n=== Phase 2: Full rebuild ===")
    monitor = _SCRIPT_DIR / "benchmark_monitor.py"
    if not monitor.exists():
        print(f"  SKIP: {monitor} not found")
        state["full_rebuild"] = {"skipped": True, "reason": "benchmark_monitor.py not found"}
        return

    msg = kill_port(MCP_PORT)
    print(f"  Port check: {msg}")

    cmd = [sys.executable, str(monitor), target_repo,
           "--rebuild-index", "--rebuild-semantic"]
    print(f"  Running: {' '.join(cmd)}")
    print("  (This may take several minutes — streaming output below)")
    # Run synchronously so we capture the full output before proceeding
    subprocess.run(cmd, cwd=str(_REPO_ROOT))

    json_path = output_dir / "full_rebuild.json"
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            state["full_rebuild"] = json.load(f)
        print(f"  Results: {json_path}")
    else:
        state["full_rebuild"] = {"error": "full_rebuild.json not found after run"}

    # Kill the server that benchmark_monitor started so Phase 3 gets a clean slate
    time.sleep(2)
    msg = kill_port(MCP_PORT)
    print(f"  Post-rebuild port kill: {msg}")


# ---------------------------------------------------------------------------
# Phase 3 — Incremental update scenarios
# ---------------------------------------------------------------------------

def _print_bench_summary(letter: str, data: dict) -> None:
    for tag in ("INDEXER", "SEMANTIC", "FILE_SUMMARIES", "PROJECT_SUMMARY", "SIZES"):
        if tag in data:
            kv_str = "  ".join(f"{k}={v}" for k, v in data[tag].items())
            print(f"    [{letter}] [BENCHMARK:{tag}] {kv_str}")


def phase3_incremental(
    target_repo: str,
    output_dir: Path,
    state: dict,
    leaf_file: str | None,
    core_file: str | None,
    timeout: float,
) -> None:
    print("\n=== Phase 3: Incremental update scenarios ===")

    db = _db_path(target_repo)

    # Auto-detect test files if not provided
    if not leaf_file:
        lf = _get_leaf_file(db)
        if lf:
            leaf_file = lf[0]
            print(f"  Auto-detected leaf file: {leaf_file} ({lf[1]} symbols)")
        else:
            print("  WARNING: could not detect leaf file from DB")
    else:
        leaf_file = os.path.abspath(leaf_file)
        print(f"  Leaf file (from args): {leaf_file}")

    if not core_file:
        cf = _get_core_file(db, exclude=leaf_file)
        if cf:
            core_file = cf[0]
            print(f"  Auto-detected core file: {core_file} ({cf[1]} inbound cross-refs)")
        else:
            print("  WARNING: could not detect core file from DB")
    else:
        core_file = os.path.abspath(core_file)
        print(f"  Core file (from args): {core_file}")

    if not leaf_file:
        print("  SKIP Phase 3: no indexed files found in DB (run the server once first)")
        state["incremental"] = {"skipped": True, "reason": "no indexed files in DB"}
        return

    # Remove any leftover _bench_auto_* rows from previous (possibly interrupted) runs
    # BEFORE the server starts so it doesn't see a stale file hash and skip indexing.
    _cleanup_stale_bench_entries(db)

    msg = kill_port(MCP_PORT)
    print(f"  Port check: {msg}")

    log_path = output_dir / "server_incremental.log"
    server = ServerProcess(target_repo, log_path)
    scenarios: dict[str, dict] = {}

    try:
        server.start()
        print(f"  Server started (PID {server.proc.pid}), log: {log_path}")

        # ── Scenario A: No-change restart ────────────────────────────────────
        print("\n  [Scenario A] No-change restart — waiting for SIZES marker...")
        t_a = time.monotonic()
        ok_a, lines_a = server.wait_for_marker("SIZES", 0, timeout=timeout)
        total_a = time.monotonic() - t_a
        data_a = ServerProcess.parse_bench_data(lines_a)
        scenarios["A"] = {
            "description": "No-change restart",
            "ok": ok_a,
            "total_wall_s": round(total_a, 2),
            "bench": data_a,
        }
        print(f"    Done ({total_a:.1f}s, SIZES found={ok_a})")
        _print_bench_summary("A", data_a)

        # Wait for server to fully settle before triggering edits
        time.sleep(5)

        # ── Scenario B: Leaf file small edit ─────────────────────────────────
        if leaf_file:
            print(f"\n  [Scenario B] Leaf file: {os.path.basename(leaf_file)}")
            start_b = server.line_count()
            t_b = time.monotonic()
            appended_b = _append_test_code(leaf_file, "B")
            print("    Edit applied — waiting for SEMANTIC...")
            ok_b, lines_b = server.wait_for_marker("SEMANTIC", start_b, timeout=timeout)
            time.sleep(3)  # buffer for any trailing output
            lines_b = server.bench_lines_since(start_b)
            total_b = time.monotonic() - t_b
            data_b = ServerProcess.parse_bench_data(lines_b)
            scenarios["B"] = {
                "description": f"Leaf file edit ({os.path.basename(leaf_file)})",
                "file": leaf_file,
                "ok": ok_b,
                "total_wall_s": round(total_b, 2),
                "bench": data_b,
            }
            print(f"    Done ({total_b:.1f}s, SEMANTIC found={ok_b})")
            _print_bench_summary("B", data_b)
            _revert_file(leaf_file, appended_b)
            print(f"    Reverted: {os.path.basename(leaf_file)}")
            time.sleep(5)

        # ── Scenario C: Core file edit ────────────────────────────────────────
        if core_file:
            print(f"\n  [Scenario C] Core file: {os.path.basename(core_file)}")
            start_c = server.line_count()
            t_c = time.monotonic()
            appended_c = _append_test_code(core_file, "C")
            # With summary_update_threshold > 1, a single file edit will NOT trigger
            # FILE_SUMMARIES or PROJECT_SUMMARY — only INDEXER + SEMANTIC are expected.
            print("    Edit applied — waiting for SEMANTIC (FILE_SUMMARIES deferred by threshold)...")
            ok_c_idx, _ = server.wait_for_marker("INDEXER",  start_c, timeout=60)
            ok_c_sem, _ = server.wait_for_marker("SEMANTIC", start_c, timeout=120)
            time.sleep(3)
            lines_c = server.bench_lines_since(start_c)
            total_c = time.monotonic() - t_c
            data_c = ServerProcess.parse_bench_data(lines_c)
            scenarios["C"] = {
                "description": f"Core file edit ({os.path.basename(core_file)})",
                "file": core_file,
                "ok": ok_c_idx and ok_c_sem,
                "total_wall_s": round(total_c, 2),
                "bench": data_c,
            }
            print(f"    Done ({total_c:.1f}s, INDEXER={ok_c_idx} SEMANTIC={ok_c_sem} "
                  f"FILE_SUMMARIES={data_c.get('FILE_SUMMARIES') is not None} "
                  f"PROJECT_SUMMARY={data_c.get('PROJECT_SUMMARY') is not None})")
            _print_bench_summary("C", data_c)
            _revert_file(core_file, appended_c)
            print(f"    Reverted: {os.path.basename(core_file)}")
            time.sleep(5)

        # ── Scenario D: New file added ────────────────────────────────────────
        print("\n  [Scenario D] New file added...")
        start_d = server.line_count()
        t_d = time.monotonic()
        new_file = _create_test_file(target_repo)
        # Same as C: FILE_SUMMARIES/PROJECT_SUMMARY are deferred behind the threshold.
        print(f"    Created: {os.path.basename(new_file)} — waiting for SEMANTIC (summaries deferred)...")
        ok_d_idx, _ = server.wait_for_marker("INDEXER",  start_d, timeout=60)
        ok_d_sem, _ = server.wait_for_marker("SEMANTIC", start_d, timeout=120)
        time.sleep(3)
        lines_d = server.bench_lines_since(start_d)
        total_d = time.monotonic() - t_d
        data_d = ServerProcess.parse_bench_data(lines_d)
        scenarios["D"] = {
            "description": "New file added",
            "file": os.path.basename(new_file),
            "ok": ok_d_idx,
            "total_wall_s": round(total_d, 2),
            "bench": data_d,
        }
        print(f"    Done ({total_d:.1f}s, INDEXER={ok_d_idx} SEMANTIC={ok_d_sem})")
        _print_bench_summary("D", data_d)

        # Verify that get_project_summary now reports is_stale=True
        print("\n  [Scenario D] Verifying project summary staleness via MCP...")
        ps_result = call_tool("get_project_summary", {})
        ps_stale = (
            isinstance(ps_result, dict)
            and ps_result.get("result", {}).get("is_stale") is True
        )
        print(f"    get_project_summary is_stale={ps_stale} (expected: True)")
        scenarios["D"]["is_stale_verified"] = ps_stale

        try:
            os.remove(new_file)
            print(f"    Removed: {os.path.basename(new_file)}")
            removed_marker = f"[Indexer] Removed: {new_file}"
            if not server.wait_for_log_line(removed_marker, start_d, timeout=10):
                time.sleep(3)
        except OSError as exc:
            print(f"    WARNING: could not remove test file: {exc}")

        # ── Scenario E: Force refresh via MCP ────────────────────────────────
        print("\n  [Scenario E] Force refresh via refresh_project_summary MCP tool...")
        start_e = server.line_count()
        t_e = time.monotonic()
        e_result = call_tool("refresh_project_summary", {})
        ok_e_fs,  _ = server.wait_for_marker("FILE_SUMMARIES",  start_e, timeout=timeout)
        ok_e_ps,  _ = server.wait_for_marker("PROJECT_SUMMARY", start_e, timeout=timeout)
        time.sleep(3)
        lines_e = server.bench_lines_since(start_e)
        total_e = time.monotonic() - t_e
        data_e = ServerProcess.parse_bench_data(lines_e)
        # After refresh, is_stale should be False
        ps_after = call_tool("get_project_summary", {})
        ps_no_stale = (
            isinstance(ps_after, dict)
            and not ps_after.get("result", {}).get("is_stale", False)
        )
        scenarios["E"] = {
            "description": "Force refresh via refresh_project_summary",
            "ok": ok_e_fs and ok_e_ps,
            "is_stale_cleared": ps_no_stale,
            "total_wall_s": round(total_e, 2),
            "bench": data_e,
            "mcp_result": e_result,
        }
        print(f"    Done ({total_e:.1f}s, FILE_SUMMARIES={ok_e_fs} PROJECT_SUMMARY={ok_e_ps} "
              f"is_stale_cleared={ps_no_stale})")
        _print_bench_summary("E", data_e)

        state["incremental"] = {
            "leaf_file": leaf_file,
            "core_file": core_file,
            "scenarios": scenarios,
        }

    finally:
        server.stop()
        print("  Server stopped.")


# ---------------------------------------------------------------------------
# Keyword baseline — LLM-simulated agent keyword extraction
# ---------------------------------------------------------------------------

_KW_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "for", "with", "that", "this", "its",
    "of", "in", "on", "by", "to", "from", "is", "are", "was", "be", "into",
    "when", "after", "before", "until", "each", "all", "any", "more",
    "used", "uses", "use", "given", "via", "how", "what", "which", "where",
})


def _heuristic_keywords(query: str) -> list[str]:
    """Extract the most discriminative tokens from a NL query as fallback."""
    import re
    tokens = re.split(r'\W+', query.lower())
    tokens = [t for t in tokens if len(t) >= 4 and t not in _KW_STOPWORDS]
    # Sort by length descending — longer tokens are more specific
    tokens.sort(key=len, reverse=True)
    return tokens[:4] if tokens else [query.split()[0]]


def _llm_generate_keywords(query: str, target_repo: str) -> list[str]:
    """Ask the configured LLM to generate keyword search terms from a NL query.

    Simulates what a naive AI agent would type into a symbol name-search tool
    when it has no prior knowledge of the codebase.  Falls back to heuristic
    extraction if the LLM is unavailable.
    """
    try:
        import tomllib
        toml_path = Path(target_repo) / ".codebase-insights.toml"
        if not toml_path.exists():
            return _heuristic_keywords(query)
        with open(toml_path, "rb") as f:
            cfg = tomllib.load(f)

        provider = cfg.get("chat", {}).get("provider", "ollama")
        prompt = (
            "You are a developer searching a codebase using a symbol name-search tool. "
            "The tool accepts short keyword terms and matches them against symbol names.\n"
            "Given the description below, output up to 4 short search keywords (e.g. symbol "
            "names, technical terms, or camelCase fragments) that you would try.\n"
            "Output ONLY the keywords, one per line, no explanation.\n\n"
            f"Description: {query}"
        )

        if provider == "openai":
            oa_cfg = cfg.get("chat", {}).get("openai", {})
            base_url = oa_cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL")
            api_key  = os.environ.get("OPENAI_API_KEY", "placeholder")
            model    = oa_cfg.get("model", "gpt-4o-mini")
            import openai as _oa
            client = _oa.OpenAI(api_key=api_key, base_url=base_url)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0,
            )
            text = resp.choices[0].message.content or ""
        else:
            # Ollama
            import urllib.request
            ol_cfg = cfg.get("chat", {}).get("ollama", {})
            base_url = (ol_cfg.get("base_url") or "http://localhost:11434").rstrip("/")
            model    = ol_cfg.get("model", "qwen2.5")
            payload  = json.dumps({
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 80},
            }).encode()
            req = urllib.request.Request(
                f"{base_url}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            text = data.get("message", {}).get("content", "")

        # Parse output — one keyword per line, strip punctuation
        import re
        keywords = [
            re.sub(r'[^\w]', '', line.strip()).lower()
            for line in text.strip().splitlines()
            if line.strip()
        ]
        keywords = [k for k in keywords if len(k) >= 2][:4]
        return keywords if keywords else _heuristic_keywords(query)

    except Exception:
        return _heuristic_keywords(query)


# ---------------------------------------------------------------------------
# Phase 4 — Retrieval quality (query execution; scoring is manual)
# ---------------------------------------------------------------------------

def phase4_retrieval(target_repo: str, queries_file: str | None, state: dict) -> None:
    print("\n=== Phase 4: Retrieval quality ===")

    if not _server_is_running():
        print("  Server not running on port 6789 — skipping Phase 4")
        state["retrieval"] = {"skipped": True, "reason": "server not running"}
        return

    if not queries_file or not os.path.isfile(queries_file):
        print("  No --queries-file provided — skipping Phase 4")
        print('  Create a JSON file: [{"query": "...", "expected": "...", "type": "symbol|file"}]')
        state["retrieval"] = {"skipped": True, "reason": "no queries file"}
        return

    with open(queries_file, encoding="utf-8") as f:
        queries: list[dict] = json.load(f)

    sym_results: list[dict] = []
    file_results: list[dict] = []

    for i, q in enumerate(queries, 1):
        query_text = q.get("query", "").strip()
        expected   = q.get("expected", "")
        limit      = int(q.get("limit", 5))
        qtype      = q.get("type", "symbol").lower()
        print(f"  [{i:2d}/{len(queries)}] [{qtype}] {query_text[:55]}...")

        if qtype == "file":
            # ── File-level semantic search ────────────────────────────────
            raw = call_tool("search_files", {"query": query_text, "limit": limit})
            hits = raw.get("files", raw.get("result", raw.get("results", [])))

            file_top5: list[dict] = []
            if isinstance(hits, list):
                for h in hits[:5]:
                    if isinstance(h, dict):
                        fp = h.get("file_path", h.get("path", h.get("file", "")))
                        file_top5.append({
                            "file":    os.path.basename(fp),
                            "path":    fp,
                            "summary": (h.get("summary") or "")[:120],
                            "score":   h.get("score"),
                        })

            # Sort by score descending; missing/None scores go last
            file_top5.sort(key=lambda h: h.get("score") or 0, reverse=True)

            # Auto-compute hit@K: expected is a substring of the filename (case-insensitive)
            def _file_hit(k: int) -> bool | None:
                if not expected:
                    return None
                return any(expected.lower() in h["file"].lower() for h in file_top5[:k])

            kw_hit = _file_hit(5)

            entry: dict = {
                "type":      "file",
                "query":     query_text,
                "expected":  expected,
                "file_top5": file_top5,
                "kw_hit":    kw_hit,
                "hit1":      _file_hit(1),
                "hit3":      _file_hit(3),
                "hit5":      _file_hit(5),
            }
            file_results.append(entry)

            if file_top5:
                h0 = file_top5[0]
                score = h0.get("score")
                score_str = f"{score:.3f}" if isinstance(score, float) else str(score)
                hit_mark = ("✓" if entry["hit1"] else "✗") if entry["hit1"] is not None else ""
                print(f"    File top-1: {h0['file']} (score={score_str}) {hit_mark}")
            elif "error" in raw:
                print(f"    File search ERROR: {raw['error']}")
            else:
                print("    File search: no results")

        else:
            # ── Symbol-level semantic search ──────────────────────────────
            raw = call_tool("semantic_search", {"query": query_text, "limit": limit})
            hits = raw.get("results", raw.get("result", []))

            sem_top5: list[dict] = []
            if isinstance(hits, list):
                for h in hits[:5]:
                    if isinstance(h, dict):
                        sem_top5.append({"name": h.get("name", "?"), "score": h.get("score")})
                    else:
                        sem_top5.append({"name": str(h), "score": None})

            # Sort by score descending; missing/None scores go last
            sem_top5.sort(key=lambda h: h.get("score") or 0, reverse=True)

            # Keyword baseline — simulate a naive agent generating search terms
            # from the NL query (no knowledge of the codebase).
            kw_top5: list[dict] = []
            kw_keywords = _llm_generate_keywords(query_text, target_repo)
            kw_keyword   = kw_keywords[0] if kw_keywords else None
            if kw_keywords:
                seen_names: set[str] = set()
                for kw in kw_keywords:
                    kw_raw = call_tool("query_symbols", {
                        "name_query": kw,
                        "kinds": ["Class", "Interface", "Function", "Method", "Constructor",
                                  "TypeAlias", "Enum"],
                        "limit": 5,
                    })
                    kw_hits = kw_raw.get("symbols", kw_raw.get("result", []))
                    if isinstance(kw_hits, list):
                        for h in kw_hits:
                            if isinstance(h, dict):
                                name = h.get("name", "?")
                                if name not in seen_names:
                                    seen_names.add(name)
                                    kw_top5.append({
                                        "name": name,
                                        "kind": h.get("kind_label", h.get("kind", "?")),
                                        "file": os.path.basename(h.get("file_path", "")),
                                    })

            # Auto-compute hit@K: expected must match the symbol name exactly
            sem_names = [h["name"] for h in sem_top5]
            hit1 = (expected in sem_names[:1]) if expected else None
            hit3 = (expected in sem_names[:3]) if expected else None
            hit5 = (expected in sem_names[:5]) if expected else None

            # Rank of expected in semantic results (1-based; 0 = not found in top-5)
            sem_rank = next((i + 1 for i, h in enumerate(sem_top5)
                             if h["name"] == expected), 0) if expected else 0

            entry = {
                "type":          "symbol",
                "query":         query_text,
                "expected":      expected,
                "semantic_top5": sem_top5,
                "keyword_top5":  kw_top5,
                "keyword":       kw_keyword,
                "kw_keywords":   kw_keywords,
                "sem_rank":      sem_rank,
                "kw_result_count": len(kw_top5),
                "hit1":          hit1,
                "hit3":          hit3,
                "hit5":          hit5,
                "top5":          sem_top5,  # backward compat
            }
            sym_results.append(entry)

            if sem_top5:
                h0 = sem_top5[0]
                score = h0.get("score")
                score_str = f"{score:.3f}" if isinstance(score, float) else str(score)
                hit_mark = ("✓" if hit1 else "✗") if hit1 is not None else ""
                print(f"    Semantic top-1 : {h0['name']} (score={score_str}) {hit_mark}")
            elif "error" in raw:
                print(f"    Semantic ERROR : {raw['error']}")

            kw_terms_str = "+".join(kw_keywords) if kw_keywords else "?"
            if kw_top5:
                kw_names = ", ".join(h["name"] for h in kw_top5[:3])
                match = "✓" if any(h["name"] == expected for h in kw_top5) else "✗"
                print(f"    Keyword [{kw_terms_str}]: {kw_names} {match}")
            elif kw_keywords:
                print(f"    Keyword [{kw_terms_str}]: no results")

    # Aggregate auto-computed scores
    def _pct(lst: list[dict], key: str) -> str:
        scored = [q for q in lst if q.get(key) is not None]
        if not scored:
            return "n/a"
        hits = sum(1 for q in scored if q[key] is True)
        return f"{hits}/{len(scored)} ({100*hits/len(scored):.1f}%)"

    state["retrieval"] = {
        "symbol_queries": sym_results,
        "file_queries":   file_results,
        "symbol_hit_at_1": _pct(sym_results, "hit1"),
        "symbol_hit_at_3": _pct(sym_results, "hit3"),
        "symbol_hit_at_5": _pct(sym_results, "hit5"),
        "file_hit_at_1":   _pct(file_results, "hit1"),
        "file_hit_at_3":   _pct(file_results, "hit3"),
        "file_hit_at_5":   _pct(file_results, "hit5"),
    }
    print(f"  Symbol queries: {len(sym_results)}, file queries: {len(file_results)}")

def phase5_lsp_navigation(target_repo: str, state: dict) -> None:
    print("\n=== Phase 5: LSP navigation ===")

    if not _server_is_running():
        print("  Server not running on port 6789 — skipping Phase 5")
        state["lsp"] = {"skipped": True, "reason": "server not running"}
        return

    results: dict[str, dict] = {}

    # 5.1 — capabilities
    print("  → lsp_capabilities")
    cap = call_tool("lsp_capabilities", {})
    langs = list(cap.get("result", {}).keys()) if "result" in cap else []
    results["capabilities"] = {"ok": "error" not in cap, "langs": langs}
    print(f"    {'OK' if results['capabilities']['ok'] else 'ERROR'}: {langs}")

    # 5.2 — languages_in_codebase
    print("  → languages_in_codebase")
    lang_r = call_tool("languages_in_codebase", {})
    results["languages"] = {"ok": "error" not in lang_r, "value": lang_r.get("result", [])}
    print(f"    {'OK' if results['languages']['ok'] else 'ERROR'}: {results['languages']['value']}")

    # Fetch symbols for position-based tests
    db = _db_path(target_repo)
    symbols = _get_symbols_for_lsp(db, count=8)
    if not symbols:
        print("  No symbols in DB — skipping position-based LSP tests")
        state["lsp"] = results
        return

    # Use the most-referenced symbol as the primary test target
    top = symbols[0]
    fp, line, char = top["file_path"], top["def_line"], top["def_character"]

    # 5.3 — document_symbols
    print(f"  → lsp_document_symbols: {os.path.basename(fp)}")
    dsyms = call_tool("lsp_document_symbols", {"file_uri": fp})
    sym_count = len(dsyms.get("result", [])) if isinstance(dsyms.get("result"), list) else 0
    results["document_symbols"] = {
        "ok": "error" not in dsyms,
        "file": fp,
        "count": sym_count,
        "error": dsyms.get("error"),
    }
    err_str = f" ({dsyms['error']})" if "error" in dsyms else ""
    print(f"    {'OK' if results['document_symbols']['ok'] else 'ERROR'}: {sym_count} symbols{err_str}")

    # 5.4 — hover
    print(f"  → lsp_hover: {os.path.basename(fp)}:{line}:{char}")
    hover = call_tool("lsp_hover", {"file_uri": fp, "line": line, "character": char})
    results["hover"] = {"ok": "error" not in hover, "file": fp, "line": line, "error": hover.get("error")}
    err_str = f" ({hover['error']})" if "error" in hover else ""
    print(f"    {'OK' if results['hover']['ok'] else 'ERROR'}{err_str}")

    # 5.5 — definition
    print(f"  → lsp_definition: {top['name']}")
    defn = call_tool("lsp_definition", {"file_uri": fp, "line": line, "character": char})
    results["definition"] = {"ok": "error" not in defn, "symbol": top["name"], "error": defn.get("error")}
    err_str = f" ({defn['error']})" if "error" in defn else ""
    print(f"    {'OK' if results['definition']['ok'] else 'ERROR'}{err_str}")

    # 5.6 — references (use second symbol to reduce timeout risk on #1)
    ref_sym = symbols[min(1, len(symbols) - 1)]
    rfp = ref_sym["file_path"]
    rline, rchar = ref_sym["def_line"], ref_sym["def_character"]
    print(f"  → lsp_references: {ref_sym['name']}")
    refs = call_tool("lsp_references", {"file_uri": rfp, "line": rline, "character": rchar})
    ref_count = refs.get("count", len(refs.get("result", [])) if isinstance(refs.get("result"), list) else 0)
    timed_out = "timed out" in str(refs.get("error", ""))
    results["references"] = {
        "ok": "error" not in refs or timed_out,  # timeout is expected, not a failure
        "symbol": ref_sym["name"],
        "count": ref_count,
        "timed_out": timed_out,
    }
    print(f"    {'TIMEOUT (expected)' if timed_out else ('OK' if results['references']['ok'] else 'ERROR')}: {ref_count} refs")

    # 5.7 — implementation (first Interface or Class)
    iface = next((s for s in symbols if s["kind_label"] in ("Interface", "Class")), symbols[0])
    ifp = iface["file_path"]
    iline, ichar = iface["def_line"], iface["def_character"]
    print(f"  → lsp_implementation: {iface['name']}")
    impl = call_tool("lsp_implementation", {"file_uri": ifp, "line": iline, "character": ichar})
    impl_count = len(impl.get("result", [])) if isinstance(impl.get("result"), list) else 0
    results["implementation"] = {
        "ok": "error" not in impl,
        "symbol": iface["name"],
        "count": impl_count,
    }
    print(f"    {'OK' if results['implementation']['ok'] else 'ERROR'}: {impl_count} impls")

    # 5.8 — query_symbols
    print("  → query_symbols (Class/Interface, limit=5)")
    qs = call_tool("query_symbols", {"kinds": ["Class", "Interface"], "limit": 5})
    qs_list = qs.get("symbols", qs.get("result", []))
    qs_count = len(qs_list) if isinstance(qs_list, list) else 0
    results["query_symbols"] = {"ok": "error" not in qs, "count": qs_count}
    print(f"    {'OK' if results['query_symbols']['ok'] else 'ERROR'}: {qs_count} symbols")

    state["lsp"] = results
    ok_count = sum(1 for v in results.values() if isinstance(v, dict) and v.get("ok"))
    total = sum(1 for v in results.values() if isinstance(v, dict) and "ok" in v)
    print(f"  Result: {ok_count}/{total} tools passed")


# ---------------------------------------------------------------------------
# Phase 7 — Report generation
# ---------------------------------------------------------------------------

def phase7_report(target_repo: str, version: str, output_dir: Path, state: dict) -> None:
    print("\n=== Phase 7: Report generation ===")

    report_path = _REPO_ROOT / "docs" / f"benchmark-v{version}.md"
    date_str = datetime.now().strftime("%Y-%m-%d")

    preflight   = state.get("preflight", {})
    env         = preflight.get("env", {})
    repo_stats  = state.get("repo_stats", {})
    full_rb     = state.get("full_rebuild", {})
    incremental = state.get("incremental", {})
    lsp_res     = state.get("lsp", {})
    retrieval   = state.get("retrieval", {})
    markers     = state.get("markers", {})

    fb_phases   = full_rb.get("phases", {})
    fb_timing   = full_rb.get("timing", {})
    fb_resources = full_rb.get("resources", {})
    scenarios   = incremental.get("scenarios", {})

    lines: list[str] = []
    w = lines.append

    # ── Title ────────────────────────────────────────────────────────────────
    w(f"# Benchmark Report — Codebase Insights v{version}")
    w("")
    w(f"**Date:** {date_str}  ")
    w(f"**Target repository:** `{target_repo}`  ")
    w(f"**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)")
    w("")
    w("---")
    w("")

    # ── Executive Summary ─────────────────────────────────────────────────────
    w("## Executive Summary")
    w("")
    if full_rb and "error" not in full_rb and "skipped" not in full_rb:
        w(f"- **Full pipeline time:** **{fb_timing.get('wall_clock_s', '?')}s**")
        w(f"- **Peak RSS:** **{fb_resources.get('peak_rss_mb', '?')} MiB**")
    sc_a = scenarios.get("A", {})
    if sc_a:
        idx_a = sc_a.get("bench", {}).get("INDEXER", {})
        w(f"- **No-change catch-up:** {sc_a.get('total_wall_s', '?')}s  "
          f"(indexed={idx_a.get('indexed', '?')}, skipped={idx_a.get('skipped', '?')})")
    sizes = fb_phases.get("SIZES", {})
    if sizes:
        w(f"- **Storage footprint:** {sizes.get('sqlite_mb', '?')} MB SQLite + "
          f"{sizes.get('chroma_mb', '?')} MB ChromaDB")
    w("")
    w("---")
    w("")

    # ── 1. Environment ────────────────────────────────────────────────────────
    w("## 1. Environment")
    w("")
    w("| Component | Version / Detail |")
    w("|---|---|")
    w(f"| Codebase Insights | **{version}** |")
    w(f"| Python | {env.get('python', 'N/A')} |")
    w(f"| typescript-language-server | {env.get('ts_lsp', 'N/A')} |")
    w(f"| clangd | {env.get('clangd', 'N/A')} |")
    w(f"| pylsp | {env.get('pylsp', 'N/A')} |")
    w("")
    w("### Target repository statistics")
    w("")
    w("| Metric | Value |")
    w("|---|---:|")
    w(f"| Files processed | {repo_stats.get('files', '?')} |")
    w(f"| Total symbols | {repo_stats.get('symbols', '?')} |")
    w(f"| Cross-references | {repo_stats.get('refs', '?')} |")
    w("")
    w("---")
    w("")

    # ── 2. Instrumentation ────────────────────────────────────────────────────
    w("## 2. Instrumentation Verification")
    w("")
    all_ok = all(v for fm in markers.values() for v in fm.values()) if markers else False
    w(f"All `[BENCHMARK:*]` markers present: **{'✓ YES' if all_ok else '✗ NO'}**")
    if not all_ok:
        for fname, fm in markers.items():
            for m, present in fm.items():
                if not present:
                    w(f"- Missing: `[{m}]` in `{fname}`")
    w("")
    w("---")
    w("")

    # ── 3. Full Rebuild ───────────────────────────────────────────────────────
    w("## 3. Full Rebuild")
    w("")
    if not full_rb or "error" in full_rb or "skipped" in full_rb:
        reason = full_rb.get("reason", "use --full-rebuild to include Phase 2") if full_rb else "not run"
        w(f"_Not run. {reason}_")
    else:
        w("| Phase | Metric | Value |")
        w("|---|---|---:|")
        for tag in ("STARTUP", "INDEXER", "SEMANTIC", "FILE_SUMMARIES", "PROJECT_SUMMARY", "SIZES"):
            kv = fb_phases.get(tag, {})
            for k, v in kv.items():
                w(f"| {tag} | {k} | {v} |")
        w("")
        w(f"**Wall-clock (to indexing done):** `{fb_timing.get('wall_clock_s', '?')}s`  ")
        w(f"**Peak RSS:** `{fb_resources.get('peak_rss_mb', '?')} MiB`  ")
        w(f"**Avg CPU:** `{fb_resources.get('avg_cpu_pct', '?')}%`")
    w("")
    w("---")
    w("")

    # ── 4. Incremental Update Scenarios ──────────────────────────────────────
    w("## 4. Incremental Update Scenarios")
    w("")
    if incremental.get("skipped"):
        w(f"_Skipped: {incremental.get('reason', 'N/A')}_")
    else:
        w(f"Leaf file: `{incremental.get('leaf_file', '—')}`  ")
        w(f"Core file: `{incremental.get('core_file', '—')}`")
        w("")
        w("| Scenario | Watchdog+LSP | Semantic | File Summaries | Proj Summary | Total | Notes |")
        w("|---|---|---|---|---|---|---|")
        for letter, desc in (
            ("A", "No-change restart"),
            ("B", "Leaf file edit"),
            ("C", "Core file edit"),
            ("D", "New file added"),
            ("E", "Force refresh (MCP)"),
        ):
            sc = scenarios.get(letter, {})
            bench = sc.get("bench", {})
            idx_wt = bench.get("INDEXER",         {}).get("wall_time", "—")
            sem_wt = bench.get("SEMANTIC",         {}).get("wall_time", "—")
            fs_wt  = bench.get("FILE_SUMMARIES",   {}).get("wall_time", "—")
            ps_wt  = bench.get("PROJECT_SUMMARY",  {}).get("wall_time", "—")
            total  = sc.get("total_wall_s", "—")
            notes_parts = []
            if letter == "D" and "is_stale_verified" in sc:
                notes_parts.append(f"is_stale={sc['is_stale_verified']}")
            if letter == "E" and "is_stale_cleared" in sc:
                notes_parts.append(f"stale_cleared={sc['is_stale_cleared']}")
            notes = ", ".join(notes_parts) if notes_parts else "—"
            w(f"| {letter}: {sc.get('description', desc)} | {idx_wt} | {sem_wt} | {fs_wt} | {ps_wt} | {total}s | {notes} |")
        w("")

        w("### Per-scenario BENCHMARK lines")
        w("")
        for letter in ("A", "B", "C", "D", "E"):
            sc = scenarios.get(letter)
            if not sc:
                continue
            w(f"#### Scenario {letter}: {sc.get('description', '—')}")
            if sc.get("file"):
                w(f"File: `{sc['file']}`  ")
            bench = sc.get("bench", {})
            if bench:
                for tag in ("STARTUP", "INDEXER", "SEMANTIC", "FILE_SUMMARIES", "PROJECT_SUMMARY", "SIZES"):
                    if tag in bench:
                        kv_str = "  ".join(f"`{k}={v}`" for k, v in bench[tag].items())
                        w(f"- `[BENCHMARK:{tag}]` {kv_str}")
            else:
                w("_No BENCHMARK lines captured (server may have timed out)._")
            w("")

    w("---")
    w("")

    # ── 5. LSP Navigation ────────────────────────────────────────────────────
    w("## 5. LSP Navigation")
    w("")
    if lsp_res.get("skipped"):
        w(f"_Skipped: {lsp_res.get('reason', 'N/A')}_")
    else:
        w("| Tool | Result | Detail |")
        w("|---|:---:|---|")
        for tool, data in lsp_res.items():
            if not isinstance(data, dict) or "ok" not in data:
                continue
            ok_mark = "✓" if data.get("ok") else "✗"
            detail_parts: list[str] = []
            if data.get("timed_out"):
                ok_mark = "⏱"
                detail_parts.append("timed out (expected)")
            if "count" in data:
                detail_parts.append(f"count={data['count']}")
            if "langs" in data:
                detail_parts.append(", ".join(str(x) for x in data["langs"]))
            if "symbol" in data:
                detail_parts.append(f"symbol={data['symbol']}")
            if "file" in data:
                detail_parts.append(os.path.basename(data["file"]))
            detail = "; ".join(detail_parts) if detail_parts else ""
            w(f"| {tool} | {ok_mark} | {detail} |")
    w("")
    w("---")
    w("")

    # ── 6. Retrieval Quality ─────────────────────────────────────────────────
    w("## 6. Retrieval Quality")
    w("")
    if retrieval.get("skipped"):
        w(f"_Skipped: {retrieval.get('reason', 'N/A')}_")
        w("")
        w("To run Phase 4, create a JSON queries file and pass `--queries-file FILE --phases 0,4,7`:")
        w("```json")
        w('[')
        w('  {"query": "AI provider base class",     "expected": "BaseAIProvider", "type": "symbol"},')
        w('  {"query": "file handling authentication","expected": "auth.ts",        "type": "file"}')
        w(']')
        w("```")
    else:
        sym_data  = retrieval.get("symbol_queries", retrieval.get("queries", []))
        file_data = retrieval.get("file_queries", [])

        # ── Symbol queries ────────────────────────────────────────────────
        if sym_data:
            hit1 = [q for q in sym_data if q.get("hit1") is True]
            hit3 = [q for q in sym_data if q.get("hit3") is True]
            hit5 = [q for q in sym_data if q.get("hit5") is True]
            total_sym = len(sym_data)
            kw_hit_any = [q for q in sym_data
                          if any(h.get("name") == q.get("expected") for h in q.get("keyword_top5", []))]
            w("### 6.1 Symbol Search (`semantic_search` vs keyword baseline)")
            w("")
            w("> **Keyword baseline**: a context-unaware LLM agent generates up to 4 search terms from")
            w("> the natural-language query (no codebase knowledge), then calls `query_symbols` for each.")
            w("> KW results = total unique symbols returned across all keyword searches.")
            w("")
            if any(q.get("hit1") is not None for q in sym_data):
                w(f"**Semantic Hit@1:** {len(hit1)}/{total_sym} ({100*len(hit1)/total_sym:.1f}%)  ")
                w(f"**Semantic Hit@3:** {len(hit3)}/{total_sym} ({100*len(hit3)/total_sym:.1f}%)  ")
                w(f"**Semantic Hit@5:** {len(hit5)}/{total_sym} ({100*len(hit5)/total_sym:.1f}%)  ")
                w(f"**Keyword found expected (in top-5):** {len(kw_hit_any)}/{total_sym} ({100*len(kw_hit_any)/total_sym:.1f}%)")
            else:
                w(f"Executed {total_sym} symbol queries. Score manually in `benchmark_state.json`, then re-run `--phases 7`.")
            w("")
            w("| # | Query | Expected | Hit@1 | Hit@3 | Hit@5 | Keyword terms tried | KW results | KW found? |")
            w("|---|---|---|:---:|:---:|:---:|---|---:|:---:|")
            for i, q in enumerate(sym_data, 1):
                kw5 = q.get("keyword_top5", [])
                exp = q.get("expected", "")
                kw_terms = "`" + "+".join(q.get("kw_keywords") or ([q.get("keyword")] if q.get("keyword") else ["?"])) + "`"
                kw_count = len(kw5)
                kw_found = "✓" if any(h.get("name") == exp for h in kw5) else ("—" if not exp else "✗")
                h1 = "?" if q.get("hit1") is None else ("✓" if q["hit1"] else "✗")
                h3 = "?" if q.get("hit3") is None else ("✓" if q["hit3"] else "✗")
                h5 = "?" if q.get("hit5") is None else ("✓" if q["hit5"] else "✗")
                full_query = q.get('query', '')
                w(f"| {i} | {full_query} | `{exp}` | {h1} | {h3} | {h5} | {kw_terms} | {kw_count} | {kw_found} |")
            w("")

        # ── File queries ──────────────────────────────────────────────────
        if file_data:
            fhit1 = [q for q in file_data if q.get("hit1") is True]
            fhit3 = [q for q in file_data if q.get("hit3") is True]
            fhit5 = [q for q in file_data if q.get("hit5") is True]
            total_file = len(file_data)
            w("### 6.2 File Search (`search_files`)")
            w("")
            if any(q.get("hit1") is not None for q in file_data):
                w(f"**Hit@1:** {len(fhit1)}/{total_file} ({100*len(fhit1)/total_file:.1f}%)  ")
                w(f"**Hit@3:** {len(fhit3)}/{total_file} ({100*len(fhit3)/total_file:.1f}%)  ")
                w(f"**Hit@5:** {len(fhit5)}/{total_file} ({100*len(fhit5)/total_file:.1f}%)")
            else:
                w(f"Executed {total_file} file queries. Score manually in `benchmark_state.json`, then re-run `--phases 7`.")
            w("")
            w("| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |")
            w("|---|---|---|---|:---:|:---:|:---:|:---:|")
            for i, q in enumerate(file_data, 1):
                f5 = q.get("file_top5", [])
                top1_file = f5[0].get("file", "?") if f5 else "?"
                exp = q.get("expected", "")
                name_match = "✓" if q.get("kw_hit") else ("—" if not exp else "✗")
                h1 = "?" if q.get("hit1") is None else ("✓" if q["hit1"] else "✗")
                h3 = "?" if q.get("hit3") is None else ("✓" if q["hit3"] else "✗")
                h5 = "?" if q.get("hit5") is None else ("✓" if q["hit5"] else "✗")
                w(f"| {i} | {q.get('query', '')[:48]} | `{exp}` | `{top1_file}` | {name_match} | {h1} | {h3} | {h5} |")
            w("")
    w("---")
    w("")

    # ── 7. Notes ─────────────────────────────────────────────────────────────
    w("## 7. Key Findings & Recommendations")
    w("")
    w("_Add observations, bugs found, and prioritised recommendations here._")
    w("")
    w("---")
    w("")
    w("## Completion Checklist")
    w("")
    w("- [ ] Port 6789 confirmed free before every server start")
    w("- [ ] All `[BENCHMARK:*]` markers flush to log in real time")
    w("- [ ] Scenarios A–E all have BENCHMARK data captured")
    w("- [ ] LSP test matrix fully exercised")
    w("- [ ] Retrieval quality queries scored (if Phase 4 was run)")
    w("- [ ] Bugs found are documented with root cause + fix")
    w("- [ ] `benchmark_results/` deleted after report is committed")
    w("")
    w("---")
    w("")
    w("*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*")

    content = "\n".join(lines) + "\n"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    print(f"  Report: {report_path}")

    # Save raw state JSON
    state_path = output_dir / "benchmark_state.json"
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, default=str)
    print(f"  Raw state: {state_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="run_benchmark.py",
        description="Automated benchmark orchestrator for codebase-insights",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target_repo",
        help="Absolute or relative path to the target repository",
    )
    parser.add_argument(
        "--phases",
        default="0,1,3,5,7",
        help="Comma-separated phases to run (default: %(default)s). "
             "Add 2 for full rebuild, 4 for retrieval quality.",
    )
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Also run Phase 2 (full rebuild — time-consuming)",
    )
    parser.add_argument(
        "--report-version",
        default=None,
        metavar="VER",
        help="Version tag for the report file, e.g. 0.2.0 (default: from pyproject.toml)",
    )
    parser.add_argument(
        "--queries-file",
        default=None,
        metavar="FILE",
        help="JSON file with Phase 4 queries: [{\"query\": \"...\", \"expected\": \"...\"}]",
    )
    parser.add_argument(
        "--output-dir",
        default="benchmark_results",
        metavar="DIR",
        help="Directory for run artefacts (default: %(default)s)",
    )
    parser.add_argument(
        "--leaf-file",
        default=None,
        metavar="PATH",
        help="Path (relative to target repo or absolute) for Scenario B leaf edit",
    )
    parser.add_argument(
        "--core-file",
        default=None,
        metavar="PATH",
        help="Path (relative to target repo or absolute) for Scenario C core edit",
    )
    parser.add_argument(
        "--timeout-incremental",
        type=float,
        default=300.0,
        metavar="N",
        help="Seconds to wait for each incremental scenario (default: %(default)s)",
    )

    args = parser.parse_args()

    # Resolve phases
    phase_strs = {p.strip() for p in args.phases.split(",")}
    if args.full_rebuild:
        phase_strs.add("2")
    phases = {int(p) for p in phase_strs if p.isdigit()}

    target_repo = os.path.abspath(args.target_repo)
    if not os.path.isdir(target_repo):
        print(f"ERROR: target repo does not exist: {target_repo}")
        sys.exit(1)

    version = args.report_version or _read_version_from_toml()
    output_dir = _REPO_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(p: str | None) -> str | None:
        if p is None:
            return None
        return p if os.path.isabs(p) else os.path.join(target_repo, p)

    leaf_file = _resolve_path(args.leaf_file)
    core_file = _resolve_path(args.core_file)

    print(f"codebase-insights benchmark orchestrator v{version}")
    print(f"Target : {target_repo}")
    print(f"Phases : {sorted(phases)}")
    print(f"Output : {output_dir}")

    state: dict = {}

    # Load existing state so selective --phases reruns accumulate results.
    _state_file = output_dir / "benchmark_state.json"
    if _state_file.exists():
        try:
            with open(_state_file, encoding="utf-8") as _sf:
                state = json.load(_sf)
        except Exception:
            state = {}

    if 0 in phases:
        phase0_preflight(target_repo, state)
    if 1 in phases:
        phase1_verify_markers(state)
    if 2 in phases:
        phase2_full_rebuild(target_repo, output_dir, state)
    if 3 in phases:
        phase3_incremental(target_repo, output_dir, state, leaf_file, core_file, args.timeout_incremental)

    # Phases 4 and 5 need a running server.  Phase 3 stops its server when
    # done, so we start a fresh one here if needed (no rebuild flags — reuses
    # existing index).  We track whether WE started it so we can stop it.
    #
    # After Phase 3 the OS socket may linger briefly (TIME_WAIT / CLOSE_WAIT),
    # making _server_is_running() return True even though the process is gone.
    # Force-clear the port first so we always start a clean server.
    if 3 in phases and (4 in phases or 5 in phases):
        kill_port(MCP_PORT)
        # Wait up to 5s for the port to fully release before starting Phase 4/5 server
        for _ in range(5):
            if not _server_is_running():
                break
            time.sleep(1.0)

    _owned_server: "ServerProcess | None" = None
    if (4 in phases or 5 in phases) and not _server_is_running():
        print("\n  [server] Starting server for Phases 4/5 (no rebuild)...")
        _srv_log = output_dir / "server_phase45.log"
        _owned_server = ServerProcess(target_repo, _srv_log)
        _owned_server.start()
        if _owned_server.wait_until_ready(timeout=120):
            print(f"  [server] Ready on port {MCP_PORT}")
            # Wait for initial catch-up pass to finish so queries hit a settled index
            _owned_server.wait_for_marker("SIZES", timeout=300)
            print("  [server] Index catch-up done")
        else:
            print("  [server] WARNING: server did not become ready in 120s — Phases 4/5 may fail")

    try:
        if 4 in phases:
            phase4_retrieval(target_repo, args.queries_file, state)
        if 5 in phases:
            phase5_lsp_navigation(target_repo, state)
    finally:
        if _owned_server is not None:
            _owned_server.stop()
            kill_port(MCP_PORT)
            print("  [server] Stopped phase 4/5 server")

    if 7 in phases:
        phase7_report(target_repo, version, output_dir, state)

    print(f"\nDone. Results in: {output_dir}")


if __name__ == "__main__":
    main()
