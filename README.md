# Codebase Insights

**LSP-powered code intelligence for AI coding agents.**  
Codebase Insights combines **Language Server Protocol (LSP)** analysis with **LLM-generated summaries** and **semantic embeddings** to build a persistent, structured understanding of a codebase.

It exposes this understanding through an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server, so any MCP-compatible client — such as Claude Desktop or GitHub Copilot — can query symbols, follow references, jump to definitions, and perform semantic code search over a repository.

---

## Why Codebase Insights?

Today’s coding agents still rely too heavily on naive keyword search over loosely guessed context. That creates three recurring problems:

1. **Noisy retrieval**  
   Keyword matches often return many superficially relevant files while missing the code that actually implements the behavior you care about.

2. **Weak code relationship awareness**  
   Definitions, references, declarations, and implementations are central to understanding a codebase, but plain text search does not model these relationships directly.

3. **No durable understanding across sessions**  
   Most agents repeatedly rediscover the same repository structure every time they return to a project, wasting tokens, time, and context budget.

**Codebase Insights** addresses these limitations by combining:

- **LSP servers** for precise symbol extraction and navigation
- **LLM summarization** for higher-level semantic understanding
- **Vector embeddings** for natural-language retrieval
- **Persistent local indexes** so unchanged code does not need to be re-explored from scratch

The result is a reusable code-understanding layer for intelligent coding assistants.

---

## What it gives you

- **Symbol-aware indexing** across an entire workspace
- **Semantic search** over code behavior, not just filenames or symbol names
- **Definition / references / implementations** via LSP
- **Incremental updates** driven by file watching and hash-based skipping
- **Persistent codebase understanding** across sessions
- **MCP-compatible access** for AI clients and agent workflows

---

## Benchmark Highlights

