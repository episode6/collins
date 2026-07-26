# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.

"""Reusable dialogs, kept out of the main window."""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .formatting import format_size, format_timestamp, format_tokens
from .i18n import _
from .providers import SessionOptions, get_provider
from .sessions import (
    Session,
    SessionDetails,
    configured_mcp_servers,
    read_mcp_config,
)


def rename_dialog(parent: Gtk.Widget, body: str, current: str, on_save: Callable[[str], None]) -> None:
    dialog = Adw.AlertDialog(heading=_("Rename session"), body=body)
    entry = Gtk.Entry(text=current, placeholder_text=_("Custom name"))
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
    dialog.present(parent)


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
    dialog.present(parent)


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
) -> None:
    """Two-button confirmation (Cancel + a destructive `confirm_label`).
    `extra_label`/`on_extra` add a third, non-destructive choice between them."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", _("Cancel"))
    if extra_label is not None:
        dialog.add_response("extra", extra_label)
    dialog.add_response("confirm", confirm_label)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response(default_response)
    dialog.set_close_response("cancel")

    def respond(_dialog, response: str) -> None:
        if response == "confirm":
            on_confirm()
        elif response == "extra" and on_extra is not None:
            on_extra()
        elif on_dismiss is not None:
            on_dismiss()

    dialog.connect("response", respond)
    dialog.present(parent)


def error_dialog(parent: Gtk.Widget, heading: str, body: str) -> None:
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("ok", _("OK"))
    dialog.present(parent)


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
    dialog.present(parent)


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
    dialog.present(parent)


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
    header.set_title_widget(Adw.WindowTitle(title=title, subtitle=session.project_name))
    view = Adw.ToolbarView()
    view.add_top_bar(header)
    view.set_content(page)

    dialog = Adw.Dialog(title=_("Session details"))
    dialog.set_content_width(480)
    dialog.set_content_height(560)
    dialog.set_child(view)
    dialog.present(parent)

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
