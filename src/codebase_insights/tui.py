"""Textual-based TUI for Codebase-Insights.

Layout:

    +--------------------------------------------------------+
    |  Codebase-Insights · <project>                          |   header
    +-------------+------------------------------------------+
    |  Sections   |  Overview (structured) OR log panel       |
    |  > Overview |                                          |
    |    Status   |                                          |
    |    Indexer  |                                          |
    |    Semantic |                                          |
    |    LSP      |                                          |
    |    HTTP     |                                          |
    |    Bench…   |                                          |
    |    Config   |                                          |
    +-------------+------------------------------------------+
    |  Active progress bars (live)                            |
    +--------------------------------------------------------+
    |  1-8 sections | ↑↓ PgUp/PgDn scroll | c clear | q quit  |   footer
    +--------------------------------------------------------+

Key bindings:
    1..8         jump to section (1=Overview 2=Status … 8=Config)
    Tab / S-Tab  cycle next / prev section
    ↑ / ↓        scroll one line
    PgUp / PgDn  scroll one page
    Home / End   jump to top / bottom
    f            toggle follow-tail (auto-scroll on new lines)
    c            clear current section
    q / Ctrl-C   quit
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Optional

from rich.markup import escape as _rich_escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, RichLog, Static

from . import cli_io


_SECTION_LABELS = {
    "overview":  "Overview",
    "all":       "All Logs",
    "sem_watch": "Semantic Watch",
    "status":    "Status",
    "indexer":   "Indexer",
    "semantic":  "Semantic",
    "lsp":       "LSP",
    "http":      "HTTP / Network",
    "benchmark": "Benchmark",
    "config":    "Config",
}

_SECTION_COLOURS = {
    "overview":  "bright_white",
    "all":       "white",
    "sem_watch": "magenta",
    "status":    "cyan",
    "indexer":   "green",
    "semantic":  "magenta",
    "lsp":       "yellow",
    "http":      "bright_black",
    "benchmark": "blue",
    "config":    "white",
}

# Groups shown on the Overview panel: (title, [(field_key, display_label), ...])
_OVERVIEW_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Project", [
        ("project.path",      "Path"),
        ("project.languages", "Languages"),
    ]),
    ("Config", [
        ("config.chat",  "Chat model"),
        ("config.embed", "Embed model"),
    ]),
    ("Indexer", [
        ("indexer.status",   "Status"),
        ("indexer.files",    "Files scanned"),
        ("indexer.symbols",  "Symbols (LSP)"),
    ]),
    ("Semantic AI", [
        ("semantic.status",         "Status"),
        ("semantic.summarised",     "Files summarised"),
    ]),
    ("MCP Server", [
        ("server.url",    "URL"),
        ("server.status", "Status"),
    ]),
]

_MAX_BUFFER_LINES = 5000


# ---------------------------------------------------------------------------
# Thread-safe messages (post_message works from any thread, unlike
# call_from_thread which raises RuntimeError when called from the event-loop
# thread itself — e.g. during pre-buffer replay inside on_mount).
# ---------------------------------------------------------------------------

class _LogLine(Message):
    """A new log line has been buffered; update the live display."""
    def __init__(self, section: str, line: str) -> None:
        super().__init__()
        self.section = section
        self.line = line


class _ProgressEvent(Message):
    """A progress payload should be rendered."""
    def __init__(self, payload: dict) -> None:
        super().__init__()
        self.payload = payload


class _OverviewUpdate(Message):
    """Overview fields have changed; redraw the overview pane."""


class _SemWatchUpdate(Message):
    """Semantic-watch data has changed; redraw the sem-watch pane."""


class _ProgressRow(Static):
    """One horizontal row in the progress panel."""

    def __init__(self, prog_id: int):
        super().__init__("", id=f"prog-{prog_id}")
        self.prog_id = prog_id

    def update_progress(self, payload: dict) -> None:
        n = payload.get("n", 0)
        total = payload.get("total")
        unit = payload.get("unit", "")
        section = payload.get("section", "status")
        colour = _SECTION_COLOURS.get(section, "white")
        desc = payload.get("desc") or section
        postfix = " ".join(f"{k}={v}" for k, v in (payload.get("postfix") or {}).items())
        elapsed = payload.get("elapsed", 0.0)
        rate = (n / elapsed) if elapsed > 0 else 0.0
        if total and total > 0:
            pct = min(100.0, (n / total) * 100.0)
            width = 28
            filled = int(width * pct / 100.0)
            bar = "█" * filled + "░" * (width - filled)
            head = f"[{colour}]{desc:<22}[/] {bar} {pct:5.1f}%  {n}/{total} {unit}"
        else:
            head = f"[{colour}]{desc:<22}[/] {n} {unit}"
        tail = f"  {rate:5.1f}/s  {elapsed:6.1f}s  {postfix}".rstrip()
        self.update(head + tail)


class CodebaseInsightsTUI(App):
    """Top-level Textual app."""

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar {
        width: 22;
        border-right: solid $primary;
        padding: 0 1;
    }
    #sidebar ListView { height: 1fr; }
    #sidebar ListItem.-highlighted { background: $accent 30%; }
    #log_pane { padding: 0 1; width: 1fr; }
    RichLog {
        background: $surface;
        height: 1fr;
        width: 1fr;
        border: round $primary 30%;
    }
    #overview_pane {
        padding: 1 2;
        height: 1fr;
        overflow-y: auto;
    }
    #overview_content { width: 1fr; }
    #sem_watch_pane {
        padding: 1 2;
        height: 1fr;
        overflow-y: auto;
    }
    #sem_watch_content { width: 1fr; }
    .ov-group-title {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    .ov-row { width: 1fr; }
    #progress_panel {
        height: auto;
        max-height: 8;
        border: round $secondary 40%;
        padding: 0 1;
        margin: 0 1;
    }
    #progress_panel #progress_title { color: $secondary; }
    """

    BINDINGS = [
        Binding("1", "select(0)", "Overview",    show=False),
        Binding("2", "select(1)", "All Logs",    show=False),
        Binding("3", "select(2)", "Sem Watch",   show=False),
        Binding("4", "select(3)", "Status",      show=False),
        Binding("5", "select(4)", "Indexer",     show=False),
        Binding("6", "select(5)", "Semantic",    show=False),
        Binding("7", "select(6)", "LSP",         show=False),
        Binding("8", "select(7)", "HTTP",        show=False),
        Binding("9", "select(8)", "Bench",       show=False),
        Binding("0", "select(9)", "Config",      show=False),
        Binding("up",       "scroll_line(-1)", "Scroll ↑", show=False),
        Binding("down",     "scroll_line(1)",  "Scroll ↓", show=False),
        Binding("pageup",   "scroll_page(-1)", "Page ↑",   show=False),
        Binding("pagedown", "scroll_page(1)",  "Page ↓",   show=False),
        Binding("home", "scroll_home", "Top",    show=False),
        Binding("end",  "scroll_end",  "Bottom", show=False),
        Binding("tab",       "next_section", "Next"),
        Binding("shift+tab", "prev_section", "Prev"),
        Binding("f", "toggle_follow", "Follow"),
        Binding("c", "clear_section", "Clear"),
        Binding("q",      "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    current_section: reactive[str] = reactive("overview")
    follow_tail: reactive[bool] = reactive(True)

    def __init__(
        self,
        *,
        title: str,
        project_root: str,
        worker: Callable[["CodebaseInsightsTUI"], None],
        on_exit: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self._title = title
        self._project_root = project_root
        self._worker = worker
        self._on_exit = on_exit
        # Per-section line buffers (so switching sections is instant + scrollable).
        self._buffers: dict[str, deque[str]] = {
            s: deque(maxlen=_MAX_BUFFER_LINES) for s in cli_io.SECTIONS
        }
        self._unread: dict[str, int] = {s: 0 for s in cli_io.SECTIONS}
        # Combined buffer for the "All Logs" view — stores (section, line) tuples
        # so each line can be rendered with its original section colour.
        self._all_buffer: deque[tuple[str, str]] = deque(maxlen=_MAX_BUFFER_LINES)
        self._progress_rows: dict[int, _ProgressRow] = {}
        self._buf_lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None
        # Overview fields — updated via update_overview()
        self._overview_fields: dict[str, str] = {
            "project.path":         project_root,
            "project.languages":    "detecting…",
            "config.chat":          "—",
            "config.embed":         "—",
            "indexer.status":       "starting…",
            "indexer.files":        "—",
            "indexer.symbols":      "—",
            "semantic.status":      "—",
            "semantic.summarised":  "—",
            "server.url":           "—",
            "server.status":        "starting…",
        }
        # Semantic Watch state — updated via update_sem_watch()
        self._sem_watch_snapshot: dict = {}
        self._sem_watch_stale: list[tuple[str, float | None]] = []  # (file_path, generated_at)

    # ------------------------------------------------------------------ UI --

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("[b]Sections[/]")
                yield ListView(
                    *[
                        ListItem(Label(self._sidebar_label(s)), id=f"sec-{s}")
                        for s in cli_io.SECTIONS
                    ],
                    id="section_list",
                )
            # Overview pane (structured dashboard — visible when section=overview)
            with VerticalScroll(id="overview_pane"):
                yield Static("", id="overview_content")
            # Semantic Watch pane (live timer/stale-file table — section=sem_watch)
            with VerticalScroll(id="sem_watch_pane"):
                yield Static("", id="sem_watch_content")
            # Log pane (RichLog — visible for all other sections)
            with Vertical(id="log_pane"):
                yield RichLog(
                    id="log",
                    highlight=False,
                    markup=True,
                    wrap=True,
                    auto_scroll=True,
                )
        with Vertical(id="progress_panel"):
            yield Label("[b]Progress[/] (no active tasks)", id="progress_title")
        yield Footer()

    def on_mount(self) -> None:
        self.title = self._title
        self.sub_title = self._project_root
        # Start on Overview — hide the log and sem_watch panes.
        self.query_one("#log_pane").display = False
        self.query_one("#sem_watch_pane").display = False
        self.query_one("#overview_pane").display = True
        self._refresh_overview_widget()
        # Wire cli_io to push events into the TUI from any thread.
        cli_io.attach_sinks(self._sink_log, self._sink_progress)
        list_view = self.query_one("#section_list", ListView)
        list_view.index = 0
        # Run the headless work (LSP init, indexer, MCP server) in the background.
        self._worker_thread = threading.Thread(
            target=self._run_worker, daemon=True, name="codebase-insights-worker"
        )
        self._worker_thread.start()

    def on_unmount(self) -> None:
        cli_io.detach_sinks()
        if self._on_exit:
            try:
                self._on_exit()
            except Exception:
                pass

    # ---------------------------------------------------------------- worker --

    def _run_worker(self) -> None:
        try:
            self._worker(self)
        except SystemExit:
            self.call_from_thread(self.exit)
        except Exception as e:  # pragma: no cover - defensive
            cli_io.emit(f"[Status] FATAL worker error: {e}", "status")
            self.call_from_thread(self.exit)

    # ----------------------------------------------------------------- sinks --

    def _sink_log(self, section: str, line: str) -> None:
        section = section if section in self._buffers else "status"
        with self._buf_lock:
            self._buffers[section].append(line)
            if section != self.current_section:
                self._unread[section] += 1
            # Mirror every line to the combined "all" buffer.
            self._all_buffer.append((section, line))
            if self.current_section != "all":
                self._unread["all"] += 1
        # post_message is thread-safe and works from any thread (including the
        # event-loop thread), unlike call_from_thread which raises RuntimeError
        # when called from the same thread as the event loop.
        try:
            self.post_message(_LogLine(section, line))
        except Exception:
            pass

    def _sink_progress(self, payload: dict) -> None:
        try:
            self.post_message(_ProgressEvent(payload))
        except Exception:
            pass

    # ----------------------------------------------------------- UI handlers --

    def on__log_line(self, message: _LogLine) -> None:
        self._on_log_pushed(message.section, message.line)

    def on__progress_event(self, message: _ProgressEvent) -> None:
        self._handle_progress_event(message.payload)

    def on__overview_update(self, _message: _OverviewUpdate) -> None:
        self._refresh_overview_widget()

    def on__sem_watch_update(self, _message: _SemWatchUpdate) -> None:
        self._refresh_sem_watch_widget()

    def _on_log_pushed(self, section: str, line: str) -> None:
        if section == self.current_section:
            log = self.query_one("#log", RichLog)
            log.write(self._format_line(section, line))
        elif self.current_section == "all":
            log = self.query_one("#log", RichLog)
            log.write(self._format_line_all(section, line))
        # Refresh sidebar unread counts.
        try:
            item = self.query_one(f"#sec-{section}", ListItem)
            label = item.query_one(Label)
            label.update(self._sidebar_label(section))
        except Exception:
            pass
        try:
            item = self.query_one("#sec-all", ListItem)
            item.query_one(Label).update(self._sidebar_label("all"))
        except Exception:
            pass

    def _handle_progress_event(self, payload: dict) -> None:
        kind = payload.get("kind")
        pid = payload.get("id")
        panel = self.query_one("#progress_panel", Vertical)
        title = self.query_one("#progress_title", Label)
        if kind == "start":
            row = _ProgressRow(pid)
            self._progress_rows[pid] = row
            panel.mount(row)
            row.update_progress(payload)
        elif kind == "update":
            row = self._progress_rows.get(pid)
            if row is None:
                row = _ProgressRow(pid)
                self._progress_rows[pid] = row
                panel.mount(row)
            row.update_progress(payload)
        elif kind == "end":
            row = self._progress_rows.pop(pid, None)
            if row is not None:
                row.update_progress(payload)
                self.set_timer(2.5, lambda r=row: r.remove())
        active = len(self._progress_rows)
        title.update(
            "[b]Progress[/] (no active tasks)" if active == 0
            else f"[b]Progress[/] · {active} active task{'s' if active != 1 else ''}"
        )

    # ----------------------------------------------------------- Overview --

    def update_overview(self, fields: dict[str, str]) -> None:
        """Update one or more overview fields from any thread."""
        with self._buf_lock:
            self._overview_fields.update(fields)
        try:
            self.post_message(_OverviewUpdate())
        except Exception:
            pass

    def _refresh_overview_widget(self) -> None:
        with self._buf_lock:
            fields = dict(self._overview_fields)
        content = self.query_one("#overview_content", Static)
        content.update(self._render_overview(fields))

    def _render_overview(self, fields: dict[str, str]) -> str:
        lines: list[str] = []
        lines.append(
            f"[bold bright_white]Codebase-Insights[/]  "
            f"[dim]v1.2.2[/]\n"
        )
        for group_title, group_fields in _OVERVIEW_GROUPS:
            lines.append(f"[bold cyan]── {group_title} {'─' * max(0, 38 - len(group_title))}[/]")
            for key, label in group_fields:
                value = _rich_escape(fields.get(key, "—"))
                lines.append(f"  [dim]{label:<16}[/] {value}")
            lines.append("")
        return "\n".join(lines)

    # --------------------------------------------------------- Semantic Watch --

    def update_sem_watch(
        self,
        snapshot: dict,
        stale_rows: list[tuple[str, float | None]],
    ) -> None:
        """Update the Semantic Watch pane from any thread."""
        with self._buf_lock:
            self._sem_watch_snapshot = snapshot
            self._sem_watch_stale = stale_rows
        try:
            self.post_message(_SemWatchUpdate())
        except Exception:
            pass

    def _refresh_sem_watch_widget(self) -> None:
        with self._buf_lock:
            snap = dict(self._sem_watch_snapshot)
            stale = list(self._sem_watch_stale)
        content = self.query_one("#sem_watch_content", Static)
        content.update(self._render_sem_watch(snap, stale))

    def _render_sem_watch(
        self,
        snap: dict,
        stale_rows: list[tuple[str, float | None]],
    ) -> str:
        """Render the Semantic Watch panel as Rich markup."""
        if not snap:
            return "[dim]Semantic Watch: waiting for data…[/]"

        lines: list[str] = []
        lines.append("[bold bright_white]Semantic Watch[/]\n")

        # ── Config ──────────────────────────────────────────────────────
        lines.append("[bold cyan]── Config ─────────────────────────────[/]")
        threshold   = snap.get("threshold", "?")
        f_timeout   = snap.get("file_idle_timeout", 0)
        p_timeout   = snap.get("project_idle_timeout", 0)
        pending     = snap.get("pending_stale_paths", [])
        pending_cnt = len(pending)

        lines.append(
            f"  [dim]Pending files[/]       "
            f"{pending_cnt} [dim](threshold: {threshold})[/]"
        )
        lines.append(
            f"  [dim]File idle timeout[/]   "
            + (f"{f_timeout:.0f}s" if f_timeout else "[dim]disabled[/]")
        )
        lines.append(
            f"  [dim]Project idle timeout[/] "
            + (f"{p_timeout:.0f}s" if p_timeout else "[dim]disabled[/]")
        )
        lines.append("")

        # ── Next regeneration ────────────────────────────────────────────
        lines.append("[bold cyan]── Next Regeneration ───────────────────[/]")
        idle = snap.get("project_idle_remaining")

        if idle is not None:
            lines.append(f"  [bold yellow]in {idle:.0f}s[/]  [dim](project idle timeout)[/]")
            lines.append(f"  [dim]Idle timer:[/]        {idle:.1f}s remaining")
        else:
            lines.append("  [dim]No regeneration scheduled[/]")
        lines.append("")

        # ── Stale files from DB ──────────────────────────────────────────
        file_timers: dict[str, float] = snap.get("file_timers", {})
        lines.append(
            f"[bold cyan]── Stale Files ({len(stale_rows)}) ─────────────────────[/]"
        )
        if not stale_rows:
            lines.append("  [dim green]All file summaries are up to date ✓[/]")
        else:
            # Header row
            lines.append(
                f"  [dim]{'File':<48}  {'Gen at':<20}  {'File idle'}[/]"
            )
            lines.append(f"  [dim]{'─'*48}  {'─'*20}  {'─'*12}[/]")
            for fp, gen_at in stale_rows:
                import os as _os
                rel = fp
                try:
                    rel = _os.path.relpath(fp, self._project_root)
                except ValueError:
                    pass
                # Truncate long paths from the left
                if len(rel) > 48:
                    rel = "…" + rel[-(47):]
                gen_str = ""
                if gen_at is not None:
                    import time as _time
                    try:
                        gen_str = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(gen_at))
                    except Exception:
                        gen_str = str(gen_at)
                timer_str = ""
                if fp in file_timers:
                    timer_str = f"{file_timers[fp]:.0f}s"
                elif fp in pending:
                    timer_str = "[dim]queued[/]"
                lines.append(
                    f"  [yellow]{_rich_escape(rel):<48}[/]  "
                    f"[dim]{gen_str:<20}[/]  "
                    f"[magenta]{timer_str}[/]"
                )
        lines.append("")

        # ── Pending (in-memory queue) ────────────────────────────────────
        lines.append(
            f"[bold cyan]── In-Memory Queue ({pending_cnt}/{threshold}) ────────────────[/]"
        )
        if not pending:
            lines.append("  [dim]Queue empty[/]")
        else:
            for fp in pending:
                import os as _os
                rel = fp
                try:
                    rel = _os.path.relpath(fp, self._project_root)
                except ValueError:
                    pass
                if len(rel) > 60:
                    rel = "…" + rel[-59:]
                timer_str = f"  [magenta]{file_timers[fp]:.0f}s[/]" if fp in file_timers else ""
                lines.append(f"  [yellow]{_rich_escape(rel)}[/]{timer_str}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------- formatting --

    def _format_line(self, section: str, line: str) -> str:
        colour = _SECTION_COLOURS.get(section, "white")
        if line.startswith("[STDERR] "):
            body = _rich_escape(line[len("[STDERR] "):])
            return f"[dim]{section[:4]:<4}[/] [bold red]ERR[/] [red]{body}[/]"
        return f"[dim]{section[:4]:<4}[/] [{colour}]{_rich_escape(line)}[/]"

    def _format_line_all(self, section: str, line: str) -> str:
        """Format a line for the combined All Logs view, showing the source section."""
        colour = _SECTION_COLOURS.get(section, "white")
        tag = section[:5].upper()
        if line.startswith("[STDERR] "):
            body = _rich_escape(line[len("[STDERR] "):])
            return f"[dim {colour}]{tag:<5}[/] [bold red]ERR[/] [red]{body}[/]"
        return f"[dim {colour}]{tag:<5}[/] [{colour}]{_rich_escape(line)}[/]"

    def _sidebar_label(self, section: str) -> str:
        name = _SECTION_LABELS.get(section, section.title())
        unread = self._unread.get(section, 0)
        if unread > 0 and section != self.current_section:
            return f"{name}  [b yellow]({unread})[/]"
        return name

    # --------------------------------------------------------------- actions --

    def action_select(self, idx: int) -> None:
        if 0 <= idx < len(cli_io.SECTIONS):
            list_view = self.query_one("#section_list", ListView)
            list_view.index = idx

    def action_next_section(self) -> None:
        list_view = self.query_one("#section_list", ListView)
        list_view.index = ((list_view.index or 0) + 1) % len(cli_io.SECTIONS)

    def action_prev_section(self) -> None:
        list_view = self.query_one("#section_list", ListView)
        list_view.index = ((list_view.index or 0) - 1) % len(cli_io.SECTIONS)

    def action_scroll_line(self, delta: int) -> None:
        if self.current_section in ("overview", "sem_watch"):
            pane_id = "#overview_pane" if self.current_section == "overview" else "#sem_watch_pane"
            pane = self.query_one(pane_id, VerticalScroll)
            pane.scroll_relative(y=delta, animate=False)
            return
        log = self.query_one("#log", RichLog)
        log.scroll_relative(y=delta, animate=False)
        if delta < 0:
            log.auto_scroll = False
            self.follow_tail = False

    def action_scroll_page(self, delta: int) -> None:
        if self.current_section in ("overview", "sem_watch"):
            pane_id = "#overview_pane" if self.current_section == "overview" else "#sem_watch_pane"
            pane = self.query_one(pane_id, VerticalScroll)
            pane.scroll_page_down() if delta > 0 else pane.scroll_page_up()
            return
        log = self.query_one("#log", RichLog)
        log.scroll_page_down() if delta > 0 else log.scroll_page_up()
        if delta < 0:
            log.auto_scroll = False
            self.follow_tail = False

    def action_scroll_home(self) -> None:
        if self.current_section in ("overview", "sem_watch"):
            pane_id = "#overview_pane" if self.current_section == "overview" else "#sem_watch_pane"
            self.query_one(pane_id, VerticalScroll).scroll_home(animate=False)
            return
        log = self.query_one("#log", RichLog)
        log.scroll_home(animate=False)
        log.auto_scroll = False
        self.follow_tail = False

    def action_scroll_end(self) -> None:
        if self.current_section in ("overview", "sem_watch"):
            pane_id = "#overview_pane" if self.current_section == "overview" else "#sem_watch_pane"
            self.query_one(pane_id, VerticalScroll).scroll_end(animate=False)
            return
        log = self.query_one("#log", RichLog)
        log.scroll_end(animate=False)
        log.auto_scroll = True
        self.follow_tail = True

    def action_toggle_follow(self) -> None:
        if self.current_section in ("overview", "sem_watch"):
            return
        self.follow_tail = not self.follow_tail
        log = self.query_one("#log", RichLog)
        log.auto_scroll = self.follow_tail
        if self.follow_tail:
            log.scroll_end(animate=False)

    def action_clear_section(self) -> None:
        sec = self.current_section
        if sec in ("overview", "sem_watch"):
            return  # Live dashboards; clearing is a no-op.
        with self._buf_lock:
            if sec == "all":
                self._all_buffer.clear()
            else:
                self._buffers[sec].clear()
            self._unread[sec] = 0
        log = self.query_one("#log", RichLog)
        log.clear()
        try:
            item = self.query_one(f"#sec-{sec}", ListItem)
            item.query_one(Label).update(self._sidebar_label(sec))
        except Exception:
            pass

    # -------------------------------------------------------- list selection --

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        item_id = event.item.id or ""
        if item_id.startswith("sec-"):
            sec = item_id[len("sec-"):]
            if sec in self._buffers or sec in ("all", "overview", "sem_watch"):
                self._switch_section(sec)

    def _switch_section(self, section: str) -> None:
        if section == self.current_section and self._unread[section] == 0:
            return
        self.current_section = section
        overview_pane   = self.query_one("#overview_pane",  VerticalScroll)
        sem_watch_pane  = self.query_one("#sem_watch_pane", VerticalScroll)
        log_pane        = self.query_one("#log_pane",       Vertical)
        if section == "overview":
            log_pane.display      = False
            sem_watch_pane.display = False
            overview_pane.display = True
            self._refresh_overview_widget()
        elif section == "sem_watch":
            log_pane.display      = False
            overview_pane.display = False
            sem_watch_pane.display = True
            self._refresh_sem_watch_widget()
        elif section == "all":
            overview_pane.display  = False
            sem_watch_pane.display = False
            log_pane.display       = True
            with self._buf_lock:
                all_lines = list(self._all_buffer)
                self._unread["all"] = 0
            log = self.query_one("#log", RichLog)
            log.clear()
            for sec, line in all_lines:
                log.write(self._format_line_all(sec, line))
            log.auto_scroll = self.follow_tail
            if self.follow_tail:
                log.scroll_end(animate=False)
        else:
            overview_pane.display  = False
            sem_watch_pane.display = False
            log_pane.display       = True
            with self._buf_lock:
                lines = list(self._buffers[section])
                self._unread[section] = 0
            log = self.query_one("#log", RichLog)
            log.clear()
            for line in lines:
                log.write(self._format_line(section, line))
            log.auto_scroll = self.follow_tail
            if self.follow_tail:
                log.scroll_end(animate=False)
        # Refresh sidebar (clear unread badge).
        try:
            for s in cli_io.SECTIONS:
                item = self.query_one(f"#sec-{s}", ListItem)
                item.query_one(Label).update(self._sidebar_label(s))
        except Exception:
            pass


def run_tui(*, title: str, project_root: str,
            worker: Callable[[CodebaseInsightsTUI], None],
            on_exit: Optional[Callable[[], None]] = None) -> None:
    """Boot the TUI on the main thread."""
    # install_print_redirect / install_logging may already have been called by
    # main() before the wizard ran (pre_buffer=True mode); calling them again
    # here is safe — both are idempotent.
    cli_io.install_print_redirect(pre_buffer=False)
    cli_io.install_logging()
    app = CodebaseInsightsTUI(
        title=title,
        project_root=project_root,
        worker=worker,
        on_exit=on_exit,
    )
    try:
        app.run()
    finally:
        cli_io.detach_sinks()
        cli_io.restore_print_redirect()
