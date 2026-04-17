---
name: codebase-insights-navigator
description: 'Navigate and implement in an unfamiliar codebase using the Codebase Insights MCP server as a substitute for file browsing. Use when: exploring a new repo, implementing a feature that requires understanding existing patterns, finding the right file/class/interface to extend. Replaces glob+grep+view-everything with a structured orient→pinpoint→scan→read workflow that reduces token consumption by ~18–30%.'
argument-hint: 'Task description, e.g. "Add a new payment provider that follows the same pattern as the Stripe provider"'
---

# Codebase Insights — Navigate-then-Read Skill

## When to Use

- You need to understand how something is structured in an unfamiliar codebase before writing code
- You need to find the right file, interface, or pattern to follow
- You're implementing something that must match existing conventions
- You want to minimize raw file reads and keep context lean

## Prerequisites

A Codebase Insights MCP server must be running against the target repository:

```powershell
codebase-insights <path-to-repo>
# Server starts on http://127.0.0.1:6789/mcp
```

The index must be pre-built (first run takes several minutes; subsequent runs are incremental).

---

## The Navigate-then-Read Workflow

**Core principle:** use CI tools as a *substitute* for file browsing — not as a warm-up before it. Every `view` call you replace with a CI call saves tokens (CI returns pre-digested summaries; `view` dumps raw source into context).

### STEP 1 — Orient (no file reads yet)

```
get_project_summary()
```

Read this once at the start. It gives you:
- Architecture overview (which modules own what)
- File responsibilities
- Data flow
- Where to make changes for common tasks

Do **not** open any files yet.

### STEP 2 — Pinpoint (do not glob or grep)

Use these tools to find exactly what you need:

```
search_files("AI provider OpenAI integration")
→ returns ranked list of files with summaries

semantic_search("streaming chat completion with tool call handling", kinds=["Class","Function"])
→ returns ranked symbols with AI summaries

query_symbols(kinds=["Interface"], name_query="Provider")
→ returns exact symbol names, file paths, line numbers
```

**Decision rule:**
- If you know the *responsibility* → `search_files`
- If you know the *behavior* → `semantic_search`
- If you know *part of the name* → `query_symbols`

### STEP 3 — Scan before opening

Before reading any file, call:

```
get_file_summary("path/to/candidate.ts")
```

Ask yourself: does the summary already answer my question?
- **Yes** → skip the `view`, move on
- **No / partial** → proceed to Step 4

This gate is the single biggest token saver. A file summary is 200–400 tokens; a `view` of the same file is 3,000–8,000 tokens.

### STEP 4 — Precision reading

When you do open a file, use line ranges:

```
view("path/to/file.ts", view_range=[42, 95])
```

Use `query_symbols` first to find the exact start line of the class or function you want:

```
query_symbols(path="path/to/file.ts", name_query="ProviderBase")
→ returns line=42
```

Then read only that section.

---

## Benchmark Evidence

On a Gemini provider implementation task (SyntaxSenpai, gpt-5-mini, temperature=0):

| Metric | Without CI | With CI | Δ |
|---|---:|---:|---|
| Agent turns | 17 | 12 | −29% |
| Total tokens | 366,388 | 301,247 | −18% |
| `view` calls | 14 | 5 | −64% |

**How:** 6 CI calls (total ~3,000–4,000 tokens of summaries) replaced 9 raw `view` calls (~30,000–60,000 tokens of source code). Compression ratio ≈ 10:1 for navigation context.

See full report: `docs/agent-token-benchmark.md`

---

## Prompt Template

Use this system prompt section when the agent has access to the CI MCP server:

```
You have access to a "codebase-insights" MCP server. These tools give you
pre-computed intelligence about the codebase — use them as a substitute for
manual file browsing, not as a warm-up before it.

## How to use codebase-insights efficiently

STEP 1 — Orient yourself (do NOT browse files yet):
  - `get_project_summary` → architecture overview, which modules own what

STEP 2 — Find exactly what you need (do NOT glob/grep):
  - `search_files` to locate files by description, e.g. "AI provider OpenAI"
  - `semantic_search` to find specific patterns, e.g. "streaming chat completion"
  - `query_symbols` to find interfaces/types by name, e.g. kind=Interface

STEP 3 — Scan candidates cheaply before opening them:
  - `get_file_summary` on any file you're considering reading
  - If the summary already answers your question → SKIP the full file read
  - Only `view` files whose summary confirms they contain what you need

STEP 4 — Precision reading:
  - When you do `view`, use line ranges to read only the relevant section
  - Use `query_symbols` to find the exact line of a class/function first

The measure of success: complete the task with the fewest `view` calls.
Every `view` call you replace with a CI tool call saves tokens.
Do NOT use glob, do NOT browse directories. Let codebase-insights navigate for you.
```

---

## Available CI Tools (quick reference)

| Tool | When to use |
|---|---|
| `get_project_summary()` | First call — always |
| `search_files(query)` | Find files by responsibility |
| `semantic_search(query, kinds=[])` | Find symbols by behavior or intent |
| `query_symbols(kinds=[], name_query="", path="")` | Find symbols by partial name, kind, or file |
| `get_file_summary(file_path)` | Scan a file cheaply before reading it |
| `get_symbol_summary(name, file_uri, line, character)` | Get the AI summary for a specific symbol |
| `lsp_definition(file_uri, line, character)` | Jump to definition from a call site |
| `lsp_references(file_uri, line, character)` | Find all usages of a symbol |
| `lsp_implementation(file_uri, line, character)` | Find all implementations of an interface |
| `lsp_hover(file_uri, line, character)` | Get type signature + docs at a position |
| `lsp_document_symbols(file_uri)` | List all symbols in a file with line numbers |
| `refresh_file_summary(file_path)` | Force-update a stale file summary |

---

## Common Mistakes to Avoid

| Anti-pattern | Why it's bad | Better approach |
|---|---|---|
| Call `get_project_summary`, then `view` every file it mentions | CI results become a warm-up, not a substitute | Use file mentions from the summary to call `get_file_summary` first; only `view` files confirmed relevant |
| Use `glob("**/*.ts")` to discover files | Dumps all paths into context with no signal | Use `search_files("description of what you need")` |
| Use `grep` to find a class name | Returns raw lines with no context | Use `query_symbols(name_query="ClassName")` — returns file path + line number directly |
| `view` an entire large file | Wastes 5,000–20,000 tokens | Get the line with `query_symbols` or `lsp_document_symbols`, then `view` with a line range |
| Call CI tools AND read every file anyway | +281% token overhead (observed) | Trust the summary; only read when the summary is insufficient |

---

## When CI Tools Are Not Enough

Fall back to `view` (with line ranges) when:
- `get_file_summary` says the file is relevant but the summary lacks implementation detail you need to copy
- `semantic_search` returns `is_stale: true` — summaries may be outdated; use `refresh_file_summary` or read directly
- The pattern you need to understand is in an inline anonymous function or closure not captured by symbol indexing
- You need the exact type signature of a complex generic type that's better read directly
