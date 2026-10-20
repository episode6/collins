# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The client's cache of blobs fetched over the API (split-service spec
§3.11, §3.23; PR-2.1).

A blob — a git blob for the diff's images, later a file, a PR file, a
project icon — is `GET /api/blob?kind=…` on the service's socket
(`SocketLink.http_get`), never a path on the wire. `fetch(url)` is the
fetcher `pictures.fetch` takes: it GETs *url* with ``If-None-Match``
against the ETag kept from the last fetch, keeps the bytes under
``~/.cache/collins/blobs/<service id>/<sha1 of the url>`` (the ETag in a
sidecar), answers ``304`` from the file already there, and raises with
the reason when the blob can't be had — the stand-in's tooltip. The
directory is this device's own (the pathless allowlist names it); PR-2.2
adds the prune and moves the diff's images and `pr.blob` onto it.

GTK-free: the file I/O is this module's, the HTTP is the link's.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from . import apilink
from .i18n import _

log = logging.getLogger(__name__)

# The most a blob may be: the server's own cap (gitops.MAX_BLOB_BYTES).
MAX_BYTES = 64 * 1024 * 1024


def cache_root() -> Path:
    """`~/.cache/collins/blobs` (XDG_CACHE_HOME honoured, as every cache
    directory of the app is)."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "collins" / "blobs"


def directory(service_id: str | None = None) -> Path:
    """The cache for the connected service (its hello's `service_id`;
    "service" with no link or no id)."""
    if service_id is None:
        link = apilink.current()
        service_id = str(getattr(link, "hello", {}).get("service_id") or "") or "service"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in service_id)[:128] or "service"
    return cache_root() / safe


def key_for(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()


def fetch(url: str, suffix: str = "", link: apilink.Link | None = None) -> Path:
    """The file *url* (a `/api/blob?…` path and query) was fetched into,
    fresh or confirmed by a ``304``. *suffix* is kept on the file's name
    (an image's extension, for the lightbox and other apps). Raises
    `ValueError` with the reason (what `pictures.fetch` hands the caller)
    when the service refuses or does not answer. Worker thread."""
    link = link or apilink.current()
    if link is None or not hasattr(link, "http_get"):
        raise ValueError(_("Not connected to the service"))
    folder = directory(str(getattr(link, "hello", {}).get("service_id") or "") or None)
    folder.mkdir(parents=True, exist_ok=True)
    key = key_for(url)
    target = folder / (key + suffix[:16])
    etag_file = folder / (key + ".etag")
    headers: dict[str, str] = {}
    try:
        etag = etag_file.read_text(encoding="utf-8").strip()
    except OSError:
        etag = ""
    if etag and target.exists():
        headers["If-None-Match"] = etag
    status, got, data = link.http_get(url, headers)
    if status == 304 and target.exists():
        return target
    if status != 200:
        raise ValueError(_reason(status))
    if len(data) > MAX_BYTES:
        raise ValueError(_("That file is too large to show."))
    # A temporary of this fetch's own (two threads fetching one URL each
    # write their own and the last rename wins whole), then one rename.
    handle, tmp_name = tempfile.mkstemp(prefix=key[:12] + ".", suffix=".part", dir=str(folder))
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(tmp_name, target)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    new_tag = got.get("ETag")
    if new_tag:
        etag_file.write_text(new_tag, encoding="utf-8")
    else:
        try:
            etag_file.unlink()
        except OSError:
            pass
    return target


def _reason(status: int) -> str:
    """The stand-in's words for a GET that did not answer 200 (§3.14:
    translated here, where they are shown)."""
    if status == 404:
        return _("No such file on this side.")
    if status == 413:
        return _("That file is too large to show.")
    if status == 403:
        return _("The service refused to read that file.")
    return _("The service answered {status}.").format(status=status)
