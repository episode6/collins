"""The shim is exercised the way the agent CLI runs it: as a real subprocess
speaking MCP over its pipes, against a threaded socket server standing in for
Collins. Nothing here imports the shim module — its import-cleanliness under
`python -m` is part of what's being tested."""

import json
import os
import queue
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from collins import mcptools

pytestmark = pytest.mark.skipif(
    not hasattr(socket, "AF_UNIX"), reason="needs Unix sockets"
)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeCollins:
    """A scriptable stand-in for the app-side socket service.

    `mode` picks the personality: "ok" answers everything, "error" fails tool
    calls the way the app's dispatch does, "silent" reads requests and never
    replies (a wedged main loop, from the shim's point of view).
    """

    def __init__(self, path, mode="ok", tools=None):
        self.mode = mode
        self.tools = tools if tools is not None else mcptools.TOOLS
        self.hellos = []
        self.requests = []
        self._connections = []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                outer._connections.append(self.connection)
                first = self.rfile.readline()
                if not first:
                    return
                outer.hellos.append(json.loads(first))
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    request = json.loads(raw)
                    outer.requests.append(request)
                    if outer.mode == "silent":
                        continue
                    self.wfile.write(mcptools.encode_message(outer._reply(request)))

        self.server = socketserver.ThreadingUnixStreamServer(str(path), Handler)
        self.server.daemon_threads = True
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def _reply(self, request):
        if request["op"] == "list":
            return {"id": request["id"], "ok": True, "tools": self.tools}
        if self.mode == "error":
            return {"id": request["id"], "ok": False, "error": "no tab for that pid"}
        title = request.get("args", {}).get("title")
        return {"id": request["id"], "ok": True, "message": f"Renamed to {title}"}

    def stop(self):
        """Die the way the real app does: listening socket *and* every open
        connection gone at once — shutdown() alone leaves handler threads
        happily serving established connections."""
        self.server.shutdown()
        self.server.server_close()
        for connection in self._connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        try:
            os.unlink(self.server.server_address)
        except OSError:
            pass


class Shim:
    """One running shim subprocess plus a line-reader off its stdout."""

    def __init__(self, socket_path=None):
        env = os.environ.copy()
        env.pop("COLLINS_MCP_SOCKET", None)
        env.pop("COLLINS_SHIM_LOG", None)
        env["PYTHONPATH"] = str(_REPO_ROOT)
        if socket_path is not None:
            env["COLLINS_MCP_SOCKET"] = str(socket_path)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "collins.mcp_shim"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.raw_lines = []
        self._replies = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._next_id = 0

    def _read(self):
        for line in self.proc.stdout:
            self.raw_lines.append(line)
            self._replies.put(line)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def send_raw(self, data: bytes):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def read_reply(self, timeout=10):
        return json.loads(self._replies.get(timeout=timeout))

    def request(self, method, params=None, timeout=10):
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        reply = self.read_reply(timeout=timeout)
        assert reply["id"] == self._next_id
        return reply

    def handshake(self):
        reply = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        })
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply

    def call_tool(self, name, arguments, timeout=10):
        return self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        # Let the reader drain stdout to EOF before anyone reads raw_lines.
        self._reader.join(timeout=5)


@pytest.fixture
def shim_factory(tmp_path):
    shims = []
    servers = []

    def make_shim(socket_path=None):
        shim = Shim(socket_path)
        shims.append(shim)
        return shim

    def make_server(path=None, **kwargs):
        server = FakeCollins(path if path is not None else tmp_path / "mcp.sock", **kwargs)
        servers.append(server)
        return server

    yield make_shim, make_server
    for shim in shims:
        shim.close()
        if shim.proc.poll() is None:
            shim.proc.kill()
    for server in servers:
        try:
            server.stop()
        except Exception:
            pass


# ---- the MCP surface itself --------------------------------------------------


def test_initialize_echoes_the_clients_protocol_version(shim_factory):
    make_shim, _ = shim_factory
    shim = make_shim()
    result = shim.handshake()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "collins"


def test_ping_pongs(shim_factory):
    make_shim, _ = shim_factory
    shim = make_shim()
    shim.handshake()
    assert shim.request("ping")["result"] == {}


def test_unknown_method_is_method_not_found(shim_factory):
    make_shim, _ = shim_factory
    shim = make_shim()
    shim.handshake()
    reply = shim.request("resources/list")
    assert reply["error"]["code"] == -32601


def test_malformed_json_is_a_parse_error(shim_factory):
    make_shim, _ = shim_factory
    shim = make_shim()
    shim.send_raw(b"this is not json\n")
    reply = shim.read_reply()
    assert reply["error"]["code"] == -32700
    assert reply["id"] is None


def test_call_without_a_tool_name_is_invalid_params(shim_factory):
    make_shim, _ = shim_factory
    shim = make_shim()
    shim.handshake()
    reply = shim.request("tools/call", {"arguments": {}})
    assert reply["error"]["code"] == -32602


def test_eof_on_stdin_exits_cleanly(shim_factory):
    make_shim, _ = shim_factory
    shim = make_shim()
    shim.handshake()
    shim.close()
    assert shim.proc.returncode == 0


