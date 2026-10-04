# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Add project → Clone repository: a dialog that clones a repository and
hands the new checkout to the window as a project.

One box does two jobs (clonerepo decides which): typed words filter the
repositories `gh` lists for the signed-in user — their own, the ones they
collaborate on, and everything their organizations show them — and a
pasted address (or an `owner/repo` the list doesn't carry) is cloned as
typed. Under it sit the target directory, which starts at the
`clone_directory` setting, and the full destination path in plain view,
with a line saying whether git will accept it there.

The list and the clone are the service's (split-service spec §3.15,
PR-1.11): `gh` and git run on the service's machine, as jobs
(`service.jobs`: ``clone.repos`` and ``clone``) whose events this dialog
reads (`jobclient`). The list arrives a page at a time and is cached for
the app's lifetime, so a second open is instant and quietly refreshed. The
clone runs `gh repo clone` / `git clone` in its own process group with
every prompt turned off; Cancel (or closing the dialog) cancels the job,
which kills the group, and git removes the half-made folder itself.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import clonerepo, ghsetup, jobclient  # noqa: E402
from .formatting import display_path  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

# The last list gh gave, shared by every dialog: a reopened dialog shows it
# at once while a fresh fetch replaces it.
_repo_cache: list[clonerepo.Repo] | None = None



