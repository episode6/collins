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
import os
from collections.abc import Callable

from .api import protocol
from .api.protocol import RequestRefused

__all__ = [
    "Link",
    "RequestRefused",
    "absent",
    "call",
    "current",
    "has",
    "implementation",
    "is_local",
    "service_home",
    "service_host",
    "set_current",
]

log = logging.getLogger(__name__)

Reply = Callable[[dict], None]
Refused = Callable[[RequestRefused], None]


class Link:
    """What a mirror needs of its connection. Subclasses provide `_request`
    (blocking) and may override `send` with a non-blocking form."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}
        # The service's home folder, as its `service.status` said at connect
        # (`home`; the app keeps it here, like the CLI's word): None until
        # said, or from a service older than the field.
        self.service_home: str | None = None
        # The families the service lists in its hello's `caps` (`has`). None
        # is a link with no hello to go by, which says nothing is missing
        # (a test's fake); `SocketLink` starts empty and fills it from each
        # hello.
        self.families: frozenset[str] | None = None

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

    def call(self, message: dict, timeout: float | None = None) -> dict:
        """Send a request and return its reply's fields. Raises
        `RequestRefused`. *timeout* is how long to wait for the reply
        (the link's default when None): a git run with its own timeout
        waits that long and a little more."""
        if timeout is None:
            return self._request(message)
        return self._request(message, timeout)

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


def is_local() -> bool:
    """Whether this client proved it runs on the service's machine (the
    `local` proof, D11: `SocketLink.local`, which every `connect` clears
    and only a `prove_local` the service took sets, so it is the last
    hello's proof and never an earlier connection's). The one reader of
    the proof (PR-2.8): `App.local` is this,
    and so is every gate of the local extras (§3.12: an external app, a
    `file:` URI on the clipboard, "Reveal" in the file manager, Open in
    Ghostty, a dropped file mentioned by its own path), which are hidden,
    not disabled, for a client that is not local. With no link, False."""
    return bool(getattr(_current, "local", False))


def has(cap: str) -> bool:
    """Whether the service serves the family *cap* (a `protocol.CAP_*`), as
    its last hello listed it (server-side spec D5). The window hides a
    surface whose family is missing, as it hides a local extra, and never
    sends that family's requests, so this is asked **before** the send or
    the show; `tests/test_client_families.py` holds every family request to
    it. The last hello stands while the link is down: a service restarting
    does not take its surfaces away for the second it is gone.

    With no link at all (the service's own process, a test, a check driving
    bare widgets) nothing is missing, so a shared module behaves as it did;
    every request there is refused ``gone`` regardless (`call`)."""
    if _current is None:
        return True
    families = getattr(_current, "families", None)
    return True if families is None else cap in families


def absent(cap: str) -> RequestRefused:
    """What a request of a family the service does not list answers, with
    no round trip: the refusal a server would send (``unknown``), for the
    callers that already handle one."""
    return RequestRefused(protocol.ERROR_UNKNOWN, "This service does not offer that", {"family": cap})


def implementation() -> str | None:
    """The server's product name, as its last hello said it (D2): logged,
    and shown under the host in Preferences' Service page. Nothing is gated
    on it. None with no link, before a hello, or when it is not a non-empty
    string of at most `protocol.IMPLEMENTATION_MAX` characters (rule 5)."""
    hello = getattr(_current, "hello", None)
    name = hello.get("implementation") if isinstance(hello, dict) else None
    if isinstance(name, str) and 0 < len(name) <= protocol.IMPLEMENTATION_MAX:
        return name
    return None


def service_home() -> str | None:
    """The service's home folder (PR-3.0 item 3): what its paths are
    abbreviated with (`formatting.display_path`) and where a shell with no
    folder of its own starts. With no link at all (the service's own
    process, a test) it is this process's home, which is the service's.
    None while a link's service has not said: nothing is abbreviated with
    a home that may be another machine's."""
    if _current is None:
        return os.path.expanduser("~")
    home = getattr(_current, "service_home", None)
    return home if isinstance(home, str) and home.startswith("/") else None


def service_host() -> str | None:
    """The service's hostname, as its last hello said it (PR-3.3: what the
    Preferences dialog's Service page is headed with). None with no link,
    before a hello, or when the hello's `host` is not a non-empty string of
    at most `protocol.HOST_MAX` characters (foreign content, rule 5)."""
    hello = getattr(_current, "hello", None)
    host = hello.get("host") if isinstance(hello, dict) else None
    if isinstance(host, str) and 0 < len(host) <= protocol.HOST_MAX:
        return host
    return None


def call(message: dict, timeout: float | None = None) -> dict:
    """`current().call`, refused as ``gone`` when there is no link at all."""
    link = current()
    if link is None:
        raise RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
    return link.call(message, timeout)
