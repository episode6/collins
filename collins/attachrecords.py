# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The images a session has seen, as a list that outlives the app run.

Images flow through a session constantly — screenshots the agent captured,
pictures it `show_image`d, files dragged into the prompt, URLs it was handed
— and the moment they scroll off the terminal there is no way back to them
short of hunting through the scrollback for a path. So every image the
session puts on screen is written down as a record here, keyed by where it
came from, and the list is persisted per session (see
AppState.set_session_attachments) exactly the way the session's PRs are.

The record is a log of what the session *saw*, not a claim about what is
still there: a file that has since been deleted keeps its entry (the panel
that will show these draws a broken-image stand-in for it), and a remote
image is keyed by its URL rather than by the cache file it landed in —
those are pruned after a day, so a cache path on disk is a path that will
soon point at nothing.

Two kinds of sighting feed the same record, and the difference matters for
captions. A lightbox sighting is an image the session deliberately showed,
and `show_image` may have carried a caption with it, which is the best
label there will ever be for that picture. A transcript sighting is a path
or URL noticed in message text, whose only label is the sentence it was
mentioned in. So a caption from a lightbox always wins and never degrades
to nothing, while a context snippet only ever fills a slot that is still
empty — which is what backfills a label for an image that was clicked in
the terminal, where no caption exists at all.

