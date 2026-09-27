# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Live grants (collins.sandboxgrants): where a grant is placed, how it is
delivered to the running boxes of a project and taken away again, the link
in the box's home, and what a dead instance left — over a fake spawn, a
fake mount table and a fake clock, so nothing here needs FUSE. One test at
the end runs the real thing, and skips where the machine can't."""

import io
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from collins import sandboxgrants, sandboxplan
from collins.sandboxgrants import LEAVING, LIVE, PENDING, STATIC, GrantMounts

BOX = "0123456789abcdef0123456789abcdef"
BOX2 = "fedcba9876543210fedcba9876543210"
BOX3 = "00000000000000000000000000000000"
CARRIER = sandboxplan.CARRIER_DEST


class _State:
    """The grants a project holds, keyed as AppState keys them."""

    def __init__(self, grants=None):
        self.grants = {k: list(v) for k, v in (grants or {}).items()}

    def get_sandbox_grants(self, key):
        return list(self.grants.get(key) or [])

    def set_sandbox_grants(self, key, grants):
        if grants:
            self.grants[key] = list(grants)
        else:
            self.grants.pop(key, None)

    def get_setting(self, key):
        return False

    def sandbox_boxes(self):
        return set()


class _Table:
    """A mount table in /proc/self/mountinfo's format."""

    def __init__(self, root_options="shared:1"):
        self.root_options = root_options
        self.mounts = {}  # mount point -> (fstype, source)
        self.lock = threading.Lock()

    def text(self):
        options = f" {self.root_options}" if self.root_options else ""
        lines = [f"36 35 98:0 / / rw,noatime{options} - ext4 /dev/sda1 rw"]
        with self.lock:
            for n, (point, (fstype, source)) in enumerate(self.mounts.items()):
                shown = point.replace(" ", "\\040")
                lines.append(
                    f"{400 + n} 36 0:{60 + n} / {shown} rw,nosuid shared:{230 + n} - "
                    f"{fstype} {source} rw,user_id=1000"
                )
        return "\n".join(lines) + "\n"

    def add(self, point, source, fstype="fuse.bindfs"):
        with self.lock:
            self.mounts[point] = (fstype, source)

    def remove(self, point):
        with self.lock:
            return self.mounts.pop(point, None) is not None

    def points(self):
        with self.lock:
            return sorted(self.mounts)


class _Proc:
    """A bindfs server that is not one."""

    def __init__(self, returncode=None, stderr=b"", stubborn=False):
        self.returncode = returncode
        self.stderr = io.BytesIO(stderr)
        self.stubborn = stubborn  # ignores everything short of SIGKILL
        self.signals = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.signals.append("TERM")
        if not self.stubborn:
            self.returncode = -15

    def kill(self):
        self.signals.append("KILL")
        self.returncode = -9


