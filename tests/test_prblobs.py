"""Tests for prblobs — the blob fetch behind the PR view's image previews:
which paths count as images, the key one is cached under, the gates a
repository's own strings pass before they can name a request, and the
degradations that leave a preview as a stand-in rather than an exception."""

import pytest

from collins import prblobs, prdetail, prstatus

REPO = "episode6/collins"
SHA = "0123456789abcdef0123456789abcdef01234567"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 56


@pytest.fixture
def gh(monkeypatch):
    """Stub gh_bytes with a queued reply; returns (push, calls)."""
    calls = []
    replies = []

    def gh_bytes(args, max_bytes=None):
        calls.append((args, max_bytes))
        return replies.pop(0) if replies else None

    monkeypatch.setattr(prstatus, "gh_bytes", gh_bytes)
    return replies.append, calls


# -- what counts as an image --------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["data/icon.png", "a/b/Shot.PNG", "x.svg", "x.jpeg", "x.gif", "x.webp", "x.ico"],
)
def test_image_names_are_recognized(path):
    assert prblobs.is_image(path) is True


@pytest.mark.parametrize(
    "path", ["collins/prview.py", "README.md", "png", "dir.png/file.txt", "", None]
)
def test_everything_else_renders_as_a_patch(path):
    assert prblobs.is_image(path) is False


def test_the_suffix_comes_back_lowercased():
    assert prblobs.image_suffix("data/Icon.SVG") == ".svg"


# -- the cache key ------------------------------------------------------------


def test_the_key_names_the_commit_not_the_branch():
    """A branch moves under an open page; the key mustn't, or a push would
    show yesterday's picture for today's file."""
    key = prblobs.cache_key(REPO, SHA, "data/icon.png")
    assert SHA in key and key.endswith("data/icon.png")
    assert key != prblobs.cache_key(REPO, "f" * 40, "data/icon.png")


# -- which sides a changed image has ------------------------------------------


BASE = "5f2397053bd8c9661eed7b5928c753a91dd20a94"


def _detail(base_oid=BASE, head_oid=SHA, head_repository=""):
    return prdetail.PullRequestDetail(
        summary=prstatus.PullRequest(number=1, url="u", repository=REPO),
        body="", author="", created_at="", base_ref="main", head_ref="topic",
        base_oid=base_oid, head_oid=head_oid, head_repository=head_repository,
        additions=0, deletions=0, changed_files=1, labels=(), checks=(),
        timeline=(), files=(),
    )


def _file(path="data/icon.png", change_type="MODIFIED", patch=None):
    return prdetail.PrFile(path, 0, 0, patch, change_type)


def test_a_changed_image_has_a_before_and_an_after():
    before, after = prblobs.sides(_file(), _detail())
    assert (before.before, before.ref, before.repository) == (True, BASE, REPO)
    assert (after.before, after.ref, after.repository) == (False, SHA, REPO)
    assert after.key == prblobs.cache_key(REPO, SHA, "data/icon.png")


def test_an_added_image_has_no_before():
    (side,) = prblobs.sides(_file(change_type="ADDED"), _detail())
    assert side.before is False


def test_a_deleted_image_has_no_after():
    for word in ("DELETED", "REMOVED"):
        (side,) = prblobs.sides(_file(change_type=word), _detail())
        assert side.before is True


def test_a_rename_shows_only_its_after():
    """gh's file list carries the new name and not the old one, so there is
    no before to ask for."""
    (side,) = prblobs.sides(_file(change_type="RENAMED"), _detail())
    assert side.before is False


def test_a_forks_after_comes_from_the_fork():
    sides = prblobs.sides(_file(), _detail(head_repository="contributor/collins"))
    assert [s.repository for s in sides] == [REPO, "contributor/collins"]


def test_the_patch_says_what_a_missing_change_type_didnt():
    added = _file(change_type="", patch="diff --git a/x b/x\nnew file mode 100644\n")
    gone = _file(change_type="", patch="diff --git a/x b/x\ndeleted file mode 100644\n")
    assert [s.before for s in prblobs.sides(added, _detail())] == [False]
    assert [s.before for s in prblobs.sides(gone, _detail())] == [True]
    assert len(prblobs.sides(_file(change_type=""), _detail())) == 2


def test_a_file_that_isnt_an_image_has_no_sides():
    assert prblobs.sides(_file(path="collins/prview.py"), _detail()) == ()


def test_no_commit_means_no_picture():
    """A preview names a commit or it doesn't happen: a branch moves under an
    open page."""
    assert prblobs.sides(_file(), _detail(base_oid="", head_oid="")) == ()
    assert len(prblobs.sides(_file(), _detail(base_oid=""))) == 1


# -- the fetch ----------------------------------------------------------------


def test_a_blob_lands_as_a_file(gh, tmp_path):
    push, calls = gh
    push(PNG)
    path = prblobs.fetch_to_file(REPO, SHA, "data/icon.png", tmp_path)
    assert path.read_bytes() == PNG
    assert path.suffix == ".png"  # the decoder downstream sniffs by name
    (args, max_bytes) = calls[0]
    assert args[0] == "api"
    assert args[1] == f"repos/{REPO}/contents/data/icon.png?ref={SHA}"
    assert args[2:] == ["-H", "Accept: application/vnd.github.raw"]
    assert max_bytes == prblobs.MAX_BLOB_BYTES


def test_a_path_with_spaces_and_marks_travels_encoded(gh, tmp_path):
    push, calls = gh
    push(PNG)
    prblobs.fetch_to_file(REPO, SHA, "docs/a b#c.png", tmp_path)
    assert calls[0][0][1] == (
        f"repos/{REPO}/contents/docs/a%20b%23c.png?ref={SHA}"
    )


def test_a_failed_call_is_a_blob_error_not_a_crash(gh, tmp_path):
    push, _calls = gh
    push(None)  # no gh, offline, or a 404: the before-side of an added file
    with pytest.raises(prblobs.BlobError):
        prblobs.fetch_to_file(REPO, SHA, "data/icon.png", tmp_path)


def test_an_empty_reply_is_a_failure(gh, tmp_path):
    push, _calls = gh
    push(b"")
    with pytest.raises(prblobs.BlobError):
        prblobs.fetch_to_file(REPO, SHA, "data/icon.png", tmp_path)


# -- the gates ----------------------------------------------------------------


@pytest.mark.parametrize(
    "repository, ref, path",
    [
        ("episode6/collins", SHA, "collins/prview.py"),  # not an image
        ("episode6", SHA, "data/icon.png"),  # not owner/name
        ("episode6/collins;rm -rf", SHA, "data/icon.png"),
        ("episode6/collins", "main", "data/icon.png"),  # a ref that isn't a commit
        ("episode6/collins", "", "data/icon.png"),
        ("episode6/collins", SHA, "../../etc/hosts.png"),
        ("episode6/collins", SHA, "/etc/hosts.png"),
        ("episode6/collins", SHA, "x" * 600 + ".png"),
    ],
)
def test_nothing_ungated_reaches_gh(gh, tmp_path, repository, ref, path):
    _push, calls = gh
    with pytest.raises(prblobs.BlobError):
        prblobs.fetch_to_file(repository, ref, path, tmp_path)
    assert calls == []
