# Agent Token Benchmark — Codebase Insights v1.1.0

**Date:** 2026-04-18  
**Codebase Insights version:** `1.1.0`  
**Model:** `gpt-5-mini` (temperature=0)  
**Task:** Implement a new Google Gemini AI provider in SyntaxSenpai  
**Target repository:** `G:\SyntaxSenpai` (Electron + React + React Native monorepo)  
**Framework:** GitHub Copilot SDK (`github-copilot-sdk`)  
**Script:** `scripts/demo_agent_benchmark.py`

---

## Executive Summary

This benchmark measures a simple question: if the agent uses Codebase Insights to navigate the repo before opening files, does it spend fewer tokens than a normal file-browsing workflow?

In this run, the answer is yes.

- Total token usage dropped from **366,388** to **301,247**.
- The agent completed the task in **12 turns instead of 17**.
- The main savings came from opening **5 files instead of 14**.
- Runtime increased slightly, from **141s** to **153s**, because the enhanced flow adds a few MCP lookups.

That is a net saving of **65,141 tokens** on a real coding task, or **17.8%** overall.

| Metric | Baseline | With Codebase Insights | Change |
|---|---:|---:|---:|
| Agent turns | 17 | 12 | **-29.4%** |
| Input tokens | 360,314 | 294,794 | **-18.2%** |
| Output tokens | 6,074 | 6,453 | +6.2% |
| **Total tokens** | **366,388** | **301,247** | **-17.8%** |
| Cache-read tokens | 326,528 | 246,016 | -24.7% |
| Elapsed time | 141 s | 153 s | +8.6% |

---

## 1. What Was Tested

The task was intentionally non-trivial: the agent had to add a new Google Gemini provider to an unfamiliar production codebase by following the existing provider pattern used for OpenAI, Anthropic, and others.

Success required the agent to do four things:

1. Find the right provider architecture in the repo.
2. Read enough code to understand the pattern.
3. Write a complete new implementation.
4. Avoid guessing.

This makes the benchmark a navigation-and-implementation test, not just a search demo.

### Modes Compared

**Baseline**

The agent used normal file-system exploration tools only: `glob`, `grep`, `view`, and `edit`.

**Enhanced**

The agent had the same baseline tools plus all Codebase Insights MCP tools. The prompt explicitly pushed a CI-first, summary-gated workflow: use CI tools to decide what matters first, then open only the files that are actually needed.

Both runs used the same committed state of `G:\SyntaxSenpai`. The repo was reset to clean state after each run.

### How Tokens Were Counted

Token counts come from the Copilot SDK `ASSISTANT_USAGE` stream event.

`total_tokens = input_tokens + output_tokens`

Cache-read tokens are shown separately by the SDK and are already included inside input tokens, so they are not added again.

---

## 2. Results

### 2.1 Tool Usage

The enhanced run replaced broad file browsing with cheap summary and lookup calls.

| Tool | Baseline | With Codebase Insights | Interpretation |
|---|---:|---:|---|
| `get_project_summary` | 0 | 1 | High-level orientation before any file read |
| `search_files` | 0 | 2 | Locate candidate files by responsibility |
| `query_symbols` | 0 | 2 | Locate exact interfaces and classes |
| `get_file_summary` | 0 | 1 | Confirm a file is worth opening |
| `glob` | 3 | 0 | Eliminated |
| `grep` | 2 | 0 | Eliminated |
| `view` | **14** | **5** | **9 fewer raw file reads** |
| `edit` | 1 | 1 | Same amount of implementation work |
| **Total tool calls** | **22** | **12** | **-45%** |

The important number here is not the extra CI calls. It is the reduction in raw file reads.

### 2.2 Token Growth Across Turns

In a ReAct-style loop, every tool result stays in context. That means exploratory file reads are expensive, because every later turn has to carry those file contents forward.

#### Baseline

The baseline agent spent most of the run figuring out where the relevant code lived.

```
Turn 1:  glob×3, report_intent        +12,496 input tokens
Turn 2:  glob×3                        +12,867
Turn 3:  view×4                        +13,999
...
Turn 14: view×1                        +27,644
Turn 15: grep×1                        +28,424
Turn 16: edit                          +28,510
Turn 17: done                          +32,072
Total input tokens:                   360,314
```

