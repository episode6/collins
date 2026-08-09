"""Caffeine Mode's shut-off timer and what it promises to keep awake.

The button's context menu, the launch setting and the header countdown all read
their options from here, so the three can never drift apart on what "3 hours"
means or which durations exist. The button's wording lives here too, so it can
never promise a screen that the current setting lets go dark.
"""

from __future__ import annotations

from .i18n import _

INDEFINITE = "indefinite"  # stays on until it's turned off by hand
WHILE_ACTIVE = "active"  # stays on while any session is working, plus a grace

# How long Caffeine Mode goes on holding the machine awake after the last
# session stops working. Long enough to cover an agent that pauses to think
# between turns or a run picked back up right away, short enough that a
# finished night's work isn't kept awake for nothing. Any session going busy
# again drops it: the grace only ever starts from the last moment of work.
ACTIVE_GRACE_S = 300

# The timed options, in menu order. Their keys ("1h", "2h"…) are persisted in
# state.json and used as menu action targets, so they must stay stable; the
# labels are translated at call time instead of being stored.
_HOURS = (1, 2, 3, 6, 12)

DURATION_KEYS: tuple[str, ...] = tuple(f"{h}h" for h in _HOURS) + (WHILE_ACTIVE, INDEFINITE)


def duration_seconds(key: str) -> int | None:
    """How long a timer of *key* runs for, or None when it has no fixed length.

    None covers both open-ended options — `INDEFINITE`, and `WHILE_ACTIVE`
    (whose deadline comes from the sessions rather than the clock, see
    `follows_activity`) — and anything unrecognised: a hand-edited setting, a
    key from a newer version. A bad value leaving Caffeine Mode on too long is
    something the user can see and undo; cutting it short on its own is not.
    """
    for hours in _HOURS:
        if key == f"{hours}h":
            return hours * 3600
    return None


def follows_activity(key: str) -> bool:
    """Whether *key* asks Caffeine Mode to track the sessions instead of the
    clock. Only the exact key does: an unknown one falls through to indefinite
    above, which keeps the machine awake rather than second-guessing it."""
    return key == WHILE_ACTIVE


def grace_deadline(
    *, working: bool, deadline: float | None, now: float, grace: float = ACTIVE_GRACE_S
) -> float | None:
    """Where a sessions-following Caffeine Mode's shut-off deadline sits.

    The whole rule of `WHILE_ACTIVE`, kept here so it can be tested without a
    display — the app polls it once a second and only has to notice when the
    answer moves (see App._follow_activity):

    - **working**: None. There is nothing to count down to while the machine is
      still being used, and no countdown to show.
    - **idle, with a grace already running**: that same deadline, untouched, so
      it runs down smoothly instead of restarting on every poll.
    - **idle, with none**: a fresh one, *grace* from now. This is what makes any
      burst of work reset the wait — the work cleared the old deadline on its
      way in, so the next quiet moment starts the five minutes over.

    *now* and *grace* only have to share a unit: the app polls in monotonic
    microseconds, while the default is `ACTIVE_GRACE_S` in seconds.
    """
    if working:
        return None
    return now + grace if deadline is None else deadline


def duration_label(key: str) -> str:
    """The menu/settings label for *key*."""
    if follows_activity(key):
        # Short on purpose: it shares a menu with "12 hours", and Preferences
        # ellipsizes a value much longer than this. What it means is spelled
        # out in the button's tooltip and the setting's subtitle.
        return _("Until idle")
    seconds = duration_seconds(key)
    if seconds is None:
        return _("Indefinitely")
    hours = seconds // 3600
    return _("1 hour") if hours == 1 else _("{n} hours").format(n=hours)


def toggle_tooltip(*, on: bool, keep_screen_on: bool, while_active: bool = False) -> str:
    """The Caffeine button's tooltip.

    Whole sentences per case rather than a stitched-together one: translators
    get the sentence, and the wording never claims the screen stays on when
    the setting only holds off suspend.

    *while_active* is the sessions-following mode with something still working:
    there is no countdown to show, so the tooltip is where "it's on because a
    session is busy" gets said. Once they all stop, the grace period counts
    down like any other timer and `countdown_tooltip` takes over.
    """
    if on:
        if while_active:
            if keep_screen_on:
                return _(
                    "Caffeine Mode is on while sessions are working — "
                    "the computer and screen will stay awake"
                )
            return _(
                "Caffeine Mode is on while sessions are working — "
                "the computer will stay awake, the screen may turn off"
            )
        if keep_screen_on:
            return _("Caffeine Mode is on — the computer and screen will stay awake")
        return _("Caffeine Mode is on — the computer will stay awake, the screen may turn off")
    if keep_screen_on:
        return _("Caffeine Mode: keep the computer awake and the screen on")
    return _("Caffeine Mode: keep the computer awake, letting the screen turn off")


def countdown_tooltip(seconds: float, *, keep_screen_on: bool) -> str:
    """The Caffeine button's tooltip while a shut-off timer is running: when it
    ends, and — as everywhere else — what stays awake until it does."""
    time = format_remaining(seconds)
    if keep_screen_on:
        return _("Caffeine Mode turns off in {time} — computer and screen stay awake").format(
            time=time
        )
    return _("Caffeine Mode turns off in {time} — computer stays awake, screen may turn off").format(
        time=time
    )


def format_remaining(seconds: float) -> str:
    """A countdown as m:ss, growing to h:mm:ss while an hour or more is left."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
