#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only; one file, so a server
# can be pointed at it as its `claude`.
"""A stand-in for Claude Code, for testing a server of the service API.

The kit installs this file as `claude` where the server under test looks
for the CLI. Everything it draws and writes is taken from the appendix of
docs/api/sessions.md ("Claude Code as observed"), and from nothing else:
the input box (a rule of `─`, a row opening `❯` and a no-break space with
the cursor in the third column, a dim hint drawn in pieces, a rule), the
box's editing keys and paste folding, the spinner column, the OSC 9;4
progress reports under the two environment variables, the transcript
directory and its records, Ctrl+C twice to leave. Where the appendix is
silent the fake is plain, so that what the kit tests is the appendix.

The prompts the kit sends are read as the scenarios it wants: a plain
prompt is a three-second turn with a reply and the records a turn leaves;
``pause`` a turn with no progress report and a quiet of 0.9 s inside it;
``stream`` a turn whose progress clears while output goes on; ``die`` (or
``--die``) a turn the fake leaves mid-way, its progress report standing;
``long N`` an N-second turn; ``menu`` a menu that takes the keys until
Enter, marked as the appendix says a menu is (the mark and an ordinary
space); ``/model X`` and ``/effort X`` the CLI's local commands with their
two records; ``!cmd`` a shell command, its output as the
reply; ``probe`` the terminal probe (below); ``replay OFFSET FILE`` a
recorded stream written to the terminal up to a byte offset (the path may
hold spaces).

The probe (``claude --probe``, or the ``probe`` prompt) puts the terminal in
raw mode and takes commands, one per line, as the kit types them:
``Q <seq> <hex>`` writes the bytes and reports what the terminal answered
within half a second; ``W <seq> <hex> <hex>`` writes the first bytes, then
the second a moment later, and reports the answer (a query cut in two);
``B <hex>`` buffers bytes and ``P <seq>`` writes the buffer; ``E <seq>``
reports the environment; ``Z <seq>`` the terminal's size; ``X`` leaves. Every report is
an ``OSC 777 ; <text> ST`` string, which a server forwards to its clients
and no terminal draws, so a report never changes the screen the kit is
about to compare.

``die`` leaves on the kit's Ctrl+D once the turn is under way. ``/bg`` is
the handoff: the fake prints that it got it and leaves. A clean exit writes
nothing to the transcript; ``--save-on-exit`` makes it save its state on
the way out, as the CLI does.

Other invocations the CLI's users make and a server may too: ``--version``,
``agents --json`` (an empty list), ``-p PROMPT`` (one line, exit 0).
"""

from __future__ import annotations

import json
import os
import re
import select
import signal
import subprocess
import sys
import termios
import time
import tty
import uuid

ESC = "\x1b"
CSI = ESC + "["
OSC = ESC + "]"
ST = ESC + "\\"
BEL = "\x07"
RULE = "─"  # ─
MARK = "❯"  # ❯
NBSP = " "
BOX_MARGIN = 4  # the box is columns − 4 wide: the mark, the space, two of right margin
PASTE_CHARS = 800
PASTE_BREAKS = 3
SUBMIT_CHUNK = 16  # a chunk this short ending in CR submits (the fake's own boundary)
STARTUP_HINT = 'Try "fix typecheck errors"'
RESTING_HINT = "Ask about the last reply, or type a new prompt"
SPINNER = ("✢", "✶", "✻", "✽")  # ✢ ✶ ✻ ✽
DEFAULT_MODEL = "claude-fake-5"
DEFAULT_EFFORT = "high"
DEFAULT_PERMISSION = "default"
VERSION = "0.0.0 (fake-claude, the Collins conformance kit)"
TURN_S = 3.0
IDLE_REPAINTS_S = (5.0, 7.0, 9.0)  # after a turn's end, as the CLI repaints an idle screen
CTRL_C_PAIR_S = 1.5
REPORT_OSC = "777"

_write_fd = 1


def write(text: str | bytes) -> None:
    data = text.encode("utf-8") if isinstance(text, str) else text
    while data:
        try:
            sent = os.write(_write_fd, data)
        except BlockingIOError:
            time.sleep(0.001)
            continue
        data = data[sent:]


def report(text: str) -> None:
    """A probe report nobody draws: a private OSC string."""
    write(f"{OSC}{REPORT_OSC};{text}{ST}")


