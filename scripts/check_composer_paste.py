#!/usr/bin/env python3
"""Behaviour check for pasting an image (or copied files) into the composer.

GTK's text view pastes text and nothing else, so an image on the clipboard —
a screenshot tool's copy, a browser's "Copy image" — used to paste nothing.
ComposerView._on_paste takes such a paste over: the image is uploaded as a
PNG to the service (a `paste-…` file in its uploads directory; with no
session behind this bare view, the pending one: PR-2.7, D37) and its mention
typed at the cursor with a thumbnail fetched back as a blob, files copied
from a file manager are mentioned in place (the harness's link is `local`),
and plain text is left to the view exactly as before. None of that is reachable from the unit tests: the decision is
made on a real Gdk.Clipboard's formats and the data comes back through its
async reads, which need a display and a main loop.

The clipboard is the display's own; every case sets it and then fires the
view's paste-clipboard signal, the one every paste gesture arrives as.
The XDG directories are pointed at a scratch tree first so the uploads and
the blob cache land there and nowhere near the user's own, and a scratch
service is started for the uploads (`e2e_service.harness_link`).

    python3 scripts/check_composer_paste.py
"""

import os
import sys
import tempfile
import threading
import time

_SCRATCH = tempfile.mkdtemp(prefix="collins-e2e-paste-")
_RUN = "r" + "".join(c for c in os.path.basename(_SCRATCH) if c.isalnum())
os.environ["XDG_CACHE_HOME"] = os.path.join(_SCRATCH, "cache")
os.environ["XDG_DATA_HOME"] = os.path.join(_SCRATCH, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(_SCRATCH, "state")
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{_RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = os.path.join(_SCRATCH, "projects")
os.environ["COLLINS_CLAUDE_CONFIG"] = os.path.join(_SCRATCH, "claude.json")
os.environ["COLLINS_CHATS_DIR"] = os.path.join(_SCRATCH, "chats")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

import e2e_service  # noqa: E402

import collins.composer as composer_mod  # noqa: E402
from collins import fileclipboard, uploads  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, condition: bool) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}")


def pump(ms: int = 400) -> None:
    """Spin the main loop: the clipboard reads land from it."""
    context = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.002)


def red_square(size: int = 4) -> Gdk.Texture:
    pixels = GLib.Bytes.new(b"\xff\x00\x00\xff" * (size * size))
    return Gdk.MemoryTexture.new(size, size, Gdk.MemoryFormat.R8G8B8A8, pixels, size * 4)


def claim(clipboard: Gdk.Clipboard, provider: Gdk.ContentProvider) -> None:
    """Put *provider* on the clipboard and wait until it is really ours.

    On Wayland a client may only set the selection off a recent input
    serial, so a set_content issued before the compositor has handed the
    window its focus is dropped on the floor, silently — and it is a race
    which of those lands first after present(). Retried a few main-loop
    turns until is_local() says the claim took; the assertion fires rather
    than letting every case below fail on a clipboard that holds nothing.
    """
    for _attempt in range(100):
        clipboard.set_content(provider)
        pump(50)
        if clipboard.is_local():
            return
    raise AssertionError("the display never let this process claim the clipboard")


def set_texture(clipboard: Gdk.Clipboard, texture: Gdk.Texture) -> None:
    """What gdk_clipboard_set_texture does; that convenience (and set_text's)
    isn't in the GIR, so the provider is built by hand (fileclipboard's
    pattern)."""
    claim(clipboard, Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.Texture, texture)))


def set_text(clipboard: Gdk.Clipboard, text: str) -> None:
    claim(clipboard, Gdk.ContentProvider.new_for_value(GObject.Value(str, text)))


def set_files(clipboard: Gdk.Clipboard, paths: list[str]) -> None:
    """fileclipboard.set_files, with the same wait for the claim to take."""
    fileclipboard.set_files(clipboard, paths)
    for _attempt in range(100):
        if clipboard.is_local():
            return
        pump(50)
        fileclipboard.set_files(clipboard, paths)
    raise AssertionError("the display never let this process claim the clipboard")


def new_view(reference=lambda path: f"@{path}", notes=None):
    view = composer_mod.ComposerView(
        pick_attach=lambda: None,
        file_reference=reference,
        notify=(notes.append if notes is not None else lambda message: None),
    )
    window = Gtk.Window()
    window.set_default_size(600, 300)
    window.set_child(view)
    window.present()
    pump(300)
    return view, window


