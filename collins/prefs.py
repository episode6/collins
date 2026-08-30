# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-30. Full change history: git log for this file.

"""Preferences dialog: terminal font, scrollback, color scheme."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import (  # noqa: E402
    apppicker,
    clisetup,
    composerkeys,
    editor,
    footerapps,
    notifycenter,
    notifysound,
    prefslayout,
    prefssearch,
    statusicon,
    tokensettings,
    updatecheck,
    welcome,
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

# The in-app notification card's own light/dark (notifycenter.CARD_SCHEMES,
# in the drop-down's order): the app's, or pinned either way.
_CARD_SCHEMES = [
    (notifycenter.CARD_SCHEME_APP, N_("Follow app")),
    (notifycenter.CARD_SCHEME_LIGHT, N_("Light")),
    (notifycenter.CARD_SCHEME_DARK, N_("Dark")),
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


# The search keywords for the Token use group's rows (tokensettings builds
# the rows; search is this dialog's business), by prefslayout.TOKEN_USE_ROWS
# key. The model pickers' tier names are what people type when they want a
# model; "auto-generate", "titles" and "off" are the switch the title
# picker's None item replaced, for anyone looking for it by its old name.
_TOKEN_ROW_TERMS = {
    "title_model": (N_("None"), "haiku", "sonnet", "opus", "auto-generate", "titles", "off"),
    "icon_model": (N_("None"), "haiku", "sonnet", "opus"),
    "auto_renew_login": ("token", "oauth", "regenerate", "refresh", "expired", "login"),
    "model_list": (N_("Refresh"), "models api"),
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

        # The groups, in the order prefslayout promises them — a loop over
        # data rather than a run of page.add calls, so the order is one the
        # unit suite can hold the page to (the Token use group directly under
        # General, above all).
        builders = {
            "cli": self._build_cli_group,
            "general": self._build_general_group,
            "token_use": self._build_token_use_group,
            "mcp_tools": self._build_mcp_tools_group,
            "sessions": self._build_sessions_group,
            "notifications": self._build_notifications_group,
            "composer": self._build_composer_group,
            "terminal": self._build_terminal_group,
            "footer_apps": self._build_footer_apps_group,
            "pull_requests": self._build_pr_group,
            "caffeine": self._build_caffeine_group,
            "editor": self._build_editor_group,
        }
        for name in prefslayout.GROUPS:
            page.add(builders[name](state))

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

    def _build_cli_group(self, state: AppState) -> _SearchableGroup:
        # Above everything, under no heading: the CLI is the tool the app is
        # about, and the row that answers "which claude is Collins running?"
        # shouldn't take scrolling — or a category — to find.
        cli_group = _SearchableGroup()
        self._build_cli_rows(state, cli_group)
        return cli_group

    def _build_general_group(self, state: AppState) -> _SearchableGroup:
        # General: what Collins looks like and what the sidebar shows.
        general_group = _SearchableGroup(title=_("General"))
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
            title=_("Show folder paths in sidebar"),
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
        self._update_check_row = Adw.SwitchRow(
            title=_("Check for updates"),
            subtitle=_(
                "Ask GitHub once a day whether a newer Collins is out, and "
                "notify you when one is. Through your gh login, or anonymously"
            ),
        )
        self._update_check_row.set_active(bool(state.get_setting(updatecheck.SETTING)))
        self._update_check_row.connect("notify::active", self._on_update_check_changed)
        general_group.add(
            _searchable(self._update_check_row, "update", "upgrade", "version", "release", "github")
        )
        return general_group

    def _build_token_use_group(self, state: AppState) -> _SearchableGroup:
        """The settings that spend the user's Claude quota, together and
        directly under General (see prefslayout). The rows are
        tokensettings' — built there so a first-launch dialog can show the
        same ones — and only the search keywords are added here."""
        group = _SearchableGroup(
            title=_(tokensettings.TITLE), description=_(tokensettings.DESCRIPTION)
        )
        _searchable(group, *tokensettings.SEARCH_TERMS)
        rows = tokensettings.build_token_rows(state, self._on_change)
        for key, row in zip(prefslayout.TOKEN_USE_ROWS, rows, strict=True):
            # The N_-marked terms ("None", "Refresh") are what the row shows,
            # so they match in the user's language; the rest are English
            # keywords gettext hands back untouched.
            group.add(_searchable(row, *(_(term) for term in _TOKEN_ROW_TERMS[key])))
        return group

    def _build_mcp_tools_group(self, state: AppState) -> _SearchableGroup:
        """The on/off switch for each tool Collins offers a session — the
        rows are tokensettings.build_mcp_rows', by tool name."""
        group = _SearchableGroup(
            title=_(tokensettings.MCP_TITLE), description=_(tokensettings.MCP_DESCRIPTION)
        )
        self._mcp_tool_rows = tokensettings.build_mcp_rows(state, self._on_change)
        for name, row in self._mcp_tool_rows.items():
            # The tool's name is in the subtitle already; "session tools" is
            # what the group used to be called.
            group.add(_searchable(row, "session tools", name))
        return group

    def _build_sessions_group(self, state: AppState) -> _SearchableGroup:
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
        return sessions_group

    def _build_notifications_group(self, state: AppState) -> _SearchableGroup:
        """The spec's Notifications group: the in-app card and its own
        light/dark, the sound it plays, bells from other sessions, and
        finished runs.

        The sound row is a combo of the three shapes the setting takes —
        Default (the desktop's message sound), None, and Custom…, which opens
        a file picker — with a ▶ beside it, the one place a user hears what
        they picked. Its subtitle says what the choice means in words
        (notifycenter.sound_subtitle), and, when GStreamer is not here to
        play anything, says that instead and the row goes insensitive: the
        beep is what a card gets then, whatever the row says.
        """
        group = _searchable(
            _SearchableGroup(title=_("Notifications")),
            *prefslayout.NOTIFICATION_SEARCH_TERMS,
        )
        self._inapp_row = Adw.SwitchRow(
            title=_("In-app notifications"),
            subtitle=_(
                "Show a message from another session inside the window while "
                "Collins is focused. Off sends every notification to the desktop"
            ),
        )
        self._inapp_row.set_active(bool(state.get_setting("inapp_notifications")))
        self._inapp_row.connect("notify::active", self._on_inapp_changed)
        group.add(_searchable(self._inapp_row, "card", "banner", "desktop", "popup"))

        # The subtitle is kept short for the same reason _COMPOSER_AUTOSHOW's
        # labels are: the selected value gets what the subtitle leaves, and
        # "Follow app" was coming back as "Follo…" behind a longer one.
        self._card_scheme_row = Adw.ComboRow(
            title=_("Card theme"),
            subtitle=_("The in-app card's own light or dark, whatever the app is"),
        )
        card_scheme_labels = [_(label) for _k, label in _CARD_SCHEMES]
        self._card_scheme_row.set_model(Gtk.StringList.new(card_scheme_labels))
        current_card_scheme = state.get_setting("notification_color_scheme")
        self._card_scheme_row.set_selected(
            next((i for i, (k, _l) in enumerate(_CARD_SCHEMES) if k == current_card_scheme), 0)
        )
        self._card_scheme_row.connect("notify::selected", self._on_card_scheme_changed)
        group.add(
            _searchable(self._card_scheme_row, *card_scheme_labels, "theme", "color", "scheme", "mode")
        )

        self._sound_row = Adw.ComboRow(title=_("Sound"))
        self._sound_row.set_model(Gtk.StringList.new([_("Default"), _("None"), _("Custom…")]))
        self._sound_value = str(state.get_setting("notification_sound") or notifycenter.SOUND_DEFAULT)
        self._sound_row.set_selected(self._sound_index(self._sound_value))
        self._sound_row.connect("notify::selected", self._on_sound_selected)
        # Re-picking the combo's "Custom…" while it is already the selection
        # emits nothing (GtkSingleSelection is silent when the position stands
        # still), so a chosen file would be a dead end without a second way
        # in: the folder button, shown only while a file is the choice.
        self._sound_browse = Gtk.Button(
            icon_name="document-open-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Choose a different sound file"),
        )
        self._sound_browse.add_css_class("flat")
        self._sound_browse.connect("clicked", lambda *_: self._browse_sound())
        self._sound_row.add_suffix(self._sound_browse)
        self._sound_play = Gtk.Button(
            icon_name="media-playback-start-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text=_("Play the notification sound"),
        )
        self._sound_play.add_css_class("flat")
        self._sound_play.connect("clicked", self._on_sound_play)
        self._sound_row.add_suffix(self._sound_play)
        self._refresh_sound_row()
        group.add(
            _searchable(self._sound_row, _("Default"), _("None"), _("Custom…"), "file", "gstreamer", "mute")
        )

        self._bell_row = Adw.SwitchRow(
            title=_("Bells from other sessions"),
            subtitle=_(
                "A terminal bell from a session you aren't looking at posts a "
                "notification and plays the sound. Off keeps the desktop's beep"
            ),
        )
        self._bell_row.set_active(bool(state.get_setting("bell_notifications")))
        self._bell_row.connect("notify::active", self._on_bell_notifications_changed)
        group.add(_searchable(self._bell_row, "beep", "BEL", "terminal"))

        self._announce_row = Adw.SwitchRow(
            title=_("Announce finished runs"),
            subtitle=_("Also notify when a session's run finishes, not only when it asks for you"),
        )
        self._announce_row.set_active(bool(state.get_setting("announce_finished_runs")))
        self._announce_row.connect("notify::active", self._on_announce_finished_changed)
        group.add(_searchable(self._announce_row, "finished", "done", "complete", "green"))
        return group

    @staticmethod
    def _sound_index(value: str) -> int:
        """Which of the combo's three rows the setting's value is."""
        if not value or value == notifycenter.SOUND_DEFAULT:
            return 0
        if value == notifycenter.SOUND_NONE:
            return 1
        return 2

    def _refresh_sound_row(self) -> None:
        """The subtitle says what the choice means — or, with no GStreamer,
        why the row is greyed and what a card gets instead."""
        self._sound_browse.set_visible(self._sound_index(self._sound_value) == 2)
        if notifysound.available():
            self._sound_row.set_subtitle(notifycenter.sound_subtitle(self._sound_value))
            self._sound_row.set_sensitive(True)
            self._sound_play.set_sensitive(not notifycenter.sound_is_silent(self._sound_value))
        else:
            self._sound_row.set_subtitle(
                _("Sound needs GStreamer ({package}); the desktop's beep is used instead").format(
                    package=notifysound.GSTREAMER_PACKAGE
                )
            )
            self._sound_row.set_sensitive(False)

    def _build_composer_group(self, state: AppState) -> _SearchableGroup:
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
        return composer_group

    def _build_terminal_group(self, state: AppState) -> _SearchableGroup:
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
        return terminal_group

    def _build_footer_apps_group(self, state: AppState) -> _SearchableGroup:
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
        return self._footer_apps_group

    def _build_pr_group(self, state: AppState) -> _SearchableGroup:
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
        return pr_group

    def _build_caffeine_group(self, state: AppState) -> _SearchableGroup:
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
        return caffeine_group

    def _build_editor_group(self, state: AppState) -> _SearchableGroup:
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
        return editor_group

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
        dialog asks a launch that can't find it (welcome), now answerable
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
        # dialog's: a title-less row in the same list. The same search terms
        # as the entry keep the two showing and hiding together.
        self._cli_reason = Adw.ActionRow(activatable=False, selectable=False)
        self._cli_reason.add_css_class("dim-label")
        group.add(_searchable(self._cli_reason, "claude", "CLI", "PATH", _("Browse…")))
        self._refresh_cli_row(save=False)

    def _on_cli_path_changed(self, _row: Adw.EntryRow) -> None:
        self._refresh_cli_row(save=True)

    def _refresh_cli_row(self, save: bool) -> None:
        """Judge the path in the box on the welcome dialog's scale, keep it when it
        would be accepted there, and say why either way.

        Saved states are the acceptable ones — plus empty, which here means
        "rely on PATH alone" (the setting's default). Anything else (a typo,
        a half-typed path) just wears its red x while the stored answer
        stays; closing preferences mid-typo loses nothing.
        """
        text = self._cli_row.get_text().strip()
        status = clisetup.validate(text)
        acceptable = not text or status in welcome.MARKS
        stored = self._state.get_setting(clisetup.PATH_SETTING) or ""
        if save and acceptable and text != stored:
            self._state.set_setting(clisetup.PATH_SETTING, text)
            # Swaps the old answer's PATH entry for the new one's, so the
            # next tab (and every headless claude run) picks up the change.
            clisetup.apply(text)
            self._on_change()
        if text:
            icon, style = welcome.MARKS.get(status, welcome.BAD_MARK)
            reason = welcome.reason_for(status, text)
        elif clisetup.on_path():
            icon, style = welcome.MARKS[clisetup.OK]
            reason = _("Using the claude found on PATH at {path}.").format(
                path=clisetup.found_at()
            )
        else:
            icon, style = welcome.BAD_MARK
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

    def show_group(self, name: str) -> bool:
        """Open on one group: the search box is filled with the group's title,
        so the page is filtered to that group — plus any other row that
        happens to name it (see _apply_filter) — the way someone who typed
        it would see it, and the one filter this dialog has (an
        Adw.PreferencesPage can scroll to its top, not to a group).

        *name* is a prefslayout.GROUPS entry. False, with the page left whole,
        for a name the layout doesn't have or a group with no title to search
        by (the untitled CLI rows) — a caller pointing at a group that isn't
        built yet gets plain Preferences rather than an empty page.
        """
        if name not in prefslayout.GROUPS:
            return False
        title = self._page.groups[prefslayout.GROUPS.index(name)].get_title() or ""
        if not title:
            return False
        self._search_entry.set_text(title)
        return True

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

    def _on_inapp_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("inapp_notifications", row.get_active())
        self._on_change()

    def _on_card_scheme_changed(self, row: Adw.ComboRow, _pspec) -> None:
        self._state.set_setting("notification_color_scheme", _CARD_SCHEMES[row.get_selected()][0])
        self._on_change()

    def _on_bell_notifications_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("bell_notifications", row.get_active())
        self._on_change()

    def _on_announce_finished_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("announce_finished_runs", row.get_active())
        self._on_change()

    def _on_update_check_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting(updatecheck.SETTING, row.get_active())
        self._on_change()

    def _on_sound_selected(self, row: Adw.ComboRow, _pspec) -> None:
        """Default and None are written at once; Custom… asks for the file
        first and writes on the pick, falling back to the row's previous
        choice when the picker is dismissed — a "Custom" with no file behind
        it would be a setting that means nothing. Changing one file for
        another is the folder button's job (see _build_notifications_group):
        this never fires for a row that is already the selection."""
        selected = row.get_selected()
        if selected == self._sound_index(self._sound_value):
            return
        if selected == 0:
            self._set_sound(notifycenter.SOUND_DEFAULT)
        elif selected == 1:
            self._set_sound(notifycenter.SOUND_NONE)
        else:
            self._browse_sound()

    def _set_sound(self, value: str) -> None:
        self._sound_value = value
        self._state.set_setting("notification_sound", value)
        self._sound_row.set_selected(self._sound_index(value))
        self._refresh_sound_row()
        self._on_change()

    def _browse_sound(self) -> None:
        picker = Gtk.FileDialog(title=_("Choose a notification sound"))
        audio = Gtk.FileFilter()
        audio.set_name(_("Sound files"))
        audio.add_mime_type("audio/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(audio)
        picker.set_filters(filters)
        picker.set_default_filter(audio)
        if self._sound_index(self._sound_value) == 2:
            picker.set_initial_file(Gio.File.new_for_path(self._sound_value))

        def picked(picker: Gtk.FileDialog, result) -> None:
            try:
                file = picker.open_finish(result)
            except GLib.Error:
                file = None  # dismissed
            path = file.get_path() if file is not None else None
            if path:
                self._set_sound(path)
            else:
                self._sound_row.set_selected(self._sound_index(self._sound_value))

        picker.open(self.get_root(), None, picked)

    def _on_sound_play(self, _button: Gtk.Button) -> None:
        """Hear the choice, now — past the debounce and the single-flight
        gate, but never past the desktop's mute (see notifysound.play)."""
        notifysound.play(self._sound_value, force=True)

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

    def _on_cli_title_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("cli_title_sessions", row.get_active())
        self._on_change()

    def _on_attach_prompt_prs_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("attach_prompt_prs", row.get_active())


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
