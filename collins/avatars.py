# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Author avatars for the PR view, fetched from GitHub and cached for the run.

GitHub serves every account's picture at a stable login-derived URL
(``github.com/<login>.png``, the documented spelling), so no API call and no
extra fetch fields are needed — the login the PR data already carries is the
whole key. `avatar` returns an `Adw.Avatar` showing the login's colored
initial immediately; the picture lands on it when (and if) its download does.

One download per login per run, successes and failures both cached in
memory: a panel rebuild must not re-fetch a row of avatars, and a login
GitHub serves nothing for must not be asked again. Downloads run on daemon
threads and never touch GTK — the widgets are filled from an idle callback,
and both caches are only ever touched on the main loop.

Logins are repository content, i.e. untrusted: only a string matching
GitHub's own username alphabet may shape a URL (anything else — bot logins
with brackets, garbage — keeps its initials), the reply is byte-capped, and
its bytes only ever become a texture, never markup.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.request

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib  # noqa: E402

log = logging.getLogger(__name__)

# GitHub's username alphabet (it also bans leading/trailing/double hyphens,
# but a 404 on those is harmless — this only has to keep URLs sane).
_LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")
_URL = "https://github.com/{login}.png?size={px}"
# One fetch size for every widget, crisp on a hidpi screen at byline size.
_FETCH_PX = 128
_MAX_BYTES = 512 * 1024  # an avatar past this isn't one
_TIMEOUT_S = 10

_textures: dict[str, Gdk.Texture | None] = {}  # login → picture; None = failed
_waiting: dict[str, list[Adw.Avatar]] = {}  # fetch in flight → widgets to fill


def avatar(login: str, size: int) -> Adw.Avatar:
    """An avatar widget for *login*: initials at once, picture when fetched.

    Main thread only (it builds a widget and reads the caches). An empty
    login — gh didn't say who — shows Adwaita's generic person instead.
    """
    widget = Adw.Avatar(size=size, text=login or "", show_initials=bool(login))
    if not _LOGIN.match(login or ""):
        return widget
    if login in _textures:
        texture = _textures[login]
        if texture is not None:
            widget.set_custom_image(texture)
        return widget
    if login in _waiting:
        _waiting[login].append(widget)
        return widget
    _waiting[login] = [widget]
    threading.Thread(target=_fetch, args=(login,), name="pr-avatar", daemon=True).start()
    return widget


def _fetch(login: str) -> None:
    data = None
    try:
        request = urllib.request.Request(
            _URL.format(login=login, px=_FETCH_PX),
            headers={"User-Agent": "collins"},
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            data = response.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            data = None
    except Exception:  # offline, 404, TLS trouble — initials are fine
        log.debug("avatars: fetch for %s failed", login, exc_info=True)
    GLib.idle_add(_landed, login, data)


def _landed(login: str, data: bytes | None) -> bool:
    texture = None
    if data:
        try:
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            log.debug("avatars: %s sent bytes that aren't an image", login)
    _textures[login] = texture
    for widget in _waiting.pop(login, []):
        if texture is not None:
            widget.set_custom_image(texture)
    return GLib.SOURCE_REMOVE
