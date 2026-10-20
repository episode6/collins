# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The Markdown export over the API (split-service spec §3.23, PR-2.8):
`store.transcript-export {session}` answers the transcript the service's
store names, rendered by `sessions.export_markdown` on a thread, under the
store's title for the session; the request carries no path; a session the
store does not know is `gone`, a transcript the service may not read (rule
5) and a render over the wire's bound are `refused`; a long render travels
chunked; and the client's `remotestore.transcript_export` is that call."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from test_core import _store_core, pump

from collins import apilink, remotestore, sessions
from collins.api import protocol
from collins.api.protocol import RequestRefused


def _entry(kind: str, content) -> str:
    return json.dumps(
        {"type": kind, "timestamp": "2026-10-05T10:00:00Z", "message": {"role": kind, "content": content}}
    )


def _append(session, *lines: str) -> None:
    with session.jsonl_path.open("a") as fh:
        fh.write("\n" + "\n".join(lines) + "\n")


def test_a_sessions_transcript_is_rendered_by_the_service_under_the_stores_title(
    app_state, projects_dir, tmp_path
):
    _root, ids = projects_dir
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        session = store.get_session(ids["alpha1"])
        store.rename(ids["alpha1"], "The export's title")
        _append(
            session,
            _entry("user", "what is in the file?"),
            _entry(
                "assistant",
                [
                    {"type": "text", "text": "Two lines."},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/etc/hosts"}},
                ],
            ),
        )
        text = client.request({"t": "store.transcript-export", "session": ids["alpha1"]})["text"]
        # Exactly what the window wrote from the file before the split.
        assert text == sessions.export_markdown(
            session.jsonl_path, "The export's title", session.session_id, session.cwd
        )
        assert text.startswith("# The export's title\n")
        assert f"- **Session:** `{ids['alpha1']}`" in text
        assert "### You\n\nwhat is in the file?" in text
        assert "### Claude\n\nTwo lines.\n\n*Used `Read`*" in text
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_render_runs_off_the_main_loop(app_state, projects_dir, tmp_path, monkeypatch):
    _root, ids = projects_dir
    core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    main = threading.get_ident()
    ran_on: list[int] = []
    real = sessions.export_markdown

    def spy(*args):
        ran_on.append(threading.get_ident())
        return real(*args)

    monkeypatch.setattr(sessions, "export_markdown", spy)
    try:
        client.request({"t": "store.transcript-export", "session": ids["alpha1"]})
        assert ran_on and all(ident != main for ident in ran_on)
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_worker_that_raises_settles_a_failure(app_state, projects_dir, tmp_path, monkeypatch):
    _root, ids = projects_dir
    core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)

    def boom(*_args):
        raise RuntimeError("no render")

    monkeypatch.setattr(sessions, "export_markdown", boom)
    try:
        with pytest.raises(RequestRefused) as refused:
            client.request({"t": "store.transcript-export", "session": ids["alpha1"]})
        assert refused.value.error == protocol.ERROR_FAILED
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_session_the_store_does_not_know_is_gone(app_state, projects_dir, tmp_path):
    core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        with pytest.raises(RequestRefused) as refused:
            client.request({"t": "store.transcript-export", "session": "nope-nope"})
        assert refused.value.error == protocol.ERROR_GONE
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_request_carries_no_path_and_a_foreign_transcript_is_refused(
    app_state, projects_dir, tmp_path, monkeypatch
):
    _root, ids = projects_dir
    assert set(protocol.TYPES["store.transcript-export"].request.fields) == {"session"}
    # A path a client slips into the request is not a field: dropped before any handler.
    checked = protocol.validate(
        {"id": 1, "t": "store.transcript-export", "session": ids["alpha1"], "path": "/etc/passwd"},
        protocol.CLIENT,
    )
    assert not isinstance(checked, protocol.Refusal) and checked.get("path") is None
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text(_entry("user", "a secret") + "\n")
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        row = SimpleNamespace(jsonl_path=elsewhere, session_id=ids["alpha1"], cwd="/p")
        monkeypatch.setattr(store, "get_session", lambda session_id: row)
        with pytest.raises(RequestRefused) as refused:
            client.request({"t": "store.transcript-export", "session": ids["alpha1"]})
        assert refused.value.error == protocol.ERROR_REFUSED
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_render_over_the_bound_is_refused_not_cut(app_state, projects_dir, tmp_path, monkeypatch):
    _root, ids = projects_dir
    core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    monkeypatch.setattr(protocol, "TRANSCRIPT_EXPORT_MAX", 10)
    try:
        with pytest.raises(RequestRefused) as refused:
            client.request({"t": "store.transcript-export", "session": ids["alpha1"]})
        assert refused.value.error == protocol.ERROR_REFUSED
        assert refused.value.details["limit"] == 10 and refused.value.details["size"] > 10
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_long_render_travels_chunked_and_validates_back():
    """Past a frame the text goes ahead as TAG_BLOB frames (`text` is a
    CHUNKED_FIELD), and the bound in characters always fits CHUNKED_MAX in
    UTF-8."""
    assert protocol.TRANSCRIPT_EXPORT_MAX * 4 <= protocol.CHUNKED_MAX
    text = "# t\n\n" + ("### You\n\nsnowman ☃ and more words on a line\n\n" * 60_000)
    assert len(text.encode()) > protocol.MAX_FRAME
    reply = {"re": 7, "ok": True, "text": text}
    frames, slim = protocol.split_reply(reply)
    assert frames and "text" not in slim and slim["text_chunked"] is True
    assert all(len(frame) <= protocol.MAX_FRAME for frame in frames)
    data = b"".join(protocol.unpack_frame(frame)[1] for frame in frames)
    joined = protocol.join_reply(slim, data)
    assert joined["text"] == text
    assert not isinstance(protocol.validate_response(joined, "store.transcript-export"), protocol.Refusal)
    too_long = {"re": 7, "ok": True, "text": "x" * (protocol.TRANSCRIPT_EXPORT_MAX + 1)}
    assert isinstance(protocol.validate_response(too_long, "store.transcript-export"), protocol.Refusal)


class _Link(apilink.Link):
    def __init__(self, answer) -> None:
        super().__init__()
        self.answer = answer
        self.calls: list[tuple[dict, float | None]] = []

    def call(self, message: dict, timeout: float | None = None) -> dict:
        self.calls.append((message, timeout))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_clients_export_is_that_call_and_a_refusal_reaches_the_caller(monkeypatch):
    link = _Link({"text": "# title\n"})
    monkeypatch.setattr(apilink, "_current", link)
    assert remotestore.transcript_export("abc") == "# title\n"
    assert link.calls == [
        ({"t": "store.transcript-export", "session": "abc"}, remotestore.EXPORT_TIMEOUT_S)
    ]
    link.answer = RequestRefused(protocol.ERROR_GONE, "This session is not here any more", {})
    with pytest.raises(RequestRefused):
        remotestore.transcript_export("abc")
    monkeypatch.setattr(apilink, "_current", None)  # no link: `gone`, never a read of this disk
    with pytest.raises(RequestRefused) as refused:
        remotestore.transcript_export("abc")
    assert refused.value.error == protocol.ERROR_GONE
