"""A Gtk.Window with a Vte.Terminal spawning $SHELL, on the macOS backend.

Spec hardware-only item 1. Feeds the shell an echo, reads the screen back,
reports the GSK renderer GTK actually picked and the font Pango resolved for
"Monospace", then takes a `screencapture` of the whole screen (the window is
placed at the top-left so it is inside the shot).

    python3 check_vte_window.py <out-dir> <label>

Run once per renderer by the workflow: plain, and with GSK_RENDERER=cairo.
"""

import os
import shutil
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo, Vte  # noqa: E402

MARKER = "SPIKE-" + str(6 * 7)
TIMEOUT_S = 25


def _font_report() -> str:
    fontmap = PangoCairo.FontMap.get_default()
    ctx = fontmap.create_context()
    desc = Pango.FontDescription.from_string("Monospace 12")
    font = fontmap.load_font(ctx, desc)
    if font is None:
        return "Monospace -> (no font)"
    return f"Monospace -> {font.describe().to_string()} (fontmap {type(fontmap).__name__})"


class Spike:
    def __init__(self, out_dir: str, label: str):
        self.out_dir = out_dir
        self.label = label
        self.loop = GLib.MainLoop()
        self.result: dict = {"label": label, "gsk_env": os.environ.get("GSK_RENDERER", "")}
        self.child_pid = None

    def run(self) -> int:
        Gtk.init()
        self.win = Gtk.Window(title=f"collins spike {self.label}")
        self.win.set_default_size(900, 400)
        self.term = Vte.Terminal()
        self.term.set_size(100, 24)
        self.win.set_child(self.term)
        self.win.connect("close-request", lambda *_: self.loop.quit())
        self.term.connect("child-exited", self._on_child_exited)
        self.win.present()
        GLib.timeout_add(500, self._after_present)
        GLib.timeout_add(TIMEOUT_S * 1000, self._timeout)
        self.loop.run()
        return self._report()

    def _after_present(self):
        try:
            native = self.win.get_native()
            renderer = native.get_renderer() if native else None
            self.result["renderer"] = type(renderer).__name__ if renderer else "(none)"
            surface = native.get_surface() if native else None
            self.result["surface"] = type(surface).__name__ if surface else "(none)"
            self.result["surface_size"] = f"{surface.get_width()}x{surface.get_height()}" if surface else "?"
            self.result["display"] = type(self.win.get_display()).__name__
        except Exception as exc:  # noqa: BLE001
            self.result["renderer_error"] = repr(exc)
        try:
            self.result["font"] = _font_report()
        except Exception as exc:  # noqa: BLE001
            self.result["font"] = f"error {exc!r}"
        shell = os.environ.get("SHELL") or "/bin/zsh"
        self.result["shell"] = shell
        self.term.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.getcwd(),
            [shell, "-il"],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            self._on_spawned,
        )
        return False

    def _on_spawned(self, terminal, pid, error):
        if error is not None:
            self.result["spawn_error"] = str(error)
            self.loop.quit()
            return
        self.child_pid = pid
        self.result["child_pid"] = pid
        # Give the shell a moment to print its prompt, then type.
        GLib.timeout_add(1500, self._type)

    def _type(self):
        self.term.feed_child(f"echo {MARKER[:6]}$((6*7))\n".encode())
        GLib.timeout_add(2500, self._read)
        return False

    def _read(self):
        text = None
        try:
            text = self.term.get_text_format(Vte.Format.TEXT)
        except Exception:  # noqa: BLE001 - older API shape
            try:
                got = self.term.get_text(None)
                text = got[0] if isinstance(got, tuple) else got
            except Exception as exc:  # noqa: BLE001
                self.result["read_error"] = repr(exc)
        self.result["screen"] = text or ""
        # The marker must appear as command output, i.e. at least twice
        # (once echoed as typed, once printed) -- the typed line is
        # `echo SPIKE-$((6*7))`, so only the output line carries "SPIKE-42".
        self.result["marker_seen"] = MARKER in (text or "")
        self._screencapture()
        try:
            self.term.feed_child(b"exit\n")
        except Exception:  # noqa: BLE001
            pass
        GLib.timeout_add(1500, self.loop.quit)
        return False

    def _screencapture(self):
        png = os.path.join(self.out_dir, f"vte-{self.label}.png")
        if not shutil.which("screencapture"):
            self.result["screencapture"] = "screencapture not on PATH"
            return
        proc = subprocess.run(["screencapture", "-x", "-t", "png", png], capture_output=True, text=True)
        size = os.path.getsize(png) if os.path.exists(png) else 0
        self.result["screencapture"] = f"rc={proc.returncode} bytes={size} stderr={proc.stderr.strip()!r}"

    def _on_child_exited(self, terminal, status):
        self.result["child_exit_status"] = status

    def _timeout(self):
        self.result["timed_out"] = True
        self.loop.quit()
        return False

    def _report(self) -> int:
        r = self.result
        screen = r.pop("screen", "")
        print("---- screen text ----")
        print(screen.rstrip())
        print("---- end screen ----")
        for k, v in r.items():
            print(f"{k}: {v}")
        ok = r.get("marker_seen") and not r.get("spawn_error") and not r.get("timed_out")
        print(
            f"RESULT vte_window label={r['label']} renderer={r.get('renderer')} "
            f"marker_seen={r.get('marker_seen')} font={r.get('font')!r} "
            f"screencapture={r.get('screencapture')!r}"
        )
        print(f"{'PASS' if ok else 'FAIL'} vte_window {r['label']}")
        return 0 if ok else 1


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    label = sys.argv[2] if len(sys.argv) > 2 else "default"
    os.makedirs(out_dir, exist_ok=True)
    started = time.monotonic()
    rc = Spike(out_dir, label).run()
    print(f"elapsed: {time.monotonic() - started:.1f}s")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
