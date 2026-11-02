# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""Level 1, the session facts: docs/api/sessions.md against a fake claude.

The server is pointed at the kit's fake claude as its `claude`. The kit
spawns a session, attaches, and holds the `session` and `item` events to
the page's required properties: `takes_prompt` within 5 s of the spawn,
false while the box holds text; a prompt that lands; `busy` within 3 s of
it and down within 10 s of the turn's end; one `finished` per turn, also
through the idle repaints that follow; `unread` on the row; `model`,
`effort` and `permission_mode` off the transcript; the id bound within
10 s of the first record; the one-series property on a second attach; a
`switch` reflected within a turn; the two roads a fall of `busy` takes
(a quiet under a second ends nothing; the agent's clear ends the turn
while output flows, and the rise after it is no failure); `busy` down
within 10 s of the agent leaving mid-turn; `close {exit}` and `pty-exited`.
"""

from __future__ import annotations

import json
import os
import re
import time

from .. import client as apiclient
from ..reporter import CheckFailed, CheckSkipped
from ..runner import COLS, ROWS, TERM, Run
from .terminal import normalise_capture

FAKE_MODEL = "claude-fake-5"
FAKE_EFFORT = "high"
FAKE_PERMISSION = "default"
PROGRESS_CLEAR = re.compile(rb"\x1b\]9;4;0;?(?:\x1b\\|\x07)")
REPORT = re.compile(rb"\x1b\]777;([^\x1b\x07]*)(?:\x1b\\|\x07)")


class Session:
    """A fake session the kit spawned, and what it knows of it."""

    def __init__(self, run: Run, client: apiclient.Client, device: str) -> None:
        self.run = run
        self.client = client
        self.pty: int | None = None
        self.handle = ""
        self.spawned_at = 0.0
        self.id: str | None = None

    def facts(self) -> dict:
        return self.client.session_facts(self.pty)

    def item(self) -> dict:
        return self.client.item_facts(self.id) if self.id else {}

    def capture(self) -> str:
        reply = self.client.call("pty.capture", pty=self.pty)
        return reply.get("text", "") if reply.ok else ""

    def output_since(self, start: int) -> bytes:
        return bytes(self.client.output.get(self.pty, b""))[start:]

    def wait_fact(self, name: str, value, timeout: float) -> bool:
        return self.client.wait_until(lambda: self.facts().get(name) == value, timeout)

    def wait_marker(self, text: str, timeout: float, start: int = 0) -> int:
        return self.client.wait_output(self.pty, text.encode(), timeout, start)

    def prompt(self, text: str, **fields) -> apiclient.Reply:
        return self.client.call("prompt", pty=self.pty, text=text, **fields)

    def finished_count(self, since: int = 0) -> int:
        return sum(1 for e in self.client.session_events(self.pty, since) if e.get("finished") is True)

    def busy_events(self, since: int = 0) -> list[tuple[float, bool]]:
        return [
            (e.when, e["busy"]) for e in self.client.session_events(self.pty, since) if "busy" in e.fields
        ]


def timeline(s: Session, since: int, t0: float) -> str:
    """The busy and finished fields of the session events since *since*, timed from *t0*."""
    parts = []
    for e in s.client.session_events(s.pty, since):
        moved = {k: v for k, v in e.fields.items() if k in ("busy", "finished", "landed", "takes_prompt")}
        if moved:
            parts.append(f"{e.when - t0:+.3f}s {moved}")
    return "; ".join(parts)


def spawn_session(run: Run, client: apiclient.Client, notes: list[str], **extra) -> Session:
    s = Session(run, client, client.device)
    fields = {
        "kind": "agent",
        "cwd": run.cwd,
        "cols": COLS,
        "rows": ROWS,
        "provider": "claude",
        # What the Collins window sends on every new chat: values that ask
        # for nothing, which every server accepts and ignores.
        "add_dirs": [],
        "worktree": False,
        "worktree_name": "",
        "sandbox": False,
    }
    fields.update(extra)
    s.spawned_at = time.monotonic()
    reply = client.call("spawn", **fields)
    if not reply.ok:
        raise CheckFailed(f"spawn: {reply.describe()}")
    s.pty = reply["pty"]
    s.handle = reply.get("handle") or ""
    if reply.get("cwd") not in (None, run.cwd):
        notes.append(f"the pty started in {reply['cwd']}, not the {run.cwd} asked for")
    attach = client.attach(s.pty, COLS, ROWS)
    if not attach.ok:
        raise CheckFailed(f"attach: {attach.describe()}")
    snapshot = client.wait_for(lambda e: e.type == "session" and e.get("pty") == s.pty, 5.0)
    if snapshot is None:
        raise CheckFailed("no `session` event within 5 s of the attach")
    if snapshot.get("handle") in (None, ""):
        raise CheckFailed("the session snapshot has no `handle`")
    if s.handle and snapshot["handle"] != s.handle:
        raise CheckFailed(
            f"the snapshot's handle {snapshot['handle']!r} is not the spawn reply's {s.handle!r}"
        )
    return s


# -- the checks -----------------------------------------------------------------------


def check_takes_prompt(s: Session, notes: list[str]) -> None:
    deadline = s.spawned_at + 5.0
    if not s.client.wait_until(
        lambda: s.facts().get("takes_prompt") is True, max(deadline - time.monotonic(), 0.1)
    ):
        facts = s.facts()
        raise CheckFailed(
            "takes_prompt was not true within 5 s of the spawn: "
            + json.dumps(
                {k: facts.get(k) for k in ("takes_prompt", "agent_running", "running_command", "pid", "cwd")}
            )
        )
    facts = s.facts()
    if facts.get("cwd") != s.run.cwd:
        raise CheckFailed(f"session.cwd is {facts.get('cwd')!r}, the spawn asked for {s.run.cwd!r}")
    if "pid" in facts and not isinstance(facts["pid"], int):
        raise CheckFailed(f"session.pid is {facts['pid']!r}")
    for name in ("agent_running", "running_command"):
        if facts.get(name) is False:
            raise CheckFailed(f"{name} is false while the fake runs")
    notes.append(f"{time.monotonic() - s.spawned_at:.1f} s from the spawn")


def check_second_attach(s: Session, notes: list[str]) -> None:
    """A client attaching now holds what the attached client holds (one
    series), and `entered` is null while the box is empty."""
    second = s.run.client("conformance-second")
    second.send_event("theme", term=TERM)
    reply = second.attach(s.pty, COLS, ROWS)
    if not reply.ok:
        raise CheckFailed(f"attach: {reply.describe()}")
    snapshot = second.wait_for(lambda e: e.type == "session" and e.get("pty") == s.pty, 5.0)
    if snapshot is None:
        raise CheckFailed("no `session` snapshot for the second client within 5 s")
    held = s.facts()
    differences = []
    for name in (
        "takes_prompt",
        "agent_running",
        "running_command",
        "busy",
        "model",
        "effort",
        "permission_mode",
        "session",
        "cwd",
    ):
        if name in held and snapshot.get(name) != held.get(name):
            differences.append(f"{name}: {snapshot.get(name)!r} against {held.get(name)!r}")
    if differences:
        raise CheckFailed("the snapshot differs from the series: " + "; ".join(differences))
    if snapshot.get("takes_prompt") is not True:
        raise CheckFailed("the snapshot's takes_prompt is not true while the box is up")
    if "entered" in snapshot.fields and snapshot["entered"] is not None:
        raise CheckFailed(f"the snapshot's entered is {snapshot['entered']!r} for an empty box")
    if snapshot.get("foreign_paste") is True:
        raise CheckFailed("the snapshot's foreign_paste is true for an empty box")
    for name in ("landed", "reset", "finished"):
        if snapshot.get(name) is False:
            raise CheckFailed(f"the one-shot {name} was sent false")
    second.close()


def check_box_text(s: Session, notes: list[str]) -> None:
    """Text written into the box: takes_prompt false while it holds text,
    `prompt {when_empty}` refused, Ctrl+C clears it."""
    text = "kit typed this"
    reply = s.client.call("write", pty=s.pty, text=text)
    if not reply.ok:
        raise CheckFailed(f"write: {reply.describe()}")
    if not s.client.wait_until(lambda: text in s.capture(), 5.0):
        raise CheckFailed("the written text is not on the screen (pty.capture)")
    if not s.wait_fact("takes_prompt", False, 5.0):
        raise CheckFailed("takes_prompt stayed true while the box holds text")
    facts = s.facts()
    if "entered" in facts and (facts["entered"] is None or facts["entered"].get("text") != text):
        raise CheckFailed(f"entered is {facts['entered']!r} while the box holds {text!r}")
    refused = s.prompt("another", when_empty=True)
    if refused.ok or refused.error != "refused":
        raise CheckFailed(f"prompt with when_empty while the box holds text: {refused.describe()}")
    s.client.send_input(s.pty, b"\x03")
    if not s.wait_fact("takes_prompt", True, 5.0):
        raise CheckFailed("takes_prompt did not come back true after the box was cleared")
    if text in s.capture().split("\n")[-6:]:
        raise CheckFailed("the box still shows the text after Ctrl+C")


def check_turn(s: Session, notes: list[str]) -> None:
    """A prompt lands; busy rises within 3 s; the id is bound within 10 s of
    the first record; busy falls within 10 s of the turn's end; one
    `finished`, `unread` on the row, and the transcript's model, effort
    and permission mode."""
    client = s.client
    since = len(client.events)
    start_out = client.output_len(s.pty)
    t0 = time.monotonic()
    reply = s.prompt("turn one")
    if not reply.ok:
        raise CheckFailed(f"prompt: {reply.describe()}")
    if s.wait_marker("fake-claude got: turn one", 5.0, start_out) < 0:
        raise CheckFailed("the fake never saw the prompt's text")
    if not s.wait_fact("busy", True, max(t0 + 3.0 - time.monotonic(), 0.1)):
        raise CheckFailed(f"busy was not true within 3 s of the prompt: {s.busy_events(since)}")
    rose_at = time.monotonic()
    if not client.wait_until(lambda: isinstance(s.facts().get("session"), str), 12.0):
        raise CheckFailed("the session was not bound to an id within 10 s of the first record")
    s.id = s.facts()["session"]
    s.run.known_session = s.id
    facts = s.facts()
    if not isinstance(facts.get("transcript_path"), str) or not facts["transcript_path"].endswith(
        s.id + ".jsonl"
    ):
        raise CheckFailed(f"transcript_path {facts.get('transcript_path')!r} is not the id's file")
    if not any(e.get("reset") is True for e in client.session_events(s.pty, since)):
        raise CheckFailed("no `reset: true` arrived with the first bind")
    if not client.wait_until(lambda: "path" in s.item(), 5.0):
        raise CheckFailed(f"no `item` with a path for the bound session {s.id}")
    if not client.wait_until(lambda: s.item().get("running") is True, 5.0):
        raise CheckFailed("item.running is not true while the fake runs the session")
    done = s.wait_marker("fake-claude turn 1 done", 10.0, start_out)
    if done < 0:
        raise CheckFailed("the fake's turn did not end within 10 s")
    turn_end = time.monotonic()
    if not s.wait_fact("busy", False, 10.0):
        raise CheckFailed("busy did not fall within 10 s of the turn's end")
    fell_at = time.monotonic()
    if not client.wait_until(lambda: s.finished_count(since) >= 1, 10.0):
        raise CheckFailed("no `finished` within 10 s of busy falling")
    finishes = [e for e in client.session_events(s.pty, since) if e.get("finished") is True]
    falls = [when for when, value in s.busy_events(since) if value is False]
    if falls and finishes and finishes[0].when < falls[0]:
        raise CheckFailed("`finished` arrived before busy fell: " + timeline(s, since, t0))
    if not client.wait_until(lambda: s.item().get("unread") is True, 5.0):
        raise CheckFailed("item.unread was not set on the counted finish")
    if not client.wait_until(lambda: s.facts().get("model") == FAKE_MODEL, 10.0):
        raise CheckFailed(f"model is {s.facts().get('model')!r}; the transcript says {FAKE_MODEL!r}")
    if s.facts().get("effort") != FAKE_EFFORT:
        raise CheckFailed(f"effort is {s.facts().get('effort')!r}; the transcript says {FAKE_EFFORT!r}")
    if s.facts().get("permission_mode") != FAKE_PERMISSION:
        raise CheckFailed(
            f"permission_mode is {s.facts().get('permission_mode')!r}; "
            f"the transcript says {FAKE_PERMISSION!r}"
        )
    if s.item().get("busy") is True:
        raise CheckFailed("item.busy is still true after the session's busy fell")
    notes.append(
        f"busy rose {rose_at - t0:.1f} s after the prompt, fell {fell_at - turn_end:.1f} s after the "
        f"turn's end, bound as {s.id}"
    )
    # The idle repaints that follow a turn count no second finish.
    client.pump(12.0)
    count = s.finished_count(since)
    if count != 1:
        raise CheckFailed(
            f"{count} `finished` for one turn (the fake repainted its screen three times after it)"
        )


def check_flags(s: Session, notes: list[str]) -> None:
    client = s.client
    reply = client.call("store.flags", session=s.id, unread=False)
    if not reply.ok:
        raise CheckFailed(f"store.flags {{unread: false}}: {reply.describe()}")
    if not client.wait_until(lambda: s.item().get("unread") is False, 5.0):
        raise CheckFailed("item.unread did not go false after the client cleared it")
    reply = client.call("store.flags", session=s.id, status="open")
    if not reply.ok:
        raise CheckFailed(f"store.flags {{status: open}}: {reply.describe()}")
    if not client.wait_until(lambda: s.item().get("status") == "open", 5.0):
        raise CheckFailed("item.status was not echoed back as 'open'")
    for name in ("busy", "backgrounding", "can_background"):
        reply = client.call("store.flags", **{"session": s.id, name: True})
        if reply.ok or reply.error != "refused":
            raise CheckFailed(
                f"store.flags {{{name}: true}} from a client: {reply.describe()}, expected `refused`"
            )


def check_quiet_road(s: Session, notes: list[str]) -> None:
    """A turn with no progress report and a quiet of 0.9 s inside it: no
    fall of busy and no one-shot from the quiet; one finish at the end."""
    client = s.client
    since = len(client.events)
    start_out = client.output_len(s.pty)
    reply = s.prompt("pause")
    if not reply.ok:
        raise CheckFailed(f"prompt: {reply.describe()}")
    if not s.wait_fact("busy", True, 3.0):
        raise CheckFailed("busy did not rise within 3 s")
    rise_index = len(client.events)
    if s.wait_marker("fake-claude turn", 15.0, start_out) < 0:
        raise CheckFailed("the turn did not end within 15 s")
    falls = [when for when, value in s.busy_events(rise_index) if value is False]
    if falls:
        raise CheckFailed("busy fell during the turn, across a quiet of 0.9 s")
    if s.finished_count(rise_index):
        raise CheckFailed("a `finished` arrived during the turn")
    unread = [
        e
        for e in client.events[rise_index:]
        if e.type == "item" and e.get("session") == s.id and e.get("unread") is True
    ]
    if unread:
        raise CheckFailed("item.unread went true during the turn")
    if not s.wait_fact("busy", False, 10.0):
        raise CheckFailed("busy did not fall within 10 s of the turn's end")
    if not client.wait_until(lambda: s.finished_count(since) >= 1, 10.0):
        raise CheckFailed("no `finished` after the turn")
    client.pump(3.0)
    if s.finished_count(since) != 1:
        raise CheckFailed(f"{s.finished_count(since)} `finished` for the turn")
    client.call("store.flags", session=s.id, unread=False)


def check_turn_end_road(s: Session, notes: list[str]) -> None:
    """The agent's clear ends busy within 10 s while its output never stops;
    one `finished`; a rise after the fall is no failure; nothing more when
    the output stops."""
    client = s.client
    since = len(client.events)
    start_out = client.output_len(s.pty)
    reply = s.prompt("stream")
    if not reply.ok:
        raise CheckFailed(f"prompt: {reply.describe()}")
    if not s.wait_fact("busy", True, 3.0):
        raise CheckFailed("busy did not rise within 3 s")
    clear_at = [0.0]

    def cleared() -> bool:
        match = PROGRESS_CLEAR.search(s.output_since(start_out))
        if match is not None:
            clear_at[0] = time.monotonic()
        return match is not None

    if not client.wait_until(cleared, 10.0):
        raise CheckFailed("the fake's progress clear never reached the client's output")
    t_clear = clear_at[0]
    fall_index = len(client.events)
    if not s.wait_fact("busy", False, 10.0):
        raise CheckFailed("busy did not fall within 10 s of the progress clear, with output flowing")
    fell_at = time.monotonic()
    out_at_fall = client.output_len(s.pty)
    if not client.wait_until(lambda: s.finished_count(fall_index) >= 1, 5.0):
        raise CheckFailed("no `finished` within 5 s of the fall")
    client.pump(2.0)
    if client.output_len(s.pty) <= out_at_fall:
        raise CheckFailed("the fake's output stopped: the scenario did not run as meant")
    rises = [when for when, value in s.busy_events(fall_index) if value is True]
    notes.append(
        f"busy fell {fell_at - t_clear:.1f} s after the clear"
        + (
            f"; rose again {rises[0] - fell_at:.2f} s later (allowed on this road)"
            if rises
            else "; no rise followed"
        )
    )
    if s.wait_marker("fake-claude stream over", 20.0, start_out) < 0:
        raise CheckFailed("the fake's output did not end")
    client.pump(12.0)
    count = s.finished_count(since)
    if count != 1:
        raise CheckFailed(f"{count} `finished` for one turn whose output went on after its clear")
    client.call("store.flags", session=s.id, unread=False)


def check_switch(s: Session, notes: list[str]) -> None:
    client = s.client
    if s.run.wants_family("composer"):
        reply = client.call("switch", pty=s.pty, effort="low")
        how = "switch"
    else:
        reply = s.prompt("/effort low")
        how = "prompt '/effort low'"
    if not reply.ok:
        raise CheckFailed(f"{how}: {reply.describe()}")
    if not s.wait_fact("effort", "low", 10.0):
        raise CheckFailed(f"effort is {s.facts().get('effort')!r} 10 s after the switch")
    reply = s.prompt("/model claude-fake-6")
    if not reply.ok:
        raise CheckFailed(f"prompt '/model': {reply.describe()}")
    if not s.wait_fact("model", "claude-fake-6", 10.0):
        raise CheckFailed(f"model is {s.facts().get('model')!r} 10 s after /model")
    notes.append(f"through {how}")
    client.pump(1.0)
    client.call("store.flags", session=s.id, unread=False)


def check_spawn_refusals(s: Session, notes: list[str]) -> None:
    run, client = s.run, s.client
    p = run.p
    reply = client.call(
        "spawn", kind="agent", cwd=run.cwd, session=s.id, cols=COLS, rows=ROWS, provider="claude"
    )
    if reply.ok:
        client.call("close", pty=reply["pty"], mode="kill")
        raise CheckFailed("a second spawn of a running session was accepted")
    if reply.error != "refused" or reply.msgid != p.ALREADY_RUNNING_MSGID or reply.args.get("pty") != s.pty:
        raise CheckFailed(f"a second spawn of a running session: {reply.describe()} args {reply.args}")
    if not run.claims("sandbox"):
        reply = client.call("spawn", kind="agent", cwd=run.cwd, sandbox=True, cols=COLS, rows=ROWS)
        if reply.ok:
            client.call("close", pty=reply["pty"], mode="kill")
            raise CheckFailed("spawn {sandbox: true} was accepted by a server without `sandbox`")
        if reply.error != "refused" or not reply.msgid:
            raise CheckFailed(f"spawn {{sandbox: true}} without `sandbox`: {reply.describe()}")
    if not run.claims("shells"):
        reply = client.call("spawn", kind="shell", cwd=run.cwd, cols=COLS, rows=ROWS)
        if reply.ok:
            client.call("close", pty=reply["pty"], mode="kill")
            raise CheckFailed("spawn {kind: shell} was accepted by a server without `shells`")
        if reply.error != "refused" or not reply.msgid:
            raise CheckFailed(f"spawn {{kind: shell}} without `shells`: {reply.describe()}")


def check_state(s: Session, notes: list[str]) -> None:
    client = s.client
    reply = client.call("state.get", key="settings")
    if not reply.ok or not isinstance(reply.get("value"), dict):
        raise CheckFailed(f"state.get settings: {reply.describe()}")
    reply = client.call("state.get", key="kit_no_such_key")
    if reply.ok or reply.error != "gone":
        raise CheckFailed(f"state.get of an unknown key: {reply.describe()}, expected `gone`")
    reply = client.call("state.get", key="service_id")
    if not reply.ok or reply.get("value") != s.run.hello["service_id"]:
        raise CheckFailed(
            f"state.get service_id: {reply.describe()}, the hello said {s.run.hello['service_id']!r}"
        )
    reply = client.call("state.set", key="settings", entry="welcome_seen", value=True)
    if not reply.ok and reply.error != "refused":
        raise CheckFailed(f"state.set settings.welcome_seen: {reply.describe()}")
    notes.append("the write was " + ("accepted" if reply.ok else "refused (allowed)"))
    reply = client.call("state.set", key="service_id", value="kit")
    if reply.ok:
        raise CheckFailed("state.set of service_id, a read-only key, was accepted")
    reply = client.call("resolver.arm", pty=s.pty)
    if not reply.ok:
        raise CheckFailed(f"resolver.arm: {reply.describe()}")


def check_close_exit(s: Session, notes: list[str]) -> None:
    client = s.client
    since = len(client.events)
    reply = client.call("close", pty=s.pty, mode="exit")
    if not reply.ok:
        raise CheckFailed(f"close {{mode: exit}}: {reply.describe()}")
    exited = client.wait_for(lambda e: e.type == "pty-exited" and e.get("pty") == s.pty, 20.0, since)
    if exited is None:
        raise CheckFailed("no `pty-exited` within 20 s of close {mode: exit}")
    if not client.wait_until(lambda: s.item().get("running") is False, 5.0):
        raise CheckFailed("item.running stayed true after the pty exited")
    client.pump(3.0)
    if s.finished_count(since):
        raise CheckFailed("a clean exit after an idle turn produced a `finished`")


def check_die(run: Run, notes: list[str]) -> None:
    """The fake leaves mid-turn with its progress report standing: busy is
    down within 10 s of its leaving, and takes_prompt is false once the
    shell, if any, has the pty."""
    client = run.client("conformance-die")
    client.subscribe()
    client.send_event("theme", term=TERM)
    s = spawn_session(run, client, notes)
    if not client.wait_until(lambda: s.facts().get("takes_prompt") is True, 15.0):
        raise CheckFailed("the fake's box never came up")
    since = len(client.events)
    reply = s.prompt("die")
    if not reply.ok:
        raise CheckFailed(f"prompt: {reply.describe()}")
    if not s.wait_fact("busy", True, 3.0):
        raise CheckFailed("busy did not rise")
    left = client.wait_for(
        lambda e: (
            (e.type == "pty-exited" and e.get("pty") == s.pty)
            or (e.type == "session" and e.get("pty") == s.pty and e.get("running_command") is False)
            or (e.type == "session" and e.get("pty") == s.pty and e.get("agent_running") is False)
        ),
        15.0,
        since,
    )
    if left is None:
        raise CheckFailed("the server never reported the fake gone (running_command false or pty-exited)")
    left_at = left.when
    if not s.wait_fact("busy", False, 10.0):
        raise CheckFailed("busy did not fall within 10 s of the agent leaving with its report standing")
    fell = [when for when, value in s.busy_events(since) if value is False][0]
    notes.append(f"busy fell {fell - left_at:.1f} s after the agent left")
    pty_gone = any(e.type == "pty-exited" and e.get("pty") == s.pty for e in client.events[since:])
    facts = s.facts()
    if not pty_gone and ("agent_running" in facts or "running_command" in facts):
        client.pump(2.0)
        if s.facts().get("takes_prompt") is not False:
            raise CheckFailed("takes_prompt is not false after the agent left and the shell has the pty")
        if facts.get("agent_running") is True:
            raise CheckFailed("agent_running stayed true after the agent left")
    if s.id is None:
        s.id = s.facts().get("session")
    if s.id and not client.wait_until(lambda: s.item().get("running") is False, 10.0):
        raise CheckFailed("item.running stayed true after the agent left")
    if not pty_gone:
        client.call("close", pty=s.pty, mode="kill")
        if client.wait_for(lambda e: e.type == "pty-exited" and e.get("pty") == s.pty, 15.0, since) is None:
            raise CheckFailed("no `pty-exited` after close {mode: kill}")
    client.close()


def check_replay(run: Run, notes: list[str]) -> None:
    """The recorded Claude Code streams written into a fake session: the
    server's `pty.capture` and `takes_prompt` against what VTE showed."""
    if not run.options.replay:
        raise CheckSkipped("--replay not given")
    streams_dir = run.need_file("streams")
    client = run.client("conformance-replay")
    client.subscribe()
    client.send_event("theme", term=TERM)
    s = spawn_session(run, client, notes)
    if not client.wait_until(lambda: s.facts().get("takes_prompt") is True, 15.0):
        raise CheckFailed("the fake's box never came up")
    failures = []
    count = 0
    for name in ("classic", "fullscreen"):
        bin_path = os.path.join(streams_dir, name + ".bin")
        if not os.path.exists(bin_path):
            continue
        with open(os.path.join(streams_dir, name + ".marks.json"), encoding="utf-8") as handle:
            marks = json.load(handle)
        with open(os.path.join(streams_dir, name + ".golden.json"), encoding="utf-8") as handle:
            goldens = json.load(handle)
        for mark in marks:
            golden = goldens.get(f"{name}.{mark['name']}")
            if golden is None:
                continue
            count += 1
            start_out = client.output_len(s.pty)
            reply = s.prompt(f"replay {bin_path} {mark['offset']}")
            if not reply.ok:
                failures.append(f"{name}.{mark['name']}: prompt {reply.describe()}")
                continue
            if client.wait_output(s.pty, f"\x1b]777;REPLAYED {mark['offset']}".encode(), 15.0, start_out) < 0:
                failures.append(f"{name}.{mark['name']}: the fake never reported the replay")
                continue
            client.pump(1.5)
            want = normalise_capture(golden["capture"])
            got = normalise_capture(s.capture())
            if not (got == want or got.endswith("\n" + want)):
                failures.append(
                    f"{name}.{mark['name']}: capture differs\n      server: {got[-160:]!r}\n"
                    f"      VTE:    {want[-160:]!r}"
                )
            want_takes = golden["takes_prompt"]
            if not client.wait_until(lambda want=want_takes: s.facts().get("takes_prompt") == want, 3.0):
                failures.append(
                    f"{name}.{mark['name']}: takes_prompt {s.facts().get('takes_prompt')}, "
                    f"VTE's reading {golden['takes_prompt']}"
                )
            client.send_input(s.pty, b"\r")
            client.pump(0.5)
    client.call("close", pty=s.pty, mode="kill")
    client.close()
    if failures:
        raise CheckFailed("; ".join(failures[:8]))
    notes.append(f"{count} marks")


