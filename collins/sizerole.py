# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Who owns a pty's size, as one client sees it (split-service spec §3.3,
§3.24 "Two clients"; PR-3.5).

The service decides: per pty the **active client** (the last one to type
into it or report focus on it; with none, the first to attach) owns the
size, a resize from any other client is only remembered, and every
attached client is told by the `attach` reply and by each `pty` event
whether it is the active one and whom the pty is sized for (``active``,
``cols``, ``rows``, ``sized_for``). The client shows: while it is not the
active one it stops sending ``resize`` (the allocation would only send
its own grid again and again for the service to file away), pins its
terminal to the pty's grid inside a box the allocation cannot regrid
(F2), and shows "Sized for <device>" with *Use this size*; a click, a key
or that button takes the size (a ``focus {focused: true}`` event, or the
typed input itself). `SizeRole` is that reading of the service's word,
GTK-free so the unit suite holds it (`tests/test_sizerole.py`);
`ptyclient.ClientTerminal` owns one per terminal and acts on what
`apply` says changed.

The device is the active client's hello ``device``: another machine's
hostname, so foreign text (rule 5). `device_label` bounds it and drops
what would draw as something else (control and format characters), and
`banner_title` builds the line; the banner shows it as plain text, never
markup.
"""

from __future__ import annotations

import unicodedata

from .api import protocol
from .i18n import _

# The most of a device's name the banner shows (characters, an ellipsis
# after): a hostname may be 255 bytes, a banner's line is not.
LABEL_MAX = 48

# What `apply` reports.
UNCHANGED = ""
PINNED = "pinned"  # this client lost the size: pin to the pty's grid
MOVED = "moved"  # still pinned, to another grid or for another device
RELEASED = "released"  # this client owns the size again


def device_label(device: object) -> str:
    """*device* (another client's hello ``device``) as a line of text:
    control, format (bidi overrides, zero-width) and other invisible
    characters dropped, whitespace runs collapsed, at most `LABEL_MAX`
    characters with an ellipsis; "" for nothing usable."""
    if not isinstance(device, str):
        return ""
    kept = []
    for char in device[: protocol.HOST_MAX]:
        category = unicodedata.category(char)
        if char.isspace():
            kept.append(" ")
        elif category[0] == "C":  # Cc, Cf, Cs, Co, Cn
            continue
        else:
            kept.append(char)
    label = " ".join("".join(kept).split())
    if len(label) > LABEL_MAX:
        label = label[: LABEL_MAX - 1].rstrip() + "…"
    return label


def banner_title(device: object) -> str:
    """The pinned terminal's banner: "Sized for <device>", or a line that
    names no device when the service knows none."""
    label = device_label(device)
    if not label:
        return _("Sized for another device")
    return _("Sized for {device}").format(device=label)


def _grid(cols: object, rows: object) -> tuple[int, int] | None:
    if isinstance(cols, bool) or isinstance(rows, bool):
        return None
    if not isinstance(cols, int) or not isinstance(rows, int):
        return None
    if not (1 <= cols <= protocol.MAX_COLS and 1 <= rows <= protocol.MAX_ROWS):
        return None
    return cols, rows


class SizeRole:
    """One terminal's reading of who owns its pty's size. Starts active (a
    client that spawned or attached first owns the size, and one that
    never heard otherwise behaves as before PR-3.5)."""

    def __init__(self) -> None:
        self.active = True
        self.grid: tuple[int, int] | None = None  # the pty's grid while pinned
        self.sized_for = ""

    @property
    def pinned(self) -> tuple[int, int] | None:
        """The grid to pin the terminal to, or None while this client owns
        the size."""
        return None if self.active else self.grid

    @property
    def sends_grid(self) -> bool:
        """Whether an allocation's grid goes to the service as ``resize``:
        only from the active client (§3.24)."""
        return self.active

    def reset(self) -> None:
        """A fresh attach: active until the service says otherwise."""
        self.active = True
        self.grid = None
        self.sized_for = ""

    def apply(self, message: dict) -> str:
        """An `attach` reply or a `pty` event: what changed (`PINNED`,
        `MOVED`, `RELEASED` or `UNCHANGED`). A message that does not say
        ``active`` leaves the role as it was; one saying the client is not
        active with no usable grid keeps the grid it had (an inactive
        client with no grid at all is not pinned, but still sends none)."""
        if not isinstance(message, dict) or "active" not in message:
            return UNCHANGED
        active = message.get("active") is not False
        grid = _grid(message.get("cols"), message.get("rows"))
        sized_for = message.get("sized_for")
        sized_for = sized_for[: protocol.HOST_MAX] if isinstance(sized_for, str) else ""
        was_active, was_grid, was_for = self.active, self.grid, self.sized_for
        self.active = active
        if active:
            self.grid = None
            self.sized_for = ""
            return UNCHANGED if was_active else RELEASED
        if grid is not None:
            self.grid = grid
        self.sized_for = sized_for
        if was_active:
            return PINNED
        if (self.grid, self.sized_for) != (was_grid, was_for):
            return MOVED
        return UNCHANGED
