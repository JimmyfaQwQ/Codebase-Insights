# Codebase Insights

**Persistent code intelligence for AI coding agents.**

Codebase Insights combines **Language Server Protocol (LSP)** analysis, **LLM-generated summaries**, and **semantic embeddings** to build a structured, reusable understanding of a codebase.

It exposes this understanding through an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server, making it usable from MCP-compatible clients such as Claude Desktop, GitHub Copilot, and other agent workflows.

---

## Why this exists

Most coding agents still rely heavily on keyword search over guessed context when trying to find relevant code. That causes three recurring problems:

1. **Noisy retrieval**  
   Lexical matches often find superficially similar code while missing the symbol or implementation that actually matters.

2. **Weak structural understanding**  
   Definitions, references, implementations, and symbol hierarchies are crucial for navigating real codebases, but plain text search does not model them directly.

3. **No durable understanding across sessions**  
   Agents often pay the repository exploration cost over and over again, even when nothing has changed.

**Codebase Insights** addresses these limitations by combining:

- **LSP servers** for precise symbol extraction and navigation
- **LLM summarization** for higher-level semantic understanding
- **Vector embeddings** for natural-language retrieval
- **Persistent local indexes** so unchanged code does not need to be rediscovered from scratch

The result is a reusable code-understanding layer for intelligent coding assistants.

---

## What it provides

- **Workspace-wide symbol indexing**
- **Natural-language semantic code search**
- **Natural-language file search** via AI-generated file summaries
- **Definition / references / implementation navigation via LSP**
- **Incremental re-indexing** driven by file watching and hashes
- **Persistent codebase understanding across sessions**
- **MCP server integration** for AI clients and agents

---

## Benchmark Highlights

Benchmark results below are from **codebase-insights v0.2.4** on a real Electron + Vue + React Native monorepo with **90 files**, **5,595 symbols**, and **31,195 cross-references**.

### Retrieval quality (v0.2.4)

| Metric | Symbol search | File search |
|---|---|---|
| **Hit@1** | **96.4%** (27/28) | **100%** (5/5) |
| **Hit@3** | **100%** (28/28) | **100%** (5/5) |
| **Hit@5** | **100%** (28/28) | **100%** (5/5) |
| **Keyword baseline Hit@1** | 1/28 (3.6%) | — |

### Progress since v0.1.1

| Version | Symbol Hit@1 | Symbol Hit@3 | File Hit@1 |
|---|---|---|---|
| v0.1.1 | 68.4% | 89.5% | — |
| v0.2.3 | 29.4% (strict queries) | 76.5% | 33.3% |
| **v0.2.4** | **96.4%** | **100%** | **100%** |

> v0.2.3 introduced harder, more realistic benchmark queries (longer natural-language descriptions instead of near-exact symbol names). The v0.2.4 ranking improvements brought Hit@1 from 29.4% back to 96.4% on these harder queries.

### Semantic search vs keyword baseline

The benchmark uses a **context-unaware LLM agent** as the keyword baseline: given only the natural-language query (no codebase knowledge), it generates 1-2 search terms and calls \query_symbols\. Results:

| Metric | Semantic search | Keyword baseline |
|---|---|---|
| Hit@1 | **27/28 (96.4%)** | 1/28 (3.6%) |
| Hit@3 | **28/28 (100%)** | 1/28 (3.6%) |
| Found expected symbol in results | **27/28** | 1/28 |
| Returned zero results | 0 queries | **18/28 queries** |

Sample failures from the keyword baseline:

| Query | KW agent searched | KW top result | Semantic |
|---|---|---|---|
| *“AI provider interface contract”* | \interface+provider\ | — (no results) | \AIProvider\ ✓ |
| *“SQLite-backed persistent chat storage”* | \conversation+persistent\ | \getMobileConversationSummary\ | \DesktopSQLiteChatStore\ ✓ |
| *“look up localized text strings by translation key”* | \	ranslation+localized\ | — (no results) | \	\ ✓ |
| *“sanitize markup by removing tags”* | \sanitize+removing\ | — (no results) | \stripHtml\ ✓ |

