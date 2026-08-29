# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""A quiet repair for an expired Claude Code login.

The sidebar usage panel and the model pickers both ride the OAuth token the
claude CLI keeps in ``~/.claude/.credentials.json`` (see usage/claudemodels),
and neither ever refreshes it — that is the CLI's job, done at the start of
any run. So a machine that sat overnight comes up with the app running, the
CLI installed and logged in, and both features reporting an expired login
that any one `claude` run would have fixed.

This module is that run. When the token is found dead — at launch, past
its expiry in the file (`maybe_start`), or mid-run, when a usage fetch
comes back refused because the app outlived its token or the token died
unexpired (`maybe_repair`) — a tiny headless ``claude -p`` prompt executes
in titles' scratch directory: the same carve-out every title and icon run
uses, so it never shows up as a session and its transcript is swept away
with the workdir. The CLI refreshes the token before answering, which is
the entire point: the reply is discarded, and what decides success is the
credentials file afterwards. On success the app re-asks what the stale
token spoiled — the model catalog, when a query already failed, and the
usage panel's bars (the caller's side, via the entries' callback).
Attempts are single-flight and cooled down, and the cooldown doubles with
every consecutive failure (an hour, then two, four... up to a day), so a
login no run can fix (a revoked refresh token) costs a handful of throwaway
subprocesses a day, not one per panel poll.

Deliberately narrow. No credentials file at all means not logged in, which
a headless run cannot fix — it would only fail slower; a token inside its
expiry is trusted even though it could in principle be revoked. The run
spends the user's quota without a prompt from them, so it has a switch —
the *Auto-renew the Claude login* row of the Token use preferences
(``auto_renew_login``, on by default) — that both entries honor, leaving
the usage panel to say the login expired and to name `claude` as the fix.
The switch is first disclosed by the welcome dialog (see welcome), and no
run may precede that disclosure: until the dialog has been answered
(``welcome_seen``) both entries refuse too. The gate lives here, beside the
switch, rather than in the callers, because the callers are not one place —
the app's launch check waits on the dialog by sequencing, but a usage panel
maps under the open dialog and asks for a repair the moment its first fetch
is refused. And the screenshot/e2e harness's fixture switch
(``COLLINS_USAGE_FIXTURE``) turns the whole check off, for the same reason
the switch exists: a throwaway harness instance must never spend real
tokens.

GTK-free, like usage and claudemodels, so the check and the runner are
unit-testable headless.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

from . import claudemodels, clisetup, state, titles, usage, welcomegate

log = logging.getLogger(__name__)

# The preference that allows a throwaway run at all (see `enabled`).
SETTING = "auto_renew_login"

# The refresh happens during the CLI's startup, but success is only read off
# the credentials file after the run — so the timeout must cover a whole
# (tiny) turn, and a cold CLI start plus one Haiku word can still be slow.
_TIMEOUT_S = 120
# The cheapest run that is still a real one.
_PROMPT = "Reply with the single word: ok"
# The CLI's own tier alias, chosen without a models query — asking the
# Models API is exactly what doesn't work yet.
_MODEL = "haiku"


def enabled() -> bool:
    """Whether a throwaway run is allowed at all: the Token use switch is
    on, and the welcome dialog that discloses it has been answered.

    Read off a fresh AppState rather than one handed in, the way a title
    run reads its model (titles._run_claude): the entries are called from
    the app and from any usage panel, and a preference flipped a moment ago
    — or the welcome answered a moment ago, whose `then` is the launch
    check itself — has to govern the very next attempt whichever of them
    makes it.
    """
    settings = state.AppState()
    return bool(settings.get_setting(SETTING)) and bool(settings.get_setting(welcomegate.SEEN_SETTING))


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


# One repair at a time, and not too often. `_running` is the single-flight
# gate — the launch check and any number of usage panels can all notice the
# same dead login at once, and one throwaway run serves them all. The
# cooldown is the loop-breaker for a login that repairs never fix (a revoked
# refresh token, say): every poll of a broken panel reports the same error,
# and without it each report would spawn another subprocess. It starts at an
# hour and doubles with every consecutive failure, up to a day — a login
# that stays broken for a week is a few runs a day, not one an hour — and a
# success sets it back to the hour. A *working* login never feels the
# cooldown at all: a successful repair means the next fetches succeed, and
# nothing triggers again until the token actually expires, hours past it.
_REPAIR_COOLDOWN_S = 3600  # after a success, or the first failure
_REPAIR_COOLDOWN_MAX_S = 24 * 3600  # the doubling stops here
_attempt_lock = threading.Lock()
_running = False
_last_attempt: float | None = None  # time.monotonic() of the last claim
_failures = 0  # consecutive failed attempts; each one doubles the cooldown


def _cooldown_s() -> float:
    """How long the slot stays claimed after the last attempt: an hour,
    doubled for every consecutive failure past the first, capped at a day."""
    return min(_REPAIR_COOLDOWN_S * 2 ** max(0, _failures - 1), _REPAIR_COOLDOWN_MAX_S)


