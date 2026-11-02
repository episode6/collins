# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""Level 1, the terminal: docs/api/terminal.md held to a live pty.

A probe runs inside a pty of the server's (a `shells` pty when the server
claims the family, else the fake claude's ``probe`` prompt) and writes each
query to its terminal, reporting what came back, so the kit sees the
server's answer as the child saw it. Then: the spawn environment, the grid,
every query of the page's tables (answered as VTE would, stripped, or
passed through to the attached client), the tokenizer's cuts, the attach
redraw's shape and `modes`, the active client and resize, `paint`, input
frames without an attach, and the screen model of record: every published
scenario painted into the pty and the server's `pty.capture` and cursor
report held to what VTE 0.84 showed for the same bytes (data/screens).
"""

from __future__ import annotations

import base64
import json
import os
import re
import time

from .. import client as apiclient
from .. import modes as modetrack
from .. import notation
from ..probe import Probe
from ..reporter import CheckFailed, CheckSkipped
from ..runner import COLS, ROWS, TERM, Run

CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")
SPAWN_WAIT_S = 20.0


def normalise_capture(text: str) -> str:
    """A capture as two readings are compared (terminal.md, the screen model):
    never-written cells as spaces, each row's trailing blanks dropped, the
    trailing empty rows dropped."""
    rows = [row.replace("\x00", " ").rstrip(" \t") for row in text.split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


def load_json(run: Run, *parts: str) -> dict:
    with open(run.need_file(*parts), encoding="utf-8") as handle:
        return json.load(handle)


class Terminal:
    """The probe pty and the clients around it, shared by the checks."""

    def __init__(self, run: Run) -> None:
        self.run = run
        self.client = run.client("conformance-term")
        self.client.subscribe()
        self.client.send_event("theme", term=TERM)
        self.pty: int | None = None
        self.kind = ""
        self.probe: Probe | None = None
        self.decrqm = load_json(run, "decrqm-modes.json")
        self.palette = load_json(run, "vte-palette.json")["colours"]
        self.queries = load_json(run, "queries.json")

    def start(self, notes: list[str]) -> None:
        run = self.run
        fake = run.options.fake_claude
        if run.wants_family("shells"):
            reply = self.client.call("spawn", kind="shell", cwd=run.cwd, cols=COLS, rows=ROWS)
            if not reply.ok:
                raise CheckFailed(f"spawn {{kind: shell}}: {reply.describe()}")
            self.kind = "shell"
        else:
            reply = self.client.call(
                "spawn", kind="agent", cwd=run.cwd, cols=COLS, rows=ROWS, provider="claude"
            )
            if not reply.ok:
                raise CheckFailed(f"spawn {{kind: agent}}: {reply.describe()}")
            self.kind = "agent"
        self.pty = reply["pty"]
        if (reply["cols"], reply["rows"]) != (COLS, ROWS):
            raise CheckFailed(f"the spawn reply's grid is {reply['cols']}x{reply['rows']}, not {COLS}x{ROWS}")
        attach = self.client.attach(self.pty, COLS, ROWS)
        if not attach.ok:
            raise CheckFailed(f"attach: {attach.describe()}")
        if not attach["active"]:
            raise CheckFailed("the first client to attach is not the active one")
        self.probe = Probe(self.client, self.pty)
        if self.kind == "shell":
            # The shell's prompt, then the probe typed into it.
            if not self.client.wait_until(lambda: self.client.output_len(self.pty) > 0, SPAWN_WAIT_S):
                raise CheckFailed("the shell wrote nothing within 20 s")
            time.sleep(0.3)
            command = f"python3 {shell_quote(fake)} --probe\r"
            self.client.send_input(self.pty, command.encode())
        else:
            if not self.client.wait_until(
                lambda: self.client.session_facts(self.pty).get("takes_prompt"), SPAWN_WAIT_S
            ):
                facts = self.client.session_facts(self.pty)
                raise CheckFailed(f"the fake's box never took a prompt within 20 s: {facts}")
            reply = self.client.call("prompt", pty=self.pty, text="probe")
            if not reply.ok:
                raise CheckFailed(f"prompt 'probe': {reply.describe()}")
        self.probe.ready()
        notes.append(f"probe on a {self.kind} pty {self.pty}")

    def stop(self) -> None:
        if self.pty is None:
            return
        try:
            if self.probe is not None:
                self.probe.leave()
            self.client.call("close", pty=self.pty, mode="kill")
        except Exception:  # noqa: BLE001
            pass


def shell_quote(path: str) -> str:
    return "'" + path.replace("'", "'\\''") + "'"


# -- the checks ------------------------------------------------------------------


def check_environment(t: Terminal, notes: list[str]) -> None:
    env = t.probe.environment()
    expected = {"TERM": "xterm-256color", "COLORTERM": "truecolor", "VTE_VERSION": str(TERM["vte"])}
    for name, value in expected.items():
        if env.get(name) != value:
            raise CheckFailed(
                f"{name}={env.get(name)!r}, expected {value!r} (the kit's theme named vte {TERM['vte']})"
            )
    if t.kind == "agent":
        settings = t.run.settings
        if settings.get("progress_termprop", True):
            for name, value in (("ConEmuANSI", "ON"), ("TERM_PROGRAM", "kitty")):
                if env.get(name) != value:
                    raise CheckFailed(f"an agent pty has {name}={env.get(name)!r}, expected {value!r}")
        else:
            notes.append("progress_termprop is off on the server: the spoofs are not expected")
    else:
        for name in ("ConEmuANSI", "TERM_PROGRAM"):
            if env.get(name) in ("ON", "kitty"):
                raise CheckFailed(f"a shell pty has {name}={env.get(name)!r}: the spoofs are the agent's")
    notes.append(" ".join(f"{k}={v}" for k, v in env.items()))


def check_grid(t: Terminal, notes: list[str]) -> None:
    size = t.probe.size()
    if size != (COLS, ROWS):
        raise CheckFailed(f"the pty is {size[0]}x{size[1]}, the attach asked for {COLS}x{ROWS}")
    answer = t.probe.query(b"\x1b[18t")
    if answer != f"\x1b[8;{ROWS};{COLS}t".encode():
        raise CheckFailed(f"CSI 18 t answered {notation.describe(answer)}")


def _expected_values(t: Terminal, name: str) -> dict:
    """The placeholder values the kit knows for a reply."""
    fg = TERM["fg"].lstrip("#")
    bg = TERM["bg"].lstrip("#")
    values = {
        "vte": str(TERM["vte"]),
        "scheme": "1" if TERM["scheme"] == "dark" else "2",
        "rows": str(ROWS),
        "cols": str(COLS),
    }
    if name in ("the foreground", "the cursor colour"):
        values.update(rrrr=fg[0:2] * 2, gggg=fg[2:4] * 2, bbbb=fg[4:6] * 2)
    elif name == "the background":
        values.update(rrrr=bg[0:2] * 2, gggg=bg[2:4] * 2, bbbb=bg[4:6] * 2)
    return values


def check_answered(t: Terminal, notes: list[str]) -> None:
    """Every query of the answered table: the probe writes it, the server's
    reply is VTE 0.84's bytes, and the query never reaches the client."""
    probe = t.probe
    client = t.client
    probe.query(b"\x1bc")  # a fresh terminal: the modes at their defaults, the pen plain
    failures = []
    count = 0
    for row in t.queries["answered"]:
        name = row["name"]
        query_tokens = notation.parse(row["query"])
        reply_tokens = notation.parse(row["reply"])
        cases = _cases(t, name, query_tokens, reply_tokens)
        for query_bytes, values, prelude in cases:
            count += 1
            start = client.output_len(t.pty)
            answer = probe.query(prelude + query_bytes)
            regex = notation.pattern(reply_tokens, values)
            match = regex.fullmatch(answer)
            if match is None:
                failures.append(
                    f"{name} ({notation.describe(query_bytes)}): answered {notation.describe(answer)}, "
                    "expected "
                    + notation.describe(
                        notation.render(reply_tokens, {**{k: "?" for k in notation.CLASSES}, **values})
                    )
                )
                continue
            if query_bytes in bytes(client.output[t.pty])[start:]:
                failures.append(f"{name}: the query reached the client")
    # The replies come in query order, each ahead of any typing queued.
    answer = probe.query(b"\x1b[5n\x1b[c\x1b[5n")
    if answer != b"\x1b[0n\x1b[?61;1;21;22;28c\x1b[0n":
        failures.append(f"three queries in a row answered out of order or short: {notation.describe(answer)}")
    # A query ended by BEL is answered ended by BEL.
    answer = probe.query(b"\x1b]11;?\x07")
    if not answer.endswith(b"\x07") or not answer.startswith(b"\x1b]11;rgb:"):
        failures.append(f"OSC 11 ended by BEL answered {notation.describe(answer)}")
    # Several palette pairs in one query: one reply each, in order.
    answer = probe.query(b"\x1b]4;1;?;2;?\x1b\\")
    want = f"\x1b]4;1;rgb:{t.palette[1]}\x1b\\\x1b]4;2;rgb:{t.palette[2]}\x1b\\".encode()
    if answer != want:
        failures.append(f"OSC 4 with two pairs answered {notation.describe(answer)}")
    if failures:
        raise CheckFailed(
            "; ".join(failures[:8]) + (f" ... ({len(failures)} in all)" if len(failures) > 8 else "")
        )
    notes.append(f"{count} queries")


