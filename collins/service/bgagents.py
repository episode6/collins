# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""Background agents on the service (split-service spec §3.22, PR-1.12d).

What `MainWindow` did about /bg until PR-1.12d, moved whole to the machine
whose agent CLI and transcripts it reads, built by
`ServiceCore.start_background()`:

- **The yellow lines.** `bgstatus.BackgroundStatusPoller` keeps the set of
  session ids the agent CLI lists as background agents (``claude agents
  --json``), woken by Gio monitors on each provider's watch directory and,
  behind the `background_status_poll` setting (read here, on the service),
  a timed poll. A row's conversation runs as a background agent when any id
  of its forward chain is listed ("running") or a /bg fed for it waits for
  the list to say so ("pending"): the row item's `background`, which a
  client reads for the yellow line of a row it has no tab for.
- **The handoff.** `handoff(record)` runs when a session's ``close {mode:
  background}`` arrives: the pending detach is written to the state (what
  a restart replays), the agents listed so far are noted, and a thread asks
  `bgstatus.match_background_fork` once a second for 30 s whether the
  session detached in place or forked under a new id; a fork is recorded
  (`store.record_forward`). Meanwhile every row standing for the session is
  `backgrounding` (disabled), until the match confirms it, the watch gives
  up, or the 45 s safety timer fires; the CLI is nudged off any screen it
  parked on once the detach is confirmed.
- **The gate.** Each row's `can_background`: a live session on this
  service stands for it whose /bg could be tracked (`bgblock.
  background_blocker`; one handoff at a time, app-wide).
- **The replay** of the pending detaches a previous service never saw
  through (at start), and **the repair** of a row's link to its agent (the
  row menu's *Repair session link*, the ``session.repair`` job).
- **The busy feed.** While a live session is attached to a background
  agent, the agent list's word on which agents are working is fetched every
  `activity.BACKGROUND_POLL_MS` and handed to the tracker
  (`tracking.ServiceActivity.background_busy`, the `BackgroundBusyWatch`
  behind it): a detached agent's environment is scrubbed and speaks no
  progress, so that list is the only word there is.

Every output is a store fact (`record_forward`, `set_pending_detach` /
`clear_pending_detach`, `set_backgrounding`, `set_can_background`,
`set_background`) and reaches the clients as `item` fields; `store.flags`
refuses the two a client used to send. GLib and Gio only through the
injected timers and `bgstatus`; nothing here imports GTK.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import bgblock, bgstatus, providers
from ..activity import BACKGROUND_POLL_MS
from ..sessions import first_message_uuid, path_within

log = logging.getLogger(__name__)

# The fork watch: how many times, a second apart, the agent list is asked
# for the session a /bg just detached (the entry appears within seconds).
WATCH_ATTEMPTS = 30
WATCH_INTERVAL_S = 1.0
# How long a /bg's pre-emptive "detached" (and the row's disabled state)
# lasts with no confirmation before it is dropped (the persisted pending
# detach stays: the next start replays it).
PENDING_TIMEOUT_S = 45
# How long after a confirmed detach the CLI gets to leave the terminal on
# its own before it is nudged off a screen it parked on.
NUDGE_DELAY_MS = 700

RUNNING = "running"
PENDING = "pending"


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

    # It advances the handoff's state: PRIORITY_DEFAULT, never default-idle
    # (CI's Xvfb starves those).
    GLib.idle_add(landed, priority=GLib.PRIORITY_DEFAULT)


def _thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, daemon=True).start()


def _watch_dirs() -> list[Path]:
    return [d for p in providers.available_providers() if (d := p.background_watch_dir()) is not None]


