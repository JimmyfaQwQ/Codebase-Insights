# Agent Token Benchmark — Codebase Insights as a Navigator

**Date:** 2026-04-18  
**Model:** `gpt-5-mini` (temperature=0)  
**Task:** Implement a new Google Gemini AI provider in SyntaxSenpai  
**Target repository:** `G:\SyntaxSenpai` (Electron + React + React Native monorepo)  
**Framework:** GitHub Copilot SDK (`github-copilot-sdk`)  
**Script:** `scripts/copilot_sdk_benchmark.py`

---

## Executive Summary

Using Codebase Insights as a **navigator** — a substitute for file browsing rather than a supplement to it — reduced token consumption by **17.8%** and agent turns by **29.4%** on a real-world implementation task.

| Metric | Baseline | + Codebase Insights | Δ |
|---|---:|---:|---|
| Agent turns | 17 | 12 | **−29.4%** |
| Input tokens | 360,314 | 294,794 | **−18.2%** |
| Output tokens | 6,074 | 6,453 | +6.2% |
| **Total tokens** | **366,388** | **301,247** | **−17.8%** |
| Cache-read tokens | 326,528 | 246,016 | −24.7% |
| Elapsed | 141 s | 153 s | +8.6% |

**65,141 tokens saved** per task run.

---

## 1. Methodology

### Task

> You are working on the SyntaxSenpai project (a cross-platform AI companion app
> built with Electron + React).
>
> Your task: Write a NEW AI provider implementation for Google Gemini.
> - Explore how existing providers (OpenAI, Anthropic, etc.) are structured.
> - Write the COMPLETE implementation following the exact same patterns.
> - Support streaming chat completions and tool calls in the same format.
> - Write the file to disk at the appropriate location.

The agent must navigate an unfamiliar codebase, understand its patterns, and produce a non-trivial implementation — a realistic coding-agent workflow.

### Two modes

**Baseline** — standard file-system tools only (`glob`, `grep`, `view`, `edit`). The agent is told to read the relevant source code and not guess.

**Enhanced** — same tools plus all 15 Codebase Insights MCP tools. The prompt teaches a **navigate-then-read** workflow: use CI tools as a _substitute_ for file browsing, not as a warm-up before it.

Both modes ran against the same Git-committed state of `G:\SyntaxSenpai`. The target repo was reset to a clean state after each run (`git checkout -- . && git clean -fd`).

### Token counting

Tokens are accumulated from the `ASSISTANT_USAGE` event in the Copilot SDK stream.  
`total_tokens = input_tokens + output_tokens` (does not double-count cache reads — those are a subset of input tokens reported separately).

---

## 2. Results

### 2.1 Tool usage comparison

| Tool | Baseline | Enhanced | Notes |
|---|---:|---:|---|
| `get_project_summary` | 0 | 1 | CI: orient before any file read |
| `search_files` | 0 | 2 | CI: locate modules by description |
| `query_symbols` | 0 | 2 | CI: find exact interfaces/classes |
| `get_file_summary` | 0 | 1 | CI: scan cheaply before committing to view |
| `glob` | 3 | 0 | Replaced by CI |
| `grep` | 2 | 0 | Replaced by CI |
| `view` | **14** | **5** | −9 raw file reads |
| `edit` | 1 | 1 | Same implementation effort |
| **Total tool calls** | **22** | **12** | −45% |

### 2.2 Token breakdown by turn

#### Baseline (17 turns)

The agent spent turns 2–15 on exploration — running `glob` to discover directories, then `view`-ing files sequentially without prior knowledge of which ones mattered. Each `view` result accumulated in context, raising the per-turn input token cost monotonically from ~13k to ~28k.

```
Turn 1:  glob×3, report_intent        +12,496 in
Turn 2:  glob×3                        +12,867 in
Turn 3:  view×4                        +13,999 in
...
Turn 14: view×1 (final read)           +27,644 in
Turn 15: grep×1                        +28,424 in
Turn 16: edit (implementation)         +28,510 in
Turn 17: (done)                        +32,072 in
Total:                                360,314 in
```

#### Enhanced (12 turns)

CI tools front-loaded orientation work in turns 1–5, after which the agent had enough precision to open exactly the right file sections. The `view` calls started later (turn 6) and targeted specific line ranges.

```
Turn 1:  get_project_summary, report_intent   +14,879 in
Turn 2:  search_files                         +15,896 in
Turn 3:  query_symbols                        +18,933 in
Turn 4:  get_file_summary                     +19,901 in
Turn 5:  search_files                         +20,109 in
Turn 6:  view×2 (targeted sections)           +23,047 in
Turn 7:  view×1                               +25,776 in
Turn 8:  query_symbols                        +27,626 in
Turn 9:  view×1                               +29,503 in
Turn 10: view×1                               +30,453 in
Turn 11: edit (implementation)                +32,156 in
Turn 12: (done)                               +36,515 in
Total:                                       294,794 in
```

