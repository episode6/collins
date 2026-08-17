# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What Collins can *do* to a pull request, and the `gh` calls that do it.

prstatus reads a PR; this decides what is worth offering to change about one
and carries it out. Both halves live here rather than beside the widgets so
they stay testable without a Gtk namespace (CI installs PyGObject but no GTK),
and so the footer's chips and the sidebar's menu can't drift apart on what a
PR offers.

Which actions a PR offers is a question about its state, and the answer is
deliberately narrow: a draft is asked to come out of draft, an open PR is
merged now or told to merge itself when its checks go green (and one already
told to is offered the way back out of it), and anything the
repository's own Claude workflow can be asked for goes through a comment
(`@claude review`) rather than an API Collins would have to hold a token for —
and only while Claude isn't the one who commented last on code that hasn't
moved since, as a review it has already given isn't one to ask for again.

The same answers dress three more surfaces, all on the native PR page.
`header_actions` is the handful of them that change the pull request itself,
which the page draws as buttons on its view switcher's row rather than burying
a merge two clicks deep. `repair_action` is the other one: whatever single
prompt would clear what is blocking the merge, drawn under the page's Checks
list where those blockers are enumerated. Both speak in the actions' `short`
wording, which is all a button has room to say.

`alternate_actions` is the third: what a right-click on that button offers
*instead* of what it says. The button is the one course Collins recommends,
and the two things it deliberately doesn't recommend live behind it — closing
a pull request rather than landing it, and landing one whose session is then
done with (MERGE_ARCHIVE, the merge plus the archive of the session that
opened it, which is the app's own business and happens only once the merge
really lands). Both are one press away from the button that already means
"finish with this PR", and neither belongs on the row itself: a Close beside a
Merge is an accident waiting for a stray click.

Five of them aren't about GitHub at all: FIX_CI, REBASE, FIX_ALL, COMMENTS and
NEW_PR send a prompt to the session that opened the PR and let the agent do the
work.
All need a session sitting at an empty prompt, and NEW_PR needs uncommitted
work to open a pull request *for* — neither is a property of the PR, so the
caller answers both.
A session that can't take a prompt right now doesn't take the action away with
it, though: it comes back `blocked`, for the menu to show greyed out with the
reason on it. What a PR offers is a question about the PR, and a badge saying
"someone is waiting on a reply" over a menu with nothing about comments in it
is the app disagreeing with itself.
Note that nothing here opens the PR's page: the menu carries its own "Open on
GitHub" row ahead of these actions, built beside the widgets (prmenu) because
opening a browser is Gtk's business and this module stays importable without
one — and a right-click on the chip or the list row goes straight there too.

Every call out is `gh`, off the main thread, and reports back as "worked" or a
sentence explaining why not (see prstatus.gh_run) — nothing here raises at the
UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .i18n import _
from .prdetail import THREAD_ID
from .prstatus import PullRequest, gh_json, gh_run, repository_for

# The actions themselves. Menu order is the order they are built in.
READY = "ready"
MERGE = "merge"
AUTO_MERGE = "auto-merge"
# Call auto-merge off again. Stands exactly where the merge offer would have
# been — a PR GitHub is already holding has no second merge to ask for, so the
# one thing left to say about landing it is "don't".
DISABLE_AUTO_MERGE = "disable-auto-merge"
# The merge again, with the session that opened the PR archived behind it.
# Never in the PR list's menu and never a button: only the page's alternates
# (`alternate_actions`), since archiving is about a session and that menu is
# opened from the session the page is docked in. `perform` merges and stops
# there — the archive is the app's, and waits on this having worked.
MERGE_ARCHIVE = "merge-archive"
# Close without merging. Alternates only, for the same reason: it is the one
# thing a PR offers that undoes the work rather than landing it.
CLOSE = "close"
REBASE = "rebase"
REVIEW = "review"
FIX_CI = "fix-ci"
COMMENTS = "comments"
NEW_PR = "new-pr"
# Both merge blockers at once, as one prompt. Never in the menu — which keeps
# offering the two separately, since a menu has room for both rows — only on
# the page's Checks list, where one button is the whole offer (`repair_action`).
FIX_ALL = "fix-all"

