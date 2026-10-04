# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-10-04. Full change history: git log for this file.
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


def ngettext(singular: str, plural: str, n: int) -> str:
    """Plural-aware translation (extracted by xgettext -k ngettext:1,2)."""
    return _translation.ngettext(singular, plural, n)


def N_(message: str) -> str:
    """No-op marker for strings translated later (extracted by xgettext -k N_)."""
    return message


def _formatted(text: str, args: dict | None) -> str | None:
    if not args:
        return text
    try:
        return text.format_map(dict(args))
    except (KeyError, ValueError, IndexError, AttributeError, TypeError):
        return None


def translate(msgid: str, args: dict | None = None) -> str:
    """A string the service sent as a msgid and its args, as this client's
    person reads it (split-service spec §3.14): the msgid through `_()`
    and, with args, `str.format_map`. Text with no args is never formatted:
    an agent's words, a git error's, cross as their own msgid and may hold
    braces of their own. A translation whose placeholders don't fit the
    args falls back to the English source, then to the msgid as it is."""
    if not msgid:
        return ""
    for candidate in (_(msgid), msgid):
        text = _formatted(candidate, args)
        if text is not None:
            return text
    return msgid


def english(msgid: str, args: dict | None = None) -> str:
    """The same string in the English source, as the service writes it
    where no client is reading (a persisted record's readable copy)."""
    if not msgid:
        return ""
    text = _formatted(msgid, args)
    return msgid if text is None else text
