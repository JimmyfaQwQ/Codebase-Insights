# Codebase Insights

Codebase Insights is a local MCP server that gives coding agents a persistent model of your repository instead of making them rediscover it from scratch every session.

It combines four layers:

- LSP navigation for definitions, references, implementations, hover, and document symbols
- SQLite indexing for persistent workspace-wide symbol and reference lookup
- LLM-generated summaries for symbols, files, and the whole project
- Vector retrieval for natural-language search by behavior or intent

The point is not just “better search”. The point is giving an agent a cheaper, more reliable way to decide what code to read.

## Why it exists

Text search works when you already know the name.

Agents usually do not. The real query looks more like this:

- “Where is the code that strips HTML?”
- “Which file owns the IPC bridge?”
- “What builds the system prompt?”
- “Show me the interface, then the implementations, then the references.”

Without a persistent index, the default workflow is wasteful:

1. Guess keywords.
2. Run repeated grep or symbol-name searches.
3. Open a lot of files that turn out not to matter.
4. Carry all of that source text forward in context.

Codebase Insights short-circuits that loop:

- `search_files()` finds files by responsibility
- `semantic_search()` finds symbols by intent
- `query_symbols()` confirms exact names from SQLite
- LSP tools expand from there with precise structural navigation

That means fewer blind reads, less irrelevant context, and less token waste.

## What it is good at

Codebase Insights is most useful when the caller knows what the code does, but not what it is called.

Examples:

- “find the function that strips HTML tags”
- “find the module responsible for encrypted key storage”
- “find the runtime object that tracks token usage”
- “find the implementation(s) of this interface and then all references”

It is less valuable when the target is already known and you just need to open one file.

## Why it helps agents spend fewer tokens

Every exploratory file read adds raw source code to the conversation. In a ReAct-style loop, that source stays in context and makes later turns more expensive.

Codebase Insights reduces that cost by returning compact routing information first: ranked files, ranked symbols, and stored summaries. The agent can narrow the search space before it opens any source.

| Waste in a browse-first workflow | What Codebase Insights changes |
|---|---|
| Repeated keyword reformulation | Semantic retrieval accepts the original intent directly |
| Large irrelevant search dumps | Ranked symbols and files come back with short summaries |
| Re-exploring the same repo every session | SQLite and ChromaDB persist the repo model locally |

The difference matters most on concept-heavy tasks where the names are not obvious.

## Benchmark highlights

### Agent token benchmark

A controlled A/B benchmark compared two agent workflows on a real implementation task in SyntaxSenpai:

- Same task
- Same model
- Same repo state
- Temperature `0`

Task: implement a new Google Gemini provider by matching the structure of existing providers.

| Metric | Baseline | With Codebase Insights | Change |
|---|---:|---:|---:|
| Agent turns | 17 | 12 | **-29%** |
| Total tokens | 366,388 | 301,247 | **-18%** |
| Raw `view` calls | 14 | 5 | **-64%** |

The main takeaway is simple: six CI calls worth roughly `3k-4k` tokens of summaries replaced nine raw file reads worth roughly `30k-60k` tokens of source.

Read the full report in [benchmarks/agent-token-benchmark-v1.1.0.md](benchmarks/agent-token-benchmark-v1.1.0.md).

### Retrieval benchmark snapshot

Latest checked-in report: [benchmarks/benchmark-v1.1.0.md](benchmarks/benchmark-v1.1.0.md)

Benchmark target: `G:\SyntaxSenpai`, a real Electron + Vue + React Native monorepo.

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

That is the core claim of the project in measurable form: semantic routing finds the right code more often than keyword guessing.

## Quick start

### Requirements

- Python `3.11+`
- At least one supported LSP server for the target repository
- Either Ollama or an OpenAI-compatible provider for chat and embeddings

### Install

From PyPI:

```bash
pip install codebase-insights
```

From source:

```bash
pip install -e .
```

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

For C and C++, indexing quality depends on `clangd` having enough project context, usually through `compile_commands.json` or `compile_flags.txt`.

### Run it

```bash
codebase-insights <project_root> [options]
```

Quick start with Ollama:

```bash
# Terminal 1
ollama serve

# Terminal 2
codebase-insights /path/to/project
```

Quick start with an OpenAI-compatible provider:

```bash
# Set OPENAI_API_KEY first
codebase-insights /path/to/project --new-config
```

On first run, Codebase Insights creates `.codebase-insights.toml` using an interactive setup wizard.

## How it works

1. Detect supported languages in the target repo and start one LSP client per language.
2. Walk the workspace, respect `.gitignore`, flatten `documentSymbol` output, and store symbols plus references in SQLite.
3. Summarize eligible symbols with an LLM and store embeddings in ChromaDB.
4. Generate per-file summaries and a project summary for higher-level routing.
5. Keep the index warm incrementally by hashing files and only updating what changed.
6. Expose the whole surface over MCP on `http://127.0.0.1:6789/mcp`.

## Recommended agent workflow

If you want token savings, the workflow matters.

Use Codebase Insights as a substitute for broad file browsing, not as a warm-up before browsing anyway.

Recommended sequence:

1. `get_project_summary()` to orient on architecture.
2. `search_files()` or `semantic_search()` to narrow candidates.
3. `query_symbols()` when you need exact-name or kind filtering.
4. `get_file_summary()` before opening source.
5. `lsp_definition()`, `lsp_implementation()`, and `lsp_references()` once you have the right symbol.
6. `view` the source only after the summaries point to the right place.

Reusable workflow instructions live in [codebase-insights-instructions.md](codebase-insights-instructions.md).

## Best practices for Copilot, Claude Code, and Cursor

