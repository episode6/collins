#!/usr/bin/env python3
"""Headless check that termstream's responder answers as VTE does — run on a
dev machine or by the e2e suite, never by pytest.

The service answers the terminal's queries itself and strips them from what
clients see (split-service spec §3.3, D8), so its answers have to be VTE's,
byte for byte. This feeds every query in the responder's table to a real
`Vte.Terminal` with no child (120x40, VTE's own colours, then a client's),
collects what VTE commits, and compares it with what
`collins.service.termstream.StreamFilter` answers to the same bytes:

- each row of F8's table and F12's additions, one at a time;
- DECRQM for every mode 0 to 9999, DEC private and ANSI, on a fresh terminal
  (the responder's mode table is VTE's);
- every mode VTE can set, set, reset, and set then soft-reset (DECSTR);
- the 256 palette entries, with BEL and with ST;
- a client's colours (dark and light, a cursor colour) mirrored into the
  responder's state;
- the cursor report in origin mode and at a pending wrap;
- a round of queries closed by DA1, answered in the order asked (F11).

A query VTE answers is collected up to a sentinel: ``CSI 5 n`` is fed last
and its ``CSI 0 n`` is the last answer committed (F3), so no timing is
involved. Nothing Collins-shaped runs: no App, no session, no state.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_termstream_answers.py

Run it behind the headless wrapper, or a window opens on the user's screen.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, GLib, Gtk, Vte  # noqa: E402

from collins.service import termstream  # noqa: E402
from collins.service.termstream import StreamFilter, TerminalState, rgb16  # noqa: E402

DEADLINE_S = 100
GLib.timeout_add_seconds(DEADLINE_S, lambda: (print("FAIL deadline", flush=True), os._exit(2)))

CSI = b"\x1b["
OSC = b"\x1b]"
DCS = b"\x1bP"
ST = b"\x1b\\"
BEL = b"\x07"
RIS = b"\x1bc"
SENTINEL = CSI + b"5n"
SENTINEL_ANSWER = b"\x1b[0n"
FOCUS = (b"\x1b[I", b"\x1b[O")

TABLE = [
    ("DA1", CSI + b"c"),
    ("DA1 0", CSI + b"0c"),
    ("DA2", CSI + b">c"),
    ("DA2 0", CSI + b">0c"),
    ("DA3", CSI + b"=c"),
    ("DA3 0", CSI + b"=0c"),
    ("XTVERSION", CSI + b">q"),
    ("XTVERSION 0", CSI + b">0q"),
    ("kitty keyboard", CSI + b"?u"),
    ("DSR 5", CSI + b"5n"),
    ("CPR", CSI + b"6n"),
    ("DECXCPR", CSI + b"?6n"),
    ("OSC 10 ST", OSC + b"10;?" + ST),
    ("OSC 10 BEL", OSC + b"10;?" + BEL),
    ("OSC 11 ST", OSC + b"11;?" + ST),
    ("OSC 11 BEL", OSC + b"11;?" + BEL),
    ("OSC 12 ST", OSC + b"12;?" + ST),
    ("OSC 12 BEL", OSC + b"12;?" + BEL),
    ("OSC 4 ST", OSC + b"4;1;?" + ST),
    ("OSC 4 two", OSC + b"4;1;?;244;?" + BEL),
    ("DECRQM 2004", CSI + b"?2004$p"),
    ("DECRQM 2026", CSI + b"?2026$p"),
    ("DECRQM 1016", CSI + b"?1016$p"),
    ("DECRQM 9999", CSI + b"?9999$p"),
    ("DECRQM ANSI 4", CSI + b"4$p"),
    ("XTGETTCAP TN", DCS + b"+q544e" + ST),
    ("XTGETTCAP TN upper", DCS + b"+q544E" + ST),
    ("DECRQSS SGR", DCS + b"$qm" + ST),
    ("size in cells", CSI + b"18t"),
    ("size in pixels", CSI + b"14t"),
    ("cell size", CSI + b"16t"),
    ("title report", CSI + b"21t"),
    ("colour scheme", CSI + b"?996n"),
    ("XTQMODKEYS", CSI + b"?4m"),
    ("ENQ", b"\x05"),
    ("kitty graphics query", b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA" + ST),
]

failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}"), flush=True)
    if not ok:
        failures.append(name)


window = Gtk.Window()
window.set_default_size(1600, 1000)
window.present()
commits: list[bytes] = []
context = GLib.MainContext.default()


def fresh_terminal():
    """A new childless VTE at 120x40 in the window, its commits collected.

    VTE resizes its grid to its allocation, and a resize resets the scroll
    region: under CI's Xvfb a terminal swapped into the window came up at
    24x11, so an origin-mode cursor report lost its region. Aligned to the
    start of a larger window it keeps the grid it was given (F2), and the
    grid is waited for before anything is fed."""
    terminal = Vte.Terminal()
    terminal.set_size(120, 40)
    terminal.set_halign(Gtk.Align.START)
    terminal.set_valign(Gtk.Align.START)
    terminal.connect("commit", lambda _t, text, _size: commits.append(text.encode()))
    window.set_child(terminal)
    pump_until(
        lambda: terminal.get_mapped()
        and terminal.get_char_width() > 0
        and (terminal.get_column_count(), terminal.get_row_count()) == (120, 40)
    )
    pump_until(lambda: False, timeout=0.1)
    grid = (terminal.get_column_count(), terminal.get_row_count())
    if grid != (120, 40):
        check("a fresh terminal holds 120x40", False, f"grid {grid}")
    return terminal


def pump_until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


def vte_answers(data: bytes) -> list[bytes]:
    """What VTE commits to `data`, up to the sentinel's answer."""
    commits.clear()
    term.feed(data + SENTINEL)
    if not pump_until(lambda: SENTINEL_ANSWER in commits):
        return [b"<no sentinel>"]
    got = [c for c in commits if c not in FOCUS]
    last = len(got) - 1 - got[::-1].index(SENTINEL_ANSWER)
    return got[:last]