The pattern is straightforward: the agent keeps opening files, context keeps getting larger, and each later turn becomes more expensive.

#### Enhanced

The enhanced agent spent the early turns choosing targets more carefully, and only started opening source once it had narrowed the search space.

```
Turn 1:  get_project_summary           +14,879 input tokens
Turn 2:  search_files                  +15,896
Turn 3:  query_symbols                 +18,933
Turn 4:  get_file_summary              +19,901
Turn 5:  search_files                  +20,109
Turn 6:  view×2                        +23,047
Turn 7:  view×1                        +25,776
Turn 8:  query_symbols                 +27,626
Turn 9:  view×1                        +29,503
Turn 10: view×1                        +30,453
Turn 11: edit                          +32,156
Turn 12: done                          +36,515
Total input tokens:                   294,794
```

The enhanced run still accumulates context, but it delays the expensive part and does less of it.

---

## 3. Why It Helped

### Raw Source Is Expensive Context

Each `view` call injects full source text into the conversation. In the baseline run, those repeated file reads likely contributed tens of thousands of extra tokens before the agent was even ready to implement the feature.

By contrast, Codebase Insights returns compressed routing information:

- `get_project_summary` gives the architecture in a few hundred tokens.
- `search_files` gives likely files by responsibility.
- `query_symbols` identifies exact symbols without opening the file.
- `get_file_summary` acts as a gate before a costly `view`.

That is the core reason the enhanced run wins: it substitutes summary data for raw code during the discovery phase.

### This Only Works If CI Replaces Browsing

The benchmark is not claiming that "more tools always help". Earlier prompt designs did worse because the agent used CI tools first and then browsed the codebase anyway.

The useful workflow is:

1. Use CI tools to narrow the search space.
2. Open source only after the summaries point to the right files.
3. Treat fewer `view` calls as an explicit optimization target.

If the agent uses CI as an extra step before normal browsing, token usage goes up. If it uses CI to avoid unnecessary browsing, token usage goes down.

### Approximate Compression

Typical CI responses are much smaller than raw file contents:

| CI tool | Typical response size |
|---|---|
| `get_project_summary` | ~600-1,000 tokens |
| `search_files` | ~300-600 tokens |
| `query_symbols` | ~200-500 tokens |
| `get_file_summary` | ~200-400 tokens |

In this run, roughly six CI calls added about **3k-4k tokens** of navigation context, while avoiding nine file reads likely saved **30k-60k tokens** of raw code context.

That is the practical trade: pay a small amount for routing, avoid a large amount of irrelevant source.

### Why Runtime Increased Slightly

The enhanced run was slightly slower, by about **12 seconds**.

That increase is expected. MCP calls have network and server overhead, and the agent spends a bit more time reasoning from summaries before it starts editing. For this task, that latency cost was small relative to the token savings.

---

## 4. Limitations

- This is a **single task, single run**. The result is directionally useful, but not statistically strong on its own.
- Most input tokens here are **cache reads**, so the economic impact depends on how your platform bills cached context.
- This benchmark favors tasks that require **codebase navigation**. Gains will be smaller when the target file is already known.
- The result depends on the model actually following the workflow. A weaker or less obedient model may ignore the CI-first guidance.
- The result also depends on **index freshness**. If summaries are stale, navigation quality will degrade and the token benefit will shrink.

---

## 5. Reproducing the Benchmark

```powershell
# 1. Start the Codebase Insights MCP server
codebase-insights G:\SyntaxSenpai

# 2. Run both benchmark modes
python scripts/demo_agent_benchmark.py --mode both --model gpt-5-mini
```

Results are written to `benchmark_results/copilot_sdk_benchmark_<timestamp>.json`.

### Requirements

- `github-copilot-sdk` installed
- Copilot CLI authenticated
- `DEEPSEEK_API_KEY` set for CI summarization
- Ollama running locally with `bge-m3`
- `G:\SyntaxSenpai` indexed and configured with `.codebase-insights.toml`

---

*Generated from `benchmark_results/copilot_sdk_benchmark_20260418_031408.json`*
