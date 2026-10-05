# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Why a session can't be handed to the background right now.

The /bg gate, as a pure function of facts both halves hold: the service
(`service.bgagents`) decides each row's `can_background` from its own
sessions, and the window reads the same rule over its tab and the store's
mirror for the reason it shows (the header button's tooltip, the close
dialog's sentence, the quit-time queue's wait). Kept apart from
`bgstatus`, whose poller and agent-list reads are the service's alone
(split-service spec §3.22, PR-1.12d): the client imports this, never that.

GTK-free and free of I/O.
"""

from __future__ import annotations

# Why a session tab can't be handed to the background right now — the values
# background_blocker() returns. "" means it can.
BLOCK_NOT_SESSION = "not-session"
BLOCK_UNSUPPORTED = "unsupported"
BLOCK_UNREGISTERED = "unregistered"
BLOCK_IN_FLIGHT = "in-flight"
BLOCK_SANDBOXED = "sandboxed"


def background_blocker(
    is_session: bool,
    supports_detach: bool,
    is_fork: bool,
    session_id: str | None,
    has_row: bool,
    detach_in_flight: bool,
    is_sandboxed: bool = False,
) -> str:
    """Why a tab can't be backgrounded right now, or "" when it can.

    A sandboxed session is never handed over: a backgrounded job is
    respawned by the CLI's daemon, a host process outside any box, and the
    daemon's job record has no seam a wrapper could ride (measured on
    2.1.268: respawn flags are an allowlist, isolation is none|worktree).
    The refusal ranks above registration — it never changes, so it is the
    reason to show.

    A /bg is only safe once the app can name the conversation it is handing
    over. The handoff has to record `old id -> the id the background agent
    forks into`, and both halves of that need a registered session: the old id
    to key the record on, and a sidebar row to disable while it's in flight and
    to redirect once it lands. Fed without those, the /bg still detaches the
    agent — but nothing records it, no row survives to reach it, and the fork's
    transcript is typically a metadata-only stub the scan skips, so the agent
    runs on with no way back to it.

    `has_row` is the real registration test, not "the store knows this id": a
    tab attached to a live fork runs under the fork's own (stub, undiscovered)
    id, and the row standing in for it is the one it forked from.

    Only one handoff runs at a time. bgstatus.match_background_fork() pairs on
    the conversation's first-message uuid and falls back to the working
    directory, and a fork that hasn't written its copy yet has no readable
    uuid — so two /bg handoffs in flight over the same project can be paired
    to each other's agents, or both to the same one.
    """
    if not is_session:
        return BLOCK_NOT_SESSION
    if not supports_detach:
        return BLOCK_UNSUPPORTED
    if is_sandboxed:
        return BLOCK_SANDBOXED
    # A fork tab deliberately shares the original's id and writes nothing under
    # it, so there is no id of its own to record a handoff against.
    if is_fork or not session_id or not has_row:
        return BLOCK_UNREGISTERED
    if detach_in_flight:
        return BLOCK_IN_FLIGHT
    return ""
