# New in the ghackett fork of agent-session-manager (GPL-3.0).
#
# The mount tables, the ordering rule, the carried() test for masks, the
# protect-check and the enclosing-repository rule are ported from aibox
# (https://github.com/EricKuck/dotfiles, packages/aibox/src/plan.rs and
# src/repo.rs), which is licensed as follows:
#
#   MIT License
#
#   Copyright (c) 2024-2026 Eric Kuck
#
#   Permission is hereby granted, free of charge, to any person obtaining a
#   copy of this software and associated documentation files (the
#   "Software"), to deal in the Software without restriction, including
#   without limitation the rights to use, copy, modify, merge, publish,
#   distribute, sublicense, and/or sell copies of the Software, and to
#   permit persons to whom the Software is furnished to do so, subject to
#   the following conditions:
#
#   The above copyright notice and this permission notice shall be included
#   in all copies or substantial portions of the Software.
#
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
#   OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
#   MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
#   IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
#   CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
#   TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
#   SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# The Collins-specific additions (the CLI's own directory, the interpreter
# prefix, the shim's package, the MCP config and socket, the sandbox home,
# the grants, the shares and the settings-file protection) are this fork's.

"""The mount plan for a sandboxed session: what bubblewrap is told to bind
where, as pure path arithmetic (see ~/specs/collins/sandboxed-sessions.md).

A sandboxed session runs `claude` inside a bubblewrap box: the workspace is
read-write, the CLI's own config (`~/.claude`) and the toolchain caches are
shared, the system is read-only, and credentials and every other checkout
on the machine are absent. `$HOME` inside the box is Collins' own *sandbox
home* (a real directory, not a tmpfs: tools persist to `$HOME` by renaming
a sibling over the file, and rename(2) onto a bind mount fails with
EBUSY), seeded once with `~/.claude.json` and diverging from the host copy
from then on.

Everything here is data about paths: the tables, the ordering rule (a mount
lands on top of the one carrying its parent; masks last, so a deeper
mount carves a secret back out of a shared tree), the `carried()` test that
decides which masks to emit (bwrap would *create* a destination it was told
to cover, so only secrets a shared mount would actually reach are masked),
and the refusal to emit a plan that would carry Collins' own state into the
box. `build_plan` never touches the filesystem beyond stat calls;
`prepare_launch` is the one that creates directories, seeds the home,
mirrors folder trust and writes the plan file the launch reads.

The plan is a JSON document rather than a line-per-argument file: it carries
the bwrap arguments, the environment scrub, the workspace and the inputs it
was generated from, so a later surface can say what is inside the box
without re-deriving it. Every path in it is absolute and validated
(bounded, no newlines, no `.`/`..` segments). It is regenerated at every
launch from current state and never reused from disk: a cache of state,
not state.

GTK-free, like the rest of the data layer, so the unit suite holds it to
the same cases aibox's tests hold `plan.rs` to.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import mcptools, sessions, trust

log = logging.getLogger(__name__)

# The plan document's shape, bumped when a reader would misread an older one.
PLAN_VERSION = 1

# Longest path the plan will carry; the kernel's PATH_MAX.
MAX_PATH = 4096

# -- the tables (aibox's, with the notes below on what was added) -------------

# Shared from the real home read-only: identity and shell startup files.
RO_HOME: tuple[str, ...] = (
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
)

# Shared from the real home read-write when present: the CLI's own state and
# the toolchain caches a build needs.
RW_HOME: tuple[str, ...] = (
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
)

# Shared read-write and *created* when absent: a bind needs a source, and a
# package manager inside the box that finds no cache directory would create
# one in the sandbox home instead — filling it twice, once per home.
RW_HOME_ALWAYS: tuple[str, ...] = (
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
)

# Secrets, masked with a deeper mount wherever a shared tree would reach
# them: /dev/null over a file, an empty tmpfs over a directory. Also what a
# grant is refused for (see guard_sensitive).
SENSITIVE_HOME: tuple[str, ...] = (
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
)

# The system, read-only. /run and /var/run are deliberately absent (docker
# and systemd sockets live there); only the entries resolvers need.
RO_ABSOLUTE: tuple[str, ...] = (
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
)

# Shared read-write: scratch space the box and the host both use.
RW_ABSOLUTE: tuple[str, ...] = ("/tmp", "/var/tmp")

# Environment the box must not inherit: where the host's agents listen.
SCRUB_ENV: tuple[str, ...] = ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO")

# The two files under ~/.claude an agent inside could plant a hook in that
# runs in the user's next *unsandboxed* session. Bound read-only over
# themselves (which also pins the inode, so a rename-over fails with
# EBUSY) unless the user's switch says otherwise. A symlinked one cannot be
# pinned (the link itself stays replaceable, and bwrap refuses the
# destination), so the plan is refused rather than built unprotected.
PROTECTED_SETTINGS: tuple[str, ...] = (".claude/settings.json", ".claude/settings.local.json")

# Directories under ~/.claude that carry code the host's next session runs
# (plugin hooks), pinned read-only on the same switch when they exist as
# real directories. The CLI's plugin installs fail inside, like its
# self-update; both come from the host.
PROTECTED_CLAUDE_DIRS: tuple[str, ...] = (".claude/plugins",)


class PlanRefused(Exception):
    """A plan that must not be emitted: it would carry a protected path into
    the box, or its workspace is nothing a box should be built on."""


# -- paths ---------------------------------------------------------------------


def valid_path(path: object) -> bool:
    """Whether *path* is a path the plan may carry: an absolute string of
    bounded length with no control characters and no `.` or `..` segments.
    Everything the plan holds passes through here, whatever wrote it."""
    if not isinstance(path, str) or not path or len(path) > MAX_PATH:
        return False
    if not path.startswith("/"):
        return False
    if any(ord(ch) < 32 or ch == "\x7f" for ch in path):
        return False
    return all(part not in (".", "..") for part in path.split("/"))


def _within(parent: str, path: str) -> bool:
    """Whether *path* is *parent* or lies under it (string arithmetic on
    normalised absolute paths; no symlink resolution here)."""
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def _under_usr(path: str) -> bool:
    """A path the system's read-only bind already covers."""
    return _within("/usr", path)


