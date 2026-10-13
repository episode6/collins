# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The git page's freshness table (split-service spec §3.23, PR-2.2): what
a `git-changed` event, a retry, a map, a landed read or a confirmed state
does to the page — compare now, keep it, retry, confirm, reload — decided
here, GTK-free, and carried out by the page (gitpage.GitPage), so the
sequences the e2e checks walk are unit-tested (should-fix 2 of PR 610's
review; rule 3 of CLAUDE.md).

The page drives one `Freshness` with the `Page` it is at the moment it
asks (mapped, a sidebar mutation in flight, a read in flight, a
working-tree load up) and gets back `Actions` to run: `compare` the
signatures (`_check_root` + `_compare_signatures`), `counted` when that
compare is one the page's `changes_heard` probe counts, `retry` to arm
the retry timer, `reload` to re-read the load, `confirm` to ask the
service for the tree state as it is now (`_confirm_state`). The table
keeps the page's freshness state: the tree state the page drew
(`tree_state`, the seed every watch is sent), a compare owed
(`compare_due`: the page was hidden or a mutation was out), a tree state
kept for later (`deferred_state`: `NO_STATE` when none) and one that
landed beside a read in flight (`watch_stale`).

The rules, as the page had them in widget code:

- A compare asked of a hidden page is owed for its next map; asked while
  a mutation is in flight it is owed and retried, since the mutation's
  own landing (`mutated`) re-seeds the signatures first and its move is
  never reloaded twice; else it runs now.
- A tree state that arrives while a mutation is in flight is kept and
  retried (the service's tick can see a mutation half done); while the
  page is hidden it is kept for the map (§3.15: only a visible page
  reloads); beside a read in flight it is kept for the read's landing;
  else a moved one reloads.
- A read that really starts with no mutation in flight drops the kept
  state: its worker samples the tree after the mutation, so the kept
  one is old news. One parked behind a read in flight, or started while
  a mutation is still out, drops nothing.
- A landed read that drew a state and holds a stale one that differs
  asks for a confirmation (the event's sample may predate the read's
  own); a landing that re-reads at once asks nothing (the re-read's
  worker samples anew). A confirmed state that differs from the drawn
  one, or none at all, reloads; one that matches reloads nothing; one
  answered while another read is out is dropped.
- Dropping the watch (another cwd, the view closed) forgets everything
  owed and kept: it was the other tree's.
"""

from __future__ import annotations

from dataclasses import dataclass

# No tree state kept for after a mutation (a kept one may be None).
NO_STATE = object()


@dataclass(frozen=True)
class Page:
    """The page as it is when the table is asked."""

    mapped: bool
    busy: bool  # a sidebar mutation in flight
    loading: bool  # a read in flight
    working: bool  # a working-tree load (unstaged / staged) is up


@dataclass(frozen=True)
class Actions:
    """What the page runs: the signatures' compare (`counted` when the
    page's probe counts it), the retry timer, a reload of the load, a
    confirmation of the tree state."""

    compare: bool = False
    counted: bool = False
    retry: bool = False
    reload: bool = False
    confirm: bool = False

    def __bool__(self) -> bool:
        return self.compare or self.retry or self.reload or self.confirm


NOTHING = Actions()


class Freshness:
    """The page's freshness state and the table over it (module docstring)."""

    def __init__(self) -> None:
        # The tree state (gitops.tree_state_signature) the page drew, seeded
        # by every working-tree load's own worker: what the watch compares
        # against, and the seed the watch is sent.
        self.tree_state: str | None = None
        # A compare the page could not run (hidden, or a mutation in
        # flight): run on the next map or the retry.
        self.compare_due = False
        # A tree state kept for later (NO_STATE: none).
        self.deferred_state: object = NO_STATE
        # The last tree state an event carried while a read was out:
        # compared when the read lands.
        self.watch_stale: str | None = None

    @property
    def pending(self) -> bool:
        """Whether a map should check now: a compare or a state kept."""
        return self.compare_due or self.deferred_state is not NO_STATE

    # -- the compare half ------------------------------------------------------------------

    def compare(self, page: Page, counted: bool = True) -> Actions:
        """A `git-changed` (or a fresh watch, or `check_now`: *counted*
        False) asks for the signatures' compare."""
        if not page.mapped:
            self.compare_due = True
            return NOTHING
        if page.busy:
            self.compare_due = True
            return Actions(retry=True)
        self.compare_due = False
        return Actions(compare=True, counted=counted)

    # -- the tree state half ---------------------------------------------------------------

    def state_event(self, state: str | None, page: Page) -> Actions:
        """A `git-changed` carried *state* for the page's tree."""
        if not page.working:
            return NOTHING
        if page.busy:
            self.deferred_state = state
            return Actions(retry=True)
        if not page.mapped:
            self.deferred_state = state
            return NOTHING
        return self._moved(state, page)

    def _moved(self, state: str | None, page: Page) -> Actions:
        if page.loading:
            self.watch_stale = state
            return NOTHING
        if state != self.tree_state:
            self.tree_state = state
            return Actions(reload=True)
        return NOTHING

    def take_deferred(self, page: Page) -> Actions:
        """A kept tree state, compared now — unless a mutation is still
        out (its retry takes it) or the page is hidden still."""
        if self.deferred_state is NO_STATE or page.busy or not page.mapped:
            return NOTHING
        state, self.deferred_state = self.deferred_state, NO_STATE
        if not page.working:
            return NOTHING
        return self._moved(state, page)  # type: ignore[arg-type]

    # -- the retry, the reads -------------------------------------------------------------

    def retry(self, page: Page) -> Actions:
        """The retry timer fired: still busy, look again; else the compare
        owed. The kept state is the page's next ask (`take_deferred`,
        with a fresh look: the compare may have started a read, and a
        state beside a read in flight waits for its landing)."""
        if page.busy:
            return Actions(retry=True)
        return self.compare(page) if self.compare_due else NOTHING

    def read_started(self, busy: bool) -> None:
        """A read really starts (past the page's early-outs): with no
        mutation in flight its worker samples the tree after any, so a
        kept state is old news."""
        if not busy:
            self.deferred_state = NO_STATE

    def read_landed(self, state: str | None, working: bool, reread: bool) -> Actions:
        """The read drew *state* (None for a load off the working tree).
        *reread*: the page re-reads at once (an equal ask parked behind
        it), so nothing is confirmed — the re-read's worker samples anew."""
        stale, self.watch_stale = self.watch_stale, None
        self.tree_state = state
        if reread or not working or stale is None or stale == state:
            return NOTHING
        return Actions(confirm=True)

    def confirmed(self, fresh: str | None, loading: bool) -> Actions:
        """The service's tree state as it is now (None: it can't be had)."""
        if loading:
            return NOTHING  # another read is out: its worker samples the state anew
        if fresh is None or fresh != self.tree_state:
            self.tree_state = fresh
            return Actions(reload=True)
        return NOTHING

    def drop(self) -> None:
        """The watch is dropped (another cwd, the view closed): nothing
        owed or kept outlives it. The drawn state is the view's (the page
        clears it with the view)."""
        self.watch_stale = None
        self.compare_due = False
        self.deferred_state = NO_STATE
