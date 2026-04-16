"""
Network MCP server exposing LSPClient query methods as tools.

Not meant to be run directly — use main.py which handles LSP
initialization/shutdown and then calls run_server().

Endpoint: http://127.0.0.1:6789/mcp  (streamable-http transport)
"""

import os
import sqlite3

from . import LSP
from mcp.server.fastmcp import FastMCP

from .language_analysis import detect_language, Language
from .workspace_indexer import _DB_FILENAME, SYMBOL_KIND_NAMES, canonical_path

# ── MCP server ──────────────────────────────────────────────────────────────
mcp = FastMCP(
    "codebase-insights",
    host="127.0.0.1",
    port=6789,
)

# Set by run_server() before the server starts.
_clients: dict[Language, LSP.LSPClient] | None = None
_root_dir: str | None = None
_semantic_indexer = None  # Optional SemanticIndexer instance


def _require_clients() -> dict[Language, LSP.LSPClient]:
    if _clients is None:
        raise RuntimeError("LSP client not initialised.")
    return _clients


def _to_file_uri(file_uri: str) -> str:
    """Normalise a caller-supplied file identifier to a ``file:///`` URI.

    Accepts:
    - Already-correct URIs: ``file:///G:/foo/bar.ts`` → unchanged
    - URIs with wrong case/encoding: cleaned up
    - Bare Windows absolute paths: ``G:\\foo\\bar.ts`` or ``G:/foo/bar.ts``
      → ``file:///G:/foo/bar.ts``
    - Bare POSIX absolute paths: ``/home/user/foo.py`` → ``file:///home/user/foo.py``
    """
    s = file_uri.strip()
    if s.lower().startswith("file:"):
        # Already a URI — return as-is (LSP server normalises case internally)
        return s
    # Bare filesystem path — use pathlib which correctly handles Windows drive
    # letters and produces exactly three slashes (file:///G:/...).
    # Using "file://" + pathname2url() is wrong on Windows: pathname2url already
    # prepends "///" so the result would be "file://///G:/..." (5 slashes).
    from pathlib import Path
    return Path(s).as_uri()


def _get_client(file_uri: str) -> tuple[LSP.LSPClient | None, dict | None]:
    """Detect language and return the matching LSP client, or an error dict."""
    language = detect_language(file_uri)
    if language is None:
        return None, {"error": f"Could not detect language for file: {file_uri}: Unsupported extension."}
    client = _require_clients().get(language)
    if client is None:
        return None, {"error": f"No LSP client available for language: {language}"}
    return client, None


