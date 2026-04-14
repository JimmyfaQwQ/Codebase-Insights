import json
import os
import shutil
import sys
from typing import Dict, Optional, Set
from urllib.parse import urlparse
import threading
import subprocess

from . import language_analysis

# LSP language IDs for each supported language
_LANGUAGE_ID: dict[language_analysis.Language, str] = {
    language_analysis.Language.PYTHON: "python",
    language_analysis.Language.JS_TS: "typescript",
    language_analysis.Language.CPP: "cpp",
    language_analysis.Language.RUST: "rust",
}


class LSPInitResult:
    def __init__(self, result: Dict):
        capabilities = result.get("result", {}).get("capabilities", {})
        self.hover_provider = capabilities.get("hoverProvider", False)
        self.declaration_provider = capabilities.get("declarationProvider", False)
        self.definition_provider = capabilities.get("definitionProvider", False)
        self.implementation_provider = capabilities.get("implementationProvider", False)
        self.references_provider = capabilities.get("referencesProvider", False)
        self.document_symbol_provider = capabilities.get("documentSymbolProvider", False)
        self.workspace_symbol_provider = capabilities.get("workspaceSymbolProvider", False)


class LSPExecutionResult:
    def __init__(self, result=None, error: Optional[Dict] = None):
        self.result = result
        self.error = error
        self.success = error is None


