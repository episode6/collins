# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The client's cache of blobs fetched over the API (split-service spec
§3.11, §3.23; PR-2.1, PR-2.2).

A blob — a git blob for the diff's images, a PR file, an image file on the
service's machine, an image off the web the service fetched, a project
icon — is `GET /api/blob?kind=…` on the service's socket
(`SocketLink.http_get`), never a path on the wire. `file_url`,
`remote_url`, `icon_url` and `image_url` name the PR-2.7 kinds, and
`fetch_image` is the fetcher every picture the lightbox, the attachments
gallery, the composer and the editor show goes through (the default
fetcher of `pictures.fetch`). `fetch(url)` is the
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

`read` is the one way a client module reads a fetched blob's bytes back
(the decoders: `animatedimage`, `pictures.thumbnail`, `imagediff`,
`remoteicons`): it refuses any path outside this cache, so what the client
decodes is always something the service sent, never a file of the
project's.

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
from urllib.parse import quote

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


# The file suffix a blob of each image content type is kept under, when the
# caller can't name one (an image off the web: `fetch(url, None)`).
TYPE_SUFFIXES = dict(remoteimages.CONTENT_TYPE_SUFFIXES)
_TYPED_SUFFIXES = tuple(sorted(set(TYPE_SUFFIXES.values()))) + (".img",)


def _q(value: str) -> str:
    return quote(value, safe="")


def file_url(path: str, session: str = "") -> str:
    """`GET /api/blob?kind=file` for the image file *path* on the service's
    machine, asked for the session *session* (its id, or its handle while
    unresolved: the service admits the paths that session's agent named,
    D38)."""
    url = "/api/blob?kind=file&path=" + _q(path)
    return url + "&session=" + _q(session) if session else url


def remote_url(url: str) -> str:
    """`GET /api/blob?kind=remote` for an http(s) image the service fetches."""
    return "/api/blob?kind=remote&url=" + _q(url)


def icon_url(root: str) -> str:
    """`GET /api/blob?kind=icon` for the project at *root*'s icon."""
    return "/api/blob?kind=icon&root=" + _q(root)


def image_url(key: str, session: str = "") -> str:
    """The blob URL of an image a surface names by *key*: an http(s) URL
    (`remote_url`) or an absolute path on the service's machine
    (`file_url`)."""
    if remoteimages.looks_remote(key):
        return remote_url(key)
    return file_url(key, session)


def image_suffix(key: str) -> str | None:
    """What `fetch_image` keeps *key*'s blob under: a path's own image
    suffix, or None (from the answer's type) for a URL."""
    if remoteimages.looks_remote(key):
        return None
    suffix = os.path.splitext(key)[1].lower()
    return suffix if len(suffix) <= 16 else ""


def fetch_image(key: str, session: str = "", link: apilink.Link | None = None) -> Path:
    """`fetch` of the image *key* names (`image_url`): the fetcher
    `pictures.fetch` uses by default. Worker thread; raises `ValueError`."""
    return fetch(image_url(key, session), image_suffix(key), link)


def read(path: str | Path) -> bytes | None:
    """The bytes of a blob `fetch` put in the cache, or None for a path
    outside it (the decoders' one read: the client decodes only what the
    service sent) or a file gone. Main loop or worker; never raises."""
    try:
        real = os.path.realpath(os.fspath(path))
        base = os.path.realpath(cache_root())
    except (OSError, TypeError, ValueError):
        return None
    if not real.startswith(base.rstrip(os.sep) + os.sep):
        log.warning("blobcache: refused to read %s, outside the cache", path)
        return None
    try:
        return Path(real).read_bytes()
    except OSError:
        return None


def fetch(url: str, suffix: str | None = "", link: apilink.Link | None = None) -> Path:
    """The file *url* (a `/api/blob?…` path and query) was fetched into,
    fresh or confirmed by a ``304``. *suffix* is kept on the file's name
    (an image's extension, for the lightbox and other apps); None takes
    it from the answer's content type (`TYPE_SUFFIXES`, ``.img`` for one
    it doesn't know), for an image off the web whose URL says nothing.
    Raises `ValueError` with the reason (what `pictures.fetch` hands the
    caller) when the service refuses or does not answer. Worker thread."""
    link = link or apilink.current()
    if link is None or not hasattr(link, "http_get"):
        raise ValueError(_("Not connected to the service"))
    folder = directory(str(getattr(link, "hello", {}).get("service_id") or "") or None)
    folder.mkdir(parents=True, exist_ok=True)
    prune(folder)
    key = key_for(url)
    etag_file = folder / (key + ".etag")
    if suffix is None:
        # The suffix is the answer's: the copy held now is whichever of
        # the typed names exists (one at most, the last answer's).
        target = folder / (key + ".img")
        for typed in _TYPED_SUFFIXES:
            if (folder / (key + typed)).exists():
                target = folder / (key + typed)
                break
    else:
        target = folder / (key + suffix[:16])
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
        # Kept as it is (the tag says the bytes are the same), its clock
        # restarted: the prune goes by mtime, so it reads "last used" — a
        # blob looked at daily is never pruned and fetched whole again.
        for held in (target, etag_file):
            try:
                os.utime(held)
            except OSError:
                pass
        return target
    if status != 200:
        raise ValueError(_reason(status))
    if len(data) > MAX_BYTES:
        raise ValueError(_("That file is too large to show."))
    if suffix is None:
        kind = (got.get("Content-Type") or "").split(";")[0].strip().lower()
        fresh = folder / (key + TYPE_SUFFIXES.get(kind, ".img"))
        if fresh != target:
            try:
                os.unlink(target)  # the old answer's copy, under its old type
            except OSError:
                pass
            target = fresh
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
    if status == 502:
        return _("The service couldn't fetch that image.")
    return _("The service answered {status}.").format(status=status)
