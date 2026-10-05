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

`is_checkout(stat)` is the pure rule (GTK-free, unit-tested); `ask` is the
request. A refusal, no link, or any failure answers False: a worktree
launch is then simply not offered, which is the safe direction.
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


def ask(cwd: str, then: Callable[[bool], None]) -> None:
    """Whether *cwd* is a git checkout, asked of the service off the main
    loop; `then(is_checkout)` lands at `GLib.PRIORITY_DEFAULT`."""
    if not cwd:
        then(False)
        return
    marker = git_marker(cwd)

    def land(kind: str, value: object) -> None:
        then(kind == "ok" and is_checkout(getattr(value, "kind", None)))

    remotefiles.off_main(lambda: remotefiles.stat_path(marker), land, name="git-checkout")
