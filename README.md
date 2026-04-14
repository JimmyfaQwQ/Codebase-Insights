# Codebase Insights

An intelligent code analysis platform that combines Language Server Protocol (LSP) technology with AI-powered semantic search to provide comprehensive code intelligence. Exposes capabilities via an [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server, making it usable by any MCP-compatible AI client such as Claude Desktop or GitHub Copilot.

## Features

- **Multi-language support** — Python, JavaScript/TypeScript, C++, Rust, powered by standard LSP servers
- **Symbol indexing** — Full workspace scan with incremental re-indexing driven by filesystem watching
- **Semantic search** — AI-generated summaries + vector embeddings for natural-language code queries
- **Hybrid ranking** — Blends keyword matching with vector similarity, boosted by reference counts
- **Flexible LLM backends** — Ollama (local) or OpenAI-compatible APIs for both chat and embeddings
- **MCP server** — Exposes all capabilities over HTTP for use by any MCP client

## Architecture

```
src/codebase_insights/
├── main.py              CLI entry point & startup orchestration
├── language_analysis.py Detects languages; parses .gitignore
├── LSP.py               Async LSP client (hover, definition, references, symbols, …)
├── workspace_indexer.py Indexes symbols into SQLite; watches for file changes
├── semantic_indexer.py  LLM summarization + ChromaDB vector indexing & search
├── semantic_config.py   TOML config loader with interactive first-time setup wizard
└── mcp_server.py        MCP server exposing all tools over HTTP
```

**Artifacts created at the project root (all added to `.gitignore` automatically):**

| File/Directory | Purpose |
|---|---|
| `.codebase-index.db` | SQLite symbol database |
| `.codebase-semantic/` | ChromaDB vector store |
| `.codebase-insights.toml` | Configuration file |

## Prerequisites

- Python 3.11+
- At least one of the LSP servers below, matching the language(s) in the target codebase
- Ollama running locally **or** an OpenAI-compatible API key

### LSP Servers

| Language | Server | Install |
|---|---|---|
| Python | `pylsp` | `pip install python-lsp-server` |
| JavaScript / TypeScript | `typescript-language-server` | `npm install -g typescript-language-server` |
| C++ | `clangd` | [clangd.llvm.org](https://clangd.llvm.org/installation.html) |
| Rust | `rust-analyzer` | `rustup component add rust-analyzer` |

Optional Python LSP plugins: `python-lsp-ruff`, `python-lsp-black`, `pylsp-mypy`.

## Installation

```bash
pip install codebase-insights
```

Or install from source for development:

```bash
git clone https://github.com/your-org/codebase-insights
cd codebase-insights
pip install -e .
```

## Usage

```bash
codebase-insights <project_root> [options]
```

On first run an interactive wizard configures the LLM provider, embedding model, and indexing settings, saving the result to `.codebase-insights.toml`.

### Options

| Flag | Description |
|---|---|
| `--new-config` | Re-run the setup wizard, overwriting the existing config |
| `--rebuild-index` | Drop and rebuild the SQLite symbol index from scratch |
| `--rebuild-semantic` | Drop all LLM summaries and ChromaDB vectors, regenerate everything |
| `--rebuild-summaries` | Regenerate only file/project summaries (keeps symbol summaries) |
| `--rebuild-vectors` | Re-embed existing summaries with the current embedding model (no LLM calls) |

### Quick start with Ollama

```bash
# Terminal 1 – start Ollama
ollama serve

# Terminal 2 – index and serve
codebase-insights /path/to/your/project
```

### Quick start with OpenAI

```bash
export OPENAI_API_KEY="sk-..."
codebase-insights /path/to/your/project --new-config
# choose "openai" when prompted for chat and embed providers
```

The MCP server starts on `http://127.0.0.1:6789/mcp` (streamable-HTTP transport).

## MCP Tools

Once running, the following tools are available to any connected MCP client:

| Tool | Description |
|---|---|
| `languages_in_codebase()` | List detected languages in the project |
| `lsp_capabilities()` | Query active LSP server capabilities |
| `lsp_hover(file_uri, line, character)` | Type info and docs at a position |
| `lsp_definition(file_uri, line, character)` | Jump-to-definition |
| `lsp_declaration(file_uri, line, character)` | Find declarations |
| `lsp_implementation(file_uri, line, character)` | Find implementations |
| `lsp_references(file_uri, line, character)` | Find all references to a symbol |
| `lsp_document_symbols(file_uri)` | List all symbols in a file |
| `query_symbols(path, kinds, name_query, limit)` | Query the SQLite index by path, kind, or name |
| `semantic_search(query, limit, kinds)` | Natural-language semantic search |

## Configuration

The config file `.codebase-insights.toml` is created interactively on first run. Key sections:

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

Environment variables (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) override the corresponding TOML values.

## How It Works

1. **Startup** — detects languages, verifies LSP servers are on `PATH`, initialises LSP clients
2. **Workspace indexing** — scans all files via LSP `documentSymbol`, stores symbols + references in SQLite; a watchdog observer re-indexes files as they change
3. **Semantic indexing** — for each qualifying symbol, extracts up to 50 lines of source context, calls the LLM for a 1–3 sentence summary, then embeds the summary in ChromaDB
4. **MCP server** — clients call tools; `semantic_search` uses hybrid vector + keyword scoring with reference-count boosting and diversity decay; `query_symbols` queries SQLite directly
5. **Incremental updates** — SHA-256 file hashes and symbol-content hashes skip unchanged work; only new or modified symbols are re-summarised

## Dependencies

| Package | Purpose |
|---|---|
| `mcp[cli]` | MCP server framework |
| `watchdog` | Filesystem monitoring |
| `langchain`, `langchain-ollama`, `langchain-openai` | LLM / embedding integration |
| `langchain-chroma`, `chromadb` | Vector store |
| `tqdm` | Progress bars |
