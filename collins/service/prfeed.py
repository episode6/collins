# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""Pull requests, served by the service (split-service spec §3.15, "Pull
requests", PR-1.11).

`gh` is authenticated on the service's machine, so every PR read and
action runs here: `prstatus` (the status fetches, the sweep), `practions`
(every action), `prdetail` (the PR page's data), `prblobs` (an image the
Files view shows), and the hub that keeps a session's PRs, `prstore.
PrStore`, which is the service store's own. A client holds the mirror
(`remoteprs.RemotePrStore`) with the hub's signals and equality guard.

`PrFeed` publishes the hub: at `subscribe`, a `pr` event for every session
with saved PRs; after, a `pr` event for each ``session-changed`` (with the
URLs that just joined it, the hub's ``pr-attached``, as `attached`) and a
`pr-status` event for each ``status-changed`` (the fetch cache's entry
for that URL). `pr.set` is the hub's `set_records`, so the equality guard
holds on this side too: an unchanged list writes nothing and tells nobody.

The gh requests (`handle_gh`) need no store, so a core with none (a widget
driven alone, a harness's own service) serves them all the same. They
are made from a client's worker thread (they block on gh), as the page and
the menus made the calls themselves; the handler runs on
that thread and touches nothing but the gh modules, whose caches lock. A
refusal or failure crosses as the reply's `error`, `practions`' own words.

`pr.detail` and `pr.threads` are the large replies: a big PR's detail (its
body, its timeline, every file's patch up to `prdetail.WIRE_PATCH_MAX`)
can come near the 1 MiB frame cap once the socket encodes it. The socket's sender will
have to drop patches (they cross as None, drawn as over the cap) or chunk
the reply there, as `storefeed`'s large state entries will.

An image the Files view shows (PR-2.2, §3.23) is two steps: `pr.blob`
checks the gates (`prblobs.check`) and answers the blob's URL, and the
client's blobcache GETs it on the same socket (`GET /api/blob?kind=pr&
repository=&ref=&path=`, routed by `api.server` to `PrBlobs.blob`), whose
answer is the bytes gh hands over, fetched on a thread, with the
commit's tag — ``304`` with no gh call for a client already holding it.
No file is written here: a blob is never a path on the wire.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import parse_qs

from .. import practions, prblobs, prdetail, prstatus
from ..api import protocol
from . import gitfeed

log = logging.getLogger(__name__)


def _records(records: list) -> list[dict]:
    return [dict(r) for r in records if isinstance(r, dict)][: protocol.PRS_MAX]


class PrFeed:
    """The hub's events, per subscriber. See the module docstring."""

    def __init__(self, pr_store) -> None:
        self.hub = pr_store
        self._subscribers: list[tuple[object, Callable[[dict], None]]] = []
        # session id -> the URLs its list held when last published.
        self._seen: dict[str, set[str]] = {
            session_id: {pr.url for pr in prstatus.from_records(records)}
            for session_id, records in pr_store._state.session_prs.items()
        }
        pr_store.connect("session-changed", self._on_session_changed)
        pr_store.connect("status-changed", self._on_status_changed)

    def subscribe(self, client, deliver: Callable[[dict], None]) -> int:
        self._subscribers = [(c, d) for c, d in self._subscribers if c is not client]
        self._subscribers.append((client, deliver))
        sent = 0
        for session_id, records in sorted(self.hub._state.session_prs.items()):
            if records:
                self._send(deliver, protocol.event("pr", session=session_id, prs=_records(records)))
                sent += 1
        return sent

    def unsubscribe(self, client) -> None:
        self._subscribers = [(c, d) for c, d in self._subscribers if c is not client]

    def _send(self, deliver, event: dict) -> None:
        try:
            deliver(event)
        except Exception:
            log.exception("prs: a subscriber's delivery failed")

    def _broadcast(self, event: dict) -> None:
        for _client, deliver in list(self._subscribers):
            self._send(deliver, event)

    def _on_session_changed(self, _hub, session_id: str) -> None:
        """A session's list changed: the list, and the URLs on it that were
        not on what was saved before (the hub's pr-attached, worked out the
        way the hub works it out, against the list last seen here)."""
        records = list(self.hub.records(session_id))
        urls = [pr.url for pr in prstatus.from_records(records)]
        before = self._seen.get(session_id, set())
        self._seen[session_id] = set(urls)
        event = protocol.event("pr", session=session_id, prs=_records(records))
        attached = list(dict.fromkeys(url for url in urls if url not in before))
        if attached:
            event["attached"] = attached[: protocol.PRS_MAX]
        self._broadcast(event)

    def _on_status_changed(self, _hub, url: str) -> None:
        entry = prstatus.status_entry(url)
        if entry is None or not prstatus._FETCHABLE.match(url):
            return
        self._broadcast(protocol.event("pr-status", url=url, status=entry))

    def set_records(self, message: protocol.Message) -> dict:
        self.hub.set_records(message.get("session"), _records(message.get("prs")))
        return protocol.reply(message.id)


# ---- the gh requests (no store needed) ---------------------------------------------


def _pr(message: protocol.Message):
    return prstatus.from_record(message.get("pr"))


def _error(message: protocol.Message, error: str | None) -> dict:
    if error:
        return protocol.reply(message.id, error=str(error)[: protocol.ARG_TEXT_MAX])
    return protocol.reply(message.id)


NOT_A_PR = "Not a pull request Collins can act on"


def handle_gh(message: protocol.Message) -> dict:
    kind = message.type
    if kind == "pr.fetch":
        for url in message.get("urls"):
            if not prstatus._FETCHABLE.match(url):
                continue
            if message.get("invalidate"):
                prstatus.invalidate(url)
            else:
                prstatus.refresh(url)
        return protocol.reply(message.id)
    if kind == "pr.sweep":
        targets = [
            (t["session"], prstatus.from_records(t.get("prs") or []), t.get("cwd"))
            for t in message.get("targets")
        ]
        swept = prstatus.sweep(targets)
        return protocol.reply(
            message.id,
            results={sid: prstatus.to_records(prs)[: protocol.PRS_MAX] for sid, prs in swept.items()},
        )
    if kind == "pr.detail":
        detail = prdetail.fetch(message.get("url"))
        if detail is None:
            return protocol.reply(message.id)
        return protocol.reply(message.id, detail=prdetail.detail_record(detail))
    if kind == "pr.threads":
        threads = prdetail.fetch_threads(message.get("url"))
        return protocol.reply(message.id, threads=[prdetail.thread_record(t) for t in threads][:1000])
    if kind == "pr.blob":
        repository, ref, path = message.get("repository"), message.get("ref"), message.get("path")
        try:
            prblobs.check(repository, ref, path)
        except prblobs.BlobError as error:
            return _error(message, str(error))
        return protocol.reply(message.id, url=prblobs.blob_url(repository, ref, path))
    pr = _pr(message)
    if pr is None:
        return _error(message, NOT_A_PR)
    if kind == "pr.action":
        return _error(message, practions.perform(message.get("key"), pr))
    if kind == "pr.comment":
        return _error(message, practions.comment(pr, message.get("body")))
    if kind == "pr.review":
        return _error(message, practions.review(pr, message.get("verdict"), message.get("body") or ""))
    if kind == "pr.thread":
        if message.get("body") is not None:
            return _error(message, practions.reply_in_thread(pr, message.get("thread"), message.get("body")))
        resolved = bool(message.get("resolved"))
        return _error(message, practions.set_thread_resolved(pr, message.get("thread"), resolved))
    return protocol.refuse(message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": kind})


# ---- the blob GET (PR-2.2) ----------------------------------------------------------


class PrBlobs:
    """`GET /api/blob?kind=pr` (see the module docstring), in
    `gitfeed.GitFeed.blob`'s shape. *dispatch* lands the thread's answer
    on the main loop and *spawn* runs it (a test's inline)."""

    def __init__(
        self,
        dispatch: Callable[[Callable[[], object]], None] | None = None,
        spawn: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        self._dispatch = dispatch or gitfeed.dispatch_default
        self._spawn = spawn or gitfeed.spawn_default

    def blob(
        self, client, query: str, if_none_match: str | None, respond: Callable[[int, dict, bytes], None]
    ) -> None:
        """*respond(status, headers, body)* on the main loop: ``400`` for a
        blob the gates refuse, ``304`` on a matching ``If-None-Match``
        (gh is not asked), ``404`` for one gh would not hand over, else
        ``200`` with the bytes and the tag. *client* is unused: a PR blob
        is a gh read, which any client of the service may ask for."""
        params = {key: values[-1] for key, values in parse_qs(query or "", keep_blank_values=True).items()}
        repository = params.get("repository") or ""
        ref = params.get("ref") or ""
        path = params.get("path") or ""
        try:
            prblobs.check(repository, ref, path)
        except prblobs.BlobError:
            self._dispatch(lambda: respond(400, {}, b""))
            return
        tag = prblobs.blob_tag(repository, ref, path)
        if if_none_match and any(part.strip() in (tag, "*") for part in if_none_match.split(",")):
            self._dispatch(lambda: respond(304, {"ETag": tag}, b""))
            return

        def work() -> None:
            try:
                data = prblobs.fetch_bytes(repository, ref, path)
            except prblobs.BlobError as error:
                log.debug("prfeed: blob %s@%s:%s: %s", repository, ref[:7], path, error)
                self._dispatch(lambda: respond(404, {}, b""))
                return
            except Exception:
                log.exception("prfeed: a blob fetch failed on its thread")
                self._dispatch(lambda: respond(500, {}, b""))
                return
            headers = {"Content-Type": "application/octet-stream", "ETag": tag}
            self._dispatch(lambda: respond(200, headers, data))

        self._spawn(work, "pr-blob")