def state_for_vte() -> TerminalState:
    return TerminalState(
        cols=term.get_column_count(),
        rows=term.get_row_count(),
        cell_width=term.get_char_width(),
        cell_height=term.get_char_height(),
    )


VTE_VERSION = (Vte.get_major_version(), Vte.get_minor_version(), Vte.get_micro_version())
print("VTE {}.{}.{}".format(*VTE_VERSION), flush=True)
term = fresh_terminal()
base_state = state_for_vte()
check(
    "the terminal is 120x40",
    (base_state.cols, base_state.rows) == (120, 40),
    f"{base_state.cols}x{base_state.rows}",
)

# -- the table, one query at a time, on a fresh terminal each
for name, query in TABLE:
    expected = vte_answers(RIS + query)
    out = StreamFilter(state_for_vte()).feed(RIS + query)
    check(f"{name}: answered as VTE", out.replies == expected, f"{out.replies!r} != VTE {expected!r}")
    check(f"{name}: stripped", out.forward == RIS, repr(out.forward))

# -- DECRQM over every mode 0 to 9999, private and ANSI
for private in (True, False):
    marker = b"?" if private else b""
    data = RIS + b"".join(CSI + marker + b"%d$p" % n for n in range(10000))
    expected = vte_answers(data)
    got = StreamFilter(state_for_vte()).feed(data).replies
    diff = [(a, b) for a, b in zip(got, expected, strict=False) if a != b][:5]
    check(
        f"DECRQM 0-9999 {'private' if private else 'ANSI'}: VTE's table",
        got == expected and len(expected) == 10000,
        f"{len(got)} vs {len(expected)}, first differences {diff!r}",
    )

# -- every mode VTE can set: set, reset, set then DECSTR
settable = [(n, True) for n, s in termstream.VTE_PRIVATE_MODES.items() if s in (1, 2)]
settable += [(n, False) for n, s in termstream.VTE_ANSI_MODES.items() if s in (1, 2)]
for mode, private in settable:
    marker = b"?" if private else b""
    query = CSI + marker + b"%d$p" % mode
    for label, setup in (
        ("set", CSI + marker + b"%dh" % mode),
        ("reset", CSI + marker + b"%dl" % mode),
        ("set then DECSTR", CSI + marker + b"%dh" % mode + CSI + b"!p"),
        ("reset then DECSTR", CSI + marker + b"%dl" % mode + CSI + b"!p"),
    ):
        data = RIS + setup + query
        expected = vte_answers(data)
        got = StreamFilter(state_for_vte()).feed(data).replies
        name = f"DECRQM {'?' if private else ''}{mode} after {label}"
        check(name, got == expected, f"{got!r} != VTE {expected!r}")
    term.feed(RIS)

