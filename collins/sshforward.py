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

import errno
import json
import logging
import os
import re
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import APP_ID, sandboxplan
from .api import server as api_server
from .connections import Connection, Target
from .i18n import N_, translate

log = logging.getLogger(__name__)

SSH = "ssh"
PRINT_SOCKET_TIMEOUT_S = 10.0
SERVER_ALIVE_INTERVAL_S = 15
SERVER_ALIVE_COUNT_MAX = 3
SSH_EXIT_FAILED = 255  # ssh's own failure: no connection, no key, no host key
SHELL_EXIT_NOT_FOUND = 127  # the remote shell found no collins-service
# The remote command, run through the box's login shell (ruled 2026-10-29,
# the review of PR 623, Q3, amended by its verification): `ssh host cmd`
# gets a non-login PATH that misses `~/.local/bin`, where a pipx install
# puts collins-service, while the guide (rightly) says "the login shell's
# PATH". sshd hands the string to the user's own shell, so the outer layer
# is POSIX `sh -c '…'` with single quotes only (every shell family keeps
# them literal: tcsh, nushell and xonsh included). Inside, for a
# POSIX-family shell or fish, the command runs as `$SHELL -lc` first — the
# login profiles (.profile, .bash_profile, .zprofile, config.fish) set the
# PATH for most — and only when that exits 127 (not found) as `$SHELL
# -lic`, which reads the interactive rc too (pipx's `ensurepath` writes
# ~/.bashrc, which Debian's default guards against non-interactive shells).
# Never `-lic` first (the second verification of PR 623, V1): an
# interactive rc may hand the shell on (`exec zsh`, `exec fish`, auto-tmux
# where chsh is refused) before the command runs — exit 0, no path — and
# `-lc` never reads it. A `-lic` that still prints no path is reported as
# that hand-over (`MSG_HANDOVER`). Any other login shell gets `sh -lc`.
# One fixed string; the app id goes inside the inner double quotes,
# validated to [A-Za-z0-9._-] by `connections.valid_app_id` and checked
# again below, so no shell reads anything of a person's. No `!` anywhere
# (csh history inside quotes).
REMOTE_COMMAND = "collins-service --print-socket"
REMOTE_SHELLS = "bash|zsh|dash|sh|ksh|mksh|fish"


def remote_command(command: str) -> str:
    """The one argument after the destination: the POSIX wrapper around
    *command* (see `REMOTE_COMMAND`)."""
    return (
        "sh -c 'case \"${SHELL##*/}\" in "
        f'{REMOTE_SHELLS}) "$SHELL" -lc "exec {command}"; rc=$?; '
        f'if [ "$rc" -eq {SHELL_EXIT_NOT_FOUND} ]; then exec "$SHELL" -lic "exec {command}"; fi; '
        'exit "$rc";; '
        f'*) exec sh -lc "exec {command}";; esac\''
    )
# How much of the child's stderr is kept: the last line, this long.
DETAIL_MAX = 240
STOP_WAIT_S = 2.0
STARTED_HOW = "forward"  # the manager's `started_how` for a remote start
# What the remote may print as its socket's path (M3 of PR 623's review):
# ssh expands `${VAR}` and `%`-tokens of a LocalForward connect path on the
# client and sends the result to the box, so whatever prints the last line
# of `--print-socket` would choose which of this device's variables cross.
# Letters, digits, dot, underscore, dash, slash and the `@ + , =` an
# SSSD/AD home holds (`/home/jo.doe@corp.example`), absolute, within the
# kernel's sun_path bound on the far side; ssh expands only `%` and `${}`
# and parses `:` and `[]` for -L, so those stay out (the verification's N4).
SOCKET_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+,=-]{1,107}$")
# The pid file beside each forward's socket (ruled 2026-10-29, the review
# of PR 623, Q2): the ssh child's pid, this client's pid and the socket, so
# a forward left by a client that crashed is ended before the socket is
# bound again (`end_stale_forward`) and by the next launch on this device
# (`sweep_orphans`: a client pid that no longer lives). No PR_SET_PDEATHSIG:
# a `preexec_fn` in a client with threads may deadlock between fork and
# exec, and the signal is per parent *thread* (the reconnect's worker).
PID_FILE_MAX = 4096

# The words the dialog and the banner show (msgids: translated with
# `i18n.translate` here, since the detail is ssh's own text and may hold
# braces).
MSG_NO_SSH = N_("ssh is not installed on this computer")
MSG_CONNECT_FAILED = N_("ssh could not connect to {target}: {detail}")
MSG_REMOTE_FAILED = N_("collins-service failed on {target} (exit {code}): {detail}")
MSG_TIMEOUT = N_("no answer from {target} within {seconds} s")
MSG_BAD_PATH = N_("collins-service on {target} printed no socket path")
MSG_NOT_ON_PATH = N_("collins-service is not on the login shell's PATH on {target}")
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


