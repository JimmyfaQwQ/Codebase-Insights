"""
workspace_indexer.py

Background workspace symbol indexer that:
  - Starts after all LSP servers are initialized
  - Walks the workspace, indexes symbols via LSP documentSymbol + references requests
  - Stores each symbol (name, kind, file, definition location, references) in a SQLite DB
  - Saves a SHA-256 hash per file and skips unchanged files on subsequent runs
  - Auto-appends the DB filename to .gitignore (if .gitignore exists)
  - Watches for file-system changes via watchdog and re-indexes affected files,
    respecting all .gitignore patterns
"""

import hashlib
import os
import re
import sqlite3
import sys
import threading
import time
from enum import Enum, auto
from typing import Optional
from urllib.parse import unquote

from tqdm import tqdm

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from . import language_analysis
from . import LSP

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB_FILENAME = ".codebase-index.db"

# Symbols whose names match this pattern are LSP artefacts (anonymous arrow
# functions, callback wrappers, dot-prefixed method chains, etc.) and carry
# no useful semantic signal.  They are filtered out before being stored so
# they never appear in query_symbols or semantic search results.
_ANON_NAME_RE: re.Pattern = re.compile(
    r"^(\$\d*|_+\d*|<[^>]*>|anonymous|\d+|callback|handler|wrapper)$"
    r"|.*\s+callback$"
    r"|^\.",
    re.IGNORECASE,
)


SYMBOL_KIND_NAMES: dict[int, str] = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS file_hashes (
    file_path  TEXT PRIMARY KEY,
    hash       TEXT NOT NULL,
    indexed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL,
    kind           INTEGER NOT NULL,
    kind_label     TEXT    NOT NULL,
    container_name TEXT,
    file_path      TEXT    NOT NULL,
    def_line       INTEGER NOT NULL,
    def_character  INTEGER NOT NULL,
    def_end_line   INTEGER NOT NULL DEFAULT -1
);

CREATE TABLE IF NOT EXISTS symbol_refs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol_id INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    file_path TEXT    NOT NULL,
    line      INTEGER NOT NULL,
    character INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_symbols_file   ON symbols(file_path);
CREATE INDEX IF NOT EXISTS idx_symbols_name   ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_refs_symbol_id ON symbol_refs(symbol_id);
CREATE INDEX IF NOT EXISTS idx_refs_file      ON symbol_refs(file_path);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _IndexResult(Enum):
    INDEXED = auto()    # file was (re)indexed
    SKIPPED = auto()    # file hash unchanged — skipped
    ERROR   = auto()    # indexing failed (LSP error / IO error)


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_path(path: str) -> str:
    """Return a normalised, canonical absolute file system path.

    Accepts any of the common representations seen in the wild on Windows:
    - Raw paths with back- or forward-slashes, any drive-letter casing
    - ``file:///E:/...`` and ``file://e%3A/...`` (percent-encoded) URIs
    - ``file://E:/...`` (two-slash Windows variant)
    """
    if path.startswith("file:"):
        # Strip scheme and any number of leading slashes
        stripped = path[5:].lstrip("/")
        # Decode percent-encoding (%3A -> :, %5C -> \, %20 -> space, …)
        stripped = unquote(stripped)
        # On POSIX, absolute paths start with '/' — restore the leading slash
        # that lstrip removed (Windows paths begin with a drive letter, not '/').
        if os.name != "nt":
            stripped = "/" + stripped
        path = stripped
    # Normalise slashes to OS separator
    path = path.replace("/", os.sep).replace("\\", os.sep)
    path = os.path.normpath(path)
    # On Windows: normalise drive letter to uppercase (e: -> E:)
    if os.name == "nt" and len(path) >= 2 and path[1] == ":":
        path = path[0].upper() + path[1:]
    return path


def _path_to_uri(file_path: str) -> str:
    """Convert an absolute filesystem path to a ``file:///`` URI."""
    norm = canonical_path(file_path).replace("\\", "/")
    # Unix paths already start with '/'; Windows drive paths do not.
    if norm.startswith("/"):
        return f"file://{norm}"
    return f"file:///{norm}"


def _uri_to_path(uri: str) -> str:
    """Convert a ``file:///`` URI back to a canonical local filesystem path."""
    return canonical_path(uri)


