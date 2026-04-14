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

## Overview of Phases

```
Phase 0 — Pre-flight checks (kill existing server, verify config, capture env)
Phase 1 — Verify [BENCHMARK:*] markers present in source
Phase 2 — Full rebuild execution + analysis
Phase 3 — Incremental update scenarios (A–D)
Phase 4 — Retrieval quality testing (semantic search)
Phase 5 — LSP navigation testing
Phase 6 — Bug triage (fix issues found during eval)
Phase 7 — Report compilation
```

---

## Phase 0 — Pre-flight Checks

> **These two checks must pass before any server is started. Abort if either fails.**

### 1. Kill existing server instance

Port 6789 must be free. Always check and kill any existing process before starting:

```powershell
$p = Get-NetTCPConnection -LocalPort 6789 -ErrorAction SilentlyContinue
if ($p) {
    Stop-Process -Id $p.OwningProcess -Force
    Write-Output "Killed PID $($p.OwningProcess) on port 6789"
} else {
    Write-Output "Port 6789 is free"
}
```

Do this before **every** server start — both the full rebuild run and the incremental scenarios.

### 2. Verify target repo has a config file

codebase-insights requires a `.codebase-insights.toml` in the target directory (LLM + embedding config). If it doesn't exist, the server will either fail or run without AI features — **abort and set up the config first**:

```powershell
if (-not (Test-Path "G:\TargetRepo\.codebase-insights.toml")) {
    Write-Error "ABORT: No .codebase-insights.toml found in target repo. Run the server manually once with --new-config to create it."
    return
}
Write-Output "Config found: OK"
```

### 3. Environment capture

After both checks pass, record:
- codebase-insights version (`pip show codebase-insights`)
- LLM provider + model (from `.codebase-insights.toml`)
- Embedding model + provider
- LSP servers installed (`typescript-language-server --version`, `clangd --version`)
- Target repo: language breakdown, approx file count

---

## Phase 1 — Verify Instrumentation

Confirm `[BENCHMARK:*]` markers are present in the source before running. Grep for the markers:

```powershell
Select-String -Path "src/codebase_insights/*.py" -Pattern "\[BENCHMARK:"
```

Expected markers:

| File | Markers |
|---|---|
| `main.py` | `[BENCHMARK:STARTUP]` |
| `workspace_indexer.py` | `[BENCHMARK:INDEXER]` |
| `semantic_indexer.py` | `[BENCHMARK:SEMANTIC]`, `[BENCHMARK:FILE_SUMMARIES]`, `[BENCHMARK:PROJECT_SUMMARY]`, `[BENCHMARK:SIZES]` |

Also verify all BENCHMARK `print()` calls use `flush=True` — without it Python buffers stdout in piped mode and lines won't appear in the log until process exit:

```powershell
Select-String -Path "src/codebase_insights/*.py" -Pattern 'BENCHMARK.*flush=True'
```

If any markers are missing or lack `flush=True`, add them before proceeding.

---

## Phase 2 — Full Rebuild Execution

### `scripts/benchmark_monitor.py`
Verify the script exists before running:

```powershell
Test-Path scripts/benchmark_monitor.py
```

If missing, it needs to be created. See the existing script for reference — it is a psutil-based wrapper that resolves `.venv/Scripts/codebase-insights.exe`, polls RSS every 0.5s, tees stdout, and writes `benchmark_results/full_rebuild.json` + `.log`.

### Running it
```powershell
python scripts/benchmark_monitor.py "G:\TargetRepo\" --rebuild-index --rebuild-semantic
```

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

**Before starting the server**: run the Phase 0 port check again (the full rebuild server must be stopped).

Start fresh server **without** rebuild flags:
```powershell
# Step 1: ensure port is free
$p = Get-NetTCPConnection -LocalPort 6789 -EA SilentlyContinue
if ($p) { Stop-Process -Id $p.OwningProcess -Force }

# Step 2: start server (reuses existing index)
.venv\Scripts\codebase-insights.exe "G:\TargetRepo\" 2>&1 | Tee-Object -FilePath "benchmark_results\server_incremental.log"
```

Run four scenarios, recording `[BENCHMARK:*]` lines between each:

### Scenario A — No-change restart
Just start the server and wait for initial pass to finish.
- Expected: `INDEXER` shows `indexed=0 skipped=<total>`, `SEMANTIC` shows `summarised=0 skipped=<total>`
- Key metric: total catch-up time should be **<1s**

### Scenario B — Leaf file small edit
Pick a provider/utility file with few symbols (e.g. a single-provider file).
Edit: change a constant value or add a comment inside a method body (name unchanged).
- Expected: watchdog triggers ~1s, `SEMANTIC` processes only that file's symbols (~5s for 3–5 syms)
- FILE_SUMMARIES / PROJECT_SUMMARY should **not** trigger if only content changes (no structural symbol change)

### Scenario C — Core file edit (signature change)
Edit a widely-imported interface file (e.g. add an optional field to a core interface).
- Expected: watchdog + LSP re-index ~1s, `SEMANTIC` processes all symbols in that file, FILE_SUMMARIES + PROJECT_SUMMARY both trigger
- PROJECT_SUMMARY is the bottleneck (~100–130s for large repos)

### Scenario D — New file added
Create a new `.ts` file with a class and a few functions.
- Expected: same as Scenario C pipeline, but includes fresh LSP document-symbols pass
- Key metric: end-to-end from file creation → PROJECT_SUMMARY complete

### After each scenario
```powershell
Select-String -Path "benchmark_results\server_incremental.log" -Pattern "\[BENCHMARK:" | Select-Object -Last 10
```

> **Cleanup**: Revert/delete any test edits before moving to next scenario to prevent cross-contamination of project summary regeneration.

---

## Phase 4 — Retrieval Quality Testing

The MCP server is already running — use `mcp_codebase-insi_*` tools directly in chat.

### Query design
Run ≥15 queries covering:
- Domain-specific concepts (`AI provider initialization`, `LLM streaming response`)
- Structural patterns (`event emitter`, `plugin extension point`)
- Behavioural features (`error handling when AI call fails`, `conversation history memory`)
- Negative space (features that don't exist as explicit symbols)

### For each query
```
mcp_codebase-insi_semantic_search(query="...", limit=5)
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

A result is "correct" if it's the most directly relevant symbol for the query intent.

### Known failure modes
- **Convention-based routing** (e.g. Expo Router, Next.js pages): no explicit symbol, can't be indexed
- **Inline logic** (`try/catch` blocks, anonymous functions): filtered by `_ANON_NAME_RE`
- **Missing abstractions**: if the codebase doesn't have an explicit class for a pattern, semantic search can't find it

---

## Phase 5 — LSP Navigation Testing

> **URI requirement**: TypeScript-language-server requires `file:///G:/...` format. Bare Windows paths (`G:\...`) return empty results silently. After the URI normalization fix in `mcp_server.py`, both formats work.

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

Structure the report as `benchmark-v<X.Y.Z>.md` in the repo root.

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
- [ ] ≥15 semantic queries run and scored
- [ ] LSP test matrix fully exercised (≥6 tool types)
- [ ] Bugs found are documented with root cause + fix
- [ ] Report written with all required sections
- [ ] Test edits reverted in target repo
- [ ] `benchmark_results/` deleted after report is committed
