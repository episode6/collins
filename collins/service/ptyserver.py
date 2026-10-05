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
size set on the slave before the fork, then `os.fork`. The child does only
os-level calls before `execvpe`: `os.login_tty(slave)` (a new session, the
slave its controlling terminal, the standard fds), the signal dispositions
Python inherited reset (SIGPIPE and SIGXFSZ back to their defaults, the
signal mask cleared), `chdir`, exec. A failed exec (an argv that is not
there, a cwd that is not) is reported back through a close-on-exec pipe,
and `spawn` raises `SpawnError` (an `OSError` subclass carrying the child's
errno) with the master closed and the child reaped: a caller sees the
failure where it asked, not as an exit 127 a moment later. In Phase 1 the
fork runs inside the GTK app (the loopback of PR-1.7), a threaded process;
that is acceptable because nothing Python-heavy runs between fork and exec
(no allocation of note, no locks taken, the same thing `pty.fork` and VTE
do), and PR-1.12 moves the whole server into the service process. The
environment is the caller's plus what VTE would have set and the CLI's
terminal detection reads: ``TERM=xterm-256color``, ``COLORTERM=truecolor``,
``VTE_VERSION`` (the client's, else 8400) and the two spoofs of
`terminal._agent_tab_environment` (``ConEmuANSI=ON``,
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

The write queue
---------------
**Every write is queued.** The master is non-blocking and the queue drains
behind a writability watch: a blocking write of a 100 KB paste deadlocked
the spike's server against a child that was echoing it (F11). Two queues
per pty, drained in this order: the entry a write already started (never
split further), then the filter's replies in the order their queries came
(§3.3, F11: a program closes a round of questions with DA1 and takes what
arrived before that answer as the round's answers), then typing and
requests in arrival order. A reply can therefore land between two frames
of a split paste; the program asked mid-paste and gets its answer there,
as it would from a terminal. Replies are never dropped: an unanswered
DA1 stalls the program. Typing is bounded at `INPUT_QUEUE_BYTES` (4 MiB):
a client that floods a pty whose child is not reading finds its input
dropped **whole**, from the first entry that would pass the bound until
the queue has drained completely (`Pty.dropped_input` counts the bytes),
so a bracketed paste is never cut in the middle leaving the program in
paste mode. Never a blocked loop.

Attach, the redraw and the active client
----------------------------------------
`attach(pty, sink, cols, rows)` answers with a redraw: `Screen.snapshot()`
with the mode tracker's preamble, in frames of at most
`protocol.MAX_PAYLOAD`, every one flagged `FLAG_REDRAW` and the last
`FLAG_REDRAW_END`, then live output. The active client owns the size (D1):
the last sink to send input or report focus, the first to attach when none
(a newcomer takes the role when no sink holds it, even with other sinks
attached: whoever last cared has gone, and the one arriving is the one
looking). A resize from the active sink is applied (``TIOCSWINSZ``, the
model resized, the filter's grid updated); one from any other sink is
remembered and applied when that sink becomes active; with no sink attached
the size is left alone. An attaching sink that is active and whose grid
differs is applied first, so its redraw is painted at its own size and the
program's repaint follows on the live stream; a sink that is not active is
redrawn at the pty's grid and told so (``active`` False and ``sized_for``,
the active sink's ``device`` when it has one). Every attached sink is sent
the protocol's ``pty`` event (the row, with the child's pid while it lives)
when the size or the active client changes, so a pinned sink and its "Sized
for" bar stay right, and a client learns the pid of what it attached to.

**Flow control** (§3.2): the server counts the live bytes it has handed
each sink for a pty and the sink reports what of them it has written out
with `PtyServer.drained(pty, sink, n)` (live bytes only, flags 0; a
redraw's frames are not counted and are not reported). When the count would pass
`protocol.QUEUE_BYTES` (4 MiB) the sink is not sent the backlog: its
``drop_queued()`` is called, the count is reset, and a fresh redraw follows,
flagged as one. A redraw's own frames are never counted against the bound
that triggered it, and **a redraw is bounded at attach time**: when the
whole snapshot would pass half the queue bound, the oldest scrollback rows
are left out of the redraw (not out of the model) until it fits, which is
§3.2's "a slow link shows the screen as it is now". A sink that never
reports draining is cut off and redrawn every 4 MiB (logged once per
attachment), the honest outcome for a transport that cannot say.

A sink is any object with ``send_output(data: bytes, flags: int)``,
``send_event(event: dict)`` and ``drop_queued()`` (discard what of this
pty is still queued: the redraw that follows supersedes it); a ``device``
attribute is optional, and so is ``term`` (its client's terminal: the
colours and scheme the hello and the ``theme`` event carry). Sinks are
keyed by identity.

**The colours a pty's queries are answered from are its active client's**
(§3.3): the active sink's ``term``, or, while no sink is active or the
active one has said nothing, the last term any client sent
(`set_term`). They are re-applied whenever the active client changes and
whenever a client's term does, so one client's theme never answers
another client's pty.

`paint(pty, text)` is rule 2's service-inserted text: it is fed to the
model and sent to the sinks as if the child had written it, between two
reads, so it lands at a token boundary; it does not go through the query
responder (the service does not answer its own questions), so a paint that
contains a query is a bug in the caller, not a reply.

A panel shell (a pty of kind ``shell``, PR-1.8) is asked three more things,
all read on the service's side of the pty: `Pty.shell_pid` (the child, or
for a sandboxed shell, one spawned with a box, the shell inside the box
below the launcher and bubblewrap, remembered once found),
`Pty.has_running_command` (the master's foreground group against that
shell's) and `Pty.process_cwd` (that shell's ``/proc`` cwd, where the row's
``cwd`` is where it started). `clear(pty)` swaps in a fresh model at the
pty's grid (a panel shell's *Clear*; the tracker keeps the program's modes
for the next attach), and `capture(pty)` is a live pty's text (what the
panel history is written from).

Exit
----
A `GLib.child_watch_add` reaps the child; the master is drained until EIO,
the filter flushed, the server's *on_exit* hook told while the model is
still whole (the service writes a panel shell's history from it there,
PR-1.11), every sink sent
``{"t": "pty-exited", "pty": id, "status": s}`` (the exit code, or minus
the signal number), the watches removed, the master closed and the pty
dropped from the table. The order is the same when EIO arrives first (the
child closed the slave) and the status comes later. `close(pty)` sends
SIGHUP to the child's process group and closes the master, which ends a
well-behaved child; one that ignores SIGHUP is sent SIGKILL after
`CLOSE_GRACE_MS`. `signal` sends anything else. `shutdown()` saves every
model and closes every pty (Phase 1: stopping the service ends every
agent, §3.10), waiting a bounded time for the saves in flight.

The saved model
---------------
A model file lives exactly as long as its pty's row: written while the
pty lives, removed when it exits (`_finish`) and at shutdown, and every
``*.model`` whose id is not in the table pruned at service start
(`prune_models`: a service that died before an exit landed). The
panel history and the transcript carry what a person needs after the
exit; the file exists for a live pty's re-adoption (PR-3.6).

`Screen.dump()` as JSON, written to
``$XDG_STATE_HOME/collins/pty/<pty id>.model`` (`COLLINS_PTY_STATE_DIR`
overrides the directory; tests, captures and e2e checks use it; the
directory is made 0700 and the file 0600) at most once per
`SAVE_INTERVAL_MS` while output arrives, for as long as the pty lives. The dump
is taken on the loop (tens of milliseconds for a full scrollback, which is
accepted for now and noted for PR-1.12's own loop); the JSON encoding and
the write run on a worker thread, one in flight per pty, a save that was
asked for meanwhile following it, the result landing at
`GLib.PRIORITY_DEFAULT` (CLAUDE.md's rule for anything that advances a
pipeline). It is what a restarted service reads to re-adopt a live pty
(§3.10 point 3; PR-3.6's keeper); it is removed when the pty exits and
pruned at service start when its pty is not in the table, and a closed
panel shell's text is the panel history's, not this file's. `Screen.load`
validates every field and bound (rule 5) and raises on anything off, and
`load_model` refuses a file over `MODEL_FILE_MAX` before reading it, so a
file an older or newer service wrote, or a damaged one, means a fresh
model, never a crash. Pty ids are
never reused across restarts: the next id is persisted beside the table
(`AppState.pty_next_id`, through the ``record_next_id`` callable), so no
two ptys ever share a model file.

The `ptys` table in `state.json` (§3.8) holds a row per live pty (kind,
session, cwd, pid, options, box, plan, size), written through the
``record`` callable the server is built with (`AppState.set_pty` /
`remove_pty`, wired in PR-1.7; a test needs no `AppState`): a spawn and an
exit are written at once, size changes coalesced to one write a second.

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
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gi.repository import GLib

from .. import proctree
from ..api.protocol import FLAG_REDRAW, FLAG_REDRAW_END, MAX_PAYLOAD, QUEUE_BYTES
from . import termscreen, termstream

log = logging.getLogger(__name__)

# The typing queue's bound per pty: past it a client's input is dropped
# whole until the queue has drained, never blocked on. The filter's replies
# are not counted.
INPUT_QUEUE_BYTES = 4 * 1024 * 1024
# A redraw is bounded at attach time: the oldest scrollback rows are left
# out until the snapshot fits (§3.2's "the screen as it is now").
REDRAW_MAX = QUEUE_BYTES // 2
READ_SIZE = 64 * 1024
# The saved model is written at most this often while output arrives.
SAVE_INTERVAL_MS = 3000
# How long shutdown waits for the saves in flight, all together.
SAVE_WAIT_S = 5.0
# A model file bigger than this is not even read (rule 5).
MODEL_FILE_MAX = 64 * 1024 * 1024
# After close(), a child that ignored SIGHUP is killed this much later.
CLOSE_GRACE_MS = 5000
# After the reap, how long a pty waits for EOF before it is finished anyway:
# a job the shell left holding the slave would otherwise keep a dead tab
# open for as long as it lives (VTE's child-exited fires at the reap).
EOF_AFTER_REAP_MS = 1000
# Size changes reach the ptys table at most this often.
RECORD_DEBOUNCE_MS = 1000
DEFAULT_VTE_VERSION = 8400
MODEL_SUFFIX = ".model"

_AGENT = "agent"
_SHELL = "shell"
KINDS = frozenset({_AGENT, _SHELL})


class SpawnError(OSError):
    """The child could not be started: the errno is the child's (ENOENT for
    an argv or cwd that is not there, EACCES, …)."""


def default_state_dir() -> Path:
    """Where the saved models live: `COLLINS_PTY_STATE_DIR`, else
    ``$XDG_STATE_HOME/collins/pty``."""
    override = os.environ.get("COLLINS_PTY_STATE_DIR")
    if override:
        return Path(override)
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "collins" / "pty"


def spawn_environment(env: dict[str, str] | None, vte_version: int, progress: bool = True) -> dict[str, str]:
    """The child's environment: the caller's, plus what VTE sets and, with
    *progress*, what makes the CLI announce its progress
    (`session.agent_environment`; off when the experimental setting is)."""
    out = dict(os.environ if env is None else env)
    out.update(TERM="xterm-256color", COLORTERM="truecolor", VTE_VERSION=str(vte_version))
    if progress:
        out.update(ConEmuANSI="ON", TERM_PROGRAM="kitty")
    return out


def set_window_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _fork_child(argv: list[str], cwd: str, env: dict[str, str], master: int, slave: int, report: int) -> None:
    """The child side of `spawn`: never returns. Only os-level calls, so a
    fork inside a threaded process is safe (see the module docstring)."""
    stage = _STAGE_SETUP
    try:
        os.close(master)
        os.login_tty(slave)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_SETMASK, set())
        stage = _STAGE_CHDIR
        os.chdir(cwd)
        stage = _STAGE_EXEC
        os.execvpe(argv[0], argv, env)
    except OSError as exc:
        code = exc.errno or errno.EIO
    except BaseException:
        code = errno.EIO
    try:
        os.write(report, struct.pack("iB", code, stage))
    except OSError:
        pass
    os._exit(127)


_STAGE_SETUP, _STAGE_CHDIR, _STAGE_EXEC = 0, 1, 2


class _Attachment:
    """One sink's view of one pty."""

    __slots__ = ("sink", "cols", "rows", "queued", "ever_drained", "fallback_logged")

    def __init__(self, sink, cols: int, rows: int):
        self.sink = sink
        self.cols, self.rows = cols, rows
        self.queued = 0  # live bytes handed to the sink and not yet drained
        self.ever_drained = False
        self.fallback_logged = False


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
        # A panel shell's history: the key its file is under (a session id,
        # a new-chat draft id; None while the tab has none, or once its page
        # closed for good) and its ordinal (panelhistory's).
        self.history: str | None = None
        self.ordinal: int = 0
        # The session the pty belongs to, by its handle: an agent's own, a
        # panel shell's agent (PR-1.12a: how `rekey_shells` finds them).
        self.handle: str | None = None
        self.state = termstream.TerminalState(cols=cols, rows=rows, vte_version=server.vte_version)
        server.apply_term(self.state)
        self.screen = termscreen.Screen(cols, rows)
        self.filter = termstream.StreamFilter(self.state, screen=self.screen)
        self.attachments: dict[int, _Attachment] = {}  # id(sink) -> attachment
        self.active: int | None = None  # id(sink)
        self.pending_sizes: dict[int, tuple[int, int]] = {}
        # The write queues: the entry a write already started, the replies,
        # the typing (see the module docstring).
        self._current: bytes | None = None
        self._current_typed = False
        self._replies: deque[bytes] = deque()
        self._inputs: deque[bytes] = deque()
        self._queued = 0  # typing bytes waiting, _current included when it was typing
        self._dropping = False
        self.dropped_input = 0
        self._read_watch = 0
        self._write_watch = 0
        self._child_watch = 0
        self._save_source = 0
        self._kill_source = 0
        self._record_source = 0
        self._eof_source = 0
        self._dirty = False
        self._save_thread: threading.Thread | None = None
        self._save_again = False
        self.exit_status: int | None = None
        self._reaped = False
        self._eof = False
        self._finished = False
        # A sandboxed panel shell's shell inside the box, once found (see
        # shell_pid).
        self._inner_pid: int | None = None

    # -- PtyPort

    def write(self, data: bytes) -> None:
        self.server.write(self.id, data)

    def resize(self, cols: int, rows: int) -> None:
        self.server.resize(self.id, cols, rows)

    def child_pid(self) -> int | None:
        return None if self._reaped else self.pid

    def foreground_pgrp(self) -> int | None:
        if self.master < 0 or self._finished:
            return None
        try:
            return os.tcgetpgrp(self.master)
        except OSError:
            return None

    # -- the shell's own reads (a panel shell, PR-1.8)

    def shell_pid(self) -> int | None:
        """The pid whose process group is "the shell at its prompt": the
        child itself; for a sandboxed panel shell (a ``shell`` pty spawned
        with a box) the shell *inside* the box, found below the launcher
        and bubblewrap once they have spawned it (`proctree.inner_shell_pid`)
        and remembered, since the wrappers never re-exec it. None once the
        child is reaped, and for a sandboxed one until the inner shell
        exists."""
        pid = self.child_pid()
        if pid is None or self.kind != _SHELL or not self.box:
            return pid
        if self._inner_pid is None:
            self._inner_pid = proctree.inner_shell_pid(pid)
        return self._inner_pid

    def has_running_command(self) -> bool:
        """Whether something other than the shell owns the terminal's
        foreground: the cue terminal emulators use for a close
        confirmation, and what the terminal tools call busy. A sandboxed
        shell is compared against the shell inside the box, which takes
        the foreground for itself (the kernel reports its process group in
        host pid numbers), so a shell at its prompt in a box reads idle like
        a plain one."""
        shell = self.shell_pid()
        foreground = self.foreground_pgrp()
        if shell is None or foreground is None:
            return False
        try:
            return foreground not in (-1, os.getpgid(shell))
        except OSError:
            return False

    def process_cwd(self) -> str | None:
        """The shell's working directory now, read from ``/proc`` (the
        row's ``cwd`` is where it was spawned): what a panel shell's
        follow-the-agent move compares against."""
        return proctree.process_cwd(self.shell_pid())

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
        if self.pid is not None and not self._reaped:
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
        record_next_id: Callable[[int], None] | None = None,
        vte_version: int = DEFAULT_VTE_VERSION,
        on_exit: Callable[[Pty], None] | None = None,
        on_output: Callable[[Pty], None] | None = None,
    ):
        """*on_exit* hears every pty whose child exited, before its model
        is dropped and before `pty-exited` goes out; *on_output* hears
        every pty that forwarded output (after the filter, once per read:
        the service's redraw signal, §3.6)."""
        self.state_dir = Path(state_dir) if state_dir is not None else default_state_dir()
        self._on_exit = on_exit
        self._on_output = on_output
        self._record = record
        self._record_next_id = record_next_id
        self._on_event = on_event
        self._next_id = max(1, next_id)
        self.vte_version = vte_version
        self.ptys: dict[int, Pty] = {}
        self._term: dict = {}  # the last term any client sent: the fallback (see pty_term)
        self._in_flight: dict[int, threading.Thread] = {}  # pty id -> its save worker

    # -- the table

    def get(self, pty_id: int) -> Pty:
        try:
            return self.ptys[pty_id]
        except KeyError:
            raise KeyError(f"no pty {pty_id}") from None

    def rows(self) -> dict[int, dict]:
        return {pty_id: pty.row() for pty_id, pty in self.ptys.items()}

    def set_term(self, term: dict) -> None:
        """A client's terminal changed (its hello's ``term``, the ``theme``
        event; the caller has already given the client's sinks their
        ``term``): remembered as the last one seen, the fallback, and every
        pty's colours re-read from its active client (`pty_term`)."""
        self._term = dict(term)
        for pty in self.ptys.values():
            self.apply_pty_term(pty)

    def pty_term(self, pty: Pty) -> dict:
        """The term *pty*'s queries are answered from: its active sink's,
        else the last one any client sent."""
        attachment = pty.attachments.get(pty.active) if pty.active is not None else None
        term = getattr(attachment.sink, "term", None) if attachment is not None else None
        return dict(term) if term else self._term

    def apply_pty_term(self, pty: Pty) -> None:
        self.apply_term(pty.state, self.pty_term(pty))

    def apply_term(self, state: termstream.TerminalState, term: dict | None = None) -> None:
        """Set *state*'s colours, scheme and VTE version from *term* (the
        fallback when None); a colour the term does not carry goes back to
        VTE's default, so a switch of active client leaves nothing of the
        last one's behind."""
        term = self._term if term is None else term
        state.foreground, state.background = termstream.VTE_FOREGROUND, termstream.VTE_BACKGROUND
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
        progress: bool = True,
        history: str | None = None,
        ordinal: int = 0,
    ) -> int:
        """Fork `argv` on a new pty; the new pty's id. Raises `SpawnError`
        when the child could not exec. *progress* adds the two progress
        declarations to the environment (`spawn_environment`); *history*
        and *ordinal* are a panel shell's history file (`Pty.history`)."""
        if kind not in KINDS:
            raise ValueError(f"kind {kind!r}")
        if not argv:
            raise ValueError("empty argv")
        cols, rows = termscreen._clamp_grid(cols, rows)
        pty_id = self._next_id
        pty = Pty(self, pty_id, kind, cwd, cols, rows)
        pty.session, pty.box, pty.plan, pty.options = session, box, plan, options
        pty.history, pty.ordinal = history, ordinal
        child_env = spawn_environment(env, pty.state.vte_version, progress)
        master, slave = os.openpty()
        report_r, report_w = os.pipe()  # close-on-exec by default (PEP 446)
        try:
            set_window_size(slave, cols, rows)
            pid = os.fork()
        except BaseException:
            for fd in (master, slave, report_r, report_w):
                os.close(fd)
            raise
        if pid == 0:
            _fork_child(argv, cwd, child_env, master, slave, report_w)  # never returns
        os.close(slave)
        os.close(report_w)
        try:
            failure = os.read(report_r, 5)  # EOF on a successful exec
        finally:
            os.close(report_r)
        if failure:
            os.close(master)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            code, stage = struct.unpack("iB", failure.ljust(5, b"\0"))
            what = cwd if stage == _STAGE_CHDIR else argv[0] if stage == _STAGE_EXEC else "the pty"
            raise SpawnError(code, os.strerror(code), what)
        self._next_id += 1
        if self._record_next_id is not None:
            try:
                self._record_next_id(self._next_id)
            except Exception:
                log.exception("the next-id writer failed")
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
        if pty._record_source:
            GLib.source_remove(pty._record_source)
            pty._record_source = 0
        if self._record is not None:
            try:
                self._record(pty.id, None if pty._finished else pty.row())
            except Exception:
                log.exception("pty %d: the state writer failed", pty.id)

    def _record_row_later(self, pty: Pty) -> None:
        """A size change: one write a second at most."""
        if pty._record_source or pty._finished:
            return
        pty._record_source = GLib.timeout_add(RECORD_DEBOUNCE_MS, self._on_record_due, pty)

    def _on_record_due(self, pty: Pty) -> bool:
        pty._record_source = 0
        if not pty._finished:
            self._record_row(pty)
        return GLib.SOURCE_REMOVE

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
            self._enqueue_reply(pty, reply)
        if filtered.forward:
            self._broadcast(pty, filtered.forward, 0)
            pty._dirty = True
            self._schedule_save(pty)
            if self._on_output is not None:
                try:
                    self._on_output(pty)
                except Exception:
                    log.exception("pty %d: the output listener failed", pty.id)
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
            if not attachment.ever_drained and not attachment.fallback_logged:
                attachment.fallback_logged = True
                log.warning("pty %d: a sink never reports draining; redrawing it on the fallback", pty.id)
            self._redraw(pty, attachment)
            return
        attachment.queued += len(data)
        try:
            attachment.sink.send_output(data, flags)
        except Exception:
            log.exception("pty %d: a sink failed; detaching it", pty.id)
            self._detach(pty, attachment)

    def drained(self, pty_id: int, sink, n: int) -> None:
        """The sink wrote ``n`` bytes of this pty's **live** output out (flags
        0; a redraw's frames are never counted and must not be reported);
        the server's count of what it is holding goes down by that much."""
        pty = self.ptys.get(pty_id)
        if pty is None:
            return
        attachment = pty.attachments.get(id(sink))
        if attachment is not None:
            attachment.ever_drained = True
            attachment.queued = max(0, attachment.queued - max(0, n))

    # -- the write queue

    def write(self, pty_id: int, data: bytes, sink=None) -> None:
        """Bytes for the child, in arrival order; from a sink, that sink
        becomes the active client."""
        pty = self.get(pty_id)
        if sink is not None:
            self._activate(pty, id(sink))
        self._enqueue_input(pty, bytes(data))

    def _enqueue_input(self, pty: Pty, data: bytes) -> None:
        if not data or pty._finished or pty.master < 0:
            return
        if pty._dropping and pty._current is None and not pty._inputs:
            pty._dropping = False  # drained since, with nothing to flush it
        if pty._dropping or pty._queued + len(data) > INPUT_QUEUE_BYTES:
            # Dropped whole, and everything after it until the queue has
            # drained; a single write over the bound with an empty queue is
            # dropped on its own.
            if not pty._dropping and (pty._current is not None or pty._inputs):
                pty._dropping = True
                log.warning("pty %d: input queue full; dropping input until it drains", pty.id)
            pty.dropped_input += len(data)
            return
        pty._inputs.append(data)
        pty._queued += len(data)
        self._flush(pty)

    def _enqueue_reply(self, pty: Pty, data: bytes) -> None:
        if not data or pty._finished or pty.master < 0:
            return
        pty._replies.append(data)
        self._flush(pty)

    def _flush(self, pty: Pty) -> bool:
        """Write what is queued until the master would block; True when
        the queues are empty."""
        while True:
            if pty._current is None:
                if pty._replies:
                    pty._current = pty._replies.popleft()
                    pty._current_typed = False
                elif pty._inputs:
                    pty._current = pty._inputs.popleft()
                    pty._current_typed = True
                else:
                    if pty._dropping:
                        pty._dropping = False
                    return True
            try:
                n = os.write(pty.master, pty._current)
            except BlockingIOError:
                if not pty._write_watch:
                    pty._write_watch = GLib.io_add_watch(
                        pty.master, GLib.PRIORITY_DEFAULT, GLib.IOCondition.OUT, self._on_writable, pty
                    )
                return False
            except OSError:
                self._clear_queues(pty)
                return True
            if pty._current_typed:
                pty._queued -= n
            if n < len(pty._current):
                pty._current = pty._current[n:]
            else:
                pty._current = None

    def _clear_queues(self, pty: Pty) -> None:
        pty._current = None
        pty._replies.clear()
        pty._inputs.clear()
        pty._queued = 0
        pty._dropping = False

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
        became_active = False
        if pty.active is None:
            pty.active = key
            became_active = True
            self.apply_pty_term(pty)
        if pty.active == key:
            pty.pending_sizes.pop(key, None)
            if (cols, rows) != (pty.cols, pty.rows):
                self._apply_size(pty, cols, rows, skip=attachment)
            elif became_active:
                self._announce(pty, skip=attachment)
        else:
            pty.pending_sizes[key] = (cols, rows)
        self._redraw(pty, attachment)
        if key in pty.attachments:
            self._announce(pty, only=attachment)  # the attacher's, once, after its redraw
        return {
            "cols": pty.cols,
            "rows": pty.rows,
            "active": pty.active == key,
            "sized_for": pty.sized_for,
            "modes": pty.filter.modes.assertions(),
        }

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
            self.apply_pty_term(pty)
            self._announce(pty)

    def _redraw(self, pty: Pty, attachment: _Attachment) -> None:
        if attachment.queued:
            try:
                attachment.sink.drop_queued()
            except Exception:
                log.exception("pty %d: a sink's drop_queued failed", pty.id)
        attachment.queued = 0
        data = self.redraw_bytes(pty)
        frames = [data[i : i + MAX_PAYLOAD] for i in range(0, len(data), MAX_PAYLOAD)] or [b""]
        for index, frame in enumerate(frames):
            flags = FLAG_REDRAW | (FLAG_REDRAW_END if index == len(frames) - 1 else 0)
            try:
                attachment.sink.send_output(frame, flags)
            except Exception:
                log.exception("pty %d: a sink failed; detaching it", pty.id)
                self._detach(pty, attachment)
                return

    def redraw_bytes(self, pty: Pty) -> bytes:
        """The redraw, bounded at `REDRAW_MAX`: the newest scrollback rows
        that fit, the screen always."""
        preamble = pty.filter.preamble(screen=False)
        data = pty.screen.snapshot(preamble)
        if len(data) <= REDRAW_MAX:
            return data
        # The screen alone, then as many of the newest rows as fit, found
        # by halving (each try serializes the rows it keeps).
        rows = len(pty.screen.scrollback)
        best = pty.screen.snapshot(preamble, scrollback_rows=0)
        low, high = 0, rows
        while high - low > 1:
            mid = (low + high) // 2
            attempt = pty.screen.snapshot(preamble, scrollback_rows=mid)
            if len(attempt) <= REDRAW_MAX:
                best, low = attempt, mid
            else:
                high = mid
        return best

    def _announce(self, pty: Pty, only: _Attachment | None = None, skip: _Attachment | None = None) -> None:
        """The `pty` event to every attached sink (or one, or all but one):
        the size and who owns it."""
        targets = [only] if only is not None else [a for a in pty.attachments.values() if a is not skip]
        for attachment in targets:
            event = {
                "t": "pty",
                "pty": pty.id,
                "kind": pty.kind,
                "cwd": pty.cwd,
                "cols": pty.cols,
                "rows": pty.rows,
                "active": pty.active == id(attachment.sink),
                "sized_for": pty.sized_for,
            }
            if pty.pid is not None and not pty._reaped:
                event["pid"] = pty.pid
            if pty.session:
                event["session"] = pty.session
            try:
                attachment.sink.send_event(event)
            except Exception:
                log.exception("pty %d: a sink failed; detaching it", pty.id)
                self._detach(pty, attachment)

    def _activate(self, pty: Pty, key: int) -> None:
        """A sink typed or took focus: it is the active client now, and the
        size it asked for while it was not is applied."""
        if key not in pty.attachments:
            return
        if pty.active != key:
            pty.active = key
            self.apply_pty_term(pty)
            size = pty.pending_sizes.pop(key, None)
            if size is not None and size != (pty.cols, pty.rows):
                self._apply_size(pty, *size)
            else:
                self._announce(pty)

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

    def _apply_size(self, pty: Pty, cols: int, rows: int, skip: _Attachment | None = None) -> None:
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
        self._record_row_later(pty)
        self._announce(pty, skip=skip)

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

    def clear(self, pty_id: int) -> None:
        """Wipe a pty's screen and scrollback: a fresh model at the pty's
        grid, which is what VTE's reset with its history cleared leaves of
        a terminal (a panel shell's *Clear*). The modes the program set
        stay in the tracker, so a client attaching again (the one that
        asked does, to be redrawn from the empty model) has them
        re-asserted. Nothing is written to the child: nudging it to repaint
        is the caller's."""
        pty = self.get(pty_id)
        pty.screen = termscreen.Screen(pty.cols, pty.rows)
        pty.filter.screen = pty.screen
        pty._dirty = True
        self._schedule_save(pty)

    def capture(self, pty_id: int) -> str:
        """A pty's text, scrollback and screen (`Screen.capture_contents`),
        "" once it is gone: its model file goes with its row, so the panel
        history is written while the shell lives (the tab's saves, the
        last one before its shells end)."""
        pty = self.ptys.get(pty_id)
        return pty.screen.capture_contents() if pty is not None else ""

    def signal(self, pty_id: int, sig: int) -> None:
        pty = self.get(pty_id)
        if pty.pid is None or pty._reaped:
            return
        try:
            os.kill(pty.pid, sig)
        except ProcessLookupError:
            pass

    def close(self, pty_id: int) -> None:
        """End a pty: SIGHUP to the child's process group and the master
        closed; SIGKILL after `CLOSE_GRACE_MS` if it is still there;
        `pty-exited` follows when the child is reaped."""
        pty = self.get(pty_id)
        if pty._finished:
            return
        if pty.pid is not None and not pty._reaped:
            # The foreground job's group too, read while the master is
            # still open: a job the shell put in a group of its own, with
            # HUP ignored, would otherwise outlive the shell and be
            # orphaned (a spawned service inherits nothing of it).
            fg = pty.foreground_pgrp()
            self._signal_group(pty, signal.SIGHUP)
            if fg and fg > 0 and fg != pty.pid:
                self._end_foreground_group(pty.id, fg)
            if not pty._kill_source:
                pty._kill_source = GLib.timeout_add(CLOSE_GRACE_MS, self._on_close_grace_over, pty)
        self._close_master(pty)
        self._maybe_finish(pty)

    @staticmethod
    def _end_foreground_group(pty_id: int, pgrp: int) -> None:
        """SIGHUP to the job's own group now and SIGKILL after the grace,
        on a timer of its own: the shell usually dies of its HUP at once
        and the pty is finished (its sources gone) before the grace is
        over, which must not spare a job that ignores HUP."""
        try:
            os.killpg(pgrp, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            return

        def kill() -> bool:
            try:
                os.killpg(pgrp, signal.SIGKILL)
                log.warning("pty %d: the foreground job (group %d) ignored SIGHUP; killed it", pty_id, pgrp)
            except (ProcessLookupError, PermissionError):
                pass
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(CLOSE_GRACE_MS, kill)

    def _signal_group(self, pty: Pty, sig: int) -> None:
        try:
            os.killpg(pty.pid, sig)
        except (ProcessLookupError, PermissionError):
            # Not a session leader (yet): the process itself, then.
            try:
                os.kill(pty.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def _on_close_grace_over(self, pty: Pty) -> bool:
        pty._kill_source = 0
        if pty.pid is not None and not pty._reaped:
            log.warning("pty %d: the child ignored SIGHUP; killing it", pty.id)
            self._signal_group(pty, signal.SIGKILL)
        return GLib.SOURCE_REMOVE

    def shutdown(self) -> None:
        """Save every model and close every pty (§3.10, Phase 1), and wait
        a bounded time for the saves in flight."""
        ptys = list(self.ptys.values())
        for pty in ptys:
            if not pty._finished:
                self.close(pty.id)
        self.wait_for_saves()
        # The reap no longer lands on a stopping service: every pty it
        # closed is finished here, its row recorded gone and its model
        # file removed with it (the file lives as long as the row).
        for pty in ptys:
            if not pty._finished:
                pty._reaped = True
                pty.exit_status = None
                self._finish(pty)

    def wait_for_saves(self, timeout: float = SAVE_WAIT_S) -> None:
        threads = [t for t in self._save_threads() if t.is_alive()]
        deadline = GLib.get_monotonic_time() + int(timeout * 1_000_000)
        for thread in threads:
            remaining = (deadline - GLib.get_monotonic_time()) / 1_000_000
            if remaining <= 0:
                break
            thread.join(remaining)

    def _save_threads(self) -> list[threading.Thread]:
        return list(self._in_flight.values())

    # -- exit

    def _on_child_exited(self, pid: int, status: int, pty: Pty) -> None:
        pty._child_watch = 0
        pty._reaped = True
        if pty._kill_source:
            GLib.source_remove(pty._kill_source)
            pty._kill_source = 0
        try:
            pty.exit_status = os.waitstatus_to_exitcode(status)
        except ValueError:
            pty.exit_status = None
        # Whatever the child wrote last is still in the kernel's buffer:
        # drain it before the farewell.
        self._drain(pty)
        self._maybe_finish(pty)
        if not pty._finished and not pty._eof_source:
            # Something the child left behind still holds the slave: the
            # pty is finished after a bound anyway, and closing the master
            # then hangs the leftover up.
            pty._eof_source = GLib.timeout_add(EOF_AFTER_REAP_MS, self._on_eof_overdue, pty)

    def _on_eof_overdue(self, pty: Pty) -> bool:
        pty._eof_source = 0
        if not pty._finished:
            self._drain(pty)
            log.debug("pty %d: reaped, no EOF after %d ms; finishing", pty.id, EOF_AFTER_REAP_MS)
            self._finish(pty)
        return GLib.SOURCE_REMOVE

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
        if self._on_exit is not None:
            try:
                self._on_exit(pty)
            except Exception:
                log.exception("pty %d: the exit hook failed", pty.id)
        for name in (
            "_read_watch", "_write_watch", "_save_source", "_kill_source", "_record_source", "_eof_source"
        ):
            source = getattr(pty, name)
            if source:
                GLib.source_remove(source)
                setattr(pty, name, 0)
        self._close_master(pty)
        self._clear_queues(pty)
        # The model file lives exactly as long as the pty's row: a save
        # in flight is let go (its landing finds the pty finished), the
        # file removed. The panel history and the transcript carry what
        # a person needs after the exit; the file exists for a live pty's
        # re-adoption (PR-3.6).
        pty._dirty = pty._save_again = False
        self.ptys.pop(pty.id, None)
        self._record_row(pty)
        self._remove_model(pty)
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
        """Start writing the pty's model now (on the worker); the path it
        goes to, or None when there is nothing to write."""
        pty = self.ptys.get(pty_id)
        if pty is None:
            return None
        return self._write_model(pty)

    def _write_model(self, pty: Pty) -> Path | None:
        if not pty._dirty:
            return pty.model_path() if pty.model_path().exists() else None
        if pty._save_thread is not None and pty._save_thread.is_alive():
            pty._save_again = True  # the worker lands and goes again
            return pty.model_path()
        data = pty.screen.dump()  # on the loop; tens of ms at most
        pty._save_again = False
        path = pty.model_path()
        thread = threading.Thread(target=self._save_worker, args=(pty, path, data), daemon=True)
        pty._save_thread = thread
        self._in_flight[pty.id] = thread
        thread.start()
        return path

    def _save_worker(self, pty: Pty, path: Path, data: dict) -> None:
        error = None
        try:
            write_model_file(path, data)
        except OSError as exc:
            error = exc
        GLib.idle_add(self._on_saved, pty, path, error, priority=GLib.PRIORITY_DEFAULT)

    def _on_saved(self, pty: Pty, path: Path, error: OSError | None) -> bool:
        self._in_flight.pop(pty.id, None)
        pty._save_thread = None
        if pty._finished:
            self._remove_model(pty)  # a save that landed after the exit
            return GLib.SOURCE_REMOVE
        if error is not None:
            log.warning("pty %d: saving the model failed: %s", pty.id, error)
            # Still dirty: the next due save retries, output or not.
        elif not pty._save_again:
            pty._dirty = False
        if pty._save_again:
            self._write_model(pty)
        elif error is not None and not pty._save_source and not pty._finished:
            self._schedule_save(pty)
        return GLib.SOURCE_REMOVE

    def _remove_model(self, pty: Pty) -> None:
        try:
            pty.model_path().unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("pty %d: removing the model file failed: %s", pty.id, exc)

    def prune_models(self) -> int:
        """At service start: every ``*.model`` whose pty is not in the
        table (a service that died before the exit landed) is removed;
        how many were."""
        removed = 0
        try:
            names = list(self.state_dir.iterdir())
        except OSError:
            return 0
        for path in names:
            if path.suffix != MODEL_SUFFIX:
                continue
            stem = path.stem
            if stem.isdigit() and int(stem) in self.ptys:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                log.warning("%s: pruning failed: %s", path, exc)
        return removed

    def load_model(self, path: Path) -> termscreen.Screen | None:
        """A saved model, or None when the file is missing, too big,
        damaged or from another format (then the caller starts fresh)."""
        try:
            if os.stat(path).st_size > MODEL_FILE_MAX:
                log.warning("%s: a model file over %d bytes; ignored", path, MODEL_FILE_MAX)
                return None
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return termscreen.Screen.load(data)
        except (OSError, ValueError, RecursionError):
            return None


def write_model_file(path: Path, data: dict) -> None:
    """The model as compact JSON, through a 0600 temp file and a rename, in
    a 0700 directory. Runs on the worker thread."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def wait_status_name(status: int | None) -> str:
    """For logs: an exit code, ``signal N``, or ``unknown``."""
    if status is None:
        return "unknown"
    if status < 0:
        return f"signal {-status}"
    return str(status)
