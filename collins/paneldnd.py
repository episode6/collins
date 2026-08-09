# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Drag-and-drop for panel pages: the grip, the drop zones, the guard.

Three GTK pieces around the dock's move machinery, sharing this module
because they share one story — how a panel page travels by pointer:

- **The grip** (`make_grip`): a drag handle in each strip's tab bar that
  starts a *custom* drag carrying the strip's selected page. Custom
  because native Adwaita tab drags use internal content and cannot be
  seen by our `Gtk.DropTarget`s — so edge docking gets its own drag,
  with its own gtype (`PageDrag`).
- **The drop zones** (`DropZones`): an overlay across the whole dock,
  active only while a grip drag is in flight, that highlights and
  resolves edge/center zones over every visible leaf (geometry in
  dockzones.py) and hands the drop to the dock.
- **The guard wiring** (`guard_view`): connects an `Adw.TabView` to the
  process-wide `tabguard.guard`, bouncing pages that native Adwaita tab
  DnD drops into a view of the wrong group (policy and rationale in
  tabguard.py). The bounce is a deferred `transfer_page`, suppressed in
  the guard so its own detach/attach pair can't counter-bounce.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .dockzones import hit, zone_rect  # noqa: E402
from .i18n import _  # noqa: E402
from .tabguard import guard  # noqa: E402


class PageDrag(GObject.Object):
    """The content a grip drag carries: which page, from which strip.
    In-process only — the dock's drop zones are the sole target."""

    __gtype_name__ = "CollinsPageDrag"

    def __init__(self, strip, widget) -> None:
        super().__init__()
        self.strip = strip
        self.widget = widget


# -- the grip ----------------------------------------------------------------


def make_grip(strip) -> Gtk.Widget:
    """A drag handle for *strip*'s tab bar (end-action box): dragging it
    carries the strip's selected page, lighting the dock's drop zones.
    Inert (drag never starts) while the strip has no page mover — an
    undocked strip has nowhere to move pages to."""
    grip = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
    grip.add_css_class("dim-label")
    grip.set_tooltip_text(_("Drag to move this tab to another edge"))
    grip.set_cursor(Gdk.Cursor.new_from_name("grab"))

    source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
    pending: dict = {}  # the payload between prepare and drag-begin

    def on_prepare(_source, _x, _y):
        mover = strip.page_mover
        widget = strip.selected_page_widget()
        if mover is None or widget is None:
            return None
        payload = PageDrag(strip, widget)
        pending["payload"] = payload
        value = GObject.Value(PageDrag, payload)
        return Gdk.ContentProvider.new_for_value(value)

    def on_begin(_source, drag):
        payload = pending.pop("payload", None)
        if payload is None:
            return
        # A small title chip travels with the pointer; dragging the whole
        # page's WidgetPaintable would shadow the zones it is aiming for.
        chip = Gtk.Label(label=payload.widget.page_title())
        chip.add_css_class("card")
        chip.set_margin_top(4)
        chip.set_margin_bottom(4)
        chip.set_margin_start(8)
        chip.set_margin_end(8)
        Gtk.DragIcon.get_for_drag(drag).set_child(chip)
        strip.page_mover.begin_page_drag(strip, payload.widget)

    def on_end(_source, _drag, _delete):
        if strip.page_mover is not None:
            strip.page_mover.end_page_drag()

    source.connect("prepare", on_prepare)
    source.connect("drag-begin", on_begin)
    source.connect("drag-end", on_end)
    grip.add_controller(source)
    return grip


# -- the drop zones ----------------------------------------------------------


