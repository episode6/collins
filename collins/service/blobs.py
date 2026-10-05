# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The image blobs and the uploads, the service's end (split-service spec
§3.11, §3.23; PR-2.7).

Three more kinds of `GET /api/blob` (`api.server._on_blob`'s table), each
in `prfeed.PrBlobs.blob`'s shape — ``blob(client, raw_query,
if_none_match, respond)``, the work on a thread and *respond* landing on
the main loop — and the `PUT /api/upload` the server hands its body to:

- **`kind=file&path=…&session=…`** (`FileBlobs`): an image file on this
  machine, for the lightbox, the attachments gallery, the composer's
  previews and the editor's image page. Confined (D38) to a path inside a
  root the service knows (`files.roots`), inside the session's upload
  directory or the pending one (`uploads.inside`), or one the *session's
  own agent* named — a show_image path (`ImageRegistry`, recorded as the
  call lands in `service.tools`) or an image the service's transcript scan
  of that live session found (`Session.transcript.attachments`); anything
  for a `local` client. Only an image suffix is served (`400` otherwise),
  at most `editorfiles`' 50 MiB viewer cap (`413`), tagged
  ``"<mtime µs>-<size>"`` and answered `304` on a match.
- **`kind=remote&url=…`** (`RemoteBlobs`): an http(s) image, fetched *by
  the service* through `remoteimages.fetch` (the 25 MiB cap, the redirect
  and content-type gates, the 10 s deadline) into its own cache
  (`remoteimages.default_directory()`, pruned at a day), and served from
  there with the cached file's tag. A PR body's images read the cache;
  `show_image` of a URL downloads fresh first (`download`, from
  `service.tools`), so a chart re-published under the same URL shows anew.
- **`kind=icon&root=…`** (`IconBlobs`): a project's `project-icon.svg`
  behind `projecticons.usable_icon_bytes` (`read_icon`), for the sidebar,
  the new-chat screen and the notifications; `404` when the project ships
  none (or one the gate refuses). The root must be one the service knows.
- **`PUT /api/upload?session=<id>&name=<basename>`** (`UploadBlobs.put`):
  the body written by `uploads.write` under the session's directory, or
  the pending one with no `session` (D37); the reply is JSON
  ``{"path": …}``. A session id the service doesn't know is `403`.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat as stat_mod
import tempfile
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

from .. import editorfiles, projecticons, remoteimages, uploads
from . import files, gitfeed

log = logging.getLogger(__name__)

Respond = Callable[[int, dict, bytes], None]

# The most a file blob may be: the image viewers' cap.
FILE_MAX_BYTES = editorfiles._MAX_IMAGE_BYTES
# The paths one session's agent named that are kept for it (D38).
REGISTRY_PER_SESSION = 512
# The sessions the registry keeps paths for (oldest dropped).
REGISTRY_SESSIONS = 256

# The content type an image suffix is served under.
SUFFIX_TYPES = {suffix: kind for kind, suffix in remoteimages.CONTENT_TYPE_SUFFIXES.items()}
SUFFIX_TYPES[".jpeg"] = "image/jpeg"


def _params(query: str) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(query or "", keep_blank_values=True).items()}


def _matches(if_none_match: str | None, tag: str) -> bool:
    return bool(if_none_match) and any(part.strip() in (tag, "*") for part in if_none_match.split(","))


def file_tag(st: os.stat_result) -> str:
    """A file's tag: its mtime (µs, as the wire carries it) and its size."""
    return f'"{files.mtime_us(st)}-{st.st_size}"'


def content_type(path: str) -> str:
    return SUFFIX_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


class ImageRegistry:
    """The paths a session's own tool calls named (D38): show_image's
    resolved path, per session (its id, or its handle while unresolved),
    at most REGISTRY_PER_SESSION each, oldest dropped; kept for the
    service's run."""

    def __init__(self) -> None:
        self._paths: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()

    def admit(self, keys, path: str) -> None:
        real = os.path.realpath(path)
        for key in keys:
            if not key:
                continue
            held = self._paths.setdefault(key, OrderedDict())
            self._paths.move_to_end(key)
            for one in {path, real}:
                held[one] = None
                held.move_to_end(one)
            while len(held) > REGISTRY_PER_SESSION:
                held.popitem(last=False)
            while len(self._paths) > REGISTRY_SESSIONS:
                self._paths.popitem(last=False)

    def paths(self, key: str) -> frozenset[str]:
        return frozenset(self._paths.get(key) or ())


def _session_records(core, key: str):
    for record in list(getattr(core, "sessions", {}).values()):
        session = getattr(record, "session", None)
        if session is None:
            continue
        if key and key in (getattr(session, "session_id", None), getattr(session, "handle", None)):
            yield session


