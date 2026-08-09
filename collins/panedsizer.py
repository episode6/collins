# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""One `Gtk.Paned`'s end-child sizing: remembered, persisted, re-applied.

Extracted from TerminalTab, which grew this machinery for the shell panel
(hardened in PR 229 against divider-clamp notifications poisoning the
remembered size) and kept a weaker copy for the editor paned. Every paned
with a size worth remembering owns one PanedSizer; the position arithmetic
lives GTK-free in panelsizing.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk  # noqa: E402

from .panelsizing import SizeMemory  # noqa: E402


class PanedSizer(GObject.Object):
    """Owns the remember/apply dance for one paned's end child.

    `key()` names the size the divider is currently expressing — the shell
    panel's flips between "bottom" and "right" with its orientation; a
    single-position paned returns a constant. `occupied()` gates
    remembering on the end child actually showing: a hidden child leaves
    the divider parked somewhere meaningless.
    """

    __gsignals__ = {
        # Emitted (debounced) when the user moves the divider: (key, size)
        # with size the new end-child px size, so the owner can persist it
        # as the app-wide default.
        "size-changed": (GObject.SignalFlags.RUN_FIRST, None, (str, int)),
    }

    def __init__(
        self,
        paned: Gtk.Paned,
        key: Callable[[], str],
        occupied: Callable[[], bool],
    ) -> None:
        super().__init__()
        self._paned = paned
        self._key = key
        self._occupied = occupied
        self._lookup: Callable[[str], int] | None = None
        self._memory = SizeMemory()
        self._apply_pending = False  # a programmatic divider set is queued
        self._apply_seq = 0  # invalidates superseded apply/settle chains
        self._emit_source: int | None = None  # debounce for size-changed
        self._emit_key: str | None = None  # key whose size changed last
        # Dragging the divider records the new size for the current key.
        paned.connect("notify::position", lambda *_: self.remember())

    def set_lookup(self, lookup: Callable[[str], int] | None) -> None:
        """`lookup(key) -> px` supplies the app-wide last-set size, used
        for keys this paned hasn't sized itself yet."""
        self._lookup = lookup

    def remembered(self, key: str) -> int:
        """This paned's remembered size for *key*, 0 when none yet."""
        return self._memory.get(key)

    def set_remembered(self, key: str, size: object) -> None:
        """Seed a remembered size (session restore); invalid values are
        ignored. Lands in this paned's own memory only — restoring a
        session must not disturb the app-wide defaults."""
        self._memory.set(key, size)

    def snapshot(self) -> dict[str, int]:
        """Every remembered size, for per-session persistence. Falsy when
        this paned was never sized."""
        return self._memory.snapshot()

    def _total(self) -> int:
        vertical = self._paned.get_orientation() == Gtk.Orientation.VERTICAL
        return self._paned.get_height() if vertical else self._paned.get_width()

    def remember(self) -> None:
        """Record the end child's size for the current key. Skipped while an
        apply is still queued — the value it would read is a stale layout's,
        and saving it would corrupt the remembered size the apply is about
        to use (the first-show width bug)."""
        if not self._occupied() or self._apply_pending:
            return
        key = self._key()
        size = self._memory.record(key, self._total(), self._paned.get_position())
        if size is None:
            return
        if self._emit_source is not None:
            GLib.source_remove(self._emit_source)
            if self._emit_key != key:
                # A different key's update is still pending (resize, then
                # swap within the debounce): flush it now rather than drop
                # it — each key's default must be preserved independently.
                self._emit_size_changed()
        self._emit_key = key
        self._emit_source = GLib.timeout_add(500, self._emit_size_changed)

    def _emit_size_changed(self) -> bool:
        self._emit_source = None
        key = self._emit_key
        if key is not None and self._memory.get(key) > 0:
            self.emit("size-changed", key, self._memory.get(key))
        return GLib.SOURCE_REMOVE

    def apply(self) -> None:
        """Position the divider once the paned's own size is known: this
        paned's remembered size for the current key, else the app-wide
        last-set size, else roughly a third of the paned.

        The apply-pending gate stays up until the position sticks. Right
        after the end child is (re)shown its content may still measure for
        an old layout (a VTE grid re-fits a frame or two later), so the
        paned clamps the fresh position on the next allocation — and that
        clamp arrives as a notify::position which remember() would record,
        silently overwriting the remembered size with the transient one.
        Holding the gate and re-asserting across a few layout passes keeps
        the clamp out of the books; if the size genuinely can't fit, we
        give up without recording it, so the user's choice survives for
        when there's room again.

        While the gate is up, remember() no-ops — including for hide and
        state-capture paths, which then fall back to the stored sizes.
        That's correct by construction: mid-settle, the stored size for
        this key is exactly the value being applied. A user who grabs the
        divider inside the settle window wins immediately: settle detects
        the drag and cedes (see below), rather than stomping it."""
        self._apply_pending = True
        self._apply_seq += 1
        seq = self._apply_seq
        # get_position() reflects the *requested* value right after a set, so
        # "did it stick?" can only be judged after the allocations that might
        # clamp it — hence wall-clock re-assertions rather than one idle.
        reasserts = [50, 150, 300]  # ms after the set; gate drops at the last

        def position() -> bool:
            if seq != self._apply_seq:
                return GLib.SOURCE_REMOVE  # a newer apply superseded this one
            key = self._key()
            fallback = (self._lookup(key) or 0) if self._lookup is not None else 0
            target = self._memory.target(key, self._total(), fallback)
            if target is None:
                self._apply_pending = False
                return GLib.SOURCE_REMOVE
            self._paned.set_position(target)
            for i, delay in enumerate(reasserts):
                GLib.timeout_add(delay, settle, target, i == len(reasserts) - 1)
            return GLib.SOURCE_REMOVE

        def settle(target: int, last: bool) -> bool:
            if seq != self._apply_seq:
                return GLib.SOURCE_REMOVE
            if self._drag_active():
                # The user grabbed the handle mid-settle: the position is
                # theirs now, not a clamp's. Cancel the rest of the chain
                # (the seq bump kills the queued timeouts), open the gate,
                # and record where their drag has the divider so far.
                self._apply_seq += 1
                self._apply_pending = False
                self.remember()
            elif last:
                # Give up quietly whether or not it stuck — one more set here
                # could clamp again *after* the gate drops and be recorded.
                # The remembered size must survive a clamp it didn't cause.
                self._apply_pending = False
            elif self._paned.get_position() != target:
                # clamped by a stale minimum — re-assert now that the end
                # child's content has had a layout pass to re-fit
                self._paned.set_position(target)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(position)

    def _drag_active(self) -> bool:
        """Whether the user is dragging this paned's own handle right now.
        The paned's internal gestures only recognize presses that land on
        the handle (presses elsewhere are denied and stay unrecognized), so
        any recognized gesture here means a live divider drag."""
        controllers = self._paned.observe_controllers()
        for i in range(controllers.get_n_items()):
            controller = controllers.get_item(i)
            if isinstance(controller, Gtk.Gesture) and controller.is_recognized():
                return True
        return False
