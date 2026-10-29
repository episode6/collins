# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The ssh forward: a remote service's Unix socket, here (split-service
spec §3.2, §3.24, D10). GTK-free; `tests/test_sshforward.py` drives it
with fakes for `subprocess.run` and `Popen`.

Two ssh runs make a remote connection:

1. ``ssh TARGET collins-service --print-socket`` (`print_socket`): starts
   the service on the far side when none answers and prints its socket's
   path; bounded by `PRINT_SOCKET_TIMEOUT_S`. ssh's exit 255 is "could not
   connect" (no route, a refused key, an unknown host key), any other
   non-zero exit is `collins-service`'s own; in both the last line of
   stderr is the word shown to the person.
2. ``ssh -N -L <local>.sock:<remote api.sock> TARGET`` (`Forward`): the
   child that holds the forward for the connection's life. Its local socket
   is ``<runtime dir>/collins/remote/<connection id>.sock``, unlinked first
   (only a socket is ever unlinked: a file of another kind at that path is
   refused). The child's exit is the connection manager's `lost`, its
   stderr's last line the banner's text (`exit_text`).

Every run has ``BatchMode=yes`` (no password or passphrase prompt can be
answered from here: a key that needs one must be in the agent, and the
connect dialog says so on exit 255), ``ServerAliveInterval=15`` with
``ServerAliveCountMax=3`` (ssh's own liveness check, 45 s; libsoup's
keepalives on both ends notice a dead link in 20 s, PR-1.12b), and
``ControlMaster=no`` / ``ControlPath=none``, so the forward is a connection
of its own: under a user's ControlMaster the forward would live in the mux
master, killing this child would end nothing, and the ServerAlive options
would be the master's (spike 5's finding, kept here).

`RemoteTransport` is what `connection.ConnectionManager.start_remote`
drives: its `find` is the local socket's probe (``none`` while no forward
runs or a restart is wanted), its `start` the two runs above, and a
connect that failed over a live forward marks the next find ``none`` so
the start runs `--print-socket` again — the way a remote service that
died is restarted.
"""

from __future__ import annotations

import logging
import os
import stat
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass

from . import sandboxplan
from .api import server as api_server
from .connections import Connection, Target
from .i18n import N_, translate

log = logging.getLogger(__name__)

SSH = "ssh"
PRINT_SOCKET_TIMEOUT_S = 10.0
SERVER_ALIVE_INTERVAL_S = 15
SERVER_ALIVE_COUNT_MAX = 3
SSH_EXIT_FAILED = 255  # ssh's own failure: no connection, no key, no host key
REMOTE_COMMAND = ["collins-service", "--print-socket"]
# How much of the child's stderr is kept: the last line, this long.
DETAIL_MAX = 240
STOP_WAIT_S = 2.0
STARTED_HOW = "forward"  # the manager's `started_how` for a remote start

# The words the dialog and the banner show (msgids: translated with
# `i18n.translate` here, since the detail is ssh's own text and may hold
# braces).
MSG_NO_SSH = N_("ssh is not installed on this computer")
MSG_CONNECT_FAILED = N_("ssh could not connect to {target}: {detail}")
MSG_REMOTE_FAILED = N_("collins-service failed on {target} (exit {code}): {detail}")
MSG_TIMEOUT = N_("no answer from {target} within {seconds} s")
MSG_BAD_PATH = N_("collins-service on {target} printed no socket path")
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


# ---- argv -------------------------------------------------------------------------------


def ssh_options(target: Target) -> list[str]:
    """The options every run of this module shares (see the module
    docstring), the port when the target names one."""
    argv = [
        "-o", "BatchMode=yes",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", f"ServerAliveInterval={SERVER_ALIVE_INTERVAL_S}",
        "-o", f"ServerAliveCountMax={SERVER_ALIVE_COUNT_MAX}",
    ]  # fmt: skip
    if target.port is not None:
        argv += ["-p", str(target.port)]
    return argv


def _target(connection: Connection) -> Target:
    target = connection.parsed_target()
    if target is None:
        raise ValueError(f"connection {connection.id!r} has no valid target")
    return target


def print_socket_argv(connection: Connection) -> list[str]:
    """``ssh … TARGET collins-service --print-socket [--app-id ID]``. The
    remote command is a fixed list; the app id was validated on the way in
    (`connections.valid_app_id`) and holds nothing a shell reads."""
    target = _target(connection)
    command = list(REMOTE_COMMAND)
    if connection.remote_app_id:
        command += ["--app-id", connection.remote_app_id]
    return [SSH, *ssh_options(target), target.destination, *command]


def forward_argv(connection: Connection, local_path: str, remote_path: str) -> list[str]:
    """§3.2's forward: ``ssh -o ExitOnForwardFailure=yes … -N -L
    <local>:<remote> TARGET``."""
    target = _target(connection)
    return [
        SSH,
        "-o", "ExitOnForwardFailure=yes",
        *ssh_options(target),
        "-N",
        "-L", f"{local_path}:{remote_path}",
        target.destination,
    ]  # fmt: skip


# ---- the local socket ----------------------------------------------------------------


def remote_dir() -> str:
    """``<runtime dir>/collins/remote``: the local sockets of every
    forward, in the per-user runtime directory (`$XDG_RUNTIME_DIR`), else
    under Collins' state directory, as `api.server.runtime_dir` falls back;
    an intended path too long for a socket takes the same hashed stand-in."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        directory = os.path.join(os.environ["XDG_RUNTIME_DIR"], "collins", "remote")
    else:
        directory = os.path.join(sandboxplan.state_base(), "collins", "remote")
    longest = os.path.join(directory, "f" * 32 + ".sock")
    if len(longest.encode()) > api_server.SOCKET_PATH_MAX:
        directory = api_server._short_runtime_dir(directory)
    return directory


