# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Drag-and-drop for panel pages: per-tab drags, the drop zones, the guard.

Three GTK pieces around the dock's move machinery, sharing this module
because they share one story — how a panel page travels by pointer:

- **Per-tab drag sources** (`wire_tab_drag`): every strip tab carries a
  *custom* drag of its own page — custom because native Adwaita tab drags
  use internal content and cannot be seen by our `Gtk.DropTarget`s, so
  the drop zones get their own drag with their own gtype (`PageDrag`).
  The source rides the tab's private `AdwTab` widget (found by the same
  fail-soft walk `MainWindow._tab_widget` uses for the tab flash). The
  native drag it replaces — which can only reorder within a bar or land
  on another bar's guard, while this one can join, reorder positionally,
  and split — is stood down rather than raced (`disarm_native_drag`).
  The tab shows a drag-handle indicator icon beside its title as the
  affordance. Because all of it rides private widget internals, the
  `panel_tab_drag_handles` setting can turn it off: `unwire_tab_drag`
  reverses the mount, `restore_native_drag` gives Adwaita its gesture
  back, and each strip shows its end-of-bar grip (`make_grip`) — the
  drag-the-selected-page fallback that speaks the same drop-zone
  language.
- **The drop zones** (`DropZones`): an overlay across the whole dock,
  active only while a page drag is in flight, that highlights and
  resolves edge/center zones over every visible leaf (geometry in
  dockzones.py) and hands the drop — with its pointer position, for
  center-zone tab placement — to the dock.
- **The guard wiring** (`guard_view`): connects an `Adw.TabView` to the
  process-wide `tabguard.guard`, bouncing pages that native Adwaita tab
  DnD drops into a view of the wrong group (policy and rationale in
  tabguard.py). Strip tabs no longer *start* native drags, but the
  session tab bar and editor file tabs still do, and a strip's bar still
  receives them — the guard stays their bouncer. The bounce is a
  deferred `transfer_page`, suppressed in the guard so its own
  detach/attach pair can't counter-bounce.
"""

from __future__ import annotations

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from .dockzones import hit, insert_index, zone_rect  # noqa: E402
from .i18n import _  # noqa: E402
from .tabguard import guard  # noqa: E402


class PageDrag(GObject.Object):
    """The content a page drag carries — whether a tab's own handle or the
    fallback grip started it: which page, from which strip. In-process
    only — the dock's drop zones are the sole target."""

    __gtype_name__ = "CollinsPageDrag"

    def __init__(self, strip, widget) -> None:
        super().__init__()
        self.strip = strip
        self.widget = widget


# -- per-tab drag sources ----------------------------------------------------

# The visible handle: shown as each tab's indicator icon, beside the title.
HANDLE_ICON = "list-drag-handle-symbolic"


def wire_tab_drag(strip, widget) -> None:
    """Give *widget*'s tab in *strip* its own drag source, carrying the
    page itself. Deferred an idle: the private `AdwTab` this rides on is
    created by the tab bar's page-attached handler, which runs after the
    strip's own (the strip connects to the view before the bar exists).
    Transfers re-wire naturally — the destination bar builds a fresh
    `AdwTab` and the destination strip's page-attached lands here again.
    Fails soft: if a libadwaita bump renames the private type, the walk
    finds nothing and moving falls back to the tab context menu."""

    def wire() -> bool:
        if not getattr(strip, "tab_drag_handles", True):
            return GLib.SOURCE_REMOVE  # turned off while this idle was queued
        # This bar now has a tab that drags itself, so Adwaita's own tab
        # drag has no business in it (idempotent — every attach lands here).
        disarm_native_drag(strip.tab_bar)
        tab = _find_tab(strip.tab_bar, widget)
        if tab is not None and getattr(tab, "_collins_page_drag", None) is None:
            source = _page_drag_source(
                strip, lambda: widget if widget in strip.pages() else None
            )
            tab._collins_page_drag = source
            tab.add_controller(source)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(wire)


def unwire_tab_drag(strip, widget) -> None:
    """Undo `wire_tab_drag` for *widget*'s tab (the fallback setting turned
    per-tab drags off). Native tab dragging comes back with the strip's
    `restore_native_drag`, which the same funnel calls once for the bar."""
    tab = _find_tab(strip.tab_bar, widget)
    source = getattr(tab, "_collins_page_drag", None) if tab is not None else None
    if source is not None:
        tab.remove_controller(source)
        tab._collins_page_drag = None


def disarm_native_drag(bar: Adw.TabBar) -> None:
    """Take Adwaita's own tab drag out of *bar*'s event propagation.

    Per-tab sources were meant to beat it on claim priority alone: a
    `Gtk.DragSource` on the deep `AdwTab` sees a press before the
    `AdwTabBox` ancestor whose `GtkGestureDrag` implements Adwaita's tab
    reordering, so ours should claim the sequence first at the drag
    threshold. It doesn't, reliably — the two gestures don't start on the
    same terms. `GtkDragSource` refuses to begin a drag for
    `MIN_TIME_TO_DND` (100ms) after the press however far the pointer has
    travelled (gtkdragsource.c: the timeout armed in its `begin` gates
    the threshold check in its `update`), while Adwaita's gesture claims
    the moment the pointer passes that threshold. Cross the eight pixels
    inside 100ms — an ordinary quick flick, as opposed to a press that
    settles first — and the native drag claims the sequence and cancels
    ours: the whole tab follows the pointer, in a drag the drop zones
    can't see, rather than the page's title chip.

    So stand it down instead of racing it. Propagation phase NONE leaves
    the gesture in place but out of every chain, and touches nothing else
    the bar does — selection, middle-click close and the context menu
    ride other gestures, and the drop target that receives a foreign tab
    for the guard to bounce is a different controller again. Fails soft
    like every walk over these private widgets: a bar whose box the walk
    can't find simply keeps racing, exactly as it did before."""
    for gesture in _native_drag_gestures(bar):
        if getattr(gesture, "_collins_phase", None) is None:
            gesture._collins_phase = gesture.get_propagation_phase()
        gesture.set_propagation_phase(Gtk.PropagationPhase.NONE)


