# Codebase Insights

**Persistent code intelligence for MCP-compatible coding agents.**

Codebase Insights builds a reusable understanding of a repository from four layers at once:

- **LSP structure** for definitions, references, implementations, hover, and symbols
- **SQLite indexing** for persistent workspace-wide symbol and reference lookup
- **LLM summaries** for symbols, files, and project-level meaning
- **Vector retrieval** for natural-language search by intent instead of exact names

It runs as a local MCP server, so an agent can stop re-discovering the same codebase every session and start from a warm, queryable model of the repo.

## Why this exists

Plain text search is fine when you already know the symbol name.

Agents usually do not.

More often, the prompt looks like:

- "Where is the code that strips HTML?"
- "Which file owns the IPC bridge?"
- "What handles translation lookup?"
- "Show me the interface, then the implementations, then the references."

Without a persistent index, the usual fallback is expensive and noisy:

1. guess several keywords
2. run repeated text or symbol-name searches
3. dump many irrelevant matches into context
4. ask the model to infer which result matters

Codebase Insights replaces that loop with stored structure plus stored meaning:

- **`search_files()`** finds the right module by responsibility
- **`semantic_search()`** finds the right symbol by behavior
- **`query_symbols()`** narrows or confirms exact names from SQLite
- **LSP tools** expand from that starting point with precise navigation

That means fewer blind search attempts, less irrelevant context pasted into prompts, and less token waste from "keyword spray and inspect" workflows.

## Why it helps agents spend fewer tokens

Codebase Insights does not just make search smarter; it makes agent context assembly smaller.

Instead of asking an agent to invent keywords, dump large result sets, and reason over them, it returns **ranked summaries of likely matches**. In practice that reduces token use in three ways:

| Waste in a keyword-only workflow | What Codebase Insights changes |
|---|---|
| Multiple query reformulations | Semantic retrieval accepts the original intent directly |
| Large irrelevant result dumps | Ranked symbols/files come back with short stored summaries |
| Repeating the same repo exploration every session | SQLite + ChromaDB persist the understanding locally |

This is exactly where the benchmark shows the biggest advantage: concept-heavy queries where the caller knows **what the code does**, but not **what it is called**.

## Benchmark snapshot (v1.1.0)

Latest checked-in report: [docs/benchmark-v1.1.0.md](docs/benchmark-v1.1.0.md)

Benchmark target: real Electron + Vue + React Native monorepo `G:\SyntaxSenpai`

| Metric | Result |
|---|---|
| Files processed | **89** |
| Symbols | **5,593** |
| Cross-references | **31,792** |
| Symbol search Hit@1 | **26/28 (92.9%)** |
| Symbol search Hit@3 | **27/28 (96.4%)** |
| Symbol search Hit@5 | **27/28 (96.4%)** |
| File search Hit@1 | **14/15 (93.3%)** |
| File search Hit@5 | **15/15 (100.0%)** |
| Keyword baseline found expected symbol | **13/28 (46.4%)** |
| Full pipeline wall time | **499.79s (~8.3 min)** |
| Storage footprint | **15.09 MB** |
| No-change catch-up | **38.02s** |
| Leaf-file edit | **13.0s** |
| Core-file edit | **63.02s** |

### What those numbers mean

- **Semantic symbol retrieval beat the keyword baseline by a wide margin**: 92.9% Hit@1 vs 46.4% "found at all".
- **File retrieval is strong enough to act as an agent's first routing step**: 14/15 top-1, 15/15 by top-5.
- **The index is persistent and cheap to keep warm**: after the initial build, unchanged restarts catch up in ~38s and leaf edits in ~13s.
- **The local footprint stays small**: about 15 MB for SQLite + ChromaDB on the benchmark repo.

## Showcase: benchmark-backed examples

These are not hand-picked demos outside the benchmark; they come from the scored v1.1.0 retrieval set.

| Natural-language request | Expected result | Semantic search | Keyword baseline |
|---|---|---|---|
| "sanitize markup by removing tags and decoding HTML entities" | `stripHtml` | **Hit@1** | **0 keyword results** |
| "look up localized text strings by translation key" | `t` | **Hit@1** | **0 keyword results** |
| "delay component mount until after the browser finishes initial paint" | `useDeferredMount` | **Hit@1** | **0 keyword results** |
| "collect and expose runtime performance counters and histograms" | `RuntimeMetrics` | **Hit@1** | **0 keyword results** |
| "orchestrate multi-turn conversation flow with tool execution" | `AIChatRuntime` | **Hit@3** | not found |

That is the core value proposition in one table: when names are non-obvious, abbreviated, or too generic for text search, Codebase Insights still gives the agent a high-quality starting point.

## Core capabilities