def _slot_free() -> bool:
    """Whether the repair slot looks claimable: no repair running, cooldown
    passed. Takes no lock and claims nothing — `_begin_attempt` calls it
    under the lock to decide for real, and `_spawn` peeks lock-free (cheap
    enough for the main loop) before paying for a thread. The lock-free
    peek is only advisory: two peeks can both say yes, and the claim
    settles who actually goes."""
    if _running:
        return False
    return _last_attempt is None or time.monotonic() - _last_attempt >= _cooldown_s()


def _begin_attempt() -> bool:
    """Claim the one repair slot: False while a repair is already running or
    the cooldown since the last claim hasn't passed. Claiming starts the
    cooldown — a failed attempt counts, that being the whole point — and
    how long it lasts is settled when the attempt ends (`_settle`)."""
    global _running, _last_attempt
    with _attempt_lock:
        if not _slot_free():
            return False
        _running, _last_attempt = True, time.monotonic()
        return True


def _settle(ok: bool) -> None:
    """Release the claim, and let the outcome set the next cooldown: a
    failure doubles it (and says so in the log, since an hourly retry that
    quietly became a daily one is otherwise a mystery), a success resets
    it. Not a lock-free counter: a claim can only follow a release, so the
    count and the flag move together."""
    global _running, _failures
    with _attempt_lock:
        _running = False
        _failures = 0 if ok else _failures + 1
        if not ok:
            log.info(
                "tokenrefresh: %d consecutive failed repair(s); next attempt in %.0fh at the earliest",
                _failures,
                _cooldown_s() / 3600,
            )


def _repair(on_refreshed: Callable[[], None]) -> None:
    """The claimed attempt: refresh, retry what the stale token spoiled,
    tell the caller. Only ever runs holding the `_begin_attempt` claim."""
    ok = False
    try:
        ok = refresh()
        if not ok:
            return
        # A models query that already failed this run left the pickers on a
        # stale (or empty) list and the failure backoff serving it; retry
        # now that the token works. A run where nothing asked yet needs no
        # retry — its first ask will use the fresh token.
        if claudemodels.cache_failed():
            claudemodels.refresh_models()
        on_refreshed()
    finally:
        _settle(ok)


def _spawn(check: Callable[[], bool], on_refreshed: Callable[[], None]) -> threading.Thread | None:
    """The shared entry shape: gate on the fixture harness, a doomed
    attempt and the preference (with the welcome that discloses it), then
    run *check* + claim + repair on a daemon thread. Returns the thread, or
    None when nothing was started (the fixture harness, a slot certain to
    refuse, the switch off, or the welcome still up) — callers need
    neither; tests join.

    The peek keeps every-poll callers honest: during a broken login's
    cooldown, `maybe_repair` costs a couple of reads, not a thread spawned
    to be told no. The settings are read last, so the state file is only
    parsed when a run is otherwise about to happen. *check* still runs on
    the thread — it does file and PATH I/O that doesn't belong on the main
    loop — and the claim is still the worker's first act, so peek races
    resolve there.
    """
    if os.environ.get("COLLINS_USAGE_FIXTURE") or not _slot_free() or not enabled():
        return None

    def work() -> None:
        if not check() or not _begin_attempt():
            return
        _repair(on_refreshed)

    thread = threading.Thread(target=work, name="token-refresh", daemon=True)
    thread.start()
    return thread


def maybe_start(on_refreshed: Callable[[], None]) -> threading.Thread | None:
    """The launch-time entry: check, and repair if called for, off-thread.

    *on_refreshed* fires on the worker thread after a successful refresh —
    with the model catalog already retried, so by the time the caller hears
    about it the pickers' next ask is served fresh — and never fires
    otherwise. The caller marshals to its main loop and re-asks whatever it
    was showing as expired (the usage panel).
    """

    # No CLI is the welcome dialog's business, not a repair to attempt — and not
    # a warning to log on every launch of a CLI-less install.
    return _spawn(lambda: clisetup.on_path() and token_expired(), on_refreshed)


def maybe_repair(on_refreshed: Callable[[], None]) -> threading.Thread | None:
    """The mid-run entry: a usage fetch was just refused for login reasons.

    An app left running rides its token past expiry with no launch to
    notice — the panel's poll is what finds out — and a token can also die
    unexpired (revoked server-side, the fetch's `auth` kind), which the
    local file never shows. So unlike `maybe_start` this trusts the
    caller's observed error over `token_expired` and attempts regardless of
    what the file claims. The caller decides what counts as a login refusal
    (the usage panel: its `expired` and `auth` error kinds).

    Safe to call on every such failure: attempts are single-flight and
    cooled down (`_begin_attempt`), with the cooldown doubling on every
    consecutive failure (`_settle`), so a login that repairs can't fix costs
    an hourly throwaway run tapering to a daily one, not one per poll — and
    a call inside the cooldown is refused before it even spawns a thread
    (`_spawn`'s peek).
    *on_refreshed* is as in `maybe_start`: worker thread, successful
    refresh only.
    """
    return _spawn(clisetup.on_path, on_refreshed)
