# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page: hunk in a VTE beside the session, driven over its session API.

What the page shows is hunk's — hunk.dev, the terminal diff viewer, running
with the collins-git extension Collins ships as package data, which draws
the commits and files panels and binds the staging keys — and all Collins
adds is a one-row header (the branch, a breadcrumb of what is loaded,
and refresh; the tab's X closes) and the plumbing that keeps hunk pointed at the right
thing: it spawns `hunk diff --watch --extension <collins-git>` in the
agent's working tree the first time the page is shown, finds the session
hunk registered for that process (`hunk session list --json`, matched by
pid — the npm wrapper spawns the real viewer as a child, so both pids are
tried), and swaps what is loaded with `hunk session reload <id> -- diff …`
(or `-- show <ref>`) whenever Ctrl+1/2/3 or the host asks for another load:
the unstaged working tree, the index, the branch against its parent, or a
commit. When the session id can't be had (an old hunk, a daemon that never
answered), every switch is a respawn instead — slower, never wrong — and a
banner over the viewer says so, naming the foreground daemon run that prints
what the auto-spawn swallowed (hunkctl.DAEMON_DIAGNOSTIC). Before each spawn
the daemon's runtime directory is made owner-only if it isn't
(hunkctl.repair_daemon_dir): the one daemon failure seen so far.

The extension does its own loads — a click on a commit, a branch header, the
Staged section — through the same session API, and the page learns of them
the way it learns of any load it didn't make: freshness rides the tab
footer's 2 s tick, the host forwards it while the page is mapped, and the
page reloads what it shows when the index, HEAD or the parent branch moved
(gitinfo.tree_signature) — hunk's own `--watch` covers edits to files, this
covers commits and staging done from a shell or by the agent; a move the
extension made itself, and reloaded hunk for, is left alone (it records the
index mtime and HEAD it showed in the sidecar, hunkctl.shown_by_extension),
since a second reload would cancel whatever dialog the user opened next. The same tick
asks `hunk session get <id>` what the viewer has loaded, and the breadcrumb
and tab title follow *that* rather than Collins' own asks: a `show <sha>`
becomes the page's commit load (reloaded on freshness, persisted, restored
into `hunk show` — after the probe checked the commit still exists, else
the default mode), its breadcrumb naming the commit (`<sha7> <subject>`,
the subject read by the worker thread that brought the title back, one
`git log -1`, see hunkctl.commit_subject), and a load Collins has no name
for (a range between two branches) is shown by hunk's title, left alone by
the freshness reload, and reclaimed by the next Ctrl+1/2/3. A reload hunk
refuses (a parent ref that
vanished between the header and the ask, a timeout) keeps the viewer — it is
still showing what it showed — and only a reply saying the session itself is
gone respawns. Everything that leaves the main loop (the version probe, the
session list, the get, the reload) runs on a daemon thread and comes back
through GLib.idle_add behind a generation counter, so a reply to a spawn
that has since been replaced changes nothing — except a spawn's own
callback, which terminates the child it announces when nobody wants it any
more (the page closed, or was unparented, while the spawn was in flight),
since its pty is the only one the viewer will ever answer a signal on.

Beside hunk's terminal, the page draws its own commits and files panels — a
native GitSidebar (gitsidebar.py) to the left of the VTE, the two lists
over an action row — fed from what the page already knows: hunk's title
(set_context, so the ▸ row follows what hunk has loaded, not the last
click), the `files[]` and cursor a `session get` reply carries (the tick's
one ask, refresh_files / set_selection), the sidecar's `selection` and
`anchor` when the extension writes them (instant; the `session get`
snapshot is the fallback), the tree signature (a move refreshes the commits
list along with the diff) and the remote refs' signature (a push moves the
`↑` marks). The sidebar asks back through signals: a row click is a load
("load-requested" → load()), a file click a `session navigate` on the live
side — or, on the working tree's other side, a load of that side with the
navigate queued behind it (_pending_navigate, run once the reload lands) —
a button that needs hunk's cursor feeds the extension's key through the pty
("key-requested": `x`, `v`, escape, `D`, the VTE focused so hunk's own
confirm answers to Enter), a native mutation ("mutated": stage all, a
commit) re-seeds the signature and reloads hunk at once rather than waiting
for the tick, and a parent pick ("parent-picked") lands where the sidecar's
used to. The sidebar hides below the Adw.BreakpointBin's breakpoint
(_NARROW_MAX_WIDTH) whatever the header's toggle says, and the toggle's
state — persisted in page_state's "sidebar" — rules above it; the install
and not-a-repo cards hide it too (nothing to list), the exited card keeps
it (the commits list still works). Page-local toasts (commit results, git
errors, a navigate hunk refused) float in an Adw.ToastOverlay over the page.

A page too narrow for both of the extension's panes shows one at a time —
hunk lays its left panes out from a fixed budget (hunkctl.pane_fit: one
pane beside the diff from 73 columns, both from 100) — and the header
grows two buttons, back and forward, that walk the extension's *levels*: the
diff alone, the files pane, the commits pane (its level.ts). Each press
feeds hunk the key the extension binds (`<` up, `>` down, through the
VTE's pty — hunk has no session command that runs an extension command),
the header takes the step for granted at once (tooltips and sensitivity
follow the level, insensitive at either end and on a page too narrow for
any pane), and the extension's own word arrives through the sidecar's
`level` on the next tick. The buttons show only while the VTE's column
count says the page is narrow, re-read after every allocation (a width
sensor at the foot of the page) and on the tick; wide, the page shows
both panes as it always did. The page's minimum width holds
one pane at the default font, so a page at its narrowest is never one
where the buttons can do nothing.

The parent branch travels both ways through a sidecar file (hunkctl's
sidecar_*): Collins writes the name it resolved — the user's pick while it
resolves, else the host's automatic rung (the newest PR's base, then the
default parent from Preferences → Git, then the default branch) — and the
default branch, so the extension groups its commits the same way the
breadcrumb reads; the extension writes the user's "Set parent branch…"
pick back, and the tick picks it up (one os.stat, a read when the mtime
moved), re-resolves, and reloads a branch diff that now has another base.
The user's pick persists with the page (page_state's "parent") from the
moment it is set or restored — a restored page that was never shown still
carries it into the next layout save — and is dropped only once a
resolution found the branch missing.

The rest of Preferences → Git reaches the page as the whole settings dict
(apply_settings, on every change the dialog makes), read into an
hunkctl.Options: the layout and theme are spawn flags, so a change to
either respawns hunk into the current load — and only when it differs from
what the running child was spawned with, since every unrelated preference
lands here too; the untracked switch rides every `diff` tail, so a change
is a `session reload` of the current mode (hunk re-reads the option on
each reload); the commits page size and the untracked switch also go into
the sidecar, rewritten at once, from which the extension pages its
commits panel and flags its own loads.

The decisions with no widget in them — argv, reply parsing, title →
breadcrumb, the chords, the layout slot, the sidecar — live in hunkctl,
where the unit tests can reach them. This module never imports terminal.py
(which imports it) and declares no "shell-exited": the strip closes any page
that emits it, and a hunk that exits should show a Reopen card, not take
the page with it.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import weakref
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import gitinfo, gitops, hunkctl, keybindings, keymap, proctree, themes  # noqa: E402
from .gitsidebar import GitSidebar  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

# Every page built and not yet shut down, for shutdown_all: the app's own
# shutdown is the one moment a hunk can still be told to go while its pty is
# alive (see GitPage._shutdown).
_LIVE_PAGES: weakref.WeakSet = weakref.WeakSet()

# The width a column has to reach for the dock to spend free gutter on one
# (see PanelDock._column_floor): room for the native sidebar (220 px, its
# own request) beside hunk's diff at its narrowest (48 columns, ~440 px at
# the default monospace size — measured at 9.1 px a column under the
# headless display), so a fresh page opens wide enough for both. Not the
# page's minimum: that is the breakpoint bin's request below, what a drag
# of the divider can shrink the column to, and under the breakpoint the
# sidebar hides so hunk keeps its 48 columns.
_MIN_PAGE_WIDTH = 680
# The Adw.BreakpointBin's size request (it reports this as the page's
# minimum rather than its children's sum, see collins/editor.py's pane):
# hunk's 48 columns and a little. Both axes, or the bin warns per allocation.
_BIN_MIN_WIDTH = 460
_BIN_MIN_HEIGHT = 120
# Below this width the sidebar hides regardless of the toggle: one pixel
# under _MIN_PAGE_WIDTH, so a page at the floor shows both.
_NARROW_MAX_WIDTH = _MIN_PAGE_WIDTH - 1
# Where the paned's divider starts: the sidebar a little wider than its
# request, the rest hunk's.
_SIDEBAR_POSITION = 240

# Sidecar serial numbers, one per page built in this process (see
# hunkctl.sidecar_path): a closed page's file is removed and the number is
# never reused, so a late write from a page that is gone can't land in a
# newer page's file.
_SERIAL = itertools.count(1)

# The tab's icon, bundled under data/icons — and the footer's git button's
# (terminal.py), so the button and the page it opens wear one glyph.
ICON = "git-merge-symbolic"

