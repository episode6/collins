# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# New in the ghackett fork of agent-session-manager. Unlike the rest of
# Collins (GPL-3.0-or-later), this module is MIT-licensed, like the published
# API specification (docs/api/) whose "Connecting over ssh" section it
# implements, so that another development environment can vendor it to reach
# collins-service on another machine. Keep it standard-library only and free
# of user-facing text for the same reason.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""Reach a `collins-service` on another machine: its Unix socket, here.

Two ssh runs make a remote connection:

1. ``ssh TARGET collins-service --print-socket`` (`print_socket`): starts
   the service on the far side when none answers and prints its socket's
   path, bounded by `PRINT_SOCKET_TIMEOUT_S`. A failure is a `failure` code
   on the result (`CONNECT_FAILED`, `NOT_ON_PATH`, ...) with the last line
   of ssh's stderr in `detail`; the words shown to a person are the
   caller's.
2. ``ssh -N -L <local>.sock:<remote api.sock> TARGET`` (`Forward`): the
   child that holds the forward for the connection's life. Its exit is the
   signal to reconnect.

::

    target = parse_target("me@build.example.net:2222")
    result = print_socket(target)
    if result.ok:
        forward = Forward(target, result.path, local_path, on_exit=reconnect)
        forward.start()
        # ...connect to local_path and say hello...

*local_path* is yours to choose, in a 0700 directory of your own: whoever
can open that socket acts as the service's user on the far side.

Every run has ``BatchMode=yes`` (nothing can answer a password or
passphrase prompt: a key that needs one must be in the agent),
``ServerAliveInterval=15`` with ``ServerAliveCountMax=3`` (ssh's own
liveness check, 45 s) and ``ControlMaster=no`` / ``ControlPath=none``, so
the forward is a connection of its own: under a user's ControlMaster the
forward would live in the mux master, killing this child would end nothing,
and the ServerAlive options would be the master's.

A server that is not `collins-service` documents its own discovery command:
pass it as *command* to `print_socket`. It must print the socket's path as the
last line of stdout and exit 0, or exit non-zero.

A pid file beside each forward's socket names the ssh child, this client
and the socket, so a forward left by a client that crashed is ended before
the socket is bound again (`end_stale_forward`) and by a sweep at the next
launch (`sweep_orphans`). The checks on a recorded pid are POSIX: `os.kill`
with signal 0 for "is it there", ``/proc/<pid>/cmdline`` where that exists and
``ps -o args=`` elsewhere for "is it still that forward". Whatever those
answer that cannot be read as the forward's own command line is "not ours",
and nothing is signalled.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import shlex
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

SSH = "ssh"
PRINT_SOCKET_TIMEOUT_S = 10.0
SERVER_ALIVE_INTERVAL_S = 15
SERVER_ALIVE_COUNT_MAX = 3
SSH_EXIT_FAILED = 255  # ssh's own failure: no connection, no key, no host key
SHELL_EXIT_NOT_FOUND = 127  # the remote shell found no collins-service
# How much of the child's stderr is kept: the last line, this long.
DETAIL_MAX = 240
STOP_WAIT_S = 2.0
PID_FILE_MAX = 4096
PROC_DIR = "/proc"  # where a process's command line can be read, on a system that has one
PS = "ps"  # where it has not
PS_TIMEOUT_S = 2.0

# Why a `print_socket` failed (`PrintSocketResult.failure`).
NO_SSH = "no-ssh"  # no ssh program on this machine
TIMED_OUT = "timed-out"  # no answer within the timeout
OS_ERROR = "os-error"  # ssh could not be run; `detail` says why
CONNECT_FAILED = "connect-failed"  # ssh exit 255: no route, a refused key, an unknown host key
REMOTE_FAILED = "remote-failed"  # the remote command failed; `code` is its exit
NOT_ON_PATH = "not-on-path"  # exit 127, and the remote shell named the command's program
NO_SHELL = "no-shell"  # exit 127 otherwise: the login shell itself is missing
BAD_PATH = "bad-path"  # exit 0 and a line that is no socket path
HANDOVER = "handover"  # exit 0 and nothing printed: a startup file took the shell over