| Area | What it provides |
|---|---|
| Workspace indexing | Persistent SQLite index of symbols, definitions, and references |
| Structural navigation | `hover`, `definition`, `declaration`, `implementation`, `references`, `document symbols` |
| Semantic symbol search | `semantic_search(query)` finds symbols by behavior or intent |
| Semantic file search | `search_files(query)` finds files by responsibility or architecture role |
| Stored summaries | Per-symbol, per-file, and project-level summaries |
| Incremental maintenance | Skips unchanged files and updates only changed content |
| Deferred summary refresh | Regenerates file/project summaries lazily to avoid paying LLM cost on every edit |
| MCP integration | Exposes the whole index and navigation surface over streamable HTTP |
| Flexible model setup | Chat and embedding providers can be configured independently |

Supported languages today: **Python**, **JavaScript/TypeScript**, **C++**, and **Rust** via standard LSP servers.

## How it works

1. **Detect languages and start LSP clients**  
   The CLI scans the target repository, detects supported languages, validates required language servers, and starts one client per language.

2. **Build the workspace index**  
   The workspace indexer walks the repo, respects `.gitignore`, flattens `documentSymbol` output, records definitions and references, hashes files, and stores the result in SQLite.

3. **Generate semantic summaries**  
   The semantic indexer summarizes eligible symbols, stores embeddings in ChromaDB, and writes summaries back to SQLite.

4. **Summarize files and the whole project**  
   File summaries support module-level search; the project summary gives an agent a high-level architectural map before it drills down.

5. **Keep the index warm incrementally**  
   Unchanged files are skipped by hash. Existing summaries are carried forward when possible. Watchdog-based file watching keeps the model fresh while the server runs.

6. **Serve everything over MCP**  
   The tool surface is exposed on `http://127.0.0.1:6789/mcp` using streamable HTTP transport.

## Requirements

- Python **3.11+**
- At least one supported LSP server for the target repository
- Either:
  - **Ollama**, or
  - an **OpenAI-compatible** chat provider and embedding provider

### Supported LSP servers

| Language | Server | Install |
|---|---|---|
| Python | `pylsp` | `pip install python-lsp-server` |
| JavaScript / TypeScript | `typescript-language-server` | `npm install -g typescript-language-server` |
| C++ | `clangd` | <https://clangd.llvm.org/installation.html> |
| Rust | `rust-analyzer` | `rustup component add rust-analyzer` |

Optional Python plugins:

- `python-lsp-ruff`
- `python-lsp-black`
- `pylsp-mypy`

For C/C++, indexing quality depends on `clangd` having enough project context, usually via `compile_commands.json` or `compile_flags.txt`.

## Installation

### From PyPI

```bash
pip install codebase-insights
```

### From source

```bash
pip install -e .
```

## Configuration

On first run, Codebase Insights creates `.codebase-insights.toml` through an interactive setup wizard.

The wizard asks for:

- chat provider (`ollama` or `openai`)
- embedding provider (`ollama` or `openai`)
- model names and base URLs
- semantic indexing kinds
- concurrency, batch size, and minimum reference count

Chat and embedding providers are configured independently, so setups like **OpenAI for chat + Ollama for embeddings** are supported.

### Default configuration shape

```toml
[chat]
provider = "ollama"

[chat.ollama]
base_url = "http://localhost:11434"
model = "qwen2.5"

[embed]
provider = "ollama"

[embed.ollama]
base_url = "http://localhost:11434"
model = "bge-m3"

[semantic]
index_kinds = ["Class", "Method", "Function", "Interface", "Enum", "Constructor"]
concurrency = 16
batch_size = 16
min_ref_count = 3
summary_update_threshold = 5
summary_file_idle_timeout = 30
summary_project_idle_timeout = 300
```

### Deferred summary refresh controls

| Setting | Default | Meaning |
|---|---|---|
| `summary_update_threshold` | `5` | Regenerate once this many files have stale summaries |
| `summary_file_idle_timeout` | `30` | Refresh one file summary after that file has been idle |
| `summary_project_idle_timeout` | `300` | Refresh all stale summaries plus the project summary after the whole repo has been idle |

Set any of these to `0` to disable that trigger. The MCP `refresh_file_summary()` and `refresh_project_summary()` tools always force regeneration immediately.

### API key precedence

When using an OpenAI-compatible provider, runtime environment variables take precedence over keys stored in the config file:

- `CODEBASE_INSIGHTS_CHAT_API_KEY`
- `CODEBASE_INSIGHTS_EMBED_API_KEY`
- `OPENAI_API_KEY`

## Running

```bash
codebase-insights <project_root> [options]
```

### Quick start with Ollama

```bash
# Terminal 1
ollama serve

# Terminal 2
codebase-insights /path/to/project
```

### Quick start with an OpenAI-compatible provider

```bash
# set OPENAI_API_KEY first
codebase-insights /path/to/project --new-config
```

Then choose `openai` for chat and/or embeddings in the setup wizard.

### CLI flags

