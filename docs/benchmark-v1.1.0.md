# Benchmark Report — Codebase Insights v1.1.0

**Date:** 2026-04-17  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary

- **Full pipeline time:** **499.79s**
- **Peak RSS:** **2844.5 MiB**
- **No-change catch-up:** 38.02s  (indexed=0, skipped=118)
- **Storage footprint:** 6.37 MB SQLite + 8.72 MB ChromaDB

---

## 1. Environment

| Component | Version / Detail |
|---|---|
| Codebase Insights | **1.1.0** |
| Python | Python 3.11.7 |
| typescript-language-server | N/A |
| clangd | clangd version 18.1.8 |
| pylsp | pylsp v1.7.2 |

### Target repository statistics

| Metric | Value |
|---|---:|
| Files processed | 89 |
| Total symbols | 5593 |
| Cross-references | 31792 |

---

## 2. Instrumentation Verification

All `[BENCHMARK:*]` markers present: **✓ YES**

---

## 3. Full Rebuild

| Phase | Metric | Value |
|---|---|---:|
| STARTUP | lsp_cpp_start_server | 0.022s |
| STARTUP | lsp_cpp_initialize | 0.317s |
| STARTUP | lsp_javascript/typescript_start_server | 0.014s |
| STARTUP | lsp_javascript/typescript_initialize | 0.509s |
| INDEXER | wall_time | 117.96s |
| INDEXER | files_total | 118 |
| INDEXER | indexed | 114 |
| INDEXER | skipped | 4 |
| INDEXER | errors | 0 |
| INDEXER | total_symbols | 5588 |
| INDEXER | total_refs | 31779 |
| SEMANTIC | wall_time | 208.41s |
| SEMANTIC | pending_query | 0.021s |
| SEMANTIC | context_extract | 0.09s |
| SEMANTIC | llm_batch | 167.05s |
| SEMANTIC | chroma_insert | 40.87s |
| SEMANTIC | sqlite_write | 0.33s |
| SEMANTIC | symbols_total | 527 |
| SEMANTIC | summarised | 527 |
| SEMANTIC | skipped | 0 |
| SEMANTIC | errors | 0 |
| SEMANTIC | input_tokens | 147181 |
| SEMANTIC | output_tokens | 26995 |
| FILE_SUMMARIES | wall_time | 39.20s |
| FILE_SUMMARIES | files_pending | 114 |
| PROJECT_SUMMARY | wall_time | 115.65s |
| PROJECT_SUMMARY | mode | full |
| SIZES | sqlite_bytes | 6676480 |
| SIZES | chroma_bytes | 9143256 |
| SIZES | sqlite_mb | 6.37 |
| SIZES | chroma_mb | 8.72 |

**Wall-clock (to indexing done):** `499.79s`  
**Peak RSS:** `2844.5 MiB`  
**Avg CPU:** `0.1%`

---

## 4. Incremental Update Scenarios

