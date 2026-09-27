#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""End-to-end check that a plan Collins writes runs under the *real*
bubblewrap, and that the box it builds holds what the plan says.

The other sandbox check (check_sandbox_policy.py) drives the widgets
through a fake `COLLINS_BWRAP`, which proves the seams but never asks
bubblewrap whether the arguments make sense. This one has no GTK in it at
all: it stages a home and a workspace, has `sandboxplan.prepare_launch`
write a plan the way a launch does, and runs it — `sandboxrun.py <plan> --
/bin/sh -c …` — reporting from inside the box.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_sandbox_launch.py

**Skips (exit 77) where no box can be built**: bubblewrap missing, or user
namespaces restricted — the shape of a CI container. The unit suite carries
the plan generator's own coverage (it is pure path arithmetic), so a skip
here costs nothing but this one proof.

**The live section** then allows a directory while two boxes *run*
(collins.sandboxgrants, a real `bindfs` through the real `fusermount3`):
readable at its real path inside the box it was allowed for within two
seconds, absent from the other, gone again on revoke. It is printed as
skipped — and the check still passes — where the machine can't deliver a
grant live: `GrantMounts.capable()` says why, or the first mount is
refused (a container with the tools and no FUSE to mount with).

Staged under ~/.cache rather than /tmp, like the policy check: /tmp is
shared read-write into every box, and Collins' own state in a scratch tree
there would trip the plan's protect-check.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

SKIP_EXIT = 77  # scripts/run_e2e.py reads this as "skipped", not "passed"

REAL_HOME = os.path.expanduser("~")
STAGE = os.path.join(REAL_HOME, ".cache", "collins-e2e")
os.makedirs(STAGE, exist_ok=True)
E2E = tempfile.mkdtemp(prefix="sandbox-launch-", dir=STAGE)


def mounts_under(tree: str) -> list[str]:
    """Every mount point at or under *tree*, deepest first."""
    found = []
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            fields = line.split(" ")
            if len(fields) > 4:
                point = fields[4].replace("\\040", " ")
                if point == tree or point.startswith(tree + "/"):
                    found.append(point)
    return sorted(found, key=len, reverse=True)


def clear_tree() -> None:
    """Remove the scratch tree — never through a mount: a bindfs mount in
    it *is* the granted directory. Whatever is still mounted is unmounted
    first, and a tree that still holds a mount is left where it is."""
    for point in mounts_under(E2E):
        subprocess.run(["fusermount3", "-u", "-z", point], check=False, capture_output=True)
    left = mounts_under(E2E)
    if left:
        print(f"not removing {E2E}: still mounted: {left}", file=sys.stderr)
        return
    shutil.rmtree(E2E, True)


# ~/.cache outlives the run, so the tree goes however the check ends: a
# skip, a failed check, bwrap outliving the timeout, any exception on the way.
atexit.register(clear_tree)
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
HOME = f"{E2E}/home"

os.environ["HOME"] = HOME
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_SANDBOX_ROOT"] = f"{E2E}/sbx"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["XDG_DATA_HOME"] = f"{HOME}/.local/share"
os.environ.pop("TMPDIR", None)
os.environ.pop("COLLINS_BWRAP", None)  # the real one, or nothing

WORKSPACE = f"{E2E}/dev/alpha"
SECOND = f"{E2E}/dev/beta"  # a second session's workspace, in a box of its own
GRANTED = f"{E2E}/dev/lib"
OTHER = f"{E2E}/dev/secret-project"
SECRET = f"{HOME}/.ssh"
SETTINGS = f"{HOME}/.claude/settings.json"
# What the host runs later, pinned read-only inside (sandboxplan's
# PROTECTED_* tables and HOST_BIN_HOME).
HOOK = f"{WORKSPACE}/.git/hooks/pre-commit"
GIT_CONFIG = f"{WORKSPACE}/.git/config"
SKILLS = f"{HOME}/.claude/skills"
INSTRUCTIONS = f"{HOME}/.claude/CLAUDE.md"
HOST_BIN = f"{HOME}/.local/bin"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from collins import providers, sandboxgrants, sandboxplan  # noqa: E402
from collins.state import AppState  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}", flush=True)
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}", flush=True)


