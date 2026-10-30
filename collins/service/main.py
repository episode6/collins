# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`collins-service`: the headless session service as its own process.

Split-service spec §3.10 and §3.20. The service is a plain Python program
on the GLib main loop: it builds the `ServiceCore` over its own
`AppState` (the state split's writer), starts the store, the tracker, the
sandbox host, the session tools and their MCP socket, listens on the API
socket (`api.server.ApiServer`), tells systemd it is ready and runs until
`SIGTERM` or `SIGINT` (or a client's `service.restart`), when it ends every
session the way *Stop sessions and quit* does (their ids recorded as
``resume_on_start``) and exits 0, so a unit with ``Restart=on-failure``
stays down and the client that wanted the restart brings it back.

    collins-service [--app-id ID] [--foreground]
    collins-service --print-socket     # start if needed, print the path
    collins-service --check            # what a headless box needs (F9)

**The session environment** (§3.10, D63): the process's environment overlaid
once at start by a login-shell capture in two rounds started at once and
waited to one 5 s deadline, ``$SHELL -lc 'env -0'`` (the login profile) and
``$SHELL -lic 'env -0'`` (the interactive rc too; ``stdin=DEVNULL``, parsed
by NUL, failing soft). A round counts only when its output holds a
``PATH``, so an interactive rc that hands the shell on (``exec zsh``,
auto-tmux), which empties the ``-lic`` round, costs only that round; the
interactive round's variables overlay the login round's. What it makes: a
headless box's shells find ``~/.local/bin/claude`` and the user's ``PATH``.
The capture fills in what the service's environment lacks and appends the
``PATH`` entries it lacks; it never overrides a variable the service was
started with (a check's ``COLLINS_*`` and ``XDG_*`` overrides, its shim
directory at the front of ``PATH``). ``--check`` prints a ``shell:`` line
saying which rounds answered.

A second ``collins-service`` for the same app id finds the live socket and
exits 0 after printing its path. ``sd_notify`` is a datagram to
``$NOTIFY_SOCKET`` (D32: no dependency). Nothing here imports GTK.

**The keeper** (§3.10 item 2, PR-3.6). Before anything is spawned the
service finds or starts the keeper (`service/keeper.py`): a connect to
``keeper.sock`` beside the API socket; failing that, ``systemctl --user
start collins-keeper.service`` for the default app id where ``systemctl``
exists, else ``python -m collins.service.keeper --socket <path>`` spawned
detached in a session of its own; then the connect again, bounded. Every
pty is forked by the keeper from then on, and at start the ptys the
keeper still holds from the last service are adopted
(`ServiceCore.adopt_ptys`), before the store scans and before the sandbox
host sweeps (§3.9's order: adopt, re-hold, re-mount, then sweep). With a
keeper the stop sequence ends no session: `ServiceCore.suspend` saves
every model and closes the service's masters, the children run on,
blocked on their output until the next service adopts them, and
``resume_on_start`` is left empty. Without one (no keeper could be
started, or ``COLLINS_KEEPER=none``) the stop sequence is §3.10 item 1's,
as before. A keeper lost mid-run is started again and handed every kept
master (`PtyServer._keeper_lost`).
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from gi.repository import GLib

from .. import APP_ID, __version__, i18n, providers
from ..api import server as api_server
from . import keeper as keeper_mod

log = logging.getLogger(__name__)

CAPTURE_TIMEOUT_S = 5.0
START_WAIT_S = 10.0
# How long the service waits for a keeper it started to listen.
KEEPER_WAIT_S = 5.0
KEEPER_UNIT = "collins-keeper.service"
# `COLLINS_KEEPER=none` (or 0, off): no keeper; the service forks its
# ptys itself and a stop ends every session (§3.10 item 1), as before
# PR-3.6. The unit suite's live services and a check that wants the old
# ending set it; the product never does.
KEEPER_OFF = frozenset({"none", "0", "off", "no"})


# ---- the keeper -----------------------------------------------------------------------


def keeper_wanted() -> bool:
    return (os.environ.get("COLLINS_KEEPER") or "").strip().lower() not in KEEPER_OFF


def start_keeper_process(app_id: str, path: str) -> str | None:
    """Bring a keeper up for *app_id* on *path*: the user unit where it
    applies (the default id, `systemctl` present), else a detached
    process of this interpreter. ``"unit"`` or ``"spawned"`` for what was
    started, None for nothing. A `Type=simple` unit's start exits 0 even
    when its `ExecStart` fails (V2-N2 of the verification of PR 629), so
    the caller waits for the keeper to answer and spawns one itself when
    the unit's does not."""
    if app_id == APP_ID and shutil.which("systemctl") and not os.environ.get("COLLINS_KEEPER"):
        try:
            result = subprocess.run(
                ["systemctl", "--user", "start", KEEPER_UNIT],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.info("systemctl --user start %s failed: %s", KEEPER_UNIT, exc)
        else:
            if result.returncode == 0:
                log.info("started %s", KEEPER_UNIT)
                return "unit"
            log.info("%s did not start (%s); spawning a keeper", KEEPER_UNIT, result.stderr.strip())
    return "spawned" if spawn_keeper_process(app_id, path) else None


def spawn_keeper_process(app_id: str, path: str) -> bool:
    """A detached `collins-keeper` of this interpreter for *app_id* on
    *path*; True when it was started."""
    env = dict(os.environ)
    env.pop("NOTIFY_SOCKET", None)
    env["COLLINS_APP_ID"] = app_id
    quiet = not os.environ.get("COLLINS_LOG")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "collins.service.keeper", "--app-id", app_id, "--socket", path],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
            start_new_session=True,  # outlives this service: a session of its own
            close_fds=True,
        )
    except OSError as exc:
        log.warning("could not spawn a keeper: %s", exc)
        return False
    return True


class KeeperMismatch(Exception):
    """A keeper of another protocol holds *held* ptys (PR-3.6's ruling on
    an older keeper): the service runs without a keeper and says so."""

    def __init__(self, protocol: object, held: int) -> None:
        super().__init__(f"the keeper speaks protocol {protocol} and holds {held} pty(s)")
        self.protocol = protocol
        self.held = held


def _probe_keeper(path: str) -> keeper_mod.KeeperClient | None:
    """One connect: the client, None when nothing answers, or
    `KeeperMismatch` when a keeper of another protocol answers. A keeper
    outside the window that holds nothing is stopped (SIGTERM, a bounded
    wait) so the one started next takes its place; one that holds ptys is
    left exactly as it is, neither replaced nor adopted from."""
    client = keeper_mod.KeeperClient(path)
    try:
        client.connect()
        return client
    except keeper_mod.KeeperError as exc:
        if not exc.protocol_mismatch:
            return None
        held = exc.reply.get("held")
        held = held if isinstance(held, int) and held > 0 else 0
        if held:
            raise KeeperMismatch(exc.reply.get("protocol"), held) from None
        pid = keeper_mod.read_pid(path)
        log.warning(
            "the keeper on %s speaks protocol %s and holds nothing; stopping it (pid %s)",
            path, exc.reply.get("protocol"), pid,
        )
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + KEEPER_WAIT_S
            while time.monotonic() < deadline and keeper_mod.pid_alive(pid):
                time.sleep(0.05)
        return None


def connect_keeper(path: str, wait_s: float = 0.0) -> keeper_mod.KeeperClient | None:
    """A connected client of the keeper at *path*, or None when none
    answers within *wait_s*; `KeeperMismatch` for one of another protocol
    that holds ptys."""
    deadline = time.monotonic() + wait_s
    while True:
        client = _probe_keeper(path)
        if client is not None:
            return client
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def find_or_start_keeper(app_id: str) -> keeper_mod.KeeperClient | None:
    """The keeper for *app_id*, connected: the one running, else one
    started here; None when none can be had (the service then forks its
    ptys itself, and a stop ends every session), or when it is off;
    `KeeperMismatch` when a keeper of another protocol holds ptys (the
    service runs without one, touches no row and reports it)."""
    if not keeper_wanted():
        return None
    path = api_server.keeper_socket_path(app_id)
    client = connect_keeper(path)
    if client is not None:
        return client
    how = start_keeper_process(app_id, path)
    if how is None:
        return None
    client = connect_keeper(path, wait_s=KEEPER_WAIT_S)
    if client is None and how == "unit":
        # The unit "started" and nothing answers (its ExecStart failed, a
        # removed venv): a keeper of this interpreter instead (V2-N2).
        log.warning("%s started but no keeper answered on %s; spawning one", KEEPER_UNIT, path)
        if spawn_keeper_process(app_id, path):
            client = connect_keeper(path, wait_s=KEEPER_WAIT_S)
    if client is None:
        log.warning("no keeper answered on %s within %.0f s; running without one", path, KEEPER_WAIT_S)
    return client


# ---- helpers ------------------------------------------------------------------------


def take_lock(app_id: str) -> int:
    """`flock` the service's lock file for this app id; the descriptor is
    held for the process's life. Raises OSError(EADDRINUSE) when another
    service holds it."""
    import fcntl

    path = api_server.lock_path(app_id)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise OSError(errno.EADDRINUSE, f"another service holds {path}") from exc
    return fd


@dataclass
class LoginCapture:
    """What the login-shell capture found (§3.10, D63): the *environment*
    (the login round's parse overlaid by the interactive round's) and which
    rounds counted, a round counting only when its output held a ``PATH``.
    *shell* is the shell the rounds ran, worked out once, so ``--check``
    names the one the capture used. *timed_out*: a round was killed at the
    deadline; *started*: at least one round's ``Popen`` succeeded; *error*:
    the first ``Popen`` failure's text; the three give ``--check`` its
    wording per cause."""

    environment: dict[str, str] = field(default_factory=dict)
    login: bool = False
    interactive: bool = False
    shell: str = ""
    timed_out: bool = False
    started: bool = False
    error: str = ""


def _parse_env0(stdout: bytes) -> dict[str, str]:
    """``env -0``'s NUL-separated ``NAME=value`` pairs; what is not a pair
    or not decodable is dropped. What an rc printed before ``env`` ran (a
    greeting ends in a newline, not a NUL) sits in front of the first entry:
    that entry is read again from after its last newline."""
    parsed: dict[str, str] = {}
    for index, entry in enumerate(stdout.split(b"\0")):
        if index == 0 and b"\n" in entry:
            if not entry.partition(b"=")[0].decode(errors="replace").isidentifier():
                entry = entry.rsplit(b"\n", 1)[-1]
        if b"=" not in entry:
            continue
        key, _, value = entry.partition(b"=")
        try:
            name = key.decode()
            text = value.decode()
        except UnicodeDecodeError:
            continue
        if name and name.isidentifier():
            parsed[name] = text
    return parsed


def capture_login_shell(
    shell: str | None = None, timeout: float = CAPTURE_TIMEOUT_S, env: dict[str, str] | None = None
) -> LoginCapture:
    """The environment the user's shell hands a terminal, in two rounds
    started at once and waited to one common deadline (D63): ``$SHELL -lc
    'env -0'`` (the login profile) and ``$SHELL -lic 'env -0'`` (that and the
    interactive rc: a ``PATH`` behind Debian's non-interactive guard). A round
    counts only when its parse holds ``PATH``: an rc that hands the shell on
    (``exec zsh``, auto-tmux) prints nothing, or exits 1, under ``-i``. The
    interactive round's variables overlay the login round's, ``PATH``
    included. Never raises; a round that fails, hands over or runs past the
    deadline (``killpg``ed) yields nothing. Each shell runs in a session of
    its own, so a kill reaps whatever it forked. *env* is what the shells
    start with (the process's when None)."""
    shell = shell or os.environ.get("SHELL") or "/bin/sh"
    deadline = time.monotonic() + timeout
    rounds: list[tuple[str, subprocess.Popen | None]] = []
    error = ""
    for flags in ("-lc", "-lic"):
        try:
            proc = subprocess.Popen(
                [shell, flags, "env -0"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # no controlling tty: a shell that asks gets EOF
                env=env,
            )
        except (OSError, ValueError) as exc:
            log.info("login-shell capture (%s) failed: %s", flags, exc)
            error = error or getattr(exc, "strerror", None) or str(exc)
            proc = None
        rounds.append((flags, proc))
    parsed: dict[str, dict[str, str]] = {}
    killed: list[str] = []

    def settle(flags: str, proc: subprocess.Popen) -> None:
        try:
            stdout, _ = proc.communicate(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            # Not a second `communicate()`: that reads to EOF, and a
            # grandchild the rc put in a session of its own (`setsid cmd &`)
            # is outside the killed group and holds the pipe, so it would
            # wait past the deadline for as long as the grandchild lives.
            # Nothing of the partial output is wanted.
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait()
            killed.append(flags)
            log.info("login-shell capture (%s) timed out after %.0f s", flags, timeout)
            return
        except Exception as exc:  # never raises: a round that breaks yields nothing
            log.info("login-shell capture (%s) failed: %s", flags, exc)
            return
        found = _parse_env0(stdout)
        if "PATH" not in found:
            log.info("login-shell capture (%s) exited %s with no PATH", flags, proc.returncode)
            return
        parsed[flags] = found  # one thread a key ("-lc" / "-lic"): no two write the same entry

    # One thread a round, so a slow round never spends the other's time.
    waiters = [
        threading.Thread(target=settle, args=(flags, proc)) for flags, proc in rounds if proc is not None
    ]
    for waiter in waiters:
        waiter.start()
    for waiter in waiters:
        waiter.join()
    login = parsed.get("-lc")
    interactive = parsed.get("-lic")
    return LoginCapture(
        environment={**(login or {}), **(interactive or {})},
        login=login is not None,
        interactive=interactive is not None,
        shell=shell,
        timed_out=bool(killed),
        started=any(proc is not None for _, proc in rounds),
        error=error,
    )


def login_shell_environment(
    shell: str | None = None, timeout: float = CAPTURE_TIMEOUT_S, env: dict[str, str] | None = None
) -> dict[str, str]:
    """`capture_login_shell`'s environment: {} when nothing answered."""
    return capture_login_shell(shell, timeout, env).environment


# What the login-shell capture never overrides: the service's own
# configuration and what started it (a check's overrides, systemd's).
PROTECTED_PREFIXES = ("COLLINS_", "XDG_")
PROTECTED_NAMES = frozenset(
    {"PYTHONPATH", "NOTIFY_SOCKET", "INVOCATION_ID", "JOURNAL_STREAM", "HOME", "USER"}
)


def merge_environment(service_env: dict[str, str], captured: dict[str, str]) -> dict[str, str]:
    """The service's environment overlaid by the login shell's (§3.10: the
    user's variables and ``PATH`` order win), except what is protected:
    ``COLLINS_*``, ``XDG_*`` and what started the service. ``PATH`` is the
    captured one with the entries the service's had and the shell's lacks
    put back in front, in their order: what an e2e harness (or a user's
    launcher) prepended stays first."""
    merged = dict(service_env)
    for key, value in captured.items():
        if key == "PATH" or key.startswith(PROTECTED_PREFIXES) or key in PROTECTED_NAMES:
            continue
        merged[key] = value
    if "PATH" in captured:
        shell_entries = [p for p in captured["PATH"].split(":") if p]
        known = set(shell_entries)
        front = [p for p in service_env.get("PATH", "").split(":") if p and p not in known]
        merged["PATH"] = ":".join(front + shell_entries)
    return merged


def sd_notify(state: str = "READY=1", target: str | None = None) -> bool:
    """One datagram to *target* (``$NOTIFY_SOCKET`` when None; D32). False
    when there is none or it could not be sent. The `Service` keeps the
    address itself, since the environment's copy is popped right after
    READY=1 so no spawned shell inherits it."""
    if target is None:
        target = os.environ.get("NOTIFY_SOCKET", "")
    if not target:
        return False
    if target.startswith("@"):
        target = "\0" + target[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode(), target)
    except OSError as exc:
        log.info("sd_notify failed: %s", exc)
        return False
    return True


def lingering() -> bool | None:
    """Whether `loginctl` says this user lingers; None when it can't say."""
    if shutil.which("loginctl") is None:
        return None
    try:
        result = subprocess.run(
            ["loginctl", "show-user", str(os.getuid()), "-p", "Linger", "--value"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    if value in ("yes", "no"):
        return value == "yes"
    return None


def check(app_id: str, out=sys.stdout) -> int:
    """`--check`: what a headless box needs, one line each; 1 when a
    requirement is missing (no ``$XDG_RUNTIME_DIR``, no ``claude`` on the
    service's PATH), 0 otherwise."""
    missing = 0
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    linger = lingering()
    print(f"service:      {__version__}, app id {app_id}", file=out)
    if linger is None:
        print("lingering:    unknown (no loginctl)", file=out)
    elif linger:
        print("lingering:    on", file=out)
    else:
        print(
            "lingering:    off (the service ends at logout on a headless box; "
            "`loginctl enable-linger` keeps it)",
            file=out,
        )
    if runtime:
        print(f"runtime dir:  {runtime}", file=out)
    else:
        print(
            "runtime dir:  $XDG_RUNTIME_DIR is not set (a login session, or lingering, provides it)",
            file=out,
        )
        missing += 1
    path = api_server.socket_path(app_id)
    print(f"socket:       {path} ({api_server.probe_socket(path)})", file=out)
    capture = capture_login_shell()
    shell = capture.shell
    window = f"{CAPTURE_TIMEOUT_S:.0f} s"
    if capture.login and capture.interactive:
        verdict = "login profile and interactive rc read"
    elif capture.login:
        also = f", or did not answer within {window}" if capture.timed_out else ""
        verdict = (
            f"login profile read; the interactive rc handed the shell on — exec zsh, tmux?{also} — so a "
            "PATH set only there is not seen: put it in ~/.profile or ~/.zprofile"
        )
    elif capture.interactive:
        verdict = "interactive rc read; the login profile did not answer"
    elif capture.timed_out:
        verdict = f"no answer within {window}: the service runs on its own environment"
    elif not capture.started:
        verdict = f"{shell} could not be run: {capture.error}; the service runs on its own environment"
    else:
        verdict = "no PATH read from either round; the service runs on its own environment"
    print(f"shell:        {shell} ({verdict})", file=out)
    env = merge_environment(dict(os.environ), capture.environment)
    claude = shutil.which("claude", path=env.get("PATH"))
    if claude:
        print(f"claude:       {claude}", file=out)
    else:
        print("claude:       not on the login shell's PATH", file=out)
        missing += 1
    bwrap = shutil.which("bwrap", path=env.get("PATH"))
    print(f"bwrap:        {bwrap or 'not found (sessions run unsandboxed)'}", file=out)
    return 1 if missing else 0


def print_socket(app_id: str, out=sys.stdout, wait_s: float = START_WAIT_S) -> int:
    """`--print-socket`: start a service when none answers, print the
    socket's path, exit 0 (1 when none came up)."""
    from .. import connection

    path = api_server.socket_path(app_id)
    if api_server.probe_socket(path) != "live":
        try:
            connection.spawn_service(app_id)
        except OSError as exc:
            print(f"collins-service: could not start: {exc}", file=sys.stderr)
            return 1
        deadline = time.monotonic() + wait_s
        while api_server.probe_socket(path) != "live":
            if time.monotonic() >= deadline:
                print(f"collins-service: no service answered on {path}", file=sys.stderr)
                return 1
            time.sleep(0.1)
    print(path, file=out)
    return 0


# ---- the service ----------------------------------------------------------------------


class Service:
    """One running service: built by `run`, stopped by a signal or
    `service.restart`."""

    def __init__(self, app_id: str) -> None:
        from .core import ServiceCore

        self.app_id = app_id
        # The very first step, before anything stateful (the state file's
        # writer, the store, the sweeps, prune_models, the MCP socket): the
        # single-instance lock, held for the service's life, then the
        # socket probe (a service that crashed holds no lock but may have
        # left a socket, which listen() unlinks when nobody answers).
        self._lock_fd = take_lock(app_id)
        path = api_server.socket_path(app_id)
        if api_server.probe_socket(path) == "live":
            raise OSError(errno.EADDRINUSE, f"a service is already listening on {path}")
        self.environment = merge_environment(dict(os.environ), login_shell_environment())
        self.core = ServiceCore.with_state(environment=lambda: dict(self.environment), app_id=app_id)
        # The keeper, before anything is spawned (PR-3.6); its loss mid-run
        # brings another (`PtyServer._keeper_lost`).
        self.keeper = self._find_keeper(app_id)
        if self.keeper is not None:
            self.core.attach_keeper(self.keeper, restart=lambda: self._find_keeper(app_id))
            log.info("the keeper is pid %s", self.keeper.pid)
        elif keeper_wanted():
            # No client, yet a keeper may be there: one of another protocol
            # holding ptys (`keeper_mismatch`), or one alive that did not
            # answer. Its ptys live, so the adoption must touch no row
            # (N-M1); with no keeper process at all the rows name dead ptys
            # and are dropped as before.
            pid = keeper_mod.read_pid(api_server.keeper_socket_path(app_id))
            if self.core.keeper_mismatch:
                self.core.mark_keeper_unread("it speaks another protocol", pid)
            elif pid is not None:
                self.core.mark_keeper_unread("it did not answer", pid)
        self._load_stubs()
        self.server = api_server.ApiServer(
            self.core,
            app_id,
            debug=os.environ.get("COLLINS_DEBUG_API") == "1",
            on_restart=self._on_restart,
        )
        self.loop = GLib.MainLoop()
        self.stopping = False
        self.exit_code = 0
        # systemd's notify address, kept here: `start` pops it from the
        # environment after READY=1, and `stop` still owes STOPPING=1.
        self.notify_socket = os.environ.get("NOTIFY_SOCKET", "")

    def _find_keeper(self, app_id: str) -> keeper_mod.KeeperClient | None:
        """`find_or_start_keeper`, a mismatch recorded on the core (its
        `service.status` says `keeper_mismatch`) and answered with no
        keeper: the ruling on an older keeper that holds ptys."""
        try:
            client = find_or_start_keeper(app_id)
        except KeeperMismatch as exc:
            log.error("%s; running without a keeper, its ptys left as they are", exc)
            self.core.keeper_mismatch = exc.held
            return None
        self.core.keeper_mismatch = 0
        return client

    def _load_stubs(self) -> None:
        """The e2e checks' stubs (scripts/e2e_stubs.py), only under the
        probe's flag (D27): a module named in ``COLLINS_E2E_STUBS`` is
        imported here, in the service's process, where the patches it
        makes are seen."""
        path = os.environ.get("COLLINS_E2E_STUBS")
        if not path or not self.core.debug:
            return
        import importlib.util

        spec = importlib.util.spec_from_file_location("collins_e2e_stubs", path)
        if spec is None or spec.loader is None:
            log.error("stubs module %s cannot be loaded", path)
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        log.info("e2e stubs applied from %s", path)

    def start(self) -> str:
        """Everything up, in the order `App.do_startup` had it; returns the
        socket's path. Raises OSError when a service already listens."""
        from .. import clisetup

        core = self.core
        # Every claude lookup of this process (the spawn's command, titles,
        # icons, the login repair, the sandbox's mount, the background
        # agents' poll, `service.status`) searches the session environment's
        # PATH, where the shells it spawns look: a service started by a
        # non-interactive ssh command has a process PATH without
        # `~/.local/bin` (PR-3.0 item 10).
        clisetup.set_search_base(lambda: self.environment.get("PATH"))
        clisetup.apply_saved(core.state)
        # The ptys the keeper held across the last service's stop, adopted
        # first (PR-3.6): the store's running rows and the sandbox host's
        # re-hold read them.
        core.adopt_ptys()
        core.start_store()
        core.start_activity()
        core.start_sandbox_host(self.app_id)
        # PR-1.12d: the background agents (the poller, the handoffs, the
        # replay) and the service's own timers (the archive sweep, gh).
        core.start_background()
        core.start_housekeeping()
        core.remove_inflight_copies()
        core.start_tools()
        core.start_sandbox()
        config = core.start_mcp(self.app_id)
        if config is not None:
            providers.MCP_CONFIG_PATH = config
        path = self.server.listen()
        for number in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, number, self._on_signal, number)
        sd_notify("READY=1", self.notify_socket)
        # As systemd's NotifyAccess/unset_environment would: a spawned shell
        # must not inherit the manager's socket (the core strips it too).
        os.environ.pop("NOTIFY_SOCKET", None)
        self.environment.pop("NOTIFY_SOCKET", None)
        log.info("collins-service %s listening on %s", __version__, path)
        return path

    def run(self) -> int:
        self.loop.run()
        return self.exit_code

    def _on_signal(self, number: int) -> bool:
        log.info("signal %d: stopping", number)
        self.stop()
        return GLib.SOURCE_REMOVE

    def _on_restart(self, when: str) -> None:
        log.info("service.restart %s: stopping", when)
        self.stop()

    def stop(self) -> None:
        """Stop accepting, disconnect the clients, then: with a keeper
        holding the ptys (§3.10 item 2), `suspend` (every model saved, the
        masters closed, every session left running for the next service);
        without one (§3.10 item 1) end every session as *Stop sessions and
        quit* does (ids recorded as ``resume_on_start``). The grants and
        the socket go either way; exit 0."""
        if self.stopping:
            return
        self.stopping = True
        sd_notify("STOPPING=1", self.notify_socket)
        try:
            self.server.stop_accepting()
            self.core.stop_mcp()
            # The clients go first (PR-1.12c): a client sees its link lost,
            # not one pty-exited per session, so it keeps its open tabs and
            # resumes each on the service that starts next (§3.21) instead
            # of closing them one by one as their sessions end.
            self.server.stop()
            if self.core.keeper_holds:
                # The agent ptys no keeper holds (forked while it was lost,
                # S1) end the item-1 way first, recorded to resume; the
                # rest are left to the keeper.
                unkept = set(self.core.unkept_agent_ptys())

                def suspended() -> None:
                    kept = self.core.suspend(keep_resume=bool(unkept))
                    keeper_pid = self.keeper.pid if self.keeper else "?"
                    log.info("leaving %d pty(s) to the keeper (pid %s)", kept, keeper_pid)
                    self._stopped()

                if unkept:
                    log.info("ending %d unkept session(s) first, recorded to resume", len(unkept))
                    self.core.stop_sessions(done=suspended, only=unkept)
                else:
                    suspended()
                return
            ended = self.core.stop_sessions(done=self._stopped)
            if ended:
                log.info("ending %d session(s), recorded to resume", len(ended))
        except Exception:
            log.exception("the stop sequence failed")
            self.exit_code = 1
            self._stopped()

    def _stopped(self) -> None:
        """Every close flow has settled (or the bound passed) and the core
        is shut: the server goes, then the loop."""
        try:
            self.server.stop()
        except Exception:
            log.exception("closing the server failed")
        GLib.idle_add(self._quit, priority=GLib.PRIORITY_DEFAULT)

    def _quit(self) -> bool:
        self.loop.quit()
        return GLib.SOURCE_REMOVE


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="collins-service", description="The Collins session service.")
    parser.add_argument("--app-id", default=os.environ.get("COLLINS_APP_ID") or APP_ID)
    parser.add_argument("--print-socket", action="store_true", help="start if needed, print the socket path")
    parser.add_argument("--check", action="store_true", help="say what a headless box needs")
    parser.add_argument("--foreground", action="store_true", help="run in the foreground (the default)")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


DESCRIPTOR_LIMIT = 65536


def raise_descriptor_limit(ceiling: int = DESCRIPTOR_LIMIT) -> int | None:
    """The soft `RLIMIT_NOFILE` raised to the hard limit, at most *ceiling*,
    as every long-running daemon does: the pty table holds a master per
    session and shell, each watch a descriptor, and a tree copy two per
    level (`projectfiles._walk`), so under systemd's user-unit default
    of 1024 a tree some 500 levels deep failed "Too many open files"
    (the Fable review of PR 613, nit 1); the ceiling then becomes the
    interpreter's recursion limit, which fails one entry. Never lowers
    the soft limit; fail-soft. Returns the soft limit set, or None."""
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = ceiling if hard == resource.RLIM_INFINITY else min(hard, ceiling)
        if soft == resource.RLIM_INFINITY or wanted <= soft:
            return soft
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
        return wanted
    except (ImportError, OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=(os.environ.get("COLLINS_LOG") or "WARNING").upper(),
        format="collins-service %(levelname)s %(name)s: %(message)s",
    )
    os.environ["COLLINS_APP_ID"] = args.app_id
    if args.check:
        return check(args.app_id)
    if args.print_socket:
        return print_socket(args.app_id)
    raise_descriptor_limit()
    # The service formats only `paint` text, per client locale (§3.14).
    i18n.init("en")
    try:
        service = Service(args.app_id)
        service.start()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            # A service already runs for this id (the lock, the probe, or
            # listen() in the race between two starting at once): exit 0,
            # so a unit does not restart over it.
            print(api_server.socket_path(args.app_id))
            log.info("not starting: %s", exc)
            return 0
        log.error("could not start: %s", exc)
        return 1
    except GLib.Error as exc:
        log.error("could not listen: %s", exc.message)
        return 1
    return service.run()


if __name__ == "__main__":
    sys.exit(main())
