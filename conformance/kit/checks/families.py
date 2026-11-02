# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""The families: one short check per family the server claims.

`git`: `git.info` and a builder on a scratch repository. `files`: a read,
a write, a `stale` write and the chunked round trip under a session's
directory. `prs`: skipped unless `--with-gh`. `sandbox`: a clean refusal
of what does not exist. `tools`: the blob and upload endpoints. `jobs`: an
unknown kind is `invalid`. `usage`: `models.defaults`. `background`:
`store.forward` answered. `shells`: a shell pty, `clear`, `panel.key`.
`composer`: the cut and the draft's restore on the fake's box. `internal`
and `debug` are Collins' own and are not checked.
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from .. import client as apiclient
from .. import ws
from ..reporter import CheckFailed, CheckSkipped
from ..runner import COLS, ROWS, TERM, Run
from .level0 import chunk_round_trip, prove_local
from .sessions import spawn_session

CLEAN_REFUSALS = {"refused", "gone", "invalid", "failed", "stale"}


def clean_refusal(reply: apiclient.Reply, what: str) -> None:
    if reply.ok:
        return
    if reply.error == "unknown":
        raise CheckFailed(f"{what}: refused `unknown` by a server that lists the family")
    if reply.error not in CLEAN_REFUSALS or not reply.msgid:
        raise CheckFailed(f"{what}: {reply.describe()}")


