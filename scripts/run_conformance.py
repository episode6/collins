#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Run the conformance kit (conformance/) against this checkout's service.

The kit is a client of the published API and knows nothing of Collins; this
script is the Collins side of running it: a scratch tree, a service of this
checkout on it with no keeper (`COLLINS_KEEPER=none`, so the run's sessions
end with the service), the kit's fake claude installed as the `claude` the
service finds (`claude_cli_path` in the scratch `state.json`, and first on
the service's `PATH`), a scratch `HOME` so the fake's transcripts land in a
`~/.claude/projects` that is the service's, and then the kit itself as a
subprocess, with its exit status as this script's. It is what the
`conformance` CI job runs; on a dev machine:

    python3 scripts/run_conformance.py                     # every level and family the service lists
    python3 scripts/run_conformance.py --skip-family prs   # the CI job's shape, no gh and no bubblewrap
    python3 scripts/run_conformance.py --service-only      # start a service, print its socket, wait

Everything after the script's own options goes to the kit (`python3 -m
conformance --help`). The real `claude` is never started: the fake is the
only `claude` on the service's PATH, and the scratch state disables the
title and icon runs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE = os.path.join(REPO, "conformance", "fake_claude.py")


def stage(root: str, app_id: str) -> dict:
    """The scratch tree and the service's environment."""
    home = os.path.join(root, "home")
    bin_dir = os.path.join(root, "bin")
    for path in (
        home,
        bin_dir,
        os.path.join(home, ".claude", "projects"),
        os.path.join(root, "config", "collins"),
    ):
        os.makedirs(path, exist_ok=True)
    claude = os.path.join(bin_dir, "claude")
    shutil.copy(FAKE, claude)
    os.chmod(claude, 0o755)
    with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as handle:
        handle.write("{}\n")
    state = {
        "settings": {
            "welcome_seen": True,
            "gh_welcome_dismissed": True,
            "title_model": "none",
            "icon_model": "none",
            "auto_renew_login": False,
            "refresh_prs_on_launch": False,
            "claude_cli_path": bin_dir,
        }
    }
    with open(os.path.join(root, "config", "collins", "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("COLLINS_"):
            del env[name]
    # What the spawn environment table says the server sets: taken out, so the
    # kit sees the server's own setting and not this shell's (a run from inside
    # a Collins session inherits ConEmuANSI=ON and TERM_PROGRAM=kitty).
    for name in ("TERM", "COLORTERM", "VTE_VERSION", "ConEmuANSI", "TERM_PROGRAM"):
        env.pop(name, None)
    env.update(
        {
            "HOME": home,
            "COLLINS_APP_ID": app_id,
            "COLLINS_KEEPER": "none",
            "COLLINS_CHATS_DIR": os.path.join(home, ".local", "share", "collins", "chats"),
            "COLLINS_UPLOADS_DIR": os.path.join(root, "uploads"),
            "COLLINS_PTY_STATE_DIR": os.path.join(root, "pty"),
            "XDG_CONFIG_HOME": os.path.join(root, "config"),
            "XDG_STATE_HOME": os.path.join(root, "state"),
            "XDG_CACHE_HOME": os.path.join(root, "cache"),
            "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
            "PATH": bin_dir + os.pathsep + env.get("PATH", "/usr/bin:/bin"),
            "SHELL": env.get("SHELL") or "/bin/bash",
        }
    )
    if env.get("COLLINS_LOG") is None and os.environ.get("COLLINS_LOG"):
        env["COLLINS_LOG"] = os.environ["COLLINS_LOG"]
    return env


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--service-only", action="store_true", help="start the service, print the socket, wait"
    )
    parser.add_argument("--keep", action="store_true", help="leave the scratch tree behind")
    parser.add_argument("--root", default="", help="the scratch tree (default: a temporary directory)")
    own, kit_args = parser.parse_known_args(argv)
    root = own.root or tempfile.mkdtemp(prefix="collins-conformance-run-")
    app_id = "com.episode6.Collins.Conformance." + uuid.uuid4().hex[:8]
    env = stage(root, app_id)
    # The harness sets XDG_RUNTIME_DIR to a short scratch directory of its
    # own and spawns the service with the environment it is handed.
    os.environ["COLLINS_APP_ID"] = app_id
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    sys.path.insert(0, REPO)
    import e2e_service

    env["XDG_RUNTIME_DIR"] = (
        os.environ["XDG_RUNTIME_DIR"] if e2e_service.RUNTIME_DIR else env.get("XDG_RUNTIME_DIR", "")
    )
    if not env["XDG_RUNTIME_DIR"]:
        del env["XDG_RUNTIME_DIR"]
    from collins.api import server as api_server

    e2e_service.start_service(env=env, app_id=app_id)
    sock = api_server.socket_path(app_id)
    print(f"service {app_id} on {sock}", flush=True)
    print(f"scratch tree {root}", flush=True)
    try:
        if own.service_only:
            print("waiting; Ctrl+C ends the service", flush=True)
            try:
                while True:
                    import time

                    time.sleep(3600)
            except KeyboardInterrupt:
                return 0
        kit_scratch = os.path.join(root, "kit")
        command = [
            sys.executable,
            "-m",
            "conformance",
            sock,
            "--fake-claude",
            FAKE,
            "--scratch",
            kit_scratch,
            *kit_args,
        ]
        print(" ".join(command), flush=True)
        kit_env = dict(os.environ)
        return subprocess.call(command, cwd=REPO, env=kit_env)
    finally:
        e2e_service._stop_all()
        if not own.keep and not own.root:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
