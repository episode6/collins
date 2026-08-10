"""Tests for prdetail — the on-demand full-PR fetch behind the native PR view:
one recorded `gh pr view --json` reply parsed into frozen records, the unified
diff split per file, and the degradations (no diff, junk fields, hostile
strings) that keep a load from failing whole."""

import pytest

from collins import prdetail, prstatus
from collins.prdetail import (
    PrComment,
    PrReview,
    fetch,
    parse_detail,
    split_unified_diff,
)

URL = "https://github.com/episode6/collins/pull/263"


@pytest.fixture(autouse=True)
def clean_status_cache():
    """fetch() absorbs into prstatus's module cache; keep tests independent."""
    prstatus._statuses.clear()
    prstatus._inflight.clear()
    yield
    prstatus._statuses.clear()
    prstatus._inflight.clear()


# Shaped like real `gh pr view --json` output (recorded off episode6/collins
# PRs 55 and 263, 2026-08-09), trimmed to the fields Collins asks for.
def _reply(**overrides):
    data = {
        "number": 263,
        "url": URL,
        "title": "Every panel tab is its own drag handle",
        "state": "OPEN",
        "isDraft": False,
        "body": "The strip's tabs now drag.\n\n## What it does\n- things",
        "author": {"id": "MDQ6VXNlcjU2ODkzMA==", "is_bot": False,
                   "login": "ghackett", "name": "Geoff Hackett"},
        "createdAt": "2026-08-09T22:11:04Z",
        "baseRefName": "main",
        "headRefName": "collins/tab-drag-handles",
        "additions": 581,
        "deletions": 32,
        "changedFiles": 2,
        "labels": [{"id": "L1", "name": "enhancement", "color": "a2eeef",
                    "description": ""}],
        "comments": [_comment()],
        "reviews": [_review()],
        "statusCheckRollup": [
            {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED",
             "conclusion": "SUCCESS", "workflowName": "CI",
             "startedAt": "2026-08-09T22:12:00Z",
             "completedAt": "2026-08-09T22:13:00Z",
             "detailsUrl": "https://github.com/episode6/collins/actions/runs/1/job/2"},
            {"__typename": "CheckRun", "name": "test", "status": "IN_PROGRESS",
             "conclusion": None, "workflowName": "CI",
             "startedAt": "2026-08-09T22:12:00Z", "completedAt": None,
             "detailsUrl": "https://github.com/episode6/collins/actions/runs/1/job/3"},
            {"__typename": "StatusContext", "context": "ci/external",
             "state": "FAILURE", "targetUrl": "https://ci.example.com/build/9"},
        ],
        "mergeable": "MERGEABLE",
        "files": [
            {"path": "collins/paneldnd.py", "additions": 500, "deletions": 30,
             "changeType": "MODIFIED"},
            {"path": "data/icon.png", "additions": 0, "deletions": 0,
             "changeType": "ADDED"},
        ],
    }
    data.update(overrides)
    return data


def _comment(**overrides):
    comment = {
        "id": "IC_kwDOTjjqB88AAAABOAQ16A",
        "author": {"login": "reviewer"},
        "authorAssociation": "MEMBER",
        "body": "Looks close; one nit.",
        "createdAt": "2026-08-09T23:00:00Z",
        "includesCreatedEdit": False,
        "isMinimized": False,
        "minimizedReason": "",
        "reactionGroups": [],
        "url": f"{URL}#issuecomment-5234767336",
        "viewerDidAuthor": False,
    }
    comment.update(overrides)
    return comment


def _review(**overrides):
    review = {
        "id": "PRR_kwDOTjjqB86ymDBK",
        "author": {"login": "reviewer"},
        "authorAssociation": "MEMBER",
        "body": "Ship it.",
        "submittedAt": "2026-08-09T23:30:00Z",
        "includesCreatedEdit": False,
        "reactionGroups": [],
        "state": "APPROVED",
    }
    review.update(overrides)
    return review


DIFF = """\
diff --git a/collins/paneldnd.py b/collins/paneldnd.py
index f1d0a187..bc060321 100644
--- a/collins/paneldnd.py
+++ b/collins/paneldnd.py
@@ -1,3 +1,4 @@
 import logging
+import re

 log = logging.getLogger(__name__)
"""


