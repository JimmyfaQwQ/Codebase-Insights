# Codebase Insights — Navigate-then-Read Instructions

You have access to a **Codebase Insights** MCP server. It exposes pre-computed intelligence about the codebase — semantic summaries, symbol indexes, LSP navigation, and file-level overviews.

**Use these tools as a substitute for manual file browsing — not as a warm-up before it.** Every `view` call you replace with a CI tool call saves tokens: CI returns pre-digested summaries (200–400 tokens each); `view` dumps raw source into context (3,000–20,000 tokens per file).

The measure of success is completing the task with the **fewest `view` calls**. Do not use `glob`. Do not browse directories. Let Codebase Insights navigate for you.

---

## Workflow

### Step 1 — Orient (no file reads yet)

Call `get_project_summary` once before doing anything else. It gives you the architecture overview, file responsibilities, data flow, and where to make changes for common tasks. Do not open any files at this stage.

### Step 2 — Pinpoint (do not glob or grep)

Use search tools to locate exactly what you need:

- **`search_files(query)`** — when you know a file's *responsibility*, e.g. `"AI provider OpenAI integration"`
- **`semantic_search(query, kinds=[...])`** — when you know *behavior*, e.g. `"streaming chat completion with tool call handling"`
- **`query_symbols(name_query=..., kinds=[...], path=...)`** — when you know *part of a name* or want to filter by kind/path

These return ranked results with short summaries. Stop searching once you have a confident candidate.

### Step 3 — Scan before opening

Before reading any file, call `get_file_summary(file_path)`.

- If the summary **answers your question** → skip `view`, move on
- If the summary **confirms the file is relevant** → proceed to Step 4
- If the summary **rules out the file** → discard it without reading

This gate is the single biggest token saver.

### Step 4 — Precision reading

When you do open a file, use line ranges:

1. Use `query_symbols(path=..., name_query=...)` or `lsp_document_symbols(file_uri)` to find the exact line number of the class or function you want.
2. Call `view` with `view_range=[start, end]` — read only that section, not the whole file.

---

## Tool Reference

| Tool | Use when |
|---|---|
| `get_project_summary()` | Always call first — architecture overview |
| `search_files(query)` | Find files by responsibility |
| `semantic_search(query, kinds=[])` | Find symbols by behavior or intent |
| `query_symbols(name_query, kinds, path)` | Find symbols by partial name, kind, or file |
| `get_file_summary(file_path)` | Scan a file cheaply before committing to `view` |
| `get_symbol_summary(name, file_uri, line, character)` | Get the AI summary for a specific symbol |
| `lsp_document_symbols(file_uri)` | List all symbols in a file with exact line numbers |
| `lsp_definition(file_uri, line, character)` | Jump to definition from a call site |
| `lsp_references(file_uri, line, character)` | Find all usages of a symbol |
| `lsp_implementation(file_uri, line, character)` | Find all implementations of an interface |
| `lsp_hover(file_uri, line, character)` | Get type signature and docs at a position |
| `refresh_file_summary(file_path)` | Force-update a summary marked as stale |

---

## Anti-patterns

| What to avoid | Why | Instead |
|---|---|---|
| `get_project_summary` → `view` every file it mentions | CI becomes a warm-up, not a substitute; same token cost as browsing | Call `get_file_summary` on each candidate first; only `view` confirmed ones |
| `glob("**/*.ts")` to discover files | Returns all paths with no signal | `search_files("what you're looking for")` |
| `grep` to find a class name | Returns raw lines with no context | `query_symbols(name_query="ClassName")` — returns path + line directly |
| `view` of an entire large file | 5,000–20,000 tokens wasted | `lsp_document_symbols` or `query_symbols` to get the line, then `view` with range |
| CI tools + reading every file anyway | Observed +281% token overhead | Trust the summary; read only when summary is explicitly insufficient |

---

## Fall back to `view` when

- The file summary confirms relevance but lacks the implementation detail you need to copy verbatim
- A summary is marked `is_stale: true` and `refresh_file_summary` is not an option
- The logic you need lives in an anonymous callback or inline closure not captured by symbol indexing
- You need an exact complex generic type signature that summaries abbreviate