def skip(reason: str) -> None:
    print(f"SKIP  {reason}", flush=True)
    sys.exit(SKIP_EXIT)


# The probe first: nothing below can run without a namespace to run it in.
reason = sandboxplan.probe()
if reason:
    skip(f"no box can be built here: {reason}")

for path in (WORKSPACE, SECOND, GRANTED, OTHER, SECRET, f"{HOME}/.claude"):
    os.makedirs(path, exist_ok=True)
with open(f"{WORKSPACE}/file.txt", "w", encoding="utf-8") as fh:
    fh.write("workspace\n")
with open(f"{GRANTED}/lib.txt", "w", encoding="utf-8") as fh:
    fh.write("granted\n")
with open(f"{OTHER}/secret.txt", "w", encoding="utf-8") as fh:
    fh.write("not for the box\n")
with open(f"{SECRET}/id_ed25519", "w", encoding="utf-8") as fh:
    fh.write("PRIVATE KEY\n")
with open(SETTINGS, "w", encoding="utf-8") as fh:
    fh.write('{"model": "opus"}\n')
for path in (os.path.dirname(HOOK), SKILLS, HOST_BIN):
    os.makedirs(path, exist_ok=True)
with open(HOOK, "w", encoding="utf-8") as fh:
    fh.write("#!/bin/sh\necho hook-ran\n")
os.chmod(HOOK, 0o755)
with open(GIT_CONFIG, "w", encoding="utf-8") as fh:
    fh.write("[core]\n")
with open(INSTRUCTIONS, "w", encoding="utf-8") as fh:
    fh.write("the user's own\n")
os.symlink("/bin/true", f"{HOST_BIN}/tool")

state = AppState()
host = sandboxplan.SandboxHost(os.environ["COLLINS_APP_ID"], state, state.state_file())
# Three sessions: two of one workspace, side by side, and one of another.
# A directory is allowed to the first — a session's grants are its box's.
BOX = host.mint_box(WORKSPACE)
BOX2 = host.mint_box(SECOND)
BOX3 = host.mint_box(WORKSPACE)
check("the guard allows the granted directory", host.allow(BOX, WORKSPACE, GRANTED) == "")
plan_path = host.prepare_launch(WORKSPACE, BOX)
check("a launch in the workspace gets a plan", bool(plan_path), plan_path)
if not plan_path:
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1)
second_path = host.prepare_launch(SECOND, BOX2)
check("a second session gets a plan of its own", bool(second_path), second_path)
beside_path = host.prepare_launch(WORKSPACE, BOX3)
check("…and so does another session of the first's workspace", bool(beside_path), beside_path)
beside = sandboxplan.load_plan(beside_path)
check(
    "whose plan binds nothing the first was allowed",
    bool(beside) and beside["inputs"]["grants"] == [] and not any(GRANTED in a for a in beside["bwrap_args"]),
    beside and beside["inputs"]["grants"],
)
BOX_DIR = sandboxplan.box_dir(BOX)
BOX2_DIR = sandboxplan.box_dir(BOX2)
CARRIER = sandboxplan.CARRIER_DEST

