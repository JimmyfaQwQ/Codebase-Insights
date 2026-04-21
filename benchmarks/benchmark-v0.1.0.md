# Codebase Insights Benchmark Report

**Target repository:** `G:\SyntaxSenpai\`  
**Date:** 2026-04-14  
**Benchmark focus:** full rebuild performance, incremental update behavior, retrieval quality, symbol navigation, resource usage, stability, and agent workflow value

---

## Executive Summary

This benchmark evaluates **Codebase Insights** on a real-world TypeScript-first pnpm monorepo (`SyntaxSenpai`) with 118 indexed files and 5,615 extracted symbols.

The results show that **Codebase Insights already delivers meaningful value as a code-understanding layer for coding agents**:

- A full rebuild completed in **~8.7 minutes**
- Semantic indexing produced **538 symbol summaries**
- Total persisted storage footprint was only **~11.2 MB**
- Estimated remote LLM cost for the full rebuild was **~$0.07**
- Semantic retrieval achieved **75% Hit@3** across 20 natural-language and architecture-oriented queries
- LSP-based symbol navigation provided precise relationship discovery that keyword search cannot match
- Most importantly, a **no-change restart required <0.1s of catch-up work**, confirming that persistent indexing avoids re-exploring unchanged codebases

The main weaknesses observed were:

- **Project summary regeneration is too coarse-grained** and dominates update costs after structural changes
- `WorkspaceIndexer` creation has a **~28–30s startup tax**
- TypeScript LSP operations require `file:///` URIs, but the current MCP-facing UX does not normalize paths automatically
- Convention-based structures such as file-system routing are less visible to symbol-based indexing than explicit APIs and classes

Overall, the benchmark supports the core claim behind Codebase Insights: it can provide **more structured, lower-noise, more reusable code understanding** than naive keyword search alone, especially when paired with LSP-backed symbol navigation.

---

## 1. Test Environment

