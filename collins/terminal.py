# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-14. Full change history: git log for this file.

"""A tab hosting a VTE terminal running the user's shell with an agent CLI inside."""

from __future__ import annotations

import os
import shlex
import threading
from collections.abc import Callable, Collection
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import (  # noqa: E402
    apppicker,
    composerkeys,
    dropimages,
    editor,
    editorfiles,
    footerapps,
    panelhistory,
    panellayout,
    prmenu,
    proctree,
    themes,
    vtehtml,
)
from .claudemodels import short_name  # noqa: E402
from .composer import ComposerPage, ComposerView  # noqa: E402
from .copylabel import copy_tooltip, enable_copy_on_click  # noqa: E402
from .formatting import display_path  # noqa: E402
from .gitinfo import current_branch, has_changes  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .lightbox import present_image_lightbox  # noqa: E402
from .linkpatterns import (  # noqa: E402
    FILE_PATTERN,
    STITCH_URL_ROWS,
    URL_PATTERN,
    bare_names_pattern,
    resolve_file_reference,
    resolve_wrapped_reference,
    resolve_wrapped_url,
    token_at_column,
)
from .panedsizer import PanedSizer  # noqa: E402
from .paneldock import PanelDock  # noqa: E402
from .panelstrip import PanelStrip  # noqa: E402
from .providers import (  # noqa: E402
    EnteredPrompt,
    Provider,
    get_provider,
    split_screen_rows,
)
from .prstatus import (  # noqa: E402
    PullRequest,
    describe,
    discover_pr,
    enrich,
    from_records,
    invalidate,
    known,
    merge_ordered,
    parse_pr_url,
    to_records,
)
from .prview import PrViewPage  # noqa: E402
from .sessions import (  # noqa: E402
    recreatable_worktree,
    recreate_worktree,
    worktree_project_root,
)
from .shellinput import shell_command  # noqa: E402
from .transcript import TranscriptModel  # noqa: E402

_TRANSCRIPT_DEBOUNCE_MS = 400
_PROMPT_POLL_MS = 1000  # backstop poll for detecting the agent's prompts
_CWD_POLL_MS = 2000  # footer refresh; only ticks while the tab is visible
# How many consecutive cwd polls a new working directory has to survive before
# the editor follows it (see _maybe_follow_editor). Two is enough to ride out
# the flap around a CLI starting, exiting or being forked, and still lands
# inside the pause after a worktree is entered.
_EDITOR_FOLLOW_TICKS = 2
# How long an injected prompt is left sitting in the input before the Return
# that sends it (see inject_prompt). Long enough that the CLI has stopped
# reading the text as a paste, short enough that nobody watching sees a pause.
_PROMPT_SUBMIT_MS = 250
# How often, and for how long, a new session set to open floating waits for
# its agent to actually come up before raising the composer over it (see
# autoshow_composer). Twenty seconds covers a cold CLI start on a slow disk;
# past that the shell is presumed to be sitting at a prompt with no agent.
_COMPOSER_AUTOSHOW_POLL_MS = 400
_COMPOSER_AUTOSHOW_TRIES = 50
# The composer's open-cut, which is a run of screen reads either side of an
# erase (see TerminalTab._begin_cut). The read is taken once _CUT_SETTLE_READS
# of them _CUT_SETTLE_MS apart agree — 150ms of a still input box, measured
# against the CLI (2.1.226), which finishes echoing a burst of typing 30-100ms
# after the last key. A box still moving after _CUT_SETTLE_TRIES of them is
# not cut at all: erasing a read that is still catching up would take the
# characters it hasn't shown yet with it. The erase is then checked again
# _CUT_VERIFY_MS apart — each gap measured from the check before it, so the
# last one lands about a second and a half after the cut — widening because a
# busy CLI can take a while to work through a line of backspaces.
_CUT_SETTLE_MS = 50
_CUT_SETTLE_READS = 4
_CUT_SETTLE_TRIES = 12
_CUT_VERIFY_MS = (150, 400, 900)
_PR_REFRESH_ICON_PX = 12  # the refresh button sits with them, not above them
# A session links every PR that passes through its tool output, including ones
# it only read, so the row is bounded: it tracks (and saves, and refreshes) the
# newest this many, and a session that busy has stopped caring about its first.
# How many of them are on screen is a question of width, not of this (see
# PrChipRow).
_MAX_PR_CHIPS = 20
_PR_CHIP_SPACING = 8  # between chips; their own parts sit 4 apart
# How long a coming-back-into-view refresh (window refocused, tab selected)
# vouches for the chips before another one is allowed to hit `gh` again.
# Focus comes and goes much faster than CI does — alt-tabbing past the window
# must not turn into a flurry of subprocesses.
_PR_FOCUS_REFRESH_MIN_US = 10 * 1_000_000

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


def _within(root: str, path: str) -> bool:
    """Whether *path* is *root* itself or something under it. Purely lexical."""
    root, path = os.path.normpath(root), os.path.normpath(path)
    return path == root or path.startswith(root + os.sep)


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

    Covers three kinds of link a terminal shows: OSC 8 hyperlinks (what agent
    CLIs emit for file references — VTE ignores the escape entirely until
    allow-hyperlink is switched on), plain URLs in the output matched by regex
    the way GNOME Terminal matches them, and path-shaped file references
    (`collins/foo.py:12`) matched by a second regex and validated against the
    filesystem only at click time — VTE has no per-match callback, so the
    hover underline can't know whether the file exists, but a click on a
    candidate that resolves nowhere falls through to the terminal unclaimed
    and costs the user nothing. Root-level files carry no slash, so the path
    grammar can't take them; _RootNameLinks keeps one more regex built from
    the names actually at the project root, so `README.md` underlines too.

    No regex can see past a newline the CLI itself wrote, so a reference too
    long for one row matches only in halves — each half underlining on its
    own. The click stitches them back together (_resolve_wrapped_at for
    paths, _resolve_wrapped_url_at for URLs) before deciding what it opens.
    """
    terminal.set_allow_hyperlink(True)
    tag_kinds: dict[int, str] = {}
    for pattern, kind in ((URL_PATTERN, "url"), (FILE_PATTERN, "file")):
        try:
            regex = Vte.Regex.new_for_match(
                pattern, len(pattern.encode()), _PCRE2_MULTILINE
            )
        except GLib.Error:
            continue  # VTE built without PCRE2: OSC 8 links still work
        tag = terminal.match_add_regex(regex, 0)
        terminal.match_set_cursor_name(tag, "pointer")
        tag_kinds[tag] = kind
    _RootNameLinks(terminal, tag_kinds)

    def on_pressed(gesture: Gtk.GestureClick, _n_press, x: float, y: float) -> None:
        if not gesture.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return
        kind = "url"
        uri = terminal.check_hyperlink_at(x, y)
        # An OSC 8 hyperlink carries its target in the escape sequence, whole
        # however the visible text wrapped; only regex matches read off the
        # screen can be a fragment of something longer.
        from_screen = uri is None
        if uri is None and tag_kinds:
            match, tag = terminal.check_match_at(x, y)
            if match is not None:
                kind = tag_kinds.get(tag, "url")
            uri = match
        roots = _reference_roots(terminal)
        if not uri:
            # A wrapped reference's continuation fragment often contains no
            # slash (`o.py:7)`) and so matches nothing — the half holding
            # the file *name* offers no click candidate at all. Hand the
            # stitcher the raw token under the pointer instead; its geometry
            # gates and existence check keep prose clicks inert. A URL's
            # continuation half (`03/files`) is just as matchless, so the URL
            # stitcher gets the same token when no file comes of it.
            resolved = _resolve_wrapped_at(terminal, None, x, y, roots)
            if resolved is not None:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                path, line, col = resolved
                _open_file_reference(terminal, path, line, col)
                return
            stitched = _resolve_wrapped_url_at(terminal, None, x, y)
            if stitched is None:
                return
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            _launch_uri(terminal, stitched)
            return
        if kind == "file":
            # Stitching runs before direct resolution: a fragment of a
            # reference the CLI hard-wrapped can resolve on its own (the
            # leading fragment of a wrapped path is often an existing
            # directory prefix), and the stitched whole is the truer
            # reading. The stitcher only ever returns geometry-gated,
            # existence-checked joins. Mid-row references fail its edge
            # gates immediately; a reference alone on its row does probe
            # its neighbours before the direct fallback wins.
            resolved = _resolve_wrapped_at(terminal, uri, x, y, roots)
            if resolved is None:
                resolved = resolve_file_reference(uri, roots)
            if resolved is not None:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                path, line, col = resolved
                _open_file_reference(terminal, path, line, col)
                return
            # The path grammar takes the tail of a wrapped URL for a relative
            # path (`303/files`), and it resolves nowhere; before the click
            # falls through, ask whether the row above hands it a scheme.
            stitched = _resolve_wrapped_url_at(terminal, None, x, y)
            if stitched is None:
                return  # over-matched prose: leave the click to the terminal
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            _launch_uri(terminal, stitched)
            return
        if from_screen:
            # The visible match may be only as much of the URL as fit on its
            # row; the rest is on the next one, past a newline no regex over
            # screen text can see. A stitch that isn't clearly a wrap comes
            # back None and the match opens as it stands.
            uri = _resolve_wrapped_url_at(terminal, uri, x, y) or uri
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        _launch_uri(terminal, uri)

    # Capture phase, so Ctrl+click opens the link even when the running app
    # has turned on mouse reporting (same trick as the context menu).
    click = Gtk.GestureClick(button=1)
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.connect("pressed", on_pressed)
    terminal.add_controller(click)


def _launch_uri(terminal: Vte.Terminal, uri: str) -> None:
    """Open a clicked link the way its scheme deserves."""
    if uri.startswith("www."):
        uri = "http://" + uri  # the bare-host grammar's half of a URL
    if uri.startswith("file:"):
        # A file: URI (or OSC 8 file: hyperlink) behaves exactly like a
        # matched path reference — lightbox for images, editor inside the
        # project, default app otherwise — however the CLI happened to emit
        # it. path_from_file_uri sheds any #L10-style fragment.
        path = editorfiles.path_from_file_uri(uri)
        if path is not None:
            _open_file_reference(terminal, path, None, None)
            return
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_uri(uri))
    else:
        launcher = Gtk.UriLauncher.new(uri)
    launcher.launch(terminal.get_root(), None, _on_link_launched)


def _on_link_launched(launcher: Gtk.UriLauncher | Gtk.FileLauncher, result) -> None:
    try:
        launcher.launch_finish(result)
    except GLib.Error:
        pass  # no handler for the scheme/type, or the user dismissed the chooser


def _reference_roots(terminal: Vte.Terminal) -> list[str | None]:
    """Where a relative file reference may be rooted: the running agent's cwd
    first (Claude often works from a worktree or subdirectory), then the
    tab's project root. Trying both catches the common cases cheaply."""
    tab = terminal.get_ancestor(TerminalTab)
    if tab is None:
        return []
    return [tab.current_agent_cwd(), tab.editor_root]


