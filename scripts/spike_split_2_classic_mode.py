"""Split-service spike 2: what the CLI repaints in classic (inline) mode.

Not product code. Spec: ~/specs/collins/split-service-and-client.md, PR-0.1
item 2, the open half of finding F6.

The question: with the CLI on the main screen (no alternate screen), after a
turn long enough to have pushed output into the terminal's scrollback, what
does it write on SIGWINCH with the size unchanged, on a resize (narrower,
back, rows only) and on Ctrl+L? Does it clear the scrollback, and how much
of the conversation does it paint again? And what does a terminal that is
fed the whole recording end up holding: one transcript or several?

    python3 scripts/spike_split_2_classic_mode.py
        record (one real turn, isolated HOME), analyse, then replay the
        recording into a childless VTE behind the headless display
    python3 scripts/spike_split_2_classic_mode.py --recordings DIR
        analyse and replay an earlier recording; spends nothing
    python3 scripts/spike_split_2_classic_mode.py --no-vte
        skip the VTE half

Recordings go to --out (default: a fresh temp dir) and are never committed:
they hold paths. Exits 0 when it ran, and 0 with a line saying what is
missing when it could not.

Found on 2026-09-29, CLI 2.1.285, VTE 0.84:

- Classic mode is picked with CLAUDE_CODE_NO_FLICKER=0 and fullscreen with
  =1; unset, the CLI reads settings.json's "tui", then its own install
  state and server gates cached in ~/.claude.json, so a fixture that leaves
  it unset records whichever the account is being served that week.
- In classic mode the CLI never clears: no ED 2, no ED 3. A resize and
  Ctrl+L home the cursor, erase each visible row (EL 2) and paint the last
  screenful of the conversation, about 1.6 KB. The scrollback is not sent
  again. SIGWINCH with the size unchanged writes nothing.
- The turn itself is written with relative cursor moves counted in rows of
  the grid it was written for. Replayed into a narrower terminal, about 40
  of the count's 120 lines were overwritten, and the CLI's repaint after the
  resize does not bring them back. Replayed at the grid it was written for
  and resized afterwards, the terminal holds what today's terminal holds.
"""

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spike_split_common as common  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADLESS = os.path.join(REPO, ".agents", "capture-screenshots", "scripts", "with-headless-display.sh")
COUNT = 120
PROMPT = f"count from 1 to {COUNT}, one number per line, nothing else"
COLS, ROWS = 120, 40
WATCHDOG_S = 300

# What each step does to the terminal, in order. (name, action, cols, rows):
# the size is the one in force once the step has been taken.
STEPS = (
    ("winch-same-size", "SIGWINCH, size unchanged", COLS, ROWS),
    ("resize-narrower", "resize 120x40 -> 100x40", 100, ROWS),
    ("resize-back", "resize 100x40 -> 120x40", COLS, ROWS),
    ("resize-rows-only", "resize 120x40 -> 120x30", COLS, 30),
    ("resize-rows-back", "resize 120x30 -> 120x40", COLS, ROWS),
    ("ctrl-l", "Ctrl+L", COLS, ROWS),
)

ESCAPES = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    rb"|\x1bP[^\x1b]*\x1b\\"  # DCS
    rb"|\x1b\[[0-9;:<=>?]*[ -/]*[@-~]"  # CSI
    rb"|\x1b[@-Z\\-_=>78]"  # two-byte escapes
)
MODE = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")
KITTY = re.compile(rb"\x1b\[([<>=])([0-9;]*)u")
MODIFY_KEYS = re.compile(rb"\x1b\[>4;?([0-9]*)m")
CURSOR_UP = re.compile(rb"\x1b\[([0-9]*)A")
NUMBER_LINE = re.compile(r"^\W{0,4}(\d{1,3})\s*$")


def plain(data: bytes) -> str:
    return ESCAPES.sub(b"", data).decode("utf-8", "replace")


def numbers_in(text: str) -> list[int]:
    """The count's own lines, in the order they appear: a line that is a
    number from 1 to COUNT and nothing else (the reply's first line carries
    the CLI's bullet, which NUMBER_LINE lets through)."""
    found = []
    for line in re.split(r"[\r\n]+", text):
        match = NUMBER_LINE.match(line.strip())
        if match and 1 <= int(match.group(1)) <= COUNT:
            found.append(int(match.group(1)))
    return found