def encode_dir(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def transcript_dir(cwd: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", encode_dir(cwd))


# -- the transcript ------------------------------------------------------------


class Transcript:
    """One session's JSONL file, written as the appendix describes."""

    def __init__(self, cwd: str, session_id: str, path: str | None = None) -> None:
        self.cwd = cwd
        self.session_id = session_id
        self.directory = transcript_dir(cwd)
        self.path = path or os.path.join(self.directory, session_id + ".jsonl")
        self.last_uuid: str | None = None
        self.started = os.path.exists(self.path)

    def _append(self, record: dict) -> None:
        os.makedirs(self.directory, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def metadata(self, permission_mode: str) -> None:
        """The records that open a file and recur at every save: a session id
        and no timestamp or cwd."""
        self._append({"type": "last-prompt", "lastPrompt": "", "sessionId": self.session_id})
        self._append(
            {"type": "permission-mode", "permissionMode": permission_mode, "sessionId": self.session_id}
        )

    def _stamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"

    def _common(self) -> dict:
        record_uuid = str(uuid.uuid4())
        common = {
            "cwd": self.cwd,
            "timestamp": self._stamp(),
            "uuid": record_uuid,
            "parentUuid": self.last_uuid,
            "isSidechain": False,
            "gitBranch": "",
            "version": "0.0.0",
            "sessionId": self.session_id,
        }
        self.last_uuid = record_uuid
        return common

    def user(self, text: str, permission_mode: str | None = None, meta: bool = False) -> None:
        if not self.started:
            self.metadata(permission_mode or DEFAULT_PERMISSION)
            self.started = True
        record = {"type": "user", **self._common(), "message": {"role": "user", "content": text}}
        if permission_mode is not None:
            record["permissionMode"] = permission_mode
        if meta:
            record["isMeta"] = True
        self._append(record)

    def assistant(self, text: str, model: str, effort: str) -> None:
        # What a naive reading trips on, as the appendix lists it: a record
        # the CLI made up locally, with the placeholder model, and a
        # sub-agent's record on a sidechain with a model of its own.
        self._append(
            {
                "type": "assistant",
                **self._common(),
                "message": {
                    "role": "assistant",
                    "model": "<synthetic>",
                    "content": [{"type": "text", "text": "..."}],
                },
            }
        )
        sidechain = {**self._common(), "isSidechain": True}
        self._append(
            {
                "type": "assistant",
                **sidechain,
                "effort": "low",
                "message": {
                    "role": "assistant",
                    "model": "claude-fake-sidechain",
                    "content": [{"type": "text", "text": "aside"}],
                },
            }
        )
        record = {
            "type": "assistant",
            **self._common(),
            "effort": effort,
            "message": {"role": "assistant", "model": model, "content": [{"type": "text", "text": text}]},
        }
        self._append(record)

    def turn_duration(self, duration_ms: int) -> None:
        self._append(
            {"type": "system", "subtype": "turn_duration", "durationMs": duration_ms, **self._common()}
        )

    def local_command(self, command: str, args: str, output: str) -> None:
        text = (
            f"<command-name>{command}</command-name><command-message>{command.lstrip('/')}</command-message>"
            f"<command-args>{args}</command-args>"
        )
        self.user(text)
        self.user(f"<local-command-stdout>{output}</local-command-stdout>")

    def save_state(self, permission_mode: str) -> None:
        if self.started:
            self._append(
                {"type": "permission-mode", "permissionMode": permission_mode, "sessionId": self.session_id}
            )


# -- the box -----------------------------------------------------------------------


def wrap_box(text: str, width: int) -> list[tuple[int, int]]:
    """The rows the box text takes at *width* columns, each a (start, end)
    index range into *text*: a typed line break ends a row, a word is moved
    down whole, and a word longer than a row fills it before breaking."""
    rows: list[tuple[int, int]] = []
    start = 0
    length = len(text)
    while True:
        newline = text.find("\n", start)
        line_end = length if newline < 0 else newline
        pos = start
        while True:
            if line_end - pos <= width:
                rows.append((pos, line_end))
                break
            cut = text.rfind(" ", pos, pos + width + 1)
            if cut <= pos:
                cut = pos + width
            rows.append((pos, cut))
            pos = cut
            while pos < line_end and text[pos] == " ":
                pos += 1
        if newline < 0:
            break
        start = newline + 1
    return rows


def paste_breaks(text: str) -> int:
    return len(re.findall(r"\r\n|\r|\n", text))


def fold_paste(text: str, number: int) -> str | None:
    """The stand-in the box shows for a paste too long or too many lines, or
    None when the text goes in as it is."""
    breaks = paste_breaks(text)
    if len(text) <= PASTE_CHARS and breaks < PASTE_BREAKS:
        return None
    if breaks:
        return f"[Pasted text #{number} +{breaks} lines]"
    return f"[Pasted text #{number}]"


class Box:
    def __init__(self) -> None:
        self.text = ""
        self.cursor = 0
        self.pastes = 0

    def insert(self, text: str) -> None:
        self.text = self.text[: self.cursor] + text + self.text[self.cursor :]
        self.cursor += len(text)

    def backspace(self) -> None:
        if self.cursor == 0:
            return
        # A Backspace right after a paste stand-in removes the whole of it.
        head = self.text[: self.cursor]
        match = re.search(r"\[Pasted text #\d+(?: \+\d+ lines)?\]$", head)
        count = len(match.group(0)) if match else 1
        self.text = head[:-count] + self.text[self.cursor :]
        self.cursor -= count

    def paste(self, text: str) -> None:
        stand_in = fold_paste(text, self.pastes + 1)
        if stand_in is None:
            self.insert(re.sub(r"\r\n|\r|\n", "\n", text))
        else:
            self.pastes += 1
            self.insert(stand_in)

    def clear(self) -> None:
        self.text = ""
        self.cursor = 0

    def rows(self, width: int) -> list[tuple[int, int]]:
        return wrap_box(self.text, width)

    def cursor_row(self, rows: list[tuple[int, int]]) -> tuple[int, int]:
        """(row index, column within the row's text) of the cursor."""
        for index, (start, end) in enumerate(rows):
            if self.cursor <= end:
                return index, max(self.cursor - start, 0)
        start, end = rows[-1]
        return len(rows) - 1, end - start

    def down(self, width: int) -> None:
        rows = self.rows(width)
        row, column = self.cursor_row(rows)
        if row + 1 < len(rows):
            start, end = rows[row + 1]
            self.cursor = min(start + column, end)

    def row_end(self, width: int) -> None:
        rows = self.rows(width)
        row, _ = self.cursor_row(rows)
        self.cursor = rows[row][1]

    def home(self) -> None:
        self.cursor = 0


# -- the screen ------------------------------------------------------------------


class Screen:
    """Where things go on the terminal: the output region above, the spinner
    row, the box, the mode row."""

    def __init__(self) -> None:
        self.cols, self.rows = 80, 24
        self.measure()
        self.out_row = 1

    def measure(self) -> None:
        try:
            size = os.get_terminal_size(1)
            self.cols, self.rows = max(size.columns, 20), max(size.lines, 8)
        except OSError:
            pass

    @property
    def box_width(self) -> int:
        return max(self.cols - BOX_MARGIN, 1)

    def layout(self, box_rows: int) -> dict:
        rows = self.rows
        bottom_rule = rows - 1
        first_prompt = bottom_rule - box_rows
        top_rule = first_prompt - 1
        spinner = top_rule - 1
        region_bottom = max(spinner - 1, 1)
        return {
            "region_bottom": region_bottom,
            "spinner": spinner,
            "top_rule": top_rule,
            "first_prompt": first_prompt,
            "bottom_rule": bottom_rule,
            "mode": rows,
        }


class Fake:
    def __init__(self, args: dict) -> None:
        self.args = args
        self.cwd = os.getcwd()
        self.model = args.get("model") or DEFAULT_MODEL
        self.effort = args.get("effort") or DEFAULT_EFFORT
        self.permission_mode = args.get("permission_mode") or DEFAULT_PERMISSION
        self.progress_on = os.environ.get("ConEmuANSI") == "ON"
        self.kitty = os.environ.get("TERM_PROGRAM") == "kitty"
        self.screen = Screen()
        self.box = Box()
        self.transcript = self._open_transcript()
        self.hint = STARTUP_HINT
        self.turn: list[tuple[float, object]] = []  # (due, step)
        self.turn_number = 0
        self.turn_started = 0.0
        self.turn_text = ""
        self.in_turn = False
        self.spinner_on = False
        self.spinner_index = 0
        self.spinner_due = 0.0
        self.repaints: list[float] = []
        self.last_ctrl_c = 0.0
        self.mode_note = ""
        self.decoder_rest = b""
        self.saved_termios = None
        self.replaying = False
        self.in_menu = False
        self.dying = False
        self.save_on_exit = bool(args.get("save_on_exit"))
        self.running = True

    def _open_transcript(self) -> Transcript:
        args = self.args
        if args.get("resume"):
            directory = transcript_dir(self.cwd)
            source = os.path.join(directory, args["resume"] + ".jsonl")
            if args.get("fork") and os.path.exists(source):
                new_id = str(uuid.uuid4())
                target = os.path.join(directory, new_id + ".jsonl")
                with open(source, "rb") as src, open(target, "wb") as dst:
                    dst.write(src.read())
                return Transcript(self.cwd, new_id)
            return Transcript(self.cwd, args["resume"])
        if args.get("continue"):
            directory = transcript_dir(self.cwd)
            try:
                files = [n for n in os.listdir(directory) if n.endswith(".jsonl")]
                files.sort(key=lambda n: os.path.getmtime(os.path.join(directory, n)))
                if files:
                    return Transcript(self.cwd, files[-1][: -len(".jsonl")])
            except OSError:
                pass
        return Transcript(self.cwd, str(uuid.uuid4()))

    # -- terminal plumbing --------------------------------------------------------

    def raw(self) -> None:
        try:
            self.saved_termios = termios.tcgetattr(0)
            tty.setraw(0)
        except termios.error:
            self.saved_termios = None

    def cooked(self) -> None:
        if self.saved_termios is not None:
            try:
                termios.tcsetattr(0, termios.TCSADRAIN, self.saved_termios)
            except termios.error:
                pass

    def progress(self, state: int, terminator: str | None = None) -> None:
        if not self.progress_on:
            return
        end = terminator if terminator is not None else (ST if self.kitty else BEL)
        write(f"{OSC}9;4;{state};{end}")

    def title(self, text: str) -> None:
        write(f"{OSC}0;{text}{ST if self.kitty else BEL}")

    # -- drawing -----------------------------------------------------------------------

    def draw_all(self, banner: str | None = None) -> None:
        s = self.screen
        s.measure()
        write(f"{CSI}?25l{CSI}0m{CSI}r{CSI}2J{CSI}H{CSI}?2004h{CSI}?1004h")
        s.out_row = 1
        if banner:
            self.emit(banner)
        self.draw_box()

    def draw_box(self) -> None:
        """The box and what sits around it, at the bottom of the screen; the
        scroll region above it is set again, and the cursor left in the box."""
        s = self.screen
        width = s.box_width
        rows = self.box.rows(width)
        layout = s.layout(len(rows))
        write(f"{CSI}?25l")
        write(f"{CSI}{layout['region_bottom'] + 1};1H{CSI}J")  # blank from the spinner row down
        write(f"{CSI}{layout['top_rule']};1H{RULE * s.cols}")
        for index, (start, end) in enumerate(rows):
            row = layout["first_prompt"] + index
            prefix = f"{MARK}{NBSP}" if index == 0 else "  "
            write(f"{CSI}{row};1H{prefix}{self.box.text[start:end]}")
        if not self.box.text:
            self.draw_hint(layout["first_prompt"])
        write(f"{CSI}{layout['bottom_rule']};1H{RULE * s.cols}")
        mode_line = f"  {self.permission_mode} mode · effort {self.effort}"
        if self.mode_note:
            mode_line += f" · {self.mode_note}"
        write(f"{CSI}{layout['mode']};1H{mode_line[: s.cols]}")
        write(f"{CSI}1;{layout['region_bottom']}r")
        if s.out_row > layout["region_bottom"]:
            s.out_row = layout["region_bottom"]
        cursor_row, column = self.box.cursor_row(rows)
        write(f"{CSI}{layout['first_prompt'] + cursor_row};{3 + column}H{CSI}?25h")

    def draw_hint(self, row: int) -> None:
        """The hint after the mark: dim, in pieces, with a cursor movement
        between the words, as the CLI draws it."""
        write(f"{CSI}{row};3H{CSI}2m")
        for index, word in enumerate(self.hint.split(" ")):
            if index:
                write(f"{CSI}1C")
            write(word)
        write(f"{CSI}0m")

    def emit(self, text: str) -> None:
        """One line into the output region, scrolling it at the bottom."""
        s = self.screen
        layout = s.layout(len(self.box.rows(s.box_width)))
        write(f"{CSI}?25l{CSI}{s.out_row};1H{text[: s.cols]}")
        if s.out_row >= layout["region_bottom"]:
            write("\n")
        else:
            s.out_row += 1
        self.park_cursor()

    def park_cursor(self) -> None:
        s = self.screen
        rows = self.box.rows(s.box_width)
        layout = s.layout(len(rows))
        cursor_row, column = self.box.cursor_row(rows)
        write(f"{CSI}{layout['first_prompt'] + cursor_row};{3 + column}H{CSI}?25h")

    def draw_spinner(self) -> None:
        s = self.screen
        layout = s.layout(len(self.box.rows(s.box_width)))
        glyph = SPINNER[self.spinner_index % len(SPINNER)]
        self.spinner_index += 1
        elapsed = int(time.monotonic() - self.turn_started)
        write(f"{CSI}?25l{CSI}{layout['spinner']};1H{CSI}K{glyph} working ({elapsed}s)")
        self.title(("◐" if self.spinner_index % 2 else "◑") + " fake claude")
        self.park_cursor()

    def clear_spinner(self) -> None:
        s = self.screen
        layout = s.layout(len(self.box.rows(s.box_width)))
        write(f"{CSI}?25l{CSI}{layout['spinner']};1H{CSI}K")
        self.title("fake claude")
        self.park_cursor()

    # -- the turns --------------------------------------------------------------------

    def schedule(self, delay: float, step) -> None:
        self.turn.append((time.monotonic() + delay, step))

    def begin_turn(self, text: str, progress: bool = True, spinner: bool = True) -> None:
        self.turn_number += 1
        self.turn_started = time.monotonic()
        self.turn_text = text
        self.in_turn = True
        self.repaints.clear()
        self.transcript.user(text, self.permission_mode)
        self.box.clear()
        self.hint = RESTING_HINT
        self.draw_box()
        self.emit(f"> {text}")
        if progress:
            self.progress(3)
        self.spinner_on = spinner
        self.spinner_due = time.monotonic()

    def end_turn(self, reply: str, clear: bool = True, records: bool = True) -> None:
        if records:
            self.transcript.assistant(reply, self.model, self.effort)
            self.transcript.turn_duration(int((time.monotonic() - self.turn_started) * 1000))
            self.transcript.save_state(self.permission_mode)
        self.spinner_on = False
        self.clear_spinner()
        if clear:
            self.progress(0)
        self.emit(f">> fake-claude turn {self.turn_number} done")
        self.in_turn = False
        now = time.monotonic()
        self.repaints = [now + delay for delay in IDLE_REPAINTS_S]

    def interrupt(self) -> None:
        self.turn.clear()
        self.transcript.user("[Request interrupted by user]", self.permission_mode)
        self.spinner_on = False
        self.clear_spinner()
        self.progress(0)
        self.emit(">> fake-claude interrupted")
        self.in_turn = False
        self.repaints = [time.monotonic() + delay for delay in IDLE_REPAINTS_S]

    def submit(self, text: str) -> None:
        text = text.strip("\n")
        if not text:
            return
        if self.in_turn:
            self.emit(f"(queued, dropped by the fake: {text})")
            self.box.clear()
            self.draw_box()
            return
        words = text.split()
        head = words[0].lower()
        if head in ("/model", "/effort"):
            self.local_command(head, " ".join(words[1:]))
            return
        if head == "/exit":
            self.box.clear()
            self.leave()
            return
        if head == "/bg":
            # The handoff: the CLI hands the conversation to its daemon and
            # leaves the foreground. The fake has no daemon; it leaves.
            self.box.clear()
            self.emit(">> fake-claude got: /bg")
            self.leave()
            return
        if head == "probe":
            self.box.clear()
            self.draw_box()
            self.probe()
            return
        if head == "replay" and len(words) >= 3:
            self.box.clear()
            self.replay(text.split(None, 2)[2], int(words[1]))
            return
        if head == "menu":
            self.box.clear()
            self.show_menu()
            return
        if head in ("die", "--die"):
            # A turn the fake leaves mid-way, its progress report standing:
            # on the kit's Ctrl+D, once the kit has seen busy true, never at a
            # fixed moment of the fake's own.
            self.begin_turn(text)
            self.emit(">> fake-claude got: " + text)
            self.emit("working until Ctrl+D, then leaving with the progress report standing")
            self.dying = True
            return
        if head == "pause":
            self.begin_turn(text, progress=False, spinner=False)
            self.emit(">> fake-claude got: " + text)
            self.schedule(0.3, lambda: self.emit("working, with no progress report"))
            self.schedule(0.6, lambda: self.emit("now quiet for 0.9 s"))
            self.schedule(1.5, lambda: self.emit("back after the quiet"))
            self.schedule(1.9, lambda: self.emit("one more line"))
            self.schedule(2.3, lambda: self.end_turn("a reply after a quiet of 0.9 s", clear=False))
            return
        if head == "stream":
            self.begin_turn(text)
            self.emit(">> fake-claude got: " + text)
            self.schedule(0.5, lambda: self.emit("streaming the reply"))
            self.schedule(1.5, lambda: self.emit("the reply's last line"))
            self.schedule(2.0, lambda: self.end_turn("a reply, then output that never stops", clear=True))
            self.schedule(2.0, lambda: self.emit(">> fake-claude cleared"))
            for tick in range(40):
                self.schedule(
                    2.3 + 0.3 * tick, lambda tick=tick: self.emit(f"still printing after the clear ({tick})")
                )
            self.schedule(14.5, lambda: self.emit(">> fake-claude stream over"))
            return
        if head == "long" and len(words) >= 2:
            seconds = max(float(words[1]), 1.0)
            self.begin_turn(text)
            self.emit(">> fake-claude got: " + text)
            ticks = int(seconds / 0.5)
            for tick in range(ticks):
                self.schedule(0.5 * tick, lambda tick=tick: self.emit(f"working ({tick})"))
            self.schedule(seconds, lambda: self.end_turn(f"a reply after {seconds:g} s"))
            return
        if text.startswith("!"):
            self.begin_turn(text)
            self.schedule(0.1, lambda: self.shell(text[1:]))
            return
        self.begin_turn(text)
        self.emit(">> fake-claude got: " + text)
        lines = [
            "Let me think about that.",
            "Here is a reply, streamed in pieces,",
            "over about three seconds.",
        ]
        for index, line in enumerate(lines):
            self.schedule(0.6 + 0.7 * index, lambda line=line: self.emit(line))
        self.schedule(TURN_S, lambda: self.end_turn("A reply to: " + text))

    def show_menu(self) -> None:
        """A menu that takes the keys: the mark with an ordinary space after
        it, which the appendix says tells a menu from the box. Enter answers."""
        s = self.screen
        layout = s.layout(1)
        write(f"{CSI}?25l{CSI}{layout['region_bottom'] + 1};1H{CSI}J")
        write(f"{CSI}{layout['top_rule']};1H{RULE * s.cols}")
        write(f"{CSI}{layout['first_prompt']};1HWhich way? (Enter to pick)")
        write(f"{CSI}{layout['bottom_rule']};1H{MARK} 1. This way")
        write(f"{CSI}{layout['mode']};1H  2. That way")
        write(f"{CSI}{layout['bottom_rule']};3H{CSI}?25h")
        self.in_menu = True

    def shell(self, command: str) -> None:
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=20,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
            )
            output = (result.stdout + result.stderr).rstrip("\n")
        except (OSError, subprocess.SubprocessError) as error:
            output = f"error: {error}"
        for line in output.splitlines()[:50]:
            self.emit("  " + line)
        self.end_turn(output or "(no output)")

    def local_command(self, command: str, argument: str) -> None:
        if command == "/model":
            if argument:
                self.model = argument
                output = f"Set model to {argument} for this session only"
            else:
                output = f"Kept model as {self.model}"
        else:
            if argument:
                self.effort = argument
                output = f"Set effort level to {argument} (this session only)"
            else:
                output = f"Effort level is {self.effort}"
        self.transcript.local_command(command, argument, output)
        self.box.clear()
        self.emit(f"> {command} {argument}".rstrip())
        self.emit("  " + output)
        self.draw_box()
        self.repaints = [time.monotonic() + delay for delay in IDLE_REPAINTS_S]

    def die(self) -> None:
        """Leave mid-turn as a killed CLI would: no clear, no records, no reset."""
        os._exit(0)

    def leave(self) -> None:
        # A clean exit leaves the transcript as it was, so that it produces no
        # finish; --save-on-exit writes the CLI's state records on the way out
        # instead, a grown file that counts.
        if self.save_on_exit:
            self.transcript.save_state(self.permission_mode)
        self.progress(0, BEL)  # the clears a leaving CLI writes end with BEL
        self.running = False

    # -- input ------------------------------------------------------------------------

    def handle_input(self, data: bytes) -> None:
        data = self.decoder_rest + data
        try:
            text = data.decode("utf-8")
            self.decoder_rest = b""
        except UnicodeDecodeError as error:
            text = data[: error.start].decode("utf-8", "replace")
            self.decoder_rest = data[error.start :]
            if len(self.decoder_rest) > 4:
                self.decoder_rest = b""
        if self.replaying:
            # The replayed bytes draw answers from the terminal; only a
            # carriage return ends the replay.
            if "\r" in text:
                self.replaying = False
                self.draw_all("back from the replay")
            return
        if self.in_menu:
            if "\r" in text:
                self.in_menu = False
                self.emit("picked: this way")
                self.draw_box()
            return
        if text == "\r":
            self.submit(self.box.text)
            return
        # A short chunk ending in a carriage return submits, as `/bg` and its
        # Return do on the CLI (appendix D); the 16-byte boundary is the
        # fake's own, the real one being unmeasured. Longer chunks are pastes.
        if text.endswith("\r") and len(text) <= SUBMIT_CHUNK and "\x1b" not in text and "\n" not in text[:-1]:
            self.box.insert(text[:-1])
            self.submit(self.box.text)
            return
        if len(text) > PASTE_CHARS and "\x1b[200~" not in text:
            self.box.paste(text)
            self.draw_box()
            return
        index = 0
        changed = False
        while index < len(text):
            char = text[index]
            if text.startswith("\x1b[200~", index):
                end = text.find("\x1b[201~", index)
                if end < 0:
                    end = len(text)
                self.box.paste(text[index + 6 : end])
                index = end + 6
                changed = True
                continue
            if char == "\x1b":
                match = re.match(
                    r"\x1b\[[?>=<]?[0-9;:]*[A-Za-z~]|\x1b[\x20-\x2f]*[\x30-\x7e]|\x1b\]", text[index:]
                )
                sequence = match.group(0) if match else "\x1b"
                if sequence == "\x1b[B":
                    self.box.down(self.screen.box_width)
                    changed = True
                elif sequence == "\x1b[C":
                    self.box.cursor = min(self.box.cursor + 1, len(self.box.text))
                    changed = True
                elif sequence == "\x1b[D":
                    self.box.cursor = max(self.box.cursor - 1, 0)
                    changed = True
                elif sequence == "\x1b" and self.in_turn:
                    self.interrupt()
                    changed = True
                index += len(sequence)
                continue
            if char == "\x03":
                self.ctrl_c()
                changed = True
            elif char == "\x04" and self.dying:
                self.die()
            elif char in ("\x7f", "\x08"):
                self.box.backspace()
                changed = True
            elif char == "\x01":
                self.box.home()
                changed = True
            elif char == "\x05":
                self.box.row_end(self.screen.box_width)
                changed = True
            elif char == "\x0c":
                self.draw_all()
            elif char in ("\r", "\n"):
                self.box.insert("\n")
                changed = True
            elif char >= " ":
                self.box.insert(char)
                changed = True
            index += 1
        if changed and self.running:
            self.draw_box()

    def ctrl_c(self) -> None:
        now = time.monotonic()
        if self.in_turn:
            self.interrupt()
            return
        if self.box.text:
            self.box.clear()
            return
        if now - self.last_ctrl_c < CTRL_C_PAIR_S:
            self.leave()
            return
        self.last_ctrl_c = now
        self.mode_note = "Ctrl-C once more leaves"

    # -- the probe and the replay ------------------------------------------------------

    def probe(self) -> None:
        probe_loop()
        self.draw_all("back from the probe")

    def replay(self, path: str, offset: int) -> None:
        try:
            with open(path, "rb") as handle:
                data = handle.read(offset)
        except OSError as error:
            self.emit(f"replay: {error}")
            self.draw_box()
            return
        write(f"{ESC}c")
        write(data)
        self.replaying = True
        report(f"REPLAYED {offset}")

    # -- the loop -----------------------------------------------------------------------

    def run(self) -> int:
        self.raw()
        signal.signal(signal.SIGWINCH, lambda *_: self.resized())
        write(f"{CSI}?2004h{CSI}?1004h")  # bracketed paste and focus reports, as the CLI asks
        write(f"{CSI}c{CSI}?u")  # who is this terminal, and does it speak kitty's keyboard protocol
        self.progress(0)  # the CLI clears its report once at startup
        self.title("fake claude")
        self.draw_all(f"fake claude {VERSION} in {self.cwd}")
        try:
            while self.running:
                timeout = self.next_due()
                try:
                    ready, _, _ = select.select([0], [], [], timeout)
                except InterruptedError:
                    continue
                if ready:
                    try:
                        data = os.read(0, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    self.handle_input(data)
                self.run_due()
        finally:
            self.cooked()
            write(f"{CSI}?25h{CSI}0m{CSI}r{CSI}?2004l{CSI}?1004l{CSI}{self.screen.rows};1H\r\n")
        return 0

    def resized(self) -> None:
        self.screen.measure()
        self.draw_all("resized")

    def next_due(self) -> float:
        now = time.monotonic()
        due = [at for at, _ in self.turn] + list(self.repaints)
        if self.spinner_on:
            due.append(self.spinner_due)
        if not due:
            return 1.0
        return max(min(due) - now, 0.0)

    def run_due(self) -> None:
        now = time.monotonic()
        if self.spinner_on and now >= self.spinner_due:
            self.draw_spinner()
            self.spinner_due = now + 0.2
        ready = [step for at, step in self.turn if at <= now]
        self.turn = [(at, step) for at, step in self.turn if at > now]
        for step in ready:
            step()
        if self.repaints and now >= self.repaints[0]:
            self.repaints.pop(0)
            self.draw_box()


# -- the probe --------------------------------------------------------------------------


def read_for(seconds: float) -> bytes:
    """Everything the terminal sends within *seconds*."""
    out = b""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return out
        ready, _, _ = select.select([0], [], [], remaining)
        if ready:
            try:
                chunk = os.read(0, 65536)
            except OSError:
                return out
            if not chunk:
                return out
            out += chunk


def probe_loop() -> None:
    """Take commands until ``X``; see the module docstring."""
    saved = None
    try:
        saved = termios.tcgetattr(0)
        tty.setraw(0)
    except termios.error:
        pass
    report("READY")
    buffer = b""
    held = b""
    try:
        while True:
            ready, _, _ = select.select([0], [], [], 60.0)
            if not ready:
                continue
            try:
                chunk = os.read(0, 65536)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip(b"\r").strip()
                if not line:
                    continue
                parts = line.split(b" ", 2)
                command = parts[0]
                if command == b"X":
                    return
                if command == b"B" and len(parts) >= 2:
                    held += bytes.fromhex(parts[1].decode())
                elif command == b"P" and len(parts) >= 2:
                    write(held)
                    held = b""
                    read_for(0.3)  # answers to queries inside the bytes: not the kit's to see
                    report(f"D {parts[1].decode()}")
                elif command == b"Q" and len(parts) >= 3:
                    write(bytes.fromhex(parts[2].decode()))
                    answer = read_for(0.5)
                    report(f"R {parts[1].decode()} {answer.hex()}")
                elif command == b"W" and len(parts) >= 3:
                    first, _, second = parts[2].partition(b" ")
                    write(bytes.fromhex(first.decode()))
                    answer = read_for(0.1)
                    write(bytes.fromhex(second.decode()))
                    answer += read_for(0.5)
                    report(f"R {parts[1].decode()} {answer.hex()}")
                elif command == b"E" and len(parts) >= 2:
                    names = ("TERM", "COLORTERM", "VTE_VERSION", "ConEmuANSI", "TERM_PROGRAM")
                    text = ";".join(f"{name}={os.environ.get(name, '')}" for name in names)
                    report(f"R {parts[1].decode()} {text.encode().hex()}")
                elif command == b"Z" and len(parts) >= 2:
                    # The pty's size as the probe's own terminal reports it.
                    size = os.get_terminal_size(1)
                    report(f"R {parts[1].decode()} {f'{size.columns}x{size.lines}'.encode().hex()}")
                else:
                    report(f"? {line.decode('utf-8', 'replace')}")
    finally:
        if saved is not None:
            try:
                termios.tcsetattr(0, termios.TCSADRAIN, saved)
            except termios.error:
                pass


# -- the command line ---------------------------------------------------------------------


def parse_args(argv: list[str]) -> dict:
    args: dict = {"positional": []}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--model", "--effort", "--permission-mode", "--resume", "--mcp-config", "--tools",
                   "--worktree",
                   "--add-dir", "--settings", "--append-system-prompt", "-p", "--print"):  # fmt: skip
            key = arg.lstrip("-").replace("-", "_")
            if key == "p":
                key = "print"
            value = argv[index + 1] if index + 1 < len(argv) and not argv[index + 1].startswith("-") else ""
            args[key] = value
            index += 2 if value else 1
            continue
        if arg == "--continue" or arg == "-c":
            args["continue"] = True
        elif arg == "--fork-session":
            args["fork"] = True
        elif arg == "-w":
            args["worktree"] = ""
        elif arg == "--version" or arg == "-v":
            args["version"] = True
        elif arg == "--probe":
            args["probe"] = True
        elif arg == "--save-on-exit":
            args["save_on_exit"] = True
        elif arg == "--strict-mcp-config" or arg.startswith("--"):
            pass
        else:
            args["positional"].append(arg)
        index += 1
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.get("version"):
        print(VERSION)
        return 0
    if args["positional"][:1] == ["agents"]:
        print("[]")
        return 0
    if args["positional"][:1] == ["attach"]:
        print("fake claude: nothing to attach to", file=sys.stderr)
        return 1
    if "print" in args:
        print("fake claude: a headless run prints this and exits")
        return 0
    if args.get("probe"):
        probe_loop()
        return 0
    if not sys.stdin.isatty():
        print("fake claude: needs a terminal (or --probe, --version, agents --json, -p)", file=sys.stderr)
        return 1
    return Fake(args).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
