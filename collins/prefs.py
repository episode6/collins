# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-21. Full change history: git log for this file.

"""Preferences dialog: terminal font, scrollback, color scheme."""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import (  # noqa: E402
    apppicker,
    claudemodels,
    clisetup,
    cliwelcome,
    composerkeys,
    editor,
    footerapps,
    formatting,
    mcptools,
    prefssearch,
    statusicon,
)
from .caffeine import DURATION_KEYS, INDEFINITE, duration_label, grace_seconds
from .i18n import LANGUAGES, N_, _, ngettext
from .state import AppState
from .themes import DEFAULT_THEME, THEME_NAMES, get_theme


def _hex_rgb(hex6: str) -> tuple[float, float, float]:
    return tuple(int(hex6[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _draw_swatch(_area, cr, width: int, height: int, name: str) -> None:
    theme = get_theme(name)
    if theme is None:  # "Default" — neutral placeholder
        cr.set_source_rgb(0.55, 0.55, 0.55)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        return
    r, g, b = _hex_rgb(theme["bg"])
    cr.set_source_rgb(r, g, b)
    cr.rectangle(0, 0, width, height)
    cr.fill()
    # foreground swatch, then the six accent colours (red…cyan)
    r, g, b = _hex_rgb(theme["fg"])
    cr.set_source_rgb(r, g, b)
    cr.rectangle(6, 4, 10, height - 8)
    cr.fill()
    sw, gap = 13, 3
    x = width - len([1, 2, 3, 4, 5, 6]) * (sw + gap)
    for i in (1, 2, 3, 4, 5, 6):
        r, g, b = _hex_rgb(theme["palette"][i])
        cr.set_source_rgb(r, g, b)
        cr.rectangle(x, 4, sw, height - 8)
        cr.fill()
        x += sw + gap


def _theme_swatch(name: str) -> Gtk.DrawingArea:
    area = Gtk.DrawingArea()
    area.set_content_width(130)
    area.set_content_height(22)
    area.set_valign(Gtk.Align.CENTER)
    area.set_draw_func(_draw_swatch, name)
    return area

_SCHEMES = [
    ("system", N_("Follow system"), Adw.ColorScheme.DEFAULT),
    ("light", N_("Light"), Adw.ColorScheme.FORCE_LIGHT),
    ("dark", N_("Dark"), Adw.ColorScheme.FORCE_DARK),
]

# What a new session opens its composer as (composerkeys.AUTOSHOW_MODES, in
# the order the drop-down offers them). The labels stay short on purpose: a
# ComboRow's selected value gets only what its subtitle leaves — under 100px
# here — and ellipsizes past that, so what each one means is spelled out in
# the subtitle instead. Translations have to keep both ends short: German's
# "Angedockt" came back cut until its subtitle lost a clause.
_COMPOSER_AUTOSHOW = [
    (composerkeys.OFF, N_("Never")),
    (composerkeys.FLOAT, N_("Floating")),
    (composerkeys.DOCK, N_("Docked")),
]

# What closing a running session's tab does when a setting stands in for the
# confirmation dialog. The labels match the dialog buttons they replace.
_RUNNING_BEHAVIORS = [
    ("ask", N_("Ask")),
    ("exit", N_("Exit Session")),
    ("background", N_("Background Session")),
]

# Closing a whole window has a fourth answer the tab close doesn't: keep
# every session exactly as it is and just hide the window (the dialog's
# "Keep Running (Hide Window)"). Shortened here because a ComboRow's value
# label ellipsizes past ~130px.
_QUIT_BEHAVIORS = _RUNNING_BEHAVIORS + [("hide", N_("Hide Window"))]

# User-facing names for the tools Collins offers the sessions it starts, by
# tool name (see mcptools.TOOLS). The table's own descriptions are written
# for the agent and left untranslated by design, so the switches get labels
# of their own: what the user gives up by turning one off, said in their
# language. The tool's real name goes in the subtitle — it is what shows up
# in the session's /mcp list and in its permission prompts.
_MCP_TOOL_LABELS = {
    "set_session_title": (
        N_("Name its own session"),
        N_("set_session_title — the session titles its own tab and sidebar row"),
    ),
    "open_in_editor": (
        N_("Open files in the editor"),
        N_("open_in_editor — put a file from the project on screen, at a line"),
    ),
    "show_image": (
        N_("Show images"),
        # The URL half is disclosure, not detail: it is the only tool that
        # sends Collins to an address the agent chose (attach_pr reaches the
        # network too, but only to ask gh about a GitHub PR).
        N_("show_image — a screenshot, plot, render, or image URL in the in-app lightbox"),
    ),
    "notify_user": (
        N_("Send desktop notifications"),
        N_("notify_user — a notification titled with the session; clicking it opens the tab"),
    ),
    "attach_pr": (
        N_("Attach pull requests"),
        N_("attach_pr — put a pull request on the session's own footer and sidebar row"),
    ),
    "start_session": (
        N_("Start sessions in the background"),
        N_("start_session — spawn a sibling agent in a new background tab, with a prompt"),
    ),
    "read_terminal": (
        N_("Read the terminal panel"),
        # "everything you typed" is the disclosure: the dump carries the
        # user's own input, echoed passwords included, not just output.
        N_("read_terminal — the panel tabs' text and scrollback, your own typing included"),
    ),
    "run_in_terminal": (
        N_("Run commands in the terminal panel"),
        N_("run_in_terminal — type a command into an idle panel tab (or a new one) and run it"),
    ),
}


def apply_color_scheme(value: str) -> None:
    for key, _label, scheme in _SCHEMES:
        if key == value:
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            return


# -- search ------------------------------------------------------------------
#
# GTK hands out no way to walk a preferences group's rows or a page's groups
# back out again, so the two containers below remember what was put into them.
# Everything the search bar hides is hidden through these lists.


class _SearchableGroup(Adw.PreferencesGroup):
    """A preferences group that remembers the rows added to it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.rows: list[Gtk.Widget] = []

    def add(self, row: Gtk.Widget) -> None:
        super().add(row)
        self.rows.append(row)

    def remove(self, row: Gtk.Widget) -> None:
        super().remove(row)
        if row in self.rows:
            self.rows.remove(row)


class _SearchablePage(Adw.PreferencesPage):
    """A preferences page that remembers the groups added to it."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.groups: list[_SearchableGroup] = []

    def add(self, group: _SearchableGroup) -> None:
        super().add(group)
        self.groups.append(group)


def _searchable(widget: Gtk.Widget, *terms: str) -> Gtk.Widget:
    """Give *widget* extra words to match, beyond its own title and subtitle.

    The options folded away inside an expander or a dropdown — "Dracula",
    "Magyar", "Background Session" — are exactly what someone searches for,
    and none of them are in the row's own text. The same goes for a group
    whose only control sits in its header suffix: "Add application…" is on a
    button beside the Footer apps heading, not in any row.
    """
    widget.search_terms = " ".join(terms)
    return widget


def _row_text(row: Gtk.Widget) -> str:
    get_subtitle = getattr(row, "get_subtitle", None)
    return " ".join(
        (
            row.get_title() or "",
            (get_subtitle() or "") if get_subtitle is not None else "",
            getattr(row, "search_terms", ""),
        )
    )


def _group_text(group: _SearchableGroup) -> str:
    return " ".join(
        (
            group.get_title() or "",
            group.get_description() or "",
            getattr(group, "search_terms", ""),
        )
    )


class PreferencesDialog(Adw.Dialog):
    """on_change() is called after any setting is saved, so the window can
    push the new settings into open terminal tabs.

    An Adw.Dialog wrapping the page by hand rather than an
    Adw.PreferencesDialog: the search bar has to stay pinned above the
    settings while they scroll, which takes a toolbar of our own. Adwaita's
    built-in preferences search hides behind a header button and answers on a
    separate results page, and it matches neither group titles ("Terminal")
    nor the options inside an expander ("Dracula").
    """

    def __init__(self, state: AppState, on_change: Callable[[], None]) -> None:
        # A fixed size, unlike Adw.PreferencesDialog's grow-to-fit-the-page:
        # the dialog has to hold still while filtering empties it out, or it
        # would resize under the pointer on every keystroke. Small windows
        # still clamp it down.
        super().__init__(title=_("Preferences"), content_width=640, content_height=700)
        self._state = state
        self._on_change = on_change

        self._search_entry = Gtk.SearchEntry(placeholder_text=_("Search settings…"), hexpand=True)
        # A name of its own for screen readers: placeholder text is announced
        # unreliably at best, and it is gone the moment anyone types.
        self._search_entry.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Search settings")]
        )
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("stop-search", self._on_search_stopped)

        page = _SearchablePage(title=_("Preferences"), icon_name="preferences-system-symbolic")
        self._page = page

        # General: what Collins runs, what it looks like, what the sidebar
        # shows. The CLI row leads — the answer to "which claude is Collins
        # running?" shouldn't take scrolling to find.
        general_group = _SearchableGroup(title=_("General"))
        self._build_cli_rows(state, general_group)
        current_lang = state.get_setting("language") or ""
        self._initial_lang = current_lang
        current_label = next(
            (label for code, label in LANGUAGES if code == current_lang), LANGUAGES[0][1]
        )
        # Restart now rides the row as a suffix, shown once the choice
        # differs from the language this run started in.
        self._restart_btn = Gtk.Button(label=_("Restart now"), valign=Gtk.Align.CENTER)
        self._restart_btn.add_css_class("suggested-action")
        self._restart_btn.set_visible(False)
        self._restart_btn.connect("clicked", self._on_restart)
        self._lang_expander = Adw.ExpanderRow(title=_("Language"), subtitle=current_label)
        self._lang_expander.add_suffix(self._restart_btn)
        lang_radio_group = None
        for code, label in LANGUAGES:
            row = Adw.ActionRow(title=label)
            radio = Gtk.CheckButton()
            if lang_radio_group is None:
                lang_radio_group = radio
            else:
                radio.set_group(lang_radio_group)
            radio.set_active(code == current_lang)
            radio.connect("toggled", self._on_language_radio, code, label)
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            self._lang_expander.add_row(row)
        general_group.add(
            _searchable(
                self._lang_expander, _("Restart now"), *(label for _c, label in LANGUAGES)
            )
        )
        scheme_row = Adw.ComboRow(title=_("Dark / Light Mode"))
        scheme_labels = [_(label) for _k, label, _s in _SCHEMES]
        scheme_row.set_model(Gtk.StringList.new(scheme_labels))
        current_scheme = state.get_setting("color_scheme") or "system"
        scheme_row.set_selected(
            next((i for i, (k, _l, _s) in enumerate(_SCHEMES) if k == current_scheme), 0)
        )
        scheme_row.connect("notify::selected", self._on_scheme_changed)
        general_group.add(_searchable(scheme_row, *scheme_labels))
        self._build_status_icon_row(state, general_group)
        self._tab_drag_row = Adw.SwitchRow(
            title=_("Tab drag handles"),
            subtitle=_(
                "Drag any panel tab by its handle to move, reorder, or "
                "split it. Relies on GTK internals — turn off to fall back "
                "to plain tab dragging plus a drag grip on each panel"
            ),
        )
        self._tab_drag_row.set_active(bool(state.get_setting("panel_tab_drag_handles")))
        self._tab_drag_row.connect("notify::active", self._on_tab_drag_changed)
        general_group.add(self._tab_drag_row)
        self._folder_path_row = Adw.SwitchRow(
            title=_("Show folder path"),
            subtitle=_("Show each session's project folder path in the sidebar"),
        )
        self._folder_path_row.set_active(bool(state.get_setting("show_folder_path")))
        self._folder_path_row.connect("notify::active", self._on_folder_path_changed)
        general_group.add(self._folder_path_row)
        icon_size_row = Adw.SpinRow.new_with_range(16, 32, 2)
        icon_size_row.set_title(_("Project icon size"))
        icon_size_row.set_subtitle(_("Size of the project and folder icons in the sidebar"))
        icon_size_row.set_value(int(state.get_setting("project_icon_size") or 16))
        icon_size_row.connect("notify::value", self._on_icon_size_changed)
        general_group.add(icon_size_row)
        self._usage_panel_row = Adw.SwitchRow(
            title=_("Show Claude usage"),
            subtitle=_("Show subscription usage limits below the session list"),
        )
        self._usage_panel_row.set_active(bool(state.get_setting("show_usage_panel")))
        self._usage_panel_row.connect("notify::active", self._on_usage_panel_changed)
        general_group.add(self._usage_panel_row)
        self._model_rows: dict[str, Adw.ComboRow] = {}
        self._model_default_labels: dict[str, str] = {}
        # Per key rather than bound into each row's handler: the list is
        # applied more than once (saved, queried, refreshed) and a handler
        # holding an older one would save the wrong id (see _apply_model_rows).
        self._model_ids: dict[str, list[str]] = {}
        self._model_handlers: dict[str, int] = {}
        for key, title, subtitle, default_label in (
            (
                "title_model",
                _("Session title model"),
                _("Model that summarizes each new session's first prompt into its name"),
                _("Default (latest Haiku)"),
            ),
            (
                "icon_model",
                _("Icon generation model"),
                _("Model that designs project icons in the sidebar's Generate Icon dialog"),
                _("Default (latest Sonnet)"),
            ),
        ):
            row = Adw.ComboRow(title=title, subtitle=subtitle)
            # A placeholder until the live list lands (see _populate_model_rows).
            row.set_model(Gtk.StringList.new([default_label]))
            self._model_rows[key] = row
            self._model_default_labels[key] = default_label
            _searchable(row, "haiku", "sonnet", "opus")
        # The list is cached for a day and across restarts (claudemodels), so
        # a model released this morning would otherwise not be offered until
        # tomorrow. This row says how old the list is and asks for a new one.
        self._model_status_row = Adw.ActionRow(
            title=_("Model list"), subtitle=_("Checking…")
        )
        self._model_refresh_btn = Gtk.Button(label=_("Refresh"), valign=Gtk.Align.CENTER)
        self._model_refresh_btn.add_css_class("flat")
        self._model_refresh_btn.set_tooltip_text(
            _("Ask Anthropic for the model list now, rather than waiting for the saved one to age out")
        )
        self._model_refresh_btn.connect("clicked", self._on_refresh_models)
        self._model_status_row.add_suffix(self._model_refresh_btn)
        _searchable(self._model_status_row, _("Refresh"), "models api")
        self._populate_model_rows()
        general_group.add(self._model_rows["icon_model"])
        general_group.add(self._model_status_row)
        page.add(general_group)

        sessions_group = _SearchableGroup(title=_("Session behavior"))
        self._restore_session_row = Adw.SwitchRow(
            title=_("Reopen the last session"),
            subtitle=_(
                "Open the session that was active when the app was last "
                "closed. Off, the app launches with no session open"
            ),
        )
        self._restore_session_row.set_active(bool(state.get_setting("restore_last_session")))
        self._restore_session_row.connect("notify::active", self._on_restore_session_changed)
        sessions_group.add(
            _searchable(self._restore_session_row, "launch", "restore", "resume", "startup")
        )
        self._worktree_row = Adw.SwitchRow(
            title=_("Start new sessions in a git worktree"),
            subtitle=_(
                "Git projects only; each new session works in its own fresh "
                "worktree, so it won't see uncommitted local changes. "
                "Right-click a project header to override per project"
            ),
        )
        self._worktree_row.set_active(bool(state.get_setting("worktree_new_sessions")))
        self._worktree_row.connect("notify::active", self._on_worktree_changed)
        sessions_group.add(self._worktree_row)
        self._auto_title_row = Adw.SwitchRow(
            title=_("Auto-generate session titles"),
            subtitle=_(
                "Summarize each new session's first prompt into a short title "
                "using the claude CLI; pre-existing sessions are titled "
                "locally from their prompt"
            ),
        )
        self._auto_title_row.set_active(bool(state.get_setting("auto_title_sessions")))
        self._auto_title_row.connect("notify::active", self._on_auto_title_changed)
        sessions_group.add(self._auto_title_row)
        sessions_group.add(self._model_rows["title_model"])
        self._cli_title_row = Adw.SwitchRow(
            title=_("Follow Claude's own session names"),
            subtitle=_(
                "Rename sessions whenever Claude names or renames them — "
                "/rename and its automatic titles; manually renamed "
                "sessions keep their name"
            ),
        )
        self._cli_title_row.set_active(bool(state.get_setting("cli_title_sessions")))
        self._cli_title_row.connect("notify::active", self._on_cli_title_changed)
        sessions_group.add(_searchable(self._cli_title_row, "rename", "cli", "title"))
        self._add_running_behavior_row(
            sessions_group,
            _("When archiving a running session"),
            _("Archiving a session that is still running also closes its tab"),
            "archive_running_session",
        )
        self._quit_behavior_row = self._add_running_behavior_row(
            sessions_group,
            _("When quitting with running sessions"),
            _("Closing a window while agent sessions are still running"),
            "quit_with_running_sessions",
            behaviors=_QUIT_BEHAVIORS,
        )
        # Hide Window works without a status icon (relaunching or clicking a
        # notification brings the window back), but the row should say what
        # the desktop can't show. Seeded from the status-icon row's answer,
        # kept live by the same availability watch that row runs.
        self._sync_quit_behavior_subtitle(self._status_icon_host_seed)
        self._remote_archive_row = Adw.SwitchRow(
            title=_("Archive on claude.ai too"),
            subtitle=_(
                "A session that also appears on claude.ai is archived and "
                "restored there along with the toggle here; best-effort, "
                "archiving locally never waits on it"
            ),
        )
        self._remote_archive_row.set_active(bool(state.get_setting("archive_on_claude_ai")))
        self._remote_archive_row.connect("notify::active", self._on_remote_archive_changed)
        sessions_group.add(
            _searchable(self._remote_archive_row, "claude.ai", "remote", "web", "sync")
        )
        self._attach_autodock_row = Adw.SwitchRow(
            title=_("Show the attachments panel automatically"),
            subtitle=_(
                "Dock a session's attachments panel beside it the first "
                "time it shows an image — only in a tab wide enough to "
                "spare the column, past the terminal's maximum width. Once "
                "per session tab, so one you close again stays closed"
            ),
        )
        self._attach_autodock_row.set_active(
            bool(state.get_setting("dock_attachments_when_room"))
        )
        self._attach_autodock_row.connect("notify::active", self._on_attach_autodock_changed)
        sessions_group.add(
            _searchable(self._attach_autodock_row, "images", "gallery", "dock")
        )
        self._bg_poll_row = Adw.SwitchRow(
            title=_("Poll for background sessions"),
            subtitle=_(
                "Fallback: check the agent CLI every 20 seconds in case the "
                "yellow guide lines stop updating on their own"
            ),
        )
        self._bg_poll_row.set_active(bool(state.get_setting("background_status_poll")))
        self._bg_poll_row.connect("notify::active", self._on_bg_poll_changed)
        sessions_group.add(self._bg_poll_row)
        self._progress_termprop_row = Adw.SwitchRow(
            title=_("Exact busy tracking from the agent"),
            subtitle=_(
                "Read Claude Code's own progress announcements for the "
                "sidebar's working indicator, instead of only inferring from "
                "terminal output (fully applies to newly opened tabs)"
            ),
        )
        self._progress_termprop_row.set_active(bool(state.get_setting("progress_termprop")))
        self._progress_termprop_row.connect("notify::active", self._on_progress_termprop_changed)
        sessions_group.add(self._progress_termprop_row)
        page.add(sessions_group)

        composer_group = _SearchableGroup(title=_("Composer"))
        self._composer_autoshow_row = Adw.ComboRow(
            title=_("Composer in new sessions"),
            subtitle=_(
                "Open the composer as soon as a new session starts — floating "
                "over the agent terminal, or docked as a panel below it, where "
                "it stays for the session's later visits"
            ),
        )
        autoshow_labels = [_(label) for _v, label in _COMPOSER_AUTOSHOW]
        self._composer_autoshow_row.set_model(Gtk.StringList.new(autoshow_labels))
        autoshow_values = [value for value, _l in _COMPOSER_AUTOSHOW]
        self._composer_autoshow_row.set_selected(
            autoshow_values.index(
                composerkeys.autoshow_mode(state.get_setting("composer_new_sessions"))
            )
        )
        self._composer_autoshow_row.connect(
            "notify::selected", self._on_composer_autoshow_changed
        )
        composer_group.add(_searchable(self._composer_autoshow_row, *autoshow_labels))
        self._composer_typing_row = Adw.SwitchRow(
            title=_("Typing opens the composer"),
            subtitle=_(
                "Start typing at an agent's empty prompt and the composer "
                "opens with what you typed. A dialog, a menu and the CLI's "
                "own /, !, # and @ keep their keys"
            ),
        )
        self._composer_typing_row.set_active(bool(state.get_setting("composer_on_typing")))
        self._composer_typing_row.connect("notify::active", self._on_composer_typing_changed)
        composer_group.add(self._composer_typing_row)
        self._attach_overlay_row = Adw.SwitchRow(
            title=_("Floating composer button"),
            subtitle=_(
                "Overlay a semi-transparent button on the corner of each "
                "agent terminal that opens the composer, a spell-checked "
                "prompt box"
            ),
        )
        self._attach_overlay_row.set_active(bool(state.get_setting("attach_overlay_button")))
        self._attach_overlay_row.connect("notify::active", self._on_attach_overlay_changed)
        composer_group.add(self._attach_overlay_row)
        self._composer_enter_row = Adw.SwitchRow(
            title=_("Enter sends composer text"),
            subtitle=_(
                "Off: Enter inserts a newline and Ctrl+Enter sends. "
                "Shift+Enter always inserts a newline"
            ),
        )
        self._composer_enter_row.set_active(
            bool(state.get_setting("composer_enter_sends"))
        )
        self._composer_enter_row.connect("notify::active", self._on_composer_enter_changed)
        composer_group.add(self._composer_enter_row)
        self._spell_click_row = Adw.SwitchRow(
            title=_("Right-click aims spell-check"),
            subtitle=_(
                "Right-clicking a misspelled word in the composer offers "
                "corrections for that word. Off: corrections follow the "
                "text cursor instead, and a right-click never moves it"
            ),
        )
        self._spell_click_row.set_active(bool(state.get_setting("composer_spell_click")))
        self._spell_click_row.connect("notify::active", self._on_spell_click_changed)
        composer_group.add(self._spell_click_row)
        page.add(composer_group)

        terminal_group = _SearchableGroup(title=_("Terminal"))
        current_theme = state.get_setting("terminal_theme") or DEFAULT_THEME
        if current_theme not in THEME_NAMES:
            current_theme = DEFAULT_THEME
        self._theme_expander = Adw.ExpanderRow(title=_("Color theme"), subtitle=current_theme)
        radio_group = None
        for name in THEME_NAMES:
            row = Adw.ActionRow(title=name)
            radio = Gtk.CheckButton()
            if radio_group is None:
                radio_group = radio
            else:
                radio.set_group(radio_group)
            radio.set_active(name == current_theme)
            radio.connect("toggled", self._on_theme_radio, name)
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            row.add_suffix(_theme_swatch(name))
            self._theme_expander.add_row(row)
        terminal_group.add(_searchable(self._theme_expander, *THEME_NAMES))

        font_row = Adw.ActionRow(title=_("Font"), subtitle=_("Applies to all terminal tabs"))
        self._font_button = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER)
        current_font = state.get_setting("font") or ""
        if current_font:
            self._font_button.set_font_desc(Pango.FontDescription.from_string(current_font))
        self._font_button.connect("notify::font-desc", self._on_font_changed)
        font_row.add_suffix(self._font_button)

        reset_font = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER)
        reset_font.add_css_class("flat")
        reset_font.set_tooltip_text(_("Reset to default font"))
        reset_font.connect("clicked", self._on_font_reset)
        font_row.add_suffix(reset_font)
        terminal_group.add(font_row)

        scroll_row = Adw.SpinRow.new_with_range(1_000, 1_000_000, 1_000)
        scroll_row.set_title(_("Scrollback lines"))
        scroll_row.set_value(int(state.get_setting("scrollback") or 10_000))
        scroll_row.connect("notify::value", self._on_scrollback_changed)
        terminal_group.add(scroll_row)

        max_width_row = Adw.SpinRow.new_with_range(0, 6_000, 20)
        max_width_row.set_title(_("Max width"))
        max_width_row.set_subtitle(
            _("Stop growing past this width and center in the tab instead (0 = no limit)")
        )
        max_width_row.set_value(int(state.get_setting("terminal_max_width") or 0))
        max_width_row.connect("notify::value", self._on_terminal_max_width_changed)
        terminal_group.add(max_width_row)

        # use_markup off, and set before the title: the bare "&" in the title
        # is not valid Pango markup.
        self._easy_copy_row = Adw.SwitchRow(use_markup=False)
        self._easy_copy_row.set_title(_("Easy copy & paste"))
        self._easy_copy_row.set_subtitle(
            _(
                "Ctrl+C copies selected text (otherwise interrupts as usual), "
                "Ctrl+V pastes, and right-click opens a copy/paste menu"
            )
        )
        self._easy_copy_row.set_active(bool(state.get_setting("easy_copy_paste")))
        self._easy_copy_row.connect("notify::active", self._on_easy_copy_changed)
        terminal_group.add(self._easy_copy_row)
        page.add(terminal_group)

        self._footer_apps_group = _SearchableGroup(
            title=_("Footer apps"),
            description=_("Buttons in each tab's footer that open the tab's directory"),
        )
        add_app_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_app_btn.add_css_class("flat")
        add_app_btn.set_tooltip_text(_("Add application…"))
        add_app_btn.connect("clicked", self._on_add_footer_app)
        self._footer_apps_group.set_header_suffix(add_app_btn)
        _searchable(self._footer_apps_group, _("Add application…"))
        self._footer_app_rows: list[Adw.PreferencesRow] = []
        self._rebuild_footer_apps()
        page.add(self._footer_apps_group)

        self._build_editor_group(state, page)

        pr_group = _SearchableGroup(title=_("Pull requests"))
        pr_scale_row = Adw.SpinRow.new_with_range(50, 300, 5)
        pr_scale_row.set_title(_("Text size"))
        pr_scale_row.set_subtitle(
            _(
                "Reading-text size in the pull request panel, as a percentage "
                "of the app font; buttons and menus keep the app size"
            )
        )
        pr_scale_row.set_value(int(state.get_setting("pr_font_scale") or 100))
        pr_scale_row.connect("notify::value", self._on_pr_font_scale_changed)
        pr_group.add(pr_scale_row)
        # Not "Show images": the MCP-tools group already has a row by
        # that name (the show_image tool), and one Preferences window can't
        # carry two of them.
        self._pr_images_row = Adw.SwitchRow(
            title=_("Show embedded images"),
            subtitle=_(
                "Render the images a description or comment embeds, and the "
                "changed image files, as pictures; click one to open it full "
                "size. Off, they stay links and patches, and opening a pull "
                "request downloads nothing"
            ),
        )
        self._pr_images_row.set_active(bool(state.get_setting("pr_inline_images")))
        self._pr_images_row.connect("notify::active", self._on_pr_images_changed)
        pr_group.add(self._pr_images_row)
        self._pr_autoshow_row = Adw.SwitchRow(
            title=_("Open new pull requests automatically"),
            subtitle=_(
                "Open a pull request's panel beside its session as soon as "
                "the session picks the PR up. Once per pull request, so one "
                "you close again stays closed"
            ),
        )
        self._pr_autoshow_row.set_active(bool(state.get_setting("open_pr_panel_on_attach")))
        self._pr_autoshow_row.connect("notify::active", self._on_pr_autoshow_changed)
        pr_group.add(_searchable(self._pr_autoshow_row, "attach", "dock"))
        self._confirm_merges_row = Adw.SwitchRow(
            title=_("Confirm before merging"),
            subtitle=_(
                "Ask before merging a pull request, enabling auto-merge, or "
                "merging and archiving the session. Off, the click merges; "
                "closing a pull request unmerged still asks either way"
            ),
        )
        self._confirm_merges_row.set_active(bool(state.get_setting("confirm_merges")))
        self._confirm_merges_row.connect("notify::active", self._on_confirm_merges_changed)
        pr_group.add(_searchable(self._confirm_merges_row, "merge", "archive", "dialog"))
        self._attach_prompt_prs_row = Adw.SwitchRow(
            title=_("Attach pull requests named in prompts"),
            subtitle=_(
                "Put every pull request a new session's first prompt "
                "mentions on that session's row, without waiting for the "
                "agent to touch it"
            ),
        )
        self._attach_prompt_prs_row.set_active(bool(state.get_setting("attach_prompt_prs")))
        self._attach_prompt_prs_row.connect("notify::active", self._on_attach_prompt_prs_changed)
        pr_group.add(self._attach_prompt_prs_row)
        self._pr_title_row = Adw.SwitchRow(
            title=_("Rename sessions after their pull requests"),
            subtitle=_(
                "Retitle a session to match the newest pull request opened "
                "in it; manually renamed sessions keep their name"
            ),
        )
        self._pr_title_row.set_active(bool(state.get_setting("pr_title_sessions")))
        self._pr_title_row.connect("notify::active", self._on_pr_title_changed)
        pr_group.add(self._pr_title_row)
        self._pr_launch_row = Adw.SwitchRow(
            title=_("Refresh pull requests at launch"),
            subtitle=_(
                "Ask GitHub about every listed session's pull requests once on "
                "startup, so the marks in the sidebar start out current rather "
                "than as they were left"
            ),
        )
        self._pr_launch_row.set_active(bool(state.get_setting("refresh_prs_on_launch")))
        self._pr_launch_row.connect("notify::active", self._on_pr_launch_changed)
        pr_group.add(self._pr_launch_row)
        page.add(pr_group)

        caffeine_group = _SearchableGroup(title=_("Caffeine Mode"))
        self._caffeine_screen_row = Adw.SwitchRow(
            title=_("Keep screen on"),
            subtitle=_(
                "Hold the screen on as well as keeping the computer awake. "
                "Off lets the screen turn off as usual, while an unattended "
                "agent still keeps the computer from sleeping"
            ),
        )
        self._caffeine_screen_row.set_active(bool(state.get_setting("caffeine_keep_screen_on")))
        self._caffeine_screen_row.connect("notify::active", self._on_caffeine_screen_changed)
        caffeine_group.add(self._caffeine_screen_row)
        # Minutes, not a duration key: the grace is a short wait, and a spin
        # row keeps any length one drag away without a menu of guesses.
        self._caffeine_grace_row = Adw.SpinRow.new_with_range(1, 120, 1)
        self._caffeine_grace_row.set_title(_("Until idle grace period"))
        self._caffeine_grace_row.set_subtitle(
            _(
                "How many minutes Until idle keeps the computer awake after "
                "the last session stops working; any session picking work "
                "back up restarts the wait"
            )
        )
        self._caffeine_grace_row.set_value(
            grace_seconds(state.get_setting("caffeine_idle_grace_minutes")) // 60
        )
        self._caffeine_grace_row.connect("notify::value", self._on_caffeine_grace_changed)
        caffeine_group.add(self._caffeine_grace_row)
        self._caffeine_launch_row = Adw.SwitchRow(
            title=_("Turn on at launch"),
            subtitle=_(
                "Start with Caffeine Mode already on, keeping the computer "
                "awake until you turn it off from the header"
            ),
        )
        self._caffeine_launch_row.set_active(bool(state.get_setting("caffeine_on_launch")))
        self._caffeine_launch_row.connect("notify::active", self._on_caffeine_launch_changed)
        caffeine_group.add(self._caffeine_launch_row)
        # The same durations the button's context menu offers, so "on at
        # launch" doesn't have to mean "on until you remember it".
        self._caffeine_timer_row = Adw.ComboRow(
            title=_("Turn off after"),
            subtitle=self._caffeine_timer_subtitle(),
        )
        duration_labels = [duration_label(key) for key in DURATION_KEYS]
        self._caffeine_timer_row.set_model(Gtk.StringList.new(duration_labels))
        current_timer = state.get_setting("caffeine_launch_timer") or INDEFINITE
        self._caffeine_timer_row.set_selected(
            DURATION_KEYS.index(current_timer) if current_timer in DURATION_KEYS
            else DURATION_KEYS.index(INDEFINITE)
        )
        self._caffeine_timer_row.set_sensitive(self._caffeine_launch_row.get_active())
        self._caffeine_timer_row.connect("notify::selected", self._on_caffeine_timer_changed)
        caffeine_group.add(_searchable(self._caffeine_timer_row, *duration_labels))
        page.add(caffeine_group)

        self._build_mcp_tools_group(state, page)

        self._no_results = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title=_("No settings found"),
            description=_("Try a different search."),
        )
        # Not homogeneous: the placeholder is free to be smaller than the
        # settings, which would otherwise widen the dialog to fit it.
        self._stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self._stack.add_named(page, "settings")
        self._stack.add_named(self._no_results, "no-results")

        # The box lines up with the setting rows rather than running the full
        # width of the dialog. Reproducing that inset takes the same clamp the
        # page uses, not a fixed margin: Adw.Clamp eases the gap open between
        # its tightening threshold and its maximum, so the rows sit 12px in at
        # 360px wide and 54px in at 640px. Same two numbers, same 12px margin
        # on the clamped child, and the two agree at every width.
        self._search_entry.set_margin_start(12)
        self._search_entry.set_margin_end(12)
        self._search_entry.set_margin_top(6)
        self._search_entry.set_margin_bottom(6)
        search_bar = Adw.Clamp(
            child=self._search_entry, maximum_size=600, tightening_threshold=400
        )

        toolbar_view = Adw.ToolbarView(content=self._stack)
        toolbar_view.add_top_bar(Adw.HeaderBar())
        toolbar_view.add_top_bar(search_bar)
        # The search box is the first focusable widget in the dialog, so
        # Adw.Dialog hands it the focus on open: preferences opens ready to
        # type into. _on_search_stopped keeps Escape closing anyway.
        self.set_child(toolbar_view)

    def _build_editor_group(self, state: AppState, page: _SearchablePage) -> None:
        editor_group = _SearchableGroup(title=_("Editor"))

        scheme_ids = [""] + sorted(editor.GtkSource.StyleSchemeManager.get_default().get_scheme_ids())
        current_editor_scheme = state.get_setting("editor_style_scheme") or ""
        if current_editor_scheme not in scheme_ids:
            current_editor_scheme = ""

        def scheme_label(scheme_id: str) -> str:
            return scheme_id or _("Follow app theme")

        self._editor_scheme_expander = Adw.ExpanderRow(
            title=_("Color scheme"), subtitle=scheme_label(current_editor_scheme)
        )
        scheme_radio_group = None
        for scheme_id in scheme_ids:
            row = Adw.ActionRow(title=scheme_label(scheme_id))
            radio = Gtk.CheckButton()
            if scheme_radio_group is None:
                scheme_radio_group = radio
            else:
                radio.set_group(scheme_radio_group)
            radio.set_active(scheme_id == current_editor_scheme)
            radio.connect("toggled", self._on_editor_scheme_radio, scheme_id)
            row.add_prefix(radio)
            row.set_activatable_widget(radio)
            self._editor_scheme_expander.add_row(row)
        editor_group.add(
            _searchable(self._editor_scheme_expander, *(scheme_label(s) for s in scheme_ids))
        )

        font_row = Adw.ActionRow(title=_("Font"), subtitle=_("Applies to the editor panel"))
        self._editor_font_button = Gtk.FontDialogButton(dialog=Gtk.FontDialog(), valign=Gtk.Align.CENTER)
        current_editor_font = state.get_setting("editor_font") or ""
        if current_editor_font:
            self._editor_font_button.set_font_desc(Pango.FontDescription.from_string(current_editor_font))
        self._editor_font_button.connect("notify::font-desc", self._on_editor_font_changed)
        font_row.add_suffix(self._editor_font_button)

        reset_editor_font = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER)
        reset_editor_font.add_css_class("flat")
        reset_editor_font.set_tooltip_text(_("Reset to system monospace"))
        reset_editor_font.connect("clicked", self._on_editor_font_reset)
        font_row.add_suffix(reset_editor_font)
        editor_group.add(font_row)

        self._editor_line_numbers_row = Adw.SwitchRow(title=_("Show line numbers"))
        self._editor_line_numbers_row.set_active(bool(state.get_setting("editor_show_line_numbers")))
        self._editor_line_numbers_row.connect("notify::active", self._on_editor_line_numbers_changed)
        editor_group.add(self._editor_line_numbers_row)

        self._editor_hidden_files_row = Adw.SwitchRow(
            title=_("Show hidden files"),
            subtitle=_("Show dotfiles in the editor's file tree"),
        )
        self._editor_hidden_files_row.set_active(bool(state.get_setting("editor_show_hidden_files")))
        self._editor_hidden_files_row.connect("notify::active", self._on_editor_hidden_files_changed)
        editor_group.add(self._editor_hidden_files_row)

        pop_out_row = Adw.SpinRow.new_with_range(0, 30_000, 100)
        pop_out_row.set_title(_("Open in a window on small screens"))
        pop_out_row.set_subtitle(
            _(
                "On screens this many pixels wide or narrower (after display "
                "scaling), the editor opens in its own window instead of a "
                "panel (0 = always open as a panel)"
            )
        )
        pop_out_row.set_value(int(state.get_setting("editor_pop_out_screen_width") or 0))
        pop_out_row.connect("notify::value", self._on_editor_pop_out_width_changed)
        editor_group.add(pop_out_row)

        page.add(editor_group)

    def _build_status_icon_row(self, state: AppState, group: _SearchableGroup) -> None:
        """The status icon's switch, and the truth about whether this desktop
        can show one.

        An ActionRow carrying its own Gtk.Switch rather than an Adw.SwitchRow:
        with no host on the bus the switch has to go insensitive, and an
        insensitive widget is skipped by GTK's tooltip picking (so is
        everything inside it), which would take the explanation down with it.
        The row stays sensitive and keeps the tooltip; only the switch inside
        it stops responding.
        """
        self._status_icon_row = Adw.ActionRow(title=_("Show status icon"))
        self._status_icon_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self._status_icon_switch.set_active(bool(state.get_setting("status_icon")))
        self._status_icon_switch.connect("notify::active", self._on_status_icon_changed)
        self._status_icon_row.add_suffix(self._status_icon_switch)
        self._status_icon_row.set_activatable_widget(self._status_icon_switch)
        group.add(_searchable(self._status_icon_row, "tray", "top bar", "indicator", "AppIndicator"))
        # Seeded synchronously so the row opens saying something, then
        # followed live: availability moves under a running app — extensions
        # get switched on and off, and an X11 shell restart takes the watcher
        # with it — and the watch's own first answer is a main-loop turn away.
        # The seed is kept for the quit-behavior row, built after this row,
        # so the dialog makes one synchronous bus round trip, not two.
        self._status_icon_host_seed = statusicon.available()
        self._on_status_icon_host(self._status_icon_host_seed)
        self._status_icon_watch = statusicon.watch_availability(self._on_status_icon_host)
        self.connect("closed", lambda *_: statusicon.unwatch(self._status_icon_watch))

    def _on_status_icon_host(self, present: bool) -> None:
        # The subtitle describes what the item does *today*: the unread count
        # is drawn onto the icon by a later change, and a row promising a
        # badge nothing draws yet would be a row that lies.
        self._status_icon_switch.set_sensitive(present)
        self._status_icon_row.set_subtitle(
            _("Shows Collins in the top bar, with a menu that jumps to any open session")
            if present
            else _(
                "No status-icon support was found in this desktop — GNOME "
                "needs an AppIndicator extension"
            )
        )
        self._status_icon_row.set_tooltip_text(
            None if present else _("Nothing on this desktop can show a status icon")
        )
        # The quit row's Hide Window choice leans on the icon to bring a
        # hidden window back; built after this group, so the first seed lands
        # before the row exists and the row seeds itself instead.
        if getattr(self, "_quit_behavior_row", None) is not None:
            self._sync_quit_behavior_subtitle(present)

    def _sync_quit_behavior_subtitle(self, host_present: bool) -> None:
        subtitle = _("Closing a window while agent sessions are still running")
        if not host_present:
            subtitle += ". " + _(
                "Without a status icon, a hidden window comes back by "
                "relaunching Collins or clicking a session notification"
            )
        self._quit_behavior_row.set_subtitle(subtitle)

    def _on_status_icon_changed(self, switch: Gtk.Switch, _pspec) -> None:
        self._state.set_setting("status_icon", switch.get_active())
        self._on_change()

    def _build_cli_rows(self, state: AppState, group: _SearchableGroup) -> None:
        """Where the Claude Code CLI is — the same question the welcome
        dialog asks a launch that can't find it (cliwelcome), now answerable
        after the fact: point at a different install, or clear the box to
        fall back to whatever PATH offers. Tabs already open keep the CLI
        they started with."""
        self._cli_row = Adw.EntryRow(title=_("Path to the claude executable"))
        self._cli_verdict = Gtk.Image(valign=Gtk.Align.CENTER)
        browse = Gtk.Button(label=_("Browse…"), valign=Gtk.Align.CENTER)
        browse.add_css_class("flat")
        browse.connect("clicked", self._on_cli_browse)
        self._cli_row.add_suffix(self._cli_verdict)
        self._cli_row.add_suffix(browse)
        self._cli_row.set_text(state.get_setting(clisetup.PATH_SETTING) or "")
        self._cli_row.connect("changed", self._on_cli_path_changed)
        group.add(_searchable(self._cli_row, "claude", "CLI", "PATH", _("Browse…")))

        # The verdict's reason, directly under the box like the welcome
        # dialog's: a title-less row in the same list, so it stays beside
        # the entry however many rows the group grows. The same search terms
        # as the entry keep the two showing and hiding together.
        self._cli_reason = Adw.ActionRow(activatable=False, selectable=False)
        self._cli_reason.add_css_class("dim-label")
        group.add(_searchable(self._cli_reason, "claude", "CLI", "PATH", _("Browse…")))
        self._refresh_cli_row(save=False)

    def _on_cli_path_changed(self, _row: Adw.EntryRow) -> None:
        self._refresh_cli_row(save=True)

    def _refresh_cli_row(self, save: bool) -> None:
        """Judge the path in the box on cliwelcome's scale, keep it when it
        would be accepted there, and say why either way.

        Saved states are the acceptable ones — plus empty, which here means
        "rely on PATH alone" (the setting's default). Anything else (a typo,
        a half-typed path) just wears its red x while the stored answer
        stays; closing preferences mid-typo loses nothing.
        """
        text = self._cli_row.get_text().strip()
        status = clisetup.validate(text)
        acceptable = not text or status in cliwelcome.MARKS
        stored = self._state.get_setting(clisetup.PATH_SETTING) or ""
        if save and acceptable and text != stored:
            self._state.set_setting(clisetup.PATH_SETTING, text)
            # Swaps the old answer's PATH entry for the new one's, so the
            # next tab (and every headless claude run) picks up the change.
            clisetup.apply(text)
            self._on_change()
        if text:
            icon, style = cliwelcome.MARKS.get(status, cliwelcome.BAD_MARK)
            reason = cliwelcome.reason_for(status, text)
        elif clisetup.on_path():
            icon, style = cliwelcome.MARKS[clisetup.OK]
            reason = _("Using the claude found on PATH at {path}.").format(
                path=clisetup.found_at()
            )
        else:
            icon, style = cliwelcome.BAD_MARK
            reason = _(
                "claude isn't on PATH — Collins will ask where it is at the "
                "next launch."
            )
        self._cli_verdict.set_from_icon_name(icon)
        for name in ("success", "warning", "error"):
            if name == style:
                self._cli_verdict.add_css_class(name)
            else:
                self._cli_verdict.remove_css_class(name)
        self._cli_reason.set_subtitle(reason)

    def _on_cli_browse(self, _button: Gtk.Button) -> None:
        picker = Gtk.FileDialog(title=_("Choose the claude executable"))

        def picked(picker: Gtk.FileDialog, result) -> None:
            try:
                file = picker.open_finish(result)
            except Exception:
                return  # dismissed
            if file is not None and file.get_path():
                # As picked — a symlink stays a symlink (see clisetup).
                self._cli_row.set_text(file.get_path())
                self._cli_row.set_position(-1)

        picker.open(self.get_root(), None, picked)

    def _build_mcp_tools_group(self, state: AppState, page: _SearchablePage) -> None:
        """The on/off switch for each tool Collins offers a session (the
        `collins` MCP server in the session's /mcp list).

        Driven by mcptools.TOOLS, in the order sessions are served them, so a
        tool added to the table can't ship without a switch — one with no
        entry in _MCP_TOOL_LABELS falls back to its own name rather than
        going unlisted.
        """
        group = _SearchableGroup(
            title=_("Built-in MCP tools"),
            description=_(
                "Turning one off takes effect immediately; sessions already "
                "running are only offered the tool again once they restart"
            ),
        )
        self._mcp_tool_rows: dict[str, Adw.SwitchRow] = {}
        for tool in mcptools.TOOLS:
            name = tool["name"]
            title, subtitle = _MCP_TOOL_LABELS.get(name, (name, ""))
            row = Adw.SwitchRow(title=_(title), subtitle=_(subtitle) if subtitle else "")
            row.set_active(bool(state.get_setting(mcptools.tool_setting_key(name))))
            row.connect("notify::active", self._on_mcp_tool_changed, name)
            self._mcp_tool_rows[name] = row
            # The tool's name is in the subtitle already; "session tools" is
            # what the group used to be called.
            group.add(_searchable(row, "session tools", name))
        page.add(group)

    def _on_mcp_tool_changed(self, row: Adw.SwitchRow, _pspec, name: str) -> None:
        self._state.set_setting(mcptools.tool_setting_key(name), row.get_active())
        self._on_change()

    # -- search --------------------------------------------------------------

    def _on_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._apply_filter()

    def _on_search_stopped(self, _entry: Gtk.SearchEntry) -> None:
        # Escape empties the box, and closes preferences once it is already
        # empty. The box holds the focus from the moment the dialog opens, so
        # it swallows the key — without this, Escape would stop closing
        # preferences at all.
        if self._search_entry.get_text():
            self._search_entry.set_text("")
        else:
            self.close()

    def _apply_filter(self) -> None:
        """Hide every setting the search box doesn't name.

        A group whose own title matches keeps all of its rows: someone typing
        "terminal" wants that whole section, not just the rows that happen to
        repeat the word.
        """
        query = self._search_entry.get_text()
        anything_matched = False
        for group in self._page.groups:
            whole_group = prefssearch.matches(query, _group_text(group))
            matched_rows = False
            for row in group.rows:
                visible = whole_group or prefssearch.matches(query, _row_text(row))
                row.set_visible(visible)
                matched_rows = matched_rows or visible
            group.set_visible(matched_rows)
            anything_matched = anything_matched or matched_rows
        self._stack.set_visible_child_name("settings" if anything_matched else "no-results")

    def _on_editor_scheme_radio(self, radio: Gtk.CheckButton, scheme_id: str) -> None:
        if not radio.get_active():
            return
        self._state.set_setting("editor_style_scheme", scheme_id)
        self._editor_scheme_expander.set_subtitle(scheme_id or _("Follow app theme"))
        self._on_change()

    def _on_editor_font_changed(self, button: Gtk.FontDialogButton, _pspec) -> None:
        desc = button.get_font_desc()
        self._state.set_setting("editor_font", desc.to_string() if desc else "")
        self._on_change()

    def _on_editor_font_reset(self, _button: Gtk.Button) -> None:
        self._editor_font_button.set_font_desc(None)
        self._state.set_setting("editor_font", "")
        self._on_change()

    def _on_editor_line_numbers_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("editor_show_line_numbers", row.get_active())
        self._on_change()

    def _on_editor_hidden_files_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("editor_show_hidden_files", row.get_active())
        self._on_change()

    def _on_editor_pop_out_width_changed(self, row: Adw.SpinRow, _pspec) -> None:
        self._state.set_setting("editor_pop_out_screen_width", int(row.get_value()))
        self._on_change()

    def _on_font_changed(self, button: Gtk.FontDialogButton, _pspec) -> None:
        desc = button.get_font_desc()
        self._state.set_setting("font", desc.to_string() if desc else "")
        self._on_change()

    def _on_font_reset(self, _button: Gtk.Button) -> None:
        self._font_button.set_font_desc(None)
        self._state.set_setting("font", "")
        self._on_change()

    def _on_scrollback_changed(self, row: Adw.SpinRow, _pspec) -> None:
        self._state.set_setting("scrollback", int(row.get_value()))
        self._on_change()

    def _on_terminal_max_width_changed(self, row: Adw.SpinRow, _pspec) -> None:
        self._state.set_setting("terminal_max_width", int(row.get_value()))
        self._on_change()

    def _on_pr_font_scale_changed(self, row: Adw.SpinRow, _pspec) -> None:
        self._state.set_setting("pr_font_scale", int(row.get_value()))
        self._on_change()

    def _on_pr_images_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("pr_inline_images", bool(row.get_active()))
        self._on_change()

    def _on_pr_autoshow_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("open_pr_panel_on_attach", bool(row.get_active()))
        self._on_change()

    def _on_confirm_merges_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("confirm_merges", bool(row.get_active()))
        self._on_change()

    def _on_theme_radio(self, radio: Gtk.CheckButton, name: str) -> None:
        if not radio.get_active():
            return
        self._state.set_setting("terminal_theme", name)
        self._theme_expander.set_subtitle(name)
        self._on_change()

    def _on_scheme_changed(self, row: Adw.ComboRow, _pspec) -> None:
        key = _SCHEMES[row.get_selected()][0]
        self._state.set_setting("color_scheme", key)
        apply_color_scheme(key)
        self._on_change()

    def _on_caffeine_screen_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("caffeine_keep_screen_on", row.get_active())
        self._on_change()

    def _on_caffeine_launch_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("caffeine_on_launch", row.get_active())
        # Nothing to time when nothing is turned on at launch.
        self._caffeine_timer_row.set_sensitive(row.get_active())
        self._on_change()

    def _caffeine_timer_subtitle(self) -> str:
        """The launch-timer row's subtitle, quoting the actual Until-idle
        grace rather than a hardcoded five minutes, so the two rows can never
        disagree about how long "idle" takes to arrive."""
        minutes = grace_seconds(self._state.get_setting("caffeine_idle_grace_minutes")) // 60
        return ngettext(
            "How long that launch-time Caffeine Mode runs before it turns "
            "itself off. Until idle never does: it holds the computer "
            "awake while any session is working (and {n} minute past), "
            "dozing in between",
            "How long that launch-time Caffeine Mode runs before it turns "
            "itself off. Until idle never does: it holds the computer "
            "awake while any session is working (and {n} minutes past), "
            "dozing in between",
            minutes,
        ).format(n=minutes)

    def _on_caffeine_grace_changed(self, row: Adw.SpinRow, _pspec) -> None:
        self._state.set_setting("caffeine_idle_grace_minutes", int(row.get_value()))
        # The launch-timer subtitle quotes the grace; keep them agreeing.
        self._caffeine_timer_row.set_subtitle(self._caffeine_timer_subtitle())
        self._on_change()

    def _on_caffeine_timer_changed(self, row: Adw.ComboRow, _pspec) -> None:
        self._state.set_setting("caffeine_launch_timer", DURATION_KEYS[row.get_selected()])
        self._on_change()

    def _on_easy_copy_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("easy_copy_paste", row.get_active())
        self._on_change()

    def _on_attach_overlay_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("attach_overlay_button", row.get_active())
        self._on_change()

    def _on_composer_typing_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("composer_on_typing", row.get_active())
        self._on_change()

    def _on_spell_click_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("composer_spell_click", row.get_active())
        self._on_change()

    def _on_composer_enter_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("composer_enter_sends", row.get_active())
        self._on_change()

    def _on_composer_autoshow_changed(self, row: Adw.ComboRow, _pspec) -> None:
        self._state.set_setting(
            "composer_new_sessions", _COMPOSER_AUTOSHOW[row.get_selected()][0]
        )
        self._on_change()

    def _on_tab_drag_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("panel_tab_drag_handles", row.get_active())
        self._on_change()

    def _on_attach_autodock_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("dock_attachments_when_room", bool(row.get_active()))
        self._on_change()

    def _add_running_behavior_row(
        self,
        group: _SearchableGroup,
        title: str,
        subtitle: str,
        key: str,
        behaviors: list[tuple[str, str]] | None = None,
    ) -> Adw.ComboRow:
        behaviors = behaviors if behaviors is not None else _RUNNING_BEHAVIORS
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        labels = [_(label) for _v, label in behaviors]
        row.set_model(Gtk.StringList.new(labels))
        values = [value for value, _l in behaviors]
        current = self._state.get_setting(key)
        row.set_selected(values.index(current) if current in values else 0)
        row.connect("notify::selected", self._on_running_behavior_changed, key, values)
        group.add(_searchable(row, *labels))
        return row

    def _on_running_behavior_changed(
        self, row: Adw.ComboRow, _pspec, key: str, values: list[str]
    ) -> None:
        self._state.set_setting(key, values[row.get_selected()])
        self._on_change()

    def _on_remote_archive_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        # Read at each archive, so it takes effect immediately; no listener
        # needs an apply nudge.
        self._state.set_setting("archive_on_claude_ai", row.get_active())

    def _on_bg_poll_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("background_status_poll", row.get_active())
        self._on_change()

    def _on_progress_termprop_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("progress_termprop", row.get_active())
        self._on_change()

    def _on_folder_path_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("show_folder_path", row.get_active())
        self._on_change()

    def _on_icon_size_changed(self, row: Adw.SpinRow, _pspec) -> None:
        self._state.set_setting("project_icon_size", int(row.get_value()))
        self._on_change()

    def _on_usage_panel_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("show_usage_panel", row.get_active())
        self._on_change()

    def _on_auto_title_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("auto_title_sessions", row.get_active())

    def _on_cli_title_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("cli_title_sessions", row.get_active())
        self._on_change()

    def _on_attach_prompt_prs_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("attach_prompt_prs", row.get_active())

    def _populate_model_rows(self) -> None:
        """Fill the model pickers, off the main loop.

        The saved list (claudemodels keeps one across restarts) fills the rows
        at once when there is one, so the page opens on real models instead of
        a placeholder; the worker behind it re-queries only if that list has
        aged out, and the CLI's own aliases stand in when the API can't be
        asked and nothing was ever saved.
        """
        cached = claudemodels.cached_models()
        if cached:
            self._apply_model_rows(cached)
            self._apply_model_status(
                len(cached), claudemodels.cache_fetched_at(), claudemodels.cache_failed()
            )

        def work() -> None:
            models = claudemodels.available_models()
            GLib.idle_add(
                apply_models,
                models,
                claudemodels.cache_fetched_at(),
                claudemodels.cache_failed(),
                priority=GLib.PRIORITY_DEFAULT,
            )

        def apply_models(
            models: list[claudemodels.ClaudeModel], fetched_at: float, failed: bool
        ) -> bool:
            self._apply_model_rows(models or list(claudemodels.FALLBACK_MODELS))
            self._apply_model_status(len(models), fetched_at, failed)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name="prefs-models", daemon=True).start()

    def _apply_model_rows(self, models: list[claudemodels.ClaudeModel]) -> None:
        """Offer *models* in every picker, keeping each row's saved choice.

        Runs more than once — the saved list, the query behind it, and every
        Refresh — so each pass has to leave exactly one "notify::selected"
        handler per row, and to stop set_model/set_selected from firing it:
        an unblocked repopulate would write the settings back as though the
        user had just picked something.
        """
        for key, row in self._model_rows.items():
            current = (self._state.get_setting(key) or "").strip()
            ids = [""] + [m.id for m in models]
            labels = [self._model_default_labels[key]] + [m.display_name for m in models]
            if current and current not in ids:
                # A saved model the API no longer lists stays visible and
                # selected rather than silently snapping to the default.
                ids.append(current)
                labels.append(current)
            handler = self._model_handlers.get(key)
            if handler is not None:
                row.handler_block(handler)
            row.set_model(Gtk.StringList.new(labels))
            row.set_selected(ids.index(current))
            if handler is not None:
                row.handler_unblock(handler)
            self._model_ids[key] = ids
            if handler is None:
                self._model_handlers[key] = row.connect(
                    "notify::selected", self._on_model_row_changed, key
                )

    def _apply_model_status(self, count: int, fetched_at: float, failed: bool) -> None:
        """Date the list under the Refresh button, and say when it's a fallback.

        The row never just goes quiet: whenever the list on screen is the
        product of a query that didn't answer, it says so and keeps naming the
        list it fell back to and how old that is.

        *failed* is `claudemodels.cache_failed()` at both call sites, not a
        flag off whichever call got here. Opening the page onto a lapsed TTL
        with the network down is the same broken as pressing Refresh with the
        network down, and the row should read the same either way.
        """
        if fetched_at <= 0:
            self._model_status_row.set_subtitle(
                _("Couldn't reach Anthropic — offering the CLI's aliases (opus, sonnet, haiku)")
            )
            return
        when = formatting.format_relative(
            datetime.fromtimestamp(fetched_at, timezone.utc).isoformat()
        )
        if failed:
            self._model_status_row.set_subtitle(
                _("Couldn't reach Anthropic — still showing the list fetched {when}").format(when=when)
            )
        else:
            self._model_status_row.set_subtitle(
                _("{count} models, updated {when}").format(count=count, when=when)
            )

    def _on_refresh_models(self, _button: Gtk.Button) -> None:
        """Query the API outright, cache and backoff ignored."""
        self._model_refresh_btn.set_sensitive(False)
        self._model_status_row.set_subtitle(_("Checking…"))

        def work() -> None:
            models = claudemodels.refresh_models()
            GLib.idle_add(
                done,
                models,
                claudemodels.cache_fetched_at(),
                claudemodels.cache_failed(),
                priority=GLib.PRIORITY_DEFAULT,
            )

        def done(
            models: list[claudemodels.ClaudeModel], fetched_at: float, failed: bool
        ) -> bool:
            self._apply_model_rows(models or list(claudemodels.FALLBACK_MODELS))
            self._apply_model_status(len(models), fetched_at, failed)
            self._model_refresh_btn.set_sensitive(True)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name="prefs-models-refresh", daemon=True).start()

    def _on_model_row_changed(self, row: Adw.ComboRow, _pspec, key: str) -> None:
        ids = self._model_ids.get(key) or [""]
        selected = row.get_selected()
        if not 0 <= selected < len(ids):
            return  # mid-repopulate; the pass that set it will restore the choice
        self._state.set_setting(key, ids[selected])
        self._on_change()

    def _on_pr_title_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("pr_title_sessions", row.get_active())
        self._on_change()

    def _on_pr_launch_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        # Only ever read at startup, so this takes effect from the next launch.
        self._state.set_setting("refresh_prs_on_launch", row.get_active())
        self._on_change()

    def _on_worktree_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("worktree_new_sessions", row.get_active())
        self._on_change()

    def _on_restore_session_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        # Only ever read at startup, so this takes effect from the next launch.
        self._state.set_setting("restore_last_session", row.get_active())
        self._on_change()

    # -- footer apps ---------------------------------------------------------

    def _footer_app_ids(self) -> list[str]:
        # Copy before mutating so the shared DEFAULT_SETTINGS list is never
        # edited in place.
        return list(self._state.get_setting("footer_apps") or [])

    def _rebuild_footer_apps(self) -> None:
        for row in self._footer_app_rows:
            self._footer_apps_group.remove(row)
        self._footer_app_rows = []
        apps = footerapps.resolve_apps(self._footer_app_ids())
        for index, (app_id, info) in enumerate(apps):
            row = Adw.ActionRow(title=info.get_display_name() or app_id, subtitle=app_id)
            row.add_prefix(apppicker.app_icon_image(info, 32))
            up_btn = Gtk.Button(icon_name="go-up-symbolic", valign=Gtk.Align.CENTER)
            up_btn.set_tooltip_text(_("Move up"))
            up_btn.set_sensitive(index > 0)
            up_btn.connect("clicked", self._on_move_footer_app, app_id, -1)
            down_btn = Gtk.Button(icon_name="go-down-symbolic", valign=Gtk.Align.CENTER)
            down_btn.set_tooltip_text(_("Move down"))
            down_btn.set_sensitive(index < len(apps) - 1)
            down_btn.connect("clicked", self._on_move_footer_app, app_id, 1)
            remove_btn = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER)
            remove_btn.set_tooltip_text(_("Remove"))
            remove_btn.connect("clicked", self._on_remove_footer_app, app_id)
            for btn in (up_btn, down_btn, remove_btn):
                btn.add_css_class("flat")
                row.add_suffix(btn)
            self._footer_apps_group.add(row)
            self._footer_app_rows.append(row)
        if not apps:
            row = Adw.ActionRow(title=_("No apps configured"))
            row.set_sensitive(False)
            self._footer_apps_group.add(row)
            self._footer_app_rows.append(row)

    def _on_add_footer_app(self, _button: Gtk.Button) -> None:
        dialog = apppicker.AppPickerDialog(
            exclude_ids=set(self._footer_app_ids()), on_select=self._append_footer_app
        )
        dialog.present(self)

    def _append_footer_app(self, app_id: str) -> None:
        apps = self._footer_app_ids()
        if app_id not in apps:
            apps.append(app_id)
            self._save_footer_apps(apps)

    def _on_remove_footer_app(self, _button: Gtk.Button, app_id: str) -> None:
        apps = [a for a in self._footer_app_ids() if a != app_id]
        self._save_footer_apps(apps)

    def _on_move_footer_app(self, _button: Gtk.Button, app_id: str, delta: int) -> None:
        apps = self._footer_app_ids()
        index = apps.index(app_id) if app_id in apps else -1
        target = index + delta
        if index < 0 or not 0 <= target < len(apps):
            return
        apps[index], apps[target] = apps[target], apps[index]
        self._save_footer_apps(apps)

    def _save_footer_apps(self, apps: list[str]) -> None:
        self._state.set_setting("footer_apps", apps)
        self._rebuild_footer_apps()
        # The rebuilt rows are visible by default; put the search back over them.
        self._apply_filter()
        self._on_change()

    def _on_language_radio(self, radio: Gtk.CheckButton, code: str, label: str) -> None:
        if not radio.get_active():
            return
        self._state.set_setting("language", code)
        self._lang_expander.set_subtitle(label)
        self._restart_btn.set_visible(code != self._initial_lang)
        self._on_change()

    def _on_restart(self, _button: Gtk.Button) -> None:
        # Relaunch after a short delay so this instance fully exits and frees
        # the single-instance lock before the new one registers.
        subprocess.Popen(
            ["sh", "-c", f"sleep 1.5; exec {shlex.quote(sys.executable)} -m collins"],
            start_new_session=True,
        )
        app = Gio.Application.get_default()
        if app is not None:
            app.quit()