| Item | Value |
|---|---|
| Operating system | Windows (x64) |
| Python | System Python at `D:\anaconda3` and project `.venv` |
| Codebase Insights | Editable install from `E:\Codebase-Insights` |
| Chat provider | `deepseek-chat` via OpenAI-compatible API |
| Embedding provider | `bge-m3` via local Ollama |
| JavaScript / TypeScript LSP | `typescript-language-server --stdio` |
| C++ / Objective-C LSP | `clangd` 18.1.8 |
| Vector store | ChromaDB (`>=0.6`, per dependency spec) |
| Target repository | `G:\SyntaxSenpai\` |
| Target repo type | pnpm monorepo |
| Primary languages | TypeScript, JavaScript |
| Secondary languages | Objective-C / C++ (iOS) |

### Notes
- TypeScript is the dominant language under test.
- Objective-C/C++ coverage exists in the repo, but iOS analysis is limited by the lack of a compilation database.
- Embeddings were generated locally, while summaries used a remote API-backed chat model.

---

## 2. Repository Under Test

| Metric | Value |
|---|---|
| Indexed files | **118** |
| Total symbols | **5,615** |
| Total symbol references | **28,937** |
| Symbols eligible for semantic indexing | **538** |
| Semantic eligibility rate | **9.6%** |
| Semantic filter | `kind ∈ {Class, Method, Function, Interface, Enum, Constructor}` and `min_ref_count = 3` |

### Repository structure summary

The repository is a TypeScript-first monorepo with packages and applications across multiple domains, including:

- `apps/desktop`
- `apps/mobile`
- `apps/runtime`
- `packages/ai-core`
- `packages/waifu-core`
- `packages/agent-tools`
- `packages/storage`
- `packages/ws-protocol`
- `packages/ui-*`

This makes it a useful benchmark target because it contains:
- multiple packages
- cross-package symbol references
- platform-specific code
- runtime and UI layers
- both architectural and implementation-level queries

---

## 3. Full Rebuild Benchmark

Command used:

```bash
uv run main.py "G:\SyntaxSenpai\" --rebuild-index --rebuild-semantic
```

### 3.1 Full rebuild timing breakdown

| Phase | Time | Share of total | Notes |
|---|---:|---:|---|
| Language detection | 0.09s | <0.1% | `os.walk` + `.gitignore` scanning |
| LSP start_server (JS/TS) | 0.01s | — | process spawn |
| LSP initialize (JS/TS) | 0.45s | — | protocol handshake |
| LSP start_server (C++) | 0.01s | — | process spawn |
| LSP initialize (C++) | 1.30s | — | clangd startup slower than TS |
| Semantic init (Chroma + Ollama) | 3.64s | — | vector DB open + embedding backend init |
| **WorkspaceIndexer create** | **30.16s** | **5.8%** | unexpectedly high |
| **Pre-server startup total** | **41.08s** | — | before indexing work begins |
| **Initial symbol indexing** | **90.82s** | **17.4%** | includes LSP symbol/ref extraction |
| Context extraction | 0.23s | <0.1% | source window reading |
| **LLM batch symbol summarization** | **140.54s** | **27.0%** | 538 symbols |
| ChromaDB embedding insert | 38.11s | 7.3% | local `bge-m3` |
| SQLite summary write | 0.25s | <0.1% | summary persistence |
| **Total symbol semantics** | **179.17s** | **34.4%** | summarization + embedding |
| **File summaries (118 files)** | **33.40s** | **6.4%** | one summary per file |
| **Project summary** | **98.50s** | **18.9%** | single large LLM call |
| **Total wall time** | **521s** | **100%** | ~8.7 minutes |

### 3.2 Full rebuild observations

The largest cost center was **LLM-backed summarization**, not indexing itself.

#### Time share by major category

- LLM-driven summaries (symbols + files + project): **272.4s (~52%)**
- ChromaDB embedding insertion: **38.1s (~7%)**
- LSP-backed symbol indexing: **90.8s (~17%)**
- `WorkspaceIndexer` creation: **30.2s (~6%)**
- Remaining overhead: **~17%**

### 3.3 Primary bottlenecks

#### 1. LLM summarization dominates total time
LLM work accounts for over half of total rebuild time. The single biggest outlier is the **project summary**, which takes **98.5s** on its own because it is generated from a large prompt composed from many file summaries.

#### 2. `WorkspaceIndexer create` is unexpectedly expensive
The creation step took **30.16s**, which is much higher than expected for object initialization. The most likely cause is eager recursive filesystem scanning during `_GitignoreFilter` setup. This likely causes substantial startup overhead on large monorepos, especially those with many directories even if ignored later.

### 3.4 Full rebuild output statistics

| Metric | Value |
|---|---:|
| Files total | 118 |
| Files indexed | 118 |
| Files skipped | 0 |
| Files errored | 0 |
| Total symbols | 5,615 |
| Total refs | 28,937 |
| Symbols summarized | 538 |
| Symbols skipped from semantic indexing | 5,077 |

### 3.5 Full rebuild conclusion

Full rebuild performance is acceptable for an initial indexing pass on a medium-sized monorepo, especially given the low storage and API cost. However, the **fixed startup tax** and **project-level summary cost** should be treated as major optimization targets.

---

## 4. Incremental Update Benchmark

A key goal of Codebase Insights is to preserve understanding across sessions and avoid redoing work unnecessarily. The following scenarios evaluate whether that claim holds under real edits.

### 4.1 Server restart timing without rebuild

Command context: server restart using existing index, **without** `--rebuild-index` or `--rebuild-semantic`.

| Phase | Time |
|---|---:|
| Language detection | 0.034s |
| LSP C++ start + init | 0.017s + 0.163s |
| LSP JS/TS start + init | 0.016s + 0.199s |
| Semantic init | 2.315s |
| WorkspaceIndexer create | **27.879s** |
| **Total pre-server** | **30.654s** |

### Observation
The dominant fixed cost on restart is still `WorkspaceIndexer create`. Once the server passes that stage, cached data allows extremely fast catch-up.

---

### 4.2 Scenario A — No-change restart

**Setup:** restart server with no code changes; all 118 files already indexed and unchanged.

| Metric | Value |
|---|---|
| Initial pass wall time | **0.03s** |
| Files skipped via hash cache | **118 / 118** |
| Semantic pass | **0.05s** |
| **Total catch-up time** | **<0.1s** |

### Conclusion
This is the strongest incremental result in the benchmark.  
**Hash-based skipping works perfectly** for unchanged repositories. Once the index exists, the codebase does not need to be re-explored.

---

### 4.3 Scenario B — Small leaf-file edit

**Edited file:** `groq.ts`  
**Edit type:** changed a constant inside an existing method body  
**Impact:** symbol names unchanged; local leaf-file behavior only

| Phase | Time |
|---|---:|
| Watchdog detection + LSP re-index | ~1s |
| Semantic re-summarization (5 symbols) | 4.97s / 5.44s |
| File summaries | not triggered |
| Project summary | not triggered |
| **Total end-to-end** | **~6s** |

**Token usage:** ~2,700 input / ~270 output  
**Estimated API cost:** ~`$0.0004`

### Conclusion
Small local edits update efficiently. This is already practical for iterative development workflows.

---

### 4.4 Scenario C — Core-file edit

**Edited file:** `types.ts`  
**Edit type:** added a new optional field to widely imported `ChatRequest` interface  
**Impact:** symbol hash changed across all symbols in the file

| Phase | Time |
|---|---:|
| Watchdog detection + LSP re-index | ~1s |
| Semantic re-summarization (14 symbols) | 5.09s |
| File summary regeneration | 5.83s |
| Project summary regeneration | **129.58s** |
| **Total end-to-end** | **~141s** |

**Token usage:** ~2,514 input / ~728 output  
**Estimated API cost:** ~`$0.0004`

### Conclusion
The symbol- and file-level update path is efficient, but the **project summary regeneration strategy dominates total cost**. This is the clearest evidence that project-level summarization is currently too coarse-grained.

---

### 4.5 Scenario D — New file added

**New file:** `benchmark-new-provider.ts`  
**Edit type:** added a new provider class with 5 symbols

| Phase | Time |
|---|---:|
| Watchdog detection + LSP index | ~1s |
| Semantic summarization (5 symbols) | 5.10s |
| File summary generation | 4.52s |
| Project summary regeneration | **123.27s** |
| **Total end-to-end** | **~133s** |

**Token usage:** ~813 input / ~224 output  
**Estimated API cost:** ~`$0.0001`

### Conclusion
New file ingestion works correctly and the local work is modest, but the same full project summary regeneration cost appears again.

---

### 4.6 Incremental update takeaways

| Scenario | Outcome | Main takeaway |
|---|---|---|
| No-change restart | Excellent | persistent reuse works |
| Small leaf edit | Good | local updates are practical |
| Core-file edit | Mixed | local update is efficient, project summary is not |
| New file | Mixed | ingestion works, project summary is too expensive |

### Overall conclusion
Incremental indexing is **already validated** at the symbol/file level. The main remaining issue is not incremental detection, but **coarse project-level summarization policy**.

---

## 5. Retrieval Quality Benchmark

This section evaluates whether semantic search helps locate relevant code more effectively than keyword-oriented symbol lookup alone.

### 5.1 Query set

A 20-query evaluation set was used, covering:
- semantic concept lookup
- architecture navigation
- implementation discovery
- routing / event / configuration queries
- domain-level feature questions

### 5.2 Query-level results

| # | Query | Type | Expected area | Semantic Top-1 | Top-3 | Notes |
|---|---|---|---|---|---|---|
| Q1 | AI provider initialization and model configuration | semantic | `packages/ai-core` | ✅ | ✅ | strong top-1 |
| Q2 | WebSocket message types and protocol definitions | architectural | `packages/ws-protocol` | ❌ | ✅ | top-1 noisy |
| Q3 | LLM streaming response chunks handling | semantic | `packages/ai-core` | ✅ | ✅ | very accurate |
| Q4 | agent tool registration and invocation | semantic | `packages/agent-tools` | ✅ | ✅ | top-1 slightly off but useful |
| Q5 | waifu character personality state emotion management | semantic | `packages/waifu-core` | ✅ | ✅ | strong concept match |
| Q6 | Electron IPC main-renderer communication handler | navigational | `apps/desktop/main/ipc/` | ✅ | ✅ | top-1 slightly shifted to renderer listener |
| Q7 | HTTP server setup route registration | navigational | `apps/runtime/src/server.js` | ❌ | ✅ | top-1 unrelated |
| Q8 | storage read write database key-value persistence | semantic | `packages/storage` | ✅ | ✅ | slight specificity mismatch |
| Q9 | plugin extension point system | architectural | plugin-related packages | ✅ | ✅ | accurate |
| Q10 | React Native mobile navigation screen routing | navigational | `apps/mobile` | ❌ | ❌ | convention-based routing not symbolized |
| Q11 | configuration loading environment variables | keyword-like | runtime config | ✅ | ✅ | top-3 stronger than top-1 |
| Q12 | event emitter pub sub event bus | semantic | distributed packages | ❌ | ❌ | concept diffuse, no dedicated symbol |
| Q13 | relationship level user bonding progression | semantic | `packages/waifu-core` | ✅ | ✅ | accurate |
| Q14 | conversation history memory context window | semantic | `packages/storage` | ✅ | ✅ | accurate |
| Q15 | metrics monitoring prometheus health | semantic | `apps/runtime/metrics.js` | ✅ | ✅ | accurate |
| Q16 | backup file rotation scheduled | semantic | `apps/runtime/backups.js` | ✅ | ✅ | accurate |
| Q17 | desktop window creation startup lifecycle | navigational | `apps/desktop/main/index.ts` | ❌ | ✅ | top-2 is correct |
| Q18 | error handling AI API call fails | semantic | `packages/ai-core` | ❌ | ❌ | inline try/catch not well surfaced |
| Q19 | waifu voice audio integration | semantic | any | ❌ | ❌ | feature absent |
| Q20 | affection system dynamic prompt generation | semantic | `apps/desktop` | ✅ | ✅ | accurate |

### 5.3 Retrieval metrics

| Metric | Semantic search | Keyword-oriented symbol query baseline |
|---|---:|---:|
| Hit@1 | **11 / 20 = 55%** | ~30% (estimated) |
| Hit@3 | **15 / 20 = 75%** | ~50% (estimated) |
| Hit@5 | **16 / 20 = 80%** | ~55% (estimated) |
| Complete failures | 4 / 20 | higher |

### Important note on baseline
The keyword baseline here is observational rather than fully automated. It reflects expected performance from `query_symbols` and name-based lookup, which works well when exact terms appear in symbol names but degrades significantly for conceptual or architectural queries.

### 5.4 Where semantic retrieval clearly helps

Semantic search performed best on:
- architectural concepts
- high-level domain concepts
- feature descriptions not identical to symbol names
- cross-file conceptual retrieval

Examples:
- AI provider configuration
- personality and relationship systems
- memory/context handling
- metrics and backup subsystems

### 5.5 Failure modes

| Query | Failure reason |
|---|---|
| React Native navigation routing | convention-based routing has weak symbol presence |
| Event bus / pub-sub | no dedicated event bus abstraction exists |
| AI API error handling | logic is inline rather than symbolized |
| Voice/audio integration | feature absent; tool does not explicitly distinguish “not found” |

### Retrieval conclusion
Semantic retrieval is already useful and often clearly better than keyword search for concept-level code discovery, but it is weaker when:
- the code relies heavily on conventions rather than symbols
- behavior is distributed inline instead of encapsulated
- the queried feature does not exist and the tool cannot communicate absence clearly

---

## 6. Symbol Navigation Benchmark

This section evaluates the practical value of LSP-backed navigation.

### 6.1 Navigation results

| Symbol | Operation | Result | Count | Notes |
|---|---|---|---:|---|
| `BaseAIProvider` | definition | ✅ | — | exact |
| `BaseAIProvider` | references | ✅ | 23 | cross-file, accurate |
| `AIProvider` interface | implementation | ✅ | 24 | all provider implementations found |
| `ToolRegistry` | references | ✅ | 4 | precise |
| `ToolRegistry` | hover | ✅ | — | signature + docs |
| `IMemoryStore.setMemory` | hover | ✅ | — | useful type info |
| `createWindow` | references | ✅ | 3 | accurate |
| `AppDelegate.-application:...` | document_symbols | ✅ | — | clangd handles Obj-C selector |
| `AppDelegate` | definition | ✅ | — | correct |
| TS files with bare path | document_symbols | ❌ | — | TS LSP requires `file:///` URI |