def describe(data: bytes) -> dict:
    modes = sorted({f"?{m.group(1).decode()}{m.group(2).decode()}" for m in MODE.finditer(data)})
    modes += sorted({f"CSI {m.group(1).decode()}{m.group(2).decode()}u" for m in KITTY.finditer(data)})
    modes += sorted({f"CSI >4;{m.group(1).decode()}m" for m in MODIFY_KEYS.finditer(data)})
    numbers = numbers_in(plain(data))
    ups = [int(m.group(1) or b"1") for m in CURSOR_UP.finditer(data)]
    return {
        "bytes": len(data),
        "ed2": data.count(b"\x1b[2J"),
        "ed3": data.count(b"\x1b[3J"),
        "ed0": data.count(b"\x1b[J") + data.count(b"\x1b[0J"),
        "home": data.count(b"\x1b[H") + data.count(b"\x1b[1;1H"),
        "el2": data.count(b"\x1b[2K"),
        "cursor_up_rows": sum(ups),
        "alt_screen": b"\x1b[?1049h" in data,
        "modes": modes,
        "numbers": len(numbers),
        "distinct_numbers": len(set(numbers)),
        "first_number": numbers[0] if numbers else None,
        "prompt_echoes": plain(data).count(PROMPT[:24]),
    }


# ---------------------------------------------------------------- record


def config_keys(home: str) -> dict:
    try:
        with open(os.path.join(home, ".claude.json")) as f:
            config = json.load(f)
    except (OSError, ValueError):
        return {}
    return {
        key: config.get(key)
        for key in ("fullscreenBootPending", "fullscreenUpsellSeenCount", "firstStartVersion")
    } | {
        "gate tengu_pewter_brook": (config.get("cachedGrowthBookFeatures") or {}).get("tengu_pewter_brook"),
        "gate tengu_amber_creek": (config.get("cachedGrowthBookFeatures") or {}).get("tengu_amber_creek"),
    }


def probe_mode(home: str, flicker: str | None) -> dict:
    """One launch, no prompt: which screen the CLI chose with
    CLAUDE_CODE_NO_FLICKER set to `flicker` (None: unset)."""
    env = common.cli_env(home)
    if flicker is not None:
        env["CLAUDE_CODE_NO_FLICKER"] = flicker
    pid, fd = common.spawn_cli(env, cwd=common.workdir(home), rows=ROWS, cols=COLS)
    got = common.drain(fd, 7, quiet_after=2.5)
    common.leave(pid, fd)
    return {
        "CLAUDE_CODE_NO_FLICKER": flicker,
        "alternate screen": b"\x1b[?1049h" in got,
        "mouse tracking": b"\x1b[?1003h" in got,
        "bytes": len(got),
    }


def record(out: str, modes: bool) -> bool:
    if not common.have_cli():
        print("missing: the `claude` CLI or its login; nothing recorded")
        return False
    left = common.token_minutes_left()
    if left is None or left < common.MIN_TOKEN_MINUTES:
        print(f"missing: a login with time left on it ({left} minutes); nothing recorded")
        return False
    os.makedirs(out, exist_ok=True)
    home = common.make_home(parent=out)
    manifest = {"cli": cli_version(), "prompt": PROMPT, "cols": COLS, "rows": ROWS}
    try:
        if modes:
            manifest["mode_probes"] = [probe_mode(home, value) for value in (None, "0", "1")]
            manifest["config_keys"] = config_keys(home)
        env = common.cli_env(home)
        env["CLAUDE_CODE_NO_FLICKER"] = "0"  # classic, whatever the gates say
        pid, fd = common.spawn_cli(
            env, cwd=common.workdir(home), rows=ROWS, cols=COLS, argv=("claude", "--model", "haiku")
        )
        segments = []

        def keep(name: str, action: str, cols: int, rows: int, data: bytes) -> None:
            with open(os.path.join(out, name + ".bin"), "wb") as f:
                f.write(data)
            segments.append({"name": name, "action": action, "cols": cols, "rows": rows})

        keep("start", "launch", COLS, ROWS, common.drain(fd, 9, quiet_after=3))
        common.type_text(fd, PROMPT.encode())
        time.sleep(0.3)
        os.write(fd, b"\r")
        keep("turn", "one turn", COLS, ROWS, common.drain(fd, 150, quiet_after=5))
        for name, action, cols, rows in STEPS:
            if name == "winch-same-size":
                os.kill(pid, signal.SIGWINCH)
            elif name == "ctrl-l":
                os.write(fd, b"\x0c")
            else:
                common.set_size(fd, rows, cols)
            keep(name, action, cols, rows, common.drain(fd, 3.5))
        keep("exit", "Ctrl+C Ctrl+C", COLS, ROWS, common.leave(pid, fd))
        manifest["segments"] = segments
    finally:
        common.remove_home(home)
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return True