class LSPClient:
    # Languages where cross-file references require a compilation database.
    # Without one, the LSP returns timeouts or incomplete results.
    _NEEDS_COMPILATION_DB = {language_analysis.Language.CPP}
    _COMPILATION_DB_FILES = ("compile_commands.json", "compile_flags.txt")

    def __init__(self, language: language_analysis.Language, server_cmd: list):
        self.language = language
        self.server_cmd = server_cmd
        self.process: Optional[subprocess.Popen] = None
        self.root_uri: Optional[str] = None
        self.LSP_init_result: Optional[LSPInitResult] = None
        self.has_compilation_database: bool = False
        self._request_id = 0
        self._id_lock = threading.Lock()
        # Maps request id -> (Event, response_box)
        # Reader thread appends the response to the box and sets the event.
        self._pending: Dict[int, tuple[threading.Event, list]] = {}
        self._pending_lock = threading.Lock()
        self._open_documents: Set[str] = set()
        self._open_doc_lock = threading.Lock()

    @property
    def references_reliable(self) -> bool:
        """Whether ``textDocument/references`` is expected to return useful results.

        For languages that require a compilation database (C/C++/ObjC), this is
        ``False`` when no ``compile_commands.json`` / ``compile_flags.txt`` was
        found in the project tree.  For all other languages it follows the
        server's reported capability.
        """
        if not self.LSP_init_result or not self.LSP_init_result.references_provider:
            return False
        if self.language in self._NEEDS_COMPILATION_DB:
            return self.has_compilation_database
        return True

    @property
    def supports_indexing(self) -> bool:
        """Whether bulk workspace indexing is expected to work reliably.

        For languages that require a compilation database, **all** LSP
        requests (including ``documentSymbol``) can be slow or unreliable
        without one.  The workspace indexer should skip these files entirely;
        the client remains available for on-demand MCP queries where a
        per-file timeout is acceptable.
        """
        if self.language in self._NEEDS_COMPILATION_DB:
            return self.has_compilation_database
        return True

    # ── Server lifecycle ──────────────────────────────────────────────────────

    def start_server(self):
        # On Windows, npm global installs are .cmd wrappers that require the
        # shell. Use shell=True unconditionally on Windows so both .cmd and
        # native executables work.
        self.process = subprocess.Popen(
            self.server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=(sys.platform == "win32"),
        )
        threading.Thread(target=self._read_messages, daemon=True).start()

    def shutdown_server(self):
        self._send_request("shutdown")
        if self.process:
            self.process.terminate()

    # ── I/O threads ───────────────────────────────────────────────────────────

    def _read_messages(self):
        while self.process and self.process.poll() is None:
            try:
                # Read all headers until blank line (LSP may send multiple headers)
                headers: Dict[str, str] = {}
                while True:
                    raw = self.process.stdout.readline()
                    if not raw:
                        return
                    line = raw.decode('ascii', errors='replace').rstrip('\r\n')
                    if not line:
                        break  # blank line marks end of headers
                    if ':' in line:
                        key, _, value = line.partition(':')
                        headers[key.strip()] = value.strip()
                if 'Content-Length' not in headers:
                    continue
                length = int(headers['Content-Length'])
                content = self.process.stdout.read(length).decode('utf-8')
                message = json.loads(content)
                req_id = message.get("id")
                if req_id is not None:
                    with self._pending_lock:
                        entry = self._pending.get(req_id)
                    if entry:
                        event, box = entry
                        box.append(message)
                        event.set()
            except Exception as e:
                print(f"[{self.language.value} LSP error] {e}")

    def _send_raw(self, message: Dict):
        content = json.dumps(message).encode('utf-8')
        header = f"Content-Length: {len(content)}\r\n\r\n".encode('ascii')
        self.process.stdin.write(header + content)
        self.process.stdin.flush()

    # ── Request / Notification ────────────────────────────────────────────────

    def _next_id(self) -> int:
        with self._id_lock:
            self._request_id += 1
            return self._request_id

    def _send_request(self, method: str, params: Optional[Dict] = None, timeout: float = 10.0) -> Optional[Dict]:
        """Send a request and block until a response arrives or timeout expires."""
        req_id = self._next_id()
        event = threading.Event()
        box: list = []
        with self._pending_lock:
            self._pending[req_id] = (event, box)
        self._send_raw({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        received = event.wait(timeout=timeout)
        with self._pending_lock:
            self._pending.pop(req_id, None)
        if not received:
            print(f"[{self.language.value} LSP] {method} timed out.")
            return None
        return box[0]

    def send_notification(self, method: str, params: Optional[Dict] = None):
        self._send_raw({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def initialize(self, root_path: str, init_options: Optional[Dict] = None):
        # Normalize to a proper file:///... URI (forward slashes, triple slash)
        uri_path = root_path.replace("\\", "/").lstrip("/")
        root_uri = f"file:///{uri_path}"
        result = self._send_request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "declaration": {"linkSupport": True},
                    "definition": {"linkSupport": True},
                    "implementation": {"linkSupport": True},
                    "references": {},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    "workspaceSymbol": {
                        "symbolKind": {
                            "valueSet": list(range(1, 26))  # all SymbolKind values
                        }
                    },
                }
            },
            "initializationOptions": init_options or {},
        }, timeout=30.0)
        if not result:
            print(f"[{self.language.value} LSP error] Failed to initialize LSP server.")
            return
        caps = result.get("result", {}).get("capabilities", {})
        relevant = {k: caps[k] for k in (
            "hoverProvider", "declarationProvider", "definitionProvider",
            "implementationProvider", "referencesProvider", "documentSymbolProvider",
        ) if k in caps}
        print(f"[{self.language.value} LSP capabilities] {json.dumps(relevant)}")
        self.LSP_init_result = LSPInitResult(result)
        self.root_uri = root_uri
        self.send_notification("initialized")

        # Detect compilation database for languages that need one
        if self.language in self._NEEDS_COMPILATION_DB:
            self.has_compilation_database = self._detect_compilation_database(root_path)
            if not self.has_compilation_database:
                print(
                    f"[{self.language.value} LSP] No compilation database found "
                    f"(looked for {', '.join(self._COMPILATION_DB_FILES)} in project tree). "
                    f"C/C++ files will be skipped during workspace indexing. "
                    f"On-demand LSP queries (hover, definition, etc.) remain available."
                )
            else:
                print(f"[{self.language.value} LSP] Compilation database detected.")

    def _detect_compilation_database(self, root_path: str) -> bool:
        """Walk from *root_path* upward looking for a compilation database file."""
        # First check the project root itself, then walk parents
        path = os.path.abspath(root_path)
        for _ in range(20):  # cap to avoid infinite loop at filesystem root
            for name in self._COMPILATION_DB_FILES:
                if os.path.isfile(os.path.join(path, name)):
                    return True
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        return False

    # ── LSP requests ──────────────────────────────────────────────────────────

    def _ensure_document_open(self, uri: str) -> None:
        """Send textDocument/didOpen the first time a URI is requested."""
        with self._open_doc_lock:
            if uri in self._open_documents:
                return
            self._open_documents.add(uri)
        try:
            parsed = urlparse(uri)
            file_path = parsed.path
            if sys.platform == "win32":
                # Remove leading slash from /E:/path/file.py
                file_path = file_path.lstrip("/")
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"[{self.language.value} LSP] Failed to read {uri}: {e}")
            return
        language_id = _LANGUAGE_ID.get(self.language, self.language.value)
        self.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            }
        })

    def _text_document_request(
        self,
        method: str,
        capability_attr: str,
        text_document: Dict,
        position: Optional[Dict] = None,
        timeout: float = 10.0,
    ) -> LSPExecutionResult:
        if not self.LSP_init_result or not getattr(self.LSP_init_result, capability_attr, False):
            return LSPExecutionResult(error={"message": f"{method} not supported"})
        self._ensure_document_open(text_document.get("uri", ""))
        params: Dict = {"textDocument": text_document}
        if position is not None:
            params["position"] = position
        response = self._send_request(method, params, timeout=timeout)
        if response is None:
            return LSPExecutionResult(error={"message": f"{method} timed out"})
        return LSPExecutionResult(result=response.get("result"), error=response.get("error"))

    def hover(self, text_document: Dict, position: Dict) -> LSPExecutionResult:
        return self._text_document_request("textDocument/hover", "hover_provider", text_document, position)

    def definition(self, text_document: Dict, position: Dict) -> LSPExecutionResult:
        return self._text_document_request("textDocument/definition", "definition_provider", text_document, position)

    def declaration(self, text_document: Dict, position: Dict) -> LSPExecutionResult:
        return self._text_document_request("textDocument/declaration", "declaration_provider", text_document, position)

    def implementation(self, text_document: Dict, position: Dict) -> LSPExecutionResult:
        return self._text_document_request("textDocument/implementation", "implementation_provider", text_document, position)

    def references(self, text_document: Dict, position: Dict, timeout: float = 30.0) -> LSPExecutionResult:
        if not self.LSP_init_result or not self.LSP_init_result.references_provider:
            return LSPExecutionResult(error={"message": "textDocument/references not supported"})
        self._ensure_document_open(text_document.get("uri", ""))
        params = {
            "textDocument": text_document,
            "position": position,
            "context": {"includeDeclaration": True},
        }
        response = self._send_request("textDocument/references", params, timeout=timeout)
        if response is None:
            return LSPExecutionResult(error={"message": "textDocument/references timed out", "timeout_seconds": timeout})
        return LSPExecutionResult(result=response.get("result"), error=response.get("error"))

    def document_symbols(self, text_document: Dict) -> LSPExecutionResult:
        return self._text_document_request("textDocument/documentSymbol", "document_symbol_provider", text_document)

