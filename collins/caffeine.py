"""Caffeine Mode's shut-off timer: the durations on offer, and how long is left.

The button's context menu, the launch setting and the header countdown all read
their options from here, so the three can never drift apart on what "3 hours"
means or which durations exist.
"""

from __future__ import annotations

from .i18n import _

INDEFINITE = "indefinite"  # stays on until it's turned off by hand

# The timed options, in menu order. Their keys ("1h", "2h"…) are persisted in
# state.json and used as menu action targets, so they must stay stable; the
# labels are translated at call time instead of being stored.
_HOURS = (1, 2, 3, 6, 12)

DURATION_KEYS: tuple[str, ...] = tuple(f"{h}h" for h in _HOURS) + (INDEFINITE,)


def duration_seconds(key: str) -> int | None:
    """How long a timer of *key* runs for, or None when it never runs out.

    Anything unrecognised — a hand-edited setting, a key from a newer version —
    reads as indefinite: a bad value can leave Caffeine Mode on too long, which
    the user can see and undo, but must never cut it short on its own.
    """
    for hours in _HOURS:
        if key == f"{hours}h":
            return hours * 3600
    return None


def duration_label(key: str) -> str:
    """The menu/settings label for *key*."""
    seconds = duration_seconds(key)
    if seconds is None:
        return _("Indefinitely")
    hours = seconds // 3600
    return _("1 hour") if hours == 1 else _("{n} hours").format(n=hours)


def format_remaining(seconds: float) -> str:
    """A countdown as m:ss, growing to h:mm:ss while an hour or more is left."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