class BackgroundAgents:
    """See the module docstring.

    *store* and *state* are the service's; *records()* lists the live agent
    sessions' `hosting.SessionRecord`s; *activity* is the tracker the busy
    feed goes to (None: no feed); *running()* the session ids an agent pty
    on the service runs. The poller, the timers, the threads and the
    landings are GLib's unless a test hands in its own."""

    def __init__(
        self,
        *,
        store,
        state,
        records: Callable[[], list],
        activity=None,
        running: Callable[[], set[str]] = lambda: set(),
        get_setting: Callable[[str], Any] = lambda key: None,
        fetch_ids: Callable[[], set[str]] = bgstatus.fetch_background_ids,
        fetch_busy: Callable[[], set[str]] = bgstatus.fetch_background_busy_ids,
        watch_dirs: Callable[[], list[Path]] = _watch_dirs,
        get_provider: Callable[[str], Any] = providers.get_provider,
        timeout_add: Callable[..., int] | None = None,
        source_remove: Callable[[int], Any] | None = None,
        land: Callable[..., None] | None = None,
        spawn: Callable[[Callable[[], None]], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poller=None,
    ) -> None:
        self.store = store
        self.state = state
        self._records = records
        self.activity = activity
        self._running = running
        self._get_setting = get_setting
        self._fetch_busy = fetch_busy
        self._watch_dirs = watch_dirs
        self._get_provider = get_provider
        self._timeout_add = timeout_add or _glib_timeout_add
        self._source_remove = source_remove or _glib_source_remove
        self._land = land or _glib_land
        self._spawn = spawn or _thread
        self._sleep = sleep
        self.poller = poller or bgstatus.BackgroundStatusPoller(
            fetch=fetch_ids, on_change=self._on_ids_changed
        )
        if poller is not None:
            poller._on_change = self._on_ids_changed
        # A /bg fed and not yet listed: session id -> the safety timer.
        self.pending: dict[str, int] = {}
        # The subset whose detach isn't confirmed yet: their rows stay
        # disabled (`backgrounding`), and the gate is shut app-wide.
        self.detaching: set[str] = set()
        self._busy_poll: int | None = None
        self._busy_fetching = False
        self._stopped = False
        self._refreshed_handler = None

    # -- life -------------------------------------------------------------------------

    def start(self) -> None:
        """Watch, take the first reading, follow the setting, and give every
        /bg a previous service never saw through one more go."""
        self.poller.start(self._watch_dirs())
        self.settings_changed()
        self._refreshed_handler = self.store.connect("refreshed", self._on_refreshed)
        self.replay()
        self.sync_rows()
        self.refresh_affordances()

    def stop(self) -> None:
        self._stopped = True
        self.poller.stop()
        for source in self.pending.values():
            self._source_remove(source)
        self.pending.clear()
        if self._busy_poll is not None:
            self._source_remove(self._busy_poll)
            self._busy_poll = None
        if self._refreshed_handler is not None:
            try:
                self.store.disconnect(self._refreshed_handler)
            except TypeError:
                pass
            self._refreshed_handler = None

    def settings_changed(self) -> None:
        """The `background_status_poll` setting, read on the service: the
        timed-poll fallback follows it."""
        self.poller.set_polling(bool(self._get_setting("background_status_poll")))

    # -- what is background -------------------------------------------------------------

    @property
    def background_ids(self) -> set[str]:
        return self.poller.background_ids

    def chain(self, session_id: str) -> set[str]:
        """Every id a row's conversation has run under (its forward chain)."""
        return set(self.state.forward_chain(session_id))

    def listed(self, session_id: str) -> bool:
        return bool(self.chain(session_id) & self.background_ids)

    def is_detached(self, session_id: str) -> bool:
        """Whether the conversation runs as a background agent, under any of
        its ids — a pending /bg counting until the watch says otherwise."""
        chain = self.chain(session_id)
        return bool(chain & self.background_ids or chain & set(self.pending))

    def detaching_now(self, session_id: str) -> bool:
        return bool(self.chain(session_id) & self.detaching)

    def row_background(self, row_id: str) -> str:
        chain = self.chain(row_id)
        if chain & self.background_ids:
            return RUNNING
        if chain & set(self.pending):
            return PENDING
        return ""

    def sync_rows(self, session_ids=None) -> None:
        """Put the rows' `background` in line: every row, or the rows that
        stand for *session_ids*."""
        if session_ids is None:
            rows = list(self.store.row_ids())
        else:
            rows = []
            for session_id in session_ids:
                rows.extend(self.store.rows_representing(session_id))
        for row_id in dict.fromkeys(rows):
            self.store.set_background(row_id, self.row_background(row_id))

    def session_is_running(self, session_id: str) -> bool:
        """A live agent pty runs the conversation, or a background agent
        does: what the archive sweep skips."""
        chain = self.chain(session_id) | {session_id}
        try:
            running = set(self._running())
        except Exception:
            log.exception("bg: the running sessions could not be read")
            running = set()
        return bool(chain & running) or self.is_detached(session_id)

    def worktree_shared(self, session_id: str, path: str) -> bool:
        """Whether the session runs on as a background agent, or a background
        agent is working in *path* or under it (the archive's worktree ask:
        the half only the service can answer)."""
        if self.is_detached(session_id):
            return True
        for sid in set(self.background_ids):
            session = self.store.get_session(sid)
            if session is not None and session.cwd and path_within(path, session.cwd):
                return True
        return False

    # -- the gate -------------------------------------------------------------------------

    def blocker(self, session) -> str:
        """Why *session* (a live `service.session.Session`) can't be handed
        to the background right now, or ""."""
        session_id = session.session_id
        return bgblock.background_blocker(
            is_session=True,
            supports_detach=session.provider.background_exit() is not None,
            is_fork=bool(session.fork),
            is_sandboxed=bool(session.sandboxed),
            session_id=session_id,
            has_row=bool(session_id and self.store.rows_representing(session_id)),
            detach_in_flight=bool(self.detaching),
        )

    def refresh_affordances(self) -> None:
        """Every row's `can_background`, at once: the gate is app-wide."""
        allowed: set[str] = set()
        for record in list(self._records()):
            if getattr(record, "exited", False):
                continue
            session = record.session
            try:
                if not self.blocker(session):
                    allowed.update(self.store.rows_representing(session.session_id))
            except Exception:
                log.exception("bg: the gate of %s could not be read", getattr(record, "handle", "?"))
        for row_id in self.store.row_ids():
            self.store.set_can_background(row_id, row_id in allowed)

    def sessions_changed(self) -> None:
        """A live session spawned, resolved or ended: the gate and the busy
        feed follow."""
        self.refresh_affordances()
        self.sync_busy_poll()

    def session_ended(self, session_id: str | None) -> None:
        """An agent pty exited. The cached agent list may predate this
        conversation's background job finishing, and nothing else in the
        close path re-polls: refresh, but only when the cache says detached
        (an ordinary exit has no reason to shell out to the CLI)."""
        if session_id and self.chain(session_id) & self.background_ids:
            self.poller.refresh()
        self.sessions_changed()

    # -- the handoff ----------------------------------------------------------------------

    def handoff(self, record) -> None:
        """A session's ``close {mode: background}``: watch for its agent and
        treat it as backgrounded right away (the yellow line, the rows
        disabled) until the agent list confirms it."""
        session = record.session
        session_id = session.session_id
        if not session_id:
            return
        # Marked first, so a watch that lands at once finds it to confirm.
        self.mark_backgrounding(session_id)
        if not session.fork:
            self.watch_fork(record)

    def watch_fork(self, record) -> None:
        """Confirm the session is running detached, and record its successor
        id if the CLI forked one. Current Claude CLIs have been observed
        (2026-07) forking on /bg: the background agent runs under a *new*
        session id whose transcript is a copy of the conversation. The
        session's own id appearing in the agent list is an in-place detach;
        a fresh id with a matching conversation is a fork, recorded old ->
        new so the stale row hides, the user's metadata carries over, and
        opening the old session redirects to the live one."""
        session = record.session
        old_id = session.session_id
        provider = session.provider
        cwd = session.current_agent_cwd()
        old_session = self.store.get_session(old_id)
        old_uuid = first_message_uuid(old_session.jsonl_path) if old_session is not None else None
        # On disk, so a restart mid-handoff can finish the pairing (replay).
        self.state.set_pending_detach(old_id, provider=provider.id, cwd=cwd or "", uuid=old_uuid or "")
        # Finished jobs included, to mirror the matcher's candidate pool. Read
        # here, before the /bg is typed, as the window read it: an agent the
        # /bg itself makes must not be in it.
        try:
            known = {a.session_id for a in provider.background_agents(include_finished=True)}
        except Exception:
            log.exception("bg-watch: the agent list could not be read")
            known = set()

        def work() -> None:
            for attempt in range(WATCH_ATTEMPTS):
                try:
                    found = bgstatus.match_background_fork(provider, old_id, cwd, old_uuid, known)
                except Exception:
                    log.exception("bg-watch: matching %s failed", old_id)
                    found = None
                log.debug("bg-watch: attempt %s for %s: %r", attempt, old_id, found)
                if found is not None:
                    self._land(self._on_backgrounded, record, old_id, found)
                    return
                self._sleep(WATCH_INTERVAL_S)
            log.info("bg-watch: %s never appeared in the agent list; giving up", old_id)
            self._land(self.clear_backgrounding, old_id, "confirmation watch gave up")

        self._spawn(work)

    def _on_backgrounded(self, record, old_id: str, new_id: str) -> None:
        """The session is confirmed running detached (new_id is its fork's
        id, or "" when it detached in place)."""
        if self._stopped:
            return
        log.info("bg-watch: %s confirmed detached (fork id: %s)", old_id, new_id or "none")
        if new_id:
            self.store.record_forward(old_id, new_id)
        self.state.clear_pending_detach(old_id)  # paired; nothing left to replay
        self.confirm_backgrounding(old_id)
        # The agent list shows the detached session already: refresh now, so
        # the line lands even if the watch directory's monitor misses it.
        self.poller.refresh()
        self._timeout_add(NUDGE_DELAY_MS, self._nudge, record)

    def _nudge(self, record) -> bool:
        """The CLI was asked to leave by /bg yet may still own the terminal,
        parked on its agent-list screen: feed the exit keystroke (a no-op
        once the CLI has exited)."""
        if not getattr(record, "exited", False):
            try:
                record.session.nudge_exit()
            except Exception:
                log.exception("bg: nudging %s failed", getattr(record, "handle", "?"))
        return False

    def _set_rows_backgrounding(self, session_id: str, flag: bool) -> None:
        for row_id in self.store.rows_representing(session_id):
            self.store.set_backgrounding(row_id, flag)

    def mark_backgrounding(self, session_id: str) -> None:
        """A /bg was just fed: treat the session as backgrounded right away
        — the yellow line, its rows disabled — until the agent list reports
        it, the watch gives up, or the safety timer fires."""
        if session_id in self.pending:
            return
        log.info("bg-pending: %s marked (detach fed, awaiting confirmation)", session_id)
        self.detaching.add(session_id)
        self.pending[session_id] = self._timeout_add(
            PENDING_TIMEOUT_S * 1000, self._expired, session_id
        )
        self._set_rows_backgrounding(session_id, True)
        self.refresh_affordances()  # the gate closes app-wide
        self.sync_rows([session_id])

    def confirm_backgrounding(self, session_id: str) -> None:
        """The detach is confirmed: the rows are enabled again (opening one
        attaches to the live agent); the assumed-detached state, and so the
        yellow line, stays until the agent list catches up."""
        if session_id not in self.detaching:
            return
        log.info("bg-pending: %s row re-enabled (detach confirmed)", session_id)
        self.detaching.discard(session_id)
        self._set_rows_backgrounding(session_id, False)
        self.refresh_affordances()

    def clear_backgrounding(self, session_id: str, reason: str = "") -> None:
        source = self.pending.pop(session_id, None)
        if source is None:
            return
        log.info("bg-pending: %s cleared (%s)", session_id, reason or "unspecified")
        self._source_remove(source)
        self.state.clear_pending_detach(session_id)
        self.detaching.discard(session_id)
        self._set_rows_backgrounding(session_id, False)
        self.sync_rows([session_id])  # the line follows the agent list again
        self.refresh_affordances()

    def _expired(self, session_id: str) -> bool:
        # Confirmation never arrived (the agent exited right after
        # detaching): stop pretending. The persisted record stays: the agent
        # may still be starting, and the next start gets one more chance.
        log.info("bg-pending: %s expired (safety timeout, never confirmed)", session_id)
        self.pending.pop(session_id, None)
        self.detaching.discard(session_id)
        self._set_rows_backgrounding(session_id, False)
        self.sync_rows([session_id])
        self.refresh_affordances()
        return False

    def _on_ids_changed(self, changed: set[str]) -> None:
        """The agent list's membership moved. A pending /bg is confirmed by
        its conversation turning up (a fork by its fork's id), and the
        lines of every row standing for a changed id follow."""
        live = changed & self.background_ids
        for session_id in list(self.pending):
            if self.chain(session_id) & live:
                self.clear_backgrounding(session_id, "detach confirmed by the agent list")
        self.sync_rows(changed)

    def _on_refreshed(self, _store, _order_changed: bool) -> None:
        """Rows appeared or went: a new row starts with no line, and a
        pending /bg that forked is handed to the fork's row once the store
        has discovered it."""
        for session_id in list(self.pending):
            target = self.state.resolve_forward(session_id)
            if target != session_id and self.store.get_session(target) is not None:
                self.clear_backgrounding(session_id, "fork discovered; row handed off")
        self.sync_rows()
        self.refresh_affordances()

    # -- across restarts -------------------------------------------------------------------

    def replay(self) -> None:
        """Finish the /bg handoffs still in flight when the last service
        stopped. The evidence the pairing needs is persisted with each
        pending detach; a record that matches nothing is dropped (its agent
        is gone). An agent another row already forwards to is spoken for,
        and the pairing is strict (`match_background_fork`'s unique_cwd)."""
        pending = self.state.get_pending_detaches()
        if not pending:
            return
        log.info("bg-replay: %s pending detach(es) to re-check", len(pending))
        claimed = set(self.state.session_forwards.values())
        get_provider = self._get_provider

        def work() -> None:
            for old_id, info in pending.items():
                try:
                    provider = get_provider(info.get("provider") or "claude")
                    found = bgstatus.match_background_fork(
                        provider,
                        old_id,
                        info.get("cwd") or "",
                        info.get("uuid") or "",
                        claimed,
                        unique_cwd=True,
                    )
                except Exception:
                    log.exception("bg-replay: matching %s failed", old_id)
                    found = None
                self._land(self._on_replayed, old_id, found)

        self._spawn(work)

    def _on_replayed(self, old_id: str, found: str | None) -> None:
        if self._stopped:
            return
        if found is None:
            log.info("bg-replay: %s matches no running agent; dropping the record", old_id)
        else:
            log.info("bg-replay: %s paired with %s", old_id, found or "itself (in place)")
            if found:
                self.store.record_forward(old_id, found)
            self.sync_rows([old_id])
        self.state.clear_pending_detach(old_id)

    # -- the repair ------------------------------------------------------------------------

    def repair_inputs(self, session_id: str) -> dict | None:
        """What a repair of the row *session_id* matches with, read on the
        main loop: the end of its forward chain (a row forwarded once may
        have been backgrounded again from the fork), its directory, the
        first-message uuid its transcript carries, and the agents other rows
        already forward to (spoken for). None for a session the store does
        not know."""
        session = self.store.get_session(session_id)
        if session is None:
            return None
        old_id = self.state.resolve_forward(session_id)
        old_session = self.store.get_session(old_id) or session
        return {
            "session": session_id,
            "old": old_id,
            "provider": session.provider,
            "cwd": old_session.cwd or session.cwd or "",
            "uuid": first_message_uuid(old_session.jsonl_path) or "",
            "claimed": sorted(set(self.state.session_forwards.values())),
        }

    def repair_match(self, inputs: dict) -> str | None:
        """The repair's match, off the main loop: the agent's id, "" when
        the session is itself the listed agent, None when nothing matches
        (or more than one candidate does)."""
        provider = self._get_provider(inputs.get("provider") or "claude")
        return bgstatus.match_background_fork(
            provider,
            inputs["old"],
            inputs.get("cwd") or "",
            inputs.get("uuid") or "",
            set(inputs.get("claimed") or ()),
            unique_cwd=True,
        )

    def repair_landed(self, inputs: dict, found: str | None) -> None:
        """The match, back on the main loop: a fork is recorded; either way
        the agent list is the fresher truth now."""
        if found is None:
            return
        old_id = inputs["old"]
        if found:
            log.info("repair: %s linked to background agent %s", old_id, found)
            self.store.record_forward(old_id, found)
        else:
            log.info("repair: %s is itself the listed background agent", old_id)
        self.sync_rows([inputs["session"]])
        self.poller.refresh()

    # -- the busy feed ---------------------------------------------------------------------

    def sync_busy_poll(self) -> None:
        """Poll the agent list for working agents while a live session is
        attached to one, and only then."""
        attached = any(
            getattr(r.session, "attached_background", False)
            for r in list(self._records())
            if not getattr(r, "exited", False)
        )
        if attached and self._busy_poll is None and not self._stopped:
            self._busy_poll = self._timeout_add(BACKGROUND_POLL_MS, self._poll_busy)
            self._poll_busy()  # the first answer shouldn't wait a beat
        elif not attached and self._busy_poll is not None:
            self._source_remove(self._busy_poll)
            self._busy_poll = None

    def _poll_busy(self) -> bool:
        if self._busy_fetching:
            return True
        self._busy_fetching = True

        def work() -> None:
            try:
                busy = self._fetch_busy()
            except Exception:  # noqa: BLE001 - a failed read is "no answer", not a crash
                busy = None
            self._land(self._busy_landed, busy)

        self._spawn(work)
        return True

    def _busy_landed(self, busy_ids: set[str] | None) -> None:
        self._busy_fetching = False
        if self._stopped or self.activity is None:
            return
        self.activity.background_busy(busy_ids)
