"""
demo_agent_benchmark.py — LangGraph Coding Agent Token Consumption Benchmark

Compares token usage when a ReAct coding agent tackles the same programming
task with and without Codebase Insights pre-loaded.

Usage:
    python scripts/demo_agent_benchmark.py [--target G:\\SyntaxSenpai]

Requirements:
    - DEEPSEEK_API_KEY (or CODEBASE_INSIGHTS_CHAT_API_KEY) env var set
    - Ollama running with bge-m3 model (for embeddings in enhanced mode)
    - Target repo must already have a .codebase-index.db and .codebase-semantic/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# LangChain / LangGraph imports
# ---------------------------------------------------------------------------
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

# ---------------------------------------------------------------------------
# Codebase Insights imports (for enhanced mode)
# ---------------------------------------------------------------------------
# Add project src to path so we can import codebase_insights
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TARGET_REPO = r"G:\SyntaxSenpai"
DB_FILENAME = ".codebase-index.db"
CHROMA_DIR = ".codebase-semantic"
MAX_FILE_READ_CHARS = 8000  # cap file reads to avoid blowing up context

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

BASELINE_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert software engineer. You have access to tools for exploring
    a codebase: listing directories, reading files, and searching with grep.
    Use them to understand the project structure and existing patterns before
    writing code. Be thorough but efficient — don't read files you don't need.
    When you have enough context, produce the final implementation.
""")

ENHANCED_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert software engineer. You have access to both filesystem tools
    AND Codebase Insights tools (project summary, file summaries, semantic search,
    symbol query).

    Strategy — use Codebase Insights tools FIRST to orient yourself:
    1. Call get_project_summary() to understand the overall architecture.
    2. Use search_files() or semantic_search() to find relevant code areas.
    3. Use query_symbols() to find specific types, functions, or classes.
    4. Use get_file_summary() to understand a file WITHOUT reading it fully.
    5. Only use read_file for files where you need the exact source code.

    This approach lets you understand the codebase with far fewer file reads.
    When you have enough context, produce the final implementation.
