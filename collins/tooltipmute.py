"""Silence background tooltips while a menu is open.

A menu popover grabs the pointer, but GTK's tooltip machinery does not follow
the grab: gtk_main_do_event hands the tooltip code the widget it *picked*
under the pointer, not the widget the grab routes the event to. So with a
context menu up, sweeping the pointer across the window behind it still pops
tooltips — for controls the click can no longer even reach, in a bubble that
lands over or behind the menu depending on how the compositor stacks the two.

GTK4 dropped the gtk-enable-tooltips setting, so there is no switch to flip.
What there is: gtk_tooltip_run_requery only asks a widget for a tooltip while
its has-tooltip flag is set. Clearing that flag is the mute; putting it back
when the last menu closes is the unmute. Widgets inside an open menu keep
theirs — the menu's own tooltips are the ones that belong on screen.

It all hangs off emission hooks rather than a call at each popup site, so the
menus we don't build ourselves — AdwTabView's tab menu, every GtkMenuButton's
— are covered too.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

_installed = False
# Menus on screen right now; more than one only while a menu opens over another.
_open: list[Gtk.Popover] = []
# Widgets we cleared has-tooltip on, to be re-armed when the last menu closes.
_muted: list[Gtk.Widget] = []
# The query-tooltip hook, live only while something is muted (see _mute).
_query_hook: int | None = None


def install() -> None:
    """Start watching for menus. Called once, at application startup."""
    global _installed
    if _installed:
        return
    _installed = True
    GObject.add_emission_hook(Gtk.Widget, "map", _on_map)
    GObject.add_emission_hook(Gtk.Widget, "unmap", _on_unmap)


def _on_map(widget: Gtk.Widget, *_args: object) -> bool:
    # Only a popover that autohides takes the grab that makes the UI behind it
    # untouchable; one that doesn't leaves that UI live, tooltips included.
    if isinstance(widget, Gtk.Popover) and widget.get_autohide():
        _open.append(widget)
        _mute()
    return True  # keep the hook installed


def _on_unmap(widget: Gtk.Widget, *_args: object) -> bool:
    if isinstance(widget, Gtk.Popover) and widget in _open:
        _open.remove(widget)
        if not _open:
            _unmute()
    return True


def _mute() -> None:
    """Clear has-tooltip on everything outside the open menus."""
    global _query_hook
    toplevels = Gtk.Window.get_toplevels()
    for i in range(toplevels.get_n_items()):
        window = toplevels.get_item(i)
        _mute_tree(window)
        # A tooltip already on screen when the menu opened outlives the sweep:
        # muting a widget doesn't retract the bubble it is already showing.
        # A requery does, since the mute leaves nothing under the pointer with
        # a tooltip to give. (Usually moot — the button press that opens a
        # context menu hides the tooltip itself — but a menu opened from the
        # keyboard gets no such press.)
        window.trigger_tooltip_query()
    if _query_hook is None:
        # The sweep only reaches what exists at the time. Anything built while
        # the menu is up — a sidebar row a background scan adds, a label that
        # is handed its tooltip text late — is caught here instead. GTK asks a
        # widget for its tooltip twice: once when the pointer settles on it,
        # and again half a second later when it's about to show the bubble. By
        # that second ask this has muted the widget and its ancestors, so the
        # requery walks off the top of the window empty-handed.
        _query_hook = GObject.add_emission_hook(Gtk.Widget, "query-tooltip", _on_query)


def _on_query(widget: Gtk.Widget, *_args: object) -> bool:
    if _open and not _in_menu(widget):
        # Ancestors too: a requery that finds this widget muted just asks its
        # parent instead, and the parent is as much behind the menu as it is.
        node: Gtk.Widget | None = widget
        while node is not None:
            _mute_widget(node)
            node = node.get_parent()
    return True


def _in_menu(widget: Gtk.Widget) -> bool:
    node: Gtk.Widget | None = widget
    while node is not None:
        if node in _open:
            return True
        node = node.get_parent()
    return False


def _mute_tree(widget: Gtk.Widget) -> None:
    if widget in _open:
        return  # the menu itself, tooltips and all
    _mute_widget(widget)
    child = widget.get_first_child()
    while child is not None:
        _mute_tree(child)
        child = child.get_next_sibling()


def _mute_widget(widget: Gtk.Widget) -> None:
    if widget.get_has_tooltip():
        widget.set_has_tooltip(False)
        _muted.append(widget)


def _unmute() -> None:
    global _query_hook
    if _query_hook is not None:
        GObject.remove_emission_hook(Gtk.Widget, "query-tooltip", _query_hook)
        _query_hook = None
    for widget in _muted:
        widget.set_has_tooltip(True)
    _muted.clear()