Leaf file: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
Core file: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`

| Scenario | Watchdog+LSP | Semantic | File Summaries | Proj Summary | Total | Notes |
|---|---|---|---|---|---|---|
| A: No-change restart | 0.04s | 7.06s | 8.16s | 15.65s | 38.02s | — |
| B: Leaf file edit (main.ts) | — | 7.34s | — | — | 13.0s | — |
| C: Core file edit (useTheme.ts) | — | 7.08s | 9.01s | — | 63.02s | — |
| D: New file added | — | 7.08s | 8.35s | 15.60s | 63.02s | is_stale=False |
| E: Force refresh via refresh_project_summary | — | — | — | 16.08s | 319.47s | stale_cleared=True |
| F: Per-file idle timer (30s) | — | 7.41s | 10.82s | 18.16s | 61.01s | idle_fs=True, idle_ps=True, stale_cleared=True |
| G: Project idle timer | — | — | — | — | — | skipped |

### Per-scenario BENCHMARK lines

#### Scenario A: No-change restart
- `[BENCHMARK:STARTUP]` `lsp_javascript/typescript_start_server=0.018s`  `lsp_javascript/typescript_initialize=0.294s`  `lsp_cpp_start_server=0.011s`  `lsp_cpp_initialize=0.177s`
- `[BENCHMARK:INDEXER]` `wall_time=0.04s`  `files_total=118`  `indexed=0`  `skipped=118`  `errors=0`  `total_symbols=5594`  `total_refs=31793`
- `[BENCHMARK:SEMANTIC]` `wall_time=7.06s`  `pending_query=0.017s`  `context_extract=0.03s`  `llm_batch=2.91s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=533`  `summarised=0`  `skipped=532`  `errors=1`  `input_tokens=96`  `output_tokens=42`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=8.16s`  `files_pending=1`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=15.65s`  `mode=incremental`  `changes=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=9155544`  `sqlite_mb=6.37`  `chroma_mb=8.73`

#### Scenario B: Leaf file edit (main.ts)
File: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=7.34s`  `pending_query=0.021s`  `context_extract=0.14s`  `llm_batch=3.10s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=2`  `summarised=0`  `skipped=0`  `errors=2`  `input_tokens=218`  `output_tokens=86`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=9155544`  `sqlite_mb=6.37`  `chroma_mb=8.73`

#### Scenario C: Core file edit (useTheme.ts)
File: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=7.08s`  `pending_query=0.018s`  `context_extract=0.08s`  `llm_batch=2.90s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=8`  `summarised=0`  `skipped=6`  `errors=2`  `input_tokens=215`  `output_tokens=76`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=9.01s`  `files_pending=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=9155544`  `sqlite_mb=6.37`  `chroma_mb=8.73`

#### Scenario D: New file added
File: `_bench_auto_scenario_d.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=7.08s`  `pending_query=0.014s`  `context_extract=0.00s`  `llm_batch=2.98s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=5`  `summarised=0`  `skipped=0`  `errors=5`  `input_tokens=608`  `output_tokens=199`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=8.35s`  `files_pending=1`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=15.60s`  `mode=incremental`  `changes=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=9155544`  `sqlite_mb=6.37`  `chroma_mb=8.73`

#### Scenario E: Force refresh via refresh_project_summary
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=16.08s`  `mode=incremental`  `changes=2`

