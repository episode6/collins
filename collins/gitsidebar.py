# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page's native sidebar: the commits list over the files list, and
the action row under them.

What the collins-git hunk extension drew inside the terminal — the commits
panel and the files panel — as GTK widgets beside hunk's VTE (gitpage
places the widget; this module never imports gitpage). The commits list is
one group per branch of interest (gitmodel.build_rows: the current branch
with its `working tree` row and `↑` unpushed marks, the parent branch when
it isn't the default, the default branch's latest page with `load more…`),
the loaded row marked `▸` after hunk's own title (set_context, not the last
click — a load made from a shell or by the agent is reflected). The files
list is hunk's own `files[]` off `session get` (refresh_files) — the loaded
changeset in review order, with counts and rename pairs — split into
UNSTAGED / STAGED on the working tree, the side hunk has loaded live and
the other read off `git status` (gitmodel.files_sections); the row hunk's
cursor is on is highlighted (set_selection — the sidecar's `selection`
first, the `session get` snapshot as the fallback). The action row feeds
the extension's keys through the VTE's pty for what needs hunk's cursor
("key-requested": stage the hunk or the anchored range, anchor a line,
discard) and runs the rest natively on worker threads — stage all, unstage
all, commit, commit with body, fix up, the parent picker — with the
confirms and dialogs of dialogs.py, and a toast for every outcome.

Nothing here decides what a click loads, which row is the loaded one, or
what the confirms say: that is gitmodel's (GTK-free, unit-tested), and
every git call is gitops'. The widget only draws, threads and emits:
"load-requested" (a hunkctl.Loaded), "navigate-requested" (a path and the
side it sits on), "key-requested" (bytes for the pty), "mutated" (a native
git mutation landed — the page re-seeds its freshness signature and
reloads hunk), "parent-picked" (a branch name, or None for Automatic). Every
thread reply lands with GLib.idle_add at default priority behind a
generation counter, so a stale reply never overwrites a newer one; every
subject, path and branch name goes through Gtk.Label.set_text, bounded by
gitmodel first (foreign content).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import dialogs, gitinfo, gitmodel, gitops, hunkctl  # noqa: E402
from .gitmodel import BranchRef, FileRow, FileSections, Row  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

# The sidebar's own minimum: the commits list needs room for `↑ a1b2c3d`
# and a few words of subject beside it. The page's paned starts it a
# little wider (gitpage).
WIDTH_REQUEST = 220
# The action group the context menu and the Commit menu's items act on,
# inserted on the widget under this prefix.
_ACTIONS = "gitsb"
# The status letter's colour, as a CSS class on the code label (an entry
# of None is the default text colour).
_CODE_CLASSES: dict[str, str | None] = {
    "A": "success",
    "D": "error",
    "R": "accent",
    "C": "accent",
    "?": "dim-label",
    "U": "warning",
    "M": None,
    "T": None,
}
# The mark on the loaded commits row.
_LOADED_MARK = "▸"
_UNPUSHED_MARK = "↑"
_BRANCH_GLYPH = "⎇"
# How many commits the Fix up picker lists at most.
_FIXUP_LIMIT = 200
# The toast lifetimes: an outcome is glanced at; a refusal is read.
_TOAST_SECONDS = 4
_REFUSAL_TOAST_SECONDS = 6


def _restore_scroll(adjustment: Gtk.Adjustment, value: float) -> bool:
    """Put a rebuilt list back where it was scrolled to (as far as the new
    content reaches) — cosmetic, from an idle, never asserted on."""
    adjustment.set_value(min(value, max(0.0, adjustment.get_upper() - adjustment.get_page_size())))
    return GLib.SOURCE_REMOVE


class _CommitRow(Gtk.ListBoxRow):
    """One line of the commits list, drawn from a gitmodel.Row: a mark
    column (`▸` while loaded), the `↑` for an unpushed commit, the
    abbreviated sha in monospace, and the subject (or the branch name on a
    header, with `⎇` in front)."""

    def __init__(self, row: Row) -> None:
        super().__init__()
        self.row = row
        self.set_activatable(row.kind != "header" or row.load is not None)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.add_css_class("git-commit-row")
        self._mark = Gtk.Label(width_chars=1, xalign=0.5)
        self._mark.add_css_class("git-row-mark")
        box.append(self._mark)
        if row.kind == "header":
            label = Gtk.Label(xalign=0, hexpand=True)
            label.set_text(f"{_BRANCH_GLYPH} {row.label}")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("git-group-header")
            box.append(label)
        elif row.kind == "commit":
            up = Gtk.Label(width_chars=1, xalign=0.5)
            up.set_text(_UNPUSHED_MARK if row.unpushed else "")
            up.add_css_class("git-unpushed")
            box.append(up)
            abbrev = Gtk.Label(xalign=0)
            abbrev.set_text(row.abbrev or "")
            abbrev.add_css_class("git-commit-abbrev")
            abbrev.add_css_class("dim-label")
            box.append(abbrev)
            subject = Gtk.Label(xalign=0, hexpand=True)
            subject.set_text(row.label)
            subject.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(subject)
        else:
            label = Gtk.Label(xalign=0, hexpand=True)
            label.set_text(row.label)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            if row.kind == "more":
                label.add_css_class("dim-label")
            box.append(label)
        self.set_child(box)

    def set_loaded(self, loaded: bool) -> None:
        self._mark.set_text(_LOADED_MARK if loaded else "")
        if loaded:
            self.add_css_class("git-row-loaded")
        else:
            self.remove_css_class("git-row-loaded")

    def set_group_loaded(self, loaded: bool) -> None:
        if loaded:
            self.add_css_class("git-group-loaded")
        else:
            self.remove_css_class("git-group-loaded")


