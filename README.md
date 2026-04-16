# Codebase Insights

**Persistent code intelligence for MCP-compatible coding agents.**

Codebase Insights builds a reusable understanding of a repository from four sources at once:

- **LSP structure** for symbols, definitions, references, and implementations
- **SQLite** for a persistent workspace symbol/reference index
- **LLM summaries** for higher-level meaning
- **Vector search** for natural-language retrieval

It runs as a local MCP server, so agents can search by intent, navigate by structure, and reuse the same codebase understanding across sessions instead of re-exploring the repo every time.

## What it is for

Codebase Insights is designed for the gap between plain text search and full repository understanding.

Typical agent problems it helps with:

- "Find the code that sanitizes HTML even if I do not know the symbol name."
- "Show me the interface, then all implementations, then all references."
- "Which file owns this feature?"
- "Give me a project-level summary before I start editing."

The project combines:

- **precise structural navigation** from language servers
- **concept-level retrieval** from AI summaries + embeddings
- **persistent local storage** so unchanged code does not need to be rediscovered
- **incremental updates** driven by file hashes and filesystem watching

## Core capabilities

| Area | What it provides |
|---|---|
| Workspace indexing | Stores symbols, definition locations, and references in SQLite |
| Structural navigation | `hover`, `definition`, `declaration`, `implementation`, `references`, `document symbols` |
| Semantic symbol search | `semantic_search(query)` finds symbols by behavior or intent |
| Semantic file search | `search_files(query)` finds files by responsibility or architecture role |
| Stored summaries | Per-symbol, per-file, and project-level summaries |
| Incremental maintenance | Skips unchanged files, watches for edits, updates changed content only |
| MCP integration | Exposes the index and navigation tools over streamable HTTP |
| Flexible model setup | Chat and embedding providers can be configured independently |

Supported languages today: **Python**, **JavaScript/TypeScript**, **C++**, and **Rust** via standard LSP servers.

## Latest benchmark snapshot

The latest benchmark checked into this repository is **v1.0.1**, generated on **2026-04-16** against a real Electron + Vue + React Native monorepo (`G:\SyntaxSenpai`).

### Benchmark target

| Metric | Value |
|---|---:|
| Files processed | 89 |
| Symbols | 5,592 |
| Cross-references | 31,791 |

### Retrieval quality

| Benchmark | Result |
|---|---|
| Symbol search Hit@1 | **26/28 (92.9%)** |
| Symbol search Hit@3 | **27/28 (96.4%)** |
| Symbol search Hit@5 | **27/28 (96.4%)** |
| File search Hit@1 | **14/15 (93.3%)** |
| File search Hit@3 | **14/15 (93.3%)** |
| File search Hit@5 | **15/15 (100.0%)** |
| Keyword baseline found expected symbol | **13/28 (46.4%)** |

Key takeaways from the latest run:

- `semantic_search` missed rank 1 on two queries: `AIChatRuntime` was still returned by **Hit@3**, and `StreamChunk` was not found in the top 5.
- `search_files` missed rank 1 only once (`executor.ts`), and still returned the correct file by **Hit@5**.
- The keyword baseline failed badly on concept-heavy queries such as translation lookup, HTML sanitization, deferred mount logic, and runtime metrics.

This matters because Codebase Insights is trying to solve the exact case where the caller knows **what the code does**, but not **what the symbol is named**.

The latest checked-in benchmark report is [docs/benchmark-v1.0.1.md](docs/benchmark-v1.0.1.md). Historical benchmark reports are also kept under [docs/](docs).

### Performance baseline

For **full rebuild** and **incremental update** behavior, the v1.0.1 baseline is:

| Metric | Result |
|---|---|
| Full pipeline wall time | **499.79s (~8.3 min)** |
| Storage footprint | **15.09 MB** (6.37 MB SQLite + 8.72 MB ChromaDB) |
| No-change catch-up | **30.2s** |
| Leaf-file edit | **27.2s** |
| Core-file edit | **63.2s** (3.65s semantic + 7.0s file summary + 14.4s incremental project summary) |
| New file | **67.0s** (3.9s semantic + 3.9s file summary + 16.4s incremental project summary) |

The v1.0.1 project summary fix reduces per-update LLM output from ~14 KB to ~2 KB by removing the redundant per-file bullet list from the project summary prompt. Incremental project summary now takes **~15–20s** instead of ~136s. Core-file edit dropped from **~179s to 63s** and new-file addition from **~157s to 67s**, both now completing end-to-end in under 70 seconds.