# -- the enclosing repository (a port of repo.rs) -------------------------------


def repo_root(workspace: str) -> str | None:
    """The nearest ancestor holding a `.git` (the workspace included), or
    None outside any repository. `.git` may be a file: a linked worktree
    is a repository root of its own for this purpose."""
    current = Path(workspace)
    while True:
        if (current / ".git").exists():
            return str(current)
        if current.parent == current:
            return None
        current = current.parent


def worktree_common_git(git_file: Path) -> str | None:
    """The common `.git` directory a linked worktree's `.git` *file* points
    at (`gitdir: <repo>/.git/worktrees/<name>`), or None when the file is
    not that shape. Bound too, or git inside sees a worktree with no
    object store."""
    try:
        first = git_file.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not first.startswith("gitdir:"):
        return None
    target = first[len("gitdir:") :].strip()
    if not target:
        return None
    gitdir = Path(target)
    if not gitdir.is_absolute():
        gitdir = git_file.parent / gitdir
    gitdir = Path(os.path.normpath(gitdir))
    while gitdir.name != ".git":
        if gitdir.parent == gitdir:
            return None
        gitdir = gitdir.parent
    common = str(gitdir)
    return common if valid_path(common) else None


# -- the plan ---------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    """Everything a plan is a function of. Assembled by `gather_inputs` for
    a launch, by hand in tests."""

    workspace: str
    home: str
    sandbox_home: str
    runtime_dir: str | None = None
    tmpdir: str | None = None
    claude_dir: str | None = None  # the resolved CLI's directory, read-only
    claude_launcher: str | None = None  # the `claude` on PATH, pinned when a real file
    prefix: str | None = None  # sys.prefix, when not under /usr
    package_parent: str | None = None  # where the shim imports collins from
    config_file: str | None = None  # the --mcp-config file itself, read-only
    socket_file: str | None = None  # the MCP socket, read-write
    grants: tuple[str, ...] = ()
    share_gh: bool = False
    share_ssh: bool = False
    ssh_auth_sock: str | None = None
    docker_host: str | None = None
    protect_settings: bool = True
    protected: tuple[str, ...] = ()  # absolute paths that must stay outside
    notes: tuple[str, ...] = ()


@dataclass
class Plan:
    """The bwrap arguments as they accumulate, plus the host paths they
    carry in (what `carried` consults) and notes for the footer."""

    args: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

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
        """Whether a mount already in the plan reaches *path* — a source
        that is a strict ancestor of it (an exact source is the mount
        itself, not something reached through one). *within* narrows the
        sources considered to those under that tree."""
        for src in self.sources:
            if within is not None and not _within(within, src):
                continue
            if path != src and _within(src, path):
                return True
        return False


def _docker_socket(docker_host: str | None) -> str | None:
    """The unix socket a DOCKER_HOST names, or None for a TCP one / none."""
    if not docker_host:
        return None
    if docker_host.startswith("unix://"):
        path = docker_host[len("unix://") :]
    elif docker_host.startswith("/"):
        path = docker_host
    else:
        return None
    return path if valid_path(path) else None


def build_plan(inputs: Inputs) -> dict:
    """The plan document for *inputs* (see the module docstring for its
    shape). Raises PlanRefused rather than emit a box that would carry a
    protected path, or one built on `$HOME` or `/`."""
    home = inputs.home.rstrip("/") or "/"
    ws = inputs.workspace
    sandbox_home = inputs.sandbox_home
    for name, path in (("workspace", ws), ("home", home), ("sandbox home", sandbox_home)):
        if not valid_path(path):
            raise PlanRefused(f"{name} is not a usable path: {path!r}")
    if _within(ws, sandbox_home) or _within(sandbox_home, ws):
        raise PlanRefused("the sandbox home and the workspace must not nest")
    # The workspace is a read-write bind like any grant, and is held to the
    # same rule: never a secret, an ancestor of one, or $HOME and above.
    protected = tuple(p for p in inputs.protected if valid_path(p))
    reason = guard_path(ws, home, protected, sandbox_home)
    if reason:
        raise PlanRefused(f"refusing to build a sandbox on {ws}: {reason}")

    plan = Plan()
    plan.notes.extend(inputs.notes)
    plan.args += ["--unshare-user", "--unshare-pid", "--die-with-parent"]
    # $HOME first: everything below stacks onto the overlay.
    plan.rw(sandbox_home, home, required=True)
    for path in RO_ABSOLUTE:
        plan.ro(path)
    for path in RW_ABSOLUTE:
        plan.rw(path)
    if inputs.runtime_dir and valid_path(inputs.runtime_dir):
        # Where ssh-agent, gpg-agent and the keyring listen: an empty tmpfs.
        plan.tmpfs(inputs.runtime_dir)
    if inputs.tmpdir and valid_path(inputs.tmpdir) and inputs.tmpdir not in RW_ABSOLUTE:
        plan.rw(inputs.tmpdir)
    for rel in RO_HOME:
        plan.ro(os.path.join(home, rel))
    for rel in RW_HOME:
        plan.rw(os.path.join(home, rel))
    for rel in RW_HOME_ALWAYS:
        plan.rw(os.path.join(home, rel))

    # Collins' own pieces the session needs, *before* the workspace: a
    # workspace that overlaps one of these (a checkout of Collins itself,
    # under ./start-debug) has to land on top and stay read-write.
    # Only the mcp.json file, never its directory: for a generated app id
    # the directory is the runtime dir, which holds the plan files.
    for path in (inputs.claude_dir, inputs.prefix, inputs.package_parent, inputs.config_file):
        if path and valid_path(path) and not _under_usr(path):
            plan.ro(path)
    if inputs.socket_file and valid_path(inputs.socket_file):
        # After the runtime dir's tmpfs, so it lands on top. Read-write:
        # connect(2) needs write permission on the socket inode. Only the
        # socket, not its directory — the plan files live beside it.
        plan.rw(inputs.socket_file, required=True)

    # The workspace, and the enclosing repository's two shared directories
    # (only those two, so a nested workspace never exposes the parent's
    # working tree). A linked worktree's common git dir comes along.
    plan.rw(ws, required=True)
    root = repo_root(ws)
    if root and valid_path(root):
        for name in (".git", ".claude"):
            plan.rw(os.path.join(root, name))
        git = Path(root) / ".git"
        if git.is_file():
            common = worktree_common_git(git)
            if common:
                plan.rw(common)
                plan.notes.append(f"linked worktree: common git dir {common}")

    # Grants: the user's per-workspace additions, each re-checked here —
    # state.json is a file on disk like any other — in both spellings (as
    # written and resolved), since a symlinked ~/.ssh is still ~/.ssh.
    for grant in inputs.grants:
        reason = guard_path(grant, home, protected, sandbox_home)
        if reason:
            plan.notes.append(f"grant {grant} skipped: {reason}")
            continue
        plan.rw(grant)

    # The two optional shares.
    if inputs.share_gh:
        # Read-only: hosts.yml carries the account and git_protocol; the
        # token itself travels as GH_TOKEN (see sandboxrun), since on a
        # desktop gh keeps it in the keyring, which the box cannot reach.
        plan.ro(os.path.join(home, ".config", "gh"))
        plan.notes.append("sharing the GitHub CLI login")
    ssh_sock = inputs.ssh_auth_sock if valid_path(inputs.ssh_auth_sock) else None
    if inputs.share_ssh and ssh_sock:
        # The socket file alone, after the runtime dir's tmpfs, like the MCP
        # socket: its directory may hold the keyring's other sockets.
        plan.rw(ssh_sock)
        plan.notes.append(f"sharing the SSH agent socket {ssh_sock}")

    # Masks last: a deeper, later mount carves a secret back out. Only
    # secrets that exist — bwrap would create the destination it was told
    # to cover — and only where a shared mount would reach them.
    for rel in SENSITIVE_HOME:
        if inputs.share_gh and rel == ".config/gh":
            continue
        full = os.path.join(home, rel)
        if not plan.carried(full, home) or not os.path.lexists(full):
            continue
        if os.path.isdir(full) and not os.path.islink(full):
            plan.tmpfs(full)
        else:
            plan.mask_file(full)
    if ssh_sock and not inputs.share_ssh and plan.carried(ssh_sock, None):
        plan.mask_file(ssh_sock)
    docker_sock = _docker_socket(inputs.docker_host)
    if docker_sock and plan.carried(docker_sock, None):
        plan.mask_file(docker_sock)
    if inputs.protect_settings:
        for rel in PROTECTED_SETTINGS:
            full = os.path.join(home, rel)
            if os.path.islink(full):
                raise PlanRefused(
                    f"~/{rel} is a symlink and cannot be protected inside a sandbox"
                )
            if os.path.isfile(full):
                plan.ro(full, required=True)
        for rel in PROTECTED_CLAUDE_DIRS:
            full = os.path.join(home, rel)
            if os.path.isdir(full) and not os.path.islink(full):
                plan.ro(full, required=True)
        # The `claude` the host's next session runs: pinned when it is a
        # real file in a shared tree. The native installer's launcher is a
        # symlink in ~/.local/bin (shared read-write), which no bind can
        # pin — noted, and stated in the docs.
        launcher = inputs.claude_launcher
        if launcher and valid_path(launcher) and plan.carried(launcher, None):
            if os.path.islink(launcher):
                plan.notes.append(f"claude launcher {launcher} is a symlink: not pinned")
            elif os.path.isfile(launcher):
                plan.ro(launcher, required=True)

    # The protect-check: nothing of Collins' own may be reachable inside,
    # whatever carried it — the workspace, a grant, a share.
    for path in protected:
        for src in plan.sources:
            if _within(src, path):
                raise PlanRefused(f"{path} must stay outside the sandbox, but {src} carries it")

    plan.args += ["--proc", "/proc", "--dev", "/dev"]
    for dev in ("/dev/kvm", "/dev/bus/usb"):
        plan.args += ["--dev-bind-try", dev, dev]
    plan.args += ["--chdir", ws]

    unsetenv = [
        var
        for var in SCRUB_ENV
        # --share-ssh binds the agent's directory in; the variables that
        # point at it have to survive for ssh to find it.
        if not (inputs.share_ssh and ssh_sock and var in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"))
    ]
    if docker_sock:
        unsetenv.append("DOCKER_HOST")

    return {
        "version": PLAN_VERSION,
        "bwrap_args": plan.args,
        "unsetenv": unsetenv,
        "setenv": {"HOME": home},
        # sandboxrun reads the token off the host with `gh auth token` and
        # hands it in as GH_TOKEN over the args fd: never on disk, never
        # on the typed command line.
        "gh_token": bool(inputs.share_gh),
        "workspace": ws,
        "inputs": {
            "workspace": ws,
            "sandbox_home": sandbox_home,
            "claude_dir": inputs.claude_dir,
            "socket_file": inputs.socket_file,
            "config_file": inputs.config_file,
            "grants": [g for g in inputs.grants if g in plan.sources],
            "share_gh": bool(inputs.share_gh),
            "share_ssh": bool(inputs.share_ssh and ssh_sock),
            "protect_settings": bool(inputs.protect_settings),
        },
        "notes": plan.notes,
    }


def guard_sensitive(
    path: str, home: str, protected: tuple[str, ...] = (), sandbox_home: str | None = None
) -> str:
    """Why *path* may not be granted (bound read-write into the box), or ""
    when it may. Refused: anything that is, lies inside, or is an ancestor
    of a secret (SENSITIVE_HOME), of Collins' own state (*protected*), of
    the sandbox home, or that is `$HOME` or `/` themselves — a mount
    namespace has no rule that denies a path back out of a bind, so an
    ancestor of a secret is the secret."""
    if not valid_path(path):
        return "not an absolute path"
    home = home.rstrip("/") or "/"
    if path == "/":
        return "the whole filesystem"
    if path == home:
        return "the home directory itself"
    if _within(path, home):
        return "an ancestor of the home directory"
    for rel in SENSITIVE_HOME:
        secret = os.path.join(home, rel)
        if _within(path, secret) or _within(secret, path):
            return f"reaches {secret}"
    for kept in protected:
        if _within(path, kept) or _within(kept, path):
            return f"reaches {kept}"
    if sandbox_home and (_within(path, sandbox_home) or _within(sandbox_home, path)):
        return "reaches the sandbox home"
    return ""


def guard_path(
    path: str, home: str, protected: tuple[str, ...] = (), sandbox_home: str | None = None
) -> str:
    """guard_sensitive over every spelling of *path*: as written and
    resolved, against the home, the protected paths and the sandbox home
    both as written and resolved, and against the resolved target of each
    secret that is itself a symlink. A grant of `~/.ssh` that points into a
    dotfiles checkout, or a home under a symlinked `/home`, resolves to a
    string the plain arithmetic would not recognise — and bwrap binds the
    resolved directory."""
    reason = guard_sensitive(path, home, protected, sandbox_home)
    if reason or not valid_path(path):
        return reason
    home = home.rstrip("/") or "/"
    real = os.path.realpath(path)
    real_home = os.path.realpath(home)
    real_protected = tuple(os.path.realpath(p) for p in protected)
    real_sandbox = os.path.realpath(sandbox_home) if sandbox_home else None
    for candidate in {path, real}:
        for h, kept, sbx in ((home, protected, sandbox_home), (real_home, real_protected, real_sandbox)):
            reason = guard_sensitive(candidate, h, kept, sbx)
            if reason:
                return reason
        for rel in SENSITIVE_HOME:
            secret = os.path.join(home, rel)
            if not os.path.islink(secret):
                continue
            target = os.path.realpath(secret)
            if _within(candidate, target) or _within(target, candidate):
                return f"reaches {secret}"
    return ""


# -- the host side of a launch ---------------------------------------------------


def sandbox_home_dir() -> str:
    """Where `$HOME` inside the box lives: COLLINS_SANDBOX_HOME, else
    `$XDG_DATA_HOME/collins/sandbox-home`. Shared by every sandboxed
    session, like aibox's."""
    override = os.environ.get("COLLINS_SANDBOX_HOME")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "collins", "sandbox-home")