class _World:
    """Everything a GrantMounts is handed instead of the machine."""

    def __init__(self, tmp_path, state=None, root_options="shared:1"):
        self.table = _Table(root_options)
        self.state = state or _State()
        self.now = 0.0
        self.spawned = []  # (argv, the thread's name)
        self.procs = []
        self.ran = []  # the fusermount3 commands
        self.behaviour = "mount"  # what the next spawn does
        self.exits_on_unmount = True
        self.bindfs = str(tmp_path / "bin" / "bindfs")
        self.fusermount = str(tmp_path / "bin" / "fusermount3")
        self.fuse_device = str(tmp_path / "dev-fuse")
        os.makedirs(tmp_path / "bin", exist_ok=True)
        open(self.fuse_device, "w").close()
        self.alive = {os.getpid()}

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def spawn(self, argv):
        self.spawned.append((list(argv), threading.current_thread().name))
        if self.behaviour == "exits":
            proc = _Proc(returncode=1, stderr=b"\nfuse: bad mount point: Permission denied\nmore\n")
        elif self.behaviour == "silent-exit":
            proc = _Proc(returncode=3)
        elif self.behaviour == "hangs":
            proc = _Proc(stubborn=True)
        elif self.behaviour == "half":
            # The mount appears as something else, and the server stays up.
            proc = _Proc()
            self.table.add(argv[-1], "collins:half", fstype="fuse")
        elif self.behaviour == "raises":
            raise OSError("no such file")
        else:
            proc = _Proc()
            source = next(a for a in argv if a.startswith("fsname=")).split("=", 1)[1]
            self.table.add(os.path.realpath(argv[-1]), source)
        proc.point = os.path.realpath(argv[-1])
        self.procs.append(proc)
        return proc

    def run(self, argv):
        self.ran.append(list(argv))
        point = argv[-1]
        found = self.table.remove(point) or self.table.remove(os.path.realpath(point))
        if found and self.exits_on_unmount:
            for proc in self.procs:
                if getattr(proc, "point", None) == os.path.realpath(point) and not proc.stubborn:
                    if proc.returncode is None:
                        proc.returncode = 0
        return 0 if found else 1

    def grants(self, host=None, **kw):
        host = host or sandboxplan.SandboxHost("com.example.App", self.state)
        args = dict(
            bindfs=self.bindfs,
            fusermount=self.fusermount,
            spawn=self.spawn,
            mountinfo=self.table.text,
            run=self.run,
            clock=self.clock,
            sleep=self.sleep,
            pid_alive=lambda pid: pid in self.alive,
            fuse_device=self.fuse_device,
        )
        args.update(kw)
        return GrantMounts(host, **args)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home with a project, two directories to grant, and a sandbox
    root of the test's own."""
    home = tmp_path / "home"
    for made in ("work/repo", "work/lib", "work/other", ".ssh"):
        (home / made).mkdir(parents=True)
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(tmp_path / "boxes"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(sandboxplan.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("COLLINS_BINDFS", raising=False)
    monkeypatch.delenv("COLLINS_FUSERMOUNT", raising=False)
    return home


@pytest.fixture
def world(tmp_path, home):
    made = []
    world = _World(tmp_path)
    real = world.grants

    def grants(*args, **kw):
        made.append(real(*args, **kw))
        return made[-1]

    world.grants = grants
    yield world
    for one in made:
        one.shutdown()


def _plan(tmp_path, home, box=BOX, key=None, grants=(), anchors=("/mnt",), binds=()):
    """A launched plan, as far as the live grants read one; its box made."""
    top = tmp_path / "boxes" / box
    for made in ("home", "grants", *(f"anchors/{os.path.basename(a)}" for a in anchors)):
        (top / made).mkdir(parents=True, exist_ok=True)
    args = ["--bind", str(top / "home"), str(home)]
    for bound in (*grants, *binds):
        args += ["--bind-try", str(bound), str(bound)]
    return {
        "version": 2,
        "bwrap_args": args,
        "setenv": {"HOME": str(home)},
        "workspace": str(home / "work" / "repo"),
        "inputs": {
            "workspace": str(home / "work" / "repo"),
            "box": box,
            "grants_key": key or str(home / "work" / "repo"),
            "grants": [str(g) for g in grants],
            "carrier": str(top / "grants"),
            "anchors": [[str(top / "anchors" / os.path.basename(a)), a] for a in anchors],
            "sandbox_home": str(top / "home"),
        },
    }


def _call(method, *args):
    """Run one of the queued operations and hand back what `done` got."""
    got = []
    landed = threading.Event()
    seen = []

    def done(deliveries):
        seen.append(threading.current_thread().name)
        got.extend(deliveries)
        landed.set()

    method(*args, done)
    assert landed.wait(10), "the worker never answered"
    assert seen == ["sandbox-grants"]  # done runs on the worker thread
    return got


def _settle(grants):
    """Wait until the worker has done everything queued so far."""
    landed = threading.Event()
    grants._submit(lambda: [], lambda _d: landed.set())
    assert landed.wait(10)


def _slot(path):
    return sandboxgrants.slot_name(str(path))


# -- can anything be delivered live? ---------------------------------------------


def test_capable_names_what_is_missing_in_order(tmp_path, home, monkeypatch):
    world = _World(tmp_path)
    grants = world.grants()
    assert grants.capable() == ""
    grants.shutdown()
    # Each of the reasons, in the order they are checked.
    monkeypatch.setenv("COLLINS_BINDFS", str(tmp_path / "missing"))
    monkeypatch.setenv("COLLINS_FUSERMOUNT", str(tmp_path / "missing"))
    nothing = world.grants(bindfs=None, fusermount=None, fuse_device=str(tmp_path / "no-fuse"))
    assert nothing.capable() == sandboxgrants.REASON_NO_BINDFS == "bindfs not installed"
    nothing.shutdown()
    no_fusermount = world.grants(fusermount=None, fuse_device=str(tmp_path / "no-fuse"))
    assert no_fusermount.capable() == sandboxgrants.REASON_NO_FUSERMOUNT
    assert sandboxgrants.REASON_NO_FUSERMOUNT == "fusermount3 not installed"
    no_fusermount.shutdown()
    no_fuse = world.grants(fuse_device=str(tmp_path / "no-fuse"))
    assert no_fuse.capable() == sandboxgrants.REASON_NO_FUSE
    no_fuse.shutdown()
    private = _World(tmp_path, root_options="")
    no_propagation = private.grants()
    assert no_propagation.capable() == sandboxgrants.REASON_NO_PROPAGATION
    assert sandboxgrants.REASON_NO_PROPAGATION == (
        "mounts don't propagate from the sandbox directory"
    )
    no_propagation.shutdown()


def test_capable_reads_the_environments_tools(tmp_path, home, monkeypatch):
    world = _World(tmp_path)
    tool = tmp_path / "bin" / "fake-tool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("COLLINS_BINDFS", str(tool))
    monkeypatch.setenv("COLLINS_FUSERMOUNT", str(tool))
    grants = world.grants(bindfs=None, fusermount=None)
    assert grants.capable() == ""
    # Settled once, and kept.
    monkeypatch.setenv("COLLINS_BINDFS", "/nonexistent")
    assert grants.capable() == ""
    grants.shutdown()
    again = world.grants(bindfs=None, fusermount=None)
    assert again.capable() == sandboxgrants.REASON_NO_BINDFS
    again.shutdown()


def test_propagation_is_read_off_the_mount_holding_the_root(tmp_path, home):
    root = os.path.realpath(sandboxplan.sandbox_root())
    shared = (
        "36 35 98:0 / / rw,noatime - ext4 /dev/sda1 rw\n"
        f"40 36 0:41 / {tmp_path} rw shared:7 master:2 - tmpfs tmpfs rw\n"
        f"41 36 0:42 / {root}-other rw - tmpfs tmpfs rw\n"
    )
    private = (
        "36 35 98:0 / / rw,noatime shared:1 - ext4 /dev/sda1 rw\n"
        f"40 36 0:41 / {tmp_path} rw master:2 - tmpfs tmpfs rw\n"
    )
    world = _World(tmp_path)
    grants = world.grants(mountinfo=lambda: shared)
    assert grants.capable() == ""
    grants.shutdown()
    # The longest prefix decides, not the root of the tree.
    grants = world.grants(mountinfo=lambda: private)
    assert grants.capable() == sandboxgrants.REASON_NO_PROPAGATION
    grants.shutdown()

    def unreadable():
        raise OSError("no /proc")

    grants = world.grants(mountinfo=unreadable)
    assert grants.capable() == sandboxgrants.REASON_NO_PROPAGATION
    grants.shutdown()


def test_parse_mountinfo():
    text = (
        "36 35 98:0 / / rw,noatime shared:1 - ext4 /dev/sda1 rw\n"
        "412 36 0:60 / /home/u/my\\040repo rw,nosuid - fuse.bindfs collins:123 rw,user_id=1000\n"
        "413 36 0:61 / /mnt/x rw master:4 propagate_from:2 - fuse.bindfs collins:9 rw\n"
        "no separator here\n"
        "1 2 - ext4\n"
    )
    assert sandboxgrants.parse_mountinfo(text) == [
        sandboxgrants.Mount("/", "ext4", "/dev/sda1", True),
        sandboxgrants.Mount("/home/u/my repo", "fuse.bindfs", "collins:123", False),
        sandboxgrants.Mount("/mnt/x", "fuse.bindfs", "collins:9", False),
    ]
    assert any(m.point == "/" for m in sandboxgrants.parse_mountinfo(sandboxgrants.read_mountinfo()))


# -- placement -------------------------------------------------------------------


def test_slot_names():
    slot = sandboxgrants.slot_name
    assert slot("/home/u/dev/lib").startswith("lib-")
    assert slot("/home/u/my repo").startswith("my_repo-")
    assert slot("/home/u/.hidden").startswith("_hidden-")
    assert slot("/home/u/café & co").startswith("caf__&_co-".replace("&", "_"))
    long = slot("/home/u/" + "x" * 100)
    assert long.startswith("x" * 40 + "-") and len(long) == 40 + 1 + 8
    # One basename, two directories: the digests differ.
    one, two = slot("/home/u/a/lib"), slot("/home/u/b/lib")
    assert one != two and one[:4] == two[:4] == "lib-"
    for name in (one, two, long, slot("/home/u/.hidden"), slot("/home/u/my repo")):
        assert "/" not in name and not name.startswith(".")
        digest = name.rsplit("-", 1)[1]
        assert len(digest) == 8 and all(c in "0123456789abcdef" for c in digest)
    assert slot("/home/u/lib") == slot("/home/u/lib")  # and stable


def _record(grants, box=BOX):
    with grants._lock:
        return grants._boxes[box]


def test_placement(tmp_path, home, world):
    lib = home / "work" / "lib"
    grants = world.grants()
    plan = _plan(tmp_path, home, anchors=("/mnt", "/srv"), binds=(home / ".cargo",))
    grants.register(plan)
    _settle(grants)
    record = _record(grants)
    top = tmp_path / "boxes" / BOX
    # Under $HOME: a slot in the carrier, and a link wanted at the real path.
    placed = sandboxgrants.place(record, str(lib))
    assert placed == sandboxgrants.Placement(
        mount_point=str(top / "grants" / _slot(lib)),
        base=str(top / "grants"),
        inside=f"{CARRIER}/{_slot(lib)}",
        link=("work", "lib"),
        linked=False,
    )
    # Under an anchor the box emitted: the real path, under the anchor's
    # host directory, and no link.
    placed = sandboxgrants.place(record, "/mnt/data/x")
    assert placed == sandboxgrants.Placement(
        mount_point=str(top / "anchors" / "mnt" / "data" / "x"),
        base=str(top / "anchors" / "mnt"),
        inside="/mnt/data/x",
        link=None,
        linked=True,
    )
    assert sandboxgrants.place(record, "/srv/www").base == str(top / "anchors" / "srv")
    # Anything else can't be placed in a running box.
    for path in ("/opt/x", "/var/lib/x", "/media/disk", "/mnt", "/mntx/y", str(home)):
        assert sandboxgrants.place(record, path) is None, path
    # Where a mount of the plan covers the spot, a link would sit under it
    # unseen: the carrier only.
    placed = sandboxgrants.place(record, str(home / ".cargo" / "registry"))
    assert placed.link is None and placed.linked is False
    assert placed.inside.startswith(CARRIER + "/registry-")
    # Every mount point lies in the box's grants/ or anchors/. Never the home.
    for path in (str(lib), "/mnt/data/x", str(home / ".cargo" / "registry")):
        point = sandboxgrants.place(record, path).mount_point
        assert point.startswith((str(top / "grants") + "/", str(top / "anchors") + "/"))
        assert not point.startswith(str(top / "home"))


# -- delivering ------------------------------------------------------------------


def test_a_grant_is_delivered_live(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = home / "work" / "repo"
    world.state.grants = {str(ws): [str(lib)]}
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    _settle(grants)
    # The registration itself delivered what the plan lacked.
    top = tmp_path / "boxes" / BOX
    point = str(top / "grants" / _slot(lib))
    assert world.table.points() == [point]
    assert grants.status(BOX, str(lib)) == LIVE
    assert grants.live_paths(BOX) == [str(lib)]
    delivery = grants.delivery(BOX, str(lib))
    assert delivery == sandboxgrants.Delivery(
        BOX, str(lib), LIVE, f"{CARRIER}/{_slot(lib)}", True, ""
    )
    # bindfs, in the foreground, never allow_other, tagged with this pid.
    argv, thread = world.spawned[0]
    assert argv == [
        world.bindfs, "-f", "--no-allow-other", "-o", f"fsname=collins:{os.getpid()}",
        str(lib), point,
    ]
    assert thread == "sandbox-grants"
    assert os.path.isdir(point)
    assert oct(os.stat(point).st_mode & 0o777) == "0o700"
    # The link at the real path, in the box's home, to the carrier.
    link = top / "home" / "work" / "lib"
    assert link.is_symlink() and os.readlink(link) == f"{CARRIER}/{_slot(lib)}"
    # A second allow of the same directory finds the box holding it.
    assert _call(grants.allow, str(ws), str(lib)) == []
    assert len(world.spawned) == 1


def test_a_server_that_exits_leaves_the_grant_pending(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = home / "work" / "repo"
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    world.behaviour = "exits"
    (delivery,) = _call(grants.allow, str(ws), str(lib))
    assert delivery == sandboxgrants.Delivery(
        BOX, str(lib), PENDING, "", False, "fuse: bad mount point: Permission denied"
    )
    assert grants.status(BOX, str(lib)) == PENDING
    assert grants.delivery(BOX, str(lib)) == delivery
    assert grants.live_paths(BOX) == []
    # Nothing left mounted, no mount point, no link.
    top = tmp_path / "boxes" / BOX
    assert world.table.points() == []
    assert os.listdir(top / "grants") == []
    assert not os.path.lexists(top / "home" / "work" / "lib")
    # A server with nothing to say still has a reason.
    world.behaviour = "silent-exit"
    (delivery,) = _call(grants.allow, str(ws), str(home / "work" / "other"))
    assert delivery.status == PENDING and delivery.reason == "bindfs exited with status 3"
    # One that can't be started at all.
    world.behaviour = "raises"
    (delivery,) = _call(grants.allow, str(ws), str(home / "work" / "third"))
    assert delivery.status == PENDING and "couldn't start bindfs" in delivery.reason
    # And the next one that works is LIVE, the reason forgotten.
    world.behaviour = "mount"
    (delivery,) = _call(grants.allow, str(ws), str(lib))
    assert delivery.status == LIVE and delivery.reason == ""
    assert grants.status(BOX, str(lib)) == LIVE


def test_a_mount_that_never_appears_is_given_up_on(tmp_path, home, world):
    lib = home / "work" / "lib"
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    world.behaviour = "hangs"
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), str(lib))
    assert delivery.status == PENDING and delivery.reason == "the mount didn't appear"
    # Five seconds of looking, then the child killed: asked first, then not.
    assert world.now >= sandboxgrants.MOUNT_TIMEOUT_S
    assert world.procs[0].signals == ["TERM", "KILL"]
    assert world.procs[0].returncode == -9
    assert os.listdir(tmp_path / "boxes" / BOX / "grants") == []


def test_a_mount_that_half_appeared_is_unmounted(tmp_path, home, world):
    lib = home / "work" / "lib"
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    world.behaviour = "half"
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), str(lib))
    assert delivery.status == PENDING
    point = str(tmp_path / "boxes" / BOX / "grants" / _slot(lib))
    assert world.table.points() == []
    assert [world.fusermount, "-u", "-z", point] in world.ran
    assert world.procs[0].returncode is not None


