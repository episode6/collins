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
on the machine are absent. `$HOME` inside the box is a *sandbox home* of
the session's own (a real directory, not a tmpfs: tools persist to `$HOME`
by renaming a sibling over the file, and rename(2) onto a bind mount fails
with EBUSY), seeded once with `~/.claude.json` and diverging from the host
copy from then on. Every sandboxed session has a *box* — a directory under
the sandbox root named by a box id, holding that home, the carrier
directory live grants mount in and the anchors that stand in for the
machine's other top-level directories — kept across its resumes and
removed when its transcript goes.

Everything here is data about paths: the tables, the ordering rule (a mount
lands on top of the one carrying its parent; masks last, so a deeper
mount carves a secret back out of a shared tree), the `carried()` test that
decides which masks to emit (bwrap would *create* a destination it was told
to cover, so only secrets a shared mount would actually reach are masked),
and the refusal to emit a plan that would carry Collins' own state into the
box. `build_plan` never touches the filesystem beyond stat calls;
`prepare_launch` is the one that creates directories, seeds the home,
mirrors folder trust, clears what stands in a mount's way in the box's own
home (`scrub_home`) and writes the plan file the launch reads.

Everything under a box's `home/` was written by the agent: every write and
every removal there goes by file descriptor with O_NOFOLLOW, and a symlink
is unlinked or replaced, never followed. `remove_box` is the one place a
tree is deleted; it removes nothing but `<root>/<32 hex>`, refuses a box
with a mount under it, and stops at another device.

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
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from . import mcptools, sandboxrun, sessions, trust

log = logging.getLogger(__name__)

# The plan document's shape, bumped when a reader would misread an older one.
# 2: a box of the session's own (its id, the carrier, the anchors, the
# grants key) recorded in `inputs`.
PLAN_VERSION = 2

# Longest path the plan will carry; the kernel's PATH_MAX.
MAX_PATH = 4096

# Where a box's carrier directory is inside it: read-only there, and what a
# live grant's mount propagates into.
CARRIER_DEST = "/run/collins/grants"

# Top-level directories that never get an anchor: the system's own (bound
# read-only, or deliberately absent), the scratch space every box shares,
# and what holds the home.
NEVER_ANCHORED: tuple[str, ...] = (
    "bin",
    "boot",
    "dev",
    "etc",
    "home",
    "lib",
    "lib32",
    "lib64",
    "libx32",
    "lost+found",
    "nix",
    "opt",
    "proc",
    "root",
    "run",
    "sbin",
    "snap",
    "sys",
    "tmp",
    "usr",
    "var",
)

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


def within(parent: str, path: str) -> bool:
    """Whether *path* is *parent* or lies under it (string arithmetic on
    normalised absolute paths; no symlink resolution here)."""
    return path == parent or path.startswith(parent.rstrip("/") + "/")


_within = within  # for the one method whose parameter bears the name


def _under_usr(path: str) -> bool:
    """A path the system's read-only bind already covers."""
    return within("/usr", path)


# -- the boxes -------------------------------------------------------------------
#
#   <sandbox root>/                  mode 0700
#     <box id>/                      mode 0700
#       home/                        $HOME inside the box
#       grants/                      the carrier: where a live grant mounts
#       anchors/<top>/               one per anchored top-level directory
#       lease                        while an instance holds the box

_BOX_ID_CHARS = frozenset("0123456789abcdef")


def sandbox_root() -> str:
    """Where every box lives: COLLINS_SANDBOX_ROOT, else
    `$XDG_DATA_HOME/collins/sandbox`. One root whatever the app id — the
    session → box map is in state.json, which every instance shares."""
    override = os.environ.get("COLLINS_SANDBOX_ROOT")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "collins", "sandbox")


def valid_box_id(box: object) -> bool:
    """Whether *box* is a box id: 32 lowercase hex characters, and nothing
    else — what `new_box_id` mints. The only names a box directory has, so
    the only names `remove_box` ever removes."""
    return isinstance(box, str) and len(box) == 32 and all(ch in _BOX_ID_CHARS for ch in box)


def new_box_id() -> str:
    return uuid.uuid4().hex


def box_dir(box: str) -> str:
    """`<root>/<box>`. Raises ValueError for anything that is not a box id."""
    if not valid_box_id(box):
        raise ValueError(f"not a sandbox box id: {box!r}")
    return os.path.join(sandbox_root(), box)


def box_home(box: str) -> str:
    """`$HOME` inside the box, on the host."""
    return os.path.join(box_dir(box), "home")


def box_carrier(box: str) -> str:
    """The directory bound at CARRIER_DEST inside the box."""
    return os.path.join(box_dir(box), "grants")


def box_anchor(box: str, top: str) -> str:
    """The empty host directory standing in for the top-level directory
    named *top* (`mnt` for `/mnt`) inside the box."""
    if not top or "/" in top or top in (".", "..") or "\0" in top:
        raise ValueError(f"not a top-level directory name: {top!r}")
    return os.path.join(box_dir(box), "anchors", top)


def anchor_roots(root: str = "/") -> tuple[str, ...]:
    """The machine's top-level directories a box gets an anchor for, sorted,
    as absolute paths: every entry of `/` that is a real directory (not a
    symlink) and not in NEVER_ANCHORED — `/mnt`, `/media`, `/srv`, a
    machine's own `/data`. Reads the filesystem, so `gather_inputs` calls
    it and `build_plan` does not."""
    try:
        names = os.listdir(root)
    except OSError:
        return ()
    found = []
    for name in sorted(names):
        if name in NEVER_ANCHORED:
            continue
        path = os.path.join(root, name)
        if not valid_path(path):
            continue
        try:
            mode = os.lstat(path).st_mode
        except OSError:
            continue
        if stat.S_ISDIR(mode):
            found.append(path)
    return tuple(found)