# What the prompt-sending actions type into the session. Read by the agent
# CLI, not by a person, so they stay in English (and untranslated) whatever the
# app's language is. Three of them name their PR: a session can have opened
# several, and a bare "the ci error(s)" would leave the agent to guess whose.
CI_PROMPT = "Address the ci error(s) on PR #{number}"
REBASE_PROMPT = "rebase PR #{number} and resolve the conflicts"
# Failures first, the rebase after: fixing CI is a commit on the branch, and
# asking for it on the far side of a rebase is asking for the same work twice.
FIX_ALL_PROMPT = (
    "Address the ci error(s) on PR #{number}, then rebase it and resolve the conflicts"
)
COMMENTS_PROMPT = "Address unresolved comments on PR #{number}"
NEW_PR_PROMPT = "Open a pull request for your changes"
# What asking for a review looks like on the PR: the mention the
# `anthropics/claude-code-action` workflow triggers on. A repository without
# that workflow gets a comment and nothing else, which is why the item says
# "ask" rather than promising a review.
REVIEW_COMMENT = "@claude review"

# gh won't pick a merge method for us off a terminal, and a repository that
# forbids the method we name refuses the merge — so the repository is asked
# what it allows, and the first of these it says yes to is used.
_MERGE_METHODS = (
    ("squashMergeAllowed", "--squash"),
    ("mergeCommitAllowed", "--merge"),
    ("rebaseMergeAllowed", "--rebase"),
)
_DEFAULT_MERGE_METHOD = "--squash"  # GitHub's own default for a new repository

# The states in which a PR is still something you can act on. Public because
# the composer (prview) asks it too: review verdicts are only offered while a
# PR is one of these — GitHub refuses an approval on a merged pull request.
LIVE = ("OPEN", "DRAFT")


@dataclass(frozen=True)
class Confirm:
    """The dialog an action puts up before it goes ahead.

    *destructive* dresses the confirming button as a warning rather than as
    the suggested course (see dialogs.confirm_dialog). Merging isn't a loss,
    however final it is; closing a pull request unmerged is the one action
    here that throws work away, so it is the one that asks in red.

    Carried on the action either way — whether it is actually put up is
    `confirmation`'s answer, since the merges' dialog can be turned off.
    """

    heading: str
    body: str
    label: str
    destructive: bool = False


@dataclass(frozen=True)
class Action:
    """One item in a PR's menu: what it says, and what picking it means.

    An action with a *prompt* is one the agent carries out rather than `gh`:
    picking it types that text into the session and sends it. Carrying the
    text on the action is what keeps the menu from having to know which of
    them is which (see prmenu._on_action_clicked).

    *blocked* is why picking it wouldn't work right now — an empty string when
    it would. The menu shows a blocked action as an unpressable row carrying
    that sentence, rather than leaving it out: an offer that comes and goes
    with what a terminal happens to be showing is one nobody can find twice.

    *short* is the same action in a word or two, for the `header_actions`
    buttons that sit on the PR page's switcher row: a menu row has a line to
    explain itself in, a button beside a view switcher has none to spare, and
    what the full label was saying is on the tooltip either way. Empty where
    the label is already short enough to be a button.
    """

    key: str
    label: str
    tooltip: str = ""
    confirm: Confirm | None = None
    prompt: str = ""
    blocked: str = ""
    short: str = ""


# The three ways of landing a pull request: now, when GitHub says the checks
# are done, and now-with-the-session-put-away. What the confirm_merges setting
# is about — see `confirmation` — and what wears the merge green, both on the
# button (prview's _ActionBar) and, through the class below, in the dialog it
# asks through.
MERGES = (MERGE, AUTO_MERGE, MERGE_ARCHIVE)
# The CSS class a merge's confirmation dialog wears, which paints its
# confirming button in the same GitHub green the button that opened it is drawn
# in (see app.py's _SCHEME_CSS). Named here, beside the keys that ask for it, so
# both askers — the chips' menu and the PR page — dress the question the same
# way.
MERGE_CONFIRM_CSS = "pr-merge-confirm"


def confirmation(action: Action, confirm_merges: bool = True) -> Confirm | None:
    """The dialog to put up before *action* runs, or None to just run it.

    What every surface offering an action asks, rather than reading
    `Action.confirm` itself: the merges ask by default and stop asking when
    the setting is turned off, and one answer to "does this one ask?" keeps
    the page's button and the menus' rows agreeing about it.

    Only the merges follow the setting. Closing a pull request unmerged is the
    one action here that ends it by throwing the work away rather than landing
    it, and a preference about merging isn't consent to that.
    """
    if action.key in MERGES and not confirm_merges:
        return None
    return action.confirm


