# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Fetching an image a session named by URL, so `show_image` can take one.

An agent that already has a picture on the web — a CI artifact, a chart it
just published, a rendered doc — shouldn't have to spend a `curl` and a temp
file before Collins can put it on screen. `app._mcp_show_image` sends any
`http(s)` argument here instead of resolving it as a path: the bytes land in
the cache directory and the lightbox shows *that* file, so everything
downstream of it (the decode, "Open With…", the failure status page) keeps
working on a plain path.

Kept GTK-free and stdlib-only (urllib, not libsoup) so the whole fetch —
redirects, the size cap, the content-type gate — is exercised headless
against a local server in `tests/test_remoteimages.py`, the way the rest of
the MCP plumbing is; and so a small feature adds no runtime typelib the app
doesn't already need.

The fetch blocks, so app.py runs it on a worker thread and defers the
session's reply (`mcptools.DeferredResult`) rather than stalling the main
loop. Every bound here sits under the shim's 15s call timeout
(`mcp_shim._CALL_TIMEOUT`): a fetch the agent has already given up waiting
for is one nobody wants finishing.

Only the GET goes out — no cookies, no credentials, no headers of the
user's, and redirects are followed only to http(s). Local and private
addresses are deliberately *not* blocked: a plot served by a dev server on
localhost is a real case, and the calling agent shares this machine's
network anyway, so refusing them would cost a genuine use without denying
anything the caller couldn't already reach.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from . import __version__, editorfiles
from .dropimages import cache_directory, save_copy

# The whole fetch, connect included. The shim gives a tool call 15s
# (mcp_shim._CALL_TIMEOUT); staying well under it means the agent hears the
# failure from us, with a reason, instead of hearing a timeout.
TIMEOUT_SECONDS = 10.0
_SOCKET_TIMEOUT = 5.0  # per socket operation; TIMEOUT_SECONDS caps the sum
_CHUNK_BYTES = 64 * 1024
# Smaller than editorfiles' 50 MB viewer cap: that one only has to be
# decodable, this one also has to arrive inside TIMEOUT_SECONDS.
MAX_BYTES = 25 * 1024 * 1024
_MAX_REDIRECTS = 5
# Downloads are view artifacts, not the user's files: a day is enough for
# "Open With…" and a second look, and the next fetch clears the rest. (The
# dropped-image cache next door keeps a week — those get *mentioned* in a
# prompt that may not be submitted until much later.)
PRUNE_AFTER_SECONDS = 24 * 60 * 60

# `<scheme>://` — what makes an argument a URL rather than a path. Anything
# matching goes to `url_error`, so a scheme we can't fetch says so instead
# of coming back as "No such file".
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")

# The suffix to save each image content type under. The lightbox decodes by
# content, but the name is what "Open With…" hands the next app and what
# `editorfiles.is_image_path` reads, so it has to be honest.
CONTENT_TYPE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/x-bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/tiff": ".tiff",
    "image/avif": ".avif",
}


class FetchError(Exception):
    """A fetch that failed for a reason worth telling the agent.

    The message is agent-facing English (like every other tool error), and
    always names the URL: the agent may have several in flight.
    """


def looks_remote(raw: str) -> bool:
    """Whether *raw* is a URL rather than a path — any `scheme://`, not just
    the ones we can fetch, so `url_error` gets to explain the rest."""
    return bool(_SCHEME_RE.match(raw))


def url_error(raw: str) -> str | None:
    """An error message when *raw* isn't a URL we'll fetch, else None."""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return f"Not a usable URL: {raw}"
    if parsed.scheme.lower() not in ("http", "https"):
        return f"Only http(s) URLs can be fetched, not {parsed.scheme}: {raw}"
    try:
        host = parsed.hostname
    except ValueError:  # a malformed port, IPv6 bracket, …
        return f"Not a usable URL: {raw}"
    if not host:
        return f"That URL names no host: {raw}"
    return None


def suffix_for(url: str, content_type: str | None) -> str | None:
    """The file suffix to save this response under, or None when it isn't an
    image Collins can display.

    The server's content type decides when it says something we know; a
    vague one (`application/octet-stream`, or none at all — both common on
    plain file servers and artifact stores) falls back to the URL's own
    suffix. A type we don't know that *is* an image (`image/heic`) fails
    here rather than downloading megabytes we can't decode.
    """
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind in CONTENT_TYPE_SUFFIXES:
        return CONTENT_TYPE_SUFFIXES[kind]
    if kind.startswith("image/"):
        return None  # an image format the viewer has no decoder for
    suffix = PurePosixPath(urllib.parse.urlsplit(url).path).suffix.lower()
    return suffix if suffix in editorfiles.IMAGE_SUFFIXES else None


