"""
semantic_indexer.py

AI-powered semantic indexing layer that:
  - Reads symbols from the existing SQLite index
  - Extracts source code context around each symbol definition
  - Generates a natural-language summary via LLM (Ollama / OpenAI)
  - Stores the summary embedding in ChromaDB for vector similarity search
  - Tracks which symbols have been summarised in a SQLite table
  - Supports incremental updates: only processes new/changed symbols
"""

import hashlib
import math
import os
import re
import sqlite3
import threading
import time
from typing import Optional

from tqdm import tqdm
from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings import Embeddings
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_chroma import Chroma

from .workspace_indexer import _DB_FILENAME, SYMBOL_KIND_NAMES, canonical_path
from .semantic_config import (
    semantic_index_kinds, semantic_concurrency, semantic_batch_size,
    semantic_min_ref_count,
    ranking_file_suffix_penalties, ranking_path_fragment_penalties,
    ranking_default_penalty,
    ranking_candidate_multiplier, ranking_ref_count_boost_weight,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHROMA_DIR_NAME = ".codebase-semantic"
_CHROMA_COLLECTION = "symbol_summaries"

_MAX_CONTEXT_LINES = 50

# ---------------------------------------------------------------------------
# Token-splitting patterns for hybrid keyword matching
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
_TOKEN_SPLIT_RE = re.compile(r'[^a-z0-9]+')

# Hybrid scoring: keyword vs. vector blend (0 = pure vector, 1 = pure keyword)
_KEYWORD_WEIGHT = 0.35
# Diversity: each extra result from the same file is penalised by this factor
_SAME_FILE_DECAY = 0.85
# Symbols with the same name appearing multiple times (e.g. the same helper
# duplicated across view files) are further penalised to improve diversity.
# Key uses name only (not kind) so e.g. Method+Function variants of the same
# name are grouped together and decay applies across the group.
_SAME_NAME_DECAY = 0.30


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens by camelCase, snake_case, and delimiters.

    Examples:
        "getUserName"          -> ["get", "user", "name"]
        "HTTP_response_code"   -> ["http", "response", "code"]
        "parse JSON config"    -> ["parse", "json", "config"]
    """
    expanded = _CAMEL_RE.sub(' ', text)
    tokens = _TOKEN_SPLIT_RE.split(expanded.lower())
    return [t for t in tokens if t and len(t) > 1]


def _make_embedding_text(name: str, kind: str, container: str, summary: str) -> str:
    """Build the text stored and embedded in the vector store.

    Combines identity (name, kind, container) with semantics (summary) so
    the resulting embedding captures both *what* the symbol is and *what it does*.
    """
    identity = f"[{kind}]"
    if container:
        identity += f" {container}.{name}"
    else:
        identity += f" {name}"
    return f"{identity} — {summary}"


def _why_matched(name: str, kind: str, container: str, file_path: str, summary: str) -> str:
    """Return a short human-readable explanation of why a result was returned."""
    parts: list[str] = []
    if container:
        parts.append(f"{kind} `{name}` in `{container}`")
    else:
        parts.append(f"{kind} `{name}`")
    rel = os.path.basename(file_path)
    parts.append(f"from {rel}")
    if summary:
        # First sentence of the summary, trimmed
        first = summary.split(".")[0].strip()
        if first:
            parts.append(f"— {first}")
    return " ".join(parts)

_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbol_summaries (
    symbol_id    INTEGER PRIMARY KEY REFERENCES symbols(id) ON DELETE CASCADE,
    symbol_hash  TEXT    NOT NULL,
    summary      TEXT    NOT NULL,
    chroma_id    TEXT    NOT NULL
);
"""

_FILE_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS file_summaries (
    file_path        TEXT PRIMARY KEY,
    structural_hash  TEXT NOT NULL,
    summary          TEXT NOT NULL,
    generated_at     REAL NOT NULL
);
"""

_PROJECT_SUMMARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_summary (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    summary         TEXT NOT NULL,
    generated_at    REAL NOT NULL,
    summaries_hash  TEXT NOT NULL
);
"""

_SYSTEM_PROMPT = (
    "You are a code documentation assistant. Given a code symbol and its source context, "
    "write a concise summary (1-3 sentences) in English describing what this symbol does, "
    "its purpose, and key behavior. Focus on semantics, not syntax. "
    "Do NOT include the symbol name in the summary."
)

_FILE_SYSTEM_PROMPT = (
    "You are a code documentation assistant. Given a list of symbols defined in a source file, "
    "write a concise summary (2-4 sentences) in English describing: "
    "1) the primary responsibility of this file, "
    "2) the key classes/functions it exposes, "
    "3) what it depends on or delegates to. "
    "Focus on architecture-level purpose, not implementation details."
)

_PROJECT_SYSTEM_PROMPT = (
    "You are a software architect. Given a list of source files and their summaries, "
    "produce a structured overview of the codebase. Your response MUST contain exactly these "
    "four sections with these headings:\n"
    "## Architecture Overview\n"
    "2-3 sentences describing the overall system purpose and design.\n"
    "## File Responsibilities\n"
    "A bullet list: one line per file in the format `filename: responsibility`.\n"
    "## Data Flow\n"
    "A short prose description of how data moves through the system end-to-end.\n"
    "## Extension Points\n"
    "A bullet list: `To do X → edit Y (function/class Z)` for the most common change scenarios."
)


# ---------------------------------------------------------------------------
# SemanticIndexer
# ---------------------------------------------------------------------------

class SemanticIndexer:
    """
    Generates AI summaries for indexed symbols and stores them as embeddings
    in a ChromaDB vector store for natural-language search.
    """

    def __init__(
        self,
        root_dir: str,
        llm: BaseChatModel,
        embeddings: Embeddings,
    ):
        self._root_dir = root_dir
        self._llm = llm
        self._embeddings = embeddings
        self._db_path = os.path.join(root_dir, _DB_FILENAME)
        self._chroma_dir = os.path.join(root_dir, _CHROMA_DIR_NAME)
        self._eligible_kinds = semantic_index_kinds()
        self._concurrency = semantic_concurrency()
        self._batch_size = semantic_batch_size()
        self._min_ref_count = semantic_min_ref_count()
        self._lock = threading.Lock()

        # Ranking config (loaded once at init, no global mutable state)
        self._suffix_penalties = ranking_file_suffix_penalties()
        self._fragment_penalties = ranking_path_fragment_penalties()
        self._default_penalty = ranking_default_penalty()
        self._candidate_multiplier = ranking_candidate_multiplier()
        self._ref_count_boost_weight = ranking_ref_count_boost_weight()

        self._vectorstore = Chroma(
            collection_name=_CHROMA_COLLECTION,
            embedding_function=self._embeddings,
            persist_directory=self._chroma_dir,
        )

        # Benchmark accumulators — reset before each _do_index() run
        self._bm_t_context: float = 0.0
        self._bm_t_llm: float = 0.0
        self._bm_t_chroma: float = 0.0
        self._bm_t_sqlite: float = 0.0
        self._bm_input_tokens: int = 0
        self._bm_output_tokens: int = 0

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def ensure_schema(self, con: sqlite3.Connection):
        con.executescript(_SUMMARY_SCHEMA)
        con.executescript(_FILE_SUMMARY_SCHEMA)
        con.executescript(_PROJECT_SUMMARY_SCHEMA)
        # Migrate: rename file_hash -> symbol_hash if the old column still exists
        cols = {row[1] for row in con.execute("PRAGMA table_info(symbol_summaries)").fetchall()}
        if "file_hash" in cols and "symbol_hash" not in cols:
            con.execute("ALTER TABLE symbol_summaries RENAME COLUMN file_hash TO symbol_hash")
            con.commit()

    # ------------------------------------------------------------------
    # Ranking helpers
    # ------------------------------------------------------------------

    def _noise_penalty(self, file_path: str, name: str) -> float:
        """Return a multiplicative score penalty (>=1, higher = worse).

        Rules are evaluated and their penalties **accumulated** (multiplied).
        """
        fp = file_path.replace("\\", "/").lower()
        penalty = self._default_penalty

        for suffix, p in self._suffix_penalties.items():
            if fp.endswith(suffix):
                penalty = max(penalty, p)
                break

        for fragment, p in self._fragment_penalties:
            if fragment in fp:
                penalty *= p

        return penalty

    def _compute_name_relevance(self, query: str, name: str, container: str) -> float:
        """Score how well a symbol's identity matches the query (0-1, higher = better).

        Uses token overlap with bonuses for substring and exact matches.
        """
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return 0.0

        name_tokens = set(_tokenize(name))

        # Token overlap (relative to query length)
        overlap = len(query_tokens & name_tokens)
        token_score = overlap / len(query_tokens)

        # Substring match bonuses
        ql, nl = query.lower(), name.lower()
        if nl == ql:
            token_score = 1.0
        elif nl in ql or ql in nl:
            token_score = max(token_score, 0.7)

        # Container matching (weaker signal)
        container_score = 0.0
        if container:
            container_tokens = set(_tokenize(container))
            if container_tokens:
                c_overlap = len(query_tokens & container_tokens)
                container_score = c_overlap / len(query_tokens)

        return min(1.0, 0.75 * token_score + 0.25 * container_score)

    def _fetch_ref_counts(self, results) -> dict[tuple[str, int], int]:
        """Bulk-fetch reference counts for all candidate symbols."""
        ref_count_map: dict[tuple[str, int], int] = {}
        if self._ref_count_boost_weight <= 0:
            return ref_count_map
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            candidate_ids: list[tuple[str, int]] = []
            for doc, _ in results:
                meta = doc.metadata
                fp = meta.get("file_path", "")
                dl = meta.get("def_line", -1)
                if fp and dl >= 0:
                    candidate_ids.append((fp, dl))
            if candidate_ids:
                placeholders = ",".join("(?,?)" for _ in candidate_ids)
                flat_params = [x for pair in candidate_ids for x in pair]
                rows = con.execute(
                    f"""SELECT s.file_path, s.def_line, COUNT(r.id) AS ref_count
                        FROM symbols s
                        LEFT JOIN symbol_refs r ON r.symbol_id = s.id
                        WHERE (s.file_path, s.def_line) IN ({placeholders})
                        GROUP BY s.id""",
                    flat_params,
                ).fetchall()
                for row in rows:
                    ref_count_map[(row["file_path"], row["def_line"])] = row["ref_count"]
            con.close()
        except Exception:
            pass  # ref_count boost is best-effort
        return ref_count_map

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_symbols(self, symbol_ids: list[int] | None = None):
        """
        Generate summaries and embeddings for symbols that need processing.
        If symbol_ids is None, processes all eligible symbols that are missing
        or have stale summaries.
        """
        with self._lock:
            try:
                affected_files = self._do_index(symbol_ids)
                # For file summaries we want ALL files that have any symbols, not
                # just those whose symbols met the kind/ref_count eligibility criteria.
                # _generate_file_summaries uses structural_hash to skip unchanged files
                # so passing the full list is safe and idempotent.
                if symbol_ids is None:
                    # Full pass: check every indexed file, including those with no symbols.
                    try:
                        con = sqlite3.connect(self._db_path, check_same_thread=False)
                        con.row_factory = sqlite3.Row
                        all_files = [
                            r[0] for r in con.execute(
                                "SELECT file_path FROM file_hashes ORDER BY file_path"
                            ).fetchall()
                        ]
                        con.close()
                    except sqlite3.Error:
                        all_files = affected_files
                    self._generate_file_summaries(all_files)
                else:
                    # Incremental: only re-check files touched by this batch,
                    # plus any that are still missing a summary.
                    try:
                        con = sqlite3.connect(self._db_path, check_same_thread=False)
                        con.row_factory = sqlite3.Row
                        unsummarised = [
                            r[0] for r in con.execute(
                                "SELECT DISTINCT s.file_path FROM symbols s "
                                "LEFT JOIN file_summaries fs ON fs.file_path = s.file_path "
                                "WHERE fs.file_path IS NULL"
                            ).fetchall()
                        ]
                        con.close()
                    except sqlite3.Error:
                        unsummarised = []
                    self._generate_file_summaries(list(set(affected_files) | set(unsummarised)))
                self._index_project_summary()
            except Exception as e:
                print(f"[Semantic] Error during indexing: {e}")
            # ── DB size snapshot ────────────────────────────────────────────
            try:
                import pathlib
                _db_size = os.path.getsize(self._db_path) if os.path.exists(self._db_path) else 0
                _chroma_path = pathlib.Path(self._chroma_dir)
                _chroma_size = (
                    sum(f.stat().st_size for f in _chroma_path.rglob("*") if f.is_file())
                    if _chroma_path.is_dir() else 0
                )
                print(
                    f"[BENCHMARK:SIZES] sqlite_bytes={_db_size} "
                    f"chroma_bytes={_chroma_size} "
                    f"sqlite_mb={_db_size / 1048576:.2f} "
                    f"chroma_mb={_chroma_size / 1048576:.2f}",
                    flush=True,
                )
            except Exception:
                pass

    def remove_file(self, file_path: str):
        """Remove all semantic data for symbols in the given file."""
        with self._lock:
            try:
                self._do_remove_file(file_path)
            except Exception as e:
                print(f"[Semantic] Error removing file {file_path}: {e}")

    def clear_semantic(self):
        """Wipe all summaries and the ChromaDB store so everything is re-generated."""
        with self._lock:
            try:
                # Drop and recreate the Chroma collection
                self._vectorstore.delete_collection()
                self._vectorstore = Chroma(
                    collection_name=_CHROMA_COLLECTION,
                    embedding_function=self._embeddings,
                    persist_directory=self._chroma_dir,
                )
                # Wipe SQLite summaries tables
                con = sqlite3.connect(self._db_path, check_same_thread=False)
                con.execute("PRAGMA foreign_keys=ON")
                self.ensure_schema(con)
                con.execute("DELETE FROM symbol_summaries")
                con.execute("DELETE FROM file_summaries")
                con.execute("DELETE FROM project_summary")
                con.commit()
                con.close()
                print("[Semantic] Semantic index cleared – full re-summarisation will run on startup.")
            except Exception as e:
                print(f"[Semantic] Error clearing semantic index: {e}")

    def clear_project_summaries(self):
        """Wipe only file_summaries and project_summary so they are re-generated
        on the next indexing run, without touching symbol_summaries or ChromaDB."""
        with self._lock:
            try:
                con = sqlite3.connect(self._db_path, check_same_thread=False)
                con.execute("PRAGMA foreign_keys=ON")
                self.ensure_schema(con)
                con.execute("DELETE FROM file_summaries")
                con.execute("DELETE FROM project_summary")
                con.commit()
                con.close()
                print("[Semantic] File/project summaries cleared – will be re-generated on next index run.")
            except Exception as e:
                print(f"[Semantic] Error clearing project summaries: {e}")

    def rebuild_vectors(self):
        """Re-embed all existing LLM summaries into a fresh ChromaDB collection.

        Unlike ``clear_semantic()``, this does **not** call the LLM — it reuses
        the summaries already stored in SQLite and only regenerates the vector
        embeddings.  Use this after changing the embedding model.
        """
        with self._lock:
            try:
                # Drop and recreate the Chroma collection
                self._vectorstore.delete_collection()
                self._vectorstore = Chroma(
                    collection_name=_CHROMA_COLLECTION,
                    embedding_function=self._embeddings,
                    persist_directory=self._chroma_dir,
                )

                con = sqlite3.connect(self._db_path, check_same_thread=False)
                con.execute("PRAGMA foreign_keys=ON")
                con.row_factory = sqlite3.Row
                self.ensure_schema(con)

                rows = con.execute(
                    """SELECT ss.symbol_id, ss.summary, ss.chroma_id,
                              s.name, s.kind_label, s.container_name,
                              s.file_path, s.def_line, s.def_character, s.def_end_line
                       FROM symbol_summaries ss
                       JOIN symbols s ON s.id = ss.symbol_id
                       ORDER BY ss.symbol_id"""
                ).fetchall()
                con.close()

                if not rows:
                    print("[Semantic] No existing summaries found – nothing to re-embed.")
                    return

                print(f"[Semantic] Re-embedding {len(rows)} summaries with new embedding model...")

                texts: list[str] = []
                metadatas: list[dict] = []
                chroma_ids: list[str] = []

                for row in rows:
                    name = row["name"]
                    kind = row["kind_label"]
                    container = row["container_name"] or ""
                    summary = row["summary"]
                    texts.append(_make_embedding_text(name, kind, container, summary))
                    metadatas.append({
                        "name":          name,
                        "kind":          kind,
                        "container":     container,
                        "file_path":     row["file_path"],
                        "def_line":      row["def_line"],
                        "def_character": row["def_character"],
                        "def_end_line":  row["def_end_line"] if row["def_end_line"] is not None else -1,
                        "summary":       summary,
                    })
                    chroma_ids.append(row["chroma_id"])

                # Insert in batches to avoid memory issues with large codebases
                batch_size = self._batch_size * 4
                for i in range(0, len(texts), batch_size):
                    self._vectorstore.add_texts(
                        texts=texts[i:i + batch_size],
                        metadatas=metadatas[i:i + batch_size],
                        ids=chroma_ids[i:i + batch_size],
                    )
                    print(f"[Semantic] Re-embedded {min(i + batch_size, len(texts))}/{len(rows)}...")

                print("[Semantic] Vector rebuild complete.")
            except Exception as e:
                print(f"[Semantic] Error rebuilding vectors: {e}")

    def get_file_summary(self, file_path: str) -> dict:
        """Return the stored AI summary for a file, or an error dict if not found."""
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT file_path, summary, generated_at FROM file_summaries WHERE file_path=?",
                (canonical_path(file_path),),
            ).fetchone()
            con.close()
        except sqlite3.Error as e:
            return {"error": f"Database error: {e}"}
        if row is None:
            return {"error": f"No file summary found for: {file_path}"}
        return {"result": {"file": row["file_path"], "summary": row["summary"]}}

    def get_project_summary(self) -> dict:
        """Return the stored AI project-level summary, or an error dict if not found."""
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT summary, generated_at FROM project_summary WHERE id=1",
            ).fetchone()
            con.close()
        except sqlite3.Error as e:
            return {"error": f"Database error: {e}"}
        if row is None:
            return {"error": "Project summary not yet generated. The indexer may still be running."}
        return {"result": {"summary": row["summary"]}}

    def search(
        self,
        query: str,
        limit: int = 10,
        kinds: list[str] | None = None,
    ) -> dict:
        """Search symbols by natural language query using hybrid vector + keyword scoring.

        Retrieval pipeline:
        1. Fetch extra candidates from ChromaDB via vector similarity
        2. Hard-filter `<unknown>`/unnamed LSP artefacts (no valid identifier chars)
        3. Compute keyword relevance from symbol name / container matching
        4. Blend vector similarity and keyword score into a hybrid score
        5. Apply noise penalties (generated paths, anonymous names, etc.)
        6. Apply ref-count boost (well-referenced symbols rank higher)
        7. Apply same-file / same-name diversity decay
        8. Return top-k by final adjusted score
        """
        limit = min(max(1, limit), 100)
        fetch_k = limit * self._candidate_multiplier

        # Build Chroma kind filter
        where_filter = None
        if kinds:
            normalised = [k.strip().capitalize() for k in kinds]
            valid = [k for k in normalised if k in set(SYMBOL_KIND_NAMES.values())]
            if valid:
                where_filter = {"kind": valid[0]} if len(valid) == 1 else {"kind": {"$in": valid}}

        try:
            raw_results = self._vectorstore.similarity_search_with_score(
                query, k=fetch_k, filter=where_filter,
            )
        except Exception as e:
            return {"error": f"Semantic search failed: {e}"}

        if not raw_results:
            return {"result": [], "count": 0}

        # Bulk-fetch ref counts for all candidates
        ref_count_map = self._fetch_ref_counts(raw_results)

        # Score each candidate
        scored: list[dict] = []
        for doc, distance in raw_results:
            meta = doc.metadata
            name = meta.get("name", "")
            kind = meta.get("kind", "")
            container = meta.get("container", "")
            file_path = meta.get("file_path", "")
            def_line = meta.get("def_line", 0)

            if not re.search(r'[a-zA-Z_$]', name or ""):
                continue

            # Raw summary: stored in metadata (new format) or page_content (legacy)
            summary = meta.get("summary", "") or doc.page_content

            # --- Hybrid scoring (higher = better) ---

            # Vector similarity: convert L2 distance to 0-1 (higher = closer)
            vector_sim = 1.0 / (1.0 + float(distance))

            # Keyword relevance from name / container matching
            name_relevance = self._compute_name_relevance(query, name, container)

            # Blend
            hybrid = (1.0 - _KEYWORD_WEIGHT) * vector_sim + _KEYWORD_WEIGHT * name_relevance

            # Noise penalty: path/name quality rules
            penalty = self._noise_penalty(file_path, name)
            hybrid /= max(penalty, 1.0)

            # Ref-count boost (more references → higher score), capped to avoid
            # swamping vector similarity with ref-count bias.
            # Formula: 1.0 + log1p(ref_count) * weight so the boost is always
            # >= 1.0 for any positive ref_count (the old log1p(ref_count*weight)
            # formula incorrectly penalised symbols with ref_count <= 5).
            ref_count = ref_count_map.get((file_path, def_line), 0)
            if self._ref_count_boost_weight > 0 and ref_count > 0:
                boost = 1.0 + math.log1p(ref_count) * self._ref_count_boost_weight
                # Cap: a symbol cannot boost more than 1.35x over baseline
                boost = min(boost, 1.35)
                hybrid *= boost

            scored.append({
                "name":       name,
                "kind":       kind,
                "container":  container,
                "file":       file_path,
                "definition": {
                    "line":      def_line,
                    "character": meta.get("def_character", 0),
                    "end_line":  meta.get("def_end_line", -1),
                },
                "summary":      summary,
                "score":        round(float(distance), 4),
                "why_matched":  _why_matched(name, kind, container, file_path, summary),
                "_hybrid":      hybrid,
                "_file_path":   file_path,
            })

        # --- Re-rank with diversity penalty ---
        scored.sort(key=lambda x: x["_hybrid"], reverse=True)

        # Collect up to limit*3 items, applying decay scores, then sort and truncate.
        # Collecting more than limit first ensures the decay has effect: without this,
        # we'd stop at `limit` items and never give better-but-later items a chance.
        pool: list[dict] = []
        file_seen: dict[str, int] = {}
        name_seen: dict[str, int] = {}  # name_lower -> count (kind ignored so
        # all variants of the same symbol name share the diversity budget)
        for item in scored[:limit * 3]:
            fp = item["_file_path"]
            name_key = item["name"].lower()
            n_file = file_seen.get(fp, 0)
            n_name = name_seen.get(name_key, 0)
            item["_adjusted"] = (
                item["_hybrid"]
                * (_SAME_FILE_DECAY ** n_file)
                * (_SAME_NAME_DECAY ** n_name)
            )
            file_seen[fp] = n_file + 1
            name_seen[name_key] = n_name + 1
            pool.append(item)

        # Final sort by adjusted score, truncate to requested limit
        pool.sort(key=lambda x: x["_adjusted"], reverse=True)
        final = pool[:limit]

        # Final sort by adjusted score
        final.sort(key=lambda x: x["_adjusted"], reverse=True)

        # Clean up internal fields
        for item in final:
            item.pop("_hybrid", None)
            item.pop("_adjusted", None)
            item.pop("_file_path", None)

        return {"result": final, "count": len(final)}

    # ------------------------------------------------------------------
    # Internal: indexing pipeline
    # ------------------------------------------------------------------

    def _do_index(self, symbol_ids: list[int] | None) -> list[str]:
        """Run symbol summary generation. Returns list of affected file_paths."""
        # Reset per-run benchmark accumulators
        self._bm_t_context = 0.0
        self._bm_t_llm = 0.0
        self._bm_t_chroma = 0.0
        self._bm_t_sqlite = 0.0
        self._bm_input_tokens = 0
        self._bm_output_tokens = 0

        _bm_t_do_start = time.perf_counter()
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys=ON")
        con.row_factory = sqlite3.Row
        self.ensure_schema(con)
        affected_files: set[str] = set()

        try:
            _bm_t_pending_s = time.perf_counter()
            pending = self._find_pending_symbols(con, symbol_ids)
            _bm_t_pending_e = time.perf_counter()
            if not pending:
                return []

            print(
                f"[Semantic] Processing {len(pending)} symbols "
                f"(batch={self._batch_size}, concurrency={self._concurrency})..."
            )
            processed = 0
            errors = 0
            skipped = 0

            with tqdm(
                total=len(pending),
                desc="[Semantic] Summarising",
                unit="sym",
                dynamic_ncols=True,
            ) as bar:
                for i in range(0, len(pending), self._batch_size):
                    batch = pending[i : i + self._batch_size]
                    ok, err = self._process_batch(batch, con)
                    processed += ok
                    errors += err
                    skipped += len(batch) - ok - err
                    bar.update(len(batch))
                    bar.set_postfix(done=processed, skipped=skipped, errors=errors)
                    for sym in batch:
                        affected_files.add(sym["file_path"])

            _bm_t_do_end = time.perf_counter()
            if errors:
                print(f"[Semantic] Done. Summarised: {processed}, Skipped (up-to-date): {skipped}, Errors: {errors}")
            else:
                print(f"[Semantic] Done. Summarised: {processed}, Skipped (up-to-date): {skipped}")
            print(
                f"[BENCHMARK:SEMANTIC] wall_time={_bm_t_do_end - _bm_t_do_start:.2f}s "
                f"pending_query={_bm_t_pending_e - _bm_t_pending_s:.3f}s "
                f"context_extract={self._bm_t_context:.2f}s "
                f"llm_batch={self._bm_t_llm:.2f}s "
                f"chroma_insert={self._bm_t_chroma:.2f}s "
                f"sqlite_write={self._bm_t_sqlite:.2f}s "
                f"symbols_total={len(pending)} summarised={processed} skipped={skipped} errors={errors} "
                f"input_tokens={self._bm_input_tokens} output_tokens={self._bm_output_tokens}",
                flush=True,
            )
            return list(affected_files)
        finally:
            con.close()

    def _generate_file_summaries(self, file_paths: list[str]) -> None:
        """Generate or update file-level summaries for the given files."""
        if not file_paths:
            return
        _bm_fs_start = time.perf_counter()
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.execute("PRAGMA foreign_keys=ON")
            con.row_factory = sqlite3.Row
            self.ensure_schema(con)
        except sqlite3.Error as e:
            print(f"[Semantic] File summary DB error: {e}")
            return

        messages_list: list[list] = []
        pending_files: list[tuple[str, str]] = []   # (file_path, structural_hash)

        try:
            for file_path in file_paths:
                norm = canonical_path(file_path)
                rows = con.execute(
                    """SELECT s.name, s.kind_label, s.container_name, s.def_line, ss.summary
                       FROM symbols s
                       LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                       WHERE s.file_path = ?
                       ORDER BY s.def_line""",
                    (norm,),
                ).fetchall()

                rel_path = os.path.relpath(norm, self._root_dir)

                if not rows:
                    # No symbols — fall back to first 50 lines of source
                    try:
                        with open(norm, "r", encoding="utf-8", errors="replace") as fh:
                            head = "".join(line for _, line in zip(range(50), fh))
                    except OSError:
                        continue
                    struct_str = "no-symbols:" + head
                    structural_hash = hashlib.sha256(struct_str.encode("utf-8")).hexdigest()
                    existing = con.execute(
                        "SELECT structural_hash FROM file_summaries WHERE file_path=?",
                        (norm,),
                    ).fetchone()
                    if existing and existing["structural_hash"] == structural_hash:
                        continue
                    user_msg = (
                        f"File: {rel_path}\n"
                        f"Note: No symbols were extracted from this file by the LSP indexer.\n"
                        f"First 50 lines of source:\n```\n{head.rstrip()}\n```"
                    )
                else:
                    # Compute structural hash from symbol structure (not content)
                    struct_str = "\n".join(
                        f"{r['def_line']}|{r['kind_label']}|{r['name']}|{r['container_name'] or ''}"
                        for r in rows
                    )
                    structural_hash = hashlib.sha256(struct_str.encode("utf-8")).hexdigest()

                    # Check if already up to date
                    existing = con.execute(
                        "SELECT structural_hash FROM file_summaries WHERE file_path=?",
                        (norm,),
                    ).fetchone()
                    if existing and existing["structural_hash"] == structural_hash:
                        continue

                    # Build symbol listing for LLM
                    sym_lines = [f"File: {rel_path}", "Symbols:"]
                    for r in rows:
                        indent = "  " if r["container_name"] else ""
                        sym_line = f"{indent}- [{r['kind_label']}] {r['name']}"
                        if r["summary"]:
                            sym_line += f": {r['summary']}"
                        sym_lines.append(sym_line)
                    user_msg = "\n".join(sym_lines)
                messages_list.append([
                    SystemMessage(content=_FILE_SYSTEM_PROMPT),
                    HumanMessage(content=user_msg),
                ])
                pending_files.append((norm, structural_hash))

            if not pending_files:
                return

            print(f"[Semantic] Generating summaries for {len(pending_files)} file(s)...")
            try:
                responses = self._llm.batch(
                    messages_list,
                    config={"max_concurrency": self._concurrency},
                )
            except Exception as e:
                print(f"[Semantic] LLM batch failed for file summaries: {e}")
                return

            now = time.time()
            for (norm, structural_hash), response in zip(pending_files, responses):
                try:
                    summary = response.content.strip()
                    con.execute(
                        """INSERT OR REPLACE INTO file_summaries
                           (file_path, structural_hash, summary, generated_at)
                           VALUES (?, ?, ?, ?)""",
                        (norm, structural_hash, summary, now),
                    )
                except Exception as e:
                    print(f"[Semantic] Failed to save file summary for {norm}: {e}")
            con.commit()
            print(f"[Semantic] File summaries updated.")
            print(
                f"[BENCHMARK:FILE_SUMMARIES] wall_time={time.perf_counter() - _bm_fs_start:.2f}s "
                f"files_pending={len(pending_files)}",
                flush=True,
            )
        finally:
            con.close()

    def _index_project_summary(self) -> None:
        """Regenerate the project-level summary if any file summary has changed."""
        _bm_ps_start = time.perf_counter()
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.execute("PRAGMA foreign_keys=ON")
            con.row_factory = sqlite3.Row
            self.ensure_schema(con)
        except sqlite3.Error as e:
            print(f"[Semantic] Project summary DB error: {e}")
            return

        try:
            rows = con.execute(
                "SELECT file_path, summary FROM file_summaries ORDER BY file_path"
            ).fetchall()
            if not rows:
                return

            # Compute hash over all file summaries
            combined = "\n".join(f"{r['file_path']}:{r['summary']}" for r in rows)
            summaries_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()

            existing = con.execute(
                "SELECT summaries_hash FROM project_summary WHERE id=1"
            ).fetchone()
            if existing and existing["summaries_hash"] == summaries_hash:
                return

            print("[Semantic] Generating project summary...")
            file_lines = [
                f"- {os.path.relpath(r['file_path'], self._root_dir)}: {r['summary']}"
                for r in rows
            ]
            user_msg = "Files in this codebase:\n" + "\n".join(file_lines)

            try:
                response = self._llm.invoke([
                    SystemMessage(content=_PROJECT_SYSTEM_PROMPT),
                    HumanMessage(content=user_msg),
                ])
                summary = response.content.strip()
            except Exception as e:
                print(f"[Semantic] LLM call failed for project summary: {e}")
                return

            con.execute(
                """INSERT OR REPLACE INTO project_summary (id, summary, generated_at, summaries_hash)
                   VALUES (1, ?, ?, ?)""",
                (summary, time.time(), summaries_hash),
            )
            con.commit()
            print("[Semantic] Project summary updated.")
            print(f"[BENCHMARK:PROJECT_SUMMARY] wall_time={time.perf_counter() - _bm_ps_start:.2f}s", flush=True)
        finally:
            con.close()

    def _find_pending_symbols(
        self,
        con: sqlite3.Connection,
        symbol_ids: list[int] | None,
    ) -> list[dict]:
        """Find symbols that need (re)summarising."""
        kind_placeholders = ",".join("?" * len(self._eligible_kinds))
        kind_params = sorted(self._eligible_kinds)

        ref_join = """
                LEFT JOIN (
                    SELECT symbol_id, COUNT(*) AS ref_count
                    FROM symbol_refs
                    GROUP BY symbol_id
                ) sr ON sr.symbol_id = s.id"""
        ref_filter = "AND COALESCE(sr.ref_count, 0) >= ?" if self._min_ref_count > 0 else ""
        ref_param  = [self._min_ref_count] if self._min_ref_count > 0 else []

        # symbol_hash is computed later from the actual code slice; here we just
        # fetch all candidates that have no summary yet (NULL) and let
        # _process_batch decide whether the code changed.
        if symbol_ids:
            id_placeholders = ",".join("?" * len(symbol_ids))
            # Primary: symbols in the changed files that need (re)summarising.
            # UNION: symbols anywhere that have NO summary yet but now meet the
            # ref_count threshold — e.g. foo() was below the threshold, someone added
            # a new call in a different file, ref_count just crossed the line.
            unsummarised_filter = "AND ss.symbol_id IS NULL" if self._min_ref_count > 0 else ""
            sql = f"""
                SELECT s.id, s.name, s.kind_label, s.container_name,
                       s.file_path, s.def_line, s.def_character, s.def_end_line,
                       ss.symbol_hash AS stored_symbol_hash
                FROM symbols s
                LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                {ref_join}
                WHERE s.id IN ({id_placeholders})
                  AND s.kind_label IN ({kind_placeholders})
                  {ref_filter}
                UNION
                SELECT s.id, s.name, s.kind_label, s.container_name,
                       s.file_path, s.def_line, s.def_character, s.def_end_line,
                       ss.symbol_hash AS stored_symbol_hash
                FROM symbols s
                LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                {ref_join}
                WHERE s.kind_label IN ({kind_placeholders})
                  {ref_filter}
                  {unsummarised_filter}
            """
            params = symbol_ids + kind_params + ref_param + kind_params + ref_param
        else:
            sql = f"""
                SELECT s.id, s.name, s.kind_label, s.container_name,
                       s.file_path, s.def_line, s.def_character, s.def_end_line,
                       ss.symbol_hash AS stored_symbol_hash
                FROM symbols s
                LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                {ref_join}
                WHERE s.kind_label IN ({kind_placeholders})
                  {ref_filter}
            """
            params = kind_params + ref_param

        rows = con.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def _process_batch(
        self,
        symbols: list[dict],
        con: sqlite3.Connection,
    ) -> tuple[int, int]:
        """Process a batch of symbols: extract context, summarise, embed. Returns (ok, errors).

        Optimisations vs. the naive sequential approach:
          1. File-read cache – each source file is read from disk only once per batch.
          2. Parallel LLM calls – all messages are sent in one llm.batch() call with
             max_concurrency, so N symbols take ~1 round-trip instead of N.
          3. Batched ChromaDB insert – summaries are written in a single add_texts() call.
        """
        # ── 1. Remove stale entries & build message list ────────────────────
        _bm_ctx_s = time.perf_counter()
        file_cache: dict[str, list[str]] = {}   # path -> lines
        messages_list = []
        valid_syms: list[dict] = []
        pre_errors = 0

        for sym in symbols:
            try:
                context = self._extract_context_cached(sym, file_cache)
                symbol_hash = hashlib.sha256(context.encode("utf-8", errors="replace")).hexdigest()
                # Skip if the code slice hasn't changed (hash-match = not an error)
                if sym.get("stored_symbol_hash") == symbol_hash:
                    continue
                sym["_symbol_hash"] = symbol_hash
                self._remove_stale_summary(con, sym["id"])
                messages_list.append(self._build_messages(sym, context))
                valid_syms.append(sym)
            except Exception as e:
                print(f"[Semantic] Pre-process failed for {sym['name']}: {e}")
                pre_errors += 1
        self._bm_t_context += time.perf_counter() - _bm_ctx_s

        if not valid_syms:
            # All symbols were either hash-matched (up-to-date) or pre-process failed.
            # Neither case should be reported as errors unless there were actual failures.
            return 0, pre_errors

        # ── 2. Parallel LLM summarisation ───────────────────────────────────
        _bm_llm_s = time.perf_counter()
        try:
            responses = self._llm.batch(
                messages_list,
                config={"max_concurrency": self._concurrency},
            )
        except Exception as e:
            print(f"[Semantic] LLM batch call failed: {e}")
            return 0, len(valid_syms)
        self._bm_t_llm += time.perf_counter() - _bm_llm_s
        # Collect token usage from response metadata if available
        for _r in responses:
            _meta = getattr(_r, 'usage_metadata', None) or {}
            self._bm_input_tokens += _meta.get('input_tokens', 0)
            self._bm_output_tokens += _meta.get('output_tokens', 0)

        # ── 3. Batched ChromaDB insert ───────────────────────────────────────
        texts: list[str] = []
        metadatas: list[dict] = []
        chroma_ids: list[str] = []
        ok_syms: list[dict] = []
        errors = 0

        for sym, response in zip(valid_syms, responses):
            try:
                summary = response.content.strip()
                chroma_id = f"sym-{sym['id']}"
                name = sym["name"]
                kind = sym["kind_label"]
                container = sym.get("container_name") or ""
                texts.append(_make_embedding_text(name, kind, container, summary))
                metadatas.append({
                    "name":          name,
                    "kind":          kind,
                    "container":     container,
                    "file_path":     sym["file_path"],
                    "def_line":      sym["def_line"],
                    "def_character": sym["def_character"],
                    "def_end_line":  sym.get("def_end_line", -1),
                    "summary":       summary,
                })
                chroma_ids.append(chroma_id)
                ok_syms.append((sym, summary, chroma_id))
            except Exception as e:
                print(f"[Semantic] Response parse failed for {sym['name']}: {e}")
                errors += 1

        if texts:
            try:
                _bm_chroma_s = time.perf_counter()
                self._vectorstore.add_texts(texts=texts, metadatas=metadatas, ids=chroma_ids)
                self._bm_t_chroma += time.perf_counter() - _bm_chroma_s
            except Exception as e:
                print(f"[Semantic] ChromaDB batch insert failed: {e}")
                return 0, len(valid_syms)

        # ── 4. Persist to SQLite ─────────────────────────────────────────────
        _bm_sqlite_s = time.perf_counter()
        for sym, summary, chroma_id in ok_syms:
            self._save_summary(con, sym["id"], sym["_symbol_hash"], summary, chroma_id)
        con.commit()
        self._bm_t_sqlite += time.perf_counter() - _bm_sqlite_s

        return len(ok_syms), errors + (len(symbols) - len(valid_syms))

    # ------------------------------------------------------------------
    # Internal: context extraction
    # ------------------------------------------------------------------

    def _extract_context_cached(self, sym: dict, file_cache: dict[str, list[str]]) -> str:
        """Read source code around the symbol, using *file_cache* to avoid re-reading."""
        file_path = sym["file_path"]
        if file_path not in file_cache:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    file_cache[file_path] = f.readlines()
            except OSError:
                file_cache[file_path] = []
        lines = file_cache[file_path]
        if not lines:
            return "(source unavailable)"
        start = max(0, sym["def_line"])
        lsp_end = sym.get("def_end_line", -1)
        if lsp_end is not None and lsp_end > start:
            end = min(len(lines), lsp_end + 1)  # LSP range.end is inclusive
        else:
            end = min(len(lines), start + _MAX_CONTEXT_LINES)
        return "".join(lines[start:end]).rstrip()

    def _extract_context(self, sym: dict) -> str:
        """Read source code around the symbol definition."""
        file_path = sym["file_path"]
        def_line = sym["def_line"]  # 0-based

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return "(source unavailable)"

        start = max(0, def_line)
        end = min(len(lines), start + _MAX_CONTEXT_LINES)
        snippet = "".join(lines[start:end]).rstrip()

        return snippet

    def _build_messages(self, sym: dict, context: str) -> list:
        """Build the LLM message list for a single symbol."""
        container_info = f" (inside {sym['container_name']})" if sym.get("container_name") else ""
        user_msg = (
            f"Symbol: {sym['name']}\n"
            f"Kind: {sym['kind_label']}{container_info}\n"
            f"File: {sym['file_path']}\n"
            f"\nSource code:\n```\n{context}\n```"
        )
        return [SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user_msg)]

    # ------------------------------------------------------------------
    # Internal: LLM summarisation (single, kept for ad-hoc use)
    # ------------------------------------------------------------------

    def _summarize(self, sym: dict, context: str) -> str:
        """Generate a summary for a single symbol using the LLM."""
        container_info = f" (inside {sym['container_name']})" if sym.get("container_name") else ""
        user_msg = (
            f"Symbol: {sym['name']}\n"
            f"Kind: {sym['kind_label']}{container_info}\n"
            f"File: {sym['file_path']}\n"
            f"\nSource code:\n```\n{context}\n```"
        )

        response = self._llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_msg),
        ])
        return response.content.strip()

    # ------------------------------------------------------------------
    # Internal: vector store operations
    # ------------------------------------------------------------------

    def _store_embedding(self, sym: dict, summary: str) -> str:
        """Store summary in ChromaDB and return the generated chroma_id."""
        chroma_id = f"sym-{sym['id']}"
        name = sym["name"]
        kind = sym["kind_label"]
        container = sym.get("container_name") or ""
        metadata = {
            "name":          name,
            "kind":          kind,
            "container":     container,
            "file_path":     sym["file_path"],
            "def_line":      sym["def_line"],
            "def_character": sym["def_character"],
            "def_end_line":  sym.get("def_end_line", -1),
            "summary":       summary,
        }
        self._vectorstore.add_texts(
            texts=[_make_embedding_text(name, kind, container, summary)],
            metadatas=[metadata],
            ids=[chroma_id],
        )
        return chroma_id

    def _remove_stale_summary(self, con: sqlite3.Connection, symbol_id: int):
        """Remove existing chroma entry and SQLite record for a symbol if present."""
        row = con.execute(
            "SELECT chroma_id FROM symbol_summaries WHERE symbol_id=?",
            (symbol_id,),
        ).fetchone()
        if row:
            try:
                self._vectorstore.delete(ids=[row["chroma_id"]])
            except Exception:
                pass  # Chroma may not have it; that's fine
            con.execute("DELETE FROM symbol_summaries WHERE symbol_id=?", (symbol_id,))

    def _save_summary(
        self,
        con: sqlite3.Connection,
        symbol_id: int,
        symbol_hash: str,
        summary: str,
        chroma_id: str,
    ):
        """Persist the summary record in SQLite."""
        con.execute(
            "INSERT OR REPLACE INTO symbol_summaries(symbol_id, symbol_hash, summary, chroma_id) VALUES(?,?,?,?)",
            (symbol_id, symbol_hash, summary, chroma_id),
        )

    # ------------------------------------------------------------------
    # Internal: file removal
    # ------------------------------------------------------------------

    def _do_remove_file(self, file_path: str):
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys=ON")
        con.row_factory = sqlite3.Row
        # NOTE: row_factory=Row is required for r["chroma_id"] below
        try:
            rows = con.execute(
                "SELECT chroma_id FROM symbol_summaries WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE file_path=?)",
                (file_path,),
            ).fetchall()
            chroma_ids = [r["chroma_id"] for r in rows]
            if chroma_ids:
                try:
                    self._vectorstore.delete(ids=chroma_ids)
                except Exception:
                    pass
            con.execute(
                "DELETE FROM symbol_summaries WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE file_path=?)",
                (file_path,),
            )
            con.commit()
        finally:
            con.close()
