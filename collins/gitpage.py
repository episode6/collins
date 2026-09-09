# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page: the native diff view beside the session, over the agent's
working tree.

What the page shows is a DiffView (diffview.py) under a one-row header —
the branch, a breadcrumb of what is loaded, the find toggle, the sidebar
toggle, the view's menu and refresh; the tab's X closes — with the commits
and files sidebar (gitsidebar.py) to the view's left. It loads one of
five things (the gitloads.Loaded vocabulary): the unstaged working tree,
the index, the branch against its parent, a commit as {"show": ref} or a
range as {"range": "a...b"}, picked by Ctrl+1/2/3, the sidebar's rows,
the host's open_git_page(mode) or the agent's show_diff tool. Every load
is one gitops.read_diff on a daemon thread behind a generation counter
(`_gen`), landing at PRIORITY_DEFAULT in `_diff_read`: the breadcrumb and
the sidebar's context come from the `Loaded` itself plus the sha and
subject the same worker read, the files list from the read's files and
the `git status` it carried (GitSidebar.refresh_files takes it), and the
view is patched by stable key so the reader's place, an untouched hunk's
widget, its selection and its notes survive a reload. A restored page is
built unselected, maybe in a hidden strip, and opens on its first "map"
(`_open_view`: a thread reads the stack git shows under HEAD and, for a
saved commit, whether it still exists — else the default mode).

The sidebar's highlight follows the view's `current-changed` (the file at
the top of the viewport, debounced in the view, or the hunk the keyboard
moved into); a files-list click reveals the section (`_navigate` →
DiffView.reveal, synchronous — on the working tree's other side, that
side loads first with the reveal queued behind it, `_pending_navigate`);
a commits row is a load ("load-requested" → load()); the sidebar's
whole-tree mutations ("mutated": stage all, a commit) re-seed the
freshness signatures, re-read the stack and reload the view at once. The
view's own mutations — a header button, its menu, `x` / `X` / `D` —
arrive as "mutation-requested" with a gitpatch.MutationRequest: the page
re-reads the file's patch from git on a thread, plans it (the planners
compare the view against the disk before trusting a line), asks through
dialogs.confirm_dialog where the plan says so, then runs it
(gitops.run_plan) behind the sidebar's busy and toasts the outcome.

The header holds a find bar (Ctrl+F: one query over every hunk, Enter /
Shift+Enter across hunks) and a menu (layout, line numbers, wrap, the
agent's notes, reload, keyboard shortcuts). The chords of
keybindings.GROUP_GIT (`]` `[` `.` `,` `}` `{` `z` `0` `1` `2` `l` `w`
`a` `r` `/` `?` `e` `q`, Ctrl+F, `x` `X` `D` `c` `E`) are `git.*`
actions in a group on the page, fired by a controller scoped to the view
(DiffView.apply_keybindings) — bare letters never reach the agent's
terminal; they go insensitive while a note editor is open. `e` opens the
file in the session's editor at the cursor line through
`win.open-in-editor`; the `0` `1` `2` `l` `w` keys and the menu write
their setting through `win.git-option` so every page follows (a page in
a bare window — the e2e — applies it to itself).

Freshness rides the tab footer's 2 s tick, forwarded by the host while
the page is mapped: gitinfo.tree_signature covers the index, HEAD and the
parent ref (a commit or staging done from a shell or by the agent
reloads what is shown), gitinfo.refs_signature a branch written anywhere
or a push (the stack and the commits list are re-read). Edits to the
working tree are caught by Gio.FileMonitors on the loaded files'
directories (at most MAX_DIR_MONITORS, else the repository root alone),
debounced _WATCH_DEBOUNCE_MS into a gitops.tree_state_signature compare
on a thread and a reload by key when it moved, plus a slow tick every
_WATCH_SLOW_TICKS ticks that compares regardless (an untracked file in a
directory nobody watches). Commit and range loads have no monitors.

The parent branch — the branch the current one stacks on, what the "vs"
load diffs against and the current group's commits stop at — is git's
word first: the stack of local branches between the default branch and
HEAD (gitops.stack_branches, read on a thread whenever the tree or a ref
moved, _refresh_branch_stack), its nearest branch being the parent and
the rest the sidebar's stack groups. Only when git shows no stack does
the host's rung name it (parent_provider: the newest PR's base, then the
default parent from Preferences → Git, then the default branch). A stack
read that moves the parent reloads a branch diff that now has another
base. There is no picker and no persisted parent: the layout slot is
gitloads.encode_state(loaded, sidebar).

Below the Adw.BreakpointBin's breakpoint (_NARROW_MAX_WIDTH) the page is
one column at a time, the editor's narrow mode: the diff by default, and
the header's panel button swaps the commits and files panels in for it
(_panels_requested, forgotten when the page widens) — a row picked, a
chord or the host's load drops back to the diff. Above it the toggle's
word — persisted in page_state's "sidebar" — rules, both columns side by
side; the not-a-repo card hides the panels either way (nothing to list). Page-local toasts (commit
results, git's refusals, a reveal that missed) float in an
Adw.ToastOverlay over the page. Preferences → Git reaches the page as the
whole settings dict (apply_settings, on every change the dialog makes),
read into a gitloads.Options: the layout, line numbers, wrap and word
diff go to the view (DiffView.set_options), the untracked switch re-reads
a working-tree load, the page size re-pages the commits list; the view
follows the editor's font and style scheme (editor_font,
editor_style_scheme) and the app's light/dark.

The decisions with no widget in them — the Loaded vocabulary, the
breadcrumb, the chords, the layout slot — live in gitloads; the diff's
model in diffmodel, the staging arithmetic in gitpatch, every git call in
gitops. This module never imports terminal.py (which imports it) and
declares no "shell-exited": the strip closes any page that emits it.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import (  # noqa: E402
    dialogs,
    diffmodel,
    diffnotes,
    diffview,
    footerapps,
    gitinfo,
    gitloads,
    gitmodel,
    gitops,
    gitpatch,
    keybindings,
    mcptools,
    prefslayout,
)
from .commitcard import CommitCard  # noqa: E402
from .diffview import DiffView  # noqa: E402
from .editor import style_scheme  # noqa: E402
from .gitmodel import BranchRef  # noqa: E402
from .gitoperation import OperationBar  # noqa: E402
from .gitsidebar import GitSidebar  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

# The width a column has to reach for the dock to spend free gutter on one
# (see PanelDock._column_floor): room for the sidebar (220 px, its own
# request) beside a readable diff, so a fresh page opens wide enough for
# both. Not the page's minimum: that is the breakpoint bin's request
# below, what a drag of the divider can shrink the column to, and under
# the breakpoint the sidebar hides so the diff keeps its width.
_MIN_PAGE_WIDTH = 680
# The Adw.BreakpointBin's size request (it reports this as the page's
# minimum rather than its children's sum, see collins/editor.py's pane).
# Both axes, or the bin warns per allocation.
_BIN_MIN_WIDTH = 460
_BIN_MIN_HEIGHT = 120
# Below this width the sidebar hides regardless of the toggle: one pixel
# under _MIN_PAGE_WIDTH, so a page at the floor shows both.
_NARROW_MAX_WIDTH = _MIN_PAGE_WIDTH - 1
# The panel toggle's glyph: the sidebar's on a wide page, a back arrow on
# a narrow one (there it is the way between the diff and the panels, the
# editor's narrow-mode back button).
_SIDEBAR_ICON = "sidebar-show-symbolic"
_BACK_ICON = "go-previous-symbolic"
# Where the paned's divider starts: the sidebar a little wider than its
# request, the rest the view's.
_SIDEBAR_POSITION = 240

# The tab's icon, bundled under data/icons — and the footer's git button's
# (terminal.py), so the button and the page it opens wear one glyph.
ICON = "git-merge-symbolic"

# How the branch label is cut when a branch name runs long: the header has
# the breadcrumb to fit beside it.
_BRANCH_MAX_CHARS = 24

# Stack page names.
_VIEW = "view"
_CARD = "card"

# The watch (see the module docstring): how many directory monitors a
# working-tree load may hold before the repository root alone is watched,
# how long after the last event the tree state is compared, and every how
# many 2 s ticks it is compared regardless (10 s).
MAX_DIR_MONITORS = 64
_WATCH_DEBOUNCE_MS = 300
_WATCH_SLOW_TICKS = 5

# The action group the view's keys and header menu act on, inserted on the
# page under this prefix (keybindings' `git.*`).
_ACTIONS = "git"
# The settings the keys and the menu may write through win.git-option.
_KEY_SETTINGS = ("git_layout", "git_line_numbers", "git_wrap_lines")

# Which card the stack shows, when it shows one (see _show_card).
_NOT_A_REPO = "not-a-repo"


