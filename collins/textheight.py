# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The arithmetic behind a text box that grows with its text (growbox.py).

A `Gtk.TextView` in a scroller that propagates its natural height grows only
as far as the lines GTK has laid out. Writing a draft in lays out the lines
on screen at once (the box's minimum, three lines or so) and leaves the rest
to an idle at `GTK_TEXT_VIEW_PRIORITY_VALIDATE` (125), below every
`PRIORITY_DEFAULT` landing the app has — so a busy main loop holds a restored
draft at the box's minimum height for seconds. growbox measures the text with
Pango itself and holds the scroller at that height until the view's own
measure catches up; this module is the part of that which needs no widget.
"""

# How much of the text is laid out for the estimate: enough to fill any
# box's cap many times over, so a huge paste costs a bounded layout per
# frame instead of the whole thing.
PREFIX_LINES = 64
PREFIX_CHARS = 16000

# How long a hold may stand before it is released whatever the view says,
# so an estimate the view never agrees with (a font the layout misreads)
# can't keep a frame callback running for as long as the box is shown.
HOLD_S = 5.0

# How far the view's own height may fall short of the estimate and still
# count as caught up: Pango alone and the view's per-paragraph layout round
# a wrapped paragraph a pixel or so apart, and a view still waiting on its
# layout is a whole line or more short (64 against 208, measured).
SLACK_PX = 4


def measured_prefix(text: str) -> str:
    """The head of *text* the estimate lays out (see PREFIX_LINES)."""
    head = text[:PREFIX_CHARS]
    cut = -1
    for _ in range(PREFIX_LINES):
        cut = head.find("\n", cut + 1)
        if cut < 0:
            return head
    return head[:cut]


def floor_for(want: int, lowest: int, highest: int) -> int:
    """The scroller's minimum content height while a hold stands: the
    estimate *want* between the box's own bounds (*highest* < 0: no cap)."""
    if highest >= 0:
        want = min(want, highest)
    return max(lowest, want)


def caught_up(natural: int, want: int, highest: int) -> bool:
    """Whether the view's own natural height has reached what the hold
    stands in for, as far as the box would show it — past the cap every
    height looks the same — less SLACK_PX."""
    if highest >= 0:
        natural, want = min(natural, highest), min(want, highest)
    return natural >= want - SLACK_PX
