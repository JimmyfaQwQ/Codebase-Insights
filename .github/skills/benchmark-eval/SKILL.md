---
name: benchmark-eval
description: 'Benchmark and evaluate codebase-insights against a real project. Use when: measuring indexing performance, testing retrieval quality, testing incremental update speed, running LSP navigation tests, identifying bugs during evaluation, writing a benchmark report. Covers instrumentation, full-rebuild timing, incremental update scenarios (no-change / leaf-edit / core-edit / new-file), semantic search quality (Hit@1/3/5), and LSP feature coverage.'
argument-hint: 'Target repo path, e.g. "G:\\MyProject\\"'
---

# Benchmark & Evaluation — codebase-insights

## When to Use
- Evaluating a new version of codebase-insights against a real codebase
- Profiling indexing/semantic performance on a new target repo
- Verifying incremental update correctness after refactors
- Measuring retrieval quality (semantic search vs keyword baseline)
- Testing LSP navigation features end-to-end
- Writing a versioned benchmark report

---

## Automated Pipeline — scripts/run_benchmark.py

Most benchmark phases are fully automated. Run this first.

```powershell
# Standard run (Phases 0, 1, 3, 5, 7)
python scripts/run_benchmark.py "G:\MyProject\"

# Include full rebuild (Phase 2)
python scripts/run_benchmark.py "G:\MyProject\" --full-rebuild

# Include retrieval quality (Phase 4)
python scripts/run_benchmark.py "G:\MyProject\" --queries-file queries.json

# Override auto-selected files for incremental scenarios
python scripts/run_benchmark.py "G:\MyProject\" --leaf-file src/utils.ts --core-file src/base.ts

# Re-generate report only from saved state
python scripts/run_benchmark.py "G:\MyProject\" --phases 7

# Full run
python scripts/run_benchmark.py "G:\MyProject\" --full-rebuild --queries-file queries.json --phases 0,1,2,3,4,5,7
```

### How to invoke — critical

Always invoke with run_in_terminal using mode=sync and timeout=0:

- mode=sync: wait until process exits
- timeout=0: no timeout for long runs

Do not use mode=async for this script.

### What the script automates

| Phase | Automated action |
|---|---|
| 0 — Pre-flight | Kill port 6789, verify config, capture environment + DB stats |
| 1 — Markers | Check BENCHMARK markers and flush usage |
| 2 — Full rebuild | Run monitor wrapper and collect full rebuild artifacts |
| 3 — Incremental A-D | Run all scenarios, collect timings, revert test edits |
| 4 — Retrieval | Run symbol semantic_search + keyword baseline and file search_files; auto compute hit@1/3/5 from score-sorted results |
| 5 — LSP matrix | Run LSP capability and navigation test set |
| 7 — Report | Generate docs/benchmark-v<VER>.md and benchmark_results/benchmark_state.json |

### What remains manual

- Phase 6 bug triage write-up: summarize symptoms, root causes, and fixes in report findings.

### Queries file format (Phase 4)

```json
[
  {"type": "symbol", "query": "AI provider initialization", "expected": "ProviderBase"},
  {"type": "symbol", "query": "error handling when AI call fails", "expected": "handleError"},
  {"type": "file", "query": "chat state persistence", "expected": "chat-store.ts"}
]
```

Type defaults to symbol when omitted.

---

## Phase 0 — Pre-flight Checks

Automated by scripts/run_benchmark.py.

Key checks:
- Port 6789 must be free before starting server
- Target repo must contain .codebase-insights.toml
- Capture environment metadata for report

## Phase 1 — Verify Instrumentation

Automated by scripts/run_benchmark.py.

Expected markers:

| File | Markers |
|---|---|
| main.py | [BENCHMARK:STARTUP] |
| workspace_indexer.py | [BENCHMARK:INDEXER] |
| semantic_indexer.py | [BENCHMARK:SEMANTIC], [BENCHMARK:FILE_SUMMARIES], [BENCHMARK:PROJECT_SUMMARY], [BENCHMARK:SIZES] |

