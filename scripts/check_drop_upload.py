#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Behaviour check for drops, uploads and pictures over the API (PR-2.7,
split-service spec §3.11, §3.23; D37, D38).

A scratch service (`e2e_service`) and two links to it: one that proved it
is `local` (the app's own, on the service's machine) and one that never
did (what a client over ssh is). Against a bare ComposerView and a window
with a lightbox overlay:

- a texture dropped on the composer is uploaded (`PUT /api/upload`) into
  the service's pending uploads (the bare view has no session: D37), the
  mention names the upload, and the thumbnail fills from the blob GET;
- a file dropped by the client that isn't `local` is uploaded under its
  basename and the mention names the upload, never the client's path; the
  `local` client's mention names the dropped file itself and nothing is
  uploaded;
- the lightbox fetches a path through `kind=file`: refused (its stand-in)
  to the client that isn't local for a file outside every root and no
  session's (D38; what the session's agent named is admitted, which the
  unit tests pin), decoded for the local one;
- show_image's remote half: an http(s) image (a local server here) is
  fetched *by the service* (`kind=remote`) into its cache and decoded in
  the lightbox, the client never opening the URL itself.

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_drop_upload.py
"""

import http.server
import os
import sys
import tempfile
import threading
import time

_SCRATCH = tempfile.mkdtemp(prefix="collins-e2e-drop-")
_RUN = "r" + "".join(c for c in os.path.basename(_SCRATCH) if c.isalnum())
os.environ["XDG_CACHE_HOME"] = os.path.join(_SCRATCH, "cache")
os.environ["XDG_DATA_HOME"] = os.path.join(_SCRATCH, "data")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SCRATCH, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(_SCRATCH, "state")
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{_RUN}"
os.environ["COLLINS_PROJECTS_DIR"] = os.path.join(_SCRATCH, "projects")
os.environ["COLLINS_CLAUDE_CONFIG"] = os.path.join(_SCRATCH, "claude.json")
os.environ["COLLINS_CHATS_DIR"] = os.path.join(_SCRATCH, "chats")
os.environ["COLLINS_DEBUG_API"] = "1"
# The service's fetch of the local image server must not go through a proxy.
for _proxy in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_proxy, None)
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "*"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import e2e_service  # noqa: E402
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

import collins.composer as composer_mod  # noqa: E402
from collins import apilink, blobcache, lightbox, remoteimages, uploads  # noqa: E402
from collins.api import server as api_server  # noqa: E402
from collins.api.client import SocketLink  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, condition: bool, detail=None) -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"  FAIL  {label}" + (f": {detail}" if detail is not None else ""))


def pump(ms: int = 200) -> None:
    context = GLib.MainContext.default()
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        while context.pending():
            context.iteration(False)
        time.sleep(0.002)


def settle() -> None:
    """Wait out the uploads and the blob fetches (threads), then their
    landings on the main loop."""
    e2e_service.wait_until(
        lambda: not any(
            t.name.startswith(("upload", "image-fetch")) for t in threading.enumerate()
        ),
        15,
    )
    pump(300)


def png_file(path: str, rgb: int, size: int = 6) -> str:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, size, size)
    pixbuf.fill(rgb)
    pixbuf.savev(path, "png", [], [])
    return path


def red_square(size: int = 4) -> Gdk.Texture:
    pixels = GLib.Bytes.new(b"\xff\x00\x00\xff" * (size * size))
    return Gdk.MemoryTexture.new(size, size, Gdk.MemoryFormat.R8G8B8A8, pixels, size * 4)


class LightboxWindow(Gtk.Window):
    """A window with the overlay the lightbox floats in (MainWindow's)."""

    def __init__(self) -> None:
        super().__init__()
        self.set_default_size(900, 700)
        self.lightbox_overlay = Gtk.Overlay(child=Gtk.Box())
        self.set_child(self.lightbox_overlay)


def new_composer(notes):
    view = composer_mod.ComposerView(
        pick_attach=lambda: None,
        file_reference=lambda path: f"@{path}",
        notify=notes.append,
    )
    window = Gtk.Window()
    window.set_default_size(600, 300)
    window.set_child(view)
    window.present()
    pump(300)
    return view, window


def thumbs(view) -> list:
    found = []
    child = view._thumb_box.get_first_child()
    while child is not None:
        found.append(child.get_child())  # the overlay's picture
        child = child.get_next_sibling()
    return found


def pending() -> list[str]:
    folder = uploads.directory(None)
    return sorted(os.listdir(folder)) if folder.is_dir() else []


def show(window, key: str, session: str = "") -> dict:
    result = {}
    lightbox.show_image(window, key, session=session, on_shown=lambda ok, why: result.update(ok=ok, why=why))
    e2e_service.wait_until(lambda: "ok" in result, 15)
    pump(200)
    return result


class _Images(http.server.BaseHTTPRequestHandler):
    body = b""
    hits = 0

    def do_GET(self):  # noqa: N802 - the stdlib's name
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_args):
        pass


