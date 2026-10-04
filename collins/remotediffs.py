# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's copy of the marks on each session's diff (split-service
spec §3.7, §3.8, PR-1.11).

The service keeps every session's notes and highlights (`service.diffs`);
a git page's own `diffnotes.MarkStore` mirrors its session's. This module
is the copy between the two: filled by the `diff.notes` events (the
subscribe snapshot, then each change), read by a page when it opens
(`marks`), told by a page when its store changed (`send`: the whole
store, as `diff.set-notes`, skipped when it is what the copy holds, so a
page loading what the service sent never writes it back), and telling a
page what the service changed under it (`listen`: an agent's tool call
that landed with no page open, another client's edit). One per link
(`mirror_for`), made by the app before it subscribes. GTK-free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .api.loopback import RequestRefused

log = logging.getLogger(__name__)

Marks = tuple[list, list]


class DiffNotesMirror:
    def __init__(self, link) -> None:
        self._link = link
        self._marks: dict[str, Marks] = {}
        self._listeners: dict[str, list[Callable[[Marks], None]]] = {}
        link.on("diff.notes", self._on_notes)

    def _on_notes(self, event: dict) -> None:
        key = event.get("session") or event.get("handle") or ""
        marks = (list(event.get("notes") or []), list(event.get("highlights") or []))
        if self._marks.get(key) == marks:
            return
        self._marks[key] = marks
        for listener in list(self._listeners.get(key, ())):
            try:
                listener(marks)
            except Exception:
                log.exception("diffs: a page's listener failed")

    def marks(self, key: str) -> Marks | None:
        return self._marks.get(key)

    def listen(self, key: str, listener: Callable[[Marks], None]) -> None:
        self._listeners.setdefault(key, []).append(listener)

    def unlisten(self, key: str, listener: Callable[[Marks], None]) -> None:
        listeners = self._listeners.get(key)
        if listeners and listener in listeners:
            listeners.remove(listener)

    def send(self, key: str, session: bool, notes: list, highlights: list) -> None:
        """A page's store, whole: written to the service unless it is what
        the copy holds already (the echo of what the service sent)."""
        marks = (list(notes), list(highlights))
        if not key or self._marks.get(key, ([], [])) == marks:
            return
        self._marks[key] = marks
        message = {"t": "diff.set-notes", "notes": marks[0], "highlights": marks[1]}
        message["session" if session else "handle"] = key
        try:
            self._link.call(message)
        except RequestRefused as refusal:
            log.warning("diffs: the service refused the marks of %s: %s", key, refusal.msgid)


_by_link: dict[int, DiffNotesMirror] = {}


def mirror_for(link) -> DiffNotesMirror | None:
    if link is None:
        return None
    mirror = _by_link.get(id(link))
    if mirror is None or mirror._link is not link:
        mirror = _by_link[id(link)] = DiffNotesMirror(link)
    return mirror