---

## Phase 2 — Full Rebuild Execution

Automated by scripts/run_benchmark.py when --full-rebuild is set.

Internal helper scripts/benchmark_monitor.py is invoked by the orchestrator.

### Key metrics to extract
| Benchmark line | Key metrics |
|---|---|
| `STARTUP` | `indexer_create` (watch for anomaly), `total_pre_server` |
| `INDEXER` | `wall_time`, `files_total`, `total_symbols`, `total_refs` |
| `SEMANTIC` | `wall_time`, `llm_batch`, `chroma_insert`, `summarised`, `input_tokens`, `output_tokens` |
| `FILE_SUMMARIES` | `wall_time`, `files_pending` |
| `PROJECT_SUMMARY` | `wall_time` |
| `SIZES` | `sqlite_mb`, `chroma_mb` |

---

## Phase 3 — Incremental Update Scenarios

Automated by scripts/run_benchmark.py.

The runner handles server start/stop, marker waiting, scenario edits, and cleanup.

Run four scenarios, recording `[BENCHMARK:*]` lines between each:

### Scenario A — No-change restart
- Expected: `INDEXER` shows `indexed=0 skipped=<total>`, `SEMANTIC` shows `summarised=0 skipped=<total>`
- Key metric: total catch-up time should be **<1s**

### Scenario B — Leaf file small edit
Pick a provider/utility file with few symbols (e.g. a single-provider file).
Edit: add a function call at the top level of the file (name unchanged).
- Expected: watchdog triggers ~1s, `SEMANTIC` processes only that file's symbols (~5s for 3–5 syms)
- FILE_SUMMARIES / PROJECT_SUMMARY should **not** trigger if only top level content changes (no definition / symbol change)

### Scenario C — Core file edit (signature change)
Edit a widely-imported interface file (e.g. add an optional field to a core interface).
- Expected: watchdog + LSP re-index ~1s, `SEMANTIC` processes all symbols in that file, FILE_SUMMARIES + PROJECT_SUMMARY both trigger
- PROJECT_SUMMARY is the bottleneck (~100–130s for large repos)

### Scenario D — New file added
Create a new `.ts` file with a class and a few functions.
- Expected: same as Scenario C pipeline, but includes fresh LSP document-symbols pass
- Key metric: end-to-end from file creation → PROJECT_SUMMARY complete

---

## Phase 4 — Retrieval Quality Testing

Automated by scripts/run_benchmark.py when --queries-file is provided.

Supported query types:
- type=symbol: semantic_search plus keyword baseline query_symbols
- type=file: search_files

Returned result lists are sorted by score descending before scoring.
hit@1, hit@3, and hit@5 are auto-computed and saved in benchmark_state.json.