Results below are from benchmarking against a real TypeScript pnpm monorepo (`G:\SyntaxSenpai\`) with **118 files** and **5,615 symbols**.

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

\* Currently dominated by full project summary regeneration.

### Incremental update summary

- **No-change restart:** `<0.1s` catch-up, `118/118` files skipped
- **Leaf-file edit:** `~6s` end-to-end update
- **Core-file edit:** `~141s`, currently dominated by project summary regeneration
- **New file:** `~133s`, currently dominated by project summary regeneration

These results suggest that **symbol- and file-level incremental updates already work well**, while **project-level summarization is still too coarse-grained** and remains a major optimization target.

---

## Features

- **Multi-language support** — Python, JavaScript/TypeScript, C++, Rust, powered by standard LSP servers
- **Symbol indexing** — Full workspace scan with incremental re-indexing driven by filesystem watching
- **Semantic search** — AI-generated summaries + vector embeddings for natural-language code queries
- **Hybrid ranking** — Blends keyword matching with vector similarity, boosted by reference counts
- **Flexible LLM backends** — Ollama (local) or OpenAI-compatible APIs for both chat and embeddings
- **MCP server** — Exposes all capabilities over HTTP for use by any MCP client

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

These are created at the project root and automatically added to `.gitignore`:

| File/Directory | Purpose |
|---|---|
| `.codebase-index.db` | SQLite symbol database |
| `.codebase-semantic/` | ChromaDB vector store |
| `.codebase-insights.toml` | Configuration file |

---

## How it works

1. **Startup**  
   Detects languages, validates required LSP servers, and initializes LSP clients.

2. **Workspace indexing**  
   Scans the repository via LSP `documentSymbol`, stores symbols and references in SQLite, and watches for file changes.

3. **Semantic indexing**  
   Extracts source context for qualifying symbols, generates short LLM summaries, and embeds them into ChromaDB.

4. **Query serving**  
   Exposes symbol and semantic capabilities over MCP.  
   - `query_symbols` reads directly from SQLite  
   - `semantic_search` uses hybrid vector + keyword ranking with reference-count boosting

5. **Incremental updates**  
   Uses file hashes and symbol-content hashes to skip unchanged work and only reprocess modified or newly added symbols.

---

## Why not just use keyword search?

Keyword search is still useful, but it has clear limitations for code understanding:

| Problem | Keyword search | Codebase Insights |
|---|---|---|
| Find code by exact name | Good | Good |
| Find code by concept or behavior | Weak | Stronger |
| Jump to definitions | Manual / indirect | Built-in |
| Find all references | Approximate | Precise via LSP |
| Find implementations of an interface | Hard | Built-in |
| Reuse understanding across sessions | None | Persistent |
| Reduce repeated exploration cost | No | Yes |

Codebase Insights is designed to complement — and often outperform — plain lexical search when an agent needs to understand code structure and meaning rather than just match text.

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

### Run with Ollama

```bash
# Terminal 1
ollama serve

# Terminal 2
codebase-insights /path/to/your/project
```

### Run with an OpenAI-compatible API

```bash
export OPENAI_API_KEY="sk-..."
codebase-insights /path/to/your/project --new-config
# choose "openai" when prompted for chat and embed providers
```

On first run, an interactive wizard creates `.codebase-insights.toml` and guides you through provider and indexing configuration.

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

---

## MCP tools

Once running, the following tools are exposed to connected MCP clients:

| Tool | Description |
|---|---|
| `languages_in_codebase()` | List detected languages in the project |
| `lsp_capabilities()` | Query active LSP server capabilities |
| `lsp_hover(file_uri, line, character)` | Type information and documentation at a position |
| `lsp_definition(file_uri, line, character)` | Jump to definition |
| `lsp_declaration(file_uri, line, character)` | Find declarations |
| `lsp_implementation(file_uri, line, character)` | Find implementations |
| `lsp_references(file_uri, line, character)` | Find references to a symbol |
| `lsp_document_symbols(file_uri)` | List all symbols in a file |
| `query_symbols(path, kinds, name_query, limit)` | Query the SQLite index by path, kind, or name |
| `semantic_search(query, limit, kinds)` | Perform natural-language semantic search |

> **Note:** some LSP servers require `file:///` URIs rather than bare filesystem paths for file-based operations.

---

## Configuration

The config file `.codebase-insights.toml` is generated interactively on first run.

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
concurrency = 16             # parallel LLM requests (set to 1 for Ollama)
batch_size = 16
min_ref_count = 3            # only index symbols referenced at least N times

[ranking]
# noise penalties and re-ranking weights (see semantic_config.py for defaults)
```

### Environment variable overrides

The following environment variables override corresponding config values:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

---

## Example use cases

Codebase Insights is especially useful for tasks like:

- **“Find the real implementation of this feature.”**
- **“Show me every implementation of this interface.”**
- **“What references this symbol?”**
- **“Where is configuration loaded and applied?”**
- **“Find the code responsible for streaming responses.”**
- **“Search the repository by behavior, not just by exact names.”**

It is particularly effective for AI agents that need to:
- move from natural language to likely symbols
- traverse definitions / references / implementations
- reduce file-opening and grep iteration overhead
- retain codebase understanding across sessions

---

## Current limitations

Current benchmark and usage findings suggest the following rough edges:

- **Project summary updates are too coarse-grained** and currently dominate structural edit costs
- **Workspace startup has a noticeable fixed cost** on larger repositories
- **Convention-based routing or framework magic** may not surface well through symbol indexing alone
- **Inline behaviors** (for example, distributed `try/catch` logic) are harder to retrieve semantically than named abstractions
- Some LSP servers require strict `file:///` URI formatting

These are active areas for improvement rather than fundamental design blockers.

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

Codebase Insights is currently an **early-stage but functional** code-understanding platform.

What is already validated:
- full-workspace symbol indexing
- semantic search over indexed symbols
- LSP-backed symbol navigation
- persistent on-disk indexes
- hash-based skipping for unchanged code
- practical incremental updates for small edits

What is still being improved:
- startup latency
- project-level summary granularity
- query quality for convention-heavy code patterns
- ergonomics around LSP path handling

---

## Contributing

Contributions, feedback, and benchmark results on additional repositories are welcome.

Particularly useful areas for contribution include:
- performance optimization
- incremental update behavior
- retrieval quality evaluation
- additional LSP integrations
- better MCP client ergonomics
- benchmark automation

---

## License

MIT License. See [LICENSE](LICENSE) for details.
