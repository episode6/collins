# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The path picker's GTK-free model (collins.pathpickermodel, split-service
spec §3.24, PR-3.2): the entry's text read as a folder and a prefix with
`~` the service's home, the rows filtered and completed, the shortcuts,
the new folder's name checked before the round trip, the listing's ask
numbering that drops a stale answer, and the three requests over a fake
link (`fs.list` with the folder as its own root, `fs.mkdir` confined to
the folder it goes in, `fs.stat` for *Open*)."""

from __future__ import annotations

import pytest

from collins import apilink, remotefiles
from collins import pathpickermodel as model
from collins.api.protocol import RequestRefused
from collins.pathpickermodel import Folder

HOME = "/home/svc"


# -- the entry's text --------------------------------------------------------------------


def test_expand_takes_the_services_home_and_absolute_paths_only():
    assert model.expand("~", HOME) == HOME
    assert model.expand("~/src/", HOME) == f"{HOME}/src"
    assert model.expand("  /srv//p/../q/ ", HOME) == "/srv/q"
    assert model.expand("/", HOME) == "/"
    assert model.expand("src", HOME) is None  # relative: whose cwd?
    assert model.expand("~other/x", HOME) is None  # never ~user
    assert model.expand("~/x", None) is None  # no home known yet
    assert model.expand("~/x", "relative") is None


def test_split_names_the_folder_and_the_prefix():
    assert model.split("/srv/p/do", HOME) == ("/srv/p", "do")
    assert model.split("/srv/p/", HOME) == ("/srv/p", "")
    assert model.split("~", HOME) == (HOME, "")
    assert model.split("~/", HOME) == (HOME, "")
    assert model.split("~/sr", HOME) == (HOME, "sr")
    assert model.split("/", HOME) == ("/", "")
    assert model.split("/s", HOME) == ("/", "s")
    assert model.split("relative/x", HOME) is None


def test_display_and_entry_text_abbreviate_with_the_services_home():
    assert model.display(HOME, HOME) == "~"
    assert model.display(f"{HOME}/src", HOME) == "~/src"
    assert model.display("/home/svcx/a", HOME) == "/home/svcx/a"  # a prefix of the text is no parent
    assert model.display("/srv", None) == "/srv"
    assert model.entry_text(f"{HOME}/src", HOME) == "~/src/"
    assert model.entry_text("/", HOME) == "/"
    assert model.entry_text(HOME, f"{HOME}/") == "~/"


def test_child_and_parent():
    assert model.child("/srv", "p") == "/srv/p"
    assert model.child("/", "srv") == "/srv"
    assert model.parent("/srv/p") == "/srv"
    assert model.parent("/srv") == "/"
    assert model.parent("/") is None


# -- the rows ----------------------------------------------------------------------------


ROWS = [Folder("Documents"), Folder("docs"), Folder("dotnet", link=True), Folder("src")]


def test_folders_keeps_the_subfolders_in_the_services_order():
    entries = [
        remotefiles.Entry("b", "dir", False),
        remotefiles.Entry("a-link", "symlink", False),
        remotefiles.Entry("README.md", "file", False),
        remotefiles.Entry("build", "dir", True),  # ignored by git: still a folder to pick
    ]
    assert model.folders(entries) == [Folder("b"), Folder("a-link", link=True), Folder("build")]


def test_matching_prefers_case_and_falls_back_to_ignoring_it():
    assert model.matching(ROWS, "") == ROWS
    assert model.matching(ROWS, "do") == [Folder("docs"), Folder("dotnet", link=True)]
    assert model.matching(ROWS, "Do") == [Folder("Documents")]
    assert model.matching(ROWS, "SR") == [Folder("src")]  # nothing exact: case ignored
    assert model.matching(ROWS, "x") == []


def test_wants_hidden_is_a_leading_dot():
    assert model.wants_hidden(".con")
    assert not model.wants_hidden("con")
    assert not model.wants_hidden("")


def test_complete_one_match_is_its_folder_and_more_their_common_start():
    assert model.complete("~/s", HOME, ROWS) == "~/src/"
    assert model.complete("~/d", HOME, ROWS) == "~/do"
    assert model.complete("~/do", HOME, ROWS) is None  # nothing longer in common
    assert model.complete("~/doc", HOME, ROWS) == "~/docs/"
    assert model.complete("/srv/x", HOME, ROWS) is None
    assert model.complete("relative", HOME, ROWS) is None
    assert model.complete("~/", HOME, [Folder("only")]) == "~/only/"


def test_complete_outside_home_keeps_the_absolute_path():
    assert model.complete("/srv/p/s", HOME, ROWS) == "/srv/p/src/"


# -- the shortcuts -----------------------------------------------------------------------


def test_shortcuts_are_home_then_distinct_roots_by_name():
    roots = ["/srv/zeta", "/srv/alpha", "/srv/alpha/", HOME, "relative", None, "/work/Beta"]
    assert model.shortcuts(HOME, roots) == [
        ("", HOME),
        ("alpha", "/srv/alpha"),
        ("Beta", "/work/Beta"),
        ("zeta", "/srv/zeta"),
    ]


def test_shortcuts_without_a_home_and_bounded():
    roots = [f"/srv/p{n:02d}" for n in range(40)]
    found = model.shortcuts(None, roots)
    assert len(found) == model.SHORTCUTS_MAX
    assert found[0] == ("p00", "/srv/p00")
    assert model.shortcuts(HOME, roots)[0] == ("", HOME)
    assert len(model.shortcuts(HOME, roots)) == model.SHORTCUTS_MAX


# -- a new folder's name -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "verdict"),
    [
        ("notes", None),
        ("  notes  ", None),  # stripped, as the request will be
        ("", "Type a name for the folder"),
        ("   ", "Type a name for the folder"),
        ("a/b", "A folder takes a bare name"),
        ("..", "A folder takes a bare name"),
        (".", "A folder takes a bare name"),
        ("a\x00b", "A folder takes a bare name"),
    ],
)
def test_new_folder_error_is_the_services_rule(name, verdict):
    assert model.new_folder_error(name) == verdict


# -- the listing's ask numbering ---------------------------------------------------------


def test_listing_applies_only_the_newest_answer():
    listing = model.Listing()
    assert listing.needs("/srv", False)
    first = listing.ask("/srv", False)
    second = listing.ask("/srv/p", False)
    assert not listing.land(first, [Folder("old")])
    assert listing.rows == []
    assert listing.land(second, [Folder("new")], truncated=True)
    assert listing.rows == [Folder("new")] and listing.truncated and listing.error == ""
    assert listing.folder == "/srv/p"
    # A filter of the same folder asks nothing; the dot folders do, once.
    assert not listing.needs("/srv/p", False)
    assert listing.needs("/srv/p", True)
    listing.ask("/srv/p", True)
    assert not listing.needs("/srv/p", False)


def test_a_refused_listing_says_why_and_is_asked_again():
    listing = model.Listing()
    token = listing.ask("/root", False)
    assert listing.land(token, None, truncated=True, error="Outside every root")
    assert listing.rows == [] and listing.error == "Outside every root" and not listing.truncated
    assert listing.needs("/root", False)  # coming back to it asks again


# -- the requests, over a fake link ------------------------------------------------------


class FakeLink(apilink.Link):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []
        self.answers: dict[str, object] = {}

    def _request(self, message: dict, timeout: float | None = None) -> dict:
        self.calls.append(dict(message))
        answer = self.answers.get(message["t"])
        if isinstance(answer, Exception):
            raise answer
        return dict(answer or {})


@pytest.fixture
def link(monkeypatch):
    fake = FakeLink()
    monkeypatch.setattr(apilink, "_current", fake)
    return fake


def test_list_folders_is_an_fs_list_of_the_folder_as_its_own_root(link):
    link.answers["fs.list"] = {
        "entries": [
            {"name": "src", "kind": "dir", "ignored": False},
            {"name": "a.txt", "kind": "file", "ignored": False},
        ],
        "truncated": True,
    }
    assert model.list_folders("/srv/p", True) == ([Folder("src")], True)
    assert link.calls[-1] == {"t": "fs.list", "path": "/srv/p", "hidden": True, "root": "/srv/p"}


def test_make_folder_is_an_fs_mkdir_inside_the_folder(link):
    assert model.make_folder("/srv/p", " new ") == "/srv/p/new"
    assert link.calls[-1] == {"t": "fs.mkdir", "path": "/srv/p/new", "root": "/srv/p"}
    link.answers["fs.mkdir"] = RequestRefused("refused", "The name is taken", {"reason": "exists"})
    with pytest.raises(RequestRefused):
        model.make_folder("/srv/p", "new")


def test_stat_folder_is_the_services_fs_stat(link):
    link.answers["fs.stat"] = {"kind": "dir", "size": None, "mtime": 1, "inside": False, "path": "/srv/p"}
    assert model.stat_folder("/srv/p") == "/srv/p"
    assert link.calls[-1]["t"] == "fs.stat" and link.calls[-1]["path"] == "/srv/p"
    link.answers["fs.stat"] = {"kind": "file", "size": 3, "mtime": 1, "inside": False, "path": "/srv/p"}
    assert model.stat_folder("/srv/p") is None
    link.answers["fs.stat"] = {"kind": "missing", "size": None, "mtime": None, "inside": False}
    assert model.stat_folder("/srv/q") is None