def checks_green(pr: PullRequest) -> bool:
    """Whether *pr*'s checks have all run and all passed.

    What decides between merging now and asking GitHub to merge when the
    checks are done. A PR whose status hasn't been fetched at all is treated
    as not green: auto-merge is the safe half of that guess, since it waits
    for a verdict Collins doesn't have.

    A repository with no checks configured reports zeroes, which is green —
    there is nothing to wait for.
    """
    if pr.passed is None and pr.failed is None and pr.pending is None:
        return False
    return not pr.failed and not pr.pending


def actions_for(
    pr: PullRequest,
    prompt_block: str = "",
    has_changes: Callable[[], bool] = lambda: False,
) -> list[Action]:
    """The menu for *pr*: every action that makes sense for the state it is in.

    Two of the three arguments are about the session rather than the PR.
    *prompt_block* is why a prompt can't be sent to it right now — no tab open,
    or an input that isn't empty (see Provider.takes_prompt) — and the empty
    string when one can. It doesn't decide *whether* the prompt-sending actions
    are offered, only whether they are pressable: they are carried on the
    actions themselves as `blocked`, and the menu greys those rows out with the
    reason. *has_changes* is whether the session's working tree has uncommitted
    work in it, asked as a callable rather than a value because answering costs
    a `git status` (see gitinfo.has_changes) and only one PR state can use the
    answer.

    Everything here needs a state, and an unfetched PR (no gh, no network) has
    none: better an empty list than a "Merge" that was never going to work.
    Empty is fine for the menu — it puts its own "Open on GitHub" row ahead of
    whatever this returns, so even a PR with nothing left to do opens onto a
    menu that does something.
    """
    actions: list[Action] = []
    if pr.state == "DRAFT":
        actions.append(ready_action(pr))
    elif pr.state == "OPEN" and not pr.conflicting:
        # A conflicting PR gets no merge item at all: merging now would only
        # come back with GitHub's refusal, and auto-merge can't be enabled on
        # a branch GitHub can't merge. The rebase action below stands where
        # the merge would have — resolving is what makes merging offerable.
        actions.append(_landing_action(pr))
    if pr.state in LIVE and pr.conflicting:
        actions.append(rebase_action(pr, prompt_block))
    if pr.state in LIVE:
        if not pr.claude_had_the_last_word:
            # Not offered when Claude wrote the newest comment and nothing has
            # been pushed since: it has already been asked and has already
            # answered, so a second `@claude review` posted directly under its
            # own review is the one item in this menu that would visibly repeat
            # itself. A reply from anyone else — ours included — puts the offer
            # back, and so does a commit: a review of code that has since
            # changed is exactly the one worth asking for again.
            actions.append(review_action(pr))
        if pr.failed:
            actions.append(fix_ci_action(pr, prompt_block))
        if pr.awaiting_reply:
            # Offered whenever someone else has the last word, not only when
            # the chip badges it — answering a reviewer doesn't wait on CI.
            comments_prompt = COMMENTS_PROMPT.format(number=pr.number)
            actions.append(
                Action(
                    COMMENTS,
                    _("Address unresolved comments"),
                    _("Send “{prompt}” to this session").format(prompt=comments_prompt),
                    prompt=comments_prompt,
                    blocked=prompt_block,
                )
            )
    elif pr.merged and has_changes():
        # The PR landed and the tree has moved on since: the work in it is
        # what the next pull request is for, so the offer is to open that one.
        # Asked last, after the cheap condition, because it is the one that
        # costs a subprocess — and unlike the three above it stays a condition
        # rather than a blocked row: uncommitted work is a fact about the tree,
        # not about whether the session can be typed into, and there is nothing
        # to offer to open a pull request for without it.
        actions.append(
            Action(
                NEW_PR,
                # "Open *a* pull request", not "Open pull request": the latter
                # is already a msgid, and it is prstatus' name for the state a
                # PR is *in* (translated as an adjective — "Offener Pull
                # Request"). One msgid can't be both, and this one is a verb.
                _("Open a pull request"),
                _("Send “{prompt}” to this session").format(prompt=NEW_PR_PROMPT),
                prompt=NEW_PR_PROMPT,
                blocked=prompt_block,
            )
        )
    return actions