# -- parsing the view reply ---------------------------------------------------


def test_the_summary_gets_the_full_status_shaping():
    detail = parse_detail(URL, _reply(), DIFF)
    pr = detail.summary
    assert (pr.number, pr.repository) == (263, "episode6/collins")
    assert pr.title == "Every panel tab is its own drag handle"
    assert pr.state == "OPEN"
    assert (pr.passed, pr.failed, pr.pending) == (1, 1, 1)
    assert pr.mergeable == "MERGEABLE"
    assert pr.unresolved is True  # the newest comment is someone else's


def test_the_header_fields_arrive_whole():
    detail = parse_detail(URL, _reply(), DIFF)
    assert detail.body.startswith("The strip's tabs now drag.")
    assert detail.author == "ghackett"
    assert detail.created_at == "2026-08-09T22:11:04Z"
    assert (detail.base_ref, detail.head_ref) == ("main", "collins/tab-drag-handles")
    assert (detail.additions, detail.deletions, detail.changed_files) == (581, 32, 2)
    assert detail.labels == ("enhancement",)


def test_checks_cover_both_of_ghs_shapes():
    checks = parse_detail(URL, _reply(), DIFF).checks
    assert [(c.name, c.state) for c in checks] == [
        ("lint", prstatus.BADGE_PASSED),
        ("test", prstatus.BADGE_PENDING),
        ("ci/external", prstatus.BADGE_FAILED),
    ]
    assert checks[0].url.endswith("/job/2")
    assert checks[2].url == "https://ci.example.com/build/9"


def test_a_check_url_must_be_http():
    """targetUrl is repository-controlled; nothing else may reach a browser."""
    rollup = [{"__typename": "StatusContext", "context": "ci/evil",
               "state": "SUCCESS", "targetUrl": "javascript:alert(1)"}]
    checks = parse_detail(URL, _reply(statusCheckRollup=rollup), None).checks
    assert [(c.name, c.url) for c in checks] == [("ci/evil", "")]


def test_a_nameless_check_is_dropped():
    rollup = [{"state": "SUCCESS"}, {"name": "lint", "conclusion": "SUCCESS"}]
    checks = parse_detail(URL, _reply(statusCheckRollup=rollup), None).checks
    assert [c.name for c in checks] == ["lint"]


def test_the_timeline_merges_comments_and_reviews_by_time():
    reply = _reply(
        comments=[_comment(createdAt="2026-08-09T23:59:00Z", body="Late word."),
                  _comment()],
        reviews=[_review()],
    )
    timeline = parse_detail(URL, reply, None).timeline
    assert [type(entry) for entry in timeline] == [PrComment, PrReview, PrComment]
    assert timeline[0].body == "Looks close; one nit."
    assert timeline[1].state == "APPROVED"
    assert timeline[2].body == "Late word."


def test_a_comment_carries_its_author_stamp_and_link():
    comment = parse_detail(URL, _reply(), None).timeline[0]
    assert comment.author == "reviewer"
    assert comment.created_at == "2026-08-09T23:00:00Z"
    assert comment.url == f"{URL}#issuecomment-5234767336"


def test_minimized_and_empty_comments_are_dropped():
    reply = _reply(
        comments=[_comment(isMinimized=True), _comment(body=""), "junk", None],
        reviews=[],
    )
    assert parse_detail(URL, reply, None).timeline == ()


def test_review_shells_and_pending_reviews_are_dropped():
    """A bodiless COMMENTED review is the shell inline comments hang off (v2's
    GraphQL work); a PENDING one is an unsubmitted draft. Neither is a card."""
    reply = _reply(comments=[], reviews=[
        _review(state="COMMENTED", body=""),
        _review(state="PENDING", submittedAt=None),
        _review(state="CHANGES_REQUESTED", body="Not yet."),
    ])
    timeline = parse_detail(URL, reply, None).timeline
    assert [(r.state, r.body) for r in timeline] == [("CHANGES_REQUESTED", "Not yet.")]


def test_a_worded_commented_review_is_kept():
    reply = _reply(comments=[], reviews=[_review(state="COMMENTED", body="Hm.")])
    assert isinstance(parse_detail(URL, reply, None).timeline[0], PrReview)


