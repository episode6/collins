# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-18. Full change history: git log for this file.
"""Collins — native GTK4 GUI to manage and resume AI coding agent sessions."""

__version__ = "0.1.1"

# The two app ids a user's real instances run under. The debug id doubles as
# the debug build's icon name, and as a prefix: any COLLINS_APP_ID derived
# from it (com.episode6.Collins.Debug.*) counts as a debug instance too.
APP_ID = "com.episode6.Collins"
DEBUG_APP_ID = "com.episode6.Collins.Debug"
