"""
demo_agent_benchmark.py — Copilot SDK Token Consumption Benchmark

Compares token usage when the Copilot agent tackles the same programming task
with and without Codebase Insights MCP server connected.

The key design principle for the enhanced mode prompt: CI tools must be used
as a SUBSTITUTE for manual file browsing — not a warm-up before it. The prompt
explicitly teaches the agent a CI-first, summary-gated workflow:
  1. get_project_summary  → orient, no file reads yet
  2. search_files / semantic_search / query_symbols  → pinpoint exact files/symbols
  3. get_file_summary  → scan candidates cheaply; skip full read if summary suffices
  4. view (line ranges only)  → read only confirmed-relevant sections

Results:
    See latest benchmark_results/ for the current task and model.

Usage:
    # Baseline (no MCP):
    python scripts/demo_agent_benchmark.py --mode baseline

    # List available tasks from config:
    python scripts/demo_agent_benchmark.py --list-tasks

    # Enhanced (with Codebase Insights MCP):
    # First start the MCP server: codebase-insights G:\\SyntaxSenpai
    python scripts/demo_agent_benchmark.py --mode enhanced

    # Both modes in sequence:
    python scripts/demo_agent_benchmark.py --mode both

    # Open the live GUI to watch model output side-by-side:
    python scripts/demo_agent_benchmark.py --mode both --gui

    # Select a specific task from config:
    python scripts/demo_agent_benchmark.py --task-id syntaxsenpai-azure-openai

Cleanup:
    benchmark_results/ is scratch output. Keep the JSON only if you still need it.
    After extracting the metrics you care about, delete benchmark_results/ so it
    does not accumulate local benchmark artifacts.

Requirements:
    - github-copilot-sdk (pip install github-copilot-sdk)
    - Copilot CLI installed and authenticated (copilot --version)
    - For enhanced mode: Codebase Insights MCP server running on port 6789
    - Note: MCPRemoteServerConfig requires explicit tools= list (not empty)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import atexit
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field

from copilot import CopilotClient, define_tool
from copilot.generated.session_events import SessionEventType
from copilot.session import PermissionHandler, MCPRemoteServerConfig, MCPLocalServerConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_REPO = r"G:\SyntaxSenpai"
MCP_URL = "http://127.0.0.1:6789/mcp"
HEALTH_URL = "http://127.0.0.1:6789/health"
CI_START_TIMEOUT_S = 600   # max seconds to wait for initial index pass to finish
DEFAULT_TASK_CONFIG = os.path.join(
    os.path.dirname(__file__),
    "demo_agent_benchmark_tasks.json",
)
CI_TEMPLATE_INSTRUCTIONS_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "codebase-insights-instructions.md")
)
CI_TOOL_NAMES = {
    "codebase-insights-get_project_summary",
    "codebase-insights-get_file_summary",
    "codebase-insights-search_files",
    "codebase-insights-semantic_search",
    "codebase-insights-query_symbols",
    "codebase-insights-get_symbol_summary",
    "codebase-insights-get_indexer_criteria",
    "codebase-insights-lsp_hover",
    "codebase-insights-lsp_definition",
    "codebase-insights-lsp_references",
    "codebase-insights-lsp_document_symbols",
    "codebase-insights-refresh_file_summary",
    "codebase-insights-refresh_project_summary",
    "codebase-insights-languages_in_codebase",
    "codebase-insights-lsp_capabilities",
}

ENHANCED_BENCHMARK_ADDENDUM = textwrap.dedent("""\
    ## Benchmark-Specific Constraints

    This benchmark is specifically measuring whether you can REPLACE exploratory
    raw file reading with Codebase Insights. Treat every unnecessary `view` call
    as a benchmark failure mode.

    - do not start with a raw source read; spend your first discovery turns on CI tools
    - before the first `view`, identify the likely owner files for each required area using CI tools
    - do not use `view` to browse multiple sibling files looking for the owner; use CI again instead
    - use `get_file_summary(file_path)` on candidate files and reduce them to a short owner list before opening source
    - prefer one targeted `view` per owner file, not repeated reads across nearby files
    - if you have opened 4 files without editing, or 2 opened files turn out not to be edited, stop raw browsing and reroute with CI tools
    - keep total raw `view` calls as low as possible, and stop additional search once you already have a strong owner candidate