def _flatten_symbols(symbols: list, file_path: str, parent_name: Optional[str] = None) -> list[dict]:
    """Recursively flatten a hierarchical LSP DocumentSymbol list.

    Handles both response shapes:
    - ``DocumentSymbol[]``  — has ``range`` / ``selectionRange`` / ``children``
    - ``SymbolInformation[]`` — has ``location.range`` / ``containerName`` (no children)
    """
    result = []
    for sym in symbols or []:
        loc = sym.get("location", {})
        if loc:
            # SymbolInformation format (older protocol / some Python LSPs)
            sel = loc.get("range", {})
            full_range = sel  # no separate full range in this format
        else:
            # DocumentSymbol format
            sel = sym.get("selectionRange", sym.get("range", {}))
            full_range = sym.get("range", {})
        start = sel.get("start", {})
        full_range_end = full_range.get("end", {})
        container = sym.get("containerName") or parent_name
        kind = sym.get("kind", 0)
        result.append({
            "name":           sym.get("name", ""),
            "kind":           kind,
            "kind_label":     SYMBOL_KIND_NAMES.get(kind, str(kind)),
            "container_name": container,
            "file_path":      file_path,
            "def_line":       start.get("line", 0),
            "def_character":  start.get("character", 0),
            "def_end_line":   full_range_end.get("line", -1),
        })
        result.extend(_flatten_symbols(sym.get("children", []), file_path, sym.get("name")))
    return result


# ---------------------------------------------------------------------------
# Gitignore filter
# ---------------------------------------------------------------------------

class _GitignoreFilter:
    """
    Parses all .gitignore files under root_dir and decides whether a given
    path should be ignored.

    Initialisation is **lazy**: the filter starts empty and is progressively
    populated by ``scan_directory()`` calls during the workspace walk.  This
    avoids an expensive upfront ``os.walk`` over the entire tree (which
    previously included huge ignored directories like ``node_modules/``).

    Call ``reload()`` to do a full re-scan (with pruning) — this is only
    needed when a ``.gitignore`` file itself changes at runtime.
    """

    def __init__(self, root_dir: str):
        self._root_dir = root_dir
        self._ignored_paths: set[str] = set()
        self._ignored_names: set[str] = set()
        self._lock = threading.Lock()
        # Intentionally NOT calling reload() here.  The filter will be
        # populated lazily via scan_directory() during _iter_workspace_files.

    def scan_directory(self, dirpath: str, filenames: list[str]):
        """Parse ``.gitignore`` in *dirpath* (if present) and add its rules.

        This is meant to be called for each directory during the workspace
        walk so that ignore rules are discovered progressively.
        """
        if ".gitignore" not in filenames:
            return
        paths: set[str] = set()
        names: set[str] = set()
        language_analysis._parse_gitignore(
            os.path.join(dirpath, ".gitignore"),
            dirpath,
            paths,
            names,
        )
        with self._lock:
            self._ignored_paths.update(paths)
            self._ignored_names.update(names)

    def reload(self):
        """Full re-scan with directory pruning (much faster than walking everything)."""
        paths: set[str] = set()
        names: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(self._root_dir, topdown=True):
            if ".gitignore" in filenames:
                language_analysis._parse_gitignore(
                    os.path.join(dirpath, ".gitignore"),
                    dirpath,
                    paths,
                    names,
                )
            # Prune directories already known to be ignored so we never
            # descend into e.g. node_modules/ or .git/
            dirnames[:] = [
                d for d in dirnames
                if os.path.normpath(os.path.join(dirpath, d)) not in paths
                and d not in names
            ]
        with self._lock:
            self._ignored_paths = paths
            self._ignored_names = names

    def is_ignored(self, path: str) -> bool:
        norm = os.path.normpath(path)
        with self._lock:
            if norm in self._ignored_paths:
                return True
            # Check every path component (not just the basename) against name patterns.
            # This catches files inside ignored directories like node_modules/.
            parts = norm.replace("\\", "/").split("/")
            return any(part in self._ignored_names for part in parts)


# ---------------------------------------------------------------------------
# WorkspaceIndexer
# ---------------------------------------------------------------------------

