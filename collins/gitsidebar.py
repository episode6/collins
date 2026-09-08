# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page's native sidebar: the commits list over the files list, and
the action row under them.

The commits panel and the files panel beside the diff view (gitpage
places the widget; this module never imports gitpage). The commits list is
the `working tree` row over one group per branch of interest
(gitmodel.build_rows: the current branch with its `↑` unpushed marks —
left out when the default branch is checked out, whose group would
repeat it — every branch of the stack under it down to the default
branch — the page reads the stack off git and hands it in with
set_context — and the default branch's latest page with `load more…`),
the loaded row marked `▸` after what the page
has loaded (set_context, not the last click — a load made by the agent's
show_diff is reflected), and a caret on every branch header that folds
the group's rows away (collapse_group; which groups are folded is the
widget's for the page's life). The files list is the loaded diff's own files
(refresh_files, one gitmodel.FileSummary per file with counts and rename
pairs) split into UNSTAGED / STAGED on the working tree, the side the
page has loaded live and the other read off `git status` (gitmodel.
files_sections — the page hands in the status its own read carried); the
row the view is on is highlighted (set_selection, fed from the view's
`current-changed`). A files filter sits above the list: a Gtk.SearchEntry
whose word hides the rows here and, through "filter-changed", the
sections in the diff; Escape clears it and "filter-escaped" hands the
keyboard back to the view. The action row runs the whole-tree mutations
on worker threads — stage all, unstage all, commit, commit with body, fix
up — with the confirms and dialogs of dialogs.py, and a toast for every
outcome; the per-hunk and per-line buttons are the diff view's own, on
its headers.

