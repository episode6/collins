# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's link to the service, as the mirrors use it.

`remotestate.RemoteState` and `remotestore.RemoteStore` (split-service spec
§3.8, §3.15) talk to the service through one `Link`: requests go out by
`call` (wait for the reply; what the loopback does anyway) or `send` (the
reply lands in a callback, the shape a socket will need), and events come
in through `dispatch`, which hands each to the handlers registered for its
type. `LoopbackLink` is the link over `api.loopback.LoopbackClient`, the
app's one connection through Phase 1; the unit tests drive the mirrors
through a fake link that holds replies back, which is how an event landing
during an unanswered write is tested.

A refusal reaches the caller as `RequestRefused` (its `error`, `msgid` and
`details`); a message the protocol does not accept is the sender's bug, and
reaches it as a `RequestRefused` with the code ``invalid`` after being
logged, so a mirror reverts rather than raising into a GTK callback.

GTK-free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .api import protocol
from .api.loopback import RequestRefused

log = logging.getLogger(__name__)

Reply = Callable[[dict], None]
Refused = Callable[[RequestRefused], None]


class Link:
    """What a mirror needs of its connection. Subclasses provide `_request`."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}

    def on(self, event_type: str, handler: Callable[[dict], None]) -> None:
        """Call *handler* with every event of *event_type*."""
        self._handlers.setdefault(event_type, []).append(handler)

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

    def _request(self, message: dict) -> dict:
        raise NotImplementedError


class LoopbackLink(Link):
    """A `Link` over a `LoopbackClient`: build it first, then connect the
    client with `dispatch` as its event callback (`bind`)."""

    def __init__(self) -> None:
        super().__init__()
        self.client = None

    def bind(self, client) -> None:
        self.client = client

    def _request(self, message: dict) -> dict:
        if self.client is None:
            raise RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
        try:
            return self.client.request(message)
        except ValueError as error:
            log.error("link: %s is not a valid request: %s", message.get("t"), error)
            raise RequestRefused(protocol.ERROR_INVALID, str(error), {}) from None