def test_a_stale_mount_at_the_mount_point_goes_first(tmp_path, home, world):
    lib = home / "work" / "lib"
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    point = str(tmp_path / "boxes" / BOX / "grants" / _slot(lib))
    os.makedirs(point)
    world.table.add(point, "collins:1")
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), str(lib))
    assert delivery.status == LIVE
    assert world.ran[0] == [world.fusermount, "-u", "-z", point]  # before the spawn
    assert world.table.mounts[point] == ("fuse.bindfs", f"collins:{os.getpid()}")


def test_what_cant_be_delivered_is_pending_with_its_reason(tmp_path, home, world, monkeypatch):
    ws = str(home / "work" / "repo")
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    # Nowhere to put it in a running box.
    (delivery,) = _call(grants.allow, ws, "/opt/thing")
    assert (delivery.status, delivery.reason) == (PENDING, "can't be added to a running sandbox")
    # The guard again, as a launch applies it: state.json is a file on disk.
    (delivery,) = _call(grants.allow, ws, str(home / ".ssh"))
    assert delivery.status == PENDING and "reaches" in delivery.reason and ".ssh" in delivery.reason
    (delivery,) = _call(grants.allow, ws, str(tmp_path / "boxes" / BOX2 / "home"))
    assert (delivery.status, delivery.reason) == (PENDING, "reaches the sandbox homes")
    assert world.spawned == []
    # A machine that can't at all: every grant, with that reason.
    private = _World(tmp_path, root_options="")
    none = private.grants()
    none.register(_plan(tmp_path, home, box=BOX3))
    (delivery,) = _call(none.allow, ws, str(home / "work" / "lib"))
    assert (delivery.status, delivery.reason) == (
        PENDING, "mounts don't propagate from the sandbox directory",
    )
    assert private.spawned == []
    none.shutdown()