""")


def load_ci_template_instructions() -> str:
    with open(CI_TEMPLATE_INSTRUCTIONS_PATH, "r", encoding="utf-8") as handle:
        return handle.read().strip()


def load_task_catalog(task_config_path: str) -> dict:
    with open(task_config_path, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    if not isinstance(catalog, dict):
        raise ValueError("Task config must be a JSON object")

    tasks = catalog.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Task config must contain a non-empty 'tasks' array")

    seen_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Each task entry must be a JSON object")
        task_id = task.get("id")
        title = task.get("title")
        prompt = task.get("prompt")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("Each task must have a non-empty string 'id'")
        if task_id in seen_ids:
            raise ValueError(f"Duplicate task id in config: {task_id}")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Task '{task_id}' is missing a non-empty string 'title'")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"Task '{task_id}' is missing a non-empty string 'prompt'")
        seen_ids.add(task_id)

    default_task_id = catalog.get("default_task_id")
    if default_task_id is not None and default_task_id not in seen_ids:
        raise ValueError(f"default_task_id '{default_task_id}' does not exist in tasks")

    return catalog


def select_task(catalog: dict, task_id: str | None) -> dict:
    tasks = catalog["tasks"]
    effective_task_id = task_id or catalog.get("default_task_id") or tasks[0]["id"]
    for task in tasks:
        if task["id"] == effective_task_id:
            return task
    raise ValueError(f"Unknown task id: {effective_task_id}")


def build_task_prompt(task: dict, mode: str) -> str:
    prompt = task["prompt"]
    if mode != "enhanced":
        return prompt.strip()
    template = load_ci_template_instructions()
    return (
        f"{template}\n\n"
        f"{ENHANCED_BENCHMARK_ADDENDUM.strip()}\n\n"
        f"## Task\n\n{prompt.strip()}"
    )


def print_task_list(catalog: dict) -> None:
    default_task_id = catalog.get("default_task_id")
    print("Available benchmark tasks:")
    for task in catalog["tasks"]:
        marker = " (default)" if task["id"] == default_task_id else ""
        print(f"  - {task['id']}: {task['title']}{marker}")


def get_git_head_revision(repo_path: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return result.stdout.strip()


def reset_repo_to_revision(repo_path: str, revision: str) -> None:
    subprocess.run(
        ["git", "reset", "--hard", revision],
        cwd=repo_path,
        capture_output=True,
        timeout=15,
        check=True,
    )
    subprocess.run(
        ["git", "clean", "-fd"],
        cwd=repo_path,
        capture_output=True,
        timeout=15,
        check=True,
    )


# ---------------------------------------------------------------------------
# Codebase Insights server lifecycle
# ---------------------------------------------------------------------------

_ci_server_proc: "subprocess.Popen | None" = None
_ci_server_started_by_us: bool = False
# (target, backup_dir) pair registered while a run is in-progress; cleared on clean exit.
_ci_pending_restore: "tuple[str, str] | None" = None


def _emergency_cleanup() -> None:
    """Best-effort cleanup called from atexit / signal handlers."""
    global _ci_pending_restore
    stop_ci_server(log_fn=print)
    if _ci_pending_restore is not None:
        target, backup_dir = _ci_pending_restore
        _ci_pending_restore = None
        print("\n  ♻️  Restoring CI artifacts after interruption…", flush=True)
        try:
            restore_ci_artifacts(target, backup_dir, log_fn=print)
        except Exception as exc:
            print(f"  ⚠️  Restore failed: {exc}", flush=True)


atexit.register(_emergency_cleanup)


def _signal_handler(signum, frame) -> None:  # noqa: ARG001
    print(f"\n  ⚡ Signal {signum} received — cleaning up…", flush=True)
    _emergency_cleanup()
    # Re-raise as KeyboardInterrupt so callers (asyncio.run etc.) unwind cleanly.
    raise KeyboardInterrupt


for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _signal_handler)
    except (OSError, ValueError):
        pass  # Some signals can't be caught in all contexts

if sys.platform == "win32":
    try:
        import win32api  # type: ignore
        win32api.SetConsoleCtrlHandler(
            lambda _: (_emergency_cleanup(), True)[-1], True
        )
    except ImportError:
        pass  # pywin32 not installed — SIGINT fallback is sufficient

# Files / directories created by Codebase Insights inside the target repo.
_CI_ARTIFACTS = [
    ".codebase-index.db",
    ".codebase-index.db-shm",
    ".codebase-index.db-wal",
    ".codebase-semantic",
]


def backup_ci_artifacts(target: str, log_fn=print) -> str:
    """Copy CI artifacts from *target* to a temp directory and return its path.

    Files / dirs that do not yet exist are simply skipped; on restore they will
    be deleted rather than recreated, leaving the repo in its original state.
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ci_backup_")
    for name in _CI_ARTIFACTS:
        src = os.path.join(target, name)
        if os.path.isdir(src):
            import shutil
            shutil.copytree(src, os.path.join(tmp, name))
            log_fn(f"  💾 Backed up {name}/")
        elif os.path.isfile(src):
            import shutil
            shutil.copy2(src, os.path.join(tmp, name))
            log_fn(f"  💾 Backed up {name}")
    return tmp


def restore_ci_artifacts(target: str, backup_dir: str, log_fn=print) -> None:
    """Restore CI artifacts from *backup_dir* into *target*, then delete the temp dir."""
    import shutil

    def _rmtree_retry(path: str, retries: int = 8, delay: float = 1.0) -> None:
        """Remove a file or directory, retrying on Windows file-in-use errors."""
        for attempt in range(retries):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                elif os.path.isfile(path):
                    os.remove(path)
                return
            except OSError as exc:
                if attempt == retries - 1:
                    raise
                log_fn(f"  ⏳ {os.path.basename(path)} still locked, retrying in {delay:.0f}s… ({exc})")
                time.sleep(delay)
                delay = min(delay * 1.5, 5.0)

    for name in _CI_ARTIFACTS:
        dst = os.path.join(target, name)
        src = os.path.join(backup_dir, name)
        # Remove whatever the benchmark run created (with retry for locked files)
        if os.path.exists(dst):
            _rmtree_retry(dst)
        # Put back what was there before (may not exist if it was absent)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
            log_fn(f"  ♻️  Restored {name}/")
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
            log_fn(f"  ♻️  Restored {name}")
    shutil.rmtree(backup_dir, ignore_errors=True)