def plan_dir(app_id: str) -> str:
    """Where this instance's per-launch plan files go: beside the socket,
    under the runtime dir, so they die with the boot and never enter the
    box (only the socket file is bound)."""
    return os.path.join(mcptools.runtime_dir(app_id), "sandbox")


def protected_paths(app_id: str) -> tuple[str, ...]:
    """Collins' own state on this machine, as absolute paths: the config,
    state and cache directories (XDG overrides honoured, so a test's
    scratch tree is protected the same way) and the plan directory."""
    home = str(Path.home())
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(home, ".local", "state")
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    paths = [
        os.path.join(config, "collins"),
        os.path.join(state, "collins"),
        os.path.join(cache, "collins"),
        plan_dir(app_id),
    ]
    return tuple(os.path.abspath(p) for p in paths)


def resolved_claude() -> tuple[str, str] | None:
    """(the file `claude` resolves to, the directory to bind read-only), or
    None with no CLI on PATH. The native installer's launcher is a symlink
    into `~/.local/share/claude/versions/<v>`: the whole `~/.local/share/
    claude` is bound so the launcher's sibling files resolve. `~/.claude/
    local` and an npm prefix bind their own directory."""
    found = shutil.which("claude")
    if not found:
        return None
    real = os.path.realpath(found)
    native = os.path.join(str(Path.home()), ".local", "share", "claude")
    if _within(native, real):
        return real, native
    return real, os.path.dirname(real)