def _normalize_lsp_uris(obj):
    """Recursively walk an LSP result and replace every ``uri`` value with a
    canonical filesystem path (uppercase drive letter, back-slashes on Windows).

    LSP servers may return ``file:///e:/...``, ``file:///E:/...``, or even
    percent-encoded variants.  Everything comes out as ``E:\\...`` after this.
    """
    if isinstance(obj, dict):
        return {
            k: (canonical_path(v) if k == "uri" and isinstance(v, str) else _normalize_lsp_uris(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_normalize_lsp_uris(item) for item in obj]
    return obj


def _unwrap(result) -> dict:
    """Convert an LSP result into a plain dict response, normalizing any URIs."""
    if not result.success:
        return {"error": result.error}
    return {"result": _normalize_lsp_uris(result.result)}


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.resource("configurations://current-root-uri")
def current_root_uri() -> dict:
    """Get the current root URI of the LSP client, or an error if not initialized."""
    if _root_dir is None:
        return "Error: Codebase-Insights not initialized."
    return _root_dir

@mcp.tool()
def languages_in_codebase() -> dict:
    """Get the set of detected languages in the codebase, or an error if not initialized."""
    clients = _require_clients()
    languages = [lang.value for lang in clients.keys()]
    return {"result": languages}

@mcp.tool()
def lsp_capabilities() -> dict:
    """Get the LSP capabilities of the initialized clients, or an error if not initialized."""
    clients = _require_clients()
    capabilities = {
        lang.value: vars(client.LSP_init_result) if client.LSP_init_result is not None else None
        for lang, client in clients.items()
    }
    return {"result": capabilities}

@mcp.tool()
def lsp_hover(file_uri: str, line: int, character: int) -> dict:
    """Get hover information (docs / type) at a position in a file.

    Args:
        file_uri: File URI or bare filesystem path, e.g. "file:///E:/my-project/src/main.py" or "E:\\my-project\\src\\main.py".
        line: Zero-based line number.
        character: Zero-based character offset on the line.
    """
    file_uri = _to_file_uri(file_uri)
    client, err = _get_client(file_uri)
    if err:
        return err
    return _unwrap(client.hover(
        text_document={"uri": file_uri},
        position={"line": line, "character": character},
    ))


@mcp.tool()
def lsp_definition(file_uri: str, line: int, character: int) -> dict:
    """Go to the definition of the symbol at the given position.

    Args:
        file_uri: File URI or bare filesystem path, e.g. "file:///E:/my-project/src/main.py" or "E:\\my-project\\src\\main.py".
        line: Zero-based line number.
        character: Zero-based character offset on the line.
    """
    file_uri = _to_file_uri(file_uri)
    client, err = _get_client(file_uri)
    if err:
        return err
    return _unwrap(client.definition(
        text_document={"uri": file_uri},
        position={"line": line, "character": character},
    ))


@mcp.tool()
def lsp_declaration(file_uri: str, line: int, character: int) -> dict:
    """Go to the declaration of the symbol at the given position.

    Args:
        file_uri: File URI or bare filesystem path, e.g. "file:///E:/my-project/src/main.py" or "E:\\my-project\\src\\main.py".
        line: Zero-based line number.
        character: Zero-based character offset on the line.
    """
    file_uri = _to_file_uri(file_uri)
    client, err = _get_client(file_uri)
    if err:
        return err
    return _unwrap(client.declaration(
        text_document={"uri": file_uri},
        position={"line": line, "character": character},
    ))


@mcp.tool()
def lsp_implementation(file_uri: str, line: int, character: int) -> dict:
    """Find implementations of the symbol at the given position.

    Args:
        file_uri: File URI or bare filesystem path, e.g. "file:///E:/my-project/src/main.py" or "E:\\my-project\\src\\main.py".
        line: Zero-based line number.
        character: Zero-based character offset on the line.
    """
    file_uri = _to_file_uri(file_uri)
    client, err = _get_client(file_uri)
    if err:
        return err
    return _unwrap(client.implementation(
        text_document={"uri": file_uri},
        position={"line": line, "character": character},
    ))


@mcp.tool()
def lsp_references(file_uri: str, line: int, character: int) -> dict:
    """Find all references to the symbol at the given position.

    Args:
        file_uri: File URI or bare filesystem path, e.g. "file:///E:/my-project/src/main.py" or "E:\\my-project\\src\\main.py".
        line: Zero-based line number.
        character: Zero-based character offset on the line.
    """
    file_uri = _to_file_uri(file_uri)
    client, err = _get_client(file_uri)
    if err:
        return err
    result = client.references(
        text_document={"uri": file_uri},
        position={"line": line, "character": character},
    )
    if not result.success:
        err_msg = (result.error or {}).get("message", "unknown error")
        if "timed out" in err_msg:
            return {
                "error": err_msg,
                "hint": (
                    "The reference scan timed out — this is common for widely-used symbols "
                    "in large Rust/TypeScript codebases. Suggestions: "
                    "(1) use query_symbols with a name_query filter to find definition location, "
                    "(2) narrow the search by opening the file in the editor and using "
                    "lsp_definition first to confirm the exact definition position, "
                    "(3) search for the symbol name as a string using grep for a faster overview."
                ),
            }
        return {"error": result.error}
    refs = result.result or []
    return {"result": _normalize_lsp_uris(refs), "count": len(refs)}


@mcp.tool()
def lsp_document_symbols(file_uri: str) -> dict:
    """List all symbols (functions, classes, variables, …) in a file.
    Note: this will return !ALL! the symbols in the file, so can be expensive for large files. Use with care. For more targeted queries use `query_symbols` or `semantic_search` instead.

    Args:
        file_uri: File URI or bare filesystem path, e.g. "file:///E:/my-project/src/main.py" or "E:\\my-project\\src\\main.py".
    """
    file_uri = _to_file_uri(file_uri)
    client, err = _get_client(file_uri)
    if err:
        return err
    return _unwrap(client.document_symbols(
        text_document={"uri": file_uri},
    ))


@mcp.tool()
def query_symbols(
    path: str | None = None,
    kinds: list[str] | None = None,
    name_query: str | None = None,
    limit: int = 200,
) -> dict:
    """Query the workspace symbol index stored in the SQLite database.

    Args:
        path: Optional absolute path to a file or directory to restrict results to.
              If omitted the entire workspace is searched.
        kinds: Optional list of symbol kind labels to include, e.g.
               ["Function", "Class", "Variable"]. Case-insensitive.
               When omitted (or empty) all kinds are returned.
               Valid values: File, Module, Namespace, Package, Class, Method,
               Property, Field, Constructor, Enum, Interface, Function,
               Variable, Constant, String, Number, Boolean, Array, Object,
               Key, Null, EnumMember, Struct, Event, Operator, TypeParameter.
        name_query: Optional substring / fuzzy filter applied to symbol names
                    (case-insensitive). Supports SQL LIKE wildcards (% and _).
                    If the string contains no wildcards a surrounding %…%
                    match is applied automatically.
        limit: Maximum number of symbols to return (default 200, max 1000).
    """
    if _root_dir is None:
        return {"error": "Codebase-Insights not initialized."}

    db_path = os.path.join(_root_dir, _DB_FILENAME)
    if not os.path.isfile(db_path):
        return {"error": "Symbol index not found. The indexer may still be running."}

    limit = min(max(1, limit), 1000)

    # ── Build query ──────────────────────────────────────────────────────────
    conditions: list[str] = []
    params: list = []

    # Path filter: prefix-match on file_path
    if path:
        norm = canonical_path(path)
        if os.path.isfile(norm):
            conditions.append("s.file_path = ?")
            params.append(norm)
        else:
            # Directory: match anything under it.
            # We use '!' as the ESCAPE character (not '\') for cross-platform
            # safety: on Windows, os.sep is '\', which SQLite would consume as
            # an escape prefix if we used ESCAPE '\', corrupting the pattern.
            # On Linux/macOS, os.sep is '/' and '\' is safe, but '!' works there
            # too and keeps the code uniform. '!' rarely appears in real paths;
            # even if it does, the "!!" doubling handles it correctly.
            prefix = norm.rstrip(os.sep) + os.sep
            safe = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            conditions.append("s.file_path LIKE ? ESCAPE '!'")
            params.append(safe + "%")

    # Kind filter (case-insensitive label match)
    if kinds:
        normalised = {k.strip().capitalize() for k in kinds}
        # Keep only valid kind labels
        valid_labels = set(SYMBOL_KIND_NAMES.values())
        matched = normalised & valid_labels
        if not matched:
            return {"error": f"No valid symbol kinds in: {list(normalised)}. "
                             f"Valid values: {sorted(valid_labels)}"}
        placeholders = ",".join("?" * len(matched))
        conditions.append(f"s.kind_label IN ({placeholders})")
        params.extend(sorted(matched))

    # Name fuzzy filter
    if name_query:
        if "%" not in name_query and "_" not in name_query:
            pattern = f"%{name_query}%"
        else:
            pattern = name_query
        conditions.append("s.name LIKE ? ESCAPE '\\'")
        params.append(pattern)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT s.id, s.name, s.kind_label, s.container_name,
               s.file_path, s.def_line, s.def_character, s.def_end_line,
               ss.summary
        FROM symbols s
        LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
        {where}
        ORDER BY s.file_path, s.def_line
        LIMIT ?
    """
    params.append(limit)

    try:
        con = sqlite3.connect(db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()

        # Fetch min_ref_count from semantic indexer for status reporting
        min_ref = getattr(_semantic_indexer, "_min_ref_count", None)
        eligible_kinds = getattr(_semantic_indexer, "_eligible_kinds", None)

        # Fetch references per symbol
        results = []
        for row in rows:
            refs = con.execute(
                "SELECT file_path, line, character FROM symbol_refs WHERE symbol_id=?",
                (row["id"],),
            ).fetchall()
            # Explain why summary is null
            if row["summary"] is None:
                if eligible_kinds is not None and row["kind_label"] not in eligible_kinds:
                    summary_status = f"kind_not_indexed — '{row['kind_label']}' is not in semantic index_kinds"
                elif min_ref is not None and min_ref > 0:
                    ref_count = len(refs)
                    if ref_count < min_ref:
                        summary_status = (
                            f"below_ref_threshold — {ref_count} reference(s), "
                            f"min_ref_count={min_ref}"
                        )
                    else:
                        summary_status = "pending — eligible but not yet summarised (indexer may still be running)"
                else:
                    summary_status = "pending — not yet summarised"
            else:
                summary_status = "available"
            results.append({
                "name":           row["name"],
                "kind":           row["kind_label"],
                "container":      row["container_name"],
                "file":           row["file_path"],
                "definition":     {"line": row["def_line"], "character": row["def_character"], "end_line": row["def_end_line"]},
                "summary":        row["summary"],
                "summary_status": summary_status,
                "references":     [
                    {"file": r["file_path"], "line": r["line"], "character": r["character"]}
                    for r in refs
                ],
            })

        # Diagnostic: if no results, explain why
        diagnostic: str | None = None
        if not results:
            if path:
                norm_path = canonical_path(path)
                if os.path.isdir(norm_path):
                    prefix = norm_path.rstrip(os.sep) + os.sep
                    safe = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                    indexed_count = con.execute(
                        "SELECT COUNT(*) FROM file_hashes WHERE file_path LIKE ? ESCAPE '!'",
                        (safe + "%",),
                    ).fetchone()[0]
                else:
                    indexed_count = con.execute(
                        "SELECT COUNT(*) FROM file_hashes WHERE file_path = ?",
                        (norm_path,),
                    ).fetchone()[0]
                if indexed_count == 0:
                    diagnostic = (
                        f"No indexed files found under '{norm_path}'. "
                        "The path may not have been indexed yet, may be excluded by "
                        ".gitignore, or the indexer may still be running."
                    )
                else:
                    diagnostic = (
                        f"{indexed_count} file(s) indexed under that path, "
                        "but no symbols matched the given filters."
                    )
            else:
                total = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
                if total == 0:
                    diagnostic = "Symbol index is empty — the indexer may still be running."
        con.close()
    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}

    response: dict = {"result": results, "count": len(results)}
    if diagnostic:
        response["diagnostic"] = diagnostic
    return response


@mcp.tool()
def get_symbol_summary(
    name: str,
    file_uri: str,
    line: int,
    character: int,
) -> dict:
    """Get the AI-generated natural language summary for a specific symbol.

    Accepts either a definition site or a call/reference site — both work.
    The symbol name is required to disambiguate when multiple symbols share
    the same position (e.g. nested decorators) or the same line.

    Note that the summary will only be available for symbols that met the criteria to be indexed by the AI indexer, so it might not be available for all symbols. To know more about the criteria used for indexing, use the `get_indexer_criteria` tool.

    Args:
        name:      Exact symbol name (case-sensitive).
        file_uri:  File URI, e.g. "file:///E:/my-project/src/main.py".
        line:      Zero-based line number of the position.
        character: Zero-based character offset on the line.
    """
    if _root_dir is None:
        return {"error": "Codebase-Insights not initialized."}

    db_path = os.path.join(_root_dir, _DB_FILENAME)
    if not os.path.isfile(db_path):
        return {"error": "Symbol index not found. The indexer may still be running."}

    # Convert URI to canonical normalised path
    file_path = canonical_path(file_uri)

    try:
        con = sqlite3.connect(db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row

        # Character tolerance: ±len(name) to handle cursors anywhere within
        # the token, and different LSP servers reporting different offsets.
        tol = len(name)
        char_lo, char_hi = max(0, character - tol), character + tol
        # Line tolerance: ±3 to handle Rust/Python attribute decorators and
        # off-by-one differences between LSP servers (rust-analyzer reports
        # selectionRange at the fn keyword, which may be 1-3 lines below the
        # outermost #[attribute]).
        line_lo, line_hi = max(0, line - 3), line + 3

        # ── 1. Try definition position (with line tolerance) ─────────────────
        row = con.execute(
            """
            SELECT s.name, s.kind_label, s.container_name, s.file_path,
                   s.def_line, s.def_character, s.def_end_line, ss.summary
            FROM symbols s
            LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
            WHERE s.name = ?
              AND s.file_path = ?
              AND s.def_line BETWEEN ? AND ?
              AND s.def_character BETWEEN ? AND ?
            ORDER BY ABS(s.def_line - ?), ABS(s.def_character - ?)
            LIMIT 1
            """,
            (name, file_path, line_lo, line_hi, char_lo, char_hi, line, character),
        ).fetchone()

        # ── 2. Fallback: look up reference site ──────────────────────────────
        if row is None:
            row = con.execute(
                """
                SELECT s.name, s.kind_label, s.container_name, s.file_path,
                       s.def_line, s.def_character, s.def_end_line, ss.summary
                FROM symbol_refs r
                JOIN symbols s ON s.id = r.symbol_id
                LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                WHERE s.name = ?
                  AND r.file_path = ?
                  AND r.line BETWEEN ? AND ?
                  AND r.character BETWEEN ? AND ?
                ORDER BY ABS(r.line - ?), ABS(r.character - ?)
                LIMIT 1
                """,
                (name, file_path, line_lo, line_hi, char_lo, char_hi, line, character),
            ).fetchone()

        con.close()
    except sqlite3.Error as e:
        return {"error": f"Database error: {e}"}

    if row is None:
        return {"error": f"Symbol '{name}' not found near {file_uri}:{line}:{character} (searched lines {max(0,line-3)}-{line+3})"}

    result = {
        "name":       row["name"],
        "kind":       row["kind_label"],
        "container":  row["container_name"],
        "file":       row["file_path"],
        "definition": {"line": row["def_line"], "character": row["def_character"], "end_line": row["def_end_line"]},
        "summary":    row["summary"],
    }
    if row["summary"] is None:
        result["summary_status"] = (
            "indexed_but_no_summary — symbol was indexed but did not meet the semantic "
            "indexer criteria (check min_ref_count and index_kinds in .codebase-insights.toml, "
            "or use get_indexer_criteria tool)"
        )
    return {"result": result}


@mcp.tool()
def get_indexer_criteria() -> dict:
    """Get the criteria used by the AI indexer to determine which symbols are indexed.

    Returns information about symbol significance thresholds and filtering rules
    applied during semantic indexing.
    """
    if _semantic_indexer is None:
        return {"error": "Semantic indexing is not available. "
                         "Ensure an LLM provider (Ollama / OpenAI) is configured."}
    cfg = _semantic_indexer
    return {"result": {
        "eligible_kinds":  sorted(getattr(cfg, "_eligible_kinds", [])),
        "min_ref_count":   getattr(cfg, "_min_ref_count", None),
        "batch_size":      getattr(cfg, "_batch_size", None),
        "concurrency":     getattr(cfg, "_concurrency", None),
        "note": (
            "Symbols must have kind in eligible_kinds AND at least min_ref_count "
            "references to receive an AI summary. Symbols below this threshold will "
            "appear in query_symbols with summary=null and summary_status explaining why."
        ),
    }}


@mcp.tool()
def semantic_search(
    query: str,
    limit: int = 10,
    kinds: list[str] | None = None,
) -> dict:
    """Search symbols by natural language description using AI-generated summaries.

    This performs a semantic / vector-similarity search over symbol summaries
    that were generated by the AI indexer.  Use it when you want to find
    symbols by *meaning* rather than name (e.g. "function that parses JSON").

    Note that the AI indexer will only perform indexing on symbols which had met certain criteria to be significant enough, so the result might not be complete. To know more about the criteria used for indexing, use the `get_indexer_criteria` tool.

    Args:
        query:  Natural language description of what you are looking for.
        limit:  Maximum results to return (default 10, max 100).
        kinds:  Optional list of symbol kind labels to restrict the search,
                e.g. ["Function", "Class"]. Same values as query_symbols.
    """
    if _semantic_indexer is None:
        return {"error": "Semantic indexing is not available. "
                         "Ensure an LLM provider (Ollama / OpenAI) is configured."}
    return _semantic_indexer.search(query=query, limit=limit, kinds=kinds)


@mcp.tool()
def search_files(
    query: str,
    limit: int = 10,
) -> dict:
    """Search source files by natural language description using AI-generated file summaries.

    Use this when you want to find *which files* are relevant to a topic rather than
    individual symbols.  For example, "file that handles authentication", "module
    responsible for database migrations", or "code that parses config files".

    Each result includes the file path, its relative path, and a short summary of
    its primary responsibility.

    Args:
        query:  Natural language description of the kind of file you are looking for.
        limit:  Maximum results to return (default 10, max 100).
    """
    if _semantic_indexer is None:
        return {"error": "Semantic indexing is not available. "
                         "Ensure an LLM provider (Ollama / OpenAI) is configured."}
    return _semantic_indexer.search_files(query=query, limit=limit)


@mcp.tool()
def get_file_summary(file_path: str) -> dict:
    """Get the AI-generated summary for a specific file.

    Returns a concise description of the file's primary responsibility,
    key classes/functions it exposes, and what it depends on.
    Summaries are updated automatically when the file's symbol structure changes.

    Args:
        file_path: Absolute path to the file, e.g. "E:/my-project/src/main.py".
    """
    if _semantic_indexer is None:
        return {"error": "Semantic indexing is not available. "
                         "Ensure an LLM provider (Ollama / OpenAI) is configured."}
    return _semantic_indexer.get_file_summary(file_path)


@mcp.tool()
def get_project_summary() -> dict:
    """Get the AI-generated structural overview of the entire codebase.

    Returns a structured summary containing:
    - Architecture Overview: overall system purpose and design
    - File Responsibilities: one-line description per file
    - Data Flow: how data moves through the system end-to-end
    - Extension Points: where to make changes for common tasks

    Use this as your first call when starting to reason about where to implement
    a feature or what code to modify.
    """
    if _semantic_indexer is None:
        return {"error": "Semantic indexing is not available. "
                         "Ensure an LLM provider (Ollama / OpenAI) is configured."}
    return _semantic_indexer.get_project_summary()


# ── Entry point ──────────────────────────────────────────────────────────────

def run_server(clients: dict[Language, LSP.LSPClient], root_dir: str, semantic_indexer=None, host: str = "127.0.0.1", port: int = 6789) -> None:
    """Set the shared LSP client and start the MCP HTTP server (blocking)."""
    global _clients, _root_dir, _semantic_indexer
    _clients = clients
    _root_dir = root_dir
    _semantic_indexer = semantic_indexer
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.run(transport="streamable-http")
