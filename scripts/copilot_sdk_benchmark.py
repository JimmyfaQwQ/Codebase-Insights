"""
copilot_sdk_benchmark.py — Copilot SDK Token Consumption Benchmark

Compares token usage when the Copilot agent tackles the same programming task
with and without Codebase Insights MCP server connected.

The Copilot SDK has built-in context compaction, so this benchmark tests
whether its smarter orchestration engine reduces the token overhead we
observed with a naive LangGraph ReAct agent (+79-234%).

Results (gpt-5-mini, SyntaxSenpai — Gemini provider task):
    Baseline: 212,071 tokens (10 turns, 10 views)
    Enhanced: 808,596 tokens (25 turns, +281%) — CI tools used but additive
    Compactions: 0 in both — context never exceeded compaction threshold
    Key finding: CI tools guide navigation but agent still does full file reads
    on top, and takes more turns to edit/test code.

Usage:
    # Baseline (no MCP):
    python scripts/copilot_sdk_benchmark.py --mode baseline

    # Enhanced (with Codebase Insights MCP):
    # First start the MCP server: codebase-insights G:\\SyntaxSenpai
    python scripts/copilot_sdk_benchmark.py --mode enhanced

    # Both modes in sequence:
    python scripts/copilot_sdk_benchmark.py --mode both

Requirements:
    - github-copilot-sdk (pip install github-copilot-sdk)
    - Copilot CLI installed and authenticated (copilot --version)
    - For enhanced mode: Codebase Insights MCP server running on port 6789
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import textwrap
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

CODING_TASK = textwrap.dedent("""\
    You are working on the SyntaxSenpai project (a cross-platform AI companion app
    built with Electron + React).

    Your task: Write a NEW AI provider implementation for **Google Gemini**.

    Steps:
    1. Understand how existing AI providers (OpenAI, Anthropic, etc.) are
       structured — find the provider directory, look at the types/interfaces,
       and study one existing provider as a reference.
    2. Write the COMPLETE implementation of a new Gemini provider file that
       follows the exact same patterns. The file should:
       - Export a class/function matching the existing provider interface
       - Support streaming chat completions
       - Handle tool calls in the same format as other providers
       - Map Gemini-specific types to the shared message format

    Output your final answer as the complete source code for the new provider file.
    Important: actually read the relevant source code first. Do NOT guess.
""")

CODING_TASK_ENHANCED = textwrap.dedent("""\
    You are working on the SyntaxSenpai project (a cross-platform AI companion app
    built with Electron + React).

    You have access to a "codebase-insights" MCP server with semantic search, project
    summaries, file summaries, and symbol queries. **Use these tools first** to
    understand the project structure and find the relevant provider code, rather than
    manually browsing the file tree.

    Your task: Write a NEW AI provider implementation for **Google Gemini**.

    Steps:
    1. Use the codebase-insights tools (get_project_summary, search_files,
       semantic_search, get_file_summary, query_symbols) to quickly understand
       how existing AI providers (OpenAI, Anthropic, etc.) are structured.
    2. Read the key source files identified by the codebase-insights tools.
    3. Write the COMPLETE implementation of a new Gemini provider file that
       follows the exact same patterns. The file should:
       - Export a class/function matching the existing provider interface
       - Support streaming chat completions
       - Handle tool calls in the same format as other providers
       - Map Gemini-specific types to the shared message format

    Output your final answer as the complete source code for the new provider file.
    Important: actually read the relevant source code first. Do NOT guess.