def header_actions(pr: PullRequest) -> list[Action]:
    """The state-changing actions the PR page puts beside its view switcher.

    `actions_for`'s first item, and only that: the action that moves the pull
    request itself along — out of draft, or into the base branch. Everything
    else the menu offers is either a prompt for the session or a comment,
    which is what the page's composer is for.

    One at a time, always: a draft is asked to come out of draft, and an open
    PR is offered the single merge that fits the state its checks are in —
    auto-merge while they are still running, the merge itself once they are
    green, and the way back out of auto-merge once GitHub is already holding
    the PR. The same answer the menu gives, drawn as a button, so the page and
    the menu can't recommend different things about the same PR; and since it
    is the one Collins recommends, it is the one that wears the accent.
    """
    if pr.state == "DRAFT":
        return [ready_action(pr)]
    if pr.state == "OPEN" and not pr.conflicting:
        # Same gate as the menu's: GitHub refuses a merge on a branch it can't
        # merge, and a draft can't be auto-merged either.
        return [_landing_action(pr)]
    return []


def _landing_action(pr: PullRequest) -> Action:
    """The one thing left to say about landing *pr*, for an open PR that can.

    Three states, one offer: GitHub is already holding it, so the offer is to
    call that off; its checks aren't in yet, so the offer is to have GitHub
    land it when they are; or they are green, so the offer is the merge itself.
    Written once and read by both the menu and the page's button, which is what
    keeps the two from disagreeing about the same pull request.
    """
    if pr.auto_merging:
        return disable_auto_merge_action(pr)
    return merge_action(pr, auto=not checks_green(pr))


def alternate_actions(pr: PullRequest, can_archive: bool = False) -> list[Action]:
    """The other courses `header_actions`' button deliberately doesn't take.

    What the PR page hangs off a right-click on that button: the actions worth
    having a click away but not worth a button of their own, because the button
    says the one thing Collins recommends and these two are the ways of *not*
    doing it — merging and being done with the session behind it, or closing
    the pull request unmerged.

    A pull request GitHub is already holding gets one more: the immediate merge
    itself, which its button has stopped offering (see `_landing_action`).
    Waiting for the checks is the course Collins recommends there — it is the
    one that was asked for — but "land it now" must stay reachable without
    turning auto-merge off first and pressing merge afterwards.

    "Merge and archive" is only ever offered beside a merge that happens now.
    On auto-merge there is nothing to wait for in the app — GitHub lands the PR
    minutes or hours later, on its own — and a session archived now would be
    archived on a promise rather than on a merge. *can_archive* is the caller's
    answer to "is there a session here to archive at all?" (see
    prmenu.ActionHost.archive): the PR page docked beside a session has one, a
    chip's menu is answering for a PR whose page isn't open, and the record
    behind a sidebar row may have no tab at all.

    Closing is offered for as long as there is something to close — every state
    but merged and closed, which is exactly `LIVE`. An unfetched PR offers
    nothing, as everywhere else here: a Close that was never going to work is
    worse than a menu with one row in it.
    """
    actions: list[Action] = []
    keys = {action.key for action in header_actions(pr)}
    if DISABLE_AUTO_MERGE in keys:
        actions.append(merge_action(pr, auto=False))
    # Beside either merge-now: the button's own when the checks are green, or
    # the alternate just added when GitHub is holding the PR instead.
    if can_archive and (keys & {MERGE, DISABLE_AUTO_MERGE}):
        actions.append(merge_archive_action(pr))
    if pr.state in LIVE:
        actions.append(close_action(pr))
    return actions


def repair_action(pr: PullRequest, prompt_block: str = "") -> Action | None:
    """The one prompt that would clear what is blocking *pr*'s merge, or None.

    What the PR page hangs under its Checks list — the one place on the page
    where the merge blockers are enumerated, and so the place to offer doing
    something about them. Which of the two blockers a PR has decides the
    wording: failed checks ask to be fixed, a conflicting branch asks to be
    rebased, and a PR carrying both gets a single button asking for both in
    one prompt rather than two the user would have to press in the right
    order (see FIX_ALL_PROMPT).

    A conflict alone counts, even with every check green: the page lists it
    as a failed check of its own (see prdetail's synthetic row), and a red row
    in that list with nothing offered under it is the page pointing at a
    problem and shrugging.

    None where there is nothing to repair, and on any settled PR: what CI said
    on the way into a merged pull request is history, and there is no branch
    left to rebase. *prompt_block* rides back as `blocked` exactly as it does
    on the menu's rows — the offer stays put and the button greys out with the
    reason on it, rather than coming and going with what a terminal happens to
    be showing.
    """
    if pr.state not in LIVE:
        return None
    if pr.failed and pr.conflicting:
        prompt = FIX_ALL_PROMPT.format(number=pr.number)
        return Action(
            FIX_ALL,
            _("Fix errors & resolve conflicts"),
            _("Send “{prompt}” to this session").format(prompt=prompt),
            prompt=prompt,
            blocked=prompt_block,
        )
    if pr.conflicting:
        return rebase_action(pr, prompt_block)
    if pr.failed:
        return fix_ci_action(pr, prompt_block)
    return None


