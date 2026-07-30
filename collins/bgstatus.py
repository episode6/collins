"""Tracks which agent sessions are running detached (backgrounded), so the
sidebar can give them a yellow "running detached" guide line.

There is no supported push mechanism for background-agent state — no hook
event fires when a session is backgrounded or when a background agent exits —
so the only data source is polling the agent CLI
(`provider.background_agents()`). To stay fresh without a hot poll loop,
refreshes are triggered by a file monitor on each provider's background watch
dir (e.g. Claude's `~/.claude/jobs/` — undocumented, so used strictly as a
wake-up signal, never parsed) plus explicit app events (startup, the /bg close
flow). A ~20s timed poll exists as a fallback behind the
`background_status_poll` setting, default off, so that if the watch dir stops
working under a future CLI, flipping the setting restores a working state
without a code change.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio, GLib

from .providers import available_providers
from .sessions import first_message_uuid

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = 20
_DEBOUNCE_MS = 1000


def fetch_background_ids() -> set[str]:
    """Session ids currently running as background agents, across providers.
    Shells out to the agent CLI — never call on the main thread."""
    ids: set[str] = set()
    for provider in available_providers():
        ids.update(agent.session_id for agent in provider.background_agents())
    return ids


def match_background_fork(
    provider,
    old_id: str,
    cwd: str | None,
    old_uuid: str | None,
    known: set[str],
    unique_cwd: bool = False,
) -> str | None:
    """Look for `old_id`'s detached agent in the provider's agent list.

    Returns the fork's session id, "" when the session detached in place
    (its own id is listed), or None when nothing there is it. Shells out to
    the agent CLI — never call on the main thread.

    Agents already listed before the /bg (`known`) can't be the new one. A
    candidate is the same conversation when its transcript starts with the same
    message uuid — /bg copies the conversation verbatim, uuids included — which
    disambiguates several same-project tabs backgrounded at once (e.g. the quit
    flow). Uuids are unavailable while the fork holds only a metadata stub, so
    matching falls back to the working directory; `unique_cwd` makes that
    fallback demand a single candidate in that directory, for callers with no
    `known` set to narrow things down (see MainWindow._replay_pending_detaches).
    """
    agents = provider.background_agents()
    fresh = [a for a in agents if a.session_id not in known and a.session_id != old_id]
    if any(a.session_id == old_id for a in agents):
        return ""  # detached in place: no fork to record

    def transcript_uuid(agent) -> str | None:
        path = next(
            (p for p in provider.transcripts_for_cwd(agent.cwd) if p.stem == agent.session_id),
            None,
        )
        return first_message_uuid(path) if path is not None else None

    if old_uuid:
        for agent in fresh:
            if transcript_uuid(agent) == old_uuid:
                return agent.session_id
    if not cwd:
        return None
    same_cwd = [
        a
        for a in fresh
        if a.cwd
        and a.cwd == cwd
        # A candidate whose transcript names a different conversation is not
        # this one, however well its directory matches.
        and not (old_uuid and (found := transcript_uuid(a)) and found != old_uuid)
    ]
    if unique_cwd and len(same_cwd) != 1:
        # Ambiguous (or nothing): a wrong pairing would hide a good row and
        # redirect it at someone else's agent, so decline to guess.
        return None
    return same_cwd[0].session_id if same_cwd else None


class BackgroundStatusPoller:
    """Owns `background_ids`, the last-known set of detached session ids.

    `fetch` runs off the main thread and returns the current ids; `on_change`
    runs on the main thread with the ids whose membership changed. Refreshes
    are coalesced: at most one fetch is in flight, and requests landing
    meanwhile queue exactly one follow-up.
    """

    def __init__(
        self,
        fetch: Callable[[], set[str]] = fetch_background_ids,
        on_change: Callable[[set[str]], None] = lambda changed: None,
    ) -> None:
        self.background_ids: set[str] = set()
        self._fetch = fetch
        self._on_change = on_change
        self._monitors: list[Gio.FileMonitor] = []
        self._refresh_running = False
        self._refresh_pending = False
        self._debounce_source: int | None = None
        self._poll_source: int | None = None
        self._stopped = False

    def start(self, watch_dirs: list[Path]) -> None:
        """Monitor the watch dirs as refresh triggers and do a first refresh.
        A dir that can't be monitored (e.g. doesn't exist yet) is skipped —
        behavior degrades to the explicit triggers and the optional poll."""
        for path in watch_dirs:
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None
                )
            except GLib.Error as err:
                log.info("bgstatus: cannot watch %s: %s", path, err.message)
                continue
            monitor.connect("changed", self._on_fs_event, str(path))
            self._monitors.append(monitor)
            log.info("bgstatus: watching %s", path)
        self.refresh()

    def stop(self) -> None:
        self._stopped = True
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []
        for source in (self._debounce_source, self._poll_source):
            if source is not None:
                GLib.source_remove(source)
        self._debounce_source = None
        self._poll_source = None

    def set_polling(self, enabled: bool) -> None:
        """Start or stop the timed-poll fallback (the setting's escape hatch).
        Turning it on refreshes immediately; the file monitors keep running
        either way."""
        if enabled and self._poll_source is None:
            log.info("bgstatus: timed-poll fallback on (every %ss)", _POLL_INTERVAL_S)
            self._poll_source = GLib.timeout_add_seconds(_POLL_INTERVAL_S, self._on_poll_tick)
            self.refresh()
        elif not enabled and self._poll_source is not None:
            log.info("bgstatus: timed-poll fallback off")
            GLib.source_remove(self._poll_source)
            self._poll_source = None

    def refresh(self) -> None:
        if self._stopped:
            return
        if self._refresh_running:
            self._refresh_pending = True
            return
        self._refresh_running = True
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            ids = set(self._fetch())
        except Exception:
            ids = None  # keep the last-known set on unexpected failures
        GLib.idle_add(self._apply, ids)

    def _apply(self, ids: set[str] | None) -> bool:
        self._refresh_running = False
        if self._stopped:
            return GLib.SOURCE_REMOVE
        if ids is not None:
            changed = ids ^ self.background_ids
            self.background_ids = ids
            if changed:
                log.info(
                    "bgstatus: background sessions now %s (changed: %s)",
                    sorted(ids) or "none",
                    sorted(changed),
                )
                self._on_change(changed)
            else:
                log.debug("bgstatus: refresh found no membership change")
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()
        return GLib.SOURCE_REMOVE

    def _on_fs_event(self, _monitor, file, _other, event, path: str) -> None:
        # The watch dir churns while agents run; debounce so a burst of
        # events becomes one refresh.
        log.debug(
            "bgstatus: fs event %s on %s", event.value_nick, file.get_path() or path
        )
        if self._debounce_source is not None:
            return
        log.info("bgstatus: %s changed -> refresh in %sms", path, _DEBOUNCE_MS)
        self._debounce_source = GLib.timeout_add(_DEBOUNCE_MS, self._debounced_refresh)

    def _debounced_refresh(self) -> bool:
        self._debounce_source = None
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _on_poll_tick(self) -> bool:
        log.debug("bgstatus: timed-poll tick")
        self.refresh()
        return GLib.SOURCE_CONTINUE
