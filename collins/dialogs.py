# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-27. Full change history: git log for this file.

"""Reusable dialogs, kept out of the main window."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import claudemodels, composerkeys, editorfiles, icongen
from .chats import is_chat_cwd
from .formatting import display_path, format_size, format_timestamp, format_tokens
from .i18n import _, ngettext
from .providers import SessionOptions, get_provider
from .sessions import (
    Session,
    SessionDetails,
    configured_mcp_servers,
    read_mcp_config,
)
from .state import AppState
from .svgtexture import svg_texture


def _present(dialog: Adw.Dialog, parent: Gtk.Widget) -> None:
    """Present *dialog* on *parent*, unhiding a hidden toplevel first.

    A window that hid instead of closing (quit_with_running_sessions =
    "hide") can still be asked questions — an MCP-triggered close
    confirmation, the quit flow, a CLI notice — and a modal parented on an
    invisible window is a hang with no UI. Every dialog in this module
    presents through here so the rule holds for all of them.
    """
    root = parent.get_root() if parent is not None else None
    if isinstance(root, Gtk.Window) and not root.get_visible():
        root.present()
    dialog.present(parent)


def rename_dialog(parent: Gtk.Widget, body: str, current: str, on_save: Callable[[str], None]) -> None:
    dialog = Adw.AlertDialog(heading=_("Rename session"), body=body)
    entry = Gtk.Entry(text=current, placeholder_text=_("Custom name"))
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)
    # Otherwise the dialog focuses its default response, leaving the name one
    # Tab away in a dialog that exists only to type a name into. set_focus
    # beats a grab from the entry's map: the dialog applies it as it presents,
    # so focus never bounces off the Save button on the way.
    dialog.set_focus(entry)

    def select_all() -> bool:
        # Selected, not just focused: the common rename types a whole new name
        # over the old one, and the rarer small edit is still a click or an
        # arrow key away. Selected end-to-start, so the cursor lands at 0 and a
        # name too long for the entry shows its beginning, not its tail.
        entry.select_region(len(current), 0)
        return GLib.SOURCE_REMOVE

    # The dialog focuses the entry as it maps, which selects everything with
    # the cursor at the end; this has to run after that, and a low-priority
    # idle off the entry's own map is what lands there (see rename_path_dialog).
    entry.connect("map", lambda *_a: GLib.idle_add(select_all, priority=GLib.PRIORITY_LOW))
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("save", _("Save"))
    dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("save")
    dialog.connect(
        "response",
        lambda _d, response: on_save(entry.get_text()) if response == "save" else None,
    )
    _present(dialog, parent)


def rename_path_dialog(
    parent: Gtk.Widget, current: str, is_dir: bool, on_rename: Callable[[str], None]
) -> None:
    """Rename a file or folder in the editor's file tree. `on_rename` gets
    the raw text — the caller (editor.py) is what decides whether it names a
    rename that can happen, and says why when it doesn't.

    The name comes up selected except for its extension: renaming `notes.md`
    is nearly always about the `notes` part, and typing over the whole thing
    would quietly drop the suffix that makes the file highlight."""
    dialog = Adw.AlertDialog(
        heading=_("Rename folder") if is_dir else _("Rename file"),
        body=_("Enter a new name for “{name}”.").format(name=current),
    )
    entry = Gtk.Entry(text=current)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("rename", _("Rename"))
    dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("rename")
    dialog.set_close_response("cancel")

    def select_name() -> bool:
        dot = -1 if is_dir else current.rfind(".")
        entry.grab_focus()  # which selects the whole name...
        entry.select_region(0, dot if dot > 0 else len(current))  # ...so this comes after
        return GLib.SOURCE_REMOVE

    # From a low-priority idle off the *entry's* map, not the dialog's: the
    # dialog puts focus in the entry itself as it maps (selecting everything),
    # and anything queued from the dialog's own map still runs before that.
    entry.connect("map", lambda *_a: GLib.idle_add(select_name, priority=GLib.PRIORITY_LOW))
    dialog.connect(
        "response",
        lambda _d, response: on_rename(entry.get_text()) if response == "rename" else None,
    )
    _present(dialog, parent)


def follow_working_dir_dialog(
    parent: Gtk.Widget,
    old_root: str,
    new_root: str,
    entries: list[editorfiles.RerootEntry],
    on_move: Callable[[dict[str, editorfiles.RerootAction]], None],
    on_done: Callable[[], None] | None = None,
) -> None:
    """Asked when the session's working directory has moved and the editor is
    about to follow it, but open buffers hold unsaved changes.

    Every file listed exists in both places — the same project-relative path
    on either side of the move, almost always the same source file on two
    branches — so there is no answer that is right for all of them, and each
    row gets its own. The three are: leave the tab where it is, so the edits
    stay attached to the file they were made against; take the edits across,
    so saving writes them over the new root's copy; or drop them and open that
    copy as it stands. Clean buffers aren't listed — they follow silently,
    having nothing to lose — and neither are files the move can't place.

    "Don't Move" (and Escape) abandons the whole re-root, editor and tree
    included, not just the files listed here."""
    RA = editorfiles.RerootAction
    dialog = Adw.AlertDialog(
        heading=_("Move editor to {name}?").format(name=Path(new_root).name),
        body=ngettext(
            "This session is now working in {path}. One open file has unsaved "
            "changes and also exists there — choose what happens to it.",
            "This session is now working in {path}. {count} open files have "
            "unsaved changes and also exist there — choose what happens to each.",
            len(entries),
        ).format(path=display_path(new_root), count=len(entries)),
    )

    choices: dict[str, editorfiles.RerootAction] = {}
    listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listbox.add_css_class("boxed-list")
    for entry in entries:
        choices[entry.path] = entry.default
        try:
            subtitle = str(Path(entry.path).relative_to(old_root))
        except ValueError:
            subtitle = display_path(entry.path)
        row = Adw.ActionRow(title=Path(entry.path).name, subtitle=subtitle)
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, valign=Gtk.Align.CENTER)
        buttons.add_css_class("linked")
        group: Gtk.ToggleButton | None = None
        for action, label, tooltip in (
            (
                RA.LEAVE,
                _("Stay"),
                _("Go on editing this file, where your unsaved changes belong"),
            ),
            (
                RA.FOLLOW,
                _("Take edits"),
                _(
                    "Move this tab to the new copy, keeping your unsaved changes — "
                    "saving will write them over whatever that copy holds"
                ),
            ),
            (
                RA.RELOAD,
                _("Use new"),
                _("Open the new copy and discard your unsaved changes"),
            ),
        ):
            button = Gtk.ToggleButton(label=label, tooltip_text=tooltip)
            if group is None:
                group = button
            else:
                button.set_group(group)
            button.set_active(action is entry.default)
            # Bound per row and per action; the default-setting above happens
            # before this, so arming the handler can't record a stray choice.
            button.connect(
                "toggled",
                lambda btn, path=entry.path, act=action: (
                    choices.__setitem__(path, act) if btn.get_active() else None
                ),
            )
            buttons.append(button)
        row.add_suffix(buttons)
        listbox.append(row)

    # Capped rather than unbounded: a session with a dozen dirty buffers would
    # otherwise grow a dialog taller than the window it is presented over. The
    # width is asked for rather than left to the default: an AlertDialog sizes
    # itself around its heading, which leaves the rows narrower than the button
    # strip plus a filename, and the filename is what loses.
    scroller = Gtk.ScrolledWindow(
        child=listbox,
        hscrollbar_policy=Gtk.PolicyType.NEVER,
        propagate_natural_height=True,
        max_content_height=280,
    )
    scroller.set_size_request(440, -1)
    dialog.set_extra_child(scroller)
    dialog.add_response("cancel", _("Don't Move"))
    dialog.add_response("move", _("Move Editor"))
    dialog.set_response_appearance("move", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("move")
    dialog.set_close_response("cancel")

    def respond(_dialog, response: str) -> None:
        # on_done fires either way: the caller holds an "asking" flag that has
        # to clear on a decline as much as on a move, or the next time the
        # session moves nothing would be asked at all.
        if response == "move":
            on_move(choices)
        if on_done is not None:
            on_done()

    dialog.connect("response", respond)
    _present(dialog, parent)


def emoji_dialog(parent: Gtk.Widget, current: str, on_save: Callable[[str], None]) -> None:
    dialog = Adw.AlertDialog(
        heading=_("Set tab emoji"),
        body=_("Shown before the tab title. Leave empty to remove."),
    )
    entry = Gtk.Entry(text=current, placeholder_text=_("e.g. 🚀"))
    entry.set_property("show-emoji-icon", True)  # click the 🙂 icon to pick one
    entry.set_property("enable-emoji-completion", True)
    entry.set_activates_default(True)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("save", _("Save"))
    dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("save")
    dialog.connect(
        "response",
        lambda _d, response: on_save(entry.get_text()) if response == "save" else None,
    )
    _present(dialog, parent)


def confirm_dialog(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    confirm_label: str,
    on_confirm: Callable[[], None],
    on_dismiss: Callable[[], None] | None = None,
    default_response: str = "cancel",
    extra_label: str | None = None,
    on_extra: Callable[[], None] | None = None,
    extra2_label: str | None = None,
    on_extra2: Callable[[], None] | None = None,
    extra_child: Gtk.Widget | None = None,
    keys: dict[str, str] | None = None,
    destructive: bool = True,
    confirm_class: str | None = None,
) -> None:
    """Two-button confirmation (Cancel + a destructive `confirm_label`).
    `extra_label`/`on_extra` add a third, non-destructive choice between them;
    `extra2_label`/`on_extra2` a fourth, sitting right after Cancel — the
    least destructive answer of all (the quit dialog's Keep Running);
    `extra_child` puts a widget (a check button, say) above the buttons.
    `keys` maps bare key names (e.g. "e") to response ids so the dialog can be
    answered without reaching for the mouse.
    `destructive=False` asks about something that isn't a loss (merging a pull
    request), so the confirming button reads as the suggested course rather
    than as a warning.
    `confirm_class` is a CSS class for the dialog, for a caller that wants its
    confirming button in a color of its own — the PR panel's merge questions
    are answered in the merge green their button was pressed in
    (practions.MERGE_CONFIRM_CSS). On the dialog rather than the button because
    AlertDialog exposes no API for its buttons; the rule reaches the one it
    means through the appearance class Adw put there."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    if confirm_class:
        dialog.add_css_class(confirm_class)
    if extra_child is not None:
        dialog.set_extra_child(extra_child)
    dialog.add_response("cancel", _("Cancel"))
    if extra2_label is not None:
        dialog.add_response("extra2", extra2_label)
    if extra_label is not None:
        dialog.add_response("extra", extra_label)
    dialog.add_response("confirm", confirm_label)
    dialog.set_response_appearance(
        "confirm",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
    )
    dialog.set_default_response(default_response)
    dialog.set_close_response("cancel")

    if keys:
        def on_key(_ctrl, keyval, _keycode, state) -> bool:
            if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
                return Gdk.EVENT_PROPAGATE
            response = keys.get(Gdk.keyval_name(Gdk.keyval_to_lower(keyval)) or "")
            if response is None or (response == "extra" and extra_label is None) or (
                response == "extra2" and extra2_label is None
            ):
                return Gdk.EVENT_PROPAGATE
            # Route through close() with the chosen response as the close
            # response: the "response" signal then fires exactly once, through
            # the same path as an Escape dismissal. Emitting the signal
            # directly and then closing would fire a second, spurious "cancel".
            dialog.set_close_response(response)
            dialog.close()
            return Gdk.EVENT_STOP

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", on_key)
        dialog.add_controller(controller)

        # Underline each shortcut's letter in its button so the mapping is
        # visible. AlertDialog exposes no API for its buttons, so once the
        # dialog maps, find each response label by its text and attribute the
        # first occurrence of the key's letter. Purely cosmetic: if a label
        # lacks the letter (translations) or the walk finds nothing, the keys
        # above still work.
        label_for = {"cancel": _("Cancel"), "confirm": confirm_label}
        if extra_label is not None:
            label_for["extra"] = extra_label
        if extra2_label is not None:
            label_for["extra2"] = extra2_label
        wanted: dict[str, int] = {}
        for key, response in keys.items():
            text = label_for.get(response, "")
            index = text.lower().find(key) if len(key) == 1 else -1
            if index >= 0:
                wanted[text] = index

        def underline_labels(widget: Gtk.Widget) -> None:
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Label) and child.get_text() in wanted:
                    text = child.get_text()
                    start = len(text[: wanted[text]].encode())  # byte offsets
                    attr = Pango.attr_underline_new(Pango.Underline.SINGLE)
                    attr.start_index = start
                    attr.end_index = start + len(text[wanted[text]].encode())
                    attrs = Pango.AttrList()
                    attrs.insert(attr)
                    child.set_attributes(attrs)
                underline_labels(child)
                child = child.get_next_sibling()

        dialog.connect("map", lambda *_a: underline_labels(dialog))

    def respond(_dialog, response: str) -> None:
        if response == "confirm":
            on_confirm()
        elif response == "extra" and on_extra is not None:
            on_extra()
        elif response == "extra2" and on_extra2 is not None:
            on_extra2()
        elif on_dismiss is not None:
            on_dismiss()

    dialog.connect("response", respond)
    _present(dialog, parent)


