# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
#
# Part of the conformance kit for the Collins service API (conformance/),
# licensed MIT like the specification it tests (see conformance/LICENSE),
# unlike the rest of Collins. Standard library only.
"""A WebSocket client over a Unix socket, and plain HTTP on the same socket.

The service listens on one Unix socket and speaks plain HTTP on it: a
WebSocket at `/api/ws` for the frames, `GET /api/blob` and `PUT /api/upload`
beside it. The standard library has no WebSocket client, so this is one:
the opening handshake of RFC 6455, masked frames out, unmasked frames in,
fragments joined, pings answered with pongs, a close frame answered and
reported. A reader thread parses frames into a queue; `recv` takes them
off it with a timeout. Nothing here knows the API: `client.py` does.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import os
import queue
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

DEFAULT_MAX_MESSAGE = 16 * 1024 * 1024  # the largest frame the API accepts


class WebSocketError(Exception):
    """The handshake failed or the peer broke the framing."""


class Closed(WebSocketError):
    """The connection is closed; `code` is the peer's close code or None."""

    def __init__(self, code: int | None = None, reason: str = "") -> None:
        super().__init__(f"websocket closed ({code}: {reason})" if code else "websocket closed")
        self.code = code
        self.reason = reason


def _mask(data: bytes, key: bytes) -> bytes:
    """*data* XORed with the repeating 4-byte *key* (RFC 6455 §5.3)."""
    if not data:
        return b""
    repeated = key * (len(data) // 4) + key[: len(data) % 4]
    return (int.from_bytes(data, "big") ^ int.from_bytes(repeated, "big")).to_bytes(len(data), "big")


class WebSocket:
    """One WebSocket connection to the Unix socket at *path*."""

    def __init__(self, path: str, resource: str = "/api/ws", max_message: int = DEFAULT_MAX_MESSAGE) -> None:
        self.path = path
        self.resource = resource
        self.max_message = max_message
        self._sock: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._reader: threading.Thread | None = None
        self.closed = False
        self.close_code: int | None = None
        self.close_reason = ""

    # -- the handshake --------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(self.path)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {self.resource} HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("the server closed the connection during the handshake")
            head += chunk
            if len(head) > 65536:
                raise WebSocketError("the handshake response is too long")
        header_bytes, rest = head.split(b"\r\n\r\n", 1)
        lines = header_bytes.decode("latin-1").split("\r\n")
        status = lines[0].split(" ", 2)
        if len(status) < 2 or status[1] != "101":
            raise WebSocketError(f"the server did not upgrade: {lines[0]!r}")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise WebSocketError("the server's Sec-WebSocket-Accept is wrong")
        sock.settimeout(None)
        self._sock = sock
        self._buffer = rest
        self._reader = threading.Thread(target=self._read_loop, name="ws-reader", daemon=True)
        self._reader.start()

    # -- sending --------------------------------------------------------------

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None:
            raise Closed(self.close_code, self.close_reason)
        key = os.urandom(4)
        length = len(payload)
        head = bytes([0x80 | opcode])
        if length < 126:
            head += bytes([0x80 | length])
        elif length < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", length)
        frame = head + key + _mask(payload, key)
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError as error:
                raise Closed(self.close_code, self.close_reason) from error

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send_frame(OP_BINARY, data)

    def send_raw_frame(self, opcode: int, payload: bytes) -> None:
        """A frame of any opcode: for a check that wants to send a ping."""
        self._send_frame(opcode, payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self._sock is None:
            return
        try:
            self._send_frame(OP_CLOSE, struct.pack(">H", code) + reason.encode("utf-8"))
        except Closed:
            pass
        self.abort()

    def abort(self) -> None:
        """Drop the socket without a close frame (what a crashed client does)."""
        sock, self._sock = self._sock, None
        self.closed = True
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self._queue.put(None)

    # -- receiving ------------------------------------------------------------

    def _read_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            sock = self._sock  # bound once: `abort` on another thread may clear it mid-read
            if sock is None:
                raise Closed(self.close_code, self.close_reason)
            chunk = sock.recv(65536)
            if not chunk:
                raise Closed(self.close_code, self.close_reason)
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack(">Q", self._read_exact(8))
        if length > self.max_message:
            raise WebSocketError(f"a frame of {length} bytes is over the limit")
        key = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        if masked:
            payload = _mask(payload, key)
        return fin, opcode, payload

    def _read_loop(self) -> None:
        pending_opcode = None
        pending = b""
        try:
            while True:
                fin, opcode, payload = self._read_frame()
                if opcode == OP_PING:
                    try:
                        self._send_frame(OP_PONG, payload)
                    except Closed:
                        pass
                    self._queue.put(("ping", payload))
                    continue
                if opcode == OP_PONG:
                    self._queue.put(("pong", payload))
                    continue
                if opcode == OP_CLOSE:
                    if len(payload) >= 2:
                        (self.close_code,) = struct.unpack(">H", payload[:2])
                        self.close_reason = payload[2:].decode("utf-8", "replace")
                    try:
                        self._send_frame(OP_CLOSE, payload[:2])
                    except Closed:
                        pass
                    break
                if opcode in (OP_TEXT, OP_BINARY):
                    pending_opcode, pending = opcode, payload
                elif opcode == OP_CONT and pending_opcode is not None:
                    pending += payload
                    if len(pending) > self.max_message:
                        raise WebSocketError("a fragmented message is over the limit")
                else:
                    raise WebSocketError(f"unexpected opcode {opcode}")
                if fin:
                    if pending_opcode == OP_TEXT:
                        self._queue.put(("text", pending.decode("utf-8")))
                    else:
                        self._queue.put(("binary", pending))
                    pending_opcode, pending = None, b""
        except (Closed, OSError, WebSocketError, UnicodeDecodeError) as error:
            if not isinstance(error, Closed):
                self._queue.put(("error", str(error)))
        finally:
            self.closed = True
            self._queue.put(None)

    def recv(self, timeout: float | None = None) -> tuple[str, object] | None:
        """The next message as ``(kind, payload)``: kind ``text`` (a str),
        ``binary`` (bytes), ``ping`` / ``pong`` (bytes) or ``error`` (a
        str). None when *timeout* passed with nothing; raises `Closed` once
        the connection is over and the queue is drained."""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is None:
            self._queue.put(None)  # stay closed for every later caller
            raise Closed(self.close_code, self.close_reason)
        return item


# -- plain HTTP on the same socket ------------------------------------------


class UnixHTTPConnection(http.client.HTTPConnection):
    """`http.client` over a Unix socket: for `GET /api/blob` and `PUT /api/upload`."""

    def __init__(self, path: str, timeout: float = 10.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.unix_path)
        self.sock = sock


def http_request(
    path: str, method: str, resource: str, body: bytes | None = None, headers: dict | None = None
) -> tuple[int, dict, bytes]:
    """One HTTP request on the Unix socket at *path*: status, headers, body."""
    connection = UnixHTTPConnection(path)
    try:
        connection.request(method, resource, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        connection.close()