SGR_CASES = (
    (b"0", "0"),
    (b"1;31", "0;1;31"),
    (b"3;4", "0;3;4"),
    (b"4:3", "0;4:3"),
    (b"7;9", "0;7;9"),
    (b"38;5;196", "0;38:5:196"),
    (b"38;2;10;20;30", "0;38:2::10:20:30"),
    (b"48;5;17", "0;48:5:17"),
    (b"91;104", "0;91;104"),
    (b"4;58;5;3", "0;4;58:5:3"),
)


def _cases(t: Terminal, name: str, query_tokens, reply_tokens) -> list[tuple[bytes, dict, bytes]]:
    """(query bytes, expected placeholder values, bytes to write first) per row."""
    values = _expected_values(t, name)
    holders = set(notation.placeholders(query_tokens)) | set(notation.placeholders(reply_tokens))
    if "n" in holders:
        cases = []
        private = "?" in [tok.text for tok in query_tokens]
        table = t.decrqm["private" if private else "ansi"]
        for mode, state in sorted(table.items(), key=lambda kv: int(kv[0])):
            cases.append(
                (notation.render(query_tokens, {"n": mode}), {**values, "n": mode, "s": str(state)}, b"")
            )
        unknown = "12345" if private else "999"
        cases.append((notation.render(query_tokens, {"n": unknown}), {**values, "n": unknown, "s": "0"}, b""))
        return cases
    if "i" in holders:
        cases = []
        for index in (0, 1, 7, 8, 15, 16, 100, 231, 232, 255):
            r, g, b = t.palette[index].split("/")
            cases.append(
                (
                    notation.render(query_tokens, {"i": str(index)}),
                    {**values, "i": str(index), "rrrr": r, "gggg": g, "bbbb": b},
                    b"",
                )
            )
        return cases
    if "row" in holders:
        # The cursor placed by the probe's own write, then asked for.
        return [
            (notation.render(query_tokens, {}), {**values, "row": "5", "col": "7"}, b"\x1b[5;7H"),
            (notation.render(query_tokens, {}), {**values, "row": "1", "col": "1"}, b"\x1b[H"),
            # A pending wrap: the last column, not the one beyond it.
            (
                notation.render(query_tokens, {}),
                {**values, "row": "2", "col": str(COLS)},
                b"\x1b[2;1H" + b"W" * COLS,
            ),
            # Origin mode: measured from the region's top row.
            (
                notation.render(query_tokens, {}),
                {**values, "row": "1", "col": "1"},
                b"\x1b[5;10r\x1b[?6h\x1b[1;1H",
            ),
            (notation.render(query_tokens, {}), {**values, "row": "1", "col": "1"}, b"\x1b[?6l\x1b[r\x1b[H"),
        ]
    if "sgr" in holders:
        return [
            (notation.render(query_tokens, {}), {**values, "sgr": expected}, b"\x1b[0m\x1b[" + sgr + b"m")
            for sgr, expected in SGR_CASES
        ] + [(notation.render(query_tokens, {}), {**values, "sgr": "0"}, b"\x1b[0m")]
    return [(notation.render(query_tokens, {}), values, b"")]


