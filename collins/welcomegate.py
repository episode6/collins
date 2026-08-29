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
- The CLI is not on PATH. That ask has no "later" (see welcome's docstring
  for why) and comes back on every launch that can't find `claude`,
  ``welcome_seen`` or not — exactly as the CLI-only dialog it grew out of.
"""

from __future__ import annotations

from . import clisetup

# The setting an answered dialog writes. Read at every launch before the
# dialog is built, so an install that has seen it pays nothing for it again.
SEEN_SETTING = "welcome_seen"


def should_show(state, cli_found: bool | None = None) -> bool:
    """Whether this launch shows the welcome dialog.

    *cli_found* is whether `claude` is on PATH; left None it is asked of
    clisetup (a `shutil.which`, no subprocess), and it is a parameter at all
    so the rule can be tested without a CLI to find.
    """
    if cli_found is None:
        cli_found = clisetup.on_path()
    return not cli_found or not bool(state.get_setting(SEEN_SETTING))
