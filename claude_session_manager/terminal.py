"""A tab hosting a VTE terminal running the user's shell with an agent CLI inside."""

from __future__ import annotations

import os
import shlex
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import themes  # noqa: E402
from .i18n import _  # noqa: E402
from .promptcard import build_question_card  # noqa: E402
from .providers import Provider, get_provider  # noqa: E402
from .transcript import TranscriptModel  # noqa: E402

_TRANSCRIPT_DEBOUNCE_MS = 400
_PROMPT_POLL_MS = 1000  # backstop poll for detecting the agent's prompts

# PCRE2 flags for the find bar: multiline, case-insensitive.
_PCRE2_CASELESS = 0x00000008
_PCRE2_MULTILINE = 0x00000400
_SEARCH_FLAGS = _PCRE2_CASELESS | _PCRE2_MULTILINE


def _has_running_command(terminal: Vte.Terminal, child_pid: int | None) -> bool:
    """True when something other than the spawned shell owns the terminal's
    foreground — the cue terminal emulators use for close-confirmation."""
    if child_pid is None:
        return False
    pty = terminal.get_pty()
    if pty is None:
        return False
    try:
        foreground = os.tcgetpgrp(pty.get_fd())
        return foreground not in (-1, os.getpgid(child_pid))
    except OSError:
        return False


def _process_cwd(pid: int | None) -> str | None:
    if not pid or pid <= 0:
        return None
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


