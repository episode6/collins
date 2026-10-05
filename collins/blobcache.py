# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The client's cache of blobs fetched over the API (split-service spec
§3.11, §3.23; PR-2.1, PR-2.2).

A blob — a git blob for the diff's images, a PR file, later a file or a
project icon — is `GET /api/blob?kind=…` on the service's socket
(`SocketLink.http_get`), never a path on the wire. `fetch(url)` is the
fetcher `pictures.fetch` takes: it GETs *url* with ``If-None-Match``
against the ETag kept from the last fetch, keeps the bytes under
``~/.cache/collins/blobs/<service id>/<sha1 of the url>`` (the ETag in a
sidecar), answers ``304`` from the file already there, and raises with
the reason when the blob can't be had — the stand-in's tooltip.

The directory is this device's own (the pathless allowlist names it), and
it is pruned on the 24-hour clock every fetched image has
(`remoteimages.prune_stale`, at most once per `PRUNE_EVERY_S` per
directory): a blob nobody fetched for a day goes with its tag, and the
next fetch of it is a full GET. The git page's image previews (`diffview`,
by `remotegit.blob_url`) and the PR page's (`prfileimages`, by the URL
`pr.blob` answers) fetch through it.

GTK-free: the file I/O is this module's, the HTTP is the link's.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from . import apilink, remoteimages
from .i18n import _

log = logging.getLogger(__name__)

# The most a blob may be: the server's own cap (gitops.MAX_BLOB_BYTES).
MAX_BYTES = 64 * 1024 * 1024
# How old a cached blob may get (remoteimages' clock: a fetched image of
# any kind lives a day), and how often one directory is swept for them.
PRUNE_AFTER_SECONDS = remoteimages.PRUNE_AFTER_SECONDS
PRUNE_EVERY_S = 10 * 60

_prune_lock = threading.Lock()
_pruned_at: dict[str, float] = {}


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
    prune(folder)
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
    try:
        status, got, data = link.http_get(url, headers)
    except Exception as err:  # the socket went away under the GET
        raise ValueError(str(err) or _("The service did not answer.")) from None
    if status == 304 and target.exists():
        return target  # kept as it is: the tag says the bytes are the same
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


def prune(folder: Path, now: float | None = None, force: bool = False) -> None:
    """Sweep *folder* of the blobs (and tags) older than
    PRUNE_AFTER_SECONDS — at most once per PRUNE_EVERY_S per directory,
    unless *force*. Never raises: housekeeping must not break the fetch
    that triggered it."""
    now = time.time() if now is None else now
    key = str(folder)
    with _prune_lock:
        last = _pruned_at.get(key)
        if not force and last is not None and 0 <= now - last < PRUNE_EVERY_S:
            return
        _pruned_at[key] = now
    remoteimages.prune_stale(folder, now=now)


def _reason(status: int) -> str:
    """The stand-in's words for a GET that did not answer 200 (§3.14:
    translated here, where they are shown)."""
    if status == 404:
        return _("No such file on this side.")
    if status == 413:
        return _("That file is too large to show.")
    if status == 403:
        return _("The service refused to read that file.")
    if status == 400:
        return _("Not a file the service can name.")
    return _("The service answered {status}.").format(status=status)
