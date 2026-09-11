#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
#
# The mount tables and their ordering rules are ported from aibox
# (github.com/EricKuck/dotfiles, packages/aibox/src/plan.rs), MIT License,
# Copyright (c) 2024-2026 Eric Kuck. See ~/specs/collins/sandboxed-sessions.md.
"""PR 0 spike for sandboxed sessions: run `claude` inside a bubblewrap box
shaped like aibox's, with Collins' MCP socket bound in.

    python3 scripts/spike_sandbox.py --check [--workspace DIR] [--app-id ID]
    python3 scripts/spike_sandbox.py --run   [--workspace DIR] [--app-id ID] [-- claude args…]
    python3 scripts/spike_sandbox.py --print-plan

`--check` runs a probe battery inside the box (no claude) and prints a
PASS/FAIL/INFO table: namespaces, the overlay home, secrets absent,
`~/.claude/settings.json` protected, the Collins socket reachable and
answering a `list`, `claude --version`, `gh` logged out, and the startup
overhead. `--run` execs an interactive claude inside the box from the
terminal you run it in, with `--permission-mode bypassPermissions` and the
live instance's `--mcp-config` (the debug instance by default when its
socket is up, else the installed one).

Not an e2e check (scripts/run_e2e.py only discovers check_*.py) and not
product code: the plan table here is the spec's, kept small, so the facts
the spec rests on can be re-measured on any machine. `--home` picks the
overlay home (default ~/.cache/collins/spike-sandbox/home, seeded from
~/.claude.json once). `--share-gh`, `--share-ssh` and `--unprotect-settings`
mirror the switches the spec proposes.

Exit status: `--check` exits 1 when any FAIL row is printed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- the plan (a port of aibox's plan.rs tables, trimmed to what matters) ----

RO_HOME = [
    ".gitconfig",
    ".config/git",
    ".config/delta",
    ".terminfo",
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshenv",
    ".zshrc",
    ".zprofile",
    ".inputrc",
]

RW_HOME = [
    ".claude",
    ".config/fish",
    ".local/share/fish",
    ".java",
    ".sdkman",
    ".konan",
    ".android",
    "Android/Sdk",
    ".pyenv",
    ".config/pip",
    ".local/lib",
    ".nvm",
    ".fnm",
    ".volta",
    ".bun",
    ".deno",
    ".yarn",
    ".config/yarn",
    "go",
    ".cache/go-build",
    ".gem",
    ".bundle",
]

RW_HOME_ALWAYS = [
    ".cargo",
    ".rustup",
    ".npm",
    ".gradle",
    ".m2",
    ".cache/uv",
    ".local/share/uv",
    ".cache/pip",
    ".cache/pypoetry",
    ".cache/node-gyp",
    ".cache/yarn",
    ".cache/pnpm",
    ".local/share/pnpm",
    ".cache/ms-playwright",
    ".cache/sccache",
    ".cache/ccache",
    ".local/bin",
]

SENSITIVE_HOME = [
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".docker",
    ".config/gh",
    ".config/gcloud",
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".config/op",
    ".config/containers",
    ".local/share/keyrings",
    ".gradle/gradle.properties",
    ".m2/settings.xml",
    ".m2/settings-security.xml",
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".rustup/credentials",
    ".rustup/credentials.toml",
    ".gem/credentials",
    ".bundle/config",
    ".config/pypoetry/auth.toml",
]

RO_ABSOLUTE = [
    "/bin",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/nix",
    "/opt",
    "/sbin",
    "/sys",
    "/usr",
    "/var/empty",
    "/run/current-system",
    "/run/systemd/resolve",
    "/run/nscd",
    "/run/wrappers",
    "/run/opengl-driver",
    "/run/booted-system",
]

RW_ABSOLUTE = ["/tmp", "/var/tmp"]

# Collins' own state, which must never be carried in even by a grant.
PROTECTED_REL = [".config/collins", ".local/state/collins", ".cache/collins"]

SCRUB_ENV = ["SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO"]


def repo_root(workspace: str) -> str | None:
    """The nearest ancestor holding a .git, the workspace included."""
    d = Path(workspace)
    while True:
        if (d / ".git").exists():
            return str(d)
        if d.parent == d:
            return None
        d = d.parent


def worktree_common_git(git_file: Path) -> str | None:
    try:
        first = git_file.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not first.startswith("gitdir:"):
        return None
    d = Path(first[len("gitdir:") :].strip())
    if not d.is_absolute():
        return None
    while d.name != ".git":
        if d.parent == d:
            return None
        d = d.parent
    return str(d)


class Plan:
    def __init__(self) -> None:
        self.args: list[str] = []
        self.sources: list[str] = []  # host paths carried in (for the masks' `carried` test)
        self.notes: list[str] = []

    def ro(self, src: str, dest: str | None = None, required: bool = False) -> None:
        self.args += ["--ro-bind" if required else "--ro-bind-try", src, dest or src]
        self.sources.append(src)

    def rw(self, src: str, dest: str | None = None, required: bool = False) -> None:
        self.args += ["--bind" if required else "--bind-try", src, dest or src]
        self.sources.append(src)

    def tmpfs(self, dest: str) -> None:
        self.args += ["--tmpfs", dest]

    def mask_file(self, dest: str) -> None:
        self.args += ["--ro-bind", "/dev/null", dest]

    def carried(self, path: str, within: str | None) -> bool:
        p = Path(path)
        for src in self.sources:
            s = Path(src)
            if within and not s.is_relative_to(within):
                continue
            if p != s and p.is_relative_to(s):
                return True
        return False


def resolved_claude() -> tuple[str, str] | None:
    """(the path `claude` resolves to, the directory to bind read-only)."""
    found = shutil.which("claude")
    if not found:
        return None
    real = os.path.realpath(found)
    home = str(Path.home())
    # The native installer: ~/.local/share/claude/versions/<v>; bind the
    # whole ~/.local/share/claude so the launcher's sibling files resolve.
    native = os.path.join(home, ".local", "share", "claude")
    if real.startswith(native + os.sep):
        return real, native
    return real, os.path.dirname(real)


def build_plan(opts: argparse.Namespace, socket_file: str | None, config_dir: str | None) -> Plan:
    home = str(Path.home())
    ws = os.path.realpath(opts.workspace)
    sandbox_home = os.path.realpath(opts.home)
    if ws in (home, "/"):
        sys.exit(f"refusing to use {ws} as a workspace")
    if sandbox_home.startswith(ws + os.sep) or ws.startswith(sandbox_home + os.sep):
        sys.exit("the overlay home and the workspace must not nest")

    p = Plan()
    p.args += ["--unshare-user", "--unshare-pid", "--die-with-parent"]
    # $HOME first: everything below stacks onto the overlay.
    p.rw(sandbox_home, home, required=True)
    for path in RO_ABSOLUTE:
        p.ro(path)
    for path in RW_ABSOLUTE:
        p.rw(path)
    run_dir = os.environ.get("XDG_RUNTIME_DIR")
    if run_dir and os.path.isabs(run_dir):
        p.tmpfs(run_dir)
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir and os.path.isabs(tmpdir) and tmpdir not in RW_ABSOLUTE:
        p.rw(tmpdir)
    for rel in RO_HOME:
        p.ro(os.path.join(home, rel))
    for rel in RW_HOME:
        p.rw(os.path.join(home, rel))
    for rel in RW_HOME_ALWAYS:
        full = os.path.join(home, rel)
        # A bind needs a source, so aibox creates these when absent. Only
        # when a box is actually launched: --print-plan must not touch the
        # real home (the bind is --bind-try, so a missing one is skipped).
        if not opts.print_plan:
            os.makedirs(full, exist_ok=True)
        p.rw(full)

    # ---- Collins-specific additions (the spec's list) ----
    # Before the workspace, so a workspace that overlaps one of these (a
    # checkout of Collins itself, as under ./start-debug) lands on top and
    # stays read-write. First run of this spike had the checkout's ro bind
    # shadow the workspace.
    cli = resolved_claude()
    if cli:
        real, bind_dir = cli
        if not bind_dir.startswith("/usr"):
            p.ro(bind_dir)
        p.notes.append(f"claude resolves to {real}; binding {bind_dir} read-only")
    else:
        p.notes.append("claude not found on PATH")
    if not sys.prefix.startswith("/usr"):
        p.ro(sys.prefix)
        p.notes.append(f"interpreter prefix {sys.prefix} bound read-only")
    if not REPO_ROOT.startswith("/usr"):
        p.ro(REPO_ROOT)  # the collins package the shim is imported from (PYTHONPATH)
    if config_dir and not config_dir.startswith("/usr"):
        p.ro(config_dir)
    if socket_file:
        # After the runtime dir's tmpfs, so it lands on top. Read-write:
        # connect(2) needs write permission on the socket inode.
        p.rw(socket_file, required=True)

    # The workspace and the enclosing repository's shared directories.
    p.rw(ws, required=True)
    root = repo_root(ws)
    if root:
        for name in (".git", ".claude"):
            p.rw(os.path.join(root, name))
        git = Path(root) / ".git"
        if git.is_file():
            common = worktree_common_git(git)
            if common:
                p.rw(common)
                p.notes.append(f"linked worktree: common git dir {common}")

    if opts.share_gh:
        p.rw(os.path.join(home, ".config", "gh"))
        p.notes.append("sharing ~/.config/gh (the GitHub token)")
    ssh_sock = os.environ.get("SSH_AUTH_SOCK")
    if opts.share_ssh and ssh_sock:
        p.rw(os.path.dirname(ssh_sock))
        p.notes.append(f"sharing the SSH agent directory {os.path.dirname(ssh_sock)}")

    # Masks last: a deeper, later mount carves a secret back out.
    for rel in SENSITIVE_HOME:
        if opts.share_gh and rel == ".config/gh":
            continue
        full = os.path.join(home, rel)
        if not p.carried(full, home):
            continue
        if not os.path.lexists(full):
            continue  # bwrap would create the destination it was told to cover
        if os.path.isdir(full) and not os.path.islink(full):
            p.tmpfs(full)
        else:
            p.mask_file(full)
    if ssh_sock and not opts.share_ssh and p.carried(ssh_sock, None):
        p.mask_file(ssh_sock)
    docker = os.environ.get("DOCKER_HOST", "")
    if docker.startswith(("unix://", "/")):
        path = docker.removeprefix("unix://")
        if p.carried(path, None):
            p.mask_file(path)
    if not opts.unprotect_settings:
        for name in ("settings.json", "settings.local.json"):
            full = os.path.join(home, ".claude", name)
            if os.path.isfile(full):
                p.ro(full, required=True)

    for rel in PROTECTED_REL:
        full = os.path.join(home, rel)
        for src in p.sources:
            if full == src or full.startswith(src + os.sep):
                sys.exit(f"{full} must stay outside the sandbox, but {src} would carry it in")

    p.args += ["--proc", "/proc", "--dev", "/dev"]
    for dev in ("/dev/kvm", "/dev/bus/usb"):
        p.args += ["--dev-bind-try", dev, dev]
    p.args += ["--chdir", ws, "--setenv", "HOME", home]
    for var in SCRUB_ENV:
        # --share-ssh binds the agent's directory in; the variable that
        # points at it has to survive for ssh to find it.
        if opts.share_ssh and var in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"):
            continue
        p.args += ["--unsetenv", var]
    if docker.startswith(("unix://", "/")):
        p.args += ["--unsetenv", "DOCKER_HOST"]
    return p


def seed_home(sandbox_home: str) -> None:
    os.makedirs(sandbox_home, mode=0o700, exist_ok=True)
    os.chmod(sandbox_home, 0o700)
    dest = os.path.join(sandbox_home, ".claude.json")
    src = os.path.join(str(Path.home()), ".claude.json")
    if not os.path.exists(dest) and os.path.isfile(src):
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o600)


def mirror_trust(sandbox_home: str, workspace: str) -> None:
    """The spec's trust mirror: copy the host's hasTrustDialogAccepted for
    this workspace into the overlay copy (and nothing else)."""
    host = os.path.join(str(Path.home()), ".claude.json")
    dest = os.path.join(sandbox_home, ".claude.json")
    try:
        with open(host) as f:
            h = json.load(f)
        with open(dest) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return
    entry = h.get("projects", {}).get(workspace)
    if not isinstance(entry, dict) or not entry.get("hasTrustDialogAccepted"):
        return
    d.setdefault("projects", {}).setdefault(workspace, {})["hasTrustDialogAccepted"] = True
    tmp = dest + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, dest)


def bwrap_command(plan: Plan, argv: list[str]) -> list[str]:
    bwrap = os.environ.get("COLLINS_BWRAP") or shutil.which("bwrap")
    if not bwrap:
        sys.exit("bwrap is not on PATH; install bubblewrap")
    return [bwrap, *plan.args, "--", *argv]


# ---- the probes that run INSIDE the box (stdlib only) ----


def inside(args: list[str]) -> int:
    socket_file, workspace, protected = args[0] or None, args[1], args[2] == "1"
    share_ssh = len(args) > 3 and args[3] == "1"
    rows: list[tuple[str, str, str]] = []

    def row(kind: str, name: str, detail: str) -> None:
        rows.append((kind, name, detail))

    home = os.environ.get("HOME", "")
    pid = os.getpid()
    nspid = ""
    try:
        for line in open("/proc/self/status"):
            if line.startswith("NSpid:"):
                nspid = line.split(":", 1)[1].strip()
    except OSError:
        pass
    row("PASS" if pid < 100 else "FAIL", "pid namespace", f"pid={pid} NSpid={nspid}")
    row("INFO", "HOME", home)
    row("PASS" if os.path.isfile(os.path.join(home, ".claude.json")) else "FAIL", "seeded ~/.claude.json", "")
    for rel in (".ssh", ".config/gh", ".gnupg", ".config/collins", ".local/state/collins"):
        full = os.path.join(home, rel)
        present = os.path.exists(full)
        listing = os.listdir(full) if present and os.path.isdir(full) else []
        ok = not present or not listing
        row(
            "PASS" if ok else "FAIL",
            f"{rel} absent or empty",
            "absent" if not present else f"{len(listing)} entries",
        )
    run_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    entries = set(os.listdir(run_dir)) if run_dir and os.path.isdir(run_dir) else set()
    ssh = os.environ.get("SSH_AUTH_SOCK")
    # Only what the plan bound on purpose: the Collins socket's directory,
    # and the agent's directory when --share-ssh put it there.
    allowed = {"collins"}
    if share_ssh and ssh and ssh.startswith(run_dir + os.sep):
        allowed.add(ssh[len(run_dir) + 1 :].split(os.sep, 1)[0])
    row(
        "PASS" if entries <= allowed else "FAIL",
        "XDG_RUNTIME_DIR private",
        f"{run_dir}: {sorted(entries)}",
    )
    if share_ssh:
        ok = bool(ssh) and os.path.exists(ssh)
        row(
            "PASS" if ok else "FAIL",
            "SSH agent shared",
            f"SSH_AUTH_SOCK={ssh} {'reachable' if ok else 'missing'}",
        )
    else:
        row("PASS" if not ssh else "FAIL", "SSH_AUTH_SOCK scrubbed", "")
    row("PASS" if os.access(workspace, os.W_OK) else "FAIL", "workspace writable", workspace)
    row("PASS" if os.access(os.path.join(home, ".claude"), os.W_OK) else "FAIL", "~/.claude writable", "")

    settings = os.path.join(home, ".claude", "settings.json")
    if os.path.exists(settings):
        try:
            with open(settings, "a"):
                pass
            write = "write succeeded"
        except OSError as e:
            write = f"write refused: {e.errno} {e.strerror}"
        tmp = settings + ".spike"
        try:
            shutil.copyfile(settings, tmp)
            os.replace(tmp, settings)
            rename = "rename-over succeeded"
        except OSError as e:
            rename = f"rename-over refused: {e.errno} {e.strerror}"
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        refused = "refused" in write and "refused" in rename
        row(
            "PASS" if refused == protected else "FAIL",
            "settings.json protected" if protected else "settings.json writable",
            f"{write}; {rename}",
        )
    else:
        row("INFO", "settings.json", "absent")

    if socket_file:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(socket_file)
            s.sendall(json.dumps({"op": "hello", "pid": pid, "v": 1}).encode() + b"\n")
            s.sendall(json.dumps({"op": "list", "id": 1}).encode() + b"\n")
            reply = s.makefile("rb").readline()
            data = json.loads(reply) if reply else None
            tools = [t.get("name") for t in (data or {}).get("tools", [])]
            row(
                "PASS" if data and data.get("ok") else "FAIL",
                "Collins socket answers list",
                f"{len(tools)} tools" if tools else repr(reply[:80]),
            )
        except OSError as e:
            row("FAIL", "Collins socket", f"{e}")
    else:
        row("INFO", "Collins socket", "no live instance found; skipped")

    for name, argv in (
        ("claude --version", ["claude", "--version"]),
        ("gh auth status", ["gh", "auth", "status"]),
        ("git status", ["git", "-C", workspace, "status", "--short", "-b"]),
    ):
        if not shutil.which(argv[0]):
            row("INFO", name, "not on PATH inside")
            continue
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            out = (r.stdout or r.stderr).strip().splitlines()
            first = out[0] if out else ""
            if name.startswith("gh"):
                kind = "PASS" if r.returncode != 0 else "INFO"
                first = "logged out inside (expected)" if r.returncode != 0 else "logged IN inside"
            else:
                kind = "PASS" if r.returncode == 0 else "FAIL"
            row(kind, name, first[:100])
        except (OSError, subprocess.TimeoutExpired) as e:
            row("FAIL", name, str(e))

    width = max(len(n) for _, n, _ in rows)
    for kind, name, detail in rows:
        print(f"{kind:4} {name.ljust(width)}  {detail}")
    return 1 if any(k == "FAIL" for k, _, _ in rows) else 0


# ---- host side ----


def live_instance(app_id: str | None) -> tuple[str | None, str | None, str | None]:
    """(app id, socket file, config dir) of a running Collins, preferring
    the id asked for, else the debug instance, else the installed one."""
    sys.path.insert(0, REPO_ROOT)
    from collins import mcptools  # GTK-free

    candidates = [app_id] if app_id else [mcptools.DEBUG_APP_ID, mcptools.APP_ID]
    for cand in candidates:
        sock = mcptools.socket_path(cand)
        if os.path.exists(sock):
            return cand, sock, os.path.dirname(mcptools.config_path(cand))
    return None, None, None


def own_service() -> tuple[str, str, str]:
    """A SessionToolService from THIS checkout on a fresh app id, run on a
    GLib loop in a daemon thread: the running Collins instance may predate
    the `_greet` change, and the socket probe has to prove the new code."""
    import threading

    sys.path.insert(0, REPO_ROOT)
    from gi.repository import GLib

    from collins import mcpserver, mcptools

    app_id = f"com.episode6.Collins.Spike.{os.getpid()}"
    tool = {
        "name": "spike",
        "description": "Reports the pid Collins sees for the calling session.",
        "inputSchema": {"type": "object", "properties": {}},
    }
    service = mcpserver.SessionToolService(
        mcptools.socket_path(app_id),
        list_tools=lambda: [tool],
        # A real dispatch would walk /proc from this pid to a tab; here the
        # pid itself is the evidence (it must be a host pid, not the
        # sandbox's).
        dispatch=lambda pid, tool, args: (True, f"host pid {pid}, cwd {proctree_cwd(pid)}"),
    )
    service.start()
    loop = GLib.MainLoop()
    threading.Thread(target=loop.run, daemon=True).start()
    # The CLI's --mcp-config for this id (under the runtime dir for a
    # generated id; the plan binds that directory read-only).
    mcptools.write_config(app_id)
    config_dir = os.path.dirname(mcptools.config_path(app_id))
    return app_id, mcptools.socket_path(app_id), config_dir


def proctree_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError as e:
        return f"unreadable ({e.strerror})"


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--inside":
        return inside(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--print-plan", action="store_true")
    ap.add_argument("--workspace", default=os.getcwd())
    ap.add_argument("--home", default=os.path.expanduser("~/.cache/collins/spike-sandbox/home"))
    ap.add_argument("--app-id")
    ap.add_argument(
        "--service",
        action="store_true",
        help="probe a SessionToolService from this checkout instead of a running Collins",
    )
    ap.add_argument("--share-gh", action="store_true")
    ap.add_argument("--share-ssh", action="store_true")
    ap.add_argument("--unprotect-settings", action="store_true")
    ap.add_argument("claude_args", nargs="*")
    opts = ap.parse_args()
    if not (opts.check or opts.run or opts.print_plan):
        ap.error("one of --check, --run, --print-plan")

    if opts.service:
        app_id, sock, config_dir = own_service()
    else:
        app_id, sock, config_dir = live_instance(opts.app_id)
    seed_home(opts.home)
    mirror_trust(opts.home, os.path.realpath(opts.workspace))
    plan = build_plan(opts, sock, config_dir)
    for note in plan.notes:
        print(f"note: {note}", file=sys.stderr)
    print(f"note: Collins instance: {app_id or 'none running'}", file=sys.stderr)

    if opts.print_plan:
        print("\n".join(plan.args))
        return 0

    if opts.check:
        t0 = time.monotonic()
        subprocess.run(bwrap_command(plan, ["/bin/true"]), check=False)
        print(f"INFO startup overhead: bwrap+true in {(time.monotonic() - t0) * 1000:.0f} ms")
        argv = [
            sys.executable,
            os.path.abspath(__file__),
            "--inside",
            sock or "",
            os.path.realpath(opts.workspace),
            "0" if opts.unprotect_settings else "1",
            "1" if opts.share_ssh else "0",
        ]
        return subprocess.run(bwrap_command(plan, argv), check=False).returncode

    if not sock:
        print("no running Collins instance: launching without --mcp-config", file=sys.stderr)
    argv = ["claude", "--permission-mode", "bypassPermissions"]
    if sock and app_id:
        argv += ["--mcp-config", os.path.join(config_dir, "mcp.json")]
    argv += opts.claude_args
    cmd = bwrap_command(plan, argv)
    print("run:", " ".join(argv), file=sys.stderr)
    # A child rather than an exec: with --service the socket lives in this
    # process, and it has to outlive the claude inside.
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