def cli_version() -> str:
    try:
        done = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        return done.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# --------------------------------------------------------------- analyse


def load(out: str) -> tuple[dict, dict]:
    with open(os.path.join(out, "manifest.json")) as f:
        manifest = json.load(f)
    data = {}
    for segment in manifest["segments"]:
        with open(os.path.join(out, segment["name"] + ".bin"), "rb") as f:
            data[segment["name"]] = f.read()
    return manifest, data


def analyse(out: str) -> dict:
    manifest, data = load(out)
    print(f"CLI: {manifest['cli']}   grid: {manifest['cols']}x{manifest['rows']}")
    for probe in manifest.get("mode_probes", ()):
        print(
            f"  launch with CLAUDE_CODE_NO_FLICKER={probe['CLAUDE_CODE_NO_FLICKER']!r}: "
            f"alternate screen {probe['alternate screen']}, "
            f"any-motion mouse {probe['mouse tracking']}"
        )
    if manifest.get("config_keys"):
        print("  the isolated home's .claude.json after those launches:", manifest["config_keys"])
    turn = describe(data["start"] + data["turn"])
    classic = not turn["alt_screen"]
    print(
        f"recorded turn: {turn['bytes']} bytes, alternate screen {turn['alt_screen']}, "
        f"{turn['distinct_numbers']} of {COUNT} lines of the count seen, "
        f"{turn['numbers']} printed"
    )
    if not classic:
        print("the CLI went to the alternate screen: this is not a classic-mode recording")
    if turn["distinct_numbers"] < COUNT:
        print("the turn did not finish inside the recording; what follows is about a partial turn")
    header = (
        f"{'step':<26}{'bytes':>7} {'2J':>3} {'3J':>3} {'J':>3} {'home':>5} {'2K':>4} "
        f"{'up':>5} {'count lines':>12} {'prompt':>7}  modes"
    )
    print(header)
    rows = {}
    for segment in manifest["segments"]:
        name = segment["name"]
        if name in ("start", "turn", "exit"):
            continue
        found = describe(data[name])
        rows[name] = found
        span = "-"
        if found["numbers"]:
            span = f"{found['numbers']} from {found['first_number']}"
        print(
            f"{segment['action']:<26}{found['bytes']:>7} {found['ed2']:>3} {found['ed3']:>3} "
            f"{found['ed0']:>3} {found['home']:>5} {found['el2']:>4} {found['cursor_up_rows']:>5} "
            f"{span:>12} {found['prompt_echoes']:>7}  {' '.join(found['modes']) or '-'}"
        )
    return {"classic": classic, "turn": turn, "steps": rows, "manifest": manifest}


# ------------------------------------------------------------------- vte

RULE_LINE = re.compile(r"^\s*[─╌]{20,}\s*$")


def scenarios(manifest: dict) -> list[tuple[str, list[tuple]]]:
    """What the childless VTE is put through. An op is ("size", cols, rows),
    ("feed", segment name, ...) or ("read", label)."""
    cols, rows = manifest["cols"], manifest["rows"]
    names = [s["name"] for s in manifest["segments"] if s["name"] != "exit"]
    by_name = {s["name"]: s for s in manifest["segments"]}
    # Today: the terminal that was there all along, resized as it went.
    live = [("size", cols, rows), ("feed", "start", "turn"), ("read", "the turn")]
    for name in names[2:]:
        segment = by_name[name]
        if name.startswith("resize"):
            live.append(("size", segment["cols"], segment["rows"]))
            live.append(("read", segment["action"] + ", before the CLI answers"))
        live.append(("feed", name))
        live.append(("read", segment["action"]))
    found = [("live", live)]
    # Attach by replay, the grid the stream ended at, no resize on the way.
    found.append(("replay: whole ring at 120x40", [("size", cols, rows), ("feed", *names), ("read", "end")]))
    # Attach at another grid the way spec 3.3 has it: replay at the client's
    # own grid, then the resize, which the CLI answers with its repaint.
    for label, segment in (
        ("100x40", "resize-narrower"),
        ("120x30", "resize-rows-only"),
    ):
        size = by_name[segment]
        found.append(
            (
                f"replay: turn at the client's {label}, then the CLI's repaint for {label}",
                [
                    ("size", size["cols"], size["rows"]),
                    ("feed", "start", "turn"),
                    ("read", "replayed"),
                    ("feed", segment),
                    ("read", "repainted"),
                ],
            )
        )
        # The same attach with the terminal held at the grid the bytes were
        # written for while they replay, and resized only afterwards.
        found.append(
            (
                f"replay: turn at the pty's 120x40, then resize to {label} and the repaint",
                [
                    ("size", cols, rows),
                    ("feed", "start", "turn"),
                    ("size", size["cols"], size["rows"]),
                    ("feed", segment),
                    ("read", "repainted"),
                ],
            )
        )
    # A grid larger than the one the bytes were written for, either way.
    for wide, tall in ((140, rows), (cols, 50)):
        found.append(
            (
                f"replay: turn at the client's {wide}x{tall}",
                [("size", wide, tall), ("feed", "start", "turn"), ("read", "replayed")],
            )
        )
    return found