On concept-level natural-language queries, keyword search fundamentally cannot generate the right search term because it has no knowledge of how the codebase is named. Semantic search finds the right symbol regardless.

### Build performance (v0.1.1)

| Metric | Value |
|---|---|
| Full pipeline wall time | **427.7s (~7.1 min)** |
| Storage footprint | **13.80 MB** |
| No-change catch-up | **0.05s** |

### Incremental updates (v0.1.1)

| Scenario | Total time |
|---|---|
| No change | **0.05s** |
| Leaf-file edit | **~18s** |
| Core-file edit | **~19s** |
| New file | **~21s** |

> See [docs/benchmark-v0.2.4.md](docs/benchmark-v0.2.4.md) for per-query results. See [docs/benchmark-v0.1.1.md](docs/benchmark-v0.1.1.md) for full rebuild timing and incremental update methodology.

---

## Features

- **Multi-language support** — Python, JavaScript/TypeScript, C++, Rust via standard LSP servers
- **Symbol indexing** — full workspace scan with file-watch-driven incremental re-indexing
- **Semantic search** — AI-generated symbol summaries + embeddings for natural-language retrieval
- **File search** — AI-generated file summaries + embeddings for module-level retrieval
- **Hybrid ranking** — blends vector similarity, stem-aware keyword matching, summary relevance, and reference-count boost with kind-preference heuristics
- **Flexible model backends** — Ollama (local) or OpenAI-compatible APIs
- **MCP server** — exposes all capabilities over HTTP for any MCP-compatible client

---

## Architecture

```text
src/codebase_insights/
├── main.py              CLI entry point & startup orchestration
├── language_analysis.py Detects languages; parses .gitignore
├── LSP.py               Async LSP client (hover, definition, references, symbols, …)
├── workspace_indexer.py Indexes symbols into SQLite; watches for file changes
├── semantic_indexer.py  LLM summarization + ChromaDB vector indexing & search
├── semantic_config.py   TOML config loader with interactive first-time setup wizard
└── mcp_server.py        MCP server exposing all tools over HTTP
```

### On-disk artifacts

These are created at the target project root and automatically added to `.gitignore`:

| File / Directory | Purpose |
|---|---|
| `.codebase-index.db` | SQLite symbol database |
| `.codebase-semantic/` | ChromaDB vector store |
| `.codebase-insights.toml` | Configuration file |

---

## How it works

1. **Startup**  
   Detects languages, validates required LSP servers, and initializes LSP clients.

2. **Workspace indexing**  
   Scans the repository with LSP `documentSymbol`, stores symbols and references in SQLite, and monitors changes with filesystem watching.

3. **Semantic indexing**  
   Extracts source context for qualifying symbols, generates short LLM summaries, and stores embeddings in ChromaDB.

4. **File and project summarization**  
   Generates file-level summaries, embeds them for file search, and maintains a project summary for higher-level semantic retrieval and context.

5. **Incremental updates**  
   Uses file hashes and symbol-content hashes to skip unchanged work and only reprocess modified or new symbols.

6. **MCP query serving**  
   Exposes symbol and semantic capabilities over MCP:
   - `query_symbols(...)` reads from SQLite
   - `semantic_search(...)` performs hybrid lexical + vector ranking
   - `search_files(...)` searches embedded file summaries by architectural intent
   - `get_file_summary(...)` returns the stored summary for one file
   - `get_project_summary(...)` returns the codebase-level structural overview
   - `lsp_*` tools expose structural navigation directly from language servers

---

## Why not just use keyword search?

Keyword search is still useful, but it breaks down quickly when agents need to reason about structure and behavior.