def test_files_join_their_patches_by_path():
    files = parse_detail(URL, _reply(), DIFF).files
    assert [f.path for f in files] == ["collins/paneldnd.py", "data/icon.png"]
    assert files[0].patch.startswith("diff --git a/collins/paneldnd.py")
    assert (files[0].additions, files[0].deletions) == (500, 30)
    assert files[1].patch is None  # not in the diff (binary)


def test_no_diff_still_loads_stat_only_files():
    files = parse_detail(URL, _reply(), None).files
    assert [f.patch for f in files] == [None, None]
    assert [f.path for f in files] == ["collins/paneldnd.py", "data/icon.png"]


def test_junk_fields_degrade_to_empty_not_fatal():
    reply = _reply(body=7, author="not-a-dict", createdAt=None, labels="nope",
                   comments={"a": 1}, reviews=None, statusCheckRollup="x",
                   files=None, additions="many", deletions=-3, changedFiles=True,
                   baseRefName=[], headRefName=9)
    detail = parse_detail(URL, reply, DIFF)
    assert detail.body == ""
    assert detail.author == ""
    assert detail.created_at == ""
    assert (detail.base_ref, detail.head_ref) == ("", "")
    assert (detail.additions, detail.deletions, detail.changed_files) == (0, 0, 0)
    assert detail.labels == ()
    assert detail.checks == ()
    assert detail.timeline == ()
    assert detail.files == ()


def test_parse_needs_a_pr_page_url():
    assert parse_detail("https://github.com/episode6", _reply(), DIFF) is None
    assert parse_detail(URL, "not a reply", DIFF) is None


def test_repository_strings_stay_bounded():
    reply = _reply(body="b" * (prdetail._MAX_BODY + 5),
                   headRefName="h" * 999,
                   labels=[{"name": "l" * 999}],
                   files=[{"path": "p" * 9999, "additions": 1, "deletions": 0}])
    detail = parse_detail(URL, reply, None)
    assert len(detail.body) == prdetail._MAX_BODY
    assert len(detail.head_ref) == prdetail._MAX_LINE
    assert len(detail.labels[0]) == prdetail._MAX_LINE
    assert len(detail.files[0].path) == prdetail._MAX_PATH


# -- splitting the unified diff -----------------------------------------------


def test_one_stanza_keeps_its_header_lines():
    split = split_unified_diff(DIFF)
    assert [path for path, _patch in split] == ["collins/paneldnd.py"]
    patch = split[0][1]
    assert patch == DIFF  # header, index line, hunks — the widget renders it all


def test_stanzas_split_in_order():
    text = DIFF + "diff --git a/second.py b/second.py\n" \
                  "--- a/second.py\n+++ b/second.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert [path for path, _ in split_unified_diff(text)] == [
        "collins/paneldnd.py", "second.py"]


def test_an_added_file_reads_its_new_name():
    text = ("diff --git a/new.py b/new.py\nnew file mode 100644\n"
            "index 0000000..1111111\n--- /dev/null\n+++ b/new.py\n"
            "@@ -0,0 +1 @@\n+hello\n")
    assert [path for path, _ in split_unified_diff(text)] == ["new.py"]


def test_a_deleted_file_keeps_its_old_name():
    text = ("diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
            "index 1111111..0000000\n--- a/gone.py\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-bye\n")
    assert [path for path, _ in split_unified_diff(text)] == ["gone.py"]


def test_a_pure_rename_reads_its_rename_to_line():
    text = ("diff --git a/old/name.py b/new/name.py\n"
            "similarity index 100%\nrename from old/name.py\n"
            "rename to new/name.py\n")
    assert [path for path, _ in split_unified_diff(text)] == ["new/name.py"]


def test_a_rename_with_edits_still_reads_the_new_name():
    text = ("diff --git a/old.py b/new.py\nsimilarity index 90%\n"
            "rename from old.py\nrename to new.py\n"
            "index 1111111..2222222 100644\n--- a/old.py\n+++ b/new.py\n"
            "@@ -1 +1 @@\n-a\n+b\n")
    assert [path for path, _ in split_unified_diff(text)] == ["new.py"]


