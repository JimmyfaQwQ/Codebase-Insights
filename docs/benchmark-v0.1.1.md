# Benchmark Report — Codebase Insights v0.1.1

**Date:** 2026-04-14  
**Target repository:** `G:\SyntaxSenpai`  
**Repository type:** Electron + Vue + React Native monorepo  
**Executed by:** automated benchmark pipeline (`SKILL.md`)

---

## Executive Summary

This benchmark evaluates **Codebase Insights v0.1.1** on a real-world frontend-heavy monorepo containing **118 files**, **5,587 symbols**, and **32,570 cross-references**.

The results support the core claim behind Codebase Insights:

> a persistent, symbol-aware, semantically indexed code understanding layer can help coding agents navigate repositories more accurately and with less repeated exploration than naive keyword search alone.

Key results:

- **Full pipeline time:** **427.7s (~7.1 min)**
- **No-change catch-up:** **0.05s**
- **Incremental edits:** **~18–21s**
- **Retrieval quality:** **68.4% Hit@1**, **89.5% Hit@3**, **100% Hit@5**
- **Storage footprint:** **13.80 MB**
- **Peak RSS:** **3,201 MiB**

Most importantly, unchanged repositories incur effectively **zero semantic rework**, confirming that persistent indexing is working as intended. Incremental update performance is already practical, and project summary maintenance now supports **incremental mode**, reducing post-edit latency substantially compared with earlier full-regeneration behavior.

Current limitations remain around:
- semantically retrieving anonymous / inline logic
- convention-heavy code patterns
- partial symbol coverage due to indexing filters
- the cost of full project-summary generation during cold rebuilds

---

## 1. Environment

| Component | Version / Detail |
|---|---|
| Codebase Insights | **0.1.1** |
| Python | 3.x (editable install from `E:\Codebase-Insights`) |
| chromadb | 1.5.7 |
| langchain-openai | 1.1.12 |
| typescript-language-server | 5.1.3 |
| clangd | 18.1.8 |
| LLM provider | OpenAI-compatible (`api.deepseek.com`) |
| LLM model | `deepseek-chat` |
| Embedding provider | Ollama (`localhost:11434`) |
| Embedding model | `bge-m3` |
| Semantic batching | batch size 16 / concurrency 16 |
| Indexed symbol kinds | Class, Method, Function, Interface, Enum, Constructor |
| OS | Windows 11 |

### Target repository statistics

| Metric | Value |
|---|---:|
| Files processed | 118 |
| Files indexed | 114 |
| Files skipped by LSP | 4 |
| Total symbols | 5,587 |
| Cross-references | 32,570 |
| Languages | TypeScript, JavaScript, Vue |

---

## 2. Benchmark Scope

This benchmark covers:

1. **Cold full rebuild**
2. **Incremental update scenarios**
3. **Semantic retrieval quality**
4. **LSP navigation correctness**
5. **Storage and resource footprint**
6. **Observed bugs and fixes**

The goal is not only to measure runtime, but also to evaluate whether Codebase Insights meaningfully improves code understanding workflows for agent-style usage.

---

## 3. Full Rebuild

This section measures the cost of building the index and semantic layer from scratch.

### 3.1 Startup

| Metric | Value |
|---|---:|
| `lang_detect` | 0.158 s |
| `lsp_javascript/typescript_start_server` | 0.013 s |
| `lsp_javascript/typescript_initialize` | 0.184 s |
| `lsp_cpp_start_server` | 0.013 s |
| `lsp_cpp_initialize` | 1.063 s |
| `semantic_init` | 2.437 s |
| `indexer_create` | 0.000 s |
| **`total_pre_server`** | **6.168 s** |

### Observation
Startup is now lightweight and behaves as expected. The previously observed anomalous `WorkspaceIndexer` creation overhead is no longer present.

---

### 3.2 Workspace Indexer

| Metric | Value |
|---|---:|
| `wall_time` | 62.01 s |
| `files_total` | 118 |
| `indexed` | 114 |
| `skipped` | 4 |
| `errors` | 0 |
| `total_symbols` | 5,587 |
| `total_refs` | 32,570 |

### Observation
The symbol indexing phase completed without errors. Four files were skipped by LSP, but the repository was otherwise fully processed.

---

### 3.3 Semantic Indexer

| Metric | Value |
|---|---:|
| `wall_time` | 178.26 s |
| `symbols_total` | 527 |
| `summarised` | 527 |
| `skipped` | 0 |
| `errors` | 0 |
| `llm_batch` | 144.46 s |
| `chroma_insert` | 33.46 s |
| `sqlite_write` | 0.26 s |
| `context_extract` | 0.04 s |
| `pending_query` | 0.014 s |
| `input_tokens` | 147,167 |
| `output_tokens` | 26,977 |

