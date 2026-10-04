# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""A `Vte.Terminal` with no child, fed by the service (spec §3.4).

`ClientTerminal` binds one VTE widget to one pty of the service through a
client of the API (`api.loopback.LoopbackClient` through Phase 1, the
socket from PR-1.12): output frames are `feed()`, what VTE `commit`s goes
back as input frames, the grid and the focus go as `resize` and `focus`
events, and `pty-exited` is what `child-exited` used to be. Nothing
reaches the VTE that did not come out of the service's stream for the pty,
and nothing reaches the pty that did not go through the service (§3.1
rule 2). The backend a tab runs on is `PTY_BACKEND`, read once from
``COLLINS_PTY_BACKEND`` (``vte``, the default: the tab's VTE spawns the
shell itself; ``server``: this module).

The redraw guard
----------------
An attach answers with a redraw painted from the service's screen model
(§3.3): the VTE is reset, and from then until the guard comes down every
`commit` is dropped, so nothing the VTE says on its own account while it is
being repainted (an answer to a query the preamble re-asserts a mode with,
a focus report the reset provokes) reaches the pty as if typed. After the
frame flagged ``REDRAW_END`` the client feeds ``CSI 5 n``; VTE's ``CSI 0
n`` is the last thing it says in reply (F3), and the guard comes down on
it. If the redraw turned focus reporting on (``CSI ?1004h`` in the
preamble: the tracker's word, read off the redraw's own bytes, the one
local read this module makes of the stream), the client then tells the
program the real focus state, which the reset had lost.

The mouse
---------
Every commit goes through `mouserate.MotionCoalescer` (GTK-free, the
rule of §3.3 "Mouse"): a no-button motion report naming the cell of the
one before it is dropped, the rest are coalesced to the latest per
`MOTION_COALESCE_MS` on a timer owned here; presses, releases, wheel
steps and drags are never dropped and flush what was pending ahead of
them.

Sizing
------
The service's pty is sized by its active client (§3.3): this one, through
Phase 1. VTE's grid changes with the widget's allocation, so the terminal
is subclassed to notice its own allocation and send the grid; `set_size`
before the first allocation (a never-shown tab) reaches the service the
same way. A `pty` event saying the pty is sized for someone else pins the
VTE to that grid (`set_size`), the "Sized for <device>" bar being PR-3.5's.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable
from typing import Any

from gi.repository import GLib, Vte

from .api import protocol
from .mouserate import MOTION_COALESCE_MS, MotionCoalescer

log = logging.getLogger(__name__)

PTY_BACKEND = os.environ.get("COLLINS_PTY_BACKEND", "vte")
BACKENDS = ("vte", "server")
if PTY_BACKEND not in BACKENDS:
    log.warning("COLLINS_PTY_BACKEND=%r is not one of %s; using vte", PTY_BACKEND, BACKENDS)
    PTY_BACKEND = "vte"

# The sentinel that ends the redraw guard, and VTE's answer to it (F3, F8).
SENTINEL = b"\x1b[5n"
SENTINEL_ANSWER = b"\x1b[0n"
FOCUS_REPORTING_ON = b"\x1b[?1004h"
FOCUS_IN, FOCUS_OUT = b"\x1b[I", b"\x1b[O"


def server_backend() -> bool:
    return PTY_BACKEND == "server"


def device_name() -> str:
    """This device, as the service names it in "Sized for <device>"."""
    try:
        return socket.gethostname()[: protocol.HOST_MAX]
    except OSError:
        return ""
class ClientVte(Vte.Terminal):
    """A VTE that reports its own allocation: GTK4 has no size-allocate
    signal, and the grid is what the service's pty has to follow."""

    def __init__(self) -> None:
        super().__init__()
        self.on_allocated: Callable[[], None] | None = None

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Vte.Terminal.do_size_allocate(self, width, height, baseline)
        if self.on_allocated is not None:
            self.on_allocated()