def fix_ci_action(pr: PullRequest, prompt_block: str = "") -> Action:
    """Ask the session to fix whatever *pr*'s checks are failing on."""
    prompt = CI_PROMPT.format(number=pr.number)
    return Action(
        FIX_CI,
        _("Address the CI errors"),
        _("Send “{prompt}” to this session").format(prompt=prompt),
        prompt=prompt,
        blocked=prompt_block,
        short=_("Fix errors"),
    )


def rebase_action(pr: PullRequest, prompt_block: str = "") -> Action:
    """Ask the session to rebase *pr* onto its base and resolve the conflicts."""
    prompt = REBASE_PROMPT.format(number=pr.number)
    return Action(
        REBASE,
        _("Rebase / resolve conflicts"),
        _("Send “{prompt}” to this session").format(prompt=prompt),
        prompt=prompt,
        blocked=prompt_block,
        short=_("Resolve conflicts"),
    )


def ready_action(pr: PullRequest) -> Action:
    """Take *pr* out of draft. No confirmation: a draft marked ready by
    mistake goes straight back to being one."""
    return Action(
        READY,
        _("Mark ready for review"),
        _("Take {slug} out of draft").format(slug=pr.slug),
        short=_("Ready"),
    )


def review_action(pr: PullRequest) -> Action:
    """Ask the repository's Claude workflow for a review of *pr*, by saying
    so in a comment — the only surface such a review has."""
    return Action(
        REVIEW,
        _("Ask Claude for a review"),
        _("Comment “{comment}” on {slug}").format(comment=REVIEW_COMMENT, slug=pr.slug),
    )


def merge_action(pr: PullRequest, auto: bool) -> Action:
    """Merge *pr* now, or when its checks say so — both ask before they go
    ahead.

    Merging is the one thing in this menu that can't be taken back and that
    everybody watching the repository sees, and auto-merge is the same act on
    a delay, so neither happens on a single stray click — unless the
    confirm_merges setting says the asking is in the way (see
    `confirmation`). The immediate merge's question says which of the two
    situations it is being asked in: the checks are in and green, or they
    aren't — the second is a real thing to offer (a branch with no required
    checks merges fine), but not one to ask about in the first one's words.
    """
    if auto:
        return Action(
            AUTO_MERGE,
            _("Merge when checks pass"),
            _("Turn on auto-merge for {slug}").format(slug=pr.slug),
            Confirm(
                _("Merge {slug} when its checks pass?").format(slug=pr.slug),
                _(
                    "GitHub merges it as soon as every required check has passed. "
                    "You can still cancel auto-merge on the pull request page."
                ),
                _("Enable auto-merge"),
            ),
            short=_("Auto-Merge"),
        )
    return Action(
        MERGE,
        _("Merge pull request"),
        _("Merge {slug} now").format(slug=pr.slug),
        Confirm(_("Merge {slug}?").format(slug=pr.slug), _merge_body(pr), _("Merge")),
        short=_("Merge"),
    )


def disable_auto_merge_action(pr: PullRequest) -> Action:
    """Tell GitHub to stop waiting to merge *pr* by itself.

    No confirmation, unlike the merges it stands in for: nothing lands and
    nothing is thrown away — the pull request goes back to sitting where it
    was, and the same button offers auto-merge again on the next fetch. It is
    the undo, so it wears neither the merge green nor the accent (see prview's
    _ActionBar).
    """
    return Action(
        DISABLE_AUTO_MERGE,
        _("Disable auto-merge"),
        _("Stop GitHub from merging {slug} when its checks pass").format(slug=pr.slug),
        short=_("Disable Auto-Merge"),
    )


