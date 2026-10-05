"""The client's blob cache (split-service spec §3.23, PR-2.2): the tag a
fetch keeps and sends back, a 304 that keeps the file, the 24-hour
prune, the reasons a stand-in carries."""

import os

import pytest

from collins import apilink, blobcache


class BlobLink(apilink.Link):
    """A link whose `http_get` answers from a script, recording each GET."""

    def __init__(self, service_id: str = "svc-1") -> None:
        super().__init__()
        self.hello = {"service_id": service_id}
        self.gets: list[tuple[str, dict]] = []
        self.answers: list[tuple[int, dict, bytes]] = []

    def _request(self, message: dict, timeout: float | None = None) -> dict:
        raise AssertionError("a blob is never a request")

    def http_get(self, path_query: str, headers=None, timeout: float = 60.0):
        self.gets.append((path_query, dict(headers or {})))
        return self.answers.pop(0)


URL = "/api/blob?kind=git&cwd=%2Fsrv%2Fp&at=ref&path=pic.png&ref=HEAD"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(blobcache, "_pruned_at", {})
    return tmp_path / "cache" / "collins" / "blobs"


def test_a_fetch_keeps_the_bytes_under_the_service_and_the_tag_beside_them(cache):
    link = BlobLink()
    link.answers.append((200, {"ETag": '"abc:pic.png"'}, b"PNG1"))
    file = blobcache.fetch(URL, ".png", link=link)
    assert file.parent == cache / "svc-1"
    assert file.name == blobcache.key_for(URL) + ".png"
    assert file.read_bytes() == b"PNG1"
    assert (cache / "svc-1" / (blobcache.key_for(URL) + ".etag")).read_text() == '"abc:pic.png"'
    # The first GET had nothing to match against.
    assert link.gets == [(URL, {})]
    assert not [p for p in file.parent.iterdir() if p.name.endswith(".part")]


def test_the_next_fetch_sends_the_tag_and_a_304_keeps_the_file(cache):
    link = BlobLink()
    link.answers.append((200, {"ETag": '"abc:pic.png"'}, b"PNG1"))
    first = blobcache.fetch(URL, ".png", link=link)
    stamp = first.stat().st_mtime_ns
    link.answers.append((304, {"ETag": '"abc:pic.png"'}, b""))
    again = blobcache.fetch(URL, ".png", link=link)
    assert link.gets[1] == (URL, {"If-None-Match": '"abc:pic.png"'})
    assert again == first and again.read_bytes() == b"PNG1"
    assert again.stat().st_mtime_ns == stamp  # not rewritten


def test_a_new_tag_replaces_the_bytes_and_no_tag_forgets_the_old_one(cache):
    link = BlobLink()
    link.answers.append((200, {"ETag": '"one"'}, b"A"))
    blobcache.fetch(URL, ".png", link=link)
    link.answers.append((200, {"ETag": '"two"'}, b"B"))
    file = blobcache.fetch(URL, ".png", link=link)
    assert file.read_bytes() == b"B"
    tag = cache / "svc-1" / (blobcache.key_for(URL) + ".etag")
    assert tag.read_text() == '"two"'
    link.answers.append((200, {}, b"C"))
    assert blobcache.fetch(URL, ".png", link=link).read_bytes() == b"C"
    assert not tag.exists()
    link.answers.append((200, {}, b"D"))
    blobcache.fetch(URL, ".png", link=link)
    assert link.gets[-1][1] == {}  # no tag kept: nothing to match


def test_a_tag_without_its_file_is_not_sent(cache):
    link = BlobLink()
    link.answers.append((200, {"ETag": '"one"'}, b"A"))
    file = blobcache.fetch(URL, ".png", link=link)
    file.unlink()
    link.answers.append((200, {"ETag": '"one"'}, b"A"))
    assert blobcache.fetch(URL, ".png", link=link).read_bytes() == b"A"
    assert link.gets[-1][1] == {}