Kept GTK-free and stdlib-only, like prstatus and panellayout, so the merge
rules, the ordering and the cap are all exercised in the test suite (CI has
no GTK stack — see tests/conftest.py). Everything that reaches this module
from disk is untrusted input: these records started life as agent output,
and a restored key is later handed to a file launcher, so validation on the
way back in is not optional — a malformed entry is dropped silently, the
way prstatus.from_record drops a PR it can't vouch for.
"""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

# Records kept per session. A gallery is a glance, not an archive: 100 rows
# is already more scrolling than anyone does, and the cap is what bounds
# what a long-running session adds to state.json (~30KB at worst).
MAX_RECORDS = 100
# Captions and context snippets are one ellipsized line in the panel, so
# what is stored is what could plausibly be read. It also keeps a novel of
# a caption from being written to disk 100 times over.
MAX_TEXT = 160

# The only kind today. "file" is the one the door is left open for — the
# panel is images-only until there is a story for which files belong in it
# — and an unknown kind read back from disk is dropped rather than shown.
KINDS = frozenset({"image"})
# Where a sighting came from, which is what decides whether it may write a
# caption (see `merged`).
LIGHTBOX = "lightbox"
TRANSCRIPT = "transcript"
SOURCES = frozenset({LIGHTBOX, TRANSCRIPT})

_REMOTE_RE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass(frozen=True)
class Attachment:
    """One image the session saw, and everything known about the sighting.

    Frozen: the tab keeps these in a dict it replaces wholesale rather than
    mutates (an update landing from a worker thread would otherwise race a
    tool call writing into it), and every merge here returns a new one.
    """

    key: str  # absolute path (local) or the URL itself (remote)
    kind: str = "image"
    remote: bool = False
    caption: str | None = None  # what the lightbox showed, and will show again
    context: str | None = None  # transcript snippet, the fallback label
    origin: str | None = None  # what the agent named — the URL, or the path it asked for
    source: str = TRANSCRIPT
    at: float = 0.0  # first sighting (unix seconds)
    last: float = 0.0  # most recent sighting; the ordering key

    @property
    def label(self) -> str:
        """The one line to show under the preview: the real caption if there
        ever was one, else what it was mentioned alongside, else the file's
        own name — which is never nothing."""
        return self.caption or self.context or os.path.basename(self.key.rstrip("/")) or self.key


def is_remote(key: str) -> bool:
    """Whether *key* is an http(s) URL rather than a local path.

    Narrower than `remoteimages.looks_remote`, deliberately: that one takes
    any `scheme://` so `url_error` can explain why the rest are refused,
    whereas a record is only written for something that was actually shown,
    which by then is http(s) or a path.
    """
    return bool(_REMOTE_RE.match(key))


def sighting(
    key: str,
    *,
    source: str,
    caption: str | None = None,
    context: str | None = None,
    origin: str | None = None,
    kind: str = "image",
    now: float | None = None,
) -> Attachment | None:
    """One encounter with an image, or None when *key* isn't usable.

    A local key must be absolute — the panel opens these long after the cwd
    that made a relative path meaningful has been forgotten — and a remote
    one must be http(s). Callers resolve before recording; anything that
    arrives unresolved is a bug upstream, and dropping it beats writing a
    row that can never be opened.
    """
    if not isinstance(key, str):
        return None
    key = key.strip()
    if not key or kind not in KINDS or source not in SOURCES:
        return None
    remote = is_remote(key)
    if not remote:
        if not os.path.isabs(key):
            return None
        key = os.path.normpath(key)
    stamp = time.time() if now is None else float(now)
    return Attachment(
        key=key,
        kind=kind,
        remote=remote,
        caption=_text(caption),
        context=_text(context),
        origin=_text(origin, limit=None),
        source=source,
        at=stamp,
        last=stamp,
    )


def merged(old: Attachment, new: Attachment) -> Attachment:
    """*old* updated by a fresh sighting of the same image.

    Recency moves, first-sighting doesn't, and the two labels follow the
    rule the module docstring gives: a caption may only be written by a
    lightbox sighting and is never cleared by one that has none (the same
    image shown twice, the second time captionless, keeps the caption it
    was given); a context snippet fills an empty slot and is then left
    alone, since the first mention of a path is the one that introduced it.
    """
    caption = old.caption
    if new.source == LIGHTBOX and new.caption:
        caption = new.caption
    return replace(
        old,
        caption=caption,
        context=old.context or new.context,
        origin=old.origin or new.origin,
        # A picture that was actually shown outranks one merely mentioned,
        # whichever order the two sightings arrived in.
        source=LIGHTBOX if LIGHTBOX in (old.source, new.source) else old.source,
        last=max(old.last, new.last),
    )


def fold(current: Mapping[str, Attachment], *seen: Attachment) -> dict[str, Attachment]:
    """*current* with every sighting in *seen* folded in, newest first.

    Returns a new dict rather than touching the one passed in: the tab's
    live state is replaced wholesale precisely so a poll landing mid-call
    can't half-see a write. Insertion order is the display order (most
    recently seen at the head), and the cap drops from the tail, which is
    the oldest sighting by definition.
    """
    folded = dict(current)
    for one in seen:
        if one is None:
            continue
        old = folded.get(one.key)
        folded[one.key] = one if old is None else merged(old, one)
    return _ordered(folded)


def union(saved: Iterable[Attachment], live: Mapping[str, Attachment]) -> dict[str, Attachment]:
    """A restored list merged with whatever the tab has already collected.

    Restore can land after a tab has begun recording (opening a tab shows
    images before the window knows which session it is), so this is a merge
    and not an assignment: each side contributes what the other is missing,
    and an image both know about is merged the ordinary way — the live
    sighting is a newer one of the same picture.
    """
    return fold({one.key: one for one in saved}, *live.values())


def _ordered(attachments: Mapping[str, Attachment]) -> dict[str, Attachment]:
    """Newest sighting first, capped. Ties break on first sighting and then
    on key, so the same set always serializes to the same bytes — an order
    that wobbled would rewrite state.json on every poll."""
    ranked = sorted(attachments.values(), key=lambda one: (-one.last, -one.at, one.key))
    return {one.key: one for one in ranked[:MAX_RECORDS]}


def _text(value: object, limit: int | None = MAX_TEXT) -> str | None:
    """A caption/context/origin string trimmed for storage, or None."""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())  # newlines would break the one-line label
    if limit is not None and len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value or None


def to_record(one: Attachment) -> dict:
    """*one* as the JSON-safe dict that goes into AppState.

    Only what is known is written — a caption nobody gave is left out of
    the record rather than stored as null — so the file says what was
    actually true and stays readable by eye.
    """
    record: dict = {"key": one.key, "kind": one.kind, "source": one.source}
    if one.remote:
        record["remote"] = True
    if one.caption:
        record["caption"] = one.caption
    if one.context:
        record["context"] = one.context
    if one.origin:
        record["origin"] = one.origin
    record["at"] = round(one.at, 3)
    record["last"] = round(one.last, 3)
    return record


def to_records(attachments: Iterable[Attachment]) -> list[dict]:
    """The persistable records for *attachments*, in the order given."""
    return [to_record(one) for one in attachments]


def from_record(record: object) -> Attachment | None:
    """An Attachment read back from `to_record`, or None if it can't be used.

    Re-validated from scratch. Having been the process that wrote the file
    earns no shortcut: the keys in it came from agent output, and this one
    is about to be handed to a picture loader and a file launcher.
    """
    if not isinstance(record, dict):
        return None
    key = record.get("key")
    if not isinstance(key, str):
        return None
    key = key.strip()
    if not key:
        return None
    remote = is_remote(key)
    if not remote and not os.path.isabs(key):
        return None
    kind = record.get("kind")
    if kind not in KINDS:
        return None
    at = _stamp(record.get("at"))
    last = _stamp(record.get("last"))
    source = record.get("source")
    return Attachment(
        key=key if remote else os.path.normpath(key),
        kind=kind,
        remote=remote,
        caption=_text(record.get("caption")),
        context=_text(record.get("context")),
        origin=_text(record.get("origin"), limit=None),
        # An unrecognized source reads as the weaker of the two: it lets a
        # later lightbox sighting write the caption this record may be
        # missing, which is the recoverable way to be wrong.
        source=source if source in SOURCES else TRANSCRIPT,
        at=at or last,
        last=last or at,
    )


def from_records(records: object) -> list[Attachment]:
    """Every usable Attachment in a saved list, newest first and capped.

    Ordering is re-derived rather than trusted: the file's own order is the
    one that was written, but nothing guarantees a hand-edited or
    older-version file agrees with the timestamps in it, and the panel's
    "newest first" has to mean the timestamps.
    """
    if not isinstance(records, list):
        return []
    seen: dict[str, Attachment] = {}
    for record in records:
        one = from_record(record)
        if one is None:
            continue
        old = seen.get(one.key)
        seen[one.key] = one if old is None else merged(old, one)
    return list(_ordered(seen).values())


def _stamp(value: object) -> float:
    """A unix timestamp read off disk, or 0.0 when it isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number