def local_socket_path(connection_id: str) -> str:
    return os.path.join(remote_dir(), f"{connection_id}.sock")


def ensure_remote_dir(directory: str) -> None:
    """Make *directory* 0700 (and keep it so)."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass


def unlink_socket(path: str) -> None:
    """Remove the socket at *path* before a forward binds it. Only a socket
    goes: anything else there is left and refused (OSError), since the path
    is under a directory this module owns and a stranger's file there is
    nothing to delete."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise OSError(f"{path} exists and is not a socket")
    os.unlink(path)


# ---- the words -------------------------------------------------------------------------


def last_line(data: bytes | str | None) -> str:
    """The last non-empty line of *data*, control characters dropped, cut
    to `DETAIL_MAX`: ssh's one-line reason, past any login banner."""
    if not data:
        return ""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    for line in reversed(text.splitlines()):
        clean = "".join(c for c in line if ord(c) >= 32 and c != "\x7f").strip()
        if clean:
            return clean[:DETAIL_MAX]
    return ""


def exit_text(connection: Connection, code: int | None, detail: str) -> str:
    """The banner's text for a forward that ended with *code* and *detail*
    (its stderr's last line)."""
    if not detail:
        # A child killed by a signal has no last line of its own (Popen
        # reports -N): say which signal; else the bare exit status.
        detail = f"signal {-code}" if isinstance(code, int) and code < 0 else f"exit {code}"
    args = {"target": connection.target, "code": code, "detail": detail}
    if code == SSH_EXIT_FAILED:
        return translate(MSG_FORWARD_ENDED, args)
    if code == 0:
        return translate(MSG_FORWARD_CLOSED, args)
    return translate(MSG_FORWARD_EXIT, args)


# ---- --print-socket ---------------------------------------------------------------------


@dataclass
class PrintSocketResult:
    ok: bool
    path: str = ""  # the remote socket's path when ok
    code: int | None = None  # ssh's exit status (None: did not run or timed out)
    detail: str = ""  # the last line of stderr
    text: str = ""  # the person's word when not ok
    batch_mode_hint: bool = False  # exit 255: say what BatchMode means


def _valid_socket_path(line: str) -> bool:
    return bool(line) and line.startswith("/") and len(line) <= 4096 and "\x00" not in line


