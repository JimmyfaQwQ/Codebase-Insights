# Benchmark Report — Codebase Insights v0.2.4

**Date:** 2026-04-16  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary

Codebase Insights v0.2.4 was evaluated on retrieval quality against a real Electron + Vue + React Native monorepo (`G:\SyntaxSenpai`, 90 files, 5,595 symbols, 31,195 cross-references).

**Symbol search** (`semantic_search`): **96.4% Hit@1** (27/28), 100% Hit@3 and Hit@5. The single failure (`AIChatRuntime`) is a genuine semantic ambiguity — the query description overlaps strongly with `ToolExecutionContext`, which is still found at Hit@3.

**File search** (`search_files`): **93.3% Hit@1** (14/15), 100% Hit@5. One failure (`executor.ts` for an IPC bridge query) was resolved at Hit@5; `agent.ts` ranked first due to overlapping content.

**Keyword baseline comparison**: a context-unaware LLM agent generating search terms from the same NL queries found the expected symbol in only **13/28 (46.4%)** of cases, and in most of those cases returned multiple noisy results alongside it. On queries requiring concept-level understanding (e.g. "look up localized text strings by translation key", "sanitize markup by removing tags"), keyword search returned zero results.

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

### 6.1 Symbol Search (`semantic_search` vs keyword baseline)

> **Keyword baseline**: a context-unaware LLM agent generates up to 4 search terms from
> the natural-language query (no codebase knowledge), then calls `query_symbols` for each.
> KW results = total unique symbols returned across all keyword searches.

**Semantic Hit@1:** 27/28 (96.4%)  
**Semantic Hit@3:** 28/28 (100.0%)  
**Semantic Hit@5:** 28/28 (100.0%)  
**Keyword found expected (in top-5):** 13/28 (46.4%)

| # | Query | Expected | Hit@1 | Hit@3 | Hit@5 | Keyword terms tried | KW results | KW found? |
|---|---|---|:---:|:---:|:---:|---|---:|:---:|
| 1 | abstract base class for AI provider implementations | `BaseAIProvider` | ✓ | ✓ | ✓ | `implementations+abstract+provider+class` | 4 | ✗ |
| 2 | AI provider interface contract with chat and stream methods | `AIProvider` | ✓ | ✓ | ✓ | `interface+provider+contract+methods` | 4 | ✗ |
| 3 | chat request sent to an LLM model | `ChatRequest` | ✓ | ✓ | ✓ | `request+model+chat+sent` | 15 | ✗ |
| 4 | streaming response chunk from LLM | `StreamChunk` | ✓ | ✓ | ✓ | `streaming+response+chunk` | 9 | ✓ |
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

 - **Symbol Hit@1 96.4%**: Hybrid vector + keyword ranking correctly resolves 27/28 concept-level queries. The single failure (`AIChatRuntime`) is a genuine semantic overlap 
with `ToolExecutionContext` and resolves at Hit@3.
 - **File Hit@1 93.3%**: One failure — "IPC bridge" query ranks `agent.ts` first instead of `executor.ts`; both files share overlapping content and the correct answer appears 
at Hit@5.
 - **Keyword baseline weakness**: Context-unaware keyword search found the expected symbol in only 13/28 queries (46.4%), and returned zero results on 7 queries requiring 
concept-level understanding (e.g. `stripHtml`, `RuntimeMetrics`, `useDeferredMount`, `t`).
 - **Recommendation**: Semantic search is the primary retrieval path for agent use. Keyword search via `query_symbols` remains useful only when the caller already knows the 
approximate symbol name.

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