Nothing here decides what a click loads, which row is the loaded one, or
what the confirms say: that is gitmodel's (GTK-free, unit-tested), and
every git call is gitops'. The widget only draws, threads and emits:
"load-requested" (a gitloads.Loaded), "navigate-requested" (a path and the
side it sits on), "revert-requested" (a path whose row's *Revert file*
was picked on a commit, the branch or a range — the page hands it to the
view's file button), "mutated" (a git mutation landed — the page re-seeds
its freshness signature and reloads the view). Every thread reply lands with
GLib.idle_add at default priority behind a generation counter, so a stale
reply never overwrites a newer one; every subject, path and branch name
goes through Gtk.Label.set_text, bounded by gitmodel first (foreign
content).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import PurePosixPath

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import dialogs, filetypes, gitinfo, gitloads, gitmodel, gitops, gitpatch  # noqa: E402
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
# The leading column every commits row shares: a header's caret or the
# other rows' mark cell, one width so the branch names and the `↑` column
# line up.
_LEAD_WIDTH = 16
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
    """One line of the commits list, drawn from a gitmodel.Row. Two
    columns lead every row: the first holds the caret that folds a branch
    header's group (or the `▸` mark on a loaded working-tree or commit
    row), the second the `↑` of an unpushed commit (blank, on the working
    tree row, so its label lines up with the commits' shas); then the
    abbreviated sha in monospace and the subject — or, on a header, the
    branch name with `⎇` in front, flush against the caret. The caret is
    its own button — a press on it never activates the row, so folding a
    branch does not load its diff — and *on_fold* hears the group id; a
    loaded header is tinted and bold, the caret standing where its mark
    would."""

    def __init__(self, row: Row, on_fold: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self.row = row
        self.set_activatable(row.kind != "header" or row.load is not None)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.add_css_class("git-commit-row")
        self._mark: Gtk.Label | None = None
        self._caret: Gtk.Button | None = None
        if row.kind == "header":
            caret = Gtk.Button(icon_name="pan-down-symbolic", focusable=False)
            caret.add_css_class("flat")
            caret.add_css_class("git-group-caret")
            caret.set_valign(Gtk.Align.CENTER)
            caret.set_size_request(_LEAD_WIDTH, -1)
            caret.set_tooltip_text(_("Fold or unfold the branch"))
            if on_fold is not None:
                caret.connect("clicked", lambda _b: on_fold(row.group))
            box.append(caret)
            self._caret = caret
        else:
            mark = Gtk.Label(width_chars=1, xalign=0.5)
            mark.add_css_class("git-row-mark")
            mark.set_size_request(_LEAD_WIDTH, -1)
            box.append(mark)
            self._mark = mark
        if row.kind == "header":
            label = Gtk.Label(xalign=0, hexpand=True)
            label.set_text(f"{_BRANCH_GLYPH} {row.label}")
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.add_css_class("git-group-header")
            box.append(label)
            self.set_tooltip_text(row.label)  # ellipsized: every twin's full name
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
            self.set_tooltip_text(row.label)  # the whole subject line
        else:
            # The working tree row and `load more…` sit in the commits'
            # column: a blank `↑` cell in front of the label.
            box.append(Gtk.Label(width_chars=1))
            label = Gtk.Label(xalign=0, hexpand=True)
            label.set_text(row.label)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            if row.kind == "more":
                label.add_css_class("dim-label")
            box.append(label)
        self.set_child(box)

    def set_loaded(self, loaded: bool) -> None:
        if self._mark is not None:
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

    def set_collapsed(self, collapsed: bool) -> None:
        """Turn a header's caret to say whether its group is folded."""
        if self._caret is not None:
            self._caret.set_icon_name("pan-end-symbolic" if collapsed else "pan-down-symbolic")


class _SectionRow(Gtk.ListBoxRow):
    """A files-list section heading — `UNSTAGED · 3` — in the house style
    (caption-heading + dim-label, never selected). A click on the live
    side's (or the flat list's) heading shows the whole stream again
    after a row's click soloed one file; the working tree's other side's
    heading loads that side."""

    def __init__(self, title: str, count: int, side: str) -> None:
        super().__init__(selectable=False)
        self.side = side
        self.set_activatable(True)
        self.add_css_class("git-section")
        label = Gtk.Label(xalign=0, hexpand=True)
        label.set_text(f"{title} · {count}")
        label.add_css_class("caption-heading")
        label.add_css_class("dim-label")
        self.set_child(label)


class _FileRow(Gtk.ListBoxRow):
    """One file: its status letter (coloured), the file-type icon the
    editor's tree shows for the name (filetypes.icon_for, with its colour
    class), the path (`old → new` for a rename), and the counts the diff
    reported (`+a −d`, or `bin`) for a live row."""

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
        icon_name, colour = filetypes.icon_for(PurePosixPath(file.path).name)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        if colour:
            icon.add_css_class(colour)
        box.append(icon)
        path = Gtk.Label(xalign=0, hexpand=True)
        if file.previous_path and file.previous_path != file.path:
            path.set_text(f"{file.previous_path} → {file.path}")
        else:
            path.set_text(file.path)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path.set_tooltip_text(file.path)
        box.append(path)
        # The counts in the diff view's colours (its file headers use the
        # same two classes): green for the added lines, red for the removed.
        counts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        counts.add_css_class("git-file-counts")
        if file.binary:
            binary = Gtk.Label(label=_("bin"))
            binary.add_css_class("dim-label")
            counts.append(binary)
        elif file.live and file.additions is not None and file.deletions is not None:
            added = Gtk.Label(label=f"+{file.additions}")
            added.add_css_class("pr-checks-passed")
            counts.append(added)
            removed = Gtk.Label(label=f"−{file.deletions}")
            removed.add_css_class("pr-checks-failed")
            counts.append(removed)
        box.append(counts)
        self.set_child(box)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.add_css_class("git-file-selected")
        else:
            self.remove_css_class("git-file-selected")


class GitSidebar(Gtk.Box):
    """The commits and files panels beside the diff view, with the action
    row. Built once per git page; fed by set_context / refresh_commits /
    refresh_files / set_selection; heard through its signals (see the
    module docstring)."""

    __gsignals__ = {
        "load-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "navigate-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        "show-all-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "revert-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "mutated": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The files filter's text changed (the view hides the sections
        # that don't match); Escape in the filter cleared it and wants the
        # keyboard back in the diff.
        "filter-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "filter-escaped": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, cwd_provider: Callable[[], str | None], options: gitloads.Options) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("git-sidebar")
        self.set_size_request(WIDTH_REQUEST, -1)
        self._cwd_provider = cwd_provider
        self._options = options

        # -- context (set_context) -------------------------------------------
        self._branch: str | None = None
        self._twins: tuple[str, ...] = ()
        self._parent: BranchRef | None = None
        self._default: BranchRef | None = None
        # The branches under the parent, nearest first (gitops.stack_branches
        # as the page read it), down to the last one above the default.
        self._stack: tuple[BranchRef, ...] = ()
        self._loaded: object = None  # a gitloads.Loaded, or None before the page said
        self._resolved_sha: str | None = None
        # Whether a working-tree side is loaded: what the action row's
        # buttons act on (stage all, commit).
        self._live = False

        # -- the commits list ---------------------------------------------------
        # How many pages each group shows, by group id (absent: one).
        self._pages: dict[str, int] = {}
        self._commits_gen = 0
        self._rows: list[Row] = []
        self._commit_widgets: dict[str, _CommitRow] = {}
        self._loaded_row_id: str | None = None
        # The groups whose commits are folded away under their header
        # (the caret), by group id; kept across re-reads.
        self._collapsed: set[str] = set()

        # -- the files list ------------------------------------------------------
        self._session_files: tuple[gitmodel.FileSummary, ...] = ()
        self._files_loaded: object = None
        self._untracked = options.untracked
        self._files_gen = 0
        self._sections = FileSections(mode="flat")
        self._file_widgets: dict[tuple[str, str], _FileRow] = {}
        self._selected_path: str | None = None
        self._selected_hunk: int | None = None
        # The filter entry's word (native viewer): a case-insensitive
        # substring of the path; rows that don't match hide.
        self._filter_text = ""

        # -- the action row --------------------------------------------------------
        # A mutation (stage all, a commit) in flight: the other mutations
        # wait, and the Commit button spins.
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
        files_secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        files_secondary.connect("pressed", self._on_files_secondary_click)
        self._file_list.add_controller(files_secondary)
        self._file_scroller = Gtk.ScrolledWindow(child=self._file_list, vexpand=True)
        self._file_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # The files filter above the list (the view's `/`): every keystroke
        # narrows the rows here and the sections in the diff, Escape clears
        # and hands the keyboard back.
        self._filter_entry = Gtk.SearchEntry()
        self._filter_entry.set_placeholder_text(_("Filter files"))
        self._filter_entry.add_css_class("git-files-filter")
        self._filter_entry.connect("search-changed", self._on_filter_changed)
        self._filter_entry.connect("stop-search", self._on_filter_stopped)
        files_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        files_box.append(self._filter_entry)
        files_box.append(self._file_scroller)

        self._paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._paned.set_start_child(self._commit_scroller)
        self._paned.set_end_child(files_box)
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
            ("reload", self.refresh_commits),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            group.add_action(action)
        copy = Gio.SimpleAction.new("copy-sha", GLib.VariantType.new("s"))
        copy.connect("activate", lambda _a, param: self._copy_text(param.get_string()))
        group.add_action(copy)
        revert = Gio.SimpleAction.new("revert-file", GLib.VariantType.new("s"))
        revert.connect("activate", lambda _a, param: self.emit("revert-requested", param.get_string()))
        group.add_action(revert)
        revert_commit = Gio.SimpleAction.new("revert-commit", GLib.VariantType.new("s"))
        revert_commit.connect("activate", lambda _a, param: self._on_revert_clicked(param.get_string()))
        group.add_action(revert_commit)
        self._actions_group = group
        self.insert_action_group(_ACTIONS, group)

    # -- public: context and content ------------------------------------------------

    @property
    def options(self) -> gitloads.Options:
        """What Preferences → Git says, as last handed in (set_options)."""
        return self._options

    @property
    def busy(self) -> bool:
        """Whether a native mutation (stage all, unstage all, a commit) is
        in flight."""
        return self._busy

    @property
    def selected_path(self) -> str | None:
        """The path of the file the view is on, as last told."""
        return self._selected_path

    def commit_rows(self) -> list[Row]:
        """The rows the commits list draws, top to bottom (for the e2e)."""
        return list(self._rows)

    def file_rows(self) -> FileSections:
        """What the files list draws (for the e2e)."""
        return self._sections

    def collapsed_groups(self) -> set[str]:
        """The group ids folded under their header (for the e2e)."""
        return set(self._collapsed)

    def collapse_group(self, group: str, collapsed: bool | None = None) -> None:
        """Fold (True), unfold (False) or toggle (None) the commits under
        the header of *group*: the caret's click. The header stays as the
        handle; the rows below it hide."""
        if collapsed is None:
            collapsed = group not in self._collapsed
        if collapsed:
            self._collapsed.add(group)
        else:
            self._collapsed.discard(group)
        self._apply_folds()

    def loaded_row_id(self) -> str | None:
        """The id of the row marked `▸`, or None."""
        return self._loaded_row_id

    @property
    def filter_text(self) -> str:
        """The files filter's word, stripped ("" = every row shows)."""
        return self._filter_text

    def focus_filter(self) -> bool:
        """Put the keyboard in the files filter (the `/` key)."""
        return self._filter_entry.grab_focus()

    def set_filter_text(self, text: str) -> None:
        """Type into the filter (the e2e's way): the rows and the signal
        follow as they would for a keystroke."""
        self._filter_entry.set_text(text)

    def set_context(
        self,
        *,
        branch: str | None,
        parent: BranchRef | None,
        default: BranchRef | None,
        loaded: object,
        resolved_sha: str | None,
        live: bool,
        stack: Sequence[BranchRef] = (),
        twins: Sequence[str] = (),
    ) -> bool:
        """What the page knows: the checked-out *branch* (and its *twins*,
        the other local branches at HEAD, which share its header), the *parent* and
        *default* branches the groups are built on (None when the tree
        can't name one) and the *stack* of branches between them (the
        branches under the parent, nearest first, as gitops.stack_branches
        lists them — the page reads it off git), what the page has
        *loaded* (a gitloads.Loaded) and, for a commit load, the sha it
        *resolved* to; and whether a working-tree side is *live* (the
        action row's buttons act on it). A change of branch, parent, stack
        or default refreshes the commits list; the loaded mark and the
        buttons follow every call. Returns whether the groups changed (and
        so the list was re-read here) — the page refreshes it itself
        otherwise after an open."""
        stack = tuple(stack)
        twins = tuple(twins)
        groups = (branch, twins, parent, default, stack)
        groups_changed = groups != (self._branch, self._twins, self._parent, self._default, self._stack)
        self._branch = branch
        self._twins = twins
        self._parent = parent
        self._default = default
        self._stack = stack
        self._loaded = loaded
        self._resolved_sha = resolved_sha
        self._live = bool(live)
        if groups_changed:
            self._pages = {}
            self.refresh_commits()
        else:
            self._mark_loaded_row()
        self._sync_buttons()
        return groups_changed

    def refresh_commits(self) -> None:
        """Re-read the groups' pages and the `↑` marks on a thread and
        rebuild the commits list; a reply to an earlier ask is dropped.
        The stack's groups are the parent (when it isn't the default) and
        the branches under it, each read as `<below>..<branch>`
        (gitmodel.stack_ranges). On the default branch the current group
        is not drawn (build_rows), so its page is not read either."""
        self._commits_gen += 1
        gen = self._commits_gen
        cwd = self._cwd_provider()
        parent, default = self._parent, self._default
        on_default = default is not None and self._branch == default.name
        stack: tuple[BranchRef, ...] = ()
        if parent is not None and (default is None or parent.name != default.name):
            stack = (parent, *self._stack)
        pages = dict(self._pages)
        page_size = self._options.log_page

        def work() -> None:
            current: list[gitmodel.Commit] = []
            current_more = False
            if not on_default:
                current_range = [f"{parent.target}..HEAD"] if parent is not None else ["HEAD"]
                current, current_more = gitops.read_page(
                    cwd, current_range, page_size, pages.get(gitmodel.CURRENT_GROUP, 1)
                )
            stack_pages: list[gitmodel.BranchPage] = []
            for ref, below in gitmodel.stack_ranges(stack, default):
                group = gitmodel.stack_group(ref.name)
                span = [f"{below}..{ref.target}"] if below is not None else [ref.target]
                commits, more = gitops.read_page(cwd, span, page_size, pages.get(group, 1))
                stack_pages.append(gitmodel.BranchPage(ref, tuple(commits), more))
            default_commits: list[gitmodel.Commit] = []
            default_more = False
            if default is not None:
                default_commits, default_more = gitops.read_page(
                    cwd, [default.target], page_size, pages.get(gitmodel.DEFAULT_GROUP, 1)
                )
            unpushed = gitops.unpushed_shas(cwd)
            GLib.idle_add(
                self._commits_read,
                gen,
                (current, current_more, stack_pages, default_commits, default_more, unpushed),
                priority=GLib.PRIORITY_DEFAULT,
            )

        threading.Thread(target=work, name="git-sidebar-log", daemon=True).start()

    def _commits_read(self, gen: int, read: tuple) -> bool:
        if gen != self._commits_gen:
            return GLib.SOURCE_REMOVE
        current, current_more, stack_pages, default_commits, default_more, unpushed = read
        self._rows = gitmodel.build_rows(
            self._branch or "HEAD",
            self._parent,
            self._default,
            current,
            current_more,
            stack_pages,
            default_commits,
            default_more,
            unpushed,
            twins=self._twins,
        )
        self._rebuild_commits()
        return GLib.SOURCE_REMOVE

    def refresh_files(
        self,
        session_files: Sequence[gitmodel.FileSummary],
        loaded: object,
        untracked: bool,
        status: gitmodel.Status | None = None,
    ) -> None:
        """Rebuild the files list from the diff's *session_files* for
        *loaded*: a working-tree load reads `git status` on a thread for
        the other side first (the `?` rows dropped when *untracked* is
        off) — unless the caller already has the *status* (the page's read
        carries it), which is drawn at once; any other load is one flat
        list, drawn at once."""
        self._session_files = tuple(session_files)
        self._files_loaded = loaded
        self._untracked = untracked
        self._files_gen += 1
        gen = self._files_gen
        if loaded not in ("unstaged", "staged"):
            self._sections = gitmodel.files_sections(None, self._session_files, loaded, untracked)
            self._rebuild_files()
            return
        if status is not None:
            self._sections = gitmodel.files_sections(status, self._session_files, loaded, untracked)
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

    def set_selection(self, path: str | None, hunk: int | None) -> None:
        """Highlight the file the view is on (its `current-changed`: the
        file at the top of the viewport, or the hunk the keyboard moved
        into). None clears it."""
        if path == self._selected_path and hunk == self._selected_hunk:
            return
        self._selected_path = path
        self._selected_hunk = hunk
        self._mark_selected_file()

    def set_options(self, options: gitloads.Options) -> None:
        """Preferences → Git changed: a new page size re-pages the commits
        list from its first page; a flipped untracked switch redraws the
        files list."""
        old, self._options = self._options, options
        if options.log_page != old.log_page:
            self._pages = {}
            self.refresh_commits()
        if options.untracked != old.untracked:
            self.refresh_files(self._session_files, self._files_loaded, options.untracked)

    def load_more(self, group: str) -> None:
        """One more page for *group* (a row's group id: the current or
        default group, or a stack branch's)."""
        if not any(row.group == group for row in self._rows):
            return
        self._pages[group] = self._pages.get(group, 1) + 1
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

        self.run_mutation(work, done)

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
                abbrev = gitloads.short_ref(sha)
                self._toast(
                    _("Committed a fixup for {sha} — fold it in with `{command}`").format(
                        sha=abbrev, command=gitmodel.autosquash_command(abbrev, is_root)
                    )
                )
            else:
                self._toast(gitops.first_line(result.stderr) or _("git commit failed"), refusal=True)
            self.emit("mutated")

        self.run_mutation(work, done)

    def revert(self, sha: str, commit: bool) -> None:
        """`git revert --no-edit <sha>` (*commit*) or `git revert
        --no-commit <sha>` (the reverse change staged in the working tree)
        on a thread; the toast names the commit made or the way out of a
        revert that stopped on conflicts, and "mutated" fires either way.
        Public so the e2e drives it without the dialog."""
        cwd = self._cwd_provider()
        abbrev = gitloads.short_ref(sha)

        def work() -> tuple:
            result = gitops.revert(cwd, sha, commit)
            head = gitops.head_abbrev(cwd) if result.ok and commit else None
            # A revert stopped on conflicts leaves unmerged paths behind
            # (REVERT_HEAD alone would also mark a --no-commit whose quit
            # failed, whose words are gitops's own).
            conflicts = False
            if not result.ok:
                status = gitops.read_status(cwd)
                conflicts = status is not None and any(row.code == "U" for row in status.unstaged)
            return result, head, conflicts

        def done(read: tuple) -> None:
            result, head, conflicts = read
            if result.ok:
                self._toast(gitmodel.revert_done(abbrev, commit, head))
            else:
                self._toast(
                    gitmodel.revert_failed(abbrev, gitops.first_line(result.stderr), conflicts),
                    refusal=True,
                )
            self.emit("mutated")

        self.run_mutation(work, done)

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

        self.run_mutation(work, done)

    def run_mutation(self, work: Callable[[], object], done: Callable[[object], None]) -> bool:
        """*work* on a daemon thread while the widget reads busy, *done*
        with its answer back on the main loop — unless the widget was
        told to forget the ask meanwhile (a newer generation). Public:
        the page runs the diff view's stage / discard / revert plans
        behind the same busy. False (and a toast) when one is running."""
        if self._busy:
            self._toast(_("Another git operation is still running"), refusal=True)
            return False
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
        return True

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
            widget = _CommitRow(row, self.collapse_group)
            self._commit_list.append(widget)
            self._commit_widgets[row.id] = widget
        self._mark_loaded_row()
        self._apply_folds()
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

    def _apply_folds(self) -> None:
        for widget in self._commit_widgets.values():
            widget.set_visible(not gitmodel.row_folded(widget.row, self._collapsed))
            widget.set_collapsed(widget.row.group in self._collapsed)

    def _on_commit_row_activated(self, _list: Gtk.ListBox, widget: Gtk.ListBoxRow) -> None:
        if not isinstance(widget, _CommitRow):
            return
        row = widget.row
        if row.kind == "more":
            self.load_more(row.group)
            return
        if row.load is not None:
            self.emit("load-requested", row.load)

    def _commit_menu_items(self, row: Row | None) -> list[tuple[str, str, str | None]]:
        """(label, action, target) for the commits list's context menu:
        *Copy sha* and *Revert…* on a commit row (whatever the page shows —
        the revert lands in the session's working tree, not in the loaded
        diff), *Reload* always."""
        items: list[tuple[str, str, str | None]] = []
        if row is not None and row.kind == "commit" and row.sha:
            items.append((_("Copy sha"), "copy-sha", row.sha))
            items.append((_("Revert…"), "revert-commit", row.sha))
        items.append((_("Reload"), "reload", None))
        return items

    def commit_menu_labels(self, row_id: str) -> list[str] | None:
        """The labels a right-click on the commits row *row_id* offers (for
        the e2e); None for a row that isn't listed."""
        widget = self._commit_widgets.get(row_id)
        if widget is None:
            return None
        return [label for label, _action, _target in self._commit_menu_items(widget.row)]

    def _on_commits_secondary_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        widget = self._commit_list.get_row_at_y(int(y))
        menu = Gio.Menu()
        row = widget.row if isinstance(widget, _CommitRow) else None
        for label, action, target in self._commit_menu_items(row):
            item = Gio.MenuItem.new(label, None)
            if target is None:
                item.set_action_and_target_value(f"{_ACTIONS}.{action}", None)
            else:
                item.set_action_and_target_value(f"{_ACTIONS}.{action}", GLib.Variant("s", target))
            menu.append_item(item)
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
                self._file_list.append(_SectionRow(title, len(rows), side))
                for file in rows:
                    widget = _FileRow(file, side)
                    self._file_list.append(widget)
                    self._file_widgets[(side, file.path)] = widget
        else:
            self._file_list.append(_SectionRow(_("FILES"), len(sections.flat), ""))
            for file in sections.flat:
                widget = _FileRow(file, "")
                self._file_list.append(widget)
                self._file_widgets[("", file.path)] = widget
        self._mark_selected_file()
        self._apply_filter()
        if value > 0:
            GLib.idle_add(_restore_scroll, adjustment, value)

    def _apply_filter(self) -> None:
        needle = self._filter_text.casefold()
        for widget in self._file_widgets.values():
            file = widget.file
            shown = not needle or needle in file.path.casefold() or (
                file.previous_path is not None and needle in file.previous_path.casefold()
            )
            widget.set_visible(shown)

    def _on_filter_changed(self, entry: Gtk.SearchEntry) -> None:
        text = (entry.get_text() or "").strip()
        if text == self._filter_text:
            return
        self._filter_text = text
        self._apply_filter()
        self.emit("filter-changed", text)

    def _on_filter_stopped(self, entry: Gtk.SearchEntry) -> None:
        """Escape in the filter: clear it (the rows and the diff come back)
        and ask the page for the keyboard to return to the diff."""
        if entry.get_text():
            entry.set_text("")
        self.emit("filter-escaped")

    def _mark_selected_file(self) -> None:
        live = self._sections.live if self._sections.mode == "split" else ""
        for (side, path), widget in self._file_widgets.items():
            widget.set_selected(side == live and path == self._selected_path)

    def _on_file_row_activated(self, _list: Gtk.ListBox, widget: Gtk.ListBoxRow) -> None:
        if isinstance(widget, _SectionRow):
            if widget.side and widget.side != self._sections.live:
                self.emit("load-requested", widget.side)
            else:
                self.emit("show-all-requested")
            return
        if isinstance(widget, _FileRow):
            self.emit("navigate-requested", widget.file.path, widget.side)

    def _file_menu_items(self, widget: _FileRow) -> list[tuple[str, str, str]]:
        """(label, action, target) for a file row's context menu: *Revert
        file* on a read-only load — the same reverse apply into the
        working tree as the diff's file header button, nothing committed
        — and nothing on the working-tree loads, whose rows' actions are
        the headers' own."""
        if gitpatch.working_side(self._loaded) is not None:
            return []
        return [(_("Revert file"), "revert-file", widget.file.path)]

    def _on_files_secondary_click(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        row = self._file_list.get_row_at_y(int(y))
        if not isinstance(row, _FileRow):
            return
        items = self._file_menu_items(row)
        if not items:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        menu = Gio.Menu()
        for label, action, target in items:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"{_ACTIONS}.{action}", GLib.Variant("s", target))
            menu.append_item(item)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(self._file_list)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    def file_menu_labels(self, path: str, side: str = "") -> list[str] | None:
        """The labels a right-click on the row of *path* offers (for the
        e2e); None when there is no such row."""
        widget = self._file_widgets.get((side, path))
        if widget is None:
            return None
        return [label for label, _action, _target in self._file_menu_items(widget)]

    def activate_file_menu(self, path: str, label: str, side: str = "") -> bool:
        """Pick *label* from the row's context menu as a click would (for
        the e2e)."""
        widget = self._file_widgets.get((side, path))
        if widget is None:
            return False
        for name, action, target in self._file_menu_items(widget):
            if name == label:
                self._actions_group.activate_action(action, GLib.Variant("s", target))
                return True
        return False

    # -- the action row ----------------------------------------------------------------------

    def _working_live(self) -> bool:
        return self._live

    def _sync_buttons(self) -> None:
        live = self._working_live()
        for button in (self._stage_all_button, self._unstage_all_button):
            button.set_sensitive(live and not self._busy)
        self._commit_button.set_sensitive(live and not self._busy)
        self._commit_stack.set_visible_child_name("spinner" if self._busy else "label")
        self._stage_all_button.set_tooltip_text(_("Stage every change (git add -A)"))
        self._unstage_all_button.set_tooltip_text(_("Unstage every change (git reset)"))

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

    def _on_revert_clicked(self, sha: str) -> None:
        """The commits list's *Revert…*: refuse while a rebase / merge /
        cherry-pick / revert is half-finished (the revert would be that
        operation's next step), else ask — commit the revert, revert into
        the working tree alone (`--no-commit`), or cancel. Offered on any
        load: the revert acts on the session's working tree, whatever diff
        the page is showing."""
        if self._busy or not gitloads.safe_ref(sha):
            return
        cwd = self._cwd_provider()

        def work() -> None:
            operation = gitops.in_progress_operation(gitinfo.git_dir(cwd))
            GLib.idle_add(self._revert_gated, sha, operation, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="git-sidebar-revert-gate", daemon=True).start()

    def _revert_gated(self, sha: str, operation: str | None) -> bool:
        if operation is not None:
            self._toast(
                _("A {operation} is half-finished here — finish or abort it first").format(
                    operation=operation
                ),
                refusal=True,
            )
            return GLib.SOURCE_REMOVE
        abbrev = gitloads.short_ref(sha)
        widget = self._commit_widgets.get(gitmodel.commit_row_id(sha))
        # The row is what the menu was opened on; a sha the list no longer
        # holds (a reload between the click and the gate) reads by its sha.
        subject = widget.row.label if widget is not None else abbrev
        dialogs.confirm_dialog(
            self,
            _("Revert {sha}?").format(sha=abbrev),
            _(
                "“{subject}” is applied in reverse. Commit the revert as `git revert` would,"
                " or leave the reverse change staged in the working tree (`--no-commit`)"
                " to edit and commit yourself."
            ).format(subject=subject),
            _("Commit revert"),
            lambda: self.revert(sha, True),
            default_response="confirm",
            extra_label=_("Revert in working tree"),
            on_extra=lambda: self.revert(sha, False),
            destructive=False,
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
