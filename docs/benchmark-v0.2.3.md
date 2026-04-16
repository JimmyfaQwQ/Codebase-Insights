# Benchmark Report — Codebase Insights v0.2.3

**Date:** 2026-04-16  
**Target repository:** `G:\SyntaxSenpai`  
**Executed by:** automated benchmark pipeline (`scripts/run_benchmark.py`)

---

## Executive Summary

v0.2.3 is a **bug-fix release** focused on correcting a critical scoring inversion in the retrieval pipeline.

- **Bug fixed:** `semantic_search` and `search_files` were exporting the raw L2 distance as `score`, but the benchmark script sorted results descending (higher = better), completely inverting the ranking before computing Hit@K. Fixed by exposing the adjusted hybrid similarity score (higher = better) in the `score` field.
- **Phase 4 only** was run for this release (no full rebuild or incremental scenarios needed to validate the retrieval fix).
- **Symbol retrieval:** Hit@1 29.4% / Hit@3 76.5% / Hit@5 76.5% — good recall at depth.
- **File retrieval:** Hit@1 33.3% / Hit@3 100% / Hit@5 100% — near-perfect recall at depth.
- The low Hit@1 for symbols is a known issue: short method names (`stream`, `chat`, `execute`) score high due to keyword matching but are not the intended target.

---

## 1. Environment

| Component | Version / Detail |
|---|---|
| Codebase Insights | **0.2.3** |
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

**Hit@1:** 5/17 (29.4%)  
**Hit@3:** 13/17 (76.5%)  
**Hit@5:** 13/17 (76.5%)

| # | Query | Expected | Semantic top-1 | Keyword hit | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | abstract base class for AI provider implementati | `BaseAIProvider` | `BaseAIProvider` | ✓ | ✓ | ✓ | ✓ |
| 2 | AI provider interface contract with chat and str | `AIProvider` | `stream` | ✗ | ✗ | ✓ | ✓ |
| 3 | chat request sent to an LLM model | `ChatRequest` | `chat` | ✓ | ✗ | ✓ | ✓ |
| 4 | streaming response chunk from LLM | `StreamChunk` | `stream` | ✓ | ✗ | ✗ | ✗ |
| 5 | centralized registry for registering and dispatc | `ToolRegistry` | `register` | ✓ | ✗ | ✓ | ✓ |
| 6 | waifu personality traits on a numerical scale | `WaifuPersonalityTraits` | `Waifu` | ✓ | ✗ | ✓ | ✓ |
| 7 | waifu companion data model with name and avatar | `Waifu` | `Waifu` | ✗ | ✓ | ✓ | ✓ |
| 8 | build the complete system prompt for a waifu int | `buildSystemPrompt` | `Waifu` | ✓ | ✗ | ✓ | ✓ |
| 9 | relationship and context used to construct waifu | `SystemPromptContext` | `Waifu` | ✓ | ✗ | ✓ | ✓ |
| 10 | platform-native adapter for storing encrypted AP | `KeystoreAdapter` | `KeystoreAdapter` | ✓ | ✓ | ✓ | ✓ |
| 11 | convert messages to OpenAI-compatible message fo | `convertToOpenAIMessages` | `convertToOpenAIMessages` | ✓ | ✓ | ✓ | ✓ |
| 12 | options passed when creating a new AI provider i | `CreateProviderOptions` | `AzureOpenAIProvider` | ✓ | ✗ | ✗ | ✗ |
| 13 | WebSocket message envelope for phone to desktop  | `WSMessage` | `Message` | ✓ | ✗ | ✓ | ✓ |
| 14 | mobile initiates pairing with desktop over WebSo | `PairRequestPayload` | `connectToDesktop` | ✓ | ✗ | ✗ | ✗ |
| 15 | payload sent when a streaming LLM token arrives  | `StreamChunkPayload` | `stream` | ✓ | ✗ | ✗ | ✗ |
| 16 | SQLite-backed persistent chat conversation stora | `DesktopSQLiteChatStore` | `DesktopSQLiteChatStore` | ✓ | ✓ | ✓ | ✓ |
| 17 | context provided to a tool when it is executed b | `ToolExecutionContext` | `execute` | ✓ | ✗ | ✓ | ✓ |

### 6.2 File Search (`search_files`)

**Hit@1:** 1/3 (33.3%)  
**Hit@3:** 3/3 (100.0%)  
**Hit@5:** 3/3 (100.0%)

| # | Query | Expected file | Top-1 file | Name match | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|:---:|:---:|:---:|:---:|
| 1 | core AI message and provider type definitions | `types.ts` | `provider.ts` | ✓ | ✗ | ✓ | ✓ |
| 2 | WebSocket server managing phone connections from | `ws-server.ts` | `server.js` | ✓ | ✗ | ✓ | ✓ |
| 3 | waifu personality and system prompt builder | `personality.ts` | `personality.ts` | ✓ | ✓ | ✓ | ✓ |

---

## 7. Bug Fixes

| # | Symptom | Root cause | Fix | Severity |
|---|---|---|---|:---:|
| 1 | `semantic_search` and `search_files` `score` field is the raw L2 distance (lower = closer); benchmark script sorted descending, inverting rankings and making Hit@1/3/5 meaningless | `semantic_indexer.py` lines 892 and 988 set `"score": round(float(distance), 4)` (raw ChromaDB L2 distance), while `run_benchmark.py` assumed higher = better | In `search()`: replace distance with `_adjusted` (hybrid similarity after diversity decay) before removing internal fields. In `search_files()`: set `score` to `round(hybrid, 4)` directly. Sort in benchmark script (descending) is now correct. | **High** |

---

## 8. Key Findings & Recommendations

| Priority | Finding | Recommendation |
|:---:|---|---|
| High | **Score field inversion fixed** (v0.2.3) | `score` now reflects adjusted hybrid similarity (higher = better); previous versions had inverted ranking | 
| Medium | **Symbol Hit@1 at 29.4%** — short method names (`stream`, `chat`, `execute`) out-rank the intended interface/type symbols | Consider adding a kind-preference boost (Class/Interface/TypeAlias rank above Method/Function when the query describes a data model or contract) |
| Medium | **File Hit@1 at 33.3%** — `types.ts` loses to `provider.ts`; `ws-server.ts` loses to `server.js` | The filename keyword weight (`_FILE_KEYWORD_WEIGHT`) may need tuning; `types.ts` and `ws-server.ts` need their summaries to describe their role more precisely |
| Low | **Hit@3/5 excellent** (76.5% / 100%) | The correct answer is almost always in top-5; a re-ranker or query expansion step could push Hit@1 up significantly |

---

## Completion Checklist

- [x] Port 6789 confirmed free before every server start
- [ ] All `[BENCHMARK:*]` markers flush to log in real time
- [ ] Scenarios A–D all have BENCHMARK data captured
- [ ] LSP test matrix fully exercised
- [x] Retrieval quality queries scored (if Phase 4 was run)
- [x] Bugs found are documented with root cause + fix
- [ ] `benchmark_results/` deleted after report is committed

---

*Generated by `scripts/run_benchmark.py` — codebase-insights automated benchmark pipeline*
