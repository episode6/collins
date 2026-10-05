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