def gather_inputs(workspace: str, app_id: str, state) -> Inputs:
    """The inputs for a launch in *workspace*: the environment, the resolved
    CLI, Collins' own paths, and the grants and switches from *state*."""
    ws = os.path.realpath(workspace)
    home = str(Path.home())
    notes: list[str] = []
    cli = resolved_claude()
    claude_dir = None
    if cli:
        claude_dir = cli[1]
        notes.append(f"claude resolves to {cli[0]}")
    else:
        notes.append("claude not found on PATH")
    prefix = None if _under_usr(sys.prefix) else sys.prefix
    package_parent = mcptools.package_parent()
    config_file = mcptools.config_path(app_id)
    if not os.path.isfile(config_file):
        config_file = None
    socket_file = mcptools.socket_path(app_id)
    if not os.path.exists(socket_file):
        socket_file = None
    # As written, not resolved: the guard checks both spellings itself, and
    # a normalised path is what the mask loop can match.
    grants = tuple(
        os.path.normpath(g) for g in state.get_sandbox_grants(ws) if isinstance(g, str) and g
    )
    launcher = shutil.which("claude")
    return Inputs(
        workspace=ws,
        home=home,
        sandbox_home=sandbox_home_dir(),
        runtime_dir=os.environ.get("XDG_RUNTIME_DIR") or None,
        tmpdir=os.environ.get("TMPDIR") or None,
        claude_dir=claude_dir,
        claude_launcher=os.path.abspath(launcher) if launcher else None,
        prefix=prefix,
        package_parent=None if _under_usr(package_parent) else package_parent,
        config_file=config_file,
        socket_file=socket_file,
        grants=grants,
        share_gh=bool(state.get_setting("sandbox_share_gh")),
        share_ssh=bool(state.get_setting("sandbox_share_ssh")),
        ssh_auth_sock=os.environ.get("SSH_AUTH_SOCK") or None,
        docker_host=os.environ.get("DOCKER_HOST") or None,
        protect_settings=not bool(state.get_setting("sandbox_settings_editable")),
        protected=protected_paths(app_id),
        notes=tuple(notes),
    )