def main() -> int:
    Adw.init()
    local = e2e_service.harness_link(device="local")
    app_id = os.environ["COLLINS_APP_ID"]
    remote = SocketLink(api_server.socket_path(app_id), app_id="", device="remote")
    remote.connect()  # never proves `local`: a client over ssh
    check("the harness's link is local and the other is not", local.local and not remote.local)

    # -- a texture dropped on the composer: an upload, its mention, its thumb ------
    apilink.set_current(remote)
    notes: list[str] = []
    view, window = new_composer(notes)
    view._on_drop(None, red_square(), 0.0, 0.0)
    settle()
    names = pending()
    check("a dropped texture is uploaded to the service's pending uploads", len(names) == 1, names)
    name = names[0] if names else ""
    upload = str(uploads.directory(None) / name)
    check("…named as a drop, as a PNG", name.startswith("drop-") and name.endswith(".png"), name)
    check("the mention names the upload", view.peek_text() == f"@{upload} ", view.peek_text())
    pictures_ = thumbs(view)
    check(
        "the thumbnail filled from the blob GET",
        len(pictures_) == 1 and pictures_[0].get_paintable() is not None,
    )
    check("…and nothing was reported", notes == [], notes)
    window.close()

    # -- a dropped file: uploaded for a remote client, its own path for a local one --
    original = png_file(os.path.join(_SCRATCH, "my shot.png"), 0x3366CCFF)
    view, window = new_composer(notes)
    files = Gdk.FileList.new_from_list([Gio.File.new_for_path(original)])
    view._on_drop(None, files, 0.0, 0.0)
    settle()
    sent = str(uploads.directory(None) / "my shot.png")
    check("a remote client's dropped file is uploaded under its basename", os.path.isfile(sent), pending())
    check(
        "…and the mention names the upload, not the client's path",
        view.peek_text() == f"@{sent} ",
        view.peek_text(),
    )
    with open(sent, "rb") as fh, open(original, "rb") as theirs:
        check("…byte for byte", fh.read() == theirs.read())
    window.close()

    apilink.set_current(local)
    before = pending()
    view, window = new_composer(notes)
    view._on_drop(None, files, 0.0, 0.0)
    settle()
    check(
        "a local client's dropped file is mentioned by its own path",
        view.peek_text() == f"@{original} ",
        view.peek_text(),
    )
    check("…and nothing is uploaded", pending() == before, pending())
    pictures_ = thumbs(view)
    check(
        "…its thumbnail fetched as a blob all the same",
        len(pictures_) == 1 and pictures_[0].get_paintable() is not None,
    )
    window.close()

    # -- the lightbox over kind=file -------------------------------------------------
    outside = png_file(os.path.join(_SCRATCH, "outside.png"), 0xCC3333FF, 40)
    lw = LightboxWindow()
    lw.present()
    pump(300)
    apilink.set_current(remote)
    got = show(lw, outside, session="sid-e2e")
    box = getattr(lw.lightbox_overlay, "_active_lightbox", None)
    check(
        "a remote client is refused a file outside every root (the stand-in)",
        got.get("ok") is False and box is not None and not box.decoded,
        got,
    )
    apilink.set_current(local)
    got = show(lw, outside)
    box = getattr(lw.lightbox_overlay, "_active_lightbox", None)
    check("the local client sees it", got.get("ok") is True and box is not None and box.decoded, got)
    check("…from a copy in the blob cache", str(box._path).startswith(str(blobcache.cache_root())), box._path)
    check("…and Open With hands over the service's own file (same machine)", box._open_with == outside)
    box.close()
    apilink.set_current(remote)

    # -- show_image's remote half: the service downloads -----------------------------
    image = png_file(os.path.join(_SCRATCH, "served.png"), 0x33AA33FF, 30)
    with open(image, "rb") as fh:
        _Images.body = fh.read()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Images)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/plot"
    got = show(lw, url)
    box = getattr(lw.lightbox_overlay, "_active_lightbox", None)
    check(
        "a remote image shows in the remote client's lightbox",
        got.get("ok") is True and box is not None and box.decoded,
        got,
    )
    check("…fetched by the service, once", _Images.hits == 1, _Images.hits)
    served = remoteimages.default_directory()
    held = sorted(os.listdir(served)) if served.is_dir() else []
    check("…into the service's own cache", len(held) == 1 and held[0].endswith(".png"), held)
    check("…and the client's copy is named by its type", str(box._path).endswith(".png"), box._path)
    check("…with no Open With of the URL itself", box._open_with == str(box._path))
    box.close()
    got = show(lw, url)
    check(
        "a second look is the service's cache (no second download)",
        got.get("ok") is True and _Images.hits == 1,
        _Images.hits,
    )
    server.shutdown()
    lw.close()

    remote.close()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
