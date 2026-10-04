#!/usr/bin/env python3
"""Probe: is the first keystroke after a click into a childless VTE lost?

F11's harness (scripts/spike_split_1_end_to_end.py) lost the first key it
typed after the terminal took focus in every run (34 of 35, 69 of 70) and
never chased it. This drives the client glue of the server backend
(collins.ptyclient.ClientTerminal over the loopback) with real input
events through the headless compositor — a click into the terminal, one
key — and counts, over many rounds, whether the key reached the pty and
came back echoed, under four conditions: with the redraw guard freshly
raised and lowered before the click (an attach per round) or not, and
with the program's focus reporting on or off.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/probe_first_keystroke.py [--rounds 50]

Input goes through org.gnome.Mutter.RemoteDesktop on the headless shell's
own bus and only that bus (spike_split_1_end_to_end.Input refuses the
session bus): never the user's desktop. No real `claude`; `cat` is behind
the pty. Exit 0 when every condition delivers every first key, 1 otherwise,
and the table either way.
"""

import argparse
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("COLLINS_PTY_STATE_DIR", tempfile.mkdtemp(prefix="collins-probe-pty-"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
import spike_split_1_end_to_end as spike  # noqa: E402
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from collins import ptyclient  # noqa: E402
from collins.api.loopback import LoopbackServer  # noqa: E402
from collins.service.core import ServiceCore  # noqa: E402

FOCUS_ON, FOCUS_OFF = "\x1b[?1004h", "\x1b[?1004l"
# What the CLI sets (F1): mouse tracking with any-motion and SGR encoding
# (fullscreen mode), the kitty keyboard flags, modifyOtherKeys, bracketed
# paste.
MOUSE_ON = "\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1003l\x1b[?1002l\x1b[?1000l\x1b[?1006l"
KITTY_ON, KITTY_OFF = "\x1b[>5u", "\x1b[<u"
MOK_ON, MOK_OFF = "\x1b[>4;2m", "\x1b[>4;0m"
PASTE_ON, PASTE_OFF = "\x1b[?2004h", "\x1b[?2004l"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument(
        "--conditions",
        default="plain,guard,focus,guard+focus,grab,grab+guard,grab+focus",
        help="comma-separated: plain | guard | focus | mouse | kitty | mok | paste | grab, combined with +",
    )
    args = parser.parse_args()
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]

    os.environ["SHELL"] = "/bin/cat"
    core = ServiceCore()
    loopback = LoopbackServer(core)
    app = Gtk.Application(
        application_id="com.episode6.Collins.ProbeFirstKey", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    results: dict[str, dict] = {}
    ctx: dict = {"out": [], "written": [], "top": 0}

    real_write = core.ptys.write

    def spy_write(pty_id, data, sink=None):
        ctx["written"].append((time.monotonic(), bytes(data)))
        return real_write(pty_id, data, sink=sink)

    core.ptys.write = spy_write

    def activate(app):
        win = Gtk.ApplicationWindow(
            application=app, title="first keystroke probe", default_width=900, default_height=600
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        entry = Gtk.Entry(placeholder_text="elsewhere")  # where focus goes between rounds
        term = ptyclient.ClientVte()
        term.set_vexpand(True)
        box.append(entry)
        box.append(term)
        win.set_child(box)
        win.present()
        client = loopback.connect(
            lambda p, d, f: (ctx["out"].append((time.monotonic(), d, f)), ctx["view"].on_output(p, d, f)),
            lambda e: ctx["view"].on_event(e),
            device="probe",
        )
        ctx["view"] = view = ptyclient.ClientTerminal(term, client, on_exited=lambda s: None)
        reply = client.request({"t": "spawn", "kind": "shell", "cwd": "/tmp", "cols": 100, "rows": 30})
        pty = reply["pty"]
        view.attach(pty)
        try:
            inp = spike.Input(Gio, GLib)
        except GLib.Error as err:
            print("no RemoteDesktop on the headless bus:", err.message)
            app.quit()
            return
        ctx["inp"] = inp

        def entry_focused():
            # A Gtk.Entry's focus sits on its inner text widget.
            focus = win.get_focus()
            return focus is not None and (focus is entry or focus.is_ancestor(entry))

        def until(pred, ms):
            deadline = time.monotonic() + ms / 1000
            while time.monotonic() < deadline:
                if pred():
                    return True
                yield 20
            return pred()

        def sleep(ms):
            yield ms

        def pointer_to(widget):
            # Move the pointer onto the widget's centre: relative motion
            # from wherever it is, so first park it at the screen's corner.
            # The window is fullscreen, so its content's origin is the
            # screen's; a top offset is measured once anyway (find_top).
            inp.motion(-5000, -5000)
            yield from sleep(30)
            ok, rect = widget.compute_bounds(win)
            x, y = rect.origin.x + rect.size.width / 2, rect.origin.y + rect.size.height / 2
            inp.motion(x, y + ctx["top"])
            yield from sleep(30)

        def click(widget):
            yield from pointer_to(widget)
            inp.button(True)
            yield from sleep(20)
            inp.button(False)
            yield from sleep(60)

        def focus(widget, how):
            """Put the keyboard in *widget*: by a real click through the
            compositor, or by GTK's own grab (no pointer involved)."""
            if how == "click":
                yield from click(widget)
            else:
                widget.grab_focus()
                yield 60

        def find_top():
            # Where the window's content starts on the screen: click at
            # growing offsets from the corner until the entry takes focus.
            ok, rect = entry.compute_bounds(win)
            for top in range(0, 120, 8):
                inp.motion(-5000, -5000)
                yield from sleep(30)
                inp.motion(rect.origin.x + 20, rect.origin.y + rect.size.height / 2 + top)
                yield from sleep(30)
                inp.button(True)
                yield from sleep(20)
                inp.button(False)
                yield from sleep(80)
                if entry_focused():
                    ctx["top"] = top
                    return True
            return False

        def run():
            win.fullscreen()  # the content's origin is the screen's, so a pointer target is a widget's bounds
            yield from until(lambda: win.is_active() and term.get_width() > 0, 10000)
            yield from sleep(800)
            found = yield from find_top()
            print(
                "top bar offset:",
                ctx.get("top"),
                "found" if found else "NOT FOUND (clicks fall back to grab_focus)",
            )
            for condition in conditions:
                how = "grab" if ("grab" in condition or not found) else "click"
                guard = "guard" in condition
                focus_mode = "focus" in condition
                modes = FOCUS_ON if focus_mode else FOCUS_OFF
                modes += MOUSE_ON if "mouse" in condition else MOUSE_OFF
                modes += KITTY_ON if "kitty" in condition else KITTY_OFF
                modes += MOK_ON if "mok" in condition else MOK_OFF
                modes += PASTE_ON if "paste" in condition else PASTE_OFF
                client.request({"t": "paint", "pty": pty, "text": modes})
                yield from sleep(200)
                delivered = 0
                lost_first = 0
                lost_second = 0
                unfocused = 0
                for round_no in range(args.rounds):
                    # Focus elsewhere first.
                    yield from focus(entry, how)
                    yield from until(entry_focused, 2000)
                    if guard:
                        view.attach(pty)
                        yield from until(lambda: not view.guarded, 3000)
                    ctx["written"].clear()
                    # The click into the terminal, then one key, then a second.
                    yield from focus(term, how)
                    focused = yield from until(lambda: term.has_focus(), 2000)
                    if not focused:
                        unfocused += 1
                    inp.tap(ord("a") + (round_no % 26))
                    want = bytes([ord("a") + (round_no % 26)])
                    got = yield from until(lambda want=want: any(want in d for _, d in ctx["written"]), 1500)
                    if got:
                        delivered += 1
                    else:
                        lost_first += 1
                    inp.tap(ord("z"))
                    got2 = yield from until(lambda: any(b"z" in d for _, d in ctx["written"]), 1500)
                    if not got2:
                        lost_second += 1
                    yield from sleep(40)
                results[condition] = {
                    "how": how,
                    "rounds": args.rounds,
                    "first_delivered": delivered,
                    "first_lost": lost_first,
                    "second_lost": lost_second,
                    "unfocused": unfocused,
                    "guard_dropped": view.dropped_commits,
                }
                print(condition, results[condition], flush=True)
            inp.stop()
            app.quit()

        gen = run()

        def tick():
            try:
                delay = next(gen)
            except StopIteration:
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(delay, tick)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(300, tick)

    app.connect("activate", activate)
    GLib.timeout_add_seconds(600, lambda: os._exit(3))
    app.run([])
    print()
    print(
        f"{'condition':14} {'how':5} {'rounds':>6} {'first ok':>8} {'first lost':>10}"
        f" {'second lost':>11} {'no focus':>8}"
    )
    for name, r in results.items():
        print(
            f"{name:14} {r['how']:5} {r['rounds']:6d} {r['first_delivered']:8d} {r['first_lost']:10d}"
            f" {r['second_lost']:11d} {r['unfocused']:8d}"
        )
    loopback.shutdown()
    return 0 if results and all(r["first_lost"] == 0 for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
