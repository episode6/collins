# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Pure position arithmetic for a paned's remembered end-child sizes.

Sizes are stored per *key* — a caller-defined label for the layout the
size belongs to (the shell panel remembers one size per "bottom"/"right"
mode; a single-position paned like the editor's uses one fixed key) — and
converted to and from divider positions against the paned's current total
extent. Everything GTK (the paned itself, timing, the apply/settle dance)
lives in panedsizer.PanedSizer; this module holds the arithmetic so it can
be unit-tested without a display.
"""

from __future__ import annotations

# The end child's share of a paned that has no remembered or app-wide size
# yet: the divider lands at 62% of the total, leaving roughly a third for
# the panel.
DEFAULT_FRACTION = 0.62

# The narrowest column worth opening a page in, and the room a paned's
# wide handle takes between its two children — the two constants in
# "would a new split beside the terminal cost it anything?" (see
# room_for_a_split).
MIN_SPLIT_SIZE = 240
HANDLE_SIZE = 8


def split_size(total: int, wanted: int = 0) -> int:
    """How wide a new strip split off a *total*-px leaf actually ends up:
    *wanted* (the app-wide last-set size) when it fits, else the same
    DEFAULT_FRACTION share `SizeMemory.target` falls back to. The two must
    agree — this is the size the divider is about to be given, not an
    estimate of it."""
    if 0 < wanted < total:
        return wanted
    return max(total - int(total * DEFAULT_FRACTION), 0)


def room_for_a_split(total: int, keep: int, wanted: int = 0) -> bool:
    """Whether a new column can be split off a *total*-px terminal without
    taking the terminal itself below *keep* px.

    *keep* is the terminal's maximum width (the "terminal_max_width"
    setting): past it the terminal stops growing and centers itself in
    whatever it was given, so every pixel beyond *keep* is gutter — room a
    new panel can have for free. 0 means no maximum, and then there is no
    free room at all: anything a split takes is width the terminal was
    using.

    The column is measured at the width it will really open at (see
    `split_size`), handle included, and one narrower than MIN_SPLIT_SIZE
    is refused however much room there is for it: a column too thin to
    read a pull request in is not a column worth opening one in.
    """
    if total <= 0 or keep <= 0:
        return False
    size = split_size(total, wanted)
    if size < MIN_SPLIT_SIZE:
        return False
    return total - size - HANDLE_SIZE >= keep


class SizeMemory:
    """Remembered end-child pixel sizes for one paned, keyed by mode."""

    def __init__(self) -> None:
        self._sizes: dict[str, int] = {}

    def get(self, key: str) -> int:
        """The remembered size for *key*, 0 when none has been recorded."""
        return self._sizes.get(key, 0)

    def set(self, key: str, size: object) -> None:
        """Seed a remembered size (session restore). Anything but a positive
        int is ignored — persisted state is untrusted."""
        if isinstance(size, int) and not isinstance(size, bool) and size > 0:
            self._sizes[key] = size

    def snapshot(self) -> dict[str, int]:
        """A copy of every remembered size, for per-session persistence.
        Empty when nothing has been recorded (falsy, so callers can test
        "was this paned ever sized?")."""
        return dict(self._sizes)

    def record(self, key: str, total: int, position: int) -> int | None:
        """Fold a live divider position into the memory: the end child's
        size is what's beyond the divider. Returns the new size when it is
        valid and different from what's remembered — the caller's cue to
        persist it — else None (paned not laid out yet, degenerate size, or
        no change)."""
        if total <= 0:
            return None
        size = total - position
        if size <= 0 or self._sizes.get(key) == size:
            return None
        self._sizes[key] = size
        return size

    def target(self, key: str, total: int, fallback: int = 0) -> int | None:
        """The divider position to apply for *key* in a paned *total* px
        across: this paned's remembered size, else *fallback* (the app-wide
        last-set size), else DEFAULT_FRACTION of the total. A size with no
        room to fit (>= total) falls to the fraction rather than the
        fallback — it stays remembered for when there's room again. None
        when the paned has no extent yet."""
        if total <= 0:
            return None
        size = self._sizes.get(key) or fallback or 0
        if 0 < size < total:
            return total - size
        return int(total * DEFAULT_FRACTION)