""")

# ---------------------------------------------------------------------------
# Token Tracking Callback
# ---------------------------------------------------------------------------

class TokenTracker(BaseCallbackHandler):
    """Accumulates token usage from LLM calls."""

    def __init__(self):
        super().__init__()
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.llm_calls = 0

    def on_llm_end(self, response, **kwargs):
        self.llm_calls += 1
        # OpenAI-compatible APIs put usage in llm_output
        llm_output = response.llm_output or {}
        usage = llm_output.get("token_usage", {})
        if usage:
            self.input_tokens += usage.get("prompt_tokens", 0)
            self.output_tokens += usage.get("completion_tokens", 0)
            self.total_tokens += usage.get("total_tokens", 0)
        else:
            # Fallback: try to extract from generation metadata
            for gen_list in response.generations:
                for gen in gen_list:
                    info = getattr(gen, "generation_info", {}) or {}
                    u = info.get("token_usage", {})
                    if u:
                        self.input_tokens += u.get("prompt_tokens", 0)
                        self.output_tokens += u.get("completion_tokens", 0)
                        self.total_tokens += u.get("total_tokens", 0)

    def summary(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


# ═══════════════════════════════════════════════════════════════════════════
# Baseline Tools — raw filesystem access only
# ═══════════════════════════════════════════════════════════════════════════

def make_baseline_tools(target: str):
    """Create filesystem-only tools scoped to *target* repo."""

    @tool
    def list_directory(path: str = "") -> str:
        """List files and directories at the given relative path (or repo root).
        Returns names, one per line.  Directories have a trailing slash."""
        abs_path = os.path.join(target, path) if path else target
        if not os.path.isdir(abs_path):
            return f"Error: not a directory — {path}"
        entries = []
        try:
            for name in sorted(os.listdir(abs_path)):
                full = os.path.join(abs_path, name)
                if name.startswith(".") or name == "node_modules":
                    continue
                entries.append(name + ("/" if os.path.isdir(full) else ""))
        except OSError as e:
            return f"Error listing directory: {e}"
        return "\n".join(entries) if entries else "(empty)"

    @tool
    def read_file(file_path: str, start_line: int = 0, end_line: int = 0) -> str:
        """Read a file from the repository.  `file_path` is relative to repo root.
        Optionally specify start_line and end_line (1-based) to read a range.
        If no range is specified, the full file is returned (truncated at ~8000 chars)."""
        abs_path = os.path.join(target, file_path)
        if not os.path.isfile(abs_path):
            return f"Error: file not found — {file_path}"
        try:
            lines = Path(abs_path).read_text(encoding="utf-8", errors="replace").splitlines(True)
        except Exception as e:
            return f"Error reading file: {e}"
        total = len(lines)
        if start_line > 0 or end_line > 0:
            s = max(1, start_line) - 1
            e = min(total, end_line) if end_line > 0 else total
            text = "".join(lines[s:e])
            header = f"[Lines {s+1}-{e} of {total}]\n"
        else:
            text = "".join(lines)
            header = f"[{total} lines]\n"
        if len(text) > MAX_FILE_READ_CHARS:
            text = text[:MAX_FILE_READ_CHARS] + f"\n\n... [truncated at {MAX_FILE_READ_CHARS} chars]"
        return header + text

    @tool
    def grep_search(pattern: str, glob_filter: str = "*.ts") -> str:
        """Search for a regex pattern in files matching *glob_filter*.
        Returns up to 30 matching lines with file paths and line numbers."""
        import fnmatch
        results = []
        try:
            pat = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Invalid regex: {e}"
        for root, dirs, files in os.walk(target):
            # Skip noisy directories
            dirs[:] = [d for d in dirs if d not in {
                "node_modules", ".git", "dist", "build", ".turbo", ".next",
            }]
            for fname in files:
                if not fnmatch.fnmatch(fname, glob_filter):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, target)
                try:
                    for i, line in enumerate(
                        Path(fpath).read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if pat.search(line):
                            results.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(results) >= 30:
                                return "\n".join(results) + "\n... (truncated)"
                except Exception:
                    continue
        return "\n".join(results) if results else "(no matches)"

    return [list_directory, read_file, grep_search]


# ═══════════════════════════════════════════════════════════════════════════
# Enhanced Tools — Codebase Insights wrappers
# ═══════════════════════════════════════════════════════════════════════════

def make_enhanced_tools(target: str):
    """Create filesystem tools PLUS Codebase Insights-powered tools."""
    baseline = make_baseline_tools(target)

    # --- Load Codebase Insights data ---
    db_path = os.path.join(target, DB_FILENAME)
    chroma_dir = os.path.join(target, CHROMA_DIR)

    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"Index DB not found: {db_path}")
    if not os.path.isdir(chroma_dir):
        raise FileNotFoundError(f"ChromaDB dir not found: {chroma_dir}")

    # Load embeddings for vector search
    from codebase_insights.semantic_config import load_config, create_embeddings
    load_config(target)
    embeddings = create_embeddings()

    from langchain_chroma import Chroma
    symbol_store = Chroma(
        collection_name="symbol_summaries",
        embedding_function=embeddings,
        persist_directory=chroma_dir,
    )
    file_store = Chroma(
        collection_name="file_summaries",
        embedding_function=embeddings,
        persist_directory=chroma_dir,
    )

    # --- Codebase Insights tools ---

    @tool
    def get_project_summary() -> str:
        """Get a high-level AI-generated overview of the entire project.
        Includes architecture, data flow, and extension points.
        Call this FIRST to understand the codebase before diving into details."""
        try:
            con = sqlite3.connect(db_path, check_same_thread=False)
            row = con.execute(
                "SELECT summary FROM project_summary WHERE id=1"
            ).fetchone()
            con.close()
            if row:
                return row[0]
            return "Project summary not yet generated."
        except Exception as e:
            return f"Error: {e}"

    @tool
    def get_file_summary(file_path: str) -> str:
        """Get the AI-generated summary for a specific source file.
        Describes the file's responsibility, key exports, and dependencies.
        `file_path` can be relative (e.g. packages/ai-core/src/types.ts) or absolute."""
        try:
            # Convert relative path to absolute
            if not os.path.isabs(file_path):
                file_path = os.path.join(target, file_path)
            norm = os.path.normpath(file_path)
            if norm[0].islower() and len(norm) > 1 and norm[1] == ":":
                norm = norm[0].upper() + norm[1:]
            con = sqlite3.connect(db_path, check_same_thread=False)
            row = con.execute(
                "SELECT summary FROM file_summaries WHERE file_path=?", (norm,)
            ).fetchone()
            con.close()
            if row:
                return row[0]
            return f"No summary found for: {file_path}"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def semantic_search(query: str, limit: int = 10) -> str:
        """Search for code symbols by natural language description.
        Example: 'function that validates API keys', 'class for streaming chat responses'.
        Returns symbol names, types, locations, and AI-generated summaries."""
        try:
            raw = symbol_store.similarity_search_with_score(query, k=limit)
            results = []
            for doc, distance in raw:
                m = doc.metadata
                results.append(
                    f"- [{m.get('kind','')}] {m.get('name','')} "
                    f"(in {os.path.relpath(m.get('file_path',''), target)}:{m.get('def_line',0)}) "
                    f"score={1/(1+distance):.3f}\n"
                    f"  Summary: {m.get('summary', doc.page_content[:200])}"
                )
            return "\n".join(results) if results else "(no results)"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def search_files(query: str, limit: int = 10) -> str:
        """Search for source FILES by natural language description.
        Example: 'file that handles AI provider configuration'.
        Returns file paths and summaries."""
        try:
            raw = file_store.similarity_search_with_score(query, k=limit)
            results = []
            for doc, distance in raw:
                m = doc.metadata
                fp = m.get("file_path", "")
                rel = m.get("rel_path", os.path.relpath(fp, target) if fp else "")
                summary = m.get("summary", doc.page_content[:200])
                results.append(
                    f"- {rel}  (score={1/(1+distance):.3f})\n  {summary}"
                )
            return "\n".join(results) if results else "(no results)"
        except Exception as e:
            return f"Error: {e}"

    @tool
    def query_symbols(
        name_query: str = "",
        kinds: str = "",
        path: str = "",
        limit: int = 20,
    ) -> str:
        """Query the symbol index by name, kind, and/or path.
        - name_query: substring match on symbol names (case-insensitive)
        - kinds: comma-separated kind labels, e.g. 'Class,Function,Interface'
        - path: restrict to a specific file or directory (relative to repo root)
        - limit: max results (default 20)
        Returns symbol name, kind, file, line number."""
        try:
            con = sqlite3.connect(db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            conditions = []
            params: list[Any] = []

            if path:
                abs_path = os.path.join(target, path)
                norm = os.path.normpath(abs_path)
                if norm[0].islower() and len(norm) > 1 and norm[1] == ":":
                    norm = norm[0].upper() + norm[1:]
                if os.path.isfile(norm):
                    conditions.append("s.file_path = ?")
                    params.append(norm)
                else:
                    conditions.append("s.file_path LIKE ?")
                    params.append(norm.rstrip(os.sep) + os.sep + "%")

            if kinds:
                kind_list = [k.strip().capitalize() for k in kinds.split(",") if k.strip()]
                if kind_list:
                    placeholders = ",".join("?" * len(kind_list))
                    conditions.append(f"s.kind_label IN ({placeholders})")
                    params.extend(kind_list)

            if name_query:
                nq = name_query
                if "%" not in nq and "_" not in nq:
                    nq = f"%{nq}%"
                conditions.append("s.name LIKE ? COLLATE NOCASE")
                params.append(nq)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            params.append(min(limit, 200))

            rows = con.execute(
                f"""SELECT s.name, s.kind_label, s.file_path, s.def_line,
                           s.container_name,
                           (SELECT COUNT(*) FROM symbol_refs r WHERE r.symbol_id = s.id) AS ref_count
                    FROM symbols s {where}
                    ORDER BY ref_count DESC LIMIT ?""",
                params,
            ).fetchall()
            con.close()

            results = []
            for r in rows:
                rel = os.path.relpath(r["file_path"], target)
                container = f" in {r['container_name']}" if r["container_name"] else ""
                results.append(
                    f"- [{r['kind_label']}] {r['name']}{container} "
                    f"({rel}:{r['def_line']}) refs={r['ref_count']}"
                )
            return "\n".join(results) if results else "(no results)"
        except Exception as e:
            return f"Error: {e}"

    return baseline + [get_project_summary, get_file_summary, semantic_search,
                       search_files, query_symbols]


# ═══════════════════════════════════════════════════════════════════════════
# LLM Factory
# ═══════════════════════════════════════════════════════════════════════════

def create_agent_llm(tracker: TokenTracker) -> ChatOpenAI:
    """Create a ChatOpenAI instance pointing at DeepSeek API."""
    api_key = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("CODEBASE_INSIGHTS_CHAT_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "No API key found. Set DEEPSEEK_API_KEY or CODEBASE_INSIGHTS_CHAT_API_KEY."
        )
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0,
        callbacks=[tracker],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

def run_agent(mode: str, target: str, max_steps: int = 25) -> dict:
    """Run the agent in 'baseline' or 'enhanced' mode and return metrics."""
    print(f"\n{'='*70}")
    print(f"  Running agent in [{mode.upper()}] mode")
    print(f"{'='*70}\n")

    tracker = TokenTracker()
    llm = create_agent_llm(tracker)

    if mode == "baseline":
        tools = make_baseline_tools(target)
        sys_prompt = BASELINE_SYSTEM_PROMPT
    else:
        tools = make_enhanced_tools(target)
        sys_prompt = ENHANCED_SYSTEM_PROMPT

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SystemMessage(content=sys_prompt),
    )

    t0 = time.perf_counter()
    step = 0

    # Track tool usage for analysis
    tool_call_counts: dict[str, int] = {}
    files_read: set[str] = set()

    # Stream events to show progress
    final_response = ""
    for event in agent.stream(
        {"messages": [HumanMessage(content=CODING_TASK)]},
        config={"recursion_limit": max_steps * 2},
        stream_mode="updates",
    ):
        step += 1
        # Show tool calls and responses
        for node_name, node_data in event.items():
            msgs = node_data.get("messages", [])
            for msg in msgs:
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        name = tc["name"]
                        tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
                        if name == "read_file":
                            fp = tc.get("args", {}).get("file_path", "")
                            if fp:
                                files_read.add(fp)
                        print(f"  [Step {step}] 🔧 Calling: {name}({_truncate_args(tc.get('args', {}))})")
                elif hasattr(msg, "content") and msg.content and node_name == "tools":
                    preview = str(msg.content)[:120].replace("\n", " ")
                    print(f"  [Step {step}] ← Tool result: {preview}...")
                elif hasattr(msg, "content") and msg.content and node_name == "agent":
                    # Final AI message
                    final_response = msg.content

    elapsed = time.perf_counter() - t0

    metrics = {
        "mode": mode,
        "steps": step,
        "elapsed_s": round(elapsed, 1),
        **tracker.summary(),
        "response_length": len(final_response),
        "tool_calls": dict(tool_call_counts),
        "unique_files_read": len(files_read),
    }

    print(f"\n  ✅ Agent finished in {elapsed:.1f}s ({step} steps)")
    print(f"  Token usage: {tracker.input_tokens:,} in + {tracker.output_tokens:,} out = {tracker.total_tokens:,} total")
    print(f"  Tool breakdown: {dict(sorted(tool_call_counts.items()))}")
    print(f"  Unique files read: {len(files_read)}")

    return metrics


def _truncate_args(args: dict) -> str:
    """Compact repr of tool arguments for logging."""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Benchmark token usage: coding agent with vs without Codebase Insights"
    )
    parser.add_argument(
        "--target", default=TARGET_REPO,
        help=f"Target repository path (default: {TARGET_REPO})",
    )
    parser.add_argument(
        "--max-steps", type=int, default=30,
        help="Maximum agent steps per run (default: 30)",
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
    print("║   LangGraph Coding Agent — Token Consumption Benchmark         ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print(f"║  Target: {target:<54} ║")
    print(f"║  LLM:    DeepSeek Chat (temperature=0)                        ║")
    print(f"║  Max steps: {args.max_steps:<51} ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    results = []

    if args.mode in ("both", "baseline"):
        metrics = run_agent("baseline", target, args.max_steps)
        results.append(metrics)

    if args.mode in ("both", "enhanced"):
        metrics = run_agent("enhanced", target, args.max_steps)
        results.append(metrics)

    # --- Comparison Table ---
    if len(results) == 2:
        b, e = results
        print("\n")
        print("╔══════════════════════════════════════════════════════════════════╗")
        print("║                    COMPARISON RESULTS                           ║")
        print("╠══════════════════════════╦══════════════╦══════════════╦═════════╣")
        print("║ Metric                   ║   Baseline   ║   Enhanced   ║ Δ (%)   ║")
        print("╠══════════════════════════╬══════════════╬══════════════╬═════════╣")
        for label, key in [
            ("LLM Calls",        "llm_calls"),
            ("Input Tokens",     "input_tokens"),
            ("Output Tokens",    "output_tokens"),
            ("Total Tokens",     "total_tokens"),
            ("Agent Steps",      "steps"),
            ("Unique Files Read", "unique_files_read"),
            ("Elapsed (s)",      "elapsed_s"),
            ("Response Length",   "response_length"),
        ]:
            bv = b[key]
            ev = e[key]
            if bv > 0:
                delta = ((ev - bv) / bv) * 100
                delta_str = f"{delta:+.1f}%"
            else:
                delta_str = "—"
            print(f"║ {label:<24} ║ {bv:>12,} ║ {ev:>12,} ║ {delta_str:>7} ║")
        print("╠══════════════════════════╩══════════════╩══════════════╩═════════╣")
        # Token savings summary
        if b["total_tokens"] > 0:
            savings = b["total_tokens"] - e["total_tokens"]
            pct = (savings / b["total_tokens"]) * 100
            if savings > 0:
                msg = f"Enhanced mode SAVED {savings:,} tokens ({pct:.1f}% reduction)"
            else:
                msg = f"Enhanced mode used {-savings:,} MORE tokens ({-pct:.1f}% increase)"
            print(f"║ {msg:<64} ║")
        print("╚═════════════════════════════════════════════════════════════════╝")

        # --- Tool Usage Comparison ---
        all_tools = sorted(set(list(b.get("tool_calls", {}).keys()) +
                               list(e.get("tool_calls", {}).keys())))
        if all_tools:
            print("\n  Tool Usage Breakdown:")
            print(f"  {'Tool':<24} {'Baseline':>10} {'Enhanced':>10}")
            print(f"  {'-'*24} {'-'*10} {'-'*10}")
            for t in all_tools:
                bv = b.get("tool_calls", {}).get(t, 0)
                ev = e.get("tool_calls", {}).get(t, 0)
                marker = ""
                if bv == 0 and ev > 0:
                    marker = " ← CI-only"
                print(f"  {t:<24} {bv:>10} {ev:>10}{marker}")

        # --- Analysis ---
        print("\n  ┌─────────────────────────────────────────────────────────────┐")
        print("  │                        ANALYSIS                            │")
        print("  ├─────────────────────────────────────────────────────────────┤")
        b_input_per_call = b["input_tokens"] // max(b["llm_calls"], 1)
        e_input_per_call = e["input_tokens"] // max(e["llm_calls"], 1)
        print(f"  │ Avg input tokens/call:  baseline={b_input_per_call:,}  enhanced={e_input_per_call:,}")
        print(f"  │")
        print(f"  │ Key finding: In a simple ReAct agent, adding Codebase")
        print(f"  │ Insights tools provides richer context but does NOT")
        print(f"  │ automatically reduce token consumption, because:")
        print(f"  │   1. All tool results accumulate in conversation history")
        print(f"  │   2. Each subsequent LLM call re-processes ALL messages")
        print(f"  │   3. The agent still reads the same core files")
        print(f"  │   4. CI summaries ADD context rather than REPLACE reads")
        print(f"  │")
        print(f"  │ To realize token savings from CI, an agent would need:")
        print(f"  │   • Conversation window management (summarize old msgs)")
        print(f"  │   • Explicit strategy to use summaries INSTEAD of reads")
        print(f"  │   • Multi-phase approach: plan with CI, then execute")
        print(f"  └─────────────────────────────────────────────────────────────┘")

    elif results:
        r = results[0]
        print(f"\n  [{r['mode'].upper()}] Final: {r['total_tokens']:,} total tokens "
              f"({r['input_tokens']:,} in, {r['output_tokens']:,} out) "
              f"in {r['elapsed_s']}s over {r['steps']} steps")

    # Save results to JSON
    out_dir = os.path.join(os.path.dirname(__file__), "..", "benchmark_results")
    os.makedirs(out_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"agent_benchmark_{ts}.json")
    with open(out_path, "w") as f:
        json.dump({
            "timestamp": ts,
            "target": target,
            "task": CODING_TASK.strip(),
            "results": results,
        }, f, indent=2)
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
