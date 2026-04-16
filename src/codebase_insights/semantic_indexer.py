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
    semantic_min_ref_count, semantic_summary_update_threshold,
    ranking_file_suffix_penalties, ranking_path_fragment_penalties,
    ranking_default_penalty,
    ranking_candidate_multiplier, ranking_ref_count_boost_weight,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHROMA_DIR_NAME = ".codebase-semantic"
_CHROMA_COLLECTION = "symbol_summaries"
_FILE_CHROMA_COLLECTION = "file_summaries"

_MAX_CONTEXT_LINES = 50

# ---------------------------------------------------------------------------
# Token-splitting patterns for hybrid keyword matching
# ---------------------------------------------------------------------------

_CAMEL_RE = re.compile(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])')
_TOKEN_SPLIT_RE = re.compile(r'[^a-z0-9]+')

# Hybrid scoring: keyword vs. vector blend (0 = pure vector, 1 = pure keyword)
# Reverted from 0.45 → 0.35 in v0.2.2: raising the weight to 0.45 caused a
# Hit@1 regression — keyword tie-breakers (e.g. "options" in both AISetupOptions
# and CreateProviderOptions) swamped the vector similarity signal that correctly
# disambiguates semantically similar names.
_KEYWORD_WEIGHT = 0.30
# Weight for query↔summary token overlap (stem-aware).  This directly rewards
# results whose LLM-generated summary shares vocabulary with the query,
# complementing the embedding-based vector similarity.
_SUMMARY_WEIGHT = 0.15
# Separate (lower) keyword weight for file search: file path tokens are much
# noisier than symbol names (e.g. "provider" in provider.ts scores identically
# for any provider-related query).  A lower weight keeps file search semantic.
_FILE_KEYWORD_WEIGHT = 0.20
# Diversity: each extra result from the same file is penalised by this factor
_SAME_FILE_DECAY = 0.85
# Symbols with the same name appearing multiple times (e.g. the same helper
# duplicated across view files) are further penalised to improve diversity.
# Key uses name only (not kind) so e.g. Method+Function variants of the same
# name are grouped together and decay applies across the group.
_SAME_NAME_DECAY = 0.30
# Exact-name-match multipliers: applied post-blend to reward queries that
# explicitly reference a symbol name.  Only fires when the name is at least 4
# chars (avoids trivial stop-word matches) and the match is unambiguous.
#   Verbatim: the full lowercased name appears as a substring of the query
#   Full-token: every camelCase/snake_case token of the name appears in the query
# These multipliers intentionally scale the entire hybrid score (including the
# vector component) so that an explicit name lookup overrides any noise penalty.
_NAME_VERBATIM_MULTIPLIER = 2.5
_NAME_FULL_TOKEN_MULTIPLIER = 1.8


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


def _stem_set(token: str) -> frozenset[str]:
    """Return the token plus lightweight morphological stem variants.

    Handles common English inflections (-ing, -ed, -s, -es) so that
    "streaming" matches "stream" and "executed" matches "execute".
    Also decomposes compound tokens (e.g. "keystore" → "key" + "store").
    """
    stems: set[str] = {token}
    if len(token) <= 3:
        return frozenset(stems)
    if token.endswith('ing') and len(token) > 5:
        base = token[:-3]
        stems.add(base)                # streaming → stream
        stems.add(base + 'e')          # creating → create, storing → store
        if len(base) >= 2 and base[-1] == base[-2] and base[-1] not in 'aeiou':
            stems.add(base[:-1])       # running → run
    if token.endswith('ed') and len(token) > 4:
        base = token[:-2]
        stems.add(base)                # dispatched → dispatch
        stems.add(base + 'e')          # executed → execute, stored → store
    if token.endswith('es') and len(token) > 4:
        stems.add(token[:-2])          # messages → messag, but also catches -e stems
    if token.endswith('s') and not token.endswith('ss') and len(token) > 3:
        stems.add(token[:-1])          # tools → tool, traits → trait
    # Compound decomposition: try splitting a long token into two known-looking
    # parts (min 3 chars each).  This lets "keystore" yield {"key", "store"}.
    if len(token) >= 6:
        for i in range(3, len(token) - 2):
            left, right = token[:i], token[i:]
            if len(left) >= 3 and len(right) >= 3:
                stems.add(left)
                stems.add(right)
    return frozenset(stems)


# Word-boundary pattern: matches a token that is NOT preceded or followed by
# a lowercase letter (allows digits, punctuation, start/end of string).
_WORD_BOUNDARY_RE_CACHE: dict[str, re.Pattern] = {}


def _word_boundary_match(needle: str, haystack: str) -> bool:
    """Check if *needle* appears as a whole word in *haystack*."""
    pat = _WORD_BOUNDARY_RE_CACHE.get(needle)
    if pat is None:
        pat = re.compile(r'(?<![a-z])' + re.escape(needle) + r'(?![a-z])')
        _WORD_BOUNDARY_RE_CACHE[needle] = pat
    return pat.search(haystack) is not None


def _compute_summary_relevance(
    query_tokens: set[str],
    summary: str,
    token_weights: dict[str, float] | None = None,
) -> float:
    """Weighted fraction of query tokens whose stems appear in the summary.

    When *token_weights* is provided (query-token → weight), each matching
    token contributes its weight rather than a flat 1.  This lets
    discriminative tokens (e.g. "websocket") count more than ubiquitous
    ones (e.g. "message").  Returns a value in [0, 1].
    """
    if not summary or not query_tokens:
        return 0.0
    summary_tokens = set(_tokenize(summary))
    summary_stems: set[str] = set()
    for t in summary_tokens:
        summary_stems.update(_stem_set(t))
    if token_weights:
        matched_w = sum(
            token_weights.get(qt, 1.0)
            for qt in query_tokens
            if _stem_set(qt) & summary_stems
        )
        total_w = sum(token_weights.get(qt, 1.0) for qt in query_tokens)
        return matched_w / total_w if total_w > 0 else 0.0
    matched = sum(1 for qt in query_tokens if _stem_set(qt) & summary_stems)
    return matched / len(query_tokens)


