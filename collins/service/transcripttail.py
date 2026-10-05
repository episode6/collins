# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The links in a transcript's tail, the service's half of `transcriptlinks`
(split-service spec §3.23, PR-2.6).

Finishing a hard-wrapped link off the transcript used to read the session's
``.jsonl`` on the main loop from the client. The transcript is the
service's file now, so the read is `store.transcript-tail {session}`: the
service parses the last `transcriptlinks.TAIL_BYTES` (2 MiB) and answers
the URL- and path-shaped tokens of its strings, bounded to what the reply
may carry (`bound`); the client's `transcriptlinks.completions` still
decides which of them the screen around a click corroborates.

One parse per transcript version: a small cache keyed by path holds the
links of the (size, mtime) they came from, so a run of clicks on one
session reads the file once. GTK-free; the read runs on a worker thread
(`service.files.Files._later`) and never on the main loop.
"""

from __future__ import annotations

import json
import os
import threading

from ..api import protocol
from ..transcriptlinks import TAIL_BYTES, harvest_links, message_strings

# How many transcripts' links the cache holds (one per session a person
# is clicking in; a service hosts a handful of live ones). Least
# recently used out first (a dict keeps its insertion order; a hit and a
# store both move the path to the end).
CACHE_ENTRIES = 8

_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], list[str]]] = {}


def read_links(path: str) -> list[str]:
    """Every URL- or path-shaped token in the strings of the last
    TAIL_BYTES of the transcript at *path*, deduplicated, in order of
    first appearance, bounded (`bound`). Empty when the file cannot be
    read. The tail starts mid-line when the file is longer; that line
    fails to parse and is skipped."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    stamp = (st.st_size, st.st_mtime_ns)
    with _lock:
        cached = _cache.get(path)
        if cached is not None:
            _cache[path] = _cache.pop(path)  # most recently used goes last
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, "rb") as fh:
            if st.st_size > TAIL_BYTES:
                fh.seek(st.st_size - TAIL_BYTES)
            data = fh.read(TAIL_BYTES + 1)
    except OSError:
        return []
    links = bound(harvest_links(message_strings(data)))
    with _lock:
        _cache.pop(path, None)
        if len(_cache) >= CACHE_ENTRIES:
            _cache.pop(next(iter(_cache)))  # the least recently used
        _cache[path] = (stamp, links)
    return links


def bound(links: list[str]) -> list[str]:
    """*links* cut to what `store.transcript-tail`'s reply may carry: a link
    over TRANSCRIPT_LINK_MAX characters is left out (a longer one is not a
    link the screen could show whole), and of the rest the most recent ones
    are kept, at most TRANSCRIPT_LINKS_MAX and TRANSCRIPT_LINKS_BYTES of
    their JSON, in their original order. The screen shows the transcript's
    end, so what the cut drops is the oldest."""
    kept: list[str] = []
    total = 0
    for link in reversed(links):
        if len(link) > protocol.TRANSCRIPT_LINK_MAX:
            continue
        try:
            size = len(json.dumps(link))
        except (TypeError, ValueError):
            continue  # not text a frame can carry
        if len(kept) >= protocol.TRANSCRIPT_LINKS_MAX or total + size > protocol.TRANSCRIPT_LINKS_BYTES:
            break
        kept.append(link)
        total += size
    kept.reverse()
    return kept