def agent_named(core, key: str) -> frozenset[str]:
    """Every path the session *key*'s agent exposed (D38): its show_image
    paths and the images the service's transcript scan of it found.
    Main loop (the records and the scan are the main loop's)."""
    if not key:
        return frozenset()
    found = set(getattr(core, "image_registry", ImageRegistry()).paths(key))
    for session in _session_records(core, key):
        try:
            seen = session.transcript.attachments()
        except Exception:
            seen = []
        for one in seen:
            if not getattr(one, "remote", False) and isinstance(one.key, str) and one.key.startswith("/"):
                found.add(one.key)
    return frozenset(found)


def known_session(core, session_id: str) -> bool:
    """Whether *session_id* is a session the service knows: a live one's,
    or one the store lists."""
    if any(_session_records(core, session_id)):
        return True
    store = getattr(core, "store", None)
    try:
        return store is not None and store.get_session(session_id) is not None
    except Exception:
        return False


def read_icon(root: str) -> tuple[bytes, str] | None:
    """The project icon at *root* as served: (its bytes, its tag), or None
    when there is none, it is implausibly large, or the gate refuses it.
    Read by descriptor (no symlink at the file's own name followed into
    somewhere else); a worker thread's."""
    path = os.path.join(root, projecticons.PROJECT_ICON_FILENAME)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode) or not 0 < st.st_size <= projecticons.MAX_ICON_BYTES:
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(projecticons.MAX_ICON_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if not projecticons.usable_icon_bytes(data):
        return None
    return data, file_tag(st)


class _Feed:
    def __init__(
        self,
        core,
        dispatch: Callable[[Callable[[], object]], None] | None = None,
        spawn: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        self.core = core
        self._dispatch = dispatch or gitfeed.dispatch_default
        self._spawn = spawn or gitfeed.spawn_default

    def _answer(self, respond: Respond, status: int, headers: dict | None = None, body: bytes = b"") -> None:
        self._dispatch(lambda: respond(status, headers or {}, body))

    def _run(self, name: str, respond: Respond, work: Callable[[], tuple[int, dict, bytes]]) -> None:
        def run() -> None:
            try:
                status, headers, body = work()
            except Exception:
                log.exception("blobs: %s failed on its thread", name)
                status, headers, body = 500, {}, b""
            self._dispatch(lambda: respond(status, headers, body))

        self._spawn(run, name)


def serve_file(
    path: str, if_none_match: str | None, max_bytes: int = FILE_MAX_BYTES
) -> tuple[int, dict, bytes]:
    """A regular file's bytes with its tag (*path* already confined): 404
    for none, 413 over *max_bytes*, 304 on a matching tag. Worker thread."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return 404, {}, b""
    try:
        st = os.fstat(fd)
        if not stat_mod.S_ISREG(st.st_mode):
            return 404, {}, b""
        if st.st_size > max_bytes:
            return 413, {}, b""
        tag = file_tag(st)
        if _matches(if_none_match, tag):
            return 304, {"ETag": tag}, b""
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return 404, {}, b""
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        return 413, {}, b""
    return 200, {"ETag": tag, "Content-Type": content_type(path)}, data


class FileBlobs(_Feed):
    """`kind=file` (see the module docstring)."""

    def blob(self, client, query: str, if_none_match: str | None, respond: Respond) -> None:
        params = _params(query)
        path = params.get("path") or ""
        session = params.get("session") or ""
        if not os.path.isabs(path) or "\x00" in path or not editorfiles.is_image_path(path):
            self._answer(respond, 400)
            return
        local = bool(getattr(client, "local", False))
        # What the worker confines to, gathered here on the main loop where
        # the store, the records and the registry live.
        roots = None if local else files.roots(self.core)
        named = frozenset() if local else agent_named(self.core, session)

        def work() -> tuple[int, dict, bytes]:
            real = os.path.realpath(path)
            if roots is not None and not (
                any(editorfiles.is_inside(root, real) for root in roots)
                or uploads.inside(real, session or None)
                or path in named
                or real in named
            ):
                return 403, {}, b""
            if not editorfiles.is_image_path(real):
                return 400, {}, b""
            return serve_file(real, if_none_match)

        self._run("file-blob", respond, work)


class RemoteBlobs(_Feed):
    """`kind=remote` (see the module docstring). *directory* is the
    service's cache of fetched images; *fetch* is `remoteimages.fetch`
    (a test's stub)."""

    def __init__(self, core, directory: Path | None = None, fetch=None, **kwargs) -> None:
        super().__init__(core, **kwargs)
        self.directory = directory
        self._fetch = fetch or remoteimages.fetch

    def _folder(self) -> Path:
        return self.directory if self.directory is not None else remoteimages.default_directory()

    def _stem(self, url: str) -> str:
        return hashlib.sha1(url.encode("utf-8", "replace")).hexdigest()

    def cached(self, url: str, now: float | None = None) -> Path | None:
        """The cached copy of *url*, when one younger than a day is here."""
        now = time.time() if now is None else now
        folder = self._folder()
        stem = self._stem(url)
        for suffix in sorted(set(remoteimages.CONTENT_TYPE_SUFFIXES.values()) | editorfiles.IMAGE_SUFFIXES):
            candidate = folder / (stem + suffix)
            try:
                st = candidate.lstat()
            except OSError:
                continue
            if stat_mod.S_ISREG(st.st_mode) and now - st.st_mtime <= remoteimages.PRUNE_AFTER_SECONDS:
                return candidate
        return None

    def download(self, url: str) -> Path:
        """Fetch *url* now (whatever the cache holds) into the cache and
        return the file. Raises `remoteimages.FetchError` with the agent's
        words. Worker thread."""
        folder = self._folder()
        folder.mkdir(parents=True, exist_ok=True)
        remoteimages.prune_stale(folder)
        data, suffix = self._fetch(url)
        stem = self._stem(url)
        target = folder / (stem + suffix)
        handle, tmp = tempfile.mkstemp(prefix=stem[:12] + ".", suffix=".part", dir=str(folder))
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            os.replace(tmp, target)
        except OSError as error:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise remoteimages.FetchError(f"Couldn't save the image Collins fetched: {error}") from None
        for other in folder.glob(stem + ".*"):
            if other != target and not other.name.endswith(".part"):
                try:
                    other.unlink()
                except OSError:
                    pass
        return target

    def download_then(self, url: str, done: Callable[[str | None], None]) -> None:
        """`download` on a thread; *done(error or None)* on the main loop."""

        def run() -> None:
            try:
                self.download(url)
                error = None
            except remoteimages.FetchError as failure:
                error = str(failure)
            except Exception as failure:
                log.exception("blobs: a remote fetch failed on its thread")
                error = f"Couldn't fetch {url}: {failure}"
            self._dispatch(lambda: done(error))

        self._spawn(run, "remote-fetch")

    def blob(self, client, query: str, if_none_match: str | None, respond: Respond) -> None:
        url = _params(query).get("url") or ""
        if not remoteimages.looks_remote(url) or remoteimages.url_error(url) is not None:
            self._answer(respond, 400)
            return

        def work() -> tuple[int, dict, bytes]:
            found = self.cached(url)
            if found is None:
                try:
                    found = self.download(url)
                except remoteimages.FetchError as failure:
                    log.info("blobs: %s", failure)
                    return 502, {}, b""
            return serve_file(str(found), if_none_match, remoteimages.MAX_BYTES)

        self._run("remote-blob", respond, work)


class IconBlobs(_Feed):
    """`kind=icon` (see the module docstring)."""

    def blob(self, client, query: str, if_none_match: str | None, respond: Respond) -> None:
        root = files.allowed_cwd(self.core, client, _params(query).get("root"))
        if root is None:
            self._answer(respond, 403)
            return

        def work() -> tuple[int, dict, bytes]:
            found = read_icon(root)
            if found is None:
                return 404, {}, b""
            data, tag = found
            if _matches(if_none_match, tag):
                return 304, {"ETag": tag}, b""
            return 200, {"ETag": tag, "Content-Type": "image/svg+xml"}, data

        self._run("icon-blob", respond, work)


class UploadBlobs(_Feed):
    """`PUT /api/upload` (see the module docstring)."""

    def put(self, client, query: str, body: bytes, respond: Respond) -> None:
        params = _params(query)
        session = params.get("session") or None
        name = uploads.safe_name(params.get("name"))
        if name is None or (session is not None and not uploads.valid_session(session)):
            self._answer(respond, 400)
            return
        if len(body) > uploads.MAX_BYTES:
            self._answer(respond, 413)
            return
        if session is not None and not known_session(self.core, session):
            self._answer(respond, 403)
            return

        def work() -> tuple[int, dict, bytes]:
            try:
                path = uploads.write(session, name, body)
            except ValueError:
                return 400, {}, b""
            except OSError as error:
                log.warning("blobs: an upload could not be written: %s", error)
                return 500, {}, b""
            reply = json.dumps({"path": str(path)}).encode("utf-8")
            return 200, {"Content-Type": "application/json"}, reply

        self._run("upload", respond, work)
