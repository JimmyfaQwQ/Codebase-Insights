"""
semantic_config.py

Unified configuration for AI-powered semantic indexing.
Reads settings from a TOML config file and provides factory functions
for creating LangChain LLM and Embedding instances.

Config file: .codebase-insights.toml  (project root)

Chat and embedding providers are configured independently, allowing
mixed setups such as OpenAI for chat + Ollama for embeddings.
"""

import os
import tomllib

from langchain_core.language_models import BaseChatModel
from langchain_core.embeddings import Embeddings

_CONFIG_FILENAME = ".codebase-insights.toml"
_LOCK_FILENAME   = ".codebase-insights.lock.toml"

_DEFAULT_CONFIG: dict = {
    "chat": {
        "provider": "ollama",
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "qwen2.5",
        },
        "openai-compatible": {
            "base_url": "",
            "model": "gpt-4o-mini",
        },
    },
    "embed": {
        "provider": "ollama",
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "bge-m3",
        },
        "openai-compatible": {
            "base_url": "",
            "model": "text-embedding-3-small",
        },
    },
    "semantic": {
        "index_kinds": [
            # "Property" intentionally omitted: TypeScript/JS LSPs report local
            # variable declarations, destructured parameters, and JSX attribute
            # values as Property symbols, generating enormous amounts of
            # low-signal noise. Add it back in your config only if you
            # specifically need class-property indexing.
            "Class", "Method", "Function", "Interface",
            "Enum", "Constructor",
        ],
        "concurrency": 16,
        "batch_size": 16,
        "min_ref_count": 3,
        # Number of files whose symbol structure must change before file/project
        # summaries are automatically regenerated during watchdog updates.
        # Set to 1 to regenerate on every single change (original behaviour).
        # Set to 0 to disable auto-regeneration entirely (use MCP refresh tools).
        "summary_update_threshold": 5,
        # Seconds a single file must be idle (no further symbol changes) before
        # its stale summary is automatically regenerated.  0 = disabled.
        "summary_file_idle_timeout": 30,
        # Seconds the whole project must be idle (no watchdog events at all)
        # before all stale file summaries and the project summary are regenerated.
        # 0 = disabled.
        "summary_project_idle_timeout": 300,
    },
    "ranking": {
        "default_penalty": 1.0,
        "anonymous_name_penalty": 1.5,
        # Fetch this many extra candidates from the vector store before
        # re-ranking; higher = better recall at cost of more DB lookups.
        "candidate_multiplier": 5,
        # ref_count boost: adjusted_score = raw_score * penalty / log(1 + ref_count * weight)
        # Set to 0 to disable the boost entirely.
        "ref_count_boost_weight": 0.3,
        "file_suffix": {
            ".d.ts": 3.0,
            ".min.js": 2.5,
            ".min.css": 2.5,
        },
        "path_fragment": {
            "node_modules/": 1.8,
            "/dist/": 1.8,
            "/build/": 1.8,
            "/.next/": 1.8,
            "/out/": 1.5,
            "/.cache/": 1.5,
            ".generated.": 2.0,
            "_generated.": 2.0,
        },
        "anonymous_names": {
            # Matches symbol names that are LSP-generated artefacts rather than
            # real declarations:
            #   <unknown>, <anonymous>, <lambda>   — angle-bracket pseudo-names
            #   map() callback, setModalDraft() callback  — TS callback closures
            #   .taskCards.map() callback          — dot-prefixed method chains
            #   pure numbers, $0, __, callback, handler, wrapper (exact match)
            "patterns": [
                r"^(\$\d*|_+\d*|<[^>]*>|anonymous|\d+|callback|handler|wrapper)$",
                r".*\s+callback$",
                r"^\.",
            ],
        },
    },
}