def save_changes_dialog(
    parent: Gtk.Widget,
    body: str,
    on_save: Callable[[], None],
    on_discard: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """The GNOME "Save Changes?" triad — Cancel / Don't Save / Save — asked
    before closing something that holds unsaved editor buffers. Save is the
    suggested default; Cancel (and Escape) aborts the *whole* action that
    asked — closing the tab, backgrounding the session, quitting — never just
    the save part of it."""
    dialog = Adw.AlertDialog(heading=_("Save Changes?"), body=body)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("discard", _("Don't Save"))
    dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.add_response("save", _("Save"))
    dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("save")
    dialog.set_close_response("cancel")

    def respond(_dialog, response: str) -> None:
        if response == "save":
            on_save()
        elif response == "discard":
            on_discard()
        elif on_cancel is not None:
            on_cancel()

    dialog.connect("response", respond)
    _present(dialog, parent)


def progress_dialog(
    parent: Gtk.Widget,
    heading: str,
    body: str,
    dismiss_label: str,
    on_dismiss: Callable[[], None],
) -> Adw.AlertDialog:
    """A one-button notice for work the user has to wait through. The caller
    owns the returned dialog: set `body` as the work advances, and close() it
    when done. `on_dismiss` fires for the button and for Escape alike, so it
    must be safe to call once the caller has already closed the dialog."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("dismiss", dismiss_label)
    dialog.set_default_response("dismiss")
    dialog.set_close_response("dismiss")
    dialog.connect("response", lambda _dialog, _response: on_dismiss())
    _present(dialog, parent)
    return dialog


def info_dialog(parent: Gtk.Widget, heading: str, body: str) -> None:
    """A one-button notice for outcomes that aren't failures (e.g. a repair
    that found nothing to change). Same shape as error_dialog — the split is
    purely so call sites say what they mean."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("ok", _("OK"))
    _present(dialog, parent)