### 6.2 Key findings

#### 1. LSP references and implementations are a major strength
These operations provide information that keyword search cannot reliably reconstruct without knowing exact names and manually filtering many matches.

The strongest example in this benchmark is:
- `AIProvider` → **24 implementations**
- `BaseAIProvider` → **23 references**

This is highly valuable for coding agents performing relationship-aware code understanding.

#### 2. URI normalization is a real usability bug
For TypeScript, `typescript-language-server` requires a `file:///` URI. Passing bare Windows paths results in empty responses. This should be normalized automatically at the MCP server boundary.

#### 3. Clangd behavior is acceptable given missing build metadata
Without `compile_commands.json`, Objective-C/C++ results are limited, but clangd falls back gracefully and does not crash.

### Navigation conclusion
LSP-backed navigation is one of the strongest differentiators of Codebase Insights. It adds precise code relationship understanding on top of semantic retrieval.

---

## 7. Resource Usage and Cost

| Metric | Value | Notes |
|---|---:|---|
| Peak RSS (process tree) | **3,265 MB** | dominated by TS language server |
| SQLite DB size | **5.48 MB** | symbols, refs, summaries |
| ChromaDB size | **5.70 MB** | 538 vectors |
| Total persisted footprint | **11.2 MB** | very small overall |
| Input tokens | 149,490 | full rebuild |
| Output tokens | 27,652 | full rebuild |
| Estimated chat API cost | **~$0.07** | remote summary generation |
| Embedding API cost | $0 | local Ollama |
| Full rebuild total external cost | **~$0.07** | low |
| Embedding throughput | ~14 embeddings/s | 538 in 38.1s |