| Task | Keyword search | Codebase Insights |
|---|---|---|
| Find exact symbol names | Good | Good |
| Find code by concept or behavior | Weak | **Strong** |
| Find single-char or abbreviated names | Impossible | **Works** |
| Jump to definitions | Manual / indirect | Built-in |
| Find references | Approximate | Precise via LSP |
| Find implementations / subclasses | Hard | Built-in |
| Reuse code understanding across sessions | No | Yes |
| Reduce repeated exploration cost | No | Yes |

Concrete benchmark examples:

- Query: **"look up localized text strings by translation key"**
  - Semantic search returns `t` (the translation function) directly
  - Keyword search returns `constructor`, `greet`, `count` — completely wrong results

- Query: **"AI provider interface contract with chat and stream methods"**
  - Semantic search returns `AIProvider` ✓
  - Keyword search returns `AzureOpenAIProvider` — a concrete implementation, not the interface

- Query: **"sanitize markup by removing tags and decoding HTML entities"**
  - Semantic search returns `stripHtml` despite zero keyword overlap between query and name

At the file level, a query like **"secure credential storage implementations for desktop and mobile"** returns `keystore.ts` directly — even when the filename doesn't literally contain the concept words.

That difference matters a lot for coding agents.

---

## Prerequisites

- Python **3.11+**
- At least one LSP server matching the language(s) in the target repository
- Either:
  - **Ollama** running locally, or
  - an **OpenAI-compatible API key**

### Supported LSP servers

