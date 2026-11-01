# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The ssh forward as Collins uses it (split-service spec §3.2, §3.24, D10).
GTK-free; `tests/test_sshforward.py` drives it with fakes.

The ssh runs themselves are `sshremote`, the MIT-licensed module another
client can vendor. This is what Collins puts around them: a connection's
target, remote app id and discovery command, where its forward's socket
lives, the words the connect dialog and the banner show, and
`RemoteTransport`.

`RemoteTransport` is what `connection.ConnectionManager.start_remote`
drives: its `find` is the local socket's probe (``none`` while no forward
runs or a restart is wanted), its `start` the two runs, and a connect that
failed over a live forward marks the next find ``none`` so the start runs
`--print-socket` again — the way a remote service that died is restarted.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass

from . import APP_ID, sandboxplan, sshremote
from .api import server as api_server
from .connections import Connection
from .i18n import N_, translate

DETAIL_MAX = sshremote.DETAIL_MAX
STARTED_HOW = "forward"  # the manager's `started_how` for a remote start

# The words the dialog and the banner show (msgids: translated with
# `i18n.translate` here, since the detail is ssh's own text and may hold
# braces).
MSG_NO_SSH = N_("ssh is not installed on this computer")
MSG_CONNECT_FAILED = N_("ssh could not connect to {target}: {detail}")
MSG_REMOTE_FAILED = N_("{program} failed on {target} (exit {code}): {detail}")
MSG_TIMEOUT = N_("no answer from {target} within {seconds} s")
MSG_BAD_PATH = N_("{program} on {target} printed no socket path")
MSG_NOT_ON_PATH = N_("{program} is not on the login shell's PATH on {target}")
MSG_HANDOVER = N_(
    "the command never ran on {target}: a login or interactive startup file there "
    "(~/.profile, ~/.bash_profile, ~/.bashrc) hands the shell on (exec zsh, tmux)? Set the "
    "PATH above that line, or guard it with [ -t 0 ]."
)
MSG_NO_SHELL = N_("exit 127 from the login shell on {target}: is $SHELL installed there?")
MSG_FORWARD_ENDED = N_("ssh to {target} ended: {detail}")
MSG_FORWARD_CLOSED = N_("the ssh forward to {target} closed")
MSG_FORWARD_EXIT = N_("the ssh forward to {target} ended (exit {code}): {detail}")
# What exit 255 means under BatchMode, appended by the dialog.
MSG_BATCHMODE = N_(
    "ssh runs in batch mode: the key must be in your ssh agent or need no "
    "passphrase, and the host's key must already be in known_hosts."
)