def error_dialog(parent: Gtk.Widget, heading: str, body: str) -> None:
    info_dialog(parent, heading, body)


def trust_folder_dialog(
    parent: Gtk.Widget,
    agent_name: str,
    path: str,
    on_trust: Callable[[], None],
    on_decline: Callable[[], None] | None = None,
    confirm_label: str | None = None,
) -> None:
    """The folder-trust question, asked before the first launch in a project
    Collins has never opened. Declining cancels the launch outright — no tab,
    no project — so this is the one gate between choosing a directory and an
    agent reading and writing inside it.

    Trusting is the suggested response rather than a destructive one: the user
    picked this folder deliberately, and the answer is a grant, not a loss.
    The *keyboard* default stays on Cancel all the same (confirm_dialog's
    default_response), which is the one place this dialog parts company with
    its own styling: granting an agent read/write/execute over a directory
    tree should take a deliberate click, not an Enter that was meant for
    whatever had focus a moment ago. Escape and Enter both decline.
    """
    confirm_dialog(
        parent,
        _("Do you trust this folder?"),
        _(
            "{agent} will be able to read, edit and execute files in\n\n{path}\n\n"
            "and everything inside it, including any worktrees it creates there. "
            "Open it only if this is a project you created or otherwise trust — "
            "like your own code, a well-known open source project, or work from "
            "your team."
        ).format(agent=agent_name, path=display_path(path)),
        confirm_label or _("Trust and open"),
        on_trust,
        on_dismiss=on_decline,
        destructive=False,
    )


