# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""`RemoteState`: the client's mirror of the service's `AppState`.

Split-service spec §3.8 "The mirror", D16. The service owns `state.json`
(its `AppState`, inside `service.core.ServiceCore`); the client holds this:
`AppState`'s whole API, every read answered synchronously off a local copy
that the subscribe snapshot fills and the service's `state.set` events
keep. It *is* an `AppState` (a subclass whose `_load` reads only this
device's file and whose `save` writes nothing to disk), so the hundred-odd
call sites that hold `app.state` / `window.state` keep every method and
attribute they used, and none of them learns that the data now lives
elsewhere.

**This device's half stays here.** `get_setting` consults the device's
`UiState` first (a key of `DEVICE_SETTINGS`, or one only ui-state.json
holds) and the mirror second, so no call site learns which side a key
lives on; the device's settings, dock layouts and editor states are read
and written on `self.ui` exactly as `AppState` did, and never cross the
API. `settings` stays the one merged dict (the window hands it to tabs).

**Writes are optimistic.** Every mutator `AppState` has ends in `save()`;
here `save()` diffs each shared key (`state.SHARED_KEYS`) against what was
shown before the mutation, and each change goes out as a `state.set`
request (a map key per entry, the rest whole) while the mirror already
holds the new value: the favorite star, a rename, a reorder, a preference
switch answer at once. A change is *pending* until its reply: a `state.set`
event for a pending key or entry (the service's echo of this very write,
or another client's write) updates the *confirmed* copy only, so the
optimistic value holds until the reply (rule: events never clobber an
unanswered write). On the reply the confirmed value wins if it differs
(the service may have normalised it); on a refusal the mirror reverts to
the confirmed value, the change is announced again (`connect_changed`
listeners, with ``reverted`` set), and the window raises a toast with the service's reason
(`on_refused`, through `i18n._()`). Every change of the mirror is
announced to those listeners, the write made here included, which is how
the store's mirror keeps a row's name and star in step with a name written
straight on the state.

A store mutation (`remotestore.RemoteStore`'s rename, archive, favorite,
...) is the same move with a different request: `request(message, mutate)`
runs the mutation against the mirror with the sends held back, marks what
it changed as pending under that one request, and sends the request
instead of the `state.set`s. The service makes the same change itself and
echoes it.

**Drafts are debounced.** `session_drafts` goes out at most once per 500
ms per draft (`DRAFT_DEBOUNCE_MS`), the latest text, and `flush_drafts` sends
whatever is waiting at once: the window calls it on a stash (a composer's
close), on its own close, on a send that empties a draft and when it loses
focus, and the app on shutdown. Today every write of a draft is one of
those, so each is flushed as it is made and the debounce coalesces only
repeated writes inside one of them; it is there for a writer that saves
as the user types. The service coalesces nothing: each `state.set` it
takes is one write of `state.json`, as each `AppState` save was.

What this module decides on its own: a write made before the snapshot
(nothing does one) is sent like any other; an event for a key this client
does not know (a newer service's) is ignored; `state_file()` names the
local `state.json`, which is the service's file through Phase 1 (one
machine), for the sandbox host's ownership check; the toast's text is
"Not saved: <reason>".

GTK-free: GLib only, for the debounce's timer.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

from . import uistate
from .api.protocol import RequestRefused
from .i18n import _
from .state import (
    DEFAULT_SETTINGS,
    MAP,
    SERVICE_SETTINGS,
    SHARED_KEYS,
    AppState,
    diff_shared,
)

log = logging.getLogger(__name__)

# One request per this many ms per draft, at most (§3.8).
DRAFT_DEBOUNCE_MS = 500

_DRAFTS = "session_drafts"
_MISSING = object()

Mark = tuple[str, "str | None"]  # (key, entry): what a pending write covers


class _SafeArgs(dict):
    """`str.format_map` arguments that leave an unknown placeholder be."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def refusal_text(refusal: RequestRefused) -> str:
    """A refusal as the toast says it: the service's msgid translated here
    (§3.14), its args filled in."""
    reason = _(refusal.msgid or "").format_map(_SafeArgs(refusal.details or {}))
    return _("Not saved: {reason}").format(reason=reason) if reason else _("Not saved")


class RemoteState(AppState):
    """See the module docstring."""

    def __init__(
        self,
        link,
        ui: uistate.UiState | None = None,
        schedule: Callable[[int, Callable[[], bool]], Any] | None = None,
        cancel: Callable[[Any], None] | None = None,
        on_refused: Callable[[str], None] | None = None,
    ) -> None:
        """*link* is an `apilink.Link`; *ui* this device's `UiState` (one
        read off ui-state.json by default); *schedule* / *cancel* the
        draft debounce's timer (GLib's by default); *on_refused* shows a
        refused write's text (the app sets it to a toast)."""
        self._link = link
        self._schedule = schedule or (lambda ms, fn: GLib.timeout_add(ms, fn))
        self._cancel = cancel or GLib.source_remove
        self.on_refused = on_refused
        # The service's values as it last said them (wire form), what was
        # shown before the last mutation (wire form, for the diff), the
        # writes waiting on a reply, and the mutation being captured.
        self._confirmed: dict[str, Any] = {}
        self._shown: dict[str, Any] = {}
        self._pending: dict[Mark, int] = {}
        self._capture: list | None = None
        self._listeners: list[Callable[[str, str | None, bool], None]] = []
        self._draft_timers: dict[str, Any] = {}
        # How many events have landed, per key (any event of it), per
        # key whole (an event with no entry) and per entry: a write takes
        # a snapshot when it goes out, and its reply confirms it only when
        # no event has spoken for its key or entry since (`_since`).
        self._any_events: dict[str, int] = {}
        self._whole_events: dict[str, int] = {}
        self._entry_events: dict[Mark, int] = {}
        super().__init__(migrate=False, device=True, ui=ui)
        for name in SHARED_KEYS:
            self._shown[name] = copy.deepcopy(self.export_key(name))
        link.on("state.set", self._on_state_event)

    # -- loading: this device's half only ------------------------------------------

    def _load(self) -> None:
        ui_settings = {k: v for k, v in self.ui.settings.items() if k not in SERVICE_SETTINGS}
        self._ui_only_keys = frozenset(k for k in ui_settings if k not in DEFAULT_SETTINGS)
        self.settings = {**DEFAULT_SETTINGS, **ui_settings}

    def _write_ui(self) -> None:
        if self.service_id:
            super()._write_ui()
            return
        # Before the snapshot names the service there is no block to keep
        # the service-scoped settings in; the device's settings still save.
        self.ui.settings = {
            k: v for k, v in self.settings.items()
            if self.is_device_setting(k) and k not in uistate.SERVICE_SCOPED_SETTINGS
        }
        self.ui.save()

    # -- reads -------------------------------------------------------------------

    def get_setting(self, key: str):
        """This device's value for a device setting (`UiState` first), the
        service's otherwise (the mirror)."""
        if self.is_device_setting(key):
            if key in uistate.SERVICE_SCOPED_SETTINGS:
                if not self.service_id:
                    return DEFAULT_SETTINGS.get(key)
                return self.ui.get_scoped(self.service_id, key)
            return self.ui.settings.get(key, DEFAULT_SETTINGS.get(key))
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def reset(self) -> None:
        """The link was lost and is back (spec §3.20): every write that
        waited on a reply is forgotten (the link failed it), and the next
        snapshot's events are taken as the service's word on every key.
        What is shown stays until an event moves it."""
        self._pending.clear()
        self._capture = None
        self._confirmed.clear()
        self._any_events.clear()
        self._whole_events.clear()
        self._entry_events.clear()

    def connect_changed(self, listener: Callable[[str, str | None, bool], None]) -> None:
        """Call *listener(key, entry, reverted)* whenever the mirror changes:
        a write made here, an event, a reply that settled on the service's
        value, a refusal that reverted (``reverted`` True)."""
        self._listeners.append(listener)

    def disconnect_changed(self, listener: Callable[[str, str | None, bool], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def is_pending(self, key: str, entry: str | None = None) -> bool:
        """Whether a write of *key* (or of its *entry*) waits on a reply."""
        if self._pending.get((key, None), 0) > 0:
            return True
        if entry is not None:
            return self._pending.get((key, entry), 0) > 0
        return any(n == key and count > 0 for (n, _e), count in self._pending.items())

    # -- writes ------------------------------------------------------------------

    def save(self) -> None:
        """What every mutator ends in: send what it changed (see the module
        docstring). Nothing is written to disk here."""
        changes = self._collect()
        if not changes:
            return
        if self._capture is not None:
            self._capture.extend(changes)
        else:
            for name, entry, value in changes:
                if name == _DRAFTS and entry is not None:
                    self._defer_draft(entry)
                    continue
                self._send_state(name, entry, value)
        # A local write is a change of the mirror like any other: its
        # listeners (the store's rows) hear it now, not at the next refresh.
        for name, entry, _value in changes:
            self._announce(name, entry, False)

    def request(
        self,
        message: dict,
        mutate: Callable[[], None] | None = None,
        applied: Callable[[], None] | None = None,
        on_reply: Callable[[dict], None] | None = None,
    ) -> None:
        """Send a store request whose effect on the state is *mutate*: run
        it against the mirror first (optimistic), hold what it changed as
        pending under this request, call *applied* (the store's mirror
        re-projects its rows), then send. A refusal reverts the lot."""
        changes: list = []
        if mutate is not None:
            self._capture = changes
            try:
                mutate()
            finally:
                self._capture = None
        marks = [(name, entry) for name, entry, _value in changes]
        for mark in marks:
            self._pending[mark] = self._pending.get(mark, 0) + 1
        if applied is not None:
            applied()

        def replied(fields: dict) -> None:
            self._settle(marks, None)
            if on_reply is not None:
                on_reply(fields)

        self._link.send(message, replied, lambda refusal: self._settle(marks, refusal))

    def flush_drafts(self, session_id: str | None = None) -> None:
        """Send the drafts waiting on the debounce now: one session's, or
        every one."""
        entries = list(self._draft_timers) if session_id is None else [session_id]
        for entry in entries:
            if entry in self._draft_timers:
                self._send_draft(entry, from_timer=False)

    def _collect(self) -> list[tuple[str, str | None, Any]]:
        """What changed in the mirror since it was last shown, as
        (key, entry, value); `_shown` moves on to the new values."""
        changes: list[tuple[str, str | None, Any]] = []
        for name, key in SHARED_KEYS.items():
            if not key.writable:
                continue
            current = self.export_key(name)
            shown = self._shown.get(name)
            diff = diff_shared(key, shown, current)
            if not diff:
                continue
            for entry, value in diff:
                changes.append((name, entry, copy.deepcopy(value)))
            self._shown[name] = copy.deepcopy(current)
        return changes

    def _send_state(self, name: str, entry: str | None, value) -> None:
        mark = (name, entry)
        self._pending[mark] = self._pending.get(mark, 0) + 1
        message: dict = {"t": "state.set", "key": name, "value": value}
        if entry is not None:
            message["entry"] = entry
        self._send_write(message, mark, value)

    def _send_write(self, message: dict, mark: Mark, value) -> None:
        """Send one `state.set`. Taken, it is what the service holds unless
        an event for the same key or entry arrived after *this* send went
        (the service's echo, or a later write): that one is the last word.
        Per send, not per mark: two writes of one entry can be in flight at
        once, and an event between them has spoken after the first only."""
        sent_at = self._since(mark)

        def taken(_fields: dict) -> None:
            if self._since(mark) == sent_at:
                self._confirm(mark, value)
            self._settle([mark], None)

        self._link.send(message, taken, lambda refusal: self._settle([mark], refusal))

    def _since(self, mark: Mark) -> tuple[int, int]:
        """The event count that speaks for *mark*: any event of its key
        for a whole-key write; for an entry, the key's whole-key events and
        the entry's own."""
        name, entry = mark
        if entry is None:
            return (self._any_events.get(name, 0), 0)
        return (self._whole_events.get(name, 0), self._entry_events.get(mark, 0))

    def _confirm(self, mark: Mark, value) -> None:
        name, entry = mark
        if entry is None:
            self._confirmed[name] = copy.deepcopy(value)
            return
        confirmed = self._confirmed.get(name)
        if not isinstance(confirmed, dict):
            confirmed = self._confirmed[name] = {}
        if value is None:
            confirmed.pop(entry, None)
        else:
            confirmed[entry] = copy.deepcopy(value)

    def _defer_draft(self, entry: str) -> None:
        if entry in self._draft_timers:
            return  # already waiting: the send reads the newest text
        mark = (_DRAFTS, entry)
        self._pending[mark] = self._pending.get(mark, 0) + 1
        self._draft_timers[entry] = self._schedule(
            DRAFT_DEBOUNCE_MS, lambda: self._send_draft(entry, from_timer=True)
        )

    def _send_draft(self, entry: str, from_timer: bool) -> bool:
        handle = self._draft_timers.pop(entry, None)
        if handle is None:
            return False
        if not from_timer:
            try:
                self._cancel(handle)
            except Exception:  # a timer that already fired
                pass
        mark = (_DRAFTS, entry)
        value = copy.deepcopy((self._shown.get(_DRAFTS) or {}).get(entry))
        message = {"t": "state.set", "key": _DRAFTS, "entry": entry, "value": value}
        self._send_write(message, mark, value)
        return False  # GLib.SOURCE_REMOVE

    def _settle(self, marks: list[Mark], refusal: RequestRefused | None) -> None:
        """A reply (or a refusal) for writes covering *marks*: each that is
        no longer pending shows the service's value, which on a refusal is
        a revert."""
        for mark in marks:
            count = self._pending.get(mark, 0) - 1
            if count > 0:
                self._pending[mark] = count
            else:
                self._pending.pop(mark, None)
        for name, entry in dict.fromkeys(marks):
            if self.is_pending(name, entry):
                continue
            self._show_confirmed(name, entry, reverted=refusal is not None)
        if refusal is not None:
            text = refusal_text(refusal)
            log.warning("state write refused: %s", text)
            if self.on_refused is not None:
                self.on_refused(text)

    def _show_confirmed(self, name: str, entry: str | None, reverted: bool) -> None:
        if name not in self._confirmed:
            return  # nothing known to go back to (before the snapshot)
        confirmed = self._confirmed[name]
        if entry is None:
            if confirmed == self._shown.get(name):
                return
            self.import_key(name, copy.deepcopy(confirmed))
        else:
            target = confirmed.get(entry, _MISSING) if isinstance(confirmed, dict) else _MISSING
            shown = self._shown.get(name)
            current = shown.get(entry, _MISSING) if isinstance(shown, dict) else _MISSING
            if target is current or target == current:
                return
            value = None if target is _MISSING else copy.deepcopy(target)
            if not self.import_entry(name, entry, value):
                return
        self._shown[name] = copy.deepcopy(self.export_key(name))
        self._announce(name, entry, reverted)

    # -- the service's events ---------------------------------------------------------

    def _on_state_event(self, event: dict) -> None:
        name = event.get("key")
        key = SHARED_KEYS.get(name)
        if key is None:
            return  # a key a newer service has; nothing here reads it
        entry = event.get("entry")
        value = event.get("value")
        self._confirm((name, entry), value)
        self._any_events[name] = self._any_events.get(name, 0) + 1
        if entry is None:
            self._whole_events[name] = self._whole_events.get(name, 0) + 1
        else:
            self._entry_events[(name, entry)] = self._entry_events.get((name, entry), 0) + 1
        if entry is None:
            if self._pending.get((name, None), 0) > 0:
                return  # the whole key waits on a reply
            incoming = copy.deepcopy(value)
            if key.form == MAP and isinstance(incoming, dict):
                shown = self._shown.get(name) or {}
                for (pending_name, pending_entry), count in self._pending.items():
                    if pending_name != name or pending_entry is None or count <= 0:
                        continue
                    if pending_entry in shown:
                        incoming[pending_entry] = copy.deepcopy(shown[pending_entry])
                    else:
                        incoming.pop(pending_entry, None)
            self.import_key(name, incoming)
        else:
            if self.is_pending(name, entry):
                return  # the optimistic value holds until the reply
            if not self.import_entry(name, entry, copy.deepcopy(value)):
                log.warning("state: the service sent %s[%s] a value it does not take", name, entry)
                return
        self._shown[name] = copy.deepcopy(self.export_key(name))
        if name == "service_id":
            self._on_service_id()
        self._announce(name, entry, False)

    def _on_service_id(self) -> None:
        for key in uistate.SERVICE_SCOPED_SETTINGS:
            self.settings[key] = self.ui.get_scoped(self.service_id, key)

    def _announce(self, name: str, entry: str | None, reverted: bool) -> None:
        for listener in list(self._listeners):
            try:
                listener(name, entry, reverted)
            except Exception:
                log.exception("state: a change listener failed")
