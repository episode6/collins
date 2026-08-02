# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-01. Full change history: git log for this file.

"""Preferences dialog: terminal font, scrollback, color scheme."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk, Pango  # noqa: E402

from . import apppicker, editor, footerapps
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


def apply_color_scheme(value: str) -> None:
    for key, _label, scheme in _SCHEMES:
        if key == value:
            Adw.StyleManager.get_default().set_color_scheme(scheme)
            return


class PreferencesDialog(Adw.PreferencesDialog):
    """on_change() is called after any setting is saved, so the window can
    push the new settings into open terminal tabs."""

    def __init__(self, state: AppState, on_change: Callable[[], None]) -> None:
        super().__init__(title=_("Preferences"))
        self._state = state
        self._on_change = on_change

        page = Adw.PreferencesPage(title=_("General"), icon_name="preferences-system-symbolic")

        terminal_group = Adw.PreferencesGroup(title=_("Terminal"))

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
        terminal_group.add(self._theme_expander)
        page.add(terminal_group)

        if editor.HAVE_GTKSOURCE:
            self._build_editor_group(state, page)

        appearance_group = Adw.PreferencesGroup(title=_("Appearance"))
        scheme_row = Adw.ComboRow(title=_("Color scheme"))
        scheme_row.set_model(Gtk.StringList.new([_(label) for _k, label, _s in _SCHEMES]))
        current_scheme = state.get_setting("color_scheme") or "system"
        scheme_row.set_selected(
            next((i for i, (k, _l, _s) in enumerate(_SCHEMES) if k == current_scheme), 0)
        )
        scheme_row.connect("notify::selected", self._on_scheme_changed)
        appearance_group.add(scheme_row)
        page.add(appearance_group)

        caffeine_group = Adw.PreferencesGroup(title=_("Caffeine Mode"))
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
        self._caffeine_timer_row.set_model(
            Gtk.StringList.new([duration_label(key) for key in DURATION_KEYS])
        )
        current_timer = state.get_setting("caffeine_launch_timer") or INDEFINITE
        self._caffeine_timer_row.set_selected(
            DURATION_KEYS.index(current_timer) if current_timer in DURATION_KEYS
            else DURATION_KEYS.index(INDEFINITE)
        )
        self._caffeine_timer_row.set_sensitive(self._caffeine_launch_row.get_active())
        self._caffeine_timer_row.connect("notify::selected", self._on_caffeine_timer_changed)
        caffeine_group.add(self._caffeine_timer_row)
        page.add(caffeine_group)

        sidebar_group = Adw.PreferencesGroup(title=_("Session list"))
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
                "using the claude CLI (haiku); pre-existing sessions are "
                "titled locally from their prompt"
            ),
        )
        self._auto_title_row.set_active(bool(state.get_setting("auto_title_sessions")))
        self._auto_title_row.connect("notify::active", self._on_auto_title_changed)
        sidebar_group.add(self._auto_title_row)
        page.add(sidebar_group)

        self._footer_apps_group = Adw.PreferencesGroup(
            title=_("Footer apps"),
            description=_("Buttons in each tab's footer that open the tab's directory"),
        )
        add_app_btn = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add_app_btn.add_css_class("flat")
        add_app_btn.set_tooltip_text(_("Add application…"))
        add_app_btn.connect("clicked", self._on_add_footer_app)
        self._footer_apps_group.set_header_suffix(add_app_btn)
        self._footer_app_rows: list[Adw.PreferencesRow] = []
        self._rebuild_footer_apps()
        page.add(self._footer_apps_group)

        current_lang = state.get_setting("language") or ""
        self._initial_lang = current_lang
        current_label = next(
            (label for code, label in LANGUAGES if code == current_lang), LANGUAGES[0][1]
        )
        lang_group = Adw.PreferencesGroup(title=_("Language"), description=_("Restart to apply"))
        self._restart_btn = Gtk.Button(label=_("Restart now"), valign=Gtk.Align.CENTER)
        self._restart_btn.add_css_class("suggested-action")
        self._restart_btn.set_visible(False)
        self._restart_btn.connect("clicked", self._on_restart)
        lang_group.set_header_suffix(self._restart_btn)
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
        lang_group.add(self._lang_expander)
        page.add(lang_group)

        notif_group = Adw.PreferencesGroup(title=_("Notifications"))
        self._notify_row = Adw.SwitchRow(
            title=_("Notify when a session goes idle"),
            subtitle=_("Desktop notification when a background tab stops producing output"),
        )
        self._notify_row.set_active(bool(state.get_setting("notify_idle")))
        self._notify_row.connect("notify::active", self._on_notify_changed)
        notif_group.add(self._notify_row)
        page.add(notif_group)

        bg_group = Adw.PreferencesGroup(title=_("Background sessions"))
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

        experimental_group = Adw.PreferencesGroup(title=_("Experimental"))
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

        self.add(page)

    def _build_editor_group(self, state: AppState, page: Adw.PreferencesPage) -> None:
        editor_group = Adw.PreferencesGroup(title=_("Editor"))

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
        editor_group.add(self._editor_scheme_expander)

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
