# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Constrains native Adwaita tab drag-and-drop to compatible tab views.

Adwaita tab DnD is process-global: a tab dragged from any `Adw.TabView`
can be dropped on any other view's tab bar, and libadwaita (1.9) has no
grouping or accept API to say otherwise. Left alone, a panel-strip shell
tab could be dropped on the *session* tab bar or the editor's *file* tab
bar — and vice versa — parenting a page into machinery that has no idea
what it is.

The one hook every arrival path shares — a drop, `transfer_page`, or
anything else — is the destination view's `page-attached` signal. So
instead of trying to prevent the drop (impossible pre-drop without
touching libadwaita internals), the guard *undoes* it: every tab view
registers with a **group** (the session bar, one per editor, one per
dock covering its strips); a detach records where a page came from; an
attach into a different group answers with a bounce target — the origin
view when it still exists, else another view of the origin group, else
the group's registered fallback (a dock recreating a strip for a page
whose source strip collapsed under it, as a single-page strip does the
moment its only tab is dragged out). Same-group drops (strip to strip
within one dock, session tab reorder across bars) pass untouched — so
native DnD ships *constrained*, not disabled, which is the outcome the
spec's spike gate asked for.

This module is the GTK-free policy, driven entirely by notifications:
`on_detached`/`should_bounce` mirror the view signals, `bounce_target`
resolves where to send an offender. The signal wiring, and the deferred
`transfer_page` that performs a bounce (suppressed here so its own
detach/attach pair can't trigger a counter-bounce ping-pong), live in
paneldnd.py. Tests drive the policy with plain objects.
"""

from __future__ import annotations


class TabGuard:
    """Group bookkeeping for the process's guarded tab views."""

    def __init__(self) -> None:
        self._groups: dict = {}  # view -> group key
        self._fallbacks: dict = {}  # group key -> () -> view | None
        # The one in-flight page: (widget, origin group, origin view).
        # Single-slot on purpose: a drag moves exactly one page, and every
        # detach of interest is followed by its attach before the next
        # detach. A close (detach with no re-attach) merely leaves a stale
        # record the next attach ignores by widget identity — nothing to
        # clean up, nothing to leak.
        self._last_detached: tuple | None = None
        # True while paneldnd is executing a bounce transfer, whose own
        # detach/attach pair must not be judged (it crosses groups by
        # construction and would otherwise bounce forever).
        self.suppressed = False

    # -- registration --------------------------------------------------------

    def register(self, view, group) -> None:
        """Guard *view* as a member of *group*. Pages may move freely
        between views of one group and never between groups."""
        self._groups[view] = group

    def unregister(self, view) -> None:
        self._groups.pop(view, None)

    def set_fallback(self, group, provider) -> None:
        """`provider() -> view | None` supplies a bounce destination when
        *group* has no registered view left to return a page to (a dock
        whose every strip collapsed mid-drag recreates one)."""
        self._fallbacks[group] = provider

    def clear_fallback(self, group) -> None:
        self._fallbacks.pop(group, None)

    # -- signal mirror -------------------------------------------------------

    def on_detached(self, view, widget) -> None:
        """A page whose child is *widget* left *view* (drop, transfer, or
        close — indistinguishable here, and it doesn't matter)."""
        if self.suppressed:
            return
        group = self._groups.get(view)
        if group is not None:
            self._last_detached = (widget, group, view)

    def should_bounce(self, view, widget):
        """Judge *widget*'s page attaching to *view*: `(origin group,
        origin view)` if this arrival crosses groups (bounce it, resolving
        the destination with `bounce_target` when the bounce actually
        runs), else None. Consumes the detach record either way — each
        detach judges one attach."""
        if self.suppressed:
            return None
        record = self._last_detached
        if record is None or record[0] is not widget:
            return None  # freshly created page, or a stale close record
        self._last_detached = None
        _widget, group, origin = record
        if self._groups.get(view, group) == group:
            # Same group — or an unregistered view, which no longer exists
            # in this app; judging those would fight programmatic
            # reparenting we can't see the purpose of.
            return None
        return group, origin

    def bounce_target(self, group, prefer=None):
        """Where to send a bounced page of *group*: *prefer* (its origin
        view) when still registered, else any surviving view of the
        group, else whatever the group's fallback can conjure. Resolved
        at bounce time, not judgment time — the origin may collapse in
        the idle gap between the two."""
        if prefer is not None and self._groups.get(prefer) == group:
            return prefer
        for view, view_group in self._groups.items():
            if view_group == group:
                return view
        provider = self._fallbacks.get(group)
        return provider() if provider is not None else None


# The process-wide instance every guarded view registers with — tab DnD
# is process-global, so its guard is too. Tests build their own.
guard = TabGuard()
