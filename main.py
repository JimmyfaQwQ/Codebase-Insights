import logging
import os
import shutil
import sys

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

import LSP
import mcp_server
import language_analysis
import workspace_indexer
import semantic_config
import semantic_indexer as semantic_indexer_mod
from workspace_indexer import canonical_path

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

    print(f"Detecting languages in project at: {project_root}...\n")
    detected_languages = language_analysis.detect_languages(project_root)
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
            
    
    for language in detected_languages:
        server_cmd = language_servers[language]
        client = LSP.LSPClient(language, server_cmd)
        client.start_server()
        client.initialize(project_root)
        lsp_clients[language] = client
        print(f"Initialized LSP client for {language.value} with command: {' '.join(server_cmd)}\n")

    # --- Semantic indexer (AI summaries + vector search) ---
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

    indexer = workspace_indexer.WorkspaceIndexer(project_root, lsp_clients, semantic_indexer=sem_indexer)

    if args.rebuild_index:
        indexer.clear_index()

    if args.rebuild_semantic and sem_indexer is not None:
        sem_indexer.clear_semantic()
    elif args.rebuild_vectors and sem_indexer is not None:
        sem_indexer.rebuild_vectors()
    elif args.rebuild_summaries and sem_indexer is not None:
        sem_indexer.clear_project_summaries()

    indexer.start()

    try:
        mcp_server.run_server(lsp_clients, project_root, semantic_indexer=sem_indexer)
    finally:
        print("Shutting down indexer and LSP clients...")
        indexer.stop()
        for client in lsp_clients.values():
            client.shutdown_server()

if __name__ == "__main__":
    main()