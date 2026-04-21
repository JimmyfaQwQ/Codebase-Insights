# Agent Token Benchmark — Codebase Insights v1.1.0

**Date:** 2026-04-21  
**Codebase Insights version:** `1.1.0`  
**Model:** `gpt-5-mini`  
**Task:** End-to-end backup import and restore workflow in SyntaxSenpai  
**Task ID:** `syntaxsenpai-backup-import-restore`  
**Target repository:** `G:\SyntaxSenpai` (Electron + React + React Native monorepo)  
**Framework:** GitHub Copilot SDK (`github-copilot-sdk`)  
**Script:** `scripts/demo_agent_benchmark.py`  
**Task config:** `scripts/demo_agent_benchmark_tasks.json`  
**Runs analyzed:** five consecutive result files from `benchmark_results/` dated 2026-04-20 to 2026-04-21

---

## Executive Summary

This report analyzes the five most recent demo-agent benchmark runs for the same implementation task, same model, and same target revision family. The question is the same in every run: does a CI-first, summary-gated workflow actually replace exploratory repo browsing, or does it just add more tool calls?

Across all five runs, the answer is consistent: Codebase Insights reduced exploratory reading and cut token usage materially.

- Total token usage fell from **7,036,458** to **3,189,240** across the five runs.
- That is a combined reduction of **3,847,218 tokens**, or **54.7%**.
- Raw `view` calls fell from **73** to **22** across the same runs.
- Agent turns fell from **187** to **116**.
- Aggregate wall-clock time dropped from **2068.8s** to **1329.1s**.

| Metric | Baseline total | Enhanced total | Change |
|---|---:|---:|---:|
| Agent turns | 187 | 116 | **-38.0%** |
| Input tokens | 6,967,040 | 3,128,182 | **-55.1%** |
| Output tokens | 69,418 | 61,058 | -12.0% |
| **Total tokens** | **7,036,458** | **3,189,240** | **-54.7%** |
| Cache-read tokens | 6,671,744 | 2,936,064 | -56.0% |
| Elapsed time | 2068.8 s | 1329.1 s | **-35.8%** |
| Raw `view` calls | 73 | 22 | **-69.9%** |
| Total tool calls | 202 | 123 | **-39.1%** |

The strongest single run saved **76.9%** of total tokens. The weakest still saved **40.8%**. The median run saved **46.0%**.

---

## 1. What Was Tested

All five runs used the same benchmark task family from `scripts/demo_agent_benchmark_tasks.json`:

- task id: `syntaxsenpai-backup-import-restore`
- goal: implement a real desktop backup import and restore workflow
- target repo: `G:\SyntaxSenpai`
- model: `gpt-5-mini`

The enhanced mode did not just attach more tools. It added an explicit exploration policy that forced the agent to:

1. call `lsp_capabilities`
2. call `get_indexer_criteria`
3. call `get_project_summary`
4. use `semantic_search`, `query_symbols`, and `get_file_summary` before opening raw source
5. treat unnecessary `view` calls as a benchmark failure mode

That distinction matters. This benchmark is measuring whether Codebase Insights replaces raw browsing during discovery, not whether a larger tool list changes model behavior by itself.

### Modes Compared

**Baseline**

The agent used ordinary browsing tools such as `grep`, `glob`, `view`, `edit`, and `create`.

**Enhanced**

The agent used the same editing tools plus Codebase Insights MCP tools with a CI-first, summary-gated prompt.

Both modes ran against the same task prompt, and the benchmark runner recorded the exact `task_id`, full prompts, target revision, and context logs in each JSON result.

---

## 2. Aggregate Results

### 2.1 Per-run summary

| Run | Baseline total | Enhanced total | Token change | Turn change | Time change | `view` change |
|---|---:|---:|---:|---:|---:|---:|
| `20260420_210449` | 2,073,095 | 479,798 | **-76.9%** | -63.5% | -51.8% | -78.9% |
| `20260421_132857` | 1,031,326 | 579,800 | **-43.8%** | -26.7% | -34.6% | -64.3% |
| `20260421_134232` | 1,807,472 | 1,070,475 | **-40.8%** | -23.3% | -16.3% | -66.7% |
| `20260421_140824` | 1,098,101 | 504,554 | **-54.1%** | -39.4% | -51.2% | -72.7% |
| `20260421_141909` | 1,026,464 | 554,613 | **-46.0%** | -24.1% | -0.6% | -63.6% |

