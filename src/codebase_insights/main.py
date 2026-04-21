import logging
import os
import shutil
import sys
import time

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

from . import LSP
from . import mcp_server
from . import language_analysis
from . import workspace_indexer
from . import semantic_config
from . import semantic_indexer as semantic_indexer_mod
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

lsp_clients: dict[language_analysis.Language, LSP.LSPClient] = {}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Codebase-Insights: LSP-powered code intelligence with AI semantic search")
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
    args = parser.parse_args()
    project_root = canonical_path(args.project_root)

    # check if the provided path exists and is a directory
    if not os.path.isdir(project_root):
        print(f"Error: Provided path '{project_root}' does not exist or is not a directory.")
        sys.exit(1)

    _bm_t0 = time.perf_counter()

    print(f"Detecting languages in project at: {project_root}...\n")
    detected_languages = language_analysis.detect_languages(project_root)
    _bm_t_lang = time.perf_counter()
    if detected_languages:
        print("Detected languages:")
        for language in detected_languages:
            print(f"- {language.value}")
    else:
        print("No currently supported languages detected.")

    print()

    print("Starting MCP servers accordingly...\n")

    failed = False

    for language in detected_languages:
        # look for lsp in PATH
        server_executable = language_servers[language][0]
        if not shutil.which(server_executable):
            download_link = language_servers_install_guides.get(language, "Unknown")
            print(f"\nError: {server_executable} not found in PATH. Please install it to enable {language.value} support.")
            print(f"Installation guide: {download_link}")
            failed = True
    
    if failed:
        sys.exit(1)
            
    
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
        print(f"Initialized LSP client for {language.value} with command: {' '.join(server_cmd)}\n")

    # --- Semantic indexer (AI summaries + vector search) ---
    _bm_t_lsp_done = time.perf_counter()
    sem_indexer = None
    try:
        cfg = semantic_config.load_config(project_root, force_new=args.new_config)
        llm = semantic_config.create_llm()
        embeddings = semantic_config.create_embeddings()
        sem_indexer = semantic_indexer_mod.SemanticIndexer(project_root, llm, embeddings)
        print(f"Semantic indexer initialised "
              f"(chat={cfg['chat']['provider']}, embed={cfg['embed']['provider']})\n")
    except Exception as e:
        print(f"[Semantic] Could not initialise semantic indexer: {e}")
        print("[Semantic] Continuing without AI-powered search.\n")
    _bm_t_sem_init = time.perf_counter()

    _bm_t_wi_s = time.perf_counter()
    indexer = workspace_indexer.WorkspaceIndexer(project_root, lsp_clients, semantic_indexer=sem_indexer)
    _bm_t_wi_created = time.perf_counter()

    if args.rebuild_index:
        indexer.clear_index()

    if args.rebuild_semantic and sem_indexer is not None:
        sem_indexer.clear_semantic()
    elif args.rebuild_vectors and sem_indexer is not None:
        sem_indexer.rebuild_vectors()
    elif args.rebuild_summaries and sem_indexer is not None:
        sem_indexer.clear_project_summaries()

    indexer.start()
    _bm_t_started = time.perf_counter()

    # ── Benchmark: startup phase summary ────────────────────────────────
    print(f"[BENCHMARK:STARTUP] lang_detect={_bm_t_lang - _bm_t0:.3f}s "
          f"semantic_init={_bm_t_sem_init - _bm_t_lsp_done:.3f}s "
          f"indexer_create={_bm_t_wi_created - _bm_t_wi_s:.3f}s "
          f"total_pre_server={_bm_t_started - _bm_t0:.3f}s", flush=True)
    for _bm_lang, _bm_lt in _bm_lsp_timings.items():
        print(f"[BENCHMARK:STARTUP] lsp_{_bm_lang}_start_server={_bm_lt['start_server']:.3f}s "
              f"lsp_{_bm_lang}_initialize={_bm_lt['initialize']:.3f}s", flush=True)
    # ────────────────────────────────────────────────────────────────────

    try:
        mcp_server.run_server(lsp_clients, project_root, semantic_indexer=sem_indexer, indexer=indexer)
    finally:
        print("Shutting down indexer and LSP clients...")
        indexer.stop()
        for client in lsp_clients.values():
            client.shutdown_server()

if __name__ == "__main__":
    main()