def restore_native_drag(bar: Adw.TabBar) -> None:
    """Undo `disarm_native_drag` — the `panel_tab_drag_handles` fallback
    needs Adwaita's gesture back on the phase it was built with, since
    with no per-tab source mounted it is the only way a tab drags at
    all."""
    for gesture in _native_drag_gestures(bar):
        phase = getattr(gesture, "_collins_phase", None)
        if phase is not None:
            gesture.set_propagation_phase(phase)
            gesture._collins_phase = None


def _native_drag_gestures(bar: Adw.TabBar) -> list:
    """The drag gesture of every `AdwTabBox` in *bar* — the controller
    behind Adwaita's tab reordering and its cross-bar DnD. Both boxes are
    walked (a strip pins nothing, but the pinned one costs nothing to
    visit), and the gesture is matched by exact gtype rather than
    `isinstance`, so a kinetic `GtkGesturePan` or `GtkGestureSwipe` — both
    `GtkGestureDrag` subclasses — could never be mistaken for it."""
    gestures: list = []

    def walk(node: Gtk.Widget) -> None:
        child = node.get_first_child()
        while child is not None:
            if child.__gtype__.name == "AdwTabBox":
                controllers = child.observe_controllers()
                for i in range(controllers.get_n_items()):
                    controller = controllers.get_item(i)
                    if controller.__gtype__.name == "GtkGestureDrag":
                        gestures.append(controller)
            else:
                walk(child)
            child = child.get_next_sibling()

    walk(bar)
    return gestures


def _find_tab(bar: Adw.TabBar, widget) -> Gtk.Widget | None:
    """The private `AdwTab` in *bar* whose page holds *widget*, or None.
    Matched through the tab's page property, never by position: the tabs
    sit in creation order in the widget tree, which reorders leave
    behind (see MainWindow._tab_widget, the same walk)."""

    def walk(node: Gtk.Widget) -> Gtk.Widget | None:
        child = node.get_first_child()
        while child is not None:
            if child.__gtype__.name == "AdwTab":
                # A just-closed tab lingers pageless while it animates out.
                page = child.get_property("page")
                if page is not None and page.get_child() is widget:
                    return child
            else:
                found = walk(child)
                if found is not None:
                    return found
            child = child.get_next_sibling()
        return None

    return walk(bar)


def make_grip(strip) -> Gtk.Widget:
    """A drag handle for *strip*'s tab bar (end-action box): dragging it
    carries the strip's *selected* page, lighting the dock's drop zones.
    The fallback affordance while `panel_tab_drag_handles` is off — the
    strip keeps it hidden otherwise. Inert without a page mover (an
    undocked strip has nowhere to move pages to)."""
    grip = Gtk.Image.new_from_icon_name(HANDLE_ICON)
    grip.add_css_class("dim-label")
    grip.set_tooltip_text(_("Drag to move this tab: drop on an edge to split, on a strip to join"))
    grip.set_cursor(Gdk.Cursor.new_from_name("grab"))
    grip.add_controller(_page_drag_source(strip, strip.selected_page_widget))
    return grip


def _page_drag_source(strip, resolve) -> Gtk.DragSource:
    """A drag source carrying the page `resolve()` answers at press time —
    a fixed page for a tab's own source (None once it left the strip), the
    selected page for the grip. On a tab it rides bubble phase, so it
    runs — and claims the drag — before the `AdwTabBox` ancestor whose
    own bubble gesture would otherwise start Adwaita's native tab drag.
    Inert while the strip has no page mover."""
    source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
    pending: dict = {}  # the payload between prepare and drag-begin

    def on_prepare(_source, _x, _y):
        widget = resolve()
        if strip.page_mover is None or widget is None:
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
    return source


def insert_position(strip, relative_to: Gtk.Widget, x: float) -> int:
    """Where a center-zone drop at *x* (in *relative_to*'s coordinates)
    inserts among *strip*'s tabs: before the first tab whose center lies
    right of the pointer (dockzones.insert_index). Tabs the walk can't
    find (private-type rename, an unmapped bar) don't count, degrading
    toward append — the pre-positional behavior."""
    centers = []
    for page_widget in strip.pages():
        tab = _find_tab(strip.tab_bar, page_widget)
        if tab is None:
            continue
        ok, bounds = tab.compute_bounds(relative_to)
        if ok:
            centers.append(bounds.get_x() + bounds.get_width() / 2)
    return insert_index(centers, x)


# -- the drop zones ----------------------------------------------------------


class DropZones(Gtk.Widget):
    """The dock-wide drop-zone overlay for page drags.

    Hidden (and untargetable) until the dock's `begin_page_drag` shows it
    with a model of `(leaf widget, allowed zones)` pairs; then a single
    `Gtk.DropTarget` on the whole overlay resolves pointer position →
    (leaf, zone) against live leaf bounds and paints the would-be
    placement. `on_drop(payload, leaf_widget, zone, x, y)` receives a
    landed drop with its overlay-relative pointer position (center drops
    place the tab by it); the drag's end hides the overlay either way."""

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
                # Negative size so no point can sit inside it — a zero-size
                # rect at the origin would still contain (0, 0) exactly and
                # swallow a drop on the overlay's top-left corner.
                leaves.append((0.0, 0.0, -1.0, -1.0, ()))
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
        self._on_drop(value, leaf_widget, zone, x, y)
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
