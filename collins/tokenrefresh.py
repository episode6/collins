# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A launch-time repair for an expired Claude Code login.

The sidebar usage panel and the model pickers both ride the OAuth token the
claude CLI keeps in ``~/.claude/.credentials.json`` (see usage/claudemodels),
and neither ever refreshes it — that is the CLI's job, done at the start of
any run. So a machine that sat overnight comes up with the app running, the
CLI installed and logged in, and both features reporting an expired login
that any one `claude` run would have fixed.

This module is that run. When a launch finds the CLI on PATH but the stored
token past its expiry, a tiny headless ``claude -p`` prompt executes in
titles' scratch directory — the same carve-out every title and icon run
uses, so it never shows up as a session and its transcript is swept away
with the workdir. The CLI refreshes the token before answering, which is
the entire point: the reply is discarded, and what decides success is the
credentials file afterwards. On success the app re-asks what the stale
token spoiled — the model catalog, when a query already failed, and the
usage panel's bars (the caller's side, via `maybe_start`'s callback).

Deliberately narrow. No credentials file at all means not logged in, which
a headless run cannot fix — it would only fail slower; a token inside its
expiry is trusted even though it could in principle be revoked. And the
screenshot/e2e harness's fixture switch (``COLLINS_USAGE_FIXTURE``) turns
the whole check off, for the same reason the switch exists: a throwaway
harness instance must never spend real tokens.

GTK-free, like usage and claudemodels, so the check and the runner are
unit-testable headless.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable

from . import claudemodels, clisetup, titles, usage

log = logging.getLogger(__name__)

# The refresh happens during the CLI's startup, but success is only read off
# the credentials file after the run — so the timeout must cover a whole
# (tiny) turn, and a cold CLI start plus one Haiku word can still be slow.
_TIMEOUT_S = 120
# The cheapest run that is still a real one.
_PROMPT = "Reply with the single word: ok"
# The CLI's own tier alias, chosen without a models query — asking the
# Models API is exactly what doesn't work yet.
_MODEL = "haiku"


def token_expired() -> bool:
    """Whether the stored token is the one thing a headless run can fix:
    present, but past its expiry. Missing credentials are not this — that is
    "not logged in", and no throwaway run logs anyone in."""
    try:
        usage.read_credentials()
    except usage.UsageError as err:
        return err.kind == "expired"
    return False


def token_valid() -> bool:
    """Whether the credentials file now holds an unexpired token — the
    after-the-run check `refresh` answers with."""
    try:
        usage.read_credentials()
    except usage.UsageError:
        return False
    return True


def refresh() -> bool:
    """One throwaway headless CLI run, for its side effect on the
    credentials file; returns whether the token is valid afterwards.

    The run's own outcome only gets logged: a nonzero exit doesn't decide
    anything, because the token refresh happens at CLI startup and can have
    landed even when the turn itself then failed (a network flake, a model
    hiccup). Blocking for up to the run's timeout — call from a worker
    thread.
    """
    cli = shutil.which(clisetup.CLI_NAME)
    if cli is None:
        log.warning("tokenrefresh: no %s on PATH to refresh with", clisetup.CLI_NAME)
        return False
    log.info("tokenrefresh: login expired — running a throwaway %s -p to refresh it", cli)
    try:
        with titles.scratch_workdir() as workdir:
            result = subprocess.run(
                [cli, "-p", "--model", _MODEL],
                input=_PROMPT,
                capture_output=True,
                text=True,
                cwd=workdir,
                timeout=_TIMEOUT_S,
            )
    except (OSError, subprocess.TimeoutExpired) as err:
        log.warning("tokenrefresh: throwaway run failed to complete: %r", err)
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        log.warning(
            "tokenrefresh: throwaway run exited %d: %s",
            result.returncode,
            detail[:200],
        )
    ok = token_valid()
    if ok:
        log.info("tokenrefresh: token refreshed")
    else:
        log.warning("tokenrefresh: token still not valid after the run")
    return ok


def maybe_start(on_refreshed: Callable[[], None]) -> threading.Thread | None:
    """The launch-time entry: check, and repair if called for, off-thread.

    *on_refreshed* fires on the worker thread after a successful refresh —
    with the model catalog already retried, so by the time the caller hears
    about it the pickers' next ask is served fresh — and never fires
    otherwise. The caller marshals to its main loop and re-asks whatever it
    was showing as expired (the usage panel).

    Returns the started thread, or None when the check is off (the fixture
    harness) — callers don't need either; tests join on it.
    """
    if os.environ.get("COLLINS_USAGE_FIXTURE"):
        return None

    def work() -> None:
        # No CLI is cliwelcome's business, not a repair to attempt — and not
        # a warning to log on every launch of a CLI-less install.
        if not clisetup.on_path() or not token_expired():
            return
        if not refresh():
            return
        # A models query that already failed this run left the pickers on a
        # stale (or empty) list and the failure backoff serving it; retry
        # now that the token works. A run where nothing asked yet needs no
        # retry — its first ask will use the fresh token.
        if claudemodels.cache_failed():
            claudemodels.refresh_models()
        on_refreshed()

    thread = threading.Thread(target=work, name="token-refresh", daemon=True)
    thread.start()
    return thread