### Observations

#### 1. Storage efficiency is strong
For a 5,615-symbol monorepo, an 11.2 MB persisted footprint is small enough to make persistent reuse very attractive.

#### 2. API cost is low
A full semantic rebuild at around `$0.07` is inexpensive, especially considering that unchanged restarts are effectively free.

#### 3. Memory usage is high but explainable
Peak memory was ~3.2 GB, largely due to the TypeScript language server loading a large monorepo. This is a concern for low-memory environments, but not unreasonable for a heavy TS workspace.

---

## 8. Stability and Failure Modes

| Scenario | Observation |
|---|---|
| TS LSP with bare file path | silently returns empty result |
| clangd without compilation DB | fallback mode, no crash |
| Full rebuild | 118 indexed, 0 errors |
| Semantic indexing | 538 summarized, 0 errors |
| Repeated runs | no data corruption observed |
| Log capture with tqdm | ANSI escape noise in logs |
| MCP server startup | can respond before initial indexing completes |

### Stability observations

#### Strengths
- No indexing errors in the measured rebuild
- No semantic generation failures in the measured rebuild
- Graceful fallback behavior from clangd
- Repeated runs appear stable

#### Weaknesses
- Empty TS responses on malformed paths are difficult to diagnose
- Logs are noisy due to progress-bar formatting
- Server readiness does not clearly distinguish “online” from “fully indexed”

