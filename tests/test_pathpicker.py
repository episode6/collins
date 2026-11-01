# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The path picker's GTK-free model (collins.pathpickermodel, split-service
spec §3.24, PR-3.2, D60): the entry's text read as a folder and the name
typed so far (a `~` left for the service to expand), the rows filtered and
completed, the shortcuts, the new folder's name checked before the round
trip, the listing's ask numbering that drops a stale answer, and the three
requests over a fake link: `fs.dirs` (the reply's shape: the folder as
listed, `{name, symlink}` rows, `truncated`), `fs.newdir` and `fs.stat` —
never the editor's confined `fs.list` / `fs.mkdir`."""

from __future__ import annotations

import pytest

from collins import apilink
from collins import pathpickermodel as model
from collins.api.protocol import RequestRefused
from collins.pathpickermodel import Folder


@pytest.mark.parametrize(
    ("typed", "home", "folder"),
    [
        ("/srv/a", "/home/u", "/srv/a"),
        ("  /srv/a/  ", None, "/srv/a"),  # as typed, tidied
        ("/srv/a/../b", None, "/srv/b"),  # normalised, never resolved
        ("~", "/home/u", "/home/u"),
        ("~/work", "/home/u", "/home/u/work"),
        ("~/work", None, None),  # nothing here can expand it
        ("~other/work", "/home/u", None),
        ("relative/path", "/home/u", None),
        ("", "/home/u", None),
        ("/srv/a\nb", None, None),
    ],
)
def test_a_typed_folder(typed, home, folder):
    """Where no picker can list (a service with no `files`, ruling 26)."""
    assert model.typed_folder(typed, home) == folder

HOME = "/home/svc"


# -- the entry's text --------------------------------------------------------------------


def test_well_formed_is_absolute_or_led_by_the_services_home():
    assert model.well_formed("/srv")
    assert model.well_formed("  ~ ")
    assert model.well_formed("~/src")
    assert not model.well_formed("src")  # relative: whose cwd?
    assert not model.well_formed("~other/x")  # never ~user
    assert not model.well_formed("")


def test_split_names_the_folder_and_the_prefix_without_expanding():
    assert model.split("/srv/p/do") == ("/srv/p", "do")
    assert model.split("/srv/p/") == ("/srv/p", "")
    assert model.split("/srv/p//") == ("/srv/p", "")
    assert model.split("~") == ("~", "")
    assert model.split("~/") == ("~", "")
    assert model.split("~/sr") == ("~", "sr")  # the service expands the `~`
    assert model.split("~/src/a") == ("~/src", "a")
    assert model.split("/") == ("/", "")
    assert model.split("/s") == ("/", "s")
    assert model.split("relative/x") is None


def test_entry_text_child_and_parent():
    assert model.entry_text(f"{HOME}/src") == f"{HOME}/src/"
    assert model.entry_text("/") == "/"
    assert model.entry_text("~") == "~/"
    assert model.child("/srv", "p") == "/srv/p"
    assert model.child("/", "srv") == "/srv"
    assert model.child("~", "src") == "~/src"
    assert model.parent("/srv/p") == "/srv"
    assert model.parent("/srv/p/") == "/srv"
    assert model.parent("/srv") == "/"
    assert model.parent("/") is None
    assert model.parent("~/x") is None  # the service's to expand first


# -- the rows ----------------------------------------------------------------------------


ROWS = [Folder("Documents"), Folder("docs"), Folder("dotnet", link=True), Folder("src")]


def test_folders_takes_the_fs_dirs_rows_in_the_services_order():
    assert model.folders([("b", False), ("a-link", True)]) == [Folder("b"), Folder("a-link", link=True)]


def test_matching_prefers_case_and_falls_back_to_ignoring_it():
    assert model.matching(ROWS, "") == ROWS
    assert model.matching(ROWS, "do") == [Folder("docs"), Folder("dotnet", link=True)]
    assert model.matching(ROWS, "Do") == [Folder("Documents")]
    assert model.matching(ROWS, "SR") == [Folder("src")]  # nothing exact: case ignored
    assert model.matching(ROWS, "x") == []


