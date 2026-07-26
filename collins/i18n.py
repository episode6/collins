# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-07-26. Full change history: git log for this file.
"""Translation setup. Call init() once at startup, then use _() everywhere."""

from __future__ import annotations

import gettext
from pathlib import Path

DOMAIN = "collins"
LOCALEDIR = Path(__file__).resolve().parent / "locale"

# Languages offered in Preferences: code -> native display name.
# "" means "follow the system locale".
LANGUAGES: list[tuple[str, str]] = [
    ("", "System default"),
    ("en", "English"),
    ("hu", "Magyar"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
]

_translation: gettext.NullTranslations = gettext.NullTranslations()


def init(language: str | None = None) -> None:
    """Load the translation for the given language code (empty/None = system)."""
    global _translation
    if language in (None, "", "system"):
        _translation = gettext.translation(DOMAIN, str(LOCALEDIR), fallback=True)
    elif language == "en":
        _translation = gettext.NullTranslations()  # source strings are English
    else:
        _translation = gettext.translation(
            DOMAIN, str(LOCALEDIR), languages=[language], fallback=True
        )


def _(message: str) -> str:
    return _translation.gettext(message)


def N_(message: str) -> str:
    """No-op marker for strings translated later (extracted by xgettext -k N_)."""
    return message