# The remote command, run through the far machine's login shell: `ssh host
# cmd` gets a non-login PATH that misses `~/.local/bin`, where a pipx install
# puts collins-service. sshd hands the string to the user's own shell, so the
# outer layer is POSIX `sh -c '…'` with single quotes only (every shell family
# keeps them literal: tcsh, nushell and xonsh included). Inside, for a
# POSIX-family shell or fish, the command runs as `$SHELL -lc` first — the
# login profiles (.profile, .bash_profile, .zprofile, config.fish) set the
# PATH for most — and only when that exits 127 (not found) as `$SHELL -lic`,
# which reads the interactive rc too (pipx's `ensurepath` writes ~/.bashrc,
# which Debian's default guards against non-interactive shells). Never `-lic`
# first: an interactive rc may hand the shell on (`exec zsh`, `exec fish`,
# auto-tmux where chsh is refused) before the command runs — exit 0, no path —
# and `-lc` never reads it. A `-lic` that still prints no path is reported as
# that hand-over (`HANDOVER`). Any other login shell gets `sh -lc`. The command
# sits inside the inner double quotes: the app id is validated by
# `print_socket_argv`, and a caller's command reaches the login shell as it was
# written, with `remote_command` spelling out what the two outer layers would
# read. No `'` beyond the outer pair and no `!` anywhere (csh history inside
# quotes), whatever the command holds.
REMOTE_COMMAND = "collins-service --print-socket"
REMOTE_SHELLS = "bash|zsh|dash|sh|ksh|mksh|fish"
COMMAND_MAX = 512  # the longest discovery command taken
# What the outer layers would read: the double quotes' own specials, the outer
# single quote and csh's history mark. The rest of a command is left to the
# login shell it is meant for.
_WRAPPER_SPECIALS = "\\\"$`'!"

# What the remote may print as its socket's path: ssh expands `${VAR}` and
# `%`-tokens of a LocalForward connect path on the client and sends the result
# to the far machine, so whatever prints the last line of `--print-socket`
# would choose which of this machine's variables cross. Letters, digits, dot,
# underscore, dash, slash and the `@ + , =` an SSSD/AD home holds
# (`/home/jo.doe@corp.example`), absolute, within the kernel's sun_path bound
# on the far side; ssh expands only `%` and `${}` and parses `:` and `[]` for
# -L, so those stay out.
SOCKET_PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+,=-]{1,107}$")

# A target's grammar: a user name, a host name (or an IPv4 address; IPv6 would
# need brackets and is not taken) and a port. No character that ssh, a shell
# or a path could read as anything but itself: letters, digits, dot, dash,
# underscore. A target starting with "-" would be read by ssh as an option;
# the grammar cannot produce one.
TARGET_MAX = 253 + 1 + 64 + 1 + 5  # host + "@" + user + ":" + port
_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$")
_PORT_RE = re.compile(r"^[0-9]{1,5}$")
_APP_ID_RE = re.compile(r"[A-Za-z0-9._-]+")


# ---- the target -------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A parsed ssh destination: what the two runs hand ssh."""

    host: str
    user: str = ""
    port: int | None = None

    @property
    def destination(self) -> str:
        """``user@host`` (or ``host``), the destination argument; the port
        goes separately as ``-p``."""
        return f"{self.user}@{self.host}" if self.user else self.host


def parse_target(text: object) -> Target | None:
    """``[user@]host[:port]`` as a `Target`, or None when it is anything
    else (too long, a stray character, a port out of range, an option)."""
    if not isinstance(text, str) or not text or len(text) > TARGET_MAX:
        return None
    user = ""
    rest = text
    if "@" in rest:
        user, _, rest = rest.partition("@")
        if not _USER_RE.fullmatch(user):
            return None
    port: int | None = None
    if ":" in rest:
        rest, _, digits = rest.partition(":")
        if not _PORT_RE.fullmatch(digits):
            return None
        port = int(digits)
        if not 1 <= port <= 65535:
            return None
    if not _HOST_RE.fullmatch(rest) or rest.endswith(".") or ".." in rest:
        return None
    return Target(host=rest, user=user, port=port)


