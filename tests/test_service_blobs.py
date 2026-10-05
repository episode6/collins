# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The image blobs on the service (collins.service.blobs, split-service
spec §3.23, PR-2.7): `kind=file` confined to the roots, the session's
uploads and what the session's own agent named (D38), `kind=remote`
fetched by the service into its cache, `kind=icon` behind the icon gate;
and the client's half of the cache those land in (blobcache's typed
suffix and its confined read)."""

import os

import pytest

from collins import blobcache, remoteimages, uploads
from collins.service import blobs


class Transcript:
    def __init__(self, keys=()):
        self.keys = list(keys)

    def attachments(self):
        class One:
            remote = False

            def __init__(self, key):
                self.key = key

        return [One(key) for key in self.keys]


class Session:
    def __init__(self, session_id="sid-1", handle="s-1", cwd=None, seen=()):
        self.session_id = session_id
        self.handle = handle
        self.cwd = cwd
        self.transcript = Transcript(seen)

    def current_agent_cwd(self):
        return self.cwd


class Record:
    def __init__(self, session):
        self.session = session


class Core:
    def __init__(self, sessions=()):
        self.sessions = {i: Record(s) for i, s in enumerate(sessions)}
        self.store = None
        self.image_registry = blobs.ImageRegistry()


class Client:
    def __init__(self, local=False):
        self.local = local


def _inline(cls, core, **kwargs):
    return cls(core, dispatch=lambda fn: fn(), spawn=lambda fn, name: fn(), **kwargs)


def _get(feed, query, client=None, inm=None):
    got = []
    feed.blob(client or Client(), query, inm, lambda *answer: got.append(answer))
    assert got, "no answer"
    return got[0]


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "outside" / "shot.png"
    path.parent.mkdir()
    path.write_bytes(b"\x89PNG-shot")
    return path


# -- kind=file ------------------------------------------------------------------------


def test_a_file_outside_every_root_is_refused_to_a_remote_client(picture):
    feed = _inline(blobs.FileBlobs, Core([Session()]))
    assert _get(feed, f"path={picture}&session=sid-1")[0] == 403
    assert _get(feed, f"path={picture}", Client(local=True))[0] == 200  # a local client reads anything


def test_a_file_inside_a_sessions_root_is_served_with_its_tag_and_304(picture):
    core = Core([Session(cwd=str(picture.parent))])
    feed = _inline(blobs.FileBlobs, core)
    status, headers, body = _get(feed, f"path={picture}")
    assert status == 200 and body == b"\x89PNG-shot" and headers["Content-Type"] == "image/png"
    st = os.stat(picture)
    assert headers["ETag"] == f'"{st.st_mtime_ns // 1000}-{st.st_size}"'
    assert _get(feed, f"path={picture}", inm=headers["ETag"])[:2] == (304, {"ETag": headers["ETag"]})


def test_a_path_the_sessions_show_image_named_is_admitted_for_that_session_only(picture):
    core = Core([Session()])
    core.image_registry.admit(["sid-1", "s-1"], str(picture))
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={picture}&session=sid-1")[0] == 200
    assert _get(feed, f"path={picture}&session=s-1")[0] == 200  # the handle, while unresolved
    assert _get(feed, f"path={picture}&session=sid-2")[0] == 403
    assert _get(feed, f"path={picture}")[0] == 403


def test_a_link_swapped_in_at_an_admitted_path_is_refused(picture, tmp_path):
    """D38 admits the exact resolved path: a symlink put at the path the
    agent named, after the call, points at a file nobody admitted."""
    named = tmp_path / "named.png"
    named.write_bytes(b"\x89PNG-named")
    core = Core([Session()])
    core.image_registry.admit(["sid-1"], str(named))
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={named}&session=sid-1")[0] == 200
    named.unlink()
    os.symlink(picture, named)  # now points outside, at an unadmitted file
    assert _get(feed, f"path={named}&session=sid-1")[0] == 403


def test_an_admitted_link_is_held_by_its_target(picture, tmp_path):
    link = tmp_path / "link.png"
    os.symlink(picture, link)
    registry = blobs.ImageRegistry()
    registry.admit(["sid-1"], str(link))
    assert registry.paths("sid-1") == frozenset({str(picture)})


def test_an_image_the_service_saw_in_the_sessions_transcript_is_admitted(picture):
    feed = _inline(blobs.FileBlobs, Core([Session(seen=[str(picture)])]))
    assert _get(feed, f"path={picture}&session=sid-1")[0] == 200
    assert _get(feed, f"path={picture}&session=sid-9")[0] == 403


def test_a_link_out_of_a_root_is_outside_it(picture, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    os.symlink(picture, root / "link.png")
    feed = _inline(blobs.FileBlobs, Core([Session(cwd=str(root))]))
    assert _get(feed, f"path={root / 'link.png'}")[0] == 403


def test_the_sessions_uploads_and_the_pending_ones_are_admitted(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    mine = uploads.write("sid-1", "a.png", b"\x89PNG")
    pending = uploads.write(None, "b.png", b"\x89PNG")
    other = uploads.write("sid-2", "c.png", b"\x89PNG")
    feed = _inline(blobs.FileBlobs, Core())
    assert _get(feed, f"path={mine}&session=sid-1")[0] == 200
    assert _get(feed, f"path={pending}&session=sid-1")[0] == 200
    assert _get(feed, f"path={pending}")[0] == 200
    assert _get(feed, f"path={other}&session=sid-1")[0] == 403


def test_only_an_image_is_a_file_blob_and_the_cap_holds(picture, monkeypatch):
    feed = _inline(blobs.FileBlobs, Core())
    local = Client(local=True)
    text = picture.parent / "notes.txt"
    text.write_text("x")
    assert _get(feed, f"path={text}", local)[0] == 400
    assert _get(feed, "path=relative.png", local)[0] == 400
    assert _get(feed, f"path={picture.parent / 'gone.png'}", local)[0] == 404
    monkeypatch.setattr(blobs, "FILE_MAX_BYTES", 4)
    monkeypatch.setattr(blobs.serve_file, "__defaults__", (4,))
    assert _get(feed, f"path={picture}", local)[0] == 413


def test_the_registry_is_bounded_per_session(tmp_path):
    registry = blobs.ImageRegistry()
    for i in range(blobs.REGISTRY_PER_SESSION + 10):
        registry.admit(["sid-1"], f"/no/such/{i}.png")
    held = registry.paths("sid-1")
    assert "/no/such/0.png" not in held and f"/no/such/{blobs.REGISTRY_PER_SESSION + 9}.png" in held
    assert len(held) <= blobs.REGISTRY_PER_SESSION


# -- kind=remote ----------------------------------------------------------------------


class Fetches:
    def __init__(self, replies):
        self.replies = list(replies)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_a_remote_image_is_fetched_by_the_service_once_and_served_from_its_cache(tmp_path):
    fetch = Fetches([(b"GIF89a-one", ".gif")])
    feed = _inline(blobs.RemoteBlobs, Core(), directory=tmp_path / "cache", fetch=fetch)
    url = "https://example.com/plot"
    status, headers, body = _get(feed, "url=" + url)
    assert status == 200 and body == b"GIF89a-one" and headers["Content-Type"] == "image/gif"
    status, _h, body = _get(feed, "url=" + url)
    assert status == 200 and body == b"GIF89a-one" and fetch.urls == [url]  # the cache's
    assert _get(feed, "url=" + url, inm=headers["ETag"])[0] == 304


def test_a_fresh_download_replaces_the_cached_copy(tmp_path):
    fetch = Fetches([(b"one", ".png"), (b"two-jpeg", ".jpg")])
    feed = _inline(blobs.RemoteBlobs, Core(), directory=tmp_path / "cache", fetch=fetch)
    url = "https://example.com/chart"
    first = feed.download(url)
    second = feed.download(url)  # show_image's fresh look
    assert second.read_bytes() == b"two-jpeg" and not first.exists()
    status, headers, body = _get(feed, "url=" + url)
    assert body == b"two-jpeg" and headers["Content-Type"] == "image/jpeg"


def test_a_remote_fetch_that_fails_is_502_and_a_non_http_url_400(tmp_path):
    fetch = Fetches([remoteimages.FetchError("The server answered 404 for x")])
    feed = _inline(blobs.RemoteBlobs, Core(), directory=tmp_path / "cache", fetch=fetch)
    assert _get(feed, "url=https://example.com/x")[0] == 502
    assert _get(feed, "url=file:///etc/passwd")[0] == 400
    assert _get(feed, "url=/etc/passwd")[0] == 400


def test_download_then_hands_the_agents_words_to_the_main_loop(tmp_path):
    fetch = Fetches([remoteimages.FetchError("That URL isn't an image Collins can display: u")])
    feed = _inline(blobs.RemoteBlobs, Core(), directory=tmp_path / "cache", fetch=fetch)
    said = []
    feed.download_then("https://example.com/u", said.append)
    assert said == ["That URL isn't an image Collins can display: u"]


# -- kind=icon --------------------------------------------------------------------------

_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>'


def test_an_icon_is_served_behind_its_gate_for_a_known_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "project-icon.svg").write_bytes(_SVG)
    feed = _inline(blobs.IconBlobs, Core([Session(cwd=str(project))]))
    status, headers, body = _get(feed, f"root={project}")
    assert status == 200 and body == _SVG and headers["Content-Type"] == "image/svg+xml"
    assert _get(feed, f"root={project}", inm=headers["ETag"])[0] == 304
    (project / "project-icon.svg").write_bytes(b"<svg><script>x</script></svg>")
    assert _get(feed, f"root={project}")[0] == 404  # the gate refuses it
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _get(feed, f"root={elsewhere}")[0] == 403  # no session's root


# -- the client's cache ------------------------------------------------------------------


class Link:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []
        self.hello = {"service_id": "svc"}

    def http_get(self, url, headers):
        self.asked.append((url, dict(headers)))
        return self.answers.pop(0)


def test_a_typed_fetch_takes_its_suffix_from_the_answer_and_keeps_it_on_a_304(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    link = Link([(200, {"ETag": '"t1"', "Content-Type": "image/gif"}, b"GIF89a"), (304, {}, b"")])
    url = blobcache.remote_url("https://example.com/a?b=c")
    first = blobcache.fetch(url, None, link)
    assert first.suffix == ".gif" and first.read_bytes() == b"GIF89a"
    second = blobcache.fetch(url, None, link)
    assert second == first and link.asked[1][1] == {"If-None-Match": '"t1"'}


def test_a_typed_fetch_whose_type_changed_drops_the_old_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    link = Link(
        [
            (200, {"ETag": '"t1"', "Content-Type": "image/png"}, b"png"),
            (200, {"ETag": '"t2"', "Content-Type": "image/jpeg"}, b"jpeg"),
        ]
    )
    url = blobcache.remote_url("https://example.com/a")
    first = blobcache.fetch(url, None, link)
    second = blobcache.fetch(url, None, link)
    assert second.suffix == ".jpg" and not first.exists()


def test_the_urls_name_their_kind_and_quote_their_values():
    file_url = blobcache.file_url("/a b/c.png", "sid-1")
    assert file_url == "/api/blob?kind=file&path=%2Fa%20b%2Fc.png&session=sid-1"
    remote_url = blobcache.remote_url("https://x/y?z=1&w")
    assert remote_url == "/api/blob?kind=remote&url=https%3A%2F%2Fx%2Fy%3Fz%3D1%26w"
    assert blobcache.icon_url("/p") == "/api/blob?kind=icon&root=%2Fp"
    assert blobcache.image_url("https://x/y").startswith("/api/blob?kind=remote&")
    assert blobcache.image_url("/a.png", "s").startswith("/api/blob?kind=file&")
    assert blobcache.image_suffix("/a.PNG") == ".png" and blobcache.image_suffix("https://x/y.png") is None


def test_read_refuses_anything_outside_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    project_file = tmp_path / "project" / "shot.png"
    project_file.parent.mkdir()
    project_file.write_bytes(b"secret")
    assert blobcache.read(project_file) is None
    inside = blobcache.directory("svc") / "abc.png"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"blob")
    assert blobcache.read(inside) == b"blob"
    os.symlink(project_file, blobcache.directory("svc") / "link.png")
    assert blobcache.read(blobcache.directory("svc") / "link.png") is None  # a link out of it
    assert blobcache.read(blobcache.directory("svc") / ".." / ".." / ".." / "project" / "shot.png") is None