def _screen_at(
    terminal: Vte.Terminal, x: float, y: float
) -> tuple[list[str], int, int, int] | None:
    """The visible screen as a list of screen-row texts, plus the row and
    column the click landed on and the row width — the shared half of both
    stitchers, which need neighbour rows rather than a single match.

    Row texts come from the *visible screen* snapshot, indexed by screen
    row, never from grid-row reads: the grid APIs address VTE's internal
    ring, and under a repaint-style renderer (claude's own UI) the ring
    drifts a full page away from what the adjustment describes, so every
    adjustment-derived get_text_range read comes back empty — discovered
    live."""
    ch = terminal.get_char_height()
    cw = terminal.get_char_width()
    if ch <= 0 or cw <= 0:
        return None
    text = terminal.get_text_format(Vte.Format.TEXT) or ""
    cols = int(terminal.get_column_count())
    rows: list[str] = []
    for line in text.split("\n"):
        if len(line) <= cols:
            rows.append(line)
        else:
            # VTE returns soft-wrapped screen rows joined into one logical
            # line; a soft-wrapped row is by definition full-width, so
            # fixed-size chunks reconstruct the screen rows exactly.
            rows.extend(line[i : i + cols] for i in range(0, len(line), cols))
    # With pixel scrolling the viewport may start mid-row; the snapshot's
    # first line is that partial row, so shift y by the fraction cut off.
    vadj = terminal.get_vadjustment()
    frac = 0.0
    if vadj is not None and terminal.get_scroll_unit_is_pixels():
        frac = vadj.get_value() % ch
    return rows, int((y + frac) // ch), int(x // cw), cols


def _row_reader(rows: list[str]) -> Callable[[int], str]:
    def row_text(r: int) -> str:
        return rows[r] if 0 <= r < len(rows) else ""

    return row_text


# The clicked row is probed with a ±1 slop in both stitchers: the y→row
# division ignores VTE's inner border, so a click near a row's edge can land
# a row out. The geometry gates reject the wrong rows on their own for
# paths; the URL side narrows the slop further (see _resolve_wrapped_url_at).
_ROW_SLOP = (0, -1, 1)


def _resolve_wrapped_at(
    terminal: Vte.Terminal,
    candidate: str | None,
    x: float,
    y: float,
    roots: list[str | None],
) -> tuple[str, int | None, int | None] | None:
    """Stitch a failed path candidate with its neighbour rows (see
    linkpatterns.resolve_wrapped_reference). With *candidate* None (nothing
    under the pointer matched at all), the whitespace-delimited token at the
    clicked cell stands in as the candidate."""
    screen = _screen_at(terminal, x, y)
    if screen is None:
        return None
    rows, row, col, _cols = screen
    row_text = _row_reader(rows)
    for r in (row + slop for slop in _ROW_SLOP):
        row_txt = row_text(r)
        cand = candidate if candidate is not None else token_at_column(row_txt, col)
        if not cand:
            continue
        resolved = resolve_wrapped_reference(
            cand,
            row_txt,
            [row_text(r - 1), row_text(r - 2)],
            [row_text(r + 1), row_text(r + 2), row_text(r + 3)],
            roots,
        )
        if resolved is not None:
            return resolved
    return None


def _resolve_wrapped_url_at(
    terminal: Vte.Terminal, candidate: str | None, x: float, y: float
) -> str | None:
    """Stitch a clicked URL fragment with its neighbour rows (see
    linkpatterns.resolve_wrapped_url), or None to open what was clicked.

    The terminal's column count stands in for the emitter's wrap column, and
    is an upper bound on it: a CLI wrapping to a narrower measure only makes
    the row-full gate stricter, which costs a stitch and never buys a wrong
    one. Claude Code's own renderer stops a column or two short, which is
    what linkpatterns' slack is for."""
    screen = _screen_at(terminal, x, y)
    if screen is None:
        return None
    rows, row, col, cols = screen
    row_text = _row_reader(rows)
    for r in (row + slop for slop in _ROW_SLOP):
        row_txt = row_text(r)
        cand = candidate if candidate is not None else token_at_column(row_txt, col)
        if not cand:
            continue
        stitched = resolve_wrapped_url(
            cand,
            row_txt,
            [row_text(r - n) for n in range(1, STITCH_URL_ROWS + 1)],
            [row_text(r + n) for n in range(1, STITCH_URL_ROWS + 1)],
            cols,
        )
        if stitched is not None:
            return stitched
        if candidate is None:
            # The slop is for a click the row division put on the wrong side
            # of a row boundary, which shows up as an empty cell where the
            # click was. A cell that *did* hold a token settles which row was
            # clicked, and a neighbour row's token is then not the user's
            # click at all — prose one row under a wrapped link would
            # otherwise open the link.
            break
    return None


class _RootNameLinks:
    """_setup_links' bare-filename grammar: one extra match tag holding an
    alternation of the names actually sitting at the tab's project root
    (bare_names_pattern), so `README.md` underlines without the slash the
    path grammar demands. Files only — root directory names (docs, tests)
    are everyday prose words, and `docs/` is already the path grammar's.

    The root lives on the ancestor TerminalTab, which isn't an ancestor yet
    while either terminal kind is being constructed — so the tag is built on
    first map and not re-resolved on later ones (panel bottom↔right swaps
    re-map without reparenting tabs). The tab does push a new root in when
    its editor follows the session's working directory somewhere else, which
    is the only way this moves (`set_root`). A directory monitor keeps the
    alternation honest: on changes the names are re-listed and the tag
    swapped only when the set really changed.
    Change events coalesce on a 500ms timer armed by the first one — a
    leading-edge throttle, deliberately not a trailing-edge debounce, so an
    agent churning root files steadily can't starve the refresh; a rebuild
    that lands mid-burst is harmless (the next event re-arms the timer, and
    most events are content writes that leave the set alone anyway).
    Between snapshots the
    usual guarantees hold: a deleted file's click resolves nowhere and falls
    through unclaimed. The terminal's signal closures keep the instance
    alive; no one else needs to hold it."""

    def __init__(self, terminal: Vte.Terminal, tag_kinds: dict[int, str]) -> None:
        self._terminal = terminal
        self._tag_kinds = tag_kinds
        self._tab: TerminalTab | None = None
        self._root: str | None = None
        self._names: frozenset[str] | None = None
        self._tag: int | None = None
        self._monitor: Gio.FileMonitor | None = None
        self._refresh_source: int | None = None
        terminal.connect("map", self._on_map)
        terminal.connect("destroy", self._on_destroy)

    def _on_map(self, _terminal: Vte.Terminal) -> None:
        if self._root is not None:
            return  # re-mapped (panel swap); the tag and monitor live on
        tab = self._terminal.get_ancestor(TerminalTab)
        if tab is None:
            return
        self._tab = tab
        tab.register_root_name_links(self)
        self._rebuild(tab.link_root)

    def set_root(self, root: str) -> None:
        """Re-point at a different project root — the tab's editor followed the
        session's working directory somewhere new. Only ever called after the
        first map, so a root of None here means this instance never resolved a
        tab at all and has nothing to re-point."""
        if self._root is None or root == self._root:
            return
        self._rebuild(root)

    def _rebuild(self, root: str) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        self._root = root
        self._apply()
        try:
            self._monitor = Gio.File.new_for_path(self._root).monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
        except GLib.Error:
            return  # no monitor backend: the map-time snapshot still serves
        self._monitor.connect("changed", self._on_root_changed)

    def _on_root_changed(self, *_args) -> None:
        if self._refresh_source is None:
            self._refresh_source = GLib.timeout_add(500, self._refresh)

    def _refresh(self) -> bool:
        self._refresh_source = None
        self._apply()
        return GLib.SOURCE_REMOVE

    def _apply(self) -> None:
        names = self._file_names()
        if names == self._names:
            return
        self._names = names
        if self._tag is not None:
            self._terminal.match_remove(self._tag)
            del self._tag_kinds[self._tag]
            self._tag = None
        pattern = bare_names_pattern(names)
        if pattern is None:
            return
        try:
            regex = Vte.Regex.new_for_match(pattern, len(pattern.encode()), _PCRE2_MULTILINE)
        except GLib.Error:
            return  # VTE built without PCRE2 (the static tags are gone too)
        self._tag = self._terminal.match_add_regex(regex, 0)
        self._terminal.match_set_cursor_name(self._tag, "pointer")
        self._tag_kinds[self._tag] = "file"

    def _file_names(self) -> frozenset[str]:
        try:
            with os.scandir(self._root) as entries:
                # is_dir follows symlinks, so a directory behind a link is
                # excluded the same way a plain one is.
                return frozenset(entry.name for entry in entries if not entry.is_dir())
        except OSError:
            return frozenset()

    def _on_destroy(self, _terminal: Vte.Terminal) -> None:
        if self._tab is not None:
            self._tab.unregister_root_name_links(self)
            self._tab = None
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        if self._refresh_source is not None:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = None


def _open_file_reference(
    terminal: Vte.Terminal, path: str, line: int | None, col: int | None
) -> None:
    """Open a resolved file reference the way the reference deserves: images
    in the lightbox (any readable path, even outside the project — its own
    "Open in Editor" button handles the editor handoff), files inside the
    clicking tab's project in that tab's editor at the referenced line, and
    everything else — directories, outside-project files, no editor — in the
    default app, as `file:` URIs always opened."""
    if os.path.isfile(path):
        if editorfiles.is_image_path(path):
            _present_image(terminal, path)
            return
        tab = terminal.get_ancestor(TerminalTab)
        if tab is not None and tab.can_open_in_editor(path):
            # The window's action, not the tab directly: it also presents a
            # popped-out editor window and applies the pop-out-on-small-
            # screen policy. Line/col travel 1-based; 0 means none.
            terminal.activate_action(
                "win.open-in-editor", GLib.Variant("(sii)", (path, line or 0, col or 0))
            )
            return
    launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
    launcher.launch(terminal.get_root(), None, _on_link_launched)


def _present_image(terminal: Vte.Terminal, path: str) -> None:
    """A clicked file reference turned out to be an image: lightbox over the
    window instead of handing it to the default app. Its "Open in Editor"
    button only appears when the click's own tab could actually open the
    file (an editor exists and the path is inside its project) — routed
    through the window's `open-in-editor` action, which also handles a
    popped-out editor window."""
    tab = terminal.get_ancestor(TerminalTab)
    can_edit = tab is not None and tab.can_open_in_editor(path)
    on_open = None
    if can_edit:

        def on_open() -> None:
            terminal.activate_action(
                "win.open-in-editor", GLib.Variant("(sii)", (path, 0, 0))
            )

    present_image_lightbox(
        terminal, path, can_open_in_editor=can_edit, on_open_in_editor=on_open
    )


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


def _prompt_read(prompt: EnteredPrompt | None) -> tuple[str, int] | None:
    """What two settle reads of the CLI's input box compare, so that "the
    box is still empty" counts as agreement too (see _settle_cut)."""
    return None if prompt is None else (prompt.text, prompt.rows_below)


class PrChipRow(Gtk.Widget):
    """The footer's PR chips: as many as fit, and the newest ones are the ones.

    A box would insist on its full width and push the footer's buttons off the
    end of the window; this drops whole chips off the *front* of the row when
    it is short of room, so what's left still reads oldest-to-newest and still
    ends with the PR the session is working on now. A dropped chip comes back
    the moment the window is wide enough for it again.

    It only ever holds a handful of small labels, so measuring them on every
    allocation is cheaper than caching would be.

    Whether any chip is currently dropped is reported through
    *on_overflow_changed*, so the footer can show a way to the full list
    exactly when the row isn't it.
    """

    def __init__(self, spacing: int, on_overflow_changed: Callable[[bool], None]) -> None:
        super().__init__()
        self._spacing = spacing
        self._on_overflow_changed = on_overflow_changed
        self._overflowing = False

    def set_chips(self, chips: list[Gtk.Widget]) -> None:
        """Replace the row's chips, oldest first.

        Resets overflow without reporting: the caller is rebuilding the row
        and hides its overflow button itself; the next allocation reports
        again if the new chips don't fit either.
        """
        self._overflowing = False
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
        self._report_overflow(len(keep) < len(chips))
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

    def _report_overflow(self, overflowing: bool) -> None:
        if overflowing == self._overflowing:
            return
        self._overflowing = overflowing
        # From inside an allocation, where a sibling's visibility must not
        # change (the box would allocate again mid-allocation) — so the report
        # waits for idle. Reading the flag then, not capturing it now, keeps a
        # stale queued report from contradicting a newer one.
        GLib.idle_add(self._deliver_overflow)

    def _deliver_overflow(self) -> bool:
        self._on_overflow_changed(self._overflowing)
        return GLib.SOURCE_REMOVE

    def do_dispose(self) -> None:
        while (child := self.get_first_child()) is not None:
            child.unparent()
        Gtk.Widget.do_dispose(self)


class PanelTerminal(Gtk.Box):
    """The tab's secondary terminal: a plain shell with no agent auto-launched.

    Spawns lazily the first time it is shown and survives hide/show and
    bottom↔right swaps; the shell is only lost when the tab itself closes.
    The shell kind of PanelPage (see panelstrip): *number* is the 1-based
    ordinal its tab title shows."""

    page_kind = "shell"

    __gsignals__ = {
        # Emitted when the panel's shell exits (e.g. the user typed `exit`).
        "shell-exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when the shell's terminal rings BEL (a PanelPage may emit
        # this; the strip re-emits it for the window's visual bell).
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, number: int = 1) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._number = number
        # The persistent panel-history ordinal this shell saves its
        # scrollback under (see panelhistory); assigned by the dock's shell
        # factory, overwritten by layout restore. Never renumbered — pages
        # move between strips, so a positional key would drift.
        self.hist = 0
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
        self.terminal.connect("bell", lambda *_: self.emit("bell"))
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
        # The line reset clears any half-typed input before the cd; see
        # shellinput for what else can be sitting on that line.
        self.terminal.feed_child(shell_command(f"cd {shlex.quote(cwd)}\n").encode())

    def has_running_command(self) -> bool:
        return _has_running_command(self.terminal, self._child_pid)

    def clear(self) -> None:
        """Wipe the screen and scrollback; a running shell keeps running and
        is nudged to repaint its prompt (\\x0c = Ctrl+L)."""
        self.terminal.reset(False, True)
        if self._spawned:
            self.terminal.feed_child(b"\x0c")

    def page_state(self) -> dict:
        """This shell's slot in a serialized dock layout (see panellayout):
        its kind plus the history ordinal its scrollback file is keyed by."""
        return {"kind": "shell", "hist": self.hist}

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

    # -- PanelPage protocol (see panelstrip) -------------------------------

    def page_title(self) -> str:
        return _("Terminal {number}").format(number=self._number)

    def page_icon(self) -> str | None:
        return None

    def grab_page_focus(self) -> None:
        self.terminal.grab_focus()

    def has_page_focus(self) -> bool:
        return self.terminal.has_focus()

    def page_busy(self) -> bool:
        return self.has_running_command()

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
        # Emitted when rotating a tab re-homed the panel: "bottom" | "right",
        # the edge the window persists as the app-wide default (as it does
        # for the whole-panel swap win.swap-panel fires).
        "panel-position-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Emitted when either of the tab's terminals rings BEL, for the
        # window's visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when the PRs on the footer row change (object = their
        # prstatus records, oldest first), so the window can save them against
        # the session. Never fires for a tab that has nothing to say yet.
        "prs-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Emitted whenever the chips are rebuilt — a check went red, a PR
        # merged, a new one turned up — so the session's sidebar row can
        # re-read its own mark from the status this tab just fetched. Separate
        # from prs-changed because the two questions are: prs-changed asks
        # "does the saved list need writing?", this one asks "does anything
        # else showing this session need redrawing?" — and a rebuild that
        # changed no record (a fetch that came back the same) still redraws.
        "pr-status-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
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
        self._baselined_dirs: set[str] = set()  # dirs whose pre-existing transcripts are excluded
        self._known_transcripts: set[Path] = set()  # transcripts predating this tab
        self._updating = False  # an off-thread transcript parse is in flight
        # Every _RootNameLinks watching a terminal inside this tab — the agent's
        # and one per panel shell — so a re-root can re-point them all. They
        # register themselves the first time they map, which is after this
        # constructor returns; it exists this early only so the _setup_links
        # calls below can never race it.
        self._root_name_links: list[_RootNameLinks] = []

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
        # The colour plain text is drawn in, once a theme has been applied;
        # None while the terminal is following the system colours. Read when
        # telling an agent's dim ghost text from typing (see _tail_is_dim).
        self._terminal_fg: tuple[int, int, int] | None = None
        self._setup_context_menu()
        self._setup_image_drop()

        self._search_bar = self._build_search_bar()
        self.append(self._search_bar)

        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Floating composer button over the terminal's bottom-left corner —
        # opens the composer panel (a GUI prompt box, see collins/composer.py)
        # right where the CLI's own input box lives. Overlaid on the
        # scrolled terminal itself, inside the width clamp below, so it hugs
        # the corner of the terminal *content* — not the tab — when the clamp
        # centers a width-limited terminal between gutters. Shown only for
        # providers with an input box Collins can cut a prompt out of (the
        # clear_prompt_keys probe; base agents return None); whether the
        # agent is actually running is checked at click time, like "Add to
        # chat" (see add_file_to_chat).
        self._composer_overlay_btn = Gtk.Button(
            icon_name="document-edit-symbolic",
            halign=Gtk.Align.START,
            valign=Gtk.Align.END,
            margin_start=5,
            margin_bottom=5,
            tooltip_text=_("Open composer (Ctrl+.)"),
        )
        self._composer_overlay_btn.add_css_class("attach-overlay")
        self._composer_overlay_btn.connect("clicked", lambda *_: self.open_composer())
        self._composer_overlay_btn.set_visible(self._provider_has_prompt_box())
        # The composer itself is built lazily on first open (_ensure_composer);
        # only its future overlay slot exists up front.
        self._composer: ComposerView | None = None
        self._composer_revealer: Gtk.Revealer | None = None
        self._composer_page: ComposerPage | None = None  # set while docked
        # The text an open-cut took out of the CLI's box and hasn't proved
        # gone yet — what a leftover has to match before anything erases it
        # (see _verify_cut) — and the number of the cut that took it, which
        # anything writing to that box on its own account bumps to call the
        # rounds still in flight off.
        self._cut_pending: str | None = None
        self._cut_seq = 0
        # Whether a cut is still deciding what the box holds, and a send that
        # arrived while it was (see _on_composer_send).
        self._cut_settling = False
        self._send_after_settle = False
        self._composer_enter_sends = True
        self._composer_font = ""
        # Counts up only while a new session set to open floating waits for
        # its agent (see autoshow_composer).
        self._composer_autoshow_tries = 0
        self._content_overlay = Gtk.Overlay(child=scrolled)
        content_overlay = self._content_overlay
        content_overlay.add_overlay(self._composer_overlay_btn)

        # Past "terminal_max_width", the clamp stops growing the terminal and
        # centers it instead; see _apply_terminal_max_width. Unset until
        # apply_settings runs, so it must start unconstrained rather than at
        # Adw.Clamp's own default (600px).
        self._width_clamp = Adw.Clamp(
            child=content_overlay,
            hexpand=True,
            vexpand=True,
            maximum_size=_UNLIMITED_CLAMP_WIDTH,
            tightening_threshold=_UNLIMITED_CLAMP_WIDTH,
        )

        # The terminal is the single live view. The "terminal-gutter" class
        # paints the space the clamp leaves beside the terminal, kept in step
        # with the terminal's own theme (themes.py).
        self._overlay = Gtk.Overlay()
        self._overlay.set_vexpand(True)
        self._overlay.add_css_class("terminal-gutter")
        self._overlay.set_child(self._width_clamp)

        # The panel dock — strips of panel pages (shells, for now) split
        # around the agent terminal in a tree of fixed-axis paneds. Pages
        # move between strips by reparenting, so shells keep running; the
        # old bottom↔right orientation flip is gone (swap now *moves* the
        # shells to the other home edge). Divider sizes ride per-paned
        # PanedSizers inside the dock; the home strip's re-emits here as
        # panel-size-changed so the window can persist app-wide defaults.
        panel_right = bool(settings) and settings.get("panel_position") == "right"
        self._dock = PanelDock(
            terminal=self._overlay,
            strip_factory=self._make_panel_strip,
            home_position="right" if panel_right else "bottom",
        )
        self._dock.connect("bell", lambda *_: self.emit("bell"))
        self._dock.connect(
            "size-changed", lambda _d, mode, size: self.emit("panel-size-changed", mode, size)
        )
        self._dock.connect(
            "home-changed", lambda _d, mode: self.emit("panel-position-changed", mode)
        )
        self._dock.set_focus_terminal(self.grab_terminal_focus)
        self._dock.set_page_factory(self._make_panel_page)

        # Editor panel: a full-height right column beside the terminal↔shell
        # split above, in a new outer paned. Built now (but hidden) rather
        # than on first toggle, so per-session restore can reopen it without
        # a construct-on-demand race.
        editor_root = cwd if cwd and Path(cwd).is_dir() else str(Path.home())
        # Where bare root-name links look (_RootNameLinks): the directory the
        # editor opens at. That includes the HOME fallback above,
        # deliberately: when a project dir is gone this whole tab is already
        # rooted at home — the editor, quick open, and click-time resolution
        # — so bare names follow suit rather than becoming the one link kind
        # that goes dark.
        self.link_root: str = editor_root
        self._editor = editor.EditorPane(editor_root)
        self._editor_detached = False  # pane reparented into its own EditorWindow
        self._outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self._outer.set_wide_handle(True)
        self._outer.set_resize_start_child(True)
        self._outer.set_shrink_start_child(False)
        self._outer.set_resize_end_child(False)
        self._outer.set_shrink_end_child(False)
        self._outer.set_start_child(self._dock)
        self._editor.set_visible(False)
        self._outer.set_end_child(self._editor)
        self._editor.connect(
            "request-pop-out", lambda *_: self.emit("editor-pop-out-requested")
        )
        self._editor.connect("add-to-chat", self._on_editor_add_to_chat)
        self._editor.connect("root-changed", self._on_editor_root_changed)
        # Following the agent's working directory (see _maybe_follow_editor):
        # the cwd it has to hold still at, how many polls it has held it for,
        # and the last one already acted on — offered and declined counts as
        # acted on, so a banner ignored doesn't come back every two seconds.
        self._editor_follow_pending: str | None = None
        self._editor_follow_ticks = 0
        self._editor_follow_settled: str | None = None
        # The editor paned's counterpart to the dock's sizers — one fixed key,
        # since the editor column only ever has the one position. Its
        # size-changed re-emits as editor-size-changed (minus the key).
        self._outer_sizer = PanedSizer(
            self._outer, key=lambda: "editor", occupied=lambda: self.editor_visible
        )
        self._outer_sizer.connect(
            "size-changed", lambda _s, _key, size: self.emit("editor-size-changed", size)
        )
        self.append(self._outer)

        self._footer_cwd: str | None = None  # last value shown in the footer
        self._footer_branch: str | None = None
        self._footer_model: str | None = None  # model id, as the transcript writes it
        # Every PR this session has opened, oldest first: url -> PR with the
        # last status known for it (see _collect_prs). Replaced wholesale,
        # never mutated in place — the update thread reads it while the main
        # loop writes it.
        self._tracked_prs: dict[str, PullRequest] = {}
        self._restored_prs: list[PullRequest] = []  # this session's, from a previous run
        # PRs the session named itself, via the attach_pr tool. Folded into
        # every collection rather than written into _tracked_prs, which an
        # in-flight update replaces wholesale when it lands (see attach_pr).
        # Replaced wholesale too, for the same thread-safety reason.
        self._attached_prs: dict[str, PullRequest] = {}
        self._footer_prs: list[PullRequest] = []  # what the chips currently show
        self._saved_pr_records: list[dict] = []  # last records handed to the window
        self._pr_discover = False  # a click's search, waiting for a free tick
        self._pr_focus_refresh_at = 0  # last time coming into view forced a refetch
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
        if session_id is not None:
            state = recreatable_worktree(self._transcript.path, cwd or "")
            if state is not None:
                # The CLI reaped this session's worktree when it last exited
                # (it deletes untouched ones); resuming without it would
                # relocate the session out of the worktree for good — it
                # re-enters one it can still find, wherever the shell starts.
                # Recreate it first — same path, branch, base commit — so the
                # resume lands back where the session left off. Off the main
                # loop: `git worktree add` checks out a whole working tree.
                # Until it finishes, readers see the worktree cwd and no
                # initial command, same as a tab whose shell hasn't spawned.
                worktree = str(state["worktreePath"])
                self._cwd = worktree
                self._initial_command = None
                self.feed_message(_("recreating removed worktree {path}").format(path=worktree))

                def recreate() -> None:
                    recreate_worktree(state)
                    # Back to the directory the tab was handed once it exists
                    # again — an agent that had moved into a subdirectory of
                    # the worktree resumes there — and to the worktree itself
                    # for a tab handed somewhere outside it (the repository
                    # root a session started in before entering the worktree).
                    # _finish_spawn re-checks the directory; on failure it
                    # falls back with its usual warning.
                    inside = cwd is not None and _within(worktree, cwd) and Path(cwd).is_dir()
                    GLib.idle_add(self._finish_spawn, cwd if inside else worktree, session_id)

                threading.Thread(target=recreate, daemon=True).start()
                return
        self._finish_spawn(cwd, session_id)

    def _finish_spawn(self, cwd: str | None, session_id: str | None) -> None:
        if cwd is None or not Path(cwd).is_dir():
            if cwd is not None:
                # A worktree that couldn't be put back still belongs to a
                # repository: start there rather than in HOME, which is where
                # the CLI relocates the session anyway.
                root = worktree_project_root(cwd)
                fallback = root if root and Path(root).is_dir() else str(Path.home())
                self.feed_message(
                    _("warning: project dir {cwd} no longer exists, starting in {fallback}").format(
                        cwd=cwd, fallback=fallback
                    )
                )
                cwd = fallback
            else:
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
        # Which model the session is answering with — the first thing the row
        # says about the session itself, ahead of where it is working. Read
        # off the transcript, which stamps every reply with its model, so a
        # `/model` switch mid-session shows up here within a poll; the short
        # name is what shows and the id itself is the tooltip (and the copy).
        # Its divider trails it, so a session too new to have replied yet
        # leaves neither a label nor a gap behind (see _sync_footer_seps).
        self._model_label = Gtk.Label(xalign=0.0)
        # A name is a couple of words; an id Collins can't shorten (a model
        # newer than this build) is the long case, and it gives up its tail
        # rather than the working directory's.
        self._model_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._model_label.set_max_width_chars(20)
        self._model_label.add_css_class("caption")
        self._model_label.add_css_class("dim-label")
        self._model_label.set_visible(False)
        enable_copy_on_click(self._model_label, lambda: self._footer_model, short_name)
        self._model_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        self._model_sep.set_visible(False)

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
        self._pr_chips = PrChipRow(_PR_CHIP_SPACING, self._on_pr_overflow_changed)
        # Leading the row, where the oldest dropped chip would be: an ellipsis
        # opens the full list, titles and all. It only shows once the row has
        # had to drop a chip (PrChipRow reports that), because that is when
        # the list holds something the chips don't — with every chip on show
        # it would only repeat them.
        # The list itself is shared with the sidebar's own PR button (prmenu);
        # the footer is at the bottom of the tab, so this copy opens upwards.
        self._pr_menu = prmenu.new_popover(Gtk.PositionType.TOP)
        menu_icon = Gtk.Image.new_from_icon_name("view-more-horizontal-symbolic")
        menu_icon.set_pixel_size(_PR_REFRESH_ICON_PX)
        menu_icon.add_css_class("dim-label")
        self._pr_menu_btn = Gtk.MenuButton(child=menu_icon, popover=self._pr_menu)
        self._pr_menu_btn.add_css_class("flat")
        self._pr_menu_btn.set_tooltip_text(_("Every pull request this session has opened"))
        self._pr_menu_btn.set_create_popup_func(self._fill_pr_menu)
        self._pr_menu_btn.set_visible(False)
        # The ellipsis stands in for chips, so it sits with them in a box of
        # their own, tighter than the footer's 8px rhythm: the button's
        # min-width already leaves ~6px of air around its dots, and the full
        # spacing on top of that read as a hole in the row. The box is what
        # comes and goes with the PR list — one visibility for the group,
        # so an empty group never costs the footer a spacing gap.
        self._pr_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._pr_group.set_visible(False)
        self._pr_group.append(self._pr_menu_btn)
        self._pr_group.append(self._pr_chips)
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

        # model, cwd, branch and PRs sit together on the left; the wrapper box
        # (not the cwd label) takes the slack so the buttons stay pinned right
        # even while the model, branch and PR labels are hidden.
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, hexpand=True)
        left.append(self._model_label)
        left.append(self._model_sep)
        left.append(self._cwd_label)
        left.append(self._branch_seps[0])
        left.append(self._branch_label)
        left.append(self._branch_seps[1])
        left.append(self._pr_group)
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
        self._editor_toggle_btn.set_tooltip_text(_("Show editor panel"))

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
        self._maybe_follow_editor(cwd)
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

    def _maybe_follow_editor(self, cwd: str | None) -> None:
        """Keep the editor pointed at wherever the agent is actually working.

        Rides the footer's cwd poll rather than adding one of its own — the
        value is already in hand — but acts on far less of it: the agent's cwd
        is read from a live process tree, and it flaps. A worktree launch moves
        it before the first prompt; a restarted background job forks a fresh
        process at the old directory; between the CLI exiting and the shell
        being read the fallback answer is the directory the tab started in. So
        a new directory has to hold still across consecutive polls before it
        counts as a move, and each settled answer is acted on exactly once —
        an offer the user ignored must not come back every two seconds.

        Where the agent went decides how far this goes: still inside the same
        project (a worktree, most often) and the editor simply follows;
        anywhere else and it only offers. See `editorfiles.follow_scope`."""
        root = str(self._editor.root)
        if cwd != self._editor_follow_pending:
            self._editor_follow_pending = cwd
            self._editor_follow_ticks = 1
            return
        self._editor_follow_ticks += 1
        if self._editor_follow_ticks < _EDITOR_FOLLOW_TICKS or cwd == self._editor_follow_settled:
            return
        scope = editorfiles.follow_scope(root, cwd)
        if scope is editorfiles.FollowScope.NONE:
            # Back where it already was — including the fallback the tab
            # started at, which is how leaving a worktree usually reads.
            self._editor_follow_settled = None
            return
        self._editor_follow_settled = cwd
        if scope is editorfiles.FollowScope.AUTO:
            self._editor.request_root(cwd)
        else:
            self._editor.offer_root(cwd)

    def register_root_name_links(self, links: _RootNameLinks) -> None:
        """Called by a `_RootNameLinks` the first time it resolves this tab, so
        a later re-root can reach every one of them — the agent terminal's and
        one per shell in the panel."""
        if links not in self._root_name_links:
            self._root_name_links.append(links)

    def unregister_root_name_links(self, links: _RootNameLinks) -> None:
        if links in self._root_name_links:
            self._root_name_links.remove(links)

    def _on_editor_root_changed(self, _pane, root: str) -> None:
        """The editor moved to a new project directory: everything else in the
        tab that was rooted at the old one moves with it — bare root-name link
        matching, and the fallback the click-time path resolver and quick open
        read (`link_root`)."""
        self.link_root = root
        for links in list(self._root_name_links):
            links.set_root(root)

    def _refresh_model_label(self) -> None:
        """Name the model the session last answered with, or hide the label
        until it has. Called wherever the transcript has just been read."""
        model = self._transcript.model()
        if model == self._footer_model:
            return
        self._footer_model = model
        self._model_label.set_text(short_name(model) if model else "")
        self._model_label.set_tooltip_text(copy_tooltip(model) if model else None)
        self._model_label.set_visible(model is not None)
        self._sync_footer_seps()

    def _sync_footer_seps(self) -> None:
        """Show only the dividers that separate two visible chips.

        The PR group ends in a button that is always there, so its own dividers
        never come and go; the model and the branch are the chips that do, and
        each is paired with the divider on the side away from the start of the
        row — so the run never opens or closes with a stray divider. The one
        ahead of the branch does double duty, standing in for the cwd's own.
        """
        branch = self._footer_branch is not None
        cwd = self._footer_cwd is not None
        self._model_sep.set_visible(self._footer_model is not None)
        self._branch_seps[0].set_visible(cwd)
        self._branch_seps[1].set_visible(branch)

    def _build_pr_chip(self, pr: PullRequest) -> Gtk.Widget:
        """One PR's chip: its state-and-status mark, then its number.

        The mark is the same two-icon overlay the menus use (see
        prmenu.status_icon) — the base icon's color is what the eye picks up
        without reading the row, and the badge on its corner is the one thing
        the PR needs doing. Icon before number, the way GitHub writes a PR.

        Every part of a chip answers for that PR and nothing else — the chips
        are siblings on the row, so each number carries its own page (on a
        click) and its own menu (on a right-click). Reading the PR is what a
        chip is usually clicked for, so it is what the plain click does; the
        things to *do* about it — the browser among them — sit one right-click
        away, the way a context menu sits behind anything else on the row.
        """
        number = Gtk.Label(label=f"#{pr.number}")
        number.add_css_class("caption")
        number.add_css_class("dim-label")
        chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        # Centered, not stretched: the badge sits at the bottom of the mark's
        # allocation, and a mark left to fill the footer row would put it flush
        # against the window's bottom edge. The base icon draws centered either
        # way, so only the badge moves — up, off the edge.
        mark = prmenu.status_icon(pr)
        mark.set_valign(Gtk.Align.CENTER)
        chip.append(mark)
        chip.append(number)
        chip.set_tooltip_text(
            describe(pr) + "\n" + pr.url + "\n"
            + _("Click to view in Collins") + "\n" + _("Right-click for actions")
        )
        # The chip is the shortest way to read a PR: its page docked beside the
        # session, on a plain click.
        prmenu.attach_view(chip, pr, self.open_pr_page)
        # And the shortest way to do something about it: the same actions the
        # footer's PR list offers, opened on the chip itself.
        prmenu.attach_actions(chip, pr, self._pr_action_host())
        return chip

    def _pr_action_host(self) -> prmenu.ActionHost:
        """How a PR's actions reach this tab: it is the session they belong to.

        The chips and the footer's PR list are the tab's own, so "can this session
        take a prompt?" is a question the tab answers about itself — a tab
        whose agent has exited, or whose prompt is half-written, keeps its
        chips and its PRs, but is not somewhere to send anything.
        """
        return prmenu.ActionHost(
            prompt_block=self.prompt_block,
            has_changes=lambda: has_changes(self.current_agent_cwd()),
            send_prompt=self.inject_prompt,
            refresh=self._request_update,
            view_pr=self.open_pr_page,
            view_unresolved=lambda pr: self.open_pr_page(pr, unresolved=True),
        )

    def _fill_pr_menu(self, _button: Gtk.MenuButton) -> None:
        """Build the ellipsis button's list, just before it opens.

        Nothing to fetch here, unlike the sidebar's copy of this menu: the
        footer's own poll is already refreshing every PR on the row, so what
        the tab is holding when the list is opened is current.
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
        self._pr_group.set_visible(bool(prs))
        # Hidden until the rebuilt row reports whether these chips fit; with
        # no PRs at all the hidden group never allocates, so this is also what
        # retires the button when the last chip goes.
        self._pr_menu_btn.set_visible(False)
        self._remember_prs(prs)
        # After the save, never before it: the sidebar row rebuilds its mark
        # from this session's saved list plus the status now sitting in
        # prstatus, so a list that just gained a PR has to be on disk by the
        # time the row goes looking.
        self.emit("pr-status-changed")
        self._sync_pr_refresh_tooltip()
        self._sync_footer_seps()

    def _on_pr_overflow_changed(self, overflowing: bool) -> None:
        """Show the ellipsis only while the chip row is short of room.

        Showing it costs the row its own width, and hiding it gives that back
        — but neither flips the answer: a row that overflowed keeps
        overflowing with less room, and one that fit still fits with more, so
        the button never oscillates.
        """
        self._pr_menu_btn.set_visible(overflowing)

    def _remember_prs(self, prs: list[PullRequest]) -> None:
        """Hand the row's PRs to the window, which saves them for this session.

        Status goes with them (see prstatus.to_record), so this fires for a
        check that turned red as well as for a PR that turned up — but only
        when the records actually differ, so a poll that came back with the
        same answer writes nothing. A session that has opened no PRs never
        emits at all.
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

    def attach_pr(self, pr: PullRequest) -> bool:
        """Adopt a PR named from outside the transcript — the attach_pr
        session tool. False when the tab already tracks it.

        Kept in a dict of its own rather than written into _tracked_prs: an
        update already in flight when the call lands replaces that wholesale,
        so a direct write could be lost. _collect_prs folds these in on every
        pass instead, and the update requested here gets the new chip its
        title and status.
        """
        if pr.url in self._tracked_prs or pr.url in self._attached_prs:
            return False
        self._attached_prs = {**self._attached_prs, pr.url: pr}
        self._request_update()
        return True

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

    def refresh_pr_statuses(self) -> None:
        """Mark every chip's status due, so the update that follows refetches it.

        The window calls this when the tab comes back into view — selected, or
        its window refocused — because that's exactly when a stale mark is
        looked at: CI that finished while the user was away should show up on
        arrival, not up to a TTL later. Only status is re-asked; discovering a
        *new* PR stays with the refresh button, whose click says to go look.

        Throttled per tab, and a tab with nothing unmerged doesn't start the
        clock — its first PR still deserves an on-arrival refresh later.
        """
        if all(pr.merged for pr in self._footer_prs):
            return
        now = GLib.get_monotonic_time()
        if now - self._pr_focus_refresh_at < _PR_FOCUS_REFRESH_MIN_US:
            return
        self._pr_focus_refresh_at = now
        for pr in self._footer_prs:
            if not pr.merged:
                invalidate(pr.url)
        self._request_update()

    # -- graceful close ----------------------------------------------------

    def feed_child_text(self, text: str) -> None:
        self.terminal.feed_child(text.encode())

    def _on_editor_add_to_chat(self, _pane, path: str, start_line: int, end_line: int) -> None:
        self.add_file_to_chat(path, start_line, end_line)

    def add_file_to_chat(self, path: str, start_line: int = 0, end_line: int = 0) -> None:
        """The editor's "Add to chat" (a right-clicked selection or file):
        type the agent's mention token for *path* into the input box —
        typed, never submitted, so the user says what they want done with
        it. The trailing space both
        terminates the CLI's mention token and leaves the cursor ready for
        that sentence; the leading one keeps the token off the end of a
        sentence already being written (see _mention_leading_space).

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
        # An open composer *is* the input box right now — the CLI's own was
        # emptied into it — so every attach entry point lands there instead.
        if self.composer_open():
            self._composer.insert_mention(reference + " ")
            return
        self.feed_child_text(self._mention_leading_space() + reference + " ")
        GLib.idle_add(self._focus_terminal_after_add_to_chat)

    def _mention_leading_space(self) -> str:
        """A space to put in front of a mention about to be typed, when the
        input box has a sentence in it already (dropimages.leading_space
        decides; this finds what it reads).

        That is the line the cursor is on, up to the cursor — the same
        screen `takes_prompt` reads, but read differently: this question is
        asked mid-sentence, where the prompt marker is no longer the last
        thing on the line, so what counts is the character immediately
        before the cursor rather than where the marker sits. A cursor at
        column 0 has nothing before it to read.
        """
        column, row = self.terminal.get_cursor_position()
        if column <= 0:
            return ""
        return dropimages.leading_space(self._row_text(row, column), column)

    def _row_text(self, row: int, end_column: int) -> str:
        """What row *row* says from its start up to *end_column* (exclusive).

        The column is a count of cells, not of characters — a wide character
        advances it by two — so callers comparing the two have to say which
        they mean (dropimages.cell_width). Trailing cells that were never
        written aren't reported at all, which is how a cursor sitting past
        the end of a line gives a string shorter than its own column.
        """
        line = self.terminal.get_text_range_format(Vte.Format.TEXT, row, 0, row, end_column)
        text = line[0] if isinstance(line, tuple) else line
        return text or ""

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
        never submitted, mirroring "Add to chat" (see add_file_to_chat for
        why the spaces around them and the missing takes_prompt gate). Paths
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
        # Drops follow the live input box: with the composer open the CLI's
        # box is empty by construction, and a mention typed into it would be
        # stranded there when the composer's text comes back over it.
        if self.composer_open():
            self._composer.insert_mention(text)
            return True
        self.feed_child_text(self._mention_leading_space() + text)
        self.grab_terminal_focus()
        return True

    # -- composer ------------------------------------------------------------

    def _provider_has_prompt_box(self) -> bool:
        """Whether this provider has an input box Collins can read and
        clear — what the composer's open-cut needs, probed with a dummy
        prompt (base agents answer None to any clear)."""
        probe = EnteredPrompt(text="x", rows_below=0)
        return self.provider.clear_prompt_keys(probe) is not None

    def composer_open(self) -> bool:
        """The routing predicate: attach entry points (drops, "Add to
        chat", the attach button) land mentions in the composer whenever
        it is up — raised over the terminal, or docked as a panel page."""
        return self._composer_page is not None or (
            self._composer_revealer is not None
            and self._composer_revealer.get_reveal_child()
        )

    def composer_focused(self) -> bool:
        """Whether the keyboard focus is inside an open composer right now
        — what the toggle shortcut branches on, so the chord that raises
        the panel lowers it only once the cursor has arrived there (see
        the window's `_toggle_composer`)."""
        if self._composer is None or not self.composer_open():
            return False
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return focus is not None and (
            focus is self._composer or focus.is_ancestor(self._composer)
        )

    def _ensure_composer(self) -> ComposerView:
        """Build the composer and its overlay slot on first use. The
        revealer rides content_overlay — the same width-clamped overlay as
        the floating button — anchored to the bottom edge, so the panel
        rises exactly over the CLI's own input box."""
        if self._composer is not None:
            return self._composer
        self._composer = ComposerView(
            pick_attach=self._pick_file_for_composer,
            # The view is its own drop target (see _setup_drop there); it
            # borrows this tab's provider and cwd through these, read live
            # at each call so a cwd change between drops is honored.
            file_reference=lambda path: self.provider.file_reference(
                path, self.current_agent_cwd()
            ),
            notify=self.feed_message,
        )
        self._composer.set_enter_sends(self._composer_enter_sends)
        self._composer.set_font(self._composer_font)
        self._composer.connect("send-requested", self._on_composer_send)
        self._composer.connect("close-requested", lambda *_a: self.close_composer())
        self._composer.connect(
            "dock-toggle-requested", lambda *_a: self._toggle_composer_dock()
        )
        revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            valign=Gtk.Align.END,
            child=self._composer,
            visible=False,
        )
        # Once the hide transition finishes, stop occupying the overlay:
        # an invisible-but-revealable widget would still shadow the
        # terminal's bottom edge from pointer events.
        revealer.connect("notify::child-revealed", self._on_composer_revealed)
        self._composer_revealer = revealer
        self._content_overlay.add_overlay(revealer)
        return self._composer

    def _on_composer_revealed(self, revealer: Gtk.Revealer, _pspec) -> None:
        if not revealer.get_child_revealed():
            revealer.set_visible(False)

    def open_composer(self) -> None:
        """The floating button: raise the composer over the terminal,
        seeded with whatever was typed-but-unsent in the CLI's input box
        (cut, not copied — the box empties so a later send can't double
        it). A box that can't be read right now — empty, or the agent
        mid-turn drawing something else — seeds nothing and opens anyway:
        composing while the agent works is half the point.

        The seeding lands a beat after the panel does, because a cut can't
        be taken off a screen that is still moving (see _begin_cut); the
        composer opens on the spot either way."""
        if self._composer_page is not None:
            # One composer per tab: docked, the button fronts the page —
            # revealing a hidden home strip if that's where it lives —
            # rather than raising a second box over the terminal.
            self._dock.reveal_page(self._composer_page)
            return
        if not self._agent_is_running():
            self.feed_message(_("Composer: the agent isn't running in this tab"))
            return
        if self.composer_open():
            self._composer.focus_view()
            return
        composer = self._ensure_composer()
        composer.set_text("")
        revealer = self._composer_revealer
        revealer.set_visible(True)

        # Revealed from an idle after the (fresh) child maps, or the first
        # open skips its slide and just appears (the sidebar's lesson).
        def reveal() -> bool:
            revealer.set_reveal_child(True)
            composer.focus_view()
            # The cut starts from here rather than above it: every round of
            # it asks whether the composer is still open before touching
            # the CLI's box, and until this line it isn't.
            self._begin_cut(composer)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def autoshow_composer(self, setting) -> None:
        """Open this tab's composer without being asked, the way the
        composer_new_sessions setting says. Called by the window on the
        sessions it starts fresh, and on nothing else: a resumed session
        brings back the panel layout it was closed with, which already has
        the answer for that session.

        "dock" lands the composer as a panel page below the terminal right
        away — docking reads nothing off the screen and needs no agent — and
        that page joins this session's saved layout, so a session that
        started with a docked composer keeps one when it is resumed.

        "float" raises it over the terminal instead, which *does* need the
        agent: the open cuts whatever is in the CLI's input box, and a
        composer over a plain shell could only paste a draft back into
        something that would run it. So it waits for the CLI to come up in
        the shell just spawned, and gives up quietly if it never does.

        Either way the floating button's provider gate applies — an agent
        whose input box Collins can't read has no composer to offer — and a
        composer the user opened first is left alone.
        """
        mode = composerkeys.autoshow_mode(setting)
        if mode == composerkeys.OFF or not self._provider_has_prompt_box():
            return
        if mode == composerkeys.DOCK:
            self.dock_composer()
            return
        self._composer_autoshow_tries = 0
        GLib.timeout_add(_COMPOSER_AUTOSHOW_POLL_MS, self._autoshow_composer_tick)

    def _autoshow_composer_tick(self) -> bool:
        """Wait out the agent's start, then raise the floating composer.
        Stops at the first sign the moment has passed: the tab is gone, the
        wait ran out, or a composer is up already — the user got there first
        (the corner button, Ctrl+., a file dropped on the terminal), and
        nothing of theirs should be re-raised over."""
        # Counted before the checks, so the last tick of the window is the one
        # that gives up: _COMPOSER_AUTOSHOW_TRIES ticks is the whole wait.
        self._composer_autoshow_tries += 1
        done = (
            self.get_root() is None
            or self._composer_autoshow_tries >= _COMPOSER_AUTOSHOW_TRIES
            or self.composer_open()
        )
        if not done and not self._agent_is_running():
            return GLib.SOURCE_CONTINUE
        if not done:
            self.open_composer()
        return GLib.SOURCE_REMOVE

    def close_composer(self, restore: bool = True) -> None:
        """Lower the composer. Its text goes back where it came from —
        typed into the CLI's box, unsubmitted (multi-line arrives as one
        paste chunk, whose newlines are line breaks in the box) — unless
        *restore* is False (sending already emptied it) or the agent has
        left the terminal, which quietly discards: what's there now is a
        shell prompt, where a pasted draft isn't a draft, it's commands
        (a shell without bracketed paste runs each line as it lands).

        Docked, the same request funnels through the page's tab close
        (busy-ask and all): the strip's `page_closed` hook lands in
        `_on_composer_page_closed`, which rescues the view and applies the
        identical paste-back rules."""
        if self._composer_page is not None:
            self._dock.close_page(self._composer_page)
            return
        if not self.composer_open():
            return
        text = self._composer.take_text()
        self._cut_pending = None  # the box is about to hold this text again
        self._cut_seq += 1
        self._composer_revealer.set_reveal_child(False)
        if restore and self._agent_is_running():
            restored = composerkeys.restore_text(text)
            if restored:
                self.feed_child_text(restored)
        self.grab_terminal_focus()

    def _on_composer_send(self, _view, text: str) -> None:
        """Send closes first, then submits — the panel is a stand-in for
        the CLI's input box, and the submitted prompt should land in view,
        not behind a panel. Nothing but whitespace just closes. Not
        re-gated on takes_prompt: the box was emptied at open, and anything
        typed into the terminal since submits along with this, same as if
        the user had pressed Enter there. It IS re-gated on the agent
        still being in the terminal — the text-then-Return of a submit
        aimed at a shell would *execute* the draft — and an undeliverable
        send keeps the panel up with the draft in it, losing nothing.

        Docked, send never closes: the page is a fixture, not a stand-in
        raised over the input box, so the buffer clears and the page stays
        for the next prompt.

        A send can outrun the open-cut, which is a chain of screen reads
        and takes a beat (see _begin_cut). Two beats to outrun, and one
        each:

        * A cut still deciding what the box holds is *waited* for, never
          worked around — the box would otherwise keep the prompt that was
          about to be taken out of it, and typing this one after it sends
          the two jammed together. `_end_settling` sends for us the moment
          it knows.

        * A cut that has erased but not yet proved the box empty carries
          its last check here: whatever it still can't account for is
          erased first, a beat ahead of the prompt rather than in front of
          it in the same write — a chunk opening with backspaces is a
          chunk the CLI could read as pasted text.
        """
        docked = self._composer_page is not None
        if not text.strip():
            if not docked:
                self.close_composer()
            return
        if self._cut_settling:
            self._send_after_settle = True
            return
        if not self._agent_is_running():
            self.feed_message(_("Composer: the agent isn't running in this tab"))
            return
        leftover = (
            self._leftover_cut_keys(self._cut_pending)
            if self._cut_pending is not None
            else None
        )
        self._cut_pending = None
        self._cut_seq += 1  # the prompt about to be typed is not a cut's to erase
        self._composer.set_text("")
        if not docked:
            self._composer_revealer.set_reveal_child(False)
        if leftover:
            self.feed_child_text(leftover)
            GLib.timeout_add(_CUT_VERIFY_MS[0], self._inject_after_cut, text)
            return
        self.inject_prompt(text)

    def _inject_after_cut(self, text: str) -> bool:
        self.inject_prompt(text)
        return GLib.SOURCE_REMOVE

    def _toggle_composer_dock(self) -> None:
        """The composer chrome's dock/float button."""
        if self._composer_page is None:
            self.dock_composer()
        else:
            self.undock_composer()

    def dock_composer(self) -> None:
        """Move the live composer view out of the overlay into its own
        panel page below the terminal — the editor pop-out's precedent:
        reparent only, nothing serialized, so text, cursor and undo
        history ride along. Join-don't-split places it: a bottom home
        strip takes the page as a tab; only an empty axis splits."""
        if self._composer_page is not None:
            self._dock.reveal_page(self._composer_page)
            return
        composer = self._ensure_composer()
        revealer = self._composer_revealer
        revealer.set_reveal_child(False)
        revealer.set_visible(False)
        revealer.set_child(None)
        composer.set_docked(True)
        self._composer_page = ComposerPage(composer, on_closed=self._on_composer_page_closed)
        self._dock.open_page(self._composer_page, side="below")

    def undock_composer(self) -> None:
        """Bring the docked composer back over the terminal, text intact
        (the chrome's float button). The page's close then runs with the
        view already rescued, so `_on_composer_page_closed` recognizes it
        as an undock and leaves the text alone."""
        page = self._composer_page
        if page is None:
            return
        self._composer_page = None
        view = page.take_view()
        view.set_docked(False)
        revealer = self._composer_revealer
        revealer.set_child(view)
        self._dock.close_page(page)
        revealer.set_visible(True)

        def reveal() -> bool:
            revealer.set_reveal_child(True)
            view.focus_view()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def _on_composer_page_closed(self, page: ComposerPage) -> None:
        """The docked composer's tab is closing for real (its X, a bulk
        close, or the chrome's close routed through the dock): rescue the
        live view back to the (lowered) overlay slot and apply the
        overlay close's paste-back semantics — the draft returns to the
        agent's input box unsubmitted, or is quietly discarded when the
        agent has left the terminal (see `close_composer`)."""
        if page is not self._composer_page:
            return  # an undock already rescued the view; just a close now
        self._composer_page = None
        view = page.take_view()
        view.set_docked(False)
        self._composer_revealer.set_child(view)
        text = view.take_text()
        self._cut_pending = None  # as in close_composer: the text goes back
        self._cut_seq += 1
        if self._agent_is_running():
            restored = composerkeys.restore_text(text)
            if restored:
                self.feed_child_text(restored)
        self.grab_terminal_focus()

    def _begin_cut(self, composer: ComposerView) -> None:
        """Take the typed-but-unsent prompt out of the CLI's input box and
        into *composer* — the open-cut, run as a chain of screen reads.

        Both halves of it need a beat, which is why this isn't the inline
        read `open_composer` used to do:

        * The **read** is only worth trusting once the screen has stopped
          moving. The CLI echoes what was typed a repaint later, so a
          composer opened from the keyboard the instant a prompt was typed
          reads a line still catching up — and erasing that read would eat
          the characters it hadn't shown yet, which no later round can get
          back. A run of identical reads is the settle test, and a box
          that never settles is left alone.

        * The **erase** is checked afterwards, because a read can fall
          short of the buffer it renders even settled: an invisible
          trailing space is dropped, and so is the space a wrap ate
          between two long words. The erase is one backspace per character
          read, running backwards from the end, so a read one character
          short leaves the box holding the *first* character of the prompt
          — which the composer's copy starts with too, so the send that
          follows types it twice.

        Nothing is cut when there is nothing to take or the provider can't
        clear its box safely: no half-cut that leaves the text behind for
        a send to duplicate.
        """
        self._cut_pending = None
        self._cut_seq += 1
        self._cut_settling = True
        self._settle_cut(composer, self._cut_seq, None, 0, 0)

    def _settle_cut(
        self,
        composer: ComposerView,
        seq: int,
        previous: EnteredPrompt | None,
        agreed: int,
        attempt: int,
    ) -> bool:
        """One settle read, cutting once *agreed* of them in a row match.

        An empty box answers None to every read, which agrees with itself
        like any other answer: the ordinary open settles on the fourth read
        and cuts nothing.

        Every way out of here ends the settling, because a send held back
        for it (`_send_after_settle`) has to be let go of on all of them.
        """
        if not self._cut_alive(composer, seq):
            self._end_settling()
            return GLib.SOURCE_REMOVE
        prompt = self.entered_prompt()
        agreed = agreed + 1 if _prompt_read(prompt) == _prompt_read(previous) else 1
        if agreed >= _CUT_SETTLE_READS:
            self._apply_cut(composer, seq, prompt)
            self._end_settling()
            return GLib.SOURCE_REMOVE
        if attempt >= _CUT_SETTLE_TRIES:
            self._end_settling()  # never still: the box keeps its text
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(
            _CUT_SETTLE_MS, self._settle_cut, composer, seq, prompt, agreed, attempt + 1
        )
        return GLib.SOURCE_REMOVE

    def _end_settling(self) -> None:
        """The cut has decided; send whatever was waiting on it.

        The waiting send is re-taken from the composer rather than replayed
        from the text it carried, because a cut that landed has just seeded
        that box: what goes out is the CLI's text and the draft written
        under it, in the order they were written, which is what the send
        would have carried had it come a moment later."""
        self._cut_settling = False
        if not self._send_after_settle:
            return
        self._send_after_settle = False
        if self._composer is not None and self.composer_open():
            self._on_composer_send(None, self._composer.peek_text())

    def _apply_cut(
        self, composer: ComposerView, seq: int, prompt: EnteredPrompt | None
    ) -> None:
        """Erase the settled read from the box, seed it into the composer,
        and start checking that the box really emptied."""
        if prompt is None or not prompt.text.strip():
            return
        keys = self.provider.clear_prompt_keys(prompt)
        if not keys:
            return
        self.feed_child_text(keys)
        self._cut_pending = prompt.text
        composer.seed_text(prompt.text)
        GLib.timeout_add(_CUT_VERIFY_MS[0], self._verify_cut, composer, seq, 0)

    def _verify_cut(self, composer: ComposerView, seq: int, index: int) -> bool:
        """Re-read the box after an erase and finish the job if it fell
        short (see _begin_cut for how it can).

        Only a leftover the cut can account for is touched: the erase runs
        backwards from the end, so whatever it failed to reach is a prefix
        of what was read. Anything else on that line got there some other
        way — the user typing into the terminal, the agent redrawing — and
        is left alone, which also ends the checking.
        """
        if not self._cut_alive(composer, seq) or self._cut_pending is None:
            return GLib.SOURCE_REMOVE
        keys = self._leftover_cut_keys(self._cut_pending)
        if keys is None:
            self._cut_pending = None  # emptied, or not ours to erase
            return GLib.SOURCE_REMOVE
        self.feed_child_text(keys)
        if index + 1 < len(_CUT_VERIFY_MS):
            GLib.timeout_add(
                _CUT_VERIFY_MS[index + 1], self._verify_cut, composer, seq, index + 1
            )
        return GLib.SOURCE_REMOVE

    def _leftover_cut_keys(self, cut: str) -> str | None:
        """Keystrokes erasing what a cut of *cut* left in the input box, or
        None when the box is empty or holds something that cut can't
        account for (see _verify_cut).

        An erase still queued reads as the whole prompt, which is a prefix
        of itself: the answer is another full line of backspaces, and the
        two lines of them meet an emptied box between them — where the
        spare ones are no-ops."""
        left = self.entered_prompt()
        if left is None or not left.text or not cut.startswith(left.text):
            return None
        return self.provider.clear_prompt_keys(left)

    def _cut_alive(self, composer: ComposerView, seq: int) -> bool:
        """Whether cut *seq* still has a composer to cut into and an agent
        to cut from.

        A composer closed mid-chain has already typed its text back into
        the box (close_composer), and a send has just typed a prompt into
        it — no later round of a chain may erase *those*, and bumping
        `_cut_seq` is how each of them says so."""
        return (
            seq == self._cut_seq
            and self._composer is composer
            and self.composer_open()
            and self._agent_is_running()
        )

    def _pick_file_for_composer(self) -> None:
        """The composer's attach button: pick a file, landing its mention in
        the composer's box instead of the terminal's.

        The dialog starts in the agent's cwd right now, not the directory the
        tab started in, matching where the mention it produces will resolve."""
        dialog = Gtk.FileDialog(title=_("Attach file"))
        cwd = self.current_agent_cwd()
        if cwd:
            dialog.set_initial_folder(Gio.File.new_for_path(cwd))
        root = self.get_root()
        parent = root if isinstance(root, Gtk.Window) else None
        dialog.open(parent, None, self._on_composer_file_chosen)

    def _on_composer_file_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        # The composer can close (or the tab die) while the dialog is up;
        # a mention with nowhere to land is dropped.
        if self.get_root() is None or not self.composer_open():
            return
        path = gfile.get_path()
        if path is None:
            return  # a remote location — nothing the CLI could read
        reference = self.provider.file_reference(path, self.current_agent_cwd())
        if reference is None:
            self.feed_message(_("Add to chat isn't available for this file"))
            return
        self._composer.insert_mention(reference + " ")

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
        this does is find what it reads — the line the cursor is on, how far
        into it the cursor sits, and whether the rest of that line is the
        agent's own dim ghost text — and rule out a terminal with no agent
        left in it.
        """
        if self._child_pid is None:
            return False
        column, row = self.terminal.get_cursor_position()
        text = self._row_text(row, self.terminal.get_column_count())
        # What the line says is enough to say yes to an empty input, and that
        # is the answer nearly every time this is asked; only a line that reads
        # as written-in is worth a second look at how it was drawn.
        if self.provider.takes_prompt(text, column):
            return True
        return self.provider.takes_prompt(text, column, self._tail_is_dim(row, column))

    def _tail_is_dim(self, row: int, column: int) -> bool:
        """Whether the line from *column* to the end of *row* is drawn dim.

        Read as HTML rather than text, because dim is a thing VTE draws, not a
        thing the line says. The range has to start at the cursor for that to
        come back at all — VTE folds the dim attribute into a colour only for
        the run a range opens on (see vtehtml) — which suits the caller: the
        cursor is exactly where an agent's ghost text begins.
        """
        html = self.terminal.get_text_range_format(
            Vte.Format.HTML, row, column, row, self.terminal.get_column_count()
        )
        text = html[0] if isinstance(html, tuple) else html
        return vtehtml.is_dim_run(text or "", self._terminal_fg)

    def prompt_block(self) -> str:
        """Why a prompt sent to this tab wouldn't land, or "" when it would.

        The sentence a PR menu greys its prompt actions out with (see
        prmenu.ActionHost). One line covers every no: an agent that has exited,
        one mid-turn, one at a permission dialog and one with half a sentence
        already typed are all "not at an empty input", and the fix for all four
        is to look at the terminal.
        """
        return "" if self.takes_prompt() else _("This session isn't at an empty prompt.")

    def entered_prompt(self) -> EnteredPrompt | None:
        """The prompt typed into the agent's input box and not yet sent, or
        None with no agent, an empty box (takes_prompt — which also rules
        out the box's dim ghost suggestion, indistinguishable from typed
        text in a plain-text read), or no box on screen at all.

        The screen is read the way the other readers do — one cursor-anchored
        snapshot, never adjustment-derived grid rows (see _resolve_wrapped_at
        for why), split back into screen rows — but reaching *past* the
        cursor too: continuation rows sit below it whenever the cursor was
        arrowed back up into the box.
        """
        if self._child_pid is None or self.takes_prompt():
            return None
        _, cursor_row = self.terminal.get_cursor_position()
        row_count = self.terminal.get_row_count()
        columns = self.terminal.get_column_count()
        top_row = max(0, cursor_row - row_count + 1)
        screen = self.terminal.get_text_range_format(
            Vte.Format.TEXT, top_row, 0, cursor_row + row_count, columns
        )
        text = screen[0] if isinstance(screen, tuple) else screen
        # Soft-wrapped screen rows come back joined (as in
        # _resolve_wrapped_at); splitting them back — by cells, since a
        # wide character fills two — keeps the row indexing the cursor
        # position lives in.
        rows = split_screen_rows(text or "", columns)
        return self.provider.entered_prompt(rows, cursor_row - top_row, columns)

    def unstarted_thread(self) -> bool:
        """Whether this tab is still a New Thread with nothing in it: a
        brand-new session — not resumed, forked or continued, and its first
        prompt never sent, so no transcript has appeared to resolve a
        session id — sitting at an empty input box right now. Closing one
        loses nothing, so the window skips the active-session confirmation
        for it. Anything typed into the box (takes_prompt says no) brings
        the confirmation back."""
        return (
            self._resolver_cwd is not None
            and self.session_id is None
            and self._command_override is None
            and self.takes_prompt()
        )

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

    # -- transcript --------------------------------------------------------

    def set_transcript_path(self, jsonl_path: str | Path | None) -> None:
        """Tail a transcript for what the tab reads out of it (touched files,
        pull requests). Used on resume, and again once a brand-new session's
        file appears on disk."""
        self._transcript.set_path(jsonl_path)
        # Another session's PRs (and another session's model); re-read from the
        # new transcript below, and the PRs restored again by the window once
        # this tab's session is known.
        self._tracked_prs = {}
        self._restored_prs = []
        self._refresh_pr_chips([])
        self._refresh_model_label()
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
        chips and everything already parsed — it only re-aims the tail and the
        monitor at where the file lives now.
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

        Four sources, in the order a PR can first be known from them: the list
        restored from a previous run, the transcript's pr-links, the PRs the
        session attached itself (the attach_pr tool), and whatever the refresh
        button just found on the branch. A URL is only ever added — a PR the
        session opened stays on the row once the branch has moved on, which is
        the whole point of showing all of them.

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
        for attached in self._attached_prs.values():
            if all(pr.url != attached.url for pr in collected):
                collected.append(attached)
        if found is not None and all(pr.url != found.url for pr in collected):
            collected.append(found)  # a PR nothing else knows about: it is the newest
        return collected

    def _enriched(self, pr: PullRequest) -> PullRequest:
        """*pr* with its title and CI status, fetching them when due.

        A merged PR that already has a title is left alone: it has no checks
        left to run and shows no badge anyway, so an old chip on a long-lived
        session never costs another `gh` call. One with no title still asks
        once — the PR menu has a line to fill, and a list saved before
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
        # Same pane object wherever it lives (in-tab or popped out).
        self._editor.set_agent_files(self._transcript.touched_files())
        self._refresh_model_label()
        if tracked is not None:
            # The shown ones come back with status, and they keep it: it is
            # what the chips fall back to when a poll brings nothing new (a
            # failed fetch, or no fetch at all), and what gets saved for the
            # next run. A fetch that does land replaces it wholesale.
            shown = {pr.url: pr for pr in prs or []}
            self._tracked_prs = {pr.url: shown.get(pr.url, pr) for pr in tracked}
            self._merge_restored()
        self._refresh_pr_chips(prs or [])
        if lookup_empty:  # even with PRs still showing: none of them is this branch's
            self._sync_pr_refresh_tooltip(not_found=True)
        return GLib.SOURCE_REMOVE

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
        self._baselined_dirs = {self._resolver_cwd}
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
        # A worktree launch (claude -w) moves the agent into a worktree under
        # the launch dir before the first prompt, and its transcript is keyed
        # by the *worktree's* cwd — the launch dir's key never sees it. Follow
        # the agent into any worktree of this tab's own project, with the same
        # baseline discipline as the launch dir: the CLI recycles unchanged
        # worktrees, so a transcript already present when we first see the
        # worktree belongs to an older session (observed live — a leftover
        # would otherwise attach the instant the cwd moved, before this
        # session's own transcript exists). We see the cwd move within one
        # poll of the CLI starting, long before a first prompt can land, so
        # the baseline can't swallow our own transcript.
        live = self.current_agent_cwd()
        if live and live != self._resolver_cwd:
            root = worktree_project_root(live)
            if root is not None and os.path.realpath(root) == os.path.realpath(
                self._resolver_cwd
            ):
                if live not in self._baselined_dirs:
                    self._baselined_dirs.add(live)
                    if self._command_override is None:
                        self._known_transcripts |= set(
                            self.provider.transcripts_for_cwd(live)
                        )
                cands += [
                    p
                    for p in self.provider.transcripts_for_cwd(live)
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

    def _make_panel_strip(self) -> PanelStrip:
        """The dock's strip factory: every strip spawns this tab's shells
        (numbered dock-wide) at the agent's current working directory."""
        strip = PanelStrip(shell_factory=self._make_panel_shell)
        strip.set_cwd_lookup(self.current_agent_cwd)
        return strip

    def _make_panel_shell(self) -> PanelTerminal:
        """A new shell page, its tab-title number and its persistent
        history ordinal both dock-assigned (layout restore overwrites the
        ordinal with the saved one right after)."""
        shell = PanelTerminal(self._dock.next_shell_number())
        shell.hist = self._dock.next_hist_ordinal()
        return shell

    # -- the native PR page --------------------------------------------------

    def open_pr_page(self, pr: PullRequest, unresolved: bool = False) -> None:
        """Show *pr*'s native page in a strip beside this session.

        One page per URL per tab: asking for a PR whose page is already open
        fronts that page (revealing its strip if hidden) and re-reads it,
        rather than opening a twin. With *unresolved*, the page lands on its
        first unresolved thread — the badge's deep link — which a fresh page
        honors as soon as its first fetch does.
        """
        page = self._find_pr_page(pr.url)
        if page is None:
            page = self._make_pr_page(pr)
            self._dock.open_page(page)
        else:
            self._dock.reveal_page(page)
            page.refresh()
        if unresolved:
            page.reveal_unresolved()

    def open_pr_page_url(self, url: str, unresolved: bool = False) -> None:
        """`open_pr_page` from a bare URL — the sidebar's way in, where the
        window action carries only strings. The tab's own copy of the PR is
        preferred (it has the title and status); a URL the tab isn't
        tracking starts from what the summary cache knows."""
        pr = next((p for p in self._footer_prs if p.url == url), None)
        if pr is None:
            pr = parse_pr_url(url)
            if pr is None:
                return
            pr = known(pr)
        self.open_pr_page(pr, unresolved=unresolved)

    def _find_pr_page(self, url: str) -> PrViewPage | None:
        for page in self._dock.pages():
            if getattr(page, "page_kind", None) == "pr" and page.pr_url == url:
                return page
        return None

    def _make_pr_page(self, pr: PullRequest) -> PrViewPage:
        return PrViewPage(pr, host_factory=self._pr_action_host)

    def _make_panel_page(self, page: dict):
        """The dock's non-shell factory for layout restore (see
        paneldock.set_page_factory). A pr page's URL is persisted state and
        therefore untrusted, so it re-passes the same gate every PR URL
        passes before reaching gh; a composer entry conjures a fresh empty
        docked composer (placement persists, drafts never)."""
        if page.get("kind") == "composer":
            return self._restore_composer_page()
        if page.get("kind") != "pr":
            return None
        pr = parse_pr_url(page.get("url"))
        if pr is None:
            return None
        return self._make_pr_page(known(pr))

    def _restore_composer_page(self) -> ComposerPage | None:
        """A saved layout's docked composer, rebuilt empty. One composer
        per tab: a duplicate entry (a hand-edited layout file) is refused,
        which drops it from the restored strip."""
        if self._composer_page is not None or self.composer_open():
            return None
        composer = self._ensure_composer()
        self._composer_revealer.set_child(None)
        composer.set_docked(True)
        self._composer_page = ComposerPage(composer, on_closed=self._on_composer_page_closed)
        return self._composer_page

    @property
    def panel_visible(self) -> bool:
        """Whether the shells' home strip is showing — the state Ctrl+J
        toggles. Satellite strips are always visible while they exist."""
        return self._dock.home_visible

    def toggle_panel(self, default_mode: str | None = None) -> None:
        if self.panel_visible:
            self.hide_panel()
        else:
            self.show_panel(default_mode)

    def show_panel(self, default_mode: str | None = None, focus: bool = True) -> None:
        """Show the home strip, starting (or re-pointing) its shell at the
        agent's current working directory. `default_mode` ("bottom" |
        "right") opens it at the app-wide last-used home edge; None keeps
        the tab's own. `focus=False` leaves keyboard focus where it is
        (session restore)."""
        if not self.panel_visible and default_mode in ("bottom", "right"):
            self._dock.set_home_position(default_mode)
        restore = None
        if not self._dock.ever_spawned:
            texts = self._load_panel_history()
            # First open with no saved layout: one shell per saved history
            # file, oldest ordinal first (the shells take fresh ordinals —
            # the files re-key to them on the next save).
            restore = [texts[ordinal] for ordinal in sorted(texts)] or None
        self._dock.show_home(restore)
        if focus:
            GLib.idle_add(self._dock.focus_home)

    def hide_panel(self) -> None:
        self._dock.hide_home()

    def panel_has_running_command(self) -> bool:
        """True when a command is running in any shell page of any strip —
        even a hidden strip's job is protected by the close confirmation."""
        return self._dock.has_running_command()

    def select_busy_panel_tab(self) -> None:
        """Front the shell page whose command is live, so the close
        confirmation's "will be terminated" points at something visible."""
        self._dock.select_busy_shell()

    def move_focused_panel_page(self) -> None:
        """Cycle the focused panel page to the next strip (win.move-panel-page)."""
        self._dock.move_focused_page_next()

    def rotate_recent_panel_page(self) -> None:
        """Send the focused (or last-touched) panel tab to the dock's other
        axis (win.rotate-panel-page)."""
        self._dock.rotate_recent_page()

    def _load_panel_history(self) -> dict[int, str]:
        """Saved shell scrollbacks by history ordinal for this session —
        forks don't restore (their panel would clash with the original
        tab's) and never save."""
        if self.fork or not self.session_id:
            return {}
        return panelhistory.load_all(self.session_id)

    def save_panel_history(self) -> None:
        """Persist each panel tab's scrollback so re-opening this session
        restores them. A panel never opened in this tab leaves prior history
        untouched; tabs closed along the way drop out of the saved set."""
        if self.fork or not self.session_id or not self._dock.ever_spawned:
            return
        panelhistory.save_all(self.session_id, self._dock.capture_shell_texts())

    def clear_panel_history(self) -> None:
        """Wipe every panel tab's scrollback and the persisted history files.
        The onscreen buffers must go too — the save on tab/window close would
        otherwise re-dump them and resurrect the files. Also clears stale
        history from a previous run when the panel was never opened here."""
        self._dock.clear_shells()
        if not self.fork and self.session_id:
            panelhistory.delete(self.session_id)

    def capture_panel_layout(self) -> dict | None:
        """Snapshot the whole dock — home mode/sizes plus the split tree of
        strips and their pages — for per-session persistence. None when the
        panel was never used in this tab, so a session's saved layout
        survives tabs that never touched it. Forks never persist
        (mirroring panel history)."""
        if self.fork:
            return None
        return self._dock.capture_layout()

    def restore_panel_layout(self, layout: dict) -> None:
        """Re-apply a session's saved dock layout. The stored entry is
        untrusted: it is validated (a malformed tree falls back to the
        fresh default) and pruned to the page kinds this build can restore.
        Mode and sizes land in this tab's own memory — restoring a session
        must not disturb the app-wide defaults for new panels — and a saved
        tree rebuilds its strips (spawning shells, hidden ones included,
        with their saved scrollback) without stealing focus from the agent
        terminal."""
        layout = panellayout.validate(layout)
        if not layout:
            return
        layout = panellayout.prune(layout, {"shell", "pr", "composer"})
        mode = layout.get("mode")
        if mode in ("bottom", "right"):
            self._dock.set_home_position(mode)
        sizes = layout.get("sizes")
        if sizes:
            self._dock.seed_home_sizes(sizes)
        tree = layout.get("tree")
        if tree:
            self._dock.restore_layout(tree, self._load_panel_history())

    def swap_panel(self) -> str:
        """Move the shells to the other home edge (bottom↔right) and return
        the new position — the whole panel at once, where Ctrl+; moves a
        single tab (win.swap-panel). The strip relocates by reparenting —
        every shell keeps running — and shell pages parked in satellite
        strips gather back into the home strip on the way."""
        return self._dock.swap_home()

    def set_panel_size_lookup(self, lookup) -> None:
        """`lookup(mode) -> px` supplies the app-wide last-set strip size,
        seeding splits this tab hasn't sized itself yet."""
        self._dock.set_size_lookup(lookup)

    # -- editor panel --------------------------------------------------------

    @property
    def editor_visible(self) -> bool:
        """Whether the editor panel is showing *inside this tab* — false while
        the pane is popped out into its own window (see editor_detached)."""
        return not self._editor_detached and self._editor.get_visible()

    @property
    def editor_detached(self) -> bool:
        return self._editor_detached

    def toggle_editor(self) -> None:
        """The footer icon's open/close half. The (a) branch — dock a
        popped-out editor back — is the window's (`_toggle_editor`), which
        checks for a live EditorWindow before falling through to this."""
        if self._editor_detached:
            return
        if self.editor_visible:
            self.hide_editor()
        else:
            self.show_editor()

    def show_editor(self) -> None:
        if self._editor_detached or self.editor_visible:
            return
        self._editor.set_visible(True)
        self._outer_sizer.apply()
        self._editor_toggle_btn.set_tooltip_text(_("Hide editor panel"))

    def hide_editor(self) -> None:
        if not self.editor_visible:
            return
        self._outer_sizer.remember()
        self._editor.set_visible(False)
        self._editor_toggle_btn.set_tooltip_text(_("Show editor panel"))

    def detach_editor(self):
        """Hand the live pane over for reparenting into an EditorWindow (the
        window builds that; see _pop_out_editor there). Returns the pane, or
        None when there's nothing to detach. The in-tab slot stays empty —
        and the footer icon means "bring it back" — until reattach_editor."""
        if self._editor_detached:
            return None
        self._outer_sizer.remember()  # dock-back reopens at this width
        self._editor_detached = True
        self._outer.set_end_child(None)
        self._editor.set_visible(True)  # it may have been hidden along with the panel
        self._editor.set_detached(True)
        self._editor_toggle_btn.set_tooltip_text(_("Bring editor back into this tab"))
        return self._editor

    def reattach_editor(self, show: bool = True) -> None:
        """Dock the pane back where it was: same outer-paned slot, same
        remembered width, open even if it was closed when detached (a pane
        the user asked back shouldn't dock back to nothing).

        `show=False` docks it back closed: the editor window was closed, not
        docked back, and dismissing a window must never make a panel appear
        in the one behind it. The pane still comes home — its buffers belong
        to the tab — the footer icon just reopens it in place."""
        if not self._editor_detached:
            return
        self._editor_detached = False
        self._editor.set_detached(False)
        self._outer.set_end_child(self._editor)
        self._editor.set_visible(False)  # show_editor's no-op guard needs "closed"
        if show:
            self.show_editor()
        else:
            self._editor_toggle_btn.set_tooltip_text(_("Show editor panel"))

    def editor_dirty_count(self) -> int:
        return self._editor.dirty_count()

    def editor_dirty_names(self) -> list[str]:
        return self._editor.dirty_names()

    def editor_save_all(self, on_done) -> None:
        """Save every dirty editor buffer; `on_done(all_succeeded)` when the
        async saves resolve (immediately, when nothing is dirty)."""
        self._editor.save_all(on_done)

    def editor_save(self) -> None:
        self._editor.save_current()

    def focus_editor(self) -> None:
        self._editor.focus_default()

    @property
    def editor_root(self) -> str:
        """The project directory this tab's editor is rooted at (also quick
        open's search root)."""
        return str(self._editor.root)

    def can_open_in_editor(self, path: str | Path) -> bool:
        """Whether `open_in_editor(path)` would land: *path* resolves inside
        the editor's project root (the pane's own guard would refuse anything
        outside; this lets the window pick a better tab)."""
        return editorfiles.is_inside(self._editor.root, path)

    def open_in_editor(self, path: str | Path, cursor: list | None = None) -> None:
        """Open *path* in this tab's editor, revealing the panel if it is
        closed, optionally placing the cursor (*cursor* is open_file's
        restore_cursor: [0-based line, char offset]). While the pane is
        popped out its window already shows it — presenting that window is
        the caller's job (it owns the windows)."""
        if not self._editor_detached and not self.editor_visible:
            self.show_editor()
        self._editor.open_file(path, restore_cursor=cursor)
        self._editor.focus_default()

    def set_editor_width_lookup(self, lookup) -> None:
        """`lookup() -> px` supplies the app-wide last-set editor width, used
        for tabs that haven't sized their own editor yet."""
        self._outer_sizer.set_lookup(lambda _key: lookup())

    def capture_editor_state(self) -> dict | None:
        """Snapshot the editor's open/width/files for per-session persistence,
        mirroring capture_panel_layout. None when the editor was never used in
        this tab, so a session's saved state survives tabs that never touched
        it. Forks never persist."""
        if self.fork:
            return None
        if not self.editor_visible and not self._editor_detached and not self._editor.open_paths():
            return None
        self._outer_sizer.remember()
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
        width = self._outer_sizer.remembered("editor")
        if width:
            state["width"] = width
        return state

    def restore_editor_state(self, state: dict) -> None:
        """Re-apply a session's saved editor snapshot, mirroring
        restore_panel_layout. Width lands in this tab's own memory — restoring
        a session must not disturb the app-wide default for new tabs."""
        self._outer_sizer.set_remembered("editor", state.get("width"))
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

    def owns_pid_ancestors(self, ancestors: set[int]) -> bool:
        """Whether one of *ancestors* is a process this tab's terminal runs.

        *ancestors* is a pid plus its whole parent chain (proctree.
        ancestor_pids) — how a session MCP tool call is traced back to the
        tab whose shell spawned its `claude`: the shim that sent it is a
        child of that CLI, so the tab's own processes sit in its ancestry.
        Both candidate ends are tested (see `_candidate_pids`); a daemon-
        hosted process descends from systemd instead, matches no tab
        anywhere, and gets the dispatcher's clean identity error.
        """
        return any(pid in ancestors for pid in self._candidate_pids())

    def has_background_descendant(self, ignore: Collection[str] = frozenset()) -> bool:
        """Whether the agent has something still running below it right now —
        a tool call in flight, or a background job (a dev server, a long
        build) it started and left running. An extra "still working" signal
        for a session whose terminal has otherwise gone quiet; see
        `ActivityTracker` in activity.py.

        *ignore* is the session's plumbing baseline — cmdlines of the MCP
        servers the CLI keeps alive for its whole life, which are children of
        the agent but never work (see proctree.has_live_descendant)."""
        cli = getattr(self.provider, "cli", "") or ""
        return any(proctree.has_live_descendant(pid, cli, ignore) for pid in self._candidate_pids())

    def background_descendant_cmdlines(self) -> set[str]:
        """The cmdlines of everything running directly below this tab's agent
        right now. Sampled while nothing has ever been submitted to a freshly
        spawned tab, this is the agent's own plumbing — the baseline
        `has_background_descendant` is later told to ignore."""
        cli = getattr(self.provider, "cli", "") or ""
        cmdlines: set[str] = set()
        for pid in self._candidate_pids():
            cmdlines |= proctree.descendant_cmdlines(pid, cli)
        return cmdlines

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
        self._terminal_fg = themes.terminal_foreground(settings.get("terminal_theme"))
        # The floating composer button can be turned off in preferences; the
        # provider gate (no readable input box = no button) still applies.
        # The setting keeps the attach button's old key: same slot, same
        # pixels, and a rename would only orphan saved preferences.
        self._composer_overlay_btn.set_visible(
            bool(settings.get("attach_overlay_button", True))
            and self._provider_has_prompt_box()
        )
        self._composer_enter_sends = bool(settings.get("composer_enter_sends", True))
        self._composer_font = font
        if self._composer is not None:
            self._composer.set_enter_sends(self._composer_enter_sends)
            self._composer.set_font(self._composer_font)
        self._easy_copy_paste = bool(settings.get("easy_copy_paste"))
        self._apply_terminal_max_width(settings)
        self._set_footer_apps(settings.get("footer_apps") or [])
        self._dock.apply_settings(settings)
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
