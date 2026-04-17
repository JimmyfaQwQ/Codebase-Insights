# Codebase Insights — Template Instructions

You have access to a **Codebase Insights** MCP server. It exposes pre-computed intelligence about the codebase: project summaries, file summaries, semantic symbol search, file search, structural LSP navigation, and a persistent symbol index.

Use Codebase Insights to reduce blind exploration. Prefer summary-driven routing over broad file browsing. The goal is to identify the right file or symbol before opening raw source.

---

## Operating Principles

1. Start with the highest-signal tool available.
2. Use summaries and indexed search to narrow the search space before reading source.
3. Read source only when you need exact implementation details.
4. When reading source, read the smallest relevant section.
5. Avoid directory browsing and wide file reads unless the indexed tools are insufficient.

---

## Recommended Workflow

### Step 1 — Get oriented

Call `get_project_summary()` first.

Use it to understand:

- overall architecture
- major subsystems
- likely extension points
- where a change probably belongs

Do not start by browsing directories or opening multiple files.

### Step 2 — Narrow the target

Use the search tools based on what you know:

- `search_files(query)` when you know a file's responsibility
- `semantic_search(query, kinds=[...])` when you know behavior or intent
- `query_symbols(name_query=..., kinds=[...], path=...)` when you know part of a symbol name or want exact filtering

Stop when you have a confident candidate instead of continuing to fan out searches.

### Step 3 — Check the file before reading it

Call `get_file_summary(file_path)` before opening a file.

- If the summary already answers the question, do not read the file.
- If the summary confirms relevance, continue.
- If the summary rules the file out, discard it and move on.

### Step 4 — Read precisely

If raw source is still needed:

1. Use `query_symbols(...)` or `lsp_document_symbols(file_uri)` to find the exact symbol.
2. Open only the relevant region.
3. Prefer symbol-level or line-range reads over full-file reads.

### Step 5 — Expand structurally

Once you have the right symbol, use structural tools instead of more keyword search:

- `lsp_definition(...)`
- `lsp_declaration(...)`
- `lsp_implementation(...)`
- `lsp_references(...)`
- `lsp_hover(...)`

This is the precise phase. Use it to move between interface, implementation, and usage sites.

---

## Tool Selection Guide

| Tool | Use when |
|---|---|
| `get_project_summary()` | You need the architectural overview or likely change area |
| `search_files(query)` | You know what a file is responsible for |
| `semantic_search(query, kinds=[])` | You know what a symbol does, but not its name |
| `query_symbols(name_query, kinds, path)` | You know part of a symbol name or need exact path/kind filtering |
| `get_file_summary(file_path)` | You want to check a file cheaply before reading raw source |
| `get_symbol_summary(name, file_uri, line, character)` | You want the stored summary of one symbol |
| `lsp_document_symbols(file_uri)` | You need symbols and positions inside one file |
| `lsp_definition(file_uri, line, character)` | You need to jump to a definition |
| `lsp_references(file_uri, line, character)` | You need usages of a symbol |
| `lsp_implementation(file_uri, line, character)` | You need implementers of an interface or base symbol |
| `lsp_hover(file_uri, line, character)` | You need type or documentation at a position |
| `refresh_file_summary(file_path)` | A file summary is stale and you need it refreshed now |
| `refresh_project_summary()` | Project-level summaries are stale and need a forced refresh |

---

## What To Avoid

| Avoid | Why | Prefer |
|---|---|---|
| Opening every file mentioned by `get_project_summary()` | Turns summary tooling into a warm-up instead of a filter | Check candidate files with `get_file_summary()` first |
| Broad directory browsing to discover files | Produces paths with little signal | `search_files("what you are looking for")` |
| Grep-first symbol hunting | Returns raw text with poor ranking | `query_symbols(...)` or `semantic_search(...)` |
| Reading an entire large file immediately | Bloats context quickly | Find the symbol first, then read only the needed section |
| Continuing to search after you already have a strong candidate | Adds noise without improving certainty much | Move to summary check or structural navigation |

---

## When Raw Source Is Still Necessary

Read the source when:

- you need exact implementation details
- you need exact signatures or complex type information
- the relevant logic is inline and not well captured by symbol indexing
- the summary confirms relevance but is too abstract for the task
- a summary is stale and cannot be refreshed immediately

Even then, prefer the smallest useful section.