def test_complete_one_match_is_its_folder_and_more_their_common_start():
    assert model.complete("~/s", ROWS) == "~/src/"
    assert model.complete("~/d", ROWS) == "~/do"
    assert model.complete("~/do", ROWS) is None  # nothing longer in common
    assert model.complete("~/doc", ROWS) == "~/docs/"
    assert model.complete("~/dot", ROWS) == "~/dotnet/"  # a link is a folder to enter
    assert model.complete("/srv/x", ROWS) is None
    assert model.complete("relative", ROWS) is None
    assert model.complete("~/", [Folder("only")]) == "~/only/"
    assert model.complete("/srv/p/s", ROWS) == "/srv/p/src/"


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
    assert listing.needs("~", False)
    first = listing.ask("/srv", False)
    second = listing.ask("~", False)
    assert not listing.land(first, [Folder("old")], path="/srv")
    assert listing.rows == [] and listing.path is None
    assert listing.land(second, [Folder("new")], path=HOME, truncated=True)
    assert listing.rows == [Folder("new")] and listing.truncated and listing.error == ""
    # What was asked, and the folder as the service listed it.
    assert listing.folder == "~" and listing.path == HOME
    # A filter of the same folder asks nothing; Show hidden, either way, does.
    assert not listing.needs("~", False)
    assert listing.needs("~", True)
    listing.ask("~", True)
    assert listing.needs("~", False)


def test_an_ask_drops_the_previous_folders_rows_until_its_answer_lands():
    """The Fable review of PR 624, S1: while a folder's listing is on its
    way, the previous folder's rows, path, cut and refusal are gone, so no
    click, Tab or New folder acts on them under the new folder's name."""
    listing = model.Listing()
    assert not listing.pending
    token = listing.ask("/home/u", False)
    assert listing.pending
    listing.land(token, [Folder("dev"), Folder("docs")], path="/home/u", truncated=True)
    assert not listing.pending and listing.path == "/home/u"
    listing.ask("/home/u/dev", False)
    assert listing.pending
    assert listing.rows == [] and listing.path is None and not listing.truncated and listing.error == ""
    # The same folder typed on (a filter) asks nothing more, and is still pending.
    assert not listing.needs("/home/u/dev", False) and listing.pending
    failed = listing.ask("/root", False)
    listing.land(failed, None, error="Couldn't read the folder")
    assert not listing.pending
    listing.ask("/srv", False)
    assert listing.error == ""  # a refusal of the folder left is not this one's


def test_a_refused_listing_says_why_and_is_asked_again():
    listing = model.Listing()
    token = listing.ask("/root", False)
    assert listing.land(token, None, truncated=True, error="Couldn't read the folder")
    assert listing.rows == [] and listing.error == "Couldn't read the folder" and not listing.truncated
    assert listing.path is None
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


def test_list_folders_is_an_fs_dirs_of_the_typed_folder(link):
    link.answers["fs.dirs"] = {
        "path": HOME,
        "entries": [{"name": "src", "symlink": False}, {"name": "shared", "symlink": True}],
        "truncated": True,
    }
    assert model.list_folders("~", True) == (HOME, [Folder("src"), Folder("shared", link=True)], True)
    assert link.calls[-1] == {"t": "fs.dirs", "path": "~", "hidden": True}
    assert not any(call["t"] in ("fs.list", "fs.mkdir") for call in link.calls)


def test_list_folders_says_a_refusal_as_it_came(link):
    # A service without the request answers `unknown`: shown as any
    # refusal, with no roots-only fallback (D60).
    link.answers["fs.dirs"] = RequestRefused("unknown", "{type}: not served here", {"type": "fs.dirs"})
    with pytest.raises(RequestRefused):
        model.list_folders("/srv", False)
    assert [call["t"] for call in link.calls] == ["fs.dirs"]


def test_make_folder_is_an_fs_newdir_of_the_name_in_the_folder(link):
    link.answers["fs.newdir"] = {"path": f"{HOME}/new"}
    assert model.make_folder(HOME, " new ") == f"{HOME}/new"
    assert link.calls[-1] == {"t": "fs.newdir", "path": f"{HOME}/new"}
    link.answers["fs.newdir"] = RequestRefused("refused", "The name is taken", {"reason": "exists"})
    with pytest.raises(RequestRefused):
        model.make_folder(HOME, "new")


def test_stat_folder_is_the_services_fs_stat_echoed(link):
    link.answers["fs.stat"] = {"kind": "dir", "size": None, "mtime": 1, "inside": False, "path": f"{HOME}/p"}
    assert model.stat_folder("~/p") == f"{HOME}/p"  # `~` expanded by the service
    assert link.calls[-1]["t"] == "fs.stat" and link.calls[-1]["path"] == "~/p"
    link.answers["fs.stat"] = {"kind": "file", "size": 3, "mtime": 1, "inside": False, "path": "/srv/p"}
    assert model.stat_folder("/srv/p") is None
    link.answers["fs.stat"] = {"kind": "missing", "size": None, "mtime": None, "inside": False}
    assert model.stat_folder("/srv/q") is None