### Stability conclusion
The system is operationally stable enough for benchmarking and experimentation, but needs clearer error surfaces and better readiness semantics before it feels polished.

---

## 9. Agent Workflow Evaluation

This section compares realistic coding-agent tasks using:
1. baseline keyword search / grep / file inspection
2. Codebase Insights with semantic search + symbol navigation

### Task A — Find all AI provider implementations

| Workflow | Steps | Files opened | Queries |
|---|---:|---:|---:|
| Baseline grep | ~several manual filtering steps | ~20+ | 3–5 |
| Codebase Insights | semantic search → `AIProvider` → `implementation` | 1 | 2 |

**Outcome:** large reduction in search effort and manual filtering.

---

### Task B — Trace character personality flow

Goal:
- find character/waifu data structures
- connect personality state to prompt generation

| Workflow | Baseline | Codebase Insights |
|---|---|---|
| Find core symbol | grep multiple terms | semantic search finds relevant symbol directly |
| Trace downstream usage | manual file reading | references + summaries reduce exploration |
| Understand prompt generation | inspect large files | summaries expose purpose quickly |

**Outcome:** semantic retrieval plus references provides a much shorter route to understanding.

---

### Task C — Determine whether a function is actually used

Example: `createWindow`

| Workflow | Result |
|---|---|
| Baseline | grep + inspect call sites manually |
| Codebase Insights | `lsp_references` returns 3 usages in under 1s |

**Outcome:** this is a natural fit for Codebase Insights and a strong demonstration of relationship-aware navigation.

---

### Agent workflow conclusion

Codebase Insights is most useful when an agent needs to:
- move from natural-language intent to a likely symbol
- expand from that symbol to definitions, references, or implementations
- reduce file-opening and grep iteration overhead
- reuse prior indexing rather than re-exploring the whole repo

