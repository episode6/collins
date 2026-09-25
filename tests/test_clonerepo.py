"""Tests for clonerepo: the Clone Repository dialog's GTK-free rules."""

from pathlib import Path

import pytest

from collins import clonerepo
from collins.clonerepo import GIT, GITHUB, CloneSource, Repo

# -- parse_source ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "spec", "name"),
    [
        ("episode6/collins", GITHUB, "episode6/collins", "collins"),
        ("  episode6/collins  ", GITHUB, "episode6/collins", "collins"),
        ("episode6/collins.git", GITHUB, "episode6/collins", "collins"),
        ("a-b/my.repo_x", GITHUB, "a-b/my.repo_x", "my.repo_x"),
        (
            "https://github.com/episode6/collins",
            GITHUB,
            "https://github.com/episode6/collins",
            "collins",
        ),
        (
            "https://github.com/episode6/collins.git",
            GITHUB,
            "https://github.com/episode6/collins.git",
            "collins",
        ),
        ("git@github.com:episode6/collins.git", GITHUB, "git@github.com:episode6/collins.git", "collins"),
        ("https://gitlab.com/group/sub/proj.git", GIT, "https://gitlab.com/group/sub/proj.git", "proj"),
        ("ssh://git@example.org:2222/srv/repo.git/", GIT, "ssh://git@example.org:2222/srv/repo.git/", "repo"),
        ("git@example.org:team/thing", GIT, "git@example.org:team/thing", "thing"),
        ("file:///tmp/src/proj/.git", GIT, "file:///tmp/src/proj/.git", "proj"),
        ("host.example:repo.git", GIT, "host.example:repo.git", "repo"),
    ],
)
def test_parse_source_recognises(text, kind, spec, name):
    assert clonerepo.parse_source(text) == CloneSource(kind, spec, name)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "collins",  # a filter word
        "e6 coll",  # two filter words
        "-upload-pack=touch/x",  # would reach argv as an option
        "--help",
        "ext::sh -c touch% /tmp/pwned",  # whitespace and a refused transport
        "ext::foo",
        "fd::17",  # any <transport>::<address> is a remote helper
        "host:",
        "javascript://github.com/a/b",  # not a scheme git clones from
        "https://github.com/",  # no folder name
        "https://example.org/..",
        "episode6/..",
        "owner/re po",
        "owner/repo\x07",
        "-owner/repo",
        "a/b/c",  # three segments is neither owner/repo nor an address
        "x" * 3000 + "://h/p",
    ],
)
def test_parse_source_rejects(text):
    assert clonerepo.parse_source(text) is None


def test_is_address_only_for_addresses():
    assert clonerepo.is_address("https://github.com/episode6/collins")
    assert clonerepo.is_address("git@example.org:team/thing")
    assert not clonerepo.is_address("episode6/collins")
    assert not clonerepo.is_address("collins")


def test_github_name_reads_the_repository_off_any_github_spelling():
    for text in (
        "episode6/collins",
        "episode6/collins.git",
        "https://github.com/episode6/collins.git",
        "git@github.com:episode6/collins.git",
    ):
        assert clonerepo.parse_source(text).github_name == "episode6/collins", text
    assert clonerepo.parse_source("https://gitlab.com/a/b").github_name is None


# -- the repository list -----------------------------------------------------


def test_repos_page_args_cover_orgs_and_collaborations():
    args = clonerepo.repos_page_args(3)
    assert args[0] == "api"
    assert "page=3" in args[1]
    assert "per_page=100" in args[1]
    assert "affiliation=owner,collaborator,organization_member" in args[1]
    assert args[2] == "--jq"


def test_parse_repos_validates_shape():
    data = [
        {"full_name": "ghackett/dots", "description": "  my\n dotfiles ", "private": True},
        {"full_name": "episode6/collins", "description": None, "fork": True, "archived": True},
        {"full_name": "not a repo"},
        {"full_name": 42},
        {"description": "nameless"},
        "junk",
        {"full_name": "x/" + "y" * 200},
    ]
    assert clonerepo.parse_repos(data) == [
        Repo("ghackett/dots", "my dotfiles", private=True),
        Repo("episode6/collins", "", fork=True, archived=True),
    ]
    assert clonerepo.parse_repos({"message": "Bad credentials"}) is None
    assert clonerepo.parse_repos(None) is None


def _page(start, count):
    return [{"full_name": f"o/r{i}"} for i in range(start, start + count)]


def test_fetch_repos_pages_until_a_short_page():
    calls = []
    pages = {1: _page(0, 100), 2: _page(100, 100), 3: _page(200, 7)}

    def fetch(args):
        page = int(args[1].split("page=")[2].split("&")[0])
        calls.append(page)
        return pages[page]

    seen = []
    repos = clonerepo.fetch_repos(fetch, lambda so_far: seen.append(len(so_far)))
    assert calls == [1, 2, 3]
    assert len(repos) == 207
    assert seen == [100, 200, 207]


def test_fetch_repos_is_bounded_and_dedupes():
    calls = []

    def fetch(args):
        calls.append(args)
        return _page(0, 100)  # the same full page forever

    repos = clonerepo.fetch_repos(fetch)
    assert len(calls) == clonerepo.MAX_PAGES
    assert len(repos) == 100


def test_fetch_repos_first_failure_is_none_later_keeps_what_arrived():
    assert clonerepo.fetch_repos(lambda args: None) is None
    replies = iter([_page(0, 100), None])
    assert len(clonerepo.fetch_repos(lambda args: next(replies))) == 100