### 2.2 Central tendency

| Metric | Baseline mean | Enhanced mean | Mean change | Median change |
|---|---:|---:|---:|---:|
| Total tokens | 1,407,291.6 | 637,848.0 | **-52.3%** | **-46.0%** |
| Input tokens | 1,393,408.0 | 625,636.4 | **-52.7%** | **-46.4%** |
| Agent turns | 37.4 | 23.2 | **-35.4%** | **-26.7%** |
| Elapsed time | 413.8 s | 265.8 s | **-30.9%** | **-34.6%** |
| Raw `view` calls | 14.6 | 4.4 | **-69.3%** | **-66.7%** |
| Total tool calls | 40.4 | 24.6 | **-37.8%** | **-41.7%** |

The most stable improvement is not elapsed time. It is exploratory reading: every enhanced run cut `view` calls sharply, and every enhanced run cut total tokens by at least 40%.

---

## 3. What Changed In Agent Behavior

The JSON context logs show a consistent behavioral split between the two modes.

### 3.1 Baseline pattern: grep and browse

In all five baseline runs, the agent started by trying to discover ownership with repeated raw search and file opening:

- `grep` loops for terms like `backup`, `restore`, `export`, `import`, `chats.json`, or `store:replaceSnapshot`
- follow-up `view` calls on candidate files to see whether they mattered
- more `grep` after each dead end or partial clue
- implementation only after the context had already grown substantially

Across the five runs, baseline used:

- **62** `grep` calls
- **73** `view` calls

This is exactly the expensive browse-first pattern the project is trying to avoid.

### 3.2 Enhanced pattern: orient, route, then read

In every enhanced run, the early turns were much more structured:

1. `lsp_capabilities`
2. `get_indexer_criteria`
3. `get_project_summary`
4. `semantic_search` for backup/export/restore behavior
5. `get_file_summary` on the likely owner files
6. only then `view` on confirmed edit targets

Across the five runs, enhanced used:

- **41** Codebase Insights calls before or instead of broad browsing
- only **22** raw `view` calls total
- zero raw `grep` calls in the successful enhanced runs analyzed here

The important point is not that enhanced had more tool types. The important point is that it spent the discovery budget on compact routing information instead of raw source text.

### 3.3 Why the token savings are so large

This benchmark behaves like a normal ReAct loop: tool outputs stay in context and make later turns more expensive.

That means the expensive action is not merely searching. The expensive action is opening source files that turn out not to be needed.

The enhanced prompt consistently reduced that waste by doing three things first:

- orient on repo structure
- identify owner files by intent
- confirm file responsibility before opening source

That is why the strongest gains correlate with the largest reduction in `view` calls.

---

## 4. Run-by-run Notes

### Run `20260420_210449`

This is the clearest example of the benchmark working as intended.

- Baseline needed **52 turns**, **56** tool calls, and **19** file reads.
- Enhanced finished in **19 turns**, **25** tool calls, and **4** file reads.
- Total tokens fell by **76.9%**.

The context log shows baseline spending sixteen turns on discovery before converging, while enhanced routed quickly through project summary, semantic search, and file summaries.

### Run `20260421_132857`

This run still shows a strong improvement, but it also exposes an operational caveat.

- Total tokens fell by **43.8%**.
- Elapsed time fell by **34.6%**.
- Enhanced hit repeated Windows file-lock retries when restoring `.codebase-index.db` after the run.

The lock issue affected cleanup, not the benchmark outcome, but it is worth documenting because it adds tail latency and can confuse operator expectations.

### Run `20260421_134232`

This run shows that the benchmark is about workflow quality, not just tool availability.

- Total tokens still fell by **40.8%**, the weakest but still substantial improvement.
- Enhanced used more planning/reporting turns and a few extra summary calls.
- Enhanced also converged on a leaner edit set, while baseline wandered through a broader architecture path.

This is a useful reminder that CI-first prompting can still underperform its best-case potential if the agent spends too many turns narrating or over-planning.

### Run `20260421_140824`

This run is close to the ideal pattern.

- Total tokens fell by **54.1%**.
- Elapsed time fell by **51.2%**.
- Raw file reads dropped from **11** to **3**.

