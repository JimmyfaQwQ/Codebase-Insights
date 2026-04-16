# Benchmark Report — Codebase Insights v0.2.4

**Date:** 2026-04-16  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary


---

## 1. Environment

| Component | Version / Detail |
|---|---|
| Codebase Insights | **0.2.4** |
| Python | Python 3.11.7 |
| typescript-language-server | N/A |
| clangd | clangd version 18.1.8 |
| pylsp | pylsp v1.7.2 |

### Target repository statistics

| Metric | Value |
|---|---:|
| Files processed | 90 |
| Total symbols | 5595 |
| Cross-references | 31195 |

---

## 2. Instrumentation Verification

All `[BENCHMARK:*]` markers present: **✗ NO**

---

## 3. Full Rebuild

_Not run. not run_

---

## 4. Incremental Update Scenarios

Leaf file: `—`  
Core file: `—`

| Scenario | Watchdog+LSP | Semantic | File Summaries | Proj Summary | Total |
|---|---|---|---|---|---|
| A: No-change restart | — | — | — | — | —s |
| B: Leaf file edit | — | — | — | — | —s |
| C: Core file edit | — | — | — | — | —s |
| D: New file added | — | — | — | — | —s |

### Per-scenario BENCHMARK lines

---

## 5. LSP Navigation

| Tool | Result | Detail |
|---|:---:|---|

---

## 6. Retrieval Quality

### 6.1 Symbol Search (`semantic_search` + `query_symbols` baseline)

**Hit@1:** 27/28 (96.4%)  
**Hit@3:** 28/28 (100.0%)  
**Hit@5:** 28/28 (100.0%)

| # | Query | Expected | Semantic top-1 | Keyword hit | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | abstract base class for AI provider implementati | `BaseAIProvider` | `BaseAIProvider` | ✓ | ✓ | ✓ | ✓ |
| 2 | AI provider interface contract with chat and str | `AIProvider` | `AIProvider` | ✗ | ✓ | ✓ | ✓ |
| 3 | chat request sent to an LLM model | `ChatRequest` | `ChatRequest` | ✓ | ✓ | ✓ | ✓ |
| 4 | streaming response chunk from LLM | `StreamChunk` | `StreamChunk` | ✓ | ✓ | ✓ | ✓ |
| 5 | centralized registry for registering and dispatc | `ToolRegistry` | `ToolRegistry` | ✓ | ✓ | ✓ | ✓ |
| 6 | waifu personality traits on a numerical scale | `WaifuPersonalityTraits` | `WaifuPersonalityTraits` | ✓ | ✓ | ✓ | ✓ |
| 7 | build the complete system prompt for a waifu int | `buildSystemPrompt` | `buildSystemPrompt` | ✓ | ✓ | ✓ | ✓ |
| 8 | relationship and context used to construct waifu | `SystemPromptContext` | `SystemPromptContext` | ✓ | ✓ | ✓ | ✓ |
| 9 | platform-native adapter for storing encrypted AP | `KeystoreAdapter` | `KeystoreAdapter` | ✓ | ✓ | ✓ | ✓ |
| 10 | convert messages to OpenAI-compatible message fo | `convertToOpenAIMessages` | `convertToOpenAIMessages` | ✓ | ✓ | ✓ | ✓ |
| 11 | options passed when creating a new AI provider i | `CreateProviderOptions` | `CreateProviderOptions` | ✓ | ✓ | ✓ | ✓ |
| 12 | WebSocket message envelope for phone to desktop  | `WSMessage` | `WSMessage` | ✓ | ✓ | ✓ | ✓ |
| 13 | SQLite-backed persistent chat conversation stora | `DesktopSQLiteChatStore` | `DesktopSQLiteChatStore` | ✓ | ✓ | ✓ | ✓ |
| 14 | context provided to a tool when it is executed b | `ToolExecutionContext` | `ToolExecutionContext` | ✓ | ✓ | ✓ | ✓ |
| 15 | orchestrate multi-turn conversation flow with to | `AIChatRuntime` | `ToolExecutionContext` | ✓ | ✗ | ✓ | ✓ |
| 16 | check whether a given API credential is valid an | `validateApiKey` | `validateApiKey` | ✓ | ✓ | ✓ | ✓ |
| 17 | sanitize markup by removing tags and decoding HT | `stripHtml` | `stripHtml` | ✓ | ✓ | ✓ | ✓ |
| 18 | QR code scanning screen for pairing with a deskt | `ScanScreen` | `ScanScreen` | ✓ | ✓ | ✓ | ✓ |
| 19 | collect and expose runtime performance counters  | `RuntimeMetrics` | `RuntimeMetrics` | ✓ | ✓ | ✓ | ✓ |
| 20 | configuration for the rainbow color cycling effe | `RainbowSettings` | `RainbowSettings` | ✓ | ✓ | ✓ | ✓ |
| 21 | permission flags controlling agent filesystem an | `ToolPermissions` | `ToolPermissions` | ✓ | ✓ | ✓ | ✓ |
| 22 | track prompt and completion token consumption fo | `TokenUsage` | `TokenUsage` | ✓ | ✓ | ✓ | ✓ |
| 23 | define the blueprint for functions that an AI mo | `ToolDefinition` | `ToolDefinition` | ✓ | ✓ | ✓ | ✓ |
| 24 | delay component mount until after the browser fi | `useDeferredMount` | `useDeferredMount` | ✓ | ✓ | ✓ | ✓ |
| 25 | manage backup copies and restoration of applicat | `BackupManager` | `BackupManager` | ✓ | ✓ | ✓ | ✓ |
| 26 | inter-process communication composable for Elect | `useIpc` | `useIpc` | ✓ | ✓ | ✓ | ✓ |
| 27 | look up localized text strings by translation ke | `t` | `t` | ✗ | ✓ | ✓ | ✓ |
| 28 | adapt shell commands for cross-platform compatib | `wrapCommand` | `wrapCommand` | ✓ | ✓ | ✓ | ✓ |

### 6.2 File Search (`search_files`)

**Hit@1:** 5/5 (100.0%)  
**Hit@3:** 5/5 (100.0%)  
**Hit@5:** 5/5 (100.0%)

| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | core AI message and provider type definitions | `types.ts` | `types.ts` | ✓ | ✓ | ✓ | ✓ |
| 2 | WebSocket server managing phone connections from | `ws-server.ts` | `ws-server.ts` | ✓ | ✓ | ✓ | ✓ |
| 3 | waifu personality and system prompt builder | `personality.ts` | `personality.ts` | ✓ | ✓ | ✓ | ✓ |
| 4 | secure credential storage implementations for de | `keystore.ts` | `keystore.ts` | ✓ | ✓ | ✓ | ✓ |
| 5 | creating and restoring backup snapshots of the r | `backups.js` | `backups.js` | ✓ | ✓ | ✓ | ✓ |

---

## 7. Key Findings & Recommendations

_Add observations, bugs found, and prioritised recommendations here._

---

## Completion Checklist

- [ ] Port 6789 confirmed free before every server start
- [ ] All `[BENCHMARK:*]` markers flush to log in real time
- [ ] Scenarios A–D all have BENCHMARK data captured
- [ ] LSP test matrix fully exercised
- [ ] Retrieval quality queries scored (if Phase 4 was run)
- [ ] Bugs found are documented with root cause + fix
- [ ] `benchmark_results/` deleted after report is committed

---

*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*
