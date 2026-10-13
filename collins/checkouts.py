# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Is a directory a git checkout: the service's answer (split-service spec
§3.23, PR-2.6).

The worktree flag means something only in a checkout, and `.git` is a file
in a worktree's checkout, so either form counts. The directory is on the
service's machine, so the question is an `fs.stat` of its `.git`, asked off
the main loop and answered at `GLib.PRIORITY_DEFAULT`: the new-chat screen's
checkbox, the sidebar's project menu, the header menu's one-off entry and a
sibling session's launch all ask here instead of a `Path.exists` of their
own.

`is_checkout(stat)` is the pure rule (GTK-free, unit-tested). There are two
requests, because a failure means two different things:

- `ask` is for *offering* a worktree (a menu entry, a sibling's launch). A
  refusal, no link, or any failure answers False: a worktree launch is then
  simply not offered, which is the safe direction.
- `ask_answer` is for a launch the user already asked to isolate (the
  new-chat screen's Send). There False is not safe: launching plain would
  put the session in the checkout the worktree was meant to protect. So a
  failure is its own answer, None ("could not ask"), on which the caller
  does not launch, and the ask has a short timeout of its own
  (ANSWER_TIMEOUT_S) because a person is waiting on it.
"""

from __future__ import annotations

from collections.abc import Callable

from . import remotefiles


def git_marker(cwd: str) -> str:
    """The path whose existence makes *cwd* a checkout."""
    return cwd.rstrip("/") + "/.git"


def is_checkout(kind: str | None) -> bool:
    """Whether an `fs.stat` kind of the `.git` marker says checkout: a
    directory (a clone) or a file (a worktree's pointer), a symlink to
    either followed by the service. Nothing there, a dangling link or
    something that is not a file is not one."""
    return kind in ("file", "dir")


# How long a held launch waits for its answer before "could not ask".
ANSWER_TIMEOUT_S = 10.0


def answer_of(kind: str, value: object) -> bool | None:
    """`remotefiles.off_main`'s landing as an answer: True or False when
    the service said what is at the marker, None when it could not be
    asked (a refusal, a timeout, no link)."""
    if kind != "ok":
        return None
    return is_checkout(getattr(value, "kind", None))


def ask_answer(
    cwd: str, then: Callable[[bool | None], None], timeout: float | None = ANSWER_TIMEOUT_S
) -> None:
    """Whether *cwd* is a git checkout, asked of the service off the main
    loop; `then(answer)` lands at `GLib.PRIORITY_DEFAULT`, exactly once,
    with True, False, or None for "could not ask" (see the module
    docstring). No directory is False: there is nothing to ask."""
    if not cwd:
        then(False)
        return
    marker = git_marker(cwd)
    remotefiles.off_main(
        lambda: remotefiles.stat_path(marker, timeout=timeout),
        lambda kind, value: then(answer_of(kind, value)),
        name="git-checkout",
    )


def ask(cwd: str, then: Callable[[bool], None]) -> None:
    """`ask_answer` for an offer: `then(is_checkout)`, False on any
    failure, with the files' ordinary timeout."""
    ask_answer(cwd, lambda answer: then(bool(answer)), timeout=None)
