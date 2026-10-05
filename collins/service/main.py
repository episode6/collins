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

**The session environment** (§3.10): the process's environment overlaid
once at start by a login-shell capture (``$SHELL -lic 'env -0'``,
``stdin=DEVNULL``, 5 s, parsed by NUL, failing soft): what makes a
headless box's shells find ``~/.local/bin/claude`` and the user's ``PATH``.
The capture fills in what the service's environment lacks and appends the
``PATH`` entries it lacks; it never overrides a variable the service was
started with (a check's ``COLLINS_*`` and ``XDG_*`` overrides, its shim
directory at the front of ``PATH``).

A second ``collins-service`` for the same app id finds the live socket and
exits 0 after printing its path. ``sd_notify`` is a datagram to
``$NOTIFY_SOCKET`` (D32: no dependency). Nothing here imports GTK.
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
import time

from gi.repository import GLib

from .. import APP_ID, __version__, i18n, providers
from ..api import server as api_server

log = logging.getLogger(__name__)

CAPTURE_TIMEOUT_S = 5.0
START_WAIT_S = 10.0


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


def login_shell_environment(shell: str | None = None, timeout: float = CAPTURE_TIMEOUT_S) -> dict[str, str]:
    """The environment a login, interactive shell ends up with, or {} when
    the capture fails (no shell, a timeout, output that isn't NUL-separated
    pairs). Never raises. The shell runs in a session of its own, so a
    timeout reaps whatever it forked along with it."""
    shell = shell or os.environ.get("SHELL") or "/bin/sh"
    try:
        proc = subprocess.Popen(
            [shell, "-lic", "env -0"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # no controlling tty: a shell that asks gets EOF
        )
    except (OSError, ValueError) as exc:
        log.info("login-shell capture failed: %s", exc)
        return {}
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        log.info("login-shell capture timed out after %.0f s", timeout)
        return {}
    if proc.returncode not in (0, None) and not stdout:
        log.info("login-shell capture exited %s", proc.returncode)
        return {}
    captured: dict[str, str] = {}
    for entry in stdout.split(b"\0"):
        if b"=" not in entry:
            continue
        key, _, value = entry.partition(b"=")
        try:
            name = key.decode()
            text = value.decode()
        except UnicodeDecodeError:
            continue
        if name and name.isidentifier():
            captured[name] = text
    return captured


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


def sd_notify(state: str = "READY=1") -> bool:
    """One datagram to ``$NOTIFY_SOCKET`` (D32). False when there is none
    or it could not be sent."""
    target = os.environ.get("NOTIFY_SOCKET")
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
    env = merge_environment(dict(os.environ), login_shell_environment())
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
        self.core = ServiceCore.with_state(environment=lambda: dict(self.environment))
        self.core.app_id = app_id
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
        clisetup.apply_saved(core.state)
        core.start_store()
        core.start_activity()
        core.start_sandbox_host(self.app_id)
        # PR-1.12d: the background agents (the poller, the handoffs, the
        # replay) and the service's own timers (the archive sweep, gh).
        core.start_background()
        core.start_housekeeping()
        core.start_tools()
        core.start_sandbox()
        config = core.start_mcp(self.app_id)
        if config is not None:
            providers.MCP_CONFIG_PATH = config
        path = self.server.listen()
        for number in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, number, self._on_signal, number)
        sd_notify("READY=1")
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
        """§3.10 item 1: stop accepting, end every session as *Stop
        sessions and quit* does (ids recorded as ``resume_on_start``), the
        grants and the ptys with the core, the socket gone, exit 0."""
        if self.stopping:
            return
        self.stopping = True
        sd_notify("STOPPING=1")
        try:
            self.server.stop_accepting()
            self.core.stop_mcp()
            # The clients go first (PR-1.12c): a client sees its link lost,
            # not one pty-exited per session, so it keeps its open tabs and
            # resumes each on the service that starts next (§3.21) instead
            # of closing them one by one as their sessions end.
            self.server.stop()
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
