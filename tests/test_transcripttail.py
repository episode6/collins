# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The links in a transcript's tail, the service's half (collins.service.
transcripttail; split-service spec §3.23, PR-2.6): what is read from the
last 2 MiB (text blocks, tool inputs and results, nothing else; a partial
first line skipped; an escaped newline never extends a URL), the cache per
size and mtime, a missing file, the bound a reply carries, and
`store.transcript-tail` itself over the in-process harness: a session's
links by its id, no links for a session the store does not know, and a
transcript the service may not tail (rule 5) answering none."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from test_core import _store_core, pump

from collins.api import protocol
from collins.service import transcripttail
from collins.service.core import transcript_path_allowed

URL = "https://github.com/episode6/collins/pull/303/files#diff-abc"
PATH = "collins/linkpatterns.py:319"


def _entry(kind: str, content) -> str:
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}})


@pytest.fixture(autouse=True)
def _fresh_cache():
    transcripttail._cache.clear()
    yield
    transcripttail._cache.clear()


def test_read_links_takes_text_blocks_tool_inputs_and_results(tmp_path):
    jsonl = tmp_path / "s.jsonl"
    lines = [
        _entry("user", f"open {URL}"),
        _entry(
            "assistant",
            [
                {"type": "text", "text": f"Edited {PATH}"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "curl https://x.test/a"}},
            ],
        ),
        _entry("user", [{"type": "tool_result", "content": [{"type": "text", "text": "at /etc/hosts"}]}]),
        json.dumps({"type": "pr-link", "url": "https://github.com/not/from/a/message"}),
        "not json at all",
    ]
    jsonl.write_text("\n".join(lines) + "\n")
    assert transcripttail.read_links(str(jsonl)) == [URL, PATH, "https://x.test/a", "/etc/hosts"]


def test_an_escaped_newline_does_not_extend_a_url(tmp_path):
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(_entry("assistant", [{"type": "text", "text": "https://a.test/b\nnext line"}]) + "\n")
    assert transcripttail.read_links(str(jsonl)) == ["https://a.test/b"]


def test_only_the_tail_is_read_and_its_partial_line_skipped(tmp_path, monkeypatch):
    jsonl = tmp_path / "s.jsonl"
    early = _entry("user", "https://early.test/gone")
    late = _entry("user", "https://late.test/kept")
    jsonl.write_text(early + "\n" + late + "\n")
    monkeypatch.setattr(transcripttail, "TAIL_BYTES", len(late) + 1 + 10)  # cuts `early` mid-line
    assert transcripttail.read_links(str(jsonl)) == ["https://late.test/kept"]


def test_links_are_cached_per_size_and_mtime(tmp_path):
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(_entry("user", "https://one.test/") + "\n")
    first = transcripttail.read_links(str(jsonl))
    assert transcripttail.read_links(str(jsonl)) is first
    with jsonl.open("a") as fh:
        fh.write(_entry("user", "https://two.test/") + "\n")
    assert transcripttail.read_links(str(jsonl)) == ["https://one.test/", "https://two.test/"]


def test_the_cache_holds_a_few_transcripts(tmp_path):
    for index in range(transcripttail.CACHE_ENTRIES + 3):
        jsonl = tmp_path / f"{index}.jsonl"
        jsonl.write_text(_entry("user", f"https://t{index}.test/") + "\n")
        transcripttail.read_links(str(jsonl))
    assert len(transcripttail._cache) == transcripttail.CACHE_ENTRIES


def test_a_missing_file_is_empty(tmp_path):
    assert transcripttail.read_links(str(tmp_path / "nope.jsonl")) == []


def test_bound_keeps_the_most_recent_in_order_and_drops_the_unshowable(monkeypatch):
    links = [f"https://x.test/{n}" for n in range(protocol.TRANSCRIPT_LINKS_MAX + 5)]
    kept = transcripttail.bound(links)
    assert len(kept) == protocol.TRANSCRIPT_LINKS_MAX == 1000
    assert kept == links[5:]  # the oldest five are the ones cut
    too_long = "https://x.test/" + "a" * protocol.TRANSCRIPT_LINK_MAX
    assert transcripttail.bound(["https://a.test/", too_long, "https://b.test/"]) == [
        "https://a.test/",
        "https://b.test/",
    ]
    monkeypatch.setattr(protocol, "TRANSCRIPT_LINKS_BYTES", 80)  # the byte budget binds too
    sized = transcripttail.bound([f"https://x.test/{n:04d}" for n in range(20)])
    assert 0 < len(sized) < 20 and sized[-1] == "https://x.test/0019"


def test_the_reply_of_a_full_bound_validates_and_fits_a_frame():
    links = transcripttail.bound([f"https://x.test/{'p' * 1000}{n}" for n in range(2000)])
    reply = protocol.reply(1, links=links)
    assert not isinstance(protocol.validate_response(reply, "store.transcript-tail"), protocol.Refusal)
    assert len(json.dumps(reply)) < protocol.MAX_FRAME


# -- store.transcript-tail --------------------------------------------------------------


def test_a_sessions_links_are_answered_by_its_id(app_state, projects_dir, tmp_path):
    _root, ids = projects_dir
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        session = store.get_session(ids["alpha1"])
        with session.jsonl_path.open("a") as fh:
            fh.write("\n" + _entry("assistant", [{"type": "text", "text": f"see {URL} and {PATH}"}]) + "\n")
        reply = client.request({"t": "store.transcript-tail", "session": ids["alpha1"]})
        assert reply["links"] == [URL, PATH]
        # Another session's id answers that session's own transcript.
        assert client.request({"t": "store.transcript-tail", "session": ids["beta1"]})["links"] == []
    finally:
        srv.shutdown()
        pump(0.3)


def test_a_session_the_store_does_not_know_has_no_links(app_state, projects_dir, tmp_path):
    core, _store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        assert client.request({"t": "store.transcript-tail", "session": "nope-nope"})["links"] == []
    finally:
        srv.shutdown()
        pump(0.3)


def test_the_request_carries_no_path_and_a_foreign_transcript_is_not_tailed(
    app_state, projects_dir, tmp_path, monkeypatch
):
    _root, ids = projects_dir
    fields = protocol.TYPES["store.transcript-tail"].request.fields
    assert set(fields) == {"session"}  # no field a path could ride in
    # A store row whose transcript is outside the projects and chats
    # directories is not one the service tails (rule 5).
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text(_entry("user", URL) + "\n")
    assert not transcript_path_allowed(str(elsewhere))
    core, store, _poller, srv, client, _ends = _store_core(app_state, tmp_path)
    try:
        row = SimpleNamespace(jsonl_path=elsewhere)
        monkeypatch.setattr(store, "get_session", lambda session_id: row)
        assert client.request({"t": "store.transcript-tail", "session": ids["alpha1"]})["links"] == []
    finally:
        srv.shutdown()
        pump(0.3)