### Query design
Run ≥15 queries covering:
- Domain-specific concepts (`AI provider initialization`, `LLM streaming response`)
- Structural patterns (`event emitter`, `plugin extension point`)
- Behavioural features (`error handling when AI call fails`, `conversation history memory`)
- Negative space (features that don't exist as explicit symbols)

### For each query
```
mcp_codebase-insi_semantic_search(query="...", limit=5)
mcp_codebase-insi_search_files(query="...", limit=5)
```
Record: top result name + score, whether it's a genuine hit.

### Keyword baseline
For selected queries, also run:
```
mcp_codebase-insi_query_symbols(name_query="keyword", kinds=["Class","Interface","Function"], limit=5)
```
Compare: semantic vs keyword — do they find the same thing? Different things?

### Scoring
| Metric | Definition |
|---|---|
| Hit@1 | Top-1 result is correct |
| Hit@3 | Any of top-3 is correct |
| Hit@5 | Any of top-5 is correct |

### Known failure modes
- **Convention-based routing** (e.g. Expo Router, Next.js pages): no explicit symbol, can't be indexed
- **Inline logic** (`try/catch` blocks, anonymous functions): filtered by `_ANON_NAME_RE`
- **Missing abstractions**: if the codebase doesn't have an explicit class for a pattern, semantic search can't find it

---

## Phase 5 — LSP Navigation Testing

Automated by scripts/run_benchmark.py.

URI guidance remains relevant for manual debugging: prefer file:///G:/... format.

### Test matrix

| Tool | Test | What to verify |
|---|---|---|
| `lsp_capabilities` | Call with no args | Both JS/TS and C++ clients initialized |
| `lsp_document_symbols` | Pick a mid-size class file | Returns nested symbol tree |
| `lsp_definition` | Hover on an interface/class definition line | Returns correct location |
| `lsp_references` | Find refs to a base class | Returns refs across multiple files |
| `lsp_implementation` | Find impls of an interface | Returns all implementing classes |
| `lsp_hover` | Hover on a class name | Returns type signature + JSDoc |
| `lsp_document_symbols` | C++ / Obj-C file | clangd returns correct ObjC selectors |

### File URI format
```
file:///G:/TargetRepo/packages/ai-core/src/providers/base.ts
```

### Red flags
- Any tool returning `{"result": []}` — likely a URI format problem
- References timeout on widely-used symbols (e.g. base class used everywhere) — expected, document as known behaviour
- C++ hover failing — check that `clangd` is finding the compilation database

---

## Phase 6 — Bug Triage

Document any bugs found with:
- **Symptom**: what was observed
- **Root cause**: where in the code
- **Fix**: the actual code change + file
- **Severity**: High / Medium / Low

Common bugs to watch for:
- stdout buffering (BENCHMARK lines missing from piped log) → `flush=True`
- URI normalization (`file:///` vs bare path) → `_to_file_uri()` in `mcp_server.py`
- WorkspaceIndexer startup slowness → `_GitignoreFilter` doing full `os.walk`
- Windows subprocess PATH issue → hardcode `.venv/Scripts/` path

---

## Phase 7 — Report Compilation

Automated by scripts/run_benchmark.py (phase 7).

Output files:
- docs/benchmark-vX.Y.Z.md
- benchmark_results/benchmark_state.json

### Required sections

1. **Environment** — versions table
2. **Full Rebuild** — startup, indexer, semantic, file summaries, project summary, storage, resources
3. **Incremental Updates** — summary timing matrix + per-scenario breakdown
4. **Retrieval Quality** — Hit@1/3/5 table, per-query results, failure analysis
5. **LSP Navigation** — test matrix results
6. **Bug Fixes** — table of bugs found + fixes applied
7. **Key Findings & Recommendations** — prioritised table

### Summary timing matrix (required)

| Scenario | Watchdog+LSP | Semantic | File Summary | Proj Summary | Total |
|---|---|---|---|---|---|
| A: No change | | | | | |
| B: Leaf file | | | | | |
| C: Core file | | | | | |
| D: New file | | | | | |
| Full rebuild | | | | | |

### Cleanup after report is finalised

Once the report is written and committed, remove the raw benchmark artifacts:

```powershell
Remove-Item -Recurse -Force benchmark_results\
```

`benchmark_results/` should not be committed — it contains large log files and binary JSON. The report markdown is the permanent record.

---

## Completion Checklist

- [ ] Port 6789 confirmed free before every server start
- [ ] `.codebase-insights.toml` confirmed present in target repo
- [ ] All `[BENCHMARK:*]` lines flush to log in real time
- [ ] `benchmark_results/full_rebuild.json` written
- [ ] Scenarios A–D all have BENCHMARK data captured
- [ ] ≥15 retrieval queries run and auto-scored
- [ ] LSP test matrix fully exercised (≥6 tool types)
- [ ] Bugs found are documented with root cause + fix
- [ ] Report written with all required sections
- [ ] Test edits reverted in target repo
- [ ] `benchmark_results/` deleted after report is committed