### Interpretation
Only **527 of 5,587 symbols** were semantically indexed. This is expected and reflects the current filtering strategy:

- only selected symbol kinds are indexed
- helper lambdas, anonymous callbacks, and type aliases are excluded
- low-value symbols are filtered out to control cost and noise

This is an intentional quality/cost trade-off, not a failure.

---

### 3.4 File Summaries

| Metric | Value |
|---|---:|
| `wall_time` | 40.51 s |
| `files_pending` | 114 |

---

### 3.5 Project Summary

| Metric | Value |
|---|---:|
| `wall_time` | 138.06 s |
| `mode` | full |

### Observation
Cold project-level summarization remains one of the most expensive phases of a full rebuild.

---

### 3.6 Storage Footprint

| Store | Size |
|---|---:|
| SQLite | 6.49 MB |
| ChromaDB | 7.31 MB |
| **Total** | **13.80 MB** |

### Interpretation
The persisted storage cost is small relative to the size of the indexed repository. This strengthens the value proposition of persistent reuse across sessions.

---

### 3.7 End-to-End Resource Usage

| Metric | Value |
|---|---:|
| Full pipeline wall-clock | **427.7 s (~7.1 min)** |
| Peak RSS (process tree) | **3,201 MiB** |
| Avg CPU | ~0% |

### Notes
- This workload is primarily **I/O- and LLM-bound**, not CPU-bound.
- Peak memory is largely attributable to the TypeScript language server process tree.

---

## 4. Incremental Update Scenarios

A central goal of Codebase Insights is to avoid redoing work on unchanged code and to keep edits local whenever possible. The following scenarios evaluate that behavior.

Server was started fresh **without rebuild flags**. Initial catch-up confirmed near-zero work on unchanged state.

### 4.1 Scenario Summary

| Scenario | Watchdog + LSP | Semantic | File Summary | Project Summary | Total (approx) |
|---|---:|---:|---:|---:|---:|
| A: No change | — | 0.04 s | — | — | **0.05 s** |
| B: Leaf file edit | ~1 s | 7.62 s | 4.01 s | 5.73 s (incremental) | **~18 s** |
| C: Core file edit | ~1 s | 4.53 s | 4.63 s | 8.66 s (incremental) | **~19 s** |
| D: New file | ~1 s | 4.58 s | 3.86 s | 11.87 s (incremental) | **~21 s** |
| Full rebuild baseline | 62.01 s | 178.26 s | 40.51 s | 138.06 s | **427.7 s** |

---

### 4.2 Scenario A — No-change restart

- **INDEXER:** `wall_time=0.05s indexed=0 skipped=118`
- **SEMANTIC:** `wall_time=0.04s summarised=0 skipped=527`

### Conclusion
Hash-based skipping works correctly. On an unchanged repository, Codebase Insights performs effectively **zero reprocessing**.

This is one of the strongest results in the benchmark because it directly validates persistent cross-session reuse.

---

### 4.3 Scenario B — Leaf file edit

**Edited file:** `packages/ui/src/composables/use-deferred-mount.ts`  
**Change:** added a top-level constant `DEFERRED_FRAMES = 2`

Results:

- **INDEXER:** triggered ~1s after save
- **SEMANTIC:** `wall_time=7.62s summarised=1`
- **FILE_SUMMARIES:** `wall_time=4.01s files_pending=1`
- **PROJECT_SUMMARY:** `wall_time=5.73s mode=incremental changes=1`

### Conclusion
A small local change remains local. The update is practical for iterative development and far cheaper than any rebuild path.

### Note
This edit added a new symbol, so file and project summaries were updated. A comment-only edit without symbol changes would be cheaper.

---

### 4.4 Scenario C — Core file edit

**Edited file:** `packages/ai-core/src/types.ts`  
**Change:** added optional `metadata?: Record<string, unknown>` to `Message`

Results:

- **SEMANTIC:** `wall_time=4.53s summarised=14`
- **FILE_SUMMARIES:** `wall_time=4.63s files_pending=1`
- **PROJECT_SUMMARY:** `wall_time=8.66s mode=incremental changes=1`
- **End-to-end time:** ~19s

### Conclusion
A structurally meaningful edit in a central type file still remains inexpensive compared with a rebuild. This is strong evidence that the incremental path is now working at symbol, file, and project-summary levels.

---

### 4.5 Scenario D — New file added

**New file:** `packages/ai-core/src/benchmark_test_new_file.ts`  
**Contents:** 1 interface, 1 class, 4 methods, 1 factory function (7 symbols)

