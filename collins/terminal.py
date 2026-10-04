# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-10-04. Full change history: git log for this file.

"""A tab hosting a VTE terminal running the user's shell with an agent CLI inside."""

from __future__ import annotations

import os
import shlex
import tempfile
import time
from collections.abc import Callable, Collection
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import (  # noqa: E402
    activity,
    apilink,  # noqa: E402
    apppicker,
    attachpanel,
    attachrecords,
    composerkeys,
    dialogs,
    dropimages,
    editor,
    editorfiles,
    footerapps,
    gitloads,
    keybindings,
    keymap,
    modelmenu,
    newchat,
    panelhistory,
    panellayout,
    prmenu,
    ptyclient,  # noqa: E402
    remotediffs,
    remoteprs,
    sandboxchip,
    sandboxgrants,
    sandboxplan,
    themes,
    transcriptlinks,
)
from .api import protocol  # noqa: E402
from .api.loopback import LoopbackServer, RequestRefused  # noqa: E402
from .claudemodels import short_name  # noqa: E402
from .composer import ComposerPage, ComposerView  # noqa: E402
from .copylabel import copy_tooltip, enable_copy_on_click  # noqa: E402
from .flash import flash  # noqa: E402
from .formatting import display_path  # noqa: E402
from .gitinfo import (  # noqa: E402
    change_summary,
    current_branch,
    github_url,
    has_changes,
    parent_branch,
    repo_root,
)
from .gitpage import ICON as GIT_PAGE_ICON  # noqa: E402
from .gitpage import GitPage  # noqa: E402
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
from .newchatview import NewChatView, is_git_checkout  # noqa: E402
from .panedsizer import PanedSizer  # noqa: E402
from .paneldock import PanelDock  # noqa: E402
from .panelstrip import PanelStrip  # noqa: E402
from .providers import (  # noqa: E402
    EnteredPrompt,
    Provider,
    get_provider,
)
from .prstatus import (  # noqa: E402
    PullRequest,
    describe,
    known,
    parse_pr_url,
    to_records,
)
from .prview import PrViewPage  # noqa: E402
from .service.session import Session, agent_environment, bracketed_paste  # noqa: E402
from .shellinput import shell_command  # noqa: E402
from .transcript import TranscriptModel  # noqa: E402

# What a prompt is wrapped in when it must land as one paste (see
# Session.inject_prompt_unfocused); kept under its old name here for the
# checks that test its sanitizing directly.
_bracketed_paste = bracketed_paste
_PR_REFRESH_ICON_PX = 12  # the refresh button sits with them, not above them
_PR_CHIP_SPACING = 8  # between chips; their own parts sit 4 apart
# How long a coming-back-into-view refresh (window refocused, tab selected)
# vouches for the chips before another one is allowed to hit `gh` again.
# Focus comes and goes much faster than CI does — alt-tabbing past the window
# must not turn into a flurry of subprocesses.
_PR_FOCUS_REFRESH_MIN_US = 10 * 1_000_000
# How long an auto-opened PR page waits for the arrival that prompted it to
# finish laying out (see _on_hub_pr_attached). Long enough to be a different
# frame from the chip that just appeared, short enough to read as "and the
# page opened".
_PR_PAGE_SETTLE_MS = 250
# And how long the attachments panel waits after a tab is shown before asking
# whether there is room for it beside the terminal (see
# TerminalTab._consider_attachments_dock): a tab that has just been switched
# to measures 0 until the frame that allocates it, and a width of 0 has room
# for nothing.
_ATTACH_ROOM_SETTLE_MS = 250

# PCRE2 flags for the find bar: multiline, case-insensitive.
_PCRE2_CASELESS = 0x00000008
_PCRE2_MULTILINE = 0x00000400
_SEARCH_FLAGS = _PCRE2_CASELESS | _PCRE2_MULTILINE

# Font zoom (Ctrl+scroll / Ctrl+plus/minus/0): multiplicative steps on VTE's
# font-scale, clamped to the range Ptyxis and GNOME Terminal allow.
_FONT_SCALE_MIN = 0.25
_FONT_SCALE_MAX = 4.0
_FONT_SCALE_STEP = 1.1
# The chords themselves are keybindings.BINDINGS' terminal.zoom-* entries:
# `plus` is what most layouts produce only with Shift held, which GDK's own
# matching folds away (see keymap.KeyMatcher).

# Adw.Clamp's maximum-size when "terminal_max_width" is 0 (no limit): larger
# than any real window, so the clamp never actually constrains the terminal.
_UNLIMITED_CLAMP_WIDTH = 1_000_000

# The termprop VTE parses ConEmu-style OSC 9;4 progress sequences into — the
# agent CLI's own busy/idle announcement, which the window turns into the
# sidebar's pole (see activity.ProgressWatch). None on a VTE too old to have
# termprops at all (pre-0.82), which also lacks the termprop-changed signal:
# the wiring is skipped and the inferred sources carry the pole alone.
PROGRESS_HINT_TERMPROP: str | None = getattr(Vte, "TERMPROP_PROGRESS_HINT", None)


# The host side of sandboxing (sandboxplan.SandboxHost, bound to the app id
# and state): how a sandboxed launch gets its plan (`prepare_launch(cwd, box)`
# for the settled launch cwd — with the worktree a `-w` launch is narrowed
# to — None when no box can be built), and what the
# footer chip reads and writes — the workspace's grants, the guard on a new
# one, whether the launched plan is stale. Set by the app at startup, like
# providers.MCP_CONFIG_PATH; None means every sandboxed decision degrades
# to an unsandboxed launch that says so.
SANDBOX_HOST: sandboxplan.SandboxHost | None = None

# The live grants of this instance (sandboxgrants.GrantMounts): every box a
# tab launches registers with it, so a directory allowed while the session
# runs is mounted into the running box, and unregisters before its plan is
# rebuilt and when its shell exits. Set by the app beside SANDBOX_HOST;
# None means a grant applies at the next restart, as a static bind.
SANDBOX_GRANTS: sandboxgrants.GrantMounts | None = None

# The service's end of the loopback (api.loopback.LoopbackServer over the
# app's service.core.ServiceCore), the pty server every tab and
# panel shell spawns its shell on and is fed from. Set by the app at startup; a tab
# built without an app (a check driving the widget alone) gets one made on
# the spot with no state writer, so the backend never depends on the app.
SERVICE_LOOPBACK: LoopbackServer | None = None


def service_loopback() -> LoopbackServer:
    global SERVICE_LOOPBACK
    if SERVICE_LOOPBACK is None:
        from .service.core import ServiceCore

        # A tab with no app behind it (a check driving the widget alone)
        # never writes a model under the real state directory: only
        # COLLINS_PTY_STATE_DIR, else a per-process temporary tree.
        state_dir = os.environ.get("COLLINS_PTY_STATE_DIR") or tempfile.mkdtemp(prefix="collins-pty-")
        SERVICE_LOOPBACK = LoopbackServer(ServiceCore(state_dir=Path(state_dir)))
    return SERVICE_LOOPBACK


# A widget built with no app behind it reaches the service through the same
# loopback (apilink.current: a PR page's gh calls, a job, a tool's reply).
apilink.set_fallback(service_loopback)

# The environment an agent tab's shell spawns with — the app's own plus the
# two declarations that coax the CLI's progress announcements out for VTE —
# is the session's now (service.session.agent_environment, which says why);
# kept under its old name for what refers to it.
_agent_tab_environment = agent_environment


def _service_term(theme_name: str | None, terminal: Vte.Terminal) -> dict:
    """The hello's ``term`` for *terminal*, drawn in *theme_name*: the
    colours the service answers the program's colour queries with (the
    session tab's and the panel shells' alike). No ``scheme``: the service
    answers the colour-scheme query from the background's luminance, as
    VTE does (`check_termstream_answers`), and VTE's Default theme is grey
    on black whatever the desktop's scheme."""
    term: dict = {"vte": Vte.get_major_version() * 10000 + Vte.get_minor_version() * 100}
    theme = themes.get_theme(theme_name)
    if theme is not None:
        term["fg"] = "#" + theme["fg"]
        term["bg"] = "#" + theme["bg"]
    else:
        # VTE's defaults: the foreground it draws plain text in, the
        # background as it is drawn now.
        term["fg"] = "#c0c0c0"
        bg = terminal.get_color_background_for_draw()
        term["bg"] = f"#{round(bg.red * 255):02x}{round(bg.green * 255):02x}{round(bg.blue * 255):02x}"
    return term


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
    paths, _resolve_wrapped_url_at for URLs) before deciding what it opens;
    when the geometry can't say, the session's transcript — which has the
    link unwrapped — is asked next (_resolve_from_transcript_at).
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

        def from_transcript(cand: str | None) -> bool:
            # The fallback behind both screen stitchers: the transcript's
            # unwrapped text, corroborated against the screen. Nothing for a
            # shell tab, or a link still streaming in — the stitchers' and
            # the direct readings keep their say then.
            hit = _resolve_from_transcript_at(terminal, cand, x, y, roots)
            if hit is None:
                return False
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            kind, value = hit
            if kind == "file":
                path, line, col = value
                _open_file_reference(terminal, path, line, col)
            else:
                _launch_uri(terminal, value)
            return True

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
                from_transcript(None)
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
            if resolved is None and from_transcript(uri):
                # Before the direct reading, for the same reason the stitcher
                # runs first: the head of a wrapped path can be an existing
                # directory, and the transcript knows it was only the head.
                return
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
            stitched = _resolve_wrapped_url_at(terminal, uri, x, y)
            if stitched is None and from_transcript(uri):
                return
            uri = stitched or uri
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


def _resolve_from_transcript_at(
    terminal: Vte.Terminal,
    candidate: str | None,
    x: float,
    y: float,
    roots: list[str | None],
) -> tuple[str, object] | None:
    """Finish the clicked fragment off the tab's transcript (see
    transcriptlinks): ``("url", uri)`` or ``("file", (path, line, col))``,
    or None when the tab has no transcript, the transcript has no link the
    fragment is part of, or the screen around the click doesn't spell that
    link out. File completions are existence-checked like any other path
    reference; a longer candidate that resolves nowhere yields to a shorter
    one that does. Same row slop and same token-settles-the-row rule as the
    URL stitcher."""
    tab = terminal.get_ancestor(TerminalTab)
    transcript = tab.transcript_path if tab is not None else None
    if not transcript:
        return None
    screen = _screen_at(terminal, x, y)
    if screen is None:
        return None
    rows, row, col, _cols = screen
    links = transcriptlinks.transcript_links(transcript)
    if not links:
        return None
    row_text = _row_reader(rows)
    for r in (row + slop for slop in _ROW_SLOP):
        cand = candidate if candidate is not None else token_at_column(row_text(r), col)
        if not cand:
            continue
        for kind, text in transcriptlinks.completions(cand, rows, r, col, links):
            if kind == "url":
                return "url", text
            resolved = resolve_file_reference(text, roots)
            if resolved is not None:
                return "file", resolved
        if candidate is None:
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

    if tab is not None:
        # No caption to record: a clicked reference is a path, not something
        # anyone described. The line it was printed on almost always says
        # what it is, and the transcript's own snippet fills the label in
        # later (attachrecords lets context land in an empty slot).
        tab.record_attachment(path)
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


def _handle_zoom_key(terminal: Vte.Terminal, keys: keymap.KeyMatcher, event: Gdk.KeyEvent) -> bool:
    """Ctrl+plus / Ctrl+minus / Ctrl+0 by default, the zoom keys every GNOME
    terminal claims from the shell (see keybindings for the chords)."""
    if keys.matches("terminal.zoom-in", event):
        _zoom_by(terminal, _FONT_SCALE_STEP)
        return True
    if keys.matches("terminal.zoom-out", event):
        _zoom_by(terminal, 1 / _FONT_SCALE_STEP)
        return True
    if keys.matches("terminal.zoom-reset", event):
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


def _capture_contents(terminal: Vte.Terminal) -> str:
    """*terminal*'s current text contents including scrollback (plain text
    — VTE's dump carries no colors or attributes)."""
    stream = Gio.MemoryOutputStream.new_resizable()
    try:
        terminal.write_contents_sync(stream, Vte.WriteFlags.DEFAULT, None)
        stream.close(None)
    except GLib.Error:
        return ""
    data = stream.steal_as_bytes().get_data()
    return (data or b"").decode("utf-8", errors="replace")


class _TabHost:
    """The `service.session.SessionHost` a TerminalTab hands its Session:
    what the session tells the tab, and the few things it asks of a widget.
    A class of its own rather than the tab itself, so the tab's namespace
    stays the tab's."""

    def __init__(self, tab: TerminalTab) -> None:
        self._tab = tab

    def alive(self) -> bool:
        return self._tab.get_root() is not None

    def paint(self, text: str) -> None:
        self._tab.feed_message(text)

    def focus_terminal(self) -> None:
        self._tab.grab_terminal_focus()

    def composer_open(self) -> bool:
        return self._tab.composer_open()

    def refocus_composer(self) -> None:
        # Popovers undo a grab made during their own action: an idle later
        # the one that asked for the switch has closed.
        GLib.idle_add(self._tab._refocus_composer)

    def resend_composed(self) -> None:
        tab = self._tab
        if tab._composer is not None and tab.composer_open():
            tab._on_composer_send(None, tab._composer.peek_text())

    def stash_draft(self, text: str) -> None:
        self._tab._stash_draft(text)

    def mapped(self) -> bool:
        return self._tab.get_mapped()

    def shown_prs(self) -> list[PullRequest]:
        return self._tab._footer_prs

    def transcript_reset(self) -> None:
        self._tab._on_transcript_reset()

    def transcript_landed(self, prs: list[PullRequest], lookup_empty: bool) -> None:
        self._tab._on_transcript_landed(prs, lookup_empty)

    def session_resolved(self, session_id: str) -> None:
        self._tab._rekey_panel_shells()
        self._tab.emit("session-resolved", session_id)

    def fork_resolved(self, session_id: str) -> None:
        self._tab.emit("fork-resolved", session_id)

    def cwd_polled(self, cwd: str | None) -> None:
        self._tab._on_cwd_polled(cwd)

    def spawn_shell(self, cwd: str, env: list[str] | None) -> None:
        # Run the user's interactive shell, which the session types the
        # agent command into: aliases and env apply, and the tab drops to a
        # prompt when the agent exits. The tab closes when the *shell* exits.
        # *env* is not used: the service builds the shell's environment
        # from its own (ServiceCore._req_spawn), progress declarations
        # included, as a remote service would have to.
        self._tab._service_spawn(cwd)

    def sandbox_changed(self) -> None:
        self._tab._sync_sandbox_chip()

    def mark_stale_shells(self) -> None:
        self._tab._mark_stale_sandboxed_shells()

    def process_exited(self, status: int) -> None:
        self._tab.emit("process-exited", status)