## How it works

1. **Language detection and LSP startup**  
   The CLI scans the target repository, detects supported languages, validates the required language servers, and starts an LSP client per language.

2. **Workspace indexing**  
   The workspace indexer walks the repo, respects `.gitignore`, flattens `documentSymbol` results, records symbol definitions and references, hashes files, and stores everything in SQLite.

3. **Semantic indexing**  
   The semantic indexer reads eligible symbols from SQLite, extracts local source context, generates short natural-language summaries, and stores embeddings in ChromaDB.

4. **File and project summarization**  
   File summaries support module-level retrieval. A project summary gives agents a higher-level map of the codebase before they start drilling down.

5. **Incremental updates**  
   Unchanged files are skipped by file hash. On re-indexing an edited file, existing symbol summaries are **carried over** by `(name, kind, container)` key so that only genuinely changed symbols are re-summarized. A watchdog observer keeps the index warm while the server is running.

6. **MCP serving**  
   All of that state is exposed through an MCP server on `http://127.0.0.1:6789/mcp` using streamable HTTP transport.

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

If the target repository already has a `.gitignore`, Codebase Insights automatically adds **`.codebase-index.db`** to it. The other generated artifacts should also be ignored in normal usage.

## Requirements

- Python **3.11+**
- At least one supported LSP server for the target repository
- Either:
  - **Ollama**, or
  - an **OpenAI-compatible chat provider and embedding provider**

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

For C/C++, indexing quality depends on `clangd` having enough project context. In practice that usually means a valid `compile_commands.json` or `compile_flags.txt`.

## Installation

### From PyPI

```bash
pip install codebase-insights
```

### From source

```bash
# from the repository root
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

Chat and embedding providers are configured independently, so mixed setups like **OpenAI for chat + Ollama for embeddings** are supported.

### Default shape

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
```

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
# set OPENAI_API_KEY in your environment first
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
| `get_symbol_summary(name, file_uri, line, character)` | Fetch the stored AI summary for a specific symbol |
| `get_indexer_criteria()` | Return the current semantic indexing thresholds and eligible kinds |

### Semantic retrieval

| Tool | Purpose |
|---|---|
| `semantic_search(query, limit, kinds)` | Find symbols by natural-language intent |
| `search_files(query, limit)` | Find files by natural-language responsibility |
| `get_file_summary(file_path)` | Fetch the stored summary for one file |
| `get_project_summary()` | Fetch the stored project-level overview |

For LSP calls, the server accepts normal `file:///...` URIs and also normal absolute filesystem paths.

## A practical agent workflow

One effective flow is:

1. Call `get_project_summary()` to understand the repo shape.
2. Use `search_files()` to find the likely module.
3. Use `semantic_search()` to find the likely symbol.
4. Use `query_symbols()` when you know part of the name or want exact paths/kinds.
5. Use `lsp_definition()`, `lsp_implementation()`, and `lsp_references()` to expand outward structurally.
6. Use `get_symbol_summary()` when you want a compact natural-language explanation of a specific symbol.

That gives agents both **semantic recall** and **structural precision**.

## Limitations and trade-offs

- **Not every symbol gets an AI summary.**  
  By default, semantic indexing only covers `Class`, `Method`, `Function`, `Interface`, `Enum`, and `Constructor`, and only when a symbol meets the `min_ref_count` threshold.

- **Named declarations work better than anonymous logic.**  
  The index intentionally filters or demotes anonymous LSP artifacts, callbacks, and low-signal pseudo-symbols.

- **LSP quality sets the floor.**  
  If the language server cannot fully understand the workspace, indexing and navigation quality drop with it.

- **Semantic search is strongest for intent, not exhaustive coverage.**  
  It is best used to find likely starting points, then combined with structural tools for complete navigation.

- **The benchmark measures retrieval quality and incremental update performance.**  
  Retrieval quality (Hit@k) is the primary benchmark; timing numbers reflect one run against a specific LLM/embedding stack and will vary with provider and hardware.

## Related benchmark material

- Latest benchmark report: [docs/benchmark-v1.0.1.md](docs/benchmark-v1.0.1.md)
- Earlier reports: [docs/](docs)
- Benchmark runner: [scripts/run_benchmark.py](scripts/run_benchmark.py)

## License

MIT. See [LICENSE](LICENSE).