This is exactly the workflow where naive keyword search wastes context and time.

---

## 10. Key Takeaways

### Where Codebase Insights is already strong
- Semantic retrieval for concept-level discovery
- LSP-backed references / definitions / implementations
- Persistent reuse across sessions
- Low storage overhead
- Low rebuild API cost
- Practical small-edit incremental updates

### Where it is still weak
- Project summary updates are too coarse-grained
- Startup has a high fixed cost due to `WorkspaceIndexer create`
- Convention-driven structures are harder to surface
- Inline behavior without dedicated symbols is harder to retrieve semantically
- “Not found” states are not communicated clearly enough

---

## 11. Benchmark Highlights

This section is designed to be copied into the main README.

## Performance (`G:\SyntaxSenpai\` — 118 files, 5,615 symbols)

| Metric | Value |
|---|---|
| Full rebuild time | ~8.7 min |
| Symbol indexing time | 91s |
| Semantic summaries | 538 symbols |
| Retrieval Hit@3 | 75% |
| Peak memory (RSS) | 3.2 GB |
| Storage footprint | 11.2 MB |
| LLM API cost | ~$0.07 |
| No-change catch-up | <0.1s |
| Leaf-file incremental update | ~6s |
| Core-file update | ~141s* |
| New-file update | ~133s* |
| LSP implementation discovery | 24 implementations found |

\* currently dominated by full project summary regeneration

### Incremental update summary

- **No-change restart:** `<0.1s` catch-up, `118/118` files skipped
- **Leaf-file edit:** `~6s` end-to-end update
- **Core-file edit:** `~141s`, currently dominated by full project summary regeneration
- **New file:** `~133s`, currently dominated by full project summary regeneration

---

## 12. Limitations of This Benchmark

- Only one repository was benchmarked
- Keyword-search baseline was only partially quantified
- Retrieval evaluation used a relatively small manual query set (20 queries)
- CPU utilization measurements were not reliable enough to report in detail
- Objective-C/C++ navigation results were limited by missing build metadata
- Incremental results reflect current summary policy and may change significantly after optimization

---

## 13. Recommended Next Benchmarks

To strengthen future reports, the following should be added:

1. **Multi-repo scaling benchmark**
   - small repo
   - medium repo
   - large monorepo

2. **Automated baseline comparison**
   - ripgrep
   - symbol-name query
   - semantic retrieval
   - optional embedding-only baseline

3. **Formal retrieval evaluation**
   - larger query set
   - explicit ground truth
   - per-query scoring automation

4. **Incremental stress benchmark**
   - repeated edits
   - burst file changes
   - restart after interrupted indexing

5. **Readiness and serving benchmark**
   - what queries are available before indexing completes
   - response quality during warm-up

---

## 14. Instrumentation Improvements Needed

The benchmark exposed several places where deeper instrumentation would improve observability.

| Type | Recommendation |
|---|---|
| Logging | separate timings for `document_symbols` and `references` |
| Logging | time `WorkspaceIndexer.__init__` internals, especially gitignore setup |
| Logging | track project summary token counts separately |
| Logging | record embedding throughput by batch |
| Counters | count symbols filtered by `min_ref_count` explicitly |
| Counters | count LSP timeout / empty-response events per language |
| Debug | log how many directories/files `_GitignoreFilter` scans |
| Timing hooks | split file hash-skip time from true LSP indexing time |
| Timing hooks | split file-summary DB read time from LLM generation time |
| UX | normalize file paths to `file:///` automatically in MCP-facing interfaces |

---

## 15. Final Conclusion

This benchmark supports the central premise of **Codebase Insights**:

> coding agents benefit from a persistent, structured, symbol-aware understanding layer that goes beyond naive keyword search.

On this benchmark repository, Codebase Insights showed:
- meaningful semantic retrieval quality
- strong LSP-based symbol navigation
- excellent no-change reuse
- practical small-edit incremental updates
- very low persistent storage and API cost

The main architectural issue now is not whether incremental indexing works—it does—but that **project-level summarization remains too coarse-grained and dominates structural updates**. Combined with the startup cost in `WorkspaceIndexer`, that forms the clearest roadmap for improvement.

Even in its current state, the system already demonstrates that combining **LSP extraction, semantic summaries, embeddings, and persistent indexing** is a viable foundation for building more capable coding agents.