def print_socket(
    connection: Connection,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout: float = PRINT_SOCKET_TIMEOUT_S,
) -> PrintSocketResult:
    """Run ``ssh TARGET collins-service --print-socket`` (bounded) and read
    the socket's path off its last line of output. Never raises: every
    failure is a result with `text`."""
    argv = print_socket_argv(connection)
    target = connection.target
    try:
        completed = run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return PrintSocketResult(ok=False, text=translate(MSG_NO_SSH))
    except subprocess.TimeoutExpired as exc:
        detail = last_line(exc.stderr)
        text = translate(MSG_TIMEOUT, {"target": target, "seconds": int(timeout)})
        return PrintSocketResult(ok=False, detail=detail, text=text)
    except OSError as exc:
        return PrintSocketResult(ok=False, text=str(exc))
    detail = last_line(completed.stderr)
    code = completed.returncode
    if code == SSH_EXIT_FAILED:
        text = translate(MSG_CONNECT_FAILED, {"target": target, "detail": detail or f"exit {code}"})
        return PrintSocketResult(ok=False, code=code, detail=detail, text=text, batch_mode_hint=True)
    if code != 0:
        text = translate(MSG_REMOTE_FAILED, {"target": target, "code": code, "detail": detail or "-"})
        return PrintSocketResult(ok=False, code=code, detail=detail, text=text)
    path = last_line(completed.stdout)
    if not _valid_socket_path(path):
        return PrintSocketResult(ok=False, code=code, detail=detail,
                                 text=translate(MSG_BAD_PATH, {"target": target}))
    return PrintSocketResult(ok=True, path=path, code=code, detail=detail)


# ---- the forward child ------------------------------------------------------------------


class Forward:
    """One ``ssh -N -L`` child. `start` unlinks the local socket and spawns
    it; a thread reads its stderr (the last line kept) and reports its exit
    through *on_exit(forward, code, detail)*, from that thread. `stop` ends
    it (and reports nothing: a stop is asked for)."""

    def __init__(
        self,
        connection: Connection,
        remote_path: str,
        *,
        local_path: str | None = None,
        on_exit: Callable[[Forward, int | None, str], None] | None = None,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.connection = connection
        self.remote_path = remote_path
        self.local_path = local_path or local_socket_path(connection.id)
        self.on_exit = on_exit
        self._popen = popen
        self.proc: subprocess.Popen | None = None
        self.detail = ""
        self.stopped = False
        self._reader: threading.Thread | None = None

    @property
    def argv(self) -> list[str]:
        return forward_argv(self.connection, self.local_path, self.remote_path)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        """Raises OSError (a non-socket at the local path, no `ssh`)."""
        ensure_remote_dir(os.path.dirname(self.local_path))
        unlink_socket(self.local_path)
        self.proc = self._popen(
            self.argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        log.info("ssh forward to %s started (pid %s): %s -> %s",
                 self.connection.target, self.proc.pid, self.local_path, self.remote_path)
        self._reader = threading.Thread(target=self._read, name="collins-ssh-forward", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        proc = self.proc
        assert proc is not None
        try:
            if proc.stderr is not None:
                while True:
                    line = proc.stderr.readline(4096)
                    if not line:
                        break
                    text = last_line(line)
                    if text:
                        self.detail = text
                        log.info("ssh forward to %s: %s", self.connection.target, text)
        except (OSError, ValueError):
            pass
        code = proc.wait()
        if self.stopped:
            return
        log.warning("ssh forward to %s exited %s: %s", self.connection.target, code, self.detail)
        if self.on_exit is not None:
            try:
                self.on_exit(self, code, self.detail)
            except Exception:
                log.exception("sshforward: on_exit failed")

    def stop(self) -> None:
        """End the child quietly (no `on_exit`): a switch, a quit, a restart
        of the forward."""
        self.stopped = True
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(STOP_WAIT_S)
        except OSError:
            pass


# ---- the manager's transport ------------------------------------------------------------


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
        make_forward: Callable[..., Forward] = Forward,
        probe: Callable[[str], str] = api_server.probe_socket,
    ) -> None:
        self.connection = connection
        self.local_path = local_socket_path(connection.id)
        self._on_lost = on_lost
        self._land = land
        self._print_socket = print_socket
        self._make_forward = make_forward
        self._probe = probe
        self.forward: Forward | None = None
        self.remote_path = ""
        self._restart_wanted = False
        self.stopped = False

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
        if self.stopped:
            raise ForwardError("stopped")
        self._restart_wanted = False
        if self.forward is not None:
            self.forward.stop()
            self.forward = None
        result = self._print_socket(self.connection)
        if not result.ok:
            raise ForwardError(result.text)
        self.remote_path = result.path
        forward = self._make_forward(
            self.connection, result.path, local_path=self.local_path, on_exit=self._exited
        )
        try:
            forward.start()
        except OSError as exc:
            raise ForwardError(str(exc)) from exc
        self.forward = forward
        return STARTED_HOW

    def _exited(self, forward: Forward, code: int | None, detail: str) -> None:
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
        self.stopped = True
        if self.forward is not None:
            self.forward.stop()
            self.forward = None
        try:
            unlink_socket(self.local_path)
        except OSError:
            pass
