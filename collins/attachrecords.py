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

The third feed is this module's own grammar (see `scan`): the transcript is
read for image paths and URLs the session merely *mentioned*, which is how
an image that was never lightboxed at all still reaches the panel, and how
a picture clicked in the terminal — recorded with no caption, because a
click has none to give — gets the sentence it was printed in as a label.

Kept GTK-free and stdlib-only, like prstatus and panellayout, so the merge
rules, the ordering, the cap and the grammar are all exercised in the test
suite (CI has no GTK stack — see tests/conftest.py). Everything that
reaches this module from disk is untrusted input: these records started
life as agent output, and a restored key is later handed to a file
launcher, so validation on the way back in is not optional — a malformed
entry is dropped silently, the way prstatus.from_record drops a PR it can't
vouch for.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
import urllib.parse
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from . import editorfiles, linkpatterns

log = logging.getLogger(__name__)

# Records kept per session. A gallery is a glance, not an archive: 100 rows
# is already more scrolling than anyone does, and the cap is what bounds
# what a long-running session adds to state.json (~30KB at worst).
MAX_RECORDS = 100
# Struck-off records (see `strike`) are counted separately, so that removing
# a row never costs a row: sharing one budget would have a session that
# struck fifty images showing fifty fewer. They are cheap to keep — a
# tombstone is a key and two dates — and a deliberate act to make, so this
# is generous. Past it the oldest tombstone goes, and the image it was
# hiding comes back if the transcript still mentions it.
MAX_STRUCK = 50
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
    hidden: bool = False  # struck off the list by hand; kept as a tombstone

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

    Every refusal says so at debug level. From the lightbox capture points
    there is nothing here that can fire — those keys are absolute by
    construction — but `scan` hands this function whatever a transcript
    said, and the first thing anyone asks about a gallery is why a picture
    they remember seeing isn't in it. A silent drop is no answer to that.
    """
    if not isinstance(key, str):
        return _refused(key, "not a string")
    key = key.strip()
    if not key:
        return _refused(key, "empty")
    if kind not in KINDS:
        return _refused(key, f"unknown kind {kind!r}")
    if source not in SOURCES:
        return _refused(key, f"unknown source {source!r}")
    remote = is_remote(key)
    if not remote:
        if not os.path.isabs(key):
            return _refused(key, "a relative path can't be reopened later")
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


def _refused(key: object, why: str) -> None:
    """Say why a sighting was dropped, and drop it."""
    log.debug("attachment ignored (%s): %r", why, key)
    return None


def merged(old: Attachment, new: Attachment) -> Attachment:
    """*old* updated by a fresh sighting of the same image.

    Recency moves, first-sighting doesn't, and the two labels follow the
    rule the module docstring gives: a caption may only be written by a
    lightbox sighting and is never cleared by one that has none (the same
    image shown twice, the second time captionless, keeps the caption it
    was given); a context snippet fills an empty slot and is then left
    alone, since the first mention of a path is the one that introduced it.

    Being struck off survives every later sighting, which is what makes the
    panel's "Remove From List" mean anything at all: the transcript that
    mentioned an image mentions it again on the next scan, and again in
    tomorrow's run when the file is read from the top, so a record that
    merely vanished would be back within seconds.
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
        hidden=old.hidden or new.hidden,
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


def strike(
    attachments: Mapping[str, Attachment], keys: Container[str]
) -> dict[str, Attachment]:
    """*attachments* with everything in *keys* marked struck off the list.

    A tombstone rather than a deletion, and it has to be: the sighting that
    put the row there is still in the transcript, so an image that was only
    removed would come back on the next scan of it. The record stays in the
    file, marked, and the panel passes over it.

    What it keeps is what it needs to go on refusing that image — the key
    and its dates. The caption and the snippet go: nothing will show them
    again, and a tombstone nobody reads should be the cheapest record in
    the file, since it is kept on its own budget (MAX_STRUCK) so that
    striking a row never pushes a listed one out.

    Re-capped on the way out, like every other collection here: a record
    that has just become a tombstone gives its place in the listed budget
    back on the spot, rather than at the next restore.
    """
    return _ordered(
        {
            key: replace(one, hidden=True, caption=None, context=None, origin=None)
            if key in keys and not one.hidden
            else one
            for key, one in attachments.items()
        }
    )


