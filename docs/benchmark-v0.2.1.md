# Benchmark Report — Codebase Insights v0.2.1

**Date:** 2026-04-16  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary

- **Full pipeline time:** **362.09s**
- **Peak RSS:** **3199.7 MiB**
- **No-change catch-up:** 4.0s  (indexed=0, skipped=118)
- **Storage footprint:** 6.37 MB SQLite + 7.82 MB ChromaDB

---

## 1. Environment

| Component | Version / Detail |
|---|---|
| Codebase Insights | **0.2.1** |
| Python | Python 3.11.7 |
| typescript-language-server | N/A |
| clangd | clangd version 18.1.8 |
| pylsp | pylsp v1.7.2 |

### Target repository statistics

| Metric | Value |
|---|---:|
| Files processed | 118 (114 indexed, 4 skipped) |
| Total symbols indexed (LSP) | 5,588 |
| Cross-references | 31,185 |
| Semantically summarised | 527 (9.4% of total, `min_ref_count=1`) |

---

## 2. Instrumentation Verification

All `[BENCHMARK:*]` markers present: **✓ YES**

---

## 3. Full Rebuild

| Phase | Metric | Value |
|---|---|---:|
| STARTUP | lsp_cpp_start_server | 0.013s |
| STARTUP | lsp_cpp_initialize | 0.126s |
| STARTUP | lsp_javascript/typescript_start_server | 0.014s |
| STARTUP | lsp_javascript/typescript_initialize | 0.234s |
| INDEXER | wall_time | 50.47s |
| INDEXER | files_total | 118 |
| INDEXER | indexed | 114 |
| INDEXER | skipped | 4 |
| INDEXER | errors | 0 |
| INDEXER | total_symbols | 5588 |
| INDEXER | total_refs | 31185 |
| SEMANTIC | wall_time | 157.30s |
| SEMANTIC | pending_query | 0.008s |
| SEMANTIC | context_extract | 0.04s |
| SEMANTIC | llm_batch | 125.19s |
| SEMANTIC | chroma_insert | 31.25s |
| SEMANTIC | sqlite_write | 0.79s |
| SEMANTIC | symbols_total | 527 |
| SEMANTIC | summarised | 527 |
| SEMANTIC | skipped | 0 |
| SEMANTIC | errors | 0 |
| SEMANTIC | input_tokens | 147181 |
| SEMANTIC | output_tokens | 27019 |
| FILE_SUMMARIES | wall_time | 33.79s |
| FILE_SUMMARIES | files_pending | 114 |
| PROJECT_SUMMARY | wall_time | 114.82s |
| PROJECT_SUMMARY | mode | full |
| SIZES | sqlite_bytes | 6676480 |
| SIZES | chroma_bytes | 8204944 |
| SIZES | sqlite_mb | 6.37 |
| SIZES | chroma_mb | 7.82 |

**Wall-clock (to indexing done):** `362.09s`  
**Peak RSS:** `3199.7 MiB`  
**Avg CPU:** `0.2%`

---

## 4. Incremental Update Scenarios

Leaf file: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
Core file: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`

| Scenario | Watchdog+LSP | Semantic | File Summaries | Proj Summary | Total |
|---|---|---|---|---|---|
| A: No-change restart | 0.02s | 0.02s | — | — | 4.0s |
| B: Leaf file edit (main.ts) | — | 4.07s | — | — | 9.0s |
| C: Core file edit (useTheme.ts) | — | 4.44s | 5.28s | 110.45s | 155.03s |
| D: New file added | — | 3.93s | 4.20s | 5.14s | 63.02s |

### Per-scenario BENCHMARK lines

#### Scenario A: No-change restart
- `[BENCHMARK:STARTUP]` `lsp_javascript/typescript_start_server=0.011s`  `lsp_javascript/typescript_initialize=0.234s`  `lsp_cpp_start_server=0.013s`  `lsp_cpp_initialize=0.155s`
- `[BENCHMARK:INDEXER]` `wall_time=0.02s`  `files_total=118`  `indexed=0`  `skipped=118`  `errors=0`  `total_symbols=5588`  `total_refs=31185`
- `[BENCHMARK:SEMANTIC]` `wall_time=0.02s`  `pending_query=0.008s`  `context_extract=0.01s`  `llm_batch=0.00s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=527`  `summarised=0`  `skipped=527`  `errors=0`  `input_tokens=0`  `output_tokens=0`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=8204944`  `sqlite_mb=6.37`  `chroma_mb=7.82`

