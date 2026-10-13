# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The uploads directory (collins.uploads), `PUT /api/upload`
(service.blobs.UploadBlobs, api.server) and the client's end
(collins.remoteuploads): split-service spec §3.11, §3.23, PR-2.7; D37 for an
upload with no session id.

The upload's name is reduced to a basename, the body capped at 64 MiB, the
file written by descriptor into the session's directory, and the directory
goes with its session (`store.trash_many` / `delete`); the pending one's
files stay until the week's sweep."""

import json
import os
import time

import pytest

from collins import dropimages, remoteuploads, uploads
from collins.service.blobs import UploadBlobs


@pytest.fixture(autouse=True)
def uploads_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    monkeypatch.setenv("COLLINS_UPLOADS_DIR", str(root))
    return root


# -- names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, kept",
    [
        ("shot.png", "shot.png"),
        ("../../etc/passwd", "passwd"),
        ("a/b/c.png", "c.png"),
        ("C:\\Users\\me\\shot.png", "shot.png"),
        ("/abs/path/report.pdf", "report.pdf"),
        (".hidden.png", "hidden.png"),
        ("bad\x00na\x1bme.png", "badname.png"),
        ("..", None),
        ("/", None),
        ("", None),
        (None, None),
        ("dir/", None),
    ],
)
def test_the_name_is_a_basename_only(name, kept):
    assert uploads.safe_name(name) == kept


def test_a_long_name_keeps_its_extension_within_the_bound():
    name = uploads.safe_name("x" * 400 + ".png")
    assert name.endswith(".png") and len(name.encode()) <= uploads.NAME_MAX


def test_a_session_id_names_a_directory_and_nothing_else():
    assert uploads.valid_session("3fa9c1d2-0000-4000-8000-000000000000")
    for bad in ("", "..", "../x", "a/b", uploads.PENDING, ".hidden", "x" * 200, None, 7):
        assert not uploads.valid_session(bad), bad


# -- the write --------------------------------------------------------------------


def test_an_upload_lands_in_its_sessions_directory_under_a_fresh_name(uploads_root):
    first = uploads.write("sid-1", "../shot.png", b"one")
    second = uploads.write("sid-1", "shot.png", b"two")
    assert first == uploads_root / "sid-1" / "shot.png"
    assert second == uploads_root / "sid-1" / "shot-2.png"
    assert first.read_bytes() == b"one" and second.read_bytes() == b"two"  # never clobbered
    assert oct(os.stat(first).st_mode & 0o777) == "0o600"
    assert oct(os.stat(uploads_root / "sid-1").st_mode & 0o777) == "0o700"


def test_an_upload_with_no_session_goes_to_the_pending_directory(uploads_root):
    path = uploads.write(None, "drop.png", b"x")
    assert path == uploads_root / uploads.PENDING / "drop.png"


def test_the_cap_is_64_mib():
    assert uploads.MAX_BYTES == 64 * 1024 * 1024
    with pytest.raises(ValueError):
        uploads.write("sid-1", "big.bin", b"\0" * (uploads.MAX_BYTES + 1))


def test_an_empty_upload_is_refused():
    with pytest.raises(ValueError):
        uploads.write("sid-1", "a.png", b"")