class GitPage(Adw.Bin):
    """The session's git page: the diff view under a one-row header
    (PanelPage, see panelstrip).

    One per session tab. Opens on first "map" (a restored page may sit
    unselected in a hidden strip and must not read a diff until shown)
    and switches what is loaded with one read per load.
    """

    page_kind = "git"
    column_floor = _MIN_PAGE_WIDTH  # the dock spends free gutter on a column at least this wide
    # ... and opens the column at least this wide even when no gutter pays
    # for it (PanelDock._column_floor). A drag may take the column down to
    # the breakpoint bin's request, and the sidebar folds. The column's
    # width includes the strip's own chrome around the page (measured:
    # 5 px), so the seed carries slack over the breakpoint — a page opened
    # exactly at the floor would sit one pixel under it, folded.
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
        loaded: gitloads.Loaded = gitloads.DEFAULT_MODE,
        sidebar: bool = True,
    ) -> None:
        """*cwd_provider*: the agent's live cwd (TerminalTab.current_agent_cwd)
        — read at every open, read and poll. *parent_provider(cwd)*: the
        parent branch NAME ("main") the host computes for a tree git shows
        no stack in (the newest PR's base, else the default branch), or
        None; the page resolves it to a diff target itself
        (gitinfo.resolve_branch). *on_closed(page)*: fired from
        page_closed() — the tab's X, through the strip's close funnel — so
        the host drops its reference. *loaded*: what to open on (a
        restored layout's, or the footer's choice): a mode, a commit as
        {"show": ref}, or a range as {"range": "a...b"}. *sidebar*:
        whether the panels show (the header's toggle, restored from the
        layout: gitloads.decode_sidebar)."""
        super().__init__()
        self.add_css_class("git-page")
        self._cwd_provider = cwd_provider
        self._parent_provider = parent_provider
        self._on_closed = on_closed
        self._loaded: gitloads.Loaded = loaded if gitloads.loaded_ok(loaded) else gitloads.DEFAULT_MODE
        # The stack git shows under HEAD (gitops.stack_branches: the local
        # branches between the default branch and HEAD, nearest first), as
        # last read by _refresh_branch_stack; cleared on a branch change,
        # and _resolve_parent skips an entry that stopped resolving. Every
        # read carries its generation; a bump orphans the one in flight.
        self._branch_stack: tuple[BranchRef, ...] = ()
        # The local branches at HEAD's own commit (read_stack's second
        # half): the current branch and its twins, the header's words.
        self._head_twins: tuple[str, ...] = ()
        self._branch_stack_gen = 0
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
        # The subject of the loaded commit, for the breadcrumb; None for a
        # mode, or while it isn't known (a load just asked for, git not
        # answering). Read on the worker thread that brings the diff back.
        self._subject: str | None = None
        # The full sha the loaded commit's ref resolved to, read by the same
        # worker: what the sidebar matches a `show HEAD` load to a row with.
        self._resolved_sha: str | None = None
        self._signature: tuple | None = None
        # What moves when any ref is written (gitinfo.refs_signature): a
        # commit on another branch of the stack, a branch made or deleted,
        # a push or fetch. A move re-reads the stack and the commits list
        # (the groups, the `↑` marks), never the diff.
        self._refs_signature: tuple | None = None

        # -- the sidebar's feed (see gitsidebar) --------------------------------
        # The header toggle's word; the sidebar shows only while it is True,
        # the page is above the breakpoint and no card hides it.
        self._sidebar_wanted = bool(sidebar)
        self._narrow = False
        # Narrow page: the panels alone instead of the diff, until a row
        # is picked or the page widens (the editor's _picker_requested).
        # Not persisted.
        self._panels_requested = False
        # _sync_sidebar sets the toggle to what the width shows; the
        # handler must not read that back as the user's word.
        self._syncing_toggle = False
        # A reveal waiting for a working-tree side to load first: (path, side).
        self._pending_navigate: tuple[str, str] | None = None

        # -- the view (see the module docstring) ---------------------------------
        # Opened = the DiffView is up over a repository (the not-a-repo card
        # is the only other face); opening = the first read (the stack, a
        # saved commit's subject) is out.
        self._opened = False
        self._opening = False
        # A read_diff in flight, the load asked while it was, and the tree
        # state (gitops.tree_state_signature) the watch compares against —
        # seeded by every working-tree load's own worker.
        self._loading = False
        self._pending_load: gitloads.Loaded | None = None
        self._tree_state: str | None = None
        self._monitors: list[Gio.FileMonitor] = []
        self._watch_source = 0
        self._watch_checking = False
        self._watch_stale = False
        self._watch_ticks = 0
        # The last whole settings dict apply_settings saw: what a key that
        # writes an option applies locally when no window action is there
        # to persist it (a page in a bare test window).
        self._settings: dict = {}
        self._scheme_setting = ""

        # -- what Preferences → Git says (see apply_settings) ------------------
        # The shipped defaults until the host's first apply_settings — a
        # page built by the strip gets the dock's settings before it maps.
        self._options = gitloads.Options()

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
        # the pointer while the button is insensitive (the not-a-repo
        # card): an insensitive widget is out of pick, its box isn't.
        self._sidebar_toggle = Gtk.ToggleButton(icon_name=_SIDEBAR_ICON)
        self._sidebar_toggle.set_active(self._sidebar_wanted)
        self._sidebar_toggle.add_css_class("flat")
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        self._sidebar_toggle_box = Gtk.Box()
        self._sidebar_toggle_box.append(self._sidebar_toggle)
        self._find_toggle = Gtk.ToggleButton(icon_name="edit-find-symbolic")
        self._find_toggle.add_css_class("flat")
        self._find_toggle.set_tooltip_text(keybindings.with_hint(_("Find in the diff"), "git.find"))
        self._menu_button = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=self._build_menu())
        self._menu_button.add_css_class("flat")
        self._menu_button.set_tooltip_text(_("Diff view options"))
        header.append(self._sidebar_toggle_box)  # first: the way to the panels, narrow or wide
        header.append(self._find_toggle)
        header.append(self._menu_button)
        header.append(refresh)
        self._refresh_button = refresh

        # -- the find bar (Ctrl+F) -----------------------------------------------
        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_show_close_button(True)
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._search_entry = Gtk.SearchEntry(hexpand=True)
        self._search_entry.set_placeholder_text(_("Find in the diff"))
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("activate", lambda *_a: self._search_step(True))
        self._search_entry.connect("next-match", lambda *_a: self._search_step(True))
        self._search_entry.connect("previous-match", lambda *_a: self._search_step(False))
        self._search_entry.connect("stop-search", lambda *_a: self._search_bar.set_search_mode(False))
        search_box.append(self._search_entry)
        previous = Gtk.Button(icon_name="go-up-symbolic")
        previous.add_css_class("flat")
        previous.set_tooltip_text(_("Previous match (Shift+Enter)"))
        previous.connect("clicked", lambda *_a: self._search_step(False))
        search_box.append(previous)
        following = Gtk.Button(icon_name="go-down-symbolic")
        following.add_css_class("flat")
        following.set_tooltip_text(_("Next match (Enter)"))
        following.connect("clicked", lambda *_a: self._search_step(True))
        search_box.append(following)
        self._search_label = Gtk.Label()
        self._search_label.add_css_class("dim-label")
        self._search_label.add_css_class("caption")
        search_box.append(self._search_label)
        self._search_bar.set_child(search_box)
        self._search_bar.connect_entry(self._search_entry)
        # No key-capture widget: typing anywhere in the page must not open
        # the bar — bare letters are the view's keys.
        self._search_bar.connect("notify::search-mode-enabled", self._on_search_mode_changed)
        self._find_toggle.bind_property(
            "active",
            self._search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        # -- the diff view, and the card that stands in for it -------------------
        self._card_slot = Adw.Bin(vexpand=True)
        self._card_button: Gtk.Button | None = None
        self._card: str | None = None  # which card is up, None while the view is
        self._stack = Gtk.Stack(vexpand=True)
        self._diffview = DiffView()
        self._diffview.connect("current-changed", self._on_current_changed)
        self._diffview.connect("open-requested", self._on_open_requested)
        self._diffview.connect("mutation-requested", self._on_mutation_requested)
        self._diffview.connect("editing-changed", self._on_note_editing_changed)
        self._diffview.apply_keybindings(keybindings.current())
        # The view column: the in-progress bar (a half-finished rebase /
        # merge / cherry-pick / revert over a working-tree load,
        # gitoperation.py), the commit card (empty but for a commit load,
        # commitcard.py), over the diff.
        self.operation_bar = OperationBar()
        self.operation_bar.connect("continue-requested", lambda _b: self._on_continue_requested())
        self.operation_bar.connect("abort-requested", lambda _b: self._on_abort_requested())
        self.commit_card = CommitCard()
        view_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        view_column.append(self.operation_bar)
        view_column.append(self.commit_card)
        view_column.append(self._diffview)
        self._stack.add_named(view_column, _VIEW)
        self._stack.add_named(self._card_slot, _CARD)
        self._install_actions()
        # The view follows the editor's scheme, and the app's light/dark
        # when that says "follow" (the same pair editor.py listens to).
        style_manager = Adw.StyleManager.get_default()
        self._dark_id = style_manager.connect("notify::dark", lambda *_a: self._apply_scheme())
        self._apply_scheme()

        # -- the sidebar beside the stack ------------------------------------------
        self.sidebar = GitSidebar(cwd_provider, self._options)
        self.sidebar.connect("load-requested", lambda _s, loaded: self.load(loaded))
        self.sidebar.connect("navigate-requested", self._on_navigate_requested)
        self.sidebar.connect("show-all-requested", lambda _s: self._show_all())
        self.sidebar.connect("revert-requested", self._on_revert_requested)
        self.sidebar.connect("file-action-requested", self._on_file_action_requested)
        self.sidebar.connect("open-requested", self._on_file_open_requested)
        self.sidebar.connect("open-with-requested", self._on_open_with_requested)
        self.sidebar.connect("mutated", self._on_mutated)
        self.sidebar.connect("filter-changed", lambda _s, text: self._on_filter_changed(text))
        self.sidebar.connect("filter-escaped", lambda _s: self._on_filter_escaped())
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
        box.append(self._search_bar)
        box.append(self._bin)
        # Page-local toasts: commit results, git's refusals, a reveal that
        # missed (see gitsidebar._toast, which finds this overlay).
        self._toast_overlay = Adw.ToastOverlay(child=box)
        self.set_child(self._toast_overlay)
        self._sync_sidebar()

        # Ctrl+1/2/3, in the capture phase on the page: they fire wherever
        # the focus sits inside it.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        self._sync_header()
        # First shown (and every re-show): make sure the view is open. A
        # restored page is built unselected, maybe in a hidden strip, and
        # must not read a diff nobody is looking at.
        self.connect("map", lambda *_a: self._ensure_open())
        # Gone with its tab, or with the window: see _on_unrealize.
        self.connect("unrealize", self._on_unrealize)

    # -- public -------------------------------------------------------------

    @property
    def loaded(self) -> gitloads.Loaded:
        """What the page is showing (or will open on): one of gitloads.MODES,
        a commit as {"show": ref} or a range as {"range": "a...b"}."""
        return self._loaded

    @property
    def diff_view(self) -> DiffView:
        """The view (the e2e's probes and the show_diff tool's reveal)."""
        return self._diffview

    @property
    def opened(self) -> bool:
        """Whether the view is up over a repository (the first read has
        been asked for; `settled()` says whether it landed)."""
        return self._opened

    @property
    def repo_root(self) -> Path | None:
        """The repository the view is over, None on the card. What the
        marking tools resolve a file against: the diff the agent sees,
        not wherever its shell has gone since."""
        return self._repo_root if self._opened or self._opening else None

    @property
    def opening(self) -> bool:
        """Whether the view is on its way up (the open's thread is out):
        a card still showing meanwhile is not the page's last word."""
        return self._opening

    def reveal(
        self,
        path: str,
        hunk: int | None = None,
        side: str | None = None,
        line: int | None = None,
        focus: bool = True,
    ) -> bool:
        """Scroll the view to *path* (its hunk *hunk*, or the hunk holding
        *line* on *side*) and focus it — DiffView.reveal (*focus* False
        leaves the keyboard where it is: the show_diff tool's way); False
        when the view isn't open or the file isn't loaded. A file the
        files filter hides is shown first: the filter is cleared (the
        sidebar's entry too), as a reveal of something hidden would
        otherwise answer True over a section nobody can see."""
        if not self._opened:
            return False
        if self._diffview.hidden_by_filter(path, side):
            self.sidebar.set_filter_text("")  # its rows follow (debounced), and the signal
            self._diffview.solo(None)  # a soloed file gives way too
            self._diffview.filter("")  # the sections, now: the reveal scrolls to one
            self._sync_search_label()
        return self._diffview.reveal(path, hunk, side, line, focus=focus)

    # The marks' doors, for a caller outside the view (the agent's tools):
    # DiffView's own, on the page's face. Each answers the store's word;
    # a page whose view isn't up has no diff to anchor anything to.

    def notes(self, path: str | None = None) -> list[diffnotes.Note]:
        """Every note on the page (of *path*), parked ones included —
        DiffView.notes."""
        return self._diffview.notes(path)

    def highlights(self, path: str | None = None) -> list[diffnotes.Highlight]:
        """Every highlight on the page (of *path*) — DiffView.highlights."""
        return self._diffview.highlights(path)

    def add_notes(
        self, specs: Sequence[diffnotes.NoteSpec], focus: bool = False, source: str = diffnotes.AGENT
    ) -> list[str] | str:
        """DiffView.add_notes: the batch lands whole or not at all — the
        ids, or the reason. Refused (untranslated, an agent's reply) while
        the view isn't up."""
        if not self._opened:
            return "The git page isn't showing a diff"
        return self._diffview.add_notes(specs, focus=focus, source=source)

    def add_highlights(self, specs: Sequence[diffnotes.HighlightSpec], focus: bool = False) -> int | str:
        """DiffView.add_highlights: the count, or the reason; refused while
        the view isn't up."""
        if not self._opened:
            return "The git page isn't showing a diff"
        return self._diffview.add_highlights(specs, focus=focus)

    def clear_marks(
        self,
        path: str | None = None,
        notes: bool = False,
        highlights: bool = False,
        include_user: bool = False,
    ) -> int:
        """DiffView.clear_marks: how many were dropped."""
        return self._diffview.clear_marks(path, notes=notes, highlights=highlights, include_user=include_user)

    def context(self) -> mcptools.DiffContext:
        """What the page shows, in one read for the diff tools
        (mcptools.DiffContext): the load and its breadcrumb, the view's
        files, the file and hunk the reader is on, the line selection, and
        every mark. Empty of files while the view isn't up."""
        view = self._diffview
        path, hunk, selection = view.current() if self._opened else (None, None, None)
        return mcptools.DiffContext(
            loaded=dict(self._loaded) if isinstance(self._loaded, dict) else self._loaded,
            breadcrumb=self.breadcrumb_text(),
            files=view.files if self._opened else (),
            path=path,
            hunk=hunk,
            selection=selection,
            notes=tuple(view.notes()),
            highlights=tuple(view.highlights()),
        )

    @property
    def card(self) -> str | None:
        """Which card stands in for the view — "not-a-repo" — or None while
        the view is up."""
        return self._card

    def breadcrumb_text(self) -> str:
        """What the header says is loaded."""
        return self._breadcrumb.get_text()

    @property
    def sidebar_shown(self) -> bool:
        """Whether the sidebar is on screen: above the breakpoint the toggle
        says so; under it the panels were asked for; and no card hides
        it."""
        return self.sidebar.get_visible()

    @property
    def diff_shown(self) -> bool:
        """Whether the diff's column is on screen — False only on a narrow
        page showing the panels alone."""
        return self._stack.get_visible()

    @property
    def narrow(self) -> bool:
        """Whether the page is under the breakpoint: one column at a time."""
        return self._narrow

    @property
    def sidebar_wanted(self) -> bool:
        """The header toggle's word (what page_state persists), whether or
        not the page is wide enough to honour it."""
        return self._sidebar_wanted

    def set_sidebar_wanted(self, wanted: bool) -> None:
        """The header toggle's persisted word: show or hide the panels
        beside the diff. On a narrow page it changes nothing on screen
        until the page widens (there the toggle is the column switch)."""
        self._sidebar_wanted = bool(wanted)
        self._sync_sidebar()

    def show_panels(self, shown: bool = True) -> None:
        """Narrow page: the commits and files panels instead of the diff
        (*shown*), or the diff again — the header toggle's press there.
        A no-op above the breakpoint, where both are up."""
        if not self._narrow or shown == self._panels_requested:
            return
        self._panels_requested = shown
        self._sync_sidebar()
        if shown:
            self.sidebar.child_focus(Gtk.DirectionType.TAB_FORWARD)
        else:
            self._diffview.grab_focus()

    def settled(self) -> bool:
        """Whether the view is up with nothing in flight: no read out or
        queued, no reveal waiting for a load. What a caller driving the
        page from outside (the show_diff tool) waits for before trusting
        `loaded` and revealing."""
        return (
            self._opened
            and not self._loading
            and self._pending_load is None
            and self._pending_navigate is None
        )

    def shows(self, loaded: gitloads.Loaded) -> bool:
        """Whether the page shows exactly *loaded* — the same mode, commit
        or range."""
        return self._loaded == loaded

    def load(self, loaded: gitloads.Loaded) -> None:
        """Show *loaded* — "unstaged" | "staged" | "branch", a commit as
        {"show": ref} or a range as {"range": "a...b"}: Ctrl+1/2/3, the
        sidebar's rows and the host's open_git_page(mode) land here.
        Updates the breadcrumb/tab title at once, then reads the diff (or
        leaves it to the open on map). "branch" with no resolvable parent
        is a no-op. Anything else raises ValueError. A reveal waiting for
        an earlier load is dropped: the newer ask wins."""
        if not gitloads.loaded_ok(loaded):
            raise ValueError(f"unknown git page load: {loaded!r}")
        self._pending_navigate = None
        self._diffview.solo(None)  # a new load is the whole stream (a row click re-solos)
        self._show_diff_column()  # a load is an ask to see the diff
        if loaded == "branch" and self._resolve_parent() is None:
            self._sync_header()
            return
        if loaded != self._loaded:
            # The new load's read fills the card; a stale message over a
            # new diff is worse than none.
            self.commit_card.clear()
            if loaded not in ("unstaged", "staged"):
                self.operation_bar.clear()  # the bar is the working tree's
        self._loaded = dict(loaded) if isinstance(loaded, dict) else loaded
        self._subject = None
        self._resolved_sha = None
        self._sync_header()
        self.emit("title-changed")
        self._sync_context()
        if self._opened:
            self._read_diff(self._loaded)
        elif self._card == _NOT_A_REPO and self.get_mapped():
            # A load asked of a page standing on the card (the host's
            # open_git_page, whose own repo check just passed: the tree
            # turned up since): the tick's path, now rather than 2 s on.
            # With no tree still, _open_view re-shows the card.
            self._open_view()
        # else the open on map reads _loaded

    def recheck_tree(self) -> None:
        """A page fronted with no load asked (the footer's button, F6):
        standing on the not-a-repo card, open the view now if the tree
        turned up — `load()`'s path, without a load — rather than on the
        tick 2 s on. With no tree still, _open_view re-shows the card. A
        no-op off the card."""
        if self._card == _NOT_A_REPO and self.get_mapped():
            self._open_view()

    def refresh(self) -> None:
        """Reload what is loaded (the header's ⟳, the `r` key): the diff
        re-read and the commits list re-read. No-op on a card."""
        if self._opened:
            self._refresh_branch_stack()
            self._read_diff(self._loaded)

    def poll_tick(self) -> None:
        """The footer's 2 s tick, forwarded by the host only for a mapped page
        (the page checks get_mapped() again itself). Re-reads the branch
        label; reopens when the agent's repo root moved (a worktree entry)
        or shows the "not a repository" card when there is none;
        re-resolves the parent; seeds and compares gitinfo.tree_signature
        and reloads the current load when it moved, gitinfo.refs_signature
        and re-reads the stack when that did. Never spawns a git process
        itself: file reads and stats; the reads it triggers run on
        threads."""
        if not self.get_mapped() or self._closing:
            return
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None:
            # The tree went away under the view (a worktree removed): take
            # the view down and say why. A card already saying so is left
            # alone.
            if self._opened or self._opening:
                self._close_view()
            if self._card != _NOT_A_REPO:
                self._show_not_a_repo()
            return
        if self._repo_root is not None and root != self._repo_root:
            self._repo_root = root
            self._signature = None
            self._reopen()
            return
        if self._card == _NOT_A_REPO:
            self._open_view()  # the tree turned up (the agent cd'd into one): no click needed
            return
        branch = gitinfo.current_branch(cwd)
        if branch != self._branch:
            self._branch = branch
            self._branch_stack = ()  # another branch: another stack, read below
            self._sync_header()
            self._sync_context()  # another branch: other groups
        target_before = self._parent_target
        self._resolve_parent()
        parent_moved = self._parent_target != target_before
        if parent_moved:
            self._sync_header()
            self.emit("title-changed")
        signature = gitinfo.tree_signature(cwd, self._parent_name)
        refs = gitinfo.refs_signature(cwd)
        # A parent that changed changes the signature's base too; that is
        # not the tree moving, and only a branch diff has to follow it.
        changed = self._signature is not None and signature != self._signature
        moved = changed and not parent_moved
        refs_moved = self._refs_signature is not None and refs != self._refs_signature
        self._signature = signature
        self._refs_signature = refs
        # The lists follow every move — a commit on another branch of the
        # stack, a push (the `↑` marks) — whether or not the diff is
        # reloaded: the stack is re-read, and the commits list with it
        # (_branch_stack_read).
        if changed or refs_moved:
            self._refresh_branch_stack()
        elif parent_moved:
            self._sync_context()
        self._tick(moved, parent_moved and target_before is not None)

    # -- PanelPage protocol (see panelstrip) -----------------------------------

    def page_title(self) -> str:
        return gitloads.tab_title(self._loaded, self._target_label())

    def page_icon(self) -> str | None:
        return ICON

    def grab_page_focus(self) -> None:
        if self._stack.get_visible_child_name() == _CARD and self._card_button is not None:
            self._card_button.grab_focus()
        elif not self._diffview.grab_focus():
            self._refresh_button.grab_focus()  # nothing loaded yet: somewhere in the page

    def has_page_focus(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return focus is not None and (focus is self or focus.is_ancestor(self))

    def page_busy(self) -> bool:
        return False  # nothing is lost by closing: the notes are the tab's, not the page's

    def holds_escape(self) -> bool:
        """The dock's restore-from-maximized yields Escape to the page (see
        paneldock) while the find bar is open (Escape closes the bar),
        while lines are selected (Escape clears the selection), and while
        a note editor is open (Escape cancels it)."""
        return (
            self._search_bar.get_search_mode()
            or self._diffview.has_selection()
            or self._diffview.editing()
        )

    def apply_settings(self, settings: dict) -> None:
        """Preferences → Git (gitloads.Options.from_settings: git_layout,
        git_untracked, git_log_page, git_line_numbers, git_wrap_lines,
        git_word_diff), the editor's font and style scheme (the view
        follows them) and the `git.*` chords. Safe before the open, which
        then reads the options. On an open page a flipped untracked switch
        re-reads a working-tree load; the page size re-pages the commits
        list; the rest reaches the view at once (DiffView.set_options).
        Nothing happens for a call that changed none of them — every
        preference the dialog touches lands here."""
        self._settings = dict(settings)
        old, self._options = self._options, gitloads.Options.from_settings(settings)
        new = self._options
        self._diffview.apply_keybindings(settings.get(keybindings.SETTING))
        diffview.apply_font(settings.get("editor_font") or "")
        self._scheme_setting = settings.get("editor_style_scheme") or ""
        self._apply_scheme()
        self._diffview.set_options(new.layout, new.line_numbers, new.wrap, new.word_diff)
        self._sync_action_states()
        self.sidebar.set_options(new)
        working = self._loaded in ("unstaged", "staged")
        if new.untracked != old.untracked and self._opened and working:
            self._read_diff(self._loaded)

    def page_state(self) -> dict:
        """This page's slot in a serialized dock layout (see panellayout):
        what is loaded, and whether the sidebar is hidden."""
        return gitloads.encode_state(self._loaded, sidebar=self._sidebar_wanted)

    def page_closed(self) -> None:
        """The tab is really closing: every read in flight orphaned, the
        monitors dropped, then on_closed(self)."""
        self._shutdown()
        self._on_closed(self)

    def _shutdown(self) -> None:
        """The page is done for good: no more reads, every reply in flight
        orphaned, the monitors dropped. Idempotent — the strip's close
        funnel and the widget's dispose can each be the first to say so."""
        if getattr(self, "_closing", True):  # a dispose can find a half-built page
            return
        self._closing = True
        self._gen += 1
        self._drop_monitors()
        dark_id = getattr(self, "_dark_id", 0)
        if dark_id:
            Adw.StyleManager.get_default().disconnect(dark_id)
            self._dark_id = 0

    def do_dispose(self) -> None:
        self._shutdown()
        Adw.Bin.do_dispose(self)

    def _on_unrealize(self, *_args) -> None:
        """Unrealized: the tab closed, or the page is being re-parented (a
        drag to another strip, a lift-out). The two look the same here, so
        the decision waits for idle, when a re-parented page is realized
        again and a closed one isn't. That one loses its view — the read
        in flight orphaned, the monitors dropped — without being marked
        closing: were the call wrong (a re-parent into a window shown
        later), the next map simply opens it again."""
        GLib.idle_add(self._after_unrealize, priority=GLib.PRIORITY_DEFAULT)

    def _after_unrealize(self) -> bool:
        if self.get_realized() or self._closing:
            return GLib.SOURCE_REMOVE
        if self._opened or self._opening:
            log.debug("gitpage: page went unrealized; closing the view")
            self._close_view()
        return GLib.SOURCE_REMOVE

    # -- header -------------------------------------------------------------------

    def _target_label(self) -> str | None:
        """The parent shown in the breadcrumb and tab title: the resolved
        target, else the parent's name."""
        return self._parent_target or self._parent_name

    def _sync_header(self) -> None:
        self._branch_label.set_text(f"⎇ {self._branch}" if self._branch else "")
        self._branch_label.set_visible(bool(self._branch))
        self._breadcrumb.set_text(
            gitloads.breadcrumb(self._loaded, self._branch, self._target_label(), self._subject)
        )

    def _on_key_pressed(self, _ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        mode = gitloads.load_for_key(int(keyval), int(state))
        if mode is not None:
            self.load(mode)
            return True
        return False

    def _resolve_parent(self) -> str | None:
        """The diff target for the parent branch (`main`, `origin/main`),
        re-read from the tree each time: a fetch or a checkout can create the
        ref between two asks. The nearest branch of the stack git showed
        (_branch_stack, the first that still resolves — one deleted since the read
        is skipped until the next read drops it), else the host's rung
        (parent_provider: the newest PR's base, then the default parent
        setting, then the default branch). None when there is no parent to
        name."""
        cwd = self._cwd_provider()
        name = next((ref.name for ref in self._branch_stack if gitinfo.resolve_branch(cwd, ref.name)), None)
        if name is None:
            name = self._parent_provider(cwd)
        resolved = gitinfo.resolve_branch(cwd, name)
        # The name goes into the header whether or not it resolves yet, so
        # it passes the same gate the read side applies to what comes back:
        # a PR base or a provider answer that does not look like a ref is
        # not a parent at all.
        self._parent_name = name if gitloads.safe_ref(name) else None
        self._parent_target = resolved[0] if resolved else None
        return self._parent_target

    def _branches_below_parent(self) -> tuple[BranchRef, ...]:
        """The stack's branches under the parent, for the sidebar's groups:
        what follows the parent in _branch_stack; nothing when the parent isn't
        one of them (the host's rung named it, or nothing did)."""
        for index, ref in enumerate(self._branch_stack):
            if ref.name == self._parent_name:
                return self._branch_stack[index + 1 :]
        return ()

    def _trunk_target(self, cwd: str | None) -> str | None:
        """The default branch's diff target (`main`, `origin/main`) — the
        lower end of the stack walk — or None when the tree can't name one
        (then there is no stack to read: the walk needs a floor)."""
        resolved = gitinfo.resolve_branch(cwd, gitinfo.default_branch(cwd))
        return resolved[0] if resolved else None

    def _refresh_branch_stack(self) -> None:
        """Re-read the stack off git on a thread (gitops.stack_branches, two
        git runs), then re-resolve the parent on it and re-read the commits
        list (_branch_stack_read). The one way the commits list is refreshed after
        a move: the tick's, the refresh button's, a mutation's."""
        self._branch_stack_gen += 1
        gen = self._branch_stack_gen
        cwd = self._cwd_provider()
        trunk = self._trunk_target(cwd)

        def work() -> None:
            stack, twins = gitops.read_stack(cwd, trunk) if trunk is not None else ([], [])
            GLib.idle_add(
                self._branch_stack_read, gen, tuple(stack), tuple(twins), priority=GLib.PRIORITY_DEFAULT
            )

        threading.Thread(target=work, name="git-page-stack", daemon=True).start()

    def _branch_stack_read(self, gen: int, stack: tuple[BranchRef, ...], twins: tuple[str, ...] = ()) -> bool:
        """A stack read landed: the parent follows it (a parent that moved
        re-seeds the freshness signature — the base changed, not the tree
        — and reloads a branch diff, as the tick would), the sidebar gets
        the groups, and the commits list is re-read either way (the
        move that asked for the read may lie inside a group)."""
        if gen != self._branch_stack_gen or self._closing:
            return GLib.SOURCE_REMOVE
        self._branch_stack = stack
        self._head_twins = twins
        target_before = self._parent_target
        self._resolve_parent()
        parent_moved = self._parent_target != target_before
        if parent_moved:
            self._signature = gitinfo.tree_signature(self._cwd_provider(), self._parent_name)
            self._sync_header()
            self.emit("title-changed")
        if not self._sync_context():
            self.sidebar.refresh_commits()
        if parent_moved and target_before is not None and self._loaded == "branch" and self._opened:
            self._read_diff("branch")
        return GLib.SOURCE_REMOVE

    # -- cards ----------------------------------------------------------------------

    def _show_card(
        self,
        kind: str,
        icon: str,
        title: str,
        description: str,
        button: str,
        on_click: Callable[[], None],
    ) -> None:
        """Replace the view with an Adw.StatusPage carrying one *button*,
        which does *on_click*."""
        page = Adw.StatusPage(icon_name=icon, title=title, description=description)
        action = Gtk.Button(label=button)
        action.add_css_class("pill")
        action.add_css_class("suggested-action")
        action.set_halign(Gtk.Align.CENTER)
        action.set_margin_top(8)
        action.connect("clicked", lambda *_a: on_click())
        page.set_child(action)
        self._card_button = action
        self._card = kind
        self._card_slot.set_child(page)
        self._stack.set_visible_child_name(_CARD)
        self._sync_sidebar()

    def _show_view(self) -> None:
        self._stack.set_visible_child_name(_VIEW)
        self._card_slot.set_child(None)
        self._card_button = None
        self._card = None
        self._sync_sidebar()

    def _show_not_a_repo(self) -> None:
        self._show_card(
            _NOT_A_REPO,
            "folder-symbolic",
            _("Not a git repository"),
            _("The session's working directory has no repository to show. Check again once it does."),
            _("Check again"),
            self._open_view,
        )

    # -- opening and closing the view --------------------------------------------------

    def _ensure_open(self) -> None:
        if self._closing:
            return
        if not self._opened and not self._opening:
            self._open_view()

    def _open_view(self) -> None:
        """Bring the view up over the tree: the stack git shows under HEAD
        and, for a commit load, whether the commit still exists (a saved
        sha the branch was rebased past opens the default mode instead),
        on a thread, then the first read. The not-a-repo card's Check
        again lands here too."""
        if self._closing or self._opened or self._opening:
            return
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None or not cwd:
            self._show_not_a_repo()
            return
        self._opening = True
        self._gen += 1
        gen = self._gen
        self._repo_root = root
        show_ref = gitloads.show_ref(self._loaded)
        trunk = self._trunk_target(cwd)
        self._branch_stack_gen += 1  # this read supersedes any in flight

        def work() -> None:
            subject = gitloads.commit_subject(cwd, show_ref) if show_ref else None
            stack, twins = gitops.read_stack(cwd, trunk) if trunk is not None else ([], [])
            GLib.idle_add(
                self._view_opened, gen, subject, tuple(stack), tuple(twins), priority=GLib.PRIORITY_DEFAULT
            )

        threading.Thread(target=work, name="git-page-open", daemon=True).start()

    def _view_opened(
        self, gen: int, subject: str | None, stack: tuple[BranchRef, ...], twins: tuple[str, ...] = ()
    ) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE  # _close_view reset the flags; a newer open may be out
        self._opening = False
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None:
            self._show_not_a_repo()
            return GLib.SOURCE_REMOVE
        self._opened = True
        self._repo_root = root
        self._branch = gitinfo.current_branch(cwd)
        self._branch_stack = stack
        self._head_twins = twins
        parent = self._resolve_parent()
        if self._loaded == "branch" and parent is None:
            self._loaded = gitloads.DEFAULT_MODE  # a saved "vs main" in a tree with no main
        if gitloads.is_show(self._loaded) and subject is None:
            # git said the commit is gone (a saved sha the branch was rebased
            # past, a layout restored in another clone): not a load to open
            # on. "" — git couldn't be asked — opens as asked.
            log.debug("gitpage: commit %s doesn't resolve; opening %s", self._loaded, gitloads.DEFAULT_MODE)
            self._loaded = gitloads.DEFAULT_MODE
        self._subject = (subject or None) if gitloads.is_show(self._loaded) else None
        self._resolved_sha = None
        self._signature = gitinfo.tree_signature(cwd, self._parent_name)
        self._refs_signature = gitinfo.refs_signature(cwd)
        self._sync_header()
        self.emit("title-changed")
        self._show_view()
        if not self._sync_context():
            self.sidebar.refresh_commits()
        self._read_diff(self._loaded)
        return GLib.SOURCE_REMOVE

    def _close_view(self) -> None:
        """Take the view down (the page unrealized, the tree went away):
        every read in flight orphaned, the monitors dropped, the view
        emptied."""
        self._opened = False
        self._opening = False
        self._loading = False
        self._pending_load = None
        self._pending_navigate = None
        self._tree_state = None
        self._gen += 1
        self._drop_monitors()
        self._diffview.load((), None, None)
        self.commit_card.clear()
        self.operation_bar.clear()
        self._sync_search_label()

    def _reopen(self) -> None:
        """Start over on the current load (the repo root moved): the view
        closes and opens again over the new tree."""
        if self._closing:
            return
        self._close_view()
        self._open_view()

    # -- reading ---------------------------------------------------------------------------

    def _read_diff(self, loaded: gitloads.Loaded) -> None:
        """Read *loaded* (gitops.read_diff — the numstat pre-pass, the patch
        stream, `git status` and the untracked files for a working-tree
        side) on a thread, with the commit's subject and sha, the merge
        base a branch or range reads its old side at, and the tree state
        the watch compares against; one read at a time, a newer ask
        waiting in _pending_load."""
        if self._closing or not self._opened:
            return
        if self._loading:
            self._pending_load = loaded
            return
        if loaded == "branch" and self._resolve_parent() is None:
            return
        self._loading = True
        gen = self._gen
        cwd = self._cwd_provider()
        parent_target = self._parent_target
        untracked = self._options.untracked
        show_ref = gitloads.show_ref(loaded)
        halves = gitloads.range_halves(gitloads.range_of(loaded))
        working = loaded in ("unstaged", "staged")

        def work() -> None:
            # The state before the read, not after: an edit landing while
            # the diff is read is then a move on the next compare (one
            # reload by key, harmless) instead of one the watch never sees.
            state = gitops.tree_state_signature(cwd) if working else None
            read = gitops.read_diff(cwd, loaded, parent_target, untracked)
            # A half-finished rebase / merge / cherry-pick / revert is the
            # working tree's business: the bar over the diff names it.
            operation = gitops.in_progress(gitinfo.git_dir(cwd)) if working else None
            # One `git log -1` names the commit for the breadcrumb, the
            # sidebar's ▸ and the commit card alike.
            message = gitloads.commit_message(cwd, show_ref) if show_ref else None
            github_url = gitinfo.github_url(cwd) if message is not None else None
            base: str | None = None
            if loaded == "branch" and parent_target:
                base = gitops.merge_base(cwd, parent_target, "HEAD")
            elif halves is not None:
                base = gitops.merge_base(cwd, halves[0], halves[1])
            GLib.idle_add(
                self._diff_read,
                gen,
                loaded,
                read,
                (message, github_url),
                base,
                state,
                operation,
                priority=GLib.PRIORITY_DEFAULT,
            )

        threading.Thread(target=work, name="git-page-read", daemon=True).start()

    def _diff_read(
        self,
        gen: int,
        loaded: gitloads.Loaded,
        read: gitops.DiffRead,
        named: tuple[gitloads.CommitMessage | None, str | None],
        base: str | None,
        state: str | None,
        operation: gitops.InProgress | None,
    ) -> bool:
        if gen != self._gen or self._closing or not self._opened:
            return GLib.SOURCE_REMOVE
        self._loading = False
        pending, self._pending_load = self._pending_load, None
        if pending is not None and pending != loaded:
            self._read_diff(pending)  # the header already says so (load); this read is old news
            return GLib.SOURCE_REMOVE
        if loaded != self._loaded:
            return GLib.SOURCE_REMOVE  # load() moved on without a read of its own yet
        # An ask for the same load that arrived while this read was out
        # (the tick's moved index, a mutation landing, the untracked switch)
        # is a move this read can't have seen — the tick already advanced
        # its signature past it, so nothing else would reload. This read is
        # drawn (something shows at once) and the ask re-reads after it.
        reread = pending is not None
        message, github_url = named
        showing = message is not None and gitloads.is_show(loaded)
        self._subject = (message.subject or None) if showing else None
        self._resolved_sha = message.sha if message is not None else None
        if showing:
            self.commit_card.show(message, github_url, self._scheme())
        else:
            self.commit_card.clear()
        if operation is not None:
            self.operation_bar.show(operation, gitmodel.unmerged_count(read.status))
        else:
            self.operation_bar.clear()
        self._sync_header()
        self.emit("title-changed")
        if not read.ok:
            log.debug("gitpage: read_diff(%r) failed: %s", loaded, read.error)
            self._toast(_("Couldn't read the diff: {error}").format(error=read.error or "git"))
        cwd = self._cwd_provider()
        parent_target = self._parent_target

        def reader(file: diffmodel.File, side: str) -> bytes | None:
            return gitops.side_bytes(cwd, loaded, side, file.path, file.previous_path, parent_target, base)

        self._diffview.load(read.files, loaded, reader, repo=str(self._repo_root or ""))
        self.sidebar.refresh_files(_file_summaries(read.files), loaded, self._options.untracked, read.status)
        working = loaded in ("unstaged", "staged")
        self._tree_state = state
        # An event that arrived while this read was out (marked stale by
        # _watch_check, which runs no compare beside a read) is compared
        # now; the monitors are re-made first, which clears the mark.
        stale = self._watch_stale
        self._install_monitors(read.files if working else None)
        self._sync_context()
        self._sync_search_label()
        if reread:
            # A navigate waiting for this side stays queued: revealed now
            # it would scroll a view the re-read is about to redraw.
            self._read_diff(loaded)  # its worker samples the tree state anew: no compare needed
            return GLib.SOURCE_REMOVE
        self._run_pending_navigate()
        if stale and working:
            self._watch_check()
        return GLib.SOURCE_REMOVE

    def _tick(self, moved: bool, parent_moved: bool) -> None:
        """The 2 s tick's second half (after the branch, parent and
        signatures were re-read): a moved base reloads a branch diff, a
        moved index / HEAD re-reads the load, and every _WATCH_SLOW_TICKS
        ticks a working-tree load's tree state is compared regardless of
        the monitors (an untracked file in a directory none watches)."""
        if not self._opened:
            return
        if parent_moved and self._loaded == "branch":
            self._read_diff("branch")
            return
        if moved:
            self._read_diff(self._loaded)
            return
        self._watch_ticks += 1
        if self._watch_ticks >= _WATCH_SLOW_TICKS:
            self._watch_ticks = 0
            self._watch_check()

    # -- the watch --

    def _install_monitors(self, files: Sequence[diffmodel.File] | None) -> None:
        """Watch the directories the loaded working-tree *files* sit in (the
        old path of a rename too) — the repository root alone with more
        than MAX_DIR_MONITORS of them, or with no file at all (an untracked
        file may appear anywhere). None (a commit or range load) watches
        nothing: the tick covers HEAD and the refs."""
        self._drop_monitors()
        root = self._repo_root
        if files is None or root is None:
            return
        dirs: set[str] = set()
        for file in files:
            for path in (file.path, file.previous_path):
                if path:
                    dirs.add(os.path.dirname(os.path.join(str(root), path)))
        if not dirs or len(dirs) > MAX_DIR_MONITORS:
            dirs = {str(root)}
        for directory in sorted(dirs):
            try:
                monitor = Gio.File.new_for_path(directory).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error as exc:
                log.debug("gitpage: no monitor on %s: %s", directory, exc.message)
                continue
            monitor.connect("changed", self._on_tree_event)
            self._monitors.append(monitor)

    def _drop_monitors(self) -> None:
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []
        if self._watch_source:
            GLib.source_remove(self._watch_source)
            self._watch_source = 0
        self._watch_stale = False

    def _on_tree_event(self, _monitor, file: Gio.File, _other, _event: Gio.FileMonitorEvent) -> None:
        """A watched directory changed: not for `.git` itself (its own
        churn is the tick's business), debounced into one compare."""
        if self._closing or not self._opened:
            return
        if file is not None and file.get_basename() == ".git":
            return
        if self._watch_source:
            GLib.source_remove(self._watch_source)
        self._watch_source = GLib.timeout_add(_WATCH_DEBOUNCE_MS, self._watch_fire)

    def _watch_fire(self) -> bool:
        self._watch_source = 0
        self._watch_check()
        return GLib.SOURCE_REMOVE

    def _watch_check(self) -> None:
        """Compare the tree state on a thread and reload by key when it
        moved. One compare at a time, and none beside a read in flight —
        either marks the event stale, and the compare (_watch_checked) or
        the read (_diff_read) re-runs this when it lands, so an edit
        during a read is drawn rather than dropped."""
        if self._closing or not self._opened or self._loaded not in ("unstaged", "staged"):
            return
        if self._loading or self._watch_checking:
            self._watch_stale = True
            return
        self._watch_checking = True
        gen = self._gen
        cwd = self._cwd_provider()

        def work() -> None:
            state = gitops.tree_state_signature(cwd)
            GLib.idle_add(self._watch_checked, gen, state, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-page-watch", daemon=True).start()

    def _watch_checked(self, gen: int, state: str | None) -> bool:
        self._watch_checking = False
        if gen != self._gen or self._closing or not self._opened:
            return GLib.SOURCE_REMOVE
        stale, self._watch_stale = self._watch_stale, False
        if state != self._tree_state:
            self._tree_state = state
            log.debug("gitpage: the working tree moved under the watch; reloading")
            self._read_diff(self._loaded)
        elif stale:
            self._watch_check()
        return GLib.SOURCE_REMOVE

    # -- the sidebar --------------------------------------------------------------------

    def _sync_context(self) -> bool:
        """Hand the sidebar what the page knows (GitSidebar.set_context):
        the branch, the parent and default branches as BranchRefs
        (gitops.resolve_group_branches — .git reads, no process), the stack
        under the parent, the load and its resolved sha, and whether a
        working-tree side is live. True when the sidebar re-read its
        commits for it (the groups changed)."""
        cwd = self._cwd_provider()
        parent, default = gitops.resolve_group_branches(cwd, self._parent_name, gitinfo.default_branch(cwd))
        operation = self.operation_bar.operation
        if parent is not None:
            # The stack read knows the parent's twins; the .git resolve doesn't.
            twins = next((ref.twins for ref in self._branch_stack if ref.name == parent.name), ())
            parent = BranchRef(parent.name, parent.target, twins)
        return self.sidebar.set_context(
            branch=self._branch,
            twins=self._head_twins,
            parent=parent,
            default=default,
            stack=self._branches_below_parent(),
            loaded=self._loaded,
            resolved_sha=self._resolved_sha,
            live=self._loaded in ("unstaged", "staged"),
            operation_kind=operation.kind if operation is not None else None,
            repo_root=str(self._repo_root) if self._repo_root is not None else None,
        )

    def _sync_sidebar(self) -> None:
        """Show the columns the width has room for. Wide: the diff, and
        the sidebar beside it when the toggle's word says so. Narrow: one
        of the two — the panels alone while they were asked for, else the
        diff — and the toggle reads which (its box's tooltip says what a
        press does). A card with nothing to list hides the panels and
        greys the toggle either way."""
        card_hides = self._card == _NOT_A_REPO
        if self._narrow:
            panels = self._panels_requested and not card_hides
            self.sidebar.set_visible(panels)
            self._stack.set_visible(not panels)
            tooltip = _("Back to the diff") if panels else _("Show the commits and files panels")
        else:
            panels = self._sidebar_wanted and not card_hides
            self.sidebar.set_visible(panels)
            self._stack.set_visible(True)
            tooltip = (
                _("Hide the commits and files panels") if panels else _("Show the commits and files panels")
            )
        self._sidebar_toggle.set_icon_name(_BACK_ICON if self._narrow else _SIDEBAR_ICON)
        self._sidebar_toggle.set_sensitive(not card_hides)
        self._syncing_toggle = True
        try:
            self._sidebar_toggle.set_active(panels)
        finally:
            self._syncing_toggle = False
        self._sidebar_toggle_box.set_tooltip_text(tooltip)
        self._sidebar_toggle.set_tooltip_text(None if card_hides else tooltip)

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._syncing_toggle:
            return
        if self._narrow:
            self.show_panels(button.get_active())
        else:
            self._sidebar_wanted = button.get_active()
            self._sync_sidebar()

    def _show_diff_column(self) -> None:
        """A narrow page showing the panels alone: back to the diff — the
        row picked, the chord pressed or the host's load asked for it."""
        if self._narrow and self._panels_requested:
            self._panels_requested = False
            self._sync_sidebar()

    def _on_narrow(self, narrow: bool) -> None:
        self._narrow = narrow
        if not narrow:
            # Widened with the panels up: the next narrowing starts on the
            # diff again, as the first did.
            self._panels_requested = False
        self._sync_sidebar()

    def _on_navigate_requested(self, _sidebar: GitSidebar, path: str, side: str) -> None:
        """A file row clicked: on the live side (or the flat list), reveal
        it now; on the working tree's other side, load that side first
        and reveal once the read lands."""
        if not side or side == self._loaded:
            self._navigate(path)
            return
        self.load(side)
        self._pending_navigate = (path, side)

    def _on_revert_requested(self, _sidebar: GitSidebar, path: str) -> None:
        """A file row's *Revert file* (its context menu on a commit, the
        branch or a range): the view's file header button, pressed for it —
        the same request, the same plan, so the toast and the reload are
        one path (_on_mutation_requested). The menu is not a header
        button: nothing greys it while a mutation runs, and the view's
        request_file drops a press made while busy, so the gate that every
        button meets is met here first — with its toast."""
        if self.sidebar.busy or self._diffview.busy():
            self._toast(_("Another git operation is still running"))
            return
        if not self._diffview.request_file_at(path):
            self._toast(_("{path} is not in the view: reloading").format(path=path))
            self._read_diff(self._loaded)

    # -- the file rows' context menu on the working tree --

    def _on_file_action_requested(self, _sidebar: GitSidebar, path: str, side: str, action: str) -> None:
        """A file row's Stage / Unstage / Discard… / Resolve with ours /
        theirs (gitsidebar's "file-action-requested", one of gitmodel's
        MENU_* ids): one git run on the path from the sidebar's mutation
        thread, the view's own planners left out — they want the file's
        diff, which the other side's rows, a status-only conflict row and
        an untracked row hidden by the switch don't have. The busy gate
        first, with its toast, as _on_revert_requested: nothing greys a
        menu item. A discard asks (gitmodel.discard_words: the trash for
        an untracked file, a restore for a deleted one); a resolution
        reads the index's stages first so its question can say what the
        side means and whether picking it removes the file."""
        if self._closing or not self._opened:
            return
        if self.sidebar.busy or self._diffview.busy():
            self._toast(_("Another git operation is still running"))
            return
        if not gitops.safe_path(path) or side not in ("unstaged", "staged"):
            return
        cwd = self._cwd_provider()
        row = self.sidebar.file_row(path, side)
        code = row.code if row is not None else None
        paths = [path]
        if row is not None and row.previous_path and gitops.safe_path(row.previous_path):
            paths.append(row.previous_path)  # a rename stages / unstages as one R
        resolve = gitmodel.resolve_side(action)
        if resolve is not None:
            self._ask_resolve(cwd, path, resolve)
        elif action == gitmodel.MENU_DISCARD:
            heading, body, button = gitmodel.discard_words(path, code)
            dialogs.confirm_dialog(
                self, heading, body, button, lambda: self._run_file_action(cwd, path, action, code, paths)
            )
        elif action in (gitmodel.MENU_STAGE, gitmodel.MENU_UNSTAGE):
            self._run_file_action(cwd, path, action, code, paths)

    def _run_file_action(
        self, cwd: str | None, path: str, action: str, code: str | None, paths: Sequence[str]
    ) -> None:
        """Stage (`add -A`), unstage (`reset`) or discard — the trash for an
        untracked file (_trash_paths, never an unlink), else `checkout`
        — behind the sidebar's busy; the toast, and `mutated` when it
        landed."""
        root = str(self._repo_root or cwd or "")
        stage = action == gitmodel.MENU_STAGE

        def work() -> gitops.GitResult:
            try:
                if action == gitmodel.MENU_STAGE:
                    return gitops.stage_paths(cwd, paths)
                if action == gitmodel.MENU_UNSTAGE:
                    return gitops.unstage_paths(cwd, paths)
                if code == "?":
                    return _trash_paths(root, [path])
                return gitops.checkout_paths(cwd, [path])
            except Exception as err:  # the worker's last line of defence
                return gitops.GitResult(False, "", str(err) or err.__class__.__name__)

        def done(answer: object) -> None:
            if not isinstance(answer, gitops.GitResult):
                self._toast(_("git failed"))
                return
            if not answer.ok:
                self._toast(gitops.first_line(answer.stderr) or _("git failed"))
                return
            if action == gitmodel.MENU_DISCARD:
                self._toast(gitmodel.discard_done(path, code))
            else:
                self._toast(gitmodel.stage_done(path, stage))
            self.sidebar.emit("mutated")

        self.sidebar.run_mutation(work, done)

    def _ask_resolve(self, cwd: str | None, path: str, side: str) -> None:
        """Read the unmerged path's index stages on the mutation thread,
        then ask (gitmodel.resolve_words) — what ours and theirs mean
        under the operation in progress, and whether *side* removes the
        file (it has no stage) — and run the resolution on a yes."""
        operation = self.operation_bar.operation
        kind = operation.kind if operation is not None else None

        def work() -> tuple:
            return (gitops.read_unmerged_stages(cwd, path),)

        def done(answer: object) -> None:
            stages = answer[0] if isinstance(answer, tuple) else None
            if stages is None:
                self._toast(_("Couldn't read the index for {path}").format(path=path))
                return
            if not stages:
                self._toast(_("{path} is not unmerged: reloading").format(path=path))
                self._read_diff(self._loaded)
                return
            deletes = gitops.resolution_deletes(side, stages)
            heading, body, button = gitmodel.resolve_words(path, side, kind, deletes)
            dialogs.confirm_dialog(self, heading, body, button, lambda: self._run_resolve(cwd, path, side))

        self.sidebar.run_mutation(work, done)

    def _run_resolve(self, cwd: str | None, path: str, side: str) -> None:
        """`checkout --<side>` + `add`, or `rm` (gitops.resolve_path, which
        re-reads the stages), behind the sidebar's busy."""
        if self._closing or not self._opened:
            return

        def work() -> gitops.Resolution:
            try:
                return gitops.resolve_path(cwd, path, side)
            except Exception as err:  # the worker's last line of defence
                return gitops.Resolution(gitops.GitResult(False, "", str(err) or err.__class__.__name__))

        def done(answer: object) -> None:
            if not isinstance(answer, gitops.Resolution):
                self._toast(_("git failed"))
                return
            if not answer.result.ok:
                self._toast(gitops.first_line(answer.result.stderr) or _("git failed"))
                return
            self._toast(gitmodel.resolve_done(path, side, answer.deleted))
            self.sidebar.emit("mutated")

        self.sidebar.run_mutation(work, done)

    def _on_file_open_requested(self, _sidebar: GitSidebar, path: str) -> None:
        """A file row's *Open in editor*: the diff's own door (`e`), with
        no cursor line."""
        self._on_open_requested(self._diffview, path, 0)

    def _on_open_with_requested(self, _sidebar: GitSidebar, path: str, app_id: str) -> None:
        """A file row's "Open In…" pick: the file, under the repository
        root, handed to the configured app (footerapps.launch_app_file —
        only apps that take a file are listed)."""
        root = self._repo_root
        if root is None or not gitops.safe_path(path):
            return
        info = footerapps.resolve_app(app_id)
        if info is None:
            self._toast(_("{app} is not installed").format(app=app_id))
            return
        if not footerapps.launch_app_file(info, os.path.join(str(root), path)):
            self._toast(_("Couldn't open {path} with {app}").format(path=path, app=info.get_display_name()))

    def _run_pending_navigate(self) -> None:
        pending, self._pending_navigate = self._pending_navigate, None
        if pending is None:
            return
        path, side = pending
        if side == self._loaded:
            self._navigate(path)

    def _navigate(self, path: str) -> None:
        """Show *path*'s section alone in the view and reveal it
        (synchronous); a miss is a toast. The section heading's click
        (_show_all) brings the whole stream back."""
        if self._closing or not self._opened:
            return
        self._show_diff_column()
        if not self._diffview.solo(path) or not self._diffview.reveal(path):
            self._toast(_("{path} isn't in this diff").format(path=path))
            return
        self._sync_search_label()

    def _show_all(self) -> None:
        """The live section heading clicked: every file of the load again
        (the files filter's word still applies)."""
        if self._closing or not self._opened:
            return
        self._show_diff_column()
        self._diffview.solo(None)
        self._sync_search_label()

    def _on_mutated(self, _sidebar: GitSidebar) -> None:
        """A mutation landed (stage all, a commit, one of the view's
        plans): re-seed the freshness signatures so the tick doesn't reload
        a second time, refresh the lists (the stack first: a commit moved
        HEAD), and reload the view now."""
        cwd = self._cwd_provider()
        self._signature = gitinfo.tree_signature(cwd, self._parent_name)
        self._refs_signature = gitinfo.refs_signature(cwd)
        self._refresh_branch_stack()
        if self._opened:
            self._read_diff(self._loaded)

    # -- the in-progress bar --

    def _on_continue_requested(self) -> None:
        """The bar's Continue: `git <kind> --continue` with no editor
        (gitops.continue_operation) on the sidebar's mutation thread, the
        toast saying whether the operation finished or stopped again, and
        the tree treated as moved (`mutated`) either way — a refused
        continue ("unmerged files") changes nothing, but the words are
        git's and the reload is cheap."""
        operation = self.operation_bar.operation
        if operation is None:
            return
        self._run_operation(operation, abort=False)

    def _on_abort_requested(self) -> None:
        """The bar's Abort…: ask first — the resolutions made since the
        stop are lost — then `git <kind> --abort` the same way."""
        operation = self.operation_bar.operation
        if operation is None:
            return
        cwd = self._cwd_provider() or "?"
        dialogs.confirm_dialog(
            self,
            gitmodel.abort_heading(operation.label),
            gitmodel.abort_body(operation.kind, operation.label, str(cwd)),
            _("Abort"),
            lambda: self._run_operation(operation, abort=True),
        )

    def _run_operation(self, operation: gitops.InProgress, abort: bool) -> None:
        """Run the continue or abort of *operation* behind the sidebar's
        busy (the bar's buttons greyed), toast the outcome and reload."""
        if self._closing or not self._opened:
            return
        if self.operation_bar.operation != operation:
            self._toast(_("The view changed since the request: nothing was done"))
            return
        cwd = self._cwd_provider()
        kind = operation.kind

        def work() -> tuple:
            # Never None: run_mutation drops a None answer without calling
            # done, and the bar would stay greyed. gitops never raises
            # today; the catch keeps that a toast rather than a stuck bar.
            try:
                if abort:
                    result = gitops.abort_operation(cwd, kind)
                else:
                    result = gitops.continue_operation(cwd, kind)
                still = gitops.in_progress(gitinfo.git_dir(cwd)) if result.ok else None
            except Exception as err:  # the worker's last line of defence
                return gitops.GitResult(False, "", str(err) or err.__class__.__name__), None
            return result, still

        def done(answer: object) -> None:
            self.operation_bar.set_busy(False)
            if not isinstance(answer, tuple):
                self._toast(_("git failed"))
                return
            result, still = answer
            if not result.ok:
                self._toast(gitmodel.operation_failed(kind, gitops.first_line(result.stderr), abort))
            elif abort:
                self._toast(gitmodel.abort_done(operation.label))
            else:
                self._toast(gitmodel.continue_done(operation.label, still.label if still else None))
            self.sidebar.emit("mutated")

        self.operation_bar.set_busy(True)
        if not self.sidebar.run_mutation(work, done):
            self.operation_bar.set_busy(False)

    def _toast(self, text: str) -> None:
        toast = Adw.Toast(title=text, timeout=4)
        toast.set_use_markup(False)
        self._toast_overlay.add_toast(toast)

    # -- what the view says --

    def _on_current_changed(self, _view: DiffView, path: str, hunk: int) -> None:
        """The view's current file / hunk moved (a scroll, a key): the
        files list's highlight follows."""
        self.sidebar.set_selection(path or None, hunk if hunk >= 0 else None)

    def _on_open_requested(self, _view: DiffView, path: str, line: int) -> None:
        """`e`: the file under the cursor in the session's editor, at the
        cursor's line (1-based; 0 = no cursor), through the window's
        open-in-editor action — the same door the terminal's file links
        use. The path is the diff's, relative to the repository root."""
        root = self._repo_root
        if root is None or not gitops.safe_path(path):
            return
        full = os.path.join(str(root), path)
        self.activate_action("win.open-in-editor", GLib.Variant("(sii)", (full, max(0, int(line)), 0)))

    def _on_filter_changed(self, text: str) -> None:
        self._diffview.filter(text)
        self._sync_search_label()

    # -- the view's mutations (stage, unstage, discard, revert) --

    def _on_mutation_requested(self, _view: DiffView, request: gitpatch.MutationRequest) -> None:
        """A header button, its menu, `x` / `X` / `D`, or the sidebar's
        *Revert file* (relayed through DiffView.request_file_at): re-read
        the file's patch from git now (gitops.file_patch — the planners
        compare the view against the disk before trusting a line) on a
        thread; then plan (_mutation_planned). The view's buttons wait
        meanwhile, the pressed one spinning."""
        if self._closing or not self._opened:
            return
        if self.sidebar.busy or self._diffview.busy():
            self._toast(_("Another git operation is still running"))
            return
        if request.load != self._loaded:
            self._toast(_("The view is behind the page: reloading"))
            self._read_diff(self._loaded)
            return
        self._diffview.set_busy(True)
        gen = self._gen
        cwd = self._cwd_provider()
        parent_target = self._parent_target
        file = request.file

        def work() -> None:
            fresh = (
                gitops.file_patch(cwd, request.load, file.path, file.previous_path, parent_target)
                if request.needs_patch
                else None
            )
            GLib.idle_add(self._mutation_planned, gen, cwd, request, fresh, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-page-plan", daemon=True).start()

    def _mutation_planned(
        self, gen: int, cwd: str, request: gitpatch.MutationRequest, fresh: str | None
    ) -> bool:
        # The view stays busy — the pressed button spinning — for the
        # request's whole life: set_busy(False) forgets which button it
        # was, so it is called only where the request ends.
        if gen != self._gen or self._closing or not self._opened:
            self._diffview.set_busy(False)
            return GLib.SOURCE_REMOVE
        plan = request.plan(fresh)
        if isinstance(plan, gitpatch.Refusal):
            self._diffview.set_busy(False)
            self._toast(plan.reason)
            if plan.stale:
                self._read_diff(self._loaded)
            return GLib.SOURCE_REMOVE
        if plan.confirm is None:
            self._run_plan(gen, cwd, request, plan)
            return GLib.SOURCE_REMOVE
        heading, button = request.confirm_words(plan)
        # The spinner stays on the pressed button while the question is up.
        dialogs.confirm_dialog(
            self,
            heading,
            plan.confirm,
            button,
            lambda: self._run_plan(gen, cwd, request, plan),
            on_dismiss=lambda: self._diffview.set_busy(False),
        )
        return GLib.SOURCE_REMOVE

    def _run_plan(self, gen: int, cwd: str, request: gitpatch.MutationRequest, plan: gitpatch.Plan) -> None:
        """Carry the plan out (gitops.run_plan) on a thread behind the
        sidebar's busy — the acting button spinning, every other button
        insensitive — then toast the outcome and treat the tree as moved
        (`mutated`: signatures re-seeded, the lists refreshed, the view
        reloaded by key, so a selection survives with its hunk). The plan
        was read against *cwd* under generation *gen* for the load the
        request names: a confirm can sit open while the page moves on (the
        agent `cd`s elsewhere, the sidebar loads another commit), and the
        plan then runs against nothing — the tree it described is not the
        one the page shows."""
        if self._closing or not self._opened:
            self._diffview.set_busy(False)
            return
        if gen != self._gen or request.load != self._loaded:
            self._diffview.set_busy(False)
            self._toast(_("The view changed since the request: nothing was done"))
            return
        three_way = request.three_way(plan)
        self._diffview.set_busy(True)

        def work() -> gitops.ApplyResult:
            return gitops.run_plan(cwd, plan, three_way=three_way, trash=_trash_paths)

        def done(answer: object) -> None:
            self._diffview.set_busy(False)
            if not isinstance(answer, gitops.ApplyResult):
                self._toast(_("git failed"))
                return
            self._toast(
                gitpatch.outcome_words(plan, answer.ok, answer.three_way, answer.conflicts, answer.stderr)
            )
            if answer.ok or answer.conflicts:
                self.sidebar.emit("mutated")

        if not self.sidebar.run_mutation(work, done):
            self._diffview.set_busy(False)

    def _scheme(self):
        """The GtkSource scheme the page's buffers wear: the editor's
        setting, resolved against the app's light/dark."""
        return style_scheme(self._scheme_setting, Adw.StyleManager.get_default().get_dark())

    def _apply_scheme(self) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        scheme = style_scheme(self._scheme_setting, dark)
        self._diffview.set_scheme(scheme, dark)
        self.commit_card.set_scheme(scheme)

    # -- the find bar --

    def _toggle_find(self) -> None:
        if self._search_bar.get_search_mode():
            self._search_bar.set_search_mode(False)
        else:
            self._search_bar.set_search_mode(True)
            self._search_entry.grab_focus()

    def _on_search_mode_changed(self, bar: Gtk.SearchBar, _pspec) -> None:
        if bar.get_search_mode():
            return
        # Closing: the highlights go, the keyboard lands on the hunk that
        # held the current match (else wherever the view puts it).
        if not self._diffview.focus_search_match():
            self._diffview.grab_focus()
        self._diffview.search_clear()
        self._search_label.set_text("")

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._show_search_position(self._diffview.search(entry.get_text()))

    def _search_step(self, forward: bool) -> None:
        self._show_search_position(self._diffview.search_step(forward))

    def _sync_search_label(self) -> None:
        """After a reload, a filter or a layout change re-counted the
        matches: the label follows (the view keeps its place)."""
        if self._search_bar.get_search_mode():
            self._show_search_position(self._diffview.search_position())

    def _show_search_position(self, position: tuple[int, int]) -> None:
        current, total = position
        if not self._search_entry.get_text():
            self._search_label.set_text("")
        elif total == 0:
            self._search_label.set_text(_("No matches"))
        else:
            self._search_label.set_text(_("{n} of {m}").format(n=current, m=total))

    # -- the keys and the menu --

    def _build_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        layout = Gio.Menu()
        for value, label in prefslayout.GIT_LAYOUTS:
            item = Gio.MenuItem.new(_(label), None)
            item.set_action_and_target_value(f"{_ACTIONS}.layout", GLib.Variant("s", value))
            layout.append_item(item)
        menu.append_section(_("Layout"), layout)
        view = Gio.Menu()
        view.append(_("Line numbers"), f"{_ACTIONS}.line-numbers")
        view.append(_("Wrap long lines"), f"{_ACTIONS}.wrap")
        view.append(_("Agent notes"), f"{_ACTIONS}.agent-notes")
        menu.append_section(None, view)
        more = Gio.Menu()
        more.append(_("Reload the diff"), f"{_ACTIONS}.refresh")
        more.append(_("Keyboard shortcuts"), f"{_ACTIONS}.help")
        menu.append_section(None, more)
        return menu

    def _install_actions(self) -> None:
        """The `git.*` actions the view's chords (keybindings.GROUP_GIT) and
        the header menu fire. The three stateful ones mirror the settings
        (layout, line numbers, wrap) and write them back through
        win.git-option."""
        group = Gio.SimpleActionGroup()
        plain: dict[str, Callable[[], object]] = {
            "next-hunk": lambda: self._diffview.focus_hunk(1),
            "prev-hunk": lambda: self._diffview.focus_hunk(-1),
            "next-file": lambda: self._diffview.focus_file(1),
            "prev-file": lambda: self._diffview.focus_file(-1),
            "cursor-down": lambda: self._diffview.step_cursor(1),
            "cursor-up": lambda: self._diffview.step_cursor(-1),
            "next-note": lambda: self._diffview.focus_annotated(1),
            "prev-note": lambda: self._diffview.focus_annotated(-1),
            "expand-gap": self._diffview.expand_gap_before_focus,
            "stage": self._diffview.request_stage,
            "stage-file": self._diffview.request_stage_file,
            "discard": self._diffview.request_discard,
            "add-note": self._diffview.add_note_at_cursor,
            "edit-note": self._diffview.edit_first_note,
            "layout-auto": lambda: self._write_option("git_layout", "auto"),
            "layout-split": lambda: self._write_option("git_layout", "split"),
            "layout-stack": lambda: self._write_option("git_layout", "stack"),
            "refresh": self.refresh,
            "filter": self._focus_filter,
            "find": self._toggle_find,
            "help": lambda: self.activate_action(
                "win.keyboard-bindings-group", GLib.Variant("s", keybindings.GROUP_GIT)
            ),
            "open-editor": self._diffview.request_open,
            "close": lambda: self.activate_action("win.toggle-git", None),
        }
        for name, handler in plain.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, run=handler: run())
            group.add_action(action)
        self._layout_action = Gio.SimpleAction.new_stateful(
            "layout", GLib.VariantType.new("s"), GLib.Variant("s", self._options.layout)
        )
        self._layout_action.connect(
            "change-state", lambda _a, value: self._write_option("git_layout", value.get_string())
        )
        group.add_action(self._layout_action)
        self._numbers_action = Gio.SimpleAction.new_stateful(
            "line-numbers", None, GLib.Variant("b", self._options.line_numbers)
        )
        self._numbers_action.connect(
            "change-state", lambda _a, value: self._write_option("git_line_numbers", value.get_boolean())
        )
        group.add_action(self._numbers_action)
        self._wrap_action = Gio.SimpleAction.new_stateful("wrap", None, GLib.Variant("b", self._options.wrap))
        self._wrap_action.connect(
            "change-state", lambda _a, value: self._write_option("git_wrap_lines", value.get_boolean())
        )
        group.add_action(self._wrap_action)
        # The agent's note cards shown or folded (`a`, the menu's check):
        # the page's for the tab's life, no setting behind it.
        self._agent_notes_action = Gio.SimpleAction.new_stateful("agent-notes", None, GLib.Variant("b", True))
        self._agent_notes_action.connect("change-state", self._on_agent_notes_state)
        group.add_action(self._agent_notes_action)
        self._git_actions = group
        self.insert_action_group(_ACTIONS, group)

    def _on_agent_notes_state(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        self._diffview.set_agent_notes_shown(value.get_boolean())

    def _on_note_editing_changed(self, _view: DiffView, editing: bool) -> None:
        """A note editor opened or closed: every `git.*` action goes
        insensitive meanwhile — a disabled named action lets its chord
        fall through, so `e` types an e and `q` a q into the editor
        instead of closing the page."""
        for name in self._git_actions.list_actions():
            action = self._git_actions.lookup_action(name)
            if action is not None:
                action.set_enabled(not editing)

    def _sync_action_states(self) -> None:
        """The stateful actions follow the settings (apply_settings), so
        the menu's checks and radios say what the view does."""
        options = self._options
        if self._layout_action.get_state().get_string() != options.layout:
            self._layout_action.set_state(GLib.Variant("s", options.layout))
        if self._numbers_action.get_state().get_boolean() != options.line_numbers:
            self._numbers_action.set_state(GLib.Variant("b", options.line_numbers))
        if self._wrap_action.get_state().get_boolean() != options.wrap:
            self._wrap_action.set_state(GLib.Variant("b", options.wrap))

    def _write_option(self, key: str, value: object) -> None:
        """A key or menu item changed a setting: persist it through the
        window (win.git-option → AppState + apply_preferences, so every
        page follows); with no window action to reach — a page in a bare
        test window — apply it to this page alone."""
        if key not in _KEY_SETTINGS:
            return
        variant = GLib.Variant("s", value) if isinstance(value, str) else GLib.Variant("b", bool(value))
        if self.activate_action("win.git-option", GLib.Variant("(sv)", (key, variant))):
            return
        self.apply_settings({**self._settings, key: value})

    def _focus_filter(self) -> None:
        """`/`: the files filter, when the sidebar is on screen — a narrow
        page brings the panels up for it."""
        self.show_panels(True)
        if self.sidebar.get_visible():
            self.sidebar.focus_filter()

    def _on_filter_escaped(self) -> None:
        """Escape in the files filter: the keyboard back in the view — on
        a narrow page that is the diff column again."""
        self._show_diff_column()
        self._diffview.grab_focus()


def _trash_paths(root: str, paths: Sequence[str]) -> gitops.GitResult:
    """gitops.run_plan's mover for OP_TRASH: each path under *root* to the
    system trash through Gio (never an unlink — the file exists nowhere
    else). Worker thread; the first failure is the answer."""
    for path in paths:
        try:
            Gio.File.new_for_path(os.path.join(root, path)).trash(None)
        except GLib.Error as exc:
            return gitops.GitResult(False, "", exc.message or _("trash failed"))
    return gitops.GitResult(True, "", "")


def _file_summaries(files: Sequence[diffmodel.File]) -> tuple[gitmodel.FileSummary, ...]:
    """The files list's rows from a read: one gitmodel.FileSummary per
    file (gitmodel.files_sections) — a binary file lists 0/0/0, which is
    how the row reads `bin`."""
    return tuple(
        gitmodel.FileSummary(
            str(index),
            file.path,
            file.previous_path if file.previous_path and file.previous_path != file.path else None,
            file.additions,
            file.deletions,
            len(file.hunks),
        )
        for index, file in enumerate(files)
    )
