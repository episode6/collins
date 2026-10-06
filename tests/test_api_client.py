# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""`api.client.SocketLink` against a real service in a subprocess (spec
§3.20, D26): a blocking `call` on the main thread, the timeout as
``gone``, a call while disconnected, the subscribe drain filling the
mirrors inside the call, a server close counting as lost while a replaced
connection's late close does not, the 64 KiB / 100 ms ack cadence, and
the local proof read from this side only."""

import os
import shutil
import time

import pytest
from gi.repository import GLib
from liveservice import LiveService

from collins import uistate
from collins.api import protocol
from collins.api import server as api_server
from collins.api.client import SocketLink
from collins.api.protocol import RequestRefused
from collins.remotestate import RemoteState


def pump(seconds=0.5, until=None):
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.iteration(False)
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


@pytest.fixture
def service(tmp_path):
    live = LiveService(tmp_path)
    live.start()
    yield live
    live.stop()
    shutil.rmtree(live.runtime, ignore_errors=True)


@pytest.fixture
def link(service):
    lost = []
    link = SocketLink(service.socket, app_id=service.app_id, device="test", on_lost=lost.append)
    link.lost_reasons = lost
    yield link
    link.shutdown()


def test_a_blocking_call_on_the_main_thread_is_answered(link):
    hello = link.connect()
    assert hello["protocol"] == protocol.PROTOCOL and "debug" in hello["caps"]
    status = link.call({"t": "service.status"})
    assert status["clients"] == 1 and status["pid"] > 0


def test_a_call_while_disconnected_is_gone(link):
    with pytest.raises(RequestRefused) as refused:
        link.call({"t": "service.status"})
    assert refused.value.error == "gone"


def test_an_unanswered_call_times_out_as_gone(link, monkeypatch):
    link.connect()
    monkeypatch.setattr("collins.api.client.CALL_TIMEOUT_S", 0.3)
    monkeypatch.setattr(link, "_send_text", lambda channel, message: None)  # the request never leaves
    started = time.monotonic()
    with pytest.raises(RequestRefused) as refused:
        link.call({"t": "service.status"})
    assert refused.value.error == "gone" and time.monotonic() - started < 2


def test_subscribe_fills_the_mirror_inside_the_call(link, tmp_path):
    link.connect()
    ui = uistate.UiState(tmp_path / "ui.json", {})
    state = RemoteState(link, ui=ui)
    reply = link.call({"t": "subscribe"})
    assert "items" in reply
    # The snapshot's state.set events landed before the reply came back.
    assert state.service_id


def test_the_local_proof_is_read_from_this_side_only(link, service, monkeypatch):
    link.connect()
    sent = []
    real_call = link.call

    def spy(message):
        sent.append(message["t"])
        return real_call(message)

    monkeypatch.setattr(link, "call", spy)
    # The hello names another file: no `local` goes out at all.
    link.hello = {"local_proof": {"path": "/etc/passwd", "length": 32}}
    link.app_id = "com.example.NoSuchService"
    assert link.prove_local() is False and sent == []
    # A length other than the protocol's: nothing either.
    link.app_id = service.app_id
    link.hello = {"local_proof": {"path": api_server.proof_path(service.app_id), "length": 4096}}
    assert link.prove_local() is False and sent == []
    # This side's own proof file for the app id, as the service wrote it.
    link.hello = {"local_proof": {"path": "/etc/passwd", "length": 32}}
    assert link.prove_local() is True and sent == ["local"] and link.local


def test_the_local_flag_is_the_last_hellos_proof(link, service):
    """Review of PR 615 (S3): `local` is a connection's. A link that
    proved it, reconnects and then cannot prove it (the proof no longer
    readable from this side: another machine's service behind the same
    link) reads False, as the service already holds it."""
    link.connect()
    assert link.local is False  # connected, nothing proved yet
    assert link.prove_local() is True and link.local is True
    link.connect()  # a reconnect: the proof is not carried over
    assert link.local is False
    link.app_id = "com.example.NoSuchService"  # this side cannot read a proof now
    assert link.prove_local() is False and link.local is False
    link.app_id = service.app_id
    assert link.prove_local() is True and link.local is True


def test_a_symlinked_or_wrong_mode_proof_is_refused(service, tmp_path):
    from collins.api.client import read_local_proof

    real = api_server.proof_path(service.app_id)
    assert read_local_proof(service.app_id) is not None
    os.chmod(real, 0o644)
    assert read_local_proof(service.app_id) is None
    os.chmod(real, 0o600)
    other = "com.example.Linked"
    directory = api_server.runtime_dir(other)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.symlink(real, api_server.proof_path(other))
    assert read_local_proof(other) is None


def test_the_service_closing_is_lost_and_a_replaced_close_is_not(link, service):
    link.connect()
    first_ws = link._primary.ws
    link.connect()  # the old connections are replaced: their late closes are nobody's
    assert link._primary.ws is not first_ws
    pump(0.5)
    assert link.lost_reasons == []
    service.stop()
    assert pump(5, lambda: bool(link.lost_reasons))
    assert link.connected is False
    with pytest.raises(RequestRefused):
        link.call({"t": "service.status"})


def test_acks_go_every_64k_or_100ms(link, tmp_path):
    link.connect()
    acks = []
    real = link.send_event

    def spy(message):
        if message.get("t") == "ack":
            acks.append((message["pty"], message["offset"], time.monotonic()))
        real(message)

    link.send_event = spy
    client = link.pty_client(lambda *a: None, lambda e: None)
    spawn = {"t": "spawn", "kind": "shell", "cwd": str(tmp_path), "cols": 200, "rows": 50}
    pty = client.request(spawn)["pty"]
    client.request({"t": "attach", "pty": pty, "cols": 200, "rows": 50})
    pump(0.5)
    acks.clear()
    # A few bytes: one ack, on the timer, about ACK_MS later.
    started = time.monotonic()
    link.send_input(pty, b"hi\r")
    assert pump(3, lambda: any(p == pty for p, _o, _t in acks))
    first = next(t for p, _o, t in acks if p == pty)
    assert first - started < 1.0
    acks.clear()
    # Past ACK_BYTES fed: an ack at once (cat echoes what it is sent).
    link.send_input(pty, b"x" * (protocol.ACK_BYTES + 1024) + b"\r")
    assert pump(5, lambda: any(o >= protocol.ACK_BYTES for _p, o, _t in acks))
    offsets = [o for _p, o, _t in acks]
    assert offsets == sorted(offsets)
