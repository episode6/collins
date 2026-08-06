"""What Collins can *do* to a pull request, and the `gh` calls that do it.

prstatus reads a PR; this decides what is worth offering to change about one
and carries it out. Both halves live here rather than beside the widgets so
they stay testable without a Gtk namespace (CI installs PyGObject but no GTK),
and so the footer's chips and the sidebar's menu can't drift apart on what a
PR offers.

Which actions a PR offers is a question about its state, and the answer is
deliberately narrow: a draft is asked to come out of draft, an open PR is
merged now or told to merge itself when its checks go green, and anything the
repository's own Claude workflow can be asked for goes through a comment
(`@claude review`) rather than an API Collins would have to hold a token for.

Four of them aren't about GitHub at all: FIX_CI, REBASE, COMMENTS and NEW_PR
send a prompt to the session that opened the PR and let the agent do the work.
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
from .prstatus import PullRequest, gh_json, gh_run, repository_for

# The actions themselves. Menu order is the order they are built in.
READY = "ready"
MERGE = "merge"
AUTO_MERGE = "auto-merge"
REBASE = "rebase"
REVIEW = "review"
FIX_CI = "fix-ci"
COMMENTS = "comments"
NEW_PR = "new-pr"

# What the prompt-sending actions type into the session. Read by the agent
# CLI, not by a person, so they stay in English (and untranslated) whatever the
# app's language is. Two of them name their PR: a session can have opened
# several, and a bare "the ci error(s)" would leave the agent to guess whose.
CI_PROMPT = "Address the ci error(s) on PR #{number}"
REBASE_PROMPT = "rebase PR #{number} and resolve the conflicts"
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

# The states in which a PR is still something you can act on.
_LIVE = ("OPEN", "DRAFT")


@dataclass(frozen=True)
class Confirm:
    """The dialog an action puts up before it goes ahead."""

    heading: str
    body: str
    label: str


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
    """

    key: str
    label: str
    tooltip: str = ""
    confirm: Confirm | None = None
    prompt: str = ""
    blocked: str = ""


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
        actions.append(
            Action(
                READY,
                _("Mark ready for review"),
                _("Take {slug} out of draft").format(slug=pr.slug),
            )
        )
    elif pr.state == "OPEN" and not pr.conflicting:
        # A conflicting PR gets no merge item at all: merging now would only
        # come back with GitHub's refusal, and auto-merge can't be enabled on
        # a branch GitHub can't merge. The rebase action below stands where
        # the merge would have — resolving is what makes merging offerable.
        actions.append(_merge_action(pr))
    if pr.state in _LIVE and pr.conflicting:
        rebase_prompt = REBASE_PROMPT.format(number=pr.number)
        actions.append(
            Action(
                REBASE,
                _("Rebase / resolve conflicts"),
                _("Send “{prompt}” to this session").format(prompt=rebase_prompt),
                prompt=rebase_prompt,
                blocked=prompt_block,
            )
        )
    if pr.state in _LIVE:
        actions.append(
            Action(
                REVIEW,
                _("Ask Claude for a review"),
                _("Comment “{comment}” on {slug}").format(comment=REVIEW_COMMENT, slug=pr.slug),
            )
        )
        if pr.failed:
            ci_prompt = CI_PROMPT.format(number=pr.number)
            actions.append(
                Action(
                    FIX_CI,
                    _("Address the CI errors"),
                    _("Send “{prompt}” to this session").format(prompt=ci_prompt),
                    prompt=ci_prompt,
                    blocked=prompt_block,
                )
            )
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


def _merge_action(pr: PullRequest) -> Action:
    """Merge now, or when the checks say so — both ask before they go ahead.

    Merging is the one thing in this menu that can't be taken back and that
    everybody watching the repository sees, and auto-merge is the same act on
    a delay, so neither happens on a single stray click.
    """
    if checks_green(pr):
        return Action(
            MERGE,
            _("Merge pull request"),
            _("Merge {slug} now").format(slug=pr.slug),
            Confirm(
                _("Merge {slug}?").format(slug=pr.slug),
                _("Its checks have passed. This merges the pull request on GitHub now."),
                _("Merge"),
            ),
        )
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
    if key in (MERGE, AUTO_MERGE):
        args = ["pr", "merge", pr.url, merge_method(repository)]
        if key == AUTO_MERGE:
            args.append("--auto")
        return _run(args)
    if key == REVIEW:
        return _run(["pr", "comment", pr.url, "--body", REVIEW_COMMENT])
    return _("Collins doesn't know how to do that.")  # unreachable; never a crash


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


def _run(args: list[str]) -> str | None:
    ok, message = gh_run(args)
    return None if ok else message