The enhanced run behaved almost exactly like the intended workflow in `scripts/demo_agent_benchmark.py`: route first, then inspect only the confirmed owner files.

### Run `20260421_141909`

This run is the best example of why time is a secondary metric.

- Total tokens fell by **46.0%**.
- Turns fell by **24.1%**.
- Raw file reads fell from **11** to **4**.
- Elapsed time was almost flat: only **0.6%** faster.

The enhanced workflow still reduced browsing cost materially, but CI lookup overhead offset most of the wall-clock gain. That does not weaken the token result. It shows that time savings are environment-sensitive, while token savings from reduced exploratory reading are more robust.

---

## 5. Key Findings

### 5.1 The benchmark is now measuring the right thing

The recent JSON files contain enough context to explain behavior, not just outcomes:

- `task_id` and `task_title`
- exact baseline and enhanced prompts
- target revision
- per-mode tool breakdown
- per-mode final responses
- full context logs

That makes these five runs stronger evidence than the older single-run archived benchmark, because we can see whether the enhanced mode really replaced browsing rather than merely adding MCP calls.

### 5.2 View-count reduction is the leading indicator

The strongest correlation in these runs is:

- fewer exploratory `view` calls
- fewer total input tokens
- fewer turns

That lines up exactly with the project thesis: token savings come from avoiding irrelevant raw code in context, not from raw search speed alone.

### 5.3 Time improvement is real but less stable

Elapsed time improved in four runs materially and was nearly flat in one run. The likely reasons, visible in the logs, are:

- CI startup or health-check overhead
- cleanup and Windows file locking during artifact restore
- cases where the agent still spent extra turns narrating plan changes

So the stable story is token efficiency first, latency improvement second.

### 5.4 Prompt discipline matters

The enhanced prompt worked because it imposed hard behavioral constraints, not because it merely mentioned the CI tools.

The most effective parts were:

- mandatory startup calls
- explicit preference for semantic and summary tools
- treating unnecessary `view` calls as a failure mode
- explicit rerouting rule after too many opened files without edits

That is the difference between a browse-first workflow with extra tools and a genuine summary-gated workflow.

---

## 6. Caveats

- This is still one task family, not a broad benchmark suite across many repositories.
- Some enhanced runs completed without actually executing tests, even when they created validation artifacts. Functional parity in the final response does not guarantee equal validation depth.
- Cache-read tokens dominate input in both modes, so platform billing specifics may change the economic interpretation.
- Cleanup on Windows can add noise because `.codebase-index.db` may remain locked briefly after the enhanced run.

Even with those caveats, the direction of the result is strong because all five runs point the same way.

---

## 7. Reproducing The Benchmark

```powershell
# 1. Start Codebase Insights for the target repo
codebase-insights G:\SyntaxSenpai

# 2. List available benchmark tasks
python scripts/demo_agent_benchmark.py --list-tasks

# 3. Run the exact task used in this report
python scripts/demo_agent_benchmark.py --mode both --model gpt-5-mini --task-id syntaxsenpai-backup-import-restore

# 4. Optional: watch the runs live in the side-by-side GUI
python scripts/demo_agent_benchmark.py --mode both --model gpt-5-mini --task-id syntaxsenpai-backup-import-restore --gui
```

Recent raw results analyzed in this report:

- `benchmark_results/copilot_sdk_benchmark_20260420_210449.json`
- `benchmark_results/copilot_sdk_benchmark_20260421_132857.json`
- `benchmark_results/copilot_sdk_benchmark_20260421_134232.json`
- `benchmark_results/copilot_sdk_benchmark_20260421_140824.json`
- `benchmark_results/copilot_sdk_benchmark_20260421_141909.json`

Full prompt and agent context logs for those five runs are preserved in [benchmarks/agent-token-benchmark-context-v1.1.0.md](benchmarks/agent-token-benchmark-context-v1.1.0.md).

---

## 8. Bottom Line

Across five consecutive real implementation runs on the same task, Codebase Insights did what it is supposed to do: it changed the agent's search pattern.

The enhanced agent did not just look smarter in the abstract. It opened fewer files, spent fewer turns rediscovering the repo, and consumed far fewer tokens while still completing the task.

That is the practical benchmark claim for v1.1.0: a CI-first, summary-gated workflow is no longer a one-off win. On this task, it is repeatable.