def test_a_symlink_planted_as_the_sessions_directory_is_not_followed(uploads_root, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    uploads_root.mkdir()
    os.symlink(elsewhere, uploads_root / "sid-1")
    with pytest.raises(OSError):
        uploads.write("sid-1", "shot.png", b"x")
    assert list(elsewhere.iterdir()) == []


def test_a_symlink_planted_at_the_name_is_not_written_through(uploads_root, tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("keep")
    (uploads_root / "sid-1").mkdir(parents=True)
    os.symlink(target, uploads_root / "sid-1" / "shot.png")
    path = uploads.write("sid-1", "shot.png", b"x")
    assert path.name == "shot-2.png" and target.read_text() == "keep"


def test_inside_names_the_sessions_directory_and_the_pending_one(uploads_root):
    mine = uploads.write("sid-1", "a.png", b"x")
    pending = uploads.write(None, "b.png", b"x")
    other = uploads.write("sid-2", "c.png", b"x")
    assert uploads.inside(mine, "sid-1") and uploads.inside(pending, "sid-1")
    assert not uploads.inside(other, "sid-1")
    assert uploads.inside(pending, None) and not uploads.inside(mine, None)


# -- removed with the session (D37) -----------------------------------------------


def test_the_sessions_uploads_go_with_it_and_the_pending_ones_stay(uploads_root):
    uploads.write("sid-1", "a.png", b"x")
    pending = uploads.write(None, "b.png", b"x")
    assert uploads.remove("sid-1") is None
    assert not (uploads_root / "sid-1").exists()
    assert pending.exists()
    assert uploads.remove("sid-1") is None  # nothing there is no error
    assert uploads.remove(uploads.PENDING) is None and pending.exists()


def test_a_trash_that_refuses_falls_back_to_an_unlink(uploads_root):
    uploads.write("sid-1", "a.png", b"x")
    asked = []

    def trash(path):
        asked.append(path)
        return "no trash on this mount"

    assert uploads.remove("sid-1", trash=trash) is None
    assert asked == [str(uploads_root / "sid-1")] and not (uploads_root / "sid-1").exists()


def test_the_pending_sweep_takes_only_what_is_a_week_old(uploads_root):
    old = uploads.write(None, "old.png", b"x")
    fresh = uploads.write(None, "fresh.png", b"x")
    week = dropimages.PRUNE_AFTER_SECONDS
    os.utime(old, (time.time() - week - 60,) * 2)
    assert uploads.sweep_pending() == ["old.png"]
    assert not old.exists() and fresh.exists()
    assert uploads.sweep_pending(now=time.time() + week + 60) == ["fresh.png"]


def test_the_orphan_sweep_takes_a_forgotten_sessions_uploads_once_a_day_old(uploads_root, tmp_path):
    """A session the store no longer lists (the CLI's cleanupPeriodDays took
    the transcript, or it went outside Collins) never reaches trash_many or
    delete: the sweep removes its directory, but only once it is a day old
    (a freshly resolved session the scan has not caught up with is kept),
    and never the pending directory, a known session's, or a planted link."""
    forgotten = uploads.write("gone-1", "a.png", b"x").parent
    fresh = uploads.write("gone-2", "b.png", b"x").parent
    kept = uploads.write("kept-1", "c.png", b"x").parent
    pending = uploads.write(None, "d.png", b"x").parent
    day = uploads.ORPHAN_AFTER_SECONDS
    for folder in (forgotten, kept, pending):
        os.utime(folder, (time.time() - day - 60,) * 2)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.png").write_bytes(b"x")
    (uploads_root / "gone-3").symlink_to(elsewhere)
    os.utime(uploads_root / "gone-3", (time.time() - day - 60,) * 2, follow_symlinks=False)
    assert uploads.sweep_orphans({"kept-1"}) == ["gone-1"]
    assert not forgotten.exists() and fresh.exists() and kept.exists() and pending.exists()
    assert (elsewhere / "x.png").exists() and (uploads_root / "gone-3").is_symlink()
    # The known set may be a callable, asked once on the sweep's thread.
    asked = []
    later = time.time() + day + 60
    assert uploads.sweep_orphans(lambda: asked.append(1) or {"kept-1"}, now=later) == ["gone-2"]
    assert asked == [1] and not fresh.exists() and kept.exists()
    os.utime(kept, None)  # touched today: unknown now, but under a day old
    assert uploads.sweep_orphans(set()) == []
    assert uploads.sweep_orphans(set(), now=time.time() + 2 * day) == ["kept-1"]


def test_the_store_takes_the_uploads_with_a_trashed_or_deleted_session(uploads_root, tmp_path, monkeypatch):
    from collins import store as store_mod

    class Session:
        def __init__(self, sid):
            self.session_id = sid
            self.cwd = str(tmp_path)
            self.jsonl_path = tmp_path / f"{sid}.jsonl"
            self.jsonl_path.write_text("{}\n")

    trashed = []
    monkeypatch.setattr(store_mod, "_trash_file", lambda path: trashed.append(str(path)) or None)
    fake = store_mod.SessionStore.__new__(store_mod.SessionStore)
    fake.sessions = {"sid-1": Session("sid-1"), "sid-2": Session("sid-2")}
    fake._last_sessions = list(fake.sessions.values())
    fake._trash_chat_dirs = lambda doomed: None
    fake._archive_orphaned_forwards = lambda gone: None
    fake._apply = lambda: None
    uploads.write("sid-1", "a.png", b"x")
    uploads.write("sid-2", "b.png", b"x")
    pending = uploads.write(None, "c.png", b"x")
    assert fake.trash_many(["sid-1"]) == {}
    assert str(uploads_root / "sid-1") in trashed  # to the trash, with the transcript
    assert fake.delete("sid-2") is None
    assert not (uploads_root / "sid-2").exists()
    assert pending.exists()  # a pending upload survives the session's trash; the sweep takes it


# -- PUT /api/upload ---------------------------------------------------------------


class Core:
    def __init__(self, known=()):
        self.sessions = {}
        self.store = self
        self.known = set(known)

    def get_session(self, sid):
        return object() if sid in self.known else None


def _put(feed, query, body, client=None):
    got = []
    feed.put(client, query, body, lambda status, headers, data: got.append((status, headers, data)))
    assert got, "no answer"
    return got[0]


def _inline(core):
    return UploadBlobs(core, dispatch=lambda fn: fn(), spawn=lambda fn, name: fn())


def test_the_put_writes_and_answers_the_path(uploads_root):
    status, headers, body = _put(_inline(Core({"sid-1"})), "session=sid-1&name=..%2Fshot.png", b"png")
    assert status == 200 and headers["Content-Type"] == "application/json"
    path = json.loads(body)["path"]
    assert path == str(uploads_root / "sid-1" / "shot.png")
    with open(path, "rb") as fh:
        assert fh.read() == b"png"


def test_the_put_with_no_session_writes_a_pending_upload(uploads_root):
    status, _h, body = _put(_inline(Core()), "name=drop.png", b"png")
    assert status == 200 and json.loads(body)["path"] == str(uploads_root / uploads.PENDING / "drop.png")


def test_the_put_refuses_an_unknown_session_a_bad_name_and_a_body_past_the_cap(uploads_root, monkeypatch):
    feed = _inline(Core({"sid-1"}))
    assert _put(feed, "session=sid-9&name=a.png", b"x")[0] == 403
    assert _put(feed, "session=..&name=a.png", b"x")[0] == 400
    assert _put(feed, "session=sid-1&name=..", b"x")[0] == 400
    assert _put(feed, "session=sid-1&name=a.png", b"")[0] == 400  # an empty body names nothing
    monkeypatch.setattr(uploads, "MAX_BYTES", 4)
    assert _put(feed, "session=sid-1&name=a.png", b"12345")[0] == 413
    assert not (uploads_root / "sid-9").exists()
    assert not (uploads_root / "sid-1").exists()


def test_the_put_refuses_a_live_sessions_handle_as_its_session(uploads_root):
    """The PR 612 review's nit 3: an upload keyed by a handle (`s-7`) would
    land in a directory no trash or delete ever names. A live session is
    known by its id alone; the handle is refused like any unknown id."""

    class Live:
        session_id = "sid-live"
        handle = "s-7"

    class Record:
        session = Live()

    core = Core()
    core.sessions = {7: Record()}
    feed = _inline(core)
    assert _put(feed, "session=sid-live&name=a.png", b"x")[0] == 200
    assert _put(feed, "session=s-7&name=a.png", b"x")[0] == 403
    assert not (uploads_root / "s-7").exists()


def test_a_name_with_a_lone_surrogate_is_written_without_it(uploads_root):
    """The PR 612 review's nit 4: a lone surrogate raised UnicodeEncodeError
    out of safe_name's measure (and would out of os.open); it is dropped."""
    assert uploads.safe_name("sh\ud800ot.png") == "shot.png"
    assert uploads.safe_name("\ud800") is None
    assert uploads.write("sid-1", "a\udcffb.png", b"x").name == "ab.png"


# -- the client's end ---------------------------------------------------------------


class Link:
    def __init__(self, status=200, path="/srv/uploads/sid-1/shot.png"):
        self.status = status
        self.path = path
        self.puts = []

    def http_put(self, query, body, headers=None):
        self.puts.append((query, body))
        return self.status, {}, json.dumps({"path": self.path}).encode()


def test_the_client_uploads_by_name_and_session_and_reads_the_path_back():
    link = Link()
    assert remoteuploads.upload(b"png", "drop 1.png", "sid-1", link) == "/srv/uploads/sid-1/shot.png"
    assert link.puts == [("/api/upload?name=drop%201.png&session=sid-1", b"png")]
    remoteuploads.upload(b"png", "drop.png", None, link)
    assert link.puts[-1][0] == "/api/upload?name=drop.png"


def test_the_client_says_why_an_upload_failed():
    with pytest.raises(ValueError):
        remoteuploads.upload(b"png", "a.png", "sid-1", Link(status=403))
    with pytest.raises(ValueError):
        remoteuploads.upload(b"png", "a.png", "sid-1", Link(path="relative/path"))
    with pytest.raises(ValueError):
        remoteuploads.upload(b"\0" * (uploads.MAX_BYTES + 1), "a.png", "sid-1", Link())
    with pytest.raises(ValueError):
        remoteuploads.upload(b"png", "a.png", "sid-1", object())  # no HTTP


# -- upload_files: the dropped files' read and the errors joined per file -------------


class Info:
    def __init__(self, kind, size, name):
        self.kind, self.size, self.name = kind, size, name

    def get_file_type(self):
        return self.kind

    def get_size(self):
        return self.size

    def get_name(self):
        return self.name


class Bytes:
    def __init__(self, data):
        self.data = data

    def get_data(self):
        return self.data


class File:
    """A `Gio.File`-shaped drop: a regular file, a directory, or one past
    the cap, each with the bytes it would load."""

    def __init__(self, name, data=b"", kind=None, size=None):
        from gi.repository import Gio

        self.name, self.data = name, data
        self.kind = Gio.FileType.REGULAR if kind is None else kind
        self.size = len(data) if size is None else size

    def query_info(self, attributes, flags, cancellable):
        return Info(self.kind, self.size, self.name)

    def load_bytes(self, cancellable):
        return Bytes(self.data), None

    def get_basename(self):
        return self.name


@pytest.fixture
def inline_later(monkeypatch):
    """`remoteuploads._later` with its thread and its idle landing run inline."""

    def later(work, land, name):
        try:
            result = work()
        except Exception as err:
            result = err if isinstance(err, ValueError) else ValueError(str(err))
        land(result)

    monkeypatch.setattr(remoteuploads, "_later", later)


def test_upload_files_sends_each_file_under_its_own_name_in_the_drops_order(inline_later, monkeypatch):
    link = Link()

    def http_put(query, body, headers=None):
        link.puts.append((query, body))
        name = query.split("name=")[1].split("&")[0]
        return 200, {}, json.dumps({"path": "/srv/uploads/sid-1/" + name}).encode()

    link.http_put = http_put
    monkeypatch.setattr(remoteuploads.apilink, "current", lambda: link)
    done = []
    files = [File("a.png", b"A"), File("b c.txt", b"BC")]
    remoteuploads.upload_files(files, "sid-1", lambda p, e: done.append((p, e)))
    assert done == [(["/srv/uploads/sid-1/a.png", "/srv/uploads/sid-1/b%20c.txt"], [])]
    assert link.puts == [
        ("/api/upload?name=a.png&session=sid-1", b"A"),
        ("/api/upload?name=b%20c.txt&session=sid-1", b"BC"),
    ]


def test_upload_files_refuses_a_directory_and_a_file_past_the_cap_and_keeps_going(inline_later, monkeypatch):
    from gi.repository import Gio

    link = Link()
    monkeypatch.setattr(remoteuploads.apilink, "current", lambda: link)
    done = []
    files = [
        File("folder", kind=Gio.FileType.DIRECTORY),
        File("huge.bin", b"x", size=uploads.MAX_BYTES + 1),
        Gio.File.new_for_path("/nonexistent/collins-test/missing.png"),  # a real GLib.Error
        File("ok.png", b"ok"),
    ]
    remoteuploads.upload_files(files, None, lambda p, e: done.append((p, e)))
    ((paths, errors),) = done
    assert paths == ["/srv/uploads/sid-1/shot.png"]  # the stub's one path
    assert link.puts == [("/api/upload?name=ok.png", b"ok")]  # the refused ones were never sent
    assert len(errors) == 3
    assert "folder isn't a file" in errors[0]
    assert "too large" in errors[1]
    assert errors[2]  # GLib's own words for the missing file


def test_upload_files_with_a_dead_link_fails_the_drop_as_a_whole(inline_later, monkeypatch):
    monkeypatch.setattr(remoteuploads.apilink, "current", lambda: None)
    done = []
    remoteuploads.upload_files([File("a.png", b"A")], "sid-1", lambda p, e: done.append((p, e)))
    assert done == [([], ["Not connected to the service"])]


def test_upload_png_names_the_texture_by_prefix_and_lands_the_path_or_the_error(inline_later, monkeypatch):
    link = Link()
    monkeypatch.setattr(remoteuploads.apilink, "current", lambda: link)
    done = []
    remoteuploads.upload_png(b"\x89PNG", "paste", "sid-1", lambda path, error: done.append((path, error)))
    assert done == [("/srv/uploads/sid-1/shot.png", None)]
    query = link.puts[0][0]
    assert query.startswith("/api/upload?name=paste-") and query.endswith(".png&session=sid-1")
    monkeypatch.setattr(remoteuploads.apilink, "current", lambda: Link(status=500))
    remoteuploads.upload_png(b"\x89PNG", "drop", None, lambda path, error: done.append((path, error)))
    assert done[-1] == (None, "The service refused the upload (500).")