class WorkspaceIndexer:
    """
    Indexes workspace symbols into a SQLite database and keeps it up-to-date
    via a watchdog observer.

    Usage:
        indexer = WorkspaceIndexer(root_dir, lsp_clients)
        indexer.start()          # call after all LSP clients are initialized
        ...
        indexer.stop()           # call during shutdown
    """

    def __init__(
        self,
        root_dir: str,
        lsp_clients: dict[language_analysis.Language, "LSP.LSPClient"],
        semantic_indexer=None,
    ):
        self._root_dir = canonical_path(root_dir)
        self._clients = lsp_clients
        self._db_path = os.path.join(self._root_dir, _DB_FILENAME)
        self._gitignore = _GitignoreFilter(root_dir)
        self._db_lock = threading.Lock()
        self._observer: Optional[Observer] = None
        self._stop_event = threading.Event()
        self._semantic = semantic_indexer
        # Queue of absolute paths that need (re)indexing / removal
        self._queue: list[str] = []
        self._queue_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(self):
        """
        Register the DB in .gitignore if applicable, create the DB schema,
        then start the background indexing thread and the watchdog observer.
        """
        self._maybe_add_to_gitignore()
        self._ensure_schema()
        threading.Thread(
            target=self._run, daemon=True, name="WorkspaceIndexer"
        ).start()

    def clear_index(self):
        """Wipe all indexed data so the next run performs a full re-index."""
        self._maybe_add_to_gitignore()
        self._ensure_schema()  # safe to call multiple times (IF NOT EXISTS)
        with self._db_lock, self._connect() as con:
            con.executescript(
                "DELETE FROM symbols;"
                "DELETE FROM file_hashes;"
            )
            con.commit()
        print("[Indexer] Symbol index cleared – full re-index will run on startup.")

    def stop(self):
        """Signal the background thread and watchdog to stop."""
        self._stop_event.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

    # ------------------------------------------------------------------
    # .gitignore registration
    # ------------------------------------------------------------------

    def _maybe_add_to_gitignore(self):
        gitignore_path = os.path.join(self._root_dir, ".gitignore")
        if not os.path.isfile(gitignore_path):
            return
        with open(gitignore_path, "r", encoding="utf-8") as f:
            contents = f.read()
        _db_entries = [
            _DB_FILENAME, f"{_DB_FILENAME}-shm", f"{_DB_FILENAME}-wal",
            ".codebase-semantic/",
            ".codebase-insights.toml",
        ]
        missing = [e for e in _db_entries if e not in contents]
        if missing:
            prefix = "\n" if contents and not contents.endswith("\n") else ""
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write(f"\n{prefix}# codebase-insights (auto-generated)\n")
                f.write("\n".join(missing) + "\n")
            print(f"[Indexer] Added to .gitignore: {', '.join(missing)}")

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _ensure_schema(self):
        with self._db_lock, self._connect() as con:
            con.executescript(_SCHEMA)
            # Migrate existing DBs that predate def_end_line
            try:
                con.execute("ALTER TABLE symbols ADD COLUMN def_end_line INTEGER NOT NULL DEFAULT -1")
                con.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

    def _get_stored_hash(self, con: sqlite3.Connection, file_path: str) -> Optional[str]:
        row = con.execute(
            "SELECT hash FROM file_hashes WHERE file_path=?", (file_path,)
        ).fetchone()
        return row[0] if row else None

    def _upsert_hash(self, con: sqlite3.Connection, file_path: str, digest: str):
        con.execute(
            "INSERT OR REPLACE INTO file_hashes(file_path, hash, indexed_at) VALUES(?,?,?)",
            (file_path, digest, time.time()),
        )

    def _remove_file(self, con: sqlite3.Connection, file_path: str):
        """Delete all indexed data for a file (symbols cascade-delete their refs)."""
        con.execute("DELETE FROM symbols WHERE file_path=?", (file_path,))
        con.execute("DELETE FROM file_hashes WHERE file_path=?", (file_path,))
        con.commit()

    # ------------------------------------------------------------------
    # Per-file indexing
    # ------------------------------------------------------------------

    def _index_file(self, file_path: str, con: sqlite3.Connection) -> _IndexResult:
        """
        Index a single file.  Skips if the file hash is unchanged.
        Returns an _IndexResult describing what happened.
        """
        lang = language_analysis.detect_language(file_path)
        if lang is None:
            return _IndexResult.SKIPPED

        client = self._clients.get(lang)
        if client is None:
            return _IndexResult.SKIPPED

        # Skip languages whose LSP cannot index reliably (e.g. C/C++
        # without a compilation database).  The client is still available
        # for on-demand MCP queries.
        if not client.supports_indexing:
            return _IndexResult.SKIPPED

        try:
            current_hash = _file_sha256(file_path)
        except OSError as e:
            print(f"[Indexer] Cannot read {file_path}: {e}")
            return _IndexResult.ERROR

        if self._get_stored_hash(con, file_path) == current_hash:
            return _IndexResult.SKIPPED

        # Remove stale entries before re-indexing
        self._remove_file(con, file_path)

        uri = _path_to_uri(file_path)
        sym_result = client.document_symbols({"uri": uri})
        if not sym_result.success or sym_result.result is None:
            # Record the hash anyway so we don't retry every run
            self._upsert_hash(con, file_path, current_hash)
            con.commit()
            return _IndexResult.ERROR

        flat_symbols = _flatten_symbols(sym_result.result, file_path)
        flat_symbols = [s for s in flat_symbols if not _ANON_NAME_RE.search(s["name"])]

        # Only request references if the LSP can reliably resolve them.
        # For C/C++ without a compilation database this is always False,
        # avoiding minutes of pointless timeouts per file.
        collect_refs = client.references_reliable

        for sym in flat_symbols:
            con.execute(
                """INSERT INTO symbols
                       (name, kind, kind_label, container_name, file_path, def_line, def_character, def_end_line)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    sym["name"], sym["kind"], sym["kind_label"],
                    sym["container_name"], sym["file_path"],
                    sym["def_line"], sym["def_character"], sym["def_end_line"],
                ),
            )
            sym_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

            if not collect_refs:
                continue

            refs_result = client.references(
                {"uri": uri},
                {"line": sym["def_line"], "character": sym["def_character"]},
            )
            if refs_result.success and refs_result.result:
                con.executemany(
                    "INSERT INTO symbol_refs(symbol_id, file_path, line, character) VALUES(?,?,?,?)",
                    [
                        (
                            sym_id,
                            _uri_to_path(ref.get("uri", "")),
                            ref.get("range", {}).get("start", {}).get("line", 0),
                            ref.get("range", {}).get("start", {}).get("character", 0),
                        )
                        for ref in refs_result.result
                    ],
                )

        self._upsert_hash(con, file_path, current_hash)
        con.commit()
        return _IndexResult.INDEXED

    # ------------------------------------------------------------------
    # Background run loop
    # ------------------------------------------------------------------

    def _iter_workspace_files(self):
        """Yield normalized absolute paths for all indexable files.

        This also **progressively populates** the gitignore filter by calling
        ``scan_directory()`` for each directory we enter, so that ignore rules
        are available before we decide which sub-directories to descend into.
        """
        for dirpath, dirnames, filenames in os.walk(self._root_dir, topdown=True):
            # Parse .gitignore in this dir first (populates the filter lazily)
            self._gitignore.scan_directory(dirpath, filenames)
            dirnames[:] = [
                d for d in dirnames
                if not self._gitignore.is_ignored(os.path.join(dirpath, d))
            ]
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                if not self._gitignore.is_ignored(full):
                    if language_analysis.detect_language(full) is not None:
                        yield canonical_path(full)

    def _initial_pass(self):
        """Full workspace scan performed once at startup."""
        print("[Indexer] Collecting workspace files...")
        all_files = list(self._iter_workspace_files())
        indexed = skipped = errors = 0
        _bm_t_pass_start = time.perf_counter()
        with self._db_lock, self._connect() as con:
            with tqdm(
                all_files,
                desc="[Indexer] Indexing",
                unit="file",
                dynamic_ncols=True,
                postfix={"indexed": 0, "skipped": 0, "errors": 0},
            ) as bar:
                for file_path in bar:
                    if self._stop_event.is_set():
                        break
                    result = self._index_file(file_path, con)
                    if result is _IndexResult.INDEXED:
                        indexed += 1
                    elif result is _IndexResult.SKIPPED:
                        skipped += 1
                    else:
                        errors += 1
                    bar.set_postfix(indexed=indexed, skipped=skipped, errors=errors)
            # Count persisted symbols and refs for benchmark output
            _bm_total_symbols = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            _bm_total_refs = con.execute("SELECT COUNT(*) FROM symbol_refs").fetchone()[0]
        _bm_t_pass_end = time.perf_counter()
        print(
            f"[Indexer] Initial pass complete. "
            f"Indexed: {indexed}, Skipped (unchanged): {skipped}, Errors: {errors}"
        )
        print(
            f"[BENCHMARK:INDEXER] wall_time={_bm_t_pass_end - _bm_t_pass_start:.2f}s "
            f"files_total={len(all_files)} indexed={indexed} skipped={skipped} errors={errors} "
            f"total_symbols={_bm_total_symbols} total_refs={_bm_total_refs}",
            flush=True,
        )

        # Trigger semantic indexing for all pending symbols
        if self._semantic is not None:
            threading.Thread(
                target=self._semantic.index_symbols,
                daemon=True,
                name="SemanticIndexer-initial",
            ).start()

    def _drain_watchdog_queue(self):
        """Process files queued by the watchdog observer in a tight loop."""
        while not self._stop_event.is_set():
            with self._queue_lock:
                paths, self._queue = list(self._queue), []

            if paths:
                changed_files: list[str] = []
                removed_files: list[str] = []
                with self._db_lock, self._connect() as con:
                    for p in paths:
                        if os.path.isfile(p):
                            result = self._index_file(p, con)
                            label = result.name.lower()
                            if result is _IndexResult.INDEXED:
                                changed_files.append(p)
                        else:
                            self._remove_file(con, p)
                            removed_files.append(p)
                            label = "removed"
                        print(f"[Indexer] {label.capitalize()}: {p}")

                # Trigger incremental semantic indexing for changed files
                if self._semantic is not None:
                    for fp in removed_files:
                        self._semantic.remove_file(fp)
                    if changed_files:
                        sym_ids = self._get_symbol_ids_for_files(changed_files)
                        if sym_ids:
                            threading.Thread(
                                target=self._semantic.index_symbols,
                                args=(sym_ids,),
                                kwargs={"debounce": True},
                                daemon=True,
                                name="SemanticIndexer-incremental",
                            ).start()

            time.sleep(1)

    def _get_symbol_ids_for_files(self, file_paths: list[str]) -> list[int]:
        """Look up symbol IDs for a list of file paths."""
        if not file_paths:
            return []
        placeholders = ",".join("?" * len(file_paths))
        with self._db_lock, self._connect() as con:
            rows = con.execute(
                f"SELECT id FROM symbols WHERE file_path IN ({placeholders})",
                file_paths,
            ).fetchall()
        return [r[0] for r in rows]

    def _run(self):
        """Entry point for the background indexer thread."""
        self._initial_pass()
        self._start_watchdog()
        self._drain_watchdog_queue()   # blocks until _stop_event is set

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _start_watchdog(self):
        handler = _ChangeHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, self._root_dir, recursive=True)
        self._observer.start()
        print(f"[Indexer] Watching '{self._root_dir}' for changes...")

    def _enqueue(self, path: str):
        """Enqueue a path for re-indexing (or removal if deleted), honoring .gitignore."""
        norm = canonical_path(path)
        # Reload gitignore if a .gitignore itself changed so future checks are fresh
        if os.path.basename(norm) == ".gitignore":
            self._gitignore.reload()
        if self._gitignore.is_ignored(norm):
            return
        if language_analysis.detect_language(norm) is None:
            return
        with self._queue_lock:
            if norm not in self._queue:
                self._queue.append(norm)


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------

class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, indexer: WorkspaceIndexer):
        self._indexer = indexer

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._indexer._enqueue(event.src_path)

    def on_created(self, event: FileSystemEvent):
        if not event.is_directory:
            self._indexer._enqueue(event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory:
            self._indexer._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent):
        if not event.is_directory:
            # Old path → remove, new path → (re)index
            self._indexer._enqueue(event.src_path)
            self._indexer._enqueue(event.dest_path)