# ---- argv -------------------------------------------------------------------------------


def _through_the_wrapper(command: str) -> str:
    """*command* as it is written inside the wrapper's double quotes, so that
    the login shell receives exactly *command*. Each special is an octal
    `printf`, with no backslash pair and no quote of the outer kind: the outer
    layer is read by the user's own shell, and fish reads a doubled backslash
    even inside single quotes."""
    return "".join(f'$(printf "\\{ord(c):03o}")' if c in _WRAPPER_SPECIALS else c for c in command)


def remote_command(command: str) -> str:
    """The one argument after the destination: the POSIX wrapper around
    *command* (see `REMOTE_COMMAND`), which the login shell runs as
    ``exec <command>``: one program and its arguments."""
    command = _through_the_wrapper(command)
    return (
        "sh -c 'case \"${SHELL##*/}\" in "
        f'{REMOTE_SHELLS}) "$SHELL" -lc "exec {command}"; rc=$?; '
        f'if [ "$rc" -eq {SHELL_EXIT_NOT_FOUND} ]; then exec "$SHELL" -lic "exec {command}"; fi; '
        'exit "$rc";; '
        f'*) exec sh -lc "exec {command}";; esac\''
    )


def ssh_options(target: Target) -> list[str]:
    """The options both runs share (see the module docstring), the port when
    the target names one."""
    # RemoteCommand=none and RequestTTY=no for the same reason as
    # ControlMaster=no: a Host block's RemoteCommand makes `ssh host cmd`
    # exit 255 ("Cannot execute command-line and remote command") and a
    # `RequestTTY force` would give the print-socket run a tty; the user's
    # config must not change what these two runs are.
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


def valid_command(command: object) -> bool:
    """A discovery command that may be handed to `print_socket_argv`: not
    blank, at most `COMMAND_MAX` characters, one line, no control character."""
    if not isinstance(command, str) or not command.strip() or len(command) > COMMAND_MAX:
        return False
    return not any(ord(c) < 32 or c == "\x7f" for c in command)


def command_program(command: str) -> str:
    """The name of the program *command* runs, as a shell's "not found" would
    say it: the first word's last path component. Empty when there is none."""
    try:
        words = shlex.split(command)
    except ValueError:  # an unbalanced quote: the far shell will say so itself
        words = command.split()
    return os.path.basename(words[0]) if words else ""


def print_socket_argv(target: Target, app_id: str = "", command: str = REMOTE_COMMAND) -> list[str]:
    """``ssh … TARGET <command> [--app-id ID]``, *command* being
    ``collins-service --print-socket`` unless the server documents another,
    the app id for a service that does not run under the default one. Raises
    ValueError on a command `valid_command` refuses and on an app id a shell
    could read."""
    if not valid_command(command):
        raise ValueError(f"not a discovery command: {command!r}")
    if app_id:
        if not _APP_ID_RE.fullmatch(app_id):
            raise ValueError(f"an app id a shell could read: {app_id!r}")
        command = f"{command} --app-id {app_id}"
    return [SSH, *ssh_options(target), target.destination, remote_command(command)]


def valid_socket_path(line: str) -> bool:
    """`SOCKET_PATH_RE`: what may go on either side of the colon of -L (see
    the constant: ssh expands `${VAR}` and `%` tokens there, on this side)."""
    return bool(SOCKET_PATH_RE.fullmatch(line))


def forward_argv(target: Target, local_path: str, remote_path: str) -> list[str]:
    """``ssh -o ExitOnForwardFailure=yes … -N -L <local>:<remote> TARGET``.
    Raises ValueError on a path ssh would expand or misparse: both halves of
    -L are expanded by ssh on this side (`${VAR}`, `%u`)."""
    for path in (local_path, remote_path):
        if not valid_socket_path(path):
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


def ensure_private_dir(directory: str) -> None:
    """Make *directory* 0700 (and keep it so)."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass


def unlink_socket(path: str) -> None:
    """Remove the socket at *path* before a forward binds it. Only a socket
    goes: anything else there is left and refused (OSError), since a
    stranger's file there is nothing to delete."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise OSError(f"{path} exists and is not a socket")
    os.unlink(path)


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