class _SectionRow(Gtk.ListBoxRow):
    """A files-list section heading — `UNSTAGED · 3` — in the house style
    (caption-heading + dim-label, never selected). Activatable only when a
    click means something: the working tree's other side, which a click
    loads."""

    def __init__(self, title: str, count: int, side: str, activatable: bool) -> None:
        super().__init__(selectable=False)
        self.side = side
        self.set_activatable(activatable)
        self.add_css_class("git-section")
        label = Gtk.Label(xalign=0, hexpand=True)
        label.set_text(f"{title} · {count}")
        label.add_css_class("caption-heading")
        label.add_css_class("dim-label")
        self.set_child(label)


class _FileRow(Gtk.ListBoxRow):
    """One file: its status letter (coloured), the path (`old → new` for a
    rename), and the counts hunk reported (`+a −d`, or `bin`) for a live
    row."""

    def __init__(self, file: FileRow, side: str) -> None:
        super().__init__()
        self.file = file
        self.side = side
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.add_css_class("git-file-row")
        code = Gtk.Label(width_chars=1, xalign=0.5)
        code.set_text(file.code or "")
        code.add_css_class("git-file-code")
        css = _CODE_CLASSES.get(file.code or "", None)
        if css:
            code.add_css_class(css)
        box.append(code)
        path = Gtk.Label(xalign=0, hexpand=True)
        if file.previous_path and file.previous_path != file.path:
            path.set_text(f"{file.previous_path} → {file.path}")
        else:
            path.set_text(file.path)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path.set_tooltip_text(file.path)
        box.append(path)
        counts = Gtk.Label(xalign=1)
        counts.add_css_class("dim-label")
        counts.add_css_class("git-file-counts")
        if file.binary:
            counts.set_text(_("bin"))
        elif file.live and file.additions is not None and file.deletions is not None:
            counts.set_text(f"+{file.additions} −{file.deletions}")
        else:
            counts.set_text("")
        box.append(counts)
        self.set_child(box)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.add_css_class("git-file-selected")
        else:
            self.remove_css_class("git-file-selected")