def check_silent(t: Terminal, notes: list[str]) -> None:
    client = t.client
    failures = []
    for row in t.queries["silent"]:
        query = notation.render(notation.parse(row["query"]), {})
        start = client.output_len(t.pty)
        answer = t.probe.query(query)
        if answer:
            failures.append(f"{row['name']}: answered {notation.describe(answer)}")
        client.pump(0.2)
        if query in bytes(client.output[t.pty])[start:]:
            failures.append(f"{row['name']}: reached the client")
    if failures:
        raise CheckFailed("; ".join(failures))
    notes.append(f"{len(t.queries['silent'])} queries")


def check_passed(t: Terminal, notes: list[str]) -> None:
    client = t.client
    failures = []
    for row in t.queries["passed"]:
        query = notation.render(notation.parse(row["query"]), {})
        start = client.output_len(t.pty)
        answer = t.probe.query(query)
        if answer:
            failures.append(f"{row['name']}: the server answered {notation.describe(answer)}")
        if client.wait_output(t.pty, query, 2.0, start) < 0:
            failures.append(f"{row['name']}: did not reach the client unchanged")
    if failures:
        raise CheckFailed("; ".join(failures))
    # A colour the program set, then reset: the query is answered again.
    answer = t.probe.query(b"\x1b]110\x1b\\\x1b]10;?\x1b\\")
    if not answer.startswith(b"\x1b]10;rgb:"):
        failures.append(f"OSC 10 after OSC 110 answered {notation.describe(answer)}")
    if failures:
        raise CheckFailed("; ".join(failures))
    notes.append(f"{len(t.queries['passed'])} sequences")