| Language | Server | Install |
|---|---|---|
| Python | `pylsp` | `pip install python-lsp-server` |
| JavaScript / TypeScript | `typescript-language-server` | `npm install -g typescript-language-server` |
| C++ | `clangd` | [clangd.llvm.org](https://clangd.llvm.org/installation.html) |
| Rust | `rust-analyzer` | `rustup component add rust-analyzer` |

Optional Python LSP plugins:
- `python-lsp-ruff`
- `python-lsp-black`
- `pylsp-mypy`

---

## Installation

### From PyPI

```bash
pip install codebase-insights
```

### From source

```bash
git clone https://github.com/your-org/codebase-insights
cd codebase-insights
pip install -e .
```

---

## Quick start

### Ollama

```bash
# Terminal 1
ollama serve

# Terminal 2
codebase-insights /path/to/your/project
```

### OpenAI-compatible API

```bash
export OPENAI_API_KEY="sk-..."
codebase-insights /path/to/your/project --new-config
# choose "openai" when prompted for chat and embed providers
```

On first run, an interactive wizard creates `.codebase-insights.toml` and helps configure model providers and indexing settings.

The MCP server starts on:

```text
http://127.0.0.1:6789/mcp
```

using streamable HTTP transport.

---

## Usage

```bash
codebase-insights <project_root> [options]
```

### CLI options

| Flag | Description |
|---|---|
| `--new-config` | Re-run the setup wizard, overwriting the existing config |
| `--rebuild-index` | Drop and rebuild the SQLite symbol index from scratch |
| `--rebuild-semantic` | Drop all LLM summaries and ChromaDB vectors, regenerate everything |
| `--rebuild-summaries` | Regenerate only file/project summaries (keeps symbol summaries) |
| `--rebuild-vectors` | Re-embed existing summaries with the current embedding model (no LLM calls) |

These rebuild flags are intended for explicit maintenance or benchmarking. In normal usage, let Codebase Insights reuse the existing index and apply incremental updates automatically.

---

## MCP tools

Once running, the following tools are available to connected MCP clients:

| Tool | Description |
|---|---|
| `languages_in_codebase()` | List detected languages in the project |
| `lsp_capabilities()` | Query active LSP server capabilities |
| `lsp_hover(file_uri, line, character)` | Type information and documentation at a position |
| `lsp_definition(file_uri, line, character)` | Jump to definition |
| `lsp_declaration(file_uri, line, character)` | Find declarations |
| `lsp_implementation(file_uri, line, character)` | Find implementations |
| `lsp_references(file_uri, line, character)` | Find all references to a symbol |
| `lsp_document_symbols(file_uri)` | List all symbols in a file |
| `query_symbols(path, kinds, name_query, limit)` | Query the SQLite index by path, kind, or name |
| `semantic_search(query, limit, kinds)` | Natural-language search over symbol summaries |
| `search_files(query, limit)` | Natural-language search over file summaries |
| `get_file_summary(file_path)` | Return the stored AI summary for one file |
| `get_project_summary()` | Return the AI-generated structural overview of the codebase |

> `file_uri` should use normal file URIs such as `file:///G:/repo/path/to/file.ts`.

---

## Configuration

The config file `.codebase-insights.toml` is created interactively on first run.

### Example

```toml
[chat]
provider = "ollama"          # "ollama" | "openai"

[chat.ollama]
base_url = "http://localhost:11434"
model = "qwen2.5"

[embed]
provider = "ollama"

[embed.ollama]
model = "bge-m3"

[semantic]
index_kinds = ["Class", "Method", "Function", "Interface", "Enum", "Constructor"]
concurrency = 16
batch_size = 16
min_ref_count = 1

[ranking]
# noise penalties and re-ranking weights (see semantic_config.py for defaults)
```

### Environment variable overrides

These environment variables override the corresponding TOML values:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

---

## Example use cases

Codebase Insights is useful for questions like:

- **{LQ}Find all implementations of this provider interface.{RQ}**
- **{LQ}What handles WebSocket messages in this repo?{RQ}**
- **{LQ}Where is configuration loaded and applied?{RQ}**
- **{LQ}Look up localized text strings by translation key.{RQ}**
- **{LQ}Sanitize markup by removing tags and decoding HTML entities.{RQ}**
- **{LQ}Permission flags controlling agent filesystem and shell access.{RQ}**
- **{LQ}Delay component mount until after the browser finishes initial paint.{RQ}**

It is particularly effective for agents that need to:

- move from natural-language intent to a likely symbol
- find code by concept when you don’t know the exact name
- expand from that symbol to definitions, references, and implementations
- reduce file-opening and grep iteration overhead
- retain reusable codebase understanding across sessions

---

## Known limitations

Current limitations and trade-offs include:

- **Only a subset of symbols are semantically indexed**  
  Filtering by symbol kind and reference count improves quality and cost, but reduces coverage.

- **Inline or anonymous logic is harder to retrieve semantically**  
  For example, anonymous callbacks and ad-hoc `try/catch` behavior do not form named symbols.

- **Convention-based framework behavior may be less visible**  
  File-system routing or other convention-heavy patterns may not map cleanly to LSP symbol graphs.

- **Full project summaries remain relatively expensive**  
  Incremental project-summary updates are much faster now, but full project summarization is still one of the largest rebuild costs.

---

## Dependencies

| Package | Purpose |
|---|---|
| `mcp[cli]` | MCP server framework |
| `watchdog` | Filesystem monitoring |
| `langchain`, `langchain-ollama`, `langchain-openai` | LLM and embedding integration |
| `langchain-chroma`, `chromadb` | Vector store |
| `tqdm` | Progress bars |

---

## Project status

Codebase Insights is an **early-stage but functional** code-understanding platform.

Validated so far:

- workspace-wide symbol indexing
- LSP-backed navigation
- high-quality semantic retrieval (96.4% Hit@1 on benchmark)
- persistent on-disk indexes
- near-zero no-change catch-up
- practical incremental update behavior
- incremental project summary updates
- automated benchmark coverage

Still improving:

- retrieval quality on diffuse / anonymous logic
- indexing coverage trade-offs
- performance on larger repositories

---

## Contributing

Contributions, benchmark results, bug reports, and design feedback are welcome.

Especially valuable areas include:

- performance optimization
- retrieval quality tuning
- incremental update behavior
- support for more repo styles and languages
- benchmark automation
- MCP client ergonomics

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