def _merge_body(pr: PullRequest) -> str:
    """What an immediate merge's question says about where *pr*'s checks got
    to. A branch with no required checks merges fine, so merging past
    unfinished ones is a real thing to offer — just not one to ask about in
    the all-clear's words."""
    if checks_green(pr):
        return _("Its checks have passed. This merges the pull request on GitHub now.")
    return _(
        "Its checks haven't all passed. This merges the pull request on GitHub "
        "now, if the repository lets it."
    )


def merge_archive_action(pr: PullRequest) -> Action:
    """Merge *pr* now, then archive the session that opened it.

    The end of a piece of work as one action: the pull request lands and the
    session that produced it leaves the sidebar. The two halves are strictly
    ordered and the caller keeps that order — `perform` only merges, and the
    archive is the app's own, run once this has come back without an error.
    A merge GitHub refused leaves the session exactly where it was, which is
    the whole point of not archiving first.
    """
    return Action(
        MERGE_ARCHIVE,
        _("Merge and archive session"),
        _("Merge {slug} now, then archive this session").format(slug=pr.slug),
        Confirm(
            _("Merge {slug} and archive this session?").format(slug=pr.slug),
            _merge_body(pr)
            + " "
            + _(
                "The session is archived once the merge lands — you can bring it "
                "back with Undo, or from “Show archived”."
            ),
            _("Merge & archive"),
        ),
        short=_("Merge & archive"),
    )


def close_action(pr: PullRequest) -> Action:
    """Close *pr* without merging it.

    The one action here that ends a pull request by throwing its work away
    rather than landing it, so it asks in red (see Confirm.destructive) — and
    says in the asking that GitHub keeps the branch and the conversation, since
    "close" reads as "delete" to anyone who hasn't reopened one before.
    """
    return Action(
        CLOSE,
        _("Close pull request"),
        _("Close {slug} without merging").format(slug=pr.slug),
        Confirm(
            _("Close {slug}?").format(slug=pr.slug),
            _(
                "The pull request is closed without merging. Its branch and its "
                "comments stay, and it can be reopened on GitHub."
            ),
            _("Close"),
            destructive=True,
        ),
        short=_("Close"),
    )


def perform(key: str, pr: PullRequest) -> str | None:
    """Carry out action *key* on *pr*. None when it worked, else why it didn't.

    Only the actions that talk to GitHub land here; the ones carrying a prompt
    are the caller's, since sending one never leaves the app.

    Never call on the main thread — every branch waits on `gh`.
    """
    repository = repository_for(pr.url)
    if repository is None:  # not a PR page: never hand it to a subprocess
        return _("{url} doesn't look like a pull request.").format(url=pr.url)
    if key == READY:
        return _run(["pr", "ready", pr.url])
    if key in (MERGE, AUTO_MERGE, MERGE_ARCHIVE):
        # MERGE_ARCHIVE is the plain merge as far as GitHub is concerned; the
        # archive that follows it is the app's, and only happens if this
        # returns None (see merge_archive_action).
        args = ["pr", "merge", pr.url, merge_method(repository)]
        if key == AUTO_MERGE:
            args.append("--auto")
        return _run(args)
    if key == DISABLE_AUTO_MERGE:
        # No merge method here: this cancels the request rather than making
        # one, and gh refuses the two flags together.
        return _run(["pr", "merge", pr.url, "--disable-auto"])
    if key == CLOSE:
        return _run(["pr", "close", pr.url])
    if key == REVIEW:
        return _run(["pr", "comment", pr.url, "--body", REVIEW_COMMENT])
    return _("Collins doesn't know how to do that.")  # unreachable; never a crash


# The composer's two review verdicts (see prview): named rather than gh flags
# so a widget never builds argv, and so a typo'd verdict is refused here
# instead of handed to a subprocess.
APPROVE = "approve"
REQUEST_CHANGES = "request-changes"
_VERDICT_FLAGS = {APPROVE: "--approve", REQUEST_CHANGES: "--request-changes"}


def comment(pr: PullRequest, body: str) -> str | None:
    """Post *body* on *pr* as an issue comment. None when it worked, else why not.

    The body is free text typed in the app, so it travels to gh as stdin
    (``--body-file -``), never as an argv entry — the shape of a command must
    not depend on what somebody wrote in a text box. Never call on the main
    thread.
    """
    return _refused(pr) or _run(["pr", "comment", pr.url, "--body-file", "-"], stdin=body)