def check_tokenizer(t: Terminal, notes: list[str]) -> None:
    probe = t.probe
    failures = []
    answer = probe.query(b"\x1b[H\x1b]10;?\x1b[6n")
    if not CPR.fullmatch(answer):
        failures.append(f"an ESC inside an OSC: expected the CPR alone, got {notation.describe(answer)}")
    answer = probe.query(b"\x1b[$6n")
    if answer:
        failures.append(
            f"CSI $ 6 n (a parameter after an intermediate) was answered {notation.describe(answer)}"
        )
    # A query cut in two by two writes is completed by the next read.
    answer = probe.query_split(b"\x1b[6", b"n")
    if not CPR.fullmatch(answer):
        failures.append(f"a CPR cut in two by two writes answered {notation.describe(answer)}")
    answer = probe.query_split(b"\x1b]11;", b"?\x1b\\")
    if not answer.startswith(b"\x1b]11;rgb:"):
        failures.append(f"an OSC cut in two by two writes answered {notation.describe(answer)}")
    # CAN aborts the sequence it arrives in: no answer.
    answer = probe.query(b"\x1b[6\x18n")
    if answer:
        failures.append(f"CSI 6 CAN n answered {notation.describe(answer)}")
    # A C1 control as UTF-8 is text, not a sequence: U+009B 6 n is no query here.
    answer = probe.query("\u009b6n".encode("utf-8"))
    if answer:
        failures.append(f"U+009B 6 n was answered {notation.describe(answer)}")
    if failures:
        raise CheckFailed("; ".join(failures))


