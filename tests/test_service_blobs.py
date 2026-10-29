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
                # A delivered file (SendUserFile) is a `file` row, a picture an `image`.
                self.kind = "image" if str(key).lower().endswith((".png", ".jpg", ".gif")) else "file"

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
        self.handle = session.handle


class Core:
    def __init__(self, sessions=()):
        self.sessions = {i: Record(s) for i, s in enumerate(sessions)}
        self.store = None
        self.image_registry = blobs.ImageRegistry()
        self.delivered_files = blobs.DeliveredFiles(spawn=lambda fn, name: fn())
        self.pending_owners = uploads.PendingOwners()

    def pending(self, name, data, device="laptop"):
        """A pending upload *device* sent (what `UploadBlobs.put` records)."""
        path = uploads.write(None, name, data)
        self.pending_owners.record(path, device)
        return path

    def landed(self):
        """Every session's transcript landed: what `ServiceCore.
        session_transcript_landed` does with its delivered files."""
        for record in self.sessions.values():
            blobs.note_delivered(self, record)
        return self


class Client:
    def __init__(self, local=False, device="laptop"):
        self.local = local
        self.device = device


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
    # The handle of a resolved session names nobody (2026-10-18, N2): the
    # unresolved case is the handle test below.
    assert _get(feed, f"path={picture}&session=s-1")[0] == 403
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


def test_a_handle_names_a_live_session_only_until_it_resolves(picture):
    """The PR 612 review's nit 8: the transcript's images are keyed by the
    handle while the session has no id (the client asks with it then) and
    by the id alone once it has one, so the handle is no second durable key."""
    unresolved = Session(session_id=None, handle="s-1", seen=[str(picture)])
    core = Core([unresolved])
    assert list(blobs._session_records(core, "s-1")) == [unresolved]
    assert blobs.agent_named(core, "s-1")[1] == frozenset({str(picture)})
    unresolved.session_id = "sid-1"
    assert list(blobs._session_records(core, "s-1")) == []
    assert list(blobs._session_records(core, "sid-1")) == [unresolved]
    assert blobs.agent_named(core, "s-1")[1] == frozenset()
    assert blobs.agent_named(core, "sid-1")[1] == frozenset({str(picture)})
    assert blobs.known_session(core, "sid-1") and not blobs.known_session(core, "s-1")
    unresolved.session_id = None
    assert not blobs.known_session(core, "s-1")  # never by handle: an upload would be orphaned


