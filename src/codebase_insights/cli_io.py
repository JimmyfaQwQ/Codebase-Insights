"""Centralised CLI I/O facade for Codebase-Insights.

Goals:
* Route every log line into a *named* section (status/indexer/semantic/lsp/http/
  benchmark/config) so the TUI can keep them on separate panels.
* Wrap ``print()``/``sys.stdout``/``sys.stderr`` so that legacy code which still
  calls ``print("[Indexer] ...")`` is automatically dispatched correctly.
* Provide a ``tqdm``-compatible progress shim so progress bars render in the
  TUI's progress panel instead of clobbering log output.
* Capture the Python ``logging`` records produced by chatty third-party
  libraries (httpx, httpcore, openai, chromadb, mcp, uvicorn, …) and corral
  them into the dedicated ``http`` section.
* Fall back to plain stdout (with section prefixes) when ``--no-tui`` is set
  or when called *before* the TUI has attached.
"""

from __future__ import annotations

import builtins
import io
import logging
import re
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from typing import Callable, Iterable, Optional

# ----------------------------------------------------------------------------
# Section catalogue
# ----------------------------------------------------------------------------

SECTIONS: tuple[str, ...] = (
    "overview",
    "all",
    "sem_watch",
    "status",
    "indexer",
    "semantic",
    "lsp",
    "http",
    "benchmark",
    "config",
)

_DEFAULT_SECTION = "status"

# ``[Tag]`` at the start of a line determines the destination section.
_TAG_RE = re.compile(r"^\[([A-Za-z][A-Za-z0-9 _:.-]+)\]\s?")
_BENCH_RE = re.compile(r"^\[BENCHMARK\b", re.IGNORECASE)


def _section_for_tag(tag: str) -> str:
    t = tag.lower()
    if "benchmark" in t:
        return "benchmark"
    if "lsp" in t:
        return "lsp"
    if "indexer" in t:
        return "indexer"
    if "semantic" in t:
        return "semantic"
    if "config" in t:
        return "config"
    return _DEFAULT_SECTION


def detect_section(line: str, default: str = _DEFAULT_SECTION) -> str:
    if _BENCH_RE.match(line):
        return "benchmark"
    m = _TAG_RE.match(line)
    if m:
        return _section_for_tag(m.group(1))
    return default


# ----------------------------------------------------------------------------
# Sinks
# ----------------------------------------------------------------------------

LogSink = Callable[[str, str], None]          # (section, line)
ProgressSink = Callable[[dict], None]         # progress event payload

_lock = threading.RLock()
_log_sink: Optional[LogSink] = None
_progress_sink: Optional[ProgressSink] = None
_no_tui: bool = False
_real_stdout = sys.stdout
_real_stderr = sys.stderr
_print_redirected: bool = False

# Lines emitted before the TUI sink is attached are stored here and replayed
# once attach_sinks() is called.  Bounded so a crash-early run
# never consumes unbounded memory.
_PRE_BUFFER_MAX = 10_000
_pre_buffer: list[tuple[str, str]] = []       # [(section, line), ...]
_pre_buffering: bool = False                  # activated by install_print_redirect()


def attach_sinks(log_sink: LogSink, progress_sink: ProgressSink) -> None:
    """Called by the TUI once it is ready to receive events.

    Any lines captured into the pre-buffer before the TUI was ready are
    replayed into the new sink so nothing is silently dropped.
    """
    global _log_sink, _progress_sink, _pre_buffer, _pre_buffering
    with _lock:
        _log_sink = log_sink
        _progress_sink = progress_sink
        _pre_buffering = False
        buffered, _pre_buffer = _pre_buffer, []
    # Replay outside the lock so the sink can call back into emit() safely.
    for sec, line in buffered:
        try:
            log_sink(sec, line)
        except Exception:
            pass


def detach_sinks() -> None:
    global _log_sink, _progress_sink
    with _lock:
        _log_sink = None
        _progress_sink = None


def flush_pre_buffer_to_terminal() -> None:
    """Drain the pre-buffer to the real terminal and disable further buffering.

    Call this before any fatal sys.exit() that fires before the TUI has
    mounted, so that error messages captured in the pre-buffer are not
    silently discarded.
    """
    global _pre_buffer, _pre_buffering
    with _lock:
        buffered, _pre_buffer = _pre_buffer, []
        _pre_buffering = False
    for sec, line in buffered:
        _real_stdout.write(f"[{sec}] {line}\n")
    if buffered:
        _real_stdout.flush()


