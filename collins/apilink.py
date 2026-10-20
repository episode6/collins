# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's link to the service, as the mirrors use it.

`remotestate.RemoteState` and `remotestore.RemoteStore` (split-service spec
§3.8, §3.15) talk to the service through one `Link`: requests go out by
`call` (block for the reply) or `send` (the reply lands in a callback on
the main loop), and events come in through `dispatch`, which hands each to
the handlers registered for its type. `api.client.SocketLink` is the link
over the service's socket (PR-1.12b, D26: two WebSockets per client, `call`
on the sync channel from any thread, `send` and every event on the
primary); the unit tests drive the mirrors through a fake link that holds
replies back, which is how an event landing during an unanswered write is
tested.

A refusal reaches the caller as `RequestRefused` (`api.protocol`'s: its
`error`, `msgid` and `details`); a message the protocol does not accept is
the sender's bug, and reaches it as a `RequestRefused` with the code
``invalid`` after being logged, so a mirror reverts rather than raising
into a GTK callback. A link that is not connected refuses every call with
``gone``.

One `Link` outlives its connections: the handlers registered on it stay
across a reconnect, and the connection manager (`connection.py`) connects
the same link again.

GTK-free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .api import protocol
from .api.protocol import RequestRefused

__all__ = ["Link", "RequestRefused", "call", "current", "set_current"]

log = logging.getLogger(__name__)

Reply = Callable[[dict], None]
Refused = Callable[[RequestRefused], None]


class Link:
    """What a mirror needs of its connection. Subclasses provide `_request`
    (blocking) and may override `send` with a non-blocking form."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}

    def on(self, event_type: str, handler: Callable[[dict], None]) -> None:
        """Call *handler* with every event of *event_type*."""
        self._handlers.setdefault(event_type, []).append(handler)

    def off(self, event_type: str, handler: Callable[[dict], None]) -> None:
        """Stop calling *handler* (a widget that goes away before the link)."""
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def dispatch(self, event: dict) -> None:
        """An event from the service (already validated by the transport)."""
        for handler in list(self._handlers.get(event.get("t"), ())):
            try:
                handler(event)
            except Exception:
                log.exception("link: a %s handler failed", event.get("t"))

    def call(self, message: dict) -> dict:
        """Send a request and return its reply's fields. Raises
        `RequestRefused`."""
        return self._request(message)

    def send(self, message: dict, on_reply: Reply | None = None, on_refused: Refused | None = None) -> None:
        """Send a request; its reply goes to *on_reply*, a refusal to
        *on_refused* (logged when there is none)."""
        try:
            fields = self._request(message)
        except RequestRefused as refusal:
            if on_refused is not None:
                on_refused(refusal)
            else:
                log.warning("link: %s refused: %s", message.get("t"), refusal.msgid)
            return
        if on_reply is not None:
            on_reply(fields)

    def send_event(self, message: dict) -> None:
        """A client event (`tool-reply`, `ack`), validated by the transport."""
        raise NotImplementedError

    def _request(self, message: dict) -> dict:
        raise NotImplementedError


# ---- the app's link ------------------------------------------------------------
#
# The client modules that ask the service for something outside the two
# mirrors (a PR action, a job, the model catalog, a tool's reply: PR-1.11)
# reach it through `current()`: the app's own link (`set_current`, at
# startup), or the one an e2e check driving bare widgets built for itself
# (scripts/e2e_service.py's `harness_link`). With no link `current()` is
# None and `call` refuses with ``gone``: the caller fails soft, visibly.

_current: Link | None = None


def set_current(link: Link | None) -> None:
    global _current
    _current = link


def current() -> Link | None:
    return _current


def call(message: dict) -> dict:
    """`current().call`, refused as ``gone`` when there is no link at all."""
    link = current()
    if link is None:
        raise RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
    return link.call(message)