# One shell inside the box answers everything, as `key=value` lines: the
# box is built once, and a failed answer names itself.
PROBE = f"""
echo pwd=$(pwd)
echo home=$HOME
echo workspace_file=$(cat {WORKSPACE}/file.txt 2>/dev/null || echo MISSING)
echo workspace_write=$(touch {WORKSPACE}/written-inside 2>/dev/null && echo yes || echo no)
echo granted=$(cat {GRANTED}/lib.txt 2>/dev/null || echo MISSING)
echo granted_write=$(touch {GRANTED}/written-inside 2>/dev/null && echo yes || echo no)
echo other=$(cat {OTHER}/secret.txt 2>/dev/null || echo MISSING)
echo ssh=$(ls {SECRET} 2>/dev/null || echo MISSING)
echo settings=$(cat {SETTINGS} 2>/dev/null || echo MISSING)
echo settings_write=$(echo x >> {SETTINGS} 2>/dev/null && echo yes || echo no)
echo hook_runs=$({HOOK} 2>/dev/null || echo no)
echo hook_write=$(echo x >> {HOOK} 2>/dev/null && echo yes || echo no)
echo hook_add=$(touch {os.path.dirname(HOOK)}/post-checkout 2>/dev/null && echo yes || echo no)
echo hooks_move=$(mv {os.path.dirname(HOOK)} {os.path.dirname(HOOK)}-aside 2>/dev/null && echo yes || echo no)
echo git_config_write=$(echo x >> {GIT_CONFIG} 2>/dev/null && echo yes || echo no)
echo skills_write=$(mkdir {SKILLS}/planted 2>/dev/null && echo yes || echo no)
echo instructions=$(cat {INSTRUCTIONS} 2>/dev/null || echo MISSING)
echo instructions_write=$(echo x >> {INSTRUCTIONS} 2>/dev/null && echo yes || echo no)
echo host_bin_runs=$({HOST_BIN}/tool 2>/dev/null && echo yes || echo no)
echo host_bin_repoint=$(ln -sfn /bin/false {HOST_BIN}/tool 2>/dev/null && echo yes || echo no)
echo host_bin_add=$(touch {HOST_BIN}/planted 2>/dev/null && echo yes || echo no)
echo collins_config=$(ls {E2E}/config 2>/dev/null || echo MISSING)
echo collins_state=$(ls {E2E}/state 2>/dev/null || echo MISSING)
echo plan=$(test -e {plan_path} && echo PRESENT || echo MISSING)
echo dev_entries=$(ls {E2E}/dev 2>/dev/null | sort | xargs echo)
echo usr=$(test -d /usr/bin && echo yes || echo no)
echo usr_write=$(touch /usr/collins-e2e 2>/dev/null && echo yes || echo no)
echo pids=$(ls /proc | grep -c '^[0-9]*$')
echo home_write=$(echo from-the-first > $HOME/written-in-the-first 2>/dev/null && echo yes || echo no)
echo carrier=$(test -d {CARRIER} && echo yes || echo no)
echo carrier_entries=$(ls -A {CARRIER} 2>/dev/null | wc -l)
echo carrier_write=$(mkdir {CARRIER}/mine 2>/dev/null && echo yes || echo no)
echo anchor=$(test -d /mnt && echo yes || echo no)
echo anchor_write=$(mkdir /mnt/collins-e2e-mine 2>/dev/null && echo yes || echo no)
echo own_box=$(ls {BOX_DIR} 2>/dev/null || echo MISSING)
echo other_box=$(ls {BOX2_DIR} 2>/dev/null || echo MISSING)
echo boxes=$(ls {E2E}/sbx 2>/dev/null || echo MISSING)
"""

# The second box, for the second session: what it sees of the first.
PROBE2 = f"""
echo pwd=$(pwd)
echo home=$HOME
echo first_file=$(cat $HOME/written-in-the-first 2>/dev/null || echo MISSING)
echo home_write=$(echo from-the-second > $HOME/written-in-the-second 2>/dev/null && echo yes || echo no)
echo first_workspace=$(ls {WORKSPACE} 2>/dev/null || echo MISSING)
echo granted=$(cat {GRANTED}/lib.txt 2>/dev/null || echo MISSING)
echo carrier_write=$(mkdir {CARRIER}/mine 2>/dev/null && echo yes || echo no)
echo own_box=$(ls {BOX2_DIR} 2>/dev/null || echo MISSING)
echo other_box=$(ls {BOX_DIR} 2>/dev/null || echo MISSING)
"""

argv = [sys.executable, providers.sandboxrun_path(), plan_path, "--", "/bin/sh", "-c", PROBE]
proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
check("the real bwrap accepted the plan", proc.returncode == 0, (proc.returncode, proc.stderr[-400:]))
inside = dict(
    line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line
)
if not inside:
    print(proc.stdout, proc.stderr, file=sys.stderr)
    check("the box reported from inside", False, proc.stderr[-400:])
