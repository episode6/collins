#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Headless check that the conformance kit's VTE data is what VTE answers.

The kit (conformance/) holds a server to the terminal contract with data
measured off a real VTE 0.84: `conformance/data/decrqm-modes.json`, VTE's
DECRQM answer for every private and ANSI mode in its default state, and
`conformance/data/vte-palette.json`, the 256 colours VTE answers an OSC 4
query with. This check feeds those queries to a real childless
`Vte.Terminal` and compares; `--write` writes the two files from what VTE
answered instead. A VTE upgrade that changes a mode's default shows up here.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_conformance_data.py [--write]

Run it behind the headless wrapper, or a window opens on the user's screen.
Both files are the kit's (MIT) data, measurements of VTE and not its code.
"""

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "conformance", "data")
MODES_PATH = os.path.join(DATA, "decrqm-modes.json")
PALETTE_PATH = os.path.join(DATA, "vte-palette.json")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import GLib, Gtk, Vte  # noqa: E402

DEADLINE_S = 600
GLib.timeout_add_seconds(DEADLINE_S, lambda: (print("FAIL deadline", flush=True), os._exit(2)))

write = "--write" in sys.argv
context = GLib.MainContext.default()
commits: list[bytes] = []
SENTINEL = b"\x1b[5n"
SENTINEL_ANSWER = b"\x1b[0n"
MODE_MAX = 65535
BATCH = 2048

window = Gtk.Window()
window.set_default_size(1600, 1000)
terminal = Vte.Terminal()
terminal.set_size(120, 40)
terminal.set_halign(Gtk.Align.START)
terminal.set_valign(Gtk.Align.START)
terminal.connect("commit", lambda _t, text, _size: commits.append(text.encode("utf-8", "surrogateescape")))
window.set_child(terminal)
window.present()


def pump_until(predicate, timeout=30.0):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            return False
        if not context.iteration(False):
            time.sleep(0.002)
    return True


pump_until(lambda: terminal.get_mapped() and terminal.get_char_width() > 0)


def ask(data: bytes) -> bytes:
    """Feed *data* with a sentinel after it; everything VTE answered."""
    commits.clear()
    terminal.feed(data + SENTINEL)
    if not pump_until(lambda: any(SENTINEL_ANSWER in c for c in commits)):
        raise SystemExit("FAIL: VTE never answered the sentinel")
    return b"".join(commits).replace(SENTINEL_ANSWER, b"")


DECRQM = re.compile(rb"\x1b\[(\??)(\d+);(\d)\$y")
PALETTE = re.compile(rb"\x1b\]4;(\d+);rgb:([0-9a-f]{4})/([0-9a-f]{4})/([0-9a-f]{4})\x1b\\")


def measure_modes() -> dict:
    """{"private": {mode: state}, "ansi": {mode: state}} for every mode VTE
    answers other than 0 (unknown), on a fresh terminal."""
    terminal.feed(b"\x1bc")
    pump_until(lambda: False, timeout=0.2)
    out: dict = {"private": {}, "ansi": {}}
    for prefix, name in ((b"?", "private"), (b"", "ansi")):
        for start in range(0, MODE_MAX + 1, BATCH):
            queries = b"".join(
                b"\x1b[" + prefix + str(n).encode() + b"$p"
                for n in range(start, min(start + BATCH, MODE_MAX + 1))
            )
            answered = ask(queries)
            for match in DECRQM.finditer(answered):
                if (match.group(1) == b"?") != (prefix == b"?"):
                    continue
                state = int(match.group(3))
                if state != 0:
                    out[name][str(int(match.group(2)))] = state
    return out


def measure_palette() -> list[str]:
    terminal.feed(b"\x1bc")
    pump_until(lambda: False, timeout=0.2)
    colours = {}
    for start in range(0, 256, 64):
        answered = ask(b"".join(b"\x1b]4;" + str(i).encode() + b";?\x1b\\" for i in range(start, start + 64)))
        for match in PALETTE.finditer(answered):
            colours[int(match.group(1))] = b"/".join(match.group(2, 3, 4)).decode()
    missing = [i for i in range(256) if i not in colours]
    if missing:
        raise SystemExit(f"FAIL: VTE answered no OSC 4 for {missing[:5]}")
    return [colours[i] for i in range(256)]


version = f"{Vte.get_major_version()}.{Vte.get_minor_version()}.{Vte.get_micro_version()}"
print(f"VTE {version}", flush=True)
modes = measure_modes()
palette = measure_palette()
print(
    f"measured {len(modes['private'])} private and {len(modes['ansi'])} ANSI modes, {len(palette)} colours",
    flush=True,
)

if write:
    with open(MODES_PATH, "w") as f:
        json.dump(
            {
                "about": "VTE's DECRQM answer for every mode in its default state, measured by "
                "scripts/check_conformance_data.py; a mode absent here answers 0 (unknown). "
                "1 set, 2 reset, 3 permanently set, 4 permanently reset.",
                "vte": version,
                "private": modes["private"],
                "ansi": modes["ansi"],
            },
            f,
            indent=1,
        )
        f.write("\n")
    with open(PALETTE_PATH, "w") as f:
        json.dump(
            {
                "about": "VTE's default 256-colour palette as OSC 4 reports it (rrrr/gggg/bbbb), "
                "measured by scripts/check_conformance_data.py.",
                "vte": version,
                "colours": palette,
            },
            f,
            indent=1,
        )
        f.write("\n")
    print(f"wrote {os.path.relpath(MODES_PATH, ROOT)} and {os.path.relpath(PALETTE_PATH, ROOT)}")
    os._exit(0)

failures = 0
with open(MODES_PATH) as f:
    want_modes = json.load(f)
with open(PALETTE_PATH) as f:
    want_palette = json.load(f)
for name in ("private", "ansi"):
    if want_modes[name] != modes[name]:
        failures += 1
        got, want = modes[name], want_modes[name]
        diff = {k: (want.get(k), got.get(k)) for k in set(got) | set(want) if want.get(k) != got.get(k)}
        print(f"FAIL {name} modes differ (kit, VTE): {dict(sorted(diff.items(), key=lambda kv: int(kv[0])))}")
    else:
        print(f"ok   {name} modes")
if want_palette["colours"] != palette:
    failures += 1
    print("FAIL palette differs")
else:
    print("ok   palette")
print(("FAIL" if failures else "PASS") + " conformance data against VTE", flush=True)
os._exit(1 if failures else 0)