class _ComposerCut:
    """The `service.session.CutSink` an open-cut lands in: the composer
    that was opened, for as long as it is still this tab's and still up."""

    def __init__(self, tab: TerminalTab, composer: ComposerView) -> None:
        self._tab = tab
        self._composer = composer

    def alive(self) -> bool:
        return self._tab._composer is self._composer and self._tab.composer_open()

    def seed(self, text: str) -> None:
        self._composer.seed_text(text)

    def refuse(self) -> None:
        # Whatever the composer had gathered in the meantime — the stash it
        # was seeded with, a keystroke — goes back to the stash.
        self._tab._stash_draft(self._composer.peek_text())
        self._tab.close_composer(restore=False)
        self._tab.feed_message(_("Composer: the input box holds a paste Collins can't read"))


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
    ordinal its tab title shows.

    As the tab does (spec §3.15, PR-1.8), the terminal has no child and
    shows a pty of kind ``shell`` on the service's pty server, through its
    own loopback client: output frames fed, commits sent back as input, the
    redraw guard, resize and focus as events, `pty-exited` for
    `child-exited` (ptyclient.ClientTerminal). Everything that reads or
    writes the shell goes through the service: its text is the screen model's capture, the
    foreground, the inner shell of a box and the cwd are the pty server's
    reads, typing is input frames, a note and the restored history are
    painted into the stream, and the panel history is written by the
    service from the model (TerminalTab.save_panel_history). `_view` is
    the glue."""

    page_kind = "shell"

    __gsignals__ = {
        # Emitted when the panel's shell exits (e.g. the user typed `exit`).
        "shell-exited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when the shell's terminal rings BEL (a PanelPage may emit
        # this; the strip re-emits it for the window's visual bell).
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(
        self,
        number: int = 1,
        plan_lookup: Callable[[], str | None] | None = None,
        box_lookup: Callable[[], str] | None = None,
        history_lookup: Callable[[], str | None] | None = None,
    ) -> None:
        """*history_lookup* (`() -> the key the tab's panel history is
        filed under`, None for none) names the history file the service
        writes this shell's scrollback to when its child exits (`spawn`'s
        ``history``, PR-1.11). *plan_lookup* makes this the **sandboxed** kind: `() -> the
        session's sandbox plan path` (or None while there is none), and
        the service spawns the shell as `bwrap <plan> -- $SHELL` through the same
        launcher the session's own typed line starts with — so it runs
        inside exactly the box the agent does, and is what the agent's
        run_in_terminal / read_terminal reach from inside one (see
        mcptools.tool_shells). Titled "Sandboxed shell N"; the same
        scrollback persistence as any shell. *box_lookup* (`() -> the
        session's box id`) is what names the box to the service:
        the service spawns the shell on the plan its own records hold for
        that box (service.core)."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._number = number
        self._plan_lookup = plan_lookup
        self._box_lookup = box_lookup
        self._history_lookup = history_lookup
        # The plan file this shell's box was actually built from, once it
        # has spawned. A box outlives the plan it was built from: after the
        # session's *Restart to apply* the tab holds a new plan, and a
        # shell still running in the old box may hold a directory the user
        # has since revoked -- so the agent is handed only the shells that
        # run in the box it runs in itself (see mcptools.tool_shells).
        self._spawn_plan: str | None = None
        # The persistent panel-history ordinal this shell saves its
        # scrollback under (see panelhistory); assigned by the dock's shell
        # factory, overwritten by layout restore. Never renumbered — pages
        # move between strips, so a positional key would drift.
        self.hist = 0
        self._child_pid: int | None = None
        self._spawned = False
        self._ever_spawned = False  # panel was used at some point in this tab's life
        self._easy_copy_paste = False
        self._keys = keymap.KeyMatcher(keybindings.current())
        # Agent input (run_command) that arrived while the shell was still
        # spawning, fed the moment the pty is up. Cleared on spawn failure
        # and on exit — a queued command must never surprise a later shell.
        self._pending_input: list[bytes] = []

        # The glue to the service (see the class docstring), and the saved
        # panel history waiting to be painted into the stream of the shell's
        # pty ahead of its first byte (shown on the widget meanwhile: a
        # sandboxed shell with no plan yet has no pty to paint into).
        self._history_paint: str | None = None
        self.terminal = ptyclient.ClientVte()
        self._client = service_loopback().connect(
            self._on_service_output, self._on_service_event, ptyclient.device_name()
        )
        self._view = ptyclient.ClientTerminal(self.terminal, self._client, on_exited=self._on_pty_exited)
        self.terminal.set_scrollback_lines(10_000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        # The sound belongs to the window, not to VTE (see Window._on_bell);
        # all this terminal does with a BEL is say that one arrived.
        self.terminal.set_audible_bell(False)
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
        # A page destroyed with no close of its own (its tab's window
        # closed with idle tabs): the shell must not outlive it.
        self.connect("destroy", lambda *_a: self.release_pty())

    @property
    def ever_spawned(self) -> bool:
        return self._ever_spawned

    @property
    def number(self) -> int:
        """The 1-based ordinal this shell's tab title shows ("Terminal N") —
        how the read_terminal tool names one to the agent."""
        return self._number

    @property
    def sandboxed(self) -> bool:
        """Whether this shell runs inside the session's sandbox — the kind
        a sandboxed session's terminal tools may reach, and the kind Ctrl+J
        never binds to (see paneldock)."""
        return self._plan_lookup is not None

    @property
    def sandbox_plan(self) -> str | None:
        """The plan file this shell's box was built from, or None while it
        hasn't spawned one (it takes the session's plan of the moment when
        it does). What tells a shell left over from the box before a
        *Restart to apply* from one running in the session's current box."""
        return self._spawn_plan

    def note(self, text: str) -> None:
        """Feed a dim one-line note into this shell's scrollback — said to
        the user, never to the shell (nothing is typed). It is painted into
        the pty's stream (rule 2 of §3.1), so the model, the history and
        every client have it; with no pty, the widget alone shows it."""
        line = f"\r\n\x1b[2m{text}\x1b[0m\r\n"
        if not self._paint(line):
            self.terminal.feed(line.encode())

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
            # The same bytes go into the pty's stream once it exists,
            # ahead of the shell's first (§3.15): the attach resets this
            # widget and the model is what everything else reads.
            self._history_paint = (
                restore_text.replace("\n", "\r\n") + f"\r\n\x1b[2m{marker}\x1b[0m\r\n"
            )
        if cwd is None or not Path(cwd).is_dir():
            cwd = str(Path.home())
        sandboxed = False
        if self._plan_lookup is not None:
            plan = self._plan_lookup()
            if not plan:
                # No box to run in (the session came up unsandboxed, or its
                # plan isn't written yet): nothing is spawned rather than a
                # shell that would run unconfined under this title. The
                # next show tries again (open_shell).
                self._spawned = False
                self.terminal.feed(
                    _("no sandbox plan for this session — the shell was not started").encode()
                )
                return
            sandboxed = True
        # The service runs the launcher on the plan its records hold for
        # the session's box, and queues the `cd` (core).
        self._service_spawn(cwd, sandboxed=sandboxed)

    # -- the service (see the class docstring) -----------------------------

    def _service_spawn(self, cwd: str, sandboxed: bool) -> None:
        """Ask the service for a ``shell`` pty in *cwd* at this terminal's
        grid (a sandboxed one by the session's box), show it, paint the
        restored history into it before the shell has said anything, and
        hand it the input queued meanwhile. A refusal is fed to the widget
        where VTE's spawn error was. The service's spawn returns once the
        child has exec'd, so the pid is known here, as VTE's callback knew
        it."""
        view, client = self._view, self._client
        cols, rows = view.grid()
        request: dict = {"t": "spawn", "kind": "shell", "cwd": cwd, "cols": cols, "rows": rows}
        history = self._history_lookup() if self._history_lookup is not None else None
        if history:
            # The service writes the scrollback here when the shell exits.
            request["history"] = history
            request["ordinal"] = self.hist
        if sandboxed:
            request["sandbox"] = True
            box = self._box_lookup() if self._box_lookup is not None else ""
            if box:
                request["sandbox_box"] = box
        try:
            reply = client.request(request)
            pty = int(reply["pty"])
            view.attach(pty)
        except RequestRefused as exc:
            reason = _(exc.msgid).format_map(exc.details) if exc.msgid else _("failed to start shell")
            self._pending_input.clear()
            self.terminal.feed(reason.encode())
            return
        except (ValueError, KeyError) as exc:
            self._pending_input.clear()
            self.terminal.feed(_("failed to start shell: {msg}").format(msg=str(exc)).encode())
            return
        found = self._service_pty()
        self._child_pid = found.child_pid() if found is not None else None
        if sandboxed and found is not None:
            self._spawn_plan = found.plan
        history, self._history_paint = self._history_paint, None
        if history:
            self._paint(history)
        pending, self._pending_input = self._pending_input, []
        for data in pending:
            client.send_input(pty, data)

    def _service_pty(self):
        """The service's pty this shell shows (the loopback's `pty_of`), or
        None: before the spawn, after the exit, once released."""
        view = self._view
        # `exited`: between `pty-exited` and the next spawn the view still
        # names the old pty, whose id the service has dropped (or, in the
        # beat before the reap lands, holds a finished pty with no master):
        # nothing is read from or written to it.
        if view.pty is None or view.exited:
            return None
        try:
            return self._client.pty_of(view.pty)
        except KeyError:
            return None

    def _paint(self, text: str) -> bool:
        """Paint *text* into the stream of this shell's pty (the service's
        `paint`, rule 2 of §3.1), in pieces the protocol takes. False when
        there is no pty to paint into: the caller
        feeds the widget instead."""
        if self._service_pty() is None:
            return False
        pty = self._view.pty
        step = protocol.TEXT_MAX
        try:
            for start in range(0, len(text), step):
                self._client.request({"t": "paint", "pty": pty, "text": text[start : start + step]})
        except (RequestRefused, ValueError):
            return False
        return True

    def _write(self, data: bytes) -> None:
        """Type *data* into the shell: an input frame to the service's pty."""
        if self._view.pty is not None:
            self._client.send_input(self._view.pty, data)

    def _on_service_output(self, pty: int, data: bytes, flags: int) -> None:
        self._view.on_output(pty, data, flags)

    def _on_service_event(self, event: dict) -> None:
        self._view.on_event(event)

    def _on_pty_exited(self, _status: int | None) -> None:
        # `pty-exited` is what `child-exited` was (§3.4).
        self._spawned = False  # a fresh shell is spawned on the next show
        self._child_pid = None
        self._spawn_plan = None  # the next spawn takes the plan of its day
        self._pending_input.clear()
        self.terminal.reset(True, True)
        self.emit("shell-exited")

    def history_source(self) -> int | str:
        """What the panel history is written from for this shell (the tab's
        save, `ServiceCore.write_panel_history`): its
        pty's id, so the service captures its model, or the widget's text
        when it has no pty (a sandboxed shell that never found a plan
        still holds the history it was restored with)."""
        found = self._service_pty()
        if found is not None:
            return int(self._view.pty)
        return _capture_contents(self.terminal)

    def release_pty(self) -> None:
        """The page is closing for good, or its tab is: end the shell on the
        service and let the loopback client go — what the widget's finalize
        did to its VTE child. A no-op once done."""
        view, client = self._view, self._client
        if client.closed:
            return
        if view.pty is not None and not view.exited:
            try:
                # Filed under no key first: a page closed for good takes its
                # history with it (the tab's saves wrote what a tab closing
                # keeps), so the exit this close causes writes nothing.
                client.request({"t": "panel.key", "pty": view.pty, "history": None})
                client.request({"t": "close", "pty": view.pty, "mode": "kill"})
            except (RequestRefused, ValueError):
                pass
        view.detach()
        client.close()

    def set_history_key(self, key: str | None) -> None:
        """Re-file this shell's history on the service under *key* (the
        tab's key moved: its session resolved, a draft became one or
        stopped being one). A no-op with no pty."""
        view, client = self._view, self._client
        if client.closed or view.pty is None or view.exited:
            return
        try:
            client.request({"t": "panel.key", "pty": view.pty, "history": key or None})
        except (RequestRefused, ValueError):
            pass

    def page_closed(self) -> None:
        """PanelPage hook: the page's X (or a stowed shell's exit) closed it
        for real, not a transfer between strips."""
        self.release_pty()

    def _shell_pid(self) -> int | None:
        """The pid whose process group is "the shell at its prompt": the
        spawned child for a plain shell; for a sandboxed one the shell
        inside the box, which the pty server finds below the launcher and
        bubblewrap. None until it exists."""
        found = self._service_pty()
        return found.shell_pid() if found is not None else None

    def _shell_cwd(self, shell_pid: int) -> str | None:
        """The shell's working directory now: the service's ``/proc`` read
        of its pty's shell (Pty.process_cwd)."""
        found = self._service_pty()
        return found.process_cwd() if found is not None else None

    def follow_cwd(self, cwd: str | None) -> bool:
        """`cd` this shell to *cwd* if it is sitting idle at its prompt —
        what the tab offers for the shells that were open beside a new-chat
        screen when its session started in a worktree (see
        TerminalTab._offer_shells_follow). False when the shell is busy and
        was left alone, or already there."""
        shell_pid = self._shell_pid()
        if not cwd or not Path(cwd).is_dir() or shell_pid is None:
            return False
        if self.has_running_command():
            return False
        if self._shell_cwd(shell_pid) == cwd:
            return False
        self._sync_cwd(cwd)
        return True

    def _sync_cwd(self, cwd: str | None) -> None:
        shell_pid = self._shell_pid()
        if not cwd or not Path(cwd).is_dir() or shell_pid is None:
            return
        if self.has_running_command():
            return  # don't interrupt whatever the user left running
        if self._shell_cwd(shell_pid) == cwd:
            return
        # The line reset clears any half-typed input before the cd; see
        # shellinput for what else can be sitting on that line.
        self._write(shell_command(f"cd {shlex.quote(cwd)}\n").encode())

    def has_running_command(self) -> bool:
        """True when something other than the shell owns the terminal's
        foreground. For a sandboxed shell the comparison is against the
        shell *inside* the box, not the launcher the terminal spawned:
        the inside shell takes the foreground for itself, and the kernel
        reports its process group in host pid numbers, so a bwrap-wrapped
        shell at its prompt reads idle like a plain one. The pty server
        answers (Pty.has_running_command), from the master it holds."""
        found = self._service_pty()
        return found.has_running_command() if found is not None else False

    def run_command(self, command: str) -> None:
        """Type *command* into this shell and run it — behind a line reset,
        so nothing already sitting on the input line joins it (see
        shellinput). The Enter is supplied; embedded newlines run as further
        commands, one Enter each. Input for a shell still spawning queues
        and is fed the moment the pty is up (the spawn answers on the
        main loop, like the callers here, so the flush can't be raced)."""
        data = shell_command(command.rstrip("\n") + "\n").encode()
        if self._child_pid is None:
            self._pending_input.append(data)
        else:
            self._write(data)

    def clear(self) -> None:
        """Wipe the screen and scrollback; a running shell keeps running and
        is nudged to repaint its prompt (\\x0c = Ctrl+L). The service wipes
        its model (what the history and the terminal tools read) and this
        terminal is redrawn from it."""
        if self._service_pty() is not None:
            pty = self._view.pty
            try:
                self._client.request({"t": "clear", "pty": pty})
                self._view.attach(pty)
            except (RequestRefused, ValueError):
                self.terminal.reset(False, True)
            self._write(b"\x0c")
            return
        self.terminal.reset(False, True)

    def page_state(self) -> dict:
        """This shell's slot in a serialized dock layout (see panellayout):
        its kind plus the history ordinal its scrollback file is keyed by,
        and whether it ran inside the sandbox — restored as the same kind."""
        state = {"kind": "shell", "hist": self.hist}
        if self.sandboxed:
            state["sandboxed"] = True
        return state

    def capture_contents(self) -> str:
        """The panel's current text contents including scrollback (plain text
        — VTE's dump carries no colors or attributes). The service's screen
        model is the text of record (§3.3): its capture, the same text the
        panel history is written from; the widget's own text before a pty
        exists."""
        found = self._service_pty()
        if found is not None:
            return self._client.screen_of(self._view.pty).capture_contents()
        return _capture_contents(self.terminal)

    def apply_settings(self, settings: dict) -> None:
        font = settings.get("font") or ""
        self.terminal.set_font(Pango.FontDescription.from_string(font) if font else None)
        try:
            self.terminal.set_scrollback_lines(int(settings.get("scrollback") or 10_000))
        except (TypeError, ValueError):
            pass
        themes.apply_terminal_theme(self.terminal, settings.get("terminal_theme"))
        # What the service answers this shell's colour queries with.
        self._view.set_term(_service_term(settings.get("terminal_theme"), self.terminal))
        self._easy_copy_paste = bool(settings.get("easy_copy_paste"))
        self._keys = keymap.KeyMatcher.from_settings(settings)

    # -- PanelPage protocol (see panelstrip) -------------------------------

    def page_title(self) -> str:
        if self.sandboxed:
            return _("Sandboxed shell {number}").format(number=self._number)
        return _("Terminal {number}").format(number=self._number)

    def page_icon(self) -> str | None:
        return sandboxchip.ICON if self.sandboxed else None

    def grab_page_focus(self) -> None:
        self.terminal.grab_focus()

    def has_page_focus(self) -> bool:
        return self.terminal.has_focus()

    def page_busy(self) -> bool:
        return self.has_running_command()

    def holds_escape(self) -> bool:
        """Whether Escape belongs to this shell rather than to the dock's
        "put the maximized page back down" (see paneldock._on_max_key).

        It does whenever something owns the terminal's foreground: vim,
        less and every other full-screen program reads Escape, and a
        maximized one losing the key would be unusable. A shell sitting at
        its prompt has no use for a bare Escape, so there the restore wins
        — the same foreground test the close confirmation asks."""
        return self.has_running_command()

    def _on_key_pressed(self, ctrl, _keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        event = ctrl.get_current_event()
        if event is None:
            return False
        keys = self._keys
        if _handle_zoom_key(self.terminal, keys, event):
            return True
        if self._easy_copy_paste:
            if keys.matches("terminal.copy", event) and self.terminal.get_has_selection():
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keys.matches("terminal.paste", event):
                self.terminal.paste_clipboard()
                return True
        if keys.matches("terminal.copy-always", event):
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if keys.matches("terminal.paste-always", event):
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
        # A sandboxed fork tab found the transcript the CLI minted for the
        # forked conversation: its id, for the sticky sandboxed set. The
        # tab itself stays bound to the id it was opened with.
        "fork-resolved": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A short message for the user, to float over the window as a toast
        # (the tab has no overlay of its own): the sandbox chip's refusal of
        # a directory, and the like. Plain text — the window escapes it.
        "toast": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Emitted (debounced) when a panel divider is moved: (scope, mode,
        # size) where scope is "home" (the shells' panel) | "page" (the
        # strip PR views and the other docked pages open into), mode is
        # "bottom" | "right", and size the new panel px size, so the window
        # can persist each as its own app-wide default.
        "panel-size-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, int)),
        # Emitted when rotating a tab re-homed the panel: "bottom" | "right",
        # the edge the window persists as the app-wide default (as it does
        # for the whole-panel swap win.swap-panel fires).
        "panel-position-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Emitted when either of the tab's terminals rings BEL, for the
        # window's visual bell.
        "bell": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when the images this session has seen change (object = their
        # attachrecords records, newest first), so the window can save them
        # against the session. Guarded like the tab's PR writes: a sighting of
        # an image already on the list that changes nothing about it says
        # nothing.
        "attachments-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Emitted each time a transcript read lands on the main loop (see
        # _on_transcript_landed), so a finish edge the window is holding for the
        # transcript's word (MainWindow._hold_finish) can be judged the
        # moment the word arrives rather than when its window runs out.
        "transcript-updated": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Emitted when this tab's stashed composer draft changes (str = the
        # draft, "" when it has been taken back or emptied), so the window
        # can save it against the session. See _stash_draft.
        "draft-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # The new-chat screen (a tab opened with new_chat=True, before its
        # first prompt is sent — see begin_session). "new-chat-changed" says
        # the draft's parts moved: text typed, the worktree box toggled, a
        # panel page opened or closed — the window debounces it into a
        # persisted draft. "new-chat-send" is the screen's Send: the prompt,
        # whether the worktree box is ticked, whether the Sandboxed box is,
        # the model picked ("" for the CLI's default) and the effort level
        # likewise; the window turns them into launch options (trust, the
        # flags) and calls begin_session.
        "new-chat-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "new-chat-send": (GObject.SignalFlags.RUN_FIRST, None, (str, bool, bool, str, str)),
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
        initial_size: tuple[int, int] | None = None,
        new_chat: bool = False,
        worktree_default: bool = False,
        sandbox_default: bool = False,
    ) -> None:
        """*new_chat* opens the tab on the new-chat screen instead of the
        agent's console: nothing is spawned until `begin_session`, which
        the screen's Send (the "new-chat-send" signal, via the window)
        reaches. *worktree_default* is what its worktree checkbox starts
        on, *sandbox_default* what its Sandboxed box starts on. Only a
        fresh session (no id, no command override) is ever a new chat.
        *options* carries a resumed session's sandbox decision too (see
        MainWindow.open_session): the plan is written here, at spawn."""
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        # The colour plain text is drawn in, once a theme has been applied;
        # None while the terminal is following the system colours. Read when
        # telling an agent's dim ghost text from typing (_theme_colours).
        self._terminal_fg: tuple[int, int, int] | None = None
        self._theme_name: str | None = None
        # The terminal has no child: it shows a pty of the service's pty
        # server through the loopback (spec §3.4): fed by output frames, its
        # commits sent back as input, the session reading the service's
        # screen model. `_view` is the glue.
        self.terminal = ptyclient.ClientVte()
        self._client = service_loopback().connect(
            self._on_service_output, self._on_service_event, ptyclient.device_name()
        )
        self._view = ptyclient.ClientTerminal(self.terminal, self._client, on_exited=self._on_pty_exited)
        self._pty = ptyclient.ServicePtyPort(self._client, self._view)
        screen = ptyclient.ServiceScreenPort(self._client, self._view, self._theme_colours)
        # The session's logic, out of the widget (see service/session.py):
        # everything the tab's terminal is *for* — launching the agent,
        # reading and writing its input box, the resolver, the transcript,
        # activity, the close — over two ports on the terminal above. The
        # tab's names for all of it forward there.
        self.session = Session(
            provider=provider or get_provider("claude"),
            pty=self._pty,
            screen=screen,
            host=_TabHost(self),
            session_id=session_id,
            fork=fork,
            options=options,
            command_override=command_override,
            cwd=cwd,
            jsonl_path=jsonl_path,
            # A VTE too old for termprops has no progress hint to watch.
            progress=PROGRESS_HINT_TERMPROP is not None,
            # Decided at spawn time, so a toggle mid-session can't half-apply
            # to a shell that inherited the other choice; new tabs pick up a
            # change.
            progress_env=bool((settings or {}).get("progress_termprop", True)),
            # Read live, as the app sets them at startup (and the checks
            # swap them).
            sandbox_host=lambda: SANDBOX_HOST,
            sandbox_grants=lambda: SANDBOX_GRANTS,
        )
        # The new-chat screen, while the tab is one (None after begin_session
        # or for a tab that never was), and the draft id its panel history is
        # filed under meanwhile (see _history_id). The prompt Send hands over
        # waits in the session (Session.hold_new_chat_prompt).
        self._new_chat: NewChatView | None = None
        self._history_key: str | None = None
        # Every _RootNameLinks watching a terminal inside this tab — the agent's
        # and one per panel shell — so a re-root can re-point them all. They
        # register themselves the first time they map, which is after this
        # constructor returns; it exists this early only so the _setup_links
        # calls below can never race it.
        self._root_name_links: list[_RootNameLinks] = []

        if initial_size is not None:
            # A tab whose page is never selected is never allocated, so its
            # child would otherwise come up at VTE's default 80x24 and stay
            # there — see Window.start_background_session. set_size reaches
            # the child's winsize with no allocation at all, but only if it
            # runs *before* the spawn at the end of this constructor: a
            # post-hoc call would let the CLI paint its first frame at 80
            # columns. Selecting the tab later hands allocation the wheel
            # back, which is an ordinary SIGWINCH the CLI redraws through.
            self.terminal.set_size(*initial_size)
        self.terminal.set_scrollback_lines(10_000)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        # The sound belongs to the window, not to VTE (see Window._on_bell);
        # all this terminal does with a BEL is say that one arrived.
        self.terminal.set_audible_bell(False)
        _setup_links(self.terminal)
        _setup_smooth_scroll(self.terminal)
        _setup_scroll_zoom(self.terminal)
        self.terminal.connect("bell", lambda *_: self.emit("bell"))

        # Clicking into the terminal lowers whatever is raised over it —
        # the composer and the attachments gallery both ride the overlay as
        # stand-ins, and a click aimed past them at the terminal means
        # "back to the CLI". Capture phase, like the link and context-menu
        # gestures above, so it fires even when the running app has turned
        # on mouse reporting; the sequence is never claimed, so the click
        # still lands in VTE (focus, selection, links) as usual.
        dismiss = Gtk.GestureClick(button=0)
        dismiss.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        dismiss.connect("pressed", lambda *_a: self.dismiss_raised_panels())
        self.terminal.add_controller(dismiss)

        self._easy_copy_paste = False
        self._keys = keymap.KeyMatcher(keybindings.current())
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
            tooltip_text=keybindings.with_hint(_("Open composer"), "win.toggle-composer"),
        )
        self._composer_overlay_btn.add_css_class("attach-overlay")
        self._composer_overlay_btn.connect("clicked", lambda *_: self.open_composer())
        # The user setting behind the button (default on); the docked state
        # and provider gate join it in _sync_composer_overlay_btn. Held
        # separately so a dock/undock can recompute without settings in hand.
        self._composer_overlay_setting = True
        self._composer_overlay_btn.set_visible(self._provider_has_prompt_box())
        # The composer itself is built lazily on first open (_ensure_composer);
        # only its future overlay slot exists up front.
        self._composer: ComposerView | None = None
        self._composer_revealer: Gtk.Revealer | None = None
        self._composer_page: ComposerPage | None = None  # set while docked
        self._composer_enter_sends = True
        self._composer_spell_click = True
        self._composer_font = ""
        self._composer_on_typing = False
        # The confirm_merges setting, at its shipped default until the first
        # apply_settings lands: a merge asks before it goes ahead. Read by the
        # PR actions this tab hosts (see _pr_action_host).
        self._confirm_merges = True
        # The git_parent_branch setting — the default parent branch name
        # the git page measures against when no PR names one ("" =
        # automatic); at its shipped default until apply_settings lands.
        # Read on every resolution (see _git_parent_branch).
        self._git_parent_setting = ""
        # An open that has been asked for but hasn't reached the screen yet:
        # the panel is revealed from an idle, so two keys pressed in the same
        # frame both find a composer that isn't open (see open_composer).
        self._composer_opening = False
        # A draft a close couldn't hand back to the CLI's input box (the
        # agent had left the terminal, where typing it back would have run
        # it as commands): re-seeded into the next composer this tab opens
        # (see _stash_draft), and saved against the session by the window,
        # so it outlives the tab and the app too.
        self._composer_stash = ""

        # The attachments handle: a slim pill on the terminal's right edge,
        # the composer button's counterpart on the other axis, opening the
        # gallery of images this session has seen (collins/attachpanel.py).
        # Always there, empty list or not — a panel nobody can find until it
        # is already full is a panel nobody finds — and it toggles: a second
        # click on the handle that raised the panel lowers it again.
        # It also lights up: while pictures have landed that the panel wasn't
        # on screen to show, the pill itself goes the app's attention orange
        # (the ".unseen" class, painted in themes.py) rather than growing a
        # badge of its own. On something this size that is the legible move —
        # a dot in the corner of an 18px pill is a smudge, and a numeral on
        # it is a numeral nobody reads, so the count goes in the tooltip and
        # the handle carries the signal with its whole surface.
        self._attachments_btn = Gtk.Button(
            icon_name="mail-attachment-symbolic",
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            margin_end=5,
            tooltip_text=_("Images and files this session has seen"),
        )
        self._attachments_btn.add_css_class("attach-overlay")
        self._attachments_btn.add_css_class("attachments-handle")
        self._attachments_btn.connect("clicked", lambda *_: self.toggle_attachments())
        # Built lazily on first open, like the composer; only the slot it
        # will ride in exists up front.
        self._attachments_view: attachpanel.AttachmentsView | None = None
        self._attachments_revealer: Gtk.Revealer | None = None
        self._attachments_page: attachpanel.AttachmentsPage | None = None  # docked
        self._git_page: GitPage | None = None  # one per tab, nulled by _on_git_page_closed
        # An open asked for but not yet on screen: the panel is revealed from
        # an idle, so without this a second click in the same frame reads as
        # "not open yet" and opens it again instead of closing it.
        self._attachments_opening = False
        # The images the badge is counting, and the moment the list was last
        # accounted for — this tab's opening, and thereafter every time the
        # panel was on screen. Everything dated before it has had its chance
        # to be seen and is not news (see attachrecords.unseen).
        self._attachments_unseen: set[str] = set()
        self._attachments_since = time.time()
        # And the pictures that have been up in the lightbox this run, which
        # the handle never lights for: whoever it would be alerting watched
        # them arrive full-screen (see _behold_attachment).
        self._attachments_beheld: set[str] = set()
        # The panel opening by itself: whether it still may (armed until the
        # panel is opened, however it was opened — see
        # _consider_attachments_dock), whether one is on its way to the
        # screen, and the moment this tab opened, which is what separates a
        # picture landing now from the history a resumed session hands over.
        self._attachments_autodock = True  # the setting; apply_settings pushes it in
        self._attachments_armed = True
        self._attachments_docking = False
        self._attachments_remap = False
        self._attachments_born = self._attachments_since
        # Room is a thing only a tab on screen has: an unmapped one measures
        # 0 and can never be found wide enough, so every tab switch re-asks
        # for the tab being switched to.
        self.connect("map", lambda *_: self._recheck_attachments_room())

        self._content_overlay = Gtk.Overlay(child=scrolled)
        content_overlay = self._content_overlay
        content_overlay.add_overlay(self._composer_overlay_btn)
        content_overlay.add_overlay(self._attachments_btn)

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
        # The visual bell over the terminal itself: a veil the window tints
        # for a beat when this tab's terminal rings (flash_bell, from
        # MainWindow._flash_session). The header bar, the tab header and the
        # sidebar row flash too, but for the tab the user is looking at
        # those are the wrong places — the tab bar is hidden by default and
        # the selected row is already the accent colour — so the flash
        # lands where their eyes are. Transparent at rest and never a
        # pointer target, so it costs the terminal nothing between bells.
        self._bell_veil = Gtk.Box(can_target=False)
        self._bell_veil.add_css_class("bell-veil")
        self._overlay.add_overlay(self._bell_veil)
        # The terminal *and* its gutters, as one widget: where the window
        # hangs the "user is at this tab" click gesture (see
        # MainWindow._wire_tab), so a click beside a width-limited terminal
        # counts as presence the same as a click into it.
        self.terminal_area: Gtk.Widget = self._overlay
        # A click that lands on the gutter itself — not the terminal, nor a
        # panel raised over it — puts the keyboard in the terminal, as the
        # click would have if it had been a few pixels further in. Bubble
        # phase, so a click the composer's text view took stays there.
        gutter_click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        gutter_click.connect("pressed", self._on_gutter_click)
        self._overlay.add_controller(gutter_click)

        # What the dock's fixed leaf holds: the terminal (and its gutters),
        # or — for a tab opened as a new chat — a stack that shows the
        # new-chat screen in its place until the first prompt is sent, when
        # `begin_session` flips it to the terminal. The screen sits *inside*
        # the dock's leaf on purpose: Ctrl+J's terminal and every other panel
        # page open beside it exactly as they would beside the console, and
        # stay put through the flip. A tab that isn't a new chat skips the
        # stack — the console is the leaf, as it always was.
        stage: Gtk.Widget = self._overlay
        if new_chat and session_id is None and command_override is None:
            self._new_chat = NewChatView(
                cwd=cwd or str(Path.home()),
                pick_attach=self._pick_file_for_composer,
                file_reference=lambda name: self.provider.file_reference(name, cwd),
                notify=self.feed_message,
                worktree_default=worktree_default,
                is_git=bool(cwd) and is_git_checkout(cwd),
                model=(options.model if options else "") or "",
                pick_model=bool(self.provider.session_models()),
                effort=(options.effort if options else "") or "",
                pick_effort=self._can_switch_effort(),
                sandbox_default=sandbox_default,
                # The cached verdict only — never the blocking probe on the
                # main loop; the app refreshes the box when it lands.
                sandbox_available=sandboxplan.probe_reason() == "",
            )
            self._new_chat.connect("changed", lambda *_a: self.emit("new-chat-changed"))
            self._new_chat.connect(
                "send-requested",
                lambda _v, text, worktree, sandbox, model, effort: self.emit(
                    "new-chat-send", text, worktree, sandbox, model, effort
                ),
            )
            self._stage = Gtk.Stack(vexpand=True, hexpand=True)
            self._stage.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self._stage.add_named(self._overlay, "terminal")
            self._stage.add_named(self._new_chat, "new-chat")
            self._stage.set_visible_child_name("new-chat")
            stage = self._stage
            self.terminal_area = stage

        # The panel dock — strips of panel pages (shells, for now) split
        # around the agent terminal in a tree of fixed-axis paneds. Pages
        # move between strips by reparenting, so shells keep running; the
        # old bottom↔right orientation flip is gone (swap now *moves* the
        # shells to the other home edge). Divider sizes ride per-paned
        # PanedSizers inside the dock; the home strip's re-emits here as
        # panel-size-changed so the window can persist app-wide defaults.
        panel_right = bool(settings) and settings.get("panel_position") == "right"
        self._dock = PanelDock(
            terminal=stage,
            strip_factory=self._make_panel_strip,
            home_position="right" if panel_right else "bottom",
        )
        self._dock.connect("bell", lambda *_: self.emit("bell"))
        self._dock.connect(
            "size-changed",
            lambda _d, scope, mode, size: self.emit("panel-size-changed", scope, mode, size),
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
        # The editor paned's counterpart to the dock's sizers — one fixed key,
        # since the editor column only ever has the one position. Its
        # size-changed re-emits as editor-size-changed (minus the key).
        self._outer_sizer = PanedSizer(
            self._outer, key=lambda: "editor", occupied=lambda: self.editor_visible
        )
        self._outer_sizer.connect(
            "size-changed", lambda _s, _key, size: self.emit("editor-size-changed", size)
        )
        # A panel tab the dock maximizes floats over the *whole* tab, so its
        # overlay wraps the outer paned (dock plus editor column) rather
        # than living inside the dock; the dock only decides what goes in
        # it. The footer below stays out: a status bar the overlay covered
        # would take this tab's PR chips and cwd off screen with it.
        self._max_overlay = Gtk.Overlay(child=self._outer, vexpand=True)
        self._dock.set_maximize_host(self._max_overlay)
        self.append(self._max_overlay)

        self._footer_cwd: str | None = None  # last value shown in the footer
        self._footer_branch: str | None = None
        self._footer_model: str | None = None  # model id, as the transcript writes it
        self._footer_effort: str | None = None  # effort level, likewise
        # What the chips currently show — the newest of the PRs the session
        # tracks (Session.tracked_prs), with their status.
        self._footer_prs: list[PullRequest] = []
        self._saved_pr_records: list[dict] = []  # last records written to the hub
        # The app-wide PR hub (see prstore), handed over by the window as the
        # tab is added. The tab writes its footer list through it and follows
        # everyone else's writes and fetches from it; a tab without one (unit
        # tests, mostly) keeps its chips to itself.
        self._pr_store = None
        self._pr_store_handlers: list[int] = []
        # Whether a PR joining this session opens its page unbidden (the
        # open_pr_panel_on_attach setting, pushed in by apply_settings).
        self._auto_open_prs = False
        # Every image this session has been shown, key -> the sighting (see
        # attachrecords). Kept apart from anything a poll produces and folded
        # in on collection, for the reason attach_pr gives: a sighting lands
        # from a tool call or a click at a moment of its choosing, and a
        # transcript update already in flight replaces what it collected
        # wholesale when it finishes. Replaced wholesale here too, never
        # mutated, so the update thread never reads a half-written dict.
        self._attachments: dict[str, attachrecords.Attachment] = {}
        self._restored_attachments: list[attachrecords.Attachment] = []  # from a previous run
        # And the polled source those are kept apart from: the images the
        # transcript scan noticed, replaced wholesale by every update.
        self._scanned_attachments: list[attachrecords.Attachment] = []
        self._struck_attachments: set[str] = set()  # removed from the panel by hand
        self._saved_attachment_records: list[dict] = []  # last records handed to the window
        self._pr_focus_refresh_at = 0  # last time coming into view forced a refetch
        self.append(self._build_footer())

        # The terminal.* chords — copy, paste, find, newline, zoom — read
        # by hand rather than as GTK shortcuts (see keymap.KeyMatcher).
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        self.terminal.add_controller(keys)

        if settings:
            self.apply_settings(settings)
        self.set_transcript_path(jsonl_path)
        if self._new_chat is not None:
            return  # nothing runs until the screen's Send (begin_session)
        self.session.spawn(cwd, session_id)
        if jsonl_path is None and session_id is None:
            self._start_transcript_resolver(cwd)  # find the new session's transcript
        elif fork and self.sandboxed:
            # A fork keeps the original's id for the tab, but the CLI mints
            # a new one for the conversation — and that one must be sticky
            # too, or resuming the fork's own row later runs unboxed. The
            # same resolver finds its transcript and reports the id on
            # "fork-resolved" instead of binding the tab to it.
            self.session.fork_resolve = True
            self._start_transcript_resolver(cwd)

    # -- the session, forwarded ------------------------------------------------
    #
    # What the tab's callers (the window, the app, the MCP handlers, the e2e
    # checks) have always read and written on a tab, now held by its Session.

    @property
    def session_id(self) -> str | None:
        """The session this tab runs, None until a fresh one resolves."""
        return self.session.session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self.session.session_id = value

    @property
    def fork(self) -> bool:
        """Whether the tab forked the session it was opened with."""
        return self.session.fork

    @property
    def provider(self) -> Provider:
        return self.session.provider

    @property
    def _options(self):
        return self.session.options

    @property
    def _command_override(self) -> str | None:
        return self.session.command_override

    @property
    def _cwd(self) -> str | None:
        return self.session.cwd

    @property
    def _child_pid(self) -> int | None:
        """The shell spawned on the agent's pty, None until it is."""
        return self._pty.pid

    @property
    def _transcript(self) -> TranscriptModel:
        return self.session.transcript

    @property
    def finish_ledger(self) -> activity.FinishLedger:
        """Whether this tab's finish edges are real turns ending, judged off
        the transcript (see activity.FinishLedger); armed by the first read
        that lands, asked by the window at each edge."""
        return self.session.finish_ledger

    @property
    def _resolver_cwd(self) -> str | None:
        return self.session.resolver_cwd

    @property
    def _initial_command(self) -> str | None:
        """The command typed into the shell at the launch (None for a
        plain shell)."""
        return self.session.initial_command

    @property
    def _worktree_launch(self) -> bool:
        return self.session.worktree_launch

    @property
    def _new_chat_prompt(self) -> str | None:
        return self.session.new_chat_prompt

    @property
    def _pasted_back(self) -> dict[str, str]:
        return self.session.pasted_back

    @property
    def _paste_back_pending(self) -> list[str] | None:
        return self.session.paste_back_pending

    # -- the new-chat screen -------------------------------------------------

    @property
    def is_new_chat(self) -> bool:
        """Whether the tab is still on its new-chat screen: no agent, no
        shell, a draft. False from `begin_session` on, and for every tab that
        was opened straight onto a console."""
        return self._new_chat is not None

    @property
    def start_cwd(self) -> str | None:
        """The directory the tab was handed to start in — what a draft is
        filed against, and what its Send launches in."""
        return self._cwd

    @property
    def launch_options(self):
        """The SessionOptions the tab was opened with (a start_session
        caller's model and permission mode), or None."""
        return self._options

    @property
    def sandboxed(self) -> bool:
        """Whether this tab's agent runs inside a sandbox — as launched, not
        as the settings now say. What the /bg and attach guards ask."""
        return self.session.sandboxed

    @property
    def sandbox_plan_path(self) -> str | None:
        """The plan file the running box was built from (None for an
        unsandboxed tab, or before the launch settled): what the footer
        chip reads back, what a sandboxed panel shell spawns through, and
        what a sibling's plan is derived from."""
        return self.session.sandbox_plan_path

    @property
    def sandbox_box(self) -> str:
        """The id of the box this tab's session runs in, "" for an
        unsandboxed tab or before the launch settled: what the window
        records against the session id once the resolver binds it."""
        return self.session.sandbox_box

    def take_sandbox_defaults_owed(self) -> bool:
        """Whether this tab's box is still owed its first grants — it was
        minted for a --continue launch, with none — and no longer after
        this call: the window asks once, when the session resolves."""
        return self.session.take_sandbox_defaults_owed()

    def new_chat_text(self) -> str:
        return self._new_chat.text() if self._new_chat is not None else ""

    def seed_new_chat_text(self, text: str) -> None:
        """Put *text* in the screen's composer, cursor at the end — a
        screenshot's staged prompt, or anything else that wants the box
        filled without restoring a whole draft (restore_new_chat)."""
        if self._new_chat is not None:
            self._new_chat.set_text(text)

    def new_chat_worktree_choice(self) -> bool | None:
        """The screen's worktree box as the user left it, None while it still
        follows the project's default (see NewChatView.worktree_choice)."""
        return self._new_chat.worktree_choice() if self._new_chat is not None else None

    def set_sandbox_available(self, available: bool, default: bool) -> None:
        """Hand the new-chat screen the probe's verdict once it lands (see
        NewChatView.set_sandbox_available); a no-op off the screen."""
        if self._new_chat is not None:
            self._new_chat.set_sandbox_available(available, default)

    def new_chat_sandbox_choice(self) -> bool | None:
        """The screen's Sandboxed box as the user left it, None while it
        still follows the project's default (see NewChatView.sandbox_choice)."""
        return self._new_chat.sandbox_choice() if self._new_chat is not None else None

    def new_chat_model(self) -> str:
        """The screen's model pick, "" while it stands on the CLI's default
        (see NewChatView.model)."""
        return self._new_chat.model() if self._new_chat is not None else ""

    def new_chat_effort(self) -> str:
        """The screen's effort pick, "" while it stands on the CLI's default
        (see NewChatView.effort)."""
        return self._new_chat.effort() if self._new_chat is not None else ""

    def new_chat_worthy(self) -> bool:
        """Whether the screen holds anything worth keeping as a draft: text,
        or a panel page open beside it (see newchat.draft_worthy)."""
        if self._new_chat is None:
            return False
        return newchat.draft_worthy(self._new_chat.text(), bool(self._dock.pages()))

    def restore_new_chat(
        self,
        draft_id: str,
        text: str,
        worktree: bool | None,
        layout: dict | None,
        model: str = "",
        effort: str = "",
        sandbox: bool | None = None,
    ) -> None:
        """Put a kept draft back on the screen: its text, its checkboxes, its
        model and effort picks, and the dock the way it was left — shells
        and their scrollback included, which are filed under the draft id
        until the session starts (see _history_id)."""
        if self._new_chat is None:
            return
        self._history_key = draft_id
        self._rekey_panel_shells()
        self._new_chat.set_text(text)
        self._new_chat.set_worktree_choice(worktree)
        self._new_chat.set_sandbox_choice(sandbox)
        self._new_chat.set_model(model)
        self._new_chat.set_effort(effort)
        if layout:
            self.restore_panel_layout(layout)

    def set_new_chat_history_key(self, draft_id: str) -> None:
        """File this screen's panel scrollback under *draft_id* from now on —
        what a fresh new chat gets the moment it becomes a draft."""
        if self._new_chat is not None:
            self._history_key = draft_id
            self._rekey_panel_shells()

    def begin_session(self, options, prompt: str) -> None:
        """The screen's Send, once the window has settled the launch options
        (trust written where the CLI reads it, the worktree flag decided):
        spawn the shell and the agent as a console tab would have at open,
        show the console, and type *prompt* in the moment the CLI is at its
        input box (`Session._new_chat_prompt_tick`) — the background spawn's
        prompt-poll, done in the foreground. An empty *prompt* is the
        screen's *Empty Session*: the agent is left at its own input box,
        with no poll waiting to type anything in. The dock's pages ride
        along: they hang off the leaf the stack sits in, not off the screen.

        The screen widget stays in the stack, hidden, rather than being torn
        down: its composer may be mid-drop when Send is clicked, and a
        destroyed drop target is a crash, not a no-op.
        """
        view = self._new_chat
        if view is None:
            return
        self._new_chat = None
        self._history_key = None
        self._rekey_panel_shells()
        self.session.options = options
        # Shells open right now, at a launch that will move into a worktree,
        # get the offer to move with it once the worktree exists.
        self.session.shells_follow_armed = bool(options and options.worktree) and bool(
            self._dock.shell_pages()
        )
        self._stage.set_visible_child_name("terminal")
        self.session.hold_new_chat_prompt(prompt)
        self.session.spawn(self._cwd, None)
        self._start_transcript_resolver(self._cwd)
        if prompt:
            self.session.start_new_chat_prompt_poll()
        self.grab_terminal_focus()

    def _note_new_chat_change(self) -> None:
        """A dock change that may make (or unmake) the screen a draft."""
        if self._new_chat is not None:
            self.emit("new-chat-changed")

    # -- spawning ----------------------------------------------------------
    #
    # The launch — the command, the sandbox plan, the reaped worktree put
    # back, the worktree launch watched, the restart — is the session's
    # (Session.spawn and what follows it); the tab asks the service for the
    # shell when asked (_TabHost.spawn_shell) and hears it land here.

    def _service_spawn(self, cwd: str) -> None:
        """The tab's `spawn_shell`: ask the service for an agent
        pty in *cwd* at this terminal's grid (the service builds the shell's
        environment, progress declarations included, from its own), show
        it, and let the session type. A refusal is painted where the spawn
        error goes. The session hears the spawn an idle later, at
        PRIORITY_DEFAULT."""
        view = self._view
        cols, rows = view.grid()
        try:
            reply = self._client.request(
                {"t": "spawn", "kind": "agent", "cwd": cwd, "cols": cols, "rows": rows}
            )
            view.attach(int(reply["pty"]))
        except RequestRefused as exc:
            reason = _(exc.msgid).format_map(exc.details) if exc.msgid else _("failed to start shell")
            self.feed_message(reason)
            return
        except (ValueError, KeyError) as exc:
            self.feed_message(_("failed to start shell: {msg}").format(msg=str(exc)))
            return
        GLib.idle_add(self._service_spawned, priority=GLib.PRIORITY_DEFAULT)

    def _service_spawned(self) -> bool:
        if self._view.pty is not None:
            self.session.shell_spawned()
        return GLib.SOURCE_REMOVE

    def _theme_colours(self) -> tuple:
        """(foreground, background, palette) of the theme the terminal is
        drawn in, each None while it follows the system colours: what the
        service's screen model is asked to read ghost text with."""
        theme = themes.get_theme(self._theme_name)
        if theme is None:
            return self._terminal_fg, None, None
        return (
            self._terminal_fg,
            themes.hex_rgb(theme["bg"]),
            tuple(themes.hex_rgb(c) for c in theme["palette"]),
        )

    def _term_for_service(self) -> dict:
        """The hello's ``term`` for this terminal (`_service_term`)."""
        return _service_term(self._theme_name, self.terminal)

    def _on_service_output(self, pty: int, data: bytes, flags: int) -> None:
        self._view.on_output(pty, data, flags)

    def _on_service_event(self, event: dict) -> None:
        self._view.on_event(event)

    def _on_pty_exited(self, status: int | None) -> None:
        # `pty-exited` is what `child-exited` was (§3.4): the shell on the
        # service's pty is gone. *status* is an exit code, or minus the
        # signal number (os.waitstatus_to_exitcode), not VTE's raw wait
        # status; one the service does not know (a keeper crash, PR-3.6)
        # is shown as a signal exit nothing names.
        self.session.shell_exited(-1 if status is None else int(status))

    def release_pty(self) -> None:
        """The tab is closing for good: end its pty on the service (Phase
        1: closing a tab ends the session, D12; *Detach* is PR-1.12's) and
        let the loopback client go. What the widget's finalize did to its
        VTE child, done where the window closes the page."""
        if self._client.closed:
            return
        pty = self._view.pty
        if pty is not None and not self._view.exited:
            try:
                self._client.request({"t": "close", "pty": pty, "mode": "kill"})
            except (RequestRefused, ValueError):
                pass
        self._view.detach()
        self._client.close()

    def _relaunch_without_worktree(self) -> None:
        """Type the same new-session command again with the worktree dropped
        (`Session.relaunch_without_worktree`)."""
        self.session.relaunch_without_worktree()

    def can_restart_sandboxed(self) -> bool:
        """Whether *Restart to apply* is on offer (`Session.can_restart_sandboxed`)."""
        return self.session.can_restart_sandboxed()

    def restart_sandboxed(self) -> bool:
        """The footer chip's *Restart to apply* (`Session.restart_sandboxed`):
        the CLI asked to exit, and this session resumed in the same shell
        with a plan rebuilt from the state now. False when nothing can be
        restarted."""
        return self.session.restart_sandboxed()

    def _on_child_exited(self, terminal: Vte.Terminal, status: int) -> None:
        # The shell is gone, and the box with it: the session tidies up
        # after it (Session.shell_exited), then "process-exited" goes out.
        self.session.shell_exited(status)

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

    def _on_gutter_click(self, _gesture, _n_press: int, x: float, y: float) -> None:
        target = self._overlay.pick(x, y, Gtk.PickFlags.DEFAULT)
        if target is not None and target.is_ancestor(self._content_overlay):
            return  # the terminal, or something riding it, has the click
        self.grab_terminal_focus()

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
        # name is what shows and the id itself is the tooltip.
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
        # The chip that carries the name into the row and takes its visibility:
        # the name label itself when there is nothing to switch, or a menu
        # button wrapping it when there is.
        self._model_chip: Gtk.Widget
        if self._can_switch_model():
            # A menu button, like the PR chips beside it: a click opens the
            # switch menu, and the copy its neighbours answer clicks with
            # lives on as that menu's own copy row. A MenuButton (not a
            # hand-parented popover) because the catalog fills in on show and
            # arrives fully only from a worker thread: a MenuButton
            # re-measures its popover as the list grows, where a popover
            # popped up by hand keeps the size it had over the empty menu and
            # strands the rows in a one-line scroller.
            popover = modelmenu.new_model_popover(
                lambda: self._footer_model, self.switch_model
            )
            # It sits at the bottom of the tab: open upwards.
            popover.set_position(Gtk.PositionType.TOP)
            model_btn = Gtk.MenuButton(popover=popover)
            model_btn.set_child(self._model_label)
            model_btn.add_css_class("flat")
            self._model_chip = model_btn
        else:
            enable_copy_on_click(self._model_label, lambda: self._footer_model, short_name)
            self._model_chip = self._model_label
        self._model_chip.set_visible(False)
        # The effort level rides beside the model — the other dial on what
        # the session answers with, read off the same transcript lines — as
        # a chip of the same make: a menu button over its switch menu when
        # the provider has one, the bare label otherwise. The two share the
        # model's divider; neither shows until the session has answered.
        self._effort_label = Gtk.Label(xalign=0.0)
        self._effort_label.add_css_class("caption")
        self._effort_label.add_css_class("dim-label")
        self._effort_chip: Gtk.Widget
        if self._can_switch_effort():
            effort_popover = modelmenu.new_effort_popover(
                lambda: self._footer_effort, lambda: self._footer_model, self.switch_effort
            )
            effort_popover.set_position(Gtk.PositionType.TOP)
            effort_btn = Gtk.MenuButton(popover=effort_popover)
            effort_btn.set_child(self._effort_label)
            effort_btn.add_css_class("flat")
            self._effort_chip = effort_btn
        else:
            self._effort_chip = self._effort_label
        self._effort_chip.set_visible(False)
        # The sandbox chip leads the row when the session runs inside a box
        # (see sandboxchip): what is inside, the workspace's grants, and a
        # restart when they changed. Hidden on every other tab, and shown
        # only once the launch settled with a plan (_sync_sandbox_chip).
        # A client of the service's sandbox.* requests (service.sandbox):
        # what the box holds, its grants and tools, its restart.
        self._sandbox_chip = sandboxchip.SandboxChip(
            box=lambda: self.session.sandbox_box,
            link=apilink.current,
            handle=lambda: self.session.handle,
            on_open_shell=self.open_sandboxed_shell,
            on_toast=lambda text: self.emit("toast", text),
        )
        self._sandbox_chip.set_visible(False)
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
        # A click opens the git page (the diff of this tree); the copy the
        # label used to be moves to the right button, so nothing is lost.
        enable_copy_on_click(
            self._branch_label,
            lambda: self._footer_branch,
            lambda b: f"⎇ {b}",
            button=Gdk.BUTTON_SECONDARY,
        )
        open_git = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        open_git.connect("released", lambda *_: self.open_git_page())
        self._branch_label.add_controller(open_git)

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
            keybindings.with_hint(_("Show/hide terminal panel"), "win.toggle-panel")
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

        # The git page, one click away beside the panel toggles: the same
        # glyph the page's own tab wears, so the button and what it opens
        # read as one thing. The footer's ⎇ branch label opens it too, but a
        # label doesn't look pressable; this is the way in you can see.
        # Greyed outside a repository (rechecked with the branch each tick)
        # rather than hidden — a button that comes and goes is one you never
        # learn the place of.
        self._git_toggle_btn = Gtk.Button(icon_name=GIT_PAGE_ICON)
        self._git_toggle_btn.add_css_class("flat")
        self._git_toggle_btn.set_action_name("win.toggle-git")
        self._git_toggle_btn.set_tooltip_text(
            keybindings.with_hint(_("Show/hide git page"), "win.toggle-git")
        )
        self._git_toggle_btn.set_sensitive(False)

        # model, effort, cwd, branch and PRs sit together on the left; the
        # wrapper box (not the cwd label) takes the slack so the buttons stay
        # pinned right even while the model, branch and PR labels are hidden.
        left = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, hexpand=True)
        left.append(self._sandbox_chip)
        left.append(self._model_chip)
        left.append(self._effort_chip)
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

        # The editor toggle closes the row, right of the git toggle — a
        # page-with-a-folded-corner glyph, not the panel-split
        # icon `toggle_btn` uses two along from it. One click means one of three
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
        footer.append(self._git_toggle_btn)
        footer.append(self._editor_toggle_btn)
        # Poll only while on screen; refresh immediately on every tab switch
        # (Session.start_cwd_poll, whose every reading lands in
        # _on_cwd_polled).
        self.connect("map", lambda *_: self.session.start_cwd_poll())
        return footer

    def _on_cwd_polled(self, cwd: str | None) -> None:
        """One tick of the session's cwd poll: the footer's cwd and branch,
        the editor following a move, and the git page's freshness check."""
        self._maybe_follow_editor(cwd)
        if cwd != self._footer_cwd:
            self._footer_cwd = cwd
            self._cwd_label.set_text(display_path(cwd) if cwd else "")
            self._cwd_label.set_tooltip_text(copy_tooltip(cwd) if cwd else None)
        branch = current_branch(cwd)  # rechecked every tick: checkouts don't change the cwd
        if branch != self._footer_branch:
            self._footer_branch = branch
            self._branch_label.set_text(f"⎇ {branch}" if branch else "")
            self._branch_label.set_tooltip_text(self._branch_tooltip(branch) if branch else None)
            self._branch_label.set_visible(branch is not None)
            self._git_toggle_btn.set_sensitive(branch is not None)
            # The chips are the session's history, so a checkout doesn't retire
            # any of them; it only makes the button worth pressing again.
            self._sync_pr_refresh_tooltip()
        self._sync_footer_seps()
        # The git page's freshness check rides this same 2 s tick — but only
        # for a page somebody can see; one in a hidden strip catches up on
        # its next map.
        page = self._git_page
        if page is not None and page.get_mapped():
            page.poll_tick()

    @staticmethod
    def _branch_tooltip(branch: str) -> str:
        """The branch label's tooltip: the branch, then both things a click
        can do to it (see the footer's gestures)."""
        return f"⎇ {branch}\n" + _("Click to open the git page") + "\n" + _("Right-click to copy")

    def _maybe_follow_editor(self, cwd: str | None) -> None:
        """Keep the editor pointed at wherever the agent is actually working.

        Rides the cwd poll, and acts only on a move that has held still
        across polls, once (`Session.settle_cwd`, which says why). Where the
        agent went decides how far this goes: still inside the same project
        (a worktree, most often) and the editor simply follows; anywhere
        else and it only offers. See `editorfiles.follow_scope`."""
        root = str(self._editor.root)
        scope = self.session.settle_cwd(cwd, root)
        if scope is None:
            return
        if scope is editorfiles.FollowScope.AUTO:
            self._editor.request_root(cwd)
        else:
            self._editor.offer_root(cwd)
        # The shells' offer rides the same settled move, whichever scope the
        # editor gave it: NONE (which settle_cwd answers None for) is never
        # a worktree (it means "not a move at all" — no cwd, a missing
        # directory, or the root itself), and a worktree under the
        # repository is AUTO, so every move the launch could have made
        # reaches here.
        self._maybe_offer_shells_follow(cwd)

    def _maybe_offer_shells_follow(self, cwd: str) -> None:
        """The session a new-chat screen just started has settled into its
        worktree, and shells were open beside the screen when Send was
        pressed: offer to move them there too.

        Offered, not done: those shells were opened against the project
        directory on purpose — a build running there, a log being tailed —
        and a `cd` typed into them uninvited would be a surprise. Only the
        launch that asked for a worktree arms this (see begin_session), and
        only once: the first settled move after the launch is the worktree
        being entered, and the answer given then stands.
        """
        if not self.session.shells_follow_armed:
            return
        self.session.shells_follow_armed = False
        shells = [shell for shell in self._dock.shell_pages() if shell.ever_spawned]
        if not shells:
            return
        dialogs.confirm_dialog(
            self,
            _("Move terminals to {name}?").format(name=Path(cwd).name),
            ngettext(
                "This session started in a worktree at {path}. The terminal "
                "open beside it is still in the project directory — change "
                "its directory to the worktree? A terminal running a command "
                "is left alone.",
                "This session started in a worktree at {path}. The {count} "
                "terminals open beside it are still in the project directory "
                "— change their directory to the worktree? A terminal running "
                "a command is left alone.",
                len(shells),
            ).format(path=display_path(cwd), count=len(shells)),
            _("Change Directory"),
            lambda: self._follow_shells(shells, cwd),
            default_response="confirm",
            destructive=False,
        )

    def _follow_shells(self, shells: list, cwd: str) -> None:
        skipped = 0
        for shell in shells:
            if shell.get_root() is None:
                continue  # closed while the question was up
            if not shell.follow_cwd(cwd) and shell.has_running_command():
                skipped += 1
        if skipped:
            self.feed_message(
                ngettext(
                    "{n} terminal is running a command and stayed where it was",
                    "{n} terminals are running commands and stayed where they were",
                    skipped,
                ).format(n=skipped)
            )

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
        until it has. Called wherever the transcript has just been read.
        The composer's picker button names the same read, so the two never
        disagree about what the session is answering with."""
        model = self._transcript.model()
        if model == self._footer_model:
            return
        self._footer_model = model
        self._model_label.set_text(short_name(model) if model else "")
        if model is None:
            tooltip = None
        elif self._can_switch_model():
            tooltip = model + "\n" + _("Click to switch the model")
        else:
            tooltip = copy_tooltip(model)
        self._model_chip.set_tooltip_text(tooltip)
        self._model_chip.set_visible(model is not None)
        if self._composer is not None:
            self._composer.set_model_name(short_name(model) if model else None)
        self._sync_footer_seps()

    def _refresh_effort_label(self) -> None:
        """Name the effort level the session last answered at, or hide the
        chip until it has — the model label's twin, off the same read, and
        pushed to the composer's effort button the same way."""
        effort = self._transcript.effort()
        if effort == self._footer_effort:
            return
        self._footer_effort = effort
        name = modelmenu.effort_label(effort) if effort else ""
        self._effort_label.set_text(name)
        if effort is None:
            tooltip = None
        elif self._can_switch_effort():
            tooltip = _("Effort: {level}").format(level=name) + "\n" + _(
                "Click to switch the effort level"
            )
        else:
            tooltip = _("Effort: {level}").format(level=name)
        self._effort_chip.set_tooltip_text(tooltip)
        self._effort_chip.set_visible(effort is not None)
        if self._composer is not None:
            self._composer.set_effort_name(name or None)
        self._sync_footer_seps()

    def _sync_sandbox_chip(self) -> None:
        """Show the footer's Sandboxed chip exactly when this tab's agent
        runs inside a box — after the launch settled with a plan, and
        never on an unsandboxed tab (or one whose box couldn't be built)."""
        self._sandbox_chip.set_visible(self.sandboxed and self.sandbox_plan_path is not None)
        self._sync_footer_seps()

    def _can_switch_model(self) -> bool:
        """Whether this provider can switch a running session's model — the
        gate on the footer label's menu and the composer's picker alike,
        probed with an alias the way _provider_has_prompt_box probes."""
        return self.provider.model_switch_command("sonnet") is not None

    def _can_switch_effort(self) -> bool:
        """Whether this provider has an effort dial at all — the gate on the
        footer chip's menu, the composer's picker and the new-chat screen's,
        probed the way _can_switch_model probes."""
        return self.provider.effort_switch_command("low") is not None

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
        self._model_sep.set_visible(
            self._footer_model is not None
            or self._footer_effort is not None
            or self._sandbox_chip.get_visible()
        )
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

        Archiving is the same kind of answer: this session is the one a PR
        page docked here would put away once its merge lands, and a tab with
        no session yet has nothing to offer that for.

        Whether a merge asks first isn't about the session at all — it is the
        app's confirm_merges setting — but it rides here for the same reason
        the rest does: this is what a PR's actions have to ask on the click.
        """
        return prmenu.ActionHost(
            prompt_block=self.prompt_block,
            has_changes=lambda: has_changes(self.current_agent_cwd()),
            send_prompt=self.inject_prompt,
            refresh=self._request_update,
            view_pr=self.open_pr_page,
            view_unresolved=lambda pr: self.open_pr_page(pr, unresolved=True),
            confirm_merges=lambda: self._confirm_merges,
            archive=self._archive_this_session if self.session_id else None,
        )

    def _archive_this_session(self) -> None:
        """Put this tab's session away — the second half of a PR page's "Merge
        and archive", once the merge itself has landed.

        Through the window's action rather than by hand: archiving a session
        with a tab open closes that tab, and the close flow is where a busy
        agent gets asked about before anything happens (see the window's
        `archive_session`). The archiving half, never the toggle's other one:
        this is answering a merge, not a click on a row.
        """
        if self.session_id:
            self.activate_action("win.archive-session-now", GLib.Variant("s", self.session_id))

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
        # The save is what everything else follows: the hub's session-changed
        # sends the sidebar row (and anyone else showing this session) back to
        # the saved list, and the statuses these chips wear already went out
        # as status-changed when whichever fetch landed them.
        self._remember_prs(prs)
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
        """Write the row's PRs to the hub, which saves them for this session.

        Status goes with them (see prstatus.to_record), so this fires for a
        check that turned red as well as for a PR that turned up — but only
        when the records actually differ, so a poll that came back with the
        same answer writes nothing.

        A fork writes nothing (its tab shares the original's session id and
        would overwrite its list), and neither does a tab whose session isn't
        resolved yet — it has nowhere to write, and its list is re-derived
        from the transcript the moment it is.
        """
        records = to_records(prs)
        if records == self._saved_pr_records:
            return
        self._saved_pr_records = records
        if self._pr_store is not None and self.session_id and not self.fork:
            self._pr_store.set_records(self.session_id, records)

    def set_pr_store(self, pr_store) -> None:
        """Join the app-wide PR hub (see prstore). The window calls this once,
        as the tab is added.

        The hub outlives every tab, so the connections are dropped on destroy
        rather than left holding a dead widget — and they survive a tab
        moving between windows, which unroots it without destroying it.
        """
        if self._pr_store is not None or pr_store is None:
            return
        self._pr_store = pr_store
        self._pr_store_handlers = [
            pr_store.connect("status-changed", self._on_hub_status_changed),
            pr_store.connect("session-changed", self._on_hub_session_changed),
            pr_store.connect("pr-attached", self._on_hub_pr_attached),
        ]
        self.connect("destroy", self._leave_pr_store)
        # A window closed with idle tabs destroys them with no close-page:
        # the tab's pty must not outlive it (idempotent).
        self.connect("destroy", lambda *_a: self.release_pty())
        # Nor its panel shells' (a stowed or maximized one included).
        self.connect("destroy", lambda *_a: self.release_panel_ptys())

    def _leave_pr_store(self, *_args) -> None:
        if self._pr_store is None:
            return
        for handler in self._pr_store_handlers:
            self._pr_store.disconnect(handler)
        self._pr_store_handlers = []
        self._pr_store = None

    def _on_hub_status_changed(self, _hub, url: str) -> None:
        """A PR this tab shows was fetched somewhere — the sidebar's sweep, a
        row's menu, another tab's poll, a PR page's own load: put what landed
        on screen now rather than up to a poll later. `known` is a dictionary
        lookup, so both redraws are main-loop safe; the chips route through
        `_refresh_pr_chips`, whose equality guard drops the fetches that
        changed nothing this tab wears."""
        page = self._find_pr_page(url)
        if page is not None:
            page.sync_summary()
        if any(pr.url == url for pr in self._footer_prs):
            self._refresh_pr_chips([known(pr) for pr in self._footer_prs])

    def _on_hub_session_changed(self, _hub, session_id: str) -> None:
        """This session's saved list was rewritten by somebody else — the
        sweep, the first-prompt attacher, its row's menu: adopt it, or a PR
        only they knew about (a branch lookup's find, say) would be dropped
        from the saved list by this tab's next poll. The tab's own write comes
        straight back around here, and leaves again just as fast: it is
        exactly what `_saved_pr_records` already holds.

        Adoption deliberately leaves `_saved_pr_records` alone — what the tab
        will actually show isn't known until the update `restore_prs` requests
        merges the adopted list with its own sources (`Session._collect_prs`) — so
        that update ends in one more `set_records`. When the merge changed
        nothing, the hub's equality guard makes that write the no-op it
        deserves to be: no disk, no signal, one spare comparison."""
        if session_id != self.session_id or self._pr_store is None:
            return
        records = self._pr_store.records(session_id)
        if records == self._saved_pr_records:
            return
        self.restore_prs(records)

    def _on_hub_pr_attached(self, _hub, session_id: str, url: str) -> None:
        """A pull request joined this session for the first time: show it.

        Only with the setting on, and it doesn't matter which path put the PR
        there — this tab's own poll, the first-prompt attacher, the attach_pr
        tool, a row's menu — because the hub announces the association rather
        than the discovery. Once per PR per session comes free with it: the
        saved list is what "already seen" is written on, so a page closed
        again is not reopened, and a resume (whose PRs are all on that list
        before any of this runs) opens nothing at all.

        A fork's tab sits on the original's session id and would open a second
        copy of the same page beside it, so it sits this out — the same reason
        it never writes the list either.

        The open is held off for a beat rather than run here or from the next
        idle. This arrives re-entrantly inside the hub write — for the tab's
        own path, inside the chip rebuild that started it — and carving a new
        strip out of the dock relayouts the whole tab. Asked for in that same
        breath, on top of the resize the new chip has just requested, GTK's
        Wayland backend segfaults; it does so reproducibly under a headless
        compositor, and neither an idle nor a low-priority one is late enough
        to escape it. `_PR_PAGE_SETTLE_MS` later the layout has settled and
        the same open is uneventful — and a panel that opens by itself is not
        something a fraction of a second is noticed in.
        """
        if not self._auto_open_prs or self.fork or session_id != self.session_id:
            return
        GLib.timeout_add(_PR_PAGE_SETTLE_MS, self._open_attached_pr_page, url)

    def _open_attached_pr_page(self, url: str) -> bool:
        # A tab that closed inside the wait has dropped its hub connections;
        # its dock is gone and there is nothing left to open a page in.
        # Quiet on purpose: this open was nobody's click, and the keyboard
        # may be mid-word in the composer or the agent's input box.
        if self._pr_store is not None:
            self.open_pr_page_url(url, focus=False)
        return GLib.SOURCE_REMOVE

    def restore_prs(self, records: object) -> None:
        """Re-adopt the PRs saved for this session (`Session.restore_prs`).

        The window calls this once the tab's session is known, and the hub's
        session-changed calls it again for every list somebody else writes
        while the tab is open."""
        self.session.restore_prs(records)

    def attach_pr(self, pr: PullRequest) -> bool:
        """Adopt a PR named from outside the transcript — the attach_pr
        session tool. False when the session already tracks it
        (`Session.attach_pr`)."""
        return self.session.attach_pr(pr)

    # -- attachments --------------------------------------------------------

    def record_attachment(
        self,
        key: str,
        *,
        source: str = attachrecords.LIGHTBOX,
        caption: str | None = None,
        context: str | None = None,
        origin: str | None = None,
    ) -> None:
        """Write down an image this session put on screen.

        *key* is the absolute path it was read from, or — for one that came
        off the web — the URL itself, never the cache file it was downloaded
        into: those are pruned after a day, so the copy is not the thing
        worth remembering. A key that is neither is dropped here rather than
        by the caller, so every capture point can pass on what it has
        without first deciding whether it counts.

        Everything after the key is keyword-only, as it is on
        attachrecords.sighting: the caption is what a caller reaches for
        first, but *source* is the parameter next to the key, and a caption
        landing there is a source nothing recognizes — which is dropped
        silently, taking the whole sighting with it.
        """
        one = attachrecords.sighting(
            key, source=source, caption=caption, context=context, origin=origin
        )
        if one is None:
            return
        self._attachments = attachrecords.fold(self._attachments, one)
        if one.source == attachrecords.LIGHTBOX:
            # Before the list is offered on, so the news pass below never
            # sees this sighting as anything to light the handle for.
            self._behold_attachment(one.key)
        self._remember_attachments()

    def attachments(self) -> list[attachrecords.Attachment]:
        """Every image this session has seen and still lists, newest first."""
        return attachrecords.visible(self._all_attachments().values())

    def _all_attachments(self) -> dict[str, attachrecords.Attachment]:
        """The whole list including what has been struck off, which is what
        gets saved: a tombstone is the only thing that keeps a removed image
        removed once the transcript is read again (see attachrecords.strike).

        Three sources, folded in the order they can know things: what a
        previous run saved, what this run has been shown, and what the
        transcript scan has noticed. Order only decides tie-breaks — a
        caption may only come from a lightbox sighting and a context snippet
        only fills an empty slot, whichever arrived first.
        """
        live = attachrecords.union(self._restored_attachments, self._attachments)
        everything = attachrecords.fold(live, *self._scanned_attachments)
        return attachrecords.strike(everything, self._struck_attachments)

    def _harvest_attachments(self) -> None:
        """Take the images the last transcript update noticed. On the main
        loop, from `_on_transcript_landed`, with the scan itself already done on the
        update thread."""
        scanned = self._transcript.attachments()
        if scanned == self._scanned_attachments:
            return
        self._scanned_attachments = scanned
        self._remember_attachments()

    def restore_attachments(self, records: object) -> None:
        """Re-adopt the images saved for this session by a previous run.

        A union rather than an assignment, and for the same reason the PRs
        need one: the window calls this once the tab's session is known,
        which can be after the session has already shown something (a
        `--continue` tab resolves its id a moment into its first turn).
        """
        restored = attachrecords.from_records(records)
        if restored:
            self._restored_attachments = restored
        # Then offer the list up again from scratch, even when nothing was
        # saved and even when it reads exactly as it did a moment ago: this
        # is also the call that flushes what a tab collected *before* it knew
        # which session it was. Those sightings were emitted into a window
        # that had nowhere to write them, and the guard below would never
        # offer them a second time on their own.
        self._saved_attachment_records = []
        self._remember_attachments()

    def forget_attachment(self, key: str) -> None:
        """Strike one image off this session's list (the panel's own "Remove
        From List").

        Marked rather than deleted, because deleting it doesn't last: the
        message that mentioned the image is still in the transcript, so the
        next scan of it — this poll, or the one on the first poll of
        tomorrow's run — would hand the row straight back. The record stays
        in the file wearing a tombstone, and everything that shows the list
        passes over it.
        """
        self._struck_attachments = self._struck_attachments | {key}
        self._remember_attachments()

    def _remember_attachments(self) -> None:
        """Hand the list to the window, which saves it against the session —
        but only when it actually reads differently, so a picture shown twice
        with nothing new to say about it writes nothing to disk. An open panel
        is refreshed on the same terms: an unchanged list has nothing to
        redraw, and the diff in `set_records` keeps the rest of it in place.

        What is saved is the whole list, struck-off records included; what
        the panel is given is only what it shows."""
        everything = self._all_attachments().values()
        records = attachrecords.to_records(everything)
        if records == self._saved_attachment_records:
            return
        self._saved_attachment_records = records
        shown = attachrecords.visible(everything)
        if self._attachments_view is not None:
            self._attachments_view.set_records(shown)
        self._consider_attachments_dock(shown)
        self._note_attachment_news(shown)
        self.emit("attachments-changed", records)

    # -- what's new in it -----------------------------------------------------

    def _attachments_showing(self) -> bool:
        """Whether the panel is in front of somebody right now.

        Mapped, rather than `attachments_open`: a docked page in an
        unselected tab, in a hidden strip, or in a session tab that isn't the
        one being looked at is a panel nobody is reading, and a picture
        landing in one of those is still news. Mapped covers all three, and
        covers the plain lowered overlay too (the revealer un-occupies itself
        once its slide-out finishes).
        """
        view = self._attachments_view
        return view is not None and view.get_mapped()

    def flash_bell(self) -> None:
        """The visual bell: tint the veil over this tab's terminal once. Rung
        by the window for a BEL from this tab or its panel shells, whether
        or not the tab is the selected one — an unselected tab's veil is
        just never seen."""
        flash(self._bell_veil)

    def _note_attachment_news(self, shown: list[attachrecords.Attachment]) -> None:
        """Light the handle for pictures that landed with nobody looking, and
        flash it for the ones that landed just now.

        A picture arriving while the panel is on screen is not news — it is a
        row landing at the foot of a list somebody is already reading — so
        that case puts the handle out instead of lighting it. Which sightings
        count as new at all is `attachrecords.unseen`'s rule, and the whole
        point of it: a restored session and the first read of a long
        transcript both deliver a history all at once, and none of it
        happened while this tab was up.

        A panel already on its way to the screen counts as showing: the
        handle it would light is about to be hidden by the dock it is
        waiting for, and a pill that flashes for a quarter of a second and
        then disappears is a signal nobody can act on.
        """
        if self._attachments_docking or self._attachments_showing():
            self._clear_attachment_news()
            return
        unseen, fresh = attachrecords.unseen(
            shown,
            noted=self._attachments_unseen,
            since=self._attachments_since,
            beheld=self._attachments_beheld,
        )
        if unseen == self._attachments_unseen:
            return
        self._attachments_unseen = unseen
        self._sync_attachments_handle()
        if fresh:
            # A pill quietly changing color at the edge of a terminal
            # somebody is reading is not something anybody notices; the flash
            # is what makes them look at it, and it settles into the lit
            # color rather than draining back to the resting one — the same
            # .bell-flash class as the visual bell, a different animation
            # under it (see themes._apply_dynamic_theme_css).
            flash(self._attachments_btn)

    def _behold_attachment(self, key: str) -> None:
        """*key* has been up in the lightbox, so it can never be news here:
        a pill lighting up to say "there is a picture you haven't seen"
        about the picture filling the screen is the handle crying wolf.

        A set of keys rather than a nudge to the baseline, because the
        showing does not arrive alone: the agent's reply usually goes on to
        mention the same picture, and the transcript scan dates that
        sighting whenever the message lands — seconds, sometimes minutes,
        after the lightbox went up. A moment would be outrun by its own
        echo; the key holds for the rest of the run (a fresh run starts
        with its baseline at the tab's opening, which covers history).

        Direct, not routed through the news pass: a picture re-shown with
        nothing new to say writes no record (`_remember_attachments`'s
        guard), so a key already counted unseen has to be let go of here.
        """
        self._attachments_beheld.add(key)
        if key in self._attachments_unseen:
            self._attachments_unseen = self._attachments_unseen - {key}
            self._sync_attachments_handle()

    def _clear_attachment_news(self) -> None:
        """The panel is on screen, so nothing in it is unseen any more.

        Hung off the view's `map`, which is every way it can arrive — the
        handle raising it, a dock, its strip being revealed, its session tab
        coming back into view — rather than off the handle's click, which is
        only one of them.

        The baseline moves along with the set, and has to: the set is only
        what has been *announced*, while `attachrecords.unseen` re-reads the
        whole list on every change. A baseline left back at the tab's opening
        would hand every picture this session has shown back as news the next
        time anything landed — the handle would light for images it had
        already shown somebody, over and over, and only a session that had
        never opened the panel would ever be right.
        """
        self._attachments_since = time.time()
        if not self._attachments_unseen:
            return
        self._attachments_unseen = set()
        self._sync_attachments_handle()

    def _sync_attachments_handle(self) -> None:
        """Dress the handle for what it is holding: lit says there is
        something new, and the tooltip — the only room on an 18px pill for a
        number — says how much. It hides entirely while the panel is docked —
        a panel already on screen needs no handle to raise it."""
        self._attachments_btn.set_visible(self._attachments_page is None)
        count = len(self._attachments_unseen)
        if count:
            self._attachments_btn.add_css_class("unseen")
        else:
            self._attachments_btn.remove_css_class("unseen")
        self._attachments_btn.set_tooltip_text(
            # One form rather than an ngettext pair: po/generate.py writes
            # flat msgid/msgstr, so a plural msgid is a string no language
            # ever gets, and the number is parenthesized here anyway.
            _("Images and files this session has seen ({n} new)").format(n=count)
            if count
            else _("Images and files this session has seen")
        )

    # -- opening itself -------------------------------------------------------

    def _consider_attachments_dock(self, shown: list | None = None) -> None:
        """Dock the gallery beside this session without being asked, when
        the tab has the room to give it a column for nothing.

        Two things have to be true, and each is asked at the moment it can
        change: a picture has landed *while this tab was up* (the list, on
        every change to it), and the terminal is sitting on gutter wide
        enough for a column of its own (the dock's own `room_for_a_column`,
        asked again after every map — a tab that was too narrow when the
        picture arrived, or was never on screen to be measured, gets its
        chance when it is looked at; see `_recheck_attachments_room`). A
        history handed over by a resumed session is not a picture landing
        (see `attachrecords.landed_since`), and neither is anything at all
        on a tab with no images in it.

        Once per tab: the arming flag is put out by the panel being opened,
        whichever way it opened — this, the handle, Ctrl+', the chrome's
        dock button, a saved layout bringing one back — so a panel closed
        again is never re-opened over somebody, and a session whose images
        arrive in a burst gets one panel rather than one per picture.

        The dock itself waits `_PR_PAGE_SETTLE_MS` for the same reason the
        automatic PR page does: this can arrive inside the transcript
        update that found the picture, and carving a strip out of the dock
        in that same breath is the relayout GTK's Wayland backend segfaults
        on.
        """
        if not self._attachments_autodock or not self._attachments_armed:
            return
        if self._attachments_docking or self.attachments_open():
            return
        if not self._saved_attachment_records:
            return  # nothing to show; cheap out before folding the list
        if shown is None:
            shown = self.attachments()
        if not attachrecords.landed_since(shown, self._attachments_born):
            return
        if not self._dock.room_for_a_column():
            return  # stays armed: the next picture, or the next map, re-asks
        self._attachments_docking = True
        GLib.timeout_add(_PR_PAGE_SETTLE_MS, self._autodock_attachments)

    def _recheck_attachments_room(self) -> None:
        """The map path: this tab has been switched to, so ask again — from
        a beat later, since a tab that has just been shown measures 0 until
        the frame that allocates it, and the answer now would be "no room"
        every time.

        One outstanding re-check at a time: switching back and forth between
        two tabs is a stream of maps, and each one would otherwise leave a
        timer behind it."""
        if not self._attachments_autodock or not self._attachments_armed:
            return
        if self._attachments_remap or self._attachments_docking:
            return
        self._attachments_remap = True
        GLib.timeout_add(_ATTACH_ROOM_SETTLE_MS, self._reconsider_attachments_dock)

    def _reconsider_attachments_dock(self) -> bool:
        self._attachments_remap = False
        self._consider_attachments_dock()
        return GLib.SOURCE_REMOVE

    def _autodock_attachments(self) -> bool:
        """Open the panel the wait above was for, if the room and the reason
        are both still there — a tab closed inside the wait, a panel opened
        by hand, a window dragged narrow. Nothing is disarmed by giving up:
        the next picture asks again."""
        self._attachments_docking = False
        if self.get_root() is None or self.attachments_open():
            return GLib.SOURCE_REMOVE
        if not self._dock.room_for_a_column():
            return GLib.SOURCE_REMOVE
        # Focus stays in the terminal: a panel nobody asked for must not take
        # the next thing typed at the agent.
        self.dock_attachments(focus=False)
        return GLib.SOURCE_REMOVE

    # -- the attachments panel ------------------------------------------------

    def attachments_open(self) -> bool:
        """Whether the panel is up anywhere: raised over the terminal, or
        docked as a panel tab — which counts as open even while its strip is
        hidden, the same way a docked composer does. Fronting it is what the
        handle then means."""
        return (
            self._attachments_page is not None
            or self._attachments_opening
            or (
                self._attachments_revealer is not None
                and self._attachments_revealer.get_reveal_child()
            )
        )

    def toggle_attachments(self) -> None:
        """The handle: raise the panel, or lower one already up.

        A *docked* panel is fronted instead of lowered — one panel per tab,
        and this is how it is found again when another tab in its strip is
        showing (or when the whole strip is hidden). Closing it stays where
        closing a panel tab lives, on the tab itself.
        """
        if self._attachments_page is None and self.attachments_open():
            self.close_attachments()
        else:
            self.open_attachments()

    def _ensure_attachments_panel(self) -> attachpanel.AttachmentsView:
        """Build the panel and its overlay slot on first use. The revealer
        rides content_overlay — the same width-clamped overlay the composer
        and the two floating buttons use — anchored to the right edge, so the
        panel slides in over the terminal's own right margin."""
        if self._attachments_view is not None:
            return self._attachments_view
        view = attachpanel.AttachmentsView(
            open_image=self._show_attachment,
            forget=self.forget_attachment,
            notify=self.feed_message,
        )
        view.set_size_request(attachpanel.PANEL_WIDTH, -1)
        view.connect("close-requested", lambda *_a: self.close_attachments())
        view.connect(
            "dock-toggle-requested", lambda *_a: self._toggle_attachments_dock()
        )
        # On screen is seen: the badge is cleared by the panel actually
        # reaching a screen, whichever way it got there.
        view.connect("map", lambda *_a: self._clear_attachment_news())
        self._attachments_view = view
        revealer = Gtk.Revealer(
            # SLIDE_LEFT is where it travels, not where it comes from: the
            # panel enters moving leftward, i.e. in from the right edge.
            transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
            halign=Gtk.Align.END,
            child=view,
            visible=False,
        )
        # Once the hide transition finishes, stop occupying the overlay: an
        # invisible-but-revealable widget would still shadow the terminal's
        # right edge from pointer events (the composer's lesson, bottom edge).
        revealer.connect("notify::child-revealed", self._on_attachments_revealed)
        self._attachments_revealer = revealer
        self._content_overlay.add_overlay(revealer)
        return view

    def _on_attachments_revealed(self, revealer: Gtk.Revealer, _pspec) -> None:
        if not revealer.get_child_revealed():
            revealer.set_visible(False)

    def open_attachments(self) -> None:
        """Slide the gallery in over the terminal's right edge, filled with
        whatever this session has seen so far."""
        if self._attachments_page is not None:
            # One panel per tab: docked, the handle fronts the page —
            # revealing a hidden strip if that is where it lives — rather
            # than raising a second copy of it over the terminal.
            self._dock.reveal_page(self._attachments_page)
            return
        if self.attachments_open():
            return
        self._attachments_armed = False  # opened; it never opens itself again
        view = self._ensure_attachments_panel()
        view.set_records(self.attachments())
        revealer = self._attachments_revealer
        revealer.set_visible(True)
        self._attachments_opening = True

        # Revealed from an idle after the (fresh) child maps, or the first
        # open skips its slide and just appears (the sidebar's lesson).
        def reveal() -> bool:
            if not self._attachments_opening:
                return GLib.SOURCE_REMOVE  # closed again before it ever showed
            self._attachments_opening = False
            revealer.set_reveal_child(True)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def close_attachments(self) -> None:
        """Lower the panel. Docked, the same request funnels through the
        page's tab close, whose `page_closed` hook lands in
        `_on_attachments_page_closed` and takes the view back to the overlay
        slot the handle raises."""
        if self._attachments_page is not None:
            self._dock.close_page(self._attachments_page)
            return
        if not self.attachments_open():
            return
        revealer = self._attachments_revealer
        if self._attachments_opening:
            # Never made it to the screen, so nothing will slide out and no
            # child-revealed notify is coming: un-occupy the overlay by hand,
            # or the slot shadows the terminal's right edge from then on.
            self._attachments_opening = False
            revealer.set_visible(False)
        revealer.set_reveal_child(False)
        self.grab_terminal_focus()

    def _show_attachment(
        self,
        one: attachrecords.Attachment,
        path: str,
        navigate: Callable[[int], None],
    ) -> None:
        """Open a row's picture in the lightbox, with the caption it was
        shown under. Same editor gating as every other image this tab opens:
        the button appears only for a file this session could edit — which a
        downloaded copy of a remote image never is. *navigate* is the panel's
        arrow-key hook, walking its gallery to the previous/next picture."""
        can_edit = self.can_open_in_editor(path)
        on_open = None
        if can_edit:

            def on_open() -> None:
                self.activate_action(
                    "win.open-in-editor", GLib.Variant("(sii)", (path, 0, 0))
                )

        present_image_lightbox(
            self,
            path,
            can_open_in_editor=can_edit,
            on_open_in_editor=on_open,
            caption=one.caption or one.context,
            origin=one.origin if one.remote else None,
            navigate=navigate,
        )

    def _toggle_attachments_dock(self) -> None:
        """The panel chrome's dock/float button."""
        if self._attachments_page is None:
            self.dock_attachments()
        else:
            self.undock_attachments()

    def dock_attachments(self, focus: bool = True) -> None:
        """Move the live panel out of the overlay into a tab of its own
        beside the terminal — the composer's dock on the other axis:
        reparented, never rebuilt, so the previews already decoded and the
        place the list was scrolled to ride along. Join-don't-split places
        it, which beside a terminal usually means the strip a pull request is
        already open in; only a bare right edge is split for it.

        *focus* False leaves the keyboard alone, which is how the panel
        docks itself (see `_consider_attachments_dock`)."""
        if self._attachments_page is not None:
            self._dock.reveal_page(self._attachments_page)
            return
        self._attachments_armed = False  # opened; it never opens itself again
        view = self._ensure_attachments_panel()
        view.set_records(self.attachments())
        revealer = self._attachments_revealer
        # A reveal still in flight is called off rather than left to fire at
        # a revealer this is about to empty (see open_attachments).
        self._attachments_opening = False
        revealer.set_reveal_child(False)
        revealer.set_visible(False)
        revealer.set_child(None)
        view.set_docked(True)
        self._attachments_page = attachpanel.AttachmentsPage(
            view, on_closed=self._on_attachments_page_closed
        )
        self._sync_attachments_handle()
        self._dock.open_page(self._attachments_page, side="right", focus=focus)

    def undock_attachments(self) -> None:
        """Raise the docked panel back over the terminal (the chrome's float
        button). The page's close then runs with the view already rescued, so
        `_on_attachments_page_closed` recognizes it as an undock and leaves
        the overlay it was just put back into alone."""
        page = self._attachments_page
        if page is None:
            return
        self._attachments_page = None
        self._sync_attachments_handle()
        view = page.take_view()
        view.set_docked(False)
        revealer = self._attachments_revealer
        revealer.set_child(view)
        self._dock.close_page(page)
        revealer.set_visible(True)

        def reveal() -> bool:
            revealer.set_reveal_child(True)
            # Unlike an open from the handle, which deliberately leaves the
            # keyboard in the terminal, this one has it: the button that was
            # just pressed is inside the panel, and the page under it is
            # going away.
            view.focus_list()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def _on_attachments_page_closed(self, page: attachpanel.AttachmentsPage) -> None:
        """The docked panel's tab is closing for real (its X, a bulk close,
        or the chrome's close routed through the dock): take the live view
        back to the (lowered) overlay slot, so the handle has something to
        raise again. Nothing is rescued *out* of it, unlike the composer's
        draft — the list belongs to the session, not to the panel."""
        if page is not self._attachments_page:
            return  # an undock already rescued the view; just a close now
        self._attachments_page = None
        self._sync_attachments_handle()
        view = page.take_view()
        view.set_docked(False)
        self._attachments_revealer.set_child(view)
        self.grab_terminal_focus()

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
                remoteprs.invalidate(pr.url)
        self._request_update()

    def note_run_finished(self) -> None:
        """The agent's output stopped: re-ask GitHub about this session's PRs.

        The window calls this on the same edge that flags the row unread (see
        _on_session_finished), because a finished run is the likeliest moment
        for a pull request to have moved and the least likely one for anybody
        to be watching it: the turn that just ended is the one that pushed the
        branch, opened the PR or answered a review, and GitHub's answer — new
        checks, a comment, a mergeability verdict — lands seconds later. The
        row's mark, the chips and any PR page open beside them would otherwise
        show the pre-run answer until a TTL ran out or somebody went and asked.

        Both halves keep their own throttles (`refresh_pr_statuses` and
        `PrViewPage.refresh_if_stale`), so a session that goes quiet between
        every permission prompt costs one round of `gh` calls rather than one
        per pause. Only the *status* is re-asked, as on arrival: looking for a
        PR the transcript never mentioned stays with the refresh button, whose
        click says to go and look.
        """
        self.refresh_pr_statuses()
        for page in self._dock.pages():
            # A page in a hidden strip (or an unselected panel tab) refetches
            # when it is next shown, on its own "map" — spending gh calls on
            # a conversation nobody has in front of them is what that hook is
            # there to avoid.
            if getattr(page, "page_kind", None) == "pr" and page.get_mapped():
                page.refresh_if_stale()
        # The git page compares the tree's signature on the footer's 2 s tick
        # anyway; asking now makes the commit the agent just made show up on
        # the finish edge instead of up to two seconds later.
        page = self._git_page
        if page is not None and page.get_mapped():
            page.poll_tick()

    # -- graceful close ----------------------------------------------------

    def feed_child_text(self, text: str) -> None:
        self.session.write_text(text)

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
        syntax, and the file name inside it is untrusted repo content.

        The new-chat screen is the one place with no agent and still an
        input box: its composer is where the prompt is being written, so
        the mention lands there (as the screen's own attach button lands
        its pick — see _on_composer_file_chosen). The path resolves
        against the project directory the session will start in."""
        if self._new_chat is None and not self._agent_is_running():
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
        # On the new-chat screen the screen's composer is that box.
        if self._new_chat is not None:
            self._new_chat.composer.insert_mention(reference + " ")
            return
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
        column, row = self.session.screen.cursor()
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
        return self.session.screen.row_text(row, end_column)

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

    def dismiss_raised_panels(self) -> None:
        """Lower any panel raised *over* the terminal — the composer and/or
        the attachments gallery — the way clicking past a popover dismisses
        it. Docked pages are fixtures beside the terminal, not stand-ins in
        front of it, so they stay put; the composer's draft goes back into
        the CLI's box exactly as its own close would put it."""
        if self._composer_page is None and self.composer_open():
            self.close_composer()
        if self._attachments_page is None and self.attachments_open():
            self.close_attachments()

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
            # The chrome's model picker, persistent for the button that owns
            # it (its content refills itself each show); what a pick means —
            # posting the switch command to the chat — is this tab's business,
            # like the view's every other signal.
            model_popover=(
                modelmenu.new_model_popover(
                    lambda: self._footer_model, self.switch_model
                )
                if self._can_switch_model()
                else None
            ),
            # Its effort picker, on the same terms.
            effort_popover=(
                modelmenu.new_effort_popover(
                    lambda: self._footer_effort, lambda: self._footer_model, self.switch_effort
                )
                if self._can_switch_effort()
                else None
            ),
        )
        self._composer.set_enter_sends(self._composer_enter_sends)
        self._composer.set_spell_click(self._composer_spell_click)
        self._composer.set_font(self._composer_font)
        self._composer.set_model_name(
            short_name(self._footer_model) if self._footer_model else None
        )
        self._composer.set_effort_name(
            modelmenu.effort_label(self._footer_effort) if self._footer_effort else None
        )
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

        A draft an earlier close couldn't hand back to the terminal goes in
        first (`_restore_stashed_draft`), and the cut — should there be one
        — lands after it, cursor at the end: every way into the composer
        appends, so typing carries on from the end of whatever is there.

        The seeding lands a beat after the panel does, because a cut can't
        be taken off a screen that is still moving (see _begin_cut); the
        composer opens on the spot either way."""
        if self._new_chat is not None:
            # The screen's composer *is* this tab's composer until the first
            # prompt goes: Ctrl+. just puts the keyboard back in it.
            self._new_chat.focus()
            return
        if self._composer_page is not None:
            # One composer per tab: docked, the button fronts the page —
            # revealing a hidden home strip if that's where it lives —
            # rather than raising a second box over the terminal.
            self._dock.reveal_page(self._composer_page)
            return
        if not self._agent_is_running():
            self.feed_message(_("Composer: the agent isn't running in this tab"))
            return
        # An open already in flight is not opened again: emptying the box a
        # second time would take with it the character that asked for the
        # first one (type_into_composer, whose keys can outrun the idle
        # below by a whole frame).
        if self.composer_open() or self._composer_opening:
            self._composer.focus_view()
            return
        # A box holding a paste the CLI folded away — one of the user's
        # own, an image — is one the open-cut can't read: cutting it would
        # cost them the paste. The composer stays down over such a box (the
        # cut's settled read checks again, for what this glance can miss).
        if self._foreign_paste_in_box():
            self.feed_message(_("Composer: the input box holds a paste Collins can't read"))
            return
        composer = self._ensure_composer()
        composer.set_text("")
        self._restore_stashed_draft(composer)
        revealer = self._composer_revealer
        revealer.set_visible(True)
        self._composer_opening = True

        # Revealed from an idle after the (fresh) child maps, or the first
        # open skips its slide and just appears (the sidebar's lesson).
        def reveal() -> bool:
            self._composer_opening = False
            revealer.set_reveal_child(True)
            composer.focus_view()
            # The cut starts from here rather than above it: every round of
            # it asks whether the composer is still open before touching
            # the CLI's box, and until this line it isn't.
            self._begin_cut(composer)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(reveal)

    def _typing_opens_composer(self, keyval: int, state: Gdk.ModifierType) -> bool:
        """Whether this key press is the one that raises the composer, the
        composer_on_typing setting says so, and it has now been typed there
        instead of into the terminal.

        The keyboard's half of the answer is `composerkeys` (a character,
        not a chord, and not one of the input box's own openers); the
        screen's half is `takes_prompt`, the same "an empty agent input box
        is what a keystroke would land in" this app types prompts on. That
        is the whole safety gate, and it is the right one: a permission
        dialog, a menu, a line already half written and a terminal with no
        agent in it read as no, so their keys go where they were aimed —
        while an agent mid-turn with an empty box reads as yes, which is
        where a composer earns its keep (verified against the CLI: the box
        redraws empty a beat after a prompt is sent, and stays that way for
        the turn).

        Asked of every key press the terminal doesn't otherwise claim, so
        The agent-liveness check the composer's own open makes is repeated
        here rather than left to it: an open that bails has already
        swallowed the key by then. Failing here — or a `type_into_composer`
        that can't land the character after all — answers False, and a
        False lets the key carry on to VTE exactly as if none of this
        existed.

        Asked of every key press the terminal doesn't otherwise claim, so
        the two reads come last, behind the setting and a look at the key,
        neither of which touches VTE or /proc. Both are paid once per
        composer: the keys after the first go to a focused composer, not
        here.
        """
        if not self._composer_on_typing:
            return False
        code = Gdk.keyval_to_unicode(keyval)
        char = chr(code) if code else ""
        if not composerkeys.typing_opens_composer(char, int(state)):
            return False
        if not self.takes_prompt() or not self._agent_is_running():
            return False
        return self.type_into_composer(char)

    def type_into_composer(self, text: str) -> bool:
        """Put *text* in the composer as typed, raising it if it isn't up,
        and answer whether it landed.

        Opening is left to `open_composer` — the docked case, the agent
        gate and the open-cut all keep their say — and the text goes in
        after it, into the box that open just emptied — or, when a close
        stashed a draft the terminal wouldn't take, after that draft. The
        cut underneath is no hazard: this is only ever reached at an empty
        input box, so it finds nothing to seed. (Were the box to fill in
        the beat it takes anyway — a mention landing, the agent redrawing —
        `seed_text` appends that too, cursor at the end.) A docked composer
        left mid-edit takes the keystroke at its end as well, not at the
        cursor: text typed at the terminal continues the box.

        Focus follows the text, docked composer included: what the keyboard
        writes into is where the rest of the keyboard — backspace, the
        arrows — has to work too.

        Whether the open took is read off the panel, never off
        `self._composer`: the view is built once and kept for the tab's
        life, so a tab that has ever opened one has a non-None view even
        while nothing is on screen. Docked or already up, it is open now;
        freshly raised, it is open an idle from now (`_composer_opening`);
        anything else means `open_composer` turned the request down — the
        agent has left the terminal — and the character belongs back in
        VTE's hands.
        """
        self.open_composer()
        if self._composer is None or not (self.composer_open() or self._composer_opening):
            return False
        self._composer.insert_typed(text)
        # A floating composer is focused by its own reveal, an idle away;
        # this is for the docked one, which the open only fronted. Asking
        # twice costs nothing, and asking too early (before the panel maps)
        # simply doesn't take — the reveal's own call is the one that does.
        self._composer.focus_view()
        return True

    def close_composer(self, restore: bool = True) -> None:
        """Lower the composer. Its text goes back where it came from —
        pasted into the CLI's box, unsubmitted, in pieces small enough that
        the CLI shows every line rather than a "[Pasted text]" stand-in
        (see `_restore_or_stash`) — unless
        *restore* is False (sending already emptied it) or the agent has
        left the terminal, which stashes it instead of typing it: what's
        there now is a shell prompt, where a pasted draft isn't a draft,
        it's commands (a shell without bracketed paste runs each line as
        it lands). The stash is not a loss — the next composer this tab
        opens is seeded with it (see `_stash_draft`).

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
        self.session.cancel_cut()  # the box is about to hold this text again
        self._composer_revealer.set_reveal_child(False)
        if restore:
            self._restore_or_stash(text)
        self.grab_terminal_focus()

    def _restore_or_stash(self, text: str) -> None:
        """A closing composer's draft goes back into the CLI's input box,
        or — when there is no agent there to take it — into the stash.

        Both closes (overlaid and docked) end here so the two can't drift:
        a draft is never dropped on the floor, whichever way the panel went
        away. How it goes back into the box — in pieces the CLI shows in
        full, and what a folded piece is recorded as — is the session's
        (`Session.restore_draft`).
        """
        if not self.session.restore_draft(text):
            self._stash_draft(text)

    def _foreign_paste_in_box(self) -> bool:
        """Whether the CLI's box holds a paste Collins can't read — asked
        before a composer is raised over it (`Session.foreign_paste_in_box`)."""
        return self.session.foreign_paste_in_box()

    def _stash_draft(self, text: str) -> None:
        """Keep a draft the terminal wouldn't take, for the next composer.

        The stash is whatever the *last* undeliverable close held, never a
        queue of them: the one that just came off the screen is the one the
        user was writing, and a box they emptied before closing it leaves
        nothing behind to come back later.

        Announced to the window on every change, which files it under this
        tab's session (`AppState.set_session_draft`) — so the draft survives
        the tab being closed and the app being quit, and an emptying is a
        deletion there too. A tab with no session id yet keeps it in memory
        until one arrives (see `restore_composer_draft`).
        """
        stashed = composerkeys.stashable_draft(text)
        if stashed == self._composer_stash:
            return
        self._composer_stash = stashed
        self.emit("draft-changed", stashed)

    def _restore_stashed_draft(self, composer: ComposerView) -> None:
        """Seed an opening composer with the draft an earlier close stashed.

        Taken, not copied: a draft that has made it back on screen is the
        composer's again, and a second open shouldn't resurrect it over
        whatever came after. An open that finds text already in the box
        leaves the stash where it is (`draft_to_restore`) — nothing typed
        is overwritten, and the draft keeps waiting for an empty one.
        """
        draft = composerkeys.draft_to_restore(
            self._composer_stash, composer.peek_text()
        )
        if not draft:
            return
        self._composer_stash = ""
        self.emit("draft-changed", "")
        composer.set_text(draft)

    def restore_composer_draft(self, draft: str) -> None:
        """Adopt the draft a previous run saved for this session.

        Called by the window when the tab opens, and again for a fresh tab
        the moment its session id resolves — which is why a stash already in
        hand wins and is handed straight back instead: it was written this
        run, after whatever is on disk, and until the id landed the window
        had nowhere to file it (the attachments list has the same two jobs,
        for the same reason).
        """
        if self._composer_stash:
            self.emit("draft-changed", self._composer_stash)
            return
        self._composer_stash = composerkeys.stashable_draft(draft)

    def capture_composer_draft(self) -> str:
        """This tab's unsent prompt, for the window's close-time save.

        An open composer is the live one — quitting with a box full of text
        is exactly the case a stash never sees, because nothing closes the
        panel on the way out — and the stash answers for every other tab.
        The two are never both filled: an open consumes the stash into the
        box it is seeding.
        """
        live = self._composer.peek_text() if self._composer is not None else ""
        return composerkeys.stashable_draft(live) or self._composer_stash

    def _on_composer_send(self, _view, text: str) -> None:
        """Send closes first, then submits — the panel is a stand-in for
        the CLI's input box, and the submitted prompt should land in view,
        not behind a panel. Nothing but whitespace just closes. The submit
        itself — re-gated on the agent still being in the terminal, waiting
        out an open-cut still settling, finishing one still proving the box
        empty — is the session's (`Session.send_composed`); an undeliverable
        send keeps the panel up with the draft in it, losing nothing.

        Docked, send never closes: the page is a fixture, not a stand-in
        raised over the input box, so the buffer clears and the page stays
        for the next prompt.
        """
        docked = self._composer_page is not None
        if not text.strip():
            if not docked:
                self.close_composer()
            return

        def clear() -> None:
            self._composer.set_text("")
            if not docked:
                self._composer_revealer.set_reveal_child(False)

        self.session.send_composed(text, clear)

    def _sync_composer_overlay_btn(self) -> None:
        """Show the floating composer button only when it has something to do:
        the user setting is on, the provider has a readable input box, and the
        composer isn't already docked — a panel on screen needs no button to
        raise it."""
        self._composer_overlay_btn.set_visible(
            self._composer_overlay_setting
            and self._provider_has_prompt_box()
            and self._composer_page is None
        )

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
        strip takes the page as a tab; only an empty axis splits.

        A dock that is this tab's first composer builds the view empty, so
        a stashed draft is seeded here too — the same open by another
        door."""
        if self._composer_page is not None:
            self._dock.reveal_page(self._composer_page)
            return
        composer = self._ensure_composer()
        self._restore_stashed_draft(composer)
        revealer = self._composer_revealer
        revealer.set_reveal_child(False)
        revealer.set_visible(False)
        revealer.set_child(None)
        composer.set_docked(True)
        self._composer_page = ComposerPage(composer, on_closed=self._on_composer_page_closed)
        self._sync_composer_overlay_btn()
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
        self._sync_composer_overlay_btn()
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
        agent's input box unsubmitted, or is stashed for the next composer
        when the agent has left the terminal (see `close_composer`)."""
        if page is not self._composer_page:
            return  # an undock already rescued the view; just a close now
        self._composer_page = None
        self._sync_composer_overlay_btn()
        view = page.take_view()
        view.set_docked(False)
        self._composer_revealer.set_child(view)
        text = view.take_text()
        self.session.cancel_cut()  # as in close_composer: the text goes back
        self._restore_or_stash(text)
        self.grab_terminal_focus()

    def _begin_cut(self, composer: ComposerView) -> None:
        """Take the typed-but-unsent prompt out of the CLI's input box and
        into *composer* — the open-cut, a chain of screen reads that settle
        before they erase and verify after (`Session.begin_cut`). The
        composer is reached through a `_ComposerCut` for as long as it is
        still this tab's and still open."""
        self.session.begin_cut(_ComposerCut(self, composer))

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
        # a mention with nowhere to land is dropped. On the new-chat screen
        # the screen's own composer is the one that asked.
        if self.get_root() is None:
            return
        if self._new_chat is not None:
            composer = self._new_chat.composer
        elif self.composer_open():
            composer = self._composer
        else:
            return
        path = gfile.get_path()
        if path is None:
            return  # a remote location — nothing the CLI could read
        reference = self.provider.file_reference(path, self.current_agent_cwd())
        if reference is None:
            self.feed_message(_("Add to chat isn't available for this file"))
            return
        composer.insert_mention(reference + " ")

    def inject_prompt(self, text: str) -> None:
        """Type *text* into the agent, send it, and put the tab in front —
        what the PR menu's prompt actions do (`Session.inject_prompt`)."""
        self.session.inject_prompt(text)

    def inject_prompt_unfocused(self, text: str) -> None:
        """Submit *text* to the agent as one bracketed paste, without taking
        focus or the view — a background spawn's start_session prompt
        (`Session.inject_prompt_unfocused`)."""
        self.session.inject_prompt_unfocused(text)

    def switch_model(self, model_id: str) -> None:
        """Post the provider's model-switch command to the chat — what a
        pick in either model menu (the footer label's, the composer's)
        means (`Session.switch_model`)."""
        self.session.switch_model(model_id)

    def switch_effort(self, effort: str) -> None:
        """Post the provider's effort-switch command to the chat — what a
        pick in either effort menu means (`Session.switch_effort`)."""
        self.session.switch_effort(effort)

    def _refocus_composer(self) -> bool:
        if self._composer is not None and self.composer_open():
            self._composer.focus_view()
        return GLib.SOURCE_REMOVE

    def takes_prompt(self) -> bool:
        """Whether a prompt sent right now would land in an empty input box
        (`Session.takes_prompt`)."""
        return self.session.takes_prompt()

    def prompt_block(self) -> str:
        """Why a prompt sent to this tab wouldn't land, or "" when it would —
        the sentence a PR menu greys its prompt actions out with
        (`Session.prompt_block`)."""
        return self.session.prompt_block()

    def entered_prompt(self) -> EnteredPrompt | None:
        """The prompt typed into the agent's input box and not yet sent, or
        None (`Session.entered_prompt`)."""
        return self.session.entered_prompt()

    def unstarted_thread(self) -> bool:
        """Whether this tab is still a New Thread with nothing in it: a
        brand-new session — not resumed, forked or continued, and its first
        prompt never sent, so no transcript has appeared to resolve a
        session id — sitting at an empty input box right now. Closing one
        loses nothing, so the window skips the active-session confirmation
        for it. Anything typed into the box (takes_prompt says no) brings
        the confirmation back.

        A tab still on its new-chat screen is unstarted by definition —
        nothing is running behind it — and what is typed there is kept as a
        draft rather than lost, so the confirmation isn't owed for that
        either."""
        if self._new_chat is not None:
            return True
        return self.session.unstarted_thread()

    def _visible_screen_text(self) -> str:
        """Everything on the terminal's visible screen, as plain text
        (`Session.visible_screen_text`)."""
        return self.session.visible_screen_text()

    def worktree_exit_prompt_keystrokes(self) -> str | None:
        """Keystrokes that accept the agent's "leaving a worktree" dialog if
        it's showing right now, or None (`Session.worktree_exit_prompt_keystrokes`)."""
        return self.session.worktree_exit_prompt_keystrokes()

    def screen_first_column(self) -> tuple[tuple[str, ...], tuple[int, int]] | None:
        """The first character of each visible screen row with the grid it
        was read at, or None with no child (`Session.screen_first_column`)."""
        return self.session.screen_first_column()

    # -- transcript --------------------------------------------------------
    #
    # The tail itself — the file monitor, the poll, the off-thread parse, the
    # PRs it collects — and the resolver are the session's; what lands is
    # shown here (_on_transcript_reset, _on_transcript_landed).

    def set_transcript_path(self, jsonl_path: str | Path | None) -> None:
        """Tail a transcript for what the tab reads out of it (touched files,
        pull requests). Used on resume, and again once a brand-new session's
        file appears on disk (`Session.set_transcript_path`)."""
        self.session.set_transcript_path(jsonl_path)

    def _on_transcript_reset(self) -> None:
        """The session was pointed at another transcript: another session's
        images go — restored again the same way as its PRs, by the window
        once this tab's session is known; the sightings this tab collected
        itself stay, they were shown in this tab's window whichever
        transcript it was pointed at at the time — and so do the chips and
        the model and effort the old one named."""
        self._restored_attachments = []
        self._refresh_pr_chips([])
        self._refresh_model_label()
        self._refresh_effort_label()

    def _on_transcript_landed(self, prs: list[PullRequest], lookup_empty: bool) -> None:
        """A transcript read landed: *prs* is what the chips show (the newest
        MAX_PR_CHIPS the session tracks, with status), *lookup_empty* a
        branch lookup that found nothing."""
        self._pr_refresh_btn.set_sensitive(True)
        # Same pane object wherever it lives (in-tab or popped out).
        self._editor.set_agent_files(self._transcript.touched_files())
        self._harvest_attachments()
        self._refresh_model_label()
        self._refresh_effort_label()
        self._refresh_pr_chips(prs)
        if lookup_empty:  # even with PRs still showing: none of them is this branch's
            self._sync_pr_refresh_tooltip(not_found=True)
        self.emit("transcript-updated")

    @property
    def transcript_path(self) -> str | None:
        """The transcript this tab is tailing, or None."""
        return self.session.transcript_path

    def finish_witness(self) -> tuple[tuple[int, int], int | None]:
        """What the transcript says right now, for the finish ledger
        (`Session.finish_witness`)."""
        return self.session.finish_witness()

    def request_transcript_update(self) -> None:
        """Re-read the transcript now rather than at the next poll — asked by
        a finish edge the window is holding for the transcript's word. A read
        already in flight is enough: its landing is the word."""
        self.session.request_update()

    def _request_update(self, discover: bool = False) -> None:
        """Ask the session for a transcript read (`Session.request_update`);
        *discover* also asks the branch which PR it has."""
        self.session.request_update(discover)

    def relocate_transcript(self, jsonl_path: str | Path) -> None:
        """Follow this tab's transcript to a new path — the CLI moved it on
        worktree entry (`Session.relocate_transcript`)."""
        self.session.relocate_transcript(jsonl_path)

    def _start_transcript_resolver(self, cwd: str | None) -> None:
        """Find the new session's transcript (`Session.start_resolver`):
        polled for as long as the tab is in the foreground, paused after ~3
        min in the background, resumed whenever the tab is brought back."""
        if not cwd:
            return
        self.connect("map", lambda *_: self.session.arm_resolver())
        self.session.start_resolver(cwd)

    # -- secondary terminal panel ------------------------------------------

    def _make_panel_strip(self) -> PanelStrip:
        """The dock's strip factory: every strip spawns this tab's shells
        (numbered dock-wide) at the agent's current working directory."""
        strip = PanelStrip(shell_factory=self._make_panel_shell)
        strip.set_cwd_lookup(self.current_agent_cwd)
        # The tab menu's "New sandboxed shell", for as long as there is a
        # box to open one in.
        strip.set_sandboxed_shell_offer(lambda: self.session.sandbox_plan_path is not None)
        # A page arriving or the last one leaving is what makes (or unmakes)
        # a new-chat screen a draft worth keeping; no-ops after the session
        # starts (see _note_new_chat_change).
        strip.connect("empty", lambda *_a: self._note_new_chat_change())
        strip.connect("page-touched", lambda *_a: self._note_new_chat_change())
        return strip

    def _make_panel_shell(self, sandboxed: bool = False) -> PanelTerminal:
        """A new shell page, its tab-title number and its persistent
        history ordinal both dock-assigned (layout restore overwrites the
        ordinal with the saved one right after). *sandboxed* builds the
        shell that runs inside this session's box — reading the plan at
        spawn, so one restored before the launch settled retries on its
        next show, and one restored into an unsandboxed tab never starts."""
        lookup = (lambda: self.session.sandbox_plan_path) if sandboxed else None
        box = (lambda: self.session.sandbox_box) if sandboxed else None
        shell = PanelTerminal(
            self._dock.next_shell_number(),
            plan_lookup=lookup,
            box_lookup=box,
            history_lookup=self._shell_history_key,
        )
        shell.hist = self._dock.next_hist_ordinal()
        return shell

    # -- the native PR page --------------------------------------------------

    def open_pr_page(
        self, pr: PullRequest, unresolved: bool = False, focus: bool = True
    ) -> None:
        """Show *pr*'s native page in a strip beside this session.

        One page per URL per tab: asking for a PR whose page is already open
        fronts that page (revealing its strip if hidden) and re-reads it,
        rather than opening a twin. With *unresolved*, the page lands on its
        first unresolved thread — the badge's deep link — which a fresh page
        honors as soon as its first fetch does.

        *focus* False shows the page without moving the keyboard, for the
        one caller nobody clicked (`_open_attached_pr_page`): a page that
        opens by itself must not take the next word typed at the agent —
        or into the composer — the same bargain the attachments autodock
        makes.
        """
        page = self._find_pr_page(pr.url)
        if page is None:
            page = self._make_pr_page(pr)
            self._dock.open_page(page, focus=focus)
        else:
            self._dock.reveal_page(page, focus=focus)
            page.refresh()
        if unresolved:
            page.reveal_unresolved()

    def open_pr_page_url(
        self, url: str, unresolved: bool = False, focus: bool = True
    ) -> None:
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
        self.open_pr_page(pr, unresolved=unresolved, focus=focus)

    def open_latest_pr_page(self) -> bool:
        """F7: show the page for the pull request this session linked most
        recently, fronting it when it is already open.

        The footer's chip row is the list and it runs oldest first (see
        `Session._collect_prs`), so the last of them is the newest thing this session
        got itself involved with — the same PR a tab with
        `open_pr_panel_on_attach` on would have opened by itself. False when
        the session has no pull request at all yet, which the caller says out
        loud: a shortcut that silently does nothing is indistinguishable from
        one that was never installed.
        """
        if not self._footer_prs:
            return False
        self.open_pr_page(self._footer_prs[-1])
        return True

    def _find_pr_page(self, url: str) -> PrViewPage | None:
        for page in self._dock.pages():
            if getattr(page, "page_kind", None) == "pr" and page.pr_url == url:
                return page
        return None

    def _make_pr_page(self, pr: PullRequest) -> PrViewPage:
        page = github_url(self._cwd)
        return PrViewPage(
            pr,
            host_factory=self._pr_action_host,
            pr_store=self._pr_store,
            home_repository=page.removeprefix("https://github.com/") if page else None,
        )

    # -- the git page ------------------------------------------------------

    def open_git_page(self, mode: str | None = None, focus: bool = True) -> bool:
        """Open — or front — this session's git page: the diff view beside
        the terminal, over the agent's working tree.

        One page per tab: a second ask fronts the page (revealing its strip
        if hidden) rather than opening a twin. *mode* None means a page
        already open keeps what it shows, and a new one opens on what the
        tree suggests — unstaged while anything in the tree is dirty, staged
        when only the index is (`gitloads.initial_mode`, fed by one `git
        status` on the click; the poll never pays for one). An explicit
        *mode* is loaded either way.

        False, doing nothing, when the agent's cwd is not inside a git
        repository — the caller announces it (the footer's branch label,
        the other way in, is already hidden then). *focus* False shows the
        page without moving the keyboard, for a caller nobody clicked.
        """
        cwd = self.current_agent_cwd()
        if repo_root(cwd) is None:
            return False
        page = self._git_page
        if page is None:
            if mode is None:
                mode = gitloads.initial_mode(*change_summary(cwd))
            page = GitPage(
                cwd_provider=self.current_agent_cwd,
                parent_provider=self._git_parent_branch,
                on_closed=self._on_git_page_closed,
                loaded=mode,
            )
            self._git_page = page
            self._bind_git_page_marks(page)
            self._dock.open_page(page, focus=focus)
        else:
            self._dock.reveal_page(page, focus=focus)
            if mode is not None:
                page.load(mode)
            else:
                page.recheck_tree()  # a page on the not-a-repo card: the tree is back (the check above)
        return True

    @property
    def git_page(self) -> GitPage | None:
        """This tab's git page while one is open (in the dock, shown or
        not), else None. What a caller that opened it through
        `open_git_page` drives afterwards (the show_diff tool)."""
        return self._git_page

    def toggle_git_page(self) -> bool:
        """F6: close the git page when the keyboard is in it, else open or
        front it. False only when there is no repository to open a page for
        (see `open_git_page`)."""
        if self.git_page_focused():
            self._dock.close_page(self._git_page)
            return True
        return self.open_git_page()

    def git_page_focused(self) -> bool:
        """Whether the git page exists and holds the focus."""
        page = self._git_page
        return page is not None and page.has_page_focus()

    def _git_parent_branch(self, cwd: str | None) -> str | None:
        """The branch the current one is measured against — what the page's
        "vs" load diffs HEAD from — when git itself shows no stack under
        HEAD (the page reads that first, gitops.stack_branches; see
        GitPage._resolve_parent): the host's rung, three deep. The
        attached PR's base first (a stacked PR whose base isn't a local
        branch is still measured against it, not trunk), then the default
        parent branch from Preferences → Git when the repository has it,
        then the repository's default branch (gitinfo.parent_branch).

        The tab's own PR records (prstatus.PullRequest) carry no base ref —
        only a fetched PR page does (prdetail's `baseRefName`) — so the base
        is what the newest PR's open page has learned, and only when the
        tree can name it (`resolve_branch`: a local or remote-tracking ref);
        a base nothing here can diff against — or a Preferences name this
        repository doesn't have — falls through rather than disabling the
        load. Until the git page fetches the base itself (the spec's second
        phase), a tab with no PR page open measures against the setting or
        the default branch."""
        return parent_branch(cwd, (self._pr_base_ref(), self._git_parent_setting))

    def _pr_base_ref(self) -> str | None:
        """The base branch of this session's newest PR, as its open page has
        fetched it; None with no PR, no page for it, or a page still loading."""
        if not self._footer_prs:
            return None
        page = self._find_pr_page(self._footer_prs[-1].url)
        return page.base_ref if page is not None else None

    def _on_git_page_closed(self, page: GitPage) -> None:
        """The page's `page_closed` hook: drop the reference, guarded on
        identity so a stale page closing late can't null a newer one."""
        if page is self._git_page:
            self._git_page = None
            self._unbind_git_page_marks(clear=True)

    def _restore_git_page(self, page: dict) -> GitPage | None:
        """A saved layout's git page, rebuilt on what it was saved showing —
        a mode, a commit or a range — with its native sidebar shown or
        hidden as it was (anything the saved dict can't name reads as
        unstaged or the sidebar shown — the layout is a preference, so
        restore never refuses on it; a "parent" key from before git alone
        named the stack is ignored). One page per tab: a duplicate entry (a
        hand-edited layout file) is refused, which drops it from the
        restored strip. The page reads its diff on its first map, so a
        restored page in a hidden strip costs nothing until it is shown."""
        if self._git_page is not None:
            return None
        self._git_page = GitPage(
            cwd_provider=self.current_agent_cwd,
            parent_provider=self._git_parent_branch,
            on_closed=self._on_git_page_closed,
            loaded=gitloads.decode_state(page),
            sidebar=gitloads.decode_sidebar(page),
        )
        self._bind_git_page_marks(self._git_page)
        return self._git_page

    # -- the diff's marks, mirrored (PR-1.11) ------------------------------------

    def _marks_key(self) -> tuple[str, bool]:
        """What this session's marks are kept under on the service: its id,
        or its handle before the id resolves."""
        if self.session_id:
            return self.session_id, True
        return self.session.handle, False

    def _bind_git_page_marks(self, page: GitPage) -> None:
        """The page's store mirrors the service's (service.diffs): seeded
        from the copy, its changes sent whole, the service's changes loaded
        into it."""
        mirror = remotediffs.mirror_for(apilink.current())
        if mirror is None:
            return
        view = page.diff_view
        key, _session = self._marks_key()
        saved = mirror.marks(key)
        if saved:
            view.load_marks(*saved)

        def send(*_args) -> None:
            key, session = self._marks_key()
            mirror.send(key, session, *view.export_marks())

        def loaded(marks) -> None:
            if self._git_page is page:
                view.load_marks(*marks)

        view.connect("notes-changed", send)
        mirror.listen(key, loaded)
        self._marks_binding = (mirror, key, loaded)

    def _unbind_git_page_marks(self, clear: bool = False) -> None:
        """The page went: its marks go with it, as they always went with the
        page (the service drops what it kept for the session)."""
        binding = getattr(self, "_marks_binding", None)
        if binding is None:
            return
        self._marks_binding = None
        mirror, key, loaded = binding
        mirror.unlisten(key, loaded)
        if clear:
            current, session = self._marks_key()
            mirror.send(current, session, [], [])

    def _make_panel_page(self, page: dict):
        """The dock's non-shell factory for layout restore (see
        paneldock.set_page_factory). A pr page's URL is persisted state and
        therefore untrusted, so it re-passes the same gate every PR URL
        passes before reaching gh; a composer entry conjures a fresh empty
        docked composer (placement persists, drafts never), an attachments
        entry a panel over this session's own saved list, and a git entry
        the page in the mode it was saved in."""
        if page.get("kind") == "composer":
            return self._restore_composer_page()
        if page.get("kind") == "attachments":
            return self._restore_attachments_page()
        if page.get("kind") == "git":
            return self._restore_git_page(page)
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
        self._sync_composer_overlay_btn()
        return self._composer_page

    def _restore_attachments_page(self) -> attachpanel.AttachmentsPage | None:
        """A saved layout's docked attachments panel, rebuilt around whatever
        this session has seen. The list is not part of the layout — it is
        saved against the session (state.json) and reaches the panel the same
        way it does in any other tab, so a restored page is filled here with
        what is known now and topped up by `_remember_attachments` once the
        tab knows which session it is. One panel per tab: a duplicate entry
        (a hand-edited layout file) is refused, which drops it from the
        restored strip."""
        if self._attachments_page is not None or self.attachments_open():
            return None
        self._attachments_armed = False  # this session's answer, already given
        view = self._ensure_attachments_panel()
        view.set_records(self.attachments())
        self._attachments_revealer.set_child(None)
        view.set_docked(True)
        self._attachments_page = attachpanel.AttachmentsPage(
            view, on_closed=self._on_attachments_page_closed
        )
        self._sync_attachments_handle()
        return self._attachments_page

    @property
    def panel_visible(self) -> bool:
        """Whether Ctrl+J's terminal is on screen — the state it toggles.
        The shortcut speaks for one shell page (the dock's
        `panel_terminal`), not for a strip: every other panel tab is
        visible whenever its own strip is."""
        return self._dock.panel_terminal_showing

    def toggle_panel(self, default_mode: str | None = None) -> None:
        if self.panel_visible:
            self.hide_panel()
        else:
            self.show_panel(default_mode)

    def show_panel(self, default_mode: str | None = None, focus: bool = True) -> None:
        """Put Ctrl+J's terminal on screen, starting (or re-pointing) its
        shell at the agent's current working directory. `default_mode`
        ("bottom" | "right") opens a new one at the app-wide last-used home
        edge; None keeps the tab's own. `focus=False` leaves keyboard focus
        where it is (session restore)."""
        # Freshly opened — no terminal bound to the shortcut at all — takes
        # the app-wide edge. One that exists keeps the edge it is on: an
        # existing terminal being brought back (or merely fronted in a row
        # showing something else) is no reason to move the panel.
        if self._dock.panel_terminal is None and default_mode in ("bottom", "right"):
            self._dock.set_home_position(default_mode)
        restore = None
        if not self._dock.ever_spawned:
            texts = self._load_panel_history()
            # First open with no saved layout: one shell per saved history
            # file, oldest ordinal first (the shells take fresh ordinals —
            # the files re-key to them on the next save).
            restore = [texts[ordinal] for ordinal in sorted(texts)] or None
        self._dock.show_panel_terminal(restore, focus=focus)
        if focus:
            GLib.idle_add(self._dock.focus_panel_terminal)
        self._note_new_chat_change()

    def hide_panel(self) -> None:
        self._dock.hide_panel_terminal()

    def panel_has_running_command(self) -> bool:
        """True when a command is running in any shell page of any strip —
        even a hidden strip's job is protected by the close confirmation."""
        return self._dock.has_running_command()

    def select_busy_panel_tab(self) -> None:
        """Front the shell page whose command is live, so the close
        confirmation's "will be terminated" points at something visible."""
        self._dock.select_busy_shell()

    def _mark_stale_sandboxed_shells(self) -> None:
        """Say so in every sandboxed shell still running in the box the
        session had before this restart. Their boxes outlive the plan they
        were built from (--die-with-parent ties one to its own pty, not to
        the session), so a grant the user has just revoked stays bound in
        them; the agent is no longer handed them (mcptools.tool_shells),
        and the user, whose scrollback they are, is told why."""
        for shell in self.panel_shells():
            if not getattr(shell, "sandboxed", False):
                continue
            plan = getattr(shell, "sandbox_plan", None)
            if plan is not None and plan != self.sandbox_plan_path:
                shell.note(
                    _("the session's sandbox was restarted — this shell still runs in the "
                      "box it was opened in, and the agent can no longer reach it")
                )

    def panel_shells(self) -> list:
        """Every shell page in this tab's dock, in spatial-then-tab order —
        maximized and stowed pages included (see PanelDock.shell_pages).
        What the read_terminal tool reads."""
        return self._dock.shell_pages()

    def open_panel_shell(self, sandboxed: bool = False):
        """A fresh shell page for the run_in_terminal tool: the Ctrl+J
        panel when the dock has no shells at all — saved history restored,
        exactly as the footer button would open it — else a new tab beside
        the last shell page. Neither takes the keyboard: the agent typing
        is not the user typing. None when no shell could be opened.

        *sandboxed* — what a sandboxed session gets — opens the shell that
        runs inside its box instead (open_sandboxed_shell, quietly), and
        never the user's own Ctrl+J shell."""
        if sandboxed:
            return self.open_sandboxed_shell(focus=False)
        if not self._dock.shell_pages():
            self.show_panel(focus=False)
            return self._dock.panel_terminal
        shell = self._dock.open_shell_page()
        self._note_new_chat_change()
        return shell

    def open_sandboxed_shell(self, focus: bool = True):
        """A fresh *sandboxed* shell page — `bwrap <this session's plan>
        -- $SHELL` — beside the last shell page, or in a strip of its own
        when the dock has none: the panel menu's and the footer chip's
        "Sandboxed shell", and what run_in_terminal from inside the box
        opens (with *focus* False: the agent typing is not the user
        typing). None when this tab runs no box."""
        if self.sandbox_plan_path is None:
            return None
        shell = self._dock.open_shell_page(sandboxed=True)
        if shell is None:
            shell = self._make_panel_shell(sandboxed=True)
            side = "below" if self._dock.home_position == "bottom" else "right"
            self._dock.open_page(shell, side=side, focus=focus)
            shell.open_shell(self.current_agent_cwd())
        else:
            self._dock.reveal_page(shell, focus=focus)
        self._note_new_chat_change()
        return shell

    def reveal_panel_shell(self, shell) -> None:
        """Put *shell* on the user's screen without moving keyboard focus
        (PanelDock.reveal_page, on its quiet setting)."""
        self._dock.reveal_page(shell, focus=False)

    def move_focused_panel_page(self) -> None:
        """Cycle the focused panel page to the next strip (win.move-panel-page)."""
        self._dock.move_focused_page_next()

    def rotate_recent_panel_page(self) -> None:
        """Send the focused (or last-touched) panel tab to the dock's other
        axis (win.rotate-panel-page)."""
        self._dock.rotate_recent_page()

    def close_recent_panel_page(self) -> bool:
        """Close the focused (or last-touched) visible panel tab, as its own X
        would. False when this tab's dock has no page on show — Ctrl+W's cue to
        close the session tab instead (win.close-tab)."""
        return self._dock.close_recent_page()

    def _load_panel_history(self) -> dict[int, str]:
        """Saved shell scrollbacks by history ordinal for this session —
        forks don't restore (their panel would clash with the original
        tab's) and never save."""
        history_id = self._history_id()
        if self.fork or not history_id:
            return {}
        return panelhistory.load_all(history_id)

    def _shell_history_key(self) -> str | None:
        """What a shell's history is filed under on the service: the
        tab's key, none for a fork (forks never save)."""
        return None if self.fork else self._history_id()

    def _rekey_panel_shells(self) -> None:
        """The tab's history key moved: tell the service, for every shell
        it holds (the key and the moment are the service's to act on when
        a shell exits, §3.15)."""
        key = self._shell_history_key()
        for shell in self._dock.shell_pages():
            rekey = getattr(shell, "set_history_key", None)
            if rekey is not None:
                rekey(key)

    def _history_id(self) -> str | None:
        """What this tab's panel scrollback is filed under: the session id,
        or — on the new-chat screen, where there is no session yet — the
        draft id the window assigned (None until it did: a screen that is
        not yet a draft has nothing on disk to restore or write)."""
        if self._new_chat is not None:
            return self._history_key
        return self.session_id

    def save_panel_history(self) -> None:
        """Persist each panel tab's scrollback so re-opening this session
        restores them. A panel never opened in this tab leaves prior history
        untouched; tabs closed along the way drop out of the saved set.

        The service writes it, from each shell's
        screen model (spec §3.15, `ServiceCore.write_panel_history`): the
        tab names the key and the shells, the moment being the same three
        (a draft save, the tab's close before its shells end, the quit)."""
        history_id = self._history_id()
        if self.fork or not history_id or not self._dock.ever_spawned:
            return
        sources = {shell.hist: shell.history_source() for shell in self._dock.shell_pages()}
        service_loopback().write_panel_history(history_id, sources)

    def release_panel_ptys(self) -> None:
        """The tab is closing for good: end every panel shell's pty on the
        service. After `save_panel_history`, which reads their models."""
        for shell in self._dock.shell_pages():
            release = getattr(shell, "release_pty", None)
            if release is not None:
                release()

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
        layout = panellayout.prune(layout, {"shell", "pr", "composer", "attachments", "git"})
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
        """`lookup(scope, mode) -> px` supplies the app-wide last-set strip
        size for one scope ("home" | "page") on one axis, seeding splits
        this tab hasn't sized itself yet."""
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

    def current_agent_cwd(self) -> str | None:
        """Best-effort cwd of what's running in the agent terminal
        (`Session.current_agent_cwd`)."""
        return self.session.current_agent_cwd()

    def current_permission_mode(self) -> str:
        """Best-effort permission mode of the agent in this tab right now
        (`Session.current_permission_mode`)."""
        return self.session.current_permission_mode()

    def current_model(self) -> str:
        """Best-effort model of the agent in this tab right now
        (`Session.current_model`)."""
        return self.session.current_model()

    def current_effort(self) -> str:
        """Best-effort effort level of the agent in this tab right now
        (`Session.current_effort`)."""
        return self.session.current_effort()

    def _agent_is_running(self) -> bool:
        """Whether the provider's CLI is alive in this terminal right now
        (`Session.agent_is_running`)."""
        return self.session.agent_is_running()

    def owns_pid_ancestors(self, ancestors: set[int]) -> bool:
        """Whether one of *ancestors* is a process this tab's terminal runs
        (`Session.owns_pid_ancestors`)."""
        return self.session.owns_pid_ancestors(ancestors)

    def has_background_descendant(self, ignore: Collection[str] = frozenset()) -> bool:
        """Whether the agent has something still running below it right now
        (`Session.has_background_descendant`)."""
        return self.session.has_background_descendant(ignore)

    def background_descendant_cmdlines(self) -> set[str]:
        """The cmdlines of everything running directly below this tab's agent
        right now (`Session.background_descendant_cmdlines`)."""
        return self.session.background_descendant_cmdlines()

    # -- helpers -----------------------------------------------------------

    def has_running_command(self) -> bool:
        """True when something other than the shell (e.g. claude) owns the
        terminal's foreground (`Session.has_running_command`)."""
        return self.session.has_running_command()

    def apply_settings(self, settings: dict) -> None:
        font = settings.get("font") or ""
        self.terminal.set_font(Pango.FontDescription.from_string(font) if font else None)
        try:
            self.terminal.set_scrollback_lines(int(settings.get("scrollback") or 10_000))
        except (TypeError, ValueError):
            pass
        themes.apply_terminal_theme(self.terminal, settings.get("terminal_theme"))
        self._terminal_fg = themes.terminal_foreground(settings.get("terminal_theme"))
        self._theme_name = settings.get("terminal_theme")
        self._view.set_term(self._term_for_service())
        # The floating composer button can be turned off in preferences; the
        # provider gate (no readable input box = no button) still applies.
        # The setting keeps the attach button's old key: same slot, same
        # pixels, and a rename would only orphan saved preferences.
        self._composer_overlay_setting = bool(settings.get("attach_overlay_button", True))
        self._sync_composer_overlay_btn()
        self._composer_enter_sends = bool(settings.get("composer_enter_sends", True))
        self._composer_on_typing = bool(settings.get("composer_on_typing"))
        self._composer_spell_click = bool(settings.get("composer_spell_click", True))
        self._composer_font = font
        for composer in (
            self._composer,
            self._new_chat.composer if self._new_chat is not None else None,
        ):
            if composer is not None:
                composer.set_enter_sends(self._composer_enter_sends)
                composer.set_spell_click(self._composer_spell_click)
                composer.set_font(self._composer_font)
        self._easy_copy_paste = bool(settings.get("easy_copy_paste"))
        self._keys = keymap.KeyMatcher.from_settings(settings)
        self._auto_open_prs = bool(settings.get("open_pr_panel_on_attach"))
        self._attachments_autodock = bool(settings.get("dock_attachments_when_room", True))
        # Read on the click rather than baked into the menus and the PR page's
        # button (see _pr_action_host), so a switch flipped in Preferences
        # takes effect on chips and pages that were built before it.
        self._confirm_merges = bool(settings.get("confirm_merges", True))
        # Likewise read at each resolution: the page re-resolves its parent
        # on every 2 s tick (and reloads a branch diff whose base moved), so
        # a changed name reaches an open page without a push.
        self._git_parent_setting = str(settings.get("git_parent_branch") or "").strip()
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
        line = f"\r\n\x1b[1;33m[session manager]\x1b[0m {text}\r\n"
        if self._view.pty is not None:
            # Rule 2 (§3.1): the line goes into the pty's output stream on
            # the service (`paint`), so the model and every client show it.
            try:
                self._client.request({"t": "paint", "pty": self._view.pty, "text": line})
                return
            except (RequestRefused, ValueError):
                pass
        # No pty yet (a note painted before the spawn, or after the exit):
        # the widget's own screen is the only place it can go.
        self.terminal.feed(line.encode())

    def grab_terminal_focus(self) -> None:
        """Put the keyboard in the agent terminal — unless a panel page is
        maximized, in which case that page is what the tab is showing and
        the terminal is under an opaque overlay. Every road back to this
        tab runs through here (a tab switch, a dialog closing, the dock
        losing a strip that held focus), so the redirect belongs here
        rather than at each of them; the dock's focus trap catches the rest
        (see paneldock._on_root_focus_changed)."""
        maximized = self._dock.maximized_page
        if maximized is not None:
            maximized.grab_page_focus()
            return
        if self._new_chat is not None:
            # The screen is what the tab is showing; its composer is where
            # the keyboard belongs, the console being hidden behind it.
            self._new_chat.focus()
            return
        self.terminal.grab_focus()

    def _on_key_pressed(self, ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        event = ctrl.get_current_event()
        if event is None:
            return False
        keys = self._keys

        if _handle_zoom_key(self.terminal, keys, event):
            return True

        # Shift+Enter → newline. Terminals send the same byte for Enter and
        # Shift+Enter, so we emit Meta+Enter (ESC + CR), which Claude Code
        # interprets as "insert a line break" rather than "submit".
        if keys.matches("terminal.newline", event):
            self.terminal.feed_child(b"\x1b\r")
            return True

        # Easy copy & paste (Black Box-style): Ctrl+C copies when text is
        # selected — otherwise it falls through as SIGINT — and Ctrl+V pastes.
        if self._easy_copy_paste:
            if keys.matches("terminal.copy", event) and self.terminal.get_has_selection():
                self.terminal.copy_clipboard_format(Vte.Format.TEXT)
                return True
            if keys.matches("terminal.paste", event):
                self.terminal.paste_clipboard()
                return True

        # Terminal-style copy / paste / find, always on.
        if keys.matches("terminal.copy-always", event):
            self.terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True
        if keys.matches("terminal.paste-always", event):
            self.terminal.paste_clipboard()
            return True
        if keys.matches("terminal.find", event):
            self.toggle_search()
            return True

        # Last, once every chord above has passed: a plain character typed
        # at an empty agent prompt can open the composer instead of landing
        # in the CLI's box (on by default, opt-out — see _typing_opens_composer).
        if self._typing_opens_composer(keyval, state):
            return True
        return False
