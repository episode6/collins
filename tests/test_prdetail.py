"""Tests for prdetail — the on-demand full-PR fetch behind the native PR view:
one recorded `gh pr view --json` reply parsed into frozen records, the unified
diff split per file, and the degradations (no diff, junk fields, hostile
strings) that keep a load from failing whole."""

import json
import subprocess

import pytest

from collins import prdetail, prstatus
from collins.prdetail import (
    PrComment,
    PrReview,
    PrThread,
    fetch,
    fetch_threads,
    file_threads,
    parse_detail,
    split_unified_diff,
)

URL = "https://github.com/episode6/collins/pull/263"


@pytest.fixture(autouse=True)
def clean_status_cache():
    """fetch() absorbs into prstatus's module cache and remembers the signed-in
    login there for the run; keep tests independent of both."""
    prstatus._statuses.clear()
    prstatus._inflight.clear()
    prstatus._viewer = ""
    yield
    prstatus._statuses.clear()
    prstatus._inflight.clear()
    prstatus._viewer = ""


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


def test_a_pr_is_the_viewers_own_when_the_logins_match():
    """What takes the review verdicts off the page: GitHub refuses a review of
    your own pull request, and logins compare case-insensitively — gh spells
    an author back the way the account was registered."""
    assert parse_detail(URL, _reply(), DIFF, viewer="ghackett").viewer_is_author
    assert parse_detail(URL, _reply(), DIFF, viewer="GHackett").viewer_is_author
    assert not parse_detail(URL, _reply(), DIFF, viewer="someone-else").viewer_is_author


def test_an_unanswerable_author_reads_as_somebody_elses():
    """No gh, offline, signed out, or a reply that never named an author: all
    of them leave the page as it was before anyone asked."""
    assert not parse_detail(URL, _reply(), DIFF).viewer_is_author  # no viewer
    assert not parse_detail(URL, _reply(author=None), DIFF, viewer="").viewer_is_author
    assert not parse_detail(
        URL, _reply(author=None), DIFF, viewer="ghackett"
    ).viewer_is_author


def test_checks_cover_both_of_ghs_shapes():
    checks = parse_detail(URL, _reply(), DIFF).checks
    assert [(c.name, c.state) for c in checks] == [
        ("lint", prstatus.BADGE_PASSED),
        ("test", prstatus.BADGE_PENDING),
        ("ci/external", prstatus.BADGE_FAILED),
    ]
    assert checks[0].url.endswith("/job/2")
    assert checks[2].url == "https://ci.example.com/build/9"


def test_a_conflicting_branch_joins_the_checks_as_its_own_row():
    """It blocks the merge exactly as a failed check does, and the Checks list
    is where the page enumerates the blockers — it leads them, carries the
    conflict badge, and has no run to open."""
    checks = parse_detail(URL, _reply(mergeable="CONFLICTING"), DIFF).checks
    assert (checks[0].state, checks[0].url) == (prstatus.BADGE_CONFLICT, "")
    assert checks[0].name
    assert [c.name for c in checks[1:]] == ["lint", "test", "ci/external"]


def test_a_conflicting_pr_with_no_ci_at_all_still_has_a_checks_list():
    reply = _reply(mergeable="CONFLICTING", statusCheckRollup=[])
    assert [c.state for c in parse_detail(URL, reply, DIFF).checks] == [
        prstatus.BADGE_CONFLICT
    ]


def test_a_mergeable_branch_adds_no_row():
    assert not [
        c
        for c in parse_detail(URL, _reply(), DIFF).checks
        if c.state == prstatus.BADGE_CONFLICT
    ]


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_a_settled_pr_never_grows_the_conflict_row(state):
    """Whatever gh still reports for it, a PR that is over isn't going
    anywhere — the summary's own rule (PullRequest.conflicting)."""
    reply = _reply(state=state, mergeable="CONFLICTING")
    assert [c.name for c in parse_detail(URL, reply, DIFF).checks] == [
        "lint", "test", "ci/external"
    ]


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


# -- review threads -----------------------------------------------------------


def _thread_node(**overrides):
    node = {
        "id": "PRRT_kwDOTjjqB85abc123",
        "isResolved": False,
        "isOutdated": False,
        "path": "collins/paneldnd.py",
        "line": 12,
        "comments": {"nodes": [_thread_comment()]},
    }
    node.update(overrides)
    return node


def _thread_comment(**overrides):
    comment = {
        "author": {"login": "reviewer"},
        "createdAt": "2026-08-09T23:10:00Z",
        "body": "Inline nit.",
        "url": f"{URL}#discussion_r100",
        "isMinimized": False,
    }
    comment.update(overrides)
    return comment


def _threads_reply(nodes, has_next=False, cursor=None):
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": nodes,
    }}}}}


