---
name: agent-token-benchmark
description: 'Benchmark Codebase Insights as an agent workflow accelerator by measuring token usage, turns, tool calls, and elapsed time with and without CI tools on the same coding task. Use when: running the Copilot SDK A/B benchmark, validating CI-first prompt changes, comparing browse-first vs summary-gated agent behavior, or producing an agent token benchmark report.'
argument-hint: 'Target repo path, e.g. "G:\\MyProject\\"'
---

# Agent Token Benchmark — codebase-insights

## When to Use
- Measuring whether Codebase Insights reduces agent token usage on a real coding task
- Comparing baseline file-browsing behavior against a CI-first workflow
- Validating prompt changes that are supposed to reduce `view` calls
- Producing or updating an agent token benchmark report
- Demonstrating CI value to agent users with a concrete A/B result

---

## Script

Use `scripts/demo_agent_benchmark.py`.

This script runs the same task in two modes:

- `baseline`: no Codebase Insights MCP server attached
- `enhanced`: Codebase Insights MCP server attached with a CI-first, summary-gated prompt

It captures:

- input tokens
- output tokens
- total tokens
- cache read tokens
- agent turns
- tool-call breakdown
- elapsed time

Results are written to `benchmark_results/copilot_sdk_benchmark_<timestamp>.json`.

`benchmark_results/` is scratch output. Do not keep it around indefinitely.

---

## How to Run

### Enhanced prerequisites

Before running enhanced mode, start the Codebase Insights MCP server against the target repo:

```powershell
codebase-insights G:\MyProject
```

### Typical invocations

```powershell
# Baseline only
python scripts/demo_agent_benchmark.py --target "G:\MyProject" --mode baseline

# Enhanced only
python scripts/demo_agent_benchmark.py --target "G:\MyProject" --mode enhanced

# Both modes, same model, same target
python scripts/demo_agent_benchmark.py --target "G:\MyProject" --mode both --model gpt-5-mini
```

### How to invoke from an agent — critical

Use `run_in_terminal` with:

- `mode=sync`
- `timeout=0`

Do not use async mode for this script.

---

## Requirements

- `github-copilot-sdk` installed
- Copilot CLI installed and authenticated
- Target repo available locally
- For enhanced mode: Codebase Insights MCP server running on port `6789`
- The target repo should be in a reproducible state before each run

## Cleanup

After you have extracted the metrics you need or written the benchmark report, delete `benchmark_results/`.

```powershell
Remove-Item -Recurse -Force benchmark_results\
```

Keep the versioned markdown report under `benchmarks/`. Treat `benchmark_results/` as local scratch data only.

---

## Benchmark Design Rules

To make the result meaningful:

1. Use the same coding task in both modes.
2. Use the same model in both modes.
3. Run against the same committed target repo state.
4. Reset target repo changes between runs.
5. Do not change the task halfway through the comparison.

The benchmark is about workflow differences, not task differences.

---

## What the Enhanced Prompt Is Testing

The enhanced prompt is not testing “more tools”. It is testing whether the agent uses Codebase Insights to avoid unnecessary raw file reads.

The intended workflow is:

1. `get_project_summary`
2. `search_files` / `semantic_search` / `query_symbols`
3. `get_file_summary`
4. raw source reads only for confirmed-relevant sections

If the agent uses CI tools and still browses the repo normally, token usage can increase instead of decrease.

---

## What to Look For in the Output

Primary metrics:

- lower `total_tokens`
- fewer `turn_count`
- fewer raw file-read tools such as `view`

Secondary metrics:

- stable or slightly higher elapsed time is acceptable
- output tokens may stay flat or rise slightly
- cache-read tokens can dominate total input, so interpret them carefully

The most important question is whether the enhanced run reduced exploratory reading.

---

## Report Checklist

When writing a report from this script, capture:

1. task description
2. model and framework
3. baseline vs enhanced comparison table
4. tool-call comparison
5. why the enhanced workflow helped or failed
6. limitations of the run
7. exact reproduction command

Store versioned reports under `benchmarks/`.

After the report is finalized, delete `benchmark_results/`.

---

## Common Failure Modes

- MCP server not running for enhanced mode
- target repo not reset between runs
- prompt changes that accidentally alter the task itself
- model ignores the CI-first instructions and reads too many files anyway
- drawing conclusions from a single run without stating that limitation

---

## Recommended Follow-up

If the enhanced run does not save tokens:

1. inspect tool-call breakdown first
2. check whether the agent still used too many raw file reads
3. tighten the prompt so summaries gate source reads
4. rerun the comparison on the same task