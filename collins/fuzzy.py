# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""GTK-free fuzzy scoring for the editor's quick-open dialog.

Subsequence matching over project-relative paths, tuned for how people type
when hunting a file: characters must appear in order, matches on the file
name beat matches buried in directories, and starts of path segments and
words beat mid-word hits. Kept free of GTK (like editorfiles.py) so it is
unit-testable headless; quickopen.py owns the widgets.
"""

from __future__ import annotations

_WORD_SEPARATORS = frozenset("/_-. ")

# Relative weights. Only their ordering really matters: a name-start match
# must outrank a consecutive run, which must outrank a scattered one, with
# shallow paths breaking ties.
_BONUS_BASENAME = 60  # match begins in the file name, not a directory
_BONUS_SEGMENT_START = 30  # match char sits right after / _ - . or space
_BONUS_CONSECUTIVE = 12  # match char directly follows the previous one
_PENALTY_GAP = 1  # per skipped candidate char inside the match window
_PENALTY_DEPTH = 2  # per directory level, so `app.py` beats `x/y/app.py`


def match(query: str, path: str) -> int | None:
    """Score *query* against *path*, or None when it doesn't match at all.

    Case-insensitive subsequence match; higher scores are better. An empty
    query matches everything with a score favouring shallow paths, which is
    what an just-opened quick-open dialog should show.
    """
    if not query:
        return -_PENALTY_DEPTH * path.count("/")
    haystack = path.lower()
    needle = query.lower()
    base_start = path.rfind("/") + 1

    # Greedy left-to-right walk. Not exhaustive best-alignment (that is
    # quadratic and not worth it for a type-ahead list) — but re-anchored
    # once: if the whole query fits inside the basename, score that
    # alignment instead, so "edit" prefers editor.py over e/d/i/t scatter.
    score = _walk(needle, haystack, 0, base_start)
    if base_start:
        base_score = _walk(needle, haystack, base_start, base_start)
        if base_score is not None and (score is None or base_score > score):
            score = base_score
    if score is None:
        return None
    return score - _PENALTY_DEPTH * path.count("/")


def _walk(needle: str, haystack: str, start: int, base_start: int) -> int | None:
    score = 0
    prev_hit = -2
    pos = start
    for ch in needle:
        hit = haystack.find(ch, pos)
        if hit == -1:
            return None
        if prev_hit == -2 and hit == base_start:
            score += _BONUS_BASENAME  # the match starts exactly at the file name
        if hit == 0 or haystack[hit - 1] in _WORD_SEPARATORS:
            score += _BONUS_SEGMENT_START
        elif hit == prev_hit + 1:
            score += _BONUS_CONSECUTIVE
        else:
            score -= _PENALTY_GAP * (hit - pos)
        prev_hit = hit
        pos = hit + 1
    return score


def rank(query: str, paths: list[str], limit: int) -> list[str]:
    """The best *limit* of *paths* for *query*, best first. Ties keep the
    caller's order (walk order: shallow directories first), which keeps the
    list stable while more of the index streams in."""
    scored = []
    for index, path in enumerate(paths):
        s = match(query, path)
        if s is not None:
            scored.append((-s, index, path))
    scored.sort()
    return [path for _s, _i, path in scored[:limit]]
