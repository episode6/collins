# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-02. Full change history: git log for this file.

"""A tab hosting a VTE terminal running the user's shell with an agent CLI inside."""

from __future__ import annotations

import os
import shlex
import threading
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import (  # noqa: E402
    apppicker,
    dialogs,
    dropimages,
    editor,
    editorfiles,
    footerapps,
    panelhistory,
    prmenu,
    proctree,
    themes,
)
from .copylabel import (  # noqa: E402
    copy_tooltip,
    enable_copy_on_click,
    enable_open_on_click,
)
from .formatting import display_path  # noqa: E402
from .gitinfo import current_branch, has_changes  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .linkpatterns import URL_PATTERN  # noqa: E402
from .promptcard import build_question_card  # noqa: E402
from .providers import Provider, get_provider  # noqa: E402
from .prstatus import (  # noqa: E402
    PullRequest,
    describe,
    discover_pr,
    enrich,
    forget_status,
    from_records,
    invalidate,
    merge_ordered,
    to_records,
)
from .transcript import TranscriptModel  # noqa: E402

_TRANSCRIPT_DEBOUNCE_MS = 400
_PROMPT_POLL_MS = 1000  # backstop poll for detecting the agent's prompts
_CWD_POLL_MS = 2000  # footer refresh; only ticks while the tab is visible
# How long an injected prompt is left sitting in the input before the Return
# that sends it (see inject_prompt). Long enough that the CLI has stopped
# reading the text as a paste, short enough that nobody watching sees a pause.
_PROMPT_SUBMIT_MS = 250
_PR_REFRESH_ICON_PX = 12  # the refresh button sits with them, not above them
# A session links every PR that passes through its tool output, including ones
# it only read, so the row is bounded: it tracks (and saves, and refreshes) the
# newest this many, and a session that busy has stopped caring about its first.
# How many of them are on screen is a question of width, not of this (see
# PrChipRow).
_MAX_PR_CHIPS = 20
_PR_CHIP_SPACING = 8  # between chips; their own parts sit 4 apart

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

# Adw.Clamp's maximum-size when "terminal_max_width" is 0 (no limit): larger
# than any real window, so the clamp never actually constrains the terminal.
_UNLIMITED_CLAMP_WIDTH = 1_000_000

# The termprop VTE parses ConEmu-style OSC 9;4 progress sequences into — the
# agent CLI's own busy/idle announcement, which the window turns into the
# sidebar's pole (see activity.ProgressWatch). None on a VTE too old to have
# termprops at all (pre-0.82), which also lacks the termprop-changed signal:
# the wiring is skipped and the inferred sources carry the pole alone.
PROGRESS_HINT_TERMPROP: str | None = getattr(Vte, "TERMPROP_PROGRESS_HINT", None)


def _agent_tab_environment() -> list[str]:
    """The environment an agent tab's shell spawns with: the app's own, plus
    what makes Claude Code announce its progress to a VTE terminal.

    The CLI (verified on 2.1.220 by reading the bundle) only emits its OSC 9;4
    progress sequences for terminals it recognizes — ConEmu env vars, ghostty,
    iTerm2 — and VTE announces itself through none of those, so a stock tab
    gets no progress at all. Worse, it terminates the sequence with BEL for
    every terminal but kitty, and VTE deliberately parses only ST-terminated
    OSC 9;4. Two declarations bridge that:

    - ``ConEmuANSI=ON`` — the announcement ConEmu's own docs define for "this
      terminal speaks ConEmu's OSC extensions", which OSC 9;4 is. The CLI's
      terminal-name detection checks VTE_VERSION first, so it still knows it
      is in a VTE terminal; this flips only the emission gate.
    - ``TERM_PROGRAM=kitty`` — the sole thing the CLI conditions on kitty is
      the OSC terminator (ST, the one VTE accepts). TERM is untouched, so
      terminfo, shell integration, and the tools that probe for real kitty
      (KITTY_WINDOW_ID, TERM=xterm-kitty) all see an ordinary xterm.

    Both are spoofs of terminal *detection*, not of behaviour, and they fail
    soft: a CLI update that stops honoring them just stops emitting progress,
    and the inferred activity sources carry the pole exactly as before.
    See specs/collins/progress-termprop-activity.md for the full findings.
    """
    env = dict(os.environ)
    env.update(ConEmuANSI="ON", TERM_PROGRAM="kitty")
    return [f"{k}={v}" for k, v in env.items()]


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
        if proctree.process_cwd(self._child_pid) == cwd:
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


