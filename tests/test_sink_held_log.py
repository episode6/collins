# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The socket sink's log of a full ack window: a client that stops acking
freezes its view of a pty, and the log is the only trace of it."""

import logging

from collins.api import protocol, server


class FakeClient:
    device = "laptop"
    client_id = "abcdef0123456789"

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def send_binary(self, frame: bytes) -> None:
        self.frames.append(frame)


def fill(sink: server._Sink) -> None:
    chunk = b"x" * (64 * 1024)
    while sink.sent - sink.acked < protocol.ACK_WINDOW:
        sink.send_output(chunk, 0)


def test_a_full_window_is_logged_once_and_its_release_too(caplog, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    sink = server._Sink(FakeClient(), 7)
    fill(sink)
    with caplog.at_level(logging.INFO, logger=server.log.name):
        sink.send_output(b"held", 0)
        sink.send_output(b"held", 0)
        held = [r for r in caplog.records if "ack window full" in r.getMessage()]
        assert len(held) == 1
        assert "pty 7 for laptop/abcdef01" in held[0].getMessage()
        clock[0] += 2.5
        sink.ack(sink.sent)
    released = [r for r in caplog.records if "window open again after 2.5 s" in r.getMessage()]
    assert len(released) == 1
    assert sink.held_since == 0.0


def test_a_window_that_stays_full_warns_once(caplog, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    sink = server._Sink(FakeClient(), 3)
    fill(sink)
    with caplog.at_level(logging.INFO, logger=server.log.name):
        sink.send_output(b"a", 0)
        clock[0] += server.HELD_WARN_S + 1
        sink.send_output(b"b", 0)
        sink.send_output(b"c", 0)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "output held 11 s with no ack" in warnings[0].getMessage()


def test_an_ack_behind_the_mark_is_logged(caplog):
    sink = server._Sink(FakeClient(), 2)
    sink.send_output(b"y" * 1000, 0)
    sink.ack(1000)
    with caplog.at_level(logging.INFO, logger=server.log.name):
        assert sink.ack(400) == 0
    assert any("an ack at 400 behind the mark 1000" in r.getMessage() for r in caplog.records)