def test_broken_stdout_still_exits_cleanly():
    """The CLI closing our stdout mid-teardown must read as shutdown on every
    reply path — including the parse-error one, where garbage arrives on
    stdin after nobody is listening anymore."""
    env = os.environ.copy()
    env.pop("COLLINS_MCP_SOCKET", None)
    env["PYTHONPATH"] = str(_REPO_ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "collins.mcp_shim"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        proc.stdout.close()  # the read end goes away; any reply write now EPIPEs
        proc.stdin.write(b"this is not json\n")
        proc.stdin.flush()
        proc.stdin.close()
        proc.wait(timeout=10)
        assert proc.returncode == 0
        assert proc.stderr.read() == b""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# ---- degradation without Collins ---------------------------------------------


def test_no_socket_configured_still_serves_the_handshake(shim_factory):
    """Env unset — a session must start fine with Collins entirely absent."""
    make_shim, _ = shim_factory
    shim = make_shim()
    shim.handshake()
    assert shim.request("tools/list")["result"] == {"tools": []}


def test_absent_socket_degrades_identically(shim_factory, tmp_path):
    make_shim, _ = shim_factory
    shim = make_shim(tmp_path / "never-bound.sock")
    shim.handshake()
    assert shim.request("tools/list")["result"] == {"tools": []}
    result = shim.call_tool("set_session_title", {"title": "x"})["result"]
    assert result["isError"] is True
    assert "Collins is not running" in result["content"][0]["text"]


def test_unresponsive_collins_times_out_without_killing_the_session(shim_factory, tmp_path):
    """Connected but wedged is the one case that reports an error rather than
    an empty list — silence here would masquerade as 'no tools'."""
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    make_server(path, mode="silent")
    shim = make_shim(path)
    shim.handshake()
    reply = shim.request("tools/list", timeout=20)
    assert "did not respond in time" in reply["error"]["message"]
    assert shim.request("ping")["result"] == {}  # the shim itself is fine


# ---- forwarding to a live Collins --------------------------------------------


def test_hello_carries_the_shims_own_pid(shim_factory, tmp_path):
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    server = make_server(path)
    shim = make_shim(path)
    shim.handshake()
    shim.request("tools/list")
    assert server.hellos == [{"op": "hello", "pid": shim.proc.pid, "v": 1}]


def test_tools_list_is_served_by_the_app(shim_factory, tmp_path):
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    make_server(path)
    shim = make_shim(path)
    shim.handshake()
    tools = shim.request("tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["set_session_title"]
    assert tools[0]["inputSchema"]["required"] == ["title"]


def test_tool_calls_are_forwarded_and_answered(shim_factory, tmp_path):
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    server = make_server(path)
    shim = make_shim(path)
    shim.handshake()
    result = shim.call_tool("set_session_title", {"title": "Fix the bug"})["result"]
    assert result["isError"] is False
    assert result["content"] == [{"type": "text", "text": "Renamed to Fix the bug"}]
    call = [r for r in server.requests if r["op"] == "call"][0]
    assert call["tool"] == "set_session_title"
    assert call["args"] == {"title": "Fix the bug"}


def test_app_side_errors_become_tool_errors(shim_factory, tmp_path):
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    make_server(path, mode="error")
    shim = make_shim(path)
    shim.handshake()
    result = shim.call_tool("set_session_title", {"title": "x"})["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "no tab for that pid"


def test_reconnects_after_a_collins_restart(shim_factory, tmp_path):
    """The heal path: quit Collins mid-session, relaunch, and the very next
    request works — one clean failure in between, no retry loops."""
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    first = make_server(path)
    shim = make_shim(path)
    shim.handshake()
    assert shim.request("tools/list")["result"]["tools"]
    first.stop()
    result = shim.call_tool("set_session_title", {"title": "lost"})["result"]
    assert result["isError"] is True
    assert "Collins is not running" in result["content"][0]["text"]
    second = make_server(path)
    result = shim.call_tool("set_session_title", {"title": "found"})["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "Renamed to found"
    assert second.hellos == [{"op": "hello", "pid": shim.proc.pid, "v": 1}]


# ---- protocol hygiene --------------------------------------------------------


def test_stdout_carries_nothing_but_protocol_lines(shim_factory, tmp_path):
    """MCP's stdio framing tolerates no stray bytes: every stdout line must be
    a complete JSON-RPC message, even while degrading."""
    make_shim, make_server = shim_factory
    path = tmp_path / "mcp.sock"
    make_server(path)
    shim = make_shim(path)
    shim.handshake()
    shim.request("tools/list")
    shim.call_tool("set_session_title", {"title": "quiet"})
    shim.send({"jsonrpc": "2.0", "method": "notifications/cancelled"})
    shim.send_raw(b"garbage\n")
    shim.read_reply()
    shim.close()
    assert shim.raw_lines  # the reader saw the whole conversation
    for line in shim.raw_lines:
        assert line.endswith(b"\n")
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0"
        assert "result" in message or "error" in message
