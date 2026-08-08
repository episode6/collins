"""SessionToolService, exercised over a real Unix socket.

The service lives on the GLib main loop, so each test runs the loop on the
main thread (with a hard timeout guard — a hung loop must fail the test, not
pytest) while a raw client speaks the wire protocol from a worker thread,
exactly as a shim would. Only Gio/GLib are touched: CI has those without the
GTK stack (see conftest.py).
"""

import json
import os
import socket
import threading

import pytest
from gi.repository import GLib

from collins import mcptools
from collins.mcpserver import SessionToolService

_TIMEOUT_S = 5


def _default_dispatch(pid, tool, args):
    return True, f"pid={pid} tool={tool} args={json.dumps(args)}"


def run_with_client(tmp_path, client_fn, list_tools=None, dispatch=None):
    """Start a service, run *client_fn(sock_path, service)* in a thread while
    the main loop serves it, and return what it returned."""
    sock_path = str(tmp_path / "mcp.sock")
    service = SessionToolService(
        sock_path,
        list_tools=list_tools or (lambda: mcptools.TOOLS),
        dispatch=dispatch or _default_dispatch,
    )
    service.start()
    loop = GLib.MainLoop()
    outcome = {}

    def worker():
        try:
            outcome["value"] = client_fn(sock_path, service)
        except BaseException as exc:  # surface client bugs as test failures
            outcome["error"] = exc
        finally:
            GLib.idle_add(loop.quit)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    guard = GLib.timeout_add_seconds(_TIMEOUT_S, loop.quit)
    loop.run()
    GLib.source_remove(guard)
    service.stop()
    thread.join(timeout=1)
    if "error" in outcome:
        raise outcome["error"]
    assert "value" in outcome, "main loop timed out before the client finished"
    return outcome["value"]


class Client:
    """A shim's-eye view of the socket: blocking line-at-a-time JSON."""

    def __init__(self, sock_path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(_TIMEOUT_S)
        self.sock.connect(sock_path)
        self.reader = self.sock.makefile("rb")

    def send(self, message: dict) -> None:
        self.sock.sendall(json.dumps(message).encode("utf-8") + b"\n")

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read(self) -> dict | None:
        """The next reply, or None when the service closed the connection."""
        line = self.reader.readline()
        return json.loads(line) if line else None

    def hello(self, pid: int | None = None) -> None:
        # The service verifies the declared pid against SO_PEERCRED, so an
        # honest hello carries this process's own pid.
        self.send({"op": "hello", "pid": os.getpid() if pid is None else pid, "v": 1})


# ---- the protocol ------------------------------------------------------------


def test_list_returns_the_injected_tool_table(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "list", "id": 1})
        return c.read()

    reply = run_with_client(tmp_path, client, list_tools=lambda: [{"name": "t"}])
    assert reply == {"id": 1, "ok": True, "tools": [{"name": "t"}]}


def test_call_reaches_dispatch_with_the_hello_pid(tmp_path):
    seen = {}

    def dispatch(pid, tool, args):
        seen.update(pid=pid, tool=tool, args=args)
        return True, "Renamed."

    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "call", "id": 7, "tool": "set_session_title", "args": {"title": "hi"}})
        return c.read()

    reply = run_with_client(tmp_path, client, dispatch=dispatch)
    assert reply == {"id": 7, "ok": True, "message": "Renamed."}
    assert seen == {"pid": os.getpid(), "tool": "set_session_title", "args": {"title": "hi"}}


def test_dispatch_failure_becomes_an_error_reply(tmp_path):
    def dispatch(pid, tool, args):
        return False, "no such session"

    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "call", "id": 2, "tool": "set_session_title", "args": {}})
        return c.read()

    reply = run_with_client(tmp_path, client, dispatch=dispatch)
    assert reply == {"id": 2, "ok": False, "error": "no such session"}


def test_requests_are_answered_in_order_on_one_connection(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "list", "id": 1})
        c.send({"op": "call", "id": 2, "tool": "t", "args": {}})
        return c.read(), c.read()

    first, second = run_with_client(tmp_path, client)
    assert first["id"] == 1
    assert second["id"] == 2


def test_missing_tool_name_is_an_error_not_a_disconnect(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "call", "id": 3})
        return c.read()

    reply = run_with_client(tmp_path, client)
    assert reply["ok"] is False
    assert "tool name" in reply["error"]


# ---- untrusted peers ---------------------------------------------------------
#
# The socket is user-private but any local process of the user's can connect;
# a peer that doesn't open with a well-formed hello, or breaks framing, is
# dropped rather than interpreted.


@pytest.mark.parametrize(
    "first_frame",
    [
        {"op": "list", "id": 1},  # skipped the hello entirely
        {"op": "hello"},  # no pid
        {"op": "hello", "pid": "4242"},  # pid must be an int
        {"op": "hello", "pid": True},  # a bool is not a pid
        {"op": "hello", "pid": -1},
        {"op": "hello", "pid": 0},
    ],
)
def test_a_bad_first_frame_closes_the_connection(tmp_path, first_frame):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.send(first_frame)
        return c.read()

    assert run_with_client(tmp_path, client) is None


def test_a_spoofed_hello_pid_closes_the_connection(tmp_path):
    """The declared pid authorizes the call (the dispatcher walks /proc
    ancestry from it to a tab), so it must be the peer's real pid per
    SO_PEERCRED — a client claiming some other live process's pid (pid 1
    always exists) is dropped, not believed."""

    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello(pid=1)
        return c.read()

    assert run_with_client(tmp_path, client) is None


def test_an_honest_hello_pid_is_accepted(tmp_path):
    """The counterpart guard: SO_PEERCRED verification must not reject the
    genuine article (the shim always sends its own os.getpid())."""

    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello(pid=os.getpid())
        c.send({"op": "list", "id": 1})
        return c.read()

    assert run_with_client(tmp_path, client)["ok"] is True


