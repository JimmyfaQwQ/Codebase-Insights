"""Entry point for the codebase-insights CLI.

The CLI defaults to a Textual-based TUI which routes logs from each subsystem
(LSP / Indexer / Semantic / HTTP libraries / Benchmark / Config) into separate
scrollable panels with a dedicated progress area. Pass ``--no-tui`` to fall
back to a plain stdout stream (useful for CI, benchmarks, and recordings).
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time

from . import LSP
from . import cli_io
from . import language_analysis
from . import mcp_server
from . import semantic_config
from . import semantic_indexer as semantic_indexer_mod
from . import workspace_indexer
from .workspace_indexer import canonical_path


language_servers = {
    language_analysis.Language.PYTHON: ["pylsp"],
    language_analysis.Language.JS_TS: ["typescript-language-server", "--stdio"],
    language_analysis.Language.CPP: ["clangd"],
    language_analysis.Language.RUST: ["rust-analyzer"],
}

language_servers_install_guides = {
    language_analysis.Language.PYTHON: "https://github.com/python-lsp/python-lsp-server",
    language_analysis.Language.JS_TS: "https://github.com/theia-ide/typescript-language-server",
    language_analysis.Language.CPP: "https://clangd.llvm.org/installation.html",
    language_analysis.Language.RUST: "https://rust-analyzer.github.io/manual.html#rust-analyzer-language-server-configuration",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Codebase-Insights: LSP-powered code intelligence with AI semantic search",
    )
    parser.add_argument("project_root", help="Path to the project root directory")
    parser.add_argument("--new-config", action="store_true", dest="new_config",
                        help="Re-run the interactive configuration wizard (overwrites existing config)")
    parser.add_argument("--rebuild-index", action="store_true", dest="rebuild_index",
                        help="Clear the symbol index and perform a full re-index on startup")
    parser.add_argument("--rebuild-semantic", action="store_true", dest="rebuild_semantic",
                        help="Clear all AI summaries and ChromaDB vectors, then re-generate from scratch")
    parser.add_argument("--rebuild-summaries", action="store_true", dest="rebuild_summaries",
                        help="Clear only file/project summaries and re-generate them (symbol summaries and ChromaDB are kept)")
    parser.add_argument("--rebuild-vectors", action="store_true", dest="rebuild_vectors",
                        help="Re-embed all existing LLM summaries into a fresh ChromaDB collection (no LLM calls; use after changing the embedding model)")
    parser.add_argument("--port", type=int, default=6789,
                        help="Port for the MCP HTTP server (default: 6789)")
    parser.add_argument("--host", type=str, default="127.0.0.1",
                        help="Host address for the MCP HTTP server (default: 127.0.0.1)")
    parser.add_argument("--no-tui", action="store_true", dest="no_tui",
                        help="Disable the Textual TUI and stream all logs to stdout (handy for CI/benchmarks)")
    return parser.parse_args()


def _ensure_languages_have_servers(detected_languages) -> bool:
    """Returns True if every detected language has its LSP available in PATH."""
    failed = False
    for language in detected_languages:
        server_executable = language_servers[language][0]
        if not shutil.which(server_executable):
            download_link = language_servers_install_guides.get(language, "Unknown")
            print(f"[Status] Error: {server_executable} not found in PATH. "
                  f"Please install it to enable {language.value} support.")
            print(f"[Status] Installation guide: {download_link}")
            failed = True
    return not failed


def _bootstrap(args: argparse.Namespace, project_root: str):
    """Detect languages, run config wizard, build LLM/embeddings.

    Returns ``(detected_languages, sem_indexer)``. May call ``sys.exit`` on
    fatal user-visible errors.

    This runs *before* the TUI starts because the wizard uses ``input()``.
    """
    print(f"[Status] Detecting languages in project at: {project_root}...")
    detected_languages = language_analysis.detect_languages(project_root)
    if detected_languages:
        print("[Status] Detected languages:")
        for language in detected_languages:
            print(f"[Status]   - {language.value}")
    else:
        print("[Status] No currently supported languages detected.")

    if not _ensure_languages_have_servers(detected_languages):
        sys.exit(1)

    sem_indexer_factory = None
    try:
        cfg = semantic_config.load_config(project_root, force_new=args.new_config)
        if not args.rebuild_vectors:
            semantic_config.check_embed_lock(project_root)
        # Build llm/embeddings now (so OpenAI key errors surface pre-TUI),
        # but defer SemanticIndexer construction to the worker so its own
        # init logs land in the proper TUI panel.
        llm = semantic_config.create_llm()
        embeddings = semantic_config.create_embeddings()

        def sem_indexer_factory():  # noqa: E306 - inline factory
            inst = semantic_indexer_mod.SemanticIndexer(project_root, llm, embeddings)
            print(f"[Semantic] Semantic indexer initialised "
                  f"(chat={cfg['chat']['provider']}, embed={cfg['embed']['provider']})")
            return inst
    except SystemExit:
        raise
    except Exception as e:
        print(f"[Semantic] Could not initialise semantic indexer: {e}")
        print("[Semantic] Continuing without AI-powered search.")
        sem_indexer_factory = None

    return detected_languages, sem_indexer_factory


def _do_startup_work(args: argparse.Namespace, project_root: str,
                     detected_languages, sem_indexer_factory,
                     tui_app=None):
    """Heavy startup: spin up LSP clients, indexer, and finally the MCP server.

    Designed to run on a worker thread so the TUI stays responsive. Returns
    once the MCP server exits. Pass ``tui_app`` to drive Overview panel updates.
    """

    def _ov(**fields):
        """Push overview fields if a TUI app is attached."""
        if tui_app is not None:
            tui_app.update_overview(fields)

    _bm_t0 = time.perf_counter()
    lsp_clients: dict[language_analysis.Language, LSP.LSPClient] = {}
    indexer = None
    sem_indexer = None

    # Overview — language list (already detected in _bootstrap)
    lang_names = ", ".join(l.value for l in detected_languages) or "none detected"
    _ov(**{"project.languages": lang_names,
           "indexer.status": "starting LSP…"})

    print("[Status] Starting language servers...")
    _bm_lsp_timings: dict[str, dict[str, float]] = {}
    for language in detected_languages:
        server_cmd = language_servers[language]
        client = LSP.LSPClient(language, server_cmd)
        _bm_lsp_s = time.perf_counter()
        client.start_server()
        _bm_lsp_started = time.perf_counter()
        client.initialize(project_root)
        _bm_lsp_inited = time.perf_counter()
        _bm_lsp_timings[language.value] = {
            "start_server": _bm_lsp_started - _bm_lsp_s,
            "initialize": _bm_lsp_inited - _bm_lsp_started,
        }
        lsp_clients[language] = client
        print(f"[LSP] Initialized {language.value} client: {' '.join(server_cmd)}")

    _bm_t_lsp_done = time.perf_counter()
    _ov(**{"indexer.status": "loading semantic config…"})

    if sem_indexer_factory is not None:
        try:
            sem_indexer = sem_indexer_factory()
            cfg = semantic_config.get_config()
            chat_info = f"{cfg['chat']['provider']} / {cfg['chat'].get(cfg['chat']['provider'], {}).get('model', '?')}"
            embed_info = f"{cfg['embed']['provider']} / {cfg['embed'].get(cfg['embed']['provider'], {}).get('model', '?')}"
            _ov(**{
                "config.chat":       chat_info,
                "config.embed":      embed_info,
                "semantic.status":   "idle (pending initial index)",
            })
        except Exception as e:
            print(f"[Semantic] Could not initialise semantic indexer: {e}")
            sem_indexer = None
            _ov(**{"semantic.status": f"unavailable ({e})"})
    else:
        _ov(**{"semantic.status": "disabled (no config)"})
    _bm_t_sem_init = time.perf_counter()

    _ov(**{"indexer.status": "initialising…"})
    indexer = workspace_indexer.WorkspaceIndexer(
        project_root, lsp_clients, semantic_indexer=sem_indexer
    )
    _bm_t_wi_created = time.perf_counter()

    if args.rebuild_index:
        indexer.clear_index()

    if args.rebuild_semantic and sem_indexer is not None:
        sem_indexer.clear_semantic()
    elif args.rebuild_vectors and sem_indexer is not None:
        sem_indexer.rebuild_vectors()
        semantic_config.write_embed_lock(project_root)
    elif args.rebuild_summaries and sem_indexer is not None:
        sem_indexer.clear_project_summaries()

    semantic_config.write_embed_lock(project_root)

    _ov(**{"indexer.status": "initial pass running…",
           "server.status":  "waiting for indexer…"})
    indexer.start()
    _bm_t_started = time.perf_counter()

    print(f"[BENCHMARK:STARTUP] semantic_init={_bm_t_sem_init - _bm_t_lsp_done:.3f}s "
          f"indexer_create={_bm_t_wi_created - _bm_t_sem_init:.3f}s "
          f"total_pre_server={_bm_t_started - _bm_t0:.3f}s")
    for _bm_lang, _bm_lt in _bm_lsp_timings.items():
        print(f"[BENCHMARK:STARTUP] lsp_{_bm_lang}_start_server={_bm_lt['start_server']:.3f}s "
              f"lsp_{_bm_lang}_initialize={_bm_lt['initialize']:.3f}s")

    server_url = f"http://{args.host}:{args.port}/mcp"
    print(f"[Status] MCP server starting on {server_url}")
    _ov(**{"server.url": server_url, "server.status": "starting…"})

    # Start a monitor thread that pushes live stats to the Overview panel.
    if tui_app is not None:
        import socket as _socket
        import sqlite3 as _sqlite3

        def _monitor():
            try:
                _monitor_impl()
            except Exception as _exc:
                print(f"[Status] monitor thread crashed: {_exc}")

        def _monitor_impl():
            # Phase 1: wait for initial indexer pass to complete.
            while not indexer._initial_pass_done:
                time.sleep(0.5)
            # Count scanned files (file_hashes) and LSP symbols from the workspace DB.
            try:
                with _sqlite3.connect(indexer._db_path, check_same_thread=False) as _con:
                    (_n_files,) = _con.execute(
                        "SELECT COUNT(*) FROM file_hashes"
                    ).fetchone()
                    (_n_syms,) = _con.execute(
                        "SELECT COUNT(*) FROM symbols"
                    ).fetchone()
            except Exception:
                _n_files, _n_syms = "?", "?"
            _ov(**{
                "indexer.status":  "watching for changes",
                "indexer.files":   str(_n_files),
                "indexer.symbols": str(_n_syms),
            })
            if sem_indexer is not None:
                _ov(**{"semantic.status": "indexing…"})

            # Phase 2: wait for the MCP server port to open.
            _deadline = time.monotonic() + 60
            while time.monotonic() < _deadline:
                try:
                    _s = _socket.create_connection((args.host, args.port), timeout=1)
                    _s.close()
                    _ov(**{"server.status": "running ✓"})
                    break
                except (ConnectionRefusedError, OSError, TimeoutError):
                    time.sleep(0.5)
            else:
                _ov(**{"server.status": "not reachable (timeout)"})

            # Phase 3: continuous live monitoring of indexer + semantic state.
            # Runs forever (daemon thread) so re-indexing cycles are always visible.
            while True:
                time.sleep(2)

                # Always refresh file/symbol counts (file_hashes always exists).
                try:
                    with _sqlite3.connect(indexer._db_path, check_same_thread=False) as _con:
                        (_n_f,) = _con.execute(
                            "SELECT COUNT(*) FROM file_hashes"
                        ).fetchone()
                        (_n_s,) = _con.execute(
                            "SELECT COUNT(*) FROM symbols"
                        ).fetchone()
                    _ov(**{
                        "indexer.files":   str(_n_f),
                        "indexer.symbols": str(_n_s),
                    })
                except Exception as _exc:
                    print(f"[Status] monitor symbols query error: {_exc}")

                # Only query file_summaries when semantic AI is configured.
                _n_sum: int | str = "—"
                _stale_rows: list = []
                if sem_indexer is not None:
                    try:
                        with _sqlite3.connect(indexer._db_path, check_same_thread=False) as _con:
                            (_n_sum,) = _con.execute(
                                "SELECT COUNT(*) FROM file_summaries WHERE is_stale=0"
                            ).fetchone()
                            _stale_rows = _con.execute(
                                "SELECT file_path, generated_at FROM file_summaries WHERE is_stale=1"
                            ).fetchall()
                        _ov(**{"semantic.summarised": str(_n_sum)})
                    except Exception as _exc:
                        print(f"[Status] monitor file_summaries query error: {_exc}")

                if sem_indexer is not None:
                    _snap = sem_indexer.get_watch_snapshot()
                    _pending = len(_snap.get("pending_stale_paths", []))
                    _thresh  = _snap.get("threshold", 0)
                    _idle    = _snap.get("project_idle_remaining")
                    # Compute semantic status string
                    if _idle is not None:
                        _ov(**{"semantic.status": f"regen in {_idle:.0f}s ({_pending}/{_thresh})"})
                    elif _pending > 0:
                        _ov(**{"semantic.status": f"pending {_pending}/{_thresh}"})
                    elif isinstance(_n_sum, int) and _n_sum > 0:
                        _ov(**{"semantic.status": f"up to date ({_n_sum} files)"})

                    if tui_app is not None:
                        tui_app.update_sem_watch(_snap, list(_stale_rows))

        import threading as _threading
        _threading.Thread(target=_monitor, daemon=True, name="overview-monitor").start()

    try:
        mcp_server.run_server(
            lsp_clients, project_root,
            semantic_indexer=sem_indexer, indexer=indexer,
            host=args.host, port=args.port,
        )
    finally:
        _ov(**{"server.status": "stopped", "indexer.status": "stopping…"})
        print("[Status] Shutting down indexer and LSP clients...")
        try:
            indexer.stop()
        except Exception:
            pass
        for client in lsp_clients.values():
            try:
                client.shutdown_server()
            except Exception:
                pass
        _ov(**{"indexer.status": "stopped"})


def main():
    args = _parse_args()
    project_root = canonical_path(args.project_root)

    if not os.path.isdir(project_root):
        print(f"Error: Provided path '{project_root}' does not exist or is not a directory.")
        sys.exit(1)

    # Tame the loudest libraries early — both for --no-tui mode and so the TUI
    # doesn't get a flood of INFO records before its handler is wired.
    for noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio",
                  "chromadb", "posthog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Auto-disable TUI when stdout is not a real terminal (piped, redirected,
    # or running inside a benchmark/CI subprocess). Textual requires a TTY.
    if not args.no_tui and not sys.stdout.isatty():
        args.no_tui = True

    if not args.no_tui:
        # Enable print redirect + pre-buffering NOW, before _bootstrap(), so
        # any log lines emitted during language detection or config loading are
        # captured and replayed into the TUI once it mounts — not lost.
        cli_io.install_print_redirect(pre_buffer=True)
        cli_io.install_logging()

    # The semantic config wizard uses input(): always run it before the TUI
    # takes over the terminal.
    detected_languages, sem_indexer_factory = _bootstrap(args, project_root)

    if args.no_tui:
        cli_io.set_no_tui(True)
        cli_io.install_logging()
        _do_startup_work(args, project_root, detected_languages, sem_indexer_factory)
        return

    # Lazy-import the TUI so plain --no-tui runs don't pay the textual import cost.
    from . import tui

    def worker(_app):
        try:
            _do_startup_work(args, project_root, detected_languages, sem_indexer_factory, tui_app=_app)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[Status] Fatal: {e}")

    tui.run_tui(
        title="Codebase-Insights",
        project_root=project_root,
        worker=worker,
    )


if __name__ == "__main__":
    main()
