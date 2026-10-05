# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's end of the pull requests (split-service spec §3.15, PR-1.11).

**The hub's mirror.** `RemotePrStore` is what `store.pr_store` is on a
client: `prstore.PrStore`'s signals (``status-changed``,
``session-changed``, ``pr-attached``) and methods (`records`, `prs`,
`set_records`, `set_prs`, `attach`), over a copy of the service hub's
lists filled by the `pr` events (the subscribe snapshot, then every change)
and its statuses kept by `pr-status` (absorbed into `prstatus`'s cache,
which `prs` and every widget read with `prstatus.known`; in Phase 1 the
service's cache is that same cache, and an entry already there changes
nothing). **The equality guard holds on both sides**: an identical write is
dropped here without a request or a signal, and an event that brings the
list the mirror already has (the echo of its own write) emits nothing, so
the subscribers that write back what they adopted still start no
carousel. Writes are optimistic: the list and its signals here first, then
`pr.set`; a refusal puts the old list back and says so again.

**The gh calls** (`perform`, `comment`, `review`, `reply_in_thread`,
`set_thread_resolved`, `fetch_detail`, `fetch_threads`, `fetch_blob`,
`invalidate`, `sweep`, `resync`) keep the names and return shapes of the
`practions`, `prdetail`, `prblobs` and `prstatus` functions the page, the
menus and the sidebar called, and are requests to the service on the
app's link (`apilink.current`). They block on gh, so they are called from
worker threads, as before. A link that refuses reads as the failure the
function would have answered (an error's words, None, an empty list).

GTK-free: GObject only.
"""

from __future__ import annotations

import logging
from pathlib import Path

from gi.repository import GObject

from . import apilink, prdetail, prstatus
from .api.protocol import RequestRefused
from .i18n import translate
from .prstatus import PullRequest

log = logging.getLogger(__name__)


class RemotePrStore(GObject.Object):
    """See the module docstring. *link* is an `apilink.Link`."""

    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "session-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "pr-attached": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, link) -> None:
        super().__init__()
        self._link = link
        self._records: dict[str, list] = {}
        link.on("pr", self._on_pr)
        link.on("pr-status", self._on_status)

    # -- events ------------------------------------------------------------------

    def _on_pr(self, event: dict) -> None:
        session_id = event["session"]
        records = [r for r in event.get("prs") or [] if isinstance(r, dict)]
        if records == self._records.get(session_id, []):
            return  # the echo of a write made here, or nothing new
        had = {pr.url for pr in prstatus.from_records(self._records.get(session_id, []))}
        self._store(session_id, records)
        self.emit("session-changed", session_id)
        for url in event.get("attached") or []:
            if url not in had:
                self.emit("pr-attached", session_id, url)

    def _on_status(self, event: dict) -> None:
        url = event["url"]
        prstatus.absorb_entry(url, event.get("status"))
        self.emit("status-changed", url)

    def _store(self, session_id: str, records: list) -> None:
        if records:
            self._records[session_id] = list(records)
        else:
            self._records.pop(session_id, None)

    # -- the hub's API --------------------------------------------------------------

    def records(self, session_id: str) -> list:
        return list(self._records.get(session_id, []))

    def prs(self, session_id: str) -> list[PullRequest]:
        return [prstatus.known(pr) for pr in prstatus.from_records(self.records(session_id))]

    def set_records(self, session_id: str, records: list) -> None:
        if not session_id or records == self._records.get(session_id, []):
            return
        old = self.records(session_id)
        had = {pr.url for pr in prstatus.from_records(old)}
        fresh = list(dict.fromkeys(pr.url for pr in prstatus.from_records(records) if pr.url not in had))
        self._store(session_id, list(records))
        self.emit("session-changed", session_id)
        for url in fresh:
            self.emit("pr-attached", session_id, url)
        try:
            self._link.call({"t": "pr.set", "session": session_id, "prs": list(records)})
        except RequestRefused as refusal:
            log.warning("prs: the service refused %s's list: %s", session_id, refusal.msgid)
            if self._records.get(session_id, []) == list(records):
                self._store(session_id, old)
                self.emit("session-changed", session_id)

    def set_prs(self, session_id: str, prs: list[PullRequest]) -> None:
        self.set_records(session_id, prstatus.to_records(prs))

    def attach(self, session_id: str, prs: list[PullRequest]) -> None:
        saved = prstatus.from_records(self.records(session_id))
        have = {pr.url for pr in saved}
        fresh = [pr for pr in prs if pr.url not in have]
        if fresh:
            self.set_prs(session_id, saved + fresh)


# ---- the gh calls, on the service ----------------------------------------------------


def _call(message: dict) -> dict | None:
    try:
        return apilink.call(message)
    except RequestRefused as refusal:
        log.debug("prs: %s refused: %s", message.get("t"), refusal.msgid)
        return {"error": translate(refusal.msgid, refusal.details) or "Not connected to the service"}


def _action(message: dict) -> str | None:
    reply = _call(message)
    return (reply or {}).get("error") or None


def _record(pr: PullRequest) -> dict:
    return prstatus.to_record(pr) or {"number": pr.number, "url": pr.url}


def perform(key: str, pr: PullRequest) -> str | None:
    """`practions.perform`, on the service: None, or the failure's words."""
    return _action({"t": "pr.action", "pr": _record(pr), "key": key})


def comment(pr: PullRequest, body: str) -> str | None:
    return _action({"t": "pr.comment", "pr": _record(pr), "body": body})


def review(pr: PullRequest, verdict: str, body: str = "") -> str | None:
    message = {"t": "pr.review", "pr": _record(pr), "verdict": verdict}
    if body:
        message["body"] = body
    return _action(message)


def reply_in_thread(pr: PullRequest, thread_id: str, body: str) -> str | None:
    return _action({"t": "pr.thread", "pr": _record(pr), "thread": thread_id, "body": body})


def set_thread_resolved(pr: PullRequest, thread_id: str, resolved: bool) -> str | None:
    return _action({"t": "pr.thread", "pr": _record(pr), "thread": thread_id, "resolved": bool(resolved)})


def fetch_detail(url: str) -> prdetail.PullRequestDetail | None:
    """`prdetail.fetch`, on the service."""
    reply = _call({"t": "pr.detail", "url": url}) or {}
    return prdetail.detail_from_record(reply.get("detail"))


def fetch_threads(url: str) -> tuple:
    reply = _call({"t": "pr.threads", "url": url}) or {}
    return prdetail.threads_from_records(reply.get("threads"))


def fetch_blob(repository: str, ref: str, path: str) -> Path:
    """`prblobs.fetch_to_file`, on the service: the file it wrote (through
    Phase 1 a path on this machine; PR-1.12 makes it a blob transfer).
    Raises `prblobs.BlobError`."""
    from .prblobs import BlobError

    reply = _call({"t": "pr.blob", "repository": repository, "ref": ref, "path": path}) or {}
    if reply.get("file"):
        return Path(reply["file"])
    raise BlobError(reply.get("error") or f"GitHub wouldn't hand over {path}")


def invalidate(url: str) -> None:
    """`prstatus.invalidate`, on the service (whose cache it is)."""
    if prstatus._FETCHABLE.match(url or ""):
        _call({"t": "pr.fetch", "urls": [url], "invalidate": True})


def sweep(targets) -> dict[str, list[PullRequest]]:
    """`prstatus.sweep`, on the service: ``(session id, PRs, cwd)`` each."""
    message_targets = []
    for session_id, prs, cwd in targets:
        target = {"session": session_id, "prs": prstatus.to_records(prs)}
        if isinstance(cwd, str) and cwd.startswith("/"):
            target["cwd"] = cwd
        message_targets.append(target)
    if not message_targets:
        return {}
    reply = _call({"t": "pr.sweep", "targets": message_targets}) or {}
    return {sid: prstatus.from_records(records) for sid, records in (reply.get("results") or {}).items()}


def resync(prs) -> list[PullRequest]:
    """`prstatus.resync`, on the service: the sweep with no directory to
    look for a PR in, which is what a resync is."""
    prs = list(prs)
    if not prs:
        return []
    return sweep([("resync", prs, None)]).get("resync", prs)