class ForwardError(OSError):
    """A start that failed, with the person's word in `text`."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


# ---- the words -------------------------------------------------------------------------


def exit_text(connection: Connection, code: int | None, detail: str) -> str:
    """The banner's text for a forward that ended with *code* and *detail*
    (its stderr's last line)."""
    if not detail:
        # A child killed by a signal has no last line of its own (Popen
        # reports -N): say which signal; else the bare exit status.
        detail = f"signal {-code}" if isinstance(code, int) and code < 0 else f"exit {code}"
    args = {"target": connection.target, "code": code, "detail": detail}
    if code == sshremote.SSH_EXIT_FAILED:
        return translate(MSG_FORWARD_ENDED, args)
    if code == 0:
        return translate(MSG_FORWARD_CLOSED, args)
    return translate(MSG_FORWARD_EXIT, args)


# ---- --print-socket ---------------------------------------------------------------------


@dataclass
class PrintSocketResult(sshremote.PrintSocketResult):
    """`sshremote`'s result with what Collins shows for it."""

    text: str = ""  # the person's word when not ok
    batch_mode_hint: bool = False  # exit 255: say what BatchMode means


def _target(connection: Connection) -> sshremote.Target:
    target = connection.parsed_target()
    if target is None:
        raise ValueError(f"connection {connection.id!r} has no valid target")
    return target


def _failure_text(
    result: sshremote.PrintSocketResult, target: str, timeout: float, program: str
) -> str:
    """The person's word for a failed discovery run of *program*."""
    args = {"target": target, "code": result.code, "detail": result.detail or "-", "program": program}
    failure = result.failure
    if failure == sshremote.NO_SSH:
        return translate(MSG_NO_SSH)
    if failure == sshremote.TIMED_OUT:
        return translate(MSG_TIMEOUT, {"target": target, "seconds": int(timeout)})
    if failure == sshremote.OS_ERROR:
        return result.detail
    if failure == sshremote.CONNECT_FAILED:
        detail = result.detail or f"exit {result.code}"
        return translate(MSG_CONNECT_FAILED, {"target": target, "detail": detail})
    if failure in (sshremote.BAD_PATH, sshremote.HANDOVER):
        text = translate(MSG_BAD_PATH, args)
        return f"{text}\n{translate(MSG_HANDOVER, args)}" if failure == sshremote.HANDOVER else text
    text = translate(MSG_REMOTE_FAILED, args)
    if failure == sshremote.NOT_ON_PATH:
        return f"{text}\n{translate(MSG_NOT_ON_PATH, args)}"
    if failure == sshremote.NO_SHELL:
        return f"{text}\n{translate(MSG_NO_SHELL, args)}"
    return text


def print_socket(
    connection: Connection,
    timeout: float = sshremote.PRINT_SOCKET_TIMEOUT_S,
    **options,
) -> PrintSocketResult:
    """`sshremote.print_socket` for *connection* (its discovery command), with
    the person's word on a failure. Raises ValueError for a connection with no
    valid target."""
    command = connection.discovery_command
    plain = sshremote.print_socket(
        _target(connection), connection.remote_app_id, timeout=timeout, command=command, **options
    )
    program = sshremote.command_program(command) or command
    return PrintSocketResult(
        ok=plain.ok,
        path=plain.path,
        code=plain.code,
        detail=plain.detail,
        failure=plain.failure,
        text="" if plain.ok else _failure_text(plain, connection.target, timeout, program),
        batch_mode_hint=plain.failure == sshremote.CONNECT_FAILED,
    )


# ---- the forward's socket --------------------------------------------------------------


def remote_dir(app_id: str = APP_ID) -> str:
    """``<runtime dir>/collins/remote/<app id>``: the local sockets (and
    pid files) of every forward of one app id, in the per-user runtime
    directory (`$XDG_RUNTIME_DIR`), else under Collins' state directory, as
    `api.server.runtime_dir` falls back; an intended path too long for a
    socket takes the same hashed stand-in. Scoped by app id (the
    verification of PR 623, N1): `collins` and `./start-debug` share
    `ui-state.json` and so the connection ids, and must never share a
    forward's path, or each would take over the other's."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        directory = os.path.join(os.environ["XDG_RUNTIME_DIR"], "collins", "remote", app_id)
    else:
        directory = os.path.join(sandboxplan.state_base(), "collins", "remote", app_id)
    longest = os.path.join(directory, "f" * 32 + ".sock")
    if len(longest.encode()) > api_server.SOCKET_PATH_MAX:
        directory = api_server._short_runtime_dir(directory)
    return directory


def local_socket_path(connection_id: str, app_id: str = APP_ID) -> str:
    return os.path.join(remote_dir(app_id), f"{connection_id}.sock")


def sweep_orphans(directory: str | None = None, app_id: str = APP_ID) -> list[int]:
    """`sshremote.sweep_orphans` over this app id's forwards."""
    return sshremote.sweep_orphans(directory or remote_dir(app_id))


# ---- the manager's transport ------------------------------------------------------------


def _make_forward(connection: Connection, remote_path: str, *, local_path: str, on_exit) -> sshremote.Forward:
    return sshremote.Forward(_target(connection), remote_path, local_path, on_exit=on_exit)


class RemoteTransport:
    """`find`, `start`, `connect_failed` and `stop` for one ssh connection,
    the shape `connection.ConnectionManager.start_remote` takes (the
    module docstring's last paragraph). *land* runs a callable on the
    owner's loop (the forward's exit is reported from its reader thread);
    *on_lost(text)* is the manager's `lost`."""

    def __init__(
        self,
        connection: Connection,
        *,
        on_lost: Callable[[str], None],
        land: Callable[[Callable[[], None]], None] = lambda fn: fn(),
        print_socket: Callable[[Connection], PrintSocketResult] = print_socket,
        make_forward: Callable[..., sshremote.Forward] = _make_forward,
        probe: Callable[[str], str] = api_server.probe_socket,
        app_id: str = APP_ID,
    ) -> None:
        self.connection = connection
        self.app_id = app_id
        self.local_path = local_socket_path(connection.id, app_id)
        self._on_lost = on_lost
        self._land = land
        self._print_socket = print_socket
        self._make_forward = make_forward
        self._probe = probe
        self.forward: sshremote.Forward | None = None
        self.remote_path = ""
        self._restart_wanted = False
        self.stopped = False
        # `start` runs on the manager's worker thread while `stop` (a
        # switch, a quit) comes from the main loop: the lock makes "stopped"
        # and "the forward" one state, so a stop that lands while
        # print-socket runs leaves no child behind (M2 of PR 623's review).
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self.connection.name

    def find(self, _app_id: str) -> tuple[str, str]:
        """The local socket's word: ``none`` while no forward runs (or a
        restart is wanted), else the probe's."""
        if self.stopped or self.forward is None or not self.forward.alive or self._restart_wanted:
            return self.local_path, "none"
        return self.local_path, self._probe(self.local_path)

    def start(self, _app_id: str) -> str:
        """The two runs; raises `ForwardError` with the person's word."""
        with self._lock:
            if self.stopped:
                raise ForwardError("stopped")
            self._restart_wanted = False
            old, self.forward = self.forward, None
        if old is not None:
            old.stop()
        result = self._print_socket(self.connection)  # up to PRINT_SOCKET_TIMEOUT_S
        if not result.ok:
            raise ForwardError(result.text)
        forward = self._make_forward(
            self.connection, result.path, local_path=self.local_path, on_exit=self._exited
        )
        with self._lock:
            if self.stopped:  # a switch or a quit landed meanwhile: spawn nothing
                raise ForwardError("stopped")
            self.remote_path = result.path
            # Known before it starts, so a death in its first instant is
            # reported as this forward's, not dropped as a stranger's.
            self.forward = forward
        try:
            forward.start()
        except (OSError, ValueError) as exc:  # a non-socket at the path, no ssh, a bad path
            with self._lock:
                if self.forward is forward:
                    self.forward = None
            raise ForwardError(str(exc)) from exc
        with self._lock:
            if self.stopped:  # stopped between the spawn and here: end it, quietly
                self.forward = None
                stop_now = True
            else:
                stop_now = False
        if stop_now:
            forward.stop()
            raise ForwardError("stopped")
        return STARTED_HOW

    def _exited(self, forward: sshremote.Forward, code: int | None, detail: str) -> None:
        text = exit_text(self.connection, code, detail)

        def report() -> None:
            if self.stopped or forward is not self.forward:
                return  # replaced or stopped meanwhile: not a loss
            self._on_lost(text)

        self._land(report)

    def connect_failed(self) -> None:
        """The hello failed over a live forward: the remote service is not
        answering. The next `find` says ``none`` so `start` runs
        `--print-socket` again, which starts it."""
        self._restart_wanted = True

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
            forward, self.forward = self.forward, None
        if forward is not None:
            forward.stop()
        try:
            sshremote.unlink_socket(self.local_path)
        except OSError:
            pass