def _find_pid_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on *port* (Windows only via netstat)."""
    pids: list[int] = []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and f":{port}" in parts[1] and parts[3] == "LISTENING":
                try:
                    pids.append(int(parts[4]))
                except ValueError:
                    pass
    except Exception:
        pass
    return pids


def stop_ci_server(log_fn=print) -> None:
    """Terminate the CI server, regardless of whether we started it.

    Tries tracked process first; falls back to finding the process by port
    (Windows only) so files are always released before artifact restore.
    """
    global _ci_server_proc, _ci_server_started_by_us

    def _kill_proc(proc: "subprocess.Popen") -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    if _ci_server_proc is not None:
        log_fn("  🛑 Stopping Codebase Insights…")
        _kill_proc(_ci_server_proc)
        _ci_server_proc = None
        _ci_server_started_by_us = False
        return

    # Server was pre-existing — find and kill by port so files get released.
    if _ci_health() is not None and sys.platform == "win32":
        pids = _find_pid_on_port(6789)
        if pids:
            log_fn(f"  🛑 Stopping pre-existing Codebase Insights (PID {pids})…")
            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
            # Wait until health endpoint goes dark (up to 10s)
            for _ in range(20):
                time.sleep(0.5)
                if _ci_health() is None:
                    break


def _ci_health() -> str | None:
    """Return the 'status' field from the /health endpoint, or None on network error."""
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            import json as _json
            return _json.loads(resp.read()).get("status")
    except Exception:
        return None


def start_ci_server(target: str, log_fn=print) -> None:
    """Ensure the Codebase Insights server is running and fully indexed for *target*.

    If the server is already up and reports ``ready``, this is a no-op.
    Otherwise the server is launched as a subprocess and this function blocks
    until ``GET /health`` returns ``{"status": "ready"}`` (or the timeout
    ``CI_START_TIMEOUT_S`` expires, in which case a RuntimeError is raised).
    """
    global _ci_server_proc

    status = _ci_health()
    if status == "ready":
        log_fn("  ✅ Codebase Insights already ready.")
        return

    if status == "indexing":
        log_fn("  ⏳ Codebase Insights is indexing — waiting for readiness…")
    else:
        # Server not running — start it in its own console window (Windows) so
        # indexing progress is visible.  On non-Windows it inherits the terminal.
        log_fn(f"  🚀 Starting Codebase Insights for {target}…")
        if sys.platform == "win32":
            _ci_server_proc = subprocess.Popen(
                ["codebase-insights", target],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        else:
            _ci_server_proc = subprocess.Popen(["codebase-insights", target])
        _ci_server_started_by_us = True

    deadline = time.monotonic() + CI_START_TIMEOUT_S
    poll_interval = 2.0
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 15.0)  # back off up to 15 s
        s = _ci_health()
        if s == "ready":
            log_fn("  ✅ Codebase Insights is ready.")
            return
        if s == "indexing":
            log_fn("  ⏳ Still indexing…")
        else:
            # Server died or hasn't bound yet — check process
            if _ci_server_proc is not None and _ci_server_proc.poll() is not None:
                raise RuntimeError(
                    f"codebase-insights process exited with code {_ci_server_proc.returncode} "
                    "before becoming ready."
                )

    raise RuntimeError(
        f"Codebase Insights did not become ready within {CI_START_TIMEOUT_S}s. "
        f"Last health status: {_ci_health()!r}"
    )


# ---------------------------------------------------------------------------
# Token & Event Tracker
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    """Accumulates token usage and event counts from the Copilot session."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    compaction_input_tokens: int = 0
    compaction_output_tokens: int = 0
    compaction_count: int = 0
    turn_count: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    final_response: str = ""
    _current_message: str = ""

    def __post_init__(self):
        self._delta_fn = None
        self._reasoning_fn = None
        self._intent_fn = None
        self._current_message_has_deltas = False

        def _safe_print(msg: str):
            try:
                print(msg)
            except (UnicodeEncodeError, UnicodeDecodeError):
                print(msg.encode("ascii", errors="replace").decode("ascii"))

        self._log_fn = _safe_print

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record_event(self, event) -> None:
        """Process a session event and update metrics."""
        etype = event.type

        if etype == SessionEventType.ASSISTANT_TURN_START:
            self.turn_count += 1
            self._log_fn(f"  [Turn {self.turn_count}] started")

        elif etype == SessionEventType.ASSISTANT_TURN_END:
            self._log_fn(f"  [Turn {self.turn_count}] ended")

        elif etype == SessionEventType.ASSISTANT_USAGE:
            data = event.data
            if data:
                inp = int(data.input_tokens or 0)
                out = int(data.output_tokens or 0)
                cr = int(data.cache_read_tokens or 0)
                cw = int(data.cache_write_tokens or 0)
                self.input_tokens += inp
                self.output_tokens += out
                self.cache_read_tokens += cr
                self.cache_write_tokens += cw
                self._log_fn(f"  [Usage] +{inp:,} in, +{out:,} out, cache_read={cr:,} "
                             f"(cumulative: {self.input_tokens:,} in, {self.output_tokens:,} out)")

        elif etype == SessionEventType.SESSION_COMPACTION_START:
            self.compaction_count += 1
            self._log_fn(f"  ⚡ Compaction #{self.compaction_count} starting...")

        elif etype == SessionEventType.SESSION_COMPACTION_COMPLETE:
            data = event.data
            if data:
                ci = int(getattr(data, "compaction_tokens_used", None)
                         and data.compaction_tokens_used
                         and getattr(data.compaction_tokens_used, "input", 0) or 0)
                co = int(getattr(data, "compaction_tokens_used", None)
                         and data.compaction_tokens_used
                         and getattr(data.compaction_tokens_used, "output", 0) or 0)
                pre = int(data.pre_compaction_tokens or 0)
                post = int(data.post_compaction_tokens or 0)
                self.compaction_input_tokens += ci
                self.compaction_output_tokens += co
                self._log_fn(f"  ⚡ Compaction complete: {pre:,} → {post:,} tokens "
                             f"(saved {pre - post:,})")

        elif etype == SessionEventType.TOOL_EXECUTION_START:
            data = event.data
            tool_name = "unknown"
            if data and hasattr(data, "tool_name"):
                tool_name = data.tool_name or "unknown"
            elif data and hasattr(data, "name"):
                tool_name = data.name or "unknown"
            self.tool_calls[tool_name] = self.tool_calls.get(tool_name, 0) + 1
            self._log_fn(f"  🔧 Tool: {tool_name}")

        elif etype == SessionEventType.ASSISTANT_INTENT:
            data = event.data
            if data and isinstance(data.intent, str) and data.intent.strip():
                if self._intent_fn is not None:
                    self._intent_fn(data.intent.strip())

        elif etype == SessionEventType.ASSISTANT_REASONING:
            # When streaming=True, ASSISTANT_REASONING_DELTA carries incremental chunks
            # and ASSISTANT_REASONING is a final summary duplicate — skip it to avoid
            # printing the reasoning twice.
            if not self._reasoning_fn:
                pass  # non-streaming fallback (no delta events): nothing to do

        elif etype == SessionEventType.ASSISTANT_REASONING_DELTA:
            data = event.data
            if data:
                chunk = (getattr(data, "reasoning_text", None)
                         or getattr(data, "delta_content", None)
                         or (data.content if isinstance(getattr(data, "content", None), str) else None)
                         or "")
                if chunk and self._reasoning_fn is not None:
                    self._reasoning_fn(chunk)

        elif etype == SessionEventType.ASSISTANT_MESSAGE:
            data = event.data
            if data:
                chunk = data.content if isinstance(data.content, str) else ""
                if chunk:
                    self._current_message = chunk
                    self.final_response = chunk
                    # Only forward to delta_fn when no delta events were received
                    # (non-streaming model). If deltas already arrived, this is a
                    # duplicate summary — skip it.
                    if self._delta_fn is not None and not self._current_message_has_deltas:
                        self._delta_fn(chunk)
                self._current_message_has_deltas = False  # reset for next message

        elif etype in (SessionEventType.ASSISTANT_MESSAGE_DELTA,
                       SessionEventType.ASSISTANT_STREAMING_DELTA):
            data = event.data
            if data:
                # Incremental chunks arrive in delta_content; fall back to content
                chunk = (data.delta_content if isinstance(data.delta_content, str) else None) \
                     or (data.content      if isinstance(data.content,      str) else None) \
                     or ""
                if chunk:
                    self._current_message += chunk
                    self._current_message_has_deltas = True
                    if self._delta_fn is not None:
                        self._delta_fn(chunk)

        elif etype == SessionEventType.SESSION_ERROR:
            data = event.data
            if data:
                parts = []
                if getattr(data, "error_type", None):
                    parts.append(data.error_type)
                if getattr(data, "message", None):
                    parts.append(data.message)
                msg = ": ".join(parts) if parts else "unknown error"
            else:
                msg = "unknown error"
            self._log_fn(f"  ❌ Error: {msg}")

        elif etype == SessionEventType.SESSION_IDLE:
            # Agent finished — flush accumulated streaming content to final_response
            if self._current_message:
                self.final_response = self._current_message
                # If the GUI delta path never fired (non-streaming model), push full text now
                if self._delta_fn is not None and not self.final_response == self._current_message:
                    self._delta_fn(self._current_message)

    def summary_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "compaction_count": self.compaction_count,
            "compaction_input_tokens": self.compaction_input_tokens,
            "compaction_output_tokens": self.compaction_output_tokens,
            "turn_count": self.turn_count,
            "tool_calls": dict(self.tool_calls),
            "response_length": len(self.final_response),
            "final_response": self.final_response,
        }


