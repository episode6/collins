# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's copy of the sandbox probe's verdict (PR-1.12a).

The probe — can a bubblewrap box be built on this machine? — runs on the
service (`ServiceCore.start_sandbox_host`), which is where the boxes are
built; a client never runs it. The verdict reaches a client twice: in the
`service.status` reply at connect (`sandbox`: "" when a box can be built,
else why not; absent until the probe has run) and as a `sandbox` event of
`what: "probe"` when it lands. This module holds that copy for every
widget that used to read `sandboxplan.probe_reason()` off the client's own
process (the sidebar's project menu, the new-chat screen, Preferences):
same three answers — None while unknown, "" when available, a reason when
not. GTK-free; the listeners run on whatever thread sets the verdict (the
main loop, where the events land).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

_reason: str | None = None
_listeners: list[Callable[[str | None], None]] = []


def probe_reason() -> str | None:
    """The service's verdict: None while the probe hasn't been heard from,
    "" when a sandbox can be built there, else why not."""
    return _reason


def set_probe_reason(reason: str | None) -> None:
    """The verdict as the service said it (a `service.status` reply's
    `sandbox`, a `sandbox` event's `reason`): kept, and told to the
    listeners when it moved. A non-string is no verdict."""
    global _reason
    value = reason if isinstance(reason, str) else None
    if value == _reason:
        return
    _reason = value
    for listener in list(_listeners):
        try:
            listener(value)
        except Exception:
            log.exception("sandbox status: a listener failed")


def listen(listener: Callable[[str | None], None]) -> Callable[[], None]:
    """Call *listener* with each new verdict; the callable returned stops
    that (a dialog that closes before the verdict lands)."""
    _listeners.append(listener)

    def stop() -> None:
        if listener in _listeners:
            _listeners.remove(listener)

    return stop


def reset() -> None:
    """Forget the verdict and the listeners (tests)."""
    global _reason
    _reason = None
    _listeners.clear()
