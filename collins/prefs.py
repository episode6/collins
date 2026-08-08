# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-07. Full change history: git log for this file.

"""Preferences dialog: terminal font, scrollback, color scheme."""

from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import apppicker, claudemodels, editor, footerapps, prefssearch
from .caffeine import DURATION_KEYS, INDEFINITE, duration_label
from .i18n import LANGUAGES, N_, _
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

# What closing a running session's tab does when a setting stands in for the
# confirmation dialog. The labels match the dialog buttons they replace.
_RUNNING_BEHAVIORS = [
    ("ask", N_("Ask")),
    ("exit", N_("Exit Session")),
    ("background", N_("Background Session")),
]


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

        page = _SearchablePage(title=_("General"), icon_name="preferences-system-symbolic")
        self._page = page

        terminal_group = _SearchableGroup(title=_("Terminal"))

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

        self._attach_overlay_row = Adw.SwitchRow(
            title=_("Floating attach-file button"),
            subtitle=_(
                "Overlay a semi-transparent attach button on the corner of "
                "each agent terminal"
            ),
        )
        self._attach_overlay_row.set_active(bool(state.get_setting("attach_overlay_button")))
        self._attach_overlay_row.connect("notify::active", self._on_attach_overlay_changed)
        terminal_group.add(self._attach_overlay_row)

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
        page.add(terminal_group)

        if editor.HAVE_GTKSOURCE:
            self._build_editor_group(state, page)

        appearance_group = _SearchableGroup(title=_("Appearance"))
        scheme_row = Adw.ComboRow(title=_("Color scheme"))
        scheme_labels = [_(label) for _k, label, _s in _SCHEMES]
        scheme_row.set_model(Gtk.StringList.new(scheme_labels))
        current_scheme = state.get_setting("color_scheme") or "system"
        scheme_row.set_selected(
            next((i for i, (k, _l, _s) in enumerate(_SCHEMES) if k == current_scheme), 0)
        )
        scheme_row.connect("notify::selected", self._on_scheme_changed)
        appearance_group.add(_searchable(scheme_row, *scheme_labels))
        page.add(appearance_group)

        caffeine_group = _SearchableGroup(title=_("Caffeine Mode"))
        self._caffeine_launch_row = Adw.SwitchRow(
            title=_("Turn on at launch"),
            subtitle=_(
                "Start with Caffeine Mode already on, keeping the computer "
                "awake and the screen on until you turn it off from the header"
            ),
        )
        self._caffeine_launch_row.set_active(bool(state.get_setting("caffeine_on_launch")))
        self._caffeine_launch_row.connect("notify::active", self._on_caffeine_launch_changed)
        caffeine_group.add(self._caffeine_launch_row)
        # The same durations the button's context menu offers, so "on at
        # launch" doesn't have to mean "on until you remember it".
        self._caffeine_timer_row = Adw.ComboRow(
            title=_("Turn off after"),
            subtitle=_("How long that launch-time Caffeine Mode runs before it turns itself off"),
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

        sidebar_group = _SearchableGroup(title=_("Session list"))
        self._folder_path_row = Adw.SwitchRow(
            title=_("Show folder path"),
            subtitle=_("Show each session's project folder path in the sidebar"),
        )
        self._folder_path_row.set_active(bool(state.get_setting("show_folder_path")))
        self._folder_path_row.connect("notify::active", self._on_folder_path_changed)
        sidebar_group.add(self._folder_path_row)
        icon_size_row = Adw.SpinRow.new_with_range(16, 32, 2)
        icon_size_row.set_title(_("Project icon size"))
        icon_size_row.set_subtitle(_("Size of the project and folder icons in the sidebar"))
        icon_size_row.set_value(int(state.get_setting("project_icon_size") or 16))
        icon_size_row.connect("notify::value", self._on_icon_size_changed)
        sidebar_group.add(icon_size_row)
        self._usage_panel_row = Adw.SwitchRow(
            title=_("Show Claude usage"),
            subtitle=_("Show subscription usage limits below the session list"),
        )
        self._usage_panel_row.set_active(bool(state.get_setting("show_usage_panel")))
        self._usage_panel_row.connect("notify::active", self._on_usage_panel_changed)
        sidebar_group.add(self._usage_panel_row)
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
        sidebar_group.add(self._auto_title_row)
        self._pr_title_row = Adw.SwitchRow(
            title=_("Rename sessions after their pull requests"),
            subtitle=_(
                "Retitle a session to match the newest pull request opened "
                "in it; manually renamed sessions keep their name"
            ),
        )
        self._pr_title_row.set_active(bool(state.get_setting("pr_title_sessions")))
        self._pr_title_row.connect("notify::active", self._on_pr_title_changed)
        sidebar_group.add(self._pr_title_row)
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
        sidebar_group.add(self._pr_launch_row)
        page.add(sidebar_group)

        models_group = _SearchableGroup(
            title=_("Claude models"),
            description=_("Models the app's own headless claude runs ask for"),
        )
        self._model_rows: dict[str, Adw.ComboRow] = {}
        self._model_default_labels: dict[str, str] = {}
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
            models_group.add(_searchable(row, "haiku", "sonnet", "opus"))
        page.add(models_group)
        self._populate_model_rows(state)

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

        current_lang = state.get_setting("language") or ""
        self._initial_lang = current_lang
        current_label = next(
            (label for code, label in LANGUAGES if code == current_lang), LANGUAGES[0][1]
        )
        lang_group = _SearchableGroup(title=_("Language"), description=_("Restart to apply"))
        self._restart_btn = Gtk.Button(label=_("Restart now"), valign=Gtk.Align.CENTER)
        self._restart_btn.add_css_class("suggested-action")
        self._restart_btn.set_visible(False)
        self._restart_btn.connect("clicked", self._on_restart)
        lang_group.set_header_suffix(self._restart_btn)
        _searchable(lang_group, _("Restart now"))
        self._lang_expander = Adw.ExpanderRow(title=_("Language"), subtitle=current_label)
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
        lang_group.add(_searchable(self._lang_expander, *(label for _c, label in LANGUAGES)))
        page.add(lang_group)

        new_sessions_group = _SearchableGroup(title=_("New sessions"))
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
        new_sessions_group.add(self._worktree_row)
        page.add(new_sessions_group)

        running_group = _SearchableGroup(
            title=_("Running sessions"),
            description=_(
                "Ask keeps the confirmation dialog; the other choices skip it "
                "and exit the session(s) cleanly or keep them running detached"
            ),
        )
        self._add_running_behavior_row(
            running_group,
            _("When archiving a running session"),
            _("Archiving a session that is still running also closes its tab"),
            "archive_running_session",
        )
        self._add_running_behavior_row(
            running_group,
            _("When quitting with running sessions"),
            _("Closing a window while agent sessions are still running"),
            "quit_with_running_sessions",
        )
        page.add(running_group)

        notif_group = _SearchableGroup(title=_("Notifications"))
        self._notify_row = Adw.SwitchRow(
            title=_("Notify when a session goes idle"),
            subtitle=_("Desktop notification when a background tab stops producing output"),
        )
        self._notify_row.set_active(bool(state.get_setting("notify_idle")))
        self._notify_row.connect("notify::active", self._on_notify_changed)
        notif_group.add(self._notify_row)
        page.add(notif_group)

        bg_group = _SearchableGroup(title=_("Background sessions"))
        self._bg_poll_row = Adw.SwitchRow(
            title=_("Poll for background sessions"),
            subtitle=_(
                "Fallback: check the agent CLI every 20 seconds in case the "
                "yellow guide lines stop updating on their own"
            ),
        )
        self._bg_poll_row.set_active(bool(state.get_setting("background_status_poll")))
        self._bg_poll_row.connect("notify::active", self._on_bg_poll_changed)
        bg_group.add(self._bg_poll_row)
        page.add(bg_group)

        experimental_group = _SearchableGroup(title=_("Experimental"))
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
        experimental_group.add(self._progress_termprop_row)
        page.add(experimental_group)

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

    def _on_caffeine_launch_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("caffeine_on_launch", row.get_active())
        # Nothing to time when nothing is turned on at launch.
        self._caffeine_timer_row.set_sensitive(row.get_active())
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

    def _add_running_behavior_row(
        self, group: _SearchableGroup, title: str, subtitle: str, key: str
    ) -> None:
        row = Adw.ComboRow(title=title, subtitle=subtitle)
        labels = [_(label) for _v, label in _RUNNING_BEHAVIORS]
        row.set_model(Gtk.StringList.new(labels))
        values = [value for value, _l in _RUNNING_BEHAVIORS]
        current = self._state.get_setting(key)
        row.set_selected(values.index(current) if current in values else 0)
        row.connect("notify::selected", self._on_running_behavior_changed, key)
        group.add(_searchable(row, *labels))

    def _on_running_behavior_changed(self, row: Adw.ComboRow, _pspec, key: str) -> None:
        self._state.set_setting(key, _RUNNING_BEHAVIORS[row.get_selected()][0])
        self._on_change()

    def _on_notify_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("notify_idle", row.get_active())
        self._on_change()

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

    def _populate_model_rows(self, state: AppState) -> None:
        """Fill the model pickers from a live Models API query, off the main
        loop; the CLI's own aliases stand in when the API can't be asked."""

        def work() -> None:
            models = claudemodels.available_models() or list(claudemodels.FALLBACK_MODELS)
            GLib.idle_add(apply_models, models)

        def apply_models(models: list[claudemodels.ClaudeModel]) -> bool:
            for key, row in self._model_rows.items():
                current = (state.get_setting(key) or "").strip()
                ids = [""] + [m.id for m in models]
                labels = [self._model_default_labels[key]] + [m.display_name for m in models]
                if current and current not in ids:
                    # A saved model the API no longer lists stays visible and
                    # selected rather than silently snapping to the default.
                    ids.append(current)
                    labels.append(current)
                row.set_model(Gtk.StringList.new(labels))
                row.set_selected(ids.index(current))
                row.connect("notify::selected", self._on_model_row_changed, key, ids)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name="prefs-models", daemon=True).start()

    def _on_model_row_changed(
        self, row: Adw.ComboRow, _pspec, key: str, ids: list[str]
    ) -> None:
        self._state.set_setting(key, ids[row.get_selected()])
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