def new_session_options_dialog(
    parent: Gtk.Widget, provider, on_start: Callable[[SessionOptions], None]
) -> None:
    """Collect optional CLI flags (model / permission-mode / extra dir) for a new
    session, then hand a SessionOptions to `on_start`."""
    dialog = Adw.AlertDialog(
        heading=_("New {name} session").format(name=provider.name),
        body=_("Optional flags for this session."),
    )
    group = Adw.PreferencesGroup()

    models = provider.session_models()
    model_values = [""] + [v for v, _l in models]
    model_row = Adw.ComboRow(title=_("Model"))
    model_row.set_model(Gtk.StringList.new([_("Default")] + [label for _v, label in models]))
    group.add(model_row)

    modes = provider.permission_modes()
    mode_values = [""] + [v for v, _l in modes]
    mode_row = Adw.ComboRow(title=_("Permission mode"))
    mode_row.set_model(Gtk.StringList.new([_("Default")] + [label for _v, label in modes]))
    group.add(mode_row)

    chosen_dir = {"path": ""}
    if provider.supports_add_dir:
        dir_row = Adw.ActionRow(title=_("Extra directory"), subtitle=_("None"))
        choose = Gtk.Button(label=_("Choose…"), valign=Gtk.Align.CENTER)

        def pick(*_a) -> None:
            fd = Gtk.FileDialog(title=_("Choose a directory"))

            def done(d: Gtk.FileDialog, res) -> None:
                try:
                    folder = d.select_folder_finish(res)
                except GLib.Error:
                    return
                chosen_dir["path"] = folder.get_path()
                dir_row.set_subtitle(folder.get_path())

            fd.select_folder(parent.get_root(), None, done)

        choose.connect("clicked", pick)
        dir_row.add_suffix(choose)
        group.add(dir_row)

    dialog.set_extra_child(group)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("start", _("Start"))
    dialog.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("start")

    def on_response(_d, response: str) -> None:
        if response != "start":
            return
        dirs = (chosen_dir["path"],) if chosen_dir["path"] else ()
        on_start(
            SessionOptions(
                model=model_values[model_row.get_selected()],
                permission_mode=mode_values[mode_row.get_selected()],
                add_dirs=dirs,
            )
        )

    dialog.connect("response", on_response)
    _present(dialog, parent)