class ClientTerminal:
    """One VTE on one pty of the service. See the module docstring.

    *terminal* is the widget (a `ClientVte` for the grid to follow the
    allocation, or any `Vte.Terminal` the caller sizes itself); *client*
    the API client; *on_exited* hears `pty-exited`'s status (None for
    unknown); *on_pty_event* every `pty` event, after the sizing rule has
    had it."""

    def __init__(
        self,
        terminal: Vte.Terminal,
        client,
        *,
        on_exited: Callable[[int | None], None],
        on_pty_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.terminal = terminal
        self.client = client
        self.pty: int | None = None
        self._on_exited = on_exited
        self._on_pty_event = on_pty_event
        self._guard = False
        self._redraw_turned_focus_on = False
        self._exited = False
        self._motion = MotionCoalescer()
        self._motion_source = 0
        self._last_grid: tuple[int, int] | None = None
        self.active = True
        self.dropped_commits = 0  # what the guard swallowed, for the checks
        terminal.connect("commit", self._on_commit)
        terminal.connect("notify::has-focus", self._on_focus_changed)
        if isinstance(terminal, ClientVte):
            terminal.on_allocated = self._on_allocated

    # -- the service's way in

    def on_output(self, pty: int, data: bytes, flags: int) -> None:
        """An output frame for *pty* (the client's `on_output` routes
        here for this terminal's pty)."""
        if pty != self.pty or self._exited:
            return
        if flags & protocol.FLAG_REDRAW:
            if FOCUS_REPORTING_ON in data:
                self._redraw_turned_focus_on = True
            self.terminal.feed(data)
            if flags & protocol.FLAG_REDRAW_END:
                # The guard stays up until VTE has answered the sentinel.
                self.terminal.feed(SENTINEL)
            return
        self.terminal.feed(data)

    def on_event(self, event: dict) -> None:
        if event.get("pty") != self.pty:
            return
        kind = event.get("t")
        if kind == "pty-exited":
            self._exited = True
            self._cancel_motion()
            self._on_exited(event.get("status"))
        elif kind == "pty":
            self.active = bool(event.get("active", True))
            if not self.active and event.get("cols") and event.get("rows"):
                # Sized for someone else: show the pty's grid, not ours
                # (§3.3; the "Sized for" bar is PR-3.5's).
                self.terminal.set_size(int(event["cols"]), int(event["rows"]))
            if self._on_pty_event is not None:
                self._on_pty_event(event)

    # -- attach

    def attach(self, pty: int) -> dict:
        """Show *pty*: reset the VTE, raise the guard, ask for the redraw
        at this VTE's grid. The attach reply."""
        self.pty = pty
        self._exited = False
        self._guard = True
        self._redraw_turned_focus_on = False
        self._motion = MotionCoalescer()
        self.terminal.reset(True, True)
        cols, rows = self.grid()
        reply = self.client.request({"t": "attach", "pty": pty, "cols": cols, "rows": rows})
        self._last_grid = (cols, rows)
        return reply

    def detach(self) -> None:
        if self.pty is not None and not self._exited:
            try:
                self.client.request({"t": "detach", "pty": self.pty})
            except Exception:
                log.debug("detach of pty %s failed", self.pty, exc_info=True)
        self._cancel_motion()
        self.pty = None

    def grid(self) -> tuple[int, int]:
        cols, rows = self.terminal.get_column_count(), self.terminal.get_row_count()
        return max(1, cols), max(1, rows)

    # -- what the VTE says

    def _on_commit(self, _terminal, text: str, size: int) -> None:
        if self.pty is None or self._exited:
            return
        data = text.encode("utf-8", "surrogateescape")[:size] if text else b""
        if not data:
            return
        if self._guard:
            self.dropped_commits += 1
            if SENTINEL_ANSWER in data:
                self._guard = False
                if self._redraw_turned_focus_on:
                    self._redraw_turned_focus_on = False
                    self.client.send_input(self.pty, FOCUS_IN if self.terminal.has_focus() else FOCUS_OUT)
            return
        if b"\x1b[<" not in data:
            self.client.send_input(self.pty, data)
            return
        now, pending = self._motion.take(data)
        if now:
            self.client.send_input(self.pty, now)
        if pending and not self._motion_source:
            self._motion_source = GLib.timeout_add(MOTION_COALESCE_MS, self._flush_motion)

    def _flush_motion(self) -> bool:
        self._motion_source = 0
        held = self._motion.flush()
        if held and self.pty is not None and not self._exited:
            self.client.send_input(self.pty, held)
        return GLib.SOURCE_REMOVE

    def _cancel_motion(self) -> None:
        if self._motion_source:
            GLib.source_remove(self._motion_source)
            self._motion_source = 0
        self._motion.flush()

    def _on_focus_changed(self, terminal, _pspec) -> None:
        if self.pty is None or self._exited:
            return
        try:
            self.client.send_event({"t": "focus", "pty": self.pty, "focused": bool(terminal.has_focus())})
        except Exception:
            log.debug("focus event for pty %s failed", self.pty, exc_info=True)

    def _on_allocated(self) -> None:
        if self.pty is None or self._exited:
            return
        grid = self.grid()
        if grid == self._last_grid:
            return
        self._last_grid = grid
        self.send_grid()

    def send_grid(self) -> None:
        """Tell the service this VTE's grid now (a `resize` event)."""
        if self.pty is None or self._exited:
            return
        cols, rows = self.grid()
        self._last_grid = (cols, rows)
        try:
            self.client.send_event({"t": "resize", "pty": self.pty, "cols": cols, "rows": rows})
        except Exception:
            log.debug("resize event for pty %s failed", self.pty, exc_info=True)

    @property
    def guarded(self) -> bool:
        return self._guard

    @property
    def exited(self) -> bool:
        return self._exited


class ServicePtyPort:
    """`service.ports.PtyPort` over a service pty, through the client: a
    write is an input frame, a resize the `resize` event; the pid and the
    foreground group are read off the loopback's pty object (a shortcut
    the socket replaces with the `pty` event's ``pid`` and a request:
    see api.loopback)."""

    def __init__(self, client, view: ClientTerminal) -> None:
        self._client = client
        self._view = view

    @property
    def pid(self) -> int | None:
        return self.child_pid()

    def _pty(self):
        if self._view.pty is None:
            return None
        try:
            return self._client.pty_of(self._view.pty)
        except KeyError:
            return None

    def write(self, data: bytes) -> None:
        if self._view.pty is not None:
            self._client.send_input(self._view.pty, data)

    def resize(self, cols: int, rows: int) -> None:
        self._view.terminal.set_size(cols, rows)
        self._view.send_grid()

    def child_pid(self) -> int | None:
        pty = self._pty()
        return pty.child_pid() if pty is not None else None

    def foreground_pgrp(self) -> int | None:
        pty = self._pty()
        return pty.foreground_pgrp() if pty is not None else None


class ServiceScreenPort:
    """`service.ports.ScreenPort` over the service's screen model
    (`termscreen.Screen`), read through the loopback's `screen_of`. The
    model is the screen as anchored to the cursor already (it has no
    scroll position), so every row index is the model's own. *foreground*
    is the theme's text colour, read live (None while the terminal follows
    the system colours), as `VteScreenPort` reads it."""

    def __init__(
        self, client, view: ClientTerminal, foreground: Callable[[], tuple[int, int, int] | None]
    ) -> None:
        self._client = client
        self._view = view
        self._foreground = foreground

    def _screen(self) -> Any:
        if self._view.pty is None:
            return None
        try:
            return self._client.screen_of(self._view.pty)
        except KeyError:
            return None

    def rows(self) -> list[str]:
        screen = self._screen()
        return screen.rows() if screen is not None else []

    def cursor(self) -> tuple[int, int]:
        screen = self._screen()
        return screen.cursor() if screen is not None else (0, 0)

    def columns(self) -> int:
        screen = self._screen()
        return screen.columns() if screen is not None else self._view.grid()[0]

    def row_count(self) -> int:
        screen = self._screen()
        return screen.row_count() if screen is not None else self._view.grid()[1]

    def tail_is_faint(self, row: int, column: int) -> bool:
        screen = self._screen()
        if screen is None or not 0 <= row < screen.row_count():
            return False
        fg = self._foreground()
        if fg is None:
            return screen.tail_is_faint(row, column)
        return screen.tail_is_faint(row, column, foreground=fg)

    def first_column(self) -> tuple[str, ...]:
        screen = self._screen()
        return screen.first_column() if screen is not None else ()

    def capture_contents(self) -> str:
        screen = self._screen()
        return screen.capture_contents() if screen is not None else ""

    def row_text(self, row: int, end_column: int) -> str:
        screen = self._screen()
        if screen is None or not 0 <= row < screen.row_count():
            return ""
        return _cells_prefix(screen.cells(row), end_column)

    def visible_text(self) -> str:
        screen = self._screen()
        if screen is None:
            return ""
        _, cursor_row = screen.cursor()
        out: list[str] = []
        wrapped = screen.grid.wrapped
        for y in range(cursor_row + 1):
            out.append(screen.row_text(y))
            if y < cursor_row and not wrapped[y]:
                out.append("\n")
        return "".join(out)


def _cells_prefix(cells: list, end_column: int) -> str:
    """The text of a model row's cells up to *end_column* (cells,
    exclusive), as `termscreen._line_text` gives the whole row: up to the
    last cell written, a blank inside reading as a space, the second half
    of a wide character nothing (its first half carries the text)."""
    from .service.termscreen import _line_text

    return _line_text(cells[:end_column])