def test_malformed_json_closes_the_connection(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send_raw(b"{nope\n")
        return c.read()

    assert run_with_client(tmp_path, client) is None


def test_a_request_without_an_id_closes_the_connection(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "list"})
        return c.read()

    assert run_with_client(tmp_path, client) is None


def test_an_unknown_op_closes_the_connection(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "shutdown", "id": 1})
        return c.read()

    assert run_with_client(tmp_path, client) is None


def test_an_oversize_line_closes_the_connection(tmp_path):
    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send_raw(b'{"pad": "' + b"x" * mcptools.MAX_LINE + b'"}\n')
        return c.read()

    assert run_with_client(tmp_path, client) is None


# ---- lifecycle ---------------------------------------------------------------


def test_a_second_connection_is_served_after_the_first_closes(tmp_path):
    def client(sock_path, _service):
        first = Client(sock_path)
        first.hello()
        first.sock.close()
        second = Client(sock_path)
        second.hello()
        second.send({"op": "list", "id": 1})
        return second.read()

    assert run_with_client(tmp_path, client)["ok"] is True


def test_concurrent_connections_are_independent(tmp_path):
    def client(sock_path, _service):
        a, b = Client(sock_path), Client(sock_path)
        a.hello()
        b.hello()
        a.send({"op": "call", "id": 1, "tool": "t", "args": {"who": "a"}})
        b.send({"op": "call", "id": 2, "tool": "t", "args": {"who": "b"}})
        return a.read(), b.read()

    ra, rb = run_with_client(tmp_path, client)
    assert ra["id"] == 1 and '"who": "a"' in ra["message"]
    assert rb["id"] == 2 and '"who": "b"' in rb["message"]


def test_stop_closes_established_connections(tmp_path):
    """A fake Collins that keeps old connections serving after 'stopping'
    is what broke the shim's reconnect test in PR 204 — the real service
    must drop live connections, not only the listener."""

    def client(sock_path, service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "list", "id": 1})
        assert c.read() is not None
        GLib.idle_add(service.stop)
        return c.read()

    assert run_with_client(tmp_path, client) is None


def test_stop_removes_the_socket_file(tmp_path):
    service = SessionToolService(
        str(tmp_path / "mcp.sock"), lambda: [], _default_dispatch
    )
    service.start()
    assert (tmp_path / "mcp.sock").exists()
    service.stop()
    assert not (tmp_path / "mcp.sock").exists()


def test_start_replaces_a_stale_socket_file(tmp_path):
    """The residue of a SIGKILLed instance must not block the next launch."""
    stale = tmp_path / "mcp.sock"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.touch()

    def client(sock_path, _service):
        c = Client(sock_path)
        c.hello()
        c.send({"op": "list", "id": 1})
        return c.read()

    assert run_with_client(tmp_path, client)["ok"] is True


def test_start_refuses_an_overlong_socket_path(tmp_path):
    """Gio silently truncates a path past sun_path's 108 bytes, leaving a
    listener no shim will ever dial — the failure must be loud instead, so
    app startup logs it and skips the feature (observed live: a 108-char
    scratch path 'started' fine while every connection degraded)."""
    service = SessionToolService(
        str(tmp_path / ("x" * 200) / "mcp.sock"), lambda: [], _default_dispatch
    )
    with pytest.raises(OSError):
        service.start()


def test_start_creates_the_runtime_dir(tmp_path):
    service = SessionToolService(
        str(tmp_path / "deep" / "mcp.sock"), lambda: [], _default_dispatch
    )
    service.start()
    try:
        assert (tmp_path / "deep" / "mcp.sock").exists()
    finally:
        service.stop()


# ---- end to end: the real shim against the real service ----------------------
#
# Everything between the agent CLI and the app, live: a shim subprocess (the
# Shim harness from test_mcp_shim, speaking MCP over its stdio exactly as the
# CLI does) relaying through its socket client to this service. The one seam
# neither module's own tests cross.


def test_shim_to_service_end_to_end(tmp_path):
    from test_mcp_shim import Shim

    calls = []

    def dispatch(pid, tool, args):
        calls.append((pid, tool, args))
        return True, "Session renamed."

    def client(sock_path, _service):
        shim = Shim(sock_path)
        try:
            shim.handshake()
            tools = shim.request("tools/list")["result"]["tools"]
            reply = shim.call_tool("set_session_title", {"title": "A better name"})
        finally:
            shim.close()
        return shim.proc.pid, tools, reply["result"]

    shim_pid, tools, result = run_with_client(tmp_path, client, dispatch=dispatch)
    assert [t["name"] for t in tools] == ["set_session_title"]
    assert result == {"content": [{"type": "text", "text": "Session renamed."}], "isError": False}
    # The hello's pid is the shim's own — the /proc ancestry walk starts there.
    assert calls == [(shim_pid, "set_session_title", {"title": "A better name"})]


def test_shim_degrades_cleanly_after_the_service_stops(tmp_path):
    from test_mcp_shim import Shim

    def client(sock_path, service):
        shim = Shim(sock_path)
        try:
            shim.handshake()
            assert shim.request("tools/list")["result"]["tools"]
            GLib.idle_add(service.stop)
            # The dead connection surfaces on the next round trip, which
            # reconnects, fails, and reports the standard degraded answer.
            reply = shim.call_tool("set_session_title", {"title": "x"})
            while not reply["result"].get("isError"):
                reply = shim.call_tool("set_session_title", {"title": "x"})
            return reply["result"]
        finally:
            shim.close()

    result = run_with_client(tmp_path, client)
    assert result["content"][0]["text"] == "Collins is not running"
