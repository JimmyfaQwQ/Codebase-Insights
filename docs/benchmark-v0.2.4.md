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

### 6.1 Symbol Search (`semantic_search` vs keyword baseline)

> **Keyword baseline**: a context-unaware LLM agent generates 1-2 search terms from
> the natural-language query (no codebase knowledge), then calls `query_symbols`.
> Tokens-saved model: each result an agent must inspect ≈ 150 tokens.
> Semantic search stops at the correct result; keyword agent scans all returned results.

**Semantic Hit@1:** 27/28 (96.4%)  
**Semantic Hit@3:** 28/28 (100.0%)  
**Semantic Hit@5:** 28/28 (100.0%)
**Est. tokens saved vs keyword:** ≈14,250 total  (≈509 avg per query)

| # | Query | Expected | Semantic #1 | KW terms | KW #1 | Sem✓ | Tokens↓ |
|---|---|---|---|---|---|:---:|---:|
| 1 | abstract base class for AI provider implemen | `BaseAIProvider` | `BaseAIProvider` | `implementations+abstract` | `—` | ✓ | +600 |
| 2 | AI provider interface contract with chat and | `AIProvider` | `AIProvider` | `interface+provider` | `—` | ✓ | +600 |
| 3 | chat request sent to an LLM model | `ChatRequest` | `ChatRequest` | `request+model` | `handleAgentRequest` | ✓ | +600 |
| 4 | streaming response chunk from LLM | `StreamChunk` | `StreamChunk` | `streaming+response` | `chunkTextForStreaming` | ✓ | +0 |
| 5 | centralized registry for registering and dis | `ToolRegistry` | `ToolRegistry` | `centralized+registering` | `—` | ✓ | +600 |
| 6 | waifu personality traits on a numerical scal | `WaifuPersonalityTraits` | `WaifuPersonalityTraits` | `personality+numerical` | `formatPersonalityTraits` | ✓ | +150 |
| 7 | build the complete system prompt for a waifu | `buildSystemPrompt` | `buildSystemPrompt` | `interaction+complete` | `—` | ✓ | +600 |
| 8 | relationship and context used to construct w | `SystemPromptContext` | `SystemPromptContext` | `relationship+construct` | `setRelationship` | ✓ | +600 |
| 9 | platform-native adapter for storing encrypte | `KeystoreAdapter` | `KeystoreAdapter` | `encrypted+platform` | `—` | ✓ | +600 |
| 10 | convert messages to OpenAI-compatible messag | `convertToOpenAIMessages` | `convertToOpenAIMessages` | `compatible+messages` | `fetchOpenAICompatibleModels` | ✓ | +150 |
| 11 | options passed when creating a new AI provid | `CreateProviderOptions` | `CreateProviderOptions` | `creating+provider` | `—` | ✓ | +600 |
| 12 | WebSocket message envelope for phone to desk | `WSMessage` | `WSMessage` | `communication+websocket` | `formatCommunicationStyle` | ✓ | +150 |
| 13 | SQLite-backed persistent chat conversation s | `DesktopSQLiteChatStore` | `DesktopSQLiteChatStore` | `conversation+persistent` | `getMobileConversationSummary` | ✓ | +600 |
| 14 | context provided to a tool when it is execut | `ToolExecutionContext` | `ToolExecutionContext` | `provided+executed` | `—` | ✓ | +600 |
| 15 | orchestrate multi-turn conversation flow wit | `AIChatRuntime` | `ToolExecutionContext` | `conversation+orchestrate` | `getMobileConversationSummary` | ✗ | +450 |
| 16 | check whether a given API credential is vali | `validateApiKey` | `validateApiKey` | `credential+whether` | `—` | ✓ | +600 |
| 17 | sanitize markup by removing tags and decodin | `stripHtml` | `stripHtml` | `sanitize+removing` | `—` | ✓ | +600 |
| 18 | QR code scanning screen for pairing with a d | `ScanScreen` | `ScanScreen` | `scanning+pairing` | `—` | ✓ | +600 |
| 19 | collect and expose runtime performance count | `RuntimeMetrics` | `RuntimeMetrics` | `performance+histograms` | `—` | ✓ | +600 |
| 20 | configuration for the rainbow color cycling  | `RainbowSettings` | `RainbowSettings` | `configuration+rainbow` | `—` | ✓ | +600 |
| 21 | permission flags controlling agent filesyste | `ToolPermissions` | `ToolPermissions` | `controlling+permission` | `—` | ✓ | +600 |
| 22 | track prompt and completion token consumptio | `TokenUsage` | `TokenUsage` | `consumption+completion` | `—` | ✓ | +600 |
| 23 | define the blueprint for functions that an A | `ToolDefinition` | `ToolDefinition` | `blueprint+functions` | `—` | ✓ | +600 |
| 24 | delay component mount until after the browse | `useDeferredMount` | `useDeferredMount` | `component+finishes` | `—` | ✓ | +600 |
| 25 | manage backup copies and restoration of appl | `BackupManager` | `BackupManager` | `restoration+application` | `—` | ✓ | +600 |
| 26 | inter-process communication composable for E | `useIpc` | `useIpc` | `communication+composable` | `formatCommunicationStyle` | ✓ | +150 |
| 27 | look up localized text strings by translatio | `t` | `t` | `translation+localized` | `—` | ✓ | +600 |
| 28 | adapt shell commands for cross-platform comp | `wrapCommand` | `wrapCommand` | `compatibility+commands` | `—` | ✓ | +600 |

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
