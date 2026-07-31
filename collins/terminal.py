# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-31. Full change history: git log for this file.

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

from . import apppicker, footerapps, panelhistory, themes  # noqa: E402
from .copylabel import (  # noqa: E402
    copy_tooltip,
    enable_copy_on_click,
    enable_open_on_click,
    open_tooltip,
    open_uri,
)
from .formatting import display_path  # noqa: E402
from .gitinfo import current_branch  # noqa: E402
from .i18n import _  # noqa: E402
from .linkpatterns import URL_PATTERN  # noqa: E402
from .promptcard import build_question_card  # noqa: E402
from .providers import Provider, get_provider  # noqa: E402
from .prstatus import (  # noqa: E402
    CHECKS_FAILED,
    CHECKS_PASSED,
    CHECKS_PENDING,
    PullRequest,
    describe,
    discover_pr,
    enrich,
    forget_status,
    from_records,
    invalidate,
    menu_name,
    merge_ordered,
    to_records,
)
from .transcript import TranscriptModel  # noqa: E402

_TRANSCRIPT_DEBOUNCE_MS = 400
_PROMPT_POLL_MS = 1000  # backstop poll for detecting the agent's prompts
_CWD_POLL_MS = 2000  # footer refresh; only ticks while the tab is visible
# The merge mark sits with caption-sized text, so it takes the same 12px as the
# glyphs it replaces rather than a symbolic icon's stock 16.
_PR_MERGED_ICON_PX = 12
_PR_REFRESH_ICON_PX = 12  # the refresh button sits with them, not above them
# A session links every PR that passes through its tool output, including ones
# it only read, so the row is bounded: it tracks (and saves, and refreshes) the
# newest this many, and a session that busy has stopped caring about its first.
# How many of them are on screen is a question of width, not of this (see
# PrChipRow).
_MAX_PR_CHIPS = 20
_PR_CHIP_SPACING = 8  # between chips; their own parts sit 4 apart
# The caret's menu: the column its status marks share (so the titles beside
# them line up), how wide a title gets before it ellipsizes, and how tall the
# list gets before it scrolls.
_PR_MARK_COLUMN_PX = 14
_PR_MENU_MAX_CHARS = 48
_PR_MENU_MAX_PX = 400
# Each CI mark is colored like its counterpart on the PR page; the shades
# themselves follow the light/dark scheme and live in app.py.
_PR_CHECKS_CSS = {
    CHECKS_PASSED: "pr-checks-passed",
    CHECKS_FAILED: "pr-checks-failed",
    CHECKS_PENDING: "pr-checks-pending",
}

# PCRE2 flags for the find bar: multiline, case-insensitive.
_PCRE2_CASELESS = 0x00000008
_PCRE2_MULTILINE = 0x00000400
_SEARCH_FLAGS = _PCRE2_CASELESS | _PCRE2_MULTILINE

# Font zoom (Ctrl+scroll / Ctrl+plus/minus/0): multiplicative steps on VTE's
# font-scale, clamped to the range Ptyxis and GNOME Terminal allow.
_FONT_SCALE_MIN = 0.25
_FONT_SCALE_MAX = 4.0
_FONT_SCALE_STEP = 1.1
# `plus` is what most layouts produce only with Shift held, so the handlers
# key off Ctrl alone and let Shift ride along.
_ZOOM_IN_KEYS = (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add)
_ZOOM_OUT_KEYS = (Gdk.KEY_minus, Gdk.KEY_underscore, Gdk.KEY_KP_Subtract)
_ZOOM_RESET_KEYS = (Gdk.KEY_0, Gdk.KEY_KP_0)


def _setup_links(terminal: Vte.Terminal) -> None:
    """Give links GNOME Terminal's behaviour: underline on hover, open on
    Ctrl+click.

    Covers both kinds of link a terminal shows: OSC 8 hyperlinks (what agent
    CLIs emit for file references — VTE ignores the escape entirely until
    allow-hyperlink is switched on) and plain URLs in the output, matched by
    regex the way GNOME Terminal matches them.
    """
    terminal.set_allow_hyperlink(True)
    try:
        regex = Vte.Regex.new_for_match(
            URL_PATTERN, len(URL_PATTERN.encode()), _PCRE2_MULTILINE
        )
        terminal.match_set_cursor_name(terminal.match_add_regex(regex, 0), "pointer")
    except GLib.Error:
        regex = None  # VTE built without PCRE2: OSC 8 links still work

    def on_launched(launcher: Gtk.UriLauncher | Gtk.FileLauncher, result) -> None:
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            pass  # no handler for the scheme/type, or the user dismissed the chooser

    def on_pressed(gesture: Gtk.GestureClick, _n_press, x: float, y: float) -> None:
        if not gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return
        uri = terminal.check_hyperlink_at(x, y)
        if uri is None and regex is not None:
            match, _tag = terminal.check_match_at(x, y)
            if match is not None and match.startswith("www."):
                match = "http://" + match
            uri = match
        if not uri:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        if uri.startswith("file:"):
            # Open the file itself in its default app (what xdg-open does).
            # UriLauncher would hand a file: URI to the portal, which only
            # reveals it in the file manager. Gio.File also sheds any line
            # fragment the emitter tacked on.
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_uri(uri))
        else:
            launcher = Gtk.UriLauncher.new(uri)
        launcher.launch(terminal.get_root(), None, on_launched)

    # Capture phase, so Ctrl+click opens the link even when the running app
    # has turned on mouse reporting (same trick as the context menu).
    click = Gtk.GestureClick(button=1)
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.connect("pressed", on_pressed)
    terminal.add_controller(click)