def _bare_thread(id, path, line, created, resolved=False):
    return PrThread(
        id=id, path=path, line=line, is_resolved=resolved, is_outdated=False,
        comments=(PrComment("reviewer", created, "hm", ""),),
    )


@pytest.fixture
def graphql(monkeypatch):
    """Stub gh_json with a queue of GraphQL replies; returns (push, calls)."""
    calls = []
    replies = []

    def gh_json(args, cwd=None, timeout=None):
        calls.append(args)
        return replies.pop(0) if replies else None

    monkeypatch.setattr(prstatus, "gh_json", gh_json)
    return replies.append, calls


def test_threads_parse_whole(graphql):
    push, _calls = graphql
    push(_threads_reply([_thread_node()]))
    (thread,) = fetch_threads(URL)
    assert thread.id == "PRRT_kwDOTjjqB85abc123"
    assert (thread.path, thread.line) == ("collins/paneldnd.py", 12)
    assert (thread.is_resolved, thread.is_outdated) == (False, False)
    (comment,) = thread.comments
    assert (comment.author, comment.body) == ("reviewer", "Inline nit.")
    assert comment.url == f"{URL}#discussion_r100"
    assert thread.created_at == "2026-08-09T23:10:00Z"


def test_threads_travel_as_typed_variables(graphql):
    """Nothing is spliced into query text: the repository rides -f (raw
    strings) and the number -F, gh typing it as the Int the query wants."""
    push, calls = graphql
    push(_threads_reply([]))
    fetch_threads(URL)
    (args,) = calls
    assert args[:2] == ["api", "graphql"]
    assert "owner=episode6" in args and "name=collins" in args
    assert args[args.index("owner=episode6") - 1] == "-f"
    assert args[args.index("number=263") - 1] == "-F"
    assert not any(arg.startswith("cursor=") for arg in args)


def test_threads_paginate_with_the_reply_cursor(graphql):
    push, calls = graphql
    push(_threads_reply([_thread_node()], has_next=True, cursor="CUR1"))
    push(_threads_reply([_thread_node(id="PRRT_second")]))
    threads = fetch_threads(URL)
    assert [t.id for t in threads] == ["PRRT_kwDOTjjqB85abc123", "PRRT_second"]
    assert "cursor=CUR1" in calls[1]


def test_threads_stop_at_the_cap(graphql, monkeypatch):
    monkeypatch.setattr(prdetail, "_MAX_THREADS", 2)
    push, calls = graphql
    push(_threads_reply(
        [_thread_node(id="PRRT_a"), _thread_node(id="PRRT_b")],
        has_next=True, cursor="CUR",
    ))
    assert len(fetch_threads(URL)) == 2
    assert len(calls) == 1  # the cap made the next page not worth asking for


def test_a_failed_later_page_keeps_the_earlier_ones(graphql):
    push, _calls = graphql
    push(_threads_reply([_thread_node()], has_next=True, cursor="CUR"))
    push("not a reply")
    assert len(fetch_threads(URL)) == 1


def test_a_failed_first_page_is_no_threads(graphql):
    push, _calls = graphql
    push(None)
    assert fetch_threads(URL) == ()


def test_threads_need_a_pr_page_url(graphql):
    _push, calls = graphql
    assert fetch_threads("https://example.com/pull/1?x=--version") == ()
    assert calls == []


def test_a_cursor_that_doesnt_look_like_one_reads_as_the_last_page(graphql):
    push, calls = graphql
    push(_threads_reply([_thread_node()], has_next=True, cursor="x" * 600))
    assert len(fetch_threads(URL)) == 1
    assert len(calls) == 1


def test_junk_thread_nodes_are_dropped(graphql):
    """No id the mutations could name, no path to anchor under, or nothing
    left to show once minimized and empty comments are skipped: not a card."""
    push, _calls = graphql
    push(_threads_reply([
        "junk",
        None,
        _thread_node(id="not a node id"),
        _thread_node(path=""),
        _thread_node(comments={"nodes": [
            _thread_comment(isMinimized=True), _thread_comment(body="")]}),
        _thread_node(id="PRRT_ok"),
    ]))
    (thread,) = fetch_threads(URL)
    assert thread.id == "PRRT_ok"


def test_a_thread_line_must_be_a_positive_int(graphql):
    push, _calls = graphql
    push(_threads_reply([
        _thread_node(id="PRRT_a", line=None),
        _thread_node(id="PRRT_b", line=True),
        _thread_node(id="PRRT_c", line=-4),
        _thread_node(id="PRRT_d", line="7"),
    ]))
    assert [t.line for t in fetch_threads(URL)] == [None] * 4


def test_a_thread_comment_url_must_be_http(graphql):
    push, _calls = graphql
    push(_threads_reply([_thread_node(comments={"nodes": [
        _thread_comment(url="javascript:alert(1)")]})]))
    (thread,) = fetch_threads(URL)
    assert thread.comments[0].url == ""


