# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The Keyboard Bindings dialog: every shortcut in the catalogue, one row
each, rebound by clicking the row and pressing the new chord.

What it edits is the "keybindings" setting (see keybindings.py); the
window it belongs to is told after each save and rebuilds its controllers
from the setting, so a change is live the moment the capture closes —
nothing here touches a controller itself.

While a chord is being captured the window's own shortcuts are suspended
(see `suspend`): the capture dialog lives inside the window's widget tree,
and the window's capture-phase controller would otherwise act on Ctrl+W
before the dialog could record it.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from . import dialogs, keybindings, keymap  # noqa: E402
from .i18n import _  # noqa: E402
from .state import AppState  # noqa: E402


class KeyboardBindingsDialog(Adw.Dialog):
    """*on_change* runs after every save; *suspend(True/False)* takes the
    window's shortcuts off and puts them back around a capture."""

    def __init__(
        self,
        state: AppState,
        on_change: Callable[[], None],
        suspend: Callable[[bool], None],
    ) -> None:
        super().__init__(title=_("Keyboard Bindings"), content_width=640, content_height=700)
        self._state = state
        self._on_change = on_change
        self._suspend = suspend
        self._rows: dict[str, Adw.ActionRow] = {}
        self._suffixes: dict[str, Gtk.Box] = {}

        self._reset_all_btn = Gtk.Button(label=_("Reset All"), valign=Gtk.Align.CENTER)
        self._reset_all_btn.add_css_class("flat")
        self._reset_all_btn.set_tooltip_text(_("Put every shortcut back to its default"))
        self._reset_all_btn.connect("clicked", self._on_reset_all)

        header = Adw.HeaderBar()
        header.set_title_widget(
            Adw.WindowTitle(
                title=_("Keyboard Bindings"), subtitle=_("Click a row to change its shortcut")
            )
        )
        header.pack_end(self._reset_all_btn)

        page = Adw.PreferencesPage()
        for group_id, group_label in keybindings.GROUP_LABELS.items():
            group = Adw.PreferencesGroup(title=_(group_label))
            for binding in keybindings.BINDINGS:
                if binding.group != group_id:
                    continue
                row = Adw.ActionRow(title=_(binding.label), activatable=True)
                if binding.note:
                    row.set_subtitle(_(binding.note))
                suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                suffix.set_valign(Gtk.Align.CENTER)
                row.add_suffix(suffix)
                row.connect("activated", self._on_row_activated, binding.action)
                group.add(row)
                self._rows[binding.action] = row
                self._suffixes[binding.action] = suffix
            page.add(group)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(page)
        self.set_child(view)
        self._refresh()

    # -- state ----------------------------------------------------------------

    @property
    def _custom(self) -> dict[str, list[str]]:
        return keybindings.sanitize(self._state.get_setting(keybindings.SETTING))

    def _save(self, custom: dict[str, list[str]]) -> None:
        self._state.set_setting(keybindings.SETTING, custom)
        self._on_change()
        self._refresh()

    def _refresh(self) -> None:
        """Redraw every row's chord, reset button and conflict mark from
        the setting as it now stands."""
        custom = self._custom
        resolved = keybindings.resolve(custom)
        conflicts = keybindings.conflicts(custom)
        for action, suffix in self._suffixes.items():
            while (child := suffix.get_first_child()) is not None:
                suffix.remove(child)
            clashing = [
                other
                for accelerator in resolved[action]
                for other in conflicts.get(accelerator, ())
                if other != action
            ]
            if clashing:
                names = ", ".join(_(keybindings.BY_ACTION[o].label) for o in clashing)
                warning = Gtk.Image(icon_name="dialog-warning-symbolic")
                warning.add_css_class("warning")
                warning.set_tooltip_text(_("Also bound to: {actions}").format(actions=names))
                suffix.append(warning)
            if resolved[action]:
                for accelerator in resolved[action]:
                    suffix.append(_keycaps(accelerator))
            else:
                unbound = Gtk.Label(label=_("Unbound"))
                unbound.add_css_class("dim-label")
                suffix.append(unbound)
            if keybindings.is_customized(custom, action):
                reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER)
                reset.add_css_class("flat")
                reset.set_tooltip_text(_("Reset to default"))
                reset.connect("clicked", self._on_reset_one, action)
                suffix.append(reset)
        self._reset_all_btn.set_sensitive(bool(custom))

    # -- handlers ----------------------------------------------------------------

    def _on_reset_one(self, _button: Gtk.Button, action: str) -> None:
        custom = self._custom
        custom.pop(action, None)
        self._save(custom)

    def _on_reset_all(self, _button: Gtk.Button) -> None:
        dialogs.confirm_dialog(
            self,
            _("Reset every shortcut?"),
            _("All of your custom keyboard bindings are replaced by the defaults."),
            _("Reset All"),
            lambda: self._save({}),
        )

    def _on_row_activated(self, _row: Adw.ActionRow, action: str) -> None:
        _CaptureDialog(self, action, self._custom, self._apply_capture, self._suspend)

    def _apply_capture(self, action: str, accelerator: str | None) -> None:
        """*accelerator* None = unbind. A chord another action holds is
        taken from it, after asking."""
        custom = self._custom
        if accelerator is None:
            self._save(keybindings.with_binding(custom, action, []))
            return
        others = keybindings.holders(custom, accelerator, except_action=action)
        if not others:
            self._save(keybindings.with_binding(custom, action, [accelerator]))
            return

        def reassign() -> None:
            updated = self._custom
            for other in others:
                remaining = [
                    a for a in keybindings.resolve(updated)[other]
                    if keybindings.canonical(a) != accelerator
                ]
                updated = keybindings.with_binding(updated, other, remaining)
            self._save(keybindings.with_binding(updated, action, [accelerator]))

        names = ", ".join(_(keybindings.BY_ACTION[o].label) for o in others)
        dialogs.confirm_dialog(
            self,
            _("{chord} is already in use").format(chord=keybindings.label(accelerator)),
            _("It is bound to {actions}. Move it to {action}?").format(
                actions=names, action=_(keybindings.BY_ACTION[action].label)
            ),
            _("Move Shortcut"),
            reassign,
            destructive=False,
        )


