#!/usr/bin/env python3
"""Probes of the server backend's client glue against a real VTE and the
in-app loopback: what PR-1.7's review asked to see with its own eyes.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/probe_server_backend.py

1. A redraw the service sends on its own (flow control, no attach) lands on
   a reset screen and its sentinel's answer never reaches the pty.
2. The guard never stays up: an attach whose request fails lowers it at
   once; a redraw with no END frame is lowered by the watchdog.
3. A commit carrying NUL (Ctrl+Space) reaches the pty as one NUL byte.
4. The mouse coalescer under random SGR streams: every non-plain report
   and every byte of text comes out in order, and only plain motions are
   ever dropped.
5. Two attaches back to back: the first sentinel's answer does not lower
   the guard; the second's does.

A `cat` is behind every pty; no real `claude`, no quota. Exit 0 when every
probe passed.
"""

import os
import random
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ.setdefault("COLLINS_PTY_STATE_DIR", tempfile.mkdtemp(prefix="collins-probe-pty-"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gio, GLib, Gtk, Vte  # noqa: E402

from collins import mouserate, ptyclient, redrawguard  # noqa: E402
from collins.api import protocol  # noqa: E402
from collins.api.loopback import LoopbackServer  # noqa: E402
from collins.service.core import ServiceCore  # noqa: E402

PASSED = FAILED = 0


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}", flush=True)
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail!r}", flush=True)


def coalescer_fuzz(rounds=300):
    rng = random.Random(7)
    for _ in range(rounds):
        c = mouserate.MotionCoalescer()
        stream = []
        for _ in range(rng.randint(1, 40)):
            kind = rng.random()
            if kind < 0.5:
                code = 35 | rng.choice((0, 4, 8, 16))
                stream.append(b"\x1b[<%d;%d;%dM" % (code, rng.randint(1, 99), rng.randint(1, 40)))
            elif kind < 0.7:
                code = rng.choice((0, 2, 32, 34, 64, 65, 128, 160))
                final = rng.choice((b"M", b"m"))
                stream.append(b"\x1b[<%d;%d;%d%s" % (code, rng.randint(1, 99), rng.randint(1, 40), final))
            else:
                stream.append(bytes([rng.randint(0x20, 0x7E)]))
        joined = b"".join(stream)
        cuts = []
        if len(joined) > 2:
            cuts = sorted(rng.sample(range(1, len(joined)), min(len(joined) - 1, rng.randint(0, 5))))
        pieces = [joined[a:b] for a, b in zip([0, *cuts], [*cuts, len(joined)], strict=True)]
        out = b"".join(c.take(piece)[0] for piece in pieces) + c.flush()
        kept = re.findall(rb"\x1b\[<\d{1,5};\d{1,5};\d{1,5}[Mm]|[ -~]", out)

        def plain(item):
            if not item.startswith(b"\x1b[<") or not item.endswith(b"M"):
                return False
            return mouserate.is_plain_motion(int(item[3:].split(b";")[0]))

        wanted = [s for s in stream if not plain(s)]
        # Every non-plain item comes out in order; a plain motion may be
        # dropped or held, never reordered past a non-plain one.
        it = iter(kept)
        in_order = all(any(k == w for k in it) for w in wanted)
        if not in_order:
            return False, (stream, pieces, out)
        if len([k for k in kept if plain(k)]) > len(stream):
            return False, "more plain reports out than in"
    return True, ""