""")


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

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record_event(self, event) -> None:
        """Process a session event and update metrics."""
        etype = event.type

        if etype == SessionEventType.ASSISTANT_TURN_START:
            self.turn_count += 1
            print(f"  [Turn {self.turn_count}] started")

        elif etype == SessionEventType.ASSISTANT_TURN_END:
            print(f"  [Turn {self.turn_count}] ended")

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
                print(f"  [Usage] +{inp:,} in, +{out:,} out, cache_read={cr:,} "
                      f"(cumulative: {self.input_tokens:,} in, {self.output_tokens:,} out)")

        elif etype == SessionEventType.SESSION_COMPACTION_START:
            self.compaction_count += 1
            print(f"  ⚡ Compaction #{self.compaction_count} starting...")

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
                print(f"  ⚡ Compaction complete: {pre:,} → {post:,} tokens "
                      f"(saved {pre - post:,})")

        elif etype == SessionEventType.TOOL_EXECUTION_START:
            data = event.data
            tool_name = "unknown"
            if data and hasattr(data, "tool_name"):
                tool_name = data.tool_name or "unknown"
            elif data and hasattr(data, "name"):
                tool_name = data.name or "unknown"
            self.tool_calls[tool_name] = self.tool_calls.get(tool_name, 0) + 1
            print(f"  🔧 Tool: {tool_name}")

        elif etype == SessionEventType.ASSISTANT_MESSAGE:
            data = event.data
            if data and hasattr(data, "content"):
                self._current_message = data.content or ""
                self.final_response = self._current_message

        elif etype == SessionEventType.ASSISTANT_MESSAGE_DELTA:
            data = event.data
            if data and hasattr(data, "content") and data.content:
                self._current_message += data.content

        elif etype == SessionEventType.SESSION_ERROR:
            data = event.data
            msg = str(data) if data else "unknown error"
            print(f"  ❌ Error: {msg}")

        elif etype == SessionEventType.SESSION_IDLE:
            # Agent finished
            if self._current_message:
                self.final_response = self._current_message

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
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_agent(mode: str, target: str, model: str) -> dict:
    """Run the Copilot agent in 'baseline' or 'enhanced' mode."""
    print(f"\n{'='*70}")
    print(f"  Running Copilot SDK agent in [{mode.upper()}] mode")
    print(f"  Model: {model}")
    print(f"{'='*70}\n")

    metrics = Metrics()
    done = asyncio.Event()

    def on_event(event):
        metrics.record_event(event)
        if event.type == SessionEventType.SESSION_IDLE:
            done.set()

    # Configure MCP servers for enhanced mode
    mcp_servers = None
    if mode == "enhanced":
        ci_tools = [
            "get_project_summary", "get_file_summary", "search_files",
            "semantic_search", "query_symbols", "get_symbol_summary",
            "get_indexer_criteria", "lsp_hover", "lsp_definition",
            "lsp_references", "lsp_document_symbols",
            "refresh_file_summary", "refresh_project_summary",
            "languages_in_codebase", "lsp_capabilities",
        ]
        mcp_servers = {
            "codebase-insights": MCPRemoteServerConfig(
                type="http",
                url=MCP_URL,
                tools=ci_tools,
            ),
        }

    t0 = time.perf_counter()

    async with CopilotClient() as client:
        session = await client.create_session(
            on_permission_request=PermissionHandler.approve_all,
            model=model,
            working_directory=target,
            mcp_servers=mcp_servers,
            on_event=on_event,
        )

        try:
            task = CODING_TASK_ENHANCED if mode == "enhanced" else CODING_TASK
            await session.send(task)
            # Wait for the agent to finish (with timeout)
            try:
                await asyncio.wait_for(done.wait(), timeout=600)
            except asyncio.TimeoutError:
                print("  ⏰ Agent timed out after 600s")
        finally:
            await session.disconnect()

    elapsed = time.perf_counter() - t0

    # Reset any file changes the agent made to the target repo
    try:
        subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=target, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=target, capture_output=True, timeout=10,
        )
        print("  🔄 Target repo reset to clean state")
    except Exception as exc:
        print(f"  ⚠️ Could not reset target repo: {exc}")

    result = {
        "mode": mode,
        "model": model,
        "elapsed_s": round(elapsed, 1),
        **metrics.summary_dict(),
    }

    print(f"\n  ✅ Agent finished in {elapsed:.1f}s ({metrics.turn_count} turns)")
    print(f"  Token usage: {metrics.input_tokens:,} in + {metrics.output_tokens:,} out "
          f"= {metrics.total_tokens:,} total")
    if metrics.compaction_count > 0:
        print(f"  Compactions: {metrics.compaction_count} "
              f"({metrics.compaction_input_tokens:,} in + "
              f"{metrics.compaction_output_tokens:,} out tokens)")
    print(f"  Tool breakdown: {dict(sorted(metrics.tool_calls.items()))}")
    print(f"  Response length: {len(metrics.final_response):,} chars")

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
            if bv == 0 and ev > 0:
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
    args = parser.parse_args()

    target = os.path.normpath(args.target)
    if not os.path.isdir(target):
        print(f"Error: target repo not found: {target}")
        sys.exit(1)

    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║   Copilot SDK — Token Consumption Benchmark                    ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print(f"║  Target: {target:<54} ║")
    print(f"║  Model:  {args.model:<54} ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    results = []

    if args.mode in ("both", "baseline"):
        metrics = asyncio.run(run_agent("baseline", target, args.model))
        results.append(metrics)

    if args.mode in ("both", "enhanced"):
        metrics = asyncio.run(run_agent("enhanced", target, args.model))
        results.append(metrics)

    # Comparison table
    if len(results) == 2:
        print_comparison(results[0], results[1])
    elif results:
        r = results[0]
        print(f"\n  [{r['mode'].upper()}] Final: {r['total_tokens']:,} total tokens "
              f"in {r['elapsed_s']}s over {r['turn_count']} turns")

    # Save results
    out_dir = os.path.join(os.path.dirname(__file__), "..", "benchmark_results")
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"copilot_sdk_benchmark_{ts}.json")
    with open(out_path, "w") as f:
        json.dump({
            "timestamp": ts,
            "target": target,
            "framework": "copilot-sdk",
            "task": CODING_TASK.strip(),
            "results": results,
        }, f, indent=2)
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