def visible(attachments: Iterable[Attachment]) -> list[Attachment]:
    """Just the records the panel shows — everything not struck off."""
    return [one for one in attachments if not one.hidden]


def _ordered(attachments: Mapping[str, Attachment]) -> dict[str, Attachment]:
    """Newest sighting first, capped. Ties break on first sighting and then
    on key, so the same set always serializes to the same bytes — an order
    that wobbled would rewrite state.json on every poll.

    The two kinds of record are capped against their own budgets and then
    put back in one recency order: what the panel shows is never crowded
    out by what it is hiding.
    """
    ranked = sorted(attachments.values(), key=lambda one: (-one.last, -one.at, one.key))
    kept = {one.key for one in [one for one in ranked if not one.hidden][:MAX_RECORDS]}
    kept |= {one.key for one in [one for one in ranked if one.hidden][:MAX_STRUCK]}
    return {one.key: one for one in ranked if one.key in kept}


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
    if one.hidden:
        record["hidden"] = True
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
        hidden=record.get("hidden") is True,
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


# -- reading the transcript ---------------------------------------------------

# References are looked for with the terminal's own two grammars, so what
# the panel notices in a message is what the terminal would have underlined
# in the output. URLs go first: FILE_PATTERN's lookbehind already keeps the
# path grammar from starting inside a URL, and matching the two in one pass
# settles any remaining argument about which reading wins.
_REFERENCE_RX = re.compile(
    f"(?P<url>{linkpatterns.URL_PATTERN})|(?P<path>{linkpatterns.FILE_PATTERN})"
)

# Claude Code's `:line[:col]` suffix. It has to come off before the suffix
# check, or `shot.png:12` isn't a png.
_LINE_SUFFIX_RX = re.compile(r":\d+(?::\d+)?$")

# How many image-shaped paths one message may have checked against the disk.
# A message naming more than this is a directory listing rather than a
# conversation, and each candidate past the cap costs a stat call on the
# update thread to win a row that MAX_RECORDS would drop anyway. It is a
# budget for filesystem calls and nothing else: a URL spends none of it and
# so is never held back by it.
MAX_SCAN_CANDIDATES = 200


def scan(
    text: object, *, roots: Iterable[str | None] = (), now: float | None = None
) -> list[Attachment]:
    """Every image *text* mentions, as transcript sightings.

    A transcript is not a list of images; it is prose that happens to name
    some, alongside paths that were proposed and never written, paths in
    other people's repositories, and paths inside sentences about what a
    path is. So a local reference only becomes a record if it names an
    image format the viewer can open *and* something is at that path right
    now — the check that keeps the panel from filling with pictures that
    never existed. Relative paths are tried against *roots* (the message's
    own cwd, in practice), because that is what they meant when they were
    written.

    Remote references are held to the URL's own suffix. Nothing else is
    knowable without fetching it, and a grammar looser than that would put
    every link in the session into the gallery — an image URL wearing no
    suffix still arrives through `show_image`, which knows what it fetched.

    Each sighting carries the line it was found on as its context (see
    `_snippet`), which is the label an image gets when nobody ever captioned
    it. *now* stamps the sightings — pass the message's own timestamp, or a
    session's whole history lands in the panel as if it had all just
    happened.
    """
    if not isinstance(text, str) or not text:
        return []
    trials = [root for root in roots if root]
    # Every image-shaped reference first, because each one's snippet elides
    # all of them: a line naming two pictures would otherwise label each row
    # with the *other* one's path, which reads as a caption pointing at the
    # wrong file.
    named = [match for match in _REFERENCE_RX.finditer(text) if _names_an_image(match)]
    spans = [_widened(text, match.span()) for match in named]
    seen: dict[str, Attachment] = {}
    checked = 0
    for index, match in enumerate(named):
        url, path = match.group("url"), match.group("path")
        written = url or path  # the reference as the message spelled it
        if url is not None:
            key = url if is_remote(url) else None
        elif checked >= MAX_SCAN_CANDIDATES:
            continue  # the budget below is spent, but a URL costs none of it
        else:
            checked += 1
            if checked == MAX_SCAN_CANDIDATES:
                log.debug("stat budget spent after %d image paths in one message", checked)
            resolved = linkpatterns.resolve_file_reference(path, trials)
            key = resolved[0] if resolved is not None else None
        if key is None:
            # Reached only by something that *looked* like an image, so it
            # is worth a line: a path nothing is at, or a reference in a
            # scheme that can't be fetched.
            log.debug("image reference resolved nowhere: %r", written)
            continue
        one = sighting(
            key,
            source=TRANSCRIPT,
            context=_snippet(text, spans[index], spans),
            # Only a remote record carries an origin (it is what the lightbox
            # puts in its title bar); a local one already *is* its path, and
            # the relative spelling it was written in helps nobody later.
            origin=url,
            now=now,
        )
        if one is None:
            continue
        old = seen.get(one.key)
        seen[one.key] = one if old is None else merged(old, one)
        if len(seen) >= MAX_RECORDS:
            break  # a whole list's worth from one message; the cap decides
    return list(seen.values())


