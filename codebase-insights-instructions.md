# Codebase Exploration Policy

When exploring this codebase, follow these rules strictly.

## Primary rule

Prefer indexed and semantic tools first. Only fall back to raw text/pattern search when the better tools cannot answer the question.

## Required startup behavior

At the beginning of a new codebase investigation, do these first when relevant:
1. Call `lsp_capabilities` to learn what navigation features are supported.
2. Call `get_indexer_criteria` to learn which symbol kinds are indexed and summary-supported.
3. If the task is broad or the repo is unfamiliar, call `get_project_summary`.

## Tool selection policy

### Use these by default
- `get_project_summary` for repo/subsystem orientation
- `get_file_summary` for file responsibility
- `get_symbol_summary` for symbol responsibility
- `semantic_search(...)` for behavior, intent, or conceptual lookup
- `query_symbols(...)` for known or partial names
- `lsp_definition(...)` for definitions
- `lsp_declaration(...)` for declarations
- `lsp_implementation(...)` for implementations
- `lsp_references(...)` for usages
- `lsp_hover(...)` for quick symbol info

### Use with caution
- `lsp_document_symbols(...)` only when you explicitly need a full symbol inventory for one file

### Avoid unless necessary
- grep, glob, or raw pattern matching to find files or symbols
- direct pattern search as the first step
- expensive broad scans when indexed tools can narrow the scope first

## Decision rules

### If I know behavior but not names
1. Use `semantic_search(...)`
2. Inspect returned justifications
3. Open the most relevant symbols/files with summary or LSP tools

### If I know a full or partial symbol name
1. Use `query_symbols(...)`
2. Then use `get_symbol_summary(...)` or LSP navigation tools

### If I know the relevant subsystem or file
1. Use `get_file_summary(...)`
2. Then use symbol/LSP tools inside that area

### If I need to understand usage or impact
1. Find the symbol with `query_symbols(...)` or `semantic_search(...)`
2. Use `lsp_references(...)`
3. Use `lsp_definition(...)` / `lsp_implementation(...)` as needed

## Freshness rules

- If a summary indicates it may be stale, refresh it before relying on it.
- Use `refresh_project_summary` or `refresh_file_summary` when freshness matters.

## Hard constraints

- Do not use raw text search first when `semantic_search`, `query_symbols`, summaries, or LSP tools can answer the question.
- Do not use `lsp_document_symbols` by default on large files.
- Always explain briefly why a chosen tool is the highest-signal next step.
- Prefer narrowing the search space before opening large files.
- When multiple tools could work, choose the cheapest high-signal tool first.

## Preferred investigation flow

1. Capabilities and index coverage:
   - `lsp_capabilities`
   - `get_indexer_criteria`
2. High-level orientation:
   - `get_project_summary`
3. Find candidates:
   - `semantic_search(...)` or `query_symbols(...)`
4. Understand candidates:
   - `get_file_summary(...)` / `get_symbol_summary(...)`
5. Navigate precisely:
   - `lsp_definition(...)`, `lsp_references(...)`, `lsp_implementation(...)`, `lsp_hover(...)`
6. Only if needed:
   - raw pattern search

## Response style

When reporting findings:
- State which tool you chose and why
- Prefer concise reasoning
- Mention when a summary may be stale
- Mention when you are falling back to raw search and why the preferred tools were insufficient