class CloneDialog(Adw.Dialog):
    """Clone a repository under a target directory; *on_cloned* gets the
    new checkout's path once git has finished."""

    def __init__(self, parent_dir: str, on_cloned: Callable[[str], None]) -> None:
        super().__init__(title=_("Clone Repository"))
        self._on_cloned = on_cloned
        self._repos: list[clonerepo.Repo] = list(_repo_cache or [])
        self._loading = True
        self._list_problem: str | None = None  # a ghsetup state, or "failed"
        self._selected_name: str | None = None
        self._job: str | None = None  # the clone job in flight
        self._list_job: str | None = None
        self._closed = False
        self.set_content_width(600)
        self.set_content_height(640)
        self.set_follows_content_size(False)

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *_a: self.close())
        header.pack_start(cancel)
        self._clone_btn = Gtk.Button(label=_("Clone"))
        self._clone_btn.add_css_class("suggested-action")
        self._clone_btn.connect("clicked", lambda *_a: self._start_clone())
        header.pack_end(self._clone_btn)

        self._entry = Gtk.SearchEntry(
            placeholder_text=_("Filter your repositories, or paste a clone address"),
            hexpand=True,
        )
        self._entry.connect("search-changed", lambda *_a: self._refilter())
        self._entry.connect("activate", lambda *_a: self._start_clone())
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self._entry.add_controller(key)

        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(False)
        self._list.add_css_class("navigation-sidebar")
        self._list.connect("row-selected", self._on_row_selected)
        self._list.connect("row-activated", lambda *_a: self._start_clone())
        self._placeholder = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self._placeholder.add_css_class("dim-label")
        self._placeholder_spinner = Gtk.Spinner(visible=False)
        placeholder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            valign=Gtk.Align.CENTER,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        placeholder.append(self._placeholder_spinner)
        placeholder.append(self._placeholder)
        scrolled = Gtk.ScrolledWindow(child=self._list, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # A stack rather than set_placeholder(): remove_all() between two
        # filters took the list's placeholder with the rows, and the empty
        # list came up blank instead of saying why.
        self._list_stack = Gtk.Stack(vexpand=True)
        self._list_stack.add_named(scrolled, "list")
        self._list_stack.add_named(placeholder, "empty")
        frame = Gtk.Frame(child=self._list_stack)

        self._target = Adw.EntryRow(title=_("Clone into"))
        self._target.set_text(display_path(clonerepo.parent_directory(parent_dir)))
        self._target.connect("changed", lambda *_a: self._refresh_destination())
        browse = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Choose the folder to clone into"),
        )
        browse.add_css_class("flat")
        browse.connect("clicked", lambda *_a: self._browse_target())
        self._target.add_suffix(browse)
        target_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        target_list.add_css_class("boxed-list")
        target_list.append(self._target)

        # The destination, spelled out: the one path the clone will create.
        dest_caption = Gtk.Label(label=_("Destination"), xalign=0.0)
        dest_caption.add_css_class("caption-heading")
        dest_caption.add_css_class("dim-label")
        self._dest_label = Gtk.Label(xalign=0.0, wrap=True)
        self._dest_label.set_wrap_mode(Pango.WrapMode.CHAR)
        self._dest_label.add_css_class("monospace")
        self._dest_label.add_css_class("heading")
        self._dest_note = Gtk.Label(xalign=0.0, wrap=True)
        self._dest_note.add_css_class("caption")
        self._spinner = Gtk.Spinner(valign=Gtk.Align.START, visible=False)
        note_row = Gtk.Box(spacing=6)
        note_row.append(self._spinner)
        note_row.append(self._dest_note)
        self._error = Gtk.Label(xalign=0.0, wrap=True, visible=False)
        self._error.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._error.add_css_class("error")
        self._error.add_css_class("caption")
        dest_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        dest_box.append(dest_caption)
        dest_box.append(self._dest_label)
        dest_box.append(note_row)
        dest_box.append(self._error)

        body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=16,
            margin_start=16,
            margin_end=16,
        )
        body.append(self._entry)
        body.append(frame)
        body.append(target_list)
        body.append(dest_box)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(body)
        self.set_child(toolbar)

        self.connect("map", lambda *_a: self._entry.grab_focus())
        self.connect("closed", self._on_closed)
        self._refilter()
        self._start_fetch()

    # -- the repository list -------------------------------------------------

    def _start_fetch(self) -> None:
        """The service's ``clone.repos`` job: a page at a time as running
        events, then the whole list, or the problem (a ghsetup state, or
        "failed")."""

        def on_event(event: jobclient.JobEvent) -> None:
            if not event.finished:
                repos = clonerepo.repos_from_records(event.result.get("repos"))
                if repos is not None:
                    self._land_page(repos)
                return
            if event.ok:
                repos = clonerepo.repos_from_records(event.result.get("repos"))
                problem = event.result.get("problem")
                problem = problem if isinstance(problem, str) else None
                self._land_repos(repos, problem if repos is None else None)
            else:
                self._land_repos(None, "failed")

        self._list_job = jobclient.start("clone.repos", {}, on_event)

    def _land_page(self, repos: list[clonerepo.Repo]) -> bool:
        """A page arrived: show what's here so far, the rest still coming."""
        if not self._closed and repos != self._repos:
            self._repos = repos
            self._refilter()
        return GLib.SOURCE_REMOVE

    def _land_repos(self, repos: list[clonerepo.Repo] | None, problem: str | None) -> bool:
        global _repo_cache
        if repos is not None:
            _repo_cache = repos
        if self._closed:
            return GLib.SOURCE_REMOVE
        self._loading = False
        self._list_problem = problem
        if repos is not None:
            self._repos = repos
        elif problem in (ghsetup.MISSING, ghsetup.LOGGED_OUT):
            # gh can't speak for this account (any more): a cached list from
            # before would offer clones that are going to fail.
            self._repos = []
        self._refilter()
        return GLib.SOURCE_REMOVE

    def _refilter(self) -> None:
        text = self._entry.get_text().strip()
        address = clonerepo.is_address(text)
        shown = [] if address else clonerepo.filter_repos(self._repos, text)
        keep = self._selected_name
        self._list.remove_all()
        chosen = None
        for repo in shown:
            row = self._make_row(repo)
            self._list.append(row)
            if repo.full_name == keep:
                chosen = row
        # Typing aims at the best match, so Enter clones it; an empty box
        # keeps whatever row was clicked, and aims at nothing otherwise.
        if chosen is None and text:
            chosen = self._list.get_row_at_index(0)
        self._list.select_row(chosen)
        self._selected_name = chosen.repo.full_name if chosen is not None else None
        self._sync_placeholder(text, address)
        self._refresh_destination()

    def _sync_placeholder(self, text: str, address: bool) -> None:
        spinning = self._loading and not self._repos and not address
        self._placeholder_spinner.set_visible(spinning)
        self._placeholder_spinner.set_spinning(spinning)
        source = clonerepo.parse_source(text)
        if address:
            message = _("A clone address: it is cloned as typed")
        elif source is not None:
            message = _("Not in your list; {repo} is cloned from GitHub as typed").format(
                repo=source.spec
            )
        elif spinning:
            message = _("Loading your repositories…")
        elif self._list_problem == ghsetup.MISSING:
            message = _(
                "Install the GitHub CLI (gh) to list your repositories. "
                "Any clone address still works"
            )
        elif self._list_problem == ghsetup.LOGGED_OUT:
            message = _(
                "Run gh auth login to list your repositories. Any clone address still works"
            )
        elif self._list_problem == "failed" and not self._repos:
            message = _("gh couldn't list your repositories. Any clone address still works")
        elif not self._repos:
            message = _("gh lists no repositories for this account")
        else:
            message = _("No repositories match")
        self._placeholder.set_text(message)
        empty = self._list.get_row_at_index(0) is None
        self._list_stack.set_visible_child_name("empty" if empty else "list")

    def _make_row(self, repo: clonerepo.Repo) -> Adw.ActionRow:
        row = Adw.ActionRow(title=repo.full_name, use_markup=False)
        row.repo = repo
        if repo.description:
            row.set_subtitle(repo.description)
            row.set_subtitle_lines(1)
        row.set_title_lines(1)
        for flag, word in (
            (repo.private, _("private")),
            (repo.fork, _("fork")),
            (repo.archived, _("archived")),
        ):
            if flag:
                tag = Gtk.Label(label=word, valign=Gtk.Align.CENTER)
                tag.add_css_class("dim-label")
                tag.add_css_class("caption")
                row.add_suffix(tag)
        return row

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        self._selected_name = row.repo.full_name if row is not None else None
        self._refresh_destination()

    def _on_key(self, _ctrl, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            selected = self._list.get_selected_row()
            index = selected.get_index() if selected is not None else -1
            target = self._list.get_row_at_index(index + (1 if keyval == Gdk.KEY_Down else -1))
            if target is not None:
                self._list.select_row(target)
                target.grab_focus()  # scrolls it into view
                self._entry.grab_focus()  # and keeps typing in the box
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    # -- the destination -----------------------------------------------------

    def source(self) -> clonerepo.CloneSource | None:
        """What Clone would clone: a typed address first, then the selected
        row, then a typed `owner/repo` the list doesn't carry."""
        text = self._entry.get_text()
        if clonerepo.is_address(text):
            return clonerepo.parse_source(text)
        row = self._list.get_selected_row()
        if row is not None:
            return clonerepo.parse_source(row.repo.full_name)
        return clonerepo.parse_source(text)

    def destination(self) -> Path | None:
        return clonerepo.destination(self._target.get_text(), self.source())

    def _refresh_destination(self) -> None:
        parent_text = self._target.get_text()
        source = self.source()
        dest = clonerepo.destination(parent_text, source)
        status = clonerepo.destination_status(parent_text, dest)
        parent = clonerepo.parent_directory(parent_text)
        if dest is not None:
            self._dest_label.set_text(display_path(str(dest)))
            self._dest_label.remove_css_class("dim-label")
        else:
            shown = display_path(os.path.normpath(parent)) if os.path.isabs(parent) else parent
            self._dest_label.set_text(os.path.join(shown, _("<repository>")))
            self._dest_label.add_css_class("dim-label")
        bad = status in (clonerepo.EXISTS, clonerepo.BLOCKED, clonerepo.RELATIVE)
        if self._job is not None:
            note = _("Cloning…")
        elif status == clonerepo.RELATIVE:
            note = _("Clone into needs a full path, like ~/dev")
        elif source is None:
            note = _("Pick a repository, or paste a clone address")
        elif status == clonerepo.EXISTS:
            note = _("Something is already there. Choose another folder to clone into")
        elif status == clonerepo.BLOCKED:
            note = _("A file is in the way: part of that path isn't a folder")
        else:
            origin = source.github_name or source.spec
            tool = "gh" if source.kind == clonerepo.GITHUB and shutil.which("gh") else "git"
            note = _("A new folder, cloned from {origin} with {tool}").format(
                origin=origin, tool=tool
            )
            if status == clonerepo.CREATES_PARENT:
                note += "\n" + _("{folder} doesn't exist yet and will be created").format(
                    folder=display_path(os.path.normpath(parent))
                )
        self._dest_note.set_text(note)
        # Red only for a problem with a real choice behind it: no pick yet
        # is not an error, and neither is a path mid-way through typing.
        error = self._job is None and (
            status == clonerepo.RELATIVE or (source is not None and bad)
        )
        if error:
            self._dest_note.add_css_class("error")
            self._dest_note.remove_css_class("dim-label")
        else:
            self._dest_note.remove_css_class("error")
            self._dest_note.add_css_class("dim-label")
        self._clone_btn.set_sensitive(
            self._job is None and source is not None and dest is not None and not bad
        )

    def _browse_target(self) -> None:
        picker = Gtk.FileDialog(title=_("Choose the folder to clone into"))
        current = clonerepo.parent_directory(self._target.get_text())
        if os.path.isdir(current):
            picker.set_initial_folder(Gio.File.new_for_path(current))

        def picked(picker: Gtk.FileDialog, result) -> None:
            try:
                folder = picker.select_folder_finish(result)
            except GLib.Error:
                return  # dismissed
            path = folder.get_path() if folder is not None else None
            if path:
                self._target.set_text(display_path(path))

        picker.select_folder(self.get_root(), None, picked)

    # -- cloning -------------------------------------------------------------

    def _start_clone(self) -> None:
        if self._job is not None or not self._clone_btn.get_sensitive():
            return
        source = self.source()
        dest = self.destination()
        if source is None or dest is None:
            return
        # Busy before the start: its refusal, which lands inside the call,
        # is what turns it off again.
        self._job = "starting"
        self._error.set_visible(False)
        self._set_busy(True)
        args = {
            "source": {"kind": source.kind, "spec": source.spec, "name": source.name},
            "dest": str(dest),
        }
        started = jobclient.start("clone", args, self._clone_event)
        if self._job == "starting":
            self._job = started

    def _clone_event(self, event: jobclient.JobEvent) -> None:
        # One clone at a time: a finished event while one is out is its.
        if event.finished and self._job is not None:
            self._clone_done(event)

    def _set_busy(self, busy: bool) -> None:
        self._spinner.set_visible(busy)
        self._spinner.set_spinning(busy)
        for widget in (self._entry, self._list, self._target):
            widget.set_sensitive(not busy)
        self._clone_btn.set_label(_("Cloning…") if busy else _("Clone"))
        self._refresh_destination()

    def _clone_done(self, event: jobclient.JobEvent) -> None:
        self._job = None
        if self._closed:
            return
        self._set_busy(False)
        dest = event.result.get("path")
        if event.ok and isinstance(dest, str) and os.path.isdir(dest):
            self.close()
            self._on_cloned(dest)
            return
        self._show_error(event.text or _("The clone failed"))

    def _show_error(self, text: str) -> None:
        self._error.set_text(text)
        self._error.set_visible(True)

    def _on_closed(self, *_args) -> None:
        """Closing mid-clone is the Cancel it looks like: the whole process
        group goes (gh and the git under it), and git's own signal handler
        removes the folder it had started."""
        self._closed = True
        job, self._job = self._job, None
        jobclient.cancel(job)
        jobclient.cancel(self._list_job)