---

## 3. Analysis

### Why the navigate-then-read workflow reduces tokens

The key insight is that in a ReAct-style agent loop, every tool result remains in the conversation history. The per-turn input token cost is roughly:

```
input_tokens(turn N) ≈ system_prompt + task + sum(all prior turns + results)
```

Each `view` of a file adds the **full file content** to that sum. In the baseline, 14 `view` calls added an estimated 80–150k tokens of raw source code to the context before the agent had enough information to start writing.

CI tools return **pre-digested summaries** instead of raw source. A `get_file_summary` response is typically 200–400 tokens vs. 3,000–8,000 tokens for a full `view`. The agent gets the same routing signal at a fraction of the cost, then reads only the 1–2 files it truly needs.

### Why this is *not* just adding CI tokens on top

An earlier experiment (prompt v1 — CI tools as orientation before file browsing) showed **+281% token overhead** because the agent used CI tools *and then* read every file anyway. The critical change in prompt v2 is:

1. **Explicit prohibition** on `glob`/directory browsing
2. **Gating `view` on `get_file_summary` first** — open a file only if its summary confirms it's relevant
3. **Framing success as "fewest view calls"** — the agent's explicit goal

This changed the behavioral pattern from _supplement_ to _substitute_.

### Token cost of the CI tools themselves

| CI tool call | Typical response size |
|---|---|
| `get_project_summary` | ~600–1,000 tokens |
| `search_files` | ~300–600 tokens (5 results × ~100 tokens each) |
| `query_symbols` | ~200–500 tokens |
| `get_file_summary` | ~200–400 tokens |

Six CI calls ≈ 3,000–4,000 tokens added to context.  
Nine replaced `view` calls ≈ 30,000–60,000 tokens avoided.

Net: roughly **10:1 compression ratio** for navigation context.

### Why elapsed time increased slightly (+8.6%)

CI tools call a running local MCP server (HTTP round-trips). Each call adds ~50–200ms. With 6 CI calls and slightly more thinking time per turn (the agent is doing more reasoning with summaries), the 12-second increase is expected and negligible.

---

## 4. Prompt Design

The navigate-then-read prompt is reproduced in full in `scripts/copilot_sdk_benchmark.py` as `CODING_TASK_ENHANCED`.

The reusable form is published in `.github/skills/codebase-insights-navigator/SKILL.md`.

**Key prompt principles:**

1. **Framing** — "use CI tools as a substitute for file browsing, not as a warm-up before it"
2. **Explicit 4-step workflow** — project summary → file search → file summary gate → targeted view
3. **Prohibition** — "do NOT use glob, do NOT browse directories"
4. **Success metric** — "the measure of success: complete the task with the fewest view calls"

---

## 5. Limitations and Caveats

- **Single task, single run** — token counts are from one execution; LLM non-determinism at temperature=0 is low but not zero.
- **Cache effects** — ~90% of input tokens are cache reads (prompt caching). Savings apply to both modes proportionally; net new token cost difference is smaller in absolute terms.
- **Task type matters** — savings will be higher for tasks requiring broad codebase exploration (e.g. "find all places that do X") and lower for tasks with a known file target (e.g. "add a parameter to function Y in file Z").
- **Model capability** — `gpt-5-mini` follows the CI-as-navigator instructions well. Weaker models may ignore the workflow directives and revert to glob/view patterns despite the prompt.
- **CI index freshness** — semantic summaries must be up-to-date for `get_file_summary` and `search_files` to return useful results. Stale summaries degrade the quality of CI-guided navigation.

---

## 6. Reproducing This Benchmark

```powershell
# 1. Start the CI MCP server
codebase-insights G:\SyntaxSenpai

# 2. Run both modes
python scripts/copilot_sdk_benchmark.py --mode both --model gpt-5-mini
```

Results are saved to `benchmark_results/copilot_sdk_benchmark_<timestamp>.json`.

### Requirements

- `github-copilot-sdk` (`pip install github-copilot-sdk`)
- Copilot CLI authenticated (`copilot --version`)
- DeepSeek API key in `DEEPSEEK_API_KEY` env var (used by the CI MCP server for summarization)
- Ollama with `bge-m3` embeddings running locally (used by the CI MCP server for vector search)
- `G:\SyntaxSenpai` with a valid `.codebase-insights.toml` and pre-built index

---

*Generated from `benchmark_results/copilot_sdk_benchmark_20260418_031408.json`*