# ---------------------------------------------------------------------------
# Kind-preference: detect type-describing queries
# ---------------------------------------------------------------------------

_TYPE_QUERY_INDICATORS = frozenset({
    'class', 'interface', 'type', 'model', 'struct', 'enum',
    'payload', 'options', 'context', 'contract', 'envelope',
    'schema', 'definition', 'adapter', 'store', 'storage',
    'registry', 'request', 'response', 'traits', 'data',
})
_TYPE_KINDS = frozenset({'Class', 'Interface', 'Enum', 'TypeAlias', 'Struct'})
_IMPL_KINDS = frozenset({'Method', 'Function', 'Constructor'})
_KIND_TYPE_BOOST = 1.20
_KIND_IMPL_DEMOTE = 0.80


def _safe_relpath(path: str, start: str) -> str:
    """Return a relative path when possible, else fall back to the original path.

    On Windows, os.path.relpath raises ValueError when path and start are on
    different drives. File-summary data can persist across benchmark targets, so
    we need a stable fallback instead of failing the entire semantic pass.
    """
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return path


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


# Maximum number of top-level exported symbols to include in the file embedding
# text.  Including key symbol names improves file search when multiple files share
# the same generic name (e.g. several "types.ts" across packages).
_MAX_FILE_EMBED_SYMBOLS = 8


def _build_file_embed_text(rel_path: str, summary: str, top_symbols: list[str]) -> str:
    """Build the text to embed for a file-level vector store entry.

    Enriches the basic ``[File] path — summary`` format with the names of the
    file's top-level exported symbols so that queries mentioning specific symbol
    names surface the right file even when the summary is generic.
    """
    text = f"[File] {rel_path} — {summary}"
    if top_symbols:
        text += f" [exports: {', '.join(top_symbols[:_MAX_FILE_EMBED_SYMBOLS])}]"
    return text

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
    generated_at     REAL NOT NULL,
    is_stale         INTEGER NOT NULL DEFAULT 0
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

