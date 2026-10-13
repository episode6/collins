"""What quitting does with each tab, GTK-free (spec §3.10, §3.12, §3.21).

`window.py` asks these for every tab when the window goes: the nothing-busy
fast path of `_on_close_request`, the plain *Quit*'s `do_detach`, and
`open_tab_entries`, the record the next launch reopens. The facts come from
the tab's mirror (`clientsession.ClientSession`): whether it shows a pty,
whether the CLI has come and gone (`cli_exited`: False for a spawn or an
attach whose first facts have not landed, so a quit a second after a
launch loses nothing — PR 605's review, should-fix 5), whether it is an
unstarted New Thread (an empty first prompt never sent).

The rules, from §3.10 "closing a tab ends it; quitting ends nothing":

- *Stop Sessions and Quit* (`exit`) releases every pty: today's close-all.
- A shell left after its CLI exited is no session to leave running: it is
  released whatever the setting (the 2026-10-05 review of PR 605).
- An unstarted New Thread has nothing to keep: a plain Quit tears it down
  as the fast path always did, instead of leaving a CLI at an empty prompt
  in the service and a "New session" row for every thread opened and
  abandoned (should-fix 2). It is never recorded as an open tab.
- Everything else detaches and is recorded, in tab order.
"""

from __future__ import annotations

RELEASE = "release"  # the pty ends with the tab (TerminalTab.release_pty)
DETACH = "detach"  # the pty runs on in the service (TerminalTab.detach_pty)


def tab_fate(*, behavior: str, has_pty: bool, cli_exited: bool, unstarted: bool) -> str | None:
    """What a quit that asks nothing more does with one tab: `RELEASE`,
    `DETACH`, or None for a tab with no pty to speak of (a new-chat screen,
    a tab already released). *behavior* is the `quit_with_running_sessions`
    setting; only `exit` ends a running session."""
    if not has_pty:
        return None
    if behavior == "exit" or cli_exited or unstarted:
        return RELEASE
    return DETACH


def records_as_open(*, has_pty: bool, cli_exited: bool, unstarted: bool, settling: bool) -> bool:
    """Whether a tab is an `open_tabs` entry: it shows a pty whose CLI is up
    or still coming up, it is not an unstarted New Thread, and it is not
    mid-close (the person is ending it)."""
    if not has_pty or settling:
        return False
    return not cli_exited and not unstarted
