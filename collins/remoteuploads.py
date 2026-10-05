# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Drops and pastes onto the service's machine: the client's end of
`PUT /api/upload` (split-service spec §3.11, §3.23; PR-2.7).

A dropped or pasted image, and a dropped file, has to exist on the service
for the CLI to read it. `upload` sends bytes (`SocketLink.http_put`) and
answers the path the service wrote them to
(``~/.local/share/collins/uploads/<session id>/…``, or ``_pending/`` for a
composer with no session yet, D37), which is what gets mentioned. Two
callers' worth of shape on top:

- `upload_png(data, prefix, session, on_done)`: a texture's PNG bytes,
  named ``<prefix>-YYYYMMDD-HHMMSS.png`` (`dropimages.png_name`) — the
  terminal's drop and the composer's drop and paste;
- `upload_files(files, session, on_done)`: the dropped `Gio.File`s, each
  read on the worker (`load_bytes`, after a size check against
  `uploads.MAX_BYTES`) and sent under its own basename. A `local` client
  (`is_local`: the link proved it shares the service's machine) skips this
  and mentions a dropped file's own path, as it always did.

Every request runs on a daemon thread and *on_done* lands on the main loop
at `PRIORITY_DEFAULT`. The failure words are the user's (translated): the
caller says them to the terminal.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from urllib.parse import quote

from gi.repository import Gio, GLib

from . import apilink, dropimages, uploads
from .i18n import _

log = logging.getLogger(__name__)


def is_local() -> bool:
    """Whether this client proved it runs on the service's machine (a
    dropped file's own path names the same file there)."""
    return bool(getattr(apilink.current(), "local", False))


def upload(data: bytes, name: str, session: str | None = None, link: apilink.Link | None = None) -> str:
    """Send *data* as *name* for *session* (None: the pending directory)
    and return the path the service wrote. Raises `ValueError` with the
    user's words. Worker thread."""
    link = link or apilink.current()
    if link is None or not hasattr(link, "http_put"):
        raise ValueError(_("Not connected to the service"))
    if len(data) > uploads.MAX_BYTES:
        raise ValueError(_("That file is too large to send (over 64 MiB)."))
    query = "/api/upload?name=" + quote(name, safe="")
    if session:
        query += "&session=" + quote(session, safe="")
    try:
        status, _headers, body = link.http_put(query, data)
    except Exception as err:  # the socket went away under the PUT
        raise ValueError(str(err) or _("The service did not answer.")) from None
    if status == 413:
        raise ValueError(_("That file is too large to send (over 64 MiB)."))
    if status != 200:
        raise ValueError(_("The service refused the upload ({status}).").format(status=status))
    try:
        path = json.loads(body.decode("utf-8")).get("path")
    except (ValueError, AttributeError):
        path = None
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(_("The service answered the upload with no path."))
    return path


def _later(work: Callable[[], object], land: Callable[[object], None], name: str) -> None:
    def run() -> None:
        try:
            result = work()
        except Exception as err:  # every failure is a result the caller says
            log.debug("uploads: %s failed", name, exc_info=True)
            result = err if isinstance(err, ValueError) else ValueError(str(err))
        GLib.idle_add(lambda: land(result) and False, priority=GLib.PRIORITY_DEFAULT)

    threading.Thread(target=run, name=name, daemon=True).start()


def upload_png(
    data: bytes,
    prefix: str,
    session: str | None,
    on_done: Callable[[str | None, str | None], None],
) -> None:
    """Upload a texture's PNG bytes; *on_done(path, error)* on the main loop."""
    name = dropimages.png_name(prefix)

    def land(result) -> None:
        if isinstance(result, Exception):
            on_done(None, str(result))
        else:
            on_done(result, None)

    _later(lambda: upload(data, name, session), land, "upload-png")


def _read(gfile: Gio.File) -> tuple[bytes, str]:
    """A dropped file's bytes and basename (worker thread): refused when it
    is not a regular file or is past the cap."""
    info = gfile.query_info("standard::type,standard::size,standard::name", Gio.FileQueryInfoFlags.NONE, None)
    if info.get_file_type() != Gio.FileType.REGULAR:
        raise ValueError(_("{name} isn't a file").format(name=info.get_name()))
    if info.get_size() > uploads.MAX_BYTES:
        raise ValueError(_("That file is too large to send (over 64 MiB)."))
    data = gfile.load_bytes(None)[0].get_data() or b""
    return bytes(data), gfile.get_basename() or "dropped"


def upload_files(
    files: list[Gio.File],
    session: str | None,
    on_done: Callable[[list[str], list[str]], None],
) -> None:
    """Upload each dropped file under its own name; *on_done(paths,
    errors)* on the main loop once all of them are through, the paths in
    the drop's order."""

    def work() -> tuple[list[str], list[str]]:
        paths: list[str] = []
        errors: list[str] = []
        for gfile in files:
            try:
                data, name = _read(gfile)
                paths.append(upload(data, name, session))
            except GLib.Error as err:
                errors.append(err.message or str(err))
            except ValueError as err:
                errors.append(str(err))
        return paths, errors

    def land(result) -> None:
        if isinstance(result, Exception):
            on_done([], [str(result)])
        else:
            on_done(*result)

    _later(work, land, "upload-files")
