# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""Busy and finish detection on the service (spec §3.6, §3.19, PR-1.12a).

`MainWindow` used to own one `activity.ActivityTracker` per window, fed by
its tabs' VTEs (the ``contents-changed`` redraws behind the echo gate, the
``vte.progress.hint`` termprop, every ``commit``), by a ``/proc`` poll over
the open tabs and by the agent list's word on attached background agents,
and it turned the verdicts into `store.flags` requests. `ServiceActivity`
is that tracker moved whole, one for the service, fed by the stream
filter's `Progress` events in place of the termprop, by the pty's output
bytes after the filter in place of the redraws (`on_output`, coalesced to
the screen's 50 ms settle), by the input frames in place of the commits
(`on_input`: the echo gate's poke, Enter's arm and pre-emptive mark), by
the same ``/proc`` poll over every live agent pty, and by the
background-busy poll over the sessions attached to a background agent. Its
verdicts call the store directly: `set_busy` on the rows a session stands
for, and on a counted finish (`finish.FinishJudge`) `set_unread(True)` and
a `finished` word to the session's clients, which refresh the pull requests
and announce the run the way they always did. A session with no row yet
(a tab's placeholder) hears the verdicts as `busy` and `finished` fields
of its `session` event, keyed by its handle.

What stays a client's (D29): "the person typed here" (`unread: false`),
"attention", and the /bg orchestration's two flags, which arrive as
`store.flags` and are read here off the items (`backgrounding` is "a /bg
was fed and not yet confirmed"; a row's status ``background`` is "detached
and no tab").

GLib only through the injected timers and the one idle landing; nothing
here imports GTK.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from .. import mcptools
from ..activity import (
    BACKGROUND_IDLE_S,
    BACKGROUND_POLL_MS,
    PROCESS_IDLE_S,
    PROCESS_POLL_MS,
    PROGRESS_FINISH_GRACE_S,
    PROGRESS_IDLE_S,
    ActivityTracker,
    BackgroundBusyWatch,
)
from ..bgstatus import fetch_background_busy_ids
from . import finish as finish_mod
from . import termstream

log = logging.getLogger(__name__)

def _glib_timeout_add(ms: int, fn: Callable[..., bool], *args: Any) -> int:
    from gi.repository import GLib

    return GLib.timeout_add(ms, fn, *args)


def _glib_source_remove(source: int) -> None:
    from gi.repository import GLib

    GLib.source_remove(source)


def _glib_land(fn: Callable[..., Any], *args: Any) -> None:
    from gi.repository import GLib

    def landed() -> bool:
        fn(*args)
        return False

    GLib.idle_add(landed, priority=GLib.PRIORITY_DEFAULT)


class ServiceActivity:
    """See the module docstring.

    *records()* lists the live agent sessions' records (`hosting.
    SessionRecord`: each with a `session`, a `handle`, `send(fields)` and
    `refresh_facts()`); *store* and *state* are the service's (None in a
    core with no store: the verdicts then reach the clients as `session`
    fields alone); *get_setting* reads the service's settings; the timers
    are GLib's unless a test hands in its own."""

    def __init__(
        self,
        *,
        records: Callable[[], list],
        store=None,
        state=None,
        get_setting: Callable[[str], Any] = lambda key: None,
        timeout_add: Callable[..., int] | None = None,
        source_remove: Callable[[int], Any] | None = None,
        land: Callable[..., None] | None = None,
        fetch_busy: Callable[[], set[str]] = fetch_background_busy_ids,
        clock: Callable[[], float] | None = None,
        announce: Callable[[str], None] | None = None,
    ) -> None:
        self._records = records
        self.store = store
        self.state = state
        self._get_setting = get_setting
        self._timeout_add = timeout_add or _glib_timeout_add
        self._source_remove = source_remove or _glib_source_remove
        self._land = land or _glib_land
        self._fetch_busy = fetch_busy
        self._announce = announce
        kwargs = {"clock": clock} if clock is not None else {}
        self.tracker = ActivityTracker(
            self._on_change,
            on_finished=self._on_finished,
            add_timeout=self._timeout_add,
            remove_timeout=self._source_remove,
            **kwargs,
        )
        self.judge = finish_mod.FinishJudge(
            self._session_for, self._land_finish, self._timeout_add, self._source_remove
        )
        self.bg_busy = BackgroundBusyWatch()
        self._process_poll: int | None = None
        self._bg_poll: int | None = None
        self._bg_fetching = False
        # Sessions that spawned their CLI fresh (no id at the spawn): no
        # agent can be mid-turn in one before its first submit, so even the
        # ungated pole starters hold through the startup paint (by handle).
        self._fresh: set[str] = set()
        # The plumbing baseline absorbed per fresh session before its id
        # resolved (by handle), persisted under the id once it does.
        self._captures: dict[str, set[str]] = {}
        self._infra = mcptools.infrastructure_cmdlines()
        if store is not None:
            store.connect("refreshed", self._on_refreshed)

    # -- the sessions ----------------------------------------------------------------

    def _record_for(self, session_id: str):
        for record in self._records():
            if record.session.session_id == session_id:
                return record
        return None

    def _session_for(self, session_id: str):
        record = self._record_for(session_id)
        return record.session if record is not None else None

    def _record_by_handle(self, handle: str):
        for record in self._records():
            if record.handle == handle:
                return record
        return None

    @staticmethod
    def _keys(session) -> list[str]:
        """What a session is tracked as: its id, and its handle (the
        placeholder row's), both marked on every verdict — a new thread
        that just resolved keeps its placeholder until the store finds the
        session."""
        return [key for key in (session.session_id, session.handle) if key]

    def started(self, record, fresh: bool) -> None:
        """A session was spawned (*fresh*: with no id, a new session or a
        --continue, whose pristine window is when its plumbing baseline is
        captured)."""
        if fresh:
            self._fresh.add(record.handle)
            self.absorb_baseline(record.session)
        record.on_settled = self.on_settled
        self._sync_polls()

    def ended(self, record) -> None:
        """The session's pty exited: nothing is left to be busy."""
        session = record.session
        for key in self._keys(session):
            self.tracker.clear(key)
            self.judge.drop(key)
        self._fresh.discard(record.handle)
        self._captures.pop(record.handle, None)
        record.on_settled = None
        self._sync_polls()

    def resolved(self, record) -> None:
        """The resolver bound the session to its id: what was absorbed
        into the baseline before the id was known has a home now, and the
        polls may have a new reason to run."""
        session = record.session
        captured = self._captures.get(record.handle)
        if captured and session.session_id and self.state is not None:
            self.state.set_process_baseline(session.session_id, captured)
        self._sync_polls()

    def startup_held(self, session) -> bool:
        """Whether this session's ungated pole starters are still held: a
        fresh spawn whose first submit hasn't armed its gate (see
        activity.EchoGate.armed)."""
        return session.handle in self._fresh and not session.echo_gate.armed

    # -- the feeds -------------------------------------------------------------------

    def on_settled(self, record) -> None:
        """A burst of output on the session's pty settled (`hosting.
        SessionRecord.output_arrived`, 50 ms): the redraw verdict."""
        session = record.session
        # The verdict itself is the session's (Session.redraw_counts): the
        # echo gate, then the spinner's second opinion, then the quiet the
        # agent's own turn-end opens. While the agent's own hint reads busy
        # a redraw mark carries the termprop's window, not the terminal's
        # short one (activity.PROGRESS_IDLE_S says why).
        agent_output = session.redraw_counts(self.startup_held(session))
        progress = session.progress
        idle_s = PROGRESS_IDLE_S if progress is not None and progress.busy else None
        for key in self._keys(session):
            if agent_output or self.tracker.is_busy(key):
                self.tracker.mark(key, idle_s=idle_s)

    def on_progress(self, record, event: termstream.Progress) -> None:
        """The agent's own busy signal: an OSC 9;4 progress change off the
        stream (what VTE's termprop carried). A busy hint marks with the
        wide termprop window; a clear arms the one honest turn-end finish."""
        if not self._get_setting("progress_termprop"):
            return
        session = record.session
        watch = session.progress
        if watch is None:
            return
        hint = None if event.state == 0 else event.state
        action = watch.reading(hint)
        held = self.startup_held(session)
        log.debug(
            "progress: %s hint=%s -> %s%s", record.handle, hint, action, " (startup hold)" if held else ""
        )
        if held or action is None:
            # A spawning CLI blips its progress hint with no turn in sight:
            # the blip's action is dropped, the watch still listens.
            return
        for key in self._keys(session):
            if action == "mark":
                self.tracker.resume(key)
                self.tracker.mark(key, idle_s=PROGRESS_IDLE_S)
            else:
                self.tracker.finish(key, grace_s=PROGRESS_FINISH_GRACE_S)

    def on_input(self, record, data: bytes) -> None:
        """A client's input frame: what the VTE committed, on its way to
        the gate (activity.EchoGate.poked). A carriage return in it is the
        arming edge — the last pristine instant of a fresh spawn, so the
        baseline takes one final snapshot first — and the pole starts on it
        pre-emptively, as the window started it on the Enter key."""
        text = data.decode("utf-8", errors="replace")
        session = record.session
        submit = "\r" in text
        if submit:
            self.absorb_baseline(session)
        session.echo_gate.poked(text)
        if submit:
            for key in self._keys(session):
                self.tracker.mark(key)

    # -- the verdicts ----------------------------------------------------------------

    def _on_change(self, key: str, busy: bool) -> None:
        log.debug("activity: %s -> %s", key, "busy" if busy else "idle")
        if busy:
            # A real new turn: a finish held for the transcript's word is moot.
            self.judge.drop(key)
        record = self._record_by_handle(key)
        if record is not None:
            record.send({"busy": busy})
            return
        self._sync_row_busy(key)

    def _on_finished(self, key: str) -> None:
        record = self._record_by_handle(key)
        if record is not None:
            # No bound transcript yet to judge by: the edge passes as it
            # always did for a placeholder.
            record.send({"finished": True})
            return
        self.judge.edge(key)

    def _chain(self, session_id: str) -> set[str]:
        if self.state is None:
            return {session_id}
        return set(self.state.forward_chain(session_id))

    def _rows(self, session_id: str) -> list[str]:
        if self.store is None:
            return []
        return list(self.store.rows_representing(session_id))

    def _sync_row_busy(self, session_id: str) -> None:
        """Push the busy flag onto every row this session shows up as: a
        row is busy when any id along its chain is."""
        busy = self.tracker.busy()
        for row_id in self._rows(session_id):
            self.store.set_busy(row_id, bool(self._chain(row_id) & busy))

    def _on_refreshed(self, _store, _order_changed: bool) -> None:
        """The store found rows: a session marked busy while it had none
        (mid-first-turn) gets its pole on the row that exists now."""
        for key in self.tracker.busy():
            if self._record_by_handle(key) is None:
                self._sync_row_busy(key)

    def _detaching(self, row_id: str) -> bool:
        """Whether a /bg was fed for this row and not yet confirmed (the
        window's `backgrounding` flag, a client's)."""
        item = self.store.get_item(row_id) if self.store is not None else None
        return bool(item is not None and item.backgrounding)

    def _detached_without_tab(self, row_id: str) -> bool:
        """A row running as a background agent with no tab on it: its line
        is the yellow of its status, never a flag."""
        item = self.store.get_item(row_id) if self.store is not None else None
        return bool(item is not None and item.status == "background")

    def _land_finish(self, session_id: str) -> None:
        """A finish that counts: the session's clients re-read its pull
        requests and announce the run, and its rows are flagged unread —
        the way MainWindow._land_finish always did it, exemptions
        included."""
        busy = self.tracker.busy()
        if not (self._chain(session_id) & busy or self._detaching(session_id)):
            # Every subscriber hears the finish (a `notify` of kind finished,
            # never persisted): the delivery and the PR refresh run there.
            record = self._record_for(session_id)
            if record is not None:
                record.send({"finished": True})
            if self._announce is not None:
                self._announce(session_id)
        for row_id in self._rows(session_id):
            # A row whose conversation still runs under another of its ids
            # hasn't finished; a detaching session's parting progress-clear
            # is a handoff, not a turn; a detached row with no tab keeps
            # its status's line.
            if self._chain(row_id) & busy:
                continue
            if self._detaching(row_id) or self._detached_without_tab(row_id):
                continue
            self.store.set_unread(row_id, True)

    def transcript_landed(self, record) -> None:
        """A transcript read landed: a held finish is judged again."""
        self.judge.transcript_landed(record.session)

    # -- the plumbing baseline -------------------------------------------------------

    def absorb_baseline(self, session) -> bool:
        """Fold what runs under a pristine fresh spawn's agent into its
        plumbing baseline, reporting whether the capture window is still
        open (the whole pre-submit life of a fresh spawn: the gate arming
        freezes the set for good)."""
        handle = session.handle
        if handle not in self._fresh or session.echo_gate.armed:
            return False
        seen = session.background_descendant_cmdlines()
        captured = self._captures.setdefault(handle, set())
        if seen - captured:
            captured |= seen
            if session.session_id and self.state is not None:
                self.state.set_process_baseline(session.session_id, captured)
        return True

    def _ignores(self, session) -> set[str]:
        """The cmdlines under this session's agent that are plumbing, not
        work: its persisted baseline, what was absorbed before the id
        resolved, and the MCP servers Collins itself configures."""
        ignores: set[str] = set()
        if session.session_id and self.state is not None:
            ignores |= self.state.get_process_baseline(session.session_id)
        ignores |= self._captures.get(session.handle, set())
        ignores |= self._infra
        return ignores

    # -- the polls -------------------------------------------------------------------

    def _sync_polls(self) -> None:
        records = list(self._records())
        if records and self._process_poll is None:
            self._process_poll = self._timeout_add(PROCESS_POLL_MS, self._poll_processes)
        elif not records and self._process_poll is not None:
            self._source_remove(self._process_poll)
            self._process_poll = None
        attached = any(getattr(r.session, "attached_background", False) for r in records)
        if attached and self._bg_poll is None:
            self._bg_poll = self._timeout_add(BACKGROUND_POLL_MS, self._poll_background_busy)
            self._poll_background_busy()  # the first answer shouldn't wait a beat
        elif not attached and self._bg_poll is not None:
            self._source_remove(self._bg_poll)
            self._bg_poll = None

    def _poll_processes(self) -> bool:
        records = list(self._records())
        if not records:
            self._process_poll = None
            return False
        for record in records:
            session = record.session
            if self.absorb_baseline(session):
                continue  # nothing ever submitted: children are plumbing, not work
            if session.has_background_descendant(self._ignores(session)):
                for key in self._keys(session):
                    self.tracker.mark(key, idle_s=PROCESS_IDLE_S)
            record.refresh_process_facts()
        return True

    def _poll_background_busy(self) -> bool:
        if self._bg_fetching:
            return True
        self._bg_fetching = True

        def work() -> None:
            try:
                busy = self._fetch_busy()
            except Exception:  # noqa: BLE001 - a failed read is "no answer", not a crash
                busy = None
            self._land(self._apply_background_busy, busy)

        threading.Thread(target=work, daemon=True).start()
        return True

    def _apply_background_busy(self, busy_ids: set[str] | None) -> None:
        """One tick's answer: only agents a live session is attached to
        count, and dropping out of that set reads as the run ending."""
        self._bg_fetching = False
        if busy_ids is None:
            return
        watched = {sid for sid in busy_ids if self._attached_session(sid) is not None}
        marks, finishes = self.bg_busy.reading(watched)
        for session_id in marks:
            self.tracker.resume(session_id)
            self.tracker.mark(session_id, idle_s=BACKGROUND_IDLE_S)
        for session_id in finishes:
            session = self._attached_session(session_id)
            if session is not None and session.progress is not None:
                session.progress.turn_ended()
            self.tracker.finish(session_id, grace_s=PROGRESS_FINISH_GRACE_S)

    def _attached_session(self, session_id: str):
        """The live session attached to the conversation *session_id* runs
        as, through its forward chain, newest first."""
        chain = self.state.forward_chain(session_id) if self.state is not None else [session_id]
        for sid in reversed(list(chain)):
            session = self._session_for(sid)
            if session is not None:
                return session
        return None

    def stop(self) -> None:
        self.tracker.stop()
        self.judge.stop()
        for source in (self._process_poll, self._bg_poll):
            if source is not None:
                self._source_remove(source)
        self._process_poll = self._bg_poll = None