def check_attach_redraw(t: Terminal, notes: list[str]) -> None:
    run = t.run
    second = run.client("conformance-second")
    second.send_event("theme", term=TERM)
    reply = second.attach(t.pty, COLS, ROWS)
    if not reply.ok:
        raise CheckFailed(f"a second attach: {reply.describe()}")
    if (reply["cols"], reply["rows"]) != (COLS, ROWS):
        raise CheckFailed(f"the redraw's grid is {reply['cols']}x{reply['rows']}")
    if reply["active"]:
        raise CheckFailed("the second client to attach was made active")
    if reply.get("sized_for") != t.client.device:
        raise CheckFailed(
            f"sized_for is {reply.get('sized_for')!r}, not the active client's device {t.client.device!r}"
        )
    if not isinstance(reply.get("modes"), list):
        raise CheckFailed("the attach reply has no `modes` list")
    frames = second.wait_redraw(t.pty, 10.0)
    if frames is None:
        raise CheckFailed("no complete redraw (a frame flagged redraw end) within 10 s")
    if not frames[-1].flags & run.p.FLAG_REDRAW_END or any(not f.flags & run.p.FLAG_REDRAW for f in frames):
        raise CheckFailed("the redraw's frames are not all flagged redraw, the last redraw end")
    painted = b"".join(f.payload for f in frames)
    if not painted:
        raise CheckFailed("the redraw carried no bytes")
    # The redraw is not counted: no live frame's offset is moved by it.
    live = second.frames.get(t.pty, [])
    live_frames = [f for f in live if not f.flags & run.p.FLAG_REDRAW]
    if live_frames and live_frames[0].offset != 0:
        raise CheckFailed(
            f"the first live frame after the redraw starts at offset {live_frames[0].offset}, not 0"
        )
    notes.append(f"{len(frames)} redraw frames, {len(painted)} bytes, modes {reply['modes']}")
    second.close()


def check_pty_events_and_resize(t: Terminal, notes: list[str]) -> None:
    """The active client's resize moves the grid and every attached client
    hears; another client's resize is remembered, not applied; `focus`
    takes the role; a detach passes it back."""
    run = t.run
    first = t.client
    second = run.client("conformance-watcher")
    second.send_event("theme", term=TERM)
    reply = second.attach(t.pty, COLS, ROWS)
    if not reply.ok:
        raise CheckFailed(f"the watcher's attach: {reply.describe()}")
    second.wait_redraw(t.pty, 10.0)
    # The watcher's resize is not applied.
    second.send_event("resize", pty=t.pty, cols=90, rows=30)
    time.sleep(1.0)
    first.pump(0.2)
    if t.probe.size() != (COLS, ROWS):
        raise CheckFailed("a resize from a client that is not active changed the pty's grid")
    # The active client's resize is, and both hear of it.
    since_first, since_second = len(first.events), len(second.events)
    first.send_event("resize", pty=t.pty, cols=100, rows=30)

    def grid(e) -> bool:
        return e.type == "pty" and e.get("pty") == t.pty and (e.get("cols"), e.get("rows")) == (100, 30)

    if first.wait_for(grid, 5.0, since_first) is None:
        raise CheckFailed("the active client's resize produced no `pty` event with the new grid for it")
    if second.wait_for(grid, 5.0, since_second) is None:
        raise CheckFailed("the other attached client heard nothing of the resize")
    size = t.probe.size()
    if size != (100, 30):
        raise CheckFailed(f"after the active client's resize the pty is {size[0]}x{size[1]}")
    # The watcher takes the role with focus, and its remembered grid applies.
    since_first = len(first.events)
    second.send_event("focus", pty=t.pty, focused=True)
    lost = first.wait_for(
        lambda e: e.type == "pty" and e.get("pty") == t.pty and e.get("active") is False, 5.0, since_first
    )
    if lost is None:
        raise CheckFailed("the active client heard nothing when another client took the role with `focus`")
    if lost.get("sized_for") != second.device:
        raise CheckFailed(f"the `pty` event names sized_for {lost.get('sized_for')!r}, not {second.device!r}")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and t.probe.size() != (90, 30):
        time.sleep(0.2)
    if t.probe.size() != (90, 30):
        raise CheckFailed(
            f"the new active client's remembered grid 90x30 was not applied: the pty is {t.probe.size()}"
        )
    # On the active client's detach the role passes back, and every attached
    # client hears of a role change through a `pty` event.
    since_first = len(first.events)
    detached = second.call("detach", pty=t.pty)
    if not detached.ok:
        raise CheckFailed(f"the active client's detach: {detached.describe()}")
    back = first.wait_for(
        lambda e: e.type == "pty" and e.get("pty") == t.pty and e.get("active") is True, 5.0, since_first
    )
    try:
        if back is None:
            heard = [e.fields for e in first.events[since_first:] if e.type == "pty"]
            raise CheckFailed(
                "no `pty` event with active true reached the remaining client within 5 s of the active "
                f"client's detach (pty events heard: {heard})"
            )
        first.send_event("resize", pty=t.pty, cols=COLS, rows=ROWS)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and t.probe.size() != (COLS, ROWS):
            time.sleep(0.2)
        if t.probe.size() != (COLS, ROWS):
            raise CheckFailed(f"the grid did not come back to 120x40 for the active client: {t.probe.size()}")
    finally:
        # Whatever happened, the first client takes the role and the grid back
        # for the checks that follow.
        first.send_event("focus", pty=t.pty, focused=True)
        first.send_event("resize", pty=t.pty, cols=COLS, rows=ROWS)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and t.probe.size() != (COLS, ROWS):
            time.sleep(0.2)
        second.close()