def project_key(workspace: str) -> str:
    """What a project's *default* grants are recorded under: the repository
    a Claude-managed worktree belongs to, else the workspace itself,
    resolved. A session in `<repo>/.claude/worktrees/<name>` has its
    repository's defaults. Only the defaults are keyed this way — a
    session's own grants are keyed by its box, and nothing goes from this
    key to a box."""
    real = os.path.realpath(workspace)
    return sessions.worktree_project_root(real) or real


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
    box: str = ""  # the box id
    sandbox_root: str = ""  # what a workspace or a grant must never reach
    carrier: str | None = None  # host directory, bound at CARRIER_DEST
    anchors: tuple[tuple[str, str], ...] = ()  # (host directory, destination)


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

    def remount_ro(self, dest: str) -> None:
        """The topmost mount at *dest* made read-only, and only it: unlike
        `--ro-bind`, which is recursive, a mount that sits in the directory
        — or propagates into it later — stays read-write."""
        self.args += ["--remount-ro", dest]

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
    # The root of every box, which no box may be built on or reach into;
    # inputs assembled by hand without one are held to their own home.
    sandbox_root = inputs.sandbox_root or sandbox_home
    carrier = inputs.carrier
    for name, path in (
        ("workspace", ws),
        ("home", home),
        ("sandbox home", sandbox_home),
        ("sandbox root", sandbox_root),
    ):
        if not valid_path(path):
            raise PlanRefused(f"{name} is not a usable path: {path!r}")
    if carrier is not None and not valid_path(carrier):
        raise PlanRefused(f"the grant carrier is not a usable path: {carrier!r}")
    if inputs.box and not valid_box_id(inputs.box):
        raise PlanRefused(f"not a sandbox box id: {inputs.box!r}")
    if within(ws, sandbox_root) or within(sandbox_root, ws):
        raise PlanRefused("the sandbox homes and the workspace must not nest")
    # The workspace is a read-write bind like any grant, and is held to the
    # same rule: never a secret, an ancestor of one, or $HOME and above.
    protected = tuple(p for p in inputs.protected if valid_path(p))
    reason = guard_path(ws, home, protected, sandbox_root)
    if reason:
        raise PlanRefused(f"refusing to build a sandbox on {ws}: {reason}")

    root = repo_root(ws)
    if root is not None and not valid_path(root):
        root = None
    common = None
    if root and (Path(root) / ".git").is_file():
        common = worktree_common_git(Path(root) / ".git")
    anchors = _kept_anchors(inputs, home, root, common)

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
    # The anchors and the carrier: empty host directories of the box's own,
    # bound here and remounted read-only at the end of the plan, so a mount
    # the host makes in one later propagates into the running box and the
    # box itself can write none of them. `--bind` and a late `--remount-ro`,
    # never `--ro-bind`: that one is recursive, and a box launched while a
    # live grant sits in the carrier (a sandboxed panel shell) would get
    # the grant read-only.
    for host_dir, dest in anchors:
        plan.rw(host_dir, dest, required=True)
    if carrier is not None:
        plan.rw(carrier, CARRIER_DEST, required=True)
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
    if root:
        for name in (".git", ".claude"):
            plan.rw(os.path.join(root, name))
        if common:
            plan.rw(common)
            plan.notes.append(f"linked worktree: common git dir {common}")

    # Grants: what the user allowed this session, each re-checked here —
    # state.json is a file on disk like any other — in both spellings (as
    # written and resolved), since a symlinked ~/.ssh is still ~/.ssh.
    granted: list[str] = []
    for grant in inputs.grants:
        reason = guard_path(grant, home, protected, sandbox_root)
        if reason:
            plan.notes.append(f"grant {grant} skipped: {reason}")
            continue
        plan.rw(grant)
        granted.append(grant)

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
            if within(src, path):
                raise PlanRefused(f"{path} must stay outside the sandbox, but {src} carries it")

    # After the last mount: bwrap has made every mount point a static bind
    # under an anchor needs, and the remount acts on the topmost mount at
    # the path — the anchor or the carrier itself, never the workspace
    # (an anchor whose destination the plan binds was dropped above).
    if carrier is not None:
        plan.remount_ro(CARRIER_DEST)
    for _host_dir, dest in anchors:
        plan.remount_ro(dest)

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
            "grants": granted,
            "share_gh": bool(inputs.share_gh),
            "share_ssh": bool(inputs.share_ssh and ssh_sock),
            "protect_settings": bool(inputs.protect_settings),
            "box": inputs.box,
            "carrier": carrier,
            "anchors": [[host_dir, dest] for host_dir, dest in anchors],
        },
        "notes": plan.notes,
    }


def _kept_anchors(
    inputs: Inputs, home: str, root: str | None, common: str | None
) -> list[tuple[str, str]]:
    """The anchors of *inputs* the plan emits. One is dropped when its
    destination is itself a destination the plan binds after it (the
    workspace, a grant, the repository's `.git` or `.claude`, the common
    git dir, one of Collins' own pieces) — the remount at the end of the
    plan acts on the topmost mount at a path, and that must never be the
    workspace — and when it holds a destination the plan bound *before* it
    (a home outside `/home`, the runtime dir, `TMPDIR`), which it would
    cover."""
    taken = {inputs.workspace, *inputs.grants}
    for path in (
        inputs.claude_dir,
        inputs.prefix,
        inputs.package_parent,
        inputs.config_file,
        inputs.socket_file,
        inputs.ssh_auth_sock,
        common,
    ):
        if path:
            taken.add(path)
    if root:
        taken.update(os.path.join(root, name) for name in (".git", ".claude"))
    before = [home, *RO_ABSOLUTE, *RW_ABSOLUTE]
    before += [p for p in (inputs.runtime_dir, inputs.tmpdir) if p]
    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    for host_dir, dest in inputs.anchors:
        if not valid_path(host_dir) or not valid_path(dest) or dest == "/":
            continue
        if dest in taken or dest in seen or any(within(dest, p) for p in before):
            continue
        seen.add(dest)
        kept.append((host_dir, dest))
    return kept


