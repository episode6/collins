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
    """Owns the remember/apply dance for one paned's managed child.

    `key()` names the size the divider is currently expressing — the shell
    panel's flips between "bottom" and "right" with its orientation; a
    single-position paned returns a constant. `occupied()` gates
    remembering on the managed child actually showing: a hidden child
    leaves the divider parked somewhere meaningless.

    The managed child — the one whose pixel size is remembered — is the
    end child by default. A dock split that puts its panel left of (or
    above) the fixed content manages the start child instead
    (`end_child=False`); SizeMemory always thinks in "position of the
    divider counted from the managed child's far edge", so both flavors
    share the arithmetic via the position↔raw-position mirror below.
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
        end_child: bool = True,
    ) -> None:
        super().__init__()
        self._paned = paned
        self._key = key
        self._occupied = occupied
        self._end_child = end_child
        self._lookup: Callable[[str], int] | None = None
        self._memory = SizeMemory()
        self._apply_pending = False  # a programmatic divider set is queued
        self._apply_seq = 0  # invalidates superseded apply/settle chains
        self._emit_source: int | None = None  # debounce for size-changed
        self._emit_key: str | None = None  # key whose size changed last
        # Dragging the divider records the new size for the current key.
        paned.connect("notify::position", lambda *_: self.remember())

    @property
    def manages_end(self) -> bool:
        """Whether the managed child sits in the paned's end slot — the
        layout serializer records it so a restored paned rebuilds with the
        same arithmetic orientation."""
        return self._end_child

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

    def _mirrored(self) -> int:
        """The live divider position in SizeMemory's space: counted so that
        `total - position` is always the managed child's size. For an
        end-managed paned that's the raw position; start-managed mirrors."""
        position = self._paned.get_position()
        return position if self._end_child else self._total() - position

    def _raw(self, position: int) -> int:
        """A SizeMemory-space position converted back to the paned's own.
        Self-inverse, recomputed against the live total — as the paned
        resizes, a start-managed conversion tracks the panel's size, not a
        stale pixel offset."""
        return position if self._end_child else self._total() - position

    def remember(self) -> None:
        """Record the end child's size for the current key. Skipped while an
        apply is still queued — the value it would read is a stale layout's,
        and saving it would corrupt the remembered size the apply is about
        to use (the first-show width bug).

        Skipped, that is, unless the user is dragging the divider right
        now: a drag inside the apply/settle window is still theirs to make,
        and the settle checkpoints alone can miss one made quickly between
        them — leaving the panel at the dragged size but the size never
        recorded (so never persisted app-wide). A live gesture is proof the
        position isn't a clamp's, so the apply chain cedes here exactly as
        a checkpoint would have: cancel it, open the gate, and fall through
        to record."""
        if not self._occupied():
            return
        if self._apply_pending:
            if not self._drag_active():
                return
            self._apply_seq += 1
            self._apply_pending = False
        key = self._key()
        size = self._memory.record(key, self._total(), self._mirrored())
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
        # A paned created this very frame (a fresh dock split) has no extent
        # in the first idle; wait out its first allocation instead of giving
        # up, bounded so a never-allocated paned can't hold the gate forever.
        waits = [0]

        def position() -> bool:
            if seq != self._apply_seq:
                return GLib.SOURCE_REMOVE  # a newer apply superseded this one
            if self._total() <= 0 and waits[0] < 10:
                waits[0] += 1
                GLib.timeout_add(50, position)
                return GLib.SOURCE_REMOVE
            key = self._key()
            fallback = (self._lookup(key) or 0) if self._lookup is not None else 0
            target = self._memory.target(key, self._total(), fallback)
            if target is None:
                self._apply_pending = False
                return GLib.SOURCE_REMOVE
            self._paned.set_position(self._raw(target))
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
            elif self._paned.get_position() != self._raw(target):
                # clamped by a stale minimum — re-assert now that the
                # managed child's content has had a layout pass to re-fit
                self._paned.set_position(self._raw(target))
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
