# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Keyed children of a container, patched rather than rebuilt — and the
scroll kept still while they are.

Grew up inside the PR page (prview), whose landed fetches used to empty both
views and build every card again for a reply that nine times in ten said
what the last one said. `Slots.plan` takes what a container should hold, as
(key, value, build) triples in order, and works out the fewest changes that
make it so: a child whose key is back with an equal value stays exactly as
it is, one whose value moved is built again in that place, and the rest are
inserted, reordered or removed around them. The native diff view patches
its file sections and hunks the same way (a reload keeps every untouched
hunk's widget, and with it the reader's selection), so the machinery lives
here for both.

The containers speak different dialects — a Box inserts after a sibling and
can reorder, a ListBox inserts at an index and wraps what it is given in a
row — so each has a subclass answering the three moves. `pin_scroll` keeps
whatever the reader is looking at where it is across a patch; `scroll_to`
puts a child's top at the top of its scroller, twice, because a just-built
buffer reports estimated heights first.

Widget code: not unit-tested (tests/conftest.py blocks GTK); exercised by
scripts/check_pr_page_patch.py and the diff view's probe.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


class Plan:
    """What one `Slots.plan` worked out, held back until `commit`.

    Between the two the owner gets the keyboard out of `dropped` — the
    widgets on their way out — since GTK re-places a focus it finds
    unparented somewhere the owner didn't choose. The building has already
    happened by then: `widgets` is the container's children as they will
    stand, in order, new and kept alike.
    """

    def __init__(
        self,
        slots: Slots,
        entries: list[tuple[object, object, Gtk.Widget]],
        dropped: list[Gtk.Widget],
        built: list[Gtk.Widget],
    ) -> None:
        self._slots = slots
        self._entries = entries
        self.dropped = dropped
        self._built = built

    @property
    def widgets(self) -> list[Gtk.Widget]:
        return [widget for _key, _value, widget in self._entries]

    @property
    def built(self) -> list[Gtk.Widget]:
        """The children this plan made new, in order."""
        return list(self._built)

    @property
    def kept(self) -> list[Gtk.Widget]:
        """The children that survive the patch, in order — what a scroll
        can be anchored on (see `pin_scroll`)."""
        return [widget for widget in self.widgets if widget not in self._built]

    def commit(self) -> list[Gtk.Widget]:
        """Make the container match: remove the dropped, insert the built
        where they go, move whatever kept its widget but not its place —
        and nothing else. Returns `widgets`."""
        slots = self._slots
        going = set(self.dropped)
        for widget in self.dropped:
            slots._remove(widget)
        # The container's children as they stand now, in order — the last
        # commit's, less the dropped — kept in step with every move below so
        # "is this one already where it goes" is a plain index check.
        current = [w for _k, _v, w in slots._entries if w not in going]
        previous: Gtk.Widget | None = None
        for index, (_key, _value, widget) in enumerate(self._entries):
            if widget in self._built:
                slots._insert(widget, previous)
                current.insert(index, widget)
            elif current[index] is not widget:
                slots._move(widget, previous)
                current.remove(widget)
                current.insert(index, widget)
            previous = widget
        slots._entries = self._entries
        return self.widgets


class Slots:
    """The keyed children of one container, patched rather than rebuilt.

    `plan` takes what the container should hold, as (key, value, build)
    triples in order; the values are frozen records (or None, for a child
    that is only ever itself), so "equal" is "says the same thing"; a key
    that repeats is told apart by its turn. `plan` and `clear` are the whole
    of what an owner says to a Slots.
    """

    def __init__(self) -> None:
        self._entries: list[tuple[object, object, Gtk.Widget]] = []

    @property
    def widgets(self) -> list[Gtk.Widget]:
        return [widget for _key, _value, widget in self._entries]

    def lookup(self, key: object) -> Gtk.Widget | None:
        """The child standing under *key* (its first showing), or None."""
        for entry_key, _value, widget in self._entries:
            if entry_key == key:
                return widget
        return None

    def plan(self, items: list[tuple[object, object, Callable[[], Gtk.Widget]]]) -> Plan:
        """Work out (and build) what it takes to hold *items* — see `Plan`."""
        old = {key: (value, widget) for key, value, widget in self._entries}
        seen: dict[object, int] = {}
        entries: list[tuple[object, object, Gtk.Widget]] = []
        dropped: list[Gtk.Widget] = []
        built: list[Gtk.Widget] = []
        for key, value, build in items:
            turn = seen.get(key, 0)
            seen[key] = turn + 1
            if turn:
                key = (key, turn)  # a repeated key: its nth showing
            previous = old.pop(key, None)
            if previous is not None and previous[0] == value:
                widget = previous[1]
            else:
                if previous is not None:
                    dropped.append(previous[1])
                widget = build()
                built.append(widget)
            entries.append((key, value, widget))
        dropped.extend(widget for _value, widget in old.values())
        return Plan(self, entries, dropped, built)

    def clear(self) -> None:
        """Let go of everything — for a state that isn't a patch of the last
        one: a loading spinner, or a setting that changes how an unchanged
        reply renders."""
        for widget in self.widgets:
            self._remove(widget)
        self._entries = []

    def _insert(self, widget: Gtk.Widget, after: Gtk.Widget | None) -> None:
        raise NotImplementedError

    def _move(self, widget: Gtk.Widget, after: Gtk.Widget | None) -> None:
        raise NotImplementedError

    def _remove(self, widget: Gtk.Widget) -> None:
        raise NotImplementedError


