# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The host-side launcher a sandboxed session's command line starts with:

    python3 <…>/collins/sandboxrun.py <plan.json> -- claude --resume <id> …

Named by file, not as `-m collins.sandboxrun`: the typed line runs in the
tab's shell, which has no PYTHONPATH for a checkout, and a system-installed
collins would shadow the one running (providers.sandboxrun_path). It
reads the plan sandboxplan wrote, turns it into a bubblewrap invocation
and execs it, so the tab's shell ends up running `bwrap … -- claude …` as
its foreground job — every close flow Collins has (Ctrl+C Ctrl+C through
the pty's process group, the force-close killing the shell, the tab
dropping to the user's own shell when the CLI exits) holds unchanged, and
`--die-with-parent` ties the box to the shell.

The bwrap arguments travel over a pipe (`--args <fd>`, NUL-separated):
nothing sensitive on the typed command line, no quoting, no argv-length
worry. The environment scrub is bwrap's own `--unsetenv`, `$HOME` is set
with `--setenv`, and the GitHub token — when the plan asks for it — is
read off the host with `gh auth token` here and handed in as `GH_TOKEN`
the same way, never written to disk.

Standard library only, and nothing imported from the collins package: it
runs as a bare script from wherever Collins is installed, and it must
never half-run — a plan it can't read or a bwrap it can't find
is a refusal (exit 2 with the reason on stderr), never an unsandboxed run
of the command it was handed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

# The largest payload the pipe carries without a reader; Linux pipes buffer
# 64 KiB by default and a plan is a few kilobytes. Anything larger goes
# through a memfd instead, which has no such limit.
_PIPE_PAYLOAD_MAX = 60_000

_USAGE = "usage: python3 sandboxrun.py <plan.json> -- <command> [args…]"


class PlanError(Exception):
    """The plan file can't be used: unreadable, or not the shape written by
    collins.sandboxplan."""


def read_plan(path: str) -> dict:
    """The plan document at *path*, validated to the shape sandboxplan
    writes. Every field is checked before anything is exec'd: the file is
    Collins' own, mode 0600 under the runtime dir, but a wrong shape must
    fail here rather than inside bwrap."""
    try:
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, ValueError) as err:
        raise PlanError(f"can't read the plan {path}: {err}") from err
    if not isinstance(plan, dict):
        raise PlanError("the plan is not a JSON object")
    args = plan.get("bwrap_args")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise PlanError("the plan carries no bwrap arguments")
    if any("\0" in a for a in args):
        raise PlanError("a bwrap argument contains NUL")
    unsetenv = plan.get("unsetenv", [])
    if not isinstance(unsetenv, list) or not all(_env_name(v) for v in unsetenv):
        raise PlanError("the plan's unsetenv list is malformed")
    setenv = plan.get("setenv", {})
    if not isinstance(setenv, dict) or not all(
        _env_name(k) and isinstance(v, str) and "\0" not in v for k, v in setenv.items()
    ):
        raise PlanError("the plan's setenv map is malformed")
    if not isinstance(plan.get("gh_token", False), bool):
        raise PlanError("the plan's gh_token flag is malformed")
    return plan


def _env_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "=" not in name
        and "\0" not in name
        and name.isascii()
    )


def bwrap_args(plan: dict, gh_token: str | None) -> list[str]:
    """The full bubblewrap argument list for *plan*: its mounts, then the
    environment scrub and the settings, then the token when there is one.
    Environment last so nothing in the plan's own list can shadow it."""
    args = list(plan["bwrap_args"])
    for var in plan.get("unsetenv", []):
        args += ["--unsetenv", var]
    for key, value in plan.get("setenv", {}).items():
        args += ["--setenv", key, value]
    if gh_token:
        args += ["--setenv", "GH_TOKEN", gh_token]
    return args


def encode_args(args: list[str]) -> bytes:
    """NUL-separated, as bwrap's `--args` reads them."""
    return b"".join(a.encode("utf-8") + b"\0" for a in args)


def host_gh_token() -> str | None:
    """The GitHub token gh holds on the host, from `gh auth token` — the
    one local, offline read that works whether the token sits in
    hosts.yml or in the desktop keyring. None when gh is missing, logged
    out, or slow."""
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        result = subprocess.run(
            [gh, "auth", "token"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.decode("utf-8", errors="replace").strip()
    return token if token and "\0" not in token and "\n" not in token else None


def args_fd(payload: bytes) -> int:
    """An inheritable file descriptor bwrap can read *payload* from: the
    read end of a pipe already holding it, or a memfd when it would not
    fit a pipe's buffer without a reader."""
    if len(payload) <= _PIPE_PAYLOAD_MAX:
        read_end, write_end = os.pipe()
        try:
            os.write(write_end, payload)
        finally:
            os.close(write_end)
        fd = read_end
    else:
        fd = os.memfd_create("collins-sandbox-args", 0)
        os.write(fd, payload)
        os.lseek(fd, 0, os.SEEK_SET)
    os.set_inheritable(fd, True)
    return fd


def bwrap_executable() -> str | None:
    """COLLINS_BWRAP (a fake for tests and e2e checks), else `bwrap` on PATH."""
    override = os.environ.get("COLLINS_BWRAP")
    if override:
        return override if os.access(override, os.X_OK) else None
    return shutil.which("bwrap")


def split_argv(argv: list[str]) -> tuple[str, list[str]]:
    """(plan path, the command to run) out of the module's argv, which is
    `<plan> -- <command…>`. Raises ValueError for anything else."""
    if len(argv) < 3 or argv[1] != "--" or not argv[2:]:
        raise ValueError(_USAGE)
    return argv[0], argv[2:]


def main(argv: list[str] | None = None, exec_fn=os.execv) -> int:
    """Exec bubblewrap for the plan and command in *argv* (sys.argv[1:] by
    default). Returns an exit status only on refusal — a successful exec
    never returns. *exec_fn* is injectable for tests."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        plan_path, command = split_argv(argv)
    except ValueError as err:
        print(f"collins.sandboxrun: {err}", file=sys.stderr)
        return 2
    try:
        plan = read_plan(plan_path)
    except PlanError as err:
        print(f"collins.sandboxrun: {err}", file=sys.stderr)
        return 2
    bwrap = bwrap_executable()
    if not bwrap:
        print(
            "collins.sandboxrun: bubblewrap (bwrap) is not installed; "
            "refusing to run the session unsandboxed",
            file=sys.stderr,
        )
        return 2
    token = None
    if plan.get("gh_token"):
        token = host_gh_token()
        if token is None:
            print(
                "collins.sandboxrun: no GitHub CLI login on the host to share "
                "(gh auth token); gh inside the box will be logged out",
                file=sys.stderr,
            )
    fd = args_fd(encode_args(bwrap_args(plan, token)))
    exec_fn(bwrap, [bwrap, "--args", str(fd), "--", *command])
    return 0  # an injected exec_fn that returned


if __name__ == "__main__":
    sys.exit(main())