# ---- argv -------------------------------------------------------------------------------


def ssh_options(target: Target) -> list[str]:
    """The options every run of this module shares (see the module
    docstring), the port when the target names one."""
    # RemoteCommand=none and RequestTTY=no for the same reason as
    # ControlMaster=no: a Host block's RemoteCommand makes `ssh host cmd`
    # exit 255 ("Cannot execute command-line and remote command") and a
    # `RequestTTY force` would give the print-socket run a tty; the user's
    # config must not change what these two runs are (PR 623's review).
    argv = [
        "-o", "BatchMode=yes",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "RemoteCommand=none",
        "-o", "RequestTTY=no",
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
    command = REMOTE_COMMAND
    if connection.remote_app_id:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", connection.remote_app_id):
            raise ValueError(f"connection {connection.id!r} has an app id a shell could read")
        command = f"{command} --app-id {connection.remote_app_id}"
    return [SSH, *ssh_options(target), target.destination, remote_command(command)]


def forward_argv(connection: Connection, local_path: str, remote_path: str) -> list[str]:
    """§3.2's forward: ``ssh -o ExitOnForwardFailure=yes … -N -L
    <local>:<remote> TARGET``."""
    target = _target(connection)
    # Both halves of -L are expanded by ssh on this side (`${VAR}`, `%u`):
    # the remote half was checked by `print_socket`, and the local half is
    # this module's own text (the runtime dir and a 32-hex id); refuse
    # anything else all the same.
    for path in (local_path, remote_path):
        if not SOCKET_PATH_RE.fullmatch(path):
            raise ValueError(f"not a socket path ssh may be handed: {path!r}")
    return [
        SSH,
        "-o", "ExitOnForwardFailure=yes",
        *ssh_options(target),
        "-N",
        "-L", f"{local_path}:{remote_path}",
        target.destination,
    ]  # fmt: skip


# ---- the local socket ----------------------------------------------------------------


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
    """`SOCKET_PATH_RE`: what may go after the colon of -L (see the
    constant: ssh expands `${VAR}` and `%` tokens there, on this side)."""
    return bool(SOCKET_PATH_RE.fullmatch(line))


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
        if code == SHELL_EXIT_NOT_FOUND:
            # 127 is "not found": collins-service when the shell says so,
            # else the login shell itself ($SHELL naming one not installed:
            # both rounds fail the same way).
            hint = MSG_NOT_ON_PATH if "collins-service" in detail else MSG_NO_SHELL
            text = f"{text}\n{translate(hint, {'target': target})}"
        return PrintSocketResult(ok=False, code=code, detail=detail, text=text)
    path = last_line(completed.stdout)
    if not _valid_socket_path(path):
        text = translate(MSG_BAD_PATH, {"target": target})
        if not path:
            # Exit 0 and nothing printed: the wrapper's -lic round ran the
            # interactive rc and the command never ran (V1).
            text = f"{text}\n{translate(MSG_HANDOVER, {'target': target})}"
        return PrintSocketResult(ok=False, code=code, detail=detail, text=text)
    return PrintSocketResult(ok=True, path=path, code=code, detail=detail)


# ---- the pid file beside each socket --------------------------------------------------


def pid_path(local_path: str) -> str:
    """``<runtime dir>/collins/remote/<connection id>.pid`` for that socket."""
    return local_path[: -len(".sock")] + ".pid" if local_path.endswith(".sock") else local_path + ".pid"


def write_pid_file(local_path: str, ssh_pid: int) -> None:
    """Record the forward: its ssh pid, this client's pid and the socket,
    0600. Fails soft (the sweep then has nothing to go on)."""
    record = {"ssh": int(ssh_pid), "client": os.getpid(), "socket": local_path}
    try:
        fd = os.open(pid_path(local_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(record))
    except OSError as exc:
        log.warning("sshforward: could not write %s: %s", pid_path(local_path), exc)


def read_pid_file(path: str) -> dict | None:
    """The record at *path*, or None when it is not one of ours (rule 5:
    a bounded read, integer pids, a socket path of the allowed shape)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(PID_FILE_MAX + 1)
    except OSError:
        return None
    if len(raw) > PID_FILE_MAX:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    ssh_pid, client, sock = record.get("ssh"), record.get("client"), record.get("socket")
    if not (isinstance(ssh_pid, int) and 1 < ssh_pid < 2**22):
        return None
    if not (isinstance(client, int) and 1 < client < 2**22):
        return None
    if not isinstance(sock, str) or not SOCKET_PATH_RE.fullmatch(sock):
        return None
    return {"ssh": ssh_pid, "client": client, "socket": sock}


def pid_alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


def is_our_forward(pid: int, local_path: str) -> bool:
    """Whether *pid* still runs an ``ssh … -L <local_path>:…``: the pid may
    have been reused since the file was written, so nothing is signalled
    unless the command line is the forward's own."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            argv = fh.read(PID_FILE_MAX).split(b"\0")
    except OSError:
        return False
    if not argv or os.path.basename(argv[0]) != b"ssh":
        return False
    want = (local_path + ":").encode()
    return any(arg.startswith(want) for arg in argv)


def _signal_forward(pid: int, local_path: str, sig: int) -> bool:
    """Signal *pid* only while its command line is still that forward's:
    with `pidfd_open` the check and the signal name one process (a pid
    reused in between is never hit: the verification's N7); without it
    (an older kernel) the check precedes a plain `os.kill`."""
    pidfd_open = getattr(os, "pidfd_open", None)
    send = getattr(signal, "pidfd_send_signal", None)
    if pidfd_open is not None and send is not None:
        fd = None
        try:
            fd = pidfd_open(pid)
        except OSError as exc:
            # A kernel or a seccomp profile without the syscall (ENOSYS,
            # EPERM, EINVAL): the check-then-kill road below (the second
            # verification of PR 623, V2). ESRCH, or anything else: gone.
            if exc.errno not in (errno.ENOSYS, errno.EPERM, errno.EINVAL):
                return False
        if fd is not None:
            try:
                if not is_our_forward(pid, local_path):
                    return False
                send(fd, sig)
                return True
            except OSError:
                return False
            finally:
                os.close(fd)
    if not is_our_forward(pid, local_path):
        return False
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    return True


def end_stale_forward(local_path: str) -> int | None:
    """End the ssh child a pid file beside *local_path* names, when it is
    still that forward (its command line says so) and its client is gone
    or is this process, and remove the file. A record whose client still
    lives and is another process is left as it is, file included: that is
    another client's live forward (the verification of PR 623, N1), and
    with the path scoped by app id only a reused client pid can read so.
    Returns the pid ended, None when there was nothing to end. Called
    before a new forward binds the socket, and by `sweep_orphans`."""
    path = pid_path(local_path)
    record = read_pid_file(path)
    if record is None:
        try:
            os.unlink(path)  # garbage, or nothing
        except OSError:
            pass
        return None
    if record["socket"] != local_path:
        return None
    if record["client"] != os.getpid() and pid_alive(record["client"]):
        log.warning("sshforward: %s names a living client (pid %d); its forward is left alone",
                    path, record["client"])
        return None
    try:
        os.unlink(path)
    except OSError:
        pass
    pid = record["ssh"]
    if not _signal_forward(pid, local_path, signal.SIGTERM):
        return None
    log.info("sshforward: ending the forward left at %s (ssh pid %d)", local_path, pid)
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline:
        if not pid_alive(pid) or not is_our_forward(pid, local_path):
            return pid
        time.sleep(0.05)
    _signal_forward(pid, local_path, signal.SIGKILL)
    return pid


def sweep_orphans(directory: str | None = None, app_id: str = APP_ID) -> list[int]:
    """At launch: every pid file of this app id whose *client* no longer
    lives names a forward left by a client that crashed — end it. A file
    whose client still lives is another client's on this device and is
    left alone; a record naming a socket that is not this file's own
    (`<name>.sock` beside it) is ignored (the verification's N5). Returns
    the pids ended."""
    directory = directory or remote_dir(app_id)
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    ended: list[int] = []
    for name in names:
        if not name.endswith(".pid"):
            continue
        path = os.path.join(directory, name)
        record = read_pid_file(path)
        if record is None:
            continue
        if record["socket"] != os.path.join(directory, name[: -len(".pid")] + ".sock"):
            log.warning("sshforward: %s names a socket elsewhere; ignored", path)
            continue
        if pid_alive(record["client"]):
            continue  # a living client's (this one's included): left alone
        pid = end_stale_forward(record["socket"])
        if pid is not None:
            ended.append(pid)
    return ended


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
        """Raises OSError (a non-socket at the local path, no `ssh`). A
        forward an earlier client left on this socket (its pid file) is
        ended first; the new child's pid file is written beside the socket."""
        ensure_remote_dir(os.path.dirname(self.local_path))
        end_stale_forward(self.local_path)
        unlink_socket(self.local_path)
        self.proc = self._popen(
            self.argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        write_pid_file(self.local_path, self.proc.pid)
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
        if proc is None:
            return
        try:
            os.unlink(pid_path(self.local_path))
        except OSError:
            pass
        if proc.poll() is not None:
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
        self.forward: Forward | None = None
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
        with self._lock:
            self.stopped = True
            forward, self.forward = self.forward, None
        if forward is not None:
            forward.stop()
        try:
            unlink_socket(self.local_path)
        except OSError:
            pass