class BoxSlots(Slots):
    """`Slots` over a Gtk.Box. With an *anchor*, the box's children up to
    and including it are the caller's own and stay put; the slots are
    everything after (a file section's header, then its hunks)."""

    def __init__(self, box: Gtk.Box, anchor: Gtk.Widget | None = None) -> None:
        super().__init__()
        self._box = box
        self._anchor = anchor

    def _insert(self, widget: Gtk.Widget, after: Gtk.Widget | None) -> None:
        self._box.insert_child_after(widget, after or self._anchor)

    def _move(self, widget: Gtk.Widget, after: Gtk.Widget | None) -> None:
        self._box.reorder_child_after(widget, after or self._anchor)

    def _remove(self, widget: Gtk.Widget) -> None:
        self._box.remove(widget)


class ListSlots(Slots):
    """`Slots` over a Gtk.ListBox, whose children are rows: what `build`
    hands it must be a Gtk.ListBoxRow (the list would wrap anything else in
    one of its own, and the row is what it would then hand back)."""

    def __init__(self, list_box: Gtk.ListBox) -> None:
        super().__init__()
        self._list = list_box

    def _insert(self, widget: Gtk.Widget, after: Gtk.Widget | None) -> None:
        self._list.insert(widget, 0 if after is None else after.get_index() + 1)

    def _move(self, widget: Gtk.Widget, after: Gtk.Widget | None) -> None:
        self._list.remove(widget)
        self._insert(widget, after)

    def _remove(self, widget: Gtk.Widget) -> None:
        self._list.remove(widget)


def pin_scroll(scroller: Gtk.ScrolledWindow, kept: list[Gtk.Widget]) -> None:
    """Keep what the reader is looking at where it is across a patch.

    Called before the patch commits, with the column's children that
    survive it. The anchor is the first of them that reaches into the
    viewport — kept, so it will still be there after — and what is
    remembered is where its top sits against the viewport's. Once the
    patch has been laid out, the scroll is re-placed so the anchor is
    back at that offset, and a child above the viewport that came out of
    its rebuild taller shifts nothing the reader can see. With no kept
    child in view (the first load, a reset) the value itself is kept,
    clamped to whatever the column now allows.

    "Once laid out" is the frame clock's layout phase, joined after GDK's
    own handler (the one that allocates the window's tree): the new
    children's allocation is a frame away, so a value set now is set
    against the old one, and an idle is no better — the clock ticks on a
    timer, and an idle runs before it. Moving the adjustment from inside
    the phase asks for another pass, which the clock runs in the same
    frame: the reader never sees the unpinned one. An unrealized page has
    no clock and nothing on screen to keep still; an idle does there.
    """
    adj = scroller.get_vadjustment()
    value = adj.get_value()
    anchor: Gtk.Widget | None = None
    offset = 0.0
    for widget in kept:
        ok, bounds = widget.compute_bounds(scroller)
        if ok and bounds.get_y() + bounds.get_height() > 0:
            anchor, offset = widget, bounds.get_y()
            break

    def place() -> None:
        target = value
        if anchor is not None and anchor.get_parent() is not None:
            ok, bounds = anchor.compute_bounds(scroller)
            if ok:
                target = adj.get_value() + bounds.get_y() - offset
        adj.set_value(max(0.0, min(target, adj.get_upper() - adj.get_page_size())))

    clock = scroller.get_frame_clock()
    if clock is None:
        GLib.idle_add(lambda: (place(), GLib.SOURCE_REMOVE)[1])
        return
    handler: list[int] = []

    def on_layout(clock: Gdk.FrameClock) -> None:
        clock.disconnect(handler[0])
        place()

    handler.append(clock.connect_after("layout", on_layout))
    # A patch that changed nothing queues no layout of its own; the phase
    # is asked for so the handler runs (and lets go) regardless.
    clock.request_phase(Gdk.FrameClockPhase.LAYOUT)


def scroll_to(scroller: Gtk.ScrolledWindow, widget: Gtk.Widget | None) -> None:
    """Put *widget*'s top at the top of *scroller* — or, with None, land at
    the very end of the scroll.

    Placed twice: a just-built (or just-expanded) buffer reports estimated
    heights first, so the first placement lands short — the PRIORITY_LOW
    re-issue runs after layout settles and corrects it (the scroll_to_iter
    lesson from the editor, box-scroll flavored).
    """

    def place() -> bool:
        adj = scroller.get_vadjustment()
        end = adj.get_upper() - adj.get_page_size()
        if widget is None:
            adj.set_value(max(0.0, end))
            return GLib.SOURCE_REMOVE
        if widget.get_parent() is None:
            return GLib.SOURCE_REMOVE
        ok, bounds = widget.compute_bounds(scroller)
        if ok:
            target = adj.get_value() + bounds.get_y()
            adj.set_value(max(0.0, min(target, end)))
        return GLib.SOURCE_REMOVE

    place()
    GLib.idle_add(place, priority=GLib.PRIORITY_LOW)