_PROJECT_SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_summary_sources (
    file_path       TEXT PRIMARY KEY,
    summary_hash    TEXT NOT NULL
);
"""

# Debounce window for project summary regeneration during incremental updates.
# When multiple files are edited in rapid succession (e.g. a Save-All), each
# watchdog event would otherwise trigger a 100-130s project summary pass.
# The debounce coalesces all edits within this window into a single pass.
_PROJECT_SUMMARY_DEBOUNCE_S = 30.0

# Fraction of files that may change before falling back to full regeneration
_INCREMENTAL_THRESHOLD = 0.3

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
    "produce a concise, high-level overview of the codebase. "
    "Your response MUST contain exactly these three sections with these headings:\n"
    "## Architecture Overview\n"
    "3-5 sentences describing the overall system purpose, major components, and design approach. "
    "Do NOT list every file — focus on the architecture.\n"
    "## Data Flow\n"
    "2-4 sentences describing how data moves through the system end-to-end.\n"
    "## Extension Points\n"
    "Up to 5 bullet points: `To do X → edit Y` for the most common change scenarios.\n"
    "Keep the total response under 400 words."
)

_INCREMENTAL_PROJECT_SYSTEM_PROMPT = (
    "You are a software architect. You are given the current project summary and a list of "
    "file-level changes (new, updated, or removed files with their summaries). "
    "Update the project summary to reflect these changes. "
    "Keep the same three-section structure:\n"
    "## Architecture Overview\n"
    "## Data Flow\n"
    "## Extension Points\n"
    "Make minimal, targeted edits — only modify sections affected by the changes. "
    "Keep the total response under 400 words."
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
        self._project_summary_timer: threading.Timer | None = None
        self._project_summary_timer_lock = threading.Lock()
        self._summary_update_threshold = semantic_summary_update_threshold()
        # In-memory set of file paths whose summaries are pending regeneration.
        # Accumulated until _summary_update_threshold is reached, then flushed.
        self._pending_stale_paths: set[str] = set()

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
        self._file_vectorstore = Chroma(
            collection_name=_FILE_CHROMA_COLLECTION,
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
        con.executescript(_PROJECT_SOURCES_SCHEMA)
        # Migrate: rename file_hash -> symbol_hash if the old column still exists
        cols = {row[1] for row in con.execute("PRAGMA table_info(symbol_summaries)").fetchall()}
        if "file_hash" in cols and "symbol_hash" not in cols:
            con.execute("ALTER TABLE symbol_summaries RENAME COLUMN file_hash TO symbol_hash")
            con.commit()
        # Migrate: add is_stale column if it doesn't exist yet
        fs_cols = {row[1] for row in con.execute("PRAGMA table_info(file_summaries)").fetchall()}
        if "is_stale" not in fs_cols:
            con.execute(
                "ALTER TABLE file_summaries ADD COLUMN is_stale INTEGER NOT NULL DEFAULT 0"
            )
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

    def _compute_name_relevance(
        self,
        query: str,
        name: str,
        container: str,
        token_weights: dict[str, float] | None = None,
    ) -> float:
        """Score how well a symbol's identity matches the query (0-1, higher = better).

        Uses stem-aware token overlap with bonuses for whole-word substring matches.
        Multi-token names receive a specificity bonus on descriptive queries.
        When *token_weights* is provided, matching tokens are weighted by their
        per-result vector affinity instead of counted equally.
        """
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return 0.0

        name_tokens = set(_tokenize(name))

        # Stem-aware token overlap: expand name tokens into stem variants,
        # then count how many query tokens match any variant.
        name_expanded = set()
        for t in name_tokens:
            name_expanded.update(_stem_set(t))

        if token_weights:
            matched_w = sum(
                token_weights.get(qt, 1.0)
                for qt in query_tokens
                if _stem_set(qt) & name_expanded
            )
            total_w = sum(token_weights.get(qt, 1.0) for qt in query_tokens)
            token_score = matched_w / total_w if total_w > 0 else 0.0
        else:
            overlap = sum(1 for qt in query_tokens if _stem_set(qt) & name_expanded)
            token_score = overlap / len(query_tokens)

        # Substring match bonuses (require whole-word boundaries)
        ql, nl = query.lower(), name.lower()
        if nl == ql:
            token_score = 1.0
        elif ql in nl:
            # Query contained in symbol name — user is searching for something specific
            token_score = max(token_score, 0.7)
        elif len(nl) >= 3 and _word_boundary_match(nl, ql):
            # Symbol name appears as a whole word in the query — scale by coverage
            coverage = len(nl) / max(len(ql), 1)
            substring_bonus = 0.3 + 0.4 * min(coverage, 1.0)
            token_score = max(token_score, substring_bonus)

        # Name specificity bonus: multi-token names matching a descriptive query
        # are more likely to be the intended target than short generic names.
        if len(query_tokens) >= 3 and len(name_tokens) >= 2:
            specificity = min(0.12, 0.04 * (len(name_tokens) - 1))
            token_score = min(1.0, token_score + specificity)

        # Container matching (weaker signal)
        container_score = 0.0
        if container:
            container_tokens = set(_tokenize(container))
            if container_tokens:
                container_expanded = set()
                for t in container_tokens:
                    container_expanded.update(_stem_set(t))
                c_overlap = sum(1 for qt in query_tokens if _stem_set(qt) & container_expanded)
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
    # Project summary debounce helpers
    # ------------------------------------------------------------------

    def _schedule_project_summary(self, debounce: bool = False) -> None:
        """Trigger project summary regeneration, optionally after a debounce delay.

        When ``debounce=True`` (incremental/watchdog updates), the call is
        deferred by ``_PROJECT_SUMMARY_DEBOUNCE_S`` seconds.  If another edit
        arrives within the window the timer is reset, coalescing all changes
        into a single project-summary pass.

        When ``debounce=False`` (initial full rebuild), the project summary is
        generated immediately in the calling thread.
        """
        if not debounce:
            self._index_project_summary()
            return
        with self._project_summary_timer_lock:
            if self._project_summary_timer is not None:
                self._project_summary_timer.cancel()
            self._project_summary_timer = threading.Timer(
                _PROJECT_SUMMARY_DEBOUNCE_S, self._run_debounced_project_summary
            )
            self._project_summary_timer.daemon = True
            self._project_summary_timer.start()
            print(
                f"[Semantic] Project summary scheduled in "
                f"{_PROJECT_SUMMARY_DEBOUNCE_S:.0f}s (debounce).",
                flush=True,
            )

    def _run_debounced_project_summary(self) -> None:
        """Timer callback: clear the pending timer reference then run the summary."""
        with self._project_summary_timer_lock:
            self._project_summary_timer = None
        try:
            self._index_project_summary()
        except Exception as e:
            print(f"[Semantic] Error in debounced project summary: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index_symbols(self, symbol_ids: list[int] | None = None, debounce: bool = False):
        """
        Generate summaries and embeddings for symbols that need processing.
        If symbol_ids is None, processes all eligible symbols that are missing
        or have stale summaries.

        Args:
            symbol_ids: Specific symbol IDs to process, or None for a full pass.
            debounce: When True, project summary regeneration is deferred by
                      ``_PROJECT_SUMMARY_DEBOUNCE_S`` seconds so that rapid
                      successive edits (watchdog events) are coalesced into one
                      expensive LLM call instead of one per file.
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
                    self._generate_file_summaries(all_files, force=True)
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
                    self._generate_file_summaries(list(set(affected_files) | set(unsummarised)), force=False)
                self._schedule_project_summary(debounce=debounce)
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

    def refresh_file_summary(self, file_path: str) -> dict:
        """Force-regenerate the AI summary for a single file, ignoring the change
        threshold.  The file's ``is_stale`` flag is cleared after a successful run.

        Returns a dict with ``"result"`` on success or ``"error"`` on failure.
        """
        norm = canonical_path(file_path)
        if not os.path.isabs(norm):
            norm = os.path.normpath(os.path.join(self._root_dir, norm))
        # Mark stale so that force=True regenerates it regardless of hash match.
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.execute("UPDATE file_summaries SET is_stale=1 WHERE file_path=?", (norm,))
            con.commit()
            con.close()
        except sqlite3.Error:
            pass
        with self._lock:
            try:
                self._generate_file_summaries([norm], force=True)
            except Exception as e:
                return {"error": f"Failed to refresh file summary: {e}"}
        try:
            self._schedule_project_summary(debounce=True)
        except Exception as e:
            print(f"[Semantic] Error scheduling project summary after file refresh: {e}")
        return {"result": f"File summary refreshed for: {norm}"}

    def refresh_project_summary(self) -> dict:
        """Force-regenerate file summaries for all stale files, then regenerate
        the project summary immediately (bypassing the change threshold).

        Returns a dict with ``"result"`` on success or ``"error"`` on failure.
        """
        with self._lock:
            try:
                # Re-generate all files with is_stale=1 or pending in memory
                stale_from_db: list[str] = []
                try:
                    con = sqlite3.connect(self._db_path, check_same_thread=False)
                    con.row_factory = sqlite3.Row
                    stale_from_db = [
                        r[0] for r in con.execute(
                            "SELECT file_path FROM file_summaries WHERE is_stale=1"
                        ).fetchall()
                    ]
                    con.close()
                except sqlite3.Error:
                    pass
                pending_paths = list(self._pending_stale_paths | set(stale_from_db))
                if pending_paths:
                    self._generate_file_summaries(pending_paths, force=True)
                self._index_project_summary(force=True)
            except Exception as e:
                return {"error": f"Failed to refresh project summary: {e}"}
        return {"result": "Project summary (and all stale file summaries) refreshed."}

    def remove_file(self, file_path: str):
        """Remove all semantic data for symbols in the given file."""
        with self._lock:
            try:
                self._do_remove_file(file_path)
            except Exception as e:
                print(f"[Semantic] Error removing file {file_path}: {e}")
                return
        # Debounce: file removal is a watchdog event — coalesce with other rapid
        # changes rather than immediately triggering a 100-130s project summary pass.
        try:
            self._schedule_project_summary(debounce=True)
        except Exception as e:
            print(f"[Semantic] Error scheduling project summary after file removal: {e}")

    def clear_semantic(self):
        """Wipe all summaries and the ChromaDB store so everything is re-generated."""
        with self._lock:
            try:
                # Drop and recreate the Chroma collections
                self._vectorstore.delete_collection()
                self._vectorstore = Chroma(
                    collection_name=_CHROMA_COLLECTION,
                    embedding_function=self._embeddings,
                    persist_directory=self._chroma_dir,
                )
                self._file_vectorstore.delete_collection()
                self._file_vectorstore = Chroma(
                    collection_name=_FILE_CHROMA_COLLECTION,
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
                con.execute("DELETE FROM project_summary_sources")
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
                con.execute("DELETE FROM project_summary_sources")
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

                # Rebuild file embeddings
                self._file_vectorstore.delete_collection()
                self._file_vectorstore = Chroma(
                    collection_name=_FILE_CHROMA_COLLECTION,
                    embedding_function=self._embeddings,
                    persist_directory=self._chroma_dir,
                )
                con2 = sqlite3.connect(self._db_path, check_same_thread=False)
                con2.row_factory = sqlite3.Row
                file_rows = con2.execute(
                    "SELECT file_path, summary FROM file_summaries ORDER BY file_path"
                ).fetchall()
                con2.close()
                if file_rows:
                    # Normalise paths: resolve any relative paths stored in SQLite
                    # against the project root, then deduplicate by canonical path.
                    seen: dict[str, str] = {}
                    for r in file_rows:
                        fp = r["file_path"]
                        if not os.path.isabs(fp):
                            fp = os.path.normpath(os.path.join(self._root_dir, fp))
                        if fp not in seen:
                            seen[fp] = r["summary"]
                    deduped_rows = list(seen.items())  # [(abs_path, summary), ...]

                    # Query top-level exported symbols for embed text enrichment
                    con_syms = sqlite3.connect(self._db_path, check_same_thread=False)
                    con_syms.row_factory = sqlite3.Row
                    try:
                        sym_rows = con_syms.execute(
                            "SELECT file_path, name FROM symbols "
                            "WHERE (container_name IS NULL OR container_name = '') "
                            "ORDER BY file_path, def_line"
                        ).fetchall()
                    finally:
                        con_syms.close()
                    rebuild_top_syms: dict[str, list[str]] = {}
                    for sr in sym_rows:
                        fp_key = canonical_path(sr["file_path"])
                        bucket = rebuild_top_syms.setdefault(fp_key, [])
                        if len(bucket) < _MAX_FILE_EMBED_SYMBOLS and re.search(
                            r'[a-zA-Z_$]', sr["name"] or ""
                        ):
                            bucket.append(sr["name"])

                    file_texts = [
                        _build_file_embed_text(
                            _safe_relpath(fp, self._root_dir),
                            summary,
                            rebuild_top_syms.get(canonical_path(fp), []),
                        )
                        for fp, summary in deduped_rows
                    ]
                    file_metadatas = [
                        {
                            "file_path": fp,
                            "rel_path": _safe_relpath(fp, self._root_dir),
                            "summary": summary,
                        }
                        for fp, summary in deduped_rows
                    ]
                    file_ids = [
                        "file-" + hashlib.sha256(fp.encode("utf-8")).hexdigest()
                        for fp, _ in deduped_rows
                    ]
                    self._file_vectorstore.add_texts(
                        texts=file_texts, metadatas=file_metadatas, ids=file_ids
                    )
                    print(f"[Semantic] Re-embedded {len(deduped_rows)} file summaries.")

                    # Remove orphaned relative-path entries from SQLite so that
                    # subsequent structural-hash checks work against absolute paths.
                    con3 = sqlite3.connect(self._db_path, check_same_thread=False)
                    try:
                        relative_paths = [
                            r["file_path"] for r in file_rows
                            if not os.path.isabs(r["file_path"])
                        ]
                        if relative_paths:
                            con3.executemany(
                                "DELETE FROM file_summaries WHERE file_path=?",
                                [(p,) for p in relative_paths],
                            )
                            con3.commit()
                            print(f"[Semantic] Removed {len(relative_paths)} orphaned relative-path "
                                  f"entries from file_summaries.")
                    finally:
                        con3.close()

                print("[Semantic] Vector rebuild complete.")
            except Exception as e:
                print(f"[Semantic] Error rebuilding vectors: {e}")

    def get_file_summary(self, file_path: str) -> dict:
        """Return the stored AI summary for a file, or an error dict if not found."""
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT file_path, summary, generated_at, is_stale FROM file_summaries WHERE file_path=?",
                (canonical_path(file_path),),
            ).fetchone()
            con.close()
        except sqlite3.Error as e:
            return {"error": f"Database error: {e}"}
        if row is None:
            return {"error": f"No file summary found for: {file_path}"}
        result: dict = {"file": row["file_path"], "summary": row["summary"]}
        if row["is_stale"]:
            result["is_stale"] = True
            result["stale_note"] = (
                "Summary is not up to date — the file's structure has changed since it was last "
                "generated. Call refresh_file_summary to regenerate it immediately."
            )
        return {"result": result}

    def get_project_summary(self) -> dict:
        """Return the stored AI project-level summary, or an error dict if not found."""
        try:
            con = sqlite3.connect(self._db_path, check_same_thread=False)
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT summary, generated_at FROM project_summary WHERE id=1",
            ).fetchone()
            stale_count_row = con.execute(
                "SELECT COUNT(*) AS n FROM file_summaries WHERE is_stale=1"
            ).fetchone()
            con.close()
        except sqlite3.Error as e:
            return {"error": f"Database error: {e}"}
        if row is None:
            return {"error": "Project summary not yet generated. The indexer may still be running."}
        db_stale = (stale_count_row["n"] if stale_count_row else 0)
        pending = max(db_stale, len(self._pending_stale_paths))
        result: dict = {"summary": row["summary"]}
        if pending > 0:
            result["is_stale"] = True
            result["stale_note"] = (
                f"{pending} file(s) have pending summary updates — the project summary may not "
                "reflect recent changes. Call refresh_project_summary to regenerate immediately."
            )
        return {"result": result}

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
        7. Apply kind-preference boost (type-like queries favour Class/Interface)
        8. Apply exact-name-match multiplier (word-boundary verbatim / full-token)
        9. Apply same-file / same-name diversity decay
        10. Return top-k by final adjusted score
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

        # Pre-compute query info once — used for exact-name-match multiplier
        query_lower = query.lower()
        query_tokens_set = set(_tokenize(query))
        # Stem-expanded query tokens for full-token multiplier check
        query_stems_expanded = set()
        for t in query_tokens_set:
            query_stems_expanded.update(_stem_set(t))
        # Kind-preference: count type-indicating tokens in the query
        type_indicator_count = len(query_tokens_set & _TYPE_QUERY_INDICATORS)

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

            # Vector similarity: convert cosine distance → (0, 1] (higher = closer).
            # Chroma uses cosine distance by default (range [0, 2]); 1/(1+d) gives [1/3, 1].
            vector_sim = 1.0 / (1.0 + float(distance))

            # Keyword relevance from name / container matching
            name_relevance = self._compute_name_relevance(
                query, name, container,
            )

            # Summary relevance: stem-aware token overlap between query and
            # the LLM-generated summary.
            summary_relevance = _compute_summary_relevance(
                query_tokens_set, summary,
            )

            # Blend: vector + keyword (name) + summary
            _VEC_WEIGHT = 1.0 - _KEYWORD_WEIGHT - _SUMMARY_WEIGHT
            hybrid = (_VEC_WEIGHT * vector_sim
                      + _KEYWORD_WEIGHT * name_relevance
                      + _SUMMARY_WEIGHT * summary_relevance)

            # Noise penalty: path/name quality rules
            penalty = self._noise_penalty(file_path, name)
            hybrid /= max(penalty, 1.0)

            # Ref-count boost (more references → higher score), capped to avoid
            # swamping vector similarity with ref-count bias.
            ref_count = ref_count_map.get((file_path, def_line), 0)
            if self._ref_count_boost_weight > 0 and ref_count > 0:
                boost = 1.0 + math.log1p(ref_count) * self._ref_count_boost_weight
                boost = min(boost, 1.35)
                hybrid *= boost

            # Kind-preference boost: when the query describes a type/model/payload,
            # favour Class/Interface/TypeAlias/Enum over Method/Function.
            if type_indicator_count >= 1:
                if kind in _TYPE_KINDS:
                    hybrid *= _KIND_TYPE_BOOST
                elif kind in _IMPL_KINDS and type_indicator_count >= 2:
                    hybrid *= _KIND_IMPL_DEMOTE

            # Exact-name-match multiplier: reward queries that explicitly
            # reference this symbol's name.  Applied after penalty + boost so
            # it overrides noise without being itself amplified by boost.
            # Only fires for names with ≥4 chars to avoid stop-word collisions.
            #
            # Verbatim: whole-word match of the name in the query, scaled by
            # coverage (how much of the query the name represents).
            # Full-token: all camelCase/snake_case tokens of the name appear in
            # the query (stem-aware), requires ≥2 name tokens.
            name_lower = name.lower()
            name_tok_set = set(_tokenize(name))
            if (len(name_lower) >= 4
                    and _word_boundary_match(name_lower, query_lower)
                    and len(name_tok_set) / max(len(query_tokens_set), 1) >= 0.15):
                name_coverage = len(name_tok_set) / max(len(query_tokens_set), 1)
                hybrid *= 1.5 + 1.0 * min(name_coverage, 1.0)
            elif len(name_tok_set) >= 2:
                # Stem-aware full token match: ALL name tokens appear in query.
                matched_count = sum(
                    1 for nt in name_tok_set
                    if _stem_set(nt) & query_stems_expanded
                )
                if matched_count == len(name_tok_set):
                    # Reward names that cover more query tokens
                    coverage_bonus = min(0.3, 0.1 * (matched_count - 1))
                    hybrid *= _NAME_FULL_TOKEN_MULTIPLIER + coverage_bonus

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

        # Promote the adjusted hybrid similarity as the public `score` (higher = better),
        # replacing the raw cosine distance that was set during initial scoring.
        for item in final:
            item["score"] = round(item["_adjusted"], 4)
            item.pop("_hybrid", None)
            item.pop("_adjusted", None)
            item.pop("_file_path", None)

        return {"result": final, "count": len(final)}

    def search_files(self, query: str, limit: int = 10) -> dict:
        """Search files by natural language query using AI-generated file summaries.

        Returns files ranked by a hybrid of semantic similarity and filename keyword
        matching, each entry with the file path and a summary of the file's purpose.

        Over-fetches ``limit * candidate_multiplier`` candidates from the vector
        store and re-ranks them by blending vector similarity with a keyword score
        derived from the filename stem and parent directory tokens.  This mirrors
        the symbol search strategy and significantly improves Hit@1 for file queries
        that contain the target filename or a recognisable word from its path.
        """
        limit = min(max(1, limit), 100)
        fetch_k = limit * self._candidate_multiplier
        _t0 = time.perf_counter()
        try:
            raw_results = self._file_vectorstore.similarity_search_with_score(query, k=fetch_k)
        except Exception as e:
            return {"error": f"File semantic search failed: {e}"}

        latency_ms = round((time.perf_counter() - _t0) * 1000, 1)

        if not raw_results:
            print(
                f"[BENCHMARK:FILE_SEARCH] latency_ms={latency_ms} results=0",
                flush=True,
            )
            return {"result": [], "count": 0}

        # Hybrid re-ranking: blend vector similarity with filename keyword score
        scored: list[dict] = []
        for doc, distance in raw_results:
            meta = doc.metadata
            file_path = meta.get("file_path", "")
            rel_path = meta.get("rel_path", os.path.basename(file_path))
            summary = meta.get("summary", "")

            vector_sim = 1.0 / (1.0 + float(distance))  # cosine distance → (0, 1]

            # Keyword score: match query tokens against filename stem and parent dir
            basename = os.path.basename(file_path)
            stem = os.path.splitext(basename)[0]
            parent = os.path.dirname(rel_path).replace("\\", "/")
            path_score = self._compute_name_relevance(query, stem, parent)

            hybrid = (1.0 - _FILE_KEYWORD_WEIGHT) * vector_sim + _FILE_KEYWORD_WEIGHT * path_score

            scored.append({
                "file":     file_path,
                "rel_path": rel_path,
                "summary":  summary,
                "score":    round(hybrid, 4),
                "_hybrid":  hybrid,
            })

        scored.sort(key=lambda x: x["_hybrid"], reverse=True)
        final = scored[:limit]
        for item in final:
            item.pop("_hybrid", None)

        print(
            f"[BENCHMARK:FILE_SEARCH] latency_ms={latency_ms} results={len(final)}",
            flush=True,
        )
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

    def _generate_file_summaries(self, file_paths: list[str], force: bool = False) -> None:
        """Generate or update file-level summaries for the given files.

        When *force* is ``False`` (incremental / watchdog updates):
          Files whose structural hash has changed are marked ``is_stale=1`` in the
          DB and added to ``_pending_stale_paths``.  Actual LLM regeneration is
          deferred until the count reaches ``_summary_update_threshold``.  When
          the threshold is 0, auto-regeneration is disabled entirely; use the
          ``refresh_file_summary`` / ``refresh_project_summary`` MCP tools instead.

        When *force* is ``True`` (full rebuild or explicit MCP refresh):
          All files whose hash has changed *or* that are already marked stale are
          regenerated immediately.  ``_pending_stale_paths`` is cleared and all
          ``is_stale`` flags are reset to 0 after a successful run.
        """
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

        # ── Phase 1: Compute structural hashes and decide action per file ─────
        # In force=False mode: mark stale in DB, update _pending_stale_paths, check threshold.
        # In force=True mode: collect all files needing regeneration.
        paths_to_regen: list[str] = []  # absolute-normalised paths that need LLM
        stale_db_updated = False

        try:
            for file_path in file_paths:
                norm = canonical_path(file_path)
                query_path = norm
                if not os.path.isabs(norm):
                    abs_norm = os.path.normpath(os.path.join(self._root_dir, norm))
                else:
                    abs_norm = norm

                # Compute structural hash
                rows = con.execute(
                    """SELECT s.name, s.kind_label, s.container_name, s.def_line, ss.summary
                       FROM symbols s
                       LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                       WHERE s.file_path = ?
                       ORDER BY s.def_line""",
                    (query_path,),
                ).fetchall()
                if not rows and query_path != abs_norm:
                    rows = con.execute(
                        """SELECT s.name, s.kind_label, s.container_name, s.def_line, ss.summary
                           FROM symbols s
                           LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                           WHERE s.file_path = ?
                           ORDER BY s.def_line""",
                        (abs_norm,),
                    ).fetchall()
                norm = abs_norm

                if not rows:
                    try:
                        with open(norm, "r", encoding="utf-8", errors="replace") as fh:
                            head = "".join(line for _, line in zip(range(50), fh))
                    except OSError:
                        continue
                    struct_str = "no-symbols:" + head
                else:
                    struct_str = "\n".join(
                        f"{r['def_line']}|{r['kind_label']}|{r['name']}|{r['container_name'] or ''}"
                        for r in rows
                    )
                structural_hash = hashlib.sha256(struct_str.encode("utf-8")).hexdigest()

                existing = con.execute(
                    "SELECT structural_hash, is_stale FROM file_summaries WHERE file_path=?",
                    (norm,),
                ).fetchone()
                hash_changed = (not existing) or (existing["structural_hash"] != structural_hash)
                db_stale = bool(existing and existing["is_stale"])

                if force:
                    if hash_changed or db_stale:
                        paths_to_regen.append(norm)
                else:
                    # Threshold mode
                    if existing and not hash_changed and not db_stale:
                        continue  # Truly up to date
                    if hash_changed and existing:
                        con.execute(
                            "UPDATE file_summaries SET is_stale=1, structural_hash=? WHERE file_path=?",
                            (structural_hash, norm),
                        )
                        stale_db_updated = True
                    self._pending_stale_paths.add(norm)

            if stale_db_updated:
                con.commit()

            if not force:
                n = len(self._pending_stale_paths)
                threshold = self._summary_update_threshold
                if threshold == 0 or n < threshold:
                    if n > 0:
                        if threshold == 0:
                            note = "auto-regen disabled (threshold=0)"
                        else:
                            note = f"{n}/{threshold} threshold"
                        print(
                            f"[Semantic] File summaries deferred: {n} file(s) pending "
                            f"({note}). "
                            "Use refresh_file_summary / refresh_project_summary MCP tools to update.",
                            flush=True,
                        )
                    return
                # Threshold reached: regenerate ALL pending (DB-stale + new/untracked files)
                stale_db_paths = [
                    r[0] for r in con.execute(
                        "SELECT file_path FROM file_summaries WHERE is_stale=1"
                    ).fetchall()
                ]
                db_set = set(stale_db_paths)
                new_file_paths = [p for p in self._pending_stale_paths if p not in db_set]
                paths_to_regen = stale_db_paths + new_file_paths
                print(
                    f"[Semantic] Summary threshold reached "
                    f"({n}/{threshold}), regenerating {len(paths_to_regen)} file(s)...",
                    flush=True,
                )

            if not paths_to_regen:
                if force:
                    self._pending_stale_paths.clear()
                return

            # ── Phase 2: Build LLM messages for files to regenerate ───────────
            messages_list: list[list] = []
            pending_files: list[tuple[str, str]] = []   # (file_path, structural_hash)
            file_top_syms: dict[str, list[str]] = {}

            for norm in paths_to_regen:
                query_path = norm if os.path.isabs(norm) else canonical_path(norm)
                rows = con.execute(
                    """SELECT s.name, s.kind_label, s.container_name, s.def_line, ss.summary
                       FROM symbols s
                       LEFT JOIN symbol_summaries ss ON ss.symbol_id = s.id
                       WHERE s.file_path = ?
                       ORDER BY s.def_line""",
                    (query_path,),
                ).fetchall()
                rel_path = _safe_relpath(norm, self._root_dir)

                if not rows:
                    try:
                        with open(norm, "r", encoding="utf-8", errors="replace") as fh:
                            head = "".join(line for _, line in zip(range(50), fh))
                    except OSError:
                        continue
                    struct_str = "no-symbols:" + head
                    structural_hash = hashlib.sha256(struct_str.encode("utf-8")).hexdigest()
                    user_msg = (
                        f"File: {rel_path}\n"
                        f"Note: No symbols were extracted from this file by the LSP indexer.\n"
                        f"First 50 lines of source:\n```\n{head.rstrip()}\n```"
                    )
                else:
                    struct_str = "\n".join(
                        f"{r['def_line']}|{r['kind_label']}|{r['name']}|{r['container_name'] or ''}"
                        for r in rows
                    )
                    structural_hash = hashlib.sha256(struct_str.encode("utf-8")).hexdigest()
                    sym_lines = [f"File: {rel_path}", "Symbols:"]
                    top_syms: list[str] = []
                    for r in rows:
                        indent = "  " if r["container_name"] else ""
                        sym_line = f"{indent}- [{r['kind_label']}] {r['name']}"
                        if r["summary"]:
                            sym_line += f": {r['summary']}"
                        sym_lines.append(sym_line)
                        if not r["container_name"] and re.search(r'[a-zA-Z_$]', r["name"] or ""):
                            top_syms.append(r["name"])
                    file_top_syms[norm] = top_syms
                    user_msg = "\n".join(sym_lines)
                messages_list.append([
                    SystemMessage(content=_FILE_SYSTEM_PROMPT),
                    HumanMessage(content=user_msg),
                ])
                pending_files.append((norm, structural_hash))

            if not pending_files:
                if force:
                    self._pending_stale_paths.clear()
                return

            # ── Phase 3: LLM batch generation ────────────────────────────────
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
            file_vec_texts: list[str] = []
            file_vec_metadatas: list[dict] = []
            file_vec_ids: list[str] = []
            for (norm, structural_hash), response in zip(pending_files, responses):
                try:
                    summary = response.content.strip()
                    con.execute(
                        """INSERT OR REPLACE INTO file_summaries
                           (file_path, structural_hash, summary, generated_at, is_stale)
                           VALUES (?, ?, ?, ?, 0)""",
                        (norm, structural_hash, summary, now),
                    )
                    rel = _safe_relpath(norm, self._root_dir)
                    file_vec_texts.append(
                        _build_file_embed_text(rel, summary, file_top_syms.get(norm, []))
                    )
                    file_vec_metadatas.append({"file_path": norm, "rel_path": rel, "summary": summary})
                    file_vec_ids.append("file-" + hashlib.sha256(norm.encode("utf-8")).hexdigest())
                except Exception as e:
                    print(f"[Semantic] Failed to save file summary for {norm}: {e}")
            con.commit()

            # Clear all stale flags and in-memory pending set
            self._pending_stale_paths.clear()

            if file_vec_texts:
                try:
                    self._file_vectorstore.add_texts(
                        texts=file_vec_texts,
                        metadatas=file_vec_metadatas,
                        ids=file_vec_ids,
                    )
                except Exception as e:
                    print(f"[Semantic] Failed to upsert file embeddings: {e}")
            print(f"[Semantic] File summaries updated.")
            print(
                f"[BENCHMARK:FILE_SUMMARIES] wall_time={time.perf_counter() - _bm_fs_start:.2f}s "
                f"files_pending={len(pending_files)}",
                flush=True,
            )
        finally:
            con.close()

    def _index_project_summary(self, force: bool = False) -> None:
        """Regenerate the project-level summary if any file summary has changed.

        Uses an **incremental** strategy when possible: if an existing project
        summary exists and only a small fraction of file summaries changed, the
        LLM is asked to *update* the summary based on a compact diff rather
        than regenerating from the full file list.  This dramatically reduces
        prompt size and latency for common edit scenarios.

        When *force* is ``True`` the summaries-hash equality check is skipped so
        that the summary is always regenerated (used by ``refresh_project_summary``).
        """
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
                "SELECT summaries_hash, summary FROM project_summary WHERE id=1"
            ).fetchone()
            if not force and existing and existing["summaries_hash"] == summaries_hash:
                return

            # --- Try incremental update -----------------------------------
            if existing:
                changed, new, removed = self._compute_file_summary_diff(con, rows)
                total_changes = len(changed) + len(new) + len(removed)
                threshold = max(int(len(rows) * _INCREMENTAL_THRESHOLD), 5)
                if 0 < total_changes <= threshold:
                    summary = self._incremental_project_summary(
                        existing["summary"], changed, new, removed,
                    )
                    if summary:
                        self._save_project_summary(con, summary, summaries_hash, rows)
                        print(f"[Semantic] Project summary updated (incremental, {total_changes} change(s)).")
                        print(
                            f"[BENCHMARK:PROJECT_SUMMARY] wall_time={time.perf_counter() - _bm_ps_start:.2f}s "
                            f"mode=incremental changes={total_changes}",
                            flush=True,
                        )
                        return
                    # If incremental failed, fall through to full regeneration

            # --- Full regeneration ----------------------------------------
            print("[Semantic] Generating project summary...")
            file_lines = [
                f"- {_safe_relpath(r['file_path'], self._root_dir)}: {r['summary']}"
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

            self._save_project_summary(con, summary, summaries_hash, rows)
            print("[Semantic] Project summary updated.")
            print(
                f"[BENCHMARK:PROJECT_SUMMARY] wall_time={time.perf_counter() - _bm_ps_start:.2f}s "
                f"mode=full",
                flush=True,
            )
        finally:
            con.close()

    # ------------------------------------------------------------------
    # Project summary helpers
    # ------------------------------------------------------------------

    def _save_project_summary(
        self,
        con: sqlite3.Connection,
        summary: str,
        summaries_hash: str,
        rows: list,
    ):
        """Persist the project summary and a per-file snapshot for future diffs."""
        con.execute(
            """INSERT OR REPLACE INTO project_summary (id, summary, generated_at, summaries_hash)
               VALUES (1, ?, ?, ?)""",
            (summary, time.time(), summaries_hash),
        )
        # Snapshot per-file summary hashes so the next run can compute a diff
        con.execute("DELETE FROM project_summary_sources")
        con.executemany(
            "INSERT INTO project_summary_sources (file_path, summary_hash) VALUES (?, ?)",
            [
                (r["file_path"], hashlib.sha256(r["summary"].encode("utf-8")).hexdigest())
                for r in rows
            ],
        )
        con.commit()

    def _compute_file_summary_diff(
        self,
        con: sqlite3.Connection,
        current_rows: list,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
        """Compare current file summaries to the snapshot from the last project
        summary generation.

        Returns ``(changed, new, removed)`` where *changed* and *new* are lists
        of ``(file_path, summary)`` tuples and *removed* is a list of file paths.
        """
        try:
            snapshot_rows = con.execute(
                "SELECT file_path, summary_hash FROM project_summary_sources"
            ).fetchall()
            stored = {r["file_path"]: r["summary_hash"] for r in snapshot_rows}
        except sqlite3.Error:
            return [], [], []  # No snapshot → can't compute diff

        if not stored:
            return [], [], []

        current_map = {r["file_path"]: r["summary"] for r in current_rows}

        changed: list[tuple[str, str]] = []
        new: list[tuple[str, str]] = []
        removed: list[str] = []

        for fp, summary in current_map.items():
            h = hashlib.sha256(summary.encode("utf-8")).hexdigest()
            if fp not in stored:
                new.append((fp, summary))
            elif stored[fp] != h:
                changed.append((fp, summary))

        for fp in stored:
            if fp not in current_map:
                removed.append(fp)

        return changed, new, removed

    def _incremental_project_summary(
        self,
        old_summary: str,
        changed: list[tuple[str, str]],
        new: list[tuple[str, str]],
        removed: list[str],
    ) -> str | None:
        """Ask the LLM to update the project summary based on a compact diff."""
        parts: list[str] = []
        if new:
            parts.append("New files:")
            for fp, summary in new:
                rel = _safe_relpath(fp, self._root_dir)
                parts.append(f"  + {rel}: {summary}")
        if changed:
            parts.append("Updated files:")
            for fp, summary in changed:
                rel = _safe_relpath(fp, self._root_dir)
                parts.append(f"  ~ {rel}: {summary}")
        if removed:
            parts.append("Removed files:")
            for fp in removed:
                rel = _safe_relpath(fp, self._root_dir)
                parts.append(f"  - {rel}")

        changes_text = "\n".join(parts)
        user_msg = (
            f"Current project summary:\n\n{old_summary}\n\n"
            f"Changes:\n{changes_text}"
        )

        try:
            response = self._llm.invoke([
                SystemMessage(content=_INCREMENTAL_PROJECT_SYSTEM_PROMPT),
                HumanMessage(content=user_msg),
            ])
            return response.content.strip()
        except Exception as e:
            print(f"[Semantic] Incremental project summary LLM call failed: {e}")
            return None

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

        # pre_errors: context-extraction failures (real errors).
        # Hash-matched symbols are accounted for as "skipped" by the caller
        # (skipped = len(batch) - ok - err), not as errors.
        return len(ok_syms), errors + pre_errors

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
        norm = canonical_path(file_path)
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.execute("PRAGMA foreign_keys=ON")
        con.row_factory = sqlite3.Row
        # NOTE: row_factory=Row is required for r["chroma_id"] below
        try:
            rows = con.execute(
                "SELECT chroma_id FROM symbol_summaries WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE file_path=?)",
                (norm,),
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
                (norm,),
            )
            # Also remove the file-level summary so the project summary
            # is regenerated and no longer references the deleted file.
            con.execute("DELETE FROM file_summaries WHERE file_path=?", (norm,))
            con.commit()
            # Remove from file vector store
            file_chroma_id = "file-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()
            try:
                self._file_vectorstore.delete(ids=[file_chroma_id])
            except Exception:
                pass
        finally:
            con.close()