def default_directory() -> Path:
    """Where fetched images are kept: beside the dropped-image copies, under
    the cache directory (regeneratable, fine to delete) rather than /tmp, so
    "Open With…" still has a file to hand over minutes later."""
    return cache_directory() / "remote-images"


def prune_stale(directory: Path, now: float | None = None) -> None:
    """Delete downloads older than PRUNE_AFTER_SECONDS. Called on each fetch
    rather than at startup, so an unused feature costs nothing; failures are
    swallowed — housekeeping must never break the fetch that triggered it."""
    if now is None:
        now = time.time()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file() and now - path.stat().st_mtime > PRUNE_AFTER_SECONDS:
                path.unlink()
        except OSError:
            continue


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects, but only to http(s) and only a few times.

    urllib's own handler also allows ftp, and allows ten hops; neither is
    something a `show_image` call should be able to walk into.
    """

    max_redirections = _MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in ("http", "https"):
            raise FetchError(f"That URL redirects somewhere we won't follow: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    """A fresh opener per fetch: the default one carries whatever handlers
    the process has installed, and nothing here wants state (cookies,
    auth caches) shared between two sessions' images. Proxy handling stays
    urllib's default, so the user's `http(s)_proxy` is honored."""
    return urllib.request.build_opener(_SafeRedirectHandler())


def fetch(url: str, now: Callable[[], float] | None = None) -> tuple[bytes, str]:
    """GET *url*, returning (bytes, file suffix). Raises FetchError.

    Blocking — the caller runs this off the main loop. *now* is the clock
    the deadline is measured against (a monotonic one by default), injected
    so tests can pin it.
    """
    if now is None:
        now = time.monotonic
    deadline = now() + TIMEOUT_SECONDS
    # Say who we are: urllib's default agent string is what a lot of CDNs
    # answer 403 to, and a server that wants to refuse us deserves to know
    # what it's refusing.
    request = urllib.request.Request(
        url, headers={"Accept": "image/*", "User-Agent": f"Collins/{__version__}"}
    )
    try:
        with _opener().open(request, timeout=_SOCKET_TIMEOUT) as response:
            suffix = suffix_for(
                response.geturl() or url, response.headers.get("Content-Type")
            )
            if suffix is None:
                raise FetchError(f"That URL isn't an image Collins can display: {url}")
            _refuse_declared_size(response.headers.get("Content-Length"), url)
            data = _read_capped(response, url, deadline, now)
    except FetchError:
        raise
    except urllib.error.HTTPError as error:
        raise FetchError(f"The server answered {error.code} for {url}") from None
    except urllib.error.URLError as error:
        raise FetchError(f"Couldn't reach {url}: {error.reason}") from None
    except (OSError, ValueError) as error:
        raise FetchError(f"Couldn't fetch {url}: {error}") from None
    return data, suffix


def _refuse_declared_size(header: str | None, url: str) -> None:
    """Fail before the first byte when the server has already said the image
    is too big — the cap in `_read_capped` still holds for servers that
    don't say, or that lie."""
    try:
        declared = int(header) if header else 0
    except ValueError:
        return
    if declared > MAX_BYTES:
        raise FetchError(f"That image is larger than {MAX_BYTES // (1024 * 1024)} MB: {url}")


def _read_capped(response, url: str, deadline: float, now) -> bytes:
    """The body, refusing to grow past MAX_BYTES or to run past *deadline*.

    Read in chunks rather than in one `read()`: a socket timeout bounds each
    read but not their sum, and a slow drip is exactly how an unbounded
    download looks from here.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        if now() > deadline:
            raise FetchError(f"Timed out after {TIMEOUT_SECONDS:g}s fetching {url}")
        chunk = response.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise FetchError(
                f"That image is larger than {MAX_BYTES // (1024 * 1024)} MB: {url}"
            )
        chunks.append(chunk)
    if not total:
        raise FetchError(f"That URL returned an empty response: {url}")
    return b"".join(chunks)


def fetch_to_file(url: str, directory: Path) -> Path:
    """Fetch *url* into *directory* and return the file's path.

    The whole blocking half of a remote `show_image`, so the worker thread
    has one call to make: prune, fetch, save. Raises FetchError.
    """
    prune_stale(directory)
    data, suffix = fetch(url)
    try:
        return save_copy(data, directory, "remote", suffix)
    except OSError as error:
        raise FetchError(f"Couldn't save the image Collins fetched: {error}") from None