REPOS = [
    Repo("ghackett/server-scripts", "HakPack Server Scripts"),
    Repo("episode6/collins", "Agent-first IDE for Claude Code"),
    Repo("episode6/mockspresso2", ""),
    Repo("someone/colline-tools", ""),
    Repo("episode6/typed2", "typed keys, collections friendly"),
]


def test_filter_empty_query_keeps_order_and_limit():
    assert clonerepo.filter_repos(REPOS, "") == REPOS
    assert clonerepo.filter_repos(REPOS, "  ", limit=2) == REPOS[:2]


def test_filter_every_word_must_match():
    names = [r.full_name for r in clonerepo.filter_repos(REPOS, "e6 coll")]
    # typed2 gets "coll" from its description only, so it trails.
    assert names == ["episode6/collins", "episode6/typed2"]
    assert clonerepo.filter_repos(REPOS, "e6 zzz") == []


def test_filter_repo_name_beats_owner_and_description():
    names = [r.full_name for r in clonerepo.filter_repos(REPOS, "coll")]
    # Name hits first (collins starts its name), description-only last.
    assert names[0] == "episode6/collins"
    assert names[-1] == "episode6/typed2"
    assert "ghackett/server-scripts" not in names


def test_filter_matches_description_words():
    names = [r.full_name for r in clonerepo.filter_repos(REPOS, "hakpack")]
    assert names == ["ghackett/server-scripts"]


# -- destinations ------------------------------------------------------------


def test_parent_directory_expands_home():
    assert clonerepo.parent_directory("", home="/home/u") == "/home/u"
    assert clonerepo.parent_directory("~", home="/home/u") == "/home/u"
    assert clonerepo.parent_directory("~/dev", home="/home/u") == "/home/u/dev"
    assert clonerepo.parent_directory(" /srv/code ", home="/home/u") == "/srv/code"
    assert clonerepo.parent_directory("dev", home="/home/u") == "dev"


def test_destination_is_parent_plus_git_folder_name():
    source = clonerepo.parse_source("https://github.com/episode6/collins.git")
    assert clonerepo.destination("~/dev/", source, home="/home/u") == Path("/home/u/dev/collins")
    assert clonerepo.destination("/a/../b", source) == Path("/b/collins")
    assert clonerepo.destination("relative", source) is None
    assert clonerepo.destination("/x", None) is None


def test_destination_status(tmp_path):
    source = CloneSource(GIT, "file:///src/proj", "proj")
    parent = str(tmp_path)
    dest = clonerepo.destination(parent, source)
    assert clonerepo.destination_status(parent, dest) == clonerepo.READY

    dest.mkdir()
    assert clonerepo.destination_status(parent, dest) == clonerepo.READY  # empty dir: git takes it
    (dest / "f").write_text("x")
    assert clonerepo.destination_status(parent, dest) == clonerepo.EXISTS

    deeper = str(tmp_path / "new" / "deeper")
    assert (
        clonerepo.destination_status(deeper, clonerepo.destination(deeper, source))
        == clonerepo.CREATES_PARENT
    )

    (tmp_path / "file").write_text("x")
    blocked = str(tmp_path / "file" / "sub")
    assert (
        clonerepo.destination_status(blocked, clonerepo.destination(blocked, source))
        == clonerepo.BLOCKED
    )
    assert clonerepo.destination_status("rel/dir", None) == clonerepo.RELATIVE


def test_destination_status_file_in_the_way(tmp_path):
    (tmp_path / "proj").write_text("x")
    dest = tmp_path / "proj"
    assert clonerepo.destination_status(str(tmp_path), dest) == clonerepo.EXISTS


# -- the command -------------------------------------------------------------


def test_clone_argv_github_goes_through_gh():
    source = clonerepo.parse_source("episode6/collins")
    dest = Path("/home/u/dev/collins")
    assert clonerepo.clone_argv(source, dest, "/usr/bin/gh") == [
        "/usr/bin/gh",
        "repo",
        "clone",
        "episode6/collins",
        "/home/u/dev/collins",
    ]


def test_clone_argv_github_without_gh_uses_https():
    source = clonerepo.parse_source("episode6/collins")
    assert clonerepo.clone_argv(source, Path("/d/collins"), None) == [
        "git",
        "clone",
        "--",
        "https://github.com/episode6/collins.git",
        "/d/collins",
    ]
    url = clonerepo.parse_source("git@github.com:episode6/collins.git")
    assert clonerepo.clone_argv(url, Path("/d/collins"), None)[3] == "git@github.com:episode6/collins.git"


def test_clone_argv_other_hosts_use_git_with_separator():
    source = clonerepo.parse_source("https://gitlab.com/a/b.git")
    assert clonerepo.clone_argv(source, Path("/d/b"), "/usr/bin/gh") == [
        "git",
        "clone",
        "--",
        "https://gitlab.com/a/b.git",
        "/d/b",
    ]


def test_clone_env_disables_prompts():
    env = clonerepo.clone_env({"PATH": "/bin"})
    assert env["PATH"] == "/bin"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GH_PROMPT_DISABLED"] == "1"


def test_error_summary_keeps_the_tail_and_collapses_progress():
    output = (
        "Cloning into '/d/x'...\n"
        "remote: Counting objects: 10% (1/10)\rremote: Counting objects: 100% (10/10)\n"
        "\n"
        "fatal: could not read Username for 'https://example.org': terminal prompts disabled\n"
    )
    summary = clonerepo.error_summary(output)
    assert summary.splitlines() == [
        "Cloning into '/d/x'...",
        "remote: Counting objects: 100% (10/10)",
        "fatal: could not read Username for 'https://example.org': terminal prompts disabled",
    ]
    assert len(clonerepo.error_summary("x" * 5000)) <= 601
