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

        self._easy_copy_row = Adw.SwitchRow(
            title=_("Easy copy & paste"),
            subtitle=_(
                "Ctrl+C copies selected text (otherwise interrupts as usual), "
                "Ctrl+V pastes, and right-click opens a copy/paste menu"
            ),
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

        sidebar_group = Adw.PreferencesGroup(title=_("Session list"))
        self._folder_path_row = Adw.SwitchRow(
            title=_("Show folder path"),
            subtitle=_("Show each session's project folder path in the sidebar"),
        )
        self._folder_path_row.set_active(bool(state.get_setting("show_folder_path")))
        self._folder_path_row.connect("notify::active", self._on_folder_path_changed)
        sidebar_group.add(self._folder_path_row)
        page.add(sidebar_group)

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

        self.add(page)

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

    def _on_easy_copy_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("easy_copy_paste", row.get_active())
        self._on_change()

    def _on_notify_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("notify_idle", row.get_active())
        self._on_change()

    def _on_folder_path_changed(self, row: Adw.SwitchRow, _pspec) -> None:
        self._state.set_setting("show_folder_path", row.get_active())
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
            ["sh", "-c", f"sleep 1.5; exec {shlex.quote(sys.executable)} -m claude_session_manager"],
            start_new_session=True,
        )
        app = Gio.Application.get_default()
        if app is not None:
            app.quit()