def guard_sensitive(
    path: str, home: str, protected: tuple[str, ...] = (), sandbox_root: str | None = None
) -> str:
    """Why *path* may not be granted (bound read-write into the box), or ""
    when it may. Refused: anything that is, lies inside, or is an ancestor
    of a secret (SENSITIVE_HOME), of the sandbox root (every box's home —
    no box is built on, or granted, another's), of Collins' own state
    (*protected*), or that is `$HOME` or `/` themselves — a mount
    namespace has no rule that denies a path back out of a bind, so an
    ancestor of a secret is the secret."""
    if not valid_path(path):
        return "not an absolute path"
    home = home.rstrip("/") or "/"
    if path == "/":
        return "the whole filesystem"
    if path == home:
        return "the home directory itself"
    if within(path, home):
        return "an ancestor of the home directory"
    for rel in SENSITIVE_HOME:
        secret = os.path.join(home, rel)
        if within(path, secret) or within(secret, path):
            return f"reaches {secret}"
    # Before the protected paths, which name the root too: this is the
    # reason worth reading.
    if sandbox_root and (within(path, sandbox_root) or within(sandbox_root, path)):
        return "reaches the sandbox homes"
    for kept in protected:
        if within(path, kept) or within(kept, path):
            return f"reaches {kept}"
    return ""


def guard_path(
    path: str, home: str, protected: tuple[str, ...] = (), sandbox_root: str | None = None
) -> str:
    """guard_sensitive over every spelling of *path*: as written and
    resolved, against the home, the protected paths and the sandbox root
    both as written and resolved, and against the resolved target of each
    secret that is itself a symlink. A grant of `~/.ssh` that points into a
    dotfiles checkout, or a home under a symlinked `/home`, resolves to a
    string the plain arithmetic would not recognise — and bwrap binds the
    resolved directory."""
    reason = guard_sensitive(path, home, protected, sandbox_root)
    if reason or not valid_path(path):
        return reason
    home = home.rstrip("/") or "/"
    real = os.path.realpath(path)
    real_home = os.path.realpath(home)
    real_protected = tuple(os.path.realpath(p) for p in protected)
    real_sandbox = os.path.realpath(sandbox_root) if sandbox_root else None
    for candidate in {path, real}:
        for h, kept, sbx in ((home, protected, sandbox_root), (real_home, real_protected, real_sandbox)):
            reason = guard_sensitive(candidate, h, kept, sbx)
            if reason:
                return reason
        for rel in SENSITIVE_HOME:
            secret = os.path.join(home, rel)
            if not os.path.islink(secret):
                continue
            target = os.path.realpath(secret)
            if within(candidate, target) or within(target, candidate):
                return f"reaches {secret}"
    return ""


# -- the host side of a launch ---------------------------------------------------


def state_base() -> str:
    """`$XDG_STATE_HOME`, or `~/.local/state`."""
    return os.environ.get("XDG_STATE_HOME") or os.path.join(str(Path.home()), ".local", "state")


def plan_dir(app_id: str) -> str:
    """Where this instance's per-launch plan files go: beside the socket,
    under the runtime dir, so they die with the boot and never enter the
    box (only the socket file is bound).

    With no `XDG_RUNTIME_DIR` — a Collins started outside a desktop login
    session, and CI — `mcptools.runtime_dir` falls back to the temp
    directory, which every box shares read-write: plans there are inside
    the sandbox, so the protect-check would refuse every launch. Fall back
    to Collins' own state directory instead, which no box carries. Plans
    there outlive a reboot rather than dying with it, which `sweep_plans`
    at startup covers."""
    if os.environ.get("XDG_RUNTIME_DIR"):
        return os.path.join(mcptools.runtime_dir(app_id), "sandbox")
    return os.path.join(state_base(), "collins", "sandbox", app_id)