def test_threads_anchor_in_the_timeline_by_first_comment():
    threads = (_bare_thread("PRRT_a", "x.py", 1, "2026-08-09T23:15:00Z"),)
    reply = _reply(comments=[_comment()], reviews=[_review()])  # 23:00, 23:30
    timeline = parse_detail(URL, reply, None, threads).timeline
    assert [type(entry) for entry in timeline] == [PrComment, PrThread, PrReview]
    assert parse_detail(URL, reply, None, threads).threads == threads


def test_file_threads_order_by_line_unanchored_last():
    threads = [
        _bare_thread("PRRT_none", "a.py", None, "2026-08-09T23:00:00Z"),
        _bare_thread("PRRT_late", "a.py", 9, "2026-08-09T23:00:00Z"),
        _bare_thread("PRRT_early", "a.py", 2, "2026-08-09T23:00:00Z"),
        _bare_thread("PRRT_other", "b.py", 1, "2026-08-09T23:00:00Z"),
        _bare_thread("PRRT_tie", "a.py", 2, "2026-08-08T23:00:00Z"),
    ]
    assert [t.id for t in file_threads(threads, "a.py")] == [
        "PRRT_tie", "PRRT_early", "PRRT_late", "PRRT_none"]


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
    """Stub the transport calls; returns (set_view, set_diff, calls). The
    thread fetch answers "no reply" here — degrading to a threadless load,
    which has its own tests above — so these stay about the view+diff pair."""
    calls = []
    view = {"value": None}
    diff = {"value": None}

    def gh_json(args, cwd=None, timeout=None):
        calls.append(args)
        return None if args[0] == "api" else view["value"]

    def gh_text(args, max_bytes=None):
        calls.append(args)
        return diff["value"]

    monkeypatch.setattr(prstatus, "gh_json", gh_json)
    monkeypatch.setattr(prstatus, "gh_text", gh_text)
    return (lambda value: view.update(value=value),
            lambda value: diff.update(value=value), calls)


def test_fetch_asks_gh_three_times_and_parses(gh):
    set_view, set_diff, calls = gh
    set_view(_reply())
    set_diff(DIFF)
    detail = fetch(URL)
    assert detail.summary.number == 263
    assert detail.files[0].patch is not None
    assert calls[0][:3] == ["pr", "view", URL]
    assert calls[1][:2] == ["api", "graphql"]  # the review threads
    assert calls[2] == ["pr", "diff", URL]


def test_fetch_hands_threads_to_the_detail(monkeypatch):
    def gh_json(args, cwd=None, timeout=None):
        return _threads_reply([_thread_node()]) if args[0] == "api" else _reply()

    monkeypatch.setattr(prstatus, "gh_json", gh_json)
    monkeypatch.setattr(prstatus, "gh_text", lambda args, max_bytes=None: DIFF)
    detail = fetch(URL)
    assert [t.id for t in detail.threads] == ["PRRT_kwDOTjjqB85abc123"]
    assert any(isinstance(entry, PrThread) for entry in detail.timeline)


def test_fetch_asks_who_is_signed_in_and_hands_it_to_the_detail(monkeypatch):
    """The fourth call, and the only one whose answer outlives the load: the
    login is remembered for the run, so a second page doesn't ask again."""
    calls = []

    def gh_json(args, cwd=None, timeout=None):
        calls.append(args)
        if args[:2] == ["api", "user"]:
            return {"login": "ghackett"}
        return None if args[0] == "api" else _reply()

    monkeypatch.setattr(prstatus, "gh_json", gh_json)
    monkeypatch.setattr(prstatus, "gh_text", lambda args, max_bytes=None: DIFF)
    assert fetch(URL).viewer_is_author is True
    assert ["api", "user"] in calls
    calls.clear()
    assert fetch(URL).viewer_is_author is True
    assert ["api", "user"] not in calls


def test_fetch_survives_not_knowing_who_is_signed_in(gh):
    """The gh fixture answers nothing to every `api` call, this one included:
    the load lands whole, the PR simply reads as somebody else's."""
    set_view, _set_diff, _calls = gh
    set_view(_reply())
    detail = fetch(URL)
    assert detail is not None
    assert detail.viewer_is_author is False


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


def test_fetch_runs_every_call_on_the_action_budget(monkeypatch):
    """All the way down to subprocess.run: a load someone is waiting on gets
    the action timeout for the heavy view reply, the thread query and the
    diff alike, not the poll's short one. The who-am-I call rides along on the
    poll budget — it is one line of reply, and a load survives losing it."""
    seen = []

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        stdout = "" if "diff" in argv else json.dumps(_reply())
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

    monkeypatch.setattr(prstatus.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(prstatus.subprocess, "run", run)
    assert fetch(URL) is not None
    assert [kwargs["timeout"] for _argv, kwargs in seen][:3] \
        == [prstatus._GH_ACTION_TIMEOUT_S] * 3
