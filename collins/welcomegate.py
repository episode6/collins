# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Whether a launch owes the user the welcome dialog (see welcome).

The dialog is the first thing Collins says: where the Claude Code CLI is,
when that needs asking, and which of its features run Claude on the user's
behalf — against their subscription's usage limits — with the switch for
each. Two things bring it up, and they are decided here so the rule is
unit-testable where GTK isn't:

- ``welcome_seen`` is not set. Once per install, and once for every install
  that predates the setting: the disclosure is as new to them as to anyone,
  and they are the people who have been spending tokens unaware the
  longest. Answering the dialog by any path but Quit sets it.
- The service has no CLI. That ask has no "later" (see welcome's docstring
  for why) and comes back on every launch whose service can't find
  `claude`, ``welcome_seen`` or not — exactly as the CLI-only dialog it
  grew out of. It is the service's word (remotecli), not this window's
  PATH: the CLI runs where the service runs, and a window started from a
  desktop launcher has a PATH the service's login-shell capture doesn't.
"""

from __future__ import annotations

from . import apilink, remotecli
from .api import protocol

# The setting an answered dialog writes. Read at every launch before the
# dialog is built, so an install that has seen it pays nothing for it again.
SEEN_SETTING = "welcome_seen"


# The families whose surfaces the dialog speaks for: the CLI group, Token use
# and the session tools.
DISCLOSED_BY = (protocol.CAP_INTERNAL, protocol.CAP_USAGE, protocol.CAP_TOOLS)


def should_show(state, cli_found: bool | None = None) -> bool:
    """Whether this launch shows the welcome dialog.

    *cli_found* is whether the service has a `claude`; left None it is
    asked of remotecli (the service's word at connect, no round trip), and
    it is a parameter at all so the rule can be tested without a service.
    """
    if not any(apilink.has(cap) for cap in DISCLOSED_BY):
        return False  # nothing to say on this service (D5): every group would be hidden
    if cli_found is None:
        cli_found = cli_is_found()
    return not cli_found or not bool(state.get_setting(SEEN_SETTING))


def cli_is_found() -> bool:
    """Whether the launch may go on as to the CLI: the service has a `claude`,
    or it serves no `cli.check`, so nothing here could find one and asking
    would hold the window for an answer nobody can take (D5, no `internal`)."""
    return not remotecli.missing() or not apilink.has(protocol.CAP_INTERNAL)