def _setup_smooth_scroll(terminal: Vte.Terminal) -> None:
    """Scroll by pixels instead of whole lines (as Ptyxis does), so touchpad
    flicks glide like the rest of the desktop instead of stepping."""
    terminal.set_enable_fallback_scrolling(False)
    terminal.set_scroll_unit_is_pixels(True)


def _zoom_by(terminal: Vte.Terminal, factor: float | None) -> None:
    """Multiply the terminal's font scale, clamped; None resets to 1.0."""
    scale = 1.0
    if factor is not None:
        scale = max(_FONT_SCALE_MIN, min(_FONT_SCALE_MAX, terminal.get_font_scale() * factor))
    terminal.set_font_scale(scale)


def _handle_zoom_key(terminal: Vte.Terminal, keyval: int, ctrl: bool) -> bool:
    """Ctrl+plus / Ctrl+minus / Ctrl+0, the zoom keys every GNOME terminal
    claims from the shell."""
    if not ctrl:
        return False
    if keyval in _ZOOM_IN_KEYS:
        _zoom_by(terminal, _FONT_SCALE_STEP)
        return True
    if keyval in _ZOOM_OUT_KEYS:
        _zoom_by(terminal, 1 / _FONT_SCALE_STEP)
        return True
    if keyval in _ZOOM_RESET_KEYS:
        _zoom_by(terminal, None)
        return True
    return False


def _setup_scroll_zoom(terminal: Vte.Terminal) -> None:
    """Ctrl+scroll zooms the font, like Ptyxis's enable-zoom-scroll-ctrl.

    The step is raised to the delta, so a wheel notch (|dy| = 1) is one full
    step while a touchpad's stream of small deltas zooms smoothly instead of
    compounding a full step per event.
    """

    def on_scroll(controller: Gtk.EventControllerScroll, _dx: float, dy: float) -> bool:
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        _zoom_by(terminal, _FONT_SCALE_STEP ** -dy)
        return True

    # Capture phase: the zoom must win over VTE's own scrolling (and mouse
    # reporting) while Ctrl is down.
    scroller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    scroller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    scroller.connect("scroll", on_scroll)
    terminal.add_controller(scroller)


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