else:
    check("it starts in the workspace", inside.get("pwd") == WORKSPACE, inside.get("pwd"))
    check("$HOME is the home the plan names", inside.get("home") == HOME, inside.get("home"))
    check("the workspace is there", inside.get("workspace_file") == "workspace", inside.get("workspace_file"))
    check("…and writable", inside.get("workspace_write") == "yes", inside.get("workspace_write"))
    check("the granted directory is there", inside.get("granted") == "granted", inside.get("granted"))
    check("…and writable", inside.get("granted_write") == "yes", inside.get("granted_write"))
    check("a directory beside it that wasn't granted is absent", inside.get("other") == "MISSING", inside.get("other"))
    check("~/.ssh is absent", inside.get("ssh") == "MISSING", inside.get("ssh"))
    check("~/.claude/settings.json is readable", '"model"' in inside.get("settings", ""), inside.get("settings"))
    check("…and not writable", inside.get("settings_write") == "no", inside.get("settings_write"))
    # What the host runs later: there, working, and not the box's to change.
    check("the repository's hook runs inside", inside.get("hook_runs") == "hook-ran", inside.get("hook_runs"))
    check("…and can't be rewritten", inside.get("hook_write") == "no", inside.get("hook_write"))
    check("…nor one added beside it", inside.get("hook_add") == "no", inside.get("hook_add"))
    check("…nor the hooks directory moved aside", inside.get("hooks_move") == "no", inside.get("hooks_move"))
    check(
        "the git config beside them stays writable",
        inside.get("git_config_write") == "yes",
        inside.get("git_config_write"),
    )
    check("~/.claude/skills can't be written", inside.get("skills_write") == "no", inside.get("skills_write"))
    check(
        "~/.claude/CLAUDE.md is readable",
        inside.get("instructions") == "the user's own",
        inside.get("instructions"),
    )
    check("…and not writable", inside.get("instructions_write") == "no", inside.get("instructions_write"))
    check("a tool in ~/.local/bin runs inside", inside.get("host_bin_runs") == "yes", inside.get("host_bin_runs"))
    check("…and can't be repointed", inside.get("host_bin_repoint") == "no", inside.get("host_bin_repoint"))
    check("…nor one added beside it", inside.get("host_bin_add") == "no", inside.get("host_bin_add"))
    check("Collins' config is absent", inside.get("collins_config") == "MISSING", inside.get("collins_config"))
    check("Collins' state is absent", inside.get("collins_state") == "MISSING", inside.get("collins_state"))
    check("the plan file itself is absent", inside.get("plan") == "MISSING", inside.get("plan"))
    # bwrap creates the directories it mounts into, so the tree above the
    # workspace exists inside as a skeleton — holding the two mounts and
    # nothing else, which is the claim worth making (the un-granted
    # directory beside them is one of the entries that must not be there).
    check(
        "the tree around the workspace holds only what was bound",
        inside.get("dev_entries") == "alpha lib",
        inside.get("dev_entries"),
    )
    check("the system is there", inside.get("usr") == "yes", inside.get("usr"))
    check("…read-only", inside.get("usr_write") == "no", inside.get("usr_write"))
    # --unshare-pid: the box sees its own handful of processes, not the
    # machine's. The host has hundreds; the box has the shell and its own.
    pids = int(inside.get("pids", "0") or 0)
    check("the pid namespace is its own", 0 < pids < 20, pids)
    # The box's own places: a home it writes, a carrier and an anchor it
    # can't, and nothing of the sandbox root — its own box included.
    check("$HOME is writable", inside.get("home_write") == "yes", inside.get("home_write"))
    check("the grant carrier is there", inside.get("carrier") == "yes", inside.get("carrier"))
    check("…empty", inside.get("carrier_entries") == "0", inside.get("carrier_entries"))
    check("…and mkdir in it fails", inside.get("carrier_write") == "no", inside.get("carrier_write"))
    check("/mnt is an anchor", inside.get("anchor") == "yes", inside.get("anchor"))
    check("…and mkdir in it fails", inside.get("anchor_write") == "no", inside.get("anchor_write"))
    check("the box's own directory is absent inside", inside.get("own_box") == "MISSING", inside.get("own_box"))
    check("the other box's directory is absent", inside.get("other_box") == "MISSING", inside.get("other_box"))
    check("the sandbox root is absent", inside.get("boxes") == "MISSING", inside.get("boxes"))

