# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Live grants: a directory allowed while a sandboxed session runs reaches
the running box, without a restart (see ~/specs/collins/sandboxed-sessions.md,
the amendment of 2026-09-27).

A box can't mount anything itself (Ubuntu's AppArmor policy denies it, and
the host can't join its mount namespace either), but bubblewrap makes every
mount a box inherits a *slave* of the host's: a mount the **host** makes
inside a directory the box has bound propagates into the running box. The
host can't mount(2) unprivileged either, but `fusermount3` is setuid root
wherever libfuse3 is shipped, so a `bindfs` passthrough of the granted
directory does it.

Where the mount lands is the whole safety question. `fusermount3` resolves
its mount point by *path* (realpath of the parent, then lstat and chdir to
the string), so a mount point in a directory the agent can write is a race
the agent can win: swap a component for a symlink and the grant — whose
content the agent writes — lands on a host directory of its choosing.
So a mount Collins makes lands **only** in a box's carrier (`grants/`,
bound read-only at /run/collins/grants inside) or under one of its anchors
(`anchors/<top>/`, bound read-only at `/<top>`): host-owned, outside the
home, unwritable from inside. A directory under `$HOME` then answers at its
real path through a *symlink* in the box's home, and creating that is
race-free: the home is walked by file descriptor with O_NOFOLLOW, and a
link that can't be made safely is simply not made (the grant still
answers inside the carrier).

One `GrantMounts` per app instance owns every mount this instance made.
Every spawn, mount and unmount runs on its one worker thread — which
serialises them, and is the thread `PR_SET_PDEATHSIG` ties the `bindfs`
servers to, so they die with the app however it dies. A grant that can't
be delivered live is PENDING, never an error and never a guess: the
restart applies it as a plain bind, which is what every grant present at a
launch is.

GTK-free. The spawn, the mount table, the clock and the commands are
injectable, so the unit suite never needs FUSE.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import queue
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import sandboxplan

log = logging.getLogger(__name__)

# What a granted directory is, in one box (GrantMounts.status).
STATIC = "static"  # bound by the plan the box was launched with
LIVE = "live"  # mounted into the running box
PENDING = "pending"  # recorded, and waiting for the restart
LEAVING = "leaving"  # bound by the plan, no longer granted: until the restart

# Why nothing can be delivered live here (GrantMounts.capable), in the
# order they are checked. User-visible: Preferences shows them.
REASON_NO_BINDFS = "bindfs not installed"
REASON_NO_FUSERMOUNT = "fusermount3 not installed"
REASON_NO_FUSE = "FUSE is not available"
REASON_NO_PROPAGATION = "mounts don't propagate from the sandbox directory"

# Why one directory can't, though others can.
REASON_UNPLACEABLE = "can't be added to a running sandbox"

FUSE_DEVICE = "/dev/fuse"
BINDFS_TYPE = "fuse.bindfs"

MOUNT_TIMEOUT_S = 5.0  # how long a mount may take to appear
MOUNT_POLL_S = 0.01
UNMOUNT_GRACE_S = 1.0  # the server's own exit after the unmount
TERM_GRACE_S = 2.0  # …after SIGTERM, before SIGKILL
WAIT_TIMEOUT_S = 15.0  # the longest a caller waits on the worker

_SLOT_NAME_MAX = 40
_OWNER = re.compile(r"^collins:(\d+)$")

# prctl(2)'s PR_SET_PDEATHSIG. Loaded here, not in the child: between fork
# and exec nothing but the call itself may run.
_PR_SET_PDEATHSIG = 1
try:
    _LIBC = ctypes.CDLL(None, use_errno=True)
except OSError:  # pragma: no cover - a libc there is always
    _LIBC = None


@dataclass(frozen=True)
class Delivery:
    """What became of one grant in one box."""

    box: str
    path: str  # the granted directory, as recorded
    status: str  # LIVE or PENDING
    inside: str  # where it answers inside the box; "" when PENDING
    linked: bool  # whether the real path answers (a link or an anchor)
    reason: str  # why PENDING, else ""


