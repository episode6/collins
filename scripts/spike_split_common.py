"""Shared plumbing for the split-service spikes (scripts/spike_split_*.py).

Not product code. Every spike that runs a real `claude` runs it in an
isolated HOME made here, so a probe never reads the user's settings or
hooks, never writes to their `~/.claude`, and can run beside other probes:

    home = make_home()            # a fresh directory, copies of the login
    env = cli_env(home)           # what a Collins tab's shell would export
    pid, fd = spawn_cli(env, cwd=workdir(home), rows=40, cols=120)
    ...
    leave(pid, fd)                # Ctrl+C Ctrl+C, the way a person leaves
    remove_home(home)

The login is copied, not shared. A probe that ran long enough for the CLI
to refresh its token would leave the user's own copy stale, so `make_home`
refuses when the token is within MIN_TOKEN_MINUTES of expiring and
`remove_home` says so when the copy changed underneath it.

Only the CLI's own login crosses into the copy. The credentials of the
user's MCP servers stay behind, their `mcpServers` entries are dropped
from the copied config, and `spawn_cli` starts the CLI with
`--strict-mcp-config`: a probe starts none of the user's servers, so it
cannot refresh (and so rotate) a token the guard above never looked at.
"""

import fcntl
import json
import os
import pty
import select
import shutil
import signal
import struct
import sys
import tempfile
import termios
import time

MIN_TOKEN_MINUTES = 30
REAL_HOME = os.path.expanduser("~")
CREDENTIALS = os.path.join(".claude", ".credentials.json")
# The one key of the credentials file a probe needs: the CLI's own login.
LOGIN_KEY = "claudeAiOauth"
# What a project entry of ~/.claude.json says about MCP servers.
MCP_PROJECT_KEYS = ("mcpServers", "enabledMcpjsonServers", "disabledMcpjsonServers", "mcpContextUris")
NO_USER_MCP = "--strict-mcp-config"

