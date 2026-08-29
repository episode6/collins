# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-29. Full change history: git log for this file.
"""Collins — native GTK4 GUI to manage and resume AI coding agent sessions."""

from __future__ import annotations

__version__ = "0.1.2"

# The two app ids a user's real instances run under. The debug id doubles as
# the debug build's icon name, and as a prefix: any COLLINS_APP_ID derived
# from it (com.episode6.Collins.Debug.*) counts as a debug instance too.
APP_ID = "com.episode6.Collins"
DEBUG_APP_ID = "com.episode6.Collins.Debug"


def is_debug_app_id(app_id: str | None) -> bool:
    """Whether *app_id* names a debug instance: the debug build itself, or
    any COLLINS_APP_ID derived from its id. The release id is not, and
    neither is a capture or e2e run's generated id (com.episode6.Collins.E2E.*)
    — that one starts with the release id, not the debug one. What keys on
    this: the recolored icon, the About dialog's build info, and the
    developer items in the sidebar's menu."""
    return bool(app_id and app_id.startswith(DEBUG_APP_ID))