# -- MCP servers browser -------------------------------------------------------


def mcp_browser_dialog(parent: Gtk.Widget) -> None:
    config = read_mcp_config()
    page = Adw.PreferencesPage()

    def server_row(name: str, summary: str) -> Adw.ActionRow:
        row = Adw.ActionRow(title=name, subtitle=summary or "—")
        row.set_property("subtitle-lines", 0)
        row.add_css_class("property")
        return row

    if config.is_empty:
        empty = Adw.PreferencesGroup()
        empty.add(Adw.ActionRow(title=_("No MCP servers configured")))
        page.add(empty)
    else:
        if config.global_servers:
            group = Adw.PreferencesGroup(
                title=_("Global"), description=_("Available to every project")
            )
            for server in config.global_servers:
                group.add(server_row(server.name, server.summary))
            page.add(group)
        for path, servers in config.project_servers:
            group = Adw.PreferencesGroup(title=GLib.path_get_basename(path), description=path)
            for server in servers:
                group.add(server_row(server.name, server.summary))
            page.add(group)

    header = Adw.HeaderBar()
    header.set_title_widget(Adw.WindowTitle(title=_("MCP Servers"), subtitle=_("Read-only")))
    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(page)

    dialog = Adw.Dialog(title=_("MCP Servers"))
    dialog.set_content_width(540)
    dialog.set_content_height(600)
    dialog.set_child(view)
    _present(dialog, parent)


# -- session details ----------------------------------------------------------


def details_dialog(parent: Gtk.Widget, session: Session, title: str) -> None:
    provider = get_provider(session.provider)
    group = Adw.PreferencesGroup()
    spinner_row = Adw.ActionRow(title=_("Reading transcript…"))
    spinner = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
    spinner_row.add_suffix(spinner)
    group.add(spinner_row)

    page = Adw.PreferencesPage()
    page.add(group)

    header = Adw.HeaderBar()
    header.set_title_widget(
        Adw.WindowTitle(
            title=title,
            subtitle="Chats" if is_chat_cwd(session.cwd) else session.project_name,
        )
    )
    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(page)

    dialog = Adw.Dialog(title=_("Session details"))
    dialog.set_content_width(480)
    dialog.set_content_height(560)
    dialog.set_child(view)
    _present(dialog, parent)

    def populate(details: SessionDetails, mcp_servers: list[str]) -> bool:
        page.remove(group)
        info = Adw.PreferencesGroup()

        def add(row_title: str, value: str) -> None:
            row = Adw.ActionRow(title=row_title, subtitle=value)
            row.add_css_class("property")
            info.add(row)

        add(_("Agent"), provider.name)
        add(_("Session ID"), session.session_id)
        add(_("Directory"), session.cwd or _("unknown"))
        # Rows the transcript doesn't record are hidden when empty.
        if details.first_timestamp:
            add(_("Created"), format_timestamp(details.first_timestamp))
        if details.last_timestamp:
            add(_("Last activity"), format_timestamp(details.last_timestamp))
        add(_("Messages"), f"{details.user_messages} user · {details.assistant_messages} assistant")
        if details.tool_calls:
            add(_("Tool calls"), str(details.tool_calls))
        if details.models:
            add(_("Models"), ", ".join(details.models))
        if details.input_tokens or details.output_tokens or details.cache_read_tokens:
            add(
                _("Tokens"),
                f"{format_tokens(details.input_tokens)} in · "
                f"{format_tokens(details.output_tokens)} out · "
                f"{format_tokens(details.cache_read_tokens)} cache-read",
            )
        add(_("Transcript size"), format_size(details.file_size))
        page.add(info)

        if mcp_servers or details.mcp_tools:
            mcp = Adw.PreferencesGroup(title=_("MCP"))

            def mcp_row(row_title: str, value: str) -> None:
                row = Adw.ActionRow(title=row_title, subtitle=value)
                row.set_property("subtitle-lines", 0)
                row.add_css_class("property")
                mcp.add(row)

            mcp_row(_("Available to this project"), ", ".join(mcp_servers) or "—")
            used = " · ".join(
                f"{server}: {count}"
                for server, count in sorted(details.mcp_tools.items(), key=lambda kv: -kv[1])
            )
            mcp_row(_("Tools used in this session"), used or "—")
            page.add(mcp)

        if details.messages:
            recent = Adw.PreferencesGroup(title=_("Recent activity"))
            for role, text in details.messages:
                row = Adw.ActionRow(
                    title=_("You") if role == "user" else provider.name,
                    subtitle=text,
                )
                row.set_property("subtitle-lines", 0)  # wrap, no truncation
                row.add_css_class("property")
                recent.add(row)
            page.add(recent)
        return GLib.SOURCE_REMOVE

    def work() -> None:
        details = provider.parse_details(session.jsonl_path)
        # MCP config lives in ~/.claude.json — only meaningful for Claude.
        mcp_servers = configured_mcp_servers(session.cwd) if session.provider == "claude" else []
        GLib.idle_add(populate, details, mcp_servers)

    threading.Thread(target=work, daemon=True).start()


