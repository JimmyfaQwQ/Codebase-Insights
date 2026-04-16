# Benchmark Report — Codebase Insights v0.2.0

**Date:** 2026-04-15  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary


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
| Files processed | 90 |
| Total symbols | 5595 |
| Cross-references | 32701 |

---

## 2. Instrumentation Verification

All `[BENCHMARK:*]` markers present: **✓ YES**

---

## 3. Full Rebuild

| Phase | Metric | Value |
|---|---|---:|
| STARTUP | lsp_cpp_start_server | 0.016s |
| STARTUP | lsp_cpp_initialize | 0.213s |
| STARTUP | lsp_javascript/typescript_start_server | 0.014s |
| STARTUP | lsp_javascript/typescript_initialize | 0.223s |
| INDEXER | wall_time | 69.55s |
| INDEXER | files_total | 118 |
| INDEXER | indexed | 114 |
| INDEXER | skipped | 4 |
| INDEXER | total_symbols | 5588 |
| INDEXER | total_refs | 32691 |
| SEMANTIC | wall_time | 7491.32s |
| SEMANTIC | summarised | 527 |
| SEMANTIC | input_tokens | 147181 |
| SEMANTIC | output_tokens | 27065 |
| FILE_SUMMARIES | wall_time | 37.18s |
| FILE_SUMMARIES | files_pending | 114 |
| PROJECT_SUMMARY | wall_time | 102.97s |
| PROJECT_SUMMARY | mode | full |
| SIZES | sqlite_mb | 9.88 |
| SIZES | chroma_mb | 51.43 |

**Wall-clock (to indexing done):** `7707.9s`  
**Peak RSS:** `3119 MiB`  
**Monitor exit code:** `1` (indexing completed and benchmark files were written)

---

## 4. Incremental Update Scenarios

Leaf file: `G:\SyntaxSenpai\apps\desktop\src\renderer\src\main.ts`  
Core file: `G:\SyntaxSenpai\apps\mobile\src\hooks\useTheme.ts`

| Scenario | Watchdog+LSP | Semantic | File Summaries | Proj Summary | Total |
|---|---|---|---|---|---|
| A: No-change restart | 0.12s | 0.13s | — | — | 12.0s |
| B: Leaf file edit | — | 4.57s | — | — | 11.0s |
| C: Core file edit | — | — | — | 1893.26s | 1893.2s |
| D: New file added | — | 0.74s | 5.53s | 2141.18s | 2223.6s |

### Per-scenario BENCHMARK lines

- Scenario A:
	`[BENCHMARK:INDEXER] wall_time=0.12s files_total=118 indexed=0 skipped=118 errors=0 total_symbols=5588 total_refs=32691`  
	`[BENCHMARK:SEMANTIC] wall_time=0.13s pending_query=0.059s context_extract=0.06s llm_batch=0.00s chroma_insert=0.00s sqlite_write=0.00s symbols_total=527 summarised=0 skipped=527 errors=0 input_tokens=0 output_tokens=0`  
	`[BENCHMARK:SIZES] sqlite_mb=9.88 chroma_mb=51.43`

- Scenario B:
	`[BENCHMARK:SEMANTIC] wall_time=4.57s pending_query=0.051s context_extract=0.00s llm_batch=3.11s chroma_insert=1.39s sqlite_write=0.01s symbols_total=1 summarised=1 skipped=0 errors=0 input_tokens=122 output_tokens=44`

- Scenario C:
	`[BENCHMARK:PROJECT_SUMMARY] wall_time=1893.26s mode=incremental changes=1`  
	`[BENCHMARK:SIZES] sqlite_mb=9.88 chroma_mb=51.43`

- Scenario D:
	`[BENCHMARK:SEMANTIC] wall_time=0.74s pending_query=0.689s context_extract=0.05s llm_batch=0.00s chroma_insert=0.00s sqlite_write=0.00s symbols_total=7 summarised=0 skipped=7 errors=0 input_tokens=0 output_tokens=0`  
	`[BENCHMARK:FILE_SUMMARIES] wall_time=5.53s files_pending=2`  
	`[BENCHMARK:PROJECT_SUMMARY] wall_time=2141.18s mode=incremental changes=2`  
	`[BENCHMARK:SIZES] sqlite_mb=9.88 chroma_mb=51.43`

---

## 5. LSP Navigation

| Tool | Result | Detail |
|---|:---:|---|
| capabilities | ✓ | cpp, javascript/typescript |
| languages | ✓ |  |
| document_symbols | ✓ | count=5; base.ts |
| hover | ✓ | base.ts |
| definition | ✓ | symbol=BaseAIProvider |
| references | ✓ | count=22; symbol=getRequired |
| implementation | ✓ | count=2; symbol=BaseAIProvider |
| query_symbols | ✓ | count=5 |

---

## 6. Retrieval Quality

### 6.1 Symbol Search (`semantic_search` + `query_symbols` baseline)

**Hit@1:** 0/18 (0.0%)  
**Hit@3:** 1/18 (5.6%)  
**Hit@5:** 15/18 (83.3%)

