# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free matching for the preferences dialog's search bar.

Word-wise rather than one substring: every word of the query has to turn up
somewhere in a setting's text, in any order, so "title session" finds
"Auto-generate session titles" the same as "session title" does. Words match
unanchored, because half of what people remember about a setting is a word
from the middle of its subtitle. Kept free of GTK (like fuzzy.py) so it is
unit-testable headless; prefs.py owns the widgets and collects the text.
"""

from __future__ import annotations


def matches(query: str, text: str) -> bool:
    """Does *text* contain every word of *query*? An empty query matches all."""
    haystack = text.casefold()
    return all(word in haystack for word in query.casefold().split())