# -- the palette, both terminators
for term_bytes in (BEL, ST):
    data = RIS + b"".join(OSC + b"4;%d;?" % i + term_bytes for i in range(256))
    expected = vte_answers(data)
    got = StreamFilter(state_for_vte()).feed(data).replies
    name = f"palette 0-255 ({'BEL' if term_bytes == BEL else 'ST'})"
    check(name, got == expected and len(expected) == 256, f"{len(got)} vs {len(expected)}")


# -- a client's colours, mirrored into the state
def rgba(hex_colour):
    colour = Gdk.RGBA()
    colour.parse(hex_colour)
    return colour


COLOUR_QUERIES = (
    OSC + b"10;?" + BEL + OSC + b"11;?" + ST + OSC + b"12;?" + BEL + OSC + b"4;1;?" + BEL
    + OSC + b"4;9;?" + BEL + OSC + b"4;20;?" + BEL + CSI + b"?996n"
)  # fmt: skip
palette16 = [f"#{i * 16:02x}{(15 - i) * 16:02x}{(i * 37) % 256:02x}" for i in range(16)]
for label, fg, bg, cursor in (
    ("dark", "#d0d0d0", "#1e1e1e", None),
    ("light", "#202020", "#fafafa", None),
    ("light with a cursor colour", "#202020", "#fafafa", "#00ff00"),
):
    term.set_colors(rgba(fg), rgba(bg), [rgba(c) for c in palette16])
    term.set_color_cursor(rgba(cursor) if cursor else None)
    expected = vte_answers(RIS + COLOUR_QUERIES)
    state = state_for_vte()
    state.foreground, state.background = rgb16(fg), rgb16(bg)
    state.cursor_colour = rgb16(cursor) if cursor else None
    state.palette = tuple(rgb16(c) for c in palette16) + termstream.VTE_PALETTE[16:]
    got = StreamFilter(state).feed(RIS + COLOUR_QUERIES).replies
    check(f"colours, {label}", got == expected, f"{got!r} != VTE {expected!r}")
term.set_colors(None, None, None)
term.set_color_cursor(None)

# -- the cursor report where VTE's rules show: origin mode, a pending wrap
for label, setup in (
    ("origin mode", CSI + b"5;20r" + CSI + b"?6h" + CSI + b"3;4H"),
    ("pending wrap", CSI + b"1;120HX"),
    ("moved", CSI + b"17;33H"),
):
    # A fresh terminal: get_cursor_position() counts rows from the top of
    # the buffer, which is the screen's top only while nothing scrolled.
    term = fresh_terminal()
    term.feed(setup)
    pump_until(lambda: False, timeout=0.05)
    column, row = term.get_cursor_position()
    grid = (term.get_column_count(), term.get_row_count())
    region = vte_answers(DCS + b"$qr" + ST)
    expected = vte_answers(CSI + b"6n" + CSI + b"?6n")
    state = state_for_vte()
    state.cursor = (column, row)
    f = StreamFilter(state)
    f.feed(setup)
    got = f.feed(CSI + b"6n" + CSI + b"?6n").replies
    detail = f"{got!r} != VTE {expected!r} (cursor {column},{row}, grid {grid}, region {region!r})"
    check(f"cursor report, {label}", got == expected, detail)

# -- a round closed by DA1, in the order asked (F11)
ROUND = (
    CSI + b"?2004h" + CSI + b">0q" + OSC + b"11;?" + ST + CSI + b"?2026$p" + CSI + b"?1016$p"
    + CSI + b"?u" + CSI + b"16t" + b"\x1b_Gi=1,a=q;" + ST + CSI + b"c"
)  # fmt: skip
expected = vte_answers(RIS + ROUND)
got = StreamFilter(state_for_vte()).feed(RIS + ROUND).replies
check("a round closed by DA1, in order", got == expected, f"{got!r} != VTE {expected!r}")
check("…and DA1's answer is the round's last", got[-1:] == [b"\x1b[?61;1;21;22;28c"], repr(got))

print(f"{len(failures)} failure(s)" if failures else "all ok", flush=True)
os._exit(1 if failures else 0)