# -- project icon generation --------------------------------------------------

# The adjustments box grows with its text up to this many lines, then scrolls.
_FEEDBACK_MAX_LINES = 5


def generate_icon_dialog(
    parent: Gtk.Widget, cwd: str, project_name: str, on_saved: Callable[[], None]
) -> None:
    """Generate a project-icon.svg for *cwd* and preview it before saving.

    A headless claude run (collins.icongen) starts the moment the dialog
    opens. The result is previewed at dialog size and at the 16px the
    sidebar actually renders; the entry takes adjustment requests, and
    Regenerate re-runs the model with the previous attempt and that feedback
    in the prompt. A model drop-down starts on the Preferences choice
    (icon_model) and changes only this dialog's runs — a stronger model for
    one stubborn project, without re-pointing every future generation.
    Nothing is written until Save — Cancel (or closing the dialog any other
    way) aborts whatever run is in flight. *on_saved* fires after a
    successful save, so the caller can refresh the sidebar.

    With the preference set to None (claudemodels.NO_MODEL — the default)
    nothing runs on open: the dialog opens on a "pick a model" page, the
    drop-down's first item reads "Choose a model…", and the Regenerate
    button, labelled Generate for its first run, stays insensitive until a
    model is picked. Generate is then the same start as the auto-start
    above, and the dialog goes on as it always has. The pick is still this
    dialog's alone — the preference is never written.
    """
    # svg: the latest accepted attempt (what Save writes, what a revision
    # builds on). run: the in-flight generation, if any. gen: a counter so a
    # superseded run's late result is recognized and dropped.
    state: dict = {"svg": None, "run": None, "gen": 0}
    # The preference as the dialog opened. None means the runs are the
    # user's to start: item 0 of the drop-down is then no model at all, and
    # nothing runs while it is selected.
    preferred = (AppState().get_setting("icon_model") or "").strip()
    pick_first = preferred == claudemodels.NO_MODEL

    spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32, halign=Gtk.Align.CENTER)
    loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
    loading_box.append(spinner)
    loading_box.append(Gtk.Label(label=_("Generating icon…")))

    big = Gtk.Image(pixel_size=128, halign=Gtk.Align.CENTER)
    small = Gtk.Image(pixel_size=16, valign=Gtk.Align.CENTER)
    small_caption = Gtk.Label(label=_("At sidebar size"))
    small_caption.add_css_class("dim-label")
    small_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
    small_row.append(small)
    small_row.append(small_caption)
    preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
    preview_box.append(big)
    preview_box.append(small_row)

    # First-attempt failures land here, where the preview would have been.
    failure = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, valign=Gtk.Align.CENTER)
    failure.add_css_class("error")

    # Where the dialog waits, under a preference of None, for a model to be
    # picked and Generate clicked — in the spinner's place.
    pick = Gtk.Label(label=_("Pick a model to generate an icon"), valign=Gtk.Align.CENTER)
    pick.add_css_class("dim-label")

    stack = Gtk.Stack(height_request=200, vexpand=True)
    stack.add_named(loading_box, "loading")
    stack.add_named(preview_box, "preview")
    stack.add_named(failure, "failure")
    stack.add_named(pick, "pick")

    # Adjustments go in a text view that grows with what's typed, up to
    # _FEEDBACK_MAX_LINES, then scrolls: "make it blue" is one line, but a
    # real brief for a stubborn icon often isn't. Enter regenerates and
    # Shift+Enter breaks a line, as in the composer.
    entry = Gtk.TextView(
        wrap_mode=Gtk.WrapMode.WORD_CHAR,
        accepts_tab=False,
        top_margin=8,
        bottom_margin=8,
        left_margin=8,
        right_margin=8,
    )
    entry_buffer = entry.get_buffer()
    placeholder = Gtk.Label(
        label=_("Optional adjustments, e.g. “make it blue”"),
        xalign=0,
        halign=Gtk.Align.START,
        valign=Gtk.Align.START,
        margin_top=8,
        margin_start=8,
        can_target=False,
    )
    placeholder.add_css_class("dim-label")
    entry_buffer.connect(
        "changed", lambda buf: placeholder.set_visible(buf.get_char_count() == 0)
    )
    # The view sits directly in its scroller: as a GtkScrollable it reports
    # its wrapped layout height as its natural height, which is what grows
    # the box; a Viewport between them (an Overlay, say) would measure it
    # unwrapped instead. So the placeholder rides over the scroller.
    entry_scroller = Gtk.ScrolledWindow(child=entry, has_frame=True)
    # EXTERNAL, not AUTOMATIC: a scrollbar's own minimum size would prop
    # the empty box up to three lines. Text past the cap still scrolls by
    # wheel and with the caret, there's just no bar drawn for it.
    entry_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
    entry_scroller.set_propagate_natural_height(True)
    entry_overlay = Gtk.Overlay(child=entry_scroller)
    entry_overlay.add_overlay(placeholder)

    def cap_entry_height(*_a) -> None:
        # The cap is in lines, so it's derived from the view's real font
        # once the view has a Pango context to measure with.
        line_h = entry.create_pango_layout("Xg").get_pixel_size()[1]
        entry_scroller.set_max_content_height(
            line_h * _FEEDBACK_MAX_LINES + entry.get_top_margin() + entry.get_bottom_margin()
        )

    entry.connect("realize", cap_entry_height)

    # Labelled for its first run under None — the click that starts it is
    # the consent the auto-start never asked for — and Regenerate from then on.
    regen = Gtk.Button(
        label=_("Generate") if pick_first else _("Regenerate"),
        sensitive=False,
        halign=Gtk.Align.END,
        hexpand=True,
    )

    # Which model the next run asks for. Item 0 is the Preferences setting
    # (whatever it resolves to at run time), the rest the live catalog, so
    # the first run — started before any list has landed — already honours
    # the preference, and a pick here never writes it back. Under None item
    # 0 is no model: "Choose a model…", and no run can start on it.
    item0 = _("Choose a model…") if pick_first else _("Default model")
    model_ids: list[str] = [""]
    models = Gtk.DropDown.new_from_strings([item0])
    models.set_tooltip_text(_("Model for this dialog's runs; Preferences sets the default"))
    action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    action_row.append(models)
    action_row.append(regen)

    def model_picked() -> bool:
        """Whether the drop-down names something a run can ask for: anything
        at all, unless the preference is None and item 0 is still selected."""
        return not pick_first or models.get_selected() != 0

    def fill_models(catalog: list[claudemodels.ClaudeModel]) -> bool:
        saved = "" if pick_first else (AppState().get_setting("icon_model") or "").strip()
        chosen = model_ids[models.get_selected()]
        ids = [""] + [m.id for m in catalog]
        labels = [item0] + [m.display_name for m in catalog]
        for extra in (saved, chosen):
            if extra and extra not in ids:
                # A saved or picked id the API no longer lists stays
                # offered rather than silently snapping to the default.
                ids.append(extra)
                labels.append(extra)
        if not pick_first:
            # Name what the default resolves to — the saved preference, or
            # with none the same automatic pick icongen makes (the newest
            # Sonnet). Under None there is no default to name.
            resolved = claudemodels.resolve_model(saved, catalog)
            if resolved in ids:
                labels[0] = _("Default ({model})").format(model=labels[ids.index(resolved)])
        model_ids[:] = ids
        models.set_model(Gtk.StringList.new(labels))
        models.set_selected(ids.index(chosen))
        return GLib.SOURCE_REMOVE

    def load_models() -> None:
        catalog = claudemodels.available_models() or list(claudemodels.FALLBACK_MODELS)
        GLib.idle_add(fill_models, catalog)

    # The cached catalog fills the list at once; the worker heals a stale or
    # alias-only list to the live one without blocking the dialog.
    cached = claudemodels.cached_models()
    if cached:
        fill_models(cached)
    threading.Thread(target=load_models, name="icon-models", daemon=True).start()

    # A regenerate that fails after a good attempt keeps the preview (and
    # Save) and reports here instead of on the failure page.
    status = Gtk.Label(wrap=True, xalign=0, visible=False)
    status.add_css_class("error")

    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        margin_top=12,
        margin_bottom=18,
        margin_start=18,
        margin_end=18,
    )
    content.append(stack)
    content.append(entry_overlay)
    content.append(action_row)
    content.append(status)

    cancel = Gtk.Button(label=_("Cancel"))
    save = Gtk.Button(label=_("Save"), sensitive=False)
    save.add_css_class("suggested-action")
    header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
    header.set_title_widget(Adw.WindowTitle(title=_("Generate Icon"), subtitle=project_name))
    header.pack_start(cancel)
    header.pack_end(save)

    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(content)

    dialog = Adw.Dialog(title=_("Generate Icon"))
    # The adjustments box grows as it's typed into, and the dialog has to
    # grow with it — which a dialog only does when it follows its content's
    # natural size; the width is then the content's to pin.
    dialog.set_follows_content_size(True)
    content.set_size_request(384, -1)  # 420 with the margins
    dialog.set_child(view)

    def start() -> None:
        if not model_picked():
            return  # nothing to run on; the button is insensitive anyway
        state["gen"] += 1
        gen = state["gen"]
        if state["run"] is not None:
            state["run"].cancel()
        run = icongen.IconRun()
        state["run"] = run
        regen.set_label(_("Regenerate"))
        regen.set_sensitive(False)
        save.set_sensitive(False)
        status.set_visible(False)
        stack.set_visible_child_name("loading")
        feedback = entry_buffer.get_text(
            entry_buffer.get_start_iter(), entry_buffer.get_end_iter(), False
        )
        previous = state["svg"]
        model = model_ids[models.get_selected()] or None

        def work() -> None:
            try:
                prompt = icongen.build_prompt(
                    cwd, project_name, feedback=feedback, previous_svg=previous
                )
                svg = run.run(prompt, model=model)
            except icongen.IconGenCancelled:
                return
            except Exception as err:  # IconGenError, OSError, ...
                GLib.idle_add(fail, gen, str(err))
            else:
                GLib.idle_add(land, gen, svg)

        threading.Thread(target=work, name="icon-gen", daemon=True).start()

    def land(gen: int, svg: bytes) -> bool:
        if gen != state["gen"]:
            return GLib.SOURCE_REMOVE
        texture = svg_texture(svg, 128)
        if texture is None:
            return fail(gen, _("the generated SVG could not be rendered"))
        state["svg"] = svg
        state["run"] = None
        big.set_from_paintable(texture)
        small.set_from_paintable(svg_texture(svg, 16))
        stack.set_visible_child_name("preview")
        regen.set_sensitive(model_picked())
        save.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def fail(gen: int, message: str) -> bool:
        if gen != state["gen"]:
            return GLib.SOURCE_REMOVE
        state["run"] = None
        text = _("Icon generation failed: {error}").format(error=message)
        if state["svg"] is None:
            failure.set_label(text)
            stack.set_visible_child_name("failure")
        else:
            status.set_label(text)
            status.set_visible(True)
            stack.set_visible_child_name("preview")
            save.set_sensitive(True)
        regen.set_sensitive(model_picked())
        return GLib.SOURCE_REMOVE

    def on_save(*_a) -> None:
        svg = state["svg"]
        if svg is None:
            return
        try:
            icongen.save_icon(cwd, svg)
        except OSError as err:
            status.set_label(_("Saving failed: {error}").format(error=err))
            status.set_visible(True)
            return
        dialog.close()
        on_saved()

    cancel.connect("clicked", lambda *_a: dialog.close())
    save.connect("clicked", on_save)
    regen.connect("clicked", lambda *_a: start())
    # The button follows the pick while no run is in flight: under None it
    # wakes on the first real model and sleeps again on "Choose a model…".
    # A repopulate re-selects the same item, so this is a no-op there.
    models.connect(
        "notify::selected",
        lambda *_a: regen.set_sensitive(state["run"] is None and model_picked()),
    )

    def on_entry_key(_ctrl, keyval: int, _keycode: int, modifiers) -> bool:
        action = composerkeys.enter_action(int(keyval), int(modifiers), True)
        if action == composerkeys.SEND:
            if regen.get_sensitive():
                start()
            return Gdk.EVENT_STOP
        if action == composerkeys.NEWLINE:
            entry_buffer.insert_at_cursor("\n")
            entry.scroll_to_mark(entry_buffer.get_insert(), 0.0, False, 0.0, 0.0)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    entry_keys = Gtk.EventControllerKey()
    entry_keys.connect("key-pressed", on_entry_key)
    entry.add_controller(entry_keys)
    # Closing by any route (Cancel, Esc, the close after a save) kills
    # whatever run is still burning tokens; cancelling a finished or absent
    # run is a no-op.
    dialog.connect("closed", lambda *_a: state["run"] and state["run"].cancel())

    _present(dialog, parent)
    if pick_first:
        # Nothing runs until a model is picked, so the pick is what gets
        # focus; Enter in the entry regenerates, and start() refuses too.
        stack.set_visible_child_name("pick")
        dialog.set_focus(models)
        return
    dialog.set_focus(entry)
    start()