@contextmanager
def bypass_print_redirect():
    """Temporarily restore raw stdout/print so interactive wizard I/O is visible.

    Flushes the pre-buffer first so no earlier messages are lost.  After the
    block the redirect is reinstated and pre-buffering resumes.  No-op when no
    redirect is installed (e.g. --no-tui mode).
    """
    global _pre_buffering
    flush_pre_buffer_to_terminal()
    if not _print_redirected:
        yield
        return
    builtins.print = _real_print
    sys.stdout = _real_stdout
    sys.stderr = _real_stderr
    try:
        yield
    finally:
        builtins.print = _routing_print
        sys.stdout = _LineSplitter("status")
        sys.stderr = _StderrSplitter("status")
        with _lock:
            _pre_buffering = True


def set_no_tui(value: bool) -> None:
    """Force plain-stdout mode (used for --no-tui / scripted runs)."""
    global _no_tui
    _no_tui = value


def is_tui_active() -> bool:
    return _log_sink is not None and not _no_tui


# ----------------------------------------------------------------------------
# Emission
# ----------------------------------------------------------------------------

def emit(line: str, section: Optional[str] = None) -> None:
    """Send a single line of text to the appropriate section."""
    if line is None:
        return
    line = line.rstrip("\r\n")
    if not line:
        return
    sec = section or detect_section(line)
    with _lock:
        sink = _log_sink
        pre = _pre_buffering and not _no_tui
    if sink is not None and not _no_tui:
        try:
            sink(sec, line)
            return
        except Exception:
            # Fall through to stdout if the TUI sink failed.
            pass
    elif pre:
        # TUI is expected but hasn't mounted yet — buffer for later replay.
        with _lock:
            if len(_pre_buffer) < _PRE_BUFFER_MAX:
                _pre_buffer.append((sec, line))
        return
    _real_stdout.write(f"[{sec}] {line}\n")
    _real_stdout.flush()


# ----------------------------------------------------------------------------
# stdout / stderr interception
# ----------------------------------------------------------------------------

class _LineSplitter(io.TextIOBase):
    """A text stream that buffers writes and dispatches whole lines.

    Carriage-return-only updates (``\\r``) are dropped — those were used by the
    legacy progress prints, which are now routed through the Progress shim.
    """

    def __init__(self, default_section: str):
        self._buf = ""
        self._default_section = default_section
        self._tlock = threading.Lock()

    # ``TextIOBase`` requires writable() == True for ``print`` to use us.
    def writable(self) -> bool:  # type: ignore[override]
        return True

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        with self._tlock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.replace("\r", "")
                if line.strip():
                    emit(line, detect_section(line, self._default_section))
        return len(s)

    def flush(self) -> None:  # type: ignore[override]
        # Surface any partial line that does not end in \n (rare).
        with self._tlock:
            if self._buf.strip():
                line = self._buf.replace("\r", "")
                self._buf = ""
                if line.strip():
                    emit(line, detect_section(line, self._default_section))

    def isatty(self) -> bool:  # type: ignore[override]
        return False


def _install_excepthooks() -> None:
    """Route unhandled exceptions (main thread + worker threads) into status."""
    def _fmt_exc(exc_type, exc_value, exc_tb) -> str:
        lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        return "".join(lines).rstrip()

    def _excepthook(exc_type, exc_value, exc_tb):
        text = _fmt_exc(exc_type, exc_value, exc_tb)
        for line in text.splitlines():
            emit(f"[STDERR] {line}", "status")

    def _thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        text = _fmt_exc(args.exc_type, args.exc_value, args.exc_traceback)
        thread_name = getattr(args.thread, "name", "?")
        emit(f"[STDERR] Exception in thread '{thread_name}':", "status")
        for line in text.splitlines():
            emit(f"[STDERR] {line}", "status")

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