# ---------------------------------------------------------------------------
# Streaming event helper
# ---------------------------------------------------------------------------

async def _stream_session_events(session, task_prompt: str, timeout: float = 600.0):
    """
    Async-generator that yields SessionEvents as they arrive from the agent.

    Bridges the SDK's synchronous callback model (session.on) to an async
    iteration interface via asyncio.Queue, so callers can write:

        async for event in _stream_session_events(session, prompt):
            process(event)
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _on_event(event):
        # Called synchronously from the asyncio event loop thread.
        loop.call_soon_threadsafe(queue.put_nowait, event)

    unsubscribe = session.on(_on_event)
    await session.send(task_prompt)

    try:
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            yield event
            if event.type == SessionEventType.SESSION_IDLE:
                break
    finally:
        unsubscribe()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_agent(mode: str, target: str, model: str, task_prompt: str, reset_revision: str, log_fn=print, delta_fn=None, reasoning_fn=None, intent_fn=None) -> dict:
    """Run the Copilot agent in 'baseline' or 'enhanced' mode."""
    log_fn(f"\n{'='*70}")
    log_fn(f"  Running Copilot SDK agent in [{mode.upper()}] mode")
    log_fn(f"  Model: {model}")
    log_fn(f"{'='*70}\n")

    metrics = Metrics()
    metrics._log_fn = log_fn
    metrics._delta_fn = delta_fn
    metrics._reasoning_fn = reasoning_fn
    metrics._intent_fn = intent_fn

    # Configure MCP servers for enhanced mode; back up CI artifacts first so
    # the index the benchmark builds can be discarded afterwards.
    mcp_servers = None
    ci_backup: str | None = None
    if mode == "enhanced":
        log_fn("  📦 Backing up existing CI artifacts…")
        ci_backup = backup_ci_artifacts(target, log_fn=log_fn)
        # Register for emergency cleanup in case of Ctrl+C / process kill.
        global _ci_pending_restore
        _ci_pending_restore = (target, ci_backup)
        # Auto-start the Codebase Insights server if needed and wait until the
        # initial index pass is complete (health endpoint returns "ready").
        start_ci_server(target, log_fn=log_fn)

        mcp_servers = {
            "codebase-insights": MCPRemoteServerConfig(
                type="http",
                url=MCP_URL,
                tools=["*"],  # expose every tool the server registers
            ),
        }

    try:
        # Always restore the exact starting commit before each run so the previous
        # mode cannot affect the next one, even if the agent created commits.
        try:
            reset_repo_to_revision(target, reset_revision)
            log_fn(f"  🔄 Target repo reset to starting revision {reset_revision[:12]}")
        except Exception as exc:
            log_fn(f"  ⚠️ Could not reset target repo before run: {exc}")

        t0 = time.perf_counter()

        async with CopilotClient() as client:
            session = await client.create_session(
                on_permission_request=PermissionHandler.approve_all,
                model=model,
                working_directory=target,
                mcp_servers=mcp_servers,
                streaming=True,
            )

            try:
                async for event in _stream_session_events(session, task_prompt):
                    metrics.record_event(event)
            except asyncio.TimeoutError:
                log_fn("  ⏰ Agent timed out after 600s")
            finally:
                await session.disconnect()

        elapsed = time.perf_counter() - t0

        # Reset any changes the agent made to the target repo, including commits.
        try:
            reset_repo_to_revision(target, reset_revision)
            log_fn(f"  🔄 Target repo reset to clean state at {reset_revision[:12]}")
        except Exception as exc:
            log_fn(f"  ⚠️ Could not reset target repo: {exc}")

    finally:
        if mode == "enhanced":
            stop_ci_server(log_fn=log_fn)
            if ci_backup is not None:
                log_fn("  📦 Restoring CI artifacts…")
                restore_ci_artifacts(target, ci_backup, log_fn=log_fn)
            # Clear the emergency-cleanup registration — normal path handled above.
            _ci_pending_restore = None

    result = {
        "mode": mode,
        "model": model,
        "elapsed_s": round(elapsed, 1),
        **metrics.summary_dict(),
    }

    log_fn(f"\n  ✅ Agent finished in {elapsed:.1f}s ({metrics.turn_count} turns)")
    log_fn(f"  Token usage: {metrics.input_tokens:,} in + {metrics.output_tokens:,} out "
           f"= {metrics.total_tokens:,} total")
    if metrics.compaction_count > 0:
        log_fn(f"  Compactions: {metrics.compaction_count} "
               f"({metrics.compaction_input_tokens:,} in + "
               f"{metrics.compaction_output_tokens:,} out tokens)")
    log_fn(f"  Tool breakdown: {dict(sorted(metrics.tool_calls.items()))}")
    log_fn(f"  Response length: {len(metrics.final_response):,} chars")

    return result


# ---------------------------------------------------------------------------
# Comparison Table
# ---------------------------------------------------------------------------

def print_comparison(b: dict, e: dict) -> None:
    print("\n")
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║            COPILOT SDK — COMPARISON RESULTS                    ║")
    print("╠══════════════════════════╦══════════════╦══════════════╦═════════╣")
    print("║ Metric                   ║   Baseline   ║   Enhanced   ║ Δ (%)   ║")
    print("╠══════════════════════════╬══════════════╬══════════════╬═════════╣")

    for label, key in [
        ("Turns",             "turn_count"),
        ("Input Tokens",      "input_tokens"),
        ("Output Tokens",     "output_tokens"),
        ("Total Tokens",      "total_tokens"),
        ("Cache Read Tokens",  "cache_read_tokens"),
        ("Compactions",       "compaction_count"),
        ("Compaction Tokens",  "compaction_input_tokens"),
        ("Elapsed (s)",       "elapsed_s"),
        ("Response Length",    "response_length"),
    ]:
        bv = b.get(key, 0)
        ev = e.get(key, 0)
        if bv > 0:
            delta = ((ev - bv) / bv) * 100
            delta_str = f"{delta:+.1f}%"
        else:
            delta_str = "—"
        print(f"║ {label:<24} ║ {bv:>12,} ║ {ev:>12,} ║ {delta_str:>7} ║")

    print("╠══════════════════════════╩══════════════╩══════════════╩═════════╣")

    if b["total_tokens"] > 0:
        savings = b["total_tokens"] - e["total_tokens"]
        pct = (savings / b["total_tokens"]) * 100
        if savings > 0:
            msg = f"Enhanced mode SAVED {savings:,} tokens ({pct:.1f}% reduction)"
        else:
            msg = f"Enhanced mode used {-savings:,} MORE tokens ({-pct:.1f}% increase)"
        print(f"║ {msg:<64} ║")

    print("╚═════════════════════════════════════════════════════════════════╝")

    # Tool usage
    all_tools = sorted(set(list(b.get("tool_calls", {}).keys()) +
                           list(e.get("tool_calls", {}).keys())))
    if all_tools:
        print("\n  Tool Usage Breakdown:")
        print(f"  {'Tool':<30} {'Baseline':>10} {'Enhanced':>10}")
        print(f"  {'-'*30} {'-'*10} {'-'*10}")
        for t in all_tools:
            bv = b.get("tool_calls", {}).get(t, 0)
            ev = e.get("tool_calls", {}).get(t, 0)
            marker = ""
            if t in CI_TOOL_NAMES:
                marker = " ← CI"
            print(f"  {t:<30} {bv:>10} {ev:>10}{marker}")

    # Compaction analysis
    if e.get("compaction_count", 0) > 0 or b.get("compaction_count", 0) > 0:
        print("\n  ┌────────────────────────────────────────────────────────────┐")
        print("  │               COMPACTION ANALYSIS                         │")
        print("  ├────────────────────────────────────────────────────────────┤")
        print(f"  │ Baseline compactions: {b.get('compaction_count', 0)}")
        print(f"  │ Enhanced compactions: {e.get('compaction_count', 0)}")
        print(f"  │")
        print(f"  │ Context compaction is the Copilot SDK's key advantage")
        print(f"  │ over naive ReAct agents — it summarizes old turns to")
        print(f"  │ keep the context window manageable.")
        print(f"  └────────────────────────────────────────────────────────────┘")


# ---------------------------------------------------------------------------
# Results persistence
# ---------------------------------------------------------------------------

def save_results(results: list, selected_task: dict, task_config_path: str, target: str, start_revision: str, context_logs: dict | None = None) -> str:
    """Write benchmark results to benchmark_results/ and return the saved path."""
    out_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "benchmark_results")
    )
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"copilot_sdk_benchmark_{ts}.json")
    payload = {
        "timestamp": ts,
        "target": target,
        "target_start_revision": start_revision,
        "task_config": task_config_path,
        "task_id": selected_task["id"],
        "task_title": selected_task["title"],
        "framework": "copilot-sdk",
        "task": build_task_prompt(selected_task, "baseline"),
        "task_prompts": {
            "baseline": build_task_prompt(selected_task, "baseline"),
            "enhanced": build_task_prompt(selected_task, "enhanced"),
        },
        "results": results,
    }
    if context_logs:
        payload["context_logs"] = context_logs
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# GUI (optional — requires tkinter, included in standard CPython)
# ---------------------------------------------------------------------------

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox
    _HAS_TK = True
except ImportError:
    _HAS_TK = False


class BenchmarkGUI:
    """Side-by-side live benchmark viewer using tkinter."""

    _C = {
        "bg":     "#1e1e1e",
        "panel":  "#252526",
        "hdr":    "#37373d",
        "fg":     "#d4d4d4",
        "accent": "#4ec9b0",
        "warn":   "#f9c74f",
        "err":    "#f44747",
        "muted":  "#888888",
        "btn":    "#0e639c",
        "resp":   "#b5cea8",
        "tool":   "#dcdcaa",
        "usage":  "#9cdcfe",
    }

    def __init__(self, args, selected_task, start_revision):
        if not _HAS_TK:
            print("Error: tkinter is not available. Run without --gui.")
            sys.exit(1)
        self.args = args
        self.selected_task = selected_task
        self.start_revision = start_revision
        self.results: dict[str, dict] = {}
        self._resp_started: dict[str, bool] = {"baseline": False, "enhanced": False}
        self._delta_active: dict[str, bool] = {"baseline": False, "enhanced": False}
        self.root = tk.Tk()
        self.root.title("Copilot SDK — Benchmark Viewer")
        self.root.geometry("1600x900")
        self.root.configure(bg=self._C["bg"])
        self.root.minsize(900, 600)
        self._build_ui()

    def _build_ui(self):
        C = self._C
        root = self.root

        # Header
        hdr = tk.Frame(root, bg=C["panel"], pady=7)
        hdr.pack(side=tk.TOP, fill=tk.X)
        tk.Label(
            hdr,
            text=(f"Copilot SDK Benchmark  ·  Task: {self.selected_task['id']}"
                  f"  ·  Model: {self.args.model}"),
            bg=C["panel"], fg=C["fg"], font=("Consolas", 11, "bold"),
        ).pack(side=tk.LEFT, padx=12)

        # Controls pinned to the very bottom
        ctrl = tk.Frame(root, bg=C["panel"], pady=6)
        ctrl.pack(side=tk.BOTTOM, fill=tk.X)
        self.run_btn = tk.Button(
            ctrl, text="▶  Run Benchmark",
            bg=C["btn"], fg="white",
            font=("Consolas", 10, "bold"),
            relief=tk.FLAT, padx=12, pady=4, cursor="hand2",
            command=self._start,
        )
        self.run_btn.pack(side=tk.LEFT, padx=12)
        self.progress_lbl = tk.Label(
            ctrl, text="Press ▶ to start",
            bg=C["panel"], fg=C["muted"], font=("Consolas", 9),
        )
        self.progress_lbl.pack(side=tk.LEFT, padx=8)

        # Middle container (fills remaining space)
        mid = tk.Frame(root, bg=C["bg"])
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Comparison frame (inside mid, hidden until both runs complete)
        self.cmp_frame = tk.Frame(mid, bg=C["panel"])
        self.cmp_txt = scrolledtext.ScrolledText(
            self.cmp_frame,
            wrap=tk.NONE, bg=C["panel"], fg=C["fg"],
            font=("Consolas", 9), height=11, relief=tk.FLAT, padx=8, pady=4,
        )
        self.cmp_txt.pack(fill=tk.BOTH, expand=True)
        for tag, color in [("ok", C["accent"]), ("warn", C["warn"]),
                           ("head", C["usage"]), ("sep", C["muted"])]:
            self.cmp_txt.tag_configure(tag, foreground=color)

        # Two side-by-side panels
        panes = tk.Frame(mid, bg=C["bg"])
        panes.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.panels: dict[str, dict] = {}
        for col, (mode, label) in enumerate([("baseline", "BASELINE"), ("enhanced", "ENHANCED")]):
            panes.columnconfigure(col, weight=1)
            panes.rowconfigure(0, weight=1)

            outer = tk.Frame(panes, bg=C["hdr"], bd=1, relief=tk.SOLID)
            outer.grid(row=0, column=col, sticky="nsew", padx=3, pady=2)

            ph = tk.Frame(outer, bg=C["hdr"], pady=4)
            ph.pack(fill=tk.X)
            tk.Label(ph, text=label, bg=C["hdr"], fg=C["accent"],
                     font=("Consolas", 10, "bold")).pack(side=tk.LEFT, padx=10)
            status_lbl = tk.Label(ph, text="Idle", bg=C["hdr"], fg=C["muted"],
                                  font=("Consolas", 9))
            status_lbl.pack(side=tk.LEFT, padx=6)
            intent_lbl = tk.Label(ph, text="", bg=C["hdr"], fg=C["warn"],
                                  font=("Consolas", 9, "italic"))
            intent_lbl.pack(side=tk.LEFT, padx=4)

            txt = scrolledtext.ScrolledText(
                outer, wrap=tk.WORD,
                bg=C["bg"], fg=C["fg"],
                font=("Consolas", 9), insertbackground="white",
                state=tk.DISABLED, relief=tk.FLAT, padx=4, pady=4,
            )
            txt.pack(fill=tk.BOTH, expand=True)
            for tag, color in [
                ("tool",      C["tool"]),
                ("usage",     C["usage"]),
                ("resp",      C["resp"]),
                ("sep",       C["muted"]),
                ("err",       C["err"]),
                ("ok",        C["accent"]),
                ("intent",    C["warn"]),
                ("reasoning", C["muted"]),
            ]:
                txt.tag_configure(tag, foreground=color)

            sb = tk.Frame(outer, bg=C["panel"], pady=2)
            sb.pack(fill=tk.X)
            stats_lbl = tk.Label(sb, text="", bg=C["panel"],
                                 fg=C["usage"], font=("Consolas", 8))
            stats_lbl.pack(side=tk.LEFT, padx=8)

            self.panels[mode] = {"text": txt, "stats": stats_lbl, "intent": intent_lbl, "status": status_lbl}

    # ── Thread-safe GUI helpers ────────────────────────────────────────────

    def _append(self, mode: str, line: str, tag: str = ""):
        def _do():
            w = self.panels[mode]["text"]
            w.config(state=tk.NORMAL)
            # Close any active streaming blocks with a newline first
            if self._reasoning_active[mode]:
                w.insert(tk.END, "\n", "reasoning")
                self._reasoning_active[mode] = False
            if self._delta_active[mode]:
                w.insert(tk.END, "\n", "resp")
                self._delta_active[mode] = False
            if tag:
                w.insert(tk.END, line + "\n", tag)
            else:
                w.insert(tk.END, line + "\n")
            w.see(tk.END)
            w.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _append_delta(self, mode: str, chunk: str):
        def _do():
            w = self.panels[mode]["text"]
            w.config(state=tk.NORMAL)
            if not self._resp_started[mode]:
                self._resp_started[mode] = True
                # If reasoning was streaming just before, add a blank line separator
                if self._reasoning_active[mode]:
                    w.insert(tk.END, "\n\n", "reasoning")
                    self._reasoning_active[mode] = False
            w.insert(tk.END, chunk, "resp")
            self._delta_active[mode] = True
            w.see(tk.END)
            w.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _append_reasoning(self, mode: str, chunk: str):
        def _do():
            w = self.panels[mode]["text"]
            w.config(state=tk.NORMAL)
            if self._delta_active[mode]:
                w.insert(tk.END, "\n", "resp")
                self._delta_active[mode] = False
            w.insert(tk.END, chunk, "reasoning")
            self._reasoning_active[mode] = True
            w.see(tk.END)
            w.config(state=tk.DISABLED)
        self.root.after(0, _do)

    def _set_stats(self, mode: str, text: str):
        self.root.after(0, lambda: self.panels[mode]["stats"].config(text=text))

    def _set_intent(self, mode: str, text: str):
        self.root.after(0, lambda: self.panels[mode]["intent"].config(text=text))

    def _set_status(self, mode: str, text: str, color: str):
        self.root.after(0, lambda: self.panels[mode]["status"].config(text=text, fg=color))

    def _set_progress(self, text: str):
        self.root.after(0, lambda: self.progress_lbl.config(text=text))

    # ── Comparison table ──────────────────────────────────────────────────

    def _render_comparison(self):
        b = self.results.get("baseline", {})
        e = self.results.get("enhanced", {})
        self.cmp_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=4, pady=2)

        w = self.cmp_txt
        w.config(state=tk.NORMAL)
        w.delete("1.0", tk.END)
        w.insert(tk.END, "  COMPARISON\n", "head")
        w.insert(tk.END, f"  {'─'*65}\n", "sep")
        w.insert(tk.END,
                 f"  {'Metric':<26}{'Baseline':>14}{'Enhanced':>14}{'Δ (%)':>10}\n", "head")
        w.insert(tk.END, f"  {'─'*26}{'─'*14}{'─'*14}{'─'*10}\n", "sep")
        for label, key in [
            ("Turns",             "turn_count"),
            ("Input Tokens",      "input_tokens"),
            ("Output Tokens",     "output_tokens"),
            ("Total Tokens",      "total_tokens"),
            ("Cache Read Tokens", "cache_read_tokens"),
            ("Compactions",       "compaction_count"),
            ("Elapsed (s)",       "elapsed_s"),
            ("Response Length",   "response_length"),
        ]:
            bv = b.get(key, 0)
            ev = e.get(key, 0)
            ds = f"{((ev - bv) / bv * 100):+.1f}%" if bv else "—"
            w.insert(tk.END, f"  {label:<26}{bv:>14,}{ev:>14,}{ds:>10}\n")
        w.insert(tk.END, f"  {'─'*65}\n", "sep")
        if b.get("total_tokens", 0) > 0:
            savings = b["total_tokens"] - e["total_tokens"]
            pct = savings / b["total_tokens"] * 100
            if savings > 0:
                w.insert(tk.END,
                         f"  ✅ Enhanced saved {savings:,} tokens ({pct:.1f}% reduction)\n", "ok")
            else:
                w.insert(tk.END,
                         f"  ⚠️  Enhanced used {-savings:,} MORE tokens ({-pct:.1f}%)\n", "warn")
        w.config(state=tk.DISABLED)
        self.run_btn.config(state=tk.NORMAL)
        self._set_progress("Benchmark complete.")

    # ── Benchmark execution ───────────────────────────────────────────────

    def _make_log_fn(self, mode: str):
        def log_fn(msg: str):
            tag = ""
            if "🔧" in msg:
                tag = "tool"
            elif "💡" in msg or "Intent:" in msg:
                tag = "intent"
            elif "[Usage]" in msg or "cache_read" in msg:
                tag = "usage"
            elif "❌" in msg or "Error" in msg:
                tag = "err"
            elif "✅" in msg or "🔄" in msg:
                tag = "ok"
            self._append(mode, msg, tag)
        return log_fn

    def _run_mode_thread(self, mode: str):
        self._set_status(mode, "▶ Running…", self._C["warn"])
        try:
            result = asyncio.run(
                run_agent(
                    mode,
                    os.path.normpath(self.args.target),
                    self.args.model,
                    build_task_prompt(self.selected_task, mode),
                    self.start_revision,
                    log_fn=self._make_log_fn(mode),
                    delta_fn=lambda chunk: self._append_delta(mode, chunk),
                    reasoning_fn=lambda chunk, m=mode: self._append_reasoning(m, chunk),
                    intent_fn=lambda text, m=mode: self._set_intent(m, text),
                )
            )
            self.results[mode] = result
            self._set_status(mode, f"{result['total_tokens']:,} tok consumed", self._C["accent"])
            self._set_stats(mode, (
                f"Turns: {result['turn_count']}  |  "
                f"In: {result['input_tokens']:,}  |  "
                f"Out: {result['output_tokens']:,}  |  "
                f"Total: {result['total_tokens']:,}  |  "
                f"Elapsed: {result['elapsed_s']}s"
            ))
        except Exception as exc:
            self._set_status(mode, "Error", self._C["err"])
            self._append(mode, f"\n❌ {exc}", "err")
        self._on_mode_done()

    def _on_mode_done(self):
        mode = self.args.mode
        expected = {"baseline", "enhanced"} if mode == "both" else {mode}
        if not expected.issubset(self.results.keys() | {"error"}):
            return  # still waiting for the other run
        if mode == "both" and "baseline" in self.results and "enhanced" in self.results:
            self.root.after(0, self._render_comparison)
        else:
            self.root.after(0, lambda: self.run_btn.config(state=tk.NORMAL))
        self.root.after(100, self._ask_save)

    def _ask_save(self):
        completed = [self.results[m] for m in ("baseline", "enhanced") if m in self.results]
        if not completed:
            return
        if messagebox.askyesno(
            "Save Report?",
            "Save benchmark results to benchmark_results/?\n\n"
            + "\n".join(f"  {r['mode'].upper()}: {r['total_tokens']:,} tokens, "
                        f"{r['turn_count']} turns, {r['elapsed_s']}s"
                        for r in completed),
            parent=self.root,
        ):
            try:
                logs = {
                    mode: self.panels[mode]["text"].get("1.0", tk.END)
                    for mode in ("baseline", "enhanced")
                    if mode in self.panels
                }
                path = save_results(
                    completed,
                    self.selected_task,
                    os.path.normpath(self.args.task_config),
                    os.path.normpath(self.args.target),
                    self.start_revision,
                    context_logs=logs,
                )
                self._set_progress(f"Saved → {path}")
            except Exception as exc:
                messagebox.showerror("Save Failed", str(exc), parent=self.root)
        else:
            self._set_progress("Report not saved.")

    def _start(self):
        self.run_btn.config(state=tk.DISABLED)
        self.results.clear()
        self._resp_started    = {"baseline": False, "enhanced": False}
        self._delta_active    = {"baseline": False, "enhanced": False}
        self._reasoning_active = {"baseline": False, "enhanced": False}
        for mode in ("baseline", "enhanced"):
            w = self.panels[mode]["text"]
            w.config(state=tk.NORMAL)
            w.delete("1.0", tk.END)
            w.config(state=tk.DISABLED)
            self._set_status(mode, "Idle", self._C["muted"])
            self._set_stats(mode, "")
            self._set_intent(mode, "")
        self.cmp_frame.pack_forget()
        mode = self.args.mode
        if mode == "both":
            self._set_progress("Running baseline…")
            def _seq():
                self._run_mode_thread("baseline")
                self.root.after(0, lambda: self._set_progress("Running enhanced…"))
                self._run_mode_thread("enhanced")
            threading.Thread(target=_seq, daemon=True).start()
        else:
            self._set_progress(f"Running {mode}…")
            threading.Thread(
                target=self._run_mode_thread, args=(mode,), daemon=True
            ).start()

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Copilot SDK Agent — Token Consumption Benchmark"
    )
    parser.add_argument(
        "--target", default=TARGET_REPO,
        help=f"Target repository path (default: {TARGET_REPO})",
    )
    parser.add_argument(
        "--model", default="gpt-5-mini",
        help="Model to use (default: gpt-5-mini)",
    )
    parser.add_argument(
        "--mode", choices=["both", "baseline", "enhanced"], default="both",
        help="Which mode(s) to run (default: both)",
    )
    parser.add_argument(
        "--task-config", default=DEFAULT_TASK_CONFIG,
        help=f"Path to benchmark task config JSON (default: {DEFAULT_TASK_CONFIG})",
    )
    parser.add_argument(
        "--task-id",
        help="Task ID from the task config JSON. Defaults to default_task_id in the config.",
    )
    parser.add_argument(
        "--list-tasks", action="store_true",
        help="List available tasks from the task config and exit.",
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Open a live side-by-side GUI to watch model output for each mode.",
    )
    args = parser.parse_args()

    task_config_path = os.path.normpath(args.task_config)
    if not os.path.isfile(task_config_path):
        print(f"Error: task config not found: {task_config_path}")
        sys.exit(1)

    try:
        task_catalog = load_task_catalog(task_config_path)
        if args.list_tasks:
            print_task_list(task_catalog)
            return
        selected_task = select_task(task_catalog, args.task_id)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    target = os.path.normpath(args.target)
    if not os.path.isdir(target):
        print(f"Error: target repo not found: {target}")
        sys.exit(1)

    try:
        start_revision = get_git_head_revision(target)
    except Exception as exc:
        print(f"Error: could not resolve target repo HEAD: {exc}")
        sys.exit(1)

    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║   Copilot SDK — Token Consumption Benchmark                    ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print(f"║  Target: {target:<54} ║")
    print(f"║  Model:  {args.model:<54} ║")
    print(f"║  Task:   {selected_task['id']:<54} ║")
    print(f"║  Reset:  {start_revision[:12]:<54} ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    if args.gui:
        gui = BenchmarkGUI(args, selected_task, start_revision)
        gui.run()
        return

    results = []

    if args.mode in ("both", "baseline"):
        metrics = asyncio.run(
            run_agent(
                "baseline",
                target,
                args.model,
                build_task_prompt(selected_task, "baseline"),
                start_revision,
            )
        )
        results.append(metrics)

    if args.mode in ("both", "enhanced"):
        metrics = asyncio.run(
            run_agent(
                "enhanced",
                target,
                args.model,
                build_task_prompt(selected_task, "enhanced"),
                start_revision,
            )
        )
        results.append(metrics)

    # Comparison table
    if len(results) == 2:
        print_comparison(results[0], results[1])
    elif results:
        r = results[0]
        print(f"\n  [{r['mode'].upper()}] Final: {r['total_tokens']:,} total tokens "
              f"in {r['elapsed_s']}s over {r['turn_count']} turns")

    # Save results
    out_path = save_results(results, selected_task, task_config_path, target, start_revision)
    print(f"\n  Results saved to: {out_path}")
    print("  Cleanup reminder: benchmark_results/ is local scratch output.")
    print("  Delete it after extracting the metrics you need.")
    print("  PowerShell: Remove-Item -Recurse -Force benchmark_results")


if __name__ == "__main__":
    main()