class PanelTerminal(Gtk.Box):
    """The tab's secondary terminal: a plain shell with no agent auto-launched.

    Spawns lazily the first time it is shown and survives hide/show and
    bottom↔right swaps; the shell is only lost when the tab itself closes."""

    __gsignals__ = {
        # Emitted when the panel's shell exits (e.g. the user typed `exit`).
        "shell-exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._child_pid: int | None = None
        self._spawned = False
        self._easy_copy_paste = False

        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(10_000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        self.terminal.connect("child-exited", self._on_child_exited)

        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.append(scrolled)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.terminal.add_controller(keys)

    def open_shell(self, cwd: str | None) -> None:
        """Spawn the shell on first show; on later shows follow the agent's
        cwd (it may have moved into a worktree) if the shell sits idle."""
        if not self._spawned:
            self._spawn(cwd)
        else:
            self._sync_cwd(cwd)

    def _spawn(self, cwd: str | None) -> None:
        self._spawned = True
        if cwd is None or not Path(cwd).is_dir():
            cwd = str(Path.home())
        shell = os.environ.get("SHELL") or "/bin/bash"
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            [shell],
            None,  # envv: inherit
            GLib.SpawnFlags.DEFAULT,
            None,  # child_setup
            None,  # child_setup_data
            -1,  # timeout
            None,  # cancellable
            self._on_spawned,
        )

    def _on_spawned(self, terminal: Vte.Terminal, pid: int, error: GLib.Error | None) -> None:
        if error is not None:
            terminal.feed(
                _("failed to start shell: {msg}").format(msg=error.message).encode()
            )
            return
        self._child_pid = pid

    def _on_child_exited(self, _terminal: Vte.Terminal, _status: int) -> None:
        self._spawned = False  # a fresh shell is spawned on the next show
        self._child_pid = None
        self.terminal.reset(True, True)
        self.emit("shell-exited")

    def _sync_cwd(self, cwd: str | None) -> None:
        if not cwd or not Path(cwd).is_dir() or self._child_pid is None:
            return
        if self.has_running_command():
            return  # don't interrupt whatever the user left running
        if _process_cwd(self._child_pid) == cwd:
            return
        # \x15 (kill-line) clears any half-typed input before the cd.
        self.terminal.feed_child(f"\x15cd {shlex.quote(cwd)}\n".encode())

    def has_running_command(self) -> bool:
        return _has_running_command(self.terminal, self._child_pid)

    def apply_settings(self, settings: dict) -> None:
        font = settings.get("font") or ""
        self.terminal.set_font(Pango.FontDescription.from_string(font) if font else None)
        try:
            self.terminal.set_scrollback_lines(int(settings.get("scrollback") or 10_000))
        except (TypeError, ValueError):
            pass
        themes.apply_terminal_theme(self.terminal, settings.get("terminal_theme"))
        self._easy_copy_paste = bool(settings.get("easy_copy_paste"))

    def grab_terminal_focus(self) -> None:
        self.terminal.grab_focus()

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if self._easy_copy_paste and ctrl and not shift:
            if keyval == Gdk.KEY_c and self.terminal.get_has_selection():
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keyval == Gdk.KEY_v:
                self.terminal.paste_clipboard()
                return True
        if ctrl and shift:
            if keyval == Gdk.KEY_C:
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keyval == Gdk.KEY_V:
                self.terminal.paste_clipboard()
                return True
        return False


class TerminalTab(Gtk.Box):
    """Embeds Vte.Terminal (with a find bar) and spawns an agent CLI into it."""

    __gsignals__ = {
        # Emitted when the agent process exits (int = exit status).
        "process-exited": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # Emitted when a tab started without a session id (new / continue)
        # discovers which session it is running (str = session id).
        "session-resolved": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Emitted (debounced) when the panel divider is moved, so the window
        # can persist the tab's per-layout positions.
        "panel-layout-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        cwd: str | None,
        session_id: str | None = None,
        fork: bool = False,
        settings: dict | None = None,
        provider: Provider | None = None,
        jsonl_path: str | Path | None = None,
        options=None,
        command_override: str | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.session_id = session_id
        self.fork = fork
        self.provider = provider or get_provider("claude")
        self._options = options
        self._command_override = command_override
        self._child_pid: int | None = None
        self._transcript_monitor: Gio.FileMonitor | None = None
        self._transcript_refresh_source: int | None = None
        self._poll_source: int | None = None
        self._resolver_source: int | None = None
        self._resolver_attempts = 0
        self._known_transcripts: set[Path] = set()  # transcripts predating this tab
        self._updating = False  # an off-thread transcript parse is in flight
        self._current_question_id: str | None = None  # question the card is showing
        self._handled_question_id: str | None = None  # answered/dismissed; don't reshow
        self._card: Gtk.Widget | None = None

        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(10_000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        self.terminal.connect("child-exited", self._on_child_exited)

        self._easy_copy_paste = False
        self._setup_context_menu()

        self._search_bar = self._build_search_bar()
        self.append(self._search_bar)

        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # The terminal is the single live view. When the agent asks a structured
        # question (detected from the transcript), a native card overlays it.
        self._overlay = Gtk.Overlay()
        self._overlay.set_vexpand(True)
        self._overlay.set_child(scrolled)

        # Secondary plain-shell panel, below or beside the agent terminal.
        # Swapping bottom↔right only flips the paned's orientation, so the
        # panel's shell keeps running.
        self._panel = PanelTerminal()
        self._panel.set_visible(False)
        self._panel.connect("shell-exited", lambda *_: self.hide_panel())
        panel_right = bool(settings) and settings.get("panel_position") == "right"
        self._paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL if panel_right else Gtk.Orientation.VERTICAL,
            vexpand=True,
        )
        # The hairline divider is hard to grab; use the wide handle throughout.
        self._paned.set_wide_handle(True)
        self._panel_positions: dict[str, int] = {}  # divider px per mode (bottom/right)
        self._panel_position_pending = False  # a programmatic apply is queued
        self._layout_emit_source: int | None = None  # debounce for panel-layout-changed
        # Dragging the divider records the new spot for the current mode.
        self._paned.connect("notify::position", lambda *_: self._remember_panel_position())
        self._paned.set_start_child(self._overlay)
        self._paned.set_end_child(self._panel)
        self._paned.set_resize_start_child(True)
        self._paned.set_shrink_start_child(False)
        self._paned.set_resize_end_child(False)
        self._paned.set_shrink_end_child(False)
        self.append(self._paned)

        self._transcript = TranscriptModel(jsonl_path, self.provider.id)

        # Ctrl+Shift+C / Ctrl+Shift+V / Ctrl+Shift+G, terminal-style
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.terminal.add_controller(keys)

        if settings:
            self.apply_settings(settings)
        self.set_transcript_path(jsonl_path)
        self._spawn(cwd, session_id)
        if jsonl_path is None and session_id is None:
            self._start_transcript_resolver(cwd)  # find the new session's transcript

    # -- spawning ----------------------------------------------------------

    def _spawn(self, cwd: str | None, session_id: str | None) -> None:
        if cwd is None or not Path(cwd).is_dir():
            if cwd is not None:
                self.feed_message(
                    _("warning: project dir {cwd} no longer exists, starting in HOME").format(cwd=cwd)
                )
            cwd = str(Path.home())
        self._cwd = cwd

        # Run the user's interactive shell and type the agent command into it,
        # so aliases/env apply and the tab drops to a prompt when the agent exits.
        # The tab closes when the *shell* exits.
        self._initial_command: str | None = None
        if self._command_override is not None:
            command = self._command_override
        elif session_id is not None:
            command = self.provider.resume_command(session_id, fork=self.fork)
        else:
            command = self.provider.new_command(self._options)
        if command is None:
            self.feed_message(
                _("warning: `{cli}` not found in PATH — starting a plain shell").format(
                    cli=self.provider.cli
                )
            )
        else:
            self._initial_command = command

        shell = os.environ.get("SHELL") or "/bin/bash"
        argv = [shell]

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            argv,
            None,  # envv: inherit
            GLib.SpawnFlags.DEFAULT,
            None,  # child_setup
            None,  # child_setup_data
            -1,  # timeout
            None,  # cancellable
            self._on_spawned,
        )

    def _on_spawned(self, terminal: Vte.Terminal, pid: int, error: GLib.Error | None) -> None:
        if error is not None:
            self.feed_message(_("failed to start shell: {msg}").format(msg=error.message))
            return
        self._child_pid = pid
        if self._initial_command:
            terminal.feed_child(f"{self._initial_command}\n".encode())

    def _on_child_exited(self, terminal: Vte.Terminal, status: int) -> None:
        self.emit("process-exited", status)

    # -- copy & paste ------------------------------------------------------

    def _setup_context_menu(self) -> None:
        actions = Gio.SimpleActionGroup()
        self._copy_action = Gio.SimpleAction.new("copy", None)
        self._copy_action.connect(
            "activate", lambda *_: self.terminal.copy_clipboard_format(Vte.Format.TEXT)
        )
        actions.add_action(self._copy_action)
        paste = Gio.SimpleAction.new("paste", None)
        paste.connect("activate", lambda *_: self.terminal.paste_clipboard())
        actions.add_action(paste)
        select_all = Gio.SimpleAction.new("select-all", None)
        select_all.connect("activate", lambda *_: self.terminal.select_all())
        actions.add_action(select_all)
        self.terminal.insert_action_group("term", actions)

        # Capture phase, so the menu wins over apps that request mouse events
        # (matching GNOME Terminal's right-click behaviour).
        right_click = Gtk.GestureClick(button=3)
        right_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        right_click.connect("pressed", self._on_right_click)
        self.terminal.add_controller(right_click)

    def _on_right_click(self, gesture: Gtk.GestureClick, _n_press, x: float, y: float) -> None:
        if not self._easy_copy_paste:  # leave the click to VTE / the running app
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._copy_action.set_enabled(self.terminal.get_has_selection())

        menu = Gio.Menu()
        menu.append(_("Copy"), "term.copy")
        menu.append(_("Paste"), "term.paste")
        menu.append(_("Select All"), "term.select-all")

        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self.terminal)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    # -- search bar --------------------------------------------------------

    def _build_search_bar(self) -> Gtk.SearchBar:
        bar = Gtk.SearchBar()
        self._search_entry = Gtk.SearchEntry(hexpand=True, placeholder_text=_("Find in terminal…"))
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("activate", lambda *_: self._search_step(forward=False))
        self._search_entry.connect("next-match", lambda *_: self._search_step(forward=True))
        self._search_entry.connect("previous-match", lambda *_: self._search_step(forward=False))
        self._search_entry.connect("stop-search", lambda *_: self.hide_search())

        prev_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Previous match"))
        prev_btn.connect("clicked", lambda *_: self._search_step(forward=False))
        next_btn = Gtk.Button(icon_name="go-down-symbolic", tooltip_text=_("Next match"))
        next_btn.connect("clicked", lambda *_: self._search_step(forward=True))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(self._search_entry)
        box.append(prev_btn)
        box.append(next_btn)
        bar.set_child(box)
        bar.connect_entry(self._search_entry)
        bar.set_show_close_button(True)
        bar.connect("notify::search-mode-enabled", self._on_search_mode_changed)
        self.terminal.search_set_wrap_around(True)
        return bar

    def _on_search_mode_changed(self, bar: Gtk.SearchBar, _pspec) -> None:
        if not bar.get_search_mode():  # cleared via the close button or Escape
            self.terminal.search_set_regex(None, 0)
            self.grab_terminal_focus()

    def toggle_search(self) -> None:
        if self._search_bar.get_search_mode():
            self.hide_search()
        else:
            self._search_bar.set_search_mode(True)
            self._search_entry.grab_focus()

    def hide_search(self) -> None:
        # _on_search_mode_changed clears the regex and refocuses the terminal.
        self._search_bar.set_search_mode(False)

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text()
        if not query:
            self.terminal.search_set_regex(None, 0)
            return
        pattern = GLib.Regex.escape_string(query, -1)
        try:
            regex = Vte.Regex.new_for_search(pattern, len(pattern.encode()), _SEARCH_FLAGS)
        except GLib.Error:
            return
        self.terminal.search_set_regex(regex, 0)
        self._search_step(forward=False)  # nearest match above the prompt

    def _search_step(self, forward: bool) -> None:
        if forward:
            self.terminal.search_find_next()
        else:
            self.terminal.search_find_previous()

    # -- graceful close ----------------------------------------------------

    def feed_child_text(self, text: str) -> None:
        self.terminal.feed_child(text.encode())

    # -- prompt card -------------------------------------------------------

    def set_transcript_path(self, jsonl_path: str | Path | None) -> None:
        """Tail a transcript to detect the agent's structured prompts. Used on
        resume, and again once a brand-new session's file appears on disk."""
        self._transcript.set_path(jsonl_path)
        self._current_question_id = None
        self._handled_question_id = None
        self._hide_card()
        if self._transcript_monitor is not None:
            self._transcript_monitor.cancel()
            self._transcript_monitor = None
        if jsonl_path:
            try:
                gfile = Gio.File.new_for_path(str(jsonl_path))
                self._transcript_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
                self._transcript_monitor.connect("changed", self._on_transcript_event)
            except GLib.Error:
                self._transcript_monitor = None
            self._ensure_poll()
            self._request_update()

    def _ensure_poll(self) -> None:
        if self._poll_source is None:
            self._poll_source = GLib.timeout_add(_PROMPT_POLL_MS, self._poll)

    def _poll(self) -> bool:
        if self.get_root() is None:  # tab closed/detached → stop ticking
            self._poll_source = None
            return GLib.SOURCE_REMOVE
        self._request_update()
        return GLib.SOURCE_CONTINUE

    def _on_transcript_event(self, *_args) -> None:
        if self._transcript_refresh_source is not None:
            return
        self._transcript_refresh_source = GLib.timeout_add(
            _TRANSCRIPT_DEBOUNCE_MS, self._debounced_update
        )

    def _debounced_update(self) -> bool:
        self._transcript_refresh_source = None
        self._request_update()
        return GLib.SOURCE_REMOVE

    def _request_update(self) -> None:
        """Parse newly-appended transcript bytes off the main thread (big
        tool-result lines would otherwise freeze the UI), then check on idle."""
        if self._updating:
            return
        self._updating = True

        def work() -> None:
            try:
                self._transcript.update()
            except Exception:
                pass
            GLib.idle_add(self._apply_update)

        threading.Thread(target=work, daemon=True).start()

    def _apply_update(self) -> bool:
        self._updating = False
        self._check_prompt()
        return GLib.SOURCE_REMOVE

    def _check_prompt(self) -> None:
        pending = self._transcript.pending_question()
        if pending is None:
            self._hide_card()
            self._current_question_id = None
            self._handled_question_id = None
            return
        qid = pending.tool_use_id
        if qid in (self._current_question_id, self._handled_question_id):
            return
        self._hide_card()
        self._current_question_id = qid
        self._card = build_question_card(
            pending.questions, self.provider, self._answer, self._dismiss_card
        )
        self._overlay.add_overlay(self._card)

    def _hide_card(self) -> None:
        if self._card is not None:
            self._overlay.remove_overlay(self._card)
            self._card = None

    def _answer(self, questions: list, option_index: int) -> None:
        self._handled_question_id = self._current_question_id
        self._current_question_id = None
        self._hide_card()
        keys = self.provider.answer_keystrokes(questions, option_index)
        if keys:
            self.feed_child_text(keys)
        else:
            self.grab_terminal_focus()

    def _dismiss_card(self) -> None:
        self._handled_question_id = self._current_question_id
        self._current_question_id = None
        self._hide_card()
        self.grab_terminal_focus()

    def _start_transcript_resolver(self, cwd: str | None) -> None:
        if not cwd:
            return
        # A brand-new session must attach to a transcript that didn't exist
        # when the tab started — the newest *existing* one belongs to some other
        # session. `--continue` (command_override) reuses the newest existing
        # transcript, which is exactly the session it resumes.
        self._known_transcripts = (
            set(self.provider.transcripts_for_cwd(cwd))
            if self._command_override is None
            else set()
        )
        self._resolver_attempts = 0
        self._resolver_source = GLib.timeout_add(1500, lambda: self._resolve_transcript(cwd))

    def _resolve_transcript(self, cwd: str) -> bool:
        if self.get_root() is None:
            self._resolver_source = None
            return GLib.SOURCE_REMOVE
        self._resolver_attempts += 1
        cands = [
            p for p in self.provider.transcripts_for_cwd(cwd) if p not in self._known_transcripts
        ]
        try:
            path = max(cands, key=lambda p: p.stat().st_mtime, default=None)
        except OSError:
            path = None
        if path is not None:
            self.set_transcript_path(str(path))
            if self.session_id is None:
                self.session_id = self.provider.session_id_for_transcript(path)
                self.emit("session-resolved", self.session_id)
            self._resolver_source = None
            return GLib.SOURCE_REMOVE
        if self._resolver_attempts > 120:  # ~3 min, give up
            self._resolver_source = None
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- secondary terminal panel ------------------------------------------

    @property
    def panel_visible(self) -> bool:
        return self._panel.get_visible()

    def toggle_panel(self, default_mode: str | None = None) -> None:
        if self.panel_visible:
            self.hide_panel()
        else:
            self.show_panel(default_mode)

    def show_panel(self, default_mode: str | None = None) -> None:
        """Show the panel, starting (or re-pointing) its shell at the agent's
        current working directory. `default_mode` ("bottom" | "right") opens
        the panel in the app-wide last-used mode; None keeps the tab's own."""
        if not self.panel_visible and default_mode in ("bottom", "right"):
            self._set_panel_mode(default_mode)
        self._panel.open_shell(self.current_agent_cwd())
        if not self.panel_visible:
            self._panel.set_visible(True)
            self._apply_panel_position()
        GLib.idle_add(self._panel.grab_terminal_focus)

    def hide_panel(self) -> None:
        if not self.panel_visible:
            return
        self._remember_panel_position()
        refocus = self._panel.terminal.has_focus()
        self._panel.set_visible(False)
        if refocus:
            self.grab_terminal_focus()

    def panel_has_running_command(self) -> bool:
        """True when a command is running in the panel shell — even a hidden
        panel's job is protected by the close confirmation."""
        return self._panel.has_running_command()

    def swap_panel(self) -> str:
        """Move the panel bottom↔right (the shell keeps running) and return
        the new position: "bottom" or "right"."""
        self._remember_panel_position()  # capture the outgoing mode's divider
        to_bottom = self._paned.get_orientation() == Gtk.Orientation.HORIZONTAL
        self._paned.set_orientation(
            Gtk.Orientation.VERTICAL if to_bottom else Gtk.Orientation.HORIZONTAL
        )
        if self.panel_visible:
            self._apply_panel_position()
        return "bottom" if to_bottom else "right"

    def _set_panel_mode(self, mode: str) -> None:
        """Reorient a hidden panel; there's no divider on screen to capture."""
        if mode != self._panel_mode():
            self._paned.set_orientation(
                Gtk.Orientation.VERTICAL if mode == "bottom" else Gtk.Orientation.HORIZONTAL
            )

    def _panel_mode(self) -> str:
        vertical = self._paned.get_orientation() == Gtk.Orientation.VERTICAL
        return "bottom" if vertical else "right"

    def _paned_total(self) -> int:
        vertical = self._paned.get_orientation() == Gtk.Orientation.VERTICAL
        return self._paned.get_height() if vertical else self._paned.get_width()

    def _remember_panel_position(self) -> None:
        """Record the divider's spot for the current mode. Skipped while an
        apply is still queued — the position it would read is the previous
        mode's, and saving it would corrupt this mode's remembered value."""
        if not self.panel_visible or self._panel_position_pending:
            return
        if self._paned_total() <= 0:
            return
        mode = self._panel_mode()
        position = self._paned.get_position()
        if position > 0 and self._panel_positions.get(mode) != position:
            self._panel_positions[mode] = position
            self._schedule_layout_emit()

    def _schedule_layout_emit(self) -> None:
        if self._layout_emit_source is not None:
            GLib.source_remove(self._layout_emit_source)
        self._layout_emit_source = GLib.timeout_add(500, self._emit_layout_changed)

    def _emit_layout_changed(self) -> bool:
        self._layout_emit_source = None
        self.emit("panel-layout-changed")
        return GLib.SOURCE_REMOVE

    @property
    def panel_positions(self) -> dict[str, int]:
        """Copy of the per-mode divider positions, for persisting."""
        return dict(self._panel_positions)

    def set_panel_positions(self, positions: dict) -> None:
        """Seed remembered divider positions (e.g. persisted from a previous
        run). Positions already recorded in this tab win."""
        for mode in ("bottom", "right"):
            value = positions.get(mode)
            if isinstance(value, int) and value > 0 and mode not in self._panel_positions:
                self._panel_positions[mode] = value

    def _apply_panel_position(self) -> None:
        """Restore this mode's remembered divider — or, first time, give the
        panel roughly a third of the paned — once sizes are known."""
        self._panel_position_pending = True

        def position() -> bool:
            self._panel_position_pending = False
            total = self._paned_total()
            if total > 0:
                saved = self._panel_positions.get(self._panel_mode())
                self._paned.set_position(saved if saved is not None else int(total * 0.62))
            return GLib.SOURCE_REMOVE

        GLib.idle_add(position)

    def current_agent_cwd(self) -> str | None:
        """Best-effort cwd of what's running in the agent terminal: the
        foreground process if any (the agent may have cd'd into a worktree),
        else the shell, else the directory the tab started in."""
        pids = []
        pty = self.terminal.get_pty()
        if pty is not None:
            try:
                pids.append(os.tcgetpgrp(pty.get_fd()))
            except OSError:
                pass
        if self._child_pid is not None:
            pids.append(self._child_pid)
        for pid in pids:
            cwd = _process_cwd(pid)
            if cwd is not None:
                return cwd
        return self._cwd

    # -- helpers -----------------------------------------------------------

    def has_running_command(self) -> bool:
        """True when something other than the shell (e.g. claude) owns the
        terminal's foreground."""
        return _has_running_command(self.terminal, self._child_pid)

    def apply_settings(self, settings: dict) -> None:
        font = settings.get("font") or ""
        self.terminal.set_font(Pango.FontDescription.from_string(font) if font else None)
        try:
            self.terminal.set_scrollback_lines(int(settings.get("scrollback") or 10_000))
        except (TypeError, ValueError):
            pass
        themes.apply_terminal_theme(self.terminal, settings.get("terminal_theme"))
        self._easy_copy_paste = bool(settings.get("easy_copy_paste"))
        self._panel.apply_settings(settings)

    def feed_message(self, text: str) -> None:
        self.terminal.feed(f"\r\n\x1b[1;33m[session manager]\x1b[0m {text}\r\n".encode())

    def grab_terminal_focus(self) -> None:
        self.terminal.grab_focus()

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        # Shift+Enter → newline. Terminals send the same byte for Enter and
        # Shift+Enter, so we emit Meta+Enter (ESC + CR), which Claude Code
        # interprets as "insert a line break" rather than "submit".
        if shift and not ctrl and keyval in (
            Gdk.KEY_Return,
            Gdk.KEY_KP_Enter,
            Gdk.KEY_ISO_Enter,
        ):
            self.terminal.feed_child(b"\x1b\r")
            return True

        # Easy copy & paste (Black Box-style): Ctrl+C copies when text is
        # selected — otherwise it falls through as SIGINT — and Ctrl+V pastes.
        if self._easy_copy_paste and ctrl and not shift:
            if keyval == Gdk.KEY_c and self.terminal.get_has_selection():
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keyval == Gdk.KEY_v:
                self.terminal.paste_clipboard()
                return True

        if ctrl and shift:
            if keyval == Gdk.KEY_C:
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keyval == Gdk.KEY_V:
                self.terminal.paste_clipboard()
                return True
            if keyval == Gdk.KEY_G:
                self.toggle_search()
                return True
        return False