class _StderrSplitter(_LineSplitter):
    """Like _LineSplitter but prepends '[STDERR] ' to every line so stderr
    output is clearly labelled and visually distinct in the status panel."""

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        with self._tlock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.replace("\r", "")
                if line.strip():
                    # Prefix with [STDERR] unless it already has a [Tag].
                    if not _TAG_RE.match(line):
                        line = f"[STDERR] {line}"
                    emit(line, detect_section(line, self._default_section))
        return len(s)


_real_print = builtins.print


def _routing_print(*args, sep=" ", end="\n", file=None, flush=False, **kwargs):
    """Replacement for builtins.print that routes through emit().

    When ``file`` is explicitly set to something other than stdout/stderr, we
    fall back to the original print so as not to break intentional redirections
    (e.g. writing to a StringIO in tests).
    """
    if file is not None and file not in (sys.stdout, sys.stderr, _real_stdout, _real_stderr):
        _real_print(*args, sep=sep, end=end, file=file, flush=flush)
        return
    text = sep.join(str(a) for a in args) + end
    if text.strip():
        # Detect section from the first line of multi-line prints.
        first_line = text.split("\n", 1)[0].rstrip()
        default = "status"
        # Determine whether this came from stderr (heuristic: file was stderr).
        if file in (sys.stderr, _real_stderr):
            default = "status"
        for line in text.rstrip("\n").split("\n"):
            if line.strip():
                emit(line, detect_section(line, default))
    if flush:
        pass  # emit already flushes where needed


def install_print_redirect(pre_buffer: bool = False) -> None:
    """Redirect ``print()`` / ``sys.stdout`` / ``sys.stderr`` to log sections.

    Patches ``builtins.print`` so that all print calls route through ``emit()``
    regardless of any later ``sys.stdout`` redirections (Textual replaces
    ``sys.stdout`` with its own capture object once the app starts, which would
    otherwise silently swallow all worker-thread print output).

    Pass ``pre_buffer=True`` when the TUI has not yet started: lines will be
    stored in the pre-buffer and replayed into the TUI once it mounts instead
    of being discarded or printed to the raw terminal.
    """
    global _print_redirected, _pre_buffering
    if _print_redirected:
        if pre_buffer:
            with _lock:
                _pre_buffering = True
        return
    # Patch builtins.print so it bypasses sys.stdout entirely.
    builtins.print = _routing_print
    # Also redirect sys.stdout / sys.stderr for code that writes directly to
    # the stream objects (e.g. uvicorn writes to sys.stdout directly).
    sys.stdout = _LineSplitter("status")
    sys.stderr = _StderrSplitter("status")
    _print_redirected = True
    if pre_buffer:
        with _lock:
            _pre_buffering = True
    # Capture unhandled exceptions (main thread and worker threads) so they
    # appear in the TUI status section rather than being lost.
    _install_excepthooks()


def restore_print_redirect() -> None:
    global _print_redirected
    if not _print_redirected:
        return
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    builtins.print = _real_print
    sys.stdout = _real_stdout
    sys.stderr = _real_stderr
    _print_redirected = False


# ----------------------------------------------------------------------------
# Logging integration
# ----------------------------------------------------------------------------

# Loggers whose records should always go to the "http" section.
_HTTP_LOGGER_PREFIXES = (
    "httpx", "httpcore", "urllib3", "requests", "openai", "anthropic",
    "chromadb", "posthog", "watchdog",
    "mcp", "uvicorn", "starlette", "fastapi", "anyio", "asyncio",
    "langchain", "langchain_core", "langchain_openai", "langchain_ollama",
    "langchain_chroma",
)


def _section_for_logger(name: str) -> str:
    n = name.lower()
    for prefix in _HTTP_LOGGER_PREFIXES:
        if n == prefix or n.startswith(prefix + "."):
            return "http"
    return _DEFAULT_SECTION


class _SectionRoutingHandler(logging.Handler):
    """Logging handler that routes records into our section sinks."""

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        sec = _section_for_logger(record.name)
        # Prepend the (short) logger name so the source is visible in the TUI.
        short = record.name.split(".")[0]
        level = record.levelname
        line = f"[{level}] {short}: {msg}"
        emit(line, sec)


_logging_installed = False