#### Scenario F: Per-file idle timer (30s)
File: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`  
- `[BENCHMARK:SEMANTIC]` `wall_time=7.41s`  `pending_query=0.020s`  `context_extract=0.06s`  `llm_batch=3.24s`  `chroma_insert=0.00s`  `sqlite_write=0.00s`  `symbols_total=8`  `summarised=0`  `skipped=6`  `errors=2`  `input_tokens=215`  `output_tokens=72`
- `[BENCHMARK:FILE_SUMMARIES]` `wall_time=10.82s`  `files_pending=1`
- `[BENCHMARK:PROJECT_SUMMARY]` `wall_time=18.16s`  `mode=incremental`  `changes=1`
- `[BENCHMARK:SIZES]` `sqlite_bytes=6676480`  `chroma_bytes=9155544`  `sqlite_mb=6.37`  `chroma_mb=8.73`

---

## 5. LSP Navigation

| Tool | Result | Detail |
|---|:---:|---|
| capabilities | ✓ | javascript/typescript, cpp |
| languages | ✓ |  |
| document_symbols | ✓ | count=14; types.ts |
| hover | ✓ | types.ts |
| definition | ✓ | symbol=ChatRequest |
| references | ✓ | count=2; symbol=StreamChunk |
| implementation | ✓ | count=0; symbol=ChatRequest |
| query_symbols | ✓ | count=5 |

---

## 6. Retrieval Quality

### 6.1 Symbol Search (`semantic_search` vs keyword baseline)

> **Keyword baseline**: a context-unaware LLM agent generates up to 4 search terms from
> the natural-language query (no codebase knowledge), then calls `query_symbols` for each.
> KW results = total unique symbols returned across all keyword searches.

**Semantic Hit@1:** 26/28 (92.9%)  
**Semantic Hit@3:** 27/28 (96.4%)  
**Semantic Hit@5:** 27/28 (96.4%)  
**Keyword found expected (in top-5):** 13/28 (46.4%)

| # | Query | Expected | Hit@1 | Hit@3 | Hit@5 | Keyword terms tried | KW results | KW found? |
|---|---|---|:---:|:---:|:---:|---|---:|:---:|
| 1 | abstract base class for AI provider implementations | `BaseAIProvider` | ✓ | ✓ | ✓ | `implementations+abstract+provider+class` | 4 | ✗ |
| 2 | AI provider interface contract with chat and stream methods | `AIProvider` | ✓ | ✓ | ✓ | `interface+provider+contract+methods` | 4 | ✗ |
| 3 | chat request sent to an LLM model | `ChatRequest` | ✓ | ✓ | ✓ | `request+model+chat+sent` | 15 | ✗ |
| 4 | streaming response chunk from LLM | `StreamChunk` | ✗ | ✗ | ✗ | `streaming+response+chunk` | 9 | ✓ |
| 5 | centralized registry for registering and dispatching tools | `ToolRegistry` | ✓ | ✓ | ✓ | `centralized+registering+dispatching+registry` | 1 | ✓ |
| 6 | waifu personality traits on a numerical scale | `WaifuPersonalityTraits` | ✓ | ✓ | ✓ | `personality+numerical+traits+waifu` | 6 | ✓ |
| 7 | build the complete system prompt for a waifu interaction | `buildSystemPrompt` | ✓ | ✓ | ✓ | `interaction+complete+system+prompt` | 7 | ✓ |
| 8 | relationship and context used to construct waifu system prompt | `SystemPromptContext` | ✓ | ✓ | ✓ | `relationship+construct+context+system` | 12 | ✓ |
| 9 | platform-native adapter for storing encrypted API keys | `KeystoreAdapter` | ✓ | ✓ | ✓ | `encrypted+platform+adapter+storing` | 4 | ✓ |
| 10 | convert messages to OpenAI-compatible message format | `convertToOpenAIMessages` | ✓ | ✓ | ✓ | `compatible+messages+convert+message` | 11 | ✓ |
| 11 | options passed when creating a new AI provider instance | `CreateProviderOptions` | ✓ | ✓ | ✓ | `creating+provider+instance+options` | 9 | ✗ |
| 12 | WebSocket message envelope for phone to desktop communication | `WSMessage` | ✓ | ✓ | ✓ | `communication+websocket+envelope+message` | 6 | ✗ |
| 13 | SQLite-backed persistent chat conversation storage on desktop | `DesktopSQLiteChatStore` | ✓ | ✓ | ✓ | `conversation+persistent+storage+desktop` | 11 | ✗ |
| 14 | context provided to a tool when it is executed by the agent | `ToolExecutionContext` | ✓ | ✓ | ✓ | `provided+executed+context+agent` | 10 | ✓ |
| 15 | orchestrate multi-turn conversation flow with tool execution | `AIChatRuntime` | ✗ | ✓ | ✓ | `conversation+orchestrate+execution+multi` | 6 | ✗ |
| 16 | check whether a given API credential is valid and non-empty | `validateApiKey` | ✓ | ✓ | ✓ | `credential+whether+check+valid` | 2 | ✓ |
| 17 | sanitize markup by removing tags and decoding HTML entities | `stripHtml` | ✓ | ✓ | ✓ | `sanitize+removing+decoding+entities` | 0 | ✗ |
| 18 | QR code scanning screen for pairing with a desktop app | `ScanScreen` | ✓ | ✓ | ✓ | `scanning+pairing+desktop+screen` | 11 | ✓ |
| 19 | collect and expose runtime performance counters and histograms | `RuntimeMetrics` | ✓ | ✓ | ✓ | `performance+histograms+counters+collect` | 0 | ✗ |
| 20 | configuration for the rainbow color cycling effect | `RainbowSettings` | ✓ | ✓ | ✓ | `configuration+rainbow+cycling+effect` | 4 | ✓ |
| 21 | permission flags controlling agent filesystem and shell access | `ToolPermissions` | ✓ | ✓ | ✓ | `controlling+permission+filesystem+access` | 1 | ✓ |
| 22 | track prompt and completion token consumption for an AI call | `TokenUsage` | ✓ | ✓ | ✓ | `consumption+completion+prompt+track` | 6 | ✗ |
| 23 | define the blueprint for functions that an AI model can invoke | `ToolDefinition` | ✓ | ✓ | ✓ | `blueprint+functions+define+invoke` | 1 | ✗ |
| 24 | delay component mount until after the browser finishes initial paint | `useDeferredMount` | ✓ | ✓ | ✓ | `component+finishes+browser+initial` | 0 | ✗ |
| 25 | manage backup copies and restoration of application data files | `BackupManager` | ✓ | ✓ | ✓ | `restoration+application+manage+backup` | 9 | ✓ |
| 26 | inter-process communication composable for Electron renderer | `useIpc` | ✓ | ✓ | ✓ | `communication+composable+electron+renderer` | 2 | ✗ |
| 27 | look up localized text strings by translation key | `t` | ✓ | ✓ | ✓ | `translation+localized+strings+look` | 0 | ✗ |
| 28 | adapt shell commands for cross-platform compatibility | `wrapCommand` | ✓ | ✓ | ✓ | `compatibility+commands+platform+adapt` | 4 | ✗ |

### 6.2 File Search (`search_files`)

**Hit@1:** 14/15 (93.3%)  
**Hit@3:** 14/15 (93.3%)  
**Hit@5:** 15/15 (100.0%)

| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | core AI message and provider type definitions | `types.ts` | `types.ts` | ✓ | ✓ | ✓ | ✓ |
| 2 | WebSocket server for desktop-mobile pairing and  | `ws-server.ts` | `ws-server.ts` | ✓ | ✓ | ✓ | ✓ |
| 3 | waifu personality traits and system prompt assem | `personality.ts` | `personality.ts` | ✓ | ✓ | ✓ | ✓ |
| 4 | platform-agnostic secure storage with desktop, m | `keystore.ts` | `keystore.ts` | ✓ | ✓ | ✓ | ✓ |
| 5 | BackupManager creating and restoring timestamped | `backups.js` | `backups.js` | ✓ | ✓ | ✓ | ✓ |
| 6 | IPC bridge registering shell command execution a | `executor.ts` | `agent.ts` | ✓ | ✗ | ✗ | ✓ |
| 7 | Spotify playback control and current track retri | `spotify.ts` | `spotify.ts` | ✓ | ✓ | ✓ | ✓ |
| 8 | cross-platform shell detection and PowerShell co | `terminal-shell.ts` | `terminal-shell.ts` | ✓ | ✓ | ✓ | ✓ |
| 9 | Prometheus-compatible metrics tracking HTTP requ | `metrics.js` | `metrics.js` | ✓ | ✓ | ✓ | ✓ |
| 10 | HTTP server routing requests to backup operation | `server.js` | `server.js` | ✓ | ✓ | ✓ | ✓ |
| 11 | mobile WebSocket connection hook for pairing and | `useWsConnection.ts` | `useWsConnection.ts` | ✓ | ✓ | ✓ | ✓ |
| 12 | mobile QR code scanner extracting pairing token  | `scan.tsx` | `scan.tsx` | ✓ | ✓ | ✓ | ✓ |
| 13 | Vue composable managing stored API keys for mult | `use-key-manager.ts` | `use-key-manager.ts` | ✓ | ✓ | ✓ | ✓ |
| 14 | internationalization composable with locale pers | `use-i18n.ts` | `use-i18n.ts` | ✓ | ✓ | ✓ | ✓ |
| 15 | agent tool dispatcher routing terminal commands, | `agent-tools.ts` | `agent-tools.ts` | ✓ | ✓ | ✓ | ✓ |

---

## 7. Key Findings & Recommendations

_Add observations, bugs found, and prioritised recommendations here._

---

## Completion Checklist

- [ ] Port 6789 confirmed free before every server start
- [ ] All `[BENCHMARK:*]` markers flush to log in real time
- [ ] Scenarios A–E all have BENCHMARK data captured
- [ ] Scenario F (per-file idle) verified: FILE_SUMMARIES fired after idle timeout
- [ ] Scenario G (project idle) verified if --project-idle-timeout was passed
- [ ] LSP test matrix fully exercised
- [ ] Retrieval quality queries scored (if Phase 4 was run)
- [ ] Bugs found are documented with root cause + fix
- [ ] `benchmark_results/` deleted after report is committed

---

*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*