def _names_an_image(match: re.Match) -> bool:
    """Whether a reference is image-shaped at all — the cheap check that
    keeps the rest of the pass off the ordinary paths and links a transcript
    is mostly made of."""
    url, path = match.group("url"), match.group("path")
    if url is not None:
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:  # a malformed port, an unbalanced IPv6 bracket, …
            return False
        return PurePosixPath(parsed.path).suffix.lower() in editorfiles.IMAGE_SUFFIXES
    return editorfiles.is_image_path(_LINE_SUFFIX_RX.sub("", path))


# Punctuation a sentence hangs off a reference — "…as shot.png, which…",
# "…in shot.png." Taken out along with the reference it is attached to:
# left behind it strands as " ." between the words either side, which reads
# as a typo rather than as the mark it was.
_HANGING = ".,;:!?)]}"
# And the brackets a reference is put inside, which are only there to hold
# it: "(see out/shot.png)" is a whole aside about a picture.
_HOLDING = "([{"


def _widened(text: str, span: tuple[int, int]) -> tuple[int, int]:
    """*span* grown over the punctuation hanging off either end of it."""
    start, end = span
    while end < len(text) and text[end] in _HANGING:
        end += 1
    while start > 0 and text[start - 1] in _HOLDING:
        start -= 1
    return start, end


def _snippet(
    text: str, span: tuple[int, int], elided: Iterable[tuple[int, int]]
) -> str | None:
    """What was said around the reference at *span*, or None when nothing
    was.

    The line it sits on, with every reference in *elided* taken out of it:
    the path is already the row's tooltip and half the time its label, so
    repeating it in the caption slot says nothing, and a *sibling* path left
    in would be worse than nothing. What survives is the sentence that
    introduced the picture — "the failing state looks like", "here's the
    mockup" — which is the whole point.

    A line longer than the panel can show is trimmed from both ends towards
    where the reference was, rather than from the tail: the words nearest a
    reference are the ones about it, and the front of a long line is
    frequently a log prefix or a tool name.
    """
    start, end = span
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    head, tail = text[line_start:start], text[end:line_end]
    # Right to left, so the cuts already made don't move the ones to come.
    for other_start, other_end in sorted(elided, reverse=True):
        if line_start <= other_start and other_end <= start:
            head = f"{head[: other_start - line_start]} {head[other_end - line_start :]}"
        elif end <= other_start and other_end <= line_end:
            tail = f"{tail[: other_start - end]} {tail[other_end - end :]}"
    before = " ".join(head.split())
    after = " ".join(tail.split())
    room = MAX_TEXT - 1  # the space that joins the two halves
    if len(before) + len(after) > room:
        share = room // 2
        if len(before) <= share:
            after = _head(after, room - len(before))
        elif len(after) <= share:
            before = _tail(before, room - len(after))
        else:
            before, after = _tail(before, room - share), _head(after, share)
    return _text(f"{before} {after}")


def _head(text: str, room: int) -> str:
    """The first *room* characters of *text*, ended on a word where one is
    near enough to the cut to be worth backing up to."""
    if len(text) <= room:
        return text
    cut = text[: max(room - 1, 0)]
    space = cut.rfind(" ")
    if space > room // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _tail(text: str, room: int) -> str:
    """The last *room* characters of *text*, started on a word boundary on
    the same terms as `_head`."""
    if len(text) <= room:
        return text
    cut = text[len(text) - max(room - 1, 0) :]
    space = cut.find(" ")
    if 0 <= space < room // 2:
        cut = cut[space + 1 :]
    return "…" + cut.lstrip()


def _stamp(value: object) -> float:
    """A unix timestamp read off disk, or 0.0 when it isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number