Results:

- **SEMANTIC:** `wall_time=4.58s summarised=7`
- **FILE_SUMMARIES:** `wall_time=3.86s files_pending=1`
- **PROJECT_SUMMARY:** `wall_time=11.87s mode=incremental changes=1`

### Conclusion
New-file ingestion is working correctly. The cost remains low enough to be usable in active editing workflows.

---

### 4.6 Incremental Update Takeaways

| Finding | Result |
|---|---|
| No-change restart | Excellent |
| Local edit handling | Practical |
| Core-file edit handling | Good |
| New-file ingestion | Good |
| Project summary maintenance | Incremental path now working |

### Overall conclusion
Incremental behavior is now one of the strongest parts of the system. The benchmark shows that Codebase Insights no longer needs to pay full-project semantic rebuild costs on ordinary edits.

---

## 5. Retrieval Quality

Queries were issued against the running MCP server using semantic search with `limit=5`.

### 5.1 Retrieval Summary

| Metric | Score |
|---|---:|
| **Hit@1** | **13 / 19 = 68.4%** |
| **Hit@3** | **17 / 19 = 89.5%** |
| **Hit@5** | **19 / 19 = 100%** |

### Interpretation
This is a strong result for concept-level retrieval over real code. The most important number here is arguably **Hit@3**, because agent workflows typically inspect more than one candidate.

---

### 5.2 Per-query Results

| # | Query | Top-1 Result | Hit@1 | Hit@3 | Hit@5 |
|---|---|---|---|---|---|
| 1 | AI provider initialization and configuration | `createOpenAIProvider` | ✓ | ✓ | ✓ |
| 2 | LLM streaming response handling | `stream (LMStudioProvider)` | ✓ | ✓ | ✓ |
| 3 | Conversation history memory management | `buildMemoryContext` | ✓ | ✓ | ✓ |
| 4 | Error handling when AI provider call fails | `AzureOpenAIProvider` | ✗ | ✗ | ✓ |
| 5 | Plugin extension point registration | `ToolPluginManifest` | ✓ | ✓ | ✓ |
| 6 | WebSocket protocol message handling | `handleWsMessage` | ✓ | ✓ | ✓ |
| 7 | Character personas and waifu configuration | `Waifu` interface | ✓ | ✓ | ✓ |
| 8 | UI animation transition deferred rendering | `on (ipcRenderer)` | ✗ | ✓ | ✓ |
| 9 | Agent tool registry lookup and dispatch | `ToolRegistry` | ✓ | ✓ | ✓ |
| 10 | Database schema migration storage initialization | `loadThemeFromStorage` | ✗ | ✓ | ✓ |
| 11 | Tool call execution and result injection | `executeToolCall` | ✓ | ✓ | ✓ |
| 12 | IPC bridge renderer to main process communication | `on (useIpc)` | ✓ | ✓ | ✓ |
| 13 | QR code pairing mobile device connection | `ensureMobileConversation` | ✗ | ✗ | ✓ |
| 14 | Runtime server startup configuration loading | `setDesktopRuntimeConfig` | ✓ | ✓ | ✓ |
| 15 | Backup and restore data management | `restoreBackup` | ✓ | ✓ | ✓ |
| 16 | Relationship level progression system | `updateRelationship` | ✓ | ✓ | ✓ |
| 17 | Token counting and context window limits | `TokenUsage` | ✓ | ✓ | ✓ |
| 18 | Spotify music playback integration | `on (useIpc)` | ✗ | ✓ | ✓ |
| 19 | Abstract base class provider interface definition | `BaseAIProvider` | ✓ | ✓ | ✓ |

---

### 5.3 Comparison with Keyword Symbol Search

| Query | Semantic top result | Keyword-style top result | Better approach |
|---|---|---|---|
| AI provider classes | `createOpenAIProvider` | `AnthropicProvider` | Semantic |
| Streaming response handling | `stream (LMStudioProvider)` | `chunkTextForStreaming`, `isStreamChunk` | Semantic |

### Key finding
Keyword symbol search only matches symbol names directly. It cannot reliably recover:

- method overrides
- abstract usage patterns
- conceptually related code that does not share the same lexical tokens

Semantic search, by contrast, successfully surfaced multiple `stream()` implementations across providers from a single natural-language query.

This is an important differentiator for agent use cases.

---

### 5.4 Failure Analysis

| Query | Failure mode | Root cause |
|---|---|---|
| Error handling when AI provider call fails | top-ranked results point to provider stubs | error handling is inline and anonymous |
| UI animation deferred rendering | generic IPC listener outranks `useDeferredMount` | embedding overlap with animation-related listener summaries |
| QR code pairing | adjacent mobile conversation logic outranks pairing payload types | relevant logic is distributed across WS handshake flow |
| Spotify playback integration | generic IPC listener ranked first | “integration” is semantically broad and overlaps generic bridges |

