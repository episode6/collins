"""Launch Collins from the checkout on macOS for ~20s and record what happens.

Spec hardware-only item 5. Mints a fresh COLLINS_APP_ID and a scratch XDG
tree (the capture-screenshots recipe: COLLINS_CHATS_DIR is not optional),
runs `python3 -m collins`, waits, takes a `screencapture`, then terminates.

    python3 launch_collins.py <out-dir> <label>

Set SPIKE_STUB_PROCTREE=1 (with scripts/spike/stub on PYTHONPATH) to run
with proctree stubbed. PASS means the process was still alive at the
deadline and stderr carries no Python traceback; stderr is saved beside
the PNG either way.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

WAIT_S = 20


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    label = sys.argv[2] if len(sys.argv) > 2 else "plain"
    os.makedirs(out_dir, exist_ok=True)
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    scratch = tempfile.mkdtemp(prefix="collins-spike-")
    run_id = uuid.uuid4().hex[:8]
    env = dict(os.environ)
    env.update(
        {
            # A D-Bus id element may not start with a digit, hence the "R".
            "COLLINS_APP_ID": f"com.episode6.Collins.Spike.R{run_id}",
            "COLLINS_PROJECTS_DIR": os.path.join(scratch, "projects"),
            "COLLINS_CLAUDE_CONFIG": os.path.join(scratch, "claude.json"),
            "COLLINS_CHATS_DIR": os.path.join(scratch, "chats"),
            "XDG_CONFIG_HOME": os.path.join(scratch, "config"),
            "XDG_STATE_HOME": os.path.join(scratch, "state"),
            "COLLINS_LOG": "INFO",
            "PYTHONUNBUFFERED": "1",
        }
    )
    os.makedirs(env["COLLINS_PROJECTS_DIR"])
    with open(env["COLLINS_CLAUDE_CONFIG"], "w") as fh:
        json.dump({}, fh)
    # A fake transcript so the sidebar has a row to draw.
    proj = os.path.join(env["COLLINS_PROJECTS_DIR"], "-Users-runner-spike")
    os.makedirs(proj)
    with open(os.path.join(proj, f"{uuid.uuid4()}.jsonl"), "w") as fh:
        fh.write(
            json.dumps(
                {
                    "type": "user",
                    "cwd": "/Users/runner/spike",
                    "sessionId": "spike",
                    "timestamp": "2026-09-05T12:00:00.000Z",
                    "message": {"role": "user", "content": "hello from the spike"},
                }
            )
            + "\n"
        )

    stderr_path = os.path.join(out_dir, f"collins-{label}.stderr.txt")
    stderr_fh = open(stderr_path, "w")
    print(f"launching python3 -m collins as {env['COLLINS_APP_ID']} (stub={env.get('SPIKE_STUB_PROCTREE')})")
    proc = subprocess.Popen(
        [sys.executable, "-m", "collins"],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stderr_fh,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.5)
    alive = proc.poll() is None

    png = os.path.join(out_dir, f"collins-{label}.png")
    cap = "screencapture not on PATH"
    if shutil.which("screencapture"):
        res = subprocess.run(["screencapture", "-x", "-t", "png", png], capture_output=True, text=True)
        cap = f"rc={res.returncode} bytes={os.path.getsize(png) if os.path.exists(png) else 0}"

    if alive:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    stderr_fh.close()
    with open(stderr_path) as fh:
        err = fh.read()
    print("---- collins stderr ----")
    print(err.rstrip()[-8000:])
    print("---- end stderr ----")
    traceback = "Traceback (most recent call last)" in err
    ok = alive and not traceback
    print(
        f"RESULT launch label={label} alive_at_{WAIT_S}s={alive} exit={proc.returncode} "
        f"traceback={traceback} screencapture={cap!r} stderr_lines={len(err.splitlines())}"
    )
    print(f"{'PASS' if ok else 'FAIL'} launch {label}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
