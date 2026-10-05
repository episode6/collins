# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The PR hub on the service and its client mirror (spec §3.15, PR-1.11):
the `pr` snapshot and events, the hub's three signals and its equality
guard on both sides, an optimistic write and a refused one reverting, and
the gh requests (with the page's data making the round trip whole)."""

import pytest

from collins import prdetail, prstatus, remoteprs
from collins.api import protocol
from collins.api.protocol import RequestRefused
from collins.apilink import Link
from collins.prstore import PrStore
from collins.remoteprs import RemotePrStore
from collins.service import prfeed

URL1 = "https://github.com/o/r/pull/1"
URL2 = "https://github.com/o/r/pull/2"


class State:
    def __init__(self):
        self.session_prs = {}

    def get_session_prs(self, session_id):
        return list(self.session_prs.get(session_id) or [])

    def set_session_prs(self, session_id, prs):
        if prs:
            self.session_prs[session_id] = list(prs)
        else:
            self.session_prs.pop(session_id, None)


class Wire(Link):
    """A client link to a PrFeed over the protocol's validation, with a
    switch that refuses every pr.set."""

    def __init__(self, feed):
        super().__init__()
        self.feed = feed
        self.refuse = False
        self.calls = []

    def deliver(self, event):
        assert isinstance(protocol.validate(dict(event), protocol.SERVICE), protocol.Message)
        self.dispatch(event)

    def _request(self, message):
        self.calls.append(message["t"])
        checked = protocol.validate({**message, "id": 1}, protocol.CLIENT)
        assert isinstance(checked, protocol.Message), checked
        if self.refuse:
            raise RequestRefused(protocol.ERROR_REFUSED, "no", {})
        if checked.type == "pr.set":
            raw = self.feed.set_records(checked)
        else:
            raw = prfeed.handle_gh(checked)
        answer = protocol.validate_response(raw, checked.type)
        return dict(answer.fields)


@pytest.fixture
def hub():
    state = State()
    state.session_prs["s0"] = [{"number": 1, "url": URL1}]
    return PrStore(state, dispatch=lambda fn, *a: fn(*a))


def connect(hub):
    feed = prfeed.PrFeed(hub)
    wire = Wire(feed)
    mirror = RemotePrStore(wire)
    feed.subscribe(wire, wire.deliver)
    signals = []
    mirror.connect("session-changed", lambda _m, sid: signals.append(("changed", sid)))
    mirror.connect("pr-attached", lambda _m, sid, url: signals.append(("attached", sid, url)))
    mirror.connect("status-changed", lambda _m, url: signals.append(("status", url)))
    return feed, wire, mirror, signals


def test_the_snapshot_fills_the_mirror(hub):
    _feed, _wire, mirror, signals = connect(hub)
    assert mirror.records("s0") == [{"number": 1, "url": URL1}]
    assert [pr.url for pr in mirror.prs("s0")] == [URL1]


def test_a_write_is_optimistic_and_its_echo_is_silent(hub):
    _feed, wire, mirror, signals = connect(hub)
    signals.clear()
    mirror.set_records("s1", [{"number": 2, "url": URL2}])
    assert signals == [("changed", "s1"), ("attached", "s1", URL2)]  # once, here
    assert hub.records("s1") == [{"number": 2, "url": URL2}]  # and on the service
    assert wire.calls == ["pr.set"]


def test_the_equality_guard_holds_on_the_client(hub):
    _feed, wire, mirror, signals = connect(hub)
    signals.clear()
    mirror.set_records("s0", [{"number": 1, "url": URL1}])
    assert signals == [] and wire.calls == []


def test_a_change_on_the_service_reaches_the_mirror_with_its_newcomers(hub):
    _feed, _wire, mirror, signals = connect(hub)
    signals.clear()
    hub.attach("s0", [prstatus.PullRequest(number=2, url=URL2)])
    assert [r["url"] for r in mirror.records("s0")] == [URL1, URL2]
    assert signals == [("changed", "s0"), ("attached", "s0", URL2)]
    signals.clear()
    hub.set_records("s0", list(hub.records("s0")))  # unchanged: nothing anywhere
    assert signals == []


def test_a_refused_write_reverts_and_says_so(hub):
    _feed, wire, mirror, signals = connect(hub)
    signals.clear()
    wire.refuse = True
    mirror.set_records("s0", [])
    assert mirror.records("s0") == [{"number": 1, "url": URL1}]
    assert signals == [("changed", "s0"), ("changed", "s0")]
    assert hub.records("s0") == [{"number": 1, "url": URL1}]


def test_a_status_change_is_absorbed_and_signalled(hub, monkeypatch):
    feed, _wire, mirror, signals = connect(hub)
    monkeypatch.setattr(prstatus, "status_entry", lambda url: {"state": "OPEN", "title": "T"})
    absorbed = []
    monkeypatch.setattr(prstatus, "absorb_entry", lambda url, entry: absorbed.append((url, entry)))
    feed._on_status_changed(hub, URL1)
    assert signals[-1] == ("status", URL1)
    assert absorbed == [(URL1, {"state": "OPEN", "title": "T"})]


# -- the gh requests -------------------------------------------------------------


@pytest.fixture
def wired(hub, monkeypatch):
    feed, wire, _mirror, _signals = connect(hub)
    monkeypatch.setattr(remoteprs.apilink, "current", lambda: wire)
    return wire


def test_an_action_runs_on_the_service_and_brings_its_words_back(wired, monkeypatch):
    from collins import practions

    seen = []
    monkeypatch.setattr(practions, "perform", lambda key, pr: seen.append((key, pr.url)) or "not mergeable")
    pr = prstatus.PullRequest(number=1, url=URL1, repository="o/r")
    assert remoteprs.perform("merge", pr) == "not mergeable"
    assert seen == [("merge", URL1)]
    monkeypatch.setattr(practions, "comment", lambda pr, body: None)
    assert remoteprs.comment(pr, "hi") is None


def test_a_refused_action_reads_as_its_failure(wired):
    wired.refuse = True
    assert remoteprs.perform("merge", prstatus.PullRequest(number=1, url=URL1)) == "no"


def _detail():
    pr = prstatus.PullRequest(number=1, url=URL1, repository="o/r", title="Fix", state="OPEN")
    comment = prdetail.PrComment("ada", "2026-01-01T00:00:00Z", "LGTM", "https://github.com/x")
    thread = prdetail.PrThread("PRRT_1", "a.py", 3, False, True, (comment,))
    return prdetail.PullRequestDetail(
        summary=pr,
        body="Body",
        author="ada",
        created_at="2026-01-01T00:00:00Z",
        base_ref="main",
        head_ref="fix",
        base_oid="a" * 40,
        head_oid="b" * 40,
        head_repository="",
        additions=3,
        deletions=1,
        changed_files=1,
        labels=("bug",),
        checks=(prdetail.PrCheck("ci", "PASSED", "https://github.com/c"),),
        timeline=(comment, prdetail.PrReview("bob", "2026-01-02T00:00:00Z", "APPROVED", ""), thread),
        files=(
            prdetail.PrFile("a.py", 3, 1, "@@ -1 +1 @@\n-a\n+b", "MODIFIED"),
            prdetail.PrFile("b.png", 0, 0, None),
        ),
        threads=(thread,),
        viewer_is_author=True,
    )


def test_the_pages_data_makes_the_round_trip_whole(wired, monkeypatch):
    detail = _detail()
    monkeypatch.setattr(prdetail, "fetch", lambda url: detail)
    assert remoteprs.fetch_detail(URL1) == detail
    monkeypatch.setattr(prdetail, "fetch", lambda url: None)
    assert remoteprs.fetch_detail(URL1) is None


def test_a_patch_too_long_for_the_wire_crosses_as_none():
    detail = _detail()
    big = prdetail.PrFile("big.txt", 1, 0, "+" * (prdetail.WIRE_PATCH_MAX + 1))
    record = prdetail.detail_record(prdetail.PullRequestDetail(**{**detail.__dict__, "files": (big,)}))
    assert record["files"][0]["patch"] is None


def test_the_sweep_runs_on_the_service(wired, monkeypatch):
    def fake(targets):
        return {sid: [*prs, prstatus.PullRequest(number=2, url=URL2)] for sid, prs, _cwd in targets}

    monkeypatch.setattr(prstatus, "sweep", fake)
    swept = remoteprs.sweep([("s0", [prstatus.PullRequest(number=1, url=URL1)], "/home/u/p")])
    assert [pr.url for pr in swept["s0"]] == [URL1, URL2]
    assert [pr.url for pr in remoteprs.resync([prstatus.PullRequest(number=1, url=URL1)])] == [URL1, URL2]


def test_a_pr_blob_is_a_url_the_blobcache_fetches(wired, monkeypatch, tmp_path):
    """`fetch_blob` (PR-2.2): `pr.blob` names the URL, the GET (here the
    service's `PrBlobs`, inline) answers the bytes, and what comes back is
    the blobcache's file — a second fetch is answered 304 from it."""
    from collins import blobcache

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    gh_calls = []
    monkeypatch.setattr(prstatus, "gh_bytes", lambda args, max_bytes=None: gh_calls.append(args) or b"PNG")
    blobs = prfeed.PrBlobs(dispatch=lambda fn: fn(), spawn=lambda fn, _name: fn())
    gets = []

    def http_get(path_query, headers=None, timeout=60.0):
        gets.append(dict(headers or {}))
        answer = []
        blobs.blob(None, path_query.split("?", 1)[1], (headers or {}).get("If-None-Match"),
                   lambda status, hdrs, body: answer.append((status, hdrs, body)))
        return answer[0]

    wired.http_get = http_get
    sha = "a" * 40
    file = remoteprs.fetch_blob("o/r", sha, "docs/shot.png")
    assert file.read_bytes() == b"PNG" and file.suffix == ".png"
    assert file.parent.parent == blobcache.cache_root()
    again = remoteprs.fetch_blob("o/r", sha, "docs/shot.png")
    assert again == file and len(gh_calls) == 1 and gets[1]["If-None-Match"].startswith('"pr-')
    with pytest.raises(Exception, match="Not a commit"):
        remoteprs.fetch_blob("o/r", "main", "docs/shot.png")
    assert wired.calls.count("pr.blob") == 3