# The second session's box: the same machine, another home.
if second_path:
    argv = [sys.executable, providers.sandboxrun_path(), second_path, "--", "/bin/sh", "-c", PROBE2]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    check("the real bwrap accepted the second plan", proc.returncode == 0, (proc.returncode, proc.stderr[-400:]))
    second = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    check("the second box starts in its own workspace", second.get("pwd") == SECOND, second.get("pwd"))
    check("$HOME inside has the same name", second.get("home") == HOME, second.get("home"))
    check(
        "a file written to $HOME in the first box is absent",
        second.get("first_file") == "MISSING",
        second.get("first_file"),
    )
    check("its own $HOME is writable", second.get("home_write") == "yes", second.get("home_write"))
    check(
        "the first session's workspace is absent",
        second.get("first_workspace") in ("MISSING", ""),
        second.get("first_workspace"),
    )
    check(
        "the first project's grant is absent",
        second.get("granted") == "MISSING",
        second.get("granted"),
    )
    check("mkdir in its carrier fails", second.get("carrier_write") == "no", second.get("carrier_write"))
    check("its own box directory is absent inside", second.get("own_box") == "MISSING", second.get("own_box"))
    check("the first box's directory is absent", second.get("other_box") == "MISSING", second.get("other_box"))
    one, two = sandboxplan.load_plan(plan_path), sandboxplan.load_plan(second_path)
    check(
        "the two homes differ on the host",
        bool(one and two)
        and one["inputs"]["sandbox_home"] == sandboxplan.box_home(BOX)
        and two["inputs"]["sandbox_home"] == sandboxplan.box_home(BOX2),
        (one and one["inputs"]["sandbox_home"], two and two["inputs"]["sandbox_home"]),
    )

# What the host sees of it afterwards: the writes landed for real, and the
# plan file is Collins' to release.
check("the write from inside reached the workspace", os.path.exists(f"{WORKSPACE}/written-inside"))
check("…and the granted directory", os.path.exists(f"{GRANTED}/written-inside"))
with open(SETTINGS, encoding="utf-8") as fh:
    check("settings.json is unchanged on the host", fh.read().strip() == '{"model": "opus"}')
with open(HOOK, encoding="utf-8") as fh:
    check("the hook is unchanged on the host", fh.read() == "#!/bin/sh\necho hook-ran\n")
check("…alone in its directory", os.listdir(os.path.dirname(HOOK)) == ["pre-commit"])
check(
    "~/.local/bin holds what it held",
    os.listdir(HOST_BIN) == ["tool"] and os.readlink(f"{HOST_BIN}/tool") == "/bin/true",
    os.listdir(HOST_BIN),
)
check("nothing was planted in ~/.claude/skills", os.listdir(SKILLS) == [])
check(
    "each box's home holds its own file",
    os.path.exists(f"{sandboxplan.box_home(BOX)}/written-in-the-first")
    and not os.path.exists(f"{sandboxplan.box_home(BOX)}/written-in-the-second")
    and os.path.exists(f"{sandboxplan.box_home(BOX2)}/written-in-the-second") == bool(second_path),
)
check("the real home has neither", not os.path.exists(f"{HOME}/written-in-the-first"))
check("nothing was written into the carrier", os.listdir(sandboxplan.box_carrier(BOX)) == [])

# ---- the live section: a directory allowed while the boxes run ------------------

