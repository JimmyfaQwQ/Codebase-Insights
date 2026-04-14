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

_DEFAULT_CONFIG: dict = {
    "chat": {
        "provider": "ollama",
        "ollama": {
            "base_url": "http://localhost:11434",
            "model": "qwen2.5",
        },
        "openai": {
            "api_key": "",
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
        "openai": {
            "api_key": "",
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

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        c = cfg["chat"]["openai"]
        # Prefer environment variable over config-file value so that API keys
        # are never required to be stored in plain text on disk.
        api_key = (
            os.environ.get("CODEBASE_INSIGHTS_CHAT_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or c.get("api_key", "")
        )
        if not api_key:
            raise RuntimeError(
                f"OpenAI API key not found. Set the CODEBASE_INSIGHTS_CHAT_API_KEY "
                f"or OPENAI_API_KEY environment variable, or add chat.openai.api_key "
                f"to {_CONFIG_FILENAME}."
            )
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"[Config] Using OpenAI chat model '{c.get('model', 'gpt-4o-mini')}' (key: {masked})")
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

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        c = cfg["embed"]["openai"]
        # Prefer environment variable over config-file value.
        api_key = (
            os.environ.get("CODEBASE_INSIGHTS_EMBED_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or c.get("api_key", "")
        )
        if not api_key:
            raise RuntimeError(
                f"OpenAI API key not found. Set the CODEBASE_INSIGHTS_EMBED_API_KEY "
                f"or OPENAI_API_KEY environment variable, or add embed.openai.api_key "
                f"to {_CONFIG_FILENAME}."
            )
        masked = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"[Config] Using OpenAI embeddings model '{c.get('model', 'text-embedding-3-small')}' (key: {masked})")
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

    Returns a dict with keys: provider, ollama, openai.
    """
    provider = _prompt_choice(
        f"Provider for {role}?",
        ["ollama", "openai"],
        default=default_provider,
    )

    ollama = dict(defaults["ollama"])
    openai = dict(defaults["openai"])

    if provider == "ollama":
        print(f"\n  -- {role.capitalize()} · Ollama --")
        ollama["base_url"] = _prompt("Base URL", ollama["base_url"])
        ollama["model"]    = _prompt("Model", ollama["model"])
    else:
        print(f"\n  -- {role.capitalize()} · OpenAI-compatible --")
        print("  Tip: leave the API key blank and set OPENAI_API_KEY (or")
        print(f"       CODEBASE_INSIGHTS_{role.upper()}_API_KEY - Higher priority) in your environment instead.")
        raw_key = _prompt("API key (or leave blank to use env var)", "")
        openai["api_key"]  = raw_key  # may be blank; env var takes priority at runtime
        openai["base_url"] = _prompt("Base URL (blank = official OpenAI)", openai["base_url"])
        openai["model"]    = _prompt("Model", openai["model"])

    return {"provider": provider, "ollama": ollama, "openai": openai}


def _write_provider_block(lines: list[str], section: str, cfg: dict):
    """Append a [section] TOML block to *lines*."""
    lines += [
        f"[{section}]",
        f'provider = "{cfg["provider"]}"',
        "",
        f"[{section}.ollama]",
        f'base_url = "{cfg["ollama"]["base_url"]}"',
        f'model    = "{cfg["ollama"]["model"]}"',
        "",
        f"[{section}.openai]",
        f'api_key  = "{cfg["openai"]["api_key"]}"',
        f'base_url = "{cfg["openai"]["base_url"]}"',
        f'model    = "{cfg["openai"]["model"]}"',
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
