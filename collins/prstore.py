"""The one place a session's pull requests are kept, and the one that tells.

Collins shows the same GitHub facts in several places at once — a tab's footer
chips, its docked PR pages, the sidebar row's mark — and each used to hold its
own copy, reconciled through a lattice of hand-wired signals: a refresh in one
surface updated the others only where somebody had remembered to run a wire.
This store is that lattice replaced by a hub. It owns both halves of what the
app knows about pull requests:

* **Status by URL** lives in prstatus's fetch cache, as it always has; the
  store subscribes to it (`prstatus.add_listener`) and re-emits every change as
  a main-loop ``status-changed`` signal, so a fetch made anywhere — a tab's
  poll, the sidebar's sweep, a PR page absorbing its own reply — reaches every
  widget showing that PR without the fetcher knowing who they are.

* **The session → PRs association** — which PRs a session has opened, oldest
  first — is persisted in AppState (``session_prs`` in state.json), and this
  store is its sole writer: every path that used to call
  ``state.set_session_prs`` itself goes through here, and an actual change
  (identity, order, or the status baked into a record) comes out the other
  side as ``session-changed``. An unchanged write is swallowed whole — no
  disk write, no signal — which is also what keeps the subscribers from
  echoing each other into a loop: a tab that adopts a list and writes the
  same one back is a wave that stops at the shore.

Everything here runs on the main loop except `_status_moved`, which is the
prstatus listener and arrives on whatever worker thread fetched; it hops
before emitting. The store itself never runs ``gh`` and never blocks: reads
are dictionary lookups (prstatus.known), and writes are AppState writes.
"""

from __future__ import annotations

from gi.repository import GLib, GObject

from . import prstatus
from .prstatus import PullRequest
from .state import AppState


class PrStore(GObject.Object):
    """The app-wide hub for pull request state. One per SessionStore."""

    __gsignals__ = {
        # A PR URL's fetched status changed — whoever fetched it, and whyever.
        # Always emitted on the main loop. Widgets showing that URL re-read it
        # with prstatus.known; everyone else ignores it.
        "status-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A session's saved PR list changed: a new PR, a reorder, or a record
        # whose baked-in status moved. Emitted after the write is on disk, so
        # a handler that re-reads sees what was written.
        "session-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, state: AppState, dispatch=None) -> None:
        """*dispatch* marshals a callable onto the main loop — GLib.idle_add
        in the app; tests pass a direct call so emissions land inline."""
        super().__init__()
        self._state = state
        self._dispatch = dispatch or GLib.idle_add
        prstatus.add_listener(self._status_moved)

    # -- status fan-out ------------------------------------------------------

    def _status_moved(self, url: str) -> None:
        """prstatus's listener: a fetch changed what is known about *url*.

        Arrives on the fetching thread — a tab's update worker, a sweep pool
        thread — so the emission hops to the main loop, where every handler
        (they all touch widgets) is entitled to run.
        """
        self._dispatch(self._emit_status, url)

    def _emit_status(self, url: str) -> bool:
        self.emit("status-changed", url)
        return False  # a one-shot idle, not a repeating source

    # -- the session → PRs association ---------------------------------------

    def records(self, session_id: str) -> list:
        """The PR records saved for a session, oldest first (see
        prstatus.to_record for the shape)."""
        return self._state.get_session_prs(session_id)

    def prs(self, session_id: str) -> list[PullRequest]:
        """A session's PRs, each wearing the freshest status this run holds.

        The saved record's status is the floor (last run's answer), and
        prstatus.known lays anything fetched since over the top — a pure
        dictionary lookup, so this is safe on the main loop and is how every
        widget should read the list it is about to show.
        """
        return [prstatus.known(pr) for pr in prstatus.from_records(self.records(session_id))]

    def set_records(self, session_id: str, records: list) -> None:
        """Replace a session's saved list wholesale; the write everyone shares.

        An identical list is dropped without a disk write or a signal — that
        equality is what lets subscribers write back what they adopted without
        starting a carousel. Main loop only, like every write here.
        """
        if not session_id or records == self._state.get_session_prs(session_id):
            return
        self._state.set_session_prs(session_id, records)
        self.emit("session-changed", session_id)

    def set_prs(self, session_id: str, prs: list[PullRequest]) -> None:
        """`set_records`, from PullRequests (status is persisted with them)."""
        self.set_records(session_id, prstatus.to_records(prs))

    def attach(self, session_id: str, prs: list[PullRequest]) -> None:
        """Append the PRs of *prs* the session doesn't already have.

        The first-prompt attacher's landing (see prattach): saved-list order
        is respected — the newcomers go after whatever the session has already
        accumulated — and a PR both sides know keeps the saved copy, which
        carries what has been learned about it since.
        """
        saved = prstatus.from_records(self.records(session_id))
        have = {pr.url for pr in saved}
        fresh = [pr for pr in prs if pr.url not in have]
        if fresh:
            self.set_prs(session_id, saved + fresh)