| # | Query | Expected | Semantic top-1 | Keyword hit | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | agent command allowlist management and persisten | `saveAllowlist` | `list` | ✓ | ✗ | ✗ | ✓ |
| 2 | execute agent tool call dispatch router | `executeToolCall` | `execute` | ✓ | ✗ | ✗ | ✓ |
| 3 | get available tools for agent mode | `getToolsForMode` | `get` | ✓ | ✗ | ✗ | ✓ |
| 4 | run terminal command from desktop agent | `runDesktopTerminalCommand` | `disconnectFromDesktop` | ✓ | ✗ | ✗ | ✓ |
| 5 | broadcast chat event to mobile device | `broadcastMobileChatEvent` | `AIChatRuntime` | ✓ | ✗ | ✗ | ✓ |
| 6 | detect LAN IP address of machine | `getLanIps` | `findPowerShell` | ✓ | ✗ | ✗ | ✓ |
| 7 | connect mobile client to desktop WebSocket | `connectToDesktop` | `get` | ✓ | ✗ | ✗ | ✓ |
| 8 | send agent request from mobile | `sendAgentRequest` | `send` | ✓ | ✗ | ✗ | ✓ |
| 9 | mobile WebSocket connection state and pairing | `useWsConnection` | `registerWsIpc` | ✓ | ✗ | ✗ | ✗ |
| 10 | load and discover plugin manifests | `loadPluginManifests` | `loadConfig` | ✓ | ✗ | ✗ | ✓ |
| 11 | runtime metrics histogram implementation | `HistogramMetric` | `AIChatRuntime` | ✓ | ✗ | ✗ | ✓ |
| 12 | collect and expose Prometheus style runtime metr | `RuntimeMetrics` | `ApiTelemetrySample` | ✓ | ✗ | ✗ | ✓ |
| 13 | register secure API key keystore IPC handlers | `registerKeystoreIpc` | `APIKeyManager` | ✓ | ✗ | ✗ | ✓ |
| 14 | web search tool execution for agent | `webSearch` | `ToolExecutionContext` | ✓ | ✗ | ✓ | ✓ |
| 15 | desktop system prompt context block generation | `getDesktopSystemPromptBlock` | `ToolExecutionContext` | ✓ | ✗ | ✗ | ✓ |
| 16 | stop response sentinel tool constant | `STOP_TOOL_NAME` | `getToolDefinitions` | ✗ | ✗ | ✗ | ✗ |
| 17 | format terminal command result for chat | `formatTerminalCommandResult` | `formatCommunicationStyle` | ✓ | ✗ | ✗ | ✓ |
| 18 | stream text chunks for progressive display | `chunkTextForStreaming` | `stream` | ✓ | ✗ | ✗ | ✗ |

### 6.2 File Search (`search_files`)

**Hit@1:** 0/6 (0.0%)  
**Hit@3:** 1/6 (16.7%)  
**Hit@5:** 6/6 (100.0%)

| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | file responsible for agent tool execution and sh | `executor.ts` | `types.ts` | ✓ | ✗ | ✗ | ✓ |
| 2 | module handling WebSocket server and mobile chat | `ws-server.ts` | `ws.ts` | ✓ | ✗ | ✗ | ✓ |
| 3 | file that manages runtime metrics and counters | `metrics.js` | `server.js` | ✓ | ✗ | ✗ | ✓ |
| 4 | module for plugin loading and manifest discovery | `plugins.js` | `use-theme.ts` | ✓ | ✗ | ✗ | ✓ |
| 5 | file managing secure credential and API key stor | `keystore.ts` | `types.ts` | ✓ | ✗ | ✓ | ✓ |
| 6 | mobile hook responsible for WebSocket pairing an | `useWsConnection.ts` | `types.ts` | ✓ | ✗ | ✗ | ✓ |

---

## 7. Key Findings & Recommendations

| Severity | Symptom | Root Cause | Fix |
|---|---|---|---|
| High | Phase 4/5 intermittently failed right after Phase 3 (`unhandled errors in a TaskGroup`, server not running) | Port/socket linger caused a false-positive server-up check immediately after Phase 3 shutdown | In `scripts/run_benchmark.py`, after Phase 3 we now force `kill_port(6789)` and wait for port release before starting the Phase 4/5 server |
| High | LSP position tools (`lsp_document_symbols`, `lsp_hover`, `lsp_definition`, `lsp_references`, `lsp_implementation`) failed on Windows paths | URI normalization produced malformed URIs like `file://///G:/...` when using `"file://" + pathname2url(...)` | In `src/codebase_insights/mcp_server.py`, `_to_file_uri` now uses `Path(...).as_uri()` to produce canonical `file:///G:/...` |
| Medium | Phase 4 file queries produced empty top-1 filename display despite successful semantic responses | `phase4_retrieval` only checked `file_path` / `path`, but `search_files` returns `file` | In `scripts/run_benchmark.py`, file path extraction now checks `file_path`, `path`, and `file` |
| Medium | Running `--phases 4,5,7` could overwrite previously captured state from Phase 0-3 | Script initialized state from `{}` on each run, not from existing `benchmark_state.json` | In `scripts/run_benchmark.py`, state is now loaded from `benchmark_results/benchmark_state.json` when present |

Recommendations:
1. Add a lightweight integration test for `phase4_retrieval` schema compatibility with `search_files` output keys.
2. Add a Windows-specific unit test for `_to_file_uri` to assert exact URI shape for absolute drive paths.
3. Persist a run-id and phase provenance in `benchmark_state.json` to make partial re-runs auditable.
4. Investigate long-tail incremental latency in core/new-file scenarios (C/D) where project summary dominates runtime.

---

## Completion Checklist

- [x] Port 6789 confirmed free before every server start
- [ ] All `[BENCHMARK:*]` markers flush to log in real time
- [x] Scenarios A–D all have BENCHMARK data captured
- [x] LSP test matrix fully exercised
- [x] Retrieval quality queries scored (if Phase 4 was run)
- [x] Bugs found are documented with root cause + fix
- [ ] `benchmark_results/` deleted after report is committed

---

*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*