# The zoom chords' step and clamp, the same numbers PanelTerminal uses
# (terminal.py's _FONT_SCALE_*), copied rather than imported: terminal.py
# imports this module.
_FONT_SCALE_MIN = 0.25
_FONT_SCALE_MAX = 4.0
_FONT_SCALE_STEP = 1.1

# How the branch label is cut when a branch name runs long: the header has
# the breadcrumb to fit beside it.
_BRANCH_MAX_CHARS = 24

# Stack page names.
_HUNK = "hunk"
_CARD = "card"

# Which card the stack shows, when it shows one (see _show_card).
_INSTALL = "install"
_EXITED = "exited"
_NOT_A_REPO = "not-a-repo"


class GitPage(Adw.Bin):
    """The session's git page: hunk in a VTE under a one-row header (PanelPage,
    see panelstrip).

    One per session tab. Spawns hunk on first "map" (a restored page may sit
    unselected in a hidden strip and must not spawn until shown), resolves
    its session id by pid, and switches what is loaded with `hunk session
    reload` — respawning whenever that path is unavailable.
    """

    page_kind = "git"
    column_floor = _MIN_PAGE_WIDTH  # the dock spends free gutter on a column at least this wide
    # ... and opens the column at least this wide even when no gutter pays
    # for it (PanelDock._column_floor): what the page's own size request
    # enforced before the sidebar became collapsible. A drag may take the
    # column down to the breakpoint bin's request, and the sidebar folds.
    # The column's width includes the strip's own chrome around the page
    # (measured: 5 px), so the seed carries slack over the breakpoint — a
    # page opened exactly at the floor would sit one pixel under it, folded.
    column_seed = _MIN_PAGE_WIDTH + 24

    __gsignals__ = {
        # The tab title follows the breadcrumb: the strip re-reads
        # page_title/page_icon.
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    # Deliberately NO "shell-exited": the strip closes any page that emits it.

    def __init__(
        self,
        cwd_provider: Callable[[], str | None],
        parent_provider: Callable[[str | None], str | None],
        on_closed: Callable[[GitPage], None],
        loaded: hunkctl.Loaded = hunkctl.DEFAULT_MODE,
        parent: str | None = None,
        sidebar: bool = True,
    ) -> None:
        """*cwd_provider*: the agent's live cwd (TerminalTab.current_agent_cwd)
        — read at every spawn and poll. *parent_provider(cwd)*: the parent
        branch NAME ("main") the host computes (the newest PR's base, else
        the default branch), or None; the page resolves it to a diff target
        itself (gitinfo.resolve_branch). *on_closed(page)*: fired from
        page_closed() — the tab's X, through the strip's close funnel — after hunk
        is signalled, so the host drops its reference. *loaded*: what to
        spawn into (a restored layout's, or the footer's choice): a mode, a
        commit as {"show": ref}, or a range as {"range": "a...b"}.
        *parent*: the branch the user set through "Set parent branch…"
        (the sidebar's picker, or the extension's), restored from the
        layout; it beats *parent_provider* while it resolves. *sidebar*:
        whether the native panels show (the header's toggle, restored from
        the layout: hunkctl.decode_sidebar)."""
        super().__init__()
        self.add_css_class("git-page")
        self._cwd_provider = cwd_provider
        self._parent_provider = parent_provider
        self._on_closed = on_closed
        self._loaded: hunkctl.Loaded = loaded if hunkctl.loaded_ok(loaded) else hunkctl.DEFAULT_MODE
        # The user-set parent branch NAME, or None for the automatic rung
        # (see _resolve_parent). Comes in from the layout, changes through
        # the sidecar, goes out in page_state.
        self._user_parent: str | None = parent if hunkctl.safe_ref(parent) else None
        # Set by _resolve_parent when the user's branch was looked for and
        # not found; cleared when it resolves again or another pick lands.
        # Only this puts the pick out of force (see _parent_source): a page
        # that hasn't resolved anything yet — restored into a hidden strip,
        # or shown over a directory that isn't a repository — keeps it.
        self._user_parent_missing = False

        # -- hunk process state ----------------------------------------------
        self._hunk_path: str | None = None
        self._child_pid: int | None = None
        self._session_id: str | None = None
        # From the start of a spawn attempt (the probe included) until the
        # child exits or the attempt fails: guards the "map" hook.
        self._spawned = False
        # The load the running child was spawned into — what a give-up on
        # the session id compares a queued load against.
        self._spawned_mode: hunkctl.Loaded | None = None
        # From the spawn until the session id is known or given up on: loads
        # in that window queue in _pending_mode rather than respawn.
        self._resolving = False
        self._pending_mode: hunkctl.Loaded | None = None
        self._reloading = False
        # A `session get` on the poll, in flight.
        self._syncing_session = False
        # While a reload or get is out: the mode a newer ask wants loaded
        # once it lands (the user's word beats any title the reply carries),
        # and whether the tree moved meanwhile (reload what hunk shows).
        self._pending_reload: hunkctl.Loaded | None = None
        self._stale = False
        # A respawn asked while a child is alive: the child is signalled and
        # the exit handler spawns the next one instead of showing the card.
        self._respawn_wanted = False
        self._closing = False
        # Every thread reply carries the generation it was dispatched under;
        # a bump orphans everything in flight.
        self._gen = 0

        # -- what the page knows about the tree ------------------------------
        self._repo_root: Path | None = None
        self._branch: str | None = None
        # The parent branch's name and the target a diff names it by
        # ("main", or "origin/main" when only the remote has it).
        self._parent_name: str | None = None
        self._parent_target: str | None = None
        # The target hunk's own title reported for a branch load — shown in
        # the breadcrumb over the resolved one, so the header says what hunk
        # has rather than what was asked.
        self._shown_target: str | None = None
        # hunk's title (repo name stripped) for a load Collins has no name
        # for — a range between two branches, from the extension's parent
        # and default headers, a shell or the agent. None while hunk shows
        # a mode or a commit; while set, _loaded is the last load Collins
        # knew.
        self._foreign: str | None = None
        # The subject of the loaded commit, for the breadcrumb; None for a
        # mode, or while it isn't known (a load just asked for, git not
        # answering). Read on the worker thread that brings a title back.
        self._subject: str | None = None
        # The full sha the loaded commit's ref resolved to, read by the
        # worker that brought the title back (_title_subject_and_sha): what
        # the sidebar matches a `show HEAD` title to a row with.
        self._resolved_sha: str | None = None
        self._signature: tuple | None = None
        # What moves on a push or fetch (gitinfo.remote_refs_signature):
        # a move refreshes the commits list's `↑` marks, nothing else.
        self._remote_signature: tuple | None = None

        # -- the native sidebar's feed (see gitsidebar) -------------------------
        # The header toggle's word; the sidebar shows only while it is True,
        # the page is above the breakpoint and no card hides it.
        self._sidebar_wanted = bool(sidebar)
        self._narrow = False
        # The (files, loaded, untracked) the sidebar's files list was last
        # built from, so a tick whose `session get` changed none of them
        # costs no `git status`; and whether the tree moved since (a
        # freshness move, a native mutation) — the next reply rebuilds.
        self._files_shown: tuple | None = None
        self._files_stale = False
        # Whether the sidecar named hunk's cursor this tick: the `session
        # get` snapshot then yields to it (the extension's word is fresher).
        self._sidecar_selection_seen = False
        # A `session navigate` of the sidebar's own in flight, and one
        # waiting for a working-tree side to load first: (path, side).
        self._navigating = False
        self._pending_navigate: tuple[str, str] | None = None
        # Whether hunk runs with the collins-git extension (the probe's
        # word); without it the sidebar hides the buttons that feed its keys.
        self._extension_loaded = hunkctl.extension_dir() is not None

        # -- what Preferences → Git says (see apply_settings) ------------------
        # The shipped defaults until the host's first apply_settings — a
        # page built by the strip gets the dock's settings before it maps.
        self._options = hunkctl.Options()
        # The Options the running (or in-flight) child was spawned with:
        # what a settings change compares against to decide on a respawn.
        self._spawned_options: hunkctl.Options | None = None
        # The untracked switch flipped while the session id was still being
        # resolved (or before the probe's argv was even built): the
        # resolution reloads the current load with the new tail (or
        # respawns, having given up on an id); a spawn that reads the
        # options after the flip clears it (see _probed).
        self._options_stale = False

        # -- the sidecar shared with the extension (see hunkctl) ---------------
        # One path per page, kept across respawns, removed at shutdown.
        self._sidecar: str = hunkctl.sidecar_path(GLib.get_user_runtime_dir(), os.getpid(), next(_SERIAL))
        # The (parent, source, default, log page, untracked) last written,
        # so the tick rewrites only on a change; None until the first write
        # lands (and again after the file went missing, so it is written
        # afresh).
        self._sidecar_written: tuple | None = None
        # The file's mtime after our own last write — or the extension's,
        # once read — so the tick reads only what the other side wrote.
        self._sidecar_mtime_ns: int | None = None
        # (index mtime ns, HEAD) the extension last reloaded the review
        # for on its own (hunkctl.read_sidecar_refreshed): a signature
        # move that matches it needs no reload from here.
        self._ext_refreshed: tuple[int, str] | None = None

        self._keys = keymap.KeyMatcher(keybindings.current())

        # -- header -------------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("git-header")
        self._branch_label = Gtk.Label(xalign=0.0)
        self._branch_label.add_css_class("dim-label")
        self._branch_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._branch_label.set_max_width_chars(_BRANCH_MAX_CHARS)
        header.append(self._branch_label)
        self._breadcrumb = Gtk.Label(xalign=0.0, hexpand=True)
        self._breadcrumb.add_css_class("git-breadcrumb")
        self._breadcrumb.set_ellipsize(Pango.EllipsizeMode.END)
        header.append(self._breadcrumb)

        # No switch: the commits list is the switch, and says more (which
        # commit, which branch). Ctrl+1/2/3 stay as shortcuts to the three
        # most common rows.
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Reload the diff"))
        refresh.connect("clicked", lambda *_a: self.refresh())
        # The sidebar toggle, in a box of its own so its tooltip reaches
        # the pointer while the button is insensitive (a narrow page): an
        # insensitive widget is out of pick, its box isn't.
        self._sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self._sidebar_toggle.set_active(self._sidebar_wanted)
        self._sidebar_toggle.add_css_class("flat")
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        self._sidebar_toggle_box = Gtk.Box()
        self._sidebar_toggle_box.append(self._sidebar_toggle)
        header.append(self._sidebar_toggle_box)
        header.append(refresh)

        # The level buttons of a narrow page (see the module docstring):
        # hidden while both panes fit, insensitive at either end of the
        # stack. Drawn as back and forward — back climbs a level (diff →
        # files → commits), forward descends — with back on the left, the
        # way a browser's pair reads; between the breadcrumb and refresh.
        self._up = Gtk.Button(icon_name="go-previous-symbolic")
        self._up.add_css_class("flat")
        self._up.connect("clicked", lambda *_a: self.step_level(up=True))
        header.insert_child_after(self._up, self._breadcrumb)
        self._down = Gtk.Button(icon_name="go-next-symbolic")
        self._down.add_css_class("flat")
        self._down.connect("clicked", lambda *_a: self.step_level(up=False))
        header.insert_child_after(self._down, self._up)
        # What the extension says a narrow page shows (the sidecar's
        # "level"), the default until it says; and the VTE's column count
        # as of the last allocation, which decides whether the page is
        # narrow at all.
        self._level = hunkctl.DEFAULT_LEVEL
        self._columns = 0

        # -- hunk's terminal, and the cards that stand in for it ----------------
        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(0)  # hunk is full-screen; nothing scrolls off
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_audible_bell(False)
        # Pixel scrolling, as the other terminals (terminal._setup_smooth_scroll).
        self.terminal.set_enable_fallback_scrolling(False)
        self.terminal.set_scroll_unit_is_pixels(True)
        self.terminal.connect("child-exited", self._on_child_exited)
        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._card_slot = Adw.Bin(vexpand=True)
        self._card_button: Gtk.Button | None = None
        self._card: str | None = None  # which card is up, None while hunk is
        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(scrolled, _HUNK)
        self._stack.add_named(self._card_slot, _CARD)

        # Shown when the viewer never registered with hunk's session daemon
        # (_resolved gave up): the page still draws, but every load the
        # extension sends — a commit clicked, `n`, a header — finds no
        # session and silently stays put. Says what to run to learn why.
        self._banner = Adw.Banner()
        self._banner.set_title(
            _(
                "The diff viewer never registered with hunk's session daemon, so commits and"
                " modes won't load here. Run `{command}` in a terminal to see why."
            ).format(command=hunkctl.DAEMON_DIAGNOSTIC)
        )
        self._banner.set_button_label(_("Retry"))
        self._banner.connect("button-clicked", lambda *_a: self._respawn())

        # -- the native sidebar beside the stack ---------------------------------
        self.sidebar = GitSidebar(cwd_provider, self._options)
        self.sidebar.connect("load-requested", lambda _s, loaded: self.load(loaded))
        self.sidebar.connect("navigate-requested", self._on_navigate_requested)
        self.sidebar.connect("key-requested", self._on_key_requested)
        self.sidebar.connect("mutated", self._on_mutated)
        self.sidebar.connect("parent-picked", self._on_parent_picked)
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        self._paned.set_start_child(self.sidebar)
        self._paned.set_resize_start_child(False)
        self._paned.set_shrink_start_child(False)
        self._paned.set_end_child(self._stack)
        self._paned.set_resize_end_child(True)
        self._paned.set_shrink_end_child(True)
        self._paned.set_position(_SIDEBAR_POSITION)
        # The width is judged by a breakpoint bin around the paned (the
        # editor's pane does the same): apply/unapply arrive from the bin's
        # own allocation, and the bin reports its size request as the
        # page's minimum rather than the children's sum — which is what
        # lets a drag take the column under sidebar + diff.
        self._bin = Adw.BreakpointBin(child=self._paned, vexpand=True)
        self._bin.set_size_request(_BIN_MIN_WIDTH, _BIN_MIN_HEIGHT)
        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(f"max-width: {_NARROW_MAX_WIDTH}px"))
        narrow.connect("apply", lambda *_a: self._on_narrow(True))
        narrow.connect("unapply", lambda *_a: self._on_narrow(False))
        self._bin.add_breakpoint(narrow)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self._banner)
        box.append(self._bin)
        # A width sensor: nothing tells a widget its allocation changed
        # (Adw.Bin allocates through a layout manager, so the vfunc never
        # runs here) except a drawing area's "resize" — zero rows tall, it
        # costs nothing, and being last in the box it fires after the
        # terminal above it was allocated; the column count is read on
        # idle all the same, so the order never matters.
        sensor = Gtk.DrawingArea(hexpand=True)
        sensor.set_content_height(0)
        sensor.set_size_request(-1, 0)
        sensor.connect(
            "resize", lambda *_a: GLib.idle_add(self._read_columns, priority=GLib.PRIORITY_DEFAULT)
        )
        box.append(sensor)
        # Page-local toasts: commit results, git's refusals, a navigate hunk
        # refused (see gitsidebar._toast, which finds this overlay).
        self._toast_overlay = Adw.ToastOverlay(child=box)
        self.set_child(self._toast_overlay)
        self._sync_sidebar()

        # Ctrl+1/2/3 and the zoom chords, ahead of VTE: in the capture phase
        # on the page they fire wherever the focus sits inside it, and before
        # the terminal would feed the press to hunk.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        self._sync_header()
        self._sync_levels()
        # First shown (and every re-show): make sure hunk is running. A
        # restored page is built unselected, maybe in a hidden strip, and
        # must not spawn a process nobody is looking at.
        self.connect("map", lambda *_a: self._ensure_spawned())
        # Gone with its tab, or with the window: see _on_unrealize.
        self.connect("unrealize", self._on_unrealize)
        _LIVE_PAGES.add(self)

    # -- public -------------------------------------------------------------

    @property
    def loaded(self) -> hunkctl.Loaded:
        """What the page is showing (or will spawn into): one of hunkctl.MODES,
        or a commit as {"show": ref}."""
        return self._loaded

    @property
    def hunk_alive(self) -> bool:
        """Whether a hunk child is running in the VTE right now."""
        return self._child_pid is not None

    @property
    def session_id(self) -> str | None:
        """The hunk session id the page drives, once resolved by pid; None
        before that, and for good once the lookup gave up (every switch is
        a respawn then, and nothing outside can drive the viewer)."""
        return self._session_id

    @property
    def hunk_path(self) -> str | None:
        """The hunk executable the page probed, None before the probe."""
        return self._hunk_path

    @property
    def card(self) -> str | None:
        """Which card stands in for hunk — "install", "exited",
        "not-a-repo" — or None while the viewer (or its spawn) is up."""
        return self._card

    @property
    def resolving(self) -> bool:
        """Whether a spawn is between its start and the session id."""
        return self._resolving

    def breadcrumb_text(self) -> str:
        """What the header says is loaded — hunk's word once it answered."""
        return self._breadcrumb.get_text()

    @property
    def level(self) -> str:
        """What a narrow page shows — "diff", "files" or "commits" (one of
        hunkctl.LEVELS): the extension's last word through the sidecar, or
        the step the header just took, or the default before either."""
        return self._level

    @property
    def columns(self) -> int:
        """The VTE's column count as of the last allocation (0 before one):
        what decides whether the page is narrow (hunkctl.page_is_narrow)."""
        return self._columns

    def level_buttons_visible(self) -> bool:
        """Whether the header shows the up/down level buttons: the page is
        narrower than both panes want."""
        return self._up.get_visible()

    @property
    def sidebar_shown(self) -> bool:
        """Whether the native sidebar is on screen: the toggle says so, the
        page is above the breakpoint, and no card hides it."""
        return self.sidebar.get_visible()

    @property
    def sidebar_wanted(self) -> bool:
        """The header toggle's word (what page_state persists), whether or
        not the page is wide enough to honour it."""
        return self._sidebar_wanted

    def set_sidebar_wanted(self, wanted: bool) -> None:
        """Flip the header's toggle: show or hide the native panels."""
        self._sidebar_toggle.set_active(bool(wanted))

    def step_level(self, up: bool) -> None:
        """The header's up (or down) button: feed hunk the key the extension
        binds for one level up (`<`) or down (`>`), and take the step for
        granted in the header until the extension's word arrives through
        the sidecar. Nothing without a running hunk, on a page that isn't
        narrow (the buttons are hidden there), or where the step has
        nowhere to go (the button is insensitive there)."""
        if not self.hunk_alive or not hunkctl.page_is_narrow(self._columns):
            return
        target = hunkctl.level_up(self._level) if up else hunkctl.level_down(self._level)
        if target is None or hunkctl.pane_fit(self._columns) == "none":
            return
        self.terminal.feed_child(hunkctl.LEVEL_UP_KEY if up else hunkctl.LEVEL_DOWN_KEY)
        self._level = target
        self._sync_levels()

    def settled(self) -> bool:
        """Whether the viewer is up with a session id in hand and nothing in
        flight: no spawn resolving, no reload or `session get` out, no load
        queued behind one. What a caller driving the page from outside
        (the show_diff tool) waits for before trusting `loaded` and before
        sending the session its own commands — nor a `session navigate` of
        the sidebar's own out or queued behind a load."""
        return (
            self.hunk_alive
            and self._session_id is not None
            and not self._resolving
            and not self._reloading
            and not self._syncing_session
            and not self._navigating
            and self._pending_mode is None
            and self._pending_reload is None
            and self._pending_navigate is None
        )

    def shows(self, loaded: hunkctl.Loaded) -> bool:
        """Whether the page shows exactly *loaded* — the same mode or commit,
        and not a load Collins has no name for."""
        return self._foreign is None and self._loaded == loaded

    def load(self, loaded: hunkctl.Loaded) -> None:
        """Show *loaded* — "unstaged" | "staged" | "branch", a commit as
        {"show": ref} or a range as {"range": "a...b"}: Ctrl+1/2/3, the
        sidebar's rows and the host's open_git_page(mode) land here.
        Updates the breadcrumb/tab title at once, then reloads the live
        session (or respawns when there is no session id, or queues the
        load while the id is still being resolved). On the "hunk exited"
        card it is the Reopen button, into this load. "branch" with no
        resolvable parent is a no-op. Anything else raises ValueError. A
        navigate waiting for an earlier load is dropped: the newer ask
        wins."""
        if not hunkctl.loaded_ok(loaded):
            raise ValueError(f"unknown git page load: {loaded!r}")
        self._pending_navigate = None
        if loaded == "branch" and self._resolve_parent() is None:
            self._sync_header()
            return
        self._loaded = dict(loaded) if isinstance(loaded, dict) else loaded
        self._foreign = None
        self._subject = None
        self._resolved_sha = None
        if loaded != "branch":
            self._shown_target = None
        self._sync_header()
        self.emit("title-changed")
        self._sync_context()
        if not self._spawned:
            if self._card == _EXITED and self.get_mapped():
                self._spawn()  # Ctrl+1/2/3 on a dead viewer: reopen into that load
            return  # else the spawn on map reads _loaded
        if self._resolving:
            self._pending_mode = self._loaded
        elif self._session_id is None:
            self._respawn()
        else:
            self._reload(self._loaded)

    def refresh(self) -> None:
        """Reload what is loaded (the header's ⟳): `hunk session reload` of
        the same target — a mode's, a commit's or a range's — or a respawn
        without a session id, and the commits list re-read. No-op on a
        card, and while hunk shows a load Collins can't name (hunk's own
        `r` key and `--watch` cover that one)."""
        if not self.hunk_alive:
            return
        if self._resolving:
            return  # the first load is still landing
        if self._foreign is not None:
            return
        self.sidebar.refresh_commits()
        self._files_stale = True
        self._reload(self._loaded)

    def poll_tick(self) -> None:
        """The footer's 2 s tick, forwarded by the host only for a mapped page
        (the page checks get_mapped() again itself). Re-reads the branch
        label; respawns when the agent's repo root moved (a worktree entry)
        or shows the "not a repository" card when there is none; picks up a
        parent the user set through the extension (the sidecar's mtime
        moved), re-resolves the parent and publishes it and the default
        branch back into the sidecar when either changed; seeds and compares
        gitinfo.tree_signature and reloads the current load when it moved —
        after asking hunk what it has loaded (`session get`), so the reload
        re-asks for what hunk shows, not what Collins last asked for. Never
        spawns a git process: file reads, one stat, and a write when the
        sidecar is due one."""
        if not self.get_mapped() or self._closing:
            return
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None:
            # The tree went away under a running hunk (a worktree removed):
            # take the child down — its exit shows the card — and say why
            # once it is gone. A card already saying so is left alone.
            if self.hunk_alive:
                self._respawn_wanted = False
                self._terminate_child()
            elif not self._spawned and self._card != _NOT_A_REPO:
                self._show_not_a_repo()
            return
        if self._repo_root is not None and root != self._repo_root:
            self._repo_root = root
            self._signature = None
            self._respawn()
            return
        if self._card == _NOT_A_REPO and not self._spawned:
            self._spawn()  # the tree turned up (the agent cd'd into one): no click needed
            return
        branch = gitinfo.current_branch(cwd)
        if branch != self._branch:
            self._branch = branch
            self._sync_header()
            self._sync_context()  # another branch: other groups
        self._read_columns()
        self._read_sidecar()
        target_before = self._parent_target
        self._resolve_parent()
        self._write_sidecar()
        parent_moved = self._parent_target != target_before
        if parent_moved:
            self._sync_header()
            self.emit("title-changed")
        signature = gitinfo.tree_signature(cwd, self._parent_name)
        remote = gitinfo.remote_refs_signature(cwd)
        # A parent that changed changes the signature's base too; that is
        # not the tree moving, and only a branch diff has to follow it. A
        # move the extension made (an `x`, a commit) and reloaded hunk for
        # itself is not one to reload again: the reload would land on the
        # dialog the user opened next and cancel it.
        changed = self._signature is not None and signature != self._signature
        moved = changed and not parent_moved
        if moved and hunkctl.shown_by_extension(self._ext_refreshed, signature, self._signature):
            log.debug("gitpage: the extension already reloaded for this move")
            moved = False
        remote_moved = self._remote_signature is not None and remote != self._remote_signature
        self._signature = signature
        self._remote_signature = remote
        # The native lists follow every move — the extension's own included,
        # and a push (the `↑` marks) — whether or not hunk is reloaded.
        if changed or remote_moved or parent_moved:
            self.sidebar.refresh_commits()
        if changed:
            self._files_stale = True
        if parent_moved:
            self._sync_context()
        if not self.hunk_alive or self._resolving:
            return
        if parent_moved and target_before is not None and self._loaded == "branch" and not self._foreign:
            # The branch diff's base is another branch now: reload against
            # it. Not from None — that is a parent coming back after a
            # refused reload put it down (_reloaded), which the header
            # follows without asking again.
            self._reload("branch")
            return
        if self._session_id is None:
            if moved:
                self._reload(self._loaded)  # no session id: a respawn
            return
        if self._reloading or self._syncing_session:
            # One ask out at a time; a move seen meanwhile is honoured by
            # whichever reply lands (see _reloaded, _synced).
            self._stale = self._stale or moved
            return
        self._sync_session(reload=moved)

    # -- PanelPage protocol (see panelstrip) -----------------------------------

    def page_title(self) -> str:
        if self._foreign is not None:
            return hunkctl.foreign_tab_title(self._foreign)
        return hunkctl.tab_title(self._loaded, self._target_label())

    def page_icon(self) -> str | None:
        return ICON

    def grab_page_focus(self) -> None:
        if self._stack.get_visible_child_name() == _CARD and self._card_button is not None:
            self._card_button.grab_focus()
        else:
            self.terminal.grab_focus()

    def has_page_focus(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return focus is not None and (focus is self or focus.is_ancestor(self))

    def page_busy(self) -> bool:
        return False  # nothing is lost by closing: hunk holds no state of ours

    def holds_escape(self) -> bool:
        """hunk reads Escape itself while it runs; the dock's restore-from-
        maximized yields to it (see paneldock)."""
        return self.hunk_alive

    def apply_settings(self, settings: dict) -> None:
        """Font ("font"), terminal theme ("terminal_theme"), the KeyMatcher
        for the zoom chords, and Preferences → Git (hunkctl.Options.
        from_settings: git_layout, git_theme, git_untracked, git_log_page).
        The scrollback setting is ignored (hunk runs at scrollback 0). Safe
        before the spawn, which then reads the options.

        On a running page: a layout or theme other than the child was
        spawned with respawns it into the current load (there is no other
        way to change either; a spawn in flight leaves a note, _respawn);
        a flipped untracked switch reloads the current working-tree or
        branch load (a commit, or a load Collins can't name, picks it up
        on its next diff load) — or, while the session id is still being
        resolved, leaves a note the resolution acts on (_options_stale);
        the sidecar is rewritten now, not on the next tick, so the page
        size and the switch reach the extension while the page is hidden
        too. Nothing happens for a call that changed none of them — every
        preference the dialog touches lands here."""
        font = settings.get("font") or ""
        self.terminal.set_font(Pango.FontDescription.from_string(font) if font else None)
        themes.apply_terminal_theme(self.terminal, settings.get("terminal_theme"))
        self._keys = keymap.KeyMatcher.from_settings(settings)
        old, self._options = self._options, hunkctl.Options.from_settings(settings)
        new = self._options
        spawned = self._spawned_options
        if (
            spawned is not None
            and (new.layout, new.theme) != (spawned.layout, spawned.theme)
            and (self.hunk_alive or self._spawned)
        ):
            log.debug("gitpage: layout/theme changed; respawning hunk")
            self._respawn()
        elif new.untracked != old.untracked and self._spawned and (self._resolving or self._respawn_wanted):
            # Between the spawn and the session id (the probe, VTE's fork,
            # `session list`), or with a respawn on its way (the child
            # signalled, its exit spawning the next): nothing to reload
            # yet. _probed reads the options for the argv and drops the
            # note; _resolved reloads.
            self._options_stale = True
        elif (
            new.untracked != old.untracked
            and self.hunk_alive
            and not hunkctl.is_show(self._loaded)
            and self._foreign is None
        ):
            self._reload(self._loaded)
        if self._sidecar_written is not None:
            self._write_sidecar()
        self.sidebar.set_options(new)

    def page_state(self) -> dict:
        """This page's slot in a serialized dock layout (see panellayout):
        what is loaded, the user-set parent while it is in force, and
        whether the sidebar is hidden."""
        parent = self._user_parent if self._parent_source() == "user" else None
        return hunkctl.encode_state(self._loaded, parent, sidebar=self._sidebar_wanted)

    def page_closed(self) -> None:
        """The tab is really closing: SIGTERM the hunk child if alive, then
        on_closed(self)."""
        self._shutdown()
        self._on_closed(self)

    def _shutdown(self) -> None:
        """The page is done for good: no more spawns, every reply in flight
        orphaned, hunk signalled. Idempotent — the strip's close funnel, the
        widget's dispose and the app's shutdown can each be the first to
        say so.

        The signal has to go out while hunk's pty is still open. Left to the
        pty's hangup alone, the npm wrapper dies and the viewer it spawnSyncs
        lives on, orphaned and deaf to SIGTERM once its terminal is gone
        (hunk 0.20.1, verified) — hence hunkctl.terminate_tree, and hence
        the three ways in."""
        if getattr(self, "_closing", True):  # a dispose can find a half-built page
            return
        self._closing = True
        self._gen += 1
        self._terminate_child()
        _LIVE_PAGES.discard(self)
        try:
            os.remove(self._sidecar)
        except OSError:
            pass  # never written, or already gone with the runtime dir

    def do_dispose(self) -> None:
        self._shutdown()
        Adw.Bin.do_dispose(self)

    def _on_unrealize(self, *_args) -> None:
        """Unrealized: the tab closed, or the page is being re-parented (a
        drag to another strip, a lift-out). The two look the same here, so
        the decision waits for idle, when a re-parented page is realized
        again and a closed one isn't. That one loses its hunk — signalled
        now, while the pty is up — without being marked closing: were the
        call wrong (a re-parent into a window shown later), the next map
        simply spawns hunk again."""
        GLib.idle_add(self._after_unrealize, priority=GLib.PRIORITY_DEFAULT)

    def _after_unrealize(self) -> bool:
        if self.get_realized() or self._closing:
            return GLib.SOURCE_REMOVE
        if self.hunk_alive:
            log.debug("gitpage: page went unrealized; stopping hunk")
            self._respawn_wanted = False
            self._terminate_child()
        elif self._spawned:
            # A spawn in flight for a page nobody shows: orphan it, so the
            # callback terminates the child it announces (see _on_spawned),
            # and let the next map start over.
            log.debug("gitpage: page went unrealized mid-spawn; dropping it")
            self._gen += 1
            self._respawn_wanted = False
            self._spawn_failed()
        return GLib.SOURCE_REMOVE

    # -- header -------------------------------------------------------------------

    def _target_label(self) -> str | None:
        """The parent shown in the breadcrumb and tab title: hunk's own word
        for a branch load, else the resolved target, else the parent's name."""
        return self._shown_target or self._parent_target or self._parent_name

    def _sync_header(self) -> None:
        self._branch_label.set_text(f"⎇ {self._branch}" if self._branch else "")
        self._branch_label.set_visible(bool(self._branch))
        if self._foreign is not None:
            self._breadcrumb.set_text(self._foreign)
        else:
            self._breadcrumb.set_text(
                hunkctl.breadcrumb(self._loaded, self._branch, self._target_label(), self._subject)
            )

    def _read_columns(self) -> bool:
        """Re-read the VTE's column count (after an allocation — the width
        sensor — and on every tick) and show or hide the level buttons by
        it."""
        columns = int(self.terminal.get_column_count() or 0)
        if columns != self._columns:
            self._columns = columns
            self._sync_levels()
        return GLib.SOURCE_REMOVE

    def _sync_levels(self) -> None:
        """The level buttons follow the column count (shown while the page
        is narrow) and the level (tooltips, sensitivity), and need a hunk
        to feed."""
        narrow = self._columns > 0 and hunkctl.page_is_narrow(self._columns)
        self._up.set_visible(narrow)
        self._down.set_visible(narrow)
        if not narrow:
            return
        for button, up in ((self._up, True), (self._down, False)):
            tooltip, sensitive = hunkctl.level_button(up, self._level, self._columns)
            button.set_tooltip_text(tooltip)
            button.set_sensitive(sensitive and self.hunk_alive)

    def _on_key_pressed(self, ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        mode = hunkctl.load_for_key(int(keyval), int(state))
        if mode is not None:
            self.load(mode)
            return True
        event = ctrl.get_current_event()
        if event is None:
            return False
        if self._keys.matches("terminal.zoom-in", event):
            self._zoom_by(_FONT_SCALE_STEP)
            return True
        if self._keys.matches("terminal.zoom-out", event):
            self._zoom_by(1 / _FONT_SCALE_STEP)
            return True
        if self._keys.matches("terminal.zoom-reset", event):
            self._zoom_by(None)
            return True
        return False

    def _zoom_by(self, factor: float | None) -> None:
        scale = 1.0
        if factor is not None:
            scale = max(_FONT_SCALE_MIN, min(_FONT_SCALE_MAX, self.terminal.get_font_scale() * factor))
        self.terminal.set_font_scale(scale)

    def _apply_title(
        self,
        title: str,
        repo_root: str | None = None,
        subject: str | None = None,
        sha: str | None = None,
    ) -> None:
        """Take hunk's word for what it has loaded (a session or reload
        reply's title): the breadcrumb and tab title follow it, so Collins
        never claims a load hunk didn't make. `<repo> show <ref>` becomes
        the page's commit load, named by *subject* when the worker that
        brought the title read one (_title_subject_and_sha, which also
        read the full *sha* the sidebar's ▸ row matches on); `<repo>
        a...b` between two safe refs is the page's range load; a title
        naming nothing Collins can load (`<repo> main..feat`) is shown as
        it is, less the repo name (*repo_root*'s, or the page's own). The
        sidebar's context follows."""
        kind, target = hunkctl.loaded_from_title(title)
        self._subject = None
        self._resolved_sha = None
        if kind is None:
            root = repo_root or (str(self._repo_root) if self._repo_root else None)
            self._foreign = hunkctl.title_tail(title, root)
        elif kind == "show":
            self._foreign = None
            self._loaded = {hunkctl.SHOW_KEY: target}
            self._shown_target = None
            self._subject = subject or None
            self._resolved_sha = sha
        elif kind == "range":
            self._foreign = None
            self._loaded = {hunkctl.RANGE_KEY: target}
            self._shown_target = None
        else:
            self._foreign = None
            self._loaded = kind
            self._shown_target = target if kind == "branch" else None
        self._sync_header()
        self.emit("title-changed")
        self._sync_context()

    def _resolve_parent(self) -> str | None:
        """The diff target for the parent branch (`main`, `origin/main`),
        re-read from the tree each time: a fetch or a checkout can create the
        ref between two asks. The user's pick (_user_parent) while it
        resolves, else the host's automatic rung (parent_provider: the
        newest PR's base, else the default branch). None when there is no
        parent to name."""
        cwd = self._cwd_provider()
        name = None
        if self._user_parent and gitinfo.resolve_branch(cwd, self._user_parent):
            name = self._user_parent
            self._user_parent_missing = False
        else:
            self._user_parent_missing = self._user_parent is not None
            name = self._parent_provider(cwd)
        resolved = gitinfo.resolve_branch(cwd, name)
        # The name goes out in the sidecar (and into the header) whether or
        # not it resolves yet, so it passes the same gate the read side
        # applies to what comes back: a PR base or a provider answer that
        # does not look like a ref is not a parent at all.
        self._parent_name = name if hunkctl.safe_ref(name) else None
        self._parent_target = resolved[0] if resolved else None
        return self._parent_target

    def _parent_source(self) -> str:
        """"user" while the user-set parent is in force — set, and not found
        missing by the last _resolve_parent (a page that never resolved,
        being unshown or over no repository, keeps the pick) — else "auto":
        the sidecar's parentSource, and whether page_state carries the
        name."""
        if self._user_parent and not self._user_parent_missing:
            return "user"
        return "auto"

    # -- the sidecar -----------------------------------------------------------------

    def _write_sidecar(self) -> None:
        """Publish (parent, source, default, log page, untracked) to the
        extension when any of them changed since the last write (or the
        file is gone). Records the mtime of our own write so the tick
        doesn't read it back."""
        cwd = self._cwd_provider()
        payload = (
            self._parent_name,
            self._parent_source(),
            gitinfo.default_branch(cwd),
            self._options.log_page,
            self._options.untracked,
        )
        if payload == self._sidecar_written:
            return
        if not hunkctl.write_sidecar(self._sidecar, hunkctl.sidecar_payload(*payload)):
            log.debug("gitpage: could not write the sidecar %s", self._sidecar)
            return
        self._sidecar_written = payload
        try:
            self._sidecar_mtime_ns = os.stat(self._sidecar).st_mtime_ns
        except OSError:
            self._sidecar_mtime_ns = None

    def _read_sidecar(self) -> None:
        """Pick up what the extension wrote: one os.stat, and a read only
        when the mtime isn't the one recorded after our own last write.
        "user" with a parent sets the user's pick; "auto" clears it. The
        caller re-resolves and writes the resolved name back. Reading our
        own write (a write and a pick a moment apart) is harmless: it says
        what the page already holds."""
        try:
            mtime_ns = os.stat(self._sidecar).st_mtime_ns
        except OSError:
            self._sidecar_written = None  # gone (a cleaned runtime dir): write it afresh
            return
        if mtime_ns == self._sidecar_mtime_ns:
            return
        self._sidecar_mtime_ns = mtime_ns
        try:
            with open(self._sidecar, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return
        self._ext_refreshed = hunkctl.read_sidecar_refreshed(text)
        level = hunkctl.read_sidecar_level(text)
        if level is not None and level != self._level:
            self._level = level
            self._sync_levels()
        # The extension's word for hunk's cursor and the `v` anchor (sidecar
        # v2): the highlight and the anchor button follow at once, and the
        # `session get` snapshot this tick yields to the selection.
        selection = hunkctl.read_sidecar_selection(text)
        if selection is not None:
            self._sidecar_selection_seen = True
            self.sidebar.set_selection(selection.path, selection.hunk, "sidecar")
        self.sidebar.set_anchor(hunkctl.read_sidecar_anchor(text))
        parent, source = hunkctl.read_sidecar(text)
        if source == "user" and parent:
            self._user_parent = parent
            self._user_parent_missing = False  # a fresh pick: resolve it before judging
        elif source == "auto":
            self._user_parent = None
            self._user_parent_missing = False

    # -- cards ----------------------------------------------------------------------

    def _show_card(
        self,
        kind: str,
        icon: str,
        title: str,
        description: str,
        button: str,
        on_click: Callable[[], None],
        link: tuple[str, str] | None = None,
    ) -> None:
        """Replace hunk with an Adw.StatusPage: *link* — (label, uri) — is a
        link button above the one *button*, which does *on_click*."""
        page = Adw.StatusPage(icon_name=icon, title=title, description=description)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_halign(Gtk.Align.CENTER)
        if link is not None:
            label, uri = link
            body.append(Gtk.LinkButton.new_with_label(uri, label))
        action = Gtk.Button(label=button)
        action.add_css_class("pill")
        action.add_css_class("suggested-action")
        action.set_halign(Gtk.Align.CENTER)
        action.set_margin_top(8)
        action.connect("clicked", lambda *_a: on_click())
        body.append(action)
        page.set_child(body)
        self._card_button = action
        self._card = kind
        self._banner.set_revealed(False)
        self._card_slot.set_child(page)
        self._stack.set_visible_child_name(_CARD)
        self._sync_sidebar()

    def _show_hunk(self) -> None:
        self._stack.set_visible_child_name(_HUNK)
        self._card_slot.set_child(None)
        self._card_button = None
        self._card = None
        self._sync_sidebar()

    def _show_install_card(self, probe: hunkctl.Probe) -> None:
        if probe.status == "missing":
            title = _("hunk isn't installed")
        else:
            title = _("hunk {version} or newer is needed").format(
                version=".".join(str(part) for part in hunkctl.MIN_VERSION)
            )
        description = _("Collins shows diffs through hunk. Install it, then check again.")
        if probe.status == "old":
            found = ".".join(str(part) for part in probe.version) if probe.version else _("unknown")
            description = (
                _("The hunk on PATH is version {found}.").format(found=found) + " " + description
            )
        self._show_card(
            _INSTALL,
            "git-merge-symbolic",
            title,
            description,
            _("Check again"),
            self._spawn,
            (_("Install instructions at hunk.dev"), hunkctl.INSTALL_URL),
        )

    def _show_exited_card(self, detail: str | None = None) -> None:
        description = _("The diff viewer closed. Reopen it to keep going.")
        if detail:
            description = f"{description}\n{detail}"
        self._show_card(
            _EXITED, "git-merge-symbolic", _("hunk exited"), description, _("Reopen"), self._spawn
        )

    def _show_not_a_repo(self) -> None:
        self._show_card(
            _NOT_A_REPO,
            "folder-symbolic",
            _("Not a git repository"),
            _("The session's working directory has no repository to show. Check again once it does."),
            _("Check again"),
            self._spawn,
        )

    # -- spawning ---------------------------------------------------------------------

    def _ensure_spawned(self) -> None:
        if not self._spawned and not self._closing:
            self._spawn()

    def _spawn(self) -> None:
        """Start (or restart, from a card) a hunk child in the current mode:
        the version probe on a thread — and, for a commit load, whether the
        commit still exists (hunkctl.commit_subject: `hunk show` of a sha
        that was rebased away exits at once, and the page would sit on the
        exited card, Reopen after Reopen) — then the VTE spawn on the main
        loop."""
        if self._closing or self.hunk_alive:
            return
        self._spawned = True
        self._resolving = True
        self._pending_mode = None
        self._session_id = None
        self._gen += 1
        gen = self._gen
        cwd = self._cwd_provider()
        show_ref = hunkctl.show_ref(self._loaded)
        runtime_dir = GLib.get_user_runtime_dir()

        def work() -> None:
            probe = hunkctl.probe()
            repaired = hunkctl.repair_daemon_dir(runtime_dir)
            if repaired in ("repaired", "failed"):
                daemon_dir = os.path.join(runtime_dir, hunkctl.DAEMON_DIR)
                if repaired == "repaired":
                    log.info("gitpage: made %s owner-only for hunk's daemon", daemon_dir)
                else:
                    log.warning("gitpage: %s isn't owner-only and can't be made so", daemon_dir)
            subject = hunkctl.commit_subject(cwd, show_ref) if show_ref else None
            GLib.idle_add(self._probed, gen, probe, subject)

        threading.Thread(target=work, name="git-page-probe", daemon=True).start()

    def _probed(self, gen: int, probe: hunkctl.Probe, subject: str | None = None) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        if probe.status != "ok":
            self._spawn_failed()
            self._show_install_card(probe)
            return GLib.SOURCE_REMOVE
        self._hunk_path = probe.path
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None or not cwd:
            self._spawn_failed()
            self._show_not_a_repo()
            return GLib.SOURCE_REMOVE
        self._repo_root = root
        self._branch = gitinfo.current_branch(cwd)
        parent = self._resolve_parent()
        if self._loaded == "branch" and parent is None:
            self._loaded = hunkctl.DEFAULT_MODE  # a saved "vs main" in a tree with no main
        if hunkctl.is_show(self._loaded) and subject is None:
            # git said the commit is gone (a saved sha the branch was rebased
            # past, a layout restored in another clone): not a load to
            # spawn into. "" — git couldn't be asked — spawns as asked.
            log.debug("gitpage: commit %s doesn't resolve; opening %s", self._loaded, hunkctl.DEFAULT_MODE)
            self._loaded = hunkctl.DEFAULT_MODE
        self._subject = (subject or None) if hunkctl.is_show(self._loaded) else None
        self._resolved_sha = None
        self._shown_target = None
        self._foreign = None
        self._signature = gitinfo.tree_signature(cwd, self._parent_name)
        self._remote_signature = gitinfo.remote_refs_signature(cwd)
        self._sync_header()
        self.emit("title-changed")
        self._spawned_mode = self._loaded
        # A respawn asked while the probe was out is answered by this very
        # spawn: the cwd and mode were read just now.
        self._respawn_wanted = False
        extension = hunkctl.extension_dir()
        if extension is None:
            log.warning(
                "gitpage: the collins-git extension is missing from %s; running hunk bare",
                hunkctl.EXTENSION_DIR,
            )
        self._extension_loaded = extension is not None
        # The sidebar's lists come up with the viewer: the groups it knows
        # (a first spawn refreshes them through set_context; a respawn over
        # the same branches re-reads them here) and, once the session
        # answers, hunk's files.
        if not self._sync_context():
            self.sidebar.refresh_commits()
        self._files_stale = True
        # The sidecar goes down before the child comes up, so the extension
        # finds it on startup; a write that failed leaves the variable out
        # and the extension guesses (see hunkctl.spawn_env).
        self._write_sidecar()
        sidecar = self._sidecar if self._sidecar_written is not None else None
        self._spawned_options = self._options
        self._options_stale = False  # the argv carries what the switch says now
        argv = hunkctl.spawn_argv(probe.path, self._loaded, parent, extension, self._options)
        self._show_hunk()
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            str(cwd),
            argv,
            hunkctl.spawn_env(sidecar),  # None: inherit
            GLib.SpawnFlags.DEFAULT,
            None,  # child_setup
            None,  # child_setup_data
            -1,  # timeout
            None,  # cancellable
            lambda terminal, pid, error: self._on_spawned(terminal, pid, error, gen),
        )
        return GLib.SOURCE_REMOVE

    def _spawn_failed(self) -> None:
        self._spawned = False
        self._resolving = False
        self._pending_mode = None
        self._options_stale = False

    def _on_spawned(self, terminal: Vte.Terminal, pid: int, error: GLib.Error | None, gen: int) -> None:
        if gen != self._gen or self._closing:
            # Nobody wants this child (the page closed or went unparented
            # while the spawn was out): take it down now, while its pty is
            # open and the group signal reaches the viewer. Its exit lands
            # in _on_child_exited with no _child_pid to match.
            if error is None:
                log.debug("gitpage: hunk %s spawned for a page that moved on; stopping it", pid)
                hunkctl.terminate_tree(pid, proctree.process_children(pid))
            return
        if error is not None:
            log.debug("gitpage: spawning hunk failed: %s", error.message)
            self._spawn_failed()
            self._show_exited_card(error.message)
            return
        self._child_pid = pid
        # A fresh hunk starts at the bottom of the stack; the sidecar says
        # otherwise soon if it doesn't.
        self._level = hunkctl.DEFAULT_LEVEL
        self._sync_levels()
        if self._respawn_wanted:
            # Asked to start over (the repo root moved) between the probe's
            # answer and this: the child's exit spawns the next one.
            self._terminate_child()
            return
        GLib.timeout_add(hunkctl.RESOLVE_DELAYS_MS[0], self._resolve_step, gen, 0)

    def _resolve_step(self, gen: int, step: int) -> bool:
        """One `hunk session list --json` on a thread, matched against the
        child's pid (or its children's) back on the main loop."""
        if gen != self._gen or self._closing or self._child_pid is None:
            return GLib.SOURCE_REMOVE
        hunk = self._hunk_path
        pid = self._child_pid
        cwd = self._cwd_provider()

        def work() -> None:
            reply = hunkctl.run(hunkctl.list_argv(hunk))
            session = hunkctl.session_for_pid(reply.stdout, pid, proctree.process_children(pid))
            named = _title_subject_and_sha(cwd, session.title) if session else (None, None)
            GLib.idle_add(self._resolved, gen, step, pid, session, named)

        threading.Thread(target=work, name="git-page-session", daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _resolved(
        self,
        gen: int,
        step: int,
        pid: int,
        session: hunkctl.Session | None,
        named: tuple[str | None, str | None],
    ) -> bool:
        if gen != self._gen or self._closing or self._child_pid != pid:
            return GLib.SOURCE_REMOVE
        if session is None:
            if step + 1 < len(hunkctl.RESOLVE_DELAYS_MS):
                GLib.timeout_add(hunkctl.RESOLVE_DELAYS_MS[step + 1], self._resolve_step, gen, step + 1)
                return GLib.SOURCE_REMOVE
            # Given up: every switch from here on is a respawn — a load
            # queued meanwhile, or an untracked switch the argv missed.
            log.debug("gitpage: no hunk session for pid %s after %d tries", pid, step + 1)
            self._banner.set_revealed(True)
            self._resolving = False
            pending, self._pending_mode = self._pending_mode, None
            stale, self._options_stale = self._options_stale, False
            if (pending and pending != self._spawned_mode) or stale:
                self._respawn()
            return GLib.SOURCE_REMOVE
        self._session_id = session.session_id
        self._banner.set_revealed(False)
        self._resolving = False
        pending, self._pending_mode = self._pending_mode, None
        stale, self._options_stale = self._options_stale, False
        self._apply_title(session.title, session.repo_root, *named)
        self._take_session(session)
        if pending and pending != self._loaded:
            self._loaded = pending
            self._sync_header()
            self.emit("title-changed")
            self._reload(pending)  # the reload's tail reads the options as they are now
        elif stale and not hunkctl.is_show(self._loaded) and self._foreign is None:
            log.debug("gitpage: the untracked switch flipped during the spawn; reloading")
            self._reload(self._loaded)
        else:
            self._run_pending_navigate()
        return GLib.SOURCE_REMOVE

    def _respawn(self) -> None:
        """Start over in the current mode: signal the running child and let
        its exit spawn the next one; leave a note for a spawn still in flight
        (the probe, or VTE's fork) to act on when it lands; or spawn straight
        away when there is nothing."""
        if self._closing:
            return
        if self.hunk_alive:
            self._respawn_wanted = True
            self._terminate_child()
        elif self._spawned:
            self._respawn_wanted = True
        else:
            self._spawned = False
            self._spawn()

    def _terminate_child(self) -> None:
        """SIGTERM hunk — the whole process group VTE started, since the npm
        wrapper never passes a signal on to the viewer it spawnSyncs (see
        hunkctl.terminate_tree). child-exited follows for the wrapper."""
        pid = self._child_pid
        if pid is None:
            return
        hunkctl.terminate_tree(pid, proctree.process_children(pid))

    def _on_child_exited(self, _terminal: Vte.Terminal, _status: int) -> None:
        if self._child_pid is None:
            # A child this page never took on — spawned for a generation
            # that had moved on and terminated in _on_spawned. Whatever the
            # page is doing now (a card, a fresh probe) is not its business.
            self.terminal.reset(True, True)
            return
        self._child_pid = None
        self._session_id = None
        self._spawned = False
        self._resolving = False
        self._reloading = False
        self._syncing_session = False
        self._pending_reload = None
        self._stale = False
        self._pending_mode = None
        self._options_stale = False
        self._navigating = False
        self._pending_navigate = None
        self._sidecar_selection_seen = False
        self._gen += 1  # orphan any session list / get / reload still out
        self._banner.set_revealed(False)
        self.terminal.reset(True, True)
        self._sync_levels()  # nothing to feed: the buttons go insensitive
        self._sync_context()  # the sidebar's cursor keys go insensitive too
        if self._closing:
            return
        if self._respawn_wanted:
            self._respawn_wanted = False
            self._spawn()
            return
        self._show_exited_card()

    # -- reloading ----------------------------------------------------------------------

    def _reload(self, loaded: hunkctl.Loaded) -> None:
        """`hunk session reload` into *loaded* (a mode, or a commit) on a
        thread. A reply saying the session is gone respawns; any other
        refusal (a target hunk can't diff, a timeout) leaves the viewer —
        still showing what it showed — and asks it what that is (see
        _reloaded)."""
        if self._closing:
            return
        if self._session_id is None or self._hunk_path is None:
            self._respawn()
            return
        if self._reloading or self._syncing_session:
            self._pending_reload = loaded
            return
        if loaded == "branch" and self._resolve_parent() is None:
            return
        self._reloading = True
        gen = self._gen
        argv = hunkctl.reload_argv(
            self._hunk_path, self._session_id, loaded, self._parent_target, self._options
        )
        cwd = self._cwd_provider()

        def work() -> None:
            reply = hunkctl.run(argv)
            title = hunkctl.parse_reload_reply(reply.stdout) if reply.ok else None
            GLib.idle_add(self._reloaded, gen, loaded, reply, title, _title_subject_and_sha(cwd, title))

        threading.Thread(target=work, name="git-page-reload", daemon=True).start()

    def _reloaded(
        self,
        gen: int,
        mode: hunkctl.Loaded,
        reply: hunkctl.Reply,
        title: str | None,
        named: tuple[str | None, str | None],
    ) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        self._reloading = False
        if title is None and reply.session_gone:
            log.debug("gitpage: session reload found no session; respawning")
            self._pending_reload = None
            self._stale = False
            self._respawn()
            return GLib.SOURCE_REMOVE
        pending, self._pending_reload = self._pending_reload, None
        if title is None:
            # hunk kept what it had (verified against 0.20.1: a bad range
            # exits 1 and the viewer stays put). A parent that stopped
            # resolving is put down until it resolves again (the next tick
            # re-asks); a newer ask queued meanwhile goes out; else the
            # header goes back to what hunk says it shows (and a move seen
            # meanwhile reloads that, see _synced).
            log.debug("gitpage: session reload into %s refused: %s", mode, reply.stderr.strip())
            if mode == "branch" and reply.returncode is not None:
                self._parent_target = None
                self._sync_header()
            if pending is not None:
                self._reload(pending)
            else:
                self._sync_session(reload=False)
            return GLib.SOURCE_REMOVE
        self._stale = False  # whatever moved, this load is newer
        if pending is not None:
            self._reload(pending)  # the header already says so (load); no flicker back
        else:
            self._apply_title(title, None, *named)
            # The reload reply carries no files: ask the session, so the
            # sidebar's list follows the load — and run the navigate that
            # waited for this side, now that hunk shows it.
            self._files_stale = True
            self._sync_session(reload=False)
            self._run_pending_navigate()
        return GLib.SOURCE_REMOVE

    def _sync_session(self, reload: bool) -> None:
        """`hunk session get` on a thread: the header follows the title hunk
        reports, then, with *reload* (the tree moved), what hunk shows is
        reloaded — a load Collins has no name for is left to hunk's own
        watch. One at a time; a move seen meanwhile waits in _stale."""
        if self._closing or self._session_id is None or self._hunk_path is None:
            return
        if self._syncing_session or self._reloading:
            self._stale = self._stale or reload
            return
        self._syncing_session = True
        gen = self._gen
        argv = hunkctl.get_argv(self._hunk_path, self._session_id)
        cwd = self._cwd_provider()

        def work() -> None:
            reply = hunkctl.run(argv)
            session = hunkctl.parse_session_get(reply.stdout) if reply.ok else None
            named = _title_subject_and_sha(cwd, session.title) if session else (None, None)
            GLib.idle_add(self._synced, gen, reload, reply, session, named)

        threading.Thread(target=work, name="git-page-get", daemon=True).start()

    def _synced(
        self,
        gen: int,
        reload: bool,
        reply: hunkctl.Reply,
        session: hunkctl.Session | None,
        named: tuple[str | None, str | None],
    ) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        self._syncing_session = False
        if reply.session_gone:
            log.debug("gitpage: session get found no session; respawning")
            self._pending_reload = None
            self._stale = False
            self._respawn()
            return GLib.SOURCE_REMOVE
        pending, self._pending_reload = self._pending_reload, None
        if pending is not None:
            self._reload(pending)  # asked meanwhile: the title here is already old news
            return GLib.SOURCE_REMOVE
        if session is not None:
            self._apply_title(session.title, session.repo_root, *named)
            self._take_session(session)
        stale, self._stale = reload or self._stale, False
        if stale and self._foreign is None:
            self._reload(self._loaded)
        return GLib.SOURCE_REMOVE

    def _take_session(self, session: hunkctl.Session) -> None:
        """What a session record carries besides its title: hunk's files
        (the sidebar's list, rebuilt when they, the load or the untracked
        switch changed, or the tree moved since) and the cursor's file
        (the highlight's fallback — the sidecar's word, when it spoke this
        tick, is fresher and stands)."""
        loaded = None if self._foreign is not None else self._loaded
        key = (session.files, loaded, self._options.untracked)
        if key != self._files_shown or self._files_stale:
            self._files_shown = key
            self._files_stale = False
            self.sidebar.refresh_files(session.files, loaded, self._options.untracked)
        if not self._sidecar_selection_seen:
            self.sidebar.set_selection(session.selected_path, session.selected_hunk, "session")
        self._sidecar_selection_seen = False

    # -- the native sidebar --------------------------------------------------------------

    def _sync_context(self) -> bool:
        """Hand the sidebar what the page knows (GitSidebar.set_context):
        the branch, the parent and default branches as BranchRefs
        (gitops.resolve_group_branches — .git reads, no process), the load
        and its resolved sha, the live working-tree side, whether hunk runs
        with the extension, and the automatic parent's name. True when the
        sidebar re-read its commits for it (the groups changed)."""
        cwd = self._cwd_provider()
        parent, default = gitops.resolve_group_branches(cwd, self._parent_name, gitinfo.default_branch(cwd))
        loaded = None if self._foreign is not None else self._loaded
        live = loaded if loaded in ("unstaged", "staged") else None
        return self.sidebar.set_context(
            branch=self._branch,
            parent=parent,
            default=default,
            loaded=loaded,
            resolved_sha=self._resolved_sha,
            live_side=live,
            hunk_alive=self.hunk_alive,
            extension_loaded=self._extension_loaded,
            auto_parent=self._parent_provider(cwd),
        )

    def _sync_sidebar(self) -> None:
        """Show or hide the sidebar: the toggle's word, unless the page is
        narrow (the toggle goes insensitive and its box's tooltip says
        what would help) or a card with nothing to list is up."""
        card_hides = self._card in (_INSTALL, _NOT_A_REPO)
        self.sidebar.set_visible(self._sidebar_wanted and not self._narrow and not card_hides)
        self._sidebar_toggle.set_sensitive(not self._narrow)
        if self._narrow:
            tooltip = _("Widen the page to show the panels")
        elif self._sidebar_wanted:
            tooltip = _("Hide the commits and files panels")
        else:
            tooltip = _("Show the commits and files panels")
        self._sidebar_toggle_box.set_tooltip_text(tooltip)
        self._sidebar_toggle.set_tooltip_text(None if self._narrow else tooltip)

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        self._sidebar_wanted = button.get_active()
        self._sync_sidebar()

    def _on_narrow(self, narrow: bool) -> None:
        self._narrow = narrow
        self._sync_sidebar()

    def _on_navigate_requested(self, _sidebar: GitSidebar, path: str, side: str) -> None:
        """A file row clicked: on the live side (or the flat list), move
        hunk's cursor there now; on the working tree's other side, load
        that side first and navigate once the reload lands."""
        if not side or (side == self._loaded and self._foreign is None):
            self._navigate(path)
            return
        self.load(side)
        self._pending_navigate = (path, side)

    def _run_pending_navigate(self) -> None:
        pending, self._pending_navigate = self._pending_navigate, None
        if pending is None:
            return
        path, side = pending
        if side == self._loaded and self._foreign is None:
            self._navigate(path)

    def _navigate(self, path: str) -> None:
        """`hunk session navigate --file <path>` on a thread (the show_diff
        tool's shape, hunkctl.navigate_argv); a refusal is a toast."""
        if self._closing or self._session_id is None or self._hunk_path is None:
            return
        argv = hunkctl.navigate_argv(self._hunk_path, self._session_id, path)
        self._navigating = True
        gen = self._gen

        def work() -> None:
            reply = hunkctl.run(argv)
            GLib.idle_add(self._navigated, gen, path, reply, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-page-navigate", daemon=True).start()

    def _navigated(self, gen: int, path: str, reply: hunkctl.Reply) -> bool:
        self._navigating = False
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        if reply.ok:
            # hunk's cursor is on the file now; the tick confirms within
            # two seconds, the highlight needn't wait for it.
            self.sidebar.set_selection(path, None, "session")
        elif reply.session_gone:
            log.debug("gitpage: session navigate found no session; respawning")
            self._respawn()
        else:
            self._toast(hunkctl.navigate_error(reply))
        return GLib.SOURCE_REMOVE

    def _on_key_requested(self, _sidebar: GitSidebar, key: bytes) -> None:
        """A button that needs hunk's cursor: feed the extension's key
        through the pty and put the keyboard in the VTE, so hunk's own
        confirm (`D`'s) answers to Enter."""
        if not self.hunk_alive:
            return
        self.terminal.feed_child(key)
        self.terminal.grab_focus()

    def _on_mutated(self, _sidebar: GitSidebar) -> None:
        """A native mutation landed (stage all, a commit): re-seed the
        freshness signature so the tick doesn't reload a second time,
        refresh the lists, and reload hunk now."""
        cwd = self._cwd_provider()
        self._signature = gitinfo.tree_signature(cwd, self._parent_name)
        self._remote_signature = gitinfo.remote_refs_signature(cwd)
        self._files_stale = True
        self.sidebar.refresh_commits()
        if not self.hunk_alive or self._resolving or self._foreign is not None:
            return
        self._reload(self._loaded)  # a respawn without a session id

    def _on_parent_picked(self, _sidebar: GitSidebar, name: str | None) -> None:
        """The sidebar's parent pick — a branch name, or None for Automatic
        — lands where the sidecar's used to: the user's pick, re-resolved,
        published to the extension, persisted (page_state), and a branch
        diff reloaded against the new base. The signature is re-seeded
        with the new base so the tick reads no move in it."""
        self._user_parent = name if hunkctl.safe_ref(name) else None
        self._user_parent_missing = False
        target_before = self._parent_target
        self._resolve_parent()
        cwd = self._cwd_provider()
        self._signature = gitinfo.tree_signature(cwd, self._parent_name)
        self._write_sidecar()
        self._sync_header()
        self.emit("title-changed")
        self._sync_context()
        if (
            self.hunk_alive
            and not self._resolving
            and self._parent_target != target_before
            and self._loaded == "branch"
            and self._foreign is None
        ):
            self._reload("branch")

    def _toast(self, text: str) -> None:
        toast = Adw.Toast(title=text, timeout=4)
        toast.set_use_markup(False)
        self._toast_overlay.add_toast(toast)


def _title_subject_and_sha(cwd: str | None, title: str | None) -> tuple[str | None, str | None]:
    """(subject, full sha) of the commit a `<repo> show <ref>` title names,
    for the worker threads that carry a title back to the main loop (one
    `git log -1`, hunkctl.commit_subject_and_sha); (None, None) for any
    other title, or when git couldn't say."""
    if not title:
        return None, None
    kind, ref = hunkctl.loaded_from_title(title)
    if kind != "show":
        return None, None
    subject, sha = hunkctl.commit_subject_and_sha(cwd, ref)
    return subject or None, sha


def shutdown_all() -> None:
    """Stop every page's hunk: the application's shutdown hook. A window
    destroyed on quit unrealizes its pages but needn't dispose them before
    the process ends, and the pty closing behind them would strand hunk's
    viewer (see GitPage._shutdown) — this is the call that doesn't rely on
    the main loop coming round again."""
    for page in list(_LIVE_PAGES):
        page._shutdown()