# Everything under the sandbox home is written by the agent inside the box
# (it is $HOME there, shared by every sandboxed session), so a path in it is
# attacker-controlled: a planted symlink would turn Collins' own write into
# a write to any host file. Every write here creates its file O_EXCL |
# O_NOFOLLOW under a fresh name and renames it over a destination that has
# been lstat'ed as a plain file (or is absent); anything else is refused.


def _write_private(path: str, data: str) -> None:
    """Create *path* (which must not exist) mode 0600 with *data*."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
    except BaseException:
        release_plan(path)
        raise


def _replace_private(dest: str, data: str) -> bool:
    """Atomically put *data* at *dest*, which must be absent or a regular
    file (never a symlink, whose target could be anywhere). False when it
    is anything else or the write fails."""
    try:
        st = os.lstat(dest)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    else:
        if not stat.S_ISREG(st.st_mode):
            log.warning("sandbox: %s is not a regular file; refusing to write it", dest)
            return False
    tmp = os.path.join(os.path.dirname(dest), f".{uuid.uuid4().hex}.tmp")
    try:
        _write_private(tmp, data)
        os.replace(tmp, dest)
    except OSError:
        release_plan(tmp)
        return False
    return True


def seed_home(sandbox_home: str, host_config: str | os.PathLike | None = None) -> None:
    """Create the sandbox home (mode 0700) and seed it with the host's
    `~/.claude.json` once — the CLI's onboarding flags, MCP approvals and
    per-project state start from the user's and diverge from there. A
    planted symlink where the copy would go is never followed."""
    os.makedirs(sandbox_home, mode=0o700, exist_ok=True)
    os.chmod(sandbox_home, 0o700)
    dest = os.path.join(sandbox_home, ".claude.json")
    src = str(host_config if host_config is not None else sessions.CLAUDE_CONFIG)
    if os.path.lexists(dest) or not os.path.isfile(src):
        return
    with open(src, encoding="utf-8") as fh:
        data = fh.read()
    _replace_private(dest, data)


def mirror_trust(
    sandbox_home: str, workspace: str, host_config: str | os.PathLike | None = None
) -> bool:
    """Copy the host's folder-trust answer for *workspace* into the sandbox
    copy of `~/.claude.json` — `hasTrustDialogAccepted` for the workspace
    and any ancestor the host trusts (the CLI honours ancestors, and trust
    is usually recorded on the project root), and nothing else. The one
    write Collins makes to a file the CLI would not have written itself;
    it mirrors trust.py's deliberate write into a file Collins owns.
    Returns whether the copy was written."""
    host = str(host_config if host_config is not None else sessions.CLAUDE_CONFIG)
    dest = os.path.join(sandbox_home, ".claude.json")
    try:
        if not stat.S_ISREG(os.lstat(dest).st_mode):
            log.warning("sandbox: %s is not a regular file; not mirroring trust", dest)
            return False
        with open(host, encoding="utf-8") as fh:
            host_data = json.load(fh)
        with open(dest, encoding="utf-8") as fh:
            box_data = json.load(fh)
    except (OSError, ValueError):
        return False
    if not isinstance(host_data, dict) or not isinstance(box_data, dict):
        return False
    host_projects = host_data.get("projects")
    if not isinstance(host_projects, dict):
        return False
    trusted = [
        key
        for key in trust.ancestors(workspace)
        if isinstance(host_projects.get(key), dict)
        and host_projects[key].get("hasTrustDialogAccepted") is True
    ]
    if not trusted:
        return False
    projects = box_data.setdefault("projects", {})
    if not isinstance(projects, dict):
        return False
    changed = False
    for key in trusted:
        entry = projects.setdefault(key, {})
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is not True:
            entry["hasTrustDialogAccepted"] = True
            changed = True
    if not changed:
        return True
    return _replace_private(dest, json.dumps(box_data, indent=2))


def write_plan(plan: dict, directory: str) -> str:
    """Write *plan* to a fresh `<directory>/<uuid>.json`, mode 0600, and
    return its path."""
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, f"{uuid.uuid4()}.json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=1)
    return path


def release_plan(path: str | None) -> None:
    """Unlink a plan file a launch is done with. Best-effort."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def sweep_plans(app_id: str) -> int:
    """Unlink every plan file left in this instance's plan directory — a
    tab destroyed before its shell's exit was observed (a quit, a force
    close) never released its plan. All of them are this app id's, and
    bwrap reads a plan once at exec, so nothing running misses it. Returns
    how many went."""
    directory = plan_dir(app_id)
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    swept = 0
    for name in names:
        if name.endswith(".json"):
            path = os.path.join(directory, name)
            try:
                os.unlink(path)
                swept += 1
            except OSError:
                pass
    return swept