def check_paint(t: Terminal, notes: list[str]) -> None:
    client = t.client
    text = "\r\npainted by the conformance kit\r\n"
    start = client.output_len(t.pty)
    reply = client.call("paint", pty=t.pty, text=text)
    if not reply.ok:
        raise CheckFailed(f"paint: {reply.describe()}")
    if client.wait_output(t.pty, text.encode(), 5.0, start) < 0:
        raise CheckFailed("the painted text did not reach the attached client as output")
    capture = client.call("pty.capture", pty=t.pty)
    if not capture.ok or "painted by the conformance kit" not in capture["text"]:
        raise CheckFailed("the painted text is not in `pty.capture`: the model did not take it")
    # A query painted is answered by nobody and goes out untouched.
    start = client.output_len(t.pty)
    reply = client.call("paint", pty=t.pty, text="\x1b[5n")
    if client.wait_output(t.pty, b"\x1b[5n", 5.0, start) < 0:
        raise CheckFailed("a painted query did not reach the client untouched")
    client.pump(0.5)


def check_input_without_attach(t: Terminal, notes: list[str]) -> None:
    """An input frame from a client that never attached is written to the pty."""
    run = t.run
    outsider = run.client("conformance-outsider")
    t.probe.seq += 1
    seq = str(t.probe.seq)
    outsider.send_input(t.pty, f"Q {seq} 1b5b356e\n".encode())
    report = t.probe.wait_report(f"R {seq} ", 5.0)
    if not report.endswith("1b5b306e"):
        raise CheckFailed(f"the probe's answer to the outsider's query: {report!r}")
    outsider.close()


def check_pty_info(t: Terminal, notes: list[str]) -> None:
    reply = t.client.call("pty.info", pty=t.pty)
    if not reply.ok:
        raise CheckFailed(f"pty.info: {reply.describe()}")
    if reply["kind"] != t.kind:
        raise CheckFailed(f"pty.info says kind {reply['kind']!r}, the spawn asked for {t.kind!r}")
    if t.kind == "shell" and reply["running_command"] is not True:
        raise CheckFailed("the probe runs in the shell's foreground, yet running_command is not true")
    if (reply.get("cols"), reply.get("rows")) not in ((COLS, ROWS), (None, None)):
        raise CheckFailed(f"pty.info's grid is {reply.get('cols')}x{reply.get('rows')}")
    gone = t.client.call("pty.info", pty=4000000000)
    if gone.ok or gone.error != "gone":
        raise CheckFailed(f"pty.info of a pty that does not exist: {gone.describe()}")
    if not gone.msgid.startswith(t.run.p.PTY_GONE_MSGID):
        raise CheckFailed(
            f"the `gone` refusal's msgid is {gone.msgid!r}, "
            f"not the matched sentence {t.run.p.PTY_GONE_MSGID!r}"
        )