def install_logging(level: int = logging.INFO) -> None:
    """Install the routing handler on the root logger.

    Also bumps the verbosity of the noisiest libraries down to WARNING so we
    don't spam the http panel with low-value INFO records during indexing.
    """
    global _logging_installed
    if _logging_installed:
        return
    handler = _SectionRoutingHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    # Replace any existing default StreamHandler so nothing prints to the raw
    # terminal once the TUI is up.
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, _SectionRoutingHandler):
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "openai", "urllib3",
                  "chromadb", "posthog", "watchdog", "asyncio",
                  "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _logging_installed = False  # allow re-install if needed
    _logging_installed = True


# ----------------------------------------------------------------------------
# Progress shim (tqdm-compatible)
# ----------------------------------------------------------------------------

_progress_id_lock = threading.Lock()
_progress_next_id = 0


def _next_progress_id() -> int:
    global _progress_next_id
    with _progress_id_lock:
        _progress_next_id += 1
        return _progress_next_id


def _progress_event(payload: dict) -> None:
    sink = _progress_sink
    if sink is not None and not _no_tui:
        try:
            sink(payload)
            return
        except Exception:
            pass


class _ProgressBase:
    """Common implementation for the tqdm-compatible shim."""

    def __init__(
        self,
        iterable: Optional[Iterable] = None,
        *,
        desc: str = "",
        total: Optional[int] = None,
        unit: str = "it",
        postfix: Optional[dict] = None,
        section: Optional[str] = None,
        **_ignored,
    ):
        self._iterable = iterable
        self._desc = desc or "Progress"
        if total is None and iterable is not None:
            try:
                total = len(iterable)  # type: ignore[arg-type]
            except Exception:
                total = None
        self._total = total
        self._unit = unit
        self._postfix: dict = dict(postfix or {})
        self._section = section or detect_section(self._desc)
        self._n = 0
        self._closed = False
        self._id = _next_progress_id()
        self._t0 = time.monotonic()
        self._last_emit = 0.0
        self._emit("start")

    # -- public tqdm-compatible API ----------------------------------------

    def update(self, n: int = 1) -> None:
        self._n += n
        self._emit_throttled()

    def set_postfix(self, postfix=None, refresh: bool = True, **kwargs) -> None:
        if isinstance(postfix, dict):
            self._postfix.update(postfix)
        if kwargs:
            self._postfix.update(kwargs)
        if refresh:
            self._emit_throttled(force=True)

    def set_description(self, desc: str, refresh: bool = True) -> None:
        self._desc = desc
        if refresh:
            self._emit_throttled(force=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._emit("end")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def __iter__(self):
        if self._iterable is None:
            return iter(())
        try:
            for item in self._iterable:
                yield item
                self._n += 1
                self._emit_throttled()
        finally:
            self.close()

    # -- internals ---------------------------------------------------------

    def _emit_throttled(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_emit) < 0.08:
            return
        self._last_emit = now
        self._emit("update")

    def _emit(self, kind: str) -> None:
        payload = {
            "kind": kind,
            "id": self._id,
            "section": self._section,
            "desc": self._desc,
            "total": self._total,
            "n": self._n,
            "unit": self._unit,
            "postfix": dict(self._postfix),
            "elapsed": time.monotonic() - self._t0,
        }
        if is_tui_active():
            _progress_event(payload)
        else:
            # Fallback: emit a textual progress line (throttled via _emit_throttled).
            if kind in ("update", "end"):
                if self._total:
                    pct = (self._n / self._total) * 100
                    bar = f"{self._n}/{self._total} ({pct:5.1f}%)"
                else:
                    bar = f"{self._n}"
                pf = " ".join(f"{k}={v}" for k, v in self._postfix.items())
                msg = f"[{self._section}] {self._desc}: {bar} {pf}".rstrip()
                if kind == "end":
                    msg += " ✓"
                _real_stdout.write(msg + "\n")
                _real_stdout.flush()


def tqdm(*args, **kwargs):
    """Drop-in tqdm replacement.

    Mirrors the small tqdm subset used in this codebase:
        ``tqdm(iterable, desc=..., unit=..., dynamic_ncols=..., postfix=...)``
        ``tqdm(total=..., desc=..., unit=..., dynamic_ncols=...)``
    plus ``.update(n)``, ``.set_postfix(**)`` and use as a context manager.
    """
    iterable = None
    if args:
        iterable = args[0]
    return _ProgressBase(iterable, **kwargs)
