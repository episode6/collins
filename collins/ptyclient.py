# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""A `Vte.Terminal` with no child, fed by the service (spec §3.4).

`ClientTerminal` binds one VTE widget to one pty of the service through a
client of the API (`api.client.PtyClient` on the app's `SocketLink`,
PR-1.12b): output frames are `feed()`, what VTE `commit`s goes
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
The service's pty is sized by its active client (§3.3): the last one to
type into it or report focus on it, the first to attach when none.
VTE's grid changes with the widget's allocation, so the terminal is
subclassed to notice its own allocation and send the grid; `set_size`
before the first allocation (a never-shown tab) reaches the service the
same way. **Two clients** (§3.24, PR-3.5): `sizerole.SizeRole` reads the
`attach` reply and every `pty` event, and while this client is not the
active one it sends no ``resize`` (`send_grid` returns), pins the VTE to
the pty's grid (`set_size`) and, inside a `TerminalFrame`, holds it in a
`PinBox` that allocates the terminal exactly that grid whatever room it
has (F2: an allocation would regrid a VTE that fills it), with an
`Adw.Banner` "Sized for <device>" above it. *Use this size* sends
``focus {focused: true}`` (`take_size`), as a click into the terminal does;
a key typed is input, which the service counts the same. When the service
says this client is active again the box lets go, and the next allocation
sends the grid it lands on.
"""

from __future__ import annotations

import logging
import socket
from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk, Vte

from . import sizerole
from .api import protocol
from .i18n import _
from .mouserate import MOTION_COALESCE_MS, MotionCoalescer
from .redrawguard import RedrawGuard
from .sizerole import SizeRole

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


class PinBox(Gtk.Widget):
    """The fixed-size box (§3.24 "Two clients", F2): holds the terminal's
    scrolled window and, while *pinned*, allocates it exactly the room the
    terminal's grid takes, at the top left, clipped: a pane smaller than
    the grid cuts the view off, a larger one leaves the rest empty, and
    neither regrids the VTE. Unpinned it is a plain bin. Its own size
    request is always the child's, so pinning never moves the layout
    around it.

    The room is the grid in cells plus everything around the cells, which
    the last unpinned allocation measured: the scrolled window's width
    less the cells the terminal fitted into it is the window's frame, the
    terminal's margins, border and padding and the remainder short of a
    cell, all of it, whatever a theme puts there. So the pinned terminal
    gets its cells plus that, and its grid is the pty's (the Fable review
    of PR 627, S1: learning it against the content box counted the
    margins and padding twice, and a 60x15 pin with 12 px margins came
    out 62x16). The terminal's natural size is not read for it: GTK's
    measure cache still holds the grid from before the allocation that
    just regridded the terminal."""

    __gtype_name__ = "CollinsPinBox"

    def __init__(self, child: Gtk.Widget, terminal: Vte.Terminal) -> None:
        super().__init__()
        self._child = child
        self._terminal = terminal
        self._pinned = False
        # Everything around the cells (see the class docstring), from the
        # last unpinned allocation; None until there was one.
        self._chrome: tuple[int, int] | None = None
        self.set_overflow(Gtk.Overflow.HIDDEN)
        child.set_parent(self)

    @property
    def pinned(self) -> bool:
        return self._pinned

    def set_pinned(self, pinned: bool) -> None:
        """Pin (or let go); also re-asks the allocation for a pin whose
        grid moved."""
        self._pinned = bool(pinned)
        self.queue_resize()

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return self._child.get_request_mode()

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple:
        minimum, natural, min_baseline, nat_baseline = self._child.measure(orientation, for_size)
        return (minimum, natural, min_baseline, nat_baseline)

    def _cells(self) -> tuple[int, int]:
        terminal = self._terminal
        return (
            terminal.get_column_count() * terminal.get_char_width(),
            terminal.get_row_count() * terminal.get_char_height(),
        )

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if not self._pinned:
            self._child.allocate(width, height, baseline, None)
            # The terminal set its grid inside that allocation.
            cells_w, cells_h = self._cells()
            self._chrome = (
                max(0, self._child.get_width() - cells_w),
                max(0, self._child.get_height() - cells_h),
            )
            return
        if self._chrome is None:
            # Pinned before it was ever shown unpinned (a tab opened on a
            # session another device owns): the terminal's natural size,
            # its grid plus its own margins and padding, and nothing for
            # the scrolled window (overlay scrollbars take no room).
            _min_w, natural_w, _b, _c = self._terminal.measure(Gtk.Orientation.HORIZONTAL, -1)
            _min_h, natural_h, _d, _e = self._terminal.measure(Gtk.Orientation.VERTICAL, natural_w)
            self._child.allocate(natural_w, natural_h, -1, None)
            return
        # `set_size` made the grid the pty's before the pin was asked for.
        cells_w, cells_h = self._cells()
        chrome_w, chrome_h = self._chrome
        self._child.allocate(cells_w + chrome_w, cells_h + chrome_h, -1, None)

    def do_dispose(self) -> None:
        if self._child is not None and self._child.get_parent() is self:
            self._child.unparent()
        Gtk.Widget.do_dispose(self)


class TerminalFrame(Gtk.Box):
    """A `ClientTerminal`'s terminal as the tab shows it: a scrolled window
    around the VTE inside a `PinBox`, under an `Adw.Banner` that says whom
    the pty is sized for while this client is not the active one, with
    *Use this size* (§3.24, PR-3.5). Built in place of the bare scrolled
    window; the view drives it (`ClientTerminal.frame`)."""

    def __init__(self, view: ClientTerminal) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.view = view
        self.scrolled = Gtk.ScrolledWindow(child=view.terminal, vexpand=True)
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.pinbox = PinBox(self.scrolled, view.terminal)
        # Expands as the bare scrolled window did (its own expand flags are
        # computed from its child's otherwise).
        self.pinbox.set_vexpand(True)
        self.banner = Adw.Banner(title="", button_label=_("Use this size"), revealed=False)
        # The device is another machine's name (rule 5): shown as text.
        self.banner.set_use_markup(False)
        self.banner.connect("button-clicked", lambda *_a: view.take_size())
        self.append(self.banner)
        self.append(self.pinbox)
        view.frame = self
        self.show_role(view.role)

    def show_role(self, role: SizeRole) -> None:
        """Pin and say so, or let go and say nothing."""
        pinned = role.pinned is not None
        if not role.active:
            self.banner.set_title(sizerole.banner_title(role.sized_for))
        self.banner.set_revealed(not role.active)
        if pinned != self.pinbox.pinned or pinned:
            self.pinbox.set_pinned(pinned)


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
        # Who owns the pty's size, as the service last said (PR-3.5), and
        # the frame that shows it (a `TerminalFrame`, once built).
        self.role = SizeRole()
        self.frame: TerminalFrame | None = None
        self.grids_withheld = 0  # allocations not sent while pinned, for the checks
        self.dropped_commits = 0  # what the guard swallowed, for the checks
        terminal.connect("commit", self._on_commit)
        terminal.connect("notify::has-focus", self._on_focus_changed)
        # A click into a pinned terminal takes the size (§3.3): capture
        # phase and never claimed, so the click still lands in VTE.
        click = Gtk.GestureClick(button=0)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_pressed)
        terminal.add_controller(click)
        self.click = click
        if isinstance(terminal, ClientVte):
            terminal.on_allocated = self._on_allocated

    @property
    def active(self) -> bool:
        """Whether this client owns the pty's size (the service's word)."""
        return self.role.active

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
            self._apply_role(event)
            if self._on_pty_event is not None:
                self._on_pty_event(event)

    # -- the size's owner (PR-3.5)

    def _apply_role(self, message: dict) -> None:
        change = self.role.apply(message)
        if change in (sizerole.PINNED, sizerole.MOVED):
            grid = self.role.pinned
            if grid is not None:
                # Sized for someone else: show the pty's grid, not ours.
                self.terminal.set_size(*grid)
        elif change == sizerole.RELEASED:
            # Ours again: the next allocation sends whatever grid it lands
            # on, even one equal to the grid sent before the pin.
            self._last_grid = None
            self.terminal.queue_allocate()
        if change != sizerole.UNCHANGED and self.frame is not None:
            self.frame.show_role(self.role)

    def take_size(self) -> None:
        """*Use this size*: tell the service this client wants the role
        (a ``focus {focused: true}`` event; §3.3, the service decides), and
        put the keyboard in the terminal."""
        if self.pty is None or self._exited:
            return
        try:
            self.client.send_event({"t": "focus", "pty": self.pty, "focused": True})
        except Exception:
            log.debug("focus event for pty %s failed", self.pty, exc_info=True)
        self.terminal.grab_focus()

    def _on_pressed(self, *_args) -> None:
        if not self.role.active:
            self.take_size()

    # -- attach

    def attach(self, pty: int) -> dict:
        """Show *pty*: reset the VTE, raise the guard, ask for the redraw
        at this VTE's grid. The attach reply."""
        self.pty = pty
        self._exited = False
        self._focus_reporting = False
        self._motion = MotionCoalescer()
        self.role.reset()
        if self.frame is not None:
            self.frame.show_role(self.role)
        self.guard.raise_for_attach()
        self.terminal.reset(True, True)
        # A pinned client attaching again (a reconnect) reads the grid it
        # was pinned to, not its pane's: the reset above unpinned the box,
        # so the next allocation regrids the VTE and `_on_allocated` sends
        # the pane's grid once the reply says this client is active (and
        # the service files it away while it is not). The Fable review of
        # PR 627, N4.
        cols, rows = self.grid()
        try:
            reply = self.client.request({"t": "attach", "pty": pty, "cols": cols, "rows": rows})
        except Exception:
            self.guard.attach_failed()  # no redraw is coming: typing may go on
            raise
        self._focus_reporting = FOCUS_REPORTING_ON in (reply.get("modes") or ())
        self._last_grid = (cols, rows)
        # Pinned before the redraw is fed (it lands after this returns), so
        # a client that is not the active one paints it at the pty's grid.
        self._apply_role(reply)
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
        if not self.role.sends_grid:
            self.grids_withheld += 1
            return
        self._last_grid = grid
        self.send_grid()

    def send_grid(self) -> None:
        """Tell the service this VTE's grid now (a `resize` event). Not
        while another client owns the size (§3.24): the pinned grid is the
        pty's own, and the service would only file it away."""
        if self.pty is None or self._exited or not self.role.sends_grid:
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