def test_a_binary_file_reads_the_header_line():
    text = ("diff --git a/data/icon.png b/data/icon.png\n"
            "index 1111111..2222222 100644\n"
            "Binary files a/data/icon.png and b/data/icon.png differ\n")
    assert [path for path, _ in split_unified_diff(text)] == ["data/icon.png"]


def test_a_binary_path_with_spaces_splits_where_the_halves_agree():
    text = ("diff --git a/my images/pic 1.png b/my images/pic 1.png\n"
            "Binary files a/my images/pic 1.png and b/my images/pic 1.png differ\n")
    assert [path for path, _ in split_unified_diff(text)] == ["my images/pic 1.png"]


def test_quoted_paths_unquote_escapes_and_octal_utf8():
    # git spells é as its UTF-8 bytes, \303\251, and quotes the whole name.
    text = ('diff --git "a/caf\\303\\251 menu.txt" "b/caf\\303\\251 menu.txt"\n'
            'index 1111111..2222222 100644\n'
            '--- "a/caf\\303\\251 menu.txt"\n'
            '+++ "b/caf\\303\\251 menu.txt"\n'
            "@@ -1 +1 @@\n-a\n+b\n")
    assert [path for path, _ in split_unified_diff(text)] == ["café menu.txt"]


def test_a_quoted_binary_header_unquotes_too():
    text = ('diff --git "a/tab\\there.png" "b/tab\\there.png"\n'
            "Binary files differ\n")
    assert [path for path, _ in split_unified_diff(text)] == ["tab\there.png"]


def test_an_out_of_range_octal_escape_stays_literal():
    # \777 names no byte; a repository writing it doesn't get a crash for it.
    text = ('diff --git "a/x\\777y" "b/x\\777y"\nBinary files differ\n')
    assert split_unified_diff(text)[0][0] == "x\\777y"


def test_preamble_and_empty_input_produce_nothing():
    assert split_unified_diff("") == []
    assert split_unified_diff("hello\nno diff here\n") == []


# -- the fetch itself ---------------------------------------------------------


@pytest.fixture
def gh(monkeypatch):
    """Stub both transport calls; returns (set_view, set_diff, calls)."""
    calls = []
    view = {"value": None}
    diff = {"value": None}

    def gh_json(args, cwd=None):
        calls.append(args)
        return view["value"]

    def gh_text(args, max_bytes=None):
        calls.append(args)
        return diff["value"]

    monkeypatch.setattr(prstatus, "gh_json", gh_json)
    monkeypatch.setattr(prstatus, "gh_text", gh_text)
    return (lambda value: view.update(value=value),
            lambda value: diff.update(value=value), calls)


def test_fetch_asks_gh_twice_and_parses(gh):
    set_view, set_diff, calls = gh
    set_view(_reply())
    set_diff(DIFF)
    detail = fetch(URL)
    assert detail.summary.number == 263
    assert detail.files[0].patch is not None
    assert calls[0][:3] == ["pr", "view", URL]
    assert calls[1] == ["pr", "diff", URL]


def test_fetch_absorbs_into_the_summary_cache(gh):
    """Opening the view updates the chip and mark for free: the reply lands in
    prstatus as a fetch of our own."""
    set_view, _set_diff, _calls = gh
    set_view(_reply())
    fetch(URL)
    pr = prstatus.known(prstatus.parse_pr_url(URL))
    assert pr.title == "Every panel tab is its own drag handle"
    assert (pr.passed, pr.failed, pr.pending) == (1, 1, 1)


def test_fetch_refuses_a_non_pr_url(gh):
    _set_view, _set_diff, calls = gh
    assert fetch("https://example.com/pull/1?x=--version") is None
    assert calls == []  # nothing that isn't a PR page ever reaches argv


def test_fetch_fails_whole_only_with_the_view_reply(gh):
    set_view, _set_diff, _calls = gh
    set_view(None)
    assert fetch(URL) is None


def test_fetch_survives_a_dead_or_oversized_diff(gh):
    """gh_text answers None for a failed call and an over-cap reply alike; the
    load degrades to stat-only files either way."""
    set_view, set_diff, _calls = gh
    set_view(_reply())
    set_diff(None)
    detail = fetch(URL)
    assert detail is not None
    assert [f.patch for f in detail.files] == [None, None]