def protected_paths(app_id: str) -> tuple[str, ...]:
    """Collins' own state on this machine, as absolute paths: the config,
    state and cache directories (XDG overrides honoured, so a test's
    scratch tree is protected the same way), the plan directory, and the
    sandbox root — a share or a bind that would carry every box's home
    into one box is refused. (A box's own home is a descendant of the
    root, not an ancestor of it, and passes.)"""
    home = str(Path.home())
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    state = state_base()
    cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(home, ".cache")
    paths = [
        os.path.join(config, "collins"),
        os.path.join(state, "collins"),
        os.path.join(cache, "collins"),
        plan_dir(app_id),
        sandbox_root(),
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
    if within(native, real):
        return real, native
    return real, os.path.dirname(real)


def gather_inputs(workspace: str, app_id: str, state, box: str) -> Inputs:
    """The inputs for a launch in *workspace* inside the box *box*: the
    environment, the resolved CLI, Collins' own paths, the box's
    directories, and the grants and switches from *state*. Raises
    ValueError for anything that is not a box id."""
    home_dir = box_home(box)  # first: a bad id stops here
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
    # The box's own grants: a directory is allowed to one session, never
    # to a workspace or a project. As written, not resolved: the guard
    # checks both spellings itself, and a normalised path is what the mask
    # loop can match.
    grants = tuple(
        os.path.normpath(g) for g in state.get_sandbox_grants(box) if isinstance(g, str) and g
    )
    launcher = shutil.which("claude")
    return Inputs(
        workspace=ws,
        home=home,
        sandbox_home=home_dir,
        box=box,
        sandbox_root=sandbox_root(),
        carrier=box_carrier(box),
        anchors=tuple(
            (box_anchor(box, os.path.basename(top)), top) for top in anchor_roots()
        ),
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


# Everything under a sandbox home is written by the agent inside the box
# (it is $HOME there), so a path in it is
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


# -- a box on disk: made, scrubbed, leased, removed --------------------------------

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

_BIND_FLAGS = ("--bind", "--bind-try", "--ro-bind", "--ro-bind-try")


def make_box(
    box: str, anchors: tuple[str, ...] | list[str] = (), lease: str | None = None
) -> str:
    """Create the box's own directories, each mode 0700: the root, the box,
    its carrier, and every anchor in *anchors* (host directories, which
    must lie inside the box). The home is seed_home's. *lease* is the app
    id to lease the box to, written the moment its directory exists —
    before anything else is made in it — so no sweep finds a box that is
    being built unheld. Returns the box directory."""
    top = box_dir(box)
    os.makedirs(sandbox_root(), mode=0o700, exist_ok=True)
    os.makedirs(top, mode=0o700, exist_ok=True)
    os.chmod(top, 0o700)
    if lease is not None:
        write_lease(box, lease)
    for path in (box_carrier(box), os.path.join(top, "anchors")):
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
    for path in anchors:
        if not within(os.path.join(top, "anchors"), path) or not valid_path(path):
            raise PlanRefused(f"an anchor outside its box: {path!r}")
        os.makedirs(path, mode=0o700, exist_ok=True)
    return top


def plan_mounts(args: list[str]) -> list[tuple[str, str | None]]:
    """(destination, source) of every bind in a plan's arguments, in order;
    the source is None for a tmpfs."""
    mounts: list[tuple[str, str | None]] = []
    for i, arg in enumerate(args):
        if arg in _BIND_FLAGS and i + 2 < len(args):
            mounts.append((args[i + 2], args[i + 1]))
        elif arg == "--tmpfs" and i + 1 < len(args):
            mounts.append((args[i + 1], None))
    return mounts


def scrub_home(home_dir: str, plan: dict, home: str) -> list[str]:
    """Remove what stands in a mount's way in this box's own home, and say
    what went. *home_dir* is the home on the host, *home* what it is inside.

    bubblewrap makes the mount points it needs, but it stops at a symlink
    where one goes ("Can't bind mount … No such file or directory") — one
    a live grant left behind, or one the agent planted to stop its own
    next launch. For every destination of *plan* strictly under *home*
    (a bind whose source exists, a tmpfs) that no earlier mount of the plan
    already covers, the path is walked one component at a time, each
    opened relative to the last with O_NOFOLLOW: a symlink is unlinked,
    a stray file where a directory goes is unlinked, an empty directory
    where a file goes is removed, and what matches (bwrap's own stubs) or
    can't be settled (a non-empty directory where a file goes) is left
    for bwrap. It never follows a link, never leaves *home_dir*, and
    touches nothing that is not on a mount's path."""
    notes: list[str] = []
    home = home.rstrip("/") or "/"
    try:
        top = os.open(home_dir, DIR_FLAGS)
    except OSError:
        return notes
    covered: list[str] = []
    try:
        for dest, src in plan_mounts(list(plan.get("bwrap_args") or [])):
            if not valid_path(dest) or dest == home or not within(home, dest):
                continue
            if src is not None and not os.path.exists(src):
                continue  # a -try bind bwrap skips; a required one it reports
            wants_dir = src is None or os.path.isdir(src)
            if any(dest != other and within(other, dest) for other in covered):
                # Inside an earlier mount: the mount point is in that
                # mount's source, not in this home.
                continue
            if wants_dir:
                covered.append(dest)
            try:
                _scrub_path(top, os.path.relpath(dest, home).split("/"), wants_dir, dest, notes)
            except OSError as err:
                log.info("sandbox: couldn't clear the way for %s: %s", dest, err)
    finally:
        os.close(top)
    for note in notes:
        log.info("sandbox: %s", note)
    return notes


def _scrub_path(top: int, parts: list[str], wants_dir: bool, dest: str, notes: list[str]) -> None:
    fd = os.dup(top)
    try:
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            try:
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                return  # bwrap creates the rest
            if stat.S_ISLNK(mode):
                os.unlink(name, dir_fd=fd)
                notes.append(f"removed a symlink in the way of {dest}")
                return
            if not last:
                if not stat.S_ISDIR(mode):
                    os.unlink(name, dir_fd=fd)
                    notes.append(f"removed a file in the way of {dest}")
                    return
                deeper = os.open(name, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = deeper
                continue
            if stat.S_ISDIR(mode):
                if not wants_dir:
                    try:
                        os.rmdir(name, dir_fd=fd)
                    except OSError:
                        return  # not empty: bwrap's to report
                    notes.append(f"removed an empty directory in the way of {dest}")
                return
            if wants_dir or not stat.S_ISREG(mode):
                os.unlink(name, dir_fd=fd)
                notes.append(f"removed a file in the way of {dest}")
    finally:
        os.close(fd)


def _lease_path(box: str) -> str:
    return os.path.join(box_dir(box), "lease")


def write_lease(box: str, app_id: str) -> bool:
    """Say, in the box's own directory, that this process holds it."""
    data = json.dumps({"pid": os.getpid(), "app_id": app_id})
    return _replace_private(_lease_path(box), data)


def read_lease(box: str) -> dict | None:
    """The box's lease as written, or None with none (or one that lost its
    shape)."""
    try:
        with open(_lease_path(box), encoding="utf-8") as fh:
            lease = json.loads(fh.read(4096))
    except (OSError, ValueError):
        return None
    if not isinstance(lease, dict):
        return None
    pid = lease.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    return lease


def lease_live(lease: dict | None) -> bool:
    """Whether the process that wrote *lease* still runs."""
    return lease is not None and os.path.exists(f"/proc/{lease['pid']}")


def unescape_mount(field_: str) -> str:
    """A mountinfo path field with its octal escapes (`\\040` for a space)
    undone."""
    if "\\" not in field_:
        return field_
    out = []
    i = 0
    while i < len(field_):
        ch = field_[i]
        if ch == "\\" and field_[i + 1 : i + 4].isdigit() and len(field_[i + 1 : i + 4]) == 3:
            try:
                out.append(chr(int(field_[i + 1 : i + 4], 8)))
                i += 4
                continue
            except ValueError:
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def mount_points(text: str | None = None) -> list[str]:
    """Every mount point this process sees, from `/proc/self/mountinfo` (or
    *text* in its format). Raises OSError when it can't be read."""
    if text is None:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as fh:
            text = fh.read()
    points = []
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) > 4:
            points.append(unescape_mount(fields[4]))
    return points


class _OtherDevice(Exception):
    """A walk under a box met an entry on another device: a mount."""


def _walk_box(fd: int, dev: int, stat_fn, remove: bool) -> None:
    """Every entry under the open directory *fd*, by file descriptor: a
    symlink is an entry like any other (unlinked when *remove*, never
    followed), a directory is opened relative to its parent with
    O_NOFOLLOW. Raises _OtherDevice at the first entry whose device is not
    *dev*, before touching it."""
    with os.scandir(fd) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        try:
            st = stat_fn(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if st.st_dev != dev:
            raise _OtherDevice(name)
        if not stat.S_ISDIR(st.st_mode):
            if remove:
                os.unlink(name, dir_fd=fd)
            continue
        child = os.open(name, DIR_FLAGS, dir_fd=fd)
        try:
            if os.fstat(child).st_dev != dev:
                raise _OtherDevice(name)
            if remove:
                # A directory the agent left read-only still has to empty.
                os.fchmod(child, 0o700)
            _walk_box(child, dev, stat_fn, remove)
        finally:
            os.close(child)
        if remove:
            os.rmdir(name, dir_fd=fd)


def remove_box(box: str, mounts=None, stat_fn=None) -> bool:
    """Delete the box's directory, tree and all; whether it went. The only
    place the feature deletes a tree, and the tree was written by the
    agent, so:

    - only `<root>/<32 hex>` is ever removed (a bad id raises ValueError);
    - a box with a mount point at or under it is refused — a bindfs mount
      in the carrier *is* the user's granted directory, and nothing is
      ever deleted through a mount;
    - the walk is by file descriptor, never by path: a symlink is
      unlinked, never followed;
    - an entry on another device than the box's is a mount the first check
      didn't see, and stops the whole removal before anything is touched
      (the tree is walked once to look, then once to remove, and the
      second walk checks again).

    Best-effort and quiet on OSError: a partly removed box goes at the next
    sweep. *mounts* (`() -> mount points`) and *stat_fn* (`os.stat`'s
    signature) are injectable for tests."""
    top = box_dir(box)
    try:
        points = (mounts or mount_points)()
    except OSError as err:
        log.warning("sandbox: can't read the mount table, not removing %s: %s", top, err)
        return False
    spellings = {top, os.path.realpath(top)}
    for point in points:
        if any(within(spelled, point) for spelled in spellings):
            log.warning("sandbox: %s holds a mount (%s); not removing it", top, point)
            return False
    stat_fn = stat_fn or os.stat
    try:
        # The root is the host's own path (the box can't write it), so it
        # may be a symlink the user made; the box under it may not.
        root_fd = os.open(sandbox_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        try:
            fd = os.open(box, DIR_FLAGS, dir_fd=root_fd)
        except OSError:
            return False
        try:
            dev = os.fstat(fd).st_dev
            _walk_box(fd, dev, stat_fn, remove=False)
            _walk_box(fd, dev, stat_fn, remove=True)
        finally:
            os.close(fd)
        os.rmdir(box, dir_fd=root_fd)
        return True
    except _OtherDevice as err:
        log.warning("sandbox: %s holds another filesystem at %s; not removing it", top, err)
    except (OSError, RecursionError) as err:
        log.info("sandbox: couldn't remove %s: %s", top, err)
    finally:
        os.close(root_fd)
    return False


# -- reading a launched plan back --------------------------------------------------

# The policy inputs a launched plan records (its `inputs` slice) that a
# later launch may differ on: what the footer chip compares the plan the
# session runs under against what the state says now (plan_stale).
POLICY_INPUTS: tuple[str, ...] = ("grants", "share_gh", "share_ssh", "protect_settings")


def load_plan(path: str | None) -> dict | None:
    """The plan document at *path* as build_plan wrote it, or None when it
    can't be read or isn't that shape. The file is Collins' own (mode 0600
    under the runtime dir), but it is read back after the launch — the
    footer chip, a sibling's derivation — so the shape is checked rather
    than assumed: sandboxrun's validation for the bwrap half, and the
    `inputs` slice here."""
    if not path:
        return None
    try:
        plan = sandboxrun.read_plan(path)
    except sandboxrun.PlanError:
        return None
    if plan.get("version") != PLAN_VERSION:
        return None
    inputs = plan.get("inputs")
    if not isinstance(inputs, dict) or not valid_path(inputs.get("workspace")):
        return None
    if not valid_path(plan.get("workspace")):
        return None
    grants = inputs.get("grants", [])
    if not isinstance(grants, list) or not all(valid_path(g) for g in grants):
        return None
    for key in ("share_gh", "share_ssh", "protect_settings"):
        if not isinstance(inputs.get(key, False), bool):
            return None
    # The box: its id, and the places of its own the plan binds. (A plan
    # an earlier build wrote may name what its grants were read under as
    # well; it loads, and nothing reads that.)
    if not valid_box_id(inputs.get("box")):
        return None
    for key in ("sandbox_home", "carrier"):
        if not valid_path(inputs.get(key)):
            return None
    anchors = inputs.get("anchors")
    if not isinstance(anchors, list):
        return None
    for pair in anchors:
        if not isinstance(pair, list) or len(pair) != 2 or not all(valid_path(p) for p in pair):
            return None
    return plan


def plan_reaches(plan: dict, path: str) -> str:
    """Why *path* is not a directory the box built from *plan* can work in,
    or "" when it lies inside the plan's workspace or one of its grants
    (as written or resolved — bwrap binds the resolved directory). The
    rule a sibling's cwd is held to: a start_session from inside the box
    must not mint a workspace mount of anything the box doesn't already
    reach."""
    if not valid_path(path):
        return f"{path!r} is not an absolute path"
    inputs = plan["inputs"]
    roots = [inputs["workspace"], *inputs.get("grants", [])]
    candidates = {path, os.path.realpath(path)}
    for root in roots:
        for spelled in {root, os.path.realpath(root)}:
            if any(within(spelled, c) for c in candidates):
                return ""
    return (
        f"{path} is outside the sandbox's workspace {inputs['workspace']} and its "
        "allowed directories"
    )


def derive_plan(plan: dict, cwd: str, box: str) -> dict:
    """*plan* again for a session starting in *cwd* with the box *box* —
    the same workspace, the same static grants and shares, the same
    settings-file protection, as the parent was launched with; a home, a
    carrier and anchors of its own; the starting directory moved. What a
    sibling spawned from a sandboxed session launches with: it reaches
    what its parent reaches and shares no directory of the parent's box.

    Every argument that is, or lies under, the parent's box directory has
    that prefix replaced by the sibling's. Raises PlanRefused when *cwd*
    is not inside the box (plan_reaches), or the plan's own box can't be
    told from its paths."""
    reason = plan_reaches(plan, cwd)
    if reason:
        raise PlanRefused(reason)
    if not valid_box_id(box):
        raise PlanRefused(f"not a sandbox box id: {box!r}")
    inputs = plan["inputs"]
    parent_dir = os.path.dirname(inputs["sandbox_home"])
    if os.path.basename(parent_dir) != inputs["box"] or inputs["box"] == box:
        raise PlanRefused("the parent's plan doesn't name its own box")
    own_dir = os.path.join(os.path.dirname(parent_dir), box)

    def moved(path):
        if isinstance(path, str) and within(parent_dir, path):
            return own_dir + path[len(parent_dir) :]
        return path

    args = [moved(arg) for arg in plan["bwrap_args"]]
    for i in range(len(args) - 2, -1, -1):
        if args[i] == "--chdir":
            args[i + 1] = cwd
            break
    else:
        args += ["--chdir", cwd]
    derived = dict(plan)
    derived["bwrap_args"] = args
    derived["inputs"] = {
        **inputs,
        "box": box,
        "sandbox_home": moved(inputs["sandbox_home"]),
        "carrier": moved(inputs["carrier"]),
        "anchors": [[moved(host_dir), dest] for host_dir, dest in inputs.get("anchors") or []],
    }
    derived["cwd"] = cwd
    derived["notes"] = [*plan.get("notes", []), f"derived for {cwd} from the parent's plan"]
    return derived


class SandboxHost:
    """The host side of sandboxing for one app instance: the plan for a
    launch, the grants a workspace's chip edits, and the questions a
    running sandboxed session asks about its own box. Bound to the app id
    and the state; handed to the tabs as terminal.SANDBOX_HOST."""

    def __init__(self, app_id: str, state, state_file: str = "") -> None:
        self.app_id = app_id
        self.state = state
        # The file *state* is saved in: what the sweep's ownership of the
        # sandbox root is settled by (see sweep_boxes). "" when unknown.
        self.state_file = os.path.abspath(state_file) if state_file else ""
        # The boxes this process holds, counted: a tab's launch, and a
        # sibling's plan between its derivation and its tab.
        self._held: dict[str, int] = {}
        self._claimed = False  # whether this run has looked at the root's owner
        self._lock = threading.Lock()

    def prepare_launch(self, workspace: str, box: str) -> str | None:
        """See prepare_launch. The box is held from before its directory
        is made — this instance's own sweep runs on a thread at startup,
        and must never find a box between its creation and its hold —
        until `release`; a launch that can't be prepared lets go again."""
        if not valid_box_id(box):
            return prepare_launch(workspace, self.app_id, self.state, box)  # the refusal
        self._count(box)
        path = prepare_launch(workspace, self.app_id, self.state, box)
        if path is None:
            self.release(box)
            return None
        self._claim_root()
        return path

    # -- the boxes ---------------------------------------------------------------

    def _count(self, box: str) -> None:
        with self._lock:
            self._held[box] = self._held.get(box, 0) + 1

    def _claim_root(self) -> None:
        """At the first box this run holds: the root exists from here on,
        and is this state's unless somebody's already (owns_root) — before
        any other instance's sweep can find it unowned."""
        with self._lock:
            claim = not self._claimed
            self._claimed = True
        if claim:
            self.owns_root()

    def hold(self, box: str) -> None:
        """This process needs *box*: counted here, and written as the
        box's lease — what keeps another instance's sweep (state.json is
        shared across app ids) off a box whose session has no id yet."""
        if not valid_box_id(box):
            return
        self._count(box)
        try:
            write_lease(box, self.app_id)
            self._claim_root()
        except OSError as err:
            log.info("sandbox: couldn't write the lease of %s: %s", box, err)

    def release(self, box: str) -> None:
        """One holder of *box* is done; with the last, the lease goes —
        when it is this process's."""
        if not valid_box_id(box):
            return
        with self._lock:
            count = self._held.get(box, 0)
            if count > 1:
                self._held[box] = count - 1
                return
            self._held.pop(box, None)
        lease = read_lease(box)
        if lease is not None and lease["pid"] == os.getpid():
            release_plan(_lease_path(box))

    def held(self, box: str) -> bool:
        with self._lock:
            return self._held.get(box, 0) > 0

    def discard_box(self, box: str) -> bool:
        """Remove *box* when nothing needs it: no session of the state's
        map, no live lease of another process, not held by this one.
        Whether it went."""
        if not valid_box_id(box) or self.held(box):
            return False
        if box in self.state.sandbox_boxes():
            return False
        lease = read_lease(box)
        if lease is not None and lease["pid"] != os.getpid() and lease_live(lease):
            return False
        return remove_box(box)

    def discard_box_async(self, box: str) -> None:
        """discard_box on a daemon thread — a home can hold a build's
        worth of files — with nothing to land."""
        if not valid_box_id(box):
            return
        threading.Thread(
            target=self.discard_box, args=(box,), name="sandbox-discard", daemon=True
        ).start()

    def owns_root(self) -> bool:
        """Whether this instance's state is the one the sandbox root's
        boxes belong to, claiming a root nobody owns. The sweep removes
        every box the state doesn't name, so it has to be the state that
        names them: an instance on a scratch state.json (an e2e check, a
        capture) beside the user's real root names none of the user's
        boxes and must remove none.

        `<root>/owner` names the owning state file. No owner, this
        instance's, or one whose state file is gone: this instance owns the
        root, and says so there. An instance that knows no state file owns
        only a root nobody has claimed, and claims nothing."""
        path = os.path.join(sandbox_root(), "owner")
        owner = None
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.loads(fh.read(8192))
            if isinstance(data, dict) and isinstance(data.get("state"), str):
                owner = data["state"]
        except (OSError, ValueError):
            pass
        if not self.state_file:
            return owner is None
        if owner == self.state_file:
            return True
        if owner and os.path.exists(owner):
            return False
        return _replace_private(path, json.dumps({"state": self.state_file}))

    def sweep_boxes(self) -> int:
        """discard_box for every box under the root; how many went. A
        directory whose name is not a box id is left alone, and so is
        every box of a root another state file owns (owns_root)."""
        root = sandbox_root()
        try:
            names = os.listdir(root)
        except OSError:
            return 0
        if not self.owns_root():
            log.info("sandbox: %s belongs to another state file; not sweeping it", root)
            return 0
        return sum(1 for name in names if valid_box_id(name) and self.discard_box(name))

    # -- grants ------------------------------------------------------------------

    # A grant is one session's: every method here is handed the box it is
    # about, and none looks up a box from a workspace or a project.

    def grants(self, box: str) -> list[str]:
        """The directories granted to the session whose box is *box*."""
        return self.state.get_sandbox_grants(box)

    def grant_reason(self, workspace: str, path: str) -> str:
        """Why *path* can't be granted to sessions in *workspace*, or "":
        the secret / protected / home rule (guard_path), plus the plain
        facts — it has to be a directory, and one the box doesn't already
        hold."""
        path = os.path.normpath(path)
        if not valid_path(path):
            return "not an absolute path"
        # The guard first: a secret is refused whether or not it exists.
        reason = guard_path(path, str(Path.home()), protected_paths(self.app_id), sandbox_root())
        if reason:
            return reason
        ws = os.path.realpath(workspace)
        if within(ws, path) or within(ws, os.path.realpath(path)):
            return "already inside the workspace"
        if not os.path.isdir(path):
            return "not a directory"
        return ""

    def allow(self, box: str, workspace: str, path: str) -> str:
        """Grant *path* to the session whose box is *box* and whose
        workspace is *workspace* (in state; the live grants deliver it to
        the running box, or the next launch binds it). Returns the refusal,
        or "" when it was recorded — or was already there. Main loop."""
        if not valid_box_id(box):
            return "this session has no sandbox yet"
        reason = self.grant_reason(workspace, path)
        if reason:
            return reason
        path = os.path.normpath(path)
        grants = self.state.get_sandbox_grants(box)
        if path not in grants:
            self.state.set_sandbox_grants(box, [*grants, path])
        return ""

    def revoke(self, box: str, path: str) -> None:
        """Take *path* back from the session whose box is *box*. Main loop."""
        grants = [g for g in self.state.get_sandbox_grants(box) if g != path]
        self.state.set_sandbox_grants(box, grants)

    # -- a project's defaults ----------------------------------------------------
    #
    # A template for new sessions, never a live link: the defaults are
    # copied into a box's list once, when the box is minted, and from then
    # on the two lists have nothing to do with each other. Marking or
    # removing a default changes no session that exists.

    def project_grants(self, workspace: str) -> list[str]:
        """The directories a new session of *workspace*'s project starts
        allowed."""
        return self.state.get_sandbox_project_grants(project_key(workspace))

    def is_project_default(self, workspace: str, path: str) -> bool:
        return os.path.normpath(path) in self.project_grants(workspace)

    def set_project_default(self, workspace: str, path: str, on: bool) -> str:
        """Make *path* a default of *workspace*'s project, or stop it being
        one. Returns the refusal, or "" when the list says so now. The
        guard is the grant's — never a secret, Collins' own state, the
        home or the sandbox homes — without the two checks that are about
        one session on one day (inside that session's workspace, not a
        directory right now): a default is about other sessions too, and
        each is checked again when a box is seeded. Main loop."""
        path = os.path.normpath(path)
        key = project_key(workspace)
        defaults = self.state.get_sandbox_project_grants(key)
        if not on:
            self.state.set_sandbox_project_grants(key, [d for d in defaults if d != path])
            return ""
        if not valid_path(path):
            return "not an absolute path"
        reason = guard_path(path, str(Path.home()), protected_paths(self.app_id), sandbox_root())
        if reason:
            return reason
        if path not in defaults:
            self.state.set_sandbox_project_grants(key, [*defaults, path])
        return ""

    def seed_box(self, box: str, workspace: str) -> list[str]:
        """Copy the project's defaults into *box*'s list, after what it
        holds already, and return the list. A default is skipped — with a
        line in the log, and left in the defaults — when this session
        can't be granted it today (grant_reason: it has become a secret's
        ancestor, is no longer a directory, or lies inside this session's
        workspace). Main loop."""
        if not valid_box_id(box):
            return []
        grants = self.state.get_sandbox_grants(box)
        for path in self.project_grants(workspace):
            reason = self.grant_reason(workspace, path)
            if reason:
                log.info("sandbox: default %s not given to a new session: %s", path, reason)
            elif path not in grants:
                grants.append(path)
        self.state.set_sandbox_grants(box, grants)
        return grants

    def mint_box(self, workspace: str, seed: bool = True) -> str:
        """A box id for a session in *workspace* — the one place one is
        made. With *seed* the project's defaults become its list (seed_box):
        what a new session starts with. Without, it starts with none: a
        fork, a sibling and a `--continue` tab get theirs from elsewhere.
        Main loop."""
        box = new_box_id()
        if seed:
            self.seed_box(box, workspace)
        return box

    def settle_box(self, session_id: str, box: str, workspace: str | None, owed: bool) -> bool:
        """A sandboxed tab that launched in *box* turned out to be
        *session_id*: record the box against the session, and settle what
        the box is allowed. Whether the box is owed a delivery
        (GrantMounts.sync) afterwards.

        For a new session there is nothing to settle: its box was minted
        with its project's defaults. A `--continue` tab could not know
        which session it would land on, so its box was minted with none
        (*owed*). When the session already had a box, the tab's takes over
        what that one was allowed — after what it was allowed itself
        meanwhile — and the old box is forgotten. When it had none, the
        tab's box gets the project's defaults now. Main loop."""
        if not session_id or not valid_box_id(box):
            return False
        previous = self.state.sandbox_box(session_id)
        taken_over = bool(previous) and previous != box
        if taken_over:
            mine = self.state.get_sandbox_grants(box)
            extra = [g for g in self.state.get_sandbox_grants(previous) if g not in mine]
            if extra:
                self.state.set_sandbox_grants(box, [*mine, *extra])
        elif owed and not previous and workspace:
            self.seed_box(box, workspace)
        self.state.set_sandboxed(session_id, True, box=box)
        if taken_over:
            self.forget_box(previous)
        return taken_over or (owed and not previous)

    def forget_box(self, box: str) -> None:
        """The box may be done with: when no session names it, its grants
        leave the state — they are the session's, and go with it — and the
        box is discarded, which still refuses one that is named, leased by
        another process or held. Main loop: this is where the state is
        written."""
        if not valid_box_id(box):
            return
        if box not in self.state.sandbox_boxes():
            self.state.set_sandbox_grants(box, [])
        self.discard_box_async(box)

    def prune_grants(self) -> int:
        """Drop the grants of every box no session names and whose
        directory is gone — a box swept at an earlier start, or removed by
        hand; how many went. A directory that exists is either about to
        be swept (the next start prunes its entry) or another instance's
        running box. Touches nothing but this instance's own state, so it
        runs whether or not this instance owns the root. Main loop, at
        startup."""
        named = self.state.sandbox_boxes()
        pruned = 0
        for box in sorted(self.state.sandbox_grant_boxes()):
            if box in named or not valid_box_id(box):
                continue
            if os.path.lexists(box_dir(box)):
                continue
            self.state.set_sandbox_grants(box, [])
            pruned += 1
        return pruned

    def plan_stale(
        self, plan_path: str | None, workspace: str, live: Iterable[str] = ()
    ) -> bool:
        """Whether a box launched from *plan_path* differs from the one the
        state would build for *workspace* now, so the chip can offer a
        restart: a share or the settings switch flipped, or the state's
        grants are not exactly the plan's static grants plus the ones
        mounted into the running box since (*live*,
        sandboxgrants.GrantMounts.live_paths) — a grant still waiting for
        the restart, or a static one taken back. False when either side
        can't be read: a box that can't be rebuilt has nothing to restart
        into."""
        launched = load_plan(plan_path)
        if launched is None:
            return False
        try:
            current = build_plan(
                gather_inputs(workspace, self.app_id, self.state, launched["inputs"]["box"])
            )
        except (PlanRefused, OSError, ValueError):
            return False
        before, now = launched["inputs"], current["inputs"]
        if any(before.get(key) != now.get(key) for key in POLICY_INPUTS if key != "grants"):
            return True
        holds = {*before.get("grants", []), *(os.path.normpath(path) for path in live)}
        return set(now.get("grants", [])) != holds

    def derive(
        self, plan_path: str | None, cwd: str, live: Iterable[str] = ()
    ) -> tuple[str | None, str, str]:
        """A sibling's launch inside the box *plan_path* describes, starting
        in *cwd*: (its plan file, its box id, "") — or (None, "", the
        reason) when the parent's plan can't be read or *cwd* lies outside
        it. The sibling gets a box of its own (derive_plan): minted,
        created, its home seeded and folder trust mirrored for *cwd*,
        scrubbed, and held. The caller's tab adopts the plan and the box,
        and releases both.

        The sibling's grants are recorded as what its plan binds: its
        parent's *static* grants, as the parent was launched. Nothing the
        parent holds live, and nothing the parent is allowed later, reaches
        it — a directory is allowed in the sibling's own chip. Main loop:
        the state is written here.

        *live* is what the parent holds mounted since its launch
        (GrantMounts.live_paths): the sibling's plan is the parent's as
        launched, so it can't *start* inside such a directory — it would
        have no mount to start in — and the refusal says so."""
        parent = load_plan(plan_path)
        if parent is None:
            return None, "", "the parent session's sandbox plan can't be read"
        # No defaults: a sibling holds nothing its parent wasn't launched
        # with, and a default the parent's user took from the parent must
        # not come back through a sibling.
        box = self.mint_box(cwd, seed=False)
        try:
            derived = derive_plan(parent, cwd, box)
        except PlanRefused as err:
            candidates = {cwd, os.path.realpath(cwd)}
            for path in live:
                for spelled in {path, os.path.realpath(path)}:
                    if any(within(spelled, candidate) for candidate in candidates):
                        return None, "", (
                            f"{cwd} was allowed while the parent session was running; "
                            "restart the parent session to start a sibling there"
                        )
            return None, "", str(err)
        self._count(box)  # held before it is there to be swept
        try:
            anchors = [host_dir for host_dir, _dest in derived["inputs"]["anchors"]]
            make_box(box, anchors, lease=self.app_id)
            home_dir = derived["inputs"]["sandbox_home"]
            seed_home(home_dir)
            mirror_trust(home_dir, cwd)
            home = derived.get("setenv", {}).get("HOME") or str(Path.home())
            derived["notes"] = [*derived["notes"], *scrub_home(home_dir, derived, home)]
            path = write_plan(derived, plan_dir(self.app_id))
        except (PlanRefused, OSError) as err:
            self.release(box)
            remove_box(box)
            if isinstance(err, PlanRefused):
                return None, "", str(err)
            return None, "", f"couldn't write the sandbox plan: {err}"
        self._claim_root()
        self.state.set_sandbox_grants(box, list(derived["inputs"].get("grants") or []))
        return path, box, ""


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


def prepare_launch(workspace: str, app_id: str, state, box: str) -> str | None:
    """Everything a sandboxed launch in *workspace* needs on the host, then
    the plan file's path — or None when no box can be built (bubblewrap
    missing, a bad box id, a refused plan, an unwritable directory), which
    the caller turns into an unsandboxed launch with a message. Creates
    the RW_HOME_ALWAYS directories and the box's own (its carrier, its
    anchors), seeds and secures its home, mirrors folder trust into its
    `~/.claude.json`, clears what stands in a mount's way there
    (scrub_home), and leaves the box leased to this process."""
    if not available():
        return None
    if not valid_box_id(box):
        log.warning("sandbox: refusing to build a box for %s: bad box id %r", workspace, box)
        return None
    try:
        inputs = gather_inputs(workspace, app_id, state, box)
        for rel in RW_HOME_ALWAYS:
            os.makedirs(os.path.join(inputs.home, rel), exist_ok=True)
        make_box(box, [host_dir for host_dir, _dest in inputs.anchors], lease=app_id)
        seed_home(inputs.sandbox_home)
        mirror_trust(inputs.sandbox_home, inputs.workspace)
        plan = build_plan(inputs)
        plan["notes"] = [*plan["notes"], *scrub_home(inputs.sandbox_home, plan, inputs.home)]
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