def paste(view) -> None:
    view._view.emit("paste-clipboard")
    pump(500)
    # The upload and the preview's blob run on threads and land on the
    # main loop: wait them out.
    e2e_service.wait_until(
        lambda: not any(t.name.startswith(("upload", "image-fetch")) for t in threading.enumerate()), 10
    )
    pump(200)


def saved_copies() -> list[str]:
    directory = uploads.directory(None)
    return sorted(os.listdir(directory)) if directory.is_dir() else []


def main() -> int:
    Adw.init()
    e2e_service.harness_link()
    clipboard = Gdk.Display.get_default().get_clipboard()

    # -- an image pastes as a saved copy, mentioned and thumbnailed ----------
    notes = []
    view, window = new_view(notes=notes)
    set_texture(clipboard, red_square())
    paste(view)
    copies = saved_copies()
    check("one copy uploaded to the service's pending uploads", len(copies) == 1)
    name = copies[0] if copies else ""
    check("…named as a paste, as a PNG", name.startswith("paste-") and name.endswith(".png"))
    path = str(uploads.directory(None) / name)
    check("the mention names the copy, with a trailing space", view.peek_text() == f"@{path} ")
    try:
        texture = Gdk.Texture.new_from_filename(path)
    except GLib.Error:
        texture = None
    check("…and the copy decodes as the pasted image", texture is not None and texture.get_width() == 4)
    check("a thumbnail appears in the strip", view._thumb_scroller.get_visible())
    check("nothing was reported", notes == [])

    # -- it keeps its distance from a half-written word ---------------------
    view.set_text("look at")
    paste(view)
    check(
        "pasted after a word, the mention gets a space in front",
        view.peek_text().startswith("look at @"),
    )
    check("…and a second copy was saved", len(saved_copies()) == 2)
    window.close()

    # -- plain text still pastes as text ------------------------------------
    view, window = new_view()
    set_text(clipboard, "hello there")
    paste(view)
    check("text on the clipboard pastes as text", view.peek_text() == "hello there")
    check("…with no copy saved", len(saved_copies()) == 2)
    check("…and no thumbnail", not view._thumb_scroller.get_visible())
    window.close()

    # -- copied files are mentioned in place ----------------------------------
    view, window = new_view()
    copied = os.path.join(_SCRATCH, "notes.txt")
    with open(copied, "w") as fh:
        fh.write("x")
    set_files(clipboard, [copied])
    paste(view)
    check("a copied file pastes as its mention", view.peek_text() == f"@{copied} ")
    check("…with no copy saved", len(saved_copies()) == 2)
    check("…and no thumbnail for a text file", not view._thumb_scroller.get_visible())
    window.close()

    # -- a list with something remote on it is thinned out loud ---------------
    # The drop target counts what it skips; a paste of the same list must
    # too, or a copy taken in a file manager showing a remote share would
    # quietly lose entries.
    notes = []
    view, window = new_view(notes=notes)
    mixed = Gdk.FileList.new_from_list(
        [Gio.File.new_for_path(copied), Gio.File.new_for_uri("https://example.test/far.png")]
    )
    claim(clipboard, Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, mixed)))
    paste(view)
    check("a mixed list mentions the local file", view.peek_text() == f"@{copied} ")
    check("…and reports the remote one skipped", notes == ["skipped 1 item that isn't a local file"])
    # The drop target's own delivery of that list lands on the same code,
    # so it must say the same thing.
    view.set_text("")
    notes.clear()
    view._on_drop(None, mixed, 0.0, 0.0)
    check("a drop of the same list mentions the same file", view.peek_text() == f"@{copied} ")
    check("…and reports the same skip", notes == ["skipped 1 item that isn't a local file"])
    window.close()

    # -- without a mention syntax the view keeps its own paste ----------------
    view, window = new_view(reference=lambda path: None)
    set_texture(clipboard, red_square())
    paste(view)
    check("no mention syntax: an image pastes nothing", view.peek_text() == "")
    check("…and saves nothing", len(saved_copies()) == 2)
    set_text(clipboard, "still text")
    paste(view)
    check("…while text still pastes", view.peek_text() == "still text")
    window.close()

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