def test_an_anchored_grant_mounts_at_its_real_path(tmp_path, home, world):
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), "/mnt/data/x")
    assert delivery == sandboxgrants.Delivery(BOX, "/mnt/data/x", LIVE, "/mnt/data/x", True, "")
    top = tmp_path / "boxes" / BOX
    assert world.table.points() == [str(top / "anchors" / "mnt" / "data" / "x")]
    # No link: nothing of it in the home.
    assert os.listdir(top / "home") == []
    _call(grants.revoke, str(home / "work" / "repo"), "/mnt/data/x")
    assert world.table.points() == []
    assert not (top / "anchors" / "mnt" / "data" / "x").exists()


def test_a_mount_point_is_never_made_through_a_link(tmp_path, home, world):
    """The carrier and the anchors are the host's own, so nothing should be
    in the way — and if something is, the mount is not made there."""
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    top = tmp_path / "boxes" / BOX
    victim = tmp_path / "victim"
    victim.mkdir()
    (top / "anchors" / "mnt" / "data").symlink_to(victim)
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), "/mnt/data/x")
    assert delivery.status == PENDING and "mount point" in delivery.reason
    assert os.listdir(victim) == []
    assert world.spawned == []


# -- the link --------------------------------------------------------------------


def _placed(grants, path, box=BOX):
    return sandboxgrants.place(_record(grants, box), str(path))


def test_the_link_is_made_through_missing_parents(tmp_path, home, world):
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    deep = home / "dev" / "deeper" / "still" / "lib"
    placement = _placed(grants, deep)
    record = _record(grants)
    assert grants._make_link(record, placement) is True
    link = tmp_path / "boxes" / BOX / "home" / "dev" / "deeper" / "still" / "lib"
    assert os.readlink(link) == placement.inside
    assert (link.parent).is_dir() and not link.parent.is_symlink()
    # Again: a symlink there is replaced, whatever it pointed at.
    link.unlink()
    link.symlink_to(tmp_path)
    assert grants._make_link(record, placement) is True
    assert os.readlink(link) == placement.inside
    # An empty directory — a static bind's old mount point — is replaced too.
    link.unlink()
    link.mkdir()
    assert grants._make_link(record, placement) is True
    assert link.is_symlink() and os.readlink(link) == placement.inside


def test_the_link_gives_way_to_what_it_cant_replace(tmp_path, home, world):
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    record = _record(grants)
    box_home = tmp_path / "boxes" / BOX / "home"
    # A directory that holds something.
    placement = _placed(grants, home / "work" / "lib")
    (box_home / "work" / "lib").mkdir(parents=True)
    (box_home / "work" / "lib" / "kept.txt").write_text("the agent's")
    assert grants._make_link(record, placement) is False
    assert (box_home / "work" / "lib" / "kept.txt").read_text() == "the agent's"
    # A file.
    placement = _placed(grants, home / "work" / "other")
    (box_home / "work" / "other").write_text("a file")
    assert grants._make_link(record, placement) is False
    assert (box_home / "work" / "other").read_text() == "a file"
    # A parent that is a file.
    placement = _placed(grants, home / "work" / "other" / "deeper")
    assert grants._make_link(record, placement) is False
    # A home that is not there, or is itself a link.
    gone = sandboxgrants._Box(
        BOX, "k", (), str(tmp_path / "c"), (), str(tmp_path / "no-home"), str(home), ()
    )
    assert grants._make_link(gone, placement) is False
    (tmp_path / "linked-home").symlink_to(box_home)
    linked = sandboxgrants._Box(
        BOX, "k", (), str(tmp_path / "c"), (), str(tmp_path / "linked-home"), str(home), ()
    )
    assert grants._make_link(linked, _placed(grants, home / "fresh")) is False
    assert not os.path.lexists(box_home / "fresh")


def test_the_link_never_follows_a_planted_parent(tmp_path, home, world):
    """A parent that is a symlink to a directory elsewhere: no link, and
    nothing created in that directory."""
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    box_home = tmp_path / "boxes" / BOX / "home"
    victim = tmp_path / "victim"
    (victim / "deep").mkdir(parents=True)
    (victim / "precious.txt").write_text("keep")
    before = sorted(str(p.relative_to(victim)) for p in victim.rglob("*"))
    (box_home / "work").symlink_to(victim)
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), str(home / "work" / "lib"))
    # Mounted all the same, in the carrier — where it answers.
    assert delivery.status == LIVE and delivery.linked is False
    assert delivery.inside == f"{CARRIER}/{_slot(home / 'work' / 'lib')}"
    assert sorted(str(p.relative_to(victim)) for p in victim.rglob("*")) == before
    assert (box_home / "work").is_symlink()
    # Deeper: the planted link one level down.
    (box_home / "dev").mkdir()
    (box_home / "dev" / "mid").symlink_to(victim / "deep")
    (delivery,) = _call(
        grants.allow, str(home / "work" / "repo"), str(home / "dev" / "mid" / "new" / "lib")
    )
    assert delivery.status == LIVE and delivery.linked is False
    assert sorted(str(p.relative_to(victim)) for p in victim.rglob("*")) == before
    assert (victim / "precious.txt").read_text() == "keep"


