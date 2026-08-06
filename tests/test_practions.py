"""Tests for practions — which actions a pull request offers in the state it
is in, and the `gh` commands the ones that talk to GitHub turn into."""

import pytest

from collins import practions
from collins.practions import (
    AUTO_MERGE,
    CI_PROMPT,
    COMMENTS,
    FIX_CI,
    MERGE,
    NEW_PR,
    NEW_PR_PROMPT,
    READY,
    REBASE,
    REBASE_PROMPT,
    REVIEW,
    REVIEW_COMMENT,
    actions_for,
    checks_green,
    merge_method,
    perform,
)
from collins.prstatus import PullRequest

URL = "https://github.com/episode6/collins/pull/55"


def _pr(**overrides) -> PullRequest:
    fields = {
        "number": 55,
        "url": URL,
        "repository": "episode6/collins",
        "title": "Give a PR its actions",
        "state": "OPEN",
        "passed": 3,
        "failed": 0,
        "pending": 0,
    }
    fields.update(overrides)
    return PullRequest(**fields)


# What a session that can't be typed into says for itself (see
# window._session_prompt_block); any non-empty sentence blocks the same way.
BLOCKED = "This session has no tab open."


def _actions(pr, takes_prompt=False, has_changes=False) -> list:
    return actions_for(pr, "" if takes_prompt else BLOCKED, lambda: has_changes)


def _keys(pr, takes_prompt=False, has_changes=False) -> list[str]:
    """Every action the menu shows, pressable or not."""
    return [action.key for action in _actions(pr, takes_prompt, has_changes)]


def _live_keys(pr, takes_prompt=False, has_changes=False) -> list[str]:
    """Only the ones that can be picked right now."""
    return [a.key for a in _actions(pr, takes_prompt, has_changes) if not a.blocked]


# -- what a PR offers -------------------------------------------------------


def test_a_draft_is_offered_the_way_out_of_draft():
    assert _keys(_pr(state="DRAFT")) == [READY, REVIEW]


def test_a_green_pr_is_offered_the_merge():
    assert _keys(_pr()) == [MERGE, REVIEW]


def test_a_pending_pr_is_offered_auto_merge_instead():
    """Merging now would be merging before the checks have spoken, so the offer
    is the one that waits for them."""
    assert _keys(_pr(passed=1, pending=2)) == [AUTO_MERGE, REVIEW]


def test_a_failing_pr_is_offered_auto_merge_too():
    assert AUTO_MERGE in _keys(_pr(passed=1, failed=1))


def test_a_pr_with_no_checks_at_all_counts_as_green():
    """A repository with nothing configured reports zeroes; there is nothing to
    wait for, so waiting would mean an auto-merge that never fires."""
    assert MERGE in _keys(_pr(passed=0, failed=0, pending=0))


def test_an_unfetched_pr_offers_nothing():
    """No gh, no network: better an empty menu than a Merge that was never
    going to work — the PR's page is a plain click away either way."""
    assert _keys(_pr(state=None, passed=None, failed=None, pending=None)) == []


def test_a_merged_pr_over_a_clean_tree_offers_nothing():
    """The work landed and nothing has happened since: the session is done."""
    assert _keys(_pr(state="MERGED"), takes_prompt=True) == []


def test_a_merged_pr_over_a_dirty_tree_is_offered_the_next_one():
    """The PR landed, the tree has moved on: that work wants a pull request of
    its own, and the agent is the one who opens it."""
    assert _keys(_pr(state="MERGED"), takes_prompt=True, has_changes=True) == [NEW_PR]


def test_opening_the_next_pr_needs_a_session_at_its_prompt():
    """It sends a prompt, like addressing CI does, so a session that is closed
    or mid-sentence is nowhere to send one — the offer stays in the menu and
    says so, rather than disappearing out of it."""
    actions = _actions(_pr(state="MERGED"), takes_prompt=False, has_changes=True)
    assert [a.key for a in actions] == [NEW_PR]
    assert actions[0].blocked == BLOCKED


def test_an_open_pr_is_never_offered_a_new_one():
    """Changes in the tree while a PR is still open belong on that PR."""
    assert NEW_PR not in _keys(_pr(), takes_prompt=True, has_changes=True)
    assert NEW_PR not in _keys(_pr(state="DRAFT"), takes_prompt=True, has_changes=True)


def test_a_closed_pr_offers_nothing():
    """Closed, not merged: whatever it was for was abandoned, so there is no
    "the next one" to offer."""
    assert _keys(_pr(state="CLOSED"), takes_prompt=True, has_changes=True) == []


def test_a_conflicting_pr_trades_its_merge_for_a_rebase():
    """Merging now would only relay GitHub's refusal, and auto-merge can't be
    enabled on a branch GitHub can't merge — resolving is what makes either
    offerable, so the rebase action stands where the merge would have."""
    keys = _keys(_pr(mergeable="CONFLICTING"), takes_prompt=True)
    assert MERGE not in keys
    assert AUTO_MERGE not in keys
    assert keys[0] == REBASE


