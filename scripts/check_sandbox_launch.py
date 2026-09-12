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

Staged under ~/.cache rather than /tmp, like the policy check: /tmp is
shared read-write into every box, and Collins' own state in a scratch tree
there would trip the plan's protect-check.
"""

import os
import shutil
import subprocess
import sys
import tempfile

SKIP_EXIT = 77  # scripts/run_e2e.py reads this as "skipped", not "passed"

REAL_HOME = os.path.expanduser("~")
STAGE = os.path.join(REAL_HOME, ".cache", "collins-e2e")
os.makedirs(STAGE, exist_ok=True)
E2E = tempfile.mkdtemp(prefix="sandbox-launch-", dir=STAGE)
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
HOME = f"{E2E}/home"

os.environ["HOME"] = HOME
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"
os.environ["COLLINS_SANDBOX_HOME"] = f"{E2E}/sbx"
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
os.environ["XDG_CACHE_HOME"] = f"{E2E}/cache"
os.environ["XDG_DATA_HOME"] = f"{HOME}/.local/share"
os.environ.pop("TMPDIR", None)
os.environ.pop("COLLINS_BWRAP", None)  # the real one, or nothing

WORKSPACE = f"{E2E}/dev/alpha"
GRANTED = f"{E2E}/dev/lib"
OTHER = f"{E2E}/dev/secret-project"
SECRET = f"{HOME}/.ssh"
SETTINGS = f"{HOME}/.claude/settings.json"

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from collins import providers, sandboxplan  # noqa: E402
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
    shutil.rmtree(E2E, ignore_errors=True)
    sys.exit(SKIP_EXIT)


# The probe first: nothing below can run without a namespace to run it in.
reason = sandboxplan.probe()
if reason:
    skip(f"no box can be built here: {reason}")

for path in (WORKSPACE, GRANTED, OTHER, SECRET, f"{HOME}/.claude"):
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

state = AppState()
state.set_sandbox_grants(os.path.realpath(WORKSPACE), [GRANTED])
host = sandboxplan.SandboxHost(os.environ["COLLINS_APP_ID"], state)
plan_path = host.prepare_launch(WORKSPACE)
check("a launch in the workspace gets a plan", bool(plan_path), plan_path)
if not plan_path:
    print(f"\n{PASSED} passed, {FAILED} failed")
    shutil.rmtree(E2E, ignore_errors=True)
    sys.exit(1)

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
echo collins_config=$(ls {E2E}/config 2>/dev/null || echo MISSING)
echo collins_state=$(ls {E2E}/state 2>/dev/null || echo MISSING)
echo plan=$(test -e {plan_path} && echo PRESENT || echo MISSING)
echo dev_entries=$(ls {E2E}/dev 2>/dev/null | sort | xargs echo)
echo usr=$(test -d /usr/bin && echo yes || echo no)
echo usr_write=$(touch /usr/collins-e2e 2>/dev/null && echo yes || echo no)
echo pids=$(ls /proc | grep -c '^[0-9]*$')
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

# What the host sees of it afterwards: the writes landed for real, and the
# plan file is Collins' to release.
check("the write from inside reached the workspace", os.path.exists(f"{WORKSPACE}/written-inside"))
check("…and the granted directory", os.path.exists(f"{GRANTED}/written-inside"))
with open(SETTINGS, encoding="utf-8") as fh:
    check("settings.json is unchanged on the host", fh.read().strip() == '{"model": "opus"}')
sandboxplan.release_plan(plan_path)
check("the plan file is released", not os.path.exists(plan_path))

shutil.rmtree(E2E, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED or not PASSED else 0)