def test_removing_the_link_leaves_one_that_points_elsewhere(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = str(home / "work" / "repo")
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    (delivery,) = _call(grants.allow, ws, str(lib))
    assert delivery.linked is True
    link = tmp_path / "boxes" / BOX / "home" / "work" / "lib"
    # The agent can't write the carrier, but it owns its home: the link
    # repointed is the agent's, and not Collins' to remove.
    link.unlink()
    link.symlink_to("/somewhere/else")
    _call(grants.revoke, ws, str(lib))
    assert link.is_symlink() and os.readlink(link) == "/somewhere/else"
    assert world.table.points() == []
    # Its own link goes.
    link.unlink()
    (delivery,) = _call(grants.allow, ws, str(lib))
    assert link.is_symlink()
    _call(grants.revoke, ws, str(lib))
    assert not os.path.lexists(link)
    assert link.parent.is_dir()
    # A directory where the link was is left alone as well.
    (delivery,) = _call(grants.allow, ws, str(lib))
    link.unlink()
    link.mkdir()
    (link / "kept").write_text("x")
    _call(grants.revoke, ws, str(lib))
    assert (link / "kept").read_text() == "x"


# -- the boxes of a project ------------------------------------------------------


def test_allow_reaches_the_boxes_of_the_project_and_no_other(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = home / "work" / "repo"
    wt = ws / ".claude" / "worktrees" / "brave-otter"
    wt.mkdir(parents=True)
    grants = world.grants()
    grants.register(_plan(tmp_path, home, BOX))
    grants.register(_plan(tmp_path, home, BOX2))  # a second session of the project
    grants.register(_plan(tmp_path, home, BOX3, key=str(home / "work" / "other")))
    # Allowed from a worktree of the project: the repository's boxes.
    delivered = _call(grants.allow, str(wt), str(lib))
    assert sorted(d.box for d in delivered) == sorted([BOX, BOX2])
    assert all(d.status == LIVE and d.linked for d in delivered)
    assert world.table.points() == sorted(
        str(tmp_path / "boxes" / box / "grants" / _slot(lib)) for box in (BOX, BOX2)
    )
    assert grants.status(BOX3, str(lib)) == PENDING
    assert grants.live_paths(BOX3) == []
    assert os.listdir(tmp_path / "boxes" / BOX3 / "grants") == []
    assert os.listdir(tmp_path / "boxes" / BOX3 / "home") == []
    assert grants.delivery(BOX3, str(lib)) is None
    # A box nobody registered knows nothing.
    assert grants.status("f" * 32, str(lib)) == PENDING
    assert grants.delivery("f" * 32, str(lib)) is None
    assert grants.registered(BOX) and not grants.registered("f" * 32)


def test_a_box_that_holds_the_path_statically_is_skipped(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = str(home / "work" / "repo")
    world.state.grants = {ws: [str(lib)]}
    grants = world.grants()
    grants.register(_plan(tmp_path, home, BOX, grants=(lib,)))  # launched with it
    grants.register(_plan(tmp_path, home, BOX2))  # launched before it was allowed
    _settle(grants)
    assert grants.status(BOX, str(lib)) == STATIC
    assert grants.status(BOX2, str(lib)) == LIVE
    assert grants.delivery(BOX, str(lib)) is None
    assert len(world.spawned) == 1
    assert _call(grants.allow, ws, str(lib)) == []
    assert len(world.spawned) == 1
    assert os.listdir(tmp_path / "boxes" / BOX / "grants") == []


def test_revoke(tmp_path, home, world):
    lib = home / "work" / "lib"
    other = home / "work" / "other"
    ws = str(home / "work" / "repo")
    world.state.grants = {ws: [str(lib), str(other)]}
    grants = world.grants()
    grants.register(_plan(tmp_path, home, BOX, grants=(other,)))
    _settle(grants)
    assert grants.status(BOX, str(lib)) == LIVE
    assert grants.status(BOX, str(other)) == STATIC
    point = str(tmp_path / "boxes" / BOX / "grants" / _slot(lib))
    # Live: unmounted, the link gone, the mount point gone.
    world.state.grants = {ws: [str(other)]}
    assert _call(grants.revoke, ws, str(lib)) == []
    assert world.ran == [[world.fusermount, "-u", "-z", point]]
    assert world.table.points() == []
    assert not os.path.exists(point)
    assert not os.path.lexists(tmp_path / "boxes" / BOX / "home" / "work" / "lib")
    assert grants.status(BOX, str(lib)) == PENDING
    assert grants.live_paths(BOX) == []
    assert world.procs[0].signals == []  # it left by itself
    # Static: nothing can be done to the running box.
    world.state.grants = {}
    assert _call(grants.revoke, ws, str(other)) == []
    assert grants.status(BOX, str(other)) == LEAVING
    assert len(world.ran) == 1
    # Revoking what was only pending forgets the reason.
    world.behaviour = "exits"
    _call(grants.allow, ws, str(lib))
    assert grants.delivery(BOX, str(lib)).status == PENDING
    _call(grants.revoke, ws, str(lib))
    assert grants.delivery(BOX, str(lib)) is None


def test_revoke_ends_a_server_that_wont_leave(tmp_path, home, world):
    """The lazy unmount takes the mount away at once, but the server keeps
    serving a file a process inside holds open. Ending the server is what
    cuts it: its own exit for a second, SIGTERM, two seconds, SIGKILL."""
    lib = home / "work" / "lib"
    ws = str(home / "work" / "repo")
    grants = world.grants()
    grants.register(_plan(tmp_path, home))
    _call(grants.allow, ws, str(lib))
    proc = world.procs[0]
    proc.stubborn = True
    world.exits_on_unmount = False
    before = world.now
    _call(grants.revoke, ws, str(lib))
    assert world.ran == [
        [world.fusermount, "-u", "-z", str(tmp_path / "boxes" / BOX / "grants" / _slot(lib))]
    ]
    assert proc.signals == ["TERM", "KILL"]
    assert proc.returncode == -9
    waited = world.now - before
    assert sandboxgrants.UNMOUNT_GRACE_S + sandboxgrants.TERM_GRACE_S <= waited < 3.5
    assert world.table.points() == []
    # One that answers SIGTERM is never killed.
    _call(grants.allow, ws, str(lib))
    polite = world.procs[1]
    _call(grants.revoke, ws, str(lib))
    assert polite.signals == ["TERM"]


def test_register_delivers_what_the_plan_lacks(tmp_path, home, world):
    lib = home / "work" / "lib"
    other = home / "work" / "other"
    ws = str(home / "work" / "repo")
    # A sibling on a plan derived earlier: launched with `other`, and `lib`
    # was allowed since.
    world.state.grants = {ws: [str(other), str(lib), "/opt/unplaceable"]}
    grants = world.grants()
    delivered = _call(grants.register, _plan(tmp_path, home, grants=(other,)))
    assert [(d.path, d.status) for d in delivered] == [
        (str(lib), LIVE), ("/opt/unplaceable", PENDING),
    ]
    assert grants.live_paths(BOX) == [str(lib)]
    # An ordinary launch has them all: nothing to deliver.
    world.state.grants = {ws: [str(other)]}
    assert _call(grants.register, _plan(tmp_path, home, BOX2, grants=(other,))) == []
    # Nothing to register.
    grants.register(None)
    grants.register({})


def test_registering_again_unmounts_first(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = str(home / "work" / "repo")
    world.state.grants = {ws: [str(lib)]}
    grants = world.grants()
    _call(grants.register, _plan(tmp_path, home))
    point = str(tmp_path / "boxes" / BOX / "grants" / _slot(lib))
    assert world.table.points() == [point]
    first = world.procs[0]
    # The restart: the same box, a plan that now binds it itself.
    assert _call(grants.register, _plan(tmp_path, home, grants=(lib,))) == []
    assert world.table.points() == []
    assert first.returncode is not None
    assert grants.status(BOX, str(lib)) == STATIC
    assert grants.live_paths(BOX) == []
    assert not os.path.lexists(tmp_path / "boxes" / BOX / "home" / "work" / "lib")
    # …or one that still lacks it: unmounted, then mounted afresh.
    world.state.grants = {ws: [str(lib), str(home / "work" / "other")]}
    _call(grants.register, _plan(tmp_path, home))
    _call(grants.register, _plan(tmp_path, home))
    assert len(world.table.points()) == 2
    assert sum(1 for proc in world.procs if proc.returncode is None) == 2


def test_unregister_waits_until_nothing_is_mounted(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = str(home / "work" / "repo")
    world.state.grants = {ws: [str(lib), "/mnt/data/x"]}
    grants = world.grants()
    _call(grants.register, _plan(tmp_path, home))
    assert len(world.table.points()) == 2
    grants.unregister(BOX, wait=True)
    # Back already: nothing mounted, no link, no box.
    assert world.table.points() == []
    assert not os.path.lexists(tmp_path / "boxes" / BOX / "home" / "work" / "lib")
    assert not grants.registered(BOX)
    assert grants.live_paths(BOX) == []
    assert all(proc.returncode is not None for proc in world.procs)
    assert grants.unregister(BOX, wait=True) is True  # twice is once
    assert grants.unregister("f" * 32) is True
    # Without waiting: `done` says when, on the worker — and at once, here,
    # for a box there is nothing to unmount in.
    _call(grants.register, _plan(tmp_path, home))
    assert len(world.table.points()) == 2
    assert _call(lambda done: grants.unregister(BOX, done=done)) == []
    assert world.table.points() == []
    seen = []
    grants.unregister(BOX, done=lambda gone: seen.append(threading.current_thread().name))
    assert seen == [threading.current_thread().name]
    # What is allowed afterwards has no box to reach.
    assert _call(grants.allow, ws, str(home / "work" / "other")) == []


def test_a_wait_that_runs_out_says_so(tmp_path, home, world, monkeypatch):
    """The caller that waits is told when the unmounts didn't finish in
    time, rather than going on as if they had."""
    grants = world.grants()
    _call(grants.register, _plan(tmp_path, home))
    monkeypatch.setattr(sandboxgrants, "WAIT_TIMEOUT_S", 0.05)
    release = threading.Event()
    grants._submit(lambda: release.wait(10) and [])  # the worker, busy
    assert grants.unregister(BOX, wait=True) is False
    release.set()
    _settle(grants)
    assert not grants.registered(BOX)


def test_shutdown_unmounts_everything_and_stops_the_thread(tmp_path, home, world):
    lib = home / "work" / "lib"
    ws = str(home / "work" / "repo")
    world.state.grants = {ws: [str(lib)]}
    grants = world.grants()
    _call(grants.register, _plan(tmp_path, home, BOX))
    _call(grants.register, _plan(tmp_path, home, BOX2))
    assert len(world.table.points()) == 2
    grants.shutdown()
    assert world.table.points() == []
    assert not grants._thread.is_alive()
    # After it, everything answers at once and mounts nothing.
    assert _calls_back(grants.allow, ws, str(lib)) == []
    grants.register(_plan(tmp_path, home, BOX3))
    grants.unregister(BOX3, wait=True)
    grants.shutdown()
    assert world.table.points() == []
    assert len(world.spawned) == 2


def _calls_back(method, *args):
    got = []
    method(*args, got.extend)
    return got


def test_every_spawn_happens_on_the_one_thread(tmp_path, home, world):
    ws = str(home / "work" / "repo")
    world.state.grants = {ws: [str(home / "work" / "lib")]}
    grants = world.grants()
    grants.register(_plan(tmp_path, home, BOX))

    def from_elsewhere(n):
        grants.allow(ws, str(home / "work" / f"dir{n}"))
        grants.register(_plan(tmp_path, home, BOX2 if n % 2 else BOX3))

    threads = [threading.Thread(target=from_elsewhere, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    _settle(grants)
    assert len(world.spawned) >= 7
    assert {name for _argv, name in world.spawned} == {"sandbox-grants"}
    assert grants._thread.name == "sandbox-grants" and grants._thread.daemon


def test_a_failing_job_doesnt_stop_the_worker(tmp_path, home, world):
    grants = world.grants()
    grants.register(_plan(tmp_path, home))

    def broken():
        raise RuntimeError("boom")

    assert _call(lambda done: grants._submit(broken, done)) == []
    (delivery,) = _call(grants.allow, str(home / "work" / "repo"), str(home / "work" / "lib"))
    assert delivery.status == LIVE


# -- what a dead instance left ---------------------------------------------------


def test_sweep_mounts_takes_only_a_dead_instances(tmp_path, home, world):
    root = tmp_path / "boxes"
    dead = str(root / BOX / "grants" / "lib-0a1b2c3d")
    alive = str(root / BOX2 / "grants" / "lib-0a1b2c3d")
    mine = str(root / BOX3 / "grants" / "lib-0a1b2c3d")
    foreign = str(root / BOX / "grants" / "somebody-elses")
    other_fs = str(root / BOX / "anchors" / "mnt" / "disk")
    outside = str(tmp_path / "elsewhere" / "lib")
    world.alive = {os.getpid(), 4242}
    world.table.add(dead, "collins:99999")
    world.table.add(alive, "collins:4242")
    world.table.add(mine, f"collins:{os.getpid()}")
    world.table.add(foreign, "/home/u/src")  # a bindfs the user made
    world.table.add(other_fs, "collins:99999", fstype="ext4")
    world.table.add(outside, "collins:99999")  # not under the sandbox root
    grants = world.grants()
    assert grants.sweep_mounts() == 1
    assert world.ran == [[world.fusermount, "-u", "-z", dead]]
    assert world.table.points() == sorted([alive, mine, foreign, other_fs, outside])
    assert grants.sweep_mounts() == 0


# -- the plan's side of it -------------------------------------------------------


def _real_host(monkeypatch, tmp_path, home, state):
    sandboxplan.reset_probe()
    fake = tmp_path / "bwrap"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("COLLINS_BWRAP", str(fake))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(sandboxplan, "RW_ABSOLUTE", ("/var/tmp",))
    monkeypatch.setattr(sandboxplan, "resolved_claude", lambda: None)
    monkeypatch.setattr(sandboxplan.shutil, "which", lambda name: None)
    monkeypatch.setattr(sandboxplan, "anchor_roots", lambda: ("/mnt",))
    return sandboxplan.SandboxHost("com.example.App", state)


def test_plan_stale_counts_what_is_live(monkeypatch, tmp_path, home):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    other = home / "work" / "other"
    state = _State({str(ws): [str(other)]})
    host = _real_host(monkeypatch, tmp_path, home, state)
    path = host.prepare_launch(str(ws), BOX)
    assert path is not None
    assert host.plan_stale(path, str(ws)) is False
    # A grant in the state that is live in the box is held: not stale.
    assert host.allow(str(ws), str(lib)) == ""
    assert host.plan_stale(path, str(ws)) is True  # pending: it waits for the restart
    assert host.plan_stale(path, str(ws), live=[str(lib)]) is False
    assert host.plan_stale(path, str(ws), live=(str(lib) + "/",)) is False
    # One that is pending is.
    assert host.plan_stale(path, str(ws), live=[]) is True
    # A static one taken back is leaving: stale, live or not.
    host.revoke(str(ws), str(other))
    assert host.plan_stale(path, str(ws), live=[str(lib)]) is True
    assert host.allow(str(ws), str(other)) == ""
    assert host.plan_stale(path, str(ws), live=[str(lib)]) is False  # the order is not the box
    # A share flipped is stale whatever the grants say.
    state.get_setting = lambda key: key == "sandbox_share_gh"
    assert host.plan_stale(path, str(ws), live=[str(lib)]) is True
    sandboxplan.reset_probe()


def test_a_sibling_cant_start_in_a_directory_its_parent_holds_live(monkeypatch, tmp_path, home):
    ws = home / "work" / "repo"
    lib = home / "work" / "lib"
    (lib / "sub").mkdir()
    host = _real_host(monkeypatch, tmp_path, home, _State())
    parent = host.prepare_launch(str(ws), BOX)
    boxes = set(os.listdir(tmp_path / "boxes"))
    for cwd in (lib, lib / "sub"):
        refused, box, reason = host.derive(parent, str(cwd), live=[str(lib)])
        assert refused is None and box == ""
        assert reason == (
            f"{cwd} was allowed while the parent session was running; "
            "restart the parent session to start a sibling there"
        )
    # Anything else outside the plan is refused as before.
    refused, _box, reason = host.derive(parent, str(home / "work" / "other"), live=[str(lib)])
    assert refused is None and "outside the sandbox" in reason
    refused, _box, reason = host.derive(parent, str(lib))
    assert refused is None and "outside the sandbox" in reason
    assert set(os.listdir(tmp_path / "boxes")) == boxes
    # Inside the workspace a sibling starts, live grants or none.
    made, box, reason = host.derive(parent, str(ws), live=[str(lib)])
    assert made is not None and reason == ""
    host.release(box)
    sandboxplan.reset_probe()


def test_the_module_stays_gtk_free():
    source = open(sandboxgrants.__file__, encoding="utf-8").read()
    assert "import gi" not in source and "from gi" not in source
    assert "gi.repository.Gtk" not in sys.modules


# -- the real thing --------------------------------------------------------------


_LOOP = """
cd "$1" || exit 9
touch ready
n=1
while [ ! -e stop ]; do
    if [ -e "cmd$n" ]; then
        sh "cmd$n" > "out$n.tmp" 2>&1
        mv "out$n.tmp" "out$n"
        n=$((n + 1))
    fi
    sleep 0.02
done
"""


class _RealBox:
    """A real bubblewrap box on a real plan, running a loop that takes
    commands through its workspace."""

    def __init__(self, root, home, name, box):
        self.box = box
        self.ws = home / "work" / name
        self.ws.mkdir(parents=True)
        for made in ("home", "grants", "anchors/mnt"):
            (root / box / made).mkdir(parents=True)
        self.plan = sandboxplan.build_plan(
            sandboxplan.Inputs(
                workspace=str(self.ws),
                home=str(home),
                sandbox_home=str(root / box / "home"),
                box=box,
                sandbox_root=str(root),
                carrier=str(root / box / "grants"),
                anchors=((str(root / box / "anchors" / "mnt"), "/mnt"),),
                grants_key=str(self.ws),
                protected=(),
            )
        )
        argv = ["/usr/bin/bwrap", *self.plan["bwrap_args"]]
        for var in self.plan["unsetenv"]:
            argv += ["--unsetenv", var]
        for key, value in self.plan["setenv"].items():
            argv += ["--setenv", key, value]
        self.proc = subprocess.Popen(
            [*argv, "--", "/bin/sh", "-c", _LOOP, "loop", str(self.ws)],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.n = 0
        deadline = time.monotonic() + 10
        while not (self.ws / "ready").exists():
            assert self.proc.poll() is None, self.proc.stderr.read().decode()
            assert time.monotonic() < deadline, "the box never came up"
            time.sleep(0.02)

    def run(self, script):
        self.n += 1
        (self.ws / "cmd.tmp").write_text(script)
        os.rename(self.ws / "cmd.tmp", self.ws / f"cmd{self.n}")
        out = self.ws / f"out{self.n}"
        deadline = time.monotonic() + 10
        while not out.exists():
            assert time.monotonic() < deadline, f"no answer to: {script}"
            time.sleep(0.02)
        return out.read_text().strip()

    def wait_for(self, script, want, seconds=5.0):
        """How long until *script* answers *want* inside, or None."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if self.run(script) == want:
                return time.monotonic() - start
            time.sleep(0.02)
        return None

    def stop(self):
        (self.ws / "stop").write_text("")
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)


def test_a_real_grant_reaches_a_real_box_and_only_that_box(tmp_path, monkeypatch, capsys):
    """Real bubblewrap, real bindfs, real fusermount3: a directory allowed
    while two boxes run is readable and writable at its real path inside
    the one it was allowed for, absent from the other, and gone again on
    revoke. Skips where the machine can't (no box, no FUSE)."""
    monkeypatch.delenv("COLLINS_BWRAP", raising=False)
    monkeypatch.delenv("COLLINS_BINDFS", raising=False)
    monkeypatch.delenv("COLLINS_FUSERMOUNT", raising=False)
    sandboxplan.reset_probe()
    if not os.path.exists("/usr/bin/bwrap") or sandboxplan.probe():
        sandboxplan.reset_probe()
        pytest.skip("no bubblewrap box on this machine")
    sandboxplan.reset_probe()
    root = tmp_path / "boxes"
    root.mkdir()
    monkeypatch.setenv("COLLINS_SANDBOX_ROOT", str(root))
    # pytest's tmp_path is under /tmp, which a real plan shares: with it
    # shared the fake home would be inside, as test_sandboxplan has it.
    monkeypatch.setattr(sandboxplan, "RW_ABSOLUTE", ("/var/tmp",))
    home = tmp_path / "home"
    granted = home / "dev" / "granted repo"
    granted.mkdir(parents=True)
    (granted / "hello.txt").write_text("from the host\n")
    state = _State()
    host = sandboxplan.SandboxHost("com.example.App", state)
    grants = GrantMounts(host)
    reason = grants.capable()
    if reason:
        grants.shutdown()
        pytest.skip(f"no live grants on this machine: {reason}")
    one = two = None
    report = []
    try:
        one = _RealBox(root, home, "alpha", BOX)
        two = _RealBox(root, home, "beta", BOX2)
        grants.register(one.plan)
        grants.register(two.plan)
        path = str(granted)
        read = f'cat "{path}/hello.txt" 2>/dev/null || echo MISSING'
        assert one.run(read) == "MISSING"
        state.grants = {str(one.ws): [path]}
        started = time.monotonic()
        delivered = _call(grants.allow, str(one.ws), path)
        mounted = time.monotonic() - started
        assert [(d.box, d.status, d.linked, d.reason) for d in delivered] == [
            (BOX, LIVE, True, "")
        ], delivered
        seen = one.wait_for(read, "from the host", seconds=2.0)
        assert seen is not None, "the grant never reached the running box"
        report.append(f"mounted in {mounted * 1000:.0f} ms, readable inside {seen * 1000:.0f} ms later")
        # At its real path, through the link; the mount itself in the carrier.
        where = one.run(f'cd "{path}" && pwd && pwd -P')
        assert where.splitlines() == [path, delivered[0].inside], where
        assert one.run(f'grep -c " {CARRIER}/" /proc/self/mountinfo') == "1"
        # Writable, and the write is the host's.
        assert one.run(f'echo inside > "{path}/from-box.txt" && echo wrote') == "wrote"
        assert (granted / "from-box.txt").read_text() == "inside\n"
        # The carrier itself is not the box's to write, mount or no mount.
        assert one.run(f"mkdir {CARRIER}/evil 2>/dev/null && echo made || echo refused") == "refused"
        # The other box: blind to it.
        assert two.run(read) == "MISSING"
        assert two.run(f"ls -A {CARRIER} | wc -l") == "0"
        assert two.run(f'test -e "{path}" && echo there || echo absent') == "absent"
        assert grants.status(BOX2, path) == PENDING and grants.live_paths(BOX2) == []
        # The host's own view: one bindfs mount, this process's, in the carrier.
        mine = [
            m for m in sandboxgrants.parse_mountinfo(sandboxgrants.read_mountinfo())
            if m.point.startswith(str(root) + "/")
        ]
        assert [(m.fstype, m.source) for m in mine] == [("fuse.bindfs", f"collins:{os.getpid()}")]
        assert mine[0].point == str(root / BOX / "grants" / _slot(granted))
        # A box with a mount under it is never removed.
        everything = sorted(str(p) for p in (root / BOX / "home").rglob("*"))
        assert sandboxplan.remove_box(BOX) is False
        assert sorted(str(p) for p in (root / BOX / "home").rglob("*")) == everything
        assert (granted / "hello.txt").read_text() == "from the host\n"
        # Revoked: gone inside, the server gone, nothing left mounted.
        state.grants = {}
        started = time.monotonic()
        _call(grants.revoke, str(one.ws), path)
        took = time.monotonic() - started
        gone = one.wait_for(read, "MISSING", seconds=2.0)
        assert gone is not None, "the grant never left the running box"
        report.append(f"revoked in {took * 1000:.0f} ms, gone inside {gone * 1000:.0f} ms later")
        assert one.run(f"ls -A {CARRIER} | wc -l") == "0"
        assert one.run(f'test -e "{path}" && echo there || echo absent') == "absent"
        assert (granted / "from-box.txt").exists()  # the directory itself is untouched
    finally:
        for box in (one, two):
            if box is not None:
                box.stop()
        grants.shutdown()
        left = [
            m.point for m in sandboxgrants.parse_mountinfo(sandboxgrants.read_mountinfo())
            if m.point.startswith(str(tmp_path) + "/")
        ]
        if left:  # never leave a mount for pytest's cleanup to delete through
            for point in left:
                subprocess.run(["fusermount3", "-u", "-z", point], check=False)
        sandboxplan.reset_probe()
    assert left == []
    with capsys.disabled():
        print(f"\n  real FUSE: {'; '.join(report)}", end="")
    assert json.dumps(report)