class PanelTabs(Gtk.Box):
    """The secondary panel's inner tab strip: one PanelTerminal per tab.

    The tab row carries a + button (new shell tab, selected immediately), a
    bottom/right swap button for the whole panel (win.swap-panel), and
    each tab an X; shells survive hide/show and die with their tab. When the
    last tab closes there is nothing left to show, so the owning TerminalTab
    hides the whole panel (see "last-tab-closed")."""

    __gsignals__ = {
        # Emitted when a tab's X (or its shell exiting) removed the last tab.
        "last-tab-closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when any tab's terminal rings BEL, for the window's
        # visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._settings: dict | None = None  # last applied; new tabs start from it
        self._ever_spawned = False
        self._next_number = 1  # "Terminal N" titles; resets when the strip empties
        self._cwd_lookup = None  # () -> agent cwd, set by the owning tab
        self._close_ok: set[Adw.TabPage] = set()  # busy closes the user confirmed
        self._close_asking: set[Adw.TabPage] = set()  # a confirm dialog is up

        self._view = Adw.TabView(vexpand=True)
        self._view.connect("close-page", self._on_close_page)
        # Clicking an inner tab should land the cursor in its shell.
        self._view.connect("notify::selected-page", self._on_selected)

        # autohide off: the bar is also where the + button lives, so it must
        # stay up even with a single tab.
        bar = Adw.TabBar(view=self._view, autohide=False)
        bar.add_css_class("inline")
        bar.set_expand_tabs(False)
        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text(_("New terminal tab"))
        add_btn.connect("clicked", lambda *_: self.new_tab())
        swap_btn = Gtk.Button(icon_name="object-rotate-right-symbolic")
        swap_btn.add_css_class("flat")
        swap_btn.set_tooltip_text(_("Move terminal panel bottom/right"))
        swap_btn.set_action_name("win.swap-panel")
        end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        end_box.append(add_btn)
        end_box.append(swap_btn)
        bar.set_end_action_widget(end_box)

        self.append(bar)
        self.append(self._view)

    @property
    def ever_spawned(self) -> bool:
        """A shell ran in some tab at some point in this session tab's life."""
        return self._ever_spawned

    @property
    def tab_count(self) -> int:
        return self._view.get_n_pages()

    def tabs(self) -> list[PanelTerminal]:
        return [
            self._view.get_nth_page(i).get_child() for i in range(self._view.get_n_pages())
        ]

    def selected_tab(self) -> PanelTerminal | None:
        page = self._view.get_selected_page()
        return page.get_child() if page is not None else None

    def set_cwd_lookup(self, lookup) -> None:
        """`lookup() -> path` supplies the agent's current cwd for the shells
        the + button (and open) start."""
        self._cwd_lookup = lookup

    def _cwd(self) -> str | None:
        return self._cwd_lookup() if self._cwd_lookup is not None else None

    def open(self, restore_texts: list[str] | None = None) -> None:
        """Make sure at least one shell tab exists and points at the agent's
        cwd. `restore_texts` (first open only) recreates one tab per saved
        panel history, oldest first."""
        if self.tab_count == 0:
            for text in restore_texts or [None]:
                self.new_tab(restore_text=text, select=False)
            self._view.set_selected_page(self._view.get_nth_page(0))
        else:
            cwd = self._cwd()
            for panel in self.tabs():
                panel.open_shell(cwd)

    def new_tab(self, restore_text: str | None = None, select: bool = True) -> PanelTerminal:
        """Append a shell tab (its shell spawns right away) and optionally
        select it. `restore_text` seeds the scrollback (session restore)."""
        panel = PanelTerminal()
        if self._settings is not None:
            panel.apply_settings(self._settings)
        panel.connect("shell-exited", self._on_shell_exited)
        panel.terminal.connect("bell", lambda *_: self.emit("bell"))
        page = self._view.append(panel)
        page.set_title(_("Terminal {number}").format(number=self._next_number))
        self._next_number += 1
        panel.open_shell(self._cwd(), restore_text)
        self._ever_spawned = True
        if select:
            self._view.set_selected_page(page)
        return panel

    def _find_page(self, panel: PanelTerminal) -> Adw.TabPage | None:
        for i in range(self._view.get_n_pages()):
            page = self._view.get_nth_page(i)
            if page.get_child() is panel:
                return page
        return None

    def _on_shell_exited(self, panel: PanelTerminal) -> None:
        """Typing `exit` in a shell closes its tab (the tab would otherwise
        sit on a dead screen). A shell already gone by teardown finds no
        page and is a no-op."""
        page = self._find_page(panel)
        if page is not None:
            self._view.close_page(page)

    def _on_close_page(self, view: Adw.TabView, page: Adw.TabPage) -> bool:
        panel = page.get_child()
        if page not in self._close_ok and panel.has_running_command():
            # The X on a busy shell asks first, mirroring the session tab's
            # own close protection — a build shouldn't die to a stray click.
            view.close_page_finish(page, False)  # keep the tab while we ask
            if page not in self._close_asking:
                self._ask_close_busy(page)
            return True
        self._close_ok.discard(page)
        view.close_page_finish(page, True)
        if view.get_n_pages() == 0:
            self._next_number = 1  # an empty strip restarts the numbering
            self.emit("last-tab-closed")
        return True  # close_page_finish already ran

    def _ask_close_busy(self, page: Adw.TabPage) -> None:
        self._close_asking.add(page)
        self._view.set_selected_page(page)  # show what's about to be killed

        def do_close() -> None:
            self._close_asking.discard(page)
            # The tab may have emptied on its own while the dialog sat open
            # (the command finished and the user typed `exit`).
            if self._find_page(page.get_child()) is page:
                self._close_ok.add(page)
                self._view.close_page(page)

        dialogs.confirm_dialog(
            self,
            _("Close tab with a running command?"),
            _("A command is still running in this terminal tab and will be terminated."),
            _("Close Tab"),
            do_close,
            on_dismiss=lambda: self._close_asking.discard(page),
            default_response="confirm",
        )

    def _on_selected(self, *_args) -> None:
        panel = self.selected_tab()
        if panel is not None and self.get_mapped():
            GLib.idle_add(panel.grab_terminal_focus)

    def grab_terminal_focus(self) -> None:
        panel = self.selected_tab()
        if panel is not None:
            panel.grab_terminal_focus()

    def has_terminal_focus(self) -> bool:
        return any(panel.terminal.has_focus() for panel in self.tabs())

    def has_running_command(self) -> bool:
        return any(panel.has_running_command() for panel in self.tabs())

    def select_busy_tab(self) -> None:
        """Bring the first tab with a live command to the front (the close
        confirmation shows the panel to reveal what's about to be killed)."""
        for panel in self.tabs():
            if panel.has_running_command():
                page = self._find_page(panel)
                if page is not None:
                    self._view.set_selected_page(page)
                return

    def capture_all(self) -> list[str]:
        """Each tab's scrollback text, in tab order."""
        return [panel.capture_contents() for panel in self.tabs()]

    def clear_all(self) -> None:
        for panel in self.tabs():
            panel.clear()

    def apply_settings(self, settings: dict) -> None:
        self._settings = settings
        for panel in self.tabs():
            panel.apply_settings(settings)


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
        # Emitted (debounced) when the editor panel's divider is moved: the
        # new panel px size, so the window can persist it as the app-wide
        # default. Mirrors panel-size-changed, minus the mode — the editor
        # panel only ever has the one position.
        "editor-size-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # The editor pane's detach button was clicked. The window owns the
        # pop-out (it has the application and AppState the new window needs),
        # so the tab just forwards the pane's request upward.
        "editor-pop-out-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
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
        # Decided at spawn time, so a toggle mid-session can't half-apply to
        # a shell that inherited the other choice; new tabs pick up a change.
        self._progress_env = bool((settings or {}).get("progress_termprop", True))
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
        self._setup_image_drop()

        self._search_bar = self._build_search_bar()
        self.append(self._search_bar)

        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Past "terminal_max_width", the clamp stops growing the terminal and
        # centers it instead; see _apply_terminal_max_width. Unset until
        # apply_settings runs, so it must start unconstrained rather than at
        # Adw.Clamp's own default (600px).
        self._width_clamp = Adw.Clamp(
            child=scrolled,
            hexpand=True,
            vexpand=True,
            maximum_size=_UNLIMITED_CLAMP_WIDTH,
            tightening_threshold=_UNLIMITED_CLAMP_WIDTH,
        )

        # The terminal is the single live view. When the agent asks a structured
        # question (detected from the transcript), a native card overlays it.
        # The "terminal-gutter" class paints the space the clamp leaves beside
        # the terminal, kept in step with the terminal's own theme (themes.py).
        self._overlay = Gtk.Overlay()
        self._overlay.set_vexpand(True)
        self._overlay.add_css_class("terminal-gutter")
        self._overlay.set_child(self._width_clamp)

        # Secondary plain-shell panel — an inner tab strip of shells — below
        # or beside the agent terminal. Swapping bottom↔right only flips the
        # paned's orientation, so the shells keep running.
        self._panel = PanelTabs()
        self._panel.set_visible(False)
        self._panel.connect("last-tab-closed", lambda *_: self.hide_panel())
        self._panel.connect("bell", lambda *_: self.emit("bell"))
        self._panel.set_cwd_lookup(self.current_agent_cwd)
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

        # Editor panel: a full-height right column beside the terminal↔shell
        # split above, in a new outer paned. Built now (but hidden) rather
        # than on first toggle, so per-session restore can reopen it without
        # a construct-on-demand race; HAVE_GTKSOURCE false leaves it None and
        # the footer button that would open it hidden (see _build_footer).
        editor_root = cwd if cwd and Path(cwd).is_dir() else str(Path.home())
        self._editor = editor.EditorPane(editor_root) if editor.HAVE_GTKSOURCE else None
        self._editor_detached = False  # pane reparented into its own EditorWindow
        self._editor_width = 0  # this tab's last-set editor width, px (0 = none yet)
        self._editor_apply_pending = False  # a programmatic divider set is queued
        self._editor_width_lookup = None  # () -> app-wide last-set width (set by the window)
        self._editor_width_emit_source: int | None = None  # debounce for editor-size-changed
        self._outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self._outer.set_wide_handle(True)
        self._outer.set_resize_start_child(True)
        self._outer.set_shrink_start_child(False)
        self._outer.set_resize_end_child(False)
        self._outer.set_shrink_end_child(False)
        self._outer.set_start_child(self._paned)
        if self._editor is not None:
            self._editor.set_visible(False)
            self._outer.set_end_child(self._editor)
            self._editor.connect(
                "request-pop-out", lambda *_: self.emit("editor-pop-out-requested")
            )
            self._editor.connect("add-to-chat", self._on_editor_add_to_chat)
        self._outer.connect("notify::position", lambda *_: self._remember_editor_width())
        self.append(self._outer)

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
            # Inherit, plus the progress-OSC coaxing — unless the experimental
            # setting is off, in which case a plain inherited environment.
            _agent_tab_environment() if self._progress_env else None,
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
        # order the work happened. Unlike their neighbours they act rather
        # than copy: a click opens the PR's actions, and its page on GitHub
        # is a right-click (or the menu's own "Open on GitHub" row) away.
        self._pr_chips = PrChipRow(_PR_CHIP_SPACING)
        self._pr_chips.set_visible(False)
        # Leading the row, where the oldest chip would be: the caret opens the
        # full list, titles and all — including the chips that didn't fit,
        # since it takes its width from the row and so costs it one.
        # The list itself is shared with the sidebar's own PR button (prmenu);
        # the footer is at the bottom of the tab, so this copy opens upwards.
        self._pr_menu = prmenu.new_popover(Gtk.PositionType.TOP)
        menu_icon = Gtk.Image.new_from_icon_name("pan-up-symbolic")
        menu_icon.set_pixel_size(_PR_REFRESH_ICON_PX)
        menu_icon.add_css_class("dim-label")
        self._pr_menu_btn = Gtk.MenuButton(child=menu_icon, popover=self._pr_menu)
        self._pr_menu_btn.add_css_class("flat")
        self._pr_menu_btn.set_tooltip_text(_("Every pull request this session has opened"))
        self._pr_menu_btn.set_create_popup_func(self._fill_pr_menu)
        self._pr_menu_btn.set_visible(False)
        # Sibling of the chips, never inside them: a chip opens its menu on
        # click, and a button in there would open the menu along with itself.
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

        # The same folder the cwd label names, one click away in the desktop's
        # file manager. Left of the panel toggles: it opens something outside
        # Collins, while everything to its right rearranges the tab itself.
        files_btn = Gtk.Button(icon_name="folder-symbolic")
        files_btn.add_css_class("flat")
        files_btn.set_tooltip_text(_("Open this folder in your file manager"))
        files_btn.connect("clicked", self._on_open_file_manager)

        # Only the selected tab is visible (and thus clickable), so routing
        # through the window's actions still targets the right tab.
        toggle_btn = Gtk.Button(icon_name="utilities-terminal-symbolic")
        toggle_btn.set_tooltip_text(
            _("Show/hide terminal panel (Ctrl+J)")
            + "\n"
            + _("Right-click to open this folder in your terminal")
        )
        toggle_btn.set_action_name("win.toggle-panel")
        # The button already means "a shell here"; a right-click asks for the
        # same thing outside Collins, in the terminal the desktop nominates —
        # for the times the panel isn't enough (a full-screen TUI, a second
        # monitor). Its own gesture, so the panel never toggles on the way.
        open_external = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        open_external.connect("pressed", self._on_open_external_terminal)
        toggle_btn.add_controller(open_external)

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

        # The editor toggle closes the row, right of the terminal toggle — a
        # page-with-a-folded-corner glyph, not the panel-split
        # icon `toggle_btn` uses next to it. One click means one of three
        # things depending on state: re-attach a detached editor, else open
        # the panel, else close it (see the window's _toggle_editor); the
        # tooltip always names whichever it is.
        self._editor_toggle_btn = Gtk.Button(icon_name="text-x-generic-symbolic")
        self._editor_toggle_btn.add_css_class("flat")
        self._editor_toggle_btn.set_action_name("win.toggle-editor")
        if self._editor is not None:
            self._editor_toggle_btn.set_tooltip_text(_("Show editor panel"))
        else:
            # Missing gtksourceview5 typelib: the window disables the
            # win.toggle-editor action, which greys this button out — keep it
            # visible so the tooltip can say what to install (per the GNOME
            # HIG, an insensitive control's tooltip explains why).
            self._editor_toggle_btn.set_tooltip_text(
                _(
                    "Editor unavailable — install GtkSourceView 5 "
                    "(gir1.2-gtksource-5) and restart Collins"
                )
            )

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        footer.add_css_class("tab-footer")
        footer.append(left)
        footer.append(self._footer_apps_box)
        footer.append(files_btn)
        toggle_btn.add_css_class("flat")
        footer.append(toggle_btn)
        footer.append(self._editor_toggle_btn)
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
        """One PR's chip: its state-and-status mark, then its number.

        The mark is the same two-icon overlay the menus use (see
        prmenu.status_icon) — the base icon's color is what the eye picks up
        without reading the row, and the badge on its corner is the one thing
        the PR needs doing. Icon before number, the way GitHub writes a PR.

        Every part of a chip answers for that PR and nothing else — the chips
        are siblings on the row, so each number carries its own menu (on a
        click) and its own link (on a right-click).
        """
        number = Gtk.Label(label=f"#{pr.number}")
        number.add_css_class("caption")
        number.add_css_class("dim-label")
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        chip.append(prmenu.status_icon(pr))
        chip.append(number)
        chip.set_tooltip_text(
            describe(pr) + "\n" + pr.url + "\n"
            + _("Click for actions") + "\n" + _("Right-click to open")
        )
        enable_open_on_click(chip, lambda: pr.url, button=Gdk.BUTTON_SECONDARY)
        # The chip is the shortest way to do something about a PR: the same
        # actions the caret's list offers, opened on the chip itself.
        prmenu.attach_actions(chip, pr, self._pr_action_host())
        return chip

    def _pr_action_host(self) -> prmenu.ActionHost:
        """How a PR's actions reach this tab: it is the session they belong to.

        The chips and the caret's list are the tab's own, so "can this session
        take a prompt?" is a question the tab answers about itself — a tab
        whose agent has exited, or whose prompt is half-written, keeps its
        chips and its PRs, but is not somewhere to send anything.
        """
        return prmenu.ActionHost(
            takes_prompt=self.takes_prompt,
            has_changes=lambda: has_changes(self.current_agent_cwd()),
            send_prompt=self.inject_prompt,
            refresh=self._request_update,
        )

    def _fill_pr_menu(self, _button: Gtk.MenuButton) -> None:
        """Build the caret's list, just before it opens.

        Nothing to fetch here, unlike the sidebar's copy of this menu: the
        footer's own poll is already refreshing every PR on the row, so what
        the tab is holding when the caret is clicked is current.
        """
        prmenu.fill(self._pr_menu, self._footer_prs, host=self._pr_action_host())

    def _refresh_pr_chips(self, prs: list[PullRequest]) -> None:
        """Show this session's PRs, oldest first, with the CI state each has.

        The whole row is rebuilt rather than patched: a chip's parts depend on
        its state (a badge appears, the base icon changes color), and the
        equality guard keeps the once-a-second poll from rebuilding anything
        unchanged.
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

    def _on_editor_add_to_chat(self, _pane, path: str, start_line: int, end_line: int) -> None:
        self.add_file_to_chat(path, start_line, end_line)

    def add_file_to_chat(self, path: str, start_line: int = 0, end_line: int = 0) -> None:
        """The editor's "Add to chat" (a right-clicked selection or file)
        and the header's attach-file button: type the agent's mention token
        for *path* into the input box — typed, never submitted, so the user
        says what they want done with it. The trailing space both
        terminates the CLI's mention token and leaves the cursor ready for
        that sentence.

        The path resolves against the agent's cwd right now, not the
        directory the tab started in — an agent that has cd'd into a
        worktree reads relative paths from there (see file_reference for
        the fallback when the file isn't under it). Unlike inject_prompt
        this isn't gated on takes_prompt: nothing is sent, and half-written
        input is exactly where a reference gets added mid-sentence. It IS
        gated on the agent actually being in the terminal, though — typed
        at a plain shell prompt the token isn't a mention, it's shell
        syntax, and the file name inside it is untrusted repo content."""
        if not self._agent_is_running():
            self.feed_message(_("Add to chat: the agent isn't running in this tab"))
            return
        reference = self.provider.file_reference(
            path, self.current_agent_cwd(), start_line, end_line
        )
        if reference is None:
            self.feed_message(_("Add to chat isn't available for this file"))
            return
        self.feed_child_text(reference + " ")
        GLib.idle_add(self._focus_terminal_after_add_to_chat)

    def _focus_terminal_after_add_to_chat(self) -> bool:
        """Move focus to the agent terminal once the "Add to chat" menu is
        gone. Deferred to idle because the grab must outlive the context
        menu that triggered it: the popover closes right after its action
        runs and hands focus back to the widget that opened it (the editor
        view), which undid an immediate grab. With the editor popped out
        the grab alone can't cross windows either — first re-select this
        tab's page (the main window may be showing another tab while the
        editor floats) and present its window, then grab."""
        view = self.get_ancestor(Adw.TabView)
        page = view.get_page(self) if view is not None else None
        if page is not None:
            view.set_selected_page(page)
        root = self.get_root()
        if isinstance(root, Gtk.Window):
            root.present()
        self.grab_terminal_focus()
        return GLib.SOURCE_REMOVE

    # -- drag & drop into the agent ----------------------------------------

    def _setup_image_drop(self) -> None:
        """Dropping onto the agent terminal references the payload in the
        chat, the same way "Add to chat" does. Two payloads are claimed:

        - files (Gdk.FileList — a file manager drag): the mention names the
          dropped file itself, wherever it lives;
        - raw image data (Gdk.Texture — a drag from a browser, a screenshot
          tool, an image viewer): there is no path to mention, so a PNG copy
          is saved first and the mention names the copy (see dropimages.py).

        Texture is listed first so a drag offering both — a browser image
        drag carries the source URL *and* the pixels — resolves to the
        pixels; the URL half would arrive as a Gio.File with no local path,
        referencing nothing the CLI could read.

        Everything else (plain text, and every drop while no agent is
        running — see _accept_drop) is left unclaimed on purpose: VTE's own
        drop handling pastes dropped text and file paths into the terminal,
        which is exactly right at a shell prompt.
        """
        # Constructed bare (PyGObject's DropTarget.new refuses the
        # "no type yet" GType) and given both payload types via set_gtypes.
        drop = Gtk.DropTarget(actions=Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.Texture, Gdk.FileList])
        drop.connect("accept", self._accept_drop)
        drop.connect("drop", self._on_drop)
        self.terminal.add_controller(drop)

    def _accept_drop(self, target: Gtk.DropTarget, drop: Gdk.Drop) -> bool:
        """Claim the drag only when the mention it would type means
        something: the payload is one of ours, the provider has a mention
        syntax at all, and the agent is actually in the terminal. Connecting
        "accept" replaces GtkDropTarget's built-in format check, so that
        check comes first — the drop's formats already carry the GTypes GDK
        can deserialize the offered mime types into, so a plain match covers
        both a real Gdk.FileList and an image/png offer. Declining here (vs
        failing in _on_drop) matters: an unclaimed drag falls through to
        VTE's path-pasting drop handling instead of dying on our target."""
        if not drop.get_formats().match(target.get_formats()):
            return False
        if self.provider.file_reference("image.png", None) is None:
            return False  # base agents: no input box to type a mention into
        return self._agent_is_running()

    def _on_drop(self, _target: Gtk.DropTarget, value, _x: float, _y: float) -> bool:
        if isinstance(value, Gdk.Texture):
            return self._drop_texture(value)
        if isinstance(value, Gdk.FileList):
            return self._drop_files(value.get_files())
        return False

    def _drop_texture(self, texture: Gdk.Texture) -> bool:
        """Raw image data: save the PNG copy, mention the copy."""
        try:
            data = texture.save_to_png_bytes().get_data()
            directory = dropimages.default_directory()
            dropimages.prune_stale(directory)
            path = dropimages.save_png(bytes(data), directory)
        except (GLib.Error, OSError):
            self.feed_message(_("couldn't save a copy of the dropped image"))
            return False
        return self._mention_dropped_paths([str(path)])

    def _drop_files(self, files: list[Gio.File]) -> bool:
        """Dropped files: mention each one directly. Skipped entries (a
        remote URI with no local path) are counted, not echoed — a URI is
        outside content arriving at first contact, and feed_message writes
        raw bytes to the tty."""
        paths = [p for f in files if (p := f.get_path()) is not None]
        skipped = len(files) - len(paths)
        if skipped:
            self.feed_message(
                ngettext(
                    "skipped {n} dropped item that isn't a local file",
                    "skipped {n} dropped items that aren't local files",
                    skipped,
                ).format(n=skipped)
            )
        return self._mention_dropped_paths(paths)

    def _mention_dropped_paths(self, paths: list[str]) -> bool:
        """Type a mention token for each path into the input box — typed,
        never submitted, mirroring "Add to chat" (see _on_editor_add_to_chat
        for why the trailing space and the missing takes_prompt gate). Paths
        the provider refuses to reference (a control character in the name —
        see file_reference) are reported by count, not echoed: those names
        are exactly the untrusted bytes feed_message must not write to the
        tty. The focus grab is immediate rather than idle-deferred: a drop
        has no popover to hand focus back anywhere."""
        cwd = self.current_agent_cwd()
        text, failed = dropimages.mention_text(
            paths, lambda path: self.provider.file_reference(path, cwd)
        )
        if failed:
            self.feed_message(
                ngettext(
                    "couldn't reference {n} dropped file name",
                    "couldn't reference {n} dropped file names",
                    failed,
                ).format(n=failed)
            )
        if not text:
            return False
        self.feed_child_text(text)
        self.grab_terminal_focus()
        return True

    def inject_prompt(self, text: str) -> None:
        """Type *text* into the agent, send it, and put the tab in front.

        What the PR menu's prompt actions do. Only offered while
        `takes_prompt` says the input is empty, so nothing of the user's is
        ever sent along with it.

        The Return goes in a second write, a beat later, rather than on the
        end of the first: an agent CLI reads a chunk arriving all at once as a
        paste, and a Return inside a paste is a newline in the box — it left
        the prompt typed out and waiting for someone to press enter. Arriving
        on its own, after the input has settled, it submits.
        """
        self.feed_child_text(text)
        GLib.timeout_add(_PROMPT_SUBMIT_MS, self._submit_prompt)
        self.grab_terminal_focus()

    def _submit_prompt(self) -> bool:
        self.feed_child_text("\r")
        return GLib.SOURCE_REMOVE

    def takes_prompt(self) -> bool:
        """Whether a prompt sent right now would land in an empty input box.

        The provider reads that off the screen (see Provider.takes_prompt); all
        this does is find what it reads — the line the cursor is on, and how
        far into it the cursor sits — and rule out a terminal with no agent
        left in it.
        """
        if self._child_pid is None:
            return False
        column, row = self.terminal.get_cursor_position()
        line = self.terminal.get_text_range_format(
            Vte.Format.TEXT, row, 0, row, self.terminal.get_column_count()
        )
        text = line[0] if isinstance(line, tuple) else line
        return self.provider.takes_prompt(text or "", column)

    def worktree_exit_prompt_keystrokes(self) -> str | None:
        """Keystrokes that accept the agent's "leaving a worktree" dialog if
        it's showing right now, or None if it isn't (see
        Provider.worktree_exit_prompt). The whole visible screen, not just
        the cursor's line — this dialog is a multi-line menu, not something
        drawn at the input prompt."""
        if self._child_pid is None:
            return None
        _, cursor_row = self.terminal.get_cursor_position()
        top_row = max(0, cursor_row - self.terminal.get_row_count() + 1)
        screen = self.terminal.get_text_range_format(
            Vte.Format.TEXT, top_row, 0, cursor_row, self.terminal.get_column_count()
        )
        text = screen[0] if isinstance(screen, tuple) else screen
        return self.provider.worktree_exit_prompt(text or "")

    def screen_first_column(self) -> tuple[tuple[str, ...], tuple[int, int]] | None:
        """The first character of each visible screen row ("" for a blank
        one), with the (columns, rows) grid it was read at — what the
        window's SpinnerWatch compares between samples — or None with no
        child to be busy. Anchored to the cursor like the other screen
        readers, so the user scrolling back never changes what is read."""
        if self._child_pid is None:
            return None
        rows = self.terminal.get_row_count()
        columns = self.terminal.get_column_count()
        _, cursor_row = self.terminal.get_cursor_position()
        top_row = max(0, cursor_row - rows + 1)
        screen = self.terminal.get_text_range_format(
            Vte.Format.TEXT, top_row, 0, cursor_row, columns
        )
        text = screen[0] if isinstance(screen, tuple) else screen
        first = [line[:1] for line in (text or "").split("\n")][:rows]
        first += [""] * (rows - len(first))
        return tuple(first), (columns, rows)

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
        self._watch_transcript(jsonl_path)

    @property
    def transcript_path(self) -> str | None:
        """The transcript this tab is tailing, or None."""
        path = self._transcript.path
        return str(path) if path else None

    def relocate_transcript(self, jsonl_path: str | Path) -> None:
        """Follow this tab's transcript to a new path.

        Entering a git worktree makes the CLI re-key the session's transcript
        under a project directory named for the new working directory, which
        moves the file out from under the monitor watching it. Nothing about
        the session changed, so unlike `set_transcript_path` this keeps the
        chips, the pending card and everything already parsed — it only
        re-aims the tail and the monitor at where the file lives now.
        """
        self._transcript.relocate(jsonl_path)
        self._watch_transcript(jsonl_path)

    def _watch_transcript(self, jsonl_path: str | Path | None) -> None:
        """Point the file monitor at *jsonl_path* and kick off a read."""
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
        left to run and shows no badge anyway, so an old chip on a long-lived
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
        if self._editor is not None:
            # Same pane object wherever it lives (in-tab or popped out).
            self._editor.set_agent_files(self._transcript.touched_files())
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
        self._panel.open(restore)
        if not self.panel_visible:
            self._panel.set_visible(True)
            self._apply_panel_size()
        if focus:
            GLib.idle_add(self._panel.grab_terminal_focus)

    def hide_panel(self) -> None:
        if not self.panel_visible:
            return
        self._remember_panel_size()
        refocus = self._panel.has_terminal_focus()
        self._panel.set_visible(False)
        if refocus:
            self.grab_terminal_focus()

    def panel_has_running_command(self) -> bool:
        """True when a command is running in any panel shell tab — even a
        hidden panel's job is protected by the close confirmation."""
        return self._panel.has_running_command()

    def select_busy_panel_tab(self) -> None:
        """Front the inner panel tab whose shell is busy, so the close
        confirmation's "will be terminated" points at something visible."""
        self._panel.select_busy_tab()

    def _load_panel_history(self) -> list[str] | None:
        """Saved panel scrollbacks (one per inner tab, in tab order) for this
        session — forks don't restore (their panel would clash with the
        original tab's) and never save."""
        if self.fork or not self.session_id:
            return None
        return panelhistory.load_all(self.session_id) or None

    def save_panel_history(self) -> None:
        """Persist each panel tab's scrollback so re-opening this session
        restores them. A panel never opened in this tab leaves prior history
        untouched; tabs closed along the way drop out of the saved set."""
        if self.fork or not self.session_id or not self._panel.ever_spawned:
            return
        panelhistory.save_all(self.session_id, self._panel.capture_all())

    def clear_panel_history(self) -> None:
        """Wipe every panel tab's scrollback and the persisted history files.
        The onscreen buffers must go too — the save on tab/window close would
        otherwise re-dump them and resurrect the files. Also clears stale
        history from a previous run when the panel was never opened here."""
        self._panel.clear_all()
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

    # -- editor panel --------------------------------------------------------

    @property
    def editor_visible(self) -> bool:
        """Whether the editor panel is showing *inside this tab* — false while
        the pane is popped out into its own window (see editor_detached)."""
        return self._editor is not None and not self._editor_detached and self._editor.get_visible()

    @property
    def editor_detached(self) -> bool:
        return self._editor_detached

    def toggle_editor(self) -> None:
        """The footer icon's open/close half. The (a) branch — dock a
        popped-out editor back — is the window's (`_toggle_editor`), which
        checks for a live EditorWindow before falling through to this."""
        if self._editor is None or self._editor_detached:
            return
        if self.editor_visible:
            self.hide_editor()
        else:
            self.show_editor()

    def show_editor(self) -> None:
        if self._editor is None or self._editor_detached or self.editor_visible:
            return
        self._editor.set_visible(True)
        self._apply_editor_width()
        self._editor_toggle_btn.set_tooltip_text(_("Hide editor panel"))

    def hide_editor(self) -> None:
        if self._editor is None or not self.editor_visible:
            return
        self._remember_editor_width()
        self._editor.set_visible(False)
        self._editor_toggle_btn.set_tooltip_text(_("Show editor panel"))

    def detach_editor(self):
        """Hand the live pane over for reparenting into an EditorWindow (the
        window builds that; see _pop_out_editor there). Returns the pane, or
        None when there's nothing to detach. The in-tab slot stays empty —
        and the footer icon means "bring it back" — until reattach_editor."""
        if self._editor is None or self._editor_detached:
            return None
        self._remember_editor_width()  # dock-back reopens at this width
        self._editor_detached = True
        self._outer.set_end_child(None)
        self._editor.set_visible(True)  # it may have been hidden along with the panel
        self._editor.set_detached(True)
        self._editor_toggle_btn.set_tooltip_text(_("Bring editor back into this tab"))
        return self._editor

    def reattach_editor(self) -> None:
        """Dock the pane back where it was: same outer-paned slot, same
        remembered width, open even if it was closed when detached (a pane
        the user was just using in a window shouldn't dock back to nothing)."""
        if self._editor is None or not self._editor_detached:
            return
        self._editor_detached = False
        self._editor.set_detached(False)
        self._outer.set_end_child(self._editor)
        self._editor.set_visible(False)  # show_editor's no-op guard needs "closed"
        self.show_editor()

    def editor_dirty_count(self) -> int:
        return self._editor.dirty_count() if self._editor is not None else 0

    def editor_dirty_names(self) -> list[str]:
        return self._editor.dirty_names() if self._editor is not None else []

    def editor_save_all(self, on_done) -> None:
        """Save every dirty editor buffer; `on_done(all_succeeded)` when the
        async saves resolve (immediately, when nothing is dirty)."""
        if self._editor is None:
            on_done(True)
            return
        self._editor.save_all(on_done)

    def editor_save(self) -> None:
        if self._editor is not None:
            self._editor.save_current()

    def focus_editor(self) -> None:
        if self._editor is not None:
            self._editor.focus_default()

    @property
    def editor_root(self) -> str | None:
        """The project directory this tab's editor is rooted at (also quick
        open's search root); None when GtkSourceView is missing."""
        return str(self._editor.root) if self._editor is not None else None

    def can_open_in_editor(self, path: str | Path) -> bool:
        """Whether `open_in_editor(path)` would land: an editor exists and
        *path* resolves inside its project root (the pane's own guard would
        refuse anything outside; this lets the window pick a better tab)."""
        return self._editor is not None and editorfiles.is_inside(self._editor.root, path)

    def open_in_editor(self, path: str | Path) -> None:
        """Open *path* in this tab's editor, revealing the panel if it is
        closed. While the pane is popped out its window already shows it —
        presenting that window is the caller's job (it owns the windows)."""
        if self._editor is None:
            return
        if not self._editor_detached and not self.editor_visible:
            self.show_editor()
        self._editor.open_file(path)
        self._editor.focus_default()

    def set_editor_width_lookup(self, lookup) -> None:
        """`lookup() -> px` supplies the app-wide last-set editor width, used
        for tabs that haven't sized their own editor yet."""
        self._editor_width_lookup = lookup

    def _remember_editor_width(self) -> None:
        """Record the editor's width off the divider. Skipped while an apply
        is still queued: revealing the pane re-lays the paned out at its
        default fraction before `_apply_editor_width`'s idle callback runs,
        and remembering *that* width poisons the very value the apply is
        about to read — the settings lookup then never wins, and the
        fraction gets persisted app-wide (the first-show width bug)."""
        if not self.editor_visible or self._editor_apply_pending:
            return
        total = self._outer.get_width()
        if total <= 0:
            return
        width = total - self._outer.get_position()
        if width <= 0 or width == self._editor_width:
            return
        self._editor_width = width
        if self._editor_width_emit_source is not None:
            GLib.source_remove(self._editor_width_emit_source)
        self._editor_width_emit_source = GLib.timeout_add(500, self._emit_editor_width_changed)

    def _emit_editor_width_changed(self) -> bool:
        self._editor_width_emit_source = None
        self.emit("editor-size-changed", self._editor_width)
        return GLib.SOURCE_REMOVE

    def _apply_editor_width(self) -> None:
        """Size the editor panel once the outer paned's own size is known:
        this tab's remembered width, else the app-wide last-set width, else
        roughly a third of the paned. A simplified copy of
        `_apply_panel_size` — one position, no per-mode bookkeeping, but the
        same apply-pending gate (see `_remember_editor_width`)."""
        self._editor_apply_pending = True

        def position() -> bool:
            self._editor_apply_pending = False
            total = self._outer.get_width()
            if total <= 0:
                return GLib.SOURCE_REMOVE
            width = self._editor_width or 0
            if width <= 0 and self._editor_width_lookup is not None:
                width = self._editor_width_lookup() or 0
            if 0 < width < total:
                self._outer.set_position(total - width)
            else:
                self._outer.set_position(int(total * 0.62))
            return GLib.SOURCE_REMOVE

        GLib.idle_add(position)

    def capture_editor_state(self) -> dict | None:
        """Snapshot the editor's open/width/files for per-session persistence,
        mirroring capture_panel_state. None when the editor was never used in
        this tab, so a session's saved state survives tabs that never touched
        it. Forks never persist."""
        if self.fork or self._editor is None:
            return None
        if not self.editor_visible and not self._editor_detached and not self._editor.open_paths():
            return None
        self._remember_editor_width()
        # A popped-out editor counts as open: the window itself isn't
        # persisted, so the session restores with the panel showing in-tab.
        state: dict = {
            "open": self.editor_visible or self._editor_detached,
            "files": self._editor.open_paths(),
        }
        cursors = self._editor.cursor_positions()
        if cursors:
            state["cursors"] = cursors
        active = self._editor.active_path()
        if active:
            state["active"] = active
        if self._editor_width:
            state["width"] = self._editor_width
        return state

    def restore_editor_state(self, state: dict) -> None:
        """Re-apply a session's saved editor snapshot, mirroring
        restore_panel_state. Width lands in this tab's own memory — restoring
        a session must not disturb the app-wide default for new tabs."""
        if self._editor is None:
            return
        width = state.get("width")
        if isinstance(width, int) and width > 0:
            self._editor_width = width
        files = state.get("files")
        if isinstance(files, list) and files:
            cursors = state.get("cursors")
            self._editor.restore(files, state.get("active"), cursors if isinstance(cursors, dict) else {})
        if state.get("open"):
            self.show_editor()

    def _candidate_pids(self) -> list[int]:
        """Pids worth searching for the agent process: the terminal's
        foreground process group leader, then the child originally spawned.

        The group leader is not always the process that moves — a
        daemon-hosted session leaves a wrapper at its head and runs the agent
        as its child — so both ends are worth trying.
        """
        pids = []
        pty = self.terminal.get_pty()
        if pty is not None:
            try:
                pids.append(os.tcgetpgrp(pty.get_fd()))
            except OSError:
                pass
        if self._child_pid is not None:
            pids.append(self._child_pid)
        return pids

    def current_agent_cwd(self) -> str | None:
        """Best-effort cwd of what's running in the agent terminal: the
        foreground process if any (the agent may have cd'd into a worktree),
        else the shell, else the directory the tab started in.

        Each candidate's agent descendants are searched before falling back
        to the candidate itself; see `_candidate_pids`.
        """
        cli = getattr(self.provider, "cli", "") or ""
        for pid in self._candidate_pids():
            cwd = proctree.agent_descendant_cwd(pid, cli)
            if cwd is not None:
                return cwd
            cwd = proctree.process_cwd(pid)
            if cwd is not None:
                return cwd
        return self._cwd

    def _agent_is_running(self) -> bool:
        """Whether the provider's CLI is alive in this terminal right now —
        the same descendant search current_agent_cwd runs, minus its
        shell-cwd fallbacks. False means whatever is at the prompt is not
        the agent (a plain shell, or something the user launched)."""
        cli = getattr(self.provider, "cli", "") or ""
        return any(
            proctree.agent_descendant_cwd(pid, cli) is not None for pid in self._candidate_pids()
        )

    def has_background_descendant(self) -> bool:
        """Whether the agent has something still running below it right now —
        a tool call in flight, or a background job (a dev server, a long
        build) it started and left running. An extra "still working" signal
        for a session whose terminal has otherwise gone quiet; see
        `ActivityTracker` in activity.py."""
        cli = getattr(self.provider, "cli", "") or ""
        return any(proctree.has_live_descendant(pid, cli) for pid in self._candidate_pids())

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
        self._apply_terminal_max_width(settings)
        self._set_footer_apps(settings.get("footer_apps") or [])
        self._panel.apply_settings(settings)
        if self._editor is not None:
            self._editor.apply_settings(settings)

    def _apply_terminal_max_width(self, settings: dict) -> None:
        try:
            max_width = int(settings.get("terminal_max_width") or 0)
        except (TypeError, ValueError):
            max_width = 0
        width = max_width if max_width > 0 else _UNLIMITED_CLAMP_WIDTH
        self._width_clamp.set_maximum_size(width)
        self._width_clamp.set_tightening_threshold(width)

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

    def _on_open_file_manager(self, _btn) -> None:
        """Open the agent's *current* working directory (worktree-aware, like
        the cwd label beside the button) in the desktop's file manager, via the
        same window action the sidebar's Open in File Manager uses."""
        cwd = self.current_agent_cwd()
        if cwd:
            self.activate_action("win.open-folder", GLib.Variant("s", cwd))

    def _on_open_external_terminal(self, gesture: Gtk.GestureClick, *_args) -> None:
        """Right-click on the panel toggle: open the agent's *current* working
        directory (worktree-aware, like the panel itself) in the desktop's own
        terminal.

        Routed through the window's action, so the folder opens the same way it
        does from the sidebar's Open in Terminal — the desktop's pick, resolved
        at click time in case it has changed since the tab opened.
        """
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        cwd = self.current_agent_cwd()
        if cwd:
            self.activate_action("win.open-folder-terminal", GLib.Variant("s", cwd))

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