def prepare_launch(workspace: str, app_id: str, state) -> str | None:
    """Everything a sandboxed launch in *workspace* needs on the host, then
    the plan file's path — or None when no box can be built (bubblewrap
    missing, a refused plan, an unwritable directory), which the caller
    turns into an unsandboxed launch with a message. Creates the
    RW_HOME_ALWAYS directories, seeds and secures the sandbox home, and
    mirrors folder trust into its `~/.claude.json`."""
    if not available():
        return None
    try:
        inputs = gather_inputs(workspace, app_id, state)
        for rel in RW_HOME_ALWAYS:
            os.makedirs(os.path.join(inputs.home, rel), exist_ok=True)
        seed_home(inputs.sandbox_home)
        mirror_trust(inputs.sandbox_home, inputs.workspace)
        plan = build_plan(inputs)
        return write_plan(plan, plan_dir(app_id))
    except PlanRefused as err:
        log.warning("sandbox: refusing to build a box for %s: %s", workspace, err)
    except OSError as err:
        log.warning("sandbox: could not prepare a box for %s: %s", workspace, err)
    return None


# -- is a box possible here? -------------------------------------------------------

# The probe's verdicts (probe_reason): "" means a box can be built.
REASON_NO_BWRAP = "bubblewrap not installed"
REASON_NO_USERNS = "user namespaces are restricted on this system"