def test_rebasing_needs_a_session_at_its_prompt():
    """It sends a prompt; a conflicted PR whose session is closed still shows
    the rebase — greyed out, saying why — and never the merge it stands in
    for, which GitHub would refuse whoever is at the terminal."""
    actions = _actions(_pr(mergeable="CONFLICTING"), takes_prompt=False)
    keys = [a.key for a in actions]
    assert REBASE in keys
    assert next(a for a in actions if a.key == REBASE).blocked == BLOCKED
    assert MERGE not in keys
    assert REBASE not in _live_keys(_pr(mergeable="CONFLICTING"), takes_prompt=False)


def test_a_conflicted_draft_still_offers_ready_before_the_rebase():
    keys = _keys(_pr(state="DRAFT", mergeable="CONFLICTING"), takes_prompt=True)
    assert keys[:2] == [READY, REBASE]


def test_rebase_is_only_offered_to_a_conflicted_live_pr():
    assert REBASE not in _keys(_pr(), takes_prompt=True)
    merged = _pr(state="MERGED", mergeable="CONFLICTING")
    assert REBASE not in _keys(merged, takes_prompt=True, has_changes=True)


def test_the_rebase_prompt_names_the_pr():
    actions = actions_for(_pr(mergeable="CONFLICTING"))
    action = next(a for a in actions if a.key == REBASE)
    assert action.prompt == "rebase PR #55 and resolve the conflicts"


def test_the_working_tree_is_only_consulted_when_it_could_matter():
    """It costs a `git status`, and only a merged PR's menu can use the answer;
    every other state must reach its menu without asking."""
    asked = []

    def has_changes():
        asked.append(True)
        return True

    actions_for(_pr(), "", has_changes)
    actions_for(_pr(state="DRAFT"), "", has_changes)
    actions_for(_pr(state="CLOSED"), "", has_changes)
    assert asked == []
    actions_for(_pr(state="MERGED"), "", has_changes)
    assert asked == [True]


def test_a_merged_pr_asks_about_the_tree_even_with_nowhere_to_send_a_prompt():
    """The row it decides on is shown either way now — greyed out when the
    session can't take a prompt — so the question can't wait on the session.
    A session with no tab at all answers "no changes" without a subprocess
    (see window._session_has_changes), so this stays cheap where it matters."""
    asked = []

    def has_changes():
        asked.append(True)
        return True

    actions_for(_pr(state="MERGED"), BLOCKED, has_changes)
    assert asked == [True]


def test_addressing_ci_is_offered_to_a_failing_pr_and_to_nothing_else():
    """A PR that is passing has nothing to say; a failing one always does, and
    whether its session can be typed into decides only whether the row can be
    picked."""
    failing = _pr(passed=1, failed=2)
    assert FIX_CI in _live_keys(failing, takes_prompt=True)
    assert FIX_CI in _keys(failing, takes_prompt=False)
    assert FIX_CI not in _live_keys(failing, takes_prompt=False)
    assert FIX_CI not in _keys(_pr(), takes_prompt=True)


def test_unresolved_comments_offer_the_agent_a_reply():
    commented = _pr(unresolved=True)
    assert COMMENTS in _live_keys(commented, takes_prompt=True)
    assert COMMENTS not in _keys(_pr(), takes_prompt=True)


def test_the_reply_is_offered_even_where_it_cannot_be_sent_yet():
    """The badge that sends someone to this menu is on the PR, not on the
    session, so the menu that opens under it always has the row the badge is
    about — greyed out, carrying the reason, when there is nowhere to send it.
    An offer that comes and goes with what a terminal is showing is one nobody
    can find twice."""
    actions = _actions(_pr(unresolved=True), takes_prompt=False)
    comments = next(a for a in actions if a.key == COMMENTS)
    assert comments.blocked == BLOCKED
    assert comments.prompt  # still knows what it would send, once it can


def test_the_comments_prompt_names_the_pr():
    actions = actions_for(_pr(unresolved=True))
    action = next(a for a in actions if a.key == COMMENTS)
    assert action.prompt == "Address unresolved comments on PR #55"


def test_addressing_comments_does_not_wait_on_ci():
    """The badge holds the triangle back until checks pass; the menu doesn't —
    answering a reviewer and fixing a red build are separate errands."""
    keys = _keys(_pr(passed=1, failed=1, unresolved=True), takes_prompt=True)
    assert FIX_CI in keys
    assert COMMENTS in keys


@pytest.mark.parametrize("state", ["MERGED", "CLOSED", None])
def test_only_a_live_pr_offers_the_reply(state):
    pr = _pr(state=state, unresolved=True)
    assert COMMENTS not in _keys(pr, takes_prompt=True, has_changes=True)


def test_only_merging_asks_first():
    """The two irreversible, everybody-can-see-it actions confirm; a comment
    and a prompt sent to a session don't."""
    asks = {a.key: a.confirm is not None for a in actions_for(_pr(passed=1, failed=1))}
    assert asks == {AUTO_MERGE: True, REVIEW: False, FIX_CI: False}
    assert {a.key: a.confirm is not None for a in actions_for(_pr())}[MERGE]


