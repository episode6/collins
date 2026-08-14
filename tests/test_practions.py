"""Tests for practions — which actions a pull request offers in the state it
is in, and the `gh` commands the ones that talk to GitHub turn into."""

import pytest

from collins import practions
from collins.practions import (
    AUTO_MERGE,
    CI_PROMPT,
    CLOSE,
    COMMENTS,
    FIX_ALL,
    FIX_ALL_PROMPT,
    FIX_CI,
    MERGE,
    MERGE_ARCHIVE,
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


def test_claude_having_the_last_word_takes_the_review_offer_away():
    """It has been asked and it has answered; a second `@claude review` posted
    under its own review is the menu repeating itself."""
    assert REVIEW not in _keys(_pr(unresolved=True, claude_replied=True))
    assert REVIEW not in _keys(_pr(state="DRAFT", claude_replied=True))


def test_the_review_offer_comes_back_once_someone_answers_claude():
    """Asking for another pass after a round of changes is exactly this: the
    newest comment is no longer Claude's, so the offer is there again."""
    assert REVIEW in _keys(_pr(unresolved=True))
    assert REVIEW in _keys(_pr())


def test_a_commit_after_claudes_review_puts_the_offer_back():
    """Nobody has to answer in words: the review is of code that is no longer
    on the branch, which is the round of changes worth another pass."""
    assert REVIEW in _keys(_pr(claude_replied=True, pushed_since=True))
    assert REVIEW in _keys(_pr(state="DRAFT", claude_replied=True, pushed_since=True))


def test_a_commit_nobody_has_reviewed_yet_changes_nothing():
    """A push with no review under it was already being offered one."""
    assert REVIEW in _keys(_pr(pushed_since=True))


def test_claude_answering_leaves_the_rest_of_the_menu_alone():
    """Only the review offer reads the newest comment's author — the merge, the
    CI errand and the reply are about the PR's state, which hasn't moved."""
    keys = _keys(_pr(passed=1, failed=1, unresolved=True, claude_replied=True),
                 takes_prompt=True)
    assert keys == [AUTO_MERGE, FIX_CI, COMMENTS]


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


# -- what the page's own buttons offer ---------------------------------------


def _header(pr) -> list[str]:
    return [action.key for action in practions.header_actions(pr)]


def test_a_draft_is_offered_its_way_out_and_nothing_else():
    """Merging a draft is a thing GitHub refuses, auto-merge included."""
    assert _header(_pr(state="DRAFT")) == [READY]


def test_a_pending_pr_is_offered_auto_merge_and_not_the_merge_itself():
    """One merge at a time: while the checks are still running, the offer is
    the one that waits for them — never both, to be chosen between."""
    assert _header(_pr(passed=1, pending=2)) == [AUTO_MERGE]
    assert _header(_pr(passed=1, failed=1)) == [AUTO_MERGE]


def test_a_green_pr_has_nothing_left_to_wait_for():
    assert _header(_pr()) == [MERGE]


def test_nothing_is_offered_where_the_menu_offers_no_merge_either():
    """A conflicting PR, a settled one, and one nothing has been fetched for:
    the bar goes away rather than showing buttons GitHub would refuse."""
    assert _header(_pr(mergeable="CONFLICTING")) == []
    assert _header(_pr(state="MERGED")) == []
    assert _header(_pr(state="CLOSED")) == []
    assert _header(_pr(state=None, passed=None, failed=None, pending=None)) == []


def test_the_page_shows_one_button_and_it_is_the_menu_s_own_answer():
    """The page never offers a choice the menu doesn't: whatever it shows is
    `actions_for`'s first item, so the two can't recommend different things
    about the same PR."""
    for pr in (_pr(state="DRAFT"), _pr(), _pr(passed=1, pending=2), _pr(passed=1, failed=1)):
        assert _header(pr) == _keys(pr)[:1]


def test_the_page_button_has_a_word_to_wear():
    """It shares a line with the view switcher, so it carries a short label
    for the button and keeps the full sentence for its tooltip."""
    for pr in (_pr(state="DRAFT"), _pr(), _pr(passed=1, pending=2)):
        for action in practions.header_actions(pr):
            assert action.short and len(action.short) <= len(action.label)
            assert action.tooltip


def test_the_merge_still_asks_first_whichever_one_it_is():
    for pr in (_pr(pending=1), _pr()):
        (action,) = practions.header_actions(pr)
        assert action.confirm is not None


def test_merging_past_unfinished_checks_says_so_when_it_asks():
    """The green wording would be a lie on a PR whose checks haven't spoken —
    and this is the press that most needs the question to be accurate."""
    green = practions.merge_action(_pr(), auto=False).confirm.body
    pending = practions.merge_action(_pr(pending=2), auto=False).confirm.body
    assert "have passed" in green
    assert green != pending
    assert "haven't all passed" in pending


# -- what that button keeps behind it ----------------------------------------


def _alternates(pr, can_archive=True) -> list[str]:
    return [action.key for action in practions.alternate_actions(pr, can_archive)]


def test_the_button_hides_the_two_courses_it_isnt_recommending():
    """A right-click on the merge offers the ways of not merging it: landing
    it and being done with the session, or closing it unmerged."""
    assert _alternates(_pr()) == [MERGE_ARCHIVE, CLOSE]


def test_closing_is_offered_until_there_is_nothing_left_to_close():
    """Every state but merged and closed — and not on a PR nothing has been
    fetched for, where a Close was never going to work either."""
    assert CLOSE in _alternates(_pr())
    assert CLOSE in _alternates(_pr(state="DRAFT"))
    assert CLOSE in _alternates(_pr(passed=1, failed=1))
    assert _alternates(_pr(state="MERGED")) == []
    assert _alternates(_pr(state="CLOSED")) == []
    assert _alternates(_pr(state=None, passed=None, failed=None, pending=None)) == []


def test_a_pr_with_no_button_at_all_can_still_be_closed():
    """A conflicting PR is offered no merge anywhere (see `_header`), which is
    the state closing it is most often the answer to."""
    conflicting = _pr(mergeable="CONFLICTING")
    assert _header(conflicting) == []
    assert _alternates(conflicting) == [CLOSE]


def test_merging_and_archiving_only_ever_stands_beside_the_merge_itself():
    """Never beside auto-merge: GitHub lands that one later and on its own, so
    a session archived now would be archived on a promise. Never on a draft,
    a settled PR, or a conflicting one, which offer no merge at all."""
    assert MERGE_ARCHIVE not in _alternates(_pr(passed=1, pending=2))
    assert MERGE_ARCHIVE not in _alternates(_pr(passed=1, failed=1))
    assert MERGE_ARCHIVE not in _alternates(_pr(state="DRAFT"))
    assert MERGE_ARCHIVE not in _alternates(_pr(mergeable="CONFLICTING"))


def test_archiving_is_only_offered_where_a_session_is_there_to_archive():
    """The caller answers that: the PR page is docked inside the session it
    would put away, and nothing else showing these actions is."""
    assert _alternates(_pr(), can_archive=False) == [CLOSE]


def test_both_alternates_ask_first_and_only_one_asks_in_red():
    """Merging is final but isn't a loss; closing unmerged is the one action
    here that throws the work away."""
    merge_archive, close = practions.alternate_actions(_pr(), True)
    assert merge_archive.confirm is not None and not merge_archive.confirm.destructive
    assert close.confirm is not None and close.confirm.destructive


def test_merging_and_archiving_asks_about_the_checks_as_the_merge_does():
    """It is the same merge, so it inherits the same wording about where the
    checks got to — with what it does afterwards said as well."""
    question = practions.merge_archive_action(_pr()).confirm
    assert practions.merge_action(_pr(), auto=False).confirm.body in question.body
    assert "archived" in question.body


def test_every_alternate_names_the_pr_and_wears_a_word():
    for action in practions.alternate_actions(_pr(), True):
        assert action.label and action.short
        assert "#55" in action.tooltip


# -- what the Checks list offers under it ------------------------------------


def _repair(pr, takes_prompt=True):
    return practions.repair_action(pr, "" if takes_prompt else BLOCKED)


def test_failing_checks_ask_to_be_fixed():
    action = _repair(_pr(passed=1, failed=2))
    assert action.key == FIX_CI
    assert action.prompt == "Address the ci error(s) on PR #55"


def test_a_conflicting_branch_asks_to_be_rebased():
    """Green checks and all: the page lists the conflict as a failed check of
    its own, and a red row with nothing offered under it is the page pointing
    at a problem and shrugging."""
    action = _repair(_pr(mergeable="CONFLICTING"))
    assert action.key == REBASE
    assert action.prompt == "rebase PR #55 and resolve the conflicts"


def test_both_blockers_at_once_are_one_prompt_in_the_right_order():
    """Two buttons would leave the order to the user, and fixing CI after the
    rebase asks for the same work twice."""
    action = _repair(_pr(passed=1, failed=1, mergeable="CONFLICTING"))
    assert action.key == FIX_ALL
    assert action.prompt == FIX_ALL_PROMPT.format(number=55)
    assert action.prompt.index("ci error") < action.prompt.index("rebase")


def test_nothing_is_offered_where_nothing_blocks_the_merge():
    assert _repair(_pr()) is None
    assert _repair(_pr(passed=1, pending=2)) is None
    assert _repair(_pr(passed=None, failed=None, pending=None)) is None


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_a_settled_pr_has_nothing_left_to_repair(state):
    """What CI said on the way into a merged PR is history, and there is no
    branch left to rebase."""
    assert _repair(_pr(state=state, failed=2, mergeable="CONFLICTING")) is None


def test_a_conflicted_draft_is_still_asked_to_resolve():
    assert _repair(_pr(state="DRAFT", mergeable="CONFLICTING")).key == REBASE


def test_repairing_needs_a_session_at_its_prompt():
    """The offer stays put and comes back blocked, the menu rows' treatment:
    an offer that vanishes with what a terminal is showing is one nobody can
    find twice."""
    for pr in (_pr(failed=1), _pr(mergeable="CONFLICTING"),
               _pr(failed=1, mergeable="CONFLICTING")):
        assert _repair(pr, takes_prompt=False).blocked == BLOCKED
        assert _repair(pr, takes_prompt=True).blocked == ""


def test_every_repair_has_a_button_s_worth_of_label_and_a_full_tooltip():
    for pr in (_pr(failed=1), _pr(mergeable="CONFLICTING"),
               _pr(failed=1, mergeable="CONFLICTING")):
        action = _repair(pr)
        assert (action.short or action.label)
        assert action.prompt in action.tooltip


def test_the_repair_wording_is_the_three_the_page_promises():
    assert _repair(_pr(failed=1)).short == "Fix errors"
    assert _repair(_pr(mergeable="CONFLICTING")).short == "Resolve conflicts"
    both = _repair(_pr(failed=1, mergeable="CONFLICTING"))
    assert (both.short or both.label) == "Fix errors & resolve conflicts"


def test_the_repair_offer_agrees_with_the_menu_on_what_to_send():
    """Same PR, same work: the button under the checks and the menu row for
    one blocker send the very same prompt."""
    failing, conflicted = _pr(failed=1), _pr(mergeable="CONFLICTING")
    assert _repair(failing).prompt == next(
        a.prompt for a in actions_for(failing) if a.key == FIX_CI
    )
    assert _repair(conflicted).prompt == next(
        a.prompt for a in actions_for(conflicted) if a.key == REBASE
    )


def test_the_combined_prompt_is_never_in_the_menu():
    """The menu has room for both rows; only the page's single button folds
    them together."""
    both = _pr(failed=1, mergeable="CONFLICTING")
    keys = _keys(both, takes_prompt=True)
    assert FIX_ALL not in keys
    assert {REBASE, FIX_CI} <= set(keys)


def test_checks_green_is_unknown_status_pessimistic():
    assert checks_green(_pr()) is True
    assert checks_green(_pr(pending=1)) is False
    assert checks_green(_pr(passed=None, failed=None, pending=None)) is False


# -- what they run ----------------------------------------------------------


class _Calls(list):
    """The argv lists gh_run got, in order; `.stdins` is what each was fed."""

    def __init__(self) -> None:
        super().__init__()
        self.stdins: list[str | None] = []


@pytest.fixture
def gh(monkeypatch):
    """Stub gh_run; returns (calls, setter for what gh comes back with)."""
    calls = _Calls()
    reply: list[tuple[bool, str]] = [(True, "")]

    def fake(args, stdin=None):
        calls.append(args)
        calls.stdins.append(stdin)
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


def test_merge_and_archive_is_the_plain_merge_as_far_as_gh_is_concerned(gh):
    """The archive half never reaches a subprocess: it is the app's own, and
    it happens only once this has come back without an error."""
    calls, _serve = gh
    assert perform(MERGE_ARCHIVE, _pr()) is None
    assert calls == [["pr", "merge", URL, "--squash"]]


def test_a_refused_merge_takes_the_archive_down_with_it(gh):
    """What the page waits on: a merge GitHub wouldn't do leaves the session
    exactly where it was."""
    _calls, serve = gh
    serve((False, "Pull request is not mergeable"))
    assert perform(MERGE_ARCHIVE, _pr()) == "Pull request is not mergeable"


def test_closing_closes_without_touching_the_branch(gh):
    calls, _serve = gh
    assert perform(CLOSE, _pr()) is None
    assert calls == [["pr", "close", URL]]


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


def test_a_comment_body_travels_by_stdin(gh):
    """The composer's text reaches gh as its input, never as an argv entry."""
    calls, _serve = gh
    assert practions.comment(_pr(), "Looks right to **me**.") is None
    assert calls == [["pr", "comment", URL, "--body-file", "-"]]
    assert calls.stdins == ["Looks right to **me**."]


def test_an_approval_without_words_is_the_flag_alone(gh):
    calls, _serve = gh
    assert practions.review(_pr(), practions.APPROVE) is None
    assert calls == [["pr", "review", URL, "--approve"]]
    assert calls.stdins == [None]


def test_a_review_body_travels_by_stdin_too(gh):
    calls, _serve = gh
    assert practions.review(_pr(), practions.REQUEST_CHANGES, "Not this way.") is None
    assert calls == [["pr", "review", URL, "--request-changes", "--body-file", "-"]]
    assert calls.stdins == ["Not this way."]


def test_a_verdict_practions_never_named_is_refused(gh):
    calls, _serve = gh
    assert practions.review(_pr(), "--approve") is not None
    assert calls == []


def test_the_write_calls_refuse_a_non_pr_url_as_perform_does(gh):
    calls, _serve = gh
    assert practions.comment(_pr(url="--version"), "hi") is not None
    assert practions.review(_pr(url="https://github.com/o/r/issues/9"), practions.APPROVE) is not None
    assert calls == []


def test_a_failed_post_comes_back_as_the_reason(gh):
    _calls, serve = gh
    serve((False, "Can not approve your own pull request"))
    assert practions.review(_pr(), practions.APPROVE) == "Can not approve your own pull request"


# -- the review-thread mutations ---------------------------------------------

THREAD = "PRRT_kwDOTjjqB85abc123"


def test_a_thread_reply_body_travels_by_stdin(gh):
    """The variable rides ``-F body=@-`` — gh reading it off stdin — so free
    text never becomes an argv entry, exactly as a comment's doesn't."""
    calls, _serve = gh
    assert practions.reply_in_thread(_pr(), THREAD, "Fixed in the next push.") is None
    (args,) = calls
    assert args[:2] == ["api", "graphql"]
    assert f"threadId={THREAD}" in args
    assert args[args.index("body=@-") - 1] == "-F"
    assert calls.stdins == ["Fixed in the next push."]
    query = next(arg for arg in args if arg.startswith("query="))
    assert "addPullRequestReviewThreadReply" in query


def test_resolving_and_unresolving_pick_their_mutations(gh):
    calls, _serve = gh
    assert practions.set_thread_resolved(_pr(), THREAD, True) is None
    assert practions.set_thread_resolved(_pr(), THREAD, False) is None
    first = next(arg for arg in calls[0] if arg.startswith("query="))
    second = next(arg for arg in calls[1] if arg.startswith("query="))
    assert "resolveReviewThread" in first and "unresolveReviewThread" not in first
    assert "unresolveReviewThread" in second
    assert f"threadId={THREAD}" in calls[0]
    assert calls.stdins == [None, None]


def test_a_thread_id_that_isnt_one_never_reaches_gh(gh):
    """Thread ids came out of a GraphQL reply; only what still looks like a
    node id may go back out into an argv entry (prdetail.THREAD_ID)."""
    calls, _serve = gh
    assert practions.reply_in_thread(_pr(), "not an id!", "hi") is not None
    assert practions.set_thread_resolved(_pr(), "two words", True) is not None
    assert practions.set_thread_resolved(_pr(), "", True) is not None
    assert calls == []


def test_the_thread_calls_refuse_a_non_pr_url_too(gh):
    calls, _serve = gh
    assert practions.reply_in_thread(_pr(url="--version"), THREAD, "hi") is not None
    assert practions.set_thread_resolved(_pr(url="--version"), THREAD, True) is not None
    assert calls == []


def test_a_failed_mutation_comes_back_as_the_reason(gh):
    _calls, serve = gh
    serve((False, "Resource not accessible by integration"))
    assert practions.set_thread_resolved(_pr(), THREAD, True) \
        == "Resource not accessible by integration"


def test_the_prompts_sent_to_a_session_are_left_in_english():
    """They are read by the agent CLI, not by a person."""
    assert CI_PROMPT == "Address the ci error(s) on PR #{number}"
    assert REBASE_PROMPT == "rebase PR #{number} and resolve the conflicts"
    assert FIX_ALL_PROMPT == (
        "Address the ci error(s) on PR #{number}, then rebase it and resolve "
        "the conflicts"
    )
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