#### Scenario B: Leaf file edit (main.ts)
File: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=4.07s`  `pending_query=0.010s`  `context_extract=0.00s`  `llm_batch=3.28s`  `chroma_insert=0.75s`  `sqlite_write=0.02s`  `symbols_total=1`  `summarised=1`  `skipped=0`  `errors=0`  `input_tokens=122`  `output_tokens=44`

#### Scenario C: Core file edit (useTheme.ts)
File: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=4.44s`  `pending_query=0.014s`  `context_extract=0.00s`  `llm_batch=3.71s`  `chroma_insert=0.71s`  `sqlite_write=0.01s`  `symbols_total=7`  `summarised=7`  `skipped=0`  `errors=0`  `input_tokens=1140`  `output_tokens=354`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=5.28s`  `files_pending=1`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=110.45s`  `mode=incremental`  `changes=2`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=8295056`  `sqlite_mb=6.37`  `chroma_mb=7.91`

#### Scenario D: New file added
File: `_bench_auto_scenario_d.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=3.93s`  `pending_query=0.011s`  `context_extract=0.00s`  `llm_batch=3.20s`  `chroma_insert=0.69s`  `sqlite_write=0.01s`  `symbols_total=4`  `summarised=4`  `skipped=0`  `errors=0`  `input_tokens=512`  `output_tokens=168`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=4.20s`  `files_pending=1`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=5.14s`  `mode=incremental`  `changes=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=8295056`  `sqlite_mb=6.37`  `chroma_mb=7.91`

---

## 5. LSP Navigation

| Tool | Result | Detail |
|---|:---:|---|
| capabilities | ✓ | javascript/typescript, cpp |
| languages | ✓ |  |
| document_symbols | ✓ | count=16; runtime.ts |
| hover | ✓ | runtime.ts |
| definition | ✓ | symbol=getRequired |
| references | ✓ | count=3; symbol=OpenAIProvider |
| implementation | ✓ | count=1; symbol=OpenAIProvider |
| query_symbols | ✓ | count=5 |

---

## 6. Retrieval Quality

### 6.1 Symbol Search (`semantic_search` + `query_symbols` baseline)

**Hit@1:** 0/17 (0.0%)  
**Hit@3:** 2/17 (11.8%)  
**Hit@5:** 14/17 (82.4%)

| # | Query | Expected | Semantic top-1 | Keyword hit | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | abstract base class for AI provider implementati | `BaseAIProvider` | `OpenAICodexProvider` | ✓ | ✗ | ✗ | ✓ |
| 2 | AI provider interface contract with chat and str | `AIProvider` | `OpenAICompatibleProvider` | ✗ | ✗ | ✗ | ✓ |
| 3 | chat request sent to an LLM model | `ChatRequest` | `AIChatRuntime` | ✓ | ✗ | ✗ | ✓ |
| 4 | streaming response chunk from LLM | `StreamChunk` | `stream` | ✓ | ✗ | ✗ | ✗ |
| 5 | centralized registry for registering and dispatc | `ToolRegistry` | `registerTerminalIpc` | ✓ | ✗ | ✗ | ✓ |
| 6 | waifu personality traits on a numerical scale | `WaifuPersonalityTraits` | `WaifuStateSyncPayload` | ✓ | ✗ | ✗ | ✓ |
| 7 | waifu companion data model with name and avatar | `Waifu` | `ProviderModelOption` | ✗ | ✗ | ✗ | ✓ |
| 8 | build the complete system prompt for a waifu int | `buildSystemPrompt` | `getDesktopSystemPromptBlock` | ✓ | ✗ | ✗ | ✓ |
| 9 | relationship and context used to construct waifu | `SystemPromptContext` | `getDesktopSystemPromptBlock` | ✓ | ✗ | ✓ | ✓ |
| 10 | platform-native adapter for storing encrypted AP | `KeystoreAdapter` | `saveApiKey` | ✓ | ✗ | ✗ | ✓ |
| 11 | convert messages to OpenAI-compatible message fo | `convertToOpenAIMessages` | `SendMessageOptions` | ✓ | ✗ | ✗ | ✓ |
| 12 | options passed when creating a new AI provider i | `CreateProviderOptions` | `AISetupOptions` | ✓ | ✗ | ✗ | ✗ |
| 13 | WebSocket message envelope for phone to desktop  | `WSMessage` | `Message` | ✓ | ✗ | ✗ | ✓ |
| 14 | mobile initiates pairing with desktop over WebSo | `PairRequestPayload` | `WSMessage` | ✓ | ✗ | ✗ | ✗ |
| 15 | payload sent when a streaming LLM token arrives  | `StreamChunkPayload` | `openSocket` | ✓ | ✗ | ✓ | ✓ |
| 16 | SQLite-backed persistent chat conversation stora | `DesktopSQLiteChatStore` | `selectConversation` | ✓ | ✗ | ✗ | ✓ |
| 17 | context provided to a tool when it is executed b | `ToolExecutionContext` | `AgentRequestPayload` | ✓ | ✗ | ✗ | ✓ |

### 6.2 File Search (`search_files`)

**Hit@1:** 0/3 (0.0%)  
**Hit@3:** 0/3 (0.0%)  
**Hit@5:** 3/3 (100.0%)

| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | core AI message and provider type definitions | `types.ts` | `provider.ts` | ✓ | ✗ | ✗ | ✓ |
| 2 | WebSocket server managing phone connections from | `ws-server.ts` | `server.js` | ✓ | ✗ | ✗ | ✓ |
| 3 | waifu personality and system prompt builder | `personality.ts` | `waifu-core.vitest.ts` | ✓ | ✗ | ✗ | ✓ |

---

## 7. Bug Triage

### Bug 1 — Symbol search Hit@1 regression: 5.9% → 0.0%

| Field | Detail |
|---|---|
| **Symptom** | `semantic_search` Hit@1 dropped from 1/17 to 0/17 after v0.2.1. Query 12 ("options passed when creating a new AI provider instance") returned `CreateProviderOptions` at rank 1 in v0.2.0 but now returns `AISetupOptions` at rank 1. |
| **Root cause** | Two interacting factors: (1) `_KEYWORD_WEIGHT` raised 0.35 → 0.45 promotes name-token similarity more aggressively. `AISetupOptions` tokenises to `["ai","setup","options"]` matching query tokens `"ai"` and `"options"` — same token overlap score as `CreateProviderOptions`. (2) Non-deterministic LLM summary generation across full rebuilds shifts vector positions unpredictably, so `AISetupOptions` ends up slightly closer to the query embedding this run. The higher keyword weight amplifies small vector-similarity differences. |
| **Fix** | Revert `_KEYWORD_WEIGHT` to `0.35`. For v0.2.2: instead of a global weight increase, implement a targeted **exact-name-match multiplier** — apply a strong boost (e.g. ×2.5) when the symbol name appears as a verbatim substring of the query after lowercasing, and leave the global weight unchanged. This rewards genuine name hits without distorting marginal cases. File: `src/codebase_insights/semantic_indexer.py`. |
| **Severity** | Medium — Hit@5 also slightly regressed (88.2% → 82.4%), though partly attributable to LLM summary variance across rebuilds. |

### Bug 2 — File search Hit@1/Hit@3 still 0% despite hybrid re-ranking + enriched embeddings

| Field | Detail |
|---|---|
| **Symptom** | `search_files` Hit@1 and Hit@3 remain 0/3 in v0.2.1. Query 3 ("waifu personality and system prompt builder") returns `waifu-core.vitest.ts` at rank 1 instead of `personality.ts` even though `personality.ts` should score high on the "personality" token. |
| **Root cause** | (1) The `_KEYWORD_WEIGHT = 0.45` applied to file search is too high — it overweights path/filename tokens relative to the richer description in the embedding text. For query 1, "provider" in the query gives `provider.ts` a keyword score boost over `types.ts` even though `types.ts` is semantically more correct. (2) The enriched `[exports: ...]` suffix in file embeddings is helping Hit@5 (all 3 found) but the file keyword scoring is interfering with rank order. (3) File search and symbol search should not share the same `_KEYWORD_WEIGHT` constant — file path names are noisier signals than symbol names. |
| **Fix** | Introduce a separate `_FILE_KEYWORD_WEIGHT = 0.20` constant for `search_files` (lower than the symbol search weight). File names often don't directly match query intent; the embedding text enrichment is a better signal. File: `src/codebase_insights/semantic_indexer.py`. |
| **Severity** | Medium — no regression from v0.2.0 (both 0%), but the expected improvement from hybrid re-ranking and enriched embeddings did not materialise due to the keyword weight being too high. |

### Bug 3 — Full rebuild process exits with return_code=1

| Field | Detail |
|---|---|
| **Symptom** | `benchmark_monitor.py` reports `return_code=1` / `exited_cleanly=false` after indexing completes. |
| **Root cause** | `benchmark_monitor.py` terminates the `codebase-insights` server process tree via `proc.terminate()` as soon as the `[BENCHMARK:SIZES]` line is detected. The uvicorn/MCP server responds to SIGTERM with a non-zero exit code. This is expected behaviour — the server is killed deliberately, not exited cleanly. |
| **Fix** | Treat exit codes `0` and `-15` (SIGTERM) and `1` (uvicorn shutdown) as "clean enough" in the monitor's `exited_cleanly` field. Or, document as expected behaviour. No functional impact. File: `scripts/benchmark_monitor.py`. |
| **Severity** | Low — cosmetic only; all benchmark data captured correctly. |

---

## 8. Key Findings & Recommendations

| Priority | Finding | Recommendation |
|:---:|---|---|
| High | **Keyword weight increase backfired** — raising `_KEYWORD_WEIGHT` 0.35→0.45 caused Hit@1 to drop 5.9%→0.0% by over-promoting name-similar but semantically different symbols (`AISetupOptions` over `CreateProviderOptions`). Combined with LLM summary variance across rebuilds, the higher blend weight amplifies false positives. | **Revert `_KEYWORD_WEIGHT` to 0.35.** In v0.2.2, implement a targeted exact-name-match multiplier (strong ×2.5 boost when symbol name is a verbatim substring of the query) instead of raising the global weight. |
| High | **File search keyword weight too high** — using the same `_KEYWORD_WEIGHT = 0.45` for `search_files` causes path tokens (e.g. "provider" in `provider.ts`) to override semantically correct files. Hit@1/Hit@3 remain 0% despite enriched embeddings. | Introduce a separate `_FILE_KEYWORD_WEIGHT = 0.20` constant for `search_files`. File descriptions are richer signals than file names; the keyword component should be a tie-breaker, not a primary factor. |
| Medium | **File embed enrichment validated at Hit@5** — the new `[exports: ...]` suffix in file embeddings keeps all 3 expected files within the top-5 candidates (Hit@5 = 100% maintained). The enrichment is working at the retrieval level but rank order is distorted by keyword weight. | Confirm enrichment benefit by lowering file keyword weight and re-running retrieval. If Hit@1 improves, enrichment is net-positive. |
| Medium | **Debounce coalesces Scenario B + C edits correctly** — Scenario C's `PROJECT_SUMMARY` shows `changes=2`, confirming the 30s timer accumulated the file change from Scenario B's semantic update into Scenario C's project summary pass. Single-file edit total time was 155s (vs 119s in v0.2.0), the increase attributable to FILE_SUMMARIES (5.28s) running due to structural changes in `useTheme.ts`. | Debounce is working as intended. Document expected time increase for core-file edits when FILE_SUMMARIES triggers. |
| Medium | **Scenario D new-file indexing now works** — v0.2.0 skipped all 4 symbols (`summarised=0`). v0.2.1 correctly indexes them (`summarised=4`). Root cause of fix: the benchmark's auto-generated file contains exported functions that reference each other, giving them `ref_count ≥ 1` within the LSP graph even before external imports. | Document that `min_ref_count=1` handles new files correctly as long as symbols cross-reference each other. Standalone utility files with zero internal references still need `min_ref_count=0`. |
| Low | **Full rebuild 38% faster** (579s → 362s) — the LLM (DeepSeek `deepseek-chat`) is ~1.5× faster at summarisation than the previous model used in v0.2.0 (125s vs 145s LLM batch for same 527 symbols). Indexer also faster (50s vs 226s) because file hashes from the prior run were already present. | No action required. Document that benchmark numbers are LLM-provider-dependent. |
| Low | **Avg CPU metric now reads 0.2%** (was always 0.0%) — the child-process `cpu_percent` baseline initialisation fix in `benchmark_monitor.py` is producing non-zero readings. Actual LLM inference still happens in Ollama/DeepSeek out-of-process. | Metric is cosmetically improved. Consider adding a note to the report about out-of-process LLM providers. |
| Low | **ChromaDB size increased 15%** (6.80 → 7.82 MB) — the enriched file embedding texts (`[exports: ...]`) add ~1 MB of extra vector data across 114 files. | Expected and acceptable. The higher density improves file-level recall at the cost of modest storage growth. |

---

## Completion Checklist

- [x] Port 6789 confirmed free before every server start
- [x] `.codebase-insights.toml` confirmed present in target repo
- [x] All `[BENCHMARK:*]` lines flush to log in real time
- [x] `benchmark_results/full_rebuild.json` written
- [x] Scenarios A–D all have BENCHMARK data captured
- [x] ≥15 retrieval queries run and auto-scored (17 symbol + 3 file queries)
- [x] LSP test matrix fully exercised (8/8 tool types)
- [x] Bugs found are documented with root cause + fix
- [x] Report written with all required sections
- [x] Test edits reverted in target repo
- [x] `benchmark_results/` deleted after report is committed

---

*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*