def test_the_prompt_actions_are_the_ones_carrying_a_prompt():
    """What the menu dispatches on: an action with text to type goes to the
    session, and everything else goes to gh."""
    sending = {
        a.key: a.prompt
        for a in actions_for(_pr(passed=1, failed=1))
        + actions_for(_pr(state="MERGED"), "", lambda: True)
        if a.prompt
    }
    assert sending == {FIX_CI: "Address the ci error(s) on PR #55", NEW_PR: NEW_PR_PROMPT}


def test_only_the_prompt_actions_are_ever_blocked():
    """gh runs the rest from here whatever the session is doing: merging a PR
    and commenting on one need a token, not a terminal."""
    actions = _actions(_pr(state="DRAFT", unresolved=True), takes_prompt=False)
    assert {a.key for a in actions if a.blocked} == {COMMENTS}
    assert {a.key for a in actions if not a.blocked} == {READY, REVIEW}


def test_every_action_names_the_pr_in_its_tooltip():
    for action in actions_for(_pr(passed=1, failed=1)):
        assert action.label and action.tooltip


def test_checks_green_is_unknown_status_pessimistic():
    assert checks_green(_pr()) is True
    assert checks_green(_pr(pending=1)) is False
    assert checks_green(_pr(passed=None, failed=None, pending=None)) is False


# -- what they run ----------------------------------------------------------


@pytest.fixture
def gh(monkeypatch):
    """Stub gh_run; returns (calls, setter for what gh comes back with)."""
    calls: list[list[str]] = []
    reply: list[tuple[bool, str]] = [(True, "")]

    def fake(args):
        calls.append(args)
        return reply[0]

    monkeypatch.setattr(practions, "gh_run", fake)
    monkeypatch.setattr(practions, "gh_json", lambda args, cwd=None: None)
    return calls, (lambda value: reply.__setitem__(0, value))


def test_ready_takes_the_pr_out_of_draft(gh):
    calls, _serve = gh
    assert perform(READY, _pr(state="DRAFT")) is None
    assert calls == [["pr", "ready", URL]]


def test_merge_names_a_method_and_auto_merge_adds_the_flag(gh):
    """gh refuses to pick a merge method off a terminal, so one is always
    named; auto-merge is the same command with --auto on the end."""
    calls, _serve = gh
    perform(MERGE, _pr())
    perform(AUTO_MERGE, _pr(pending=1))
    assert calls[0] == ["pr", "merge", URL, "--squash"]
    assert calls[1] == ["pr", "merge", URL, "--squash", "--auto"]


def test_review_asks_for_one_in_a_comment(gh):
    calls, _serve = gh
    perform(REVIEW, _pr())
    assert calls == [["pr", "comment", URL, "--body", REVIEW_COMMENT]]
    assert "@claude" in REVIEW_COMMENT


def test_a_failure_comes_back_as_the_reason(gh):
    _calls, serve = gh
    serve((False, "Pull request is not mergeable"))
    assert perform(MERGE, _pr()) == "Pull request is not mergeable"


def test_a_url_that_isnt_a_pr_never_reaches_gh(gh):
    """PR URLs come out of transcripts, i.e. out of repository content: one
    that doesn't look like a PR page is refused rather than put in an argv."""
    calls, _serve = gh
    assert perform(MERGE, _pr(url="--version")) is not None
    assert perform(READY, _pr(url="https://github.com/o/r/issues/9")) is not None
    assert calls == []


def test_the_prompts_sent_to_a_session_are_left_in_english():
    """They are read by the agent CLI, not by a person."""
    assert CI_PROMPT == "Address the ci error(s) on PR #{number}"
    assert REBASE_PROMPT == "rebase PR #{number} and resolve the conflicts"
    assert NEW_PR_PROMPT == "Open a pull request for your changes"


# -- which merge method ------------------------------------------------------


def _repo_reply(monkeypatch, reply):
    seen: list[list[str]] = []

    def fake(args, cwd=None):
        seen.append(args)
        return reply

    monkeypatch.setattr(practions, "gh_json", fake)
    return seen


def test_the_merge_method_is_the_first_the_repository_allows(monkeypatch):
    _repo_reply(monkeypatch, {"squashMergeAllowed": False, "mergeCommitAllowed": True})
    assert merge_method("episode6/collins") == "--merge"


def test_squash_wins_where_it_is_allowed(monkeypatch):
    seen = _repo_reply(
        monkeypatch, {"squashMergeAllowed": True, "mergeCommitAllowed": True, "rebaseMergeAllowed": True}
    )
    assert merge_method("episode6/collins") == "--squash"
    assert seen[0][:3] == ["repo", "view", "episode6/collins"]


def test_an_unanswered_repository_falls_back_to_squash(monkeypatch):
    """No gh, no network, or a reply in a shape we don't know: GitHub's own
    default for a new repository is the best guess left."""
    _repo_reply(monkeypatch, None)
    assert merge_method("episode6/collins") == "--squash"