def check_screens(t: Terminal, notes: list[str]) -> None:
    """Every published scenario painted into the pty: `pty.capture` and the
    cursor report against VTE 0.84's reading of the same bytes, and the
    attach reply's `modes`."""
    run = t.run
    screens_dir = run.need_file("screens")
    names = sorted(n for n in os.listdir(screens_dir) if n.endswith(".json"))
    if not names:
        raise CheckSkipped("no screens data (run scripts/check_termscreen_parity.py --write)")
    second = run.client("conformance-screens")
    second.send_event("theme", term=TERM)
    failures = []
    for name in names:
        with open(os.path.join(screens_dir, name), encoding="utf-8") as handle:
            scenario = json.load(handle)
        stream = base64.b64decode(scenario["stream"])
        label = scenario["scenario"]
        # A fresh terminal, its history blanked, then the scenario.
        t.probe.paint(b"\x1bc\x1b[3J" + stream)
        tracker = modetrack.ModeTracker(t.decrqm)
        tracker.feed(stream)
        cursor = t.probe.query(b"\x1b[6n")
        match = CPR.fullmatch(cursor)
        want_col, want_row = scenario["cursor"]
        expected_col = min(want_col, COLS - 1) + 1  # a pending wrap reports the last column
        expected_row = want_row + 1
        if tracker.private.get(6):
            expected_row = max(expected_row - (tracker.region_top - 1), 1)
        if match is None:
            failures.append(f"{label}: no CPR answer ({notation.describe(cursor)})")
        elif (int(match.group(1)), int(match.group(2))) != (expected_row, expected_col):
            failures.append(
                f"{label}: cursor {match.group(1).decode()};{match.group(2).decode()}, "
                f"VTE showed {expected_row};{expected_col}"
            )
        if not scenario.get("alternate"):
            capture = t.client.call("pty.capture", pty=t.pty)
            got = normalise_capture(capture.get("text", ""))
            want = normalise_capture(scenario["capture"])
            if not (got == want or got.endswith("\n" + want) or (not want and True)):
                failures.append(
                    f"{label}: capture differs\n      server: {got[-200:]!r}\n      VTE:    {want[-200:]!r}"
                )
        redraws_before = len(second.redraws.get(t.pty, []))
        reply = second.attach(t.pty, COLS, ROWS)
        if not reply.ok:
            failures.append(f"{label}: attach {reply.describe()}")
            continue
        if second.wait_redraw(t.pty, 10.0, redraws_before + 1) is None:
            failures.append(f"{label}: no redraw within 10 s of the attach")
        got_modes = set(reply.get("modes") or [])
        want_modes = tracker.expected()
        if got_modes != want_modes:
            failures.append(f"{label}: modes {sorted(got_modes)}, expected {sorted(want_modes)}")
        second.call("detach", pty=t.pty)
    t.probe.paint(b"\x1bc")
    second.close()
    if failures:
        raise CheckFailed(
            "; ".join(failures[:10]) + (f" ... ({len(failures)} in all)" if len(failures) > 10 else "")
        )
    notes.append(f"{len(names)} scenarios")


def run_terminal(run: Run) -> None:
    r = run.reporter
    r.group("terminal")
    if not os.path.isfile(run.options.fake_claude):
        r.skip("the probe", f"no fake claude at {run.options.fake_claude}")
        return
    t = Terminal(run)
    run.settings = _settings(t.client)
    if r.run("a pty with the probe in it", t.start) is None:
        t.stop()
        return
    try:
        r.run("the spawn environment", check_environment, t)
        r.run("the grid is the active client's", check_grid, t)
        r.run("queries answered in the child's place, as VTE answers them", check_answered, t)
        r.run("queries stripped without an answer", check_silent, t)
        r.run("sequences passed through unchanged", check_passed, t)
        r.run("the tokenizer's cuts", check_tokenizer, t)
        r.run("an attach's redraw", check_attach_redraw, t)
        r.run("the active client, resize and focus", check_pty_events_and_resize, t)
        r.run("paint", check_paint, t)
        r.run("an input frame is written without an attach", check_input_without_attach, t)
        r.run("pty.info", check_pty_info, t)
        r.run("the screen model of record: every scenario's capture, cursor and modes", check_screens, t)
    finally:
        t.stop()


def _settings(client: apiclient.Client) -> dict:
    settings = {}
    for event in client.events_of("state.set"):
        if event.get("key") == "settings":
            if "entry" in event.fields:
                settings[event.get("entry")] = event.get("value")
            elif isinstance(event.get("value"), dict):
                settings.update(event.get("value"))
    return settings