def check_git(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("git")
    repo = os.path.join(run.options.scratch, "repo")
    os.makedirs(repo, exist_ok=True)
    try:
        subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True, capture_output=True, timeout=30)
        env = dict(os.environ, GIT_AUTHOR_NAME="kit", GIT_AUTHOR_EMAIL="kit@example.invalid",
                   GIT_COMMITTER_NAME="kit", GIT_COMMITTER_EMAIL="kit@example.invalid")  # fmt: skip
        with open(os.path.join(repo, "README"), "w", encoding="utf-8") as handle:
            handle.write("kit\n")
        subprocess.run(
            ["git", "-C", repo, "add", "README"], check=True, capture_output=True, timeout=30, env=env
        )
        subprocess.run(
            ["git", "-C", repo, "commit", "-q", "-m", "kit"],
            check=True,
            capture_output=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CheckSkipped(f"no git on the kit's machine to make a repository with: {error}") from error
    reply = client.call("git.info", cwd=repo, changes=True)
    if not reply.ok:
        raise CheckFailed(f"git.info: {reply.describe()}")
    if reply.get("root") != repo or reply.get("branch") != "main":
        raise CheckFailed(f"git.info answered root {reply.get('root')!r}, branch {reply.get('branch')!r}")
    reply = client.call("git.info", cwd=run.options.scratch)
    if not reply.ok or reply.get("root") is not None:
        raise CheckFailed(f"git.info outside any repository: {reply.describe()}")
    reply = client.call("git.run", cwd=repo, builder="head_abbrev_argv", args={}, timeout=30)
    if not reply.ok or reply.get("status") != 0 or not (reply.get("stdout") or "").strip():
        raise CheckFailed(f"git.run head_abbrev_argv: {reply.describe()}")
    reply = client.call("git.run", cwd=repo, builder="kit_no_such_builder", args={})
    if reply.ok or reply.error != "unknown":
        raise CheckFailed(
            f"git.run with a builder name the server has no builder for: {reply.describe()}, "
            "expected `unknown` (a named operation a known type selects that the server does not know)"
        )
    reply = client.call(
        "git.run", cwd=repo, builder="log_argv", args={"range_args": ["HEAD"], "limit": "ten"}
    )
    if reply.ok or reply.error != "invalid":
        raise CheckFailed(
            f"git.run with an argument of the wrong type: {reply.describe()}, expected `invalid`"
        )
    resource = "/api/blob?kind=git&repo=" + repo + "&ref=HEAD&path=README"
    status, _, body = ws.http_request(run.options.sock, "GET", resource,
                                      headers={"Collins-Client": client.client_id})  # fmt: skip
    notes.append(f"GET /api/blob?kind=git answered {status}")


def check_files(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("files")
    directory = run.cwd  # a session's directory: a root the server knows
    reply = client.call("fs.stat", path=directory)
    if not reply.ok or reply.get("kind") != "dir":
        raise CheckFailed(f"fs.stat of the session's directory: {reply.describe()}")
    reply = client.call("fs.stat", path=os.path.join(directory, "kit-missing"))
    if not reply.ok or reply.get("kind") != "missing":
        raise CheckFailed(f"fs.stat of a missing path: {reply.describe()}")
    path = os.path.join(directory, "kit.txt")
    reply = client.call("fs.write", path=path, text="one\n", expect_mtime=None)
    if not reply.ok:
        raise CheckFailed(f"fs.write of a new file: {reply.describe()}")
    mtime = reply["mtime"]
    read = client.call("fs.read", path=path)
    if (
        not read.ok
        or read.get("text") != "one\n"
        or read.get("mtime") != mtime
        or read.get("binary") is not False
    ):
        raise CheckFailed(f"fs.read after the write: {read.describe()}")
    time.sleep(0.05)  # past the file system's timestamp granularity, so the mtime moves
    reply = client.call("fs.write", path=path, text="two\n", expect_mtime=mtime)
    if not reply.ok:
        raise CheckFailed(f"fs.write with the mtime the read answered: {reply.describe()}")
    if reply["mtime"] == mtime:
        raise CheckFailed(
            "the second write's mtime did not move: the server's mtimes cannot tell two writes apart"
        )
    stale = client.call("fs.write", path=path, text="three\n", expect_mtime=mtime)
    if stale.ok or stale.error != "stale":
        raise CheckFailed(f"fs.write with a moved mtime: {stale.describe()}, expected `stale`")
    read = client.call("fs.read", path=path)
    if read.get("text") != "two\n":
        raise CheckFailed("a `stale` write changed the file")
    reply = client.call("fs.list", path=directory, hidden=False, root=directory)
    if not reply.ok or not any(e.get("name") == "kit.txt" for e in reply.get("entries", [])):
        raise CheckFailed(f"fs.list of the directory: {reply.describe()}")
    reply = client.call("fs.list", path=os.path.join(directory, "kit-missing"), hidden=False, root=directory)
    if reply.ok or reply.error != "gone" or reply.msgid != run.p.FOLDER_GONE_MSGID:
        raise CheckFailed(
            f"fs.list of a missing folder: {reply.describe()}, expected `gone` with the matched sentence"
        )
    chunk_round_trip(client, os.path.join(directory, "kit-chunked.txt"))
    notes.append(
        "the chunked round trip under the session's directory" + (" (local)" if client.local_proved else "")
    )


def check_prs(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("prs")
    if not run.options.with_gh:
        raise CheckSkipped("--with-gh not given (the checks need the GitHub CLI and the network)")
    reply = client.call("pr.fetch", urls=["https://github.com/episode6/collins/pull/1"], timeout=60)
    if not reply.ok:
        raise CheckFailed(f"pr.fetch: {reply.describe()}")
    reply = client.call("pr.detail", url="https://github.com/episode6/collins/pull/1", timeout=60)
    clean_refusal(reply, "pr.detail")


def check_sandbox(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("sandbox")
    status = client.call("service.status")
    reason = status.get("sandbox")
    notes.append("sandbox available" if reason == "" else f"sandbox unavailable: {reason!r}")
    for name, fields in (
        ("sandbox.grants", {"box": "kit-no-such-box"}),
        ("sandbox.plan", {"box": "kit-no-such-box"}),
    ):
        reply = client.call(name, **fields)
        if reply.ok:
            continue
        clean_refusal(reply, name)
    reply = client.call("sandbox.allow", box="kit-no-such-box", scope="session", path="/")
    clean_refusal(reply, "sandbox.allow")


def check_tools(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("tools")
    headers = {"Collins-Client": client.client_id}
    status, _, _ = ws.http_request(
        run.options.sock, "GET", "/api/blob?kind=kit-no-such-kind", headers=headers
    )
    if status != 404:
        raise CheckFailed(f"GET /api/blob of an unknown kind answered {status}, not 404")
    body = b"the kit's upload\n"
    status, _, data = ws.http_request(
        run.options.sock, "PUT", "/api/upload?name=kit.txt", body=body,
        headers={**headers, "Content-Length": str(len(body)), "Content-Type": "text/plain"},
    )  # fmt: skip
    if status != 200:
        raise CheckFailed(f"PUT /api/upload answered {status}: {data[:200]!r}")
    try:
        answer = json.loads(data)
    except ValueError as error:
        raise CheckFailed(f"the upload's answer is not JSON: {data[:100]!r}") from error
    if (
        not isinstance(answer, dict)
        or not isinstance(answer.get("path"), str)
        or not answer["path"].startswith("/")
    ):
        raise CheckFailed(f"the upload's answer names no path: {answer!r}")
    reply = client.call("diff.set-notes", session="kit-no-such-session", notes=[], highlights=[])
    clean_refusal(reply, "diff.set-notes")
    client.send_event("tool-reply", call="kit-no-such-call", ok=False, text="not here")
    client.pump(0.5)
    status = client.call("service.status")
    if not status.ok:
        raise CheckFailed("the connection after a tool-reply for an unknown call")


def check_jobs(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("jobs")
    reply = client.call_on(client.primary, {"t": "job.start", "kind": "kit-no-such-kind", "args": {}})
    if reply.ok or reply.error != "invalid":
        raise CheckFailed(f"job.start of an unknown kind: {reply.describe()}, expected `invalid`")
    reply = client.call("job.cancel", job="kit-no-such-job")
    clean_refusal(reply, "job.cancel")


def check_usage(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("usage")
    reply = client.call("models.defaults", cwd=run.cwd, timeout=20)
    if not reply.ok:
        raise CheckFailed(f"models.defaults: {reply.describe()}")
    notes.append(f"defaults {reply.fields}")


def check_background(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("background")
    reply = client.call("store.forward", session="kit-no-such-a", to="kit-no-such-b")
    clean_refusal(reply, "store.forward")
    reply = client.call("close", pty=4000000000, mode="background")
    if reply.ok or reply.error != "gone":
        raise CheckFailed(
            f"close {{mode: background}} of a pty that does not exist: {reply.describe()}, expected `gone`"
        )


def check_shells(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("shells")
    reply = client.call(
        "spawn", kind="shell", cwd=run.cwd, cols=COLS, rows=ROWS, history="kit-history", ordinal=0
    )
    if not reply.ok:
        raise CheckFailed(f"spawn {{kind: shell}}: {reply.describe()}")
    pty = reply["pty"]
    attach = client.attach(pty, COLS, ROWS)
    if not attach.ok:
        raise CheckFailed(f"attach: {attach.describe()}")
    # The shell's prompt, drawn and quiet, before anything is typed: a line
    # typed earlier sits in the terminal's cooked-mode buffer, echoed on the
    # screen before the shell has read it, and a `clear` sent then runs
    # before the command does. The marker is computed by the shell, so what
    # the kit waits for is the command's output and never the echo of what
    # it typed.
    if not client.wait_quiet(pty, 0.5, 15.0):
        raise CheckFailed("the shell did not draw its prompt and go quiet within 15 s")
    client.send_input(pty, b"echo kit-shell-$((40+2))-marker\r")
    if client.wait_output(pty, b"kit-shell-42-marker", 10.0) < 0:
        raise CheckFailed("the shell did not run what was typed")
    if not client.wait_until(
        lambda: "kit-shell-42-marker" in (client.call("pty.capture", pty=pty).get("text") or ""), 5.0
    ):
        raise CheckFailed("the command's output is not in pty.capture")
    client.wait_quiet(pty, 0.3, 5.0)
    start = client.output_len(pty)
    reply = client.call("clear", pty=pty)
    if not reply.ok:
        raise CheckFailed(f"clear: {reply.describe()}")
    if not client.wait_until(
        lambda: "kit-shell-42-marker" not in (client.call("pty.capture", pty=pty).get("text") or ""), 5.0
    ):
        capture = client.call("pty.capture", pty=pty).get("text") or ""
        received = bytes(client.output[pty])[start:]
        raise CheckFailed(
            "`clear` left the marker in the shell's capture (its contract: wipe the screen and scrollback); "
            f"the client received {received[:120]!r} after the call; the capture's tail: {capture[-200:]!r}"
        )
    reply = client.call("panel.key", pty=pty, history=None)
    if not reply.ok:
        raise CheckFailed(f"panel.key: {reply.describe()}")
    info = client.call("pty.info", pty=pty)
    if not info.ok or info.get("kind") != "shell":
        raise CheckFailed(f"pty.info of the shell: {info.describe()}")
    since = len(client.events)
    client.call("close", pty=pty, mode="kill")
    if client.wait_for(lambda e: e.type == "pty-exited" and e.get("pty") == pty, 15.0, since) is None:
        raise CheckFailed("no `pty-exited` after close {mode: kill} of the shell")


def check_composer(run: Run, client: apiclient.Client, notes: list[str]) -> None:
    run.need("composer")
    if not os.path.isfile(run.options.fake_claude):
        raise CheckSkipped("no fake claude")
    s = spawn_session(run, client, notes)
    try:
        if not client.wait_until(lambda: s.facts().get("takes_prompt") is True, 15.0):
            raise CheckFailed("the fake's box never came up")
        text = "a draft the composer lifts out"
        client.call("write", pty=s.pty, text=text)
        if not client.wait_until(lambda: s.facts().get("takes_prompt") is False, 5.0):
            raise CheckFailed("takes_prompt stayed true with text in the box")
        since = len(client.events)
        reply = client.call("cut", pty=s.pty)
        if not reply.ok or not reply.get("handle"):
            raise CheckFailed(f"cut: {reply.describe()}")
        handle = reply["handle"]
        event = client.wait_for(
            lambda e: e.type == "cut" and e.get("handle") == handle and e.get("state") != "pending",
            10.0,
            since,
        )
        if event is None:
            raise CheckFailed("no `cut` event for the handle within 10 s")
        if event.get("state") != "seeded" or event.get("text") != text:
            raise CheckFailed(f"the cut's event: state {event.get('state')!r}, text {event.get('text')!r}")
        if not client.wait_until(lambda: s.facts().get("takes_prompt") is True, 5.0):
            raise CheckFailed("the box did not come up empty after the cut")
        reply = client.call("draft.restore", pty=s.pty, text=text)
        if not reply.ok or reply.get("restored") is not True:
            raise CheckFailed(f"draft.restore: {reply.describe()}")
        if not client.wait_until(
            lambda: text in s.capture() and s.facts().get("takes_prompt") is False, 10.0
        ):
            raise CheckFailed("the restored draft is not back in the box")
        client.send_input(s.pty, b"\x03")
        client.wait_until(lambda: s.facts().get("takes_prompt") is True, 5.0)
        reply = client.call("send", pty=s.pty, text="sent by the composer")
        if not reply.ok or reply.get("sent") is not True:
            raise CheckFailed(f"send: {reply.describe()}")
        if client.wait_output(s.pty, b"fake-claude got: sent by the composer", 10.0) < 0:
            raise CheckFailed("the fake never saw the composer's send")
    finally:
        client.call("close", pty=s.pty, mode="kill")


def run_families(run: Run) -> None:
    r = run.reporter
    r.group("families")
    client = run.client("conformance-families")
    client.subscribe()
    client.send_event("theme", term=TERM)
    client.local_proved = prove_local(run, client)
    checks = (
        ("git", "git.info and a builder on a scratch repository", check_git),
        ("files", "fs.read, fs.write, stale, fs.list, the chunked round trip", check_files),
        ("prs", "pr.fetch and pr.detail", check_prs),
        ("sandbox", "a clean refusal of what does not exist", check_sandbox),
        ("tools", "GET /api/blob, PUT /api/upload, diff.set-notes, tool-reply", check_tools),
        ("jobs", "job.start of an unknown kind is invalid", check_jobs),
        ("usage", "models.defaults", check_usage),
        ("background", "store.forward and close {mode: background} answered", check_background),
        ("shells", "a shell pty, clear, panel.key", check_shells),
        ("composer", "cut, draft.restore, send on the fake's box", check_composer),
    )
    for family, name, check in checks:
        label = f"{family}: {name}"
        if not run.claims(family):
            r.skip(label, f"the server does not list `{family}`")
        elif not run.wants_family(family):
            r.skip(label, f"`{family}` left out by the options")
        else:
            r.run(label, check, run, client)
    for family in ("internal", "debug"):
        if run.claims(family):
            r.skip(f"{family}", "the Collins service's own; no published contract to check")
    client.close()
