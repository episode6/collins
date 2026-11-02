# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The service's claude CLI, as this client knows it.

The CLI runs where the service runs, so whether there is one, where it is
and what a typed path is worth are the service's answers: `service.status`'s
``cli`` at connect (`set_found`) and `cli.check` after it. Never this
process's PATH or disk: a window started from a desktop launcher has a PATH
without ``~/.local/bin`` while the service, with the login shell's PATH,
runs `claude` fine, and a client on another machine has another disk
altogether. The welcome gate (welcomegate), the welcome dialog's CLI group
(welcome) and Preferences' CLI row read from here; the answer they collect
is the ``claude_cli_path`` setting, which the service puts on its own PATH
as it is written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import apilink, clisetup, providers
from .api import protocol
from .api.protocol import RequestRefused

log = logging.getLogger(__name__)

# Where the service's `claude` lookup lands, "" when it finds none, None
# when the service hasn't said (not asked yet, or a build older than the
# field).
_found: str | None = None


def set_found(found: object) -> None:
    """Record the service's word (a `service.status` reply's ``cli``, a
    `cli.check` reply's ``found``); anything but text is "hasn't said"."""
    global _found
    _found = found if isinstance(found, str) else None


def found_at() -> str | None:
    """Where the service's `claude` is, or None for none or not known."""
    return _found or None


def missing() -> bool:
    """Whether the service said it has no CLI. Not knowing is not missing:
    a service that never said must not bring up an ask whose answer it
    could not take (rule 4: an undocumented surface fails soft)."""
    return _found == ""


def available_providers() -> list[providers.Provider]:
    """The agents the client offers to launch: every provider, unless the
    service said it has no CLI. The service assembles every command from
    its own lookup (PR-3.0 item 1), so this client's PATH says nothing
    about it: `Provider.available()` is the service's question, never
    asked here."""
    return [] if missing() else list(providers.ALL_PROVIDERS)


def default_provider() -> providers.Provider:
    """The agent a plain "new session" starts: the first one offered, else
    Claude (as `providers.default_provider` answers on the service)."""
    offered = available_providers()
    return offered[0] if offered else providers.get_provider("claude")


def detect() -> str:
    """The service's pre-fill for the ask: the first known location on its
    machine that would be accepted, "" for none. A blocking call, made once
    as the welcome dialog is built (a few stats on the service). Nothing
    without `internal`, where `cli.check` is not served (D5)."""
    if not apilink.has(protocol.CAP_INTERNAL):
        return ""
    try:
        reply = apilink.call({"t": "cli.check"})
    except RequestRefused as refusal:
        log.info("remotecli: cli.check refused: %s", refusal.msgid)
        return ""
    set_found(reply.get("found"))
    return reply.get("detected") or ""


def check(path: str, on_verdict: Callable[[str], None]) -> None:
    """Judge *path* on the service; *on_verdict* gets clisetup's verdict on
    the main loop, or `clisetup.MISSING` when the service can't say (not
    connected, an older service). Never blocks: it runs on every keystroke.
    It rides the primary like `state.set`, so a check sent after a write
    sees the write applied."""
    link = apilink.current()
    if link is None or not apilink.has(protocol.CAP_INTERNAL):
        on_verdict(clisetup.MISSING)
        return

    def replied(fields: dict) -> None:
        set_found(fields.get("found"))
        on_verdict(fields.get("status") or clisetup.MISSING)

    def refused(refusal: RequestRefused) -> None:
        log.info("remotecli: cli.check refused: %s", refusal.msgid)
        on_verdict(clisetup.MISSING)

    link.send({"t": "cli.check", "path": path}, replied, refused)


def refresh(on_done: Callable[[], None] | None = None) -> None:
    """Ask the service where its `claude` is now (after an answer was
    written, or cleared), and run *on_done* once it said."""
    link = apilink.current()
    if link is None or not apilink.has(protocol.CAP_INTERNAL):
        if on_done is not None:
            on_done()
        return

    def replied(fields: dict) -> None:
        set_found(fields.get("found"))
        if on_done is not None:
            on_done()

    def refused(refusal: RequestRefused) -> None:
        log.info("remotecli: cli.check refused: %s", refusal.msgid)
        if on_done is not None:
            on_done()

    link.send({"t": "cli.check"}, replied, refused)