def main():
    core = ServiceCore()
    loopback = LoopbackServer(core)
    app = Gtk.Application(application_id="com.episode6.Collins.ProbeServerBackend",
        flags=Gio.ApplicationFlags.NON_UNIQUE)
    ok, detail = coalescer_fuzz()
    check("coalescer: order kept and nothing but plain motion ever dropped (300 random streams)", ok, detail)
    check("commit_bytes: a lone NUL", ptyclient.commit_bytes("", 1) == b"\x00")
    check("commit_bytes: a short read is None", ptyclient.commit_bytes("a", 3) is None)
    check("commit_bytes: plain text", ptyclient.commit_bytes("ab", 2) == b"ab")

    def activate(app):
        win = Gtk.ApplicationWindow(application=app, title="probe")
        win.set_default_size(1600, 1000)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        win.set_child(box)
        win.present()
        os.environ["SHELL"] = "/bin/cat"
        written = []
        real_write = core.ptys.write

        def spy(pty_id, data, sink=None):
            written.append(bytes(data))
            return real_write(pty_id, data, sink=sink)

        core.ptys.write = spy

        def make(client_factory=None):
            term = ptyclient.ClientVte()
            term.set_size(100, 30)
            term.set_halign(Gtk.Align.START)
            term.set_valign(Gtk.Align.START)
            box.append(term)
            holder = {}
            client = loopback.connect(
                lambda p, d, f: holder["view"].on_output(p, d, f),
                lambda e: holder["view"].on_event(e),
                device="probe",
            )
            holder["view"] = view = ptyclient.ClientTerminal(term, client, on_exited=lambda s: None)
            return term, client, view

        def until(pred, ms):
            deadline = time.monotonic() + ms / 1000
            while time.monotonic() < deadline:
                if pred():
                    return True
                yield 20
            return pred()

        def run():
            yield from until(lambda: win.is_active() or win.get_mapped(), 5000)
            yield 300

            # 1. a flow-control redraw: no attach, the service redraws on its own
            term, client, view = make()
            pty = client.request({"t": "spawn", "kind": "shell",
                "cwd": "/tmp", "cols": 100, "rows": 30})["pty"]
            view.attach(pty)
            yield from until(lambda: not view.guarded, 3000)
            check("1. the attach's guard came down", not view.guarded)
            client.send_input(pty, b"before\r")

            def shown(text):
                got = term.get_text_range_format(Vte.Format.TEXT, 0, 0, 5, 100)
                got = got[0] if isinstance(got, tuple) else got
                return text in (got or "")

            yield from until(lambda: shown("before"), 3000)
            written.clear()
            dropped = view.dropped_commits
            sink = client.sink_for(pty)
            core.ptys._redraw(core.ptys.get(pty), core.ptys.get(pty).attachments[id(sink)])
            yield from until(lambda: not view.guarded, 3000)
            check("1. the unannounced redraw raised and lowered the guard",
                not view.guarded and view.dropped_commits > dropped,
                (view.guarded, view.dropped_commits - dropped),
            )
            check("1. its sentinel answer never reached the pty",
                not any(b"\x1b]4;" in w for w in written), written)
            client.send_input(pty, b"after\r")
            yield from until(lambda: any(w == b"after\r" for w in written), 2000)
            check("1. typing after it reaches the pty", any(w == b"after\r" for w in written))

            # 5. two attaches back to back
            written.clear()
            view.attach(pty)
            view.attach(pty)
            gen = view.guard.generation
            yield from until(lambda: not view.guarded, 3000)
            check("5. two attaches: one guard, lowered once by the latest sentinel",
                not view.guarded and view.guard.expired == 0, (gen, view.guard.expired))
            check("5. no sentinel answer reached the pty", not any(b"\x1b]4;" in w for w in written), written)

            # 2. the guard never stays up
            class Refusing:
                device = "x"

                def __init__(self, inner):
                    self.inner = inner

                def __getattr__(self, name):
                    return getattr(self.inner, name)

                def request(self, message):
                    if message.get("t") == "attach":
                        raise RuntimeError("no")
                    return self.inner.request(message)

            term2, client2, view2 = make()
            view2.client = Refusing(client2)
            try:
                view2.attach(pty)
            except RuntimeError:
                pass
            check("2. a failed attach lowers the guard at once", not view2.guarded)
            view2.client = client2
            # a redraw with no END frame: only the watchdog can lower it
            view2.pty = pty
            view2.on_output(pty, b"\x1b[0m\x1b[1;1H", protocol.FLAG_REDRAW)
            check("2. an endless redraw raises the guard", view2.guarded)
            t0 = time.monotonic()
            yield from until(lambda: not view2.guarded, redrawguard.WATCHDOG_MS + 1500)
            elapsed = time.monotonic() - t0
            check("2. the watchdog lowered it after about 2 s",
                not view2.guarded and 1.5 < elapsed < 3.5 and view2.guard.expired == 1, (elapsed, view2.guard.expired))

            # 3. NUL through a real key event
            term.grab_focus()
            yield from until(term.has_focus, 2000)
            written.clear()
            controller = Gtk.EventControllerKey()
            # Synthesize Ctrl+Space the way VTE sees it: through its own IM/key path.
            # GTK4 cannot inject key events; feed the commit VTE would make.
            term.emit("commit", "", 1)
            yield from until(lambda: b"\x00" in b"".join(written), 2000)
            check("3. a NUL commit reaches the pty as one NUL byte",
                any(w == b"\x00" for w in written), written)
            del controller

            client.request({"t": "close", "pty": pty, "mode": "kill"})
            yield 300
            app.quit()

        gen_ = run()

        def tick():
            try:
                delay = next(gen_)
            except StopIteration:
                return GLib.SOURCE_REMOVE
            except Exception as exc:  # noqa: BLE001
                check("the probe ran to its end", False, repr(exc))
                app.quit()
                return GLib.SOURCE_REMOVE
            GLib.timeout_add(delay, tick)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(300, tick)

    app.connect("activate", activate)
    GLib.timeout_add_seconds(60, lambda: os._exit(3))
    app.run([])
    loopback.shutdown()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