@dataclass(frozen=True)
class Mount:
    """One line of /proc/self/mountinfo, as far as this module reads it."""

    point: str
    fstype: str
    source: str
    shared: bool  # whether the mount is in a peer group (propagates)


@dataclass(frozen=True)
class Placement:
    """Where a grant goes in one box."""

    mount_point: str  # on the host
    base: str  # the host-only directory holding it: the carrier or an anchor
    inside: str  # where the mount answers inside the box
    link: tuple[str, ...] | None  # under the box's home, where a link is wanted
    linked: bool  # whether the real path answers without one


@dataclass
class _Live:
    path: str
    placement: Placement
    proc: object  # the bindfs server
    linked: bool


@dataclass
class _Box:
    box: str
    static: tuple[str, ...]  # what the plan it launched with binds
    carrier: str
    anchors: tuple[tuple[str, str], ...]  # (host directory, destination)
    home_dir: str  # the box's home, on the host
    home: str  # …and what it is inside
    covered: tuple[str, ...]  # destinations under the home the plan mounts
    live: dict[str, _Live] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)  # path -> reason
    stale: list[_Live] = field(default_factory=list)  # a re-registered box's old mounts


def parse_mountinfo(text: str) -> list[Mount]:
    """The mounts in *text*, which is in /proc/self/mountinfo's format:
    `id parent major:minor root mount-point options [optional…] - type
    source super-options`."""
    mounts = []
    for line in text.splitlines():
        head, sep, tail = line.partition(" - ")
        if not sep:
            continue
        fields = head.split(" ")
        rest = tail.split(" ")
        if len(fields) < 6 or len(rest) < 2:
            continue
        mounts.append(
            Mount(
                point=sandboxplan.unescape_mount(fields[4]),
                fstype=rest[0],
                source=sandboxplan.unescape_mount(rest[1]),
                shared=any(option.startswith("shared:") for option in fields[6:]),
            )
        )
    return mounts


def read_mountinfo() -> str:
    with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def slot_name(path: str) -> str:
    """The name a grant's mount point has in a carrier: the path's last
    component made safe and short, then the first 8 hex of the path's
    SHA-256 — readable in `ls /run/collins/grants`, and distinct for two
    directories of one name."""
    name = os.path.basename(path.rstrip("/")) or "root"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if name.startswith("."):
        name = "_" + name[1:]
    digest = hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()[:8]
    return f"{name[:_SLOT_NAME_MAX]}-{digest}"


def place(box: _Box, path: str) -> Placement | None:
    """Where *path* goes in *box*, or None when it can't be placed in a
    running box at all (`/opt/…`, `/var/…`, a root that appeared after the
    launch) — which the restart covers.

    Under `$HOME`: a slot in the carrier, and a link in the home at the
    real path — unless a mount of the box's plan covers that spot (the
    link would sit underneath it, unseen), in which case the grant answers
    in the carrier only. Under an anchor the box emitted: at its real path
    under the anchor's host directory, and no link is needed."""
    within = sandboxplan.within
    if path != box.home and within(box.home, path):
        slot = slot_name(path)
        hidden = any(within(dest, path) for dest in box.covered)
        return Placement(
            mount_point=os.path.join(box.carrier, slot),
            base=box.carrier,
            inside=f"{sandboxplan.CARRIER_DEST}/{slot}",
            link=None if hidden else tuple(os.path.relpath(path, box.home).split("/")),
            linked=False,
        )
    for host_dir, dest in box.anchors:
        if path != dest and within(dest, path):
            return Placement(
                mount_point=host_dir + path[len(dest) :],
                base=host_dir,
                inside=path,
                link=None,
                linked=True,
            )
    return None


def _set_pdeathsig() -> None:
    """In the child, before exec: SIGTERM when the thread that forked it
    goes. How a bindfs server is kept from outliving the app."""
    if _LIBC is not None:
        _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)


def spawn_server(argv: list[str]):
    """Start a bindfs server. Only ever called on the worker thread:
    PR_SET_PDEATHSIG is tied to the *thread* that forked."""
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        preexec_fn=_set_pdeathsig,
    )


