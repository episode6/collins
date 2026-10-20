# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""A `Vte.Terminal` with no child, fed by the service (spec §3.4).

`ClientTerminal` binds one VTE widget to one pty of the service through a
client of the API (`api.loopback.LoopbackClient` through Phase 1, the
socket from PR-1.12b): output frames are `feed()`, what VTE `commit`s goes
back as input frames, the grid and the focus go as `resize` and `focus`
events, and `pty-exited` is what `child-exited` used to be. Nothing
reaches the VTE that did not come out of the service's stream for the pty,
and nothing reaches the pty that did not go through the service (§3.1
rule 2). Every session tab and panel shell runs on it; there is no other
backend (PR-1.9 deleted the tab's own in-widget pty). The session's own
reads of the pty and the screen model are the service's since PR-1.12a
(`service.hosting`); the tab holds `clientsession.ClientSession`.

The redraw guard
----------------
`redrawguard.RedrawGuard` (GTK-free, its own tests) is the state machine:
raised by an attach or by the first frame of a redraw the service sends
on its own (flow control, §3.2; the VTE is reset then), lowered only by
the VTE's answer to the latest sentinel fed after the frame flagged
``REDRAW_END`` (a generation-tagged ``OSC 4`` query), or by a 2 s
watchdog. While it is up every commit is dropped, so nothing the VTE
says on its own account while it is being repainted reaches the pty as
if typed. The `attach` reply lists the modes the redraw's preamble
re-asserted; if focus reporting (``?1004h``) is among them the client
then tells the program the real focus state, which the reset had lost.

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
import socket
from collections.abc import Callable

from gi.repository import GLib, Vte

from .api import protocol
from .mouserate import MOTION_COALESCE_MS, MotionCoalescer
from .redrawguard import RedrawGuard

log = logging.getLogger(__name__)

FOCUS_REPORTING_ON = "?1004h"  # as the attach reply's `modes` lists it
FOCUS_IN, FOCUS_OUT = b"\x1b[I", b"\x1b[O"


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
        self.guard = RedrawGuard(self._arm, GLib.source_remove)
        self._focus_reporting = False  # the redraw's preamble turned it on
        self._nul_warned = False
        self._exited = False
        self.term: dict = {}  # the hello's term: colours and scheme, sent as `theme`
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
        reset, sentinel = self.guard.on_frame(flags)
        if reset:
            # A redraw the service sent on its own (flow control, a resize
            # for another client): the screen it lands on is stale.
            self.terminal.reset(True, True)
        self.terminal.feed(data)
        if sentinel is not None:
            # The guard stays up until VTE has answered this sentinel.
            self.terminal.feed(sentinel)

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
        self._focus_reporting = False
        self._motion = MotionCoalescer()
        self.guard.raise_for_attach()
        self.terminal.reset(True, True)
        cols, rows = self.grid()
        try:
            reply = self.client.request({"t": "attach", "pty": pty, "cols": cols, "rows": rows})
        except Exception:
            self.guard.attach_failed()  # no redraw is coming: typing may go on
            raise
        self._focus_reporting = FOCUS_REPORTING_ON in (reply.get("modes") or ())
        self._last_grid = (cols, rows)
        self.send_theme()
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
        data = commit_bytes(text, size)
        if data is None:
            if not self._nul_warned:
                self._nul_warned = True
                log.warning("a commit of %d bytes came through as %r; sent as far as it reads", size, text)
            data = (text or "").encode("utf-8", "surrogateescape")
        if not data:
            return
        was_up = self.guard.up
        if not self.guard.on_commit(data):
            self.dropped_commits += 1
            if was_up and not self.guard.up and self._focus_reporting:
                # The redraw turned focus reporting on; the reset lost the
                # real state, so say it now.
                self._focus_reporting = False
                self.client.send_input(self.pty, FOCUS_IN if self.terminal.has_focus() else FOCUS_OUT)
            return
        if b"\x1b[<" not in data and self._motion.pending is None:
            self.client.send_input(self.pty, data)  # nothing pending to keep order behind
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

    def _arm(self, ms: int, fn) -> int:
        return GLib.timeout_add(ms, lambda: (fn(), GLib.SOURCE_REMOVE)[1])

    def send_theme(self) -> None:
        """The `theme` event: the terminal's colours and scheme, so the
        service answers OSC 10/11/4 and the colour-scheme query as this
        terminal would (§3.3). Sent at attach and whenever the theme
        changes (`set_term`)."""
        if self.pty is None or self._exited or not self.term:
            return
        try:
            self.client.send_event({"t": "theme", "term": dict(self.term)})
        except Exception:
            log.debug("theme event for pty %s failed", self.pty, exc_info=True)

    def set_term(self, term: dict) -> None:
        self.term = {k: v for k, v in term.items() if v is not None}
        self.send_theme()

    @property
    def guarded(self) -> bool:
        return self.guard.up

    @property
    def exited(self) -> bool:
        return self._exited


def commit_bytes(text: str | None, size: int) -> bytes | None:
    """The bytes a `commit` carried. PyGObject hands the signal a C
    string, so a NUL ends the text early: ``\\0`` alone (Ctrl+Space,
    Ctrl+@) arrives as ``''`` with *size* 1, and ``a\\0b`` as ``'a'`` with
    size 3. The first is given back whole; any other short read is None
    (the caller sends what it has and says so once)."""
    data = (text or "").encode("utf-8", "surrogateescape")
    if size <= len(data):
        return data[:size]
    if not data and size == 1:
        return b"\x00"
    return None