_probe_lock = threading.Lock()
_probe_result: str | None = None  # None until probed; then a reason or ""


def bwrap_path() -> str | None:
    """The bubblewrap executable: COLLINS_BWRAP (a fake for tests and e2e
    checks), else `bwrap` on PATH."""
    override = os.environ.get("COLLINS_BWRAP")
    if override:
        return override if os.access(override, os.X_OK) else None
    return shutil.which("bwrap")


def probe() -> str:
    """Run the probe now (a bwrap that unshares user and pid namespaces
    around `/bin/true`) and cache its verdict: "" when a box can be built,
    else the reason. Blocking — a few tens of milliseconds — so the app
    runs it on a thread once per launch (probe_async)."""
    global _probe_result
    bwrap = bwrap_path()
    if not bwrap:
        verdict = REASON_NO_BWRAP
    else:
        argv = [
            bwrap,
            "--unshare-user",
            "--unshare-pid",
            "--die-with-parent",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind-try",
            "/bin",
            "/bin",
            "--ro-bind-try",
            "/lib",
            "/lib",
            "--ro-bind-try",
            "/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--",
            "/bin/true",
        ]
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
            verdict = "" if result.returncode == 0 else REASON_NO_USERNS
            if verdict:
                log.info("sandbox probe failed: %s", result.stderr.decode(errors="replace")[:200])
        except (OSError, subprocess.SubprocessError) as err:
            log.info("sandbox probe failed to run: %s", err)
            verdict = REASON_NO_USERNS
    with _probe_lock:
        _probe_result = verdict
    return verdict


def probe_async(then=None) -> None:
    """Run the probe on a daemon thread; *then(reason)* is called on that
    thread when it lands (land it on the main loop yourself)."""

    def work() -> None:
        reason = probe()
        if then is not None:
            then(reason)

    threading.Thread(target=work, name="sandbox-probe", daemon=True).start()


def probe_reason() -> str | None:
    """The cached verdict: None before the probe ran, "" when a box can be
    built, else the reason (REASON_NO_BWRAP / REASON_NO_USERNS)."""
    with _probe_lock:
        return _probe_result


def available() -> bool:
    """Whether sandboxed sessions can be launched here. Probes synchronously
    the first time when nothing has asked yet (a launch before the thread
    landed), so an answer is always real."""
    reason = probe_reason()
    if reason is None:
        reason = probe()
    return reason == ""


def reset_probe() -> None:
    """Forget the cached verdict (tests, and a Preferences refresh)."""
    global _probe_result
    with _probe_lock:
        _probe_result = None