For agent-specific prompting guidance, see [docs/agent-usage.md](docs/agent-usage.md).

Use that guide if you want to configure Codebase Insights for GitHub Copilot, Claude Code, or Cursor without bloating the main README.

## Configuration

Chat and embedding providers are configured independently, so combinations like OpenAI for chat plus Ollama for embeddings are supported.

Default configuration shape:

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

Summary refresh controls:

| Setting | Default | Meaning |
|---|---|---|
| `summary_update_threshold` | `5` | Refresh summaries after this many files become stale |
| `summary_file_idle_timeout` | `30` | Refresh one file summary after that file has been idle |
| `summary_project_idle_timeout` | `300` | Refresh all stale summaries plus the project summary after the repo has been idle |

Set any of these to `0` to disable that trigger. `refresh_file_summary()` and `refresh_project_summary()` always force regeneration immediately.

API key precedence for OpenAI-compatible providers:

- `CODEBASE_INSIGHTS_CHAT_API_KEY`
- `CODEBASE_INSIGHTS_EMBED_API_KEY`
- `OPENAI_API_KEY`

Environment variables take precedence over keys stored in config.

## CLI flags

| Flag | Purpose |
|---|---|
| `--new-config` | Re-run the interactive setup wizard |
| `--rebuild-index` | Clear and rebuild the SQLite symbol index |
| `--rebuild-semantic` | Clear all summaries and vector data, then regenerate everything |
| `--rebuild-summaries` | Rebuild file and project summaries only |
| `--rebuild-vectors` | Re-embed existing summaries without new LLM summarization |

Normal usage is incremental. Rebuild flags are mainly for maintenance, model changes, or benchmarking.

## MCP tools

### Workspace and LSP

| Tool | Purpose |
|---|---|
| `languages_in_codebase()` | Return detected languages |
| `lsp_capabilities()` | Return active LSP capability information |
| `lsp_hover(file_uri, line, character)` | Hover docs or type at a position |
| `lsp_definition(file_uri, line, character)` | Jump to definition |
| `lsp_declaration(file_uri, line, character)` | Find declaration |
| `lsp_implementation(file_uri, line, character)` | Find implementations |
| `lsp_references(file_uri, line, character)` | Find references |
| `lsp_document_symbols(file_uri)` | List symbols in a file |

### Indexed and semantic retrieval

| Tool | Purpose |
|---|---|
| `query_symbols(path, kinds, name_query, limit)` | Search the SQLite symbol index by path, kind, or fuzzy name |
| `get_symbol_summary(name, file_uri, line, character)` | Fetch the stored summary for a symbol |
| `get_indexer_criteria()` | Return semantic indexing thresholds and eligible kinds |
| `semantic_search(query, limit, kinds)` | Find symbols by natural-language intent |
| `search_files(query, limit)` | Find files by natural-language responsibility |
| `get_file_summary(file_path)` | Fetch the stored summary for one file |
| `get_project_summary()` | Fetch the stored project-level overview |
| `refresh_file_summary(file_path)` | Force-regenerate one file summary |
| `refresh_project_summary()` | Force-regenerate all stale file summaries and the project summary |

LSP calls accept either `file:///...` URIs or absolute filesystem paths.

## Generated files in the indexed repository

| Path | Purpose |
|---|---|
| `.codebase-index.db` | Persistent SQLite symbol and reference database |
| `.codebase-semantic/` | ChromaDB data for symbol and file summaries |
| `.codebase-insights.toml` | Local provider and indexing configuration |

If the target repo already has a `.gitignore`, Codebase Insights automatically adds `.codebase-index.db` to it. The other generated artifacts should also be ignored in normal use.

## Repository layout

```text
src/codebase_insights/
├── main.py              CLI entry point and startup orchestration
├── LSP.py               LSP client wrapper
├── language_analysis.py Language detection and .gitignore parsing
├── workspace_indexer.py SQLite symbol/reference index plus file watching
├── semantic_config.py   Config loading and first-run setup wizard
├── semantic_indexer.py  LLM summaries, embeddings, ranking, file/project summaries
├── mcp_server.py        MCP tool surface

scripts/
├── benchmark_monitor.py     Benchmark process monitor
├── demo_agent_benchmark.py  Demo benchmark script
├── run_benchmark.py         Benchmark orchestrator

docs/
└── agent-usage.md                 Agent-specific usage guidance

benchmarks/
├── benchmark-v*.md                Versioned indexing and retrieval reports
└── agent-token-benchmark-v*.md    Agent token A/B benchmark reports
```

## Limitations

- Not every symbol gets an AI summary. Semantic indexing is intentionally selective.
- Named declarations work better than anonymous inline logic.
- LSP quality sets the floor. If the language server cannot understand the workspace, indexing quality will degrade.
- Semantic search is a routing tool, not a proof of completeness.
- Timing numbers are environment-specific; retrieval quality is the more stable signal.

## Benchmark material

- Latest benchmark report: [benchmarks/benchmark-v1.1.0.md](benchmarks/benchmark-v1.1.0.md)
- Latest agent token benchmark: [benchmarks/agent-token-benchmark-v1.1.0.md](benchmarks/agent-token-benchmark-v1.1.0.md)
- Benchmark runner: [scripts/run_benchmark.py](scripts/run_benchmark.py)
- Benchmark skill: [.github/skills/benchmark-eval/SKILL.md](.github/skills/benchmark-eval/SKILL.md)
- Agent token benchmark script: [scripts/demo_agent_benchmark.py](scripts/demo_agent_benchmark.py)
- Agent token benchmark skill: [.github/skills/agent-token-benchmark/SKILL.md](.github/skills/agent-token-benchmark/SKILL.md)

## License

MIT. See [LICENSE](LICENSE).
