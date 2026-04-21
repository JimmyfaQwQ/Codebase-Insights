# Benchmark Report — Codebase Insights v0.2.0

**Date:** 2026-04-16  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary

- **Full pipeline time:** **579.11s**
- **Peak RSS:** **2789.2 MiB**
- **No-change catch-up:** 10.0s  (indexed=0, skipped=118)
- **Storage footprint:** 6.02 MB SQLite + 6.80 MB ChromaDB

---

## 1. Environment

| Component | Version / Detail |
|---|---|
| Codebase Insights | **0.2.0** |
| Python | Python 3.11.7 |
| typescript-language-server | N/A |
| clangd | clangd version 18.1.8 |
| pylsp | pylsp v1.7.2 |

### Target repository statistics

| Metric | Value |
|---|---:|
| Files processed | 118 (114 indexed, 4 skipped) |
| Total symbols indexed (LSP) | 5,588 |
| Cross-references | 32,915 |
| Semantically summarised | 527 (9.4% of total, `min_ref_count=1`) |

---

## 2. Instrumentation Verification

All `[BENCHMARK:*]` markers present: **✓ YES**

---

## 3. Full Rebuild

| Phase | Metric | Value |
|---|---|---:|
| STARTUP | lsp_cpp_start_server | 0.036s |
| STARTUP | lsp_cpp_initialize | 0.236s |
| STARTUP | lsp_javascript/typescript_start_server | 0.018s |
| STARTUP | lsp_javascript/typescript_initialize | 0.430s |
| INDEXER | wall_time | 225.72s |
| INDEXER | files_total | 118 |
| INDEXER | indexed | 114 |
| INDEXER | skipped | 4 |
| INDEXER | errors | 0 |
| INDEXER | total_symbols | 5588 |
| INDEXER | total_refs | 32915 |
| SEMANTIC | wall_time | 183.31s |
| SEMANTIC | pending_query | 0.039s |
| SEMANTIC | context_extract | 0.13s |
| SEMANTIC | llm_batch | 144.86s |
| SEMANTIC | chroma_insert | 37.88s |
| SEMANTIC | sqlite_write | 0.35s |
| SEMANTIC | symbols_total | 527 |
| SEMANTIC | summarised | 527 |
| SEMANTIC | skipped | 0 |
| SEMANTIC | errors | 0 |
| SEMANTIC | input_tokens | 147181 |
| SEMANTIC | output_tokens | 27113 |
| FILE_SUMMARIES | wall_time | 36.88s |
| FILE_SUMMARIES | files_pending | 114 |
| PROJECT_SUMMARY | wall_time | 116.09s |
| PROJECT_SUMMARY | mode | full |
| SIZES | sqlite_bytes | 6307840 |
| SIZES | chroma_bytes | 7127368 |
| SIZES | sqlite_mb | 6.02 |
| SIZES | chroma_mb | 6.80 |

**Wall-clock (to indexing done):** `579.11s`  
**Peak RSS:** `2789.2 MiB`  
**Avg CPU:** `0.0%`

---

## 4. Incremental Update Scenarios

Leaf file: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
Core file: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`

| Scenario | Watchdog+LSP | Semantic | File Summaries | Proj Summary | Total |
|---|---|---|---|---|---|
| A: No-change restart | 0.06s | 0.07s | — | — | 10.0s |
| B: Leaf file edit (main.ts) | — | 4.18s | — | — | 9.01s |
| C: Core file edit (useTheme.ts) | — | 5.10s | — | 113.31s | 119.05s |
| D: New file added | — | 0.02s | 3.99s | 5.93s | 63.09s |

### Per-scenario BENCHMARK lines

#### Scenario A: No-change restart
- `[BENCHMARK:STARTUP]` `lang_detect=0.052s`  `semantic_init=4.540s`  `indexer_create=0.000s`  `total_pre_server=5.358s`  `lsp_javascript/typescript_start_server=0.025s`  `lsp_javascript/typescript_initialize=0.430s`  `lsp_cpp_start_server=0.016s`  `lsp_cpp_initialize=0.252s`
- `[BENCHMARK:INDEXER]` `wall_time=0.06s`  `files_total=118`  `indexed=0`  `skipped=118`  `errors=0`  `total_symbols=5588`  `total_refs=32915`
- `[BENCHMARK:SEMANTIC]` `wall_time=0.07s`  `pending_query=0.023s`  `context_extract=0.03s`  `llm_batch=0.00s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=527`  `summarised=0`  `skipped=527`  `errors=0`  `input_tokens=0`  `output_tokens=0`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6656000`  `chroma_bytes=7127368`  `sqlite_mb=6.35`  `chroma_mb=6.80`