def vte_main(out: str) -> int:
    """Runs behind the headless display: puts a childless VTE through each
    scenario and writes what it held at every read to vte.json."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Vte", "3.91")
    from gi.repository import Gio, GLib, Gtk, Vte

    manifest, data = load(out)
    results = {}
    app = Gtk.Application(
        application_id=f"com.episode6.Collins.SpikeSplit2.p{os.getpid()}",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def held(term) -> dict:
        cols, rows = term.get_column_count(), term.get_row_count()
        _column, row = term.get_cursor_position()
        got = term.get_text_range_format(Vte.Format.TEXT, 0, 0, row + rows, cols)
        text = (got[0] if isinstance(got, tuple) else got) or ""
        lines = [line.rstrip() for line in text.rstrip("\n").split("\n")]
        numbers = numbers_in(text)
        copies = {}
        for number in numbers:
            copies[number] = copies.get(number, 0) + 1
        return {
            "grid": [cols, rows],
            "lines": len(lines),
            "count_lines": len(numbers),
            "copies_of_a_line": max(copies.values()) if copies else 0,
            "missing": [n for n in range(1, COUNT + 1) if n not in copies],
            "doubled": sorted(n for n, c in copies.items() if c > 1),
            "rule_lines": sum(1 for line in lines if RULE_LINE.match(line)),
            "prompt_echoes": text.count(PROMPT[:24]),
            "text": lines,
        }

    def activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.set_default_size(1800, 1150)
        win.present()
        plan = []
        for name, ops in scenarios(manifest):
            state = {}
            reads = results.setdefault(name, [])

            def fresh(state=state):
                term = Vte.Terminal()
                term.set_halign(Gtk.Align.START)
                term.set_valign(Gtk.Align.START)
                term.set_scrollback_lines(100000)
                win.set_child(term)
                state["term"] = term

            plan.append(fresh)
            for op in ops:
                if op[0] == "size":
                    plan.append(lambda op=op, state=state: state["term"].set_size(op[1], op[2]))
                elif op[0] == "feed":
                    plan.append(
                        lambda op=op, state=state: state["term"].feed(b"".join(data[name] for name in op[1:]))
                    )
                else:
                    plan.append(
                        lambda op=op, state=state, reads=reads: reads.append(
                            {"at": op[1], **held(state["term"])}
                        )
                    )

        def tick():
            if not plan:
                with open(os.path.join(out, "vte.json"), "w") as f:
                    json.dump(results, f, indent=1, ensure_ascii=False)
                app.quit()
                return GLib.SOURCE_REMOVE
            plan.pop(0)()
            GLib.timeout_add(300, tick, priority=GLib.PRIORITY_DEFAULT)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(600, tick, priority=GLib.PRIORITY_DEFAULT)

    app.connect("activate", activate)
    GLib.timeout_add_seconds(100, lambda: os._exit(3))
    app.run([])
    return 0


def differing(a: list[str], b: list[str]) -> int:
    """How many lines one text has that the other has not, either way."""
    import difflib

    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    same = sum(block.size for block in matcher.get_matching_blocks())
    return (len(a) - same) + (len(b) - same)


def vte(out: str) -> dict | None:
    try:
        import gi

        gi.require_version("Vte", "3.91")
    except (ImportError, ValueError) as err:
        print(f"missing: VTE for GTK 4 ({err}); the VTE half was skipped")
        return None
    if not os.path.exists(HEADLESS):
        print("missing: the headless display wrapper; the VTE half was skipped")
        return None
    target = os.path.join(out, "vte.json")
    if os.path.exists(target):
        os.unlink(target)
    try:
        subprocess.run(
            ["bash", HEADLESS, sys.executable, os.path.abspath(__file__), "--vte-child", out],
            timeout=160,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        print(f"the VTE half did not run: {err}")
        return None
    if not os.path.exists(target):
        print("the VTE half wrote nothing (no display came up?)")
        return None
    with open(target) as f:
        results = json.load(f)
    live = {read["at"]: read for read in results.get("live", ())}
    print()
    print("A childless VTE 0.84 fed the recording. Columns: grid, lines held (scrollback and")
    print(f"screen), lines of the count held (of {COUNT}), doubled, missing, rule lines, and lines")
    print("that differ from the live terminal at the same point.")
    for name, reads in results.items():
        print(f"  {name}")
        for read in reads:
            against = None
            if name != "live":
                if "120x40" in name and "whole ring" in name:
                    against = live.get("Ctrl+L")
                elif "100x40" in name and read["at"] == "repainted":
                    against = live.get("resize 120x40 -> 100x40")
                elif "120x30" in name and read["at"] == "repainted":
                    against = live.get("resize 120x40 -> 120x30")
            read["differs"] = None if against is None else differing(against["text"], read["text"])
            print(
                f"    {read['at']:<50} {read['grid'][0]}x{read['grid'][1]:<4}"
                f"{read['lines']:>5} {read['count_lines']:>5} {len(read['doubled']):>4} "
                f"{len(read['missing']):>4} {read['rule_lines']:>4} "
                f"{'-' if read['differs'] is None else read['differs']:>5}"
            )
    return results


# ------------------------------------------------------------------ main


def findings(found: dict, held: dict | None) -> None:
    print()
    print("Finding:")
    if not found["classic"]:
        print("  not measured: the recording is not in classic mode")
        return
    steps = found["steps"]
    for name, _action, _cols, _rows in STEPS:
        step = steps.get(name)
        if step is None:
            continue
        clears = []
        if step["ed2"]:
            clears.append("clears the screen")
        if step["ed3"]:
            clears.append("clears the scrollback")
        if step["el2"]:
            clears.append(f"erases {step['el2']} rows one by one from home")
        if step["distinct_numbers"] >= COUNT:
            paints = "paints the whole conversation again"
        elif step["numbers"]:
            paints = f"paints the last {step['numbers']} lines of the count again"
        elif step["bytes"] > 200:
            paints = "repaints its live region only"
        else:
            paints = "paints nothing"
        print(f"  {name}: {step['bytes']} bytes, {', '.join(clears) or 'clears nothing'}, {paints}")
    if not held:
        return
    live = held.get("live") or []
    worst = max((len(read["doubled"]) for read in live), default=0)
    print(f"  today's terminal, resized as it went: at worst {worst} lines of the count held twice")
    for name, reads in held.items():
        if name == "live" or not reads:
            continue
        last = reads[-1]
        against = ""
        if last["differs"] is not None:
            against = f", {last['differs']} lines differ from the live terminal"
        print(f"  {name}: {len(last['doubled'])} doubled, {len(last['missing'])} missing{against}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--recordings", help="analyse this directory instead of recording")
    parser.add_argument("--out", help="where a new recording goes (default: a temp dir)")
    parser.add_argument("--no-vte", action="store_true", help="skip the VTE half")
    parser.add_argument("--no-modes", action="store_true", help="skip the three mode launches")
    parser.add_argument("--vte-child", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.vte_child:
        return vte_main(args.vte_child)
    signal.signal(signal.SIGALRM, lambda *_: os._exit(3))
    signal.alarm(WATCHDOG_S)
    out = args.recordings
    if out is None:
        out = args.out or tempfile.mkdtemp(prefix="spike-split-2-")
        if not record(out, modes=not args.no_modes):
            return 0
        print(f"recorded into {out} (one real turn spent); not for committing")
    elif not os.path.exists(os.path.join(out, "manifest.json")):
        print(f"missing: {out}/manifest.json; nothing to analyse")
        return 0
    found = analyse(out)
    held = None if args.no_vte else vte(out)
    findings(found, held)
    return 0


if __name__ == "__main__":
    sys.exit(main())