def run_sessions(run: Run) -> None:
    r = run.reporter
    r.group("sessions")
    if not os.path.isfile(run.options.fake_claude):
        r.skip("a fake claude session", f"no fake claude at {run.options.fake_claude}")
        return
    client = run.client("conformance-sessions")
    client.subscribe()
    client.send_event("theme", term=TERM)
    s = r.run("spawn with the window's fields, attach, the snapshot", spawn_session, run, client)
    if s is None:
        return
    try:
        r.run("takes_prompt within 5 s of the spawn", check_takes_prompt, s)
        r.run("a second attach holds the series' word; entered is null", check_second_attach, s)
        r.run("text in the box: takes_prompt false, prompt refused, Ctrl+C clears", check_box_text, s)
        if (
            r.run("a turn: busy, the bind, one finished, unread, the transcript's facts", check_turn, s)
            is None
        ):
            return
        r.run("store.flags: the client's fields taken, the server's refused", check_flags, s)
        r.run("the quiet road: a quiet under a second ends nothing", check_quiet_road, s)
        r.run("the turn-end road: the clear ends busy while output flows", check_turn_end_road, s)
        r.run("a switch is reflected within a turn", check_switch, s)
        r.run("spawn refuses a running session and what it lacks", check_spawn_refusals, s)
        r.run("state.get, state.set and resolver.arm", check_state, s)
        r.run("close {exit} ends the pty; a clean exit flags nothing", check_close_exit, s)
    finally:
        pass
    r.run("the agent leaving mid-turn ends busy within 10 s", check_die, run)
    r.run("the recorded Claude Code streams (--replay)", check_replay, run)