# ---- --print-socket ---------------------------------------------------------------------


@dataclass
class PrintSocketResult:
    ok: bool
    path: str = ""  # the remote socket's path when ok
    code: int | None = None  # ssh's exit status (None: did not run or timed out)
    detail: str = ""  # the last line of stderr, or why ssh could not run
    failure: str = ""  # why not ok: `NO_SSH`, `CONNECT_FAILED`, ...


def print_socket(
    target: Target,
    app_id: str = "",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    timeout: float = PRINT_SOCKET_TIMEOUT_S,
    command: str = REMOTE_COMMAND,
) -> PrintSocketResult:
    """Run ``ssh TARGET collins-service --print-socket``, or *command* in its
    place (bounded), and read the socket's path off its last line of output.
    Every failure of the run is a result with `failure`; only an unusable
    *app_id* or *command* raises (ValueError, `print_socket_argv`)."""
    argv = print_socket_argv(target, app_id, command)
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
        return PrintSocketResult(ok=False, failure=NO_SSH)
    except subprocess.TimeoutExpired as exc:
        return PrintSocketResult(ok=False, detail=last_line(exc.stderr), failure=TIMED_OUT)
    except OSError as exc:
        return PrintSocketResult(ok=False, detail=str(exc), failure=OS_ERROR)
    detail = last_line(completed.stderr)
    code = completed.returncode
    if code == SSH_EXIT_FAILED:
        return PrintSocketResult(ok=False, code=code, detail=detail, failure=CONNECT_FAILED)
    if code != 0:
        failure = REMOTE_FAILED
        if code == SHELL_EXIT_NOT_FOUND:
            # 127 is "not found": the command's program when the shell says
            # so, else the login shell itself ($SHELL naming one not
            # installed: both rounds fail the same way).
            program = command_program(command)
            failure = NOT_ON_PATH if program and program in detail else NO_SHELL
        return PrintSocketResult(ok=False, code=code, detail=detail, failure=failure)
    path = last_line(completed.stdout)
    if not valid_socket_path(path):
        # Exit 0 and nothing printed: the wrapper's -lic round ran the
        # interactive rc and the command never ran.
        failure = BAD_PATH if path else HANDOVER
        return PrintSocketResult(ok=False, code=code, detail=detail, failure=failure)
    return PrintSocketResult(ok=True, path=path, code=code, detail=detail)


# ---- the pid file beside each socket --------------------------------------------------


def pid_path(local_path: str) -> str:
    """``<name>.pid`` beside the socket ``<name>.sock``."""
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
        log.warning("sshremote: could not write %s: %s", pid_path(local_path), exc)


def read_pid_file(path: str) -> dict | None:
    """The record at *path*, or None when it is not one of ours: a bounded
    read, integer pids, a socket path of the allowed shape."""
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
    """Whether a process has *pid*: signal 0, on any POSIX system. A process
    of another user is there (`PermissionError`), and so is a zombie its
    parent has not reaped yet (`is_our_forward` tells that one apart)."""
    if pid <= 0:  # to `kill` these name a process group, not a process
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, OverflowError):
        return False
    return True