| Flag | What it does |
|---|---|
| `--new-config` | Re-run the interactive setup wizard |
| `--rebuild-index` | Clear and rebuild the SQLite symbol index |
| `--rebuild-semantic` | Clear summaries and vector data, then regenerate everything |
| `--rebuild-summaries` | Rebuild file/project summaries only |
| `--rebuild-vectors` | Re-embed existing summaries without new LLM summarization |

Normal usage is incremental. The rebuild flags are mainly for maintenance, model changes, or benchmarking.

## MCP tools

### Workspace and LSP

| Tool | Purpose |
|---|---|
| `languages_in_codebase()` | Return detected languages |
| `lsp_capabilities()` | Return active LSP capability information |
| `lsp_hover(file_uri, line, character)` | Hover docs/type at a position |
| `lsp_definition(file_uri, line, character)` | Jump to definition |
| `lsp_declaration(file_uri, line, character)` | Find declaration |
| `lsp_implementation(file_uri, line, character)` | Find implementations |
| `lsp_references(file_uri, line, character)` | Find references |
| `lsp_document_symbols(file_uri)` | List symbols in a file |

### Indexed symbol queries

| Tool | Purpose |
|---|---|
| `query_symbols(path, kinds, name_query, limit)` | Search the SQLite symbol index by path, kind, or fuzzy name |
| `get_symbol_summary(name, file_uri, line, character)` | Fetch the stored AI summary for a symbol |
| `get_indexer_criteria()` | Return the current semantic indexing thresholds and eligible kinds |

### Semantic retrieval

| Tool | Purpose |
|---|---|
| `semantic_search(query, limit, kinds)` | Find symbols by natural-language intent |
| `search_files(query, limit)` | Find files by natural-language responsibility |
| `get_file_summary(file_path)` | Fetch the stored summary for one file |
| `get_project_summary()` | Fetch the stored project-level overview |
| `refresh_file_summary(file_path)` | Force-regenerate one file summary immediately |
| `refresh_project_summary()` | Force-regenerate all stale file summaries and the project summary |

For LSP calls, the server accepts normal `file:///...` URIs and absolute filesystem paths.

When `get_file_summary()` or `get_project_summary()` returns `is_stale: true`, the last known summary is still available, but you can force an immediate refresh with the corresponding `refresh_*` tool.

## A practical agent workflow

One effective workflow is:

1. call `get_project_summary()` to orient on architecture
2. call `search_files()` to find the likely module
3. call `semantic_search()` to find the likely symbol by behavior
4. call `query_symbols()` when you know part of the name or want exact path/kind filtering
5. call `lsp_definition()`, `lsp_implementation()`, and `lsp_references()` for precise structural expansion

That gives an agent both **semantic recall** and **structural precision**.

## Repository layout

```text
src/codebase_insights/
├── main.py              CLI entry point and startup orchestration
├── LSP.py               LSP client wrapper
├── language_analysis.py Language detection and .gitignore parsing
├── workspace_indexer.py SQLite symbol/reference index + file watching
├── semantic_config.py   Config loading and first-run setup wizard
├── semantic_indexer.py  LLM summaries, embeddings, ranking, file/project summaries
└── mcp_server.py        MCP tool surface

scripts/
└── run_benchmark.py     Benchmark orchestrator

docs/
└── benchmark-v*.md      Versioned benchmark reports
```

## Files created in the indexed repository

| Path | Purpose |
|---|---|
| `.codebase-index.db` | Persistent SQLite symbol/reference database |
| `.codebase-semantic/` | ChromaDB collections for symbol and file summaries |
| `.codebase-insights.toml` | Local configuration for model providers and semantic indexing |

If the target repository already has a `.gitignore`, Codebase Insights automatically adds **`.codebase-index.db`** to it. The other generated artifacts should also be ignored in normal use.

## Limitations and trade-offs

- **Not every symbol gets an AI summary.** By default, semantic indexing covers `Class`, `Method`, `Function`, `Interface`, `Enum`, and `Constructor`, and only when a symbol meets the `min_ref_count` threshold.
- **Named declarations work better than anonymous logic.** Anonymous callbacks and low-signal pseudo-symbols are intentionally filtered or demoted.
- **LSP quality sets the floor.** If the language server cannot understand the workspace, indexing and navigation quality drop with it.
- **Semantic search is a routing tool, not a proof of completeness.** It is strongest for finding a likely starting point, then handing off to structural tools for exact navigation.
- **Timing numbers are environment-specific.** Retrieval quality is the key benchmark; wall times depend on provider choice, hardware, and codebase shape.

## Benchmark material

- Latest report: [docs/benchmark-v1.1.0.md](docs/benchmark-v1.1.0.md)
- Historical reports: [docs/](docs)
- Benchmark runner: [scripts/run_benchmark.py](scripts/run_benchmark.py)

## License

MIT. See [LICENSE](LICENSE).