LIVE = f"{HOME}/dev/live lib"  # under $HOME, and a space in its name
LOOP = """
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


class RunningBox:
    """A box that stays up — the session — taking commands through a
    directory of its own in its workspace, which it may share with
    another box."""

    def __init__(self, plan: str, workspace: str, name: str) -> None:
        self.dir = f"{workspace}/live-check-{name}"
        os.makedirs(self.dir, exist_ok=True)
        argv = [sys.executable, providers.sandboxrun_path(), plan, "--"]
        self.proc = subprocess.Popen(
            [*argv, "/bin/sh", "-c", LOOP, "loop", self.dir],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.n = 0
        deadline = time.monotonic() + 15
        while not os.path.exists(f"{self.dir}/ready"):
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(self.proc.stderr.read().decode(errors="replace")[-400:])
            time.sleep(0.02)

    def run(self, script: str) -> str:
        self.n += 1
        with open(f"{self.dir}/cmd.tmp", "w", encoding="utf-8") as fh:
            fh.write(script)
        os.rename(f"{self.dir}/cmd.tmp", f"{self.dir}/cmd{self.n}")
        deadline = time.monotonic() + 10
        while not os.path.exists(f"{self.dir}/out{self.n}"):
            if time.monotonic() > deadline:
                return "TIMEOUT"
            time.sleep(0.02)
        with open(f"{self.dir}/out{self.n}", encoding="utf-8") as fh:
            return fh.read().strip()

    def until(self, script: str, want: str, seconds: float = 2.0) -> float | None:
        """How long until *script* answers *want* inside; None if never."""
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if self.run(script) == want:
                return time.monotonic() - start
            time.sleep(0.02)
        return None

    def stop(self) -> None:
        with open(f"{self.dir}/stop", "w", encoding="utf-8"):
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)


def ask(method, *args) -> list:
    """One of the live grants' queued operations, waited for."""
    got: list = []
    landed = threading.Event()
    method(*args, lambda delivered: (got.extend(delivered), landed.set()))
    landed.wait(20)
    return got