def test_the_sessions_uploads_and_the_pending_ones_are_admitted(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    core = Core()
    mine = uploads.write("sid-1", "a.png", b"\x89PNG")
    pending = core.pending("b.png", b"\x89PNG")
    other = uploads.write("sid-2", "c.png", b"\x89PNG")
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={mine}&session=sid-1")[0] == 200
    assert _get(feed, f"path={pending}&session=sid-1")[0] == 200
    assert _get(feed, f"path={pending}")[0] == 200
    assert _get(feed, f"path={other}&session=sid-1")[0] == 403


def test_a_pending_upload_is_served_to_the_device_that_sent_it_only(tmp_path, monkeypatch):
    """PR-3.5 (the Fable review of PR 615, call 5): the pending folder is
    every device's, so a drop there is served to a client that is not
    `local` only on the device that uploaded it, picture or not, with any
    session; one whose device nobody recorded (sent with none, or the
    service restarted since) is served to no such client. A `local` client
    is answered as before, and the session's own uploads are its session's
    whatever the device."""
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    core = Core([Session()])
    feed = _inline(blobs.FileBlobs, core)
    picture = core.pending("shot.png", b"\x89PNG pending")
    document = core.pending("notes.pdf", b"%PDF pending")
    forgotten = uploads.write(None, "lost.png", b"\x89PNG lost")
    nameless = core.pending("anon.png", b"\x89PNG anon", device="")
    mine = uploads.write("sid-1", "mine.png", b"\x89PNG mine")
    laptop, phone, nobody = Client(device="laptop"), Client(device="phone"), Client(device="")
    for query in (f"path={picture}", f"path={picture}&session=sid-1", f"path={picture}&as=file"):
        assert _get(feed, query, laptop)[0] == 200, query
        assert _get(feed, query, phone)[0] == 403, query
        assert _get(feed, query, nobody)[0] == 403, query
    assert _get(feed, f"path={document}&session=sid-1&as=file", laptop)[0] == 200
    assert _get(feed, f"path={document}&session=sid-1&as=file", phone)[0] == 403
    for path in (forgotten, nameless):
        for client in (laptop, phone, nobody):
            assert _get(feed, f"path={path}&session=sid-1", client)[0] == 403, (path, client.device)
    assert _get(feed, f"path={mine}&session=sid-1", phone)[0] == 200
    local = Client(local=True, device="phone")
    assert _get(feed, f"path={picture}", local)[0] == 200
    assert _get(feed, f"path={forgotten}", local)[0] == 200
    # A request that names no client (no header: `client` None) is no device.
    got = []
    feed.blob(None, f"path={picture}", None, lambda *answer: got.append(answer))
    assert got[0][0] == 403


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


# -- kind=file&as=file: a file that is no picture, for an app (D51, PR-2.8) -------------


@pytest.fixture
def report(tmp_path):
    path = tmp_path / "handed-over" / "report.pdf"
    path.parent.mkdir()
    path.write_bytes(b"%PDF-1.4 report")
    return path


def test_a_named_file_that_is_no_picture_is_served_whole_to_a_client_that_is_not_local(report):
    """The session's agent named it (a tool call's registered path, or its
    transcript's attachment record): served as it is, with the pictures'
    tag and 304, as an octet stream."""
    core = Core([Session(seen=[str(report)])])
    feed = _inline(blobs.FileBlobs, core)
    # A record the service has not taken in yet admits nothing: the scan's
    # landing is what names the file (`note_delivered`).
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 400
    core.landed()
    # By its id alone once it has one: the handle is no second durable key
    # (`_session_records`' rule, kept for a delivered file).
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 403
    status, headers, body = _get(feed, f"path={report}&session=sid-1&as=file")
    assert status == 200 and body == b"%PDF-1.4 report"
    assert headers["Content-Type"] == "application/octet-stream"
    st = os.stat(report)
    assert headers["ETag"] == f'"{st.st_mtime_ns // 1000}-{st.st_size}"'
    assert _get(feed, f"path={report}&session=sid-1&as=file", inm=headers["ETag"])[0] == 304
    # By the registry too, and only for the session that named it.
    core = Core([Session()])
    core.image_registry.admit(["sid-1"], str(report))
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 200
    assert _get(feed, f"path={report}&session=sid-2&as=file")[0] == 403
    assert _get(feed, f"path={report}&as=file")[0] == 400  # no session named: nobody's file


def test_a_registered_path_is_served_by_the_handle_only_while_the_session_has_no_id(picture, report):
    """The Fable pass of 2026-10-18, N2: show_image admits a path under the
    session's id and its handle, both keys always, and `agent_named` read
    the registry by whatever key the request named; after the resolve the
    handle still served the picture (and the file with `as=file`) where
    `_session_records` and `DeliveredFiles` refused it. One handle rule."""
    session = Session(session_id=None, handle="s-1")
    core = Core([session])
    core.image_registry.admit(["sid-1", "s-1"], str(picture))
    core.image_registry.admit(["sid-1", "s-1"], str(report))
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={picture}&session=s-1")[0] == 200  # the handle, while unresolved
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 200
    session.session_id = "sid-1"
    assert _get(feed, f"path={picture}&session=s-1")[0] == 403
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 403
    assert _get(feed, f"path={picture}&session=sid-1")[0] == 200
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 200
    assert _get(feed, f"path={picture}&session=nobody")[0] == 403


def test_a_delivered_file_is_served_by_the_handle_only_while_the_session_has_no_id(report):
    core = Core([Session(session_id=None, handle="s-1", seen=[str(report)])])
    feed = _inline(blobs.FileBlobs, core)
    core.landed()
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 200


def test_a_file_that_is_no_picture_is_never_served_without_a_session(tmp_path, monkeypatch):
    """The Fable review of PR 615, nit 7: `uploads.inside(real, None)`
    admits the pending uploads folder, so `as=file` with no session would
    hand any pending upload whole to a client that is not local. A
    non-picture needs its session; a pending picture is served as before
    (the composer's preview of a drop made before the session has an id)."""
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    core = Core([Session()])
    document = core.pending("notes.pdf", b"%PDF pending")
    picture = core.pending("shot.png", b"\x89PNG pending")
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={document}&as=file")[0] == 400
    assert _get(feed, f"path={document}&session=&as=file")[0] == 400
    assert _get(feed, f"path={picture}&as=file")[0] == 200
    assert _get(feed, f"path={picture}")[0] == 200
    # With a session the service knows named, a pending upload is served (D37).
    assert _get(feed, f"path={document}&session=sid-1&as=file")[0] == 200


def test_a_session_nobody_knows_gets_no_file_that_is_no_picture(tmp_path, monkeypatch):
    """The verification of PR 615, S1: the refusal above was by shape (an
    empty `session`), and `uploads.inside` admits the pending folder
    whatever the session, so `&session=x` was the same request served. A
    non-picture counts as uploaded only for a session the service knows:
    a live one's id, its handle only while it has no id, or an id the
    store lists."""
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    core = Core([Session()])
    document = core.pending("notes.pdf", b"%PDF pending")
    picture = core.pending("shot.png", b"\x89PNG pending")
    theirs = uploads.write("sid-9", "theirs.csv", b"a,b\n")
    feed = _inline(blobs.FileBlobs, core)
    for session in ("nobody-at-all", "x", "../..", "s-1", "sid-9"):
        # Unknown, unknown, no valid name, a resolved session's handle, and
        # a valid id nobody has: each named nobody.
        assert _get(feed, f"path={document}&session={session}&as=file")[0] == 403, session
    assert _get(feed, f"path={theirs}&session=sid-9&as=file")[0] == 403  # its own folder, an unknown session
    assert _get(feed, f"path={document}&session=sid-1&as=file")[0] == 200  # a live session
    # The picture preview is as it was, with any session or none.
    for query in (f"path={picture}", f"path={picture}&as=file", f"path={picture}&session=x"):
        assert _get(feed, query)[0] == 200, query
    # A session with no id yet is named by its handle...
    unresolved = Core([Session(session_id=None, handle="s-7")])
    unresolved.pending_owners = core.pending_owners  # the same service's record of who sent it
    assert _get(_inline(blobs.FileBlobs, unresolved), f"path={document}&session=s-7&as=file")[0] == 200
    # ...and one that is not live by the id the store lists.

    class Store:
        def get_session(self, session_id):
            return object() if session_id == "sid-9" else None

    core.store = Store()
    assert _get(feed, f"path={theirs}&session=sid-9&as=file")[0] == 200
    assert _get(feed, f"path={document}&session=nobody-at-all&as=file")[0] == 403


def test_a_delivered_file_noted_under_a_handle_is_not_served_by_it_after_the_resolve(report):
    """Nit 1 of the same verification: `DeliveredFiles` keeps the key a
    record was noted under, so one noted while the session had no id
    stayed admitted by the handle after the resolve. The request's
    session is judged first (`names_a_session`), so the stale key is
    never read; by the id it is served once the transcript lands again."""
    session = Session(session_id=None, handle="s-1", seen=[str(report)])
    core = Core([session])
    feed = _inline(blobs.FileBlobs, core)
    core.landed()
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 200
    session.session_id = "sid-1"
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 403
    core.landed()
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 200
    assert _get(feed, f"path={report}&session=s-1&as=file")[0] == 403


def test_without_as_file_nothing_but_a_picture_is_served_named_or_not(report):
    """What a decoder fetches (`fetch_image`'s URL) is a picture or a
    refusal, exactly as before D51: a named file that is no picture is 400."""
    feed = _inline(blobs.FileBlobs, Core([Session(seen=[str(report)])]).landed())
    assert _get(feed, f"path={report}&session=sid-1")[0] == 400


def test_a_file_inside_a_root_that_nobody_named_is_not_served_whole(report):
    """A root is not an attachment: project text is `fs.read`'s, and
    `as=file` does not turn the blob GET into a second reader of it."""
    core = Core([Session(cwd=str(report.parent))])
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 400
    assert _get(feed, f"path={report}&as=file")[0] == 400
    secret = report.parent / ".env"
    secret.write_text("TOKEN=1")
    assert _get(feed, f"path={secret}&session=sid-1&as=file")[0] == 400
    # Outside every root and unnamed: 403, as a picture there is.
    elsewhere = report.parent.parent / "elsewhere.pdf"
    elsewhere.write_bytes(b"%PDF")
    assert _get(feed, f"path={elsewhere}&session=sid-1&as=file")[0] == 403
    # Named *and* inside a root: served.
    core.image_registry.admit(["sid-1"], str(report))
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 200


def test_an_upload_that_is_no_picture_is_served_whole_to_its_session(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(tmp_path / "uploads"))
    mine = uploads.write("sid-1", "a.csv", b"a,b\n")
    other = uploads.write("sid-2", "c.csv", b"c,d\n")
    feed = _inline(blobs.FileBlobs, Core([Session()]))
    assert _get(feed, f"path={mine}&session=sid-1&as=file")[:1] == (200,)
    assert _get(feed, f"path={mine}&session=sid-1")[0] == 400  # not without as=file
    assert _get(feed, f"path={other}&session=sid-1&as=file")[0] == 403


def test_a_whole_file_keeps_the_cap(report, monkeypatch):
    feed = _inline(blobs.FileBlobs, Core([Session(seen=[str(report)])]).landed())
    monkeypatch.setattr(blobs, "FILE_MAX_BYTES", 4)
    monkeypatch.setattr(blobs.serve_file, "__defaults__", (4,))
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 413


def test_a_link_swapped_in_at_a_named_file_is_refused(report, tmp_path):
    """The exact resolved path is what was named (D38's rule, kept): a
    link put there afterwards resolves to a file nobody named."""
    secret = tmp_path / "secret.key"
    secret.write_text("private")
    named = tmp_path / "named.pdf"
    named.write_bytes(b"%PDF named")
    core = Core([Session()])
    core.image_registry.admit(["sid-1"], str(named))
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={named}&session=sid-1&as=file")[0] == 200
    named.unlink()
    os.symlink(secret, named)
    assert _get(feed, f"path={named}&session=sid-1&as=file")[0] == 403


def test_a_link_swapped_in_at_a_delivered_file_is_refused(report, tmp_path):
    """Review of PR 615 (S2): the transcript's road holds the exact
    resolved path too. A `SendUserFile` call names `link.pdf`, a link to a
    report; the service resolves it when the record lands. The link is
    then pointed at a private key, and at a file inside the project: the
    path the record resolves to now was never delivered, so it is not
    served, however often the transcript lands again."""
    secret = tmp_path / "id_ed25519"
    secret.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("TOKEN=1\n")
    link = tmp_path / "link.pdf"
    os.symlink(report, link)
    core = Core([Session(cwd=str(project), seen=[str(link)])]).landed()
    feed = _inline(blobs.FileBlobs, core)
    assert _get(feed, f"path={link}&session=sid-1&as=file")[:1] == (200,)
    assert core.delivered_files.paths("sid-1") == frozenset({str(report)})
    for target in (secret, project / ".env"):
        link.unlink()
        os.symlink(target, link)
        core.landed()  # a later scan sees the same record: nothing is resolved again
        status, _headers, body = _get(feed, f"path={link}&session=sid-1&as=file")
        assert status in (400, 403) and body == b"", (target, status)
    assert core.delivered_files.paths("sid-1") == frozenset({str(report)})
    # The file that was delivered is still served, by its own path.
    assert _get(feed, f"path={report}&session=sid-1&as=file")[0] == 200


def test_delivered_files_resolve_once_off_the_callers_thread_and_are_bounded(tmp_path, monkeypatch):
    ran: list[str] = []
    held: list = []
    delivered = blobs.DeliveredFiles(spawn=lambda fn, name: (ran.append(name), held.append(fn)))
    a = tmp_path / "a.pdf"
    a.write_text("a")
    delivered.note(["sid-1", "", None, "s-1"], [str(a)])
    assert ran == ["delivered-files"] and delivered.paths("sid-1") == frozenset()  # not yet resolved
    held.pop()()
    assert delivered.paths("sid-1") == delivered.paths("s-1") == frozenset({str(a)})
    delivered.note(["sid-1", "s-1"], [str(a)])
    assert ran == ["delivered-files"]  # seen before: no second resolve
    assert delivered.paths("sid-2") == frozenset()
    monkeypatch.setattr(blobs, "REGISTRY_PER_SESSION", 3)
    inline = blobs.DeliveredFiles(spawn=lambda fn, name: fn())
    inline.note(["sid-1"], [f"/p/{n}.pdf" for n in range(5)])
    assert inline.paths("sid-1") == frozenset({"/p/2.pdf", "/p/3.pdf", "/p/4.pdf"})
    # A picture record is not a delivered file: it is admitted as before (D38).
    core = Core([Session(seen=[str(tmp_path / "shot.png")])]).landed()
    assert core.delivered_files.paths("sid-1") == frozenset()


def test_serve_file_follows_no_link_at_the_resolved_path(report, tmp_path):
    """The swap between the confinement and the open: the path the worker
    resolved is opened `O_NOFOLLOW`, so a link put at its last component
    in between is a 404, never the link's target."""
    secret = tmp_path / "secret.key"
    secret.write_text("private")
    assert blobs.serve_file(str(report), None)[0] == 200
    report.unlink()
    os.symlink(secret, report)
    assert blobs.serve_file(str(report), None)[0] == 404


def test_a_local_client_is_answered_as_before(report, picture):
    """A local client opens the file itself and never asks for one whole:
    `as=file` changes nothing for it."""
    feed = _inline(blobs.FileBlobs, Core([Session(seen=[str(report)])]).landed())
    local = Client(local=True)
    assert _get(feed, f"path={report}&session=sid-1&as=file", local)[0] == 400
    assert _get(feed, f"path={report}", local)[0] == 400
    assert _get(feed, f"path={picture}&as=file", local)[0] == 200


def test_the_cached_copys_suffix_is_never_one_a_desktop_would_run():
    """The copy's name is the cache's (`<sha1><suffix>`); the suffix is the
    service path's and untrusted: a document's is kept, a launcher's, a
    script's, a program's, a missing or an odd one becomes `.bin`, and
    then the app chooser is shown instead of a default app."""
    for key, kept in (("/p/report.pdf", ".pdf"), ("/p/Data.CSV", ".csv"), ("/p/a.tar.gz", ".gz")):
        assert blobcache.file_suffix(key) == kept and blobcache.opens_by_default(key)
    for key in (
        "/p/evil.desktop", "/p/run.sh", "/p/tool.AppImage", "/p/x.py", "/p/setup.exe", "/p/a.jar",
        "/p/Makefile", "/p/.bashrc", "/p/odd.p-d-f", "/p/long." + "x" * 40, "/p/uni.pdф", "/p/dot.",
        "/p/pkg.deb", "/p/blob.bin",
    ):
        assert blobcache.file_suffix(key) == ".bin", key
        assert not blobcache.opens_by_default(key), key
        # The copy's own name answers the same (nit 8 of the Fable review of
        # PR 615: the panel asks the copy, not the key).
        assert not blobcache.copy_opens_by_default("/cache/abc" + blobcache.file_suffix(key))
    assert blobcache.copy_opens_by_default("/cache/abc.pdf")
    assert not blobcache.copy_opens_by_default("/cache/abc")  # no suffix at all: the chooser
    url = blobcache.whole_file_url("/p/a b.pdf", "sid-1")
    assert url == "/api/blob?kind=file&path=%2Fp%2Fa%20b.pdf&session=sid-1&as=file"


def test_a_whole_file_is_cached_under_the_caches_name_without_an_exec_bit(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    class Link:
        hello = {"service_id": "svc1"}

        def __init__(self):
            self.asked = []

        def http_get(self, url, headers):
            self.asked.append(url)
            headers = {"ETag": '"1-2"', "Content-Type": "application/octet-stream"}
            return 200, headers, b"[Desktop Entry]\nExec=rm\n"

    link = Link()
    copy = blobcache.fetch_file("/srv/p/evil.desktop", "sid-1", link)
    assert link.asked == [blobcache.whole_file_url("/srv/p/evil.desktop", "sid-1")]
    assert copy.suffix == ".bin" and "evil" not in copy.name
    assert copy.name == blobcache.key_for(link.asked[0]) + ".bin"
    assert str(copy).startswith(str(blobcache.cache_root()))
    assert os.stat(copy).st_mode & 0o111 == 0


def test_the_cache_folder_is_this_users_alone_and_a_looser_one_is_tightened(tmp_path, monkeypatch):
    """Review of PR 615 (N2): the copies are 0600; the folder holding
    them (documents handed to apps among them, since D51) is 0700, also
    when it was made under the umask by an earlier version."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    class Link:
        hello = {"service_id": "svc1"}

        def http_get(self, url, headers):
            return 200, {"ETag": '"1-2"'}, b"%PDF"

    copy = blobcache.fetch_file("/srv/p/a.pdf", "sid-1", Link())
    assert os.stat(copy.parent).st_mode & 0o777 == 0o700
    os.chmod(copy.parent, 0o775)
    blobcache.fetch_file("/srv/p/b.pdf", "sid-1", Link())
    assert os.stat(copy.parent).st_mode & 0o777 == 0o700