# Module-level cache – populated by load_config()
_config: dict = {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base* (non-destructive)."""
    merged = base.copy()
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config(root_dir: str, force_new: bool = False) -> dict:
    """Load the TOML config file from *root_dir*.

    If the file does not exist (or *force_new* is True), run an interactive
    setup wizard to create it, then load the newly written file.
    """
    global _config
    config_path = os.path.join(root_dir, _CONFIG_FILENAME)
    if force_new or not os.path.isfile(config_path):
        _interactive_setup(config_path)
    user: dict = {}
    if os.path.isfile(config_path):
        with open(config_path, "rb") as f:
            user = tomllib.load(f)
        print(f"[Config] Loaded {config_path}")
    _config = _deep_merge(_DEFAULT_CONFIG, user)
    return _config


def get_config() -> dict:
    """Return the currently loaded config (call load_config first)."""
    return _config or _DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Factory: LLM (chat)
# ---------------------------------------------------------------------------

def create_llm() -> BaseChatModel:
    cfg = get_config()
    provider = cfg["chat"]["provider"].strip().lower()

    if provider in ("openai", "openai-compatible"):
        from langchain_openai import ChatOpenAI

        # Support both old "openai" and new "openai-compatible" section names.
        c = cfg["chat"].get("openai-compatible") or cfg["chat"].get("openai", {})
        api_key = (
            os.environ.get("CODEBASE_INSIGHTS_CHAT_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "OpenAI-compatible API key not found. "
                "Set the CODEBASE_INSIGHTS_CHAT_API_KEY or OPENAI_API_KEY "
                "environment variable."
            )
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"[Config] Using OpenAI-compatible chat model '{c.get('model', 'gpt-4o-mini')}' (key: {masked})")
        return ChatOpenAI(
            model=c.get("model", "gpt-4o-mini"),
            api_key=api_key,
            base_url=c.get("base_url") or None,
            temperature=0,
        )

    # Default: Ollama
    from langchain_ollama import ChatOllama

    c = cfg["chat"]["ollama"]
    return ChatOllama(
        model=c.get("model", "qwen2.5"),
        base_url=c.get("base_url", "http://localhost:11434"),
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Factory: Embeddings
# ---------------------------------------------------------------------------

def create_embeddings() -> Embeddings:
    cfg = get_config()
    provider = cfg["embed"]["provider"].strip().lower()

    if provider in ("openai", "openai-compatible"):
        from langchain_openai import OpenAIEmbeddings

        # Support both old "openai" and new "openai-compatible" section names.
        c = cfg["embed"].get("openai-compatible") or cfg["embed"].get("openai", {})
        api_key = (
            os.environ.get("CODEBASE_INSIGHTS_EMBED_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "OpenAI-compatible API key not found. "
                "Set the CODEBASE_INSIGHTS_EMBED_API_KEY or OPENAI_API_KEY "
                "environment variable."
            )
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"[Config] Using OpenAI-compatible embeddings model '{c.get('model', 'text-embedding-3-small')}' (key: {masked})")
        return OpenAIEmbeddings(
            model=c.get("model", "text-embedding-3-small"),
            api_key=api_key,
            base_url=c.get("base_url") or None,
        )

    # Default: Ollama
    from langchain_ollama import OllamaEmbeddings

    c = cfg["embed"]["ollama"]
    return OllamaEmbeddings(
        model=c.get("model", "bge-m3"),
        base_url=c.get("base_url", "http://localhost:11434"),
    )


# ---------------------------------------------------------------------------
# Semantic index settings
# ---------------------------------------------------------------------------

def semantic_index_kinds() -> set[str]:
    cfg = get_config()
    kinds = cfg.get("semantic", {}).get("index_kinds", [])
    return {k.strip().capitalize() for k in kinds if k.strip()}


def semantic_concurrency() -> int:
    return int(get_config().get("semantic", {}).get("concurrency", 4))


def semantic_batch_size() -> int:
    return int(get_config().get("semantic", {}).get("batch_size", 20))


def semantic_min_ref_count() -> int:
    return int(get_config().get("semantic", {}).get("min_ref_count", 1))


def semantic_summary_update_threshold() -> int:
    """Return the number of structurally-changed files required to trigger
    an automatic file/project summary regeneration during watchdog updates.

    0 = never auto-regenerate (use refresh MCP tools only)
    1 = regenerate on every single change (original behaviour)
    N = accumulate N changes before regenerating (default: 5)
    """
    return int(get_config().get("semantic", {}).get("summary_update_threshold", 5))


def semantic_summary_file_idle_timeout() -> int:
    """Return seconds a file must be idle (no further symbol changes) before
    its stale summary is automatically regenerated.  0 = disabled (default: 30).
    """
    return int(get_config().get("semantic", {}).get("summary_file_idle_timeout", 30))


def semantic_summary_project_idle_timeout() -> int:
    """Return seconds the project must be idle (no watchdog events at all)
    before all stale summaries are regenerated.  0 = disabled (default: 300).
    """
    return int(get_config().get("semantic", {}).get("summary_project_idle_timeout", 300))


# ---------------------------------------------------------------------------
# Ranking / noise-penalty settings
# ---------------------------------------------------------------------------

import re as _re


def ranking_config() -> dict:
    """Return the full ``[ranking]`` section, merged with defaults."""
    return get_config().get("ranking", {})


def ranking_file_suffix_penalties() -> dict[str, float]:
    """Return ``{suffix: penalty}`` from ``[ranking.file_suffix]``."""
    raw = ranking_config().get("file_suffix", {})
    return {k.lower(): float(v) for k, v in raw.items()}


def ranking_path_fragment_penalties() -> list[tuple[str, float]]:
    """Return ``[(fragment, penalty), ...]`` from ``[ranking.path_fragment]``."""
    raw = ranking_config().get("path_fragment", {})
    return [(k, float(v)) for k, v in raw.items()]


def ranking_anonymous_name_re() -> _re.Pattern | None:
    """Compile a combined regex from ``[ranking.anonymous_names].patterns``."""
    patterns = ranking_config().get("anonymous_names", {}).get("patterns", [])
    if not patterns:
        return None
    combined = "|".join(f"(?:{p})" for p in patterns)
    return _re.compile(combined, _re.IGNORECASE)


def ranking_default_penalty() -> float:
    return float(ranking_config().get("default_penalty", 1.0))


def ranking_anonymous_name_penalty() -> float:
    return float(ranking_config().get("anonymous_name_penalty", 1.5))


def ranking_candidate_multiplier() -> int:
    return max(1, int(ranking_config().get("candidate_multiplier", 3)))


def ranking_ref_count_boost_weight() -> float:
    return float(ranking_config().get("ref_count_boost_weight", 0.3))


# ---------------------------------------------------------------------------
# Interactive setup wizard
# ---------------------------------------------------------------------------

def _prompt(msg: str, default: str = "") -> str:
    """Print a prompt and return user input (or *default* if empty)."""
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {msg}{suffix}: ").strip()
    return answer or default


def _prompt_choice(msg: str, choices: list[str], default: str) -> str:
    """Present numbered choices, return the selected value."""
    print(f"\n  {msg}")
    for i, c in enumerate(choices, 1):
        marker = " (default)" if c == default else ""
        print(f"    {i}) {c}{marker}")
    while True:
        raw = input(f"  Enter choice [1-{len(choices)}, default={choices.index(default)+1}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        print("    Invalid choice, try again.")


def _wizard_provider_section(role: str, default_provider: str, defaults: dict) -> dict:
    """Wizard sub-flow for one provider section (chat or embed).

    Returns a dict with keys: provider, ollama, openai-compatible.
    """
    # Normalise legacy "openai" default so the choice list stays consistent.
    normalised_default = "openai-compatible" if default_provider == "openai" else default_provider
    provider = _prompt_choice(
        f"Provider for {role}?",
        ["ollama", "openai-compatible"],
        default=normalised_default,
    )

    ollama = dict(defaults["ollama"])
    compat = dict(defaults.get("openai-compatible") or defaults.get("openai") or {})

    if provider == "ollama":
        print(f"\n  -- {role.capitalize()} · Ollama --")
        ollama["base_url"] = _prompt("Base URL", ollama["base_url"])
        ollama["model"]    = _prompt("Model", ollama["model"])
    else:
        print(f"\n  -- {role.capitalize()} · OpenAI-compatible --")
        env_specific = f"CODEBASE_INSIGHTS_{role.upper()}_API_KEY"
        env_generic  = "OPENAI_API_KEY"
        print(f"  API key must be set as an environment variable (not stored in config).")
        print(f"  Accepted variables (higher priority first):")
        print(f"    1) {env_specific}")
        print(f"    2) {env_generic}")
        found_key = os.environ.get(env_specific) or os.environ.get(env_generic)
        if found_key:
            masked = found_key[:6] + "..." + found_key[-4:] if len(found_key) > 10 else "***"
            print(f"  [OK] API key detected in environment (masked: {masked})")
        else:
            print(f"  [WARNING] Neither {env_specific} nor {env_generic} is currently set.")
            print(f"  Please export one of those variables before starting Codebase Insights.")
        compat["base_url"] = _prompt("Base URL (blank = official OpenAI endpoint)", compat.get("base_url", ""))
        compat["model"]    = _prompt("Model", compat.get("model", "gpt-4o-mini"))

    return {"provider": provider, "ollama": ollama, "openai-compatible": compat}


def _write_provider_block(lines: list[str], section: str, cfg: dict):
    """Append a [section] TOML block to *lines*."""
    compat = cfg.get("openai-compatible") or cfg.get("openai") or {}
    env_var = f"CODEBASE_INSIGHTS_{section.upper()}_API_KEY"
    lines += [
        f"[{section}]",
        f'provider = "{cfg["provider"]}"',
        "",
        f"[{section}.ollama]",
        f'base_url = "{cfg["ollama"]["base_url"]}"',
        f'model    = "{cfg["ollama"]["model"]}"',
        "",
        f"[{section}.openai-compatible]",
        f"# API key is read from the {env_var} or OPENAI_API_KEY environment variable.",
        f"# Do not store API keys in this file.",
        f'base_url = "{compat.get("base_url", "")}"',
        f'model    = "{compat.get("model", "gpt-4o-mini")}"',
        "",
    ]


def _interactive_setup(config_path: str):
    """Guide the user through first-time configuration and write the TOML file."""
    print("\n" + "=" * 60)
    print("  Codebase-Insights — First-time Setup")
    print("=" * 60)
    print(f"\n  No config file found. Let's create {os.path.basename(config_path)}.")
    print("  Chat (summarisation) and embedding providers can be set independently.\n")

    chat_cfg  = _wizard_provider_section("chat",  "ollama", _DEFAULT_CONFIG["chat"])
    embed_cfg = _wizard_provider_section("embed", "ollama", _DEFAULT_CONFIG["embed"])

    # -- Semantic kinds --
    default_kinds = _DEFAULT_CONFIG["semantic"]["index_kinds"]
    print(f"\n  Symbol kinds to index (default: {', '.join(default_kinds)})")
    if input("  Keep defaults? [Y/n]: ").strip().lower() == "n":
        raw   = input("  Enter comma-separated kinds: ").strip()
        kinds = [k.strip() for k in raw.split(",") if k.strip()] or default_kinds
    else:
        kinds = default_kinds

    # -- Performance --
    print("\n  -- Performance --")
    concurrency   = int(_prompt("Parallel LLM requests (1 recommended for Ollama)", str(_DEFAULT_CONFIG["semantic"]["concurrency"])))
    batch_size    = int(_prompt("Symbols per batch", str(_DEFAULT_CONFIG["semantic"]["batch_size"])))
    min_ref_count = int(_prompt("Min references to index a symbol (0 = no filter)", str(_DEFAULT_CONFIG["semantic"]["min_ref_count"])))

    # -- Write TOML --
    lines = ["# Codebase-Insights configuration (auto-generated by setup wizard)", ""]
    _write_provider_block(lines, "chat",  chat_cfg)
    _write_provider_block(lines, "embed", embed_cfg)
    lines += [
        "[semantic]",
        "index_kinds = [",
    ]
    for k in kinds:
        lines.append(f'    "{k}",')
    lines += [
        "]",
        f"concurrency   = {concurrency}",
        f"batch_size    = {batch_size}",
        f"min_ref_count = {min_ref_count}",
        "",
    ]

    with open(config_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n  Config saved to {config_path}")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Embed config lock file
# ---------------------------------------------------------------------------

def _embed_fingerprint(cfg: dict) -> dict:
    """Extract the embed-relevant fields that affect the ChromaDB vectors."""
    embed = cfg.get("embed", {})
    provider = embed.get("provider", "ollama").strip().lower()
    if provider in ("openai", "openai-compatible"):
        c = embed.get("openai-compatible") or embed.get("openai") or {}
        return {
            "provider": "openai-compatible",
            "base_url": c.get("base_url", "").strip(),
            "model":    c.get("model", "text-embedding-3-small").strip(),
        }
    # ollama (default)
    c = embed.get("ollama", {})
    return {
        "provider": "ollama",
        "base_url": c.get("base_url", "http://localhost:11434").strip(),
        "model":    c.get("model", "bge-m3").strip(),
    }


def check_embed_lock(root_dir: str) -> None:
    """Compare the current embed config against the lock file.

    If the lock file exists and the embed fingerprint has changed, print an
    error message and raise ``SystemExit`` so the caller can abort cleanly.
    Does nothing when the lock file is absent (first run).
    """
    lock_path = os.path.join(root_dir, _LOCK_FILENAME)
    if not os.path.isfile(lock_path):
        return  # first run — no lock yet
    with open(lock_path, "rb") as f:
        locked: dict = tomllib.load(f).get("embed", {})
    current = _embed_fingerprint(get_config())
    if locked != current:
        print("\n[Config] ERROR: The embedding configuration has changed since the last run.")
        print(f"  Locked : provider={locked.get('provider')}  model={locked.get('model')}  base_url={locked.get('base_url')}")
        print(f"  Current: provider={current['provider']}  model={current['model']}  base_url={current['base_url']}")
        print("\n  The existing ChromaDB vectors are no longer compatible with the new model.")
        print("  Please re-run with --rebuild-vectors to re-embed all summaries, or revert")
        print(f"  the embed settings in {_CONFIG_FILENAME}.\n")
        raise SystemExit(1)


def write_embed_lock(root_dir: str) -> None:
    """Write (or overwrite) the embed lock file with the current config."""
    fp = _embed_fingerprint(get_config())
    lock_path = os.path.join(root_dir, _LOCK_FILENAME)
    lines = [
        "# codebase-insights embed lock file (auto-generated — do not edit manually)",
        "# This records the embedding model used to build the ChromaDB vector store.",
        "# If the embed config in .codebase-insights.toml changes, re-run with",
        "# --rebuild-vectors to regenerate the vectors with the new model.",
        "",
        "[embed]",
        f'provider = "{fp["provider"]}"',
        f'model    = "{fp["model"]}"',
        f'base_url = "{fp["base_url"]}"',
        "",
    ]
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[Config] Embed lock written to {_LOCK_FILENAME}")
