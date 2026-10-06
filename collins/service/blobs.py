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
  for a `local` client. Only an image suffix is served (`400` otherwise;
  the one exception is `as=file`, D51, in `FileBlobs`' docstring: a file
  the session's agent named, whole, for a client that is not `local` to
  hand to an app), at most `editorfiles`' 50 MiB viewer cap (`413`), tagged
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
  ``{"path": …}``. A session id the service doesn't know is `403`, an
  empty body `400`.

`kind=remote` lets any attached client have the service GET any http(s)
URL, localhost included — deliberate for show_image (a dev server's plot)
and acceptable for one user's service, whose every client is already
trusted with a shell (§3.16); it answers only image content types.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat as stat_mod
import tempfile
import threading
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
        """Hold *path*'s resolved path for each key (D38: the exact
        resolved path; a link swapped in later points at a file nobody
        admitted)."""
        real = os.path.realpath(path)
        for key in keys:
            if not key:
                continue
            held = self._paths.setdefault(key, OrderedDict())
            self._paths.move_to_end(key)
            held[real] = None
            held.move_to_end(real)
            while len(held) > REGISTRY_PER_SESSION:
                held.popitem(last=False)
            while len(self._paths) > REGISTRY_SESSIONS:
                self._paths.popitem(last=False)

    def paths(self, key: str) -> frozenset[str]:
        return frozenset(self._paths.get(key) or ())


class DeliveredFiles:
    """The files a session's agent delivered (its transcript's attachment
    records that are no picture: a `SendUserFile` call's), each held by
    the path it resolved to **when the service first saw the record**
    (D51, review of PR 615): what `as=file` admits from the transcript.
    The registry's rule for a tool call, kept for a record: a link put at
    the named path afterwards resolves to a file nobody delivered, and is
    refused. Per session key (its id, its handle), bounded as the
    registry is, kept for the service's run.

    `note` is the main loop's (the transcript's landing); the resolving
    runs on a thread (*spawn*), since a `realpath` is a disk read, and a
    record is admitted once that lands: a request that beats it is
    refused, and the client asks afresh on the next click."""

    def __init__(self, spawn: Callable[[Callable[[], None], str], None] | None = None) -> None:
        self._lock = threading.Lock()
        # key -> record key -> the path it resolved to (None while unresolved)
        self._seen: OrderedDict[str, OrderedDict[str, str | None]] = OrderedDict()
        self._spawn = spawn or _thread

    def note(self, keys, record_keys) -> None:
        """Record *record_keys* (absolute paths the transcript named) for
        each of the session's *keys*; the ones not seen before are
        resolved now, once, and never again."""
        keys = [key for key in keys if key]
        fresh: list[str] = []
        with self._lock:
            for key in keys:
                held = self._seen.setdefault(key, OrderedDict())
                self._seen.move_to_end(key)
                for record_key in record_keys:
                    if record_key not in held:
                        held[record_key] = None
                        if record_key not in fresh:
                            fresh.append(record_key)
                while len(held) > REGISTRY_PER_SESSION:
                    held.popitem(last=False)
            while len(self._seen) > REGISTRY_SESSIONS:
                self._seen.popitem(last=False)
        if not fresh:
            return

        def resolve() -> None:
            resolved = {record_key: os.path.realpath(record_key) for record_key in fresh}
            with self._lock:
                for key in keys:
                    held = self._seen.get(key)
                    if held is None:
                        continue
                    for record_key, real in resolved.items():
                        if record_key in held and held[record_key] is None:
                            held[record_key] = real

        self._spawn(resolve, "delivered-files")

    def paths(self, key: str) -> frozenset[str]:
        with self._lock:
            return frozenset(real for real in (self._seen.get(key) or {}).values() if real)


def _thread(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


def note_delivered(core, record) -> None:
    """A session's transcript landed (main loop): hand its delivered-file
    records to the core's `DeliveredFiles`, under the session's id and its
    handle. Pictures are not noted: they are admitted as before (D38)."""
    delivered = getattr(core, "delivered_files", None)
    session = getattr(record, "session", None)
    if delivered is None or session is None:
        return
    try:
        seen = session.transcript.attachments()
    except Exception:
        return
    keys = [
        one.key
        for one in seen
        if getattr(one, "kind", "image") != "image"
        and not getattr(one, "remote", False)
        and isinstance(one.key, str)
        and one.key.startswith("/")
    ]
    if keys:
        delivered.note([getattr(session, "session_id", None), getattr(record, "handle", None)], keys)


def _session_records(core, key: str):
    for record in list(getattr(core, "sessions", {}).values()):
        session = getattr(record, "session", None)
        if session is None:
            continue
        if key and key in (getattr(session, "session_id", None), getattr(session, "handle", None)):
            yield session


def agent_named(core, key: str) -> tuple[frozenset[str], frozenset[str]]:
    """What the session *key*'s agent exposed (D38): (the resolved paths
    its show_image calls named, the image paths the service's transcript
    scan of it found — resolved by the caller, on its worker). Main loop
    (the records and the scan are the main loop's)."""
    if not key:
        return frozenset(), frozenset()
    admitted = getattr(core, "image_registry", ImageRegistry()).paths(key)
    found: set[str] = set()
    for session in _session_records(core, key):
        try:
            seen = session.transcript.attachments()
        except Exception:
            seen = []
        for one in seen:
            if not getattr(one, "remote", False) and isinstance(one.key, str) and one.key.startswith("/"):
                found.add(one.key)
    return admitted, frozenset(found)


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
        # *path* is resolved already (the confinement was of its realpath):
        # a link put at its last component since then is not followed.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
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
    """`kind=file` (see the module docstring).

    A picture is served from a root, the session's uploads, or a path the
    session's agent named (D38); anything for a `local` client. **A file
    that is no picture** (D51, PR-2.8: the attachments panel's Open With…
    and a file row's default app, on a client that is not `local`) is
    served only to a request that says `as=file`, and only when the
    session's agent *named* it — its uploads, the
    exact resolved path a tool call registered (`ImageRegistry`), or the
    path a delivered file's transcript record resolved to when the service
    first saw it (`DeliveredFiles`) — and never merely for being
    inside a root: `fs.read` is the reader of a project's text, and a
    root is not an attachment. A `local` client is never served one (it
    opens the file itself), so its answers are what they were. The cap
    (`FILE_MAX_BYTES`), the tag and the 304 are the pictures'; the type
    of a file that is no picture is `application/octet-stream`."""

    def blob(self, client, query: str, if_none_match: str | None, respond: Respond) -> None:
        params = _params(query)
        path = params.get("path") or ""
        session = params.get("session") or ""
        local = bool(getattr(client, "local", False))
        # `as=file`: the asker wants the file as it is, to hand to an app
        # (D51), and takes no picture for granted. Without it only a
        # picture is served, exactly as before PR-2.8, so nothing that
        # decodes what it fetched (the lightbox, a thumbnail) is ever sent
        # anything else.
        whole = params.get("as") == "file" and not local
        if not os.path.isabs(path) or "\x00" in path:
            self._answer(respond, 400)
            return
        if not whole and not editorfiles.is_image_path(path):
            self._answer(respond, 400)
            return
        # What the worker confines to, gathered here on the main loop where
        # the store, the records and the registry live.
        roots = None if local else files.roots(self.core)
        admitted, seen = (frozenset(), frozenset()) if local else agent_named(self.core, session)
        delivered = getattr(self.core, "delivered_files", None)
        handed = frozenset() if local or delivered is None or not session else delivered.paths(session)

        def work() -> tuple[int, dict, bytes]:
            real = os.path.realpath(path)
            picture = editorfiles.is_image_path(path) and editorfiles.is_image_path(real)
            if roots is None:
                return serve_file(real, if_none_match) if picture else (400, {}, b"")
            # Named by the session's agent, by the exact path it resolved to
            # when it was named (D38, D51): its uploads, a tool call's
            # registered path, a delivered file's as the service first saw
            # its record. A link swapped in since resolves elsewhere.
            exact = uploads.inside(real, session or None) or real in admitted or real in handed
            # A picture the transcript scan found is admitted as before
            # PR-2.8, by what its record's key resolves to now (D38).
            named = exact or real in {os.path.realpath(key) for key in seen}
            if not named and not any(editorfiles.is_inside(root, real) for root in roots):
                return 403, {}, b""
            if not picture and not (whole and exact):
                # No picture, and not a file the session's agent named asked
                # for whole: inside a root it is `fs.read`'s to read.
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
        if not body:
            self._answer(respond, 400)  # an empty upload is nothing to mention
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