@pytest.mark.parametrize(
    "status, words",
    [
        (404, "No such file on this side."),
        (413, "That file is too large to show."),
        (403, "The service refused to read that file."),
        (500, "The service answered 500."),
    ],
)
def test_a_refusal_raises_its_reason_and_writes_nothing(cache, status, words):
    link = BlobLink()
    link.answers.append((status, {}, b""))
    with pytest.raises(ValueError, match=words):
        blobcache.fetch(URL, ".png", link=link)
    assert not any((cache / "svc-1").iterdir())


def test_a_304_with_the_file_gone_is_a_failure_not_a_path(cache):
    link = BlobLink()
    link.answers.append((304, {}, b""))
    with pytest.raises(ValueError):
        blobcache.fetch(URL, ".png", link=link)


def test_no_link_or_a_link_with_no_http_is_not_connected(cache, monkeypatch):
    monkeypatch.setattr(apilink, "_current", None)
    with pytest.raises(ValueError, match="Not connected"):
        blobcache.fetch(URL)

    class Bare(apilink.Link):
        def _request(self, message, timeout=None):
            return {}

    with pytest.raises(ValueError, match="Not connected"):
        blobcache.fetch(URL, link=Bare())


def test_a_lost_socket_is_a_reason(cache):
    class Lost(BlobLink):
        def http_get(self, path_query, headers=None, timeout=60.0):
            raise ConnectionError("socket closed")

    with pytest.raises(ValueError, match="socket closed"):
        blobcache.fetch(URL, link=Lost())


def test_each_service_has_its_own_directory(cache):
    one, two = BlobLink("svc-1"), BlobLink("svc/../2")
    one.answers.append((200, {}, b"1"))
    two.answers.append((200, {}, b"2"))
    assert blobcache.fetch(URL, link=one).parent.name == "svc-1"
    assert blobcache.fetch(URL, link=two).parent.name == "svc_.._2"


def test_a_blob_older_than_a_day_is_pruned_with_its_tag(cache):
    link = BlobLink()
    link.answers.append((200, {"ETag": '"old"'}, b"OLD"))
    old = blobcache.fetch(URL, ".png", link=link)
    tag = old.with_suffix(".etag")
    day_ago = old.stat().st_mtime - blobcache.PRUNE_AFTER_SECONDS - 60
    for path in (old, tag):
        os.utime(path, (day_ago, day_ago))
    other = "/api/blob?kind=pr&repository=o%2Fr&ref=abc1234&path=a.png"
    link.answers.append((200, {}, b"NEW"))
    blobcache._pruned_at.clear()  # the throttle: the fetch above swept a moment ago
    fresh = blobcache.fetch(other, ".png", link=link)
    assert fresh.read_bytes() == b"NEW"
    assert not old.exists() and not tag.exists()


def test_a_blob_younger_than_a_day_survives_the_prune(cache):
    link = BlobLink()
    link.answers.append((200, {"ETag": '"t"'}, b"KEEP"))
    kept = blobcache.fetch(URL, ".png", link=link)
    hours_ago = kept.stat().st_mtime - blobcache.PRUNE_AFTER_SECONDS + 3600
    os.utime(kept, (hours_ago, hours_ago))
    blobcache.prune(kept.parent, force=True)
    assert kept.exists() and kept.with_suffix(".etag").exists()


def test_the_prune_runs_at_most_once_per_interval(cache, monkeypatch):
    swept: list[float] = []
    monkeypatch.setattr(blobcache.remoteimages, "prune_stale", lambda folder, now=None: swept.append(now))
    folder = cache / "svc-1"
    blobcache.prune(folder, now=1000.0)
    blobcache.prune(folder, now=1000.0 + blobcache.PRUNE_EVERY_S - 1)
    assert swept == [1000.0]
    blobcache.prune(folder, now=1000.0 + blobcache.PRUNE_EVERY_S)
    blobcache.prune(folder, now=1001.0, force=True)
    assert swept == [1000.0, 1000.0 + blobcache.PRUNE_EVERY_S, 1001.0]
