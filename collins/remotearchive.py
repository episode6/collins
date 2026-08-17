# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Mirror the archive toggle to claude.ai's Claude Code session list.

A session that has been remote-controlled (or teleported into) from claude.ai
has a page there too, and archiving it in Collins alone leaves that page
behind on the web's session list. This module carries the local toggle over:
archive here, archive there — and restore likewise, since the local archive
is a toggle.

Strictly best-effort, by design. The local archive has already landed by the
time this runs; everything here happens on a daemon thread, and every failure
— no remote counterpart, no credentials, an expired token, a dead network, an
HTTP error — is logged and swallowed. Nothing ever blocks or reverts the
local toggle.

The endpoint is the Claude Code CLI's own (undocumented) session API: the
transcript's ``bridge-session`` record names the remote session id, and
``POST /v1/code/sessions/<id>/archive`` (or ``/unarchive``) flips it, on the
same OAuth token the CLI stores in ``~/.claude/.credentials.json`` (read via
``usage.read_credentials``, which never refreshes anything). 409 means the
session is already in the requested state — success, same as the CLI treats
it.

Widget-free by design (no ``gi`` imports) so the scanner and syncer are unit
testable, mirroring ``usage.py``: the HTTP transport is injectable and the
credentials path inherits usage.py's ``COLLINS_CLAUDE_CREDENTIALS`` override.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from .usage import UsageError, read_credentials

log = logging.getLogger(__name__)

_SESSIONS_URL = "https://api.anthropic.com/v1/code/sessions"
_HTTP_TIMEOUT_S = 15

# Remote session ids as the bridge-session record carries them (cse_...).
# Anything else never reaches a URL.
_BRIDGE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def bridge_session_id(jsonl_path: Path) -> str | None:
    """The remote session id recorded in *jsonl_path*, or None.

    A transcript that has been remote-controlled carries a
    ``{"type": "bridge-session", "bridgeSessionId": "cse_..."}`` record; one
    that never was carries none, which is the common case and means there is
    nothing to sync. The last record wins, matching how the CLI re-registers.
    """
    found: str | None = None
    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"bridge-session"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "bridge-session":
                    continue
                candidate = record.get("bridgeSessionId")
                if isinstance(candidate, str) and _BRIDGE_ID.match(candidate):
                    found = candidate
    except OSError as err:
        log.debug("remotearchive: cannot read %s: %s", jsonl_path, err)
        return None
    return found


def _http_post(url: str, headers: dict[str, str]) -> int:
    """POST an empty JSON body; the response status code is the answer."""
    request = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code


def sync_archived(
    jsonl_path: Path,
    archived: bool,
    transport: Callable[[str, dict[str, str]], int] = _http_post,
    get_token: Callable[[], str] | None = None,
) -> bool:
    """Archive (or restore) *jsonl_path*'s remote counterpart. Never raises.

    True means the remote side now matches — a 200, or a 409 saying it
    already did. False is every way there was nothing to do or it didn't
    work, each logged at a volume matching how expected it is.

    *get_token* supplies the OAuth token and may raise ``UsageError`` like
    the default (a fresh ``read_credentials``); the worker passes a memoized
    one so a bulk archive reads the credentials file once, and either way it
    is only consulted after a remote counterpart turns up.
    """
    try:
        session_id = bridge_session_id(jsonl_path)
        if not session_id:
            return False
        if get_token is not None:
            token = get_token()
        else:
            token, _subscription = read_credentials()
        verb = "archive" if archived else "unarchive"
        status = transport(
            f"{_SESSIONS_URL}/{session_id}/{verb}",
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )
        if status in (200, 409):
            log.info("remotearchive: %sd %s on claude.ai", verb, session_id)
            return True
        log.warning("remotearchive: %s of %s failed: HTTP %s", verb, session_id, status)
    except UsageError as err:
        # No credentials or an expired login: normal on machines that never
        # signed in, and the panel already reports expiry — stay quiet.
        log.debug("remotearchive: skipped (%s): %s", err.kind, err)
    except Exception:
        log.warning("remotearchive: sync failed for %s", jsonl_path, exc_info=True)
    return False


# The sync queue: the newest desired state per transcript, drained in toggle
# order by a single worker so requests for one session can never interleave
# on the wire (an archive whose HTTP call outlives a quick Undo would
# otherwise land last and leave claude.ai opposite to the final local state).
_lock = threading.Lock()
_pending: dict[Path, bool] = {}
_worker: threading.Thread | None = None


def sync_archived_async(jsonl_paths: Iterable[Path], archived: bool) -> None:
    """Fire-and-forget batch sync through the single worker thread.

    The caller's archive is already done; this only chases the remote side,
    so nothing is returned and nothing is waited on. Sessions that were never
    remote-controlled — the common case — cost one transcript scan and no
    network at all.

    Ordering is the whole design: every sync drains through one worker, and
    a transcript toggled again while still queued just has its pending state
    replaced — so archive-then-Undo can't race itself, and the last toggle
    always wins on the remote side too.
    """
    paths = list(jsonl_paths)
    if not paths:
        return
    global _worker
    with _lock:
        for path in paths:
            # Re-queue at the end so the drain order follows the toggles.
            _pending.pop(path, None)
            _pending[path] = archived
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_drain, name="remote-archive", daemon=True)
            _worker.start()


def _memoized_token() -> Callable[[], str]:
    """One credentials read for a whole drain, success and failure alike.

    Still lazy: nothing is read until a transcript with a remote counterpart
    actually needs the token, so a batch with none never touches the
    credentials file. A failed read is remembered too — re-raised per
    session so sync_archived logs each skip, without re-reading the file.
    """
    result: list[object] = []

    def get() -> str:
        if not result:
            try:
                token, _subscription = read_credentials()
                result.append(token)
            except Exception as err:
                result.append(err)
        value = result[0]
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]

    return get


def _drain() -> None:
    global _worker
    get_token = _memoized_token()
    while True:
        with _lock:
            if not _pending:
                _worker = None
                return
            path = next(iter(_pending))
            archived = _pending.pop(path)
        sync_archived(path, archived, get_token=get_token)
