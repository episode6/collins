# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The pty server: the service's table of terminals.

One `Pty` per agent session and per panel shell, owned by a `PtyServer`
(split-service spec §3.3). The server spawns the child on a pty it holds the
master of, runs every byte the child writes through `termstream.StreamFilter`
(the queries answered and stripped) into `termscreen.Screen` (the model of
record), and hands what is left to every attached sink. Nothing reaches a
sink that did not come out of that stream, and nothing reaches the pty that
did not go through `write` (§3.1 rule 2). GLib and Gio only; nothing here
imports GTK, Adw, Gdk, Vte or GtkSource.

Spawn
-----
`spawn(kind, argv, cwd, env, cols, rows, …)`: `os.openpty()`, the window
size set on the slave before the fork, the child made a session leader with
the slave as its controlling terminal (`os.login_tty`), the master
non-blocking. The environment is the caller's plus what VTE would have set
and the CLI's terminal detection reads: ``TERM=xterm-256color``,
``COLORTERM=truecolor``, ``VTE_VERSION`` (the client's, else 8400) and the
two spoofs of `terminal._agent_tab_environment` (``ConEmuANSI=ON``,
``TERM_PROGRAM=kitty``), unchanged (D15). An agent's command line is typed
in by its `Session`, not here: the server only runs the argv it is given.

The read loop
-------------
A `GLib.io_add_watch` on the master at `PRIORITY_DEFAULT`; each read goes
through the pty's filter, which feeds the screen token by token, then the
filter's replies are written back through the write queue, the forward bytes
go to every sink as live output (flags 0), and the stream events (progress,
bell, title, modes, the alternate screen) go to the server's listener for
the service's own detection (§3.6).

**Every write is queued.** The master is non-blocking and the queue drains
behind a writability watch: a blocking write of a 100 KB paste deadlocked
the spike's server against a child that was echoing it (F11). Requests and
typing alike go through one queue per pty in arrival order, the filter's
replies ahead of what is waiting (a query's answer is what the program is
blocked on). The queue is bounded at `INPUT_QUEUE_BYTES`: a client that
floods a pty whose child is not reading finds its input beyond the bound
dropped and counted (`Pty.dropped_input`), never a blocked loop.

Attach, the redraw and the active client
----------------------------------------
`attach(pty, sink, cols, rows)` answers with a redraw: `Screen.snapshot()`
with the mode tracker's preamble, in frames of at most
`protocol.MAX_PAYLOAD`, every one flagged `FLAG_REDRAW` and the last
`FLAG_REDRAW_END`, then live output. The active client owns the size (D1):
the last sink to send input or report focus, the first to attach when none.
A resize from the active sink is applied (``TIOCSWINSZ``, the model resized,
the filter's grid updated); one from any other sink is remembered and
applied when that sink becomes active; with no sink attached the size is
left alone. An attaching sink that is active and whose grid differs is
applied first, so its redraw is painted at its own size and the program's
repaint follows on the live stream; a sink that is not active is redrawn at
the pty's grid and told so (``active`` False and ``sized_for``, the active
sink's ``device`` when it has one).

**Flow control** (§3.2): the server counts the bytes it has handed each
sink for a pty and the sink reports what it has written out with
`PtyServer.drained(pty, sink, n)`. When the count would pass
`protocol.QUEUE_BYTES` (4 MiB) the sink is not sent the backlog: its
``drop_queued()`` is called when it has one and something is counted as
queued, the count is reset, and a fresh redraw follows, flagged as one. A transport therefore treats the
first frame of a redraw as "discard whatever of this pty is still queued
behind me". A sink that never reports draining is cut off and redrawn
every 4 MiB, which is the honest outcome for a transport that cannot say.

A sink is any object with ``send_output(data: bytes, flags: int)`` and
``send_event(event: dict)``; ``drop_queued()`` and a ``device`` attribute
are optional. Sinks are keyed by identity.

`paint(pty, text)` is rule 2's service-inserted text: it is fed to the
model and sent to the sinks as if the child had written it, between two
reads, so it lands at a token boundary; it does not go through the query
responder (the service does not answer its own questions), so a paint that
contains a query is a bug in the caller, not a reply.

Exit
----
A `GLib.child_watch_add` reaps the child; the master is drained until EIO,
the filter flushed, the model saved once more, every sink sent
``{"t": "pty-exited", "pty": id, "status": s}`` (the exit code, or minus
the signal number), the watches removed, the master closed and the pty
dropped from the table. The order is the same when EIO arrives first (the
child closed the slave) and the status comes later. `close(pty)` sends
SIGHUP and closes the master, which ends a well-behaved child; `signal`
sends anything else. `shutdown()` saves every model and closes every pty
(Phase 1: stopping the service ends every agent, §3.10).

The saved model
---------------
`Screen.dump()` as JSON, written atomically to
``$XDG_STATE_HOME/collins/pty/<pty id>.model`` (`COLLINS_PTY_STATE_DIR`
overrides the directory; tests, captures and e2e checks use it) at most
once per `SAVE_INTERVAL_MS` while output arrives, at exit and at shutdown.
It is what a restarted service reads to show a resumed session's scrollback
and what `capture_contents` of a closed panel shell is read from (§3.10
point 3); PR-3.6's keeper adopts it. `Screen.load` validates every field
and bound (rule 5) and raises on anything off, so a file an older or
newer service wrote, or a damaged one, means a fresh model, never a crash.
The file is kept when the pty exits; the service removes it with the
session (a later PR).

The `ptys` table in `state.json` (§3.8) holds a row per live pty (kind,
session, cwd, pid, options, box, plan, size), written through the
``record`` callable the server is built with (`AppState.set_pty` /
`remove_pty` in the app; a test needs no `AppState`).

Measured 2026-10-02 (`tests/test_ptyserver.py`, the dev box, Python
3.14.4): the redraw of a model holding 10 000 scrollback rows of coloured
text (four pens and 85 bytes a row, a diagnostic-shaped line) is 0.84 MiB
and serializes and frames in 34 ms, three runs within a millisecond of
each other (§3.18's "attach" row).
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import signal
import struct
import termios
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gi.repository import GLib

from ..api.protocol import FLAG_REDRAW, FLAG_REDRAW_END, MAX_PAYLOAD, QUEUE_BYTES
from . import termscreen, termstream

log = logging.getLogger(__name__)

# The write queue's bound per pty (typing, requests and the filter's replies
# together): past it a client's input is dropped, never blocked on.
INPUT_QUEUE_BYTES = 1024 * 1024
READ_SIZE = 64 * 1024
# The saved model is written at most this often while output arrives.
SAVE_INTERVAL_MS = 3000
DEFAULT_VTE_VERSION = 8400
MODEL_SUFFIX = ".model"

_AGENT = "agent"
_SHELL = "shell"
KINDS = frozenset({_AGENT, _SHELL})


def default_state_dir() -> Path:
    """Where the saved models live: `COLLINS_PTY_STATE_DIR`, else
    ``$XDG_STATE_HOME/collins/pty``."""
    override = os.environ.get("COLLINS_PTY_STATE_DIR")
    if override:
        return Path(override)
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "collins" / "pty"


def spawn_environment(env: dict[str, str] | None, vte_version: int) -> dict[str, str]:
    """The child's environment: the caller's, plus what VTE sets and what
    makes the CLI announce its progress (`terminal._agent_tab_environment`)."""
    out = dict(os.environ if env is None else env)
    out.update(
        TERM="xterm-256color",
        COLORTERM="truecolor",
        VTE_VERSION=str(vte_version),
        ConEmuANSI="ON",
        TERM_PROGRAM="kitty",
    )
    return out


def set_window_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class _Attachment:
    """One sink's view of one pty."""

    __slots__ = ("sink", "cols", "rows", "queued")

    def __init__(self, sink, cols: int, rows: int):
        self.sink = sink
        self.cols, self.rows = cols, rows
        self.queued = 0  # bytes handed to the sink and not yet drained


class Pty:
    """One terminal: the child, its master, its filter and screen, its
    sinks. Implements the `PtyPort` of §3.5 (`write`, `resize`, `child_pid`,
    `foreground_pgrp`) for the `Session` that will sit on it (PR-1.7)."""

    def __init__(self, server: PtyServer, pty_id: int, kind: str, cwd: str, cols: int, rows: int):
        self.server = server
        self.id = pty_id
        self.kind = kind
        self.cwd = cwd
        self.cols, self.rows = cols, rows
        self.pid: int | None = None
        self.master: int = -1
        self.session: str | None = None
        self.box: str | None = None
        self.plan: str | None = None
        self.options: dict | None = None
        self.state = termstream.TerminalState(cols=cols, rows=rows, vte_version=server.vte_version)
        server.apply_term(self.state)
        self.screen = termscreen.Screen(cols, rows)
        self.filter = termstream.StreamFilter(self.state, screen=self.screen)
        self.attachments: dict[int, _Attachment] = {}  # id(sink) -> attachment
        self.active: int | None = None  # id(sink)
        self.pending_sizes: dict[int, tuple[int, int]] = {}
        self._queue: list[bytes] = []
        self._queued = 0
        self.dropped_input = 0
        self._read_watch = 0
        self._write_watch = 0
        self._child_watch = 0
        self._save_source = 0
        self._dirty = False
        self.exit_status: int | None = None
        self._reaped = False
        self._eof = False
        self._finished = False

    # -- PtyPort

    def write(self, data: bytes) -> None:
        self.server.write(self.id, data)

    def resize(self, cols: int, rows: int) -> None:
        self.server.resize(self.id, cols, rows)

    def child_pid(self) -> int | None:
        return None if self._finished else self.pid

    def foreground_pgrp(self) -> int | None:
        if self.master < 0 or self._finished:
            return None
        try:
            return os.tcgetpgrp(self.master)
        except OSError:
            return None

    # -- reads

    def row(self) -> dict:
        """The pty table's row (§3.8), as `state.json` and the `pty` event
        carry it."""
        out: dict[str, Any] = {
            "kind": self.kind,
            "cwd": self.cwd,
            "cols": self.cols,
            "rows": self.rows,
        }
        if self.pid is not None and not self._finished:
            out["pid"] = self.pid
        if self.session:
            out["session"] = self.session
        if self.box:
            out["box"] = self.box
        if self.plan:
            out["plan"] = self.plan
        if self.options:
            out["options"] = self.options
        return out

    @property
    def sized_for(self) -> str:
        if self.active is None:
            return ""
        attachment = self.attachments.get(self.active)
        return str(getattr(attachment.sink, "device", "") or "") if attachment else ""

    def model_path(self) -> Path:
        return self.server.state_dir / f"{self.id}{MODEL_SUFFIX}"


class PtyServer:
    """The table of ptys and the loop that serves them. See the module
    docstring."""

    def __init__(
        self,
        state_dir: Path | None = None,
        record: Callable[[int, dict | None], None] | None = None,
        on_event: Callable[[int, object], None] | None = None,
        next_id: int = 1,
        vte_version: int = DEFAULT_VTE_VERSION,
    ):
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self._record = record
        self._on_event = on_event
        self._next_id = max(1, next_id)
        self.vte_version = vte_version
        self.ptys: dict[int, Pty] = {}
        self._term: dict = {}  # the active client's term, applied to every TerminalState

    # -- the table

    def get(self, pty_id: int) -> Pty:
        try:
            return self.ptys[pty_id]
        except KeyError:
            raise KeyError(f"no pty {pty_id}") from None

    def rows(self) -> dict[int, dict]:
        return {pty_id: pty.row() for pty_id, pty in self.ptys.items()}

    def set_term(self, term: dict) -> None:
        """The active client's terminal (hello's ``term``, the ``theme``
        event): colours and scheme the responder answers from."""
        self._term = dict(term)
        for pty in self.ptys.values():
            self.apply_term(pty.state)

    def apply_term(self, state: termstream.TerminalState) -> None:
        term = self._term
        try:
            if term.get("fg"):
                state.foreground = termstream.rgb16(term["fg"])
            if term.get("bg"):
                state.background = termstream.rgb16(term["bg"])
        except ValueError:
            pass
        scheme = term.get("scheme")
        state.dark = None if scheme not in ("dark", "light") else scheme == "dark"
        if isinstance(term.get("vte"), int) and term["vte"] > 0:
            state.vte_version = term["vte"]

    # -- spawn

    def spawn(
        self,
        kind: str,
        argv: list[str],
        cwd: str,
        env: dict[str, str] | None = None,
        cols: int = termscreen.DEFAULT_COLS,
        rows: int = termscreen.DEFAULT_ROWS,
        session: str | None = None,
        box: str | None = None,
        plan: str | None = None,
        options: dict | None = None,
    ) -> int:
        """Fork `argv` on a new pty; the new pty's id."""
        if kind not in KINDS:
            raise ValueError(f"kind {kind!r}")
        if not argv:
            raise ValueError("empty argv")
        cols, rows = termscreen._clamp_grid(cols, rows)
        pty_id = self._next_id
        self._next_id += 1
        pty = Pty(self, pty_id, kind, cwd, cols, rows)
        pty.session, pty.box, pty.plan, pty.options = session, box, plan, options
        vte = pty.state.vte_version
        child_env = spawn_environment(env, vte)
        master, slave = os.openpty()
        try:
            set_window_size(slave, cols, rows)
            pid = os.fork()
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        if pid == 0:  # the child
            try:
                os.close(master)
                os.login_tty(slave)
                os.chdir(cwd)
                os.execvpe(argv[0], argv, child_env)
            except BaseException:
                pass
            os._exit(127)
        os.close(slave)
        os.set_blocking(master, False)
        pty.pid, pty.master = pid, master
        self.ptys[pty_id] = pty
        pty._read_watch = GLib.io_add_watch(
            master,
            GLib.PRIORITY_DEFAULT,
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._on_readable,
            pty,
        )
        pty._child_watch = GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self._on_child_exited, pty)
        self._record_row(pty)
        return pty_id

    def _record_row(self, pty: Pty) -> None:
        if self._record is not None:
            try:
                self._record(pty.id, None if pty._finished else pty.row())
            except Exception:
                log.exception("pty %d: the state writer failed", pty.id)

    # -- the read loop

    def _on_readable(self, fd: int, condition, pty: Pty) -> bool:
        if pty._finished:
            return GLib.SOURCE_REMOVE
        try:
            data = os.read(fd, READ_SIZE)
        except BlockingIOError:
            return GLib.SOURCE_CONTINUE
        except OSError as exc:
            if exc.errno != errno.EIO:
                log.warning("pty %d: read failed: %s", pty.id, exc)
            data = b""
        if data:
            self._consume(pty, pty.filter.feed(data))
            return GLib.SOURCE_CONTINUE
        # EOF: the slave side is closed, the child is gone or going.
        pty._eof = True
        pty._read_watch = 0
        self._consume(pty, pty.filter.flush())
        self._maybe_finish(pty)
        return GLib.SOURCE_REMOVE

    def _consume(self, pty: Pty, filtered: termstream.Filtered) -> None:
        for reply in filtered.replies:
            self._enqueue(pty, reply, reply=True)
        if filtered.forward:
            self._broadcast(pty, filtered.forward, 0)
            pty._dirty = True
            self._schedule_save(pty)
        if self._on_event is not None:
            for event in filtered.events:
                try:
                    self._on_event(pty.id, event)
                except Exception:
                    log.exception("pty %d: the event listener failed", pty.id)

    def _broadcast(self, pty: Pty, data: bytes, flags: int) -> None:
        for attachment in list(pty.attachments.values()):
            self._send(pty, attachment, data, flags)

    def _send(self, pty: Pty, attachment: _Attachment, data: bytes, flags: int) -> None:
        if attachment.queued + len(data) > QUEUE_BYTES:
            # The sink is not keeping up: it is not sent the backlog but a
            # fresh picture of the screen (§3.2).
            self._redraw(pty, attachment)
            return
        attachment.queued += len(data)
        try:
            attachment.sink.send_output(data, flags)
        except Exception:
            log.exception("pty %d: a sink failed; detaching it", pty.id)
            self._detach(pty, attachment)

    def drained(self, pty_id: int, sink, n: int) -> None:
        """The sink wrote ``n`` bytes of this pty's output out; the server's
        count of what it is holding goes down by that much."""
        pty = self.ptys.get(pty_id)
        if pty is None:
            return
        attachment = pty.attachments.get(id(sink))
        if attachment is not None:
            attachment.queued = max(0, attachment.queued - max(0, n))

    # -- the write queue

    def write(self, pty_id: int, data: bytes, sink=None) -> None:
        """Bytes for the child, in arrival order; from a sink, that sink
        becomes the active client."""
        pty = self.get(pty_id)
        if sink is not None:
            self._activate(pty, id(sink))
        self._enqueue(pty, bytes(data))

    def _enqueue(self, pty: Pty, data: bytes, reply: bool = False) -> None:
        if not data or pty._finished or pty.master < 0:
            return
        if pty._queued + len(data) > INPUT_QUEUE_BYTES:
            pty.dropped_input += len(data)
            if pty.dropped_input == len(data):
                log.warning("pty %d: input queue full; dropping input", pty.id)
            return
        if reply:
            # A query's answer is what the program is blocked on: ahead of
            # what is waiting, after any partial write already started.
            pty._queue.insert(1 if pty._queue else 0, data)
        else:
            pty._queue.append(data)
        pty._queued += len(data)
        self._flush(pty)

    def _flush(self, pty: Pty) -> bool:
        while pty._queue:
            head = pty._queue[0]
            try:
                n = os.write(pty.master, head)
            except BlockingIOError:
                if not pty._write_watch:
                    pty._write_watch = GLib.io_add_watch(
                        pty.master, GLib.PRIORITY_DEFAULT, GLib.IOCondition.OUT, self._on_writable, pty
                    )
                return False
            except OSError:
                pty._queued = 0
                pty._queue.clear()
                break
            pty._queued -= n
            if n < len(head):
                pty._queue[0] = head[n:]
            else:
                pty._queue.pop(0)
        return True

    def _on_writable(self, fd: int, condition, pty: Pty) -> bool:
        if pty._finished:
            pty._write_watch = 0
            return GLib.SOURCE_REMOVE
        if self._flush(pty):
            pty._write_watch = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    # -- sinks

    def attach(self, pty_id: int, sink, cols: int, rows: int) -> dict:
        """Show a pty on a sink: a redraw, then live output. The reply of
        the `attach` request: the grid the redraw was painted at, whether
        this sink is the active client, and whom the pty is sized for."""
        pty = self.get(pty_id)
        cols, rows = termscreen._clamp_grid(cols, rows)
        key = id(sink)
        attachment = pty.attachments.get(key)
        if attachment is None:
            attachment = pty.attachments[key] = _Attachment(sink, cols, rows)
        else:
            attachment.cols, attachment.rows, attachment.queued = cols, rows, 0
        if pty.active is None:
            pty.active = key
        if pty.active == key:
            pty.pending_sizes.pop(key, None)
            if (cols, rows) != (pty.cols, pty.rows):
                self._apply_size(pty, cols, rows)
        else:
            pty.pending_sizes[key] = (cols, rows)
        self._redraw(pty, attachment)
        return {"cols": pty.cols, "rows": pty.rows, "active": pty.active == key, "sized_for": pty.sized_for}

    def detach(self, pty_id: int, sink) -> None:
        pty = self.ptys.get(pty_id)
        if pty is None:
            return
        attachment = pty.attachments.get(id(sink))
        if attachment is not None:
            self._detach(pty, attachment)

    def _detach(self, pty: Pty, attachment: _Attachment) -> None:
        key = id(attachment.sink)
        pty.attachments.pop(key, None)
        pty.pending_sizes.pop(key, None)
        if pty.active == key:
            pty.active = None  # the next sink to attach, type or focus takes it

    def _redraw(self, pty: Pty, attachment: _Attachment) -> None:
        if attachment.queued:
            # The sink holds output this redraw supersedes.
            drop = getattr(attachment.sink, "drop_queued", None)
            if callable(drop):
                try:
                    drop()
                except Exception:
                    log.exception("pty %d: a sink's drop_queued failed", pty.id)
        attachment.queued = 0
        data = pty.screen.snapshot(pty.filter.preamble(screen=False))
        frames = [data[i : i + MAX_PAYLOAD] for i in range(0, len(data), MAX_PAYLOAD)] or [b""]
        for index, frame in enumerate(frames):
            flags = FLAG_REDRAW | (FLAG_REDRAW_END if index == len(frames) - 1 else 0)
            attachment.queued += len(frame)
            try:
                attachment.sink.send_output(frame, flags)
            except Exception:
                log.exception("pty %d: a sink failed; detaching it", pty.id)
                self._detach(pty, attachment)
                return

    def _activate(self, pty: Pty, key: int) -> None:
        """A sink typed or took focus: it is the active client now, and the
        size it asked for while it was not is applied."""
        if key not in pty.attachments:
            return
        if pty.active != key:
            pty.active = key
            size = pty.pending_sizes.pop(key, None)
            if size is not None and size != (pty.cols, pty.rows):
                self._apply_size(pty, *size)

    def focus(self, pty_id: int, sink, focused: bool) -> None:
        pty = self.get(pty_id)
        if focused:
            self._activate(pty, id(sink))

    def resize(self, pty_id: int, cols: int, rows: int, sink=None) -> None:
        """A grid for the pty: applied from the active sink or from the
        service itself (``sink`` None), remembered from any other sink."""
        pty = self.get(pty_id)
        cols, rows = termscreen._clamp_grid(cols, rows)
        if sink is not None:
            key = id(sink)
            attachment = pty.attachments.get(key)
            if attachment is None:
                return
            attachment.cols, attachment.rows = cols, rows
            if pty.active != key:
                pty.pending_sizes[key] = (cols, rows)
                return
        if (cols, rows) != (pty.cols, pty.rows):
            self._apply_size(pty, cols, rows)

    def _apply_size(self, pty: Pty, cols: int, rows: int) -> None:
        pty.cols, pty.rows = cols, rows
        pty.state.cols, pty.state.rows = cols, rows
        pty.screen.resize(cols, rows)
        if pty.master >= 0 and not pty._finished:
            try:
                set_window_size(pty.master, cols, rows)
            except OSError as exc:
                log.warning("pty %d: TIOCSWINSZ failed: %s", pty.id, exc)
        pty._dirty = True
        self._schedule_save(pty)
        self._record_row(pty)

    # -- the service's own writes

    def paint(self, pty_id: int, text: str) -> None:
        """Rule 2's inserted text: into the model and to every sink as if
        the child had written it."""
        pty = self.get(pty_id)
        data = text.encode("utf-8", "replace")
        if not data:
            return
        tokenizer = termstream.Tokenizer()
        pty.screen.feed(tokenizer.feed(data) + tokenizer.flush())
        self._broadcast(pty, data, 0)
        pty._dirty = True
        self._schedule_save(pty)

    def signal(self, pty_id: int, sig: int) -> None:
        pty = self.get(pty_id)
        if pty.pid is None or pty._finished:
            return
        try:
            os.kill(pty.pid, sig)
        except ProcessLookupError:
            pass

    def close(self, pty_id: int) -> None:
        """End a pty: SIGHUP to the child's process group and the master
        closed; `pty-exited` follows when the child is reaped."""
        pty = self.get(pty_id)
        if pty._finished:
            return
        if pty.pid is not None:
            try:
                os.killpg(pty.pid, signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                # Not a session leader yet (closed before it exec'd): the
                # process itself, then.
                try:
                    os.kill(pty.pid, signal.SIGHUP)
                except (ProcessLookupError, PermissionError):
                    pass
        self._close_master(pty)
        self._maybe_finish(pty)

    def shutdown(self) -> None:
        """Save every model and close every pty (§3.10, Phase 1)."""
        for pty in list(self.ptys.values()):
            self.save_model(pty.id)
            if not pty._finished:
                self.close(pty.id)

    # -- exit

    def _on_child_exited(self, pid: int, status: int, pty: Pty) -> None:
        pty._child_watch = 0
        pty._reaped = True
        try:
            pty.exit_status = os.waitstatus_to_exitcode(status)
        except ValueError:
            pty.exit_status = None
        # Whatever the child wrote last is still in the kernel's buffer:
        # drain it before the farewell.
        self._drain(pty)
        self._maybe_finish(pty)

    def _drain(self, pty: Pty) -> None:
        while pty.master >= 0 and not pty._eof:
            try:
                data = os.read(pty.master, READ_SIZE)
            except BlockingIOError:
                return
            except OSError:
                data = b""
            if not data:
                pty._eof = True
                self._consume(pty, pty.filter.flush())
                return
            self._consume(pty, pty.filter.feed(data))

    def _maybe_finish(self, pty: Pty) -> None:
        if pty._finished:
            return
        if not pty._reaped:
            # EOF before the child watch: the child closed the slave and is
            # exiting (or a daemon let go of its terminal). The watch reaps
            # it and comes back here; reaping it ourselves would race GLib.
            return
        if not pty._eof and pty.master >= 0:
            self._drain(pty)
            if not pty._eof:
                return
        self._finish(pty)

    def _finish(self, pty: Pty) -> None:
        pty._finished = True
        for name in ("_read_watch", "_write_watch", "_save_source"):
            source = getattr(pty, name)
            if source:
                GLib.source_remove(source)
                setattr(pty, name, 0)
        self._close_master(pty)
        pty._queue.clear()
        pty._queued = 0
        self._write_model(pty)
        self.ptys.pop(pty.id, None)
        self._record_row(pty)
        log.debug("pty %d: exited, status %s", pty.id, wait_status_name(pty.exit_status))
        event = {"t": "pty-exited", "pty": pty.id, "status": pty.exit_status}
        for attachment in list(pty.attachments.values()):
            try:
                attachment.sink.send_event(dict(event))
            except Exception:
                log.exception("pty %d: a sink failed on exit", pty.id)
        pty.attachments.clear()
        pty.active = None

    def _close_master(self, pty: Pty) -> None:
        if pty.master >= 0:
            if pty._read_watch:
                GLib.source_remove(pty._read_watch)
                pty._read_watch = 0
            if pty._write_watch:
                GLib.source_remove(pty._write_watch)
                pty._write_watch = 0
            try:
                os.close(pty.master)
            except OSError:
                pass
            pty.master = -1
            pty._eof = True

    # -- the saved model

    def _schedule_save(self, pty: Pty) -> None:
        if pty._save_source or pty._finished:
            return
        pty._save_source = GLib.timeout_add(SAVE_INTERVAL_MS, self._on_save_due, pty)

    def _on_save_due(self, pty: Pty) -> bool:
        pty._save_source = 0
        if not pty._finished:
            self._write_model(pty)
        return GLib.SOURCE_REMOVE

    def save_model(self, pty_id: int) -> Path | None:
        """Write the pty's model now; the path, or None when it failed."""
        pty = self.ptys.get(pty_id)
        if pty is None:
            return None
        return self._write_model(pty)

    def _write_model(self, pty: Pty) -> Path | None:
        if not pty._dirty:
            return pty.model_path() if pty.model_path().exists() else None
        path = pty.model_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(pty.screen.dump(), f, separators=(",", ":"), ensure_ascii=False)
            tmp.replace(path)
        except OSError as exc:
            log.warning("pty %d: saving the model failed: %s", pty.id, exc)
            return None
        pty._dirty = False
        return path

    def load_model(self, path: Path) -> termscreen.Screen | None:
        """A saved model, or None when the file is missing, damaged or
        from another format (then the caller starts fresh)."""
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return termscreen.Screen.load(data)
        except (OSError, ValueError, RecursionError):
            return None


def wait_status_name(status: int | None) -> str:
    """For logs: an exit code, ``signal N``, or ``unknown``."""
    if status is None:
        return "unknown"
    if status < 0:
        return f"signal {-status}"
    return str(status)