def run_command(argv: list[str]) -> int:
    """Run a short command to its end; its exit status, or -1."""
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).returncode
    except (OSError, subprocess.SubprocessError):
        return -1


def _tool(override_var: str, name: str) -> str | None:
    """An executable: the environment's override (a test's fake, or a path
    that doesn't exist to say "not installed"), else *name* on PATH."""
    override = os.environ.get(override_var)
    if override:
        return override if os.access(override, os.X_OK) else None
    return shutil.which(name)


def _first_line(proc) -> str:
    """The first line a finished server wrote to stderr, bounded."""
    stream = getattr(proc, "stderr", None)
    if stream is None:
        return ""
    try:
        data = stream.read(4096) or b""
    except (OSError, ValueError):
        return ""
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    for line in data.splitlines():
        if line.strip():
            return line.strip()[:200]
    return ""


class GrantMounts:
    """See the module docstring. Handed to the tabs as
    `ServiceCore.sandbox_grants`, the way the SandboxHost is (PR-1.12a).

    `register` / `unregister` follow a box's life, `allow` / `revoke` a
    grant's; each queues its work for the worker and returns at once.
    *done(list[Delivery])* is called **on the worker thread** — land it on
    the main loop yourself, at `GLib.PRIORITY_DEFAULT`. `status`,
    `delivery` and `live_paths` read a table under a lock and never
    block."""

    def __init__(
        self,
        host: sandboxplan.SandboxHost,
        *,
        bindfs: str | None = None,
        fusermount: str | None = None,
        spawn: Callable[[list[str]], object] | None = None,
        mountinfo: Callable[[], str] | None = None,
        run: Callable[[list[str]], int] | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
        fuse_device: str = FUSE_DEVICE,
    ) -> None:
        self._host = host
        self._bindfs_override = bindfs
        self._fusermount_override = fusermount
        self._spawn = spawn or spawn_server
        self._mountinfo = mountinfo or read_mountinfo
        self._run = run or run_command
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._pid_alive = pid_alive or (lambda pid: os.path.exists(f"/proc/{pid}"))
        self._fuse_device = fuse_device
        self._lock = threading.Lock()
        self._boxes: dict[str, _Box] = {}
        self._capable: str | None = None
        self._stopped = False
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="sandbox-grants", daemon=True)
        self._thread.start()

    # -- the worker ---------------------------------------------------------------

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            work, done, finished = job
            result: list[Delivery] = []
            try:
                result = work() or []
            except Exception:  # the thread must outlive any one job
                log.exception("sandbox grants: a job failed")
            if finished is not None:
                finished.set()
            if done is not None:
                try:
                    done(result)
                except Exception:
                    log.exception("sandbox grants: a job's callback failed")

    def _submit(self, work, done=None, wait: bool = False) -> bool:
        """Queue *work* for the worker. With *wait*, whether it was done
        within WAIT_TIMEOUT_S; True otherwise."""
        with self._lock:
            stopped = self._stopped
        if stopped or threading.current_thread() is self._thread:
            # Nothing is mounted after the shutdown, and a job is never
            # queued from inside one.
            if done is not None:
                done([])
            return True
        finished = threading.Event() if wait else None
        self._jobs.put((work, done, finished))
        if finished is not None and not finished.wait(WAIT_TIMEOUT_S):
            log.warning("sandbox grants: gave up waiting for the worker")
            return False
        return True

    # -- can anything be delivered live here? -------------------------------------

    def _bindfs(self) -> str | None:
        return self._bindfs_override or _tool("COLLINS_BINDFS", "bindfs")

    def _fusermount(self) -> str | None:
        return self._fusermount_override or _tool("COLLINS_FUSERMOUNT", "fusermount3")

    def capable(self) -> str:
        """"" when a grant can reach a running box on this machine, else
        why not (one of the REASON_ strings). Settled once, and kept."""
        with self._lock:
            if self._capable is not None:
                return self._capable
        verdict = self._probe()
        with self._lock:
            self._capable = verdict
        return verdict

    def _probe(self) -> str:
        if not self._bindfs():
            return REASON_NO_BINDFS
        if not self._fusermount():
            return REASON_NO_FUSERMOUNT
        if not os.path.exists(self._fuse_device):
            return REASON_NO_FUSE
        try:
            mounts = parse_mountinfo(self._mountinfo())
        except OSError:
            return REASON_NO_PROPAGATION
        # The mount holding the sandbox root: the longest mount point that
        # is a prefix of it. A box's binds are slaves of *that* mount's
        # peer group; a private one has no peers to tell.
        root = os.path.realpath(sandboxplan.sandbox_root())
        holding = None
        for mount in mounts:
            if sandboxplan.within(mount.point, root):
                if holding is None or len(mount.point) >= len(holding.point):
                    holding = mount
        if holding is None or not holding.shared:
            return REASON_NO_PROPAGATION
        return ""

    # -- a box's life -------------------------------------------------------------

    def register(self, plan: dict | None, done=None) -> None:
        """A box came up on *plan* (sandboxplan.load_plan's). Records it and
        ends as `sync` does: every grant the state holds for this box that
        the plan lacks is delivered — nothing for an ordinary launch, whose
        plan was built from that very list. A box already registered is
        replaced, and what was live in it unmounted first."""
        if not plan:
            return
        inputs = plan["inputs"]
        home = (plan.get("setenv") or {}).get("HOME") or str(Path.home())
        home = home.rstrip("/") or "/"
        record = _Box(
            box=inputs["box"],
            static=tuple(inputs.get("grants") or ()),
            carrier=inputs["carrier"],
            anchors=tuple((host_dir, dest) for host_dir, dest in inputs.get("anchors") or ()),
            home_dir=inputs["sandbox_home"],
            home=home,
            covered=tuple(
                dest
                for dest, _src in sandboxplan.plan_mounts(plan["bwrap_args"])
                if dest != home and sandboxplan.within(home, dest)
            ),
        )
        with self._lock:
            previous = self._boxes.get(record.box)
            if previous is not None:
                record.stale = [*previous.stale, *previous.live.values()]
            self._boxes[record.box] = record

        def work() -> list[Delivery]:
            self._retire_stale(record)
            return self._sync(record)

        self._submit(work, done)

    def unregister(self, box: str, wait: bool = False, done=None) -> bool:
        """The box is gone (or about to be rebuilt): unmount everything
        live in it and remove its links. A restart needs the home clear
        before the next plan is prepared, and nothing may remove a box
        before its mounts are gone: *done([])* is called once they are —
        on the worker thread, or at once for a box this doesn't know — and
        is how a caller on the main loop goes on without waiting there.
        *wait* blocks instead, and the answer is whether the unmounts
        finished in time (always True without it)."""
        with self._lock:
            record = self._boxes.pop(box, None)
        if record is None:
            if done is not None:
                done([])
            return True

        def work() -> list[Delivery]:
            self._retire_all(record)
            return []

        return self._submit(work, done, wait=wait)

    def shutdown(self) -> None:
        """Unmount everything this instance mounted and stop the worker.
        Blocks until it has, within WAIT_TIMEOUT_S."""
        with self._lock:
            if self._stopped:
                return
            records = list(self._boxes.values())
            self._boxes.clear()

        def work() -> list[Delivery]:
            for record in records:
                self._retire_all(record)
            return []

        self._submit(work, None, wait=True)
        with self._lock:
            self._stopped = True
        self._jobs.put(None)
        self._thread.join(timeout=TERM_GRACE_S)

    # -- a grant's life -----------------------------------------------------------

    # A grant is one session's. Each of these is handed the box it is
    # about and touches that box alone: there is no way from a workspace,
    # a project or a repository to a set of boxes anywhere in here.

    def allow(self, box: str, path: str, done=None) -> None:
        """*path* was granted to the session whose box is *box*
        (SandboxHost.allow recorded it): deliver it to that box, when it is
        registered and doesn't hold the path already, static or live.
        *done* gets at most one Delivery, and none for a box that isn't
        running: the grant is in the state, and its next launch binds it.
        A path the state doesn't grant the box is never mounted."""
        path = os.path.normpath(path)

        def work() -> list[Delivery]:
            record = self._record(box)
            if record is None or self._holds(record, path):
                return []
            if path not in self._state_grants(box):
                log.warning("sandbox grants: %s is not granted to %s; not mounting it", path, box)
                return []
            return [self._deliver(record, path)]

        self._submit(work, done)

    def revoke(self, box: str, path: str, done=None) -> None:
        """*path* is no longer granted to the session whose box is *box*:
        take it out of that box where it arrived live. Where it is static
        nothing can be done to the running box; its status there is
        LEAVING. *done* gets an empty list."""
        path = os.path.normpath(path)

        def work() -> list[Delivery]:
            record = self._record(box)
            if record is None:
                return []
            with self._lock:
                live = record.live.pop(path, None)
                record.pending.pop(path, None)
            if live is not None:
                self._retire(record, live)
            return []

        self._submit(work, done)

    def sync(self, box: str, done=None) -> None:
        """Deliver every grant the state holds for *box* that the box holds
        neither statically nor live — what a box that took over another's
        grants is owed. Nothing, when there is nothing."""

        def work() -> list[Delivery]:
            record = self._record(box)
            return self._sync(record) if record is not None else []

        self._submit(work, done)

    def _sync(self, record: _Box) -> list[Delivery]:
        return [
            self._deliver(record, path)
            for path in self._state_grants(record.box)
            if not self._holds(record, path)
        ]

    # -- the table ----------------------------------------------------------------

    def status(self, box: str, path: str) -> str:
        """STATIC when the box's plan holds *path* and the state still
        does, LEAVING when the plan holds it and the state no longer does,
        LIVE when it is mounted there, PENDING otherwise."""
        with self._lock:
            record = self._boxes.get(box)
            if record is None:
                return PENDING
            static = path in record.static
            live = path in record.live
        if static:
            return STATIC if path in self._state_grants(box) else LEAVING
        return LIVE if live else PENDING

    def delivery(self, box: str, path: str) -> Delivery | None:
        """How *path* stands in *box* when it is LIVE or PENDING there;
        None for a box or a grant this knows nothing of, and for a static
        one."""
        with self._lock:
            record = self._boxes.get(box)
            if record is None:
                return None
            live = record.live.get(path)
            if live is not None:
                return Delivery(box, path, LIVE, live.placement.inside, live.linked, "")
            if path in record.pending:
                return Delivery(box, path, PENDING, "", False, record.pending[path])
        return None

    def registered(self, box: str) -> bool:
        with self._lock:
            return box in self._boxes

    def live_paths(self, box: str) -> list[str]:
        with self._lock:
            record = self._boxes.get(box)
            return list(record.live) if record is not None else []

    def _record(self, box: str) -> _Box | None:
        with self._lock:
            return self._boxes.get(box)

    def _holds(self, record: _Box, path: str) -> bool:
        with self._lock:
            return path in record.static or path in record.live

    def _state_grants(self, box: str) -> list[str]:
        return [
            os.path.normpath(grant)
            for grant in self._host.state.get_sandbox_grants(box)
            if isinstance(grant, str) and grant
        ]

    # -- delivering ---------------------------------------------------------------

    def _deliver(self, record: _Box, path: str) -> Delivery:
        reason = self.capable()
        placement = None
        if not reason:
            # Again, as build_plan does for a static grant: state.json is a
            # file on disk like any other.
            reason = sandboxplan.guard_path(
                path,
                record.home,
                sandboxplan.protected_paths(self._host.app_id),
                sandboxplan.sandbox_root(),
            )
        if not reason:
            placement = place(record, path)
            if placement is None:
                reason = REASON_UNPLACEABLE
        proc = None
        if not reason:
            proc, reason = self._mount(path, placement)
        if reason or proc is None:
            reason = reason or REASON_UNPLACEABLE
            with self._lock:
                record.pending[path] = reason
            log.info("sandbox grants: %s waits for a restart in %s: %s", path, record.box, reason)
            return Delivery(record.box, path, PENDING, "", False, reason)
        linked = placement.linked
        if placement.link is not None:
            linked = self._make_link(record, placement)
        with self._lock:
            record.pending.pop(path, None)
            record.live[path] = _Live(path, placement, proc, linked)
            registered = self._boxes.get(record.box) is record
        if not registered:
            # The box went while this was mounting: nothing may stay behind.
            with self._lock:
                live = record.live.pop(path)
            self._retire(record, live)
            return Delivery(record.box, path, PENDING, "", False, "the sandbox is gone")
        log.info("sandbox grants: %s is live in %s at %s", path, record.box, placement.inside)
        return Delivery(record.box, path, LIVE, placement.inside, linked, "")

    def _mounted(self, point: str, fstype: str | None = None) -> bool:
        spellings = {point, os.path.realpath(point)}
        try:
            mounts = parse_mountinfo(self._mountinfo())
        except OSError:
            return False
        return any(
            mount.point in spellings and (fstype is None or mount.fstype == fstype)
            for mount in mounts
        )

    def _make_point(self, placement: Placement) -> None:
        """The mount point, created under its base one component at a time
        and by file descriptor. The carrier and the anchors are the host's
        own — the box can't write them — so nothing should ever be in the
        way; a symlink or a file there is refused all the same."""
        if not sandboxplan.within(placement.base, placement.mount_point):
            raise OSError(f"{placement.mount_point} is outside {placement.base}")
        parts = os.path.relpath(placement.mount_point, placement.base).split("/")
        fd = os.open(placement.base, sandboxplan.DIR_FLAGS)
        try:
            for name in parts:
                if name in ("", ".", ".."):
                    raise OSError(f"not a mount point: {placement.mount_point}")
                try:
                    os.mkdir(name, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                deeper = os.open(name, sandboxplan.DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = deeper
        finally:
            os.close(fd)

    def _mount(self, path: str, placement: Placement):
        """(the server, "") once the mount is there, else (None, why)."""
        point = placement.mount_point
        try:
            self._make_point(placement)
        except OSError as err:
            return None, f"couldn't make the mount point: {err}"
        if self._mounted(point):
            self._unmount(point, None)  # a stale one: never mount over a mount
        argv = [
            self._bindfs() or "bindfs",
            "-f",
            "--no-allow-other",
            "-o",
            f"fsname=collins:{os.getpid()}",
            path,
            point,
        ]
        try:
            proc = self._spawn(argv)
        except OSError as err:
            self._drop_point(placement)
            return None, f"couldn't start bindfs: {err}"
        deadline = self._clock() + MOUNT_TIMEOUT_S
        while not self._mounted(point, BINDFS_TYPE):
            if proc.poll() is not None:
                reason = _first_line(proc) or f"bindfs exited with status {proc.returncode}"
                break
            if self._clock() >= deadline:
                reason = "the mount didn't appear"
                break
            self._sleep(MOUNT_POLL_S)
        else:
            return proc, ""
        # No mount, or half of one: the server stopped, whatever appeared
        # taken away again.
        self._stop(proc, grace=0.0)
        if self._mounted(point):
            self._unmount(point, None)
        self._drop_point(placement)
        return None, reason

    def _make_link(self, record: _Box, placement: Placement) -> bool:
        """The symlink at the real path in the box's home, pointing at the
        mount in the carrier; whether it is there. Walked from the home
        down by file descriptor: every component opened relative to the
        last with O_NOFOLLOW, what is missing created. At the last
        component a symlink is replaced, an empty directory (a static
        bind's old mount point) is removed and replaced, a missing name is
        created. Anything else in the way, or a component that is not a
        directory, leaves the grant where it is — inside the carrier."""
        parts = placement.link or ()
        if not parts or any(name in ("", ".", "..") for name in parts):
            return False
        try:
            fd = os.open(record.home_dir, sandboxplan.DIR_FLAGS)
        except OSError:
            return False
        try:
            for name in parts[:-1]:
                try:
                    deeper = os.open(name, sandboxplan.DIR_FLAGS, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(name, 0o755, dir_fd=fd)
                    deeper = os.open(name, sandboxplan.DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = deeper
            name = parts[-1]
            try:
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            except FileNotFoundError:
                mode = None
            if mode is not None:
                if stat.S_ISLNK(mode):
                    os.unlink(name, dir_fd=fd)
                elif stat.S_ISDIR(mode):
                    os.rmdir(name, dir_fd=fd)  # OSError when it holds anything
                else:
                    return False
            os.symlink(placement.inside, name, dir_fd=fd)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def _remove_link(self, record: _Box, placement: Placement) -> None:
        """Take the link away again — only while it still is a symlink
        whose target is this grant's place in the carrier. One that points
        elsewhere is the agent's, and stays."""
        parts = placement.link or ()
        if not parts:
            return
        try:
            fd = os.open(record.home_dir, sandboxplan.DIR_FLAGS)
        except OSError:
            return
        try:
            for name in parts[:-1]:
                deeper = os.open(name, sandboxplan.DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = deeper
            name = parts[-1]
            mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode) and os.readlink(name, dir_fd=fd) == placement.inside:
                os.unlink(name, dir_fd=fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    # -- taking away --------------------------------------------------------------

    def _wait(self, proc, seconds: float) -> bool:
        """Whether *proc* has exited, giving it up to *seconds*."""
        deadline = self._clock() + seconds
        while proc.poll() is None:
            if self._clock() >= deadline:
                return False
            self._sleep(MOUNT_POLL_S)
        return True

    def _stop(self, proc, grace: float) -> None:
        """The server's end: its own exit within *grace*, else SIGTERM,
        else SIGKILL. The lazy unmount takes the mount out of every
        namespace at once but leaves the server serving a file a process
        inside still holds open; ending the server is what cuts that."""
        if proc is None or self._wait(proc, grace):
            return
        try:
            proc.terminate()
        except OSError:
            pass
        if self._wait(proc, TERM_GRACE_S):
            return
        try:
            proc.kill()
        except OSError:
            pass
        self._wait(proc, TERM_GRACE_S)

    def _unmount(self, point: str, proc) -> None:
        fusermount = self._fusermount()
        if fusermount:
            self._run([fusermount, "-u", "-z", point])
        self._stop(proc, UNMOUNT_GRACE_S)

    def _drop_point(self, placement: Placement) -> None:
        """Remove the (empty) mount point. Never anything mounted: rmdir
        refuses a mount point, and nothing else is tried."""
        if self._mounted(placement.mount_point):
            return
        try:
            os.rmdir(placement.mount_point)
        except OSError:
            pass

    def _retire(self, record: _Box, live: _Live) -> None:
        if live.placement.link is not None:
            self._remove_link(record, live.placement)
        self._unmount(live.placement.mount_point, live.proc)
        self._drop_point(live.placement)
        log.info("sandbox grants: %s left %s", live.path, record.box)

    def _retire_stale(self, record: _Box) -> None:
        with self._lock:
            stale, record.stale = record.stale, []
        for live in stale:
            self._retire(record, live)

    def _retire_all(self, record: _Box) -> None:
        self._retire_stale(record)
        with self._lock:
            lives = list(record.live.values())
            record.live.clear()
            record.pending.clear()
        for live in lives:
            self._retire(record, live)

    # -- what a dead instance left ------------------------------------------------

    def sweep_mounts(self) -> int:
        """Unmount every bindfs mount under the sandbox root that an
        instance no longer running made (`collins:<pid>` as its source,
        and no such process); how many went. A mount whose pid is alive is
        another running instance's — or this one's — and is left alone, and
        so is any mount with another source. Runs once at startup, before
        the boxes are swept: a box with a mount under it is never removed."""
        root = sandboxplan.sandbox_root()
        spellings = {root, os.path.realpath(root)}
        try:
            mounts = parse_mountinfo(self._mountinfo())
        except OSError:
            return 0
        fusermount = self._fusermount()
        swept = 0
        for mount in mounts:
            if mount.fstype != BINDFS_TYPE:
                continue
            if not any(sandboxplan.within(spelled, mount.point) for spelled in spellings):
                continue
            owner = _OWNER.match(mount.source)
            if owner is None or self._pid_alive(int(owner.group(1))):
                continue
            if fusermount and self._run([fusermount, "-u", "-z", mount.point]) == 0:
                swept += 1
        return swept