def _ps_words(pid: int) -> list[bytes] | None:
    """*pid*'s command line as ``ps -o args=`` prints it, split on whitespace,
    or None when the answer is anything but one bounded line from a `ps` that
    succeeded. `ps` cuts its line to a width that is undefined on a pipe (80
    columns is common, and a socket's path runs past that) and a terminal's
    when it finds one on any stream: `COLUMNS` is POSIX's override, and no
    stream of the run is a terminal."""
    try:
        completed = subprocess.run(
            [PS, "-o", "args=", "-p", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={**os.environ, "COLUMNS": str(PID_FILE_MAX)},
            timeout=PS_TIMEOUT_S,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or len(completed.stdout) > PID_FILE_MAX:
        return None
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return lines[0].split() if len(lines) == 1 else None


def _command_words(pid: int) -> list[bytes] | None:
    """*pid*'s command line: ``/proc/<pid>/cmdline`` when that exists, else
    `_ps_words`. None when it cannot be read."""
    try:
        with open(f"{PROC_DIR}/{pid}/cmdline", "rb") as fh:
            return fh.read(PID_FILE_MAX).split(b"\0")
    except FileNotFoundError:
        return _ps_words(pid)
    except OSError:
        return None


def is_our_forward(pid: int, local_path: str) -> bool:
    """Whether *pid* still runs an ``ssh … -N … -L <local_path>:…``: the pid
    may have been reused since the file was written, so nothing is signalled
    unless the command line is the forward's own. The program must be the
    first word and the socket the word after ``-L``, up to its colon; anything
    less, anything unreadable, a zombie's empty or bracketed line: not ours."""
    if pid <= 0:
        return False
    words = _command_words(pid)
    if not words or os.path.basename(words[0]) != b"ssh" or b"-N" not in words:
        return False
    want = os.fsencode(local_path + ":")
    return any(
        flag == b"-L" and spec.startswith(want)
        for flag, spec in zip(words, words[1:], strict=False)
    )


def _signal_forward(pid: int, local_path: str, sig: int) -> bool:
    """Signal *pid* only while its command line is still that forward's:
    with `pidfd_open` the check and the signal name one process (a pid
    reused in between is never hit); without it (an older kernel, another
    system) the check precedes a plain `os.kill`."""
    pidfd_open = getattr(os, "pidfd_open", None)
    send = getattr(signal, "pidfd_send_signal", None)
    if pidfd_open is not None and send is not None:
        fd = None
        try:
            fd = pidfd_open(pid)
        except OSError as exc:
            # A kernel or a seccomp profile without the syscall (ENOSYS,
            # EPERM, EINVAL): the check-then-kill road below. ESRCH, or
            # anything else: gone.
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
    another client's live forward. Returns the pid ended, None when there
    was nothing to end. Called before a new forward binds the socket, and
    by `sweep_orphans`."""
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
        log.warning("sshremote: %s names a living client (pid %d); its forward is left alone",
                    path, record["client"])
        return None
    try:
        os.unlink(path)
    except OSError:
        pass
    pid = record["ssh"]
    if not _signal_forward(pid, local_path, signal.SIGTERM):
        return None
    log.info("sshremote: ending the forward left at %s (ssh pid %d)", local_path, pid)
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline:
        if not pid_alive(pid) or not is_our_forward(pid, local_path):
            return pid
        time.sleep(0.05)
    _signal_forward(pid, local_path, signal.SIGKILL)
    return pid


def sweep_orphans(directory: str) -> list[int]:
    """At launch: every pid file in *directory* whose *client* no longer
    lives names a forward left by a client that crashed — end it. A file
    whose client still lives is another client's on this machine and is
    left alone; a record naming a socket that is not this file's own
    (`<name>.sock` beside it) is ignored. Returns the pids ended."""
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
            log.warning("sshremote: %s names a socket elsewhere; ignored", path)
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
        target: Target,
        remote_path: str,
        local_path: str,
        *,
        on_exit: Callable[[Forward, int | None, str], None] | None = None,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self.target = target
        self.remote_path = remote_path
        self.local_path = local_path
        self.on_exit = on_exit
        self._popen = popen
        self.proc: subprocess.Popen | None = None
        self.detail = ""
        self.stopped = False
        self._reader: threading.Thread | None = None

    @property
    def argv(self) -> list[str]:
        return forward_argv(self.target, self.local_path, self.remote_path)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        """Raises OSError (a non-socket at the local path, no `ssh`). A
        forward an earlier client left on this socket (its pid file) is
        ended first; the new child's pid file is written beside the socket."""
        ensure_private_dir(os.path.dirname(self.local_path))
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
                 self.target.destination, self.proc.pid, self.local_path, self.remote_path)
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
                        log.info("ssh forward to %s: %s", self.target.destination, text)
        except (OSError, ValueError):
            pass
        code = proc.wait()
        if self.stopped:
            return
        log.warning("ssh forward to %s exited %s: %s", self.target.destination, code, self.detail)
        if self.on_exit is not None:
            try:
                self.on_exit(self, code, self.detail)
            except Exception:
                log.exception("sshremote: on_exit failed")

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
