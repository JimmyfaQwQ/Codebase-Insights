# Agent Usage Guide

This guide explains how to use Codebase Insights effectively from agentic coding tools such as GitHub Copilot, Claude Code, and Cursor.

## Core idea

The biggest mistake is treating Codebase Insights as an orientation layer and then browsing the repository normally anyway.

The better pattern is:

1. Call `get_project_summary()` before opening files.
2. Prefer `search_files()`, `semantic_search()`, and `query_symbols()` over globbing or grep-style exploration.
3. Call `get_file_summary()` before reading any file.
4. Read raw source only when the summary confirms relevance and exact implementation detail is still needed.
5. Use structural LSP tools once you have the right symbol.

That pattern works across Copilot, Claude Code, and Cursor even though their UX differs.

## What to put in your instructions

Your prompt or project instructions should make three things explicit:

- Codebase Insights is the default routing layer for code discovery.
- Broad file browsing is a fallback, not the first move.
- Success means solving the task with as few raw file reads as practical.

You can use the reusable template in [codebase-insights-instructions.md](../codebase-insights-instructions.md) directly, or adapt it into your system prompt, repo instructions, or workspace rules.

## Notes by tool

### GitHub Copilot

Copilot tends to follow workflow constraints well when they are stated as ordered steps. Give it a clear sequence such as project summary first, search second, file summary gate third, source read last.

### Claude Code

Claude Code is usually strong at using semantic tools, but it will still over-read if the prompt is vague. Be explicit that Codebase Insights should replace broad browsing, not supplement it.

### Cursor

Cursor often has stronger built-in file navigation habits, so the instruction has to counter that default. Tell it to avoid opening files until Codebase Insights has already narrowed the candidate set.

## Short instruction block

```text
Use Codebase Insights as the default discovery layer for this repo. Start with get_project_summary(), then use search_files(), semantic_search(), and query_symbols() to narrow the target. Before opening any file, call get_file_summary(). Only read raw source when the summary confirms relevance and exact implementation details are still needed. Prefer LSP navigation tools over broad browsing once the correct symbol is identified.
```