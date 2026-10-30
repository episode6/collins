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

from .i18n import _

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


# -- the quit dialog's words (`MainWindow._confirm_quit`, the `ask` setting) ----------


def quit_heading(agent_busy: int, panel_busy: bool) -> str:
    """The dialog's heading: how many *sessions* keep running — the agents
    owning their terminals, not a tab that is busy only in its terminal
    panel (should-fix 3). With no agent busy the heading names the panel's
    command, the one thing the quit then has to say."""
    if agent_busy == 0 and panel_busy:
        return _("A command is running in a terminal panel")
    if agent_busy == 1:
        return _("1 session keeps running in the Collins service")
    return _("{n} sessions keep running in the Collins service").format(n=agent_busy)


def quit_body(*, other_windows: bool, panel_busy: bool, tray_present: bool, first_notice: bool) -> str:
    """The dialog's body. *Quit* and *Stop Sessions and Quit* both end the
    terminal panels' shells with their tabs, so a command running in one
    is named when there is one (`panel_busy`): with `detach` the default,
    nothing else would say so before it is killed. *other_windows*: this
    window closes, Collins does not; *tray_present*: a hidden window has a
    status icon to come back from; *first_notice*: the once-per-device
    sentence that the icon's going is no sign the agents did."""
    if other_windows:
        parts = [_("This window closes and its agents keep working; their tabs come "
                   "back the next time Collins opens. Stop Sessions and Quit asks "
                   "each agent to exit first.")]
    else:
        parts = [_("Quit closes Collins and leaves every agent working; the tabs "
                   "come back the next time Collins opens. Stop Sessions and Quit "
                   "asks each agent to exit first.")]
    if panel_busy:
        parts.append(_("The command running in a terminal panel ends either way: "
                       "panel shells go with their tabs. Only Keep Running keeps it."))
    parts.append(_("Keep Running hides the window and leaves every "
                   "session exactly as it is."))
    if not tray_present:
        parts.append(_("Without a status icon, a hidden window comes back by "
                       "launching Collins again."))
    if first_notice:
        parts.append(_("Collins's status icon leaves with the window; "
                       "the sessions keep running."))
    return " ".join(parts)


# -- the *Restart service* dialog's body (`MainWindow._ask_restart_service`, §3.21) ---


def restart_body(*, sessions: int, busy: int, background: int, keeper: bool = False) -> str:
    """The blast radius of a service restart. *sessions* are the agent ptys
    the service runs and *busy* how many of them are working. Without a
    keeper they end with the service (gracefully; each open tab resumes
    its own once it is back); with one (*keeper*, PR-3.6: the service's
    status names the keeper that holds the ptys) they run on through the
    restart, blocked on their output for the moment it takes, and each
    open tab shows its own again. *background* are the rows running as
    `/bg` agents: the CLI's background daemon is no child of the service's
    stop (the unit is `KillMode=process`, D52), so they are counted as
    surviving, never as ending."""
    if sessions and keeper:
        parts = [_("{n} sessions keep running through the restart, {busy} of them working: "
                   "the keeper holds their terminals. Each open tab shows its session "
                   "again once the service is back.").format(n=sessions, busy=busy)]
    elif sessions:
        parts = [_("{n} sessions end with it, {busy} of them working. Each open tab "
                   "resumes its session once the service is back.").format(n=sessions, busy=busy)]
    else:
        parts = [_("No session is running.")]
    if background == 1:
        parts.append(_("The background (/bg) agent keeps running: it is not the service's."))
    elif background:
        parts.append(_("The {n} background (/bg) agents keep running: they are not "
                       "the service's.").format(n=background))
    parts.append(_("Collins and its status icon stay while the service restarts."))
    return " ".join(parts)


# -- the protocol mismatch over a forward (`App._mismatch_at_startup`, §3.24) ----------

MISMATCH_LOCAL = "local"  # *Connect to This Computer*
MISMATCH_QUIT = "quit"


def mismatch_words(*, remote_name: str, service: object, client: int) -> tuple[str, str, list]:
    """The dialog for a service reached over ssh that speaks another
    protocol (ruled 2026-10-29, the review of PR 623, Q1): never *Restart
    Service* — the client cannot stop a process on another machine, and
    the local service is not the one that refused. The heading, the body
    (naming the connection and the side to upgrade) and the choices as
    `dialogs.answer_dialog` takes them: *Connect to This Computer* only;
    the dialog's own Cancel is *Quit* at launch and "stay disconnected" on
    a reconnect."""
    shown = service if service is not None else "?"
    heading = _("Collins and {name} speak different protocols").format(name=remote_name)
    if isinstance(service, int) and service < client:
        body = _(
            "The service on {name} speaks protocol {service} and this Collins speaks "
            "protocol {client}: upgrade Collins on {name}."
        ).format(name=remote_name, service=shown, client=client)
    else:
        body = _(
            "The service on {name} speaks protocol {service} and this Collins speaks "
            "protocol {client}: upgrade Collins here."
        ).format(name=remote_name, service=shown, client=client)
    choices = [(MISMATCH_LOCAL, _("Connect to This Computer"), "suggested")]
    return heading, body, choices