### Retrieval conclusion
Semantic retrieval is already strong on concept-level, architecture-level, and implementation-oriented queries, but remains weaker for:
- anonymous inline logic
- cross-cutting flows without a strong named symbol
- broad natural-language terms whose embeddings overlap multiple subsystems

---

## 6. LSP Navigation

All navigation tests used `file:///G:/...` URI format.

| Tool | Test | Result |
|---|---|---|
| `lsp_capabilities` | no args | ✓ both `javascript/typescript` and `cpp` clients initialized |
| `lsp_document_symbols` | `packages/ai-core/src/providers/base.ts` | ✓ nested symbol tree returned |
| `lsp_definition` | `BaseAIProvider` reference in `anthropic.ts:8` | ✓ resolved to `base.ts:17` |
| `lsp_references` | `BaseAIProvider` in `base.ts:17` | ✓ 23 locations across 12 files |
| `lsp_implementation` | `BaseAIProvider` in `base.ts:17` | ✓ 24 subclasses returned |
| `lsp_hover` | `BaseAIProvider` in `base.ts:17` | ✓ type + JSDoc returned |

### Conclusion
LSP-backed navigation worked correctly in all tested cases. This remains one of the strongest differentiators of Codebase Insights.

In particular, `definition`, `references`, and `implementation` provide structural information that keyword search cannot reconstruct reliably.

---

## 7. Bug Fixes Verified During Benchmarking

### Bug #1 — File deletion did not update project summary

| Field | Detail |
|---|---|
| Symptom | deleted files remained represented in project summary |
| Root cause | `file_summaries` row was not removed; project summary was not re-indexed after deletion |
| Fix | delete `file_summaries` row in `_do_remove_file`; call `_index_project_summary()` from `remove_file` outside the lock |
| File | `src/codebase_insights/semantic_indexer.py` |
| Severity | Medium |

### Verification result
The bug was reproduced, fixed, and verified as part of the benchmark workflow.

This is a good example of the benchmark pipeline serving not just as measurement tooling, but as a mechanism for validating correctness in realistic editing scenarios.

---

## 8. Key Findings

| Priority | Finding | Recommendation |
|---|---|---|
| P1 | Incremental project summary updates are now working | Keep this path as the default and continue optimizing |
| P2 | Full project-summary generation remains the largest cold-build bottleneck | Explore chunked or hierarchical project summarization |
| P3 | Only 527 / 5,587 symbols were semantically indexed | Document filtering behavior clearly; expose tuning guidance for `min_ref_count` |
| P4 | Retrieval errors cluster around inline or anonymous logic | Document as expected limitation; consider future file-level or block-level retrieval |
| P5 | Peak memory is high during full indexing | Acceptable for TS-heavy repos, but worth profiling over larger monorepos |
| P6 | Startup is now healthy | No action needed; prior anomaly appears resolved |

---

## 9. Overall Assessment

This benchmark shows that **Codebase Insights v0.1.1 is now meaningfully stronger as an agent-facing code understanding system than a pure keyword-search workflow**.

What is already validated:

- fast startup
- reliable full indexing
- practical incremental updates
- near-zero no-change catch-up
- strong LSP navigation
- useful semantic retrieval quality
- low persistent storage overhead

What still needs improvement:

- retrieval of anonymous and inline behaviors
- semantic coverage trade-offs caused by symbol filtering
- cost of full project-level summarization on cold rebuilds

Overall, the system now demonstrates a compelling balance of:

- **structural precision** from LSP
- **semantic flexibility** from embeddings and summaries
- **practical persistence** through incremental indexing

That combination is exactly what coding agents need, and the benchmark now provides concrete evidence that it works in practice.

---

## 10. Suggested README Highlights

Use the following condensed summary in the main README if desired:

```md
## Benchmark Highlights (v0.1.1)

Benchmarked on a real Electron + Vue + React Native monorepo:

- **118 files**, **5,587 symbols**, **32,570 refs**
- **Full pipeline:** ~7.1 min
- **No-change catch-up:** 0.05s
- **Incremental edits:** ~18–21s
- **Hit@1:** 68.4%
- **Hit@3:** 89.5%
- **Hit@5:** 100%
- **Storage footprint:** 13.8 MB
- **LSP navigation:** 23 references and 24 implementations resolved for `BaseAIProvider`
```

---

*Report generated by the automated benchmark pipeline. Raw intermediate outputs were written to `benchmark_results/` and are not committed.*