class DropZones(Gtk.Widget):
    """The dock-wide drop-zone overlay for grip drags.

    Hidden (and untargetable) until the dock's `begin_page_drag` shows it
    with a model of `(leaf widget, allowed zones)` pairs; then a single
    `Gtk.DropTarget` on the whole overlay resolves pointer position →
    (leaf, zone) against live leaf bounds and paints the would-be
    placement. `on_drop(payload, leaf_widget, zone)` receives a landed
    drop; the grip's drag-end hides the overlay again either way."""

    def __init__(self, on_drop) -> None:
        super().__init__(visible=False)
        self._on_drop = on_drop
        self._model: list = []  # (leaf widget, allowed zones)
        self._active: tuple | None = None  # (leaf index, zone, highlight rect)

        # Built bare, gtypes after: the constructor's formats arguments
        # don't survive PyGObject (droptarget-multi-gtype-construction).
        target = Gtk.DropTarget()
        target.set_gtypes([PageDrag])
        target.set_actions(Gdk.DragAction.MOVE)
        target.connect("enter", self._on_motion)
        target.connect("motion", self._on_motion)
        target.connect("leave", self._on_leave)
        target.connect("drop", self._on_dropped)
        self.add_controller(target)

    def begin(self, model: list) -> None:
        self._model = list(model)
        self._active = None
        self.set_visible(True)

    def end(self) -> None:
        self._model = []
        self._active = None
        self.set_visible(False)
        self.queue_draw()

    def _leaves(self) -> list:
        """The model's live bounds in overlay coordinates, recomputed per
        event so mid-drag relayouts (a source strip collapsing under the
        drag) can't leave the zones pointing at stale rectangles."""
        leaves = []
        for widget, allowed in self._model:
            ok, bounds = widget.compute_bounds(self)
            if not ok or not widget.get_mapped():
                leaves.append((0, 0, 0, 0, ()))  # unhittable placeholder
            else:
                leaves.append(
                    (bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height(), allowed)
                )
        return leaves

    def _on_motion(self, _target, x, y):
        leaves = self._leaves()
        found = hit(leaves, x, y)
        if found is None:
            active = None
        else:
            index, zone = found
            lx, ly, width, height, _allowed = leaves[index]
            active = (index, zone, zone_rect(lx, ly, width, height, zone))
        if active != self._active:
            self._active = active
            self.queue_draw()
        return Gdk.DragAction.MOVE if active is not None else 0

    def _on_leave(self, _target) -> None:
        self._active = None
        self.queue_draw()

    def _on_dropped(self, _target, value, x, y) -> bool:
        active = self._active
        self._active = None
        self.queue_draw()
        if not isinstance(value, PageDrag) or active is None:
            return False
        index, zone, _rect = active
        leaf_widget = self._model[index][0]
        self._on_drop(value, leaf_widget, zone)
        return True

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._active is None:
            return
        _index, _zone, (x, y, width, height) = self._active
        rect = Graphene.Rect().init(x, y, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(rect, 6)
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        fill = Gdk.RGBA()
        fill.red, fill.green, fill.blue, fill.alpha = accent.red, accent.green, accent.blue, 0.25
        edge = Gdk.RGBA()
        edge.red, edge.green, edge.blue, edge.alpha = accent.red, accent.green, accent.blue, 0.85
        snapshot.push_rounded_clip(rounded)
        snapshot.append_color(fill, rect)
        snapshot.pop()
        snapshot.append_border(rounded, [2.0, 2.0, 2.0, 2.0], [edge, edge, edge, edge])


# -- the native-DnD guard ----------------------------------------------------


def guard_view(view: Adw.TabView, group, fallback=None) -> None:
    """Register *view* with the process-wide tab guard as a member of
    *group* and wire the signals that drive it. Pages dropped here from
    another group are transferred right back out (see tabguard). The
    registration dies with the view; *fallback* (optional, per group)
    conjures a bounce destination when the whole group emptied mid-drag."""
    guard.register(view, group)
    if fallback is not None:
        guard.set_fallback(group, fallback)
    view.connect(
        "page-detached", lambda v, page, _pos: guard.on_detached(v, page.get_child())
    )
    view.connect("page-attached", _on_guarded_attach)
    view.connect("destroy", guard.unregister)


def _on_guarded_attach(view: Adw.TabView, page: Adw.TabPage, _pos: int) -> None:
    verdict = guard.should_bounce(view, page.get_child())
    if verdict is None:
        return
    group, origin = verdict
    widget = page.get_child()

    def bounce() -> bool:
        # Resolved now, not at judgment: a single-page source strip
        # collapses in an idle scheduled before this one, so the origin
        # view may already be gone (the fallback then conjures a strip).
        target = guard.bounce_target(group, prefer=origin)
        if target is None or target is view:
            return GLib.SOURCE_REMOVE
        for i in range(view.get_n_pages()):
            p = view.get_nth_page(i)
            if p.get_child() is widget:
                guard.suppressed = True
                try:
                    view.transfer_page(p, target, target.get_n_pages())
                finally:
                    guard.suppressed = False
                target.set_selected_page(p)
                break
        return GLib.SOURCE_REMOVE

    GLib.idle_add(bounce)
