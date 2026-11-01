#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""A Level 0 server: the floor of the API, for looking at the window on it.

Answers `hello` (with no family in `caps`), `subscribe` (an empty snapshot) and
`service.status` (`version` and `protocol`), and every other request `unknown`,
which is everything `docs/api/index.md` asks of Level 0. The window then shows
a connected, empty sidebar with no error dialog (server-side spec §3.1, the
acceptance of PR-S2):

    python3 scripts/stub_server.py --app-id com.episode6.Collins.Stub &
    COLLINS_APP_ID=com.episode6.Collins.Stub python3 -m collins

A dev script, not a product: `conformance/` is what tests a server.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gi  # noqa: E402

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Soup  # noqa: E402

from collins.api import protocol, server  # noqa: E402

_held = []  # the open connections: a WebsocketConnection nothing holds is collected


_named = {"implementation": "stub-server"}  # --predates: none (spec D18)


def answer(message: dict) -> dict:
    kind, re_id = message.get("t"), message.get("id")
    if kind == "hello":
        return protocol.reply(
            re_id,
            protocol=protocol.PROTOCOL,
            min_protocol=protocol.MIN_PROTOCOL,
            version="0.0.0",
            service_id="stub",
            host="stub",
            caps=[],
            **_named,
        )
    if kind == "subscribe":
        return protocol.reply(re_id, items=0, ptys=0)
    if kind == "service.status":
        return protocol.reply(re_id, version="0.0.0", protocol=protocol.PROTOCOL)
    print(f"unknown: {kind}", flush=True)  # what the window still asks of a server like this
    return protocol.refuse(re_id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": str(kind)})


def on_message(connection, kind, payload) -> None:
    if kind != Soup.WebsocketDataType.TEXT:
        return
    message = protocol.decode(payload.get_data())
    if isinstance(message.get("id"), int):
        if message.get("t") == "subscribe":
            connection.send_text(protocol.encode({"t": "rows", "rows": [], "groups": []}))
        connection.send_text(protocol.encode(answer(message)))


def on_websocket(_server, _msg, _path, connection, *_rest) -> None:
    _held.append(connection)
    connection.connect("message", on_message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-id", default="com.episode6.Collins.Stub")
    parser.add_argument(
        "--predates", action="store_true", help="say hello as a Collins service from before families"
    )
    arguments = parser.parse_args()
    if arguments.predates:
        _named.clear()
    path = server.socket_path(arguments.app_id)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    soup = Soup.Server()
    soup.add_websocket_handler(server.WS_PATH, None, None, on_websocket)
    soup.listen(Gio.UnixSocketAddress.new(path), Soup.ServerListenOptions(0))
    print(f"listening on {path}", flush=True)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
