# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The finish judge: whether a finish edge is a turn ending (PR-1.12a).

`MainWindow` used to hold this beside its activity tracker (`_judge_finish`,
`_hold_finish`, `_on_hold_expired`, `_on_tab_transcript_updated`,
`_drop_held_finish`); with the tracker on the service (`service.tracking`)
the judge comes with it, word for word. The tracker's finish edge for a
session is first judged against the session's transcript
(`activity.FinishLedger`, armed by the session's first full read): only an
edge whose transcript has moved since the last counted finish counts; one
that finds it unchanged is held for `FINISH_CONFIRM_S` while the session
re-reads its file, delivered the moment a read lands with the stamp
advanced, and dropped when the window runs out — or when the session goes
busy again, which makes the held edge moot.

GLib only through the injected timers; nothing here imports GTK.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..activity import FINISH, FINISH_CONFIRM_S

log = logging.getLogger(__name__)


class FinishJudge:
    """See the module docstring. *session_for(session_id)* answers the live
    `Session` bound to an id, or None; *land(session_id)* is what a finish
    that counts does; *timeout_add* and *source_remove* are GLib's (a
    test's fake)."""

    def __init__(
        self,
        session_for: Callable[[str], Any],
        land: Callable[[str], None],
        timeout_add: Callable[..., int],
        source_remove: Callable[[int], Any],
    ) -> None:
        self._session_for = session_for
        self._land = land
        self._timeout_add = timeout_add
        self._source_remove = source_remove
        # Finish edges the transcript called repaints, held for
        # FINISH_CONFIRM_S while the session re-reads its file: session id ->
        # the timeout that drops them.
        self.held: dict[str, int] = {}
        # Sessions whose finishes have been logged as passing ungated (no
        # transcript read yet), once each.
        self._ungated_logged: set[str] = set()

    def judge(self, session_id: str, *, final: bool = False) -> str:
        """Ask the session whether its transcript has moved since the last
        finish that counted (activity.FinishLedger): `FINISH` or
        `FINISH_DUPLICATE`. A session the service no longer holds passes —
        finishes come from live sessions — and so does one whose transcript
        hasn't been read yet (a fresh spawn before the resolver binds it, an
        attach to a live background agent, a CLI with transcript saving
        off), which keeps the old behaviour rather than losing a real
        finish."""
        session = self._session_for(session_id)
        if session is None:
            return FINISH
        if not session.finish_ledger.armed:
            if session.handle not in self._ungated_logged:
                self._ungated_logged.add(session.handle)
                log.info("activity: %s finishes pass ungated (no transcript read yet)", session_id)
            return FINISH
        stamp, size = session.finish_witness()
        return session.finish_ledger.decide(stamp, size, final=final)

    def edge(self, session_id: str) -> None:
        """A finish edge for *session_id*: landed when the transcript has
        moved, held for its word when it hasn't. One held edge per session;
        a second inside the window is absorbed."""
        if session_id in self.held:
            return
        if self.judge(session_id) == FINISH:
            self._land(session_id)
        else:
            self.hold(session_id)

    def hold(self, session_id: str) -> None:
        """The transcript hasn't moved since the last counted finish — but it
        is parsed on a thread and lands on idle, so "unchanged at the edge"
        can also mean "not ingested yet". Ask the session to re-read it and
        hold the edge for FINISH_CONFIRM_S: the read landing with the stamp
        advanced delivers it (`transcript_landed`); the window running out
        drops it (`_on_hold_expired`); the session going busy again
        meanwhile drops it too (`drop`)."""
        log.debug("activity: %s finish held (awaiting transcript)", session_id)
        session = self._session_for(session_id)
        if session is not None:
            session.request_update()
        self.held[session_id] = self._timeout_add(
            int(FINISH_CONFIRM_S * 1000), self._on_hold_expired, session_id
        )

    def _on_hold_expired(self, session_id: str) -> bool:
        self.held.pop(session_id, None)
        if self._session_for(session_id) is None:
            return False  # the session went away under the hold
        if self.judge(session_id, final=True) == FINISH:
            log.debug("activity: %s finish confirmed", session_id)
            self._land(session_id)
        else:
            log.debug("activity: %s finish ignored (transcript unchanged)", session_id)
        return False

    def transcript_landed(self, session) -> None:
        """A transcript read landed for *session*: any finish held for its
        word is judged again now, and delivered the moment the stamp has
        moved."""
        for session_id in list(self.held):
            if self._session_for(session_id) is not session:
                continue
            if self.judge(session_id) != FINISH:
                continue
            self.drop(session_id)
            log.debug("activity: %s finish confirmed", session_id)
            self._land(session_id)

    def drop(self, session_id: str) -> None:
        """Forget a held edge (the session went busy again, or away)."""
        source = self.held.pop(session_id, None)
        if source is not None:
            self._source_remove(source)

    def stop(self) -> None:
        for session_id in list(self.held):
            self.drop(session_id)