class _Chord(Gtk.Box):
    """A box under Gtk.ShortcutLabel's CSS name: Adwaita draws keycaps only
    as `shortcut > .keycap`, so a row of our own labels has to sit under a
    "shortcut" node to get the boxes."""


_Chord.set_css_name("shortcut")


def _keycaps(accelerator: str) -> Gtk.Widget:
    """A chord as a row of keycaps — "Ctrl" "+" "Shift" "+" "T" — spelled
    the way the docs and tooltips spell it (keybindings.label), rather than
    Gtk.ShortcutLabel's "Shift + Ctrl + Page_Down"."""
    box = _Chord(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, valign=Gtk.Align.CENTER)
    parts = keybindings.label_parts(accelerator)
    for index, part in enumerate(parts):
        if index:
            box.append(Gtk.Label(label="+", css_classes=["dim-label"]))
        box.append(Gtk.Label(label=part, css_classes=["keycap"]))
    return box


class _CaptureDialog:
    """One key press, recorded. Escape keeps the current binding, Backspace
    alone removes it, anything else (modifiers and all) becomes the new
    chord — including Enter, which the alert would otherwise answer with."""

    def __init__(
        self,
        parent: Gtk.Widget,
        action: str,
        custom: dict[str, list[str]],
        on_done: Callable[[str, str | None], None],
        suspend: Callable[[bool], None],
    ) -> None:
        self._action = action
        self._on_done = on_done
        self._suspend = suspend
        self._result: str | None = None
        self._decided = False

        current = keybindings.resolve(custom)[action]
        current_text = (
            ", ".join(keybindings.label(a) for a in current) if current else _("unbound")
        )
        self._dialog = Adw.AlertDialog(
            heading=_("Set shortcut for “{action}”").format(
                action=_(keybindings.BY_ACTION[action].label)
            ),
            body=_(
                "Press the new key combination. Currently: {current}.\n"
                "Backspace removes the binding; Escape keeps it."
            ).format(current=current_text),
        )
        self._dialog.add_response("cancel", _("Cancel"))
        self._dialog.set_close_response("cancel")
        self._dialog.connect("closed", self._on_closed)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self._dialog.add_controller(keys)

        self._suspend(True)
        self._dialog.present(parent)

    def _on_key_pressed(self, controller: Gtk.EventControllerKey, keyval: int, _code: int, state) -> bool:
        event = controller.get_current_event()
        mods = int(state) & int(Gtk.accelerator_get_default_mod_mask())
        if keyval == Gdk.KEY_Escape and not mods:
            self._dialog.close()
            return True
        if keyval == Gdk.KEY_BackSpace and not mods:
            self._decide(None)
            return True
        accelerator = keymap.accelerator_for_press(event) if event is not None else None
        if accelerator is None:
            return False  # a bare modifier: the chord isn't finished yet
        self._decide(accelerator)
        return True

    def _decide(self, accelerator: str | None) -> None:
        self._decided = True
        self._result = accelerator
        self._dialog.close()

    def _on_closed(self, _dialog) -> None:
        self._suspend(False)
        if self._decided:
            self._on_done(self._action, self._result)