#### Scenario B: Leaf file edit (main.ts)
File: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=4.18s`  `pending_query=0.025s`  `context_extract=0.00s`  `llm_batch=3.41s`  `chroma_insert=0.73s`  `sqlite_write=0.02s`  `symbols_total=1`  `summarised=1`  `skipped=0`  `errors=0`  `input_tokens=122`  `output_tokens=44`

#### Scenario C: Core file edit (useTheme.ts)
File: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=5.10s`  `pending_query=0.026s`  `context_extract=0.00s`  `llm_batch=4.36s`  `chroma_insert=0.68s`  `sqlite_write=0.01s`  `symbols_total=8`  `summarised=8`  `skipped=0`  `errors=0`  `input_tokens=1242`  `output_tokens=378`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=113.31s`  `mode=incremental`  `changes=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6656000`  `chroma_bytes=7127368`  `sqlite_mb=6.35`  `chroma_mb=6.80`

#### Scenario D: New file added
File: `_bench_auto_scenario_d.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=0.02s`  `pending_query=0.021s`  `context_extract=0.00s`  `llm_batch=0.00s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=4`  `summarised=0`  `skipped=4`  `errors=0`  `input_tokens=0`  `output_tokens=0`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=3.99s`  `files_pending=1`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=5.93s`  `mode=incremental`  `changes=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6656000`  `chroma_bytes=7311688`  `sqlite_mb=6.35`  `chroma_mb=6.97`

---

## 5. LSP Navigation

| Tool | Result | Detail |
|---|:---:|---|
| capabilities | ✓ | cpp, javascript/typescript |
| languages | ✓ |  |
| document_symbols | ✓ | count=14; types.ts |
| hover | ✓ | types.ts |
| definition | ✓ | symbol=ChatRequest |
| references | ✓ | count=2; symbol=StreamChunk |
| implementation | ✓ | count=0; symbol=ChatRequest |
| query_symbols | ✓ | count=5 |

---

## 6. Retrieval Quality

### 6.1 Symbol Search (`semantic_search` + `query_symbols` baseline)

**Hit@1:** 1/17 (5.9%)  
**Hit@3:** 3/17 (17.6%)  
**Hit@5:** 15/17 (88.2%)

| # | Query | Expected | Semantic top-1 | Keyword hit | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | abstract base class for AI provider implementati | `BaseAIProvider` | `OpenAICodexProvider` | ✓ | ✗ | ✗ | ✓ |
| 2 | AI provider interface contract with chat and str | `AIProvider` | `OpenAIProvider` | ✗ | ✗ | ✗ | ✓ |
| 3 | chat request sent to an LLM model | `ChatRequest` | `AIChatRuntime` | ✓ | ✗ | ✗ | ✓ |
| 4 | streaming response chunk from LLM | `StreamChunk` | `stream` | ✓ | ✗ | ✗ | ✗ |
| 5 | centralized registry for registering and dispatc | `ToolRegistry` | `registerTerminalIpc` | ✓ | ✗ | ✗ | ✓ |
| 6 | waifu personality traits on a numerical scale | `WaifuPersonalityTraits` | `WaifuStateSyncPayload` | ✓ | ✗ | ✗ | ✓ |
| 7 | waifu companion data model with name and avatar | `Waifu` | `ProviderModelOption` | ✗ | ✗ | ✗ | ✓ |
| 8 | build the complete system prompt for a waifu int | `buildSystemPrompt` | `getDesktopSystemPromptBlock` | ✓ | ✗ | ✗ | ✓ |
| 9 | relationship and context used to construct waifu | `SystemPromptContext` | `getDesktopSystemPromptBlock` | ✓ | ✗ | ✓ | ✓ |
| 10 | platform-native adapter for storing encrypted AP | `KeystoreAdapter` | `saveApiKey` | ✓ | ✗ | ✗ | ✓ |
| 11 | convert messages to OpenAI-compatible message fo | `convertToOpenAIMessages` | `SendMessageOptions` | ✓ | ✗ | ✗ | ✓ |
| 12 | options passed when creating a new AI provider i | `CreateProviderOptions` | `CreateProviderOptions` | ✓ | ✓ | ✓ | ✓ |
| 13 | WebSocket message envelope for phone to desktop  | `WSMessage` | `Message` | ✓ | ✗ | ✗ | ✓ |
| 14 | mobile initiates pairing with desktop over WebSo | `PairRequestPayload` | `WSMessage` | ✓ | ✗ | ✗ | ✗ |
| 15 | payload sent when a streaming LLM token arrives  | `StreamChunkPayload` | `openSocket` | ✓ | ✗ | ✓ | ✓ |
| 16 | SQLite-backed persistent chat conversation stora | `DesktopSQLiteChatStore` | `getChatStore` | ✓ | ✗ | ✗ | ✓ |
| 17 | context provided to a tool when it is executed b | `ToolExecutionContext` | `AgentRequestPayload` | ✓ | ✗ | ✗ | ✓ |

### 6.2 File Search (`search_files`)

**Hit@1:** 0/3 (0.0%)  
**Hit@3:** 0/3 (0.0%)  
**Hit@5:** 3/3 (100.0%)

| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | core AI message and provider type definitions | `types.ts` | `index.ts` | ✓ | ✗ | ✗ | ✓ |
| 2 | WebSocket server managing phone connections from | `ws-server.ts` | `scan.tsx` | ✓ | ✗ | ✗ | ✓ |
| 3 | waifu personality and system prompt builder | `personality.ts` | `waifu-core.vitest.ts` | ✓ | ✗ | ✗ | ✓ |

---

## 7. Bug Triage

### Bug 1 — BenchmarkMonitor reports Avg CPU = 0%

| Field | Detail |
|---|---|
| **Symptom** | `Avg CPU (process tree): 0%` printed in all benchmark runs despite active LLM inference and I/O over 579s |
| **Root cause** | `psutil.cpu_percent(interval=None)` returns `0.0` on the very first call (requires a prior baseline measurement). The monitor calls it once without a warm-up interval, so the reading is always 0. |
| **Fix** | Call `psutil.cpu_percent(interval=None)` during process spawn to initialise the baseline, then sample again after a short delay, **or** use `psutil.cpu_percent(interval=1)` (blocking) for each sample. File: `scripts/benchmark_monitor.py`. |
| **Severity** | Low — cosmetic metric only; no functional impact |

### Bug 2 — Scenario D new-file symbols skipped by semantic indexer (min_ref_count)

| Field | Detail |
|---|---|
| **Symptom** | `_bench_auto_scenario_d.ts` created with 4 exported functions → `SEMANTIC summarised=0 skipped=4`. Symbols are indexed in SQLite but never embedded. |
| **Root cause** | `min_ref_count=1` in `.codebase-insights.toml` filters out symbols with zero cross-references. A freshly added file's symbols have ref_count=0 until something else imports them. |
| **Fix** | Document as expected behaviour. Consider adding an `min_ref_count=0` opt-in config option so users can embed all symbols regardless of reference count. |
| **Severity** | Medium — users adding new standalone utility files won't see semantic results until refs accumulate |

### Bug 3 — Scenario D INDEXER marker not captured

| Field | Detail |
|---|---|
| **Symptom** | Scenario D shows `INDEXER=False` — no `[BENCHMARK:INDEXER]` line captured in the scenario window. |
| **Root cause** | `[BENCHMARK:INDEXER]` is emitted only once during the initial startup indexing pass; incremental watchdog events do not re-emit it. The orchestrator correctly waits for `PROJECT_SUMMARY` instead. |
| **Fix** | No fix required. Document that `BENCHMARK:INDEXER` is a startup-only marker. Scenario D total time is measured correctly via `PROJECT_SUMMARY`. |
| **Severity** | Low — benchmark data captured correctly via other markers |

---

## 8. Key Findings & Recommendations

| Priority | Finding | Recommendation |
|:---:|---|---|
| High | **Semantic Hit@1 = 5.9%, Hit@5 = 88.2%** — correct symbol almost always in top-5 but rarely at rank 1 | The embedding model (bge-m3) ranks structurally similar symbols above the correct answer. Investigate re-ranking or boosting symbols whose description more closely matches the query intent. Alternatively, evaluate a larger embedding model. |
| High | **File search Hit@1 = 0%, Hit@5 = 100%** — correct file always found in top-5 but not at rank 1 | File-level descriptions are too generic and don't distinguish between related files (e.g. multiple `types.ts` across packages). Consider including the package path and key exported symbol names in the file description summary. |
| High | **Keyword baseline dominates** — `query_symbols` exact-match found the correct symbol for 14/17 queries where semantic search failed at Hit@1 | A hybrid re-ranking strategy (semantic score + keyword-match boost) would dramatically improve Hit@1. Consider fusing the two signal types before returning results. |
| Medium | **PROJECT_SUMMARY is the incremental bottleneck** — 113s on a 1-file edit (Scenario C), 6s on a brand-new file | Consider adding a debounce/coalescing window (e.g. 30s) to batch rapid edits before triggering project summary regeneration. |
| Medium | **New-file symbols not semantically indexed** — `min_ref_count=1` silently excludes standalone new files | Add a config note to docs; expose `min_ref_count=0` as opt-in. |
| Low | **Avg CPU metric always 0%** in BenchmarkMonitor | Fix `psutil.cpu_percent` baseline initialisation (see Bug 1). |

---

## Completion Checklist

- [x] Port 6789 confirmed free before every server start
- [x] `.codebase-insights.toml` confirmed present in target repo
- [x] All `[BENCHMARK:*]` lines flush to log in real time
- [x] `benchmark_results/full_rebuild.json` written
- [x] Scenarios A–D all have BENCHMARK data captured
- [x] ≥15 retrieval queries run and auto-scored (20 total)
- [x] LSP test matrix fully exercised (8/8 tool types)
- [x] Bugs found are documented with root cause + fix
- [x] Report written with all required sections
- [x] Test edits reverted in target repo
- [x] `benchmark_results/` deleted after report is committed

---

*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*