class GitSidebar(Gtk.Box):
    """The native commits and files panels beside hunk, with the action
    row. Built once per git page; fed by set_context / refresh_commits /
    refresh_files / set_selection / set_anchor; heard through its signals
    (see the module docstring)."""

    __gsignals__ = {
        "load-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "navigate-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "key-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "mutated": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "parent-picked": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, cwd_provider: Callable[[], str | None], options: hunkctl.Options) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("git-sidebar")
        self.set_size_request(WIDTH_REQUEST, -1)
        self._cwd_provider = cwd_provider
        self._options = options

        # -- context (set_context) -------------------------------------------
        self._branch: str | None = None
        self._parent: BranchRef | None = None
        self._default: BranchRef | None = None
        self._loaded: object = None  # a hunkctl.Loaded, or None for a foreign load
        self._resolved_sha: str | None = None
        self._live_side: str | None = None
        self._hunk_alive = False
        self._extension_loaded = False
        self._auto_parent: str | None = None

        # -- the commits list ---------------------------------------------------
        self._pages: dict[str, int] = dict.fromkeys(gitmodel.GROUPS, 1)
        self._commits_gen = 0
        self._rows: list[Row] = []
        self._commit_widgets: dict[str, _CommitRow] = {}
        self._loaded_row_id: str | None = None

        # -- the files list ------------------------------------------------------
        self._session_files: tuple[hunkctl.SessionFile, ...] = ()
        self._files_loaded: object = None
        self._untracked = options.untracked
        self._files_gen = 0
        self._sections = FileSections(mode="flat")
        self._file_widgets: dict[tuple[str, str], _FileRow] = {}
        self._selected_path: str | None = None
        self._selected_hunk: int | None = None

        # -- the action row --------------------------------------------------------
        self._anchor: hunkctl.Anchor | None = None
        # A native mutation (stage all, a commit) in flight: the other
        # mutations wait, and the Commit button spins.
        self._busy = False
        self._mutation_gen = 0

        self._build()
        self._install_actions()
        self._sync_buttons()

    # -- widgets -------------------------------------------------------------------

    def _build(self) -> None:
        self._commit_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._commit_list.add_css_class("navigation-sidebar")
        self._commit_list.add_css_class("git-commits")
        self._commit_list.connect("row-activated", self._on_commit_row_activated)
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_commits_secondary_click)
        self._commit_list.add_controller(secondary)
        self._commit_scroller = Gtk.ScrolledWindow(child=self._commit_list, vexpand=True)
        self._commit_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._file_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._file_list.add_css_class("navigation-sidebar")
        self._file_list.add_css_class("git-files")
        self._file_list.connect("row-activated", self._on_file_row_activated)
        self._file_scroller = Gtk.ScrolledWindow(child=self._file_list, vexpand=True)
        self._file_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._paned.set_start_child(self._commit_scroller)
        self._paned.set_end_child(self._file_scroller)
        self._paned.set_resize_start_child(True)
        self._paned.set_resize_end_child(True)
        self._paned.set_shrink_start_child(False)
        self._paned.set_shrink_end_child(False)
        self._paned.set_position(240)
        self.append(self._paned)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # The action row wraps: seven controls never fit 220 px in one line,
        # and a FlowBox is the one GTK 4.10 container that reflows.
        actions = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=False,
            max_children_per_line=8,
            min_children_per_line=1,
            row_spacing=2,
            column_spacing=2,
        )
        actions.set_activate_on_single_click(False)
        actions.add_css_class("git-actions")
        self._actions = actions

        def add(widget: Gtk.Widget) -> None:
            child = Gtk.FlowBoxChild(child=widget, focusable=False)
            actions.append(child)
            self._action_children[widget] = child

        self._action_children: dict[Gtk.Widget, Gtk.FlowBoxChild] = {}
        self._stage_button = self._flat_button(
            _("Stage hunk"), lambda: self._feed(hunkctl.STAGE_KEY)
        )
        add(self._stage_button)
        self._anchor_button = self._flat_button(_("Anchor line"), self._on_anchor_clicked)
        add(self._anchor_button)
        self._discard_button = self._flat_button(_("Discard"), lambda: self._feed(hunkctl.DISCARD_KEY))
        add(self._discard_button)
        self._stage_all_button = self._flat_button(_("Stage all"), lambda: self._on_all_clicked(True))
        add(self._stage_all_button)
        self._unstage_all_button = self._flat_button(
            _("Unstage all"), lambda: self._on_all_clicked(False)
        )
        add(self._unstage_all_button)

        menu = Gio.Menu()
        menu.append(_("Commit…"), f"{_ACTIONS}.commit")
        menu.append(_("Commit with body…"), f"{_ACTIONS}.commit-body")
        menu.append(_("Fix up…"), f"{_ACTIONS}.fixup")
        self._commit_button = Gtk.MenuButton(menu_model=menu)
        self._commit_button.add_css_class("flat")
        self._commit_button.set_always_show_arrow(True)
        self._commit_stack = Gtk.Stack()
        self._commit_stack.add_named(Gtk.Label(label=_("Commit")), "label")
        self._commit_stack.add_named(Gtk.Spinner(spinning=True), "spinner")
        self._commit_button.set_child(self._commit_stack)
        add(self._commit_button)

        self._parent_button = self._flat_button(f"{_BRANCH_GLYPH} ?", self._on_parent_clicked)
        self._parent_button.set_tooltip_text(_("Set parent branch…"))
        add(self._parent_button)
        self.append(actions)

    @staticmethod
    def _flat_button(label: str, on_click: Callable[[], None]) -> Gtk.Button:
        button = Gtk.Button(label=label)
        button.add_css_class("flat")
        button.connect("clicked", lambda *_a: on_click())
        return button

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, handler in (
            ("commit", lambda: self._on_commit_clicked(False)),
            ("commit-body", lambda: self._on_commit_clicked(True)),
            ("fixup", self._on_fixup_clicked),
            ("set-parent", self._on_parent_clicked),
            ("reload", self.refresh_commits),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            group.add_action(action)
        copy = Gio.SimpleAction.new("copy-sha", GLib.VariantType.new("s"))
        copy.connect("activate", lambda _a, param: self._copy_text(param.get_string()))
        group.add_action(copy)
        self._actions_group = group
        self.insert_action_group(_ACTIONS, group)

    # -- public: context and content ------------------------------------------------

    @property
    def options(self) -> hunkctl.Options:
        """What Preferences → Git says, as last handed in (set_options)."""
        return self._options

    @property
    def busy(self) -> bool:
        """Whether a native mutation (stage all, unstage all, a commit) is
        in flight."""
        return self._busy

    @property
    def selected_path(self) -> str | None:
        """The path of the file hunk's cursor is on, as last told."""
        return self._selected_path

    def commit_rows(self) -> list[Row]:
        """The rows the commits list draws, top to bottom (for the e2e)."""
        return list(self._rows)

    def file_rows(self) -> FileSections:
        """What the files list draws (for the e2e)."""
        return self._sections

    def loaded_row_id(self) -> str | None:
        """The id of the row marked `▸`, or None."""
        return self._loaded_row_id

    def anchor_button_label(self) -> str:
        return self._anchor_button.get_label() or ""

    def stage_button_label(self) -> str:
        return self._stage_button.get_label() or ""

    def set_context(
        self,
        *,
        branch: str | None,
        parent: BranchRef | None,
        default: BranchRef | None,
        loaded: object,
        resolved_sha: str | None,
        live_side: str | None,
        hunk_alive: bool,
        extension_loaded: bool,
        auto_parent: str | None,
    ) -> bool:
        """What the page knows: the checked-out *branch*, the *parent* and
        *default* branches the groups are built on (None when the tree
        can't name one), what hunk has *loaded* (a hunkctl.Loaded, or None
        for a load Collins has no name for) and, for a commit load, the
        sha it *resolved* to; which working-tree side is live (None for
        any other load); whether hunk runs and with the extension; and
        the automatic parent's name, for the picker's first row. A change
        of branch, parent or default refreshes the commits list; the
        loaded mark and the buttons follow every call. Returns whether the
        groups changed (and so the list was re-read here) — the page
        refreshes it itself otherwise after a spawn."""
        groups_changed = (branch, parent, default) != (self._branch, self._parent, self._default)
        self._branch = branch
        self._parent = parent
        self._default = default
        self._loaded = loaded
        self._resolved_sha = resolved_sha
        self._live_side = live_side
        self._hunk_alive = hunk_alive
        self._extension_loaded = extension_loaded
        self._auto_parent = auto_parent
        name = parent.name if parent is not None else (auto_parent or "?")
        self._parent_button.set_label(f"{_BRANCH_GLYPH} {name}")
        if groups_changed:
            self._pages = dict.fromkeys(gitmodel.GROUPS, 1)
            self.refresh_commits()
        else:
            self._mark_loaded_row()
        self._sync_buttons()
        return groups_changed

    def refresh_commits(self) -> None:
        """Re-read the groups' pages and the `↑` marks on a thread and
        rebuild the commits list; a reply to an earlier ask is dropped."""
        self._commits_gen += 1
        gen = self._commits_gen
        cwd = self._cwd_provider()
        parent, default = self._parent, self._default
        pages = dict(self._pages)
        page_size = self._options.log_page

        def work() -> None:
            current_range = [f"{parent.target}..HEAD"] if parent is not None else ["HEAD"]
            current, current_more = gitops.read_page(cwd, current_range, page_size, pages["current"])
            parent_commits: list[gitmodel.Commit] = []
            parent_more = False
            if parent is not None and default is not None and parent.name != default.name:
                parent_commits, parent_more = gitops.read_page(
                    cwd, [f"{default.target}..{parent.target}"], page_size, pages["parent"]
                )
            default_commits: list[gitmodel.Commit] = []
            default_more = False
            if default is not None:
                default_commits, default_more = gitops.read_page(
                    cwd, [default.target], page_size, pages["default"]
                )
            unpushed = gitops.unpushed_shas(cwd)
            GLib.idle_add(
                self._commits_read,
                gen,
                (current, current_more, parent_commits, parent_more, default_commits, default_more, unpushed),
                priority=GLib.PRIORITY_DEFAULT,
            )

        threading.Thread(target=work, name="git-sidebar-log", daemon=True).start()

    def _commits_read(self, gen: int, read: tuple) -> bool:
        if gen != self._commits_gen:
            return GLib.SOURCE_REMOVE
        current, current_more, parent_commits, parent_more, default_commits, default_more, unpushed = read
        self._rows = gitmodel.build_rows(
            self._branch or "HEAD",
            self._parent,
            self._default,
            current,
            current_more,
            parent_commits,
            parent_more,
            default_commits,
            default_more,
            unpushed,
        )
        self._rebuild_commits()
        return GLib.SOURCE_REMOVE

    def refresh_files(
        self, session_files: Sequence[hunkctl.SessionFile], loaded: object, untracked: bool
    ) -> None:
        """Rebuild the files list from hunk's *files[]* for *loaded*: a
        working-tree load reads `git status` on a thread for the other
        side first (the `?` rows dropped when *untracked* is off); any
        other load is one flat list, drawn at once."""
        self._session_files = tuple(session_files)
        self._files_loaded = loaded
        self._untracked = untracked
        self._files_gen += 1
        gen = self._files_gen
        if loaded not in ("unstaged", "staged"):
            self._sections = gitmodel.files_sections(None, self._session_files, loaded, untracked)
            self._rebuild_files()
            return
        cwd = self._cwd_provider()

        def work() -> None:
            status = gitops.read_status(cwd)
            GLib.idle_add(self._status_read, gen, status, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-sidebar-status", daemon=True).start()

    def _status_read(self, gen: int, status: gitmodel.Status | None) -> bool:
        if gen != self._files_gen:
            return GLib.SOURCE_REMOVE
        self._sections = gitmodel.files_sections(
            status, self._session_files, self._files_loaded, self._untracked
        )
        self._rebuild_files()
        return GLib.SOURCE_REMOVE

    def set_selection(self, path: str | None, hunk: int | None, source: str) -> None:
        """Highlight the file hunk's cursor is on: *source* "sidecar" (the
        extension's word, instant) or "session" (`session get`'s snapshot,
        up to a tick late). The page arbitrates between the two before
        calling (GitPage._take_session); *source* names the caller's word
        for readers and is not kept. None clears it."""
        del source
        if path == self._selected_path and hunk == self._selected_hunk:
            return
        self._selected_path = path
        self._selected_hunk = hunk
        self._mark_selected_file()

    def set_anchor(self, anchor: hunkctl.Anchor | None) -> None:
        """The line `v` anchored, off the sidecar (None once cleared): the
        anchor button reads Clear anchor while one is set, and the stage
        button Stage lines."""
        self._anchor = anchor
        self._sync_buttons()

    def set_options(self, options: hunkctl.Options) -> None:
        """Preferences → Git changed: a new page size re-pages the commits
        list from its first page; a flipped untracked switch redraws the
        files list."""
        old, self._options = self._options, options
        if options.log_page != old.log_page:
            self._pages = dict.fromkeys(gitmodel.GROUPS, 1)
            self.refresh_commits()
        if options.untracked != old.untracked:
            self.refresh_files(self._session_files, self._files_loaded, options.untracked)

    def load_more(self, group: str) -> None:
        """One more page for *group* (one of gitmodel.GROUPS)."""
        if group not in self._pages:
            return
        self._pages[group] += 1
        self.refresh_commits()

    # -- public: the e2e's way of clicking ----------------------------------------------

    def click_commit_row(self, row_id: str) -> bool:
        """Activate the commits row with *row_id* as a click would; False
        when no such row is drawn."""
        widget = self._commit_widgets.get(row_id)
        if widget is None:
            return False
        self._on_commit_row_activated(self._commit_list, widget)
        return True

    def click_file_row(self, path: str, side: str = "") -> bool:
        """Activate the files row for *path* on *side* ("unstaged" |
        "staged" | "" for the flat list) as a click would."""
        widget = self._file_widgets.get((side, path))
        if widget is None:
            return False
        self._on_file_row_activated(self._file_list, widget)
        return True

    def click_section(self, side: str) -> bool:
        """Activate the section heading of *side* as a click would."""
        row = self._file_list.get_row_at_index(0)
        while row is not None:
            if isinstance(row, _SectionRow) and row.side == side:
                self._on_file_row_activated(self._file_list, row)
                return True
            row = row.get_next_sibling()
        return False

    # -- public: the native mutations ---------------------------------------------------

    def commit(self, summary: str, body: str | None = None) -> None:
        """`git commit -q -m <summary> [-m <body>]` on a thread; the toast
        names the commit (or git's first error line) and "mutated" fires
        either way — hooks may have moved the tree. Public so the e2e
        drives it without the dialog."""
        cwd = self._cwd_provider()

        def work() -> tuple:
            result = gitops.commit(cwd, summary, body)
            abbrev = gitops.head_abbrev(cwd) if result.ok else None
            return result, abbrev

        def done(read: tuple) -> None:
            result, abbrev = read
            if result.ok:
                self._toast(
                    _("Committed {sha} “{summary}” — undo with `git reset --soft HEAD~1`").format(
                        sha=abbrev or "?", summary=summary
                    )
                )
            else:
                self._toast(gitops.first_line(result.stderr) or _("git commit failed"), refusal=True)
            self.emit("mutated")

        self._run_mutation(work, done)

    def fixup(self, sha: str) -> None:
        """`git commit -q -m "fixup! <sha>"` on a thread; the toast names
        the rebase that folds it in."""
        cwd = self._cwd_provider()

        def work() -> tuple:
            result = gitops.commit_fixup(cwd, sha)
            is_root = gitops.is_root_commit(cwd, sha) if result.ok else False
            return result, is_root

        def done(read: tuple) -> None:
            result, is_root = read
            if result.ok:
                abbrev = hunkctl.short_ref(sha)
                self._toast(
                    _("Committed a fixup for {sha} — fold it in with `{command}`").format(
                        sha=abbrev, command=gitmodel.autosquash_command(abbrev, is_root)
                    )
                )
            else:
                self._toast(gitops.first_line(result.stderr) or _("git commit failed"), refusal=True)
            self.emit("mutated")

        self._run_mutation(work, done)

    def stage_all(self) -> None:
        """`git add -A` on a thread (the confirm is the button's, see
        _on_all_clicked); the toast counts what was staged."""
        self._all(True)

    def unstage_all(self) -> None:
        """`git reset -q` on a thread; the toast counts what was unstaged."""
        self._all(False)

    def _all(self, stage: bool) -> None:
        cwd = self._cwd_provider()

        def work() -> tuple:
            count, _heading = gitmodel.plan_all(gitops.read_status(cwd), stage)
            result = gitops.stage_all(cwd) if stage else gitops.unstage_all(cwd)
            return result, count

        def done(read: tuple) -> None:
            result, count = read
            if result.ok:
                self._toast(gitmodel.all_done(count, stage))
            else:
                self._toast(gitops.first_line(result.stderr) or _("git failed"), refusal=True)
            self.emit("mutated")

        self._run_mutation(work, done)

    def pick_parent(self, name: str | None) -> None:
        """The user's parent branch pick: *name*, or None for Automatic.
        Emits "parent-picked"; the page persists and re-resolves."""
        if name is not None and not hunkctl.safe_ref(name):
            return
        self.emit("parent-picked", name)

    def _run_mutation(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """*work* on a daemon thread while the widget reads busy, *done*
        with its answer back on the main loop — unless the widget was
        told to forget the ask meanwhile (a newer generation)."""
        if self._busy:
            self._toast(_("Another git operation is still running"), refusal=True)
            return
        self._busy = True
        self._mutation_gen += 1
        gen = self._mutation_gen
        self._sync_buttons()

        def run() -> None:
            try:
                answer = work()
            except Exception as err:  # a worker must never die silently
                log.warning("git sidebar: mutation failed: %s", err)
                answer = None
            GLib.idle_add(self._mutation_done, gen, done, answer, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=run, name="git-sidebar-mutation", daemon=True).start()

    def _mutation_done(self, gen: int, done: Callable[[object], None], answer: object) -> bool:
        if gen != self._mutation_gen:
            return GLib.SOURCE_REMOVE
        self._busy = False
        self._sync_buttons()
        if answer is not None:
            done(answer)
        return GLib.SOURCE_REMOVE

    # -- the commits list --------------------------------------------------------------

    def _rebuild_commits(self) -> None:
        adjustment = self._commit_scroller.get_vadjustment()
        value = adjustment.get_value()
        self._commit_list.remove_all()
        self._commit_widgets = {}
        for row in self._rows:
            widget = _CommitRow(row)
            self._commit_list.append(widget)
            self._commit_widgets[row.id] = widget
        self._mark_loaded_row()
        if value > 0:
            GLib.idle_add(_restore_scroll, adjustment, value)

    def _mark_loaded_row(self) -> None:
        self._loaded_row_id = gitmodel.loaded_row_id(self._rows, self._loaded, self._resolved_sha)
        loaded_group = None
        for row in self._rows:
            if row.id == self._loaded_row_id:
                loaded_group = row.group
        for row_id, widget in self._commit_widgets.items():
            widget.set_loaded(row_id == self._loaded_row_id)
            widget.set_group_loaded(widget.row.kind == "header" and widget.row.group == loaded_group)

    def _on_commit_row_activated(self, _list: Gtk.ListBox, widget: Gtk.ListBoxRow) -> None:
        if not isinstance(widget, _CommitRow):
            return
        row = widget.row
        if row.kind == "more":
            self.load_more(row.group)
            return
        if row.load is not None:
            self.emit("load-requested", row.load)

    def _on_commits_secondary_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        row = self._commit_list.get_row_at_y(int(y))
        menu = Gio.Menu()
        if isinstance(row, _CommitRow) and row.row.kind == "commit" and row.row.sha:
            item = Gio.MenuItem.new(_("Copy sha"), None)
            item.set_action_and_target_value(f"{_ACTIONS}.copy-sha", GLib.Variant("s", row.row.sha))
            menu.append_item(item)
        menu.append(_("Set parent branch…"), f"{_ACTIONS}.set-parent")
        menu.append(_("Reload"), f"{_ACTIONS}.reload")
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self._commit_list)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def _copy_text(self, text: str) -> None:
        display = self.get_display()
        if display is not None:
            display.get_clipboard().set(text)

    # -- the files list ---------------------------------------------------------------------

    def _rebuild_files(self) -> None:
        adjustment = self._file_scroller.get_vadjustment()
        value = adjustment.get_value()
        self._file_list.remove_all()
        self._file_widgets = {}
        sections = self._sections
        if sections.mode == "split":
            for side, title, rows in (
                ("unstaged", _("UNSTAGED"), sections.unstaged),
                ("staged", _("STAGED"), sections.staged),
            ):
                self._file_list.append(_SectionRow(title, len(rows), side, activatable=side != sections.live))
                for file in rows:
                    widget = _FileRow(file, side)
                    self._file_list.append(widget)
                    self._file_widgets[(side, file.path)] = widget
        else:
            self._file_list.append(_SectionRow(_("FILES"), len(sections.flat), "", activatable=False))
            for file in sections.flat:
                widget = _FileRow(file, "")
                self._file_list.append(widget)
                self._file_widgets[("", file.path)] = widget
        self._mark_selected_file()
        if value > 0:
            GLib.idle_add(_restore_scroll, adjustment, value)

    def _mark_selected_file(self) -> None:
        live = self._sections.live if self._sections.mode == "split" else ""
        for (side, path), widget in self._file_widgets.items():
            widget.set_selected(side == live and path == self._selected_path)

    def _on_file_row_activated(self, _list: Gtk.ListBox, widget: Gtk.ListBoxRow) -> None:
        if isinstance(widget, _SectionRow):
            if widget.side and widget.side != self._sections.live:
                self.emit("load-requested", widget.side)
            return
        if isinstance(widget, _FileRow):
            self.emit("navigate-requested", widget.file.path, widget.side)

    # -- the action row ----------------------------------------------------------------------

    def _working_live(self) -> bool:
        return self._live_side is not None

    def _sync_buttons(self) -> None:
        live = self._working_live()
        cursor_keys = live and self._hunk_alive and self._extension_loaded
        for button in (self._stage_button, self._anchor_button, self._discard_button):
            child = self._action_children.get(button)
            if child is not None:
                child.set_visible(self._extension_loaded)
            button.set_sensitive(cursor_keys)
        anchored = self._anchor is not None
        self._stage_button.set_label(_("Stage lines") if anchored else _("Stage hunk"))
        self._stage_button.set_tooltip_text(
            _("Stage the lines from the anchor to hunk's cursor (x)")
            if anchored
            else _("Stage the hunk under hunk's cursor — unstage it in the staged view (x)")
        )
        self._anchor_button.set_label(_("Clear anchor") if anchored else _("Anchor line"))
        self._anchor_button.set_tooltip_text(
            _("Clear the line-range anchor (Esc)")
            if anchored
            else _("Anchor a line range at hunk's cursor line (v)")
        )
        self._discard_button.set_tooltip_text(
            _("Discard the hunk under hunk's cursor, or the anchored range, after hunk's confirmation (D)")
        )
        for button in (self._stage_all_button, self._unstage_all_button):
            button.set_sensitive(live and not self._busy)
        self._commit_button.set_sensitive(live and not self._busy)
        self._commit_stack.set_visible_child_name("spinner" if self._busy else "label")
        self._stage_all_button.set_tooltip_text(_("Stage every change (git add -A)"))
        self._unstage_all_button.set_tooltip_text(_("Unstage every change (git reset)"))

    def _feed(self, key: bytes) -> None:
        if not (self._working_live() and self._hunk_alive and self._extension_loaded):
            return
        self.emit("key-requested", key)

    def _on_anchor_clicked(self) -> None:
        self._feed(hunkctl.CLEAR_ANCHOR_KEY if self._anchor is not None else hunkctl.ANCHOR_KEY)

    def _on_all_clicked(self, stage: bool) -> None:
        if not self._working_live() or self._busy:
            return
        cwd = self._cwd_provider()

        def work() -> None:
            status = gitops.read_status(cwd)
            GLib.idle_add(self._all_planned, stage, status, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-sidebar-plan", daemon=True).start()

    def _all_planned(self, stage: bool, status: gitmodel.Status | None) -> bool:
        count, heading = gitmodel.plan_all(status, stage)
        if count == 0:
            self._toast(_("Nothing to stage") if stage else _("Nothing to unstage"))
            return GLib.SOURCE_REMOVE
        dialogs.confirm_dialog(
            self,
            heading,
            _("`git add -A` in {cwd}").format(cwd=self._cwd_provider() or "?")
            if stage
            else _("`git reset` in {cwd}").format(cwd=self._cwd_provider() or "?"),
            _("Stage all") if stage else _("Unstage all"),
            lambda: self._all(stage),
            default_response="confirm",
            destructive=False,
        )
        return GLib.SOURCE_REMOVE

    def _commit_gate(self, then: Callable[[], None]) -> None:
        """Refuse a commit before asking anything of the user when one
        can't be made: a half-finished rebase / merge / cherry-pick /
        revert, or nothing staged. *then* runs on the main loop when the
        gate is passed."""
        cwd = self._cwd_provider()

        def work() -> None:
            operation = gitops.in_progress_operation(gitinfo.git_dir(cwd))
            staged = gitops.staged_paths(cwd) if operation is None else []
            GLib.idle_add(self._commit_gated, operation, staged, then, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-sidebar-gate", daemon=True).start()

    def _commit_gated(self, operation: str | None, staged: list[str], then: Callable[[], None]) -> bool:
        if operation is not None:
            self._toast(
                _("A {operation} is half-finished here — finish or abort it first").format(
                    operation=operation
                ),
                refusal=True,
            )
        elif not staged:
            self._toast(_("Nothing staged — stage a hunk or file first"), refusal=True)
        else:
            then()
        return GLib.SOURCE_REMOVE

    def _on_commit_clicked(self, with_body: bool) -> None:
        if not self._working_live() or self._busy:
            return

        def ask() -> None:
            dialogs.commit_dialog(
                self,
                _("Commit with body") if with_body else _("Commit"),
                with_body,
                self.commit,
            )

        self._commit_gate(ask)

    def _on_fixup_clicked(self) -> None:
        if not self._working_live() or self._busy:
            return
        cwd = self._cwd_provider()
        parent = self._parent

        def ask() -> None:
            def work() -> None:
                commits = gitops.unpushed_in_group(
                    cwd, parent.target if parent is not None else None, _FIXUP_LIMIT
                )
                GLib.idle_add(self._fixup_listed, commits, priority=GLib.PRIORITY_DEFAULT)

            threading.Thread(target=work, name="git-sidebar-fixup", daemon=True).start()

        self._commit_gate(ask)

    def _fixup_listed(self, commits: list[gitmodel.Commit]) -> bool:
        if not commits:
            self._toast(_("No unpushed commit to fix up — commit first"), refusal=True)
            return GLib.SOURCE_REMOVE
        cwd = self._cwd_provider()

        def picked(index: int) -> None:
            commit = commits[index]

            def work() -> None:
                is_root = gitops.is_root_commit(cwd, commit.sha)
                GLib.idle_add(confirm, is_root, priority=GLib.PRIORITY_DEFAULT)

            def confirm(is_root: bool) -> bool:
                command = gitmodel.autosquash_command(commit.abbrev, is_root)
                dialogs.confirm_dialog(
                    self,
                    _("Fix up {sha}?").format(sha=commit.abbrev),
                    _(
                        "The index is committed as `fixup! {sha}` for “{subject}”. Fold it in"
                        " afterwards with `{command}` — named here, never run."
                    ).format(sha=commit.abbrev, subject=commit.subject, command=command),
                    _("Commit fixup"),
                    lambda: self.fixup(commit.sha),
                    default_response="confirm",
                    destructive=False,
                )
                return GLib.SOURCE_REMOVE

            threading.Thread(target=work, name="git-sidebar-fixup-root", daemon=True).start()

        dialogs.choice_dialog(
            self,
            _("Fix up which commit?"),
            _("Unpushed commits of this branch, newest first."),
            gitmodel.fixup_options(commits),
            picked,
            _("Choose"),
        )
        return GLib.SOURCE_REMOVE

    def _on_parent_clicked(self) -> None:
        cwd = self._cwd_provider()

        def work() -> None:
            branches = gitops.local_branches(cwd)
            GLib.idle_add(self._branches_listed, branches, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-sidebar-branches", daemon=True).start()

    def _branches_listed(self, branches: list[str]) -> bool:
        auto = self._auto_parent or "?"
        options = [_("Automatic ({name})").format(name=auto), *branches]

        def picked(index: int) -> None:
            self.pick_parent(None if index == 0 else branches[index - 1])

        dialogs.choice_dialog(
            self,
            _("Set parent branch"),
            _("The branch this one is measured against: its diff, and the commits listed as its own."),
            options,
            picked,
            _("Set"),
        )
        return GLib.SOURCE_REMOVE

    # -- toasts ------------------------------------------------------------------------------

    def _toast(self, text: str, refusal: bool = False) -> None:
        overlay = self.get_ancestor(Adw.ToastOverlay)
        if overlay is None:
            log.info("git sidebar: %s", text)
            return
        toast = Adw.Toast(title=text, timeout=_REFUSAL_TOAST_SECONDS if refusal else _TOAST_SECONDS)
        # A toast title is Pango markup by default; these carry commit
        # summaries and git's stderr, which are nobody's markup.
        toast.set_use_markup(False)
        overlay.add_toast(toast)