class PrChipRow(Gtk.Widget):
    """The footer's PR chips: as many as fit, and the newest ones are the ones.

    A box would insist on its full width and push the footer's buttons off the
    end of the window; this drops whole chips off the *front* of the row when
    it is short of room, so what's left still reads oldest-to-newest and still
    ends with the PR the session is working on now. A dropped chip comes back
    the moment the window is wide enough for it again.

    It only ever holds a handful of small labels, so measuring them on every
    allocation is cheaper than caching would be.
    """

    def __init__(self, spacing: int) -> None:
        super().__init__()
        self._spacing = spacing

    def set_chips(self, chips: list[Gtk.Widget]) -> None:
        """Replace the row's chips, oldest first."""
        while (child := self.get_first_child()) is not None:
            child.unparent()
        for chip in chips:
            chip.set_parent(self)

    def _chips(self) -> list[Gtk.Widget]:
        chips, child = [], self.get_first_child()
        while child is not None:
            chips.append(child)
            child = child.get_next_sibling()
        return chips

    @staticmethod
    def _natural(chip: Gtk.Widget, orientation: Gtk.Orientation) -> int:
        return chip.measure(orientation, -1)[1]

    def do_measure(self, orientation, _for_size):
        chips = self._chips()
        sizes = [self._natural(chip, orientation) for chip in chips]
        if orientation != Gtk.Orientation.HORIZONTAL:
            return max(sizes, default=0), max(sizes, default=0), -1, -1
        # The newest chip alone is the least this row is worth keeping; every
        # chip, spaced, is what it would like. Anything between is a row with
        # its oldest chips dropped.
        return (
            sizes[-1] if sizes else 0,
            sum(sizes) + self._spacing * max(len(sizes) - 1, 0),
            -1,
            -1,
        )

    def do_size_allocate(self, width, height, baseline):
        chips = self._chips()
        keep: list[Gtk.Widget] = []
        used = 0
        for chip in reversed(chips):  # newest first: it is the one that stays
            needed = self._natural(chip, Gtk.Orientation.HORIZONTAL)
            if keep:
                needed += self._spacing
            if keep and used + needed > width:
                break
            keep.append(chip)
            used += needed
        x = 0
        for chip in chips:
            chip.set_child_visible(chip in keep)
            if chip not in keep:
                continue
            chip_width = self._natural(chip, Gtk.Orientation.HORIZONTAL)
            rect = Gdk.Rectangle()
            rect.x, rect.y, rect.width, rect.height = x, 0, chip_width, height
            chip.size_allocate(rect, baseline)
            x += chip_width + self._spacing

    def do_dispose(self) -> None:
        while (child := self.get_first_child()) is not None:
            child.unparent()
        Gtk.Widget.do_dispose(self)


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
        self._ever_spawned = False  # panel was used at some point in this tab's life
        self._easy_copy_paste = False

        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(10_000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        self.terminal.connect("child-exited", self._on_child_exited)
        _setup_links(self.terminal)
        _setup_smooth_scroll(self.terminal)
        _setup_scroll_zoom(self.terminal)

        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.append(scrolled)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.terminal.add_controller(keys)

    @property
    def ever_spawned(self) -> bool:
        return self._ever_spawned

    def open_shell(self, cwd: str | None, restore_text: str | None = None) -> None:
        """Spawn the shell on first show; on later shows follow the agent's
        cwd (it may have moved into a worktree) if the shell sits idle.
        `restore_text` (first spawn only) is replayed into the scrollback
        before the shell starts — the previous tab's saved panel history."""
        if not self._spawned:
            self._spawn(cwd, restore_text)
        else:
            self._sync_cwd(cwd)

    def _spawn(self, cwd: str | None, restore_text: str | None = None) -> None:
        self._spawned = True
        self._ever_spawned = True
        if restore_text:
            self.terminal.feed(restore_text.replace("\n", "\r\n").encode())
            marker = _("── restored panel history ──")
            self.terminal.feed(f"\r\n\x1b[2m{marker}\x1b[0m\r\n".encode())
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

    def clear(self) -> None:
        """Wipe the screen and scrollback; a running shell keeps running and
        is nudged to repaint its prompt (\\x0c = Ctrl+L)."""
        self.terminal.reset(False, True)
        if self._spawned:
            self.terminal.feed_child(b"\x0c")

    def capture_contents(self) -> str:
        """The panel's current text contents including scrollback (plain text
        — VTE's dump carries no colors or attributes)."""
        stream = Gio.MemoryOutputStream.new_resizable()
        try:
            self.terminal.write_contents_sync(stream, Vte.WriteFlags.DEFAULT, None)
            stream.close(None)
        except GLib.Error:
            return ""
        data = stream.steal_as_bytes().get_data()
        return (data or b"").decode("utf-8", errors="replace")

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
        if _handle_zoom_key(self.terminal, keyval, ctrl):
            return True
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
        # Emitted (debounced) when the panel divider is moved: (mode, size)
        # where mode is "bottom" | "right" and size the new panel px size,
        # so the window can persist it as the app-wide default.
        "panel-size-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
        # Emitted when either of the tab's terminals rings BEL, for the
        # window's visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when the PRs on the footer row change (object = their
        # prstatus records, oldest first), so the window can save them against
        # the session. Never fires for a tab that has nothing to say yet.
        "prs-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
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
        self._resolver_cwd: str | None = None  # set iff this tab resolves its own transcript
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
        _setup_links(self.terminal)
        _setup_smooth_scroll(self.terminal)
        _setup_scroll_zoom(self.terminal)
        self.terminal.connect("bell", lambda *_: self.emit("bell"))

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
        self._panel.terminal.connect("bell", lambda *_: self.emit("bell"))
        panel_right = bool(settings) and settings.get("panel_position") == "right"
        self._paned = Gtk.Paned(
            orientation=Gtk.Orientation.HORIZONTAL if panel_right else Gtk.Orientation.VERTICAL,
            vexpand=True,
        )
        # The hairline divider is hard to grab; use the wide handle throughout.
        self._paned.set_wide_handle(True)
        self._panel_sizes: dict[str, int] = {}  # this tab's panel px size per mode
        self._panel_apply_pending = False  # a programmatic divider set is queued
        self._panel_size_lookup = None  # mode -> app-wide last-set size (set by the window)
        self._size_emit_source: int | None = None  # debounce for panel-size-changed
        self._size_emit_mode: str | None = None  # mode whose size changed last
        # Dragging the divider records the new panel size for the current mode.
        self._paned.connect("notify::position", lambda *_: self._remember_panel_size())
        self._paned.set_start_child(self._overlay)
        self._paned.set_end_child(self._panel)
        self._paned.set_resize_start_child(True)
        self._paned.set_shrink_start_child(False)
        self._paned.set_resize_end_child(False)
        self._paned.set_shrink_end_child(False)
        self.append(self._paned)

        self._footer_cwd: str | None = None  # last value shown in the footer
        self._footer_branch: str | None = None
        # Every PR this session has opened, oldest first: url -> PR without its
        # CI status (see _collect_prs). Replaced wholesale, never mutated in
        # place — the update thread reads it while the main loop writes it.
        self._tracked_prs: dict[str, PullRequest] = {}
        self._restored_prs: list[PullRequest] = []  # this session's, from a previous run
        self._footer_prs: list[PullRequest] = []  # what the chips currently show
        self._saved_pr_records: list[dict] = []  # last records handed to the window
        self._pr_discover = False  # a click's search, waiting for a free tick
        self._cwd_refresh_source: int | None = None
        self.append(self._build_footer())

        self._transcript = TranscriptModel(jsonl_path)

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

    # -- footer ------------------------------------------------------------

    def _build_footer(self) -> Gtk.Widget:
        """Slim status row under the terminal: the tab's live working
        directory and git branch, plus the buttons controlling the terminal
        panel."""
        self._cwd_label = Gtk.Label(xalign=0.0)
        self._cwd_label.set_ellipsize(Pango.EllipsizeMode.START)
        self._cwd_label.add_css_class("caption")
        self._cwd_label.add_css_class("dim-label")
        enable_copy_on_click(self._cwd_label, lambda: self._footer_cwd)

        # dividers flanking the branch label; the 8px box spacing on each
        # side of them matches the footer's own 8px edge padding
        self._branch_seps = tuple(
            Gtk.Separator(orientation=Gtk.Orientation.VERTICAL) for _ in range(2)
        )
        for sep in self._branch_seps:
            sep.set_visible(False)

        self._branch_label = Gtk.Label()
        self._branch_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._branch_label.set_max_width_chars(24)
        self._branch_label.add_css_class("caption")
        self._branch_label.add_css_class("dim-label")
        self._branch_label.set_visible(False)
        enable_copy_on_click(self._branch_label, lambda: self._footer_branch, lambda b: f"⎇ {b}")

        # The PR chips trail the branch, sharing its leading divider — one per
        # PR the session has opened, oldest first, so the row reads in the
        # order the work happened. Unlike their neighbours they open rather
        # than copy: a number on screen is a stand-in for its PR page, and
        # going there is what you want next.
        self._pr_chips = PrChipRow(_PR_CHIP_SPACING)
        self._pr_chips.set_visible(False)
        # Leading the row, where the oldest chip would be: the caret opens the
        # full list, titles and all — including the chips that didn't fit,
        # since it takes its width from the row and so costs it one.
        self._pr_menu = Gtk.Popover()
        self._pr_menu.set_position(Gtk.PositionType.TOP)  # the footer is at the bottom
        self._pr_menu.add_css_class("menu")
        # Its own class as well: the list inside is buttons, and the footer's
        # rules for those (tight, no hover background) would otherwise reach
        # into it — a popover is a child of the widget it is attached to.
        self._pr_menu.add_css_class("pr-menu")
        menu_icon = Gtk.Image.new_from_icon_name("pan-up-symbolic")
        menu_icon.set_pixel_size(_PR_REFRESH_ICON_PX)
        menu_icon.add_css_class("dim-label")
        self._pr_menu_btn = Gtk.MenuButton(child=menu_icon, popover=self._pr_menu)
        self._pr_menu_btn.add_css_class("flat")
        self._pr_menu_btn.set_tooltip_text(_("Every pull request this session has opened"))
        self._pr_menu_btn.set_create_popup_func(self._fill_pr_menu)
        self._pr_menu_btn.set_visible(False)
        # Sibling of the chips, never inside them: a chip opens its PR on click,
        # and a button in there would open the browser along with itself.
        # It shows whether or not a PR does — with none, it is the way to go
        # looking for one (see _on_pr_refresh).
        refresh_icon = Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        refresh_icon.set_pixel_size(_PR_REFRESH_ICON_PX)
        refresh_icon.add_css_class("dim-label")
        self._pr_refresh_btn = Gtk.Button(child=refresh_icon)
        self._pr_refresh_btn.add_css_class("flat")
        self._pr_refresh_btn.connect("clicked", self._on_pr_refresh)
        self._sync_pr_refresh_tooltip()
        self._pr_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)

        # Only the selected tab is visible (and thus clickable), so routing
        # through the window's actions still targets the right tab.
        toggle_btn = Gtk.Button(icon_name="utilities-terminal-symbolic")
        toggle_btn.set_tooltip_text(_("Show/hide terminal panel (Ctrl+J)"))
        toggle_btn.set_action_name("win.toggle-panel")
        self._swap_panel_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        self._swap_panel_btn.set_tooltip_text(_("Move terminal panel bottom/right"))
        self._swap_panel_btn.set_action_name("win.swap-panel")
        self._swap_panel_btn.set_visible(False)  # only shown while a panel is open

        # cwd, branch and PRs sit together on the left; the wrapper box (not the
        # cwd label) takes the slack so the buttons stay pinned right even
        # while the branch and PR labels are hidden.
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, hexpand=True)
        left.append(self._cwd_label)
        left.append(self._branch_seps[0])
        left.append(self._branch_label)
        left.append(self._branch_seps[1])
        left.append(self._pr_menu_btn)
        left.append(self._pr_chips)
        left.append(self._pr_refresh_btn)
        left.append(self._pr_sep)

        # User-configured app launchers sit just left of the panel buttons;
        # populated from settings via _set_footer_apps.
        self._footer_apps_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        footer.add_css_class("tab-footer")
        footer.append(left)
        footer.append(self._footer_apps_box)
        for btn in (toggle_btn, self._swap_panel_btn):
            btn.add_css_class("flat")
            footer.append(btn)
        # Poll only while on screen; refresh immediately on every tab switch.
        self.connect("map", lambda *_: self._start_cwd_refresh())
        return footer

    def _start_cwd_refresh(self) -> None:
        self._refresh_cwd_label()
        if self._cwd_refresh_source is None:
            self._cwd_refresh_source = GLib.timeout_add(_CWD_POLL_MS, self._cwd_tick)

    def _cwd_tick(self) -> bool:
        if not self.get_mapped():  # hidden/closed tab → resume on next map
            self._cwd_refresh_source = None
            return GLib.SOURCE_REMOVE
        self._refresh_cwd_label()
        return GLib.SOURCE_CONTINUE

    def _refresh_cwd_label(self) -> None:
        cwd = self.current_agent_cwd()
        if cwd != self._footer_cwd:
            self._footer_cwd = cwd
            self._cwd_label.set_text(display_path(cwd) if cwd else "")
            self._cwd_label.set_tooltip_text(copy_tooltip(cwd) if cwd else None)
        branch = current_branch(cwd)  # rechecked every tick: checkouts don't change the cwd
        if branch != self._footer_branch:
            self._footer_branch = branch
            self._branch_label.set_text(f"⎇ {branch}" if branch else "")
            self._branch_label.set_tooltip_text(copy_tooltip(branch) if branch else None)
            self._branch_label.set_visible(branch is not None)
            # The chips are the session's history, so a checkout doesn't retire
            # any of them; it only makes the button worth pressing again.
            self._sync_pr_refresh_tooltip()
        self._sync_footer_seps()

    def _sync_footer_seps(self) -> None:
        """Show only the dividers that separate two visible chips.

        The PR group ends in a button that is always there, so its own dividers
        never come and go; the branch is the one chip that does, and the
        divider ahead of it does double duty when it's absent — the row never
        opens or closes with a stray divider.
        """
        branch = self._footer_branch is not None
        cwd = self._footer_cwd is not None
        self._branch_seps[0].set_visible(cwd)
        self._branch_seps[1].set_visible(branch)

    def _build_pr_chip(self, pr: PullRequest) -> Gtk.Widget:
        """One PR's chip: its number, its CI mark, and a merge mark if it landed.

        Every part of a chip opens that PR and nothing else — the chips are
        siblings on the row, so each number is its own link.
        """
        number = Gtk.Label(label=f"#{pr.number}")
        number.add_css_class("caption")
        number.add_css_class("dim-label")
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chip.append(number)
        # The CI mark is its own label so it can carry its own color: green,
        # red or yellow (undimmed, like the merge mark), which is what the eye
        # picks up without reading the row.
        glyph = pr.glyph
        if glyph is not None:
            checks = Gtk.Label(label=glyph)
            checks.set_css_classes(["caption", _PR_CHECKS_CSS.get(glyph, "dim-label")])
            chip.append(checks)
        # A merged PR trades its CI glyph for GitHub's git-merge mark, purple
        # and undimmed: the one PR state worth spotting from across the row.
        if pr.merged:
            merged = Gtk.Image.new_from_icon_name("git-merge-symbolic")
            merged.set_pixel_size(_PR_MERGED_ICON_PX)
            merged.add_css_class("pr-merged")
            chip.append(merged)
        chip.set_tooltip_text(open_tooltip(describe(pr) + "\n" + pr.url))
        enable_open_on_click(chip, lambda: pr.url)
        return chip

    def _pr_status_mark(self, pr: PullRequest) -> Gtk.Widget:
        """A PR's status as one widget: its CI glyph, or the merge mark.

        Always returns something, so titles line up down the menu even beside
        a PR whose status hasn't been fetched yet.
        """
        if pr.merged:
            mark: Gtk.Widget = Gtk.Image.new_from_icon_name("git-merge-symbolic")
            mark.set_pixel_size(_PR_MERGED_ICON_PX)
            mark.add_css_class("pr-merged")
        else:
            glyph = pr.glyph
            mark = Gtk.Label(label=glyph or "")
            mark.set_css_classes(["caption", _PR_CHECKS_CSS.get(glyph or "", "dim-label")])
        mark.set_size_request(_PR_MARK_COLUMN_PX, -1)
        return mark

    def _fill_pr_menu(self, _button: Gtk.MenuButton) -> None:
        """Build the caret's list, just before it opens.

        Every PR the session has picked up, oldest first like the row, with the
        titles the chips have no room for. Built per opening rather than kept
        in sync: statuses move under it, and it is only ever on screen for as
        long as someone is reading it.
        """
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for pr in self._footer_prs:
            name = Gtk.Label(label=menu_name(pr), xalign=0.0, hexpand=True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(_PR_MENU_MAX_CHARS)
            # Its own label, so a long title ellipsizes without taking the one
            # thing that identifies the PR with it.
            number = Gtk.Label(label=f"(#{pr.number})")
            number.add_css_class("dim-label")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.append(self._pr_status_mark(pr))
            row.append(name)
            row.append(number)
            button = Gtk.Button(child=row)
            button.add_css_class("flat")
            button.add_css_class("pr-menu-row")  # menu-sized, and lit under the pointer
            button.set_tooltip_text(open_tooltip(describe(pr) + "\n" + pr.url))
            button.connect("clicked", self._on_pr_menu_row, pr.url)
            rows.append(button)
        # A session with a lot of PRs would otherwise open a popover taller
        # than the window it is in.
        scroller = Gtk.ScrolledWindow(child=rows)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_propagate_natural_width(True)
        scroller.set_propagate_natural_height(True)
        scroller.set_max_content_height(_PR_MENU_MAX_PX)
        self._pr_menu.set_child(scroller)

    def _on_pr_menu_row(self, button: Gtk.Button, url: str) -> None:
        self._pr_menu.popdown()
        open_uri(button, url)

    def _refresh_pr_chips(self, prs: list[PullRequest]) -> None:
        """Show this session's PRs, oldest first, with the CI state each has.

        The whole row is rebuilt rather than patched: a chip's parts depend on
        its state (a glyph appears, a merge mark replaces it), and the equality
        guard keeps the once-a-second poll from rebuilding anything unchanged.
        """
        if prs == self._footer_prs:
            return
        self._footer_prs = list(prs)
        self._pr_chips.set_chips([self._build_pr_chip(pr) for pr in prs])
        self._pr_chips.set_visible(bool(prs))
        self._pr_menu_btn.set_visible(bool(prs))
        self._remember_prs(prs)
        self._sync_pr_refresh_tooltip()
        self._sync_footer_seps()

    def _remember_prs(self, prs: list[PullRequest]) -> None:
        """Hand the row's PRs to the window, which saves them for this session.

        Their CI status doesn't go with them (see prstatus.to_record), so what
        changes here is the list itself — a new PR, or one that just merged —
        and a session that hasn't opened any never emits at all.
        """
        records = to_records(prs)
        if records == self._saved_pr_records:
            return
        self._saved_pr_records = records
        self.emit("prs-changed", records)

    def restore_prs(self, records: object) -> None:
        """Re-adopt the PRs saved for this session by a previous run.

        The window calls this once the tab's session is known. The transcript's
        own pr-links come back on the next poll anyway, but a PR the refresh
        button found by branch is written down nowhere else, and a PR that was
        already merged shows its mark before any `gh` call goes out.
        """
        restored = from_records(records)
        if not restored:
            return
        self._restored_prs = restored
        self._merge_restored()
        self._request_update()

    def _merge_restored(self) -> None:
        """Put this session's restored PRs back at the head of the tracked list.

        Replayed after every update lands, not just once: an update that was
        already in flight when the window restored (opening a tab starts one
        immediately) would otherwise finish and overwrite the restore with the
        list it had snapshotted before it. The saved order decides where a PR
        the transcript never mentions belongs; the live copy of one it does
        mention wins on everything except its place in the row.
        """
        if not self._restored_prs:
            return
        live = list(self._tracked_prs.values())
        merged = {pr.url: pr for pr in merge_ordered(self._restored_prs, live)}
        merged.update({pr.url: pr for pr in live})  # positions keep, values don't
        self._tracked_prs = merged

    def _sync_pr_refresh_tooltip(self, not_found: bool = False) -> None:
        """What the button offers to do, which depends on what the row shows.

        A search that came back empty says so until something changes, so a
        click that found nothing isn't indistinguishable from one that did.
        """
        if not_found:
            text = _("No pull request found for this branch")
        elif self._footer_prs:
            text = _("Re-check this branch's pull requests")
        else:
            text = _("Look for this branch's pull request")
        self._pr_refresh_btn.set_tooltip_text(text)

    def _on_pr_refresh(self, _button: Gtk.Button) -> None:
        """Ask the branch which PR it has, and refresh every chip's status.

        Always the branch, whatever the row currently shows: the transcript is
        the *automatic* path to a PR, and a manual refresh that only ever
        re-read it could never notice a PR opened by hand.

        The work lands on the update thread, so the click itself only asks; the
        button goes insensitive until the answer arrives.
        """
        self._pr_refresh_btn.set_sensitive(False)
        self._request_update(discover=True)

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
        # Another session's PRs; re-read from the new transcript below, and
        # restored again by the window once this tab's session is known.
        self._tracked_prs = {}
        self._restored_prs = []
        self._refresh_pr_chips([])
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

    def _request_update(self, discover: bool = False) -> None:
        """Parse newly-appended transcript bytes off the main thread (big
        tool-result lines would otherwise freeze the UI), then check on idle.

        `discover` asks the branch which PR it has. It is only ever set by the
        footer's refresh button; a request that arrives while one is running is
        carried to the next poll rather than dropped, so the click always gets
        its lookup.
        """
        if self._updating:
            self._pr_discover = self._pr_discover or discover
            return
        self._updating = True
        looking = discover or self._pr_discover
        self._pr_discover = False

        def work() -> None:
            try:
                self._transcript.update()
            except Exception:
                pass
            found = self._look_up_branch_pr() if looking else None
            try:
                tracked = self._collect_prs(found)
                # reads the gh status cache, so it belongs on this thread too;
                # a session with no linked PR touches no files at all
                prs = [self._enriched(pr) for pr in tracked[-_MAX_PR_CHIPS:]]
            except Exception:
                tracked, prs = None, self._footer_prs  # leave the chips as they are
            GLib.idle_add(self._apply_update, prs, looking and found is None, tracked)

        threading.Thread(target=work, daemon=True).start()

    def _collect_prs(self, found: PullRequest | None) -> list[PullRequest]:
        """Every PR this tab knows about, oldest first. On the update thread.

        Three sources, in the order a PR can first be known from them: the list
        restored from a previous run, the transcript's pr-links, and whatever
        the refresh button just found on the branch. A URL is only ever added —
        a PR the session opened stays on the row once the branch has moved on,
        which is the whole point of showing all of them.

        Uncapped, and it must stay that way even though the row isn't: cap the
        list here and the PRs trimmed off the front would come back from the
        transcript on the next poll — as the *newest* entries — and the row
        would spin.
        """
        try:
            links = self._transcript.pull_requests()
        except Exception:
            links = []
        collected = merge_ordered(self._tracked_prs.values(), links)
        if found is not None and all(pr.url != found.url for pr in collected):
            collected.append(found)  # a PR nothing else knows about: it is the newest
        return collected

    def _enriched(self, pr: PullRequest) -> PullRequest:
        """*pr* with its title and CI status, fetching them when due.

        A merged PR that already has a title is left alone: it has no checks
        left to run and shows no glyph anyway, so an old chip on a long-lived
        session never costs another `gh` call. One with no title still asks
        once — the caret's menu has a line to fill, and a list saved before
        Collins knew about titles has nothing in it.
        """
        return pr if pr.merged and pr.title else (enrich(pr) or pr)

    def _look_up_branch_pr(self) -> PullRequest | None:
        """The refresh button's own path to a PR: whatever branch is checked out
        right now, then gh. Runs on the update thread.

        cwd and branch are re-read here rather than taken from the footer's 2s
        poll, so a click straight after a checkout asks about the branch the user
        is actually on instead of the one the last tick happened to see.

        Every chip already on the row is marked due first, so one click
        refreshes the lot — status is the other half of what the button is for,
        and a branch that turns up nothing still leaves the row up to date.
        """
        for pr in self._footer_prs:
            if not pr.merged:
                invalidate(pr.url)
        cwd = self.current_agent_cwd()
        try:
            return discover_pr(cwd, current_branch(cwd))
        except Exception:
            return None

    def _apply_update(
        self,
        prs: list[PullRequest] | None = None,
        lookup_empty: bool = False,
        tracked: list[PullRequest] | None = None,
    ) -> bool:
        """Land an update's results on the main loop.

        *prs* is what the row shows (the newest _MAX_PR_CHIPS, with status);
        *tracked* is everything the tab knows about, which is what the next
        collection starts from — None when the update failed and the row is
        being left alone.
        """
        self._updating = False
        self._pr_refresh_btn.set_sensitive(True)
        self._check_prompt()
        if tracked is not None:
            # The shown ones come back with status; a merge is the one part of
            # it worth keeping, and keeping it is what stops that PR from being
            # refetched (and re-saved) for the rest of the session.
            shown = {pr.url: pr for pr in prs or []}
            self._tracked_prs = {
                pr.url: forget_status(shown.get(pr.url, pr)) for pr in tracked
            }
            self._merge_restored()
        self._refresh_pr_chips(prs or [])
        if lookup_empty:  # even with PRs still showing: none of them is this branch's
            self._sync_pr_refresh_tooltip(not_found=True)
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
        self._resolver_cwd = cwd
        # The transcript only appears once the first prompt is sent, which can
        # be arbitrarily long after the tab opens. Poll for as long as the tab
        # is in the foreground; in the background allow ~3 min before pausing,
        # and resume whenever the tab is brought back.
        self.connect("map", lambda *_: self._arm_transcript_resolver())
        self._arm_transcript_resolver()

    def _arm_transcript_resolver(self) -> None:
        if self._resolver_cwd is None or self.session_id is not None:
            return  # never started for this tab, or already resolved
        self._resolver_attempts = 0
        if self._resolver_source is not None:
            return  # already polling; just refresh the background budget
        # A brand-new session must attach to a transcript that appeared while
        # polling: the newest one *existing* at (re)start belongs to some other
        # session — a submitted prompt creates the file well within the ~3 min
        # background budget, so anything from a pause can't be ours either.
        # `--continue` (command_override) reuses the newest existing
        # transcript, which is exactly the session it resumes.
        self._known_transcripts = (
            set(self.provider.transcripts_for_cwd(self._resolver_cwd))
            if self._command_override is None
            else set()
        )
        self._resolver_source = GLib.timeout_add(1500, self._resolve_transcript)

    def _resolve_transcript(self) -> bool:
        if self.get_root() is None:
            self._resolver_source = None
            return GLib.SOURCE_REMOVE
        cands = [
            p
            for p in self.provider.transcripts_for_cwd(self._resolver_cwd)
            if p not in self._known_transcripts
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
        if self.get_mapped():
            self._resolver_attempts = 0  # foreground tab: keep polling indefinitely
        else:
            self._resolver_attempts += 1
            if self._resolver_attempts > 120:  # ~3 min in the background: pause until next map
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

    def show_panel(self, default_mode: str | None = None, focus: bool = True) -> None:
        """Show the panel, starting (or re-pointing) its shell at the agent's
        current working directory. `default_mode` ("bottom" | "right") opens
        the panel in the app-wide last-used mode; None keeps the tab's own.
        `focus=False` leaves keyboard focus where it is (session restore)."""
        if not self.panel_visible and default_mode in ("bottom", "right"):
            self._set_panel_mode(default_mode)
        restore = self._load_panel_history() if not self._panel.ever_spawned else None
        self._panel.open_shell(self.current_agent_cwd(), restore)
        if not self.panel_visible:
            self._panel.set_visible(True)
            self._apply_panel_size()
            self._swap_panel_btn.set_visible(True)
        if focus:
            GLib.idle_add(self._panel.grab_terminal_focus)

    def hide_panel(self) -> None:
        if not self.panel_visible:
            return
        self._remember_panel_size()
        refocus = self._panel.terminal.has_focus()
        self._panel.set_visible(False)
        self._swap_panel_btn.set_visible(False)
        if refocus:
            self.grab_terminal_focus()

    def panel_has_running_command(self) -> bool:
        """True when a command is running in the panel shell — even a hidden
        panel's job is protected by the close confirmation."""
        return self._panel.has_running_command()

    def _load_panel_history(self) -> str | None:
        """Saved panel scrollback for this session — forks don't restore (their
        panel would clash with the original tab's) and never save."""
        if self.fork or not self.session_id:
            return None
        return panelhistory.load(self.session_id)

    def save_panel_history(self) -> None:
        """Persist the panel's scrollback so re-opening this session restores
        it. A panel never opened in this tab leaves prior history untouched."""
        if self.fork or not self.session_id or not self._panel.ever_spawned:
            return
        panelhistory.save(self.session_id, self._panel.capture_contents())

    def clear_panel_history(self) -> None:
        """Wipe the panel's scrollback and its persisted history file. The
        onscreen buffer must go too — the save on tab/window close would
        otherwise re-dump it and resurrect the file. Also clears stale
        history from a previous run when the panel was never opened here."""
        self._panel.clear()
        if not self.fork and self.session_id:
            panelhistory.delete(self.session_id)

    def capture_panel_state(self) -> dict | None:
        """Snapshot the panel's open/mode/sizes for per-session persistence.
        None when the panel was never used in this tab, so a session's saved
        state survives tabs that never touched the panel. Forks never
        persist (mirroring panel history)."""
        if self.fork or (not self._panel.ever_spawned and not self._panel_sizes):
            return None
        self._remember_panel_size()  # capture the live divider position
        state: dict = {"open": self.panel_visible, "mode": self._panel_mode()}
        if self._panel_sizes:
            state["sizes"] = dict(self._panel_sizes)
        return state

    def restore_panel_state(self, state: dict) -> None:
        """Re-apply a session's saved panel snapshot. Mode and sizes land in
        this tab's own memory — restoring a session must not disturb the
        app-wide defaults for new panels — and a panel saved open is shown
        again (spawning its shell and restoring its saved history) without
        stealing focus from the agent terminal."""
        sizes = state.get("sizes")
        if isinstance(sizes, dict):
            for mode in ("bottom", "right"):
                size = sizes.get(mode)
                if isinstance(size, int) and size > 0:
                    self._panel_sizes[mode] = size
        mode = state.get("mode")
        if mode in ("bottom", "right"):
            self._set_panel_mode(mode)
        if state.get("open"):
            self.show_panel(focus=False)

    def swap_panel(self) -> str:
        """Move the panel bottom↔right (the shell keeps running) and return
        the new position: "bottom" or "right"."""
        self._remember_panel_size()  # capture the outgoing mode's panel size
        to_bottom = self._paned.get_orientation() == Gtk.Orientation.HORIZONTAL
        self._paned.set_orientation(
            Gtk.Orientation.VERTICAL if to_bottom else Gtk.Orientation.HORIZONTAL
        )
        if self.panel_visible:
            self._apply_panel_size()
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

    def set_panel_size_lookup(self, lookup) -> None:
        """`lookup(mode) -> px` supplies the app-wide last-set panel size,
        used for modes this tab hasn't sized itself yet."""
        self._panel_size_lookup = lookup

    def _remember_panel_size(self) -> None:
        """Record the panel's size for the current mode. Skipped while an
        apply is still queued — the value it would read is the previous
        mode's, and saving it would corrupt this mode's remembered size."""
        if not self.panel_visible or self._panel_apply_pending:
            return
        total = self._paned_total()
        if total <= 0:
            return
        mode = self._panel_mode()
        size = total - self._paned.get_position()
        if size > 0 and self._panel_sizes.get(mode) != size:
            self._panel_sizes[mode] = size
            if self._size_emit_source is not None:
                GLib.source_remove(self._size_emit_source)
                if self._size_emit_mode != mode:
                    # A different mode's update is still pending (resize, then
                    # swap within the debounce): flush it now rather than drop
                    # it — each mode's default must be preserved independently.
                    self._emit_size_changed()
            self._size_emit_mode = mode
            self._size_emit_source = GLib.timeout_add(500, self._emit_size_changed)

    def _emit_size_changed(self) -> bool:
        self._size_emit_source = None
        mode = self._size_emit_mode
        if mode in self._panel_sizes:
            self.emit("panel-size-changed", mode, self._panel_sizes[mode])
        return GLib.SOURCE_REMOVE

    def _apply_panel_size(self) -> None:
        """Size the panel once the paned's own size is known: this tab's
        remembered size for the mode, else the app-wide last-set size, else
        roughly a third of the paned."""
        self._panel_apply_pending = True

        def position() -> bool:
            self._panel_apply_pending = False
            total = self._paned_total()
            if total <= 0:
                return GLib.SOURCE_REMOVE
            size = self._panel_sizes.get(self._panel_mode()) or 0
            if size <= 0 and self._panel_size_lookup is not None:
                size = self._panel_size_lookup(self._panel_mode()) or 0
            if 0 < size < total:
                self._paned.set_position(total - size)
            else:  # nothing sensible remembered anywhere yet
                self._paned.set_position(int(total * 0.62))
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
        self._set_footer_apps(settings.get("footer_apps") or [])
        self._panel.apply_settings(settings)

    def _set_footer_apps(self, app_ids: list) -> None:
        """(Re)build the footer's app-launcher buttons; uninstalled desktop
        IDs are skipped."""
        while (child := self._footer_apps_box.get_first_child()) is not None:
            self._footer_apps_box.remove(child)
        for _app_id, info in footerapps.resolve_apps(list(app_ids)):
            btn = Gtk.Button(child=apppicker.app_icon_image(info, 16))
            btn.add_css_class("flat")
            btn.set_tooltip_text(_("Open in {name}").format(name=info.get_display_name()))
            btn.connect("clicked", self._on_footer_app_clicked, info)
            self._footer_apps_box.append(btn)

    def _on_footer_app_clicked(self, _btn, info) -> None:
        footerapps.launch_app(info, self.current_agent_cwd())

    def feed_message(self, text: str) -> None:
        self.terminal.feed(f"\r\n\x1b[1;33m[session manager]\x1b[0m {text}\r\n".encode())

    def grab_terminal_focus(self) -> None:
        self.terminal.grab_focus()

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if _handle_zoom_key(self.terminal, keyval, ctrl):
            return True

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
