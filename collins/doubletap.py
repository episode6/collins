# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The "was that the second tap?" half of a double-tap shortcut.

A shortcut that means one thing pressed once and another pressed twice needs
two facts about the press before it: what it acted on, and how long ago. Kept
apart from the widgets that press it so the rules are testable without a GTK
stack (see tests/conftest.py for why that matters) — the caller supplies both
the target and the clock.
"""

from __future__ import annotations


class DoubleTap:
    """One shortcut's memory of the press that a second tap would follow up.

    A press that leaves something worth following up `arm`s the target it
    acted on; the next press asks `follows` whether it is that second tap.
    Arming is per-target, so a tap on something else — another tab, another
    row — is never mistaken for the second half of a double-tap.
    """

    def __init__(self, window_us: int) -> None:
        self.window_us = window_us
        self._target: object | None = None
        self._at = 0

    def arm(self, target: object | None, now: int) -> None:
        """Record that the press at `now` (a monotonic clock, in
        microseconds) acted on `target`. None arms nothing: the press did
        something a second tap has no follow-up for."""
        self._target = target
        self._at = now

    def follows(self, target: object, now: int) -> bool:
        """True when the press at `now` is a second tap on an armed `target`
        soon enough to count. Saying yes consumes the arming, so a third
        press in the same burst is a plain press again — the double-tap's
        own effect is what a second tap does, once."""
        second = target is self._target and now - self._at < self.window_us
        if second:
            self._target = None
        return second

    def forget(self, target: object) -> None:
        """Drop the arming if it names `target` — whatever it stood for is
        going away, and a later object must not inherit its double-tap."""
        if target is self._target:
            self._target = None