def review(pr: PullRequest, verdict: str, body: str = "") -> str | None:
    """Submit a review of *pr*: APPROVE or REQUEST_CHANGES, with *body* if any.

    None when it worked, else why not. The body rides stdin exactly as
    `comment`'s does; without one the flag goes alone — which GitHub accepts
    for an approval and refuses for requested changes, so the composer only
    offers the latter over a written comment. Never call on the main thread.
    """
    flag = _VERDICT_FLAGS.get(verdict)
    if flag is None:
        return _("Collins doesn't know how to do that.")
    args = ["pr", "review", pr.url, flag]
    if body:
        args += ["--body-file", "-"]
    return _refused(pr) or _run(args, stdin=body or None)


# The review-thread mutations (see prdetail.PrThread for where the ids come
# from). GraphQL because that is the only surface threads exist on; variables
# rather than string-building, like prdetail's query, so nothing typed or
# fetched is ever spliced into mutation text.
_REPLY_MUTATION = """\
mutation($threadId: ID!, $body: String!) {
  addPullRequestReviewThreadReply(
    input: {pullRequestReviewThreadId: $threadId, body: $body}
  ) { comment { id } }
}
"""
_RESOLVE_MUTATION = """\
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) { thread { id } }
}
"""
_UNRESOLVE_MUTATION = """\
mutation($threadId: ID!) {
  unresolveReviewThread(input: {threadId: $threadId}) { thread { id } }
}
"""


def reply_in_thread(pr: PullRequest, thread_id: str, body: str) -> str | None:
    """Post *body* as a reply in review thread *thread_id* on *pr*.

    None when it worked, else why not. The body travels as ``-F body=@-`` —
    gh reading the variable's value off stdin — so free text never becomes
    an argv entry, exactly as `comment`'s does. Never call on the main
    thread.
    """
    refusal = _refused(pr) or _bad_thread(thread_id)
    if refusal:
        return refusal
    return _run(
        [
            "api", "graphql",
            "-f", f"query={_REPLY_MUTATION}",
            "-f", f"threadId={thread_id}",
            "-F", "body=@-",
        ],
        stdin=body,
    )


def set_thread_resolved(pr: PullRequest, thread_id: str, resolved: bool) -> str | None:
    """Mark review thread *thread_id* on *pr* resolved, or unresolved.

    None when it worked, else why not. Never call on the main thread.
    """
    refusal = _refused(pr) or _bad_thread(thread_id)
    if refusal:
        return refusal
    mutation = _RESOLVE_MUTATION if resolved else _UNRESOLVE_MUTATION
    return _run(
        ["api", "graphql", "-f", f"query={mutation}", "-f", f"threadId={thread_id}"]
    )


def _bad_thread(thread_id: str) -> str | None:
    """Why *thread_id* must not reach a subprocess — the ids the mutations
    take came from a GraphQL reply, and only what still looks like one may
    go back out (see prdetail.THREAD_ID)."""
    if not isinstance(thread_id, str) or not THREAD_ID.match(thread_id):
        return _("Collins doesn't know how to do that.")
    return None


def _refused(pr: PullRequest) -> str | None:
    """Why *pr* must not reach a subprocess at all — the gate `perform` opens
    with, for the write calls that don't need the repository name back."""
    if repository_for(pr.url) is None:
        return _("{url} doesn't look like a pull request.").format(url=pr.url)
    return None


def merge_method(repository: str) -> str:
    """The gh flag naming a merge method *repository* actually allows.

    One extra round trip per merge, and worth it: naming a method the
    repository has turned off fails the merge outright, and which methods are
    on is a per-repository setting Collins has no other way to know. If the
    question can't be answered, squash is assumed — GitHub's own default, and
    the method the repositories this is written for use.
    """
    fields = ",".join(field for field, _flag in _MERGE_METHODS)
    data = gh_json(["repo", "view", repository, "--json", fields])
    if not isinstance(data, dict):
        return _DEFAULT_MERGE_METHOD
    for field, flag in _MERGE_METHODS:
        if data.get(field) is True:
            return flag
    return _DEFAULT_MERGE_METHOD


def _run(args: list[str], stdin: str | None = None) -> str | None:
    ok, message = gh_run(args, stdin=stdin)
    return None if ok else message
