# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""Level 0, the wire: what docs/api/index.md asks of every server.

The hello and its negotiation, `sequence` before hello, `unknown` for a
made-up type, `invalid` on a bound and on a service-only event sent with an
id (`direction` itself no client can draw: see `check_direction`),
the two channels, `subscribe`'s snapshot ordering and reply counts, the
keepalives across 25 s of idleness, a chunked request and reply where the
server claims `files`, `service.status`'s required fields, the local
proof, and the rule that every request is answered.
"""

from __future__ import annotations

import os
import stat
import time

from .. import client as apiclient
from .. import shapes, ws
from ..reporter import CheckFailed, CheckSkipped
from ..runner import Run

IDLE_S = 25.0
CHUNK_TEXT_CHARS = 1536 * 1024  # a 1.5 MiB text: over a frame, so it travels chunked
REFUSAL_PROMPT_S = 2.0  # "a refusal promptly: a few hundred milliseconds at most"


def expect_refusal(reply: apiclient.Reply, code: str, what: str) -> None:
    if reply.ok:
        raise CheckFailed(f"{what}: answered ok, expected `{code}`")
    if reply.error != code:
        raise CheckFailed(f"{what}: refused `{reply.error}` ({reply.msgid}), expected `{code}`")


def check_hello(run: Run, notes: list[str]) -> None:
    c = run.client("conformance-0")
    reply = c.hello
    assert reply is not None
    p = run.p
    for name in ("protocol", "min_protocol", "version", "service_id"):
        if name not in reply.fields:
            raise CheckFailed(f"the hello reply lacks `{name}`")
    agreed = p.negotiate(p.PROTOCOL, p.MIN_PROTOCOL, reply["protocol"], reply["min_protocol"])
    if agreed is None or reply["protocol"] != agreed:
        raise CheckFailed(
            f"the server answered protocol {reply['protocol']} (min {reply['min_protocol']}); "
            f"the kit speaks {p.MIN_PROTOCOL}..{p.PROTOCOL}"
        )
    if not isinstance(reply.get("caps"), list):
        raise CheckFailed("the hello reply has no `caps` list")
    run.hello = reply
    run.caps = set(reply["caps"])
    run.implementation = reply.get("implementation") or ""
    unknown_caps = sorted(cap for cap in run.caps if cap not in p.CAPABILITIES)
    if unknown_caps:
        notes.append(f"caps the kit does not know (allowed, ignored): {', '.join(unknown_caps)}")
    if not run.implementation:
        notes.append(
            "no `implementation` in the hello: a server should send one, or a Collins window reads a "
            "family-less hello as a service from before families"
        )
    if "local_proof" in reply.fields and "local" not in run.caps:
        raise CheckFailed("the hello names a `local_proof` but does not list `local`")
    if "local" in run.caps and "local_proof" not in reply.fields:
        raise CheckFailed("the hello lists `local` but names no `local_proof`")
    notes.append(
        f"implementation {run.implementation or '?'} {reply['version']}, "
        f"caps: {' '.join(sorted(run.caps)) or '(none)'}"
    )
    c.close()


def check_protocol_refusal(run: Run, notes: list[str]) -> None:
    """A hello whose window does not meet the server's is refused `protocol`,
    naming both numbers and the caps."""
    c = apiclient.Client(run.options.sock, device="conformance-far")
    c.primary.connect()
    reply = c.call_on(c.primary, c.hello_message(protocol=60000, min_protocol=60000))
    expect_refusal(reply, "protocol", "a hello at protocol 60000")
    if "caps" not in reply.args:
        raise CheckFailed(f"the `protocol` refusal names no `caps` in its args: {reply.args}")
    numbers = [v for v in reply.args.values() if isinstance(v, int)]
    if len(numbers) < 2:
        notes.append(f"the refusal's args name fewer than two numbers: {reply.args}")
    # The server closes the connection after the refusal.
    closed = False
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            c.primary.pump(0.5)
    except ws.Closed:
        closed = True
    if not closed:
        notes.append("the connection stayed open after a `protocol` refusal (the spec says it is closed)")
    c.abort()


def check_sequence(run: Run, notes: list[str]) -> None:
    c = apiclient.Client(run.options.sock, device="conformance-early")
    c.primary.connect()
    reply = c.call_on(c.primary, {"t": "service.status"})
    expect_refusal(reply, "sequence", "a request before hello")
    hello = c.call_on(c.primary, c.hello_message())
    if not hello.ok:
        raise CheckFailed(f"hello after a refused early request: {hello.describe()}")
    again = c.call_on(c.primary, c.hello_message())
    expect_refusal(again, "sequence", "a second hello")
    c.close()


def check_unknown(run: Run, notes: list[str]) -> None:
    c = run.client()
    started = time.monotonic()
    reply = c.call_on(c.primary, {"t": "kit.no-such-type", "field": 1})
    expect_refusal(reply, "unknown", "a made-up type")
    if time.monotonic() - started > REFUSAL_PROMPT_S:
        raise CheckFailed(f"the refusal took {time.monotonic() - started:.1f} s")
    c.close()


def check_direction(run: Run, notes: list[str]) -> None:
    """A service-only type from a client. With an id it is an event with an
    id, refused `invalid`; without one it is an event from the wrong peer,
    which has no id to answer and is dropped, the connection unharmed.
    (`direction` itself names a request from the peer that may not send it,
    and every request type is the client's: no frame a client sends can
    draw it, which the kit records rather than tests.)"""
    c = run.client()
    reply = c.call_on(c.primary, {"t": "rows", "rows": []})
    expect_refusal(reply, "invalid", "`rows`, a service-only event, with an id")
    reply = c.call_on(c.primary, {"t": "pty-exited", "pty": shapes.IMPOSSIBLE_PTY, "status": 0})
    expect_refusal(reply, "invalid", "`pty-exited` with an id")
    before = len(c.events)
    c.send_raw({"t": "item", "session": "kit-direction", "path": "/x"})
    c.send_raw({"t": "pty", "pty": shapes.IMPOSSIBLE_PTY, "kind": "agent"})
    c.pump(0.5)
    if len(c.events) != before:
        raise CheckFailed("a wrong-peer event drew an answer: " + c.events[before].type)
    status = c.call("service.status")
    if not status.ok:
        raise CheckFailed(f"the connection after a wrong-peer event: {status.describe()}")
    c.close()


def check_invalid(run: Run, notes: list[str]) -> None:
    c = run.client()
    nowhere = shapes.IMPOSSIBLE_PTY
    reply = c.call("attach", pty=nowhere, cols=5000, rows=10)
    expect_refusal(reply, "invalid", "`attach` with cols 5000")
    reply = c.call("state.get", key="")
    expect_refusal(reply, "invalid", "`state.get` with an empty key")
    reply = c.call("attach", pty=nowhere)
    expect_refusal(reply, "invalid", "`attach` without cols and rows")
    reply = c.call("pty.info", pty="one")
    expect_refusal(reply, "invalid", "`pty.info` with a string pty")
    reply = c.call("close", pty=nowhere, mode="later")
    expect_refusal(reply, "invalid", "`close` with a mode outside its enumeration")
    # A request whose id is not one (below 0) has nothing to answer with: it
    # is dropped, and the connection goes on.
    c.send_raw({"t": "service.status", "id": -1})
    c.pump(0.5)
    status = c.call("service.status")
    if not status.ok:
        raise CheckFailed(f"the connection after an unusable id: {status.describe()}")
    c.close()


def check_two_channels(run: Run, notes: list[str]) -> None:
    c = run.client(client_id="kit-two-" + os.urandom(4).hex())
    sync_hello = c.open_sync()
    if not sync_hello.ok:
        raise CheckFailed(f"the sync channel's hello: {sync_hello.describe()}")
    before = len(c.events)
    reply = c.call_sync("service.status")
    if not reply.ok:
        raise CheckFailed(f"`service.status` on the sync channel: {reply.describe()}")
    if any(e.connection == "sync" for e in c.events[before:]):
        raise CheckFailed("the sync channel carried an event")
    # A sync channel whose primary has not said hello is refused `sequence`.
    lone = apiclient.Client(
        run.options.sock, device="conformance-lone", client_id="kit-lone-" + os.urandom(4).hex()
    )
    lone.sync = apiclient.Connection(lone, "sync", "sync")
    lone.sync.connect()
    reply = lone.call_on(lone.sync, lone.hello_message(channel="sync"))
    expect_refusal(reply, "sequence", "a sync channel before its primary")
    lone.abort()
    # A reconnect: a hello with the same client_id takes the client over.
    taker = apiclient.Client(run.options.sock, device="conformance-taker", client_id=c.client_id)
    reply = taker.connect()
    if not reply.ok:
        raise CheckFailed(f"a hello reusing a client_id: {reply.describe()}")
    old_closed = False
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            c.primary.pump(0.5)
    except ws.Closed:
        old_closed = True
    if not old_closed:
        raise CheckFailed("the old primary stayed open after its client_id was taken over")
    taker.close()
    c.abort()


def check_subscribe(run: Run, notes: list[str]) -> None:
    c = run.client()
    reply, snapshot = c.subscribe()
    if not reply.ok:
        raise CheckFailed(f"subscribe: {reply.describe()}")
    rows = [e for e in snapshot if e.type == "rows"]
    if not rows:
        raise CheckFailed("no `rows` event came before the subscribe reply")
    if not isinstance(rows[-1].get("rows"), list):
        raise CheckFailed("`rows` carries no list")
    if run.options.level >= 1:
        ptys_seen = {e.get("pty") for e in snapshot if e.type == "pty" and e.get("kind") == "agent"}
        sessions_seen = {e.get("pty") for e in snapshot if e.type == "session"}
        if ptys_seen - sessions_seen:
            raise CheckFailed(
                f"the snapshot sent no `session` for the agent ptys {sorted(ptys_seen - sessions_seen)}"
            )
    items = [e for e in snapshot if e.type == "item"]
    ptys = [e for e in snapshot if e.type == "pty"]
    if reply.get("items", len(items)) != len(items):
        raise CheckFailed(
            f"the reply counts {reply.get('items')} items; {len(items)} `item` events were sent"
        )
    if reply.get("ptys", len(ptys)) != len(ptys):
        raise CheckFailed(f"the reply counts {reply.get('ptys')} ptys; {len(ptys)} `pty` events were sent")
    first_seen: set[str] = set()
    for item in items:
        session = item.get("session")
        if session in first_seen:
            continue
        first_seen.add(session)
        if "path" not in item.fields:
            raise CheckFailed(f"the first `item` for {session} has no `path`")
    for name in [e.type for e in snapshot]:
        entry = run.p.TYPES.get(name)
        if entry is not None and entry.family != run.p.FAMILY_CORE and entry.family not in run.caps:
            raise CheckFailed(f"the snapshot carried `{name}` of the unlisted family `{entry.family}`")
    settings = [e for e in snapshot if e.type == "state.set" and e.get("key") == "settings"]
    if run.options.level >= 1 and not settings:
        raise CheckFailed("the snapshot carried no `state.set` for the `settings` key (Level 1)")
    for e in snapshot:
        if e.type == "state.set":
            key = e.get("key")
            home = _state_key_home(run, key)
            if home is not None and home != "core" and home not in run.caps:
                raise CheckFailed(f"the snapshot sent state key `{key}` of the unlisted family `{home}`")
    notes.append(f"{len(items)} items, {len(ptys)} ptys, {len(snapshot)} events")
    c.close()


def _state_key_home(run: Run, key: str) -> str | None:
    p = run.p
    for entry in p.CONFORMANCE_LEVELS.values():
        if key in entry.state_keys:
            return "core"
    for family, entry in p.CONFORMANCE_FAMILIES.items():
        if key in entry.state_keys:
            return family
    return None


def check_keepalive(run: Run, notes: list[str]) -> None:
    c = run.client()
    c.pump(IDLE_S)
    reply = c.call("service.status")
    if not reply.ok:
        raise CheckFailed(f"`service.status` after {IDLE_S:.0f} s idle: {reply.describe()}")
    if c.pings == 0:
        notes.append(f"no WebSocket ping arrived in {IDLE_S:.0f} s (the numbers table says every 10 s)")
    else:
        notes.append(f"{c.pings} pings in {IDLE_S:.0f} s")
    c.close()


def check_service_status(run: Run, notes: list[str]) -> None:
    c = run.client()
    reply = c.call("service.status")
    if not reply.ok:
        raise CheckFailed(f"service.status: {reply.describe()}")
    if not isinstance(reply.get("version"), str) or not reply["version"]:
        raise CheckFailed("`version` missing or empty")
    if reply.get("protocol") != run.hello["protocol"]:
        raise CheckFailed(
            f"`protocol` {reply.get('protocol')} differs from the hello's {run.hello['protocol']}"
        )
    p = run.p
    for name in reply.fields:
        home = None
        for entry in p.CONFORMANCE_LEVELS.values():
            if name in entry.status_fields:
                home = "core"
        for family, entry in p.CONFORMANCE_FAMILIES.items():
            if name in entry.status_fields:
                home = family
        if home not in (None, "core") and home not in run.caps:
            raise CheckFailed(f"`service.status` carries `{name}` of the unlisted family `{home}`")
    if run.options.level >= 1:
        for name in ("cli", "home"):
            if name not in reply.fields:
                notes.append(f"no `{name}` in service.status (a Level 1 server should send it)")
    c.close()


def check_local(run: Run, notes: list[str]) -> None:
    c = run.client()
    proof = run.hello.get("local_proof")
    if proof is None:
        reply = c.call("local", proof="00" * 32)
        expect_refusal(reply, "refused", "`local` with no proof offered")
        c.close()
        return
    if not isinstance(proof, dict) or not isinstance(proof.get("length"), int):
        raise CheckFailed(f"`local_proof` is not {{path, length}}: {proof!r}")
    length = proof["length"]
    wrong = c.call("local", proof="00" * length)
    expect_refusal(wrong, "refused", "`local` with wrong bytes")
    # The proof file as the kit finds it on its own filesystem: beside the
    # socket, never at the path the hello names.
    candidate = os.path.join(os.path.dirname(os.path.abspath(run.options.sock)), "local-proof")
    try:
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        notes.append("no readable proof file beside the socket: the kit stays remote")
        c.close()
        return
    try:
        info = os.fstat(fd)
        data = os.read(fd, length + 1)
    finally:
        os.close(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        notes.append(
            "the proof file beside the socket is not a regular 0600 file of this uid: the kit stays remote"
        )
        c.close()
        return
    if len(data) != length:
        raise CheckFailed(f"the proof file holds {len(data)} bytes; the hello said {length}")
    right = c.call("local", proof=data.hex())
    if not right.ok:
        raise CheckFailed(f"`local` with the proof's bytes: {right.describe()}")
    notes.append("local proof accepted")
    c.close()


def prove_local(run: Run, c: apiclient.Client) -> bool:
    """Send the proof when the kit can read it beside the socket; whether it was accepted."""
    proof = run.hello.get("local_proof") if run.hello else None
    if not isinstance(proof, dict):
        return False
    candidate = os.path.join(os.path.dirname(os.path.abspath(run.options.sock)), "local-proof")
    try:
        fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        data = os.read(fd, int(proof.get("length", 32)) + 1)
    finally:
        os.close(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        return False
    if len(data) != proof.get("length"):
        return False
    return c.call("local", proof=data.hex()).ok


def chunk_round_trip(c: apiclient.Client, path: str) -> None:
    text = ("0123456789abcdef" * (CHUNK_TEXT_CHARS // 16))[:CHUNK_TEXT_CHARS]
    reply = c.call("fs.write", timeout=30, path=path, text=text, expect_mtime=None)
    if not reply.ok:
        raise CheckFailed(f"a 1.5 MiB `fs.write` sent chunked: {reply.describe()}")
    read = c.call("fs.read", timeout=30, path=path)
    if not read.ok:
        raise CheckFailed(f"`fs.read` of the file: {read.describe()}")
    if read.chunked != "text":
        raise CheckFailed("`fs.read` of a 1.5 MiB file came back in one frame, not chunked")
    if read.get("text") != text:
        raise CheckFailed("the text that came back differs from what was written")


def check_chunking(run: Run, notes: list[str]) -> None:
    """A 1.5 MiB `fs.write`, chunked out, and the `fs.read` that comes back
    chunked. A remote client's writes may be confined to the roots the
    server knows (its sessions' directories), none of which exists yet at
    Level 0: the kit proves local when it can, and otherwise leaves this to
    the `files` family's check, which writes under a session's directory."""
    run.need("files")
    c = run.client()
    local = prove_local(run, c)
    path = os.path.join(run.options.scratch, "chunked.txt")
    try:
        chunk_round_trip(c, path)
    except CheckFailed as failure:
        if not local and "outside" in str(failure):
            raise CheckSkipped(
                "a client that has not proved local is confined to the server's roots, as the page says; "
                "the `files` check writes under a session's directory instead"
            ) from failure
        raise
    notes.append(f"{CHUNK_TEXT_CHARS} characters each way" + (" (local)" if local else ""))
    c.close()


def check_every_request_answered(run: Run, notes: list[str]) -> None:
    """Every request type is answered within the call timeout; a type of a
    family the server does not list gets `unknown` and nothing else; a core
    type at or below the level under test is never `unknown` (a server run at
    `--level 1` may answer the store's requests `unknown`). Every sample names
    a pty no server has, and nothing of `internal`, `debug` or a skipped
    family is sent to a server that lists it: the kit touches nothing it did
    not make."""
    c = run.client()
    p = run.p
    unanswered = []
    wrong = []
    slow = []
    # Types with side effects a run must not trigger are left out; the
    # unknown-family ones have no side effect by definition.
    destructive = {
        "service.restart", "store.trash", "store.delete", "store.forget", "store.forget-project",
        "store.archive-project", "store.keep-projects", "fs.trash", "fs.paste", "fs.rename",
        "fs.mkdir", "fs.newdir", "fs.write", "close", "spawn", "job.start", "notify.clear",
        "notify.remove", "notify.test", "pr.action", "pr.comment", "pr.review", "pr.thread",
        "pr.set", "pr.sweep", "sandbox.allow", "sandbox.revoke", "sandbox.restart", "sandbox.drop",
        "sandbox.forget", "sandbox.derive", "sandbox.tools", "store.add-project", "trust.grant",
        "store.move-project", "store.archive", "store.rename", "store.favorite",
        "store.regenerate-name", "store.show-archived", "store.refresh", "store.forward",
        "icon.save", "usage.get", "models.get", "diff.set-notes", "prs.restore", "state.set",
        "store.flags", "notify.green", "notify.rekey", "notify.post", "seen", "local",
        "subscribe", "hello", "git.run", "git.plan", "cut", "send", "switch", "mention",
        "draft.restore", "write", "prompt", "paint", "clear", "panel.key", "panel.history",
    }  # fmt: skip
    for name in shapes.request_types():
        entry = p.TYPES[name]
        claimed = entry.family == p.FAMILY_CORE or entry.family in run.caps
        if claimed and (name in destructive or entry.family in (p.CAP_DEBUG, p.CAP_INTERNAL)):
            continue  # nothing the kit would ask a live server for its own sake
        if claimed and entry.family in run.options.skip_families:
            continue
        above_level = entry.family == p.FAMILY_CORE and entry.level > run.options.level
        message = shapes.minimal_request(name)
        assert message.get("pty") in (None, shapes.IMPOSSIBLE_PTY), name  # never a pty that could exist
        try:
            reply = c.call_on(c.primary, message, timeout=10.0)
        except apiclient.Timeout:
            unanswered.append(name)
            continue
        if reply.elapsed > REFUSAL_PROMPT_S and not reply.ok:
            slow.append(f"{name} ({reply.elapsed:.1f} s)")
        if not claimed:
            if reply.ok or reply.error != "unknown":
                wrong.append(f"{name} ({entry.family}): {reply.describe()}")
        elif not reply.ok and reply.error == "unknown" and not above_level:
            wrong.append(f"{name} ({entry.family}, claimed): refused `unknown`")
    if unanswered:
        raise CheckFailed(f"never answered: {', '.join(unanswered)}")
    if wrong:
        raise CheckFailed("; ".join(wrong[:6]) + (" ..." if len(wrong) > 6 else ""))
    if slow:
        notes.append("slow refusals: " + ", ".join(slow))
    c.close()


def run_level0_hello_only(run: Run) -> None:
    """Only the hello: what every later group needs of Level 0."""
    run.reporter.group("level 0")
    run.reporter.run("hello and caps", check_hello, run)


def run_level0(run: Run) -> None:
    r = run.reporter
    r.group("level 0")
    r.run("hello and caps", check_hello, run)
    if run.hello is None:
        raise CheckSkipped("no hello")
    r.run("a hello outside the window is refused `protocol`", check_protocol_refusal, run)
    r.run("a request before hello is `sequence`", check_sequence, run)
    r.run("a made-up type is `unknown`", check_unknown, run)
    r.run("a service-only type from a client is `invalid` with an id, dropped without", check_direction, run)
    r.run("a bound exceeded is `invalid`", check_invalid, run)
    r.run("two channels", check_two_channels, run)
    r.run("subscribe: the snapshot, then the reply", check_subscribe, run)
    r.run("service.status", check_service_status, run)
    r.run("the local proof", check_local, run)
    r.run("a chunked request and reply", check_chunking, run)
    r.run("every request is answered; unlisted families are `unknown`", check_every_request_answered, run)
    r.run(f"the connection survives {IDLE_S:.0f} s idle", check_keepalive, run)
