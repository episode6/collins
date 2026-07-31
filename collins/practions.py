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
The one action that isn't about GitHub at all is FIX_CI, which sends a prompt
to the session that opened the PR — it needs a session sitting at an empty
prompt, so its caller decides whether it is on offer.

Every call out is `gh`, off the main thread, and reports back as "worked" or a
sentence explaining why not (see prstatus.gh_run) — nothing here raises at the
UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from .i18n import _
from .prstatus import PullRequest, gh_json, gh_run, repository_for

# The actions themselves. Menu order is the order they are built in.
OPEN = "open"
READY = "ready"
MERGE = "merge"
AUTO_MERGE = "auto-merge"
REVIEW = "review"
FIX_CI = "fix-ci"

# What "address the CI errors" sends to the session. Read by the agent CLI,
# not by a person, so it stays in English (and untranslated) whatever the app's
# language is.
CI_PROMPT = "address the ci error(s)"
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
    """One item in a PR's menu: what it says, and what picking it means."""

    key: str
    label: str
    tooltip: str = ""
    confirm: Confirm | None = None


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


def actions_for(pr: PullRequest, takes_prompt: bool) -> list[Action]:
    """The menu for *pr*: every action that makes sense for the state it is in.

    *takes_prompt* is whether the session this PR belongs to is somewhere a
    prompt can be sent right now — a tab open, at an empty input (see
    Provider.takes_prompt). It is the only thing here that isn't a property of
    the PR, and the reason "address the CI errors" comes and goes: it sends a
    prompt, and a session that is closed, or mid-sentence, is not somewhere to
    send one.

    Opening the PR is always first and always there, so the menu is never
    empty — a merged PR still has a page worth visiting. Everything past that
    needs a state, and an unfetched PR (no gh, no network) has none: better a
    short menu than a "Merge" that was never going to work.
    """
    actions = [
        Action(
            OPEN,
            _("Open pull request"),
            _("Open {slug} on GitHub").format(slug=pr.slug),
        )
    ]
    if pr.state == "DRAFT":
        actions.append(
            Action(
                READY,
                _("Mark ready for review"),
                _("Take {slug} out of draft").format(slug=pr.slug),
            )
        )
    elif pr.state == "OPEN":
        actions.append(_merge_action(pr))
    if pr.state in _LIVE:
        actions.append(
            Action(
                REVIEW,
                _("Ask Claude for a review"),
                _("Comment “{comment}” on {slug}").format(comment=REVIEW_COMMENT, slug=pr.slug),
            )
        )
        if pr.failed and takes_prompt:
            actions.append(
                Action(
                    FIX_CI,
                    _("Address the CI errors"),
                    _("Send “{prompt}” to this session").format(prompt=CI_PROMPT),
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

    Only the actions that talk to GitHub land here; opening the page and
    prompting a session are the caller's, since neither leaves the app.

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