# VTE 0.84's answers to what the CLI asks at startup (spec F8).
VTE_ANSWERS = (
    (b"\x1b[c", b"\x1b[?61;1;21;22;28c"),
    (b"\x1b[>0q", b"\x1bP>|VTE(8400)\x1b\\"),
    (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    (b"\x1b]11;?\x07", b"\x1b]11;rgb:0000/0000/0000\x07"),
)


def have_cli() -> bool:
    return shutil.which("claude") is not None and os.path.exists(
        os.path.join(REAL_HOME, CREDENTIALS)
    )


def token_minutes_left() -> float | None:
    try:
        with open(os.path.join(REAL_HOME, CREDENTIALS)) as f:
            expires = json.load(f)["claudeAiOauth"]["expiresAt"]
        return (float(expires) / 1000 - time.time()) / 60
    except (OSError, ValueError, KeyError, TypeError):
        return None


def make_home(parent: str | None = None, trust: tuple[str, ...] = ()) -> str:
    """A fresh isolated HOME holding copies of the login and the config, with
    a trusted `work` directory inside it. `trust` names more directories to
    mark trusted in the copy."""
    left = token_minutes_left()
    if left is None or left < MIN_TOKEN_MINUTES:
        raise SystemExit(
            f"the CLI's token has {left} minutes left; a probe could make the CLI "
            "refresh it in the copy and strand the user's own login. Run "
            "`claude` once for real first."
        )
    home = tempfile.mkdtemp(prefix="spike-home-", dir=parent)
    os.chmod(home, 0o700)
    os.makedirs(os.path.join(home, ".claude"), mode=0o700)
    work = os.path.join(home, "work")
    os.makedirs(work)
    with open(os.path.join(REAL_HOME, CREDENTIALS)) as f:
        login = {LOGIN_KEY: json.load(f)[LOGIN_KEY]}
    _write_private(os.path.join(home, CREDENTIALS), login)
    with open(os.path.join(REAL_HOME, ".claude.json")) as f:
        config = json.load(f)
    config.pop("mcpServers", None)
    projects = config.get("projects")
    if not isinstance(projects, dict):
        projects = config["projects"] = {}
    for entry in projects.values():
        if isinstance(entry, dict):
            for key in MCP_PROJECT_KEYS:
                entry.pop(key, None)
    for path in (work, *trust):
        entry = projects.setdefault(os.path.realpath(path), {})
        entry["hasTrustDialogAccepted"] = True
    _write_private(os.path.join(home, ".claude.json"), config)
    return home


def _write_private(path: str, data: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def workdir(home: str) -> str:
    return os.path.join(home, "work")


def remove_home(home: str) -> None:
    """Remove an isolated HOME, saying so first if the CLI refreshed the
    login inside it (the user's own copy is then the stale one)."""
    try:
        with open(os.path.join(home, CREDENTIALS)) as f:
            copy = json.load(f).get(LOGIN_KEY)
        with open(os.path.join(REAL_HOME, CREDENTIALS)) as f:
            real = json.load(f).get(LOGIN_KEY)
        if copy != real:
            print(
                "WARNING: the login changed inside the isolated HOME (or outside it) "
                "during this probe; if `claude` asks to log in again, that is why.",
                file=sys.stderr,
            )
    except (OSError, ValueError, AttributeError):
        pass
    shutil.rmtree(home, ignore_errors=True)


def cli_env(home: str, fullscreen: bool | None = None, term_program: str = "kitty") -> dict:
    """The environment a Collins tab gives the CLI, in the isolated HOME.
    `fullscreen` True forces the alternate-screen mode, False the classic
    inline one, None leaves the CLI to its own choice (its settings, then
    what the account's server gates say: not something a fixture can rely
    on)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CLAUDE", "AI_AGENT", "COLLINS", "PROBE"))
    }
    env["HOME"] = home
    if fullscreen is not None:
        env["CLAUDE_CODE_NO_FLICKER"] = "1" if fullscreen else "0"
    env.update(
        TERM="xterm-256color",
        COLORTERM="truecolor",
        VTE_VERSION="8400",
        ConEmuANSI="ON",
        TERM_PROGRAM=term_program,
    )
    return env


def set_size(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def spawn_cli(env: dict, cwd: str, rows: int = 40, cols: int = 120, argv=("claude",)):
    """Fork `claude` on a fresh pty, with none of the user's MCP servers.
    Returns (pid, master fd)."""
    argv = list(argv)
    if os.path.basename(argv[0]) == "claude" and NO_USER_MCP not in argv:
        argv.insert(1, NO_USER_MCP)
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(argv[0], argv, env)
    set_size(fd, rows, cols)
    return pid, fd


def drain(fd: int, seconds: float, answer: bool = True, quiet_after: float | None = None) -> bytes:
    """Read the pty for `seconds` (or until `quiet_after` seconds pass with
    nothing arriving, once something has), answering the startup queries the
    way VTE would unless told not to."""
    end = time.monotonic() + seconds
    got = b""
    last = None
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            got += data
            last = time.monotonic()
            if answer:
                for query, reply in VTE_ANSWERS:
                    if query in data:
                        os.write(fd, reply)
        elif quiet_after is not None and last is not None and time.monotonic() - last > quiet_after:
            break
    return got


def type_text(fd: int, text: bytes, gap: float = 0.01) -> None:
    for b in text:
        os.write(fd, bytes([b]))
        time.sleep(gap)


def leave(pid: int, fd: int, wait: float = 4.0) -> bytes:
    """Leave the CLI the way a person does (Ctrl+C Ctrl+C), so it tidies its
    own records; only then make sure nothing is left of it."""
    got = b""
    try:
        os.write(fd, b"\x03")
        time.sleep(0.15)
        os.write(fd, b"\x03")
        end = time.monotonic() + wait
        while time.monotonic() < end:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                data = os.read(fd, 65536)
                if not data:
                    break
                got += data
    except OSError:
        pass
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass
    return got