def live_section() -> None:
    grants = sandboxgrants.GrantMounts(host)
    boxes: list[RunningBox] = []
    try:
        reason = grants.capable()
        if reason:
            print(f"SKIP  the live section: {reason}", flush=True)
            return
        os.makedirs(LIVE, exist_ok=True)
        with open(f"{LIVE}/hello.txt", "w", encoding="utf-8") as fh:
            fh.write("from the host\n")
        # Two sessions of the **same workspace**, each in its own box.
        first = RunningBox(plan_path, WORKSPACE, "first")
        boxes.append(first)
        second = RunningBox(beside_path, WORKSPACE, "second")
        boxes.append(second)
        grants.register(sandboxplan.load_plan(plan_path))
        grants.register(beside)
        ask(grants.sync, BOX)  # nothing owed; and the registrations are done
        read = f'cat "{LIVE}/hello.txt" 2>/dev/null || echo MISSING'
        absent = first.run(read) == "MISSING" and second.run(read) == "MISSING"
        static = f'cat "{GRANTED}/lib.txt" 2>/dev/null || echo MISSING'
        shared = second.run(f'test -d "{first.dir}" && echo yes || echo no')
        refusal = host.allow(BOX, WORKSPACE, LIVE)
        started = time.monotonic()
        delivered = ask(grants.allow, BOX, LIVE)
        took = time.monotonic() - started
        own = next((d for d in delivered if d.box == BOX), None)
        if own is None or own.status != sandboxgrants.LIVE:
            why = own.reason if own is not None else "nothing was delivered"
            print(f"SKIP  the live section: the grant couldn't be mounted here: {why}", flush=True)
            return
        print(
            f"  --  live: two sessions of {WORKSPACE} running, {LIVE} allowed to the first",
            flush=True,
        )
        check("the two boxes share their workspace", shared == "yes", shared)
        check("the first holds its launch-time grant", first.run(static) == "granted")
        check("…which the second, beside it, was never allowed", second.run(static) == "MISSING")
        check("the directory is in neither box before it is allowed", absent)
        check("the guard allows it", refusal == "", refusal)
        check("it is delivered to the first box, and only to it", [d.box for d in delivered] == [BOX])
        check("it is the first's alone in the state", host.grants(BOX3) == [] and LIVE in host.grants(BOX))
        check("…live, at its real path", own.linked and own.inside.startswith(CARRIER + "/"), own)
        seen = first.until(read, "from the host")
        check("the file is readable at its real path inside within 2 s", seen is not None, seen)
        if seen is not None:
            print(
                f"  --  live: mounted in {took * 1000:.0f} ms, "
                f"readable inside {seen * 1000:.0f} ms after that",
                flush=True,
            )
        where = first.run(f'cd "{LIVE}" && pwd && pwd -P').splitlines()
        check("its physical path is in the carrier", where == [LIVE, own.inside], where)
        wrote = first.run(f'echo inside > "{LIVE}/from-box.txt" && echo wrote')
        check("it is writable inside", wrote == "wrote", wrote)
        check("…and the write is the host's", os.path.exists(f"{LIVE}/from-box.txt"))
        made = first.run(f"mkdir {CARRIER}/mine 2>/dev/null && echo made || echo refused")
        check("the carrier itself still can't be written", made == "refused", made)
        check("the second box is blind to it", second.run(read) == "MISSING")
        entries = second.run(f"ls -A {CARRIER} | wc -l")
        check("…its carrier empty", entries == "0", entries)
        check("…and nothing of it in the live grants", grants.delivery(BOX3, LIVE) is None)
        check(
            "the second has nothing to restart for",
            not host.plan_stale(beside_path, WORKSPACE, live=grants.live_paths(BOX3)),
        )
        check("the grant is live in the first box", grants.status(BOX, LIVE) == sandboxgrants.LIVE)
        check("…so the box is not stale", not host.plan_stale(plan_path, WORKSPACE, live=grants.live_paths(BOX)))
        check("…though its plan alone would be", host.plan_stale(plan_path, WORKSPACE))
        points = [p for p in mounts_under(E2E)]
        expected = f"{sandboxplan.box_carrier(BOX)}/{sandboxgrants.slot_name(LIVE)}"
        check("one mount on the host, in the first box's carrier", points == [expected], points)
        check("a box with a mount under it is never removed", sandboxplan.remove_box(BOX) is False)
        check("…nor anything in the granted directory", os.path.exists(f"{LIVE}/hello.txt"))
        host.revoke(BOX, LIVE)
        started = time.monotonic()
        ask(grants.revoke, BOX, LIVE)
        took = time.monotonic() - started
        gone = first.until(read, "MISSING")
        check("revoked, it is gone inside within 2 s", gone is not None, gone)
        if gone is not None:
            print(
                f"  --  live: revoked in {took * 1000:.0f} ms, gone inside {gone * 1000:.0f} ms after that",
                flush=True,
            )
        there = first.run(f'test -e "{LIVE}" && echo there || echo absent')
        check("…the real path with it", there == "absent", there)
        check("nothing is left mounted", mounts_under(E2E) == [], mounts_under(E2E))
        check("the granted directory itself is untouched", os.path.exists(f"{LIVE}/from-box.txt"))
    finally:
        for box in boxes:
            box.stop()
        grants.shutdown()


if beside_path:
    live_section()
check("nothing is mounted under the scratch tree", mounts_under(E2E) == [], mounts_under(E2E))

sandboxplan.release_plan(plan_path)
sandboxplan.release_plan(second_path)
sandboxplan.release_plan(beside_path)
check("the plan file is released", not os.path.exists(plan_path))
# Boxes no session names, let go: their grants leave the state with them,
# and they are removed, by file descriptor, tree and all.
check("the first box's grant is still recorded", host.grants(BOX) == [GRANTED], host.grants(BOX))
for box in (BOX, BOX2, BOX3):
    host.release(box)
    host.forget_box(box)
check("forgotten, a box's grants are gone", AppState().sandbox_grants == {}, AppState().sandbox_grants)
for _tick in range(200):
    if not any(os.path.exists(sandboxplan.box_dir(box)) for box in (BOX, BOX2, BOX3)):
        break
    time.sleep(0.05)
check("the first box goes once nothing needs it", not os.path.exists(BOX_DIR))
check("…and the others", not os.path.exists(BOX2_DIR) and not os.path.exists(sandboxplan.box_dir(BOX3)))

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
