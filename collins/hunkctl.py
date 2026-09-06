# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Everything the git page decides without a widget: driving hunk from outside.

The git page (gitpage) is hunk — hunk.dev, the terminal diff viewer — running
in a VTE, steered over its session API: `hunk session list --json` names the
live viewers by pid, `hunk session reload <id> --json -- diff …` swaps what
one of them shows. This module is the GTK-free half of that: the version
gate behind the install card, the argv for each command (with the bundled
collins-git extension on it, see extension_dir), the runner that turns one
into a Reply, the parsers for their JSON replies (and for the stderr that
says a session is gone, as opposed to a load hunk refused), and the
mapping from hunk's own session titles back to the things the page can
load (loaded_from_title, title_tail, foreign_tab_title). What those things
*are* — the three modes "unstaged", "staged", "branch", a commit as
{"show": ref}, a range as {"range": "a...b"}, the breadcrumb and tab title
each wears, the Ctrl+1/2/3 chords, the layout slot, the show_diff tool's
`what` and file-path readings, the git calls behind a commit's name, and
the Options Preferences → Git normalises into — is gitloads' (the `Loaded`
vocabulary, split out so the native diff view can share it); every one of
its names is re-exported from here, so callers read them off hunkctl as
before. A `session get` reply also carries the loaded changeset's files in
review order (Session.files, SessionFiles with their counts and rename
pairs) and the cursor's file (Session.selected_path / selected_hunk): what
the native files list (gitmodel.files_sections) is built from, and its
fallback for following hunk's cursor. The `show_diff` session tool
(app.py's _mcp_show_diff, driving the page) keeps its hunk-side decisions
here: the `session navigate` argv, the reply the agent reads back, and
the deadline the whole call gets.

What Preferences → Git decides about hunk arrives as a gitloads.Options:
the layout (`--mode`) and theme (`--theme`) go on the spawn argv, since
nothing in hunk's session API changes them on a running viewer; the
untracked switch goes on every `diff` tail, spawn and reload alike
(`--exclude-untracked`, which hunk resolves afresh on each reload — and
which `show` refuses); the log page rides in the sidecar.

The extension and the page share a *sidecar*: a small JSON file under the
runtime dir whose path rides to hunk's child in COLLINS_GIT_STATE
(contract version 2, SIDECAR_VERSION). Collins writes the untracked
switch into it (sidecar_payload); the extension writes back `selection`
— the file and hunk under hunk's cursor, on every move — `anchor`, the
line `v` was pressed on, and `refreshed`, the index mtime and HEAD it
last reloaded the review for on its own (so the page's freshness reload
stays home for a move hunk has already shown — shown_by_extension). The
path, payload, readers (read_sidecar_selection, read_sidecar_anchor,
read_sidecar_refreshed) and writer live here, beside the keys the native
sidebar's buttons feed hunk's pty — `x`, `X`, `v`, escape, `D`, pinned
to the extension's registerCommand keys (STAGE_KEY and friends; a test
greps index.ts for them). hunk runs with `--no-sidebar` (NO_SIDEBAR_FLAG,
the reason MIN_VERSION is 0.21): its own files pane would only take
columns from the diff the native sidebar already lists. Kept importable
by the unit tests (see tests/conftest.py), which is where all of it is
exercised.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass

# The Loaded vocabulary lives in gitloads (split out so the native diff view
# can load the same things without this module); every name is re-exported
# here as it was — callers keep reading them off hunkctl. The redundant
# aliases mark the re-export for the linter.
from .gitloads import DEFAULT_LAYOUT as DEFAULT_LAYOUT
from .gitloads import DEFAULT_MODE as DEFAULT_MODE
from .gitloads import DEFAULT_VIEWER as DEFAULT_VIEWER
from .gitloads import GIT_TIMEOUT_S as GIT_TIMEOUT_S
from .gitloads import LAYOUTS as LAYOUTS
from .gitloads import LOG_PAGE as LOG_PAGE
from .gitloads import MAX_LOG_PAGE as MAX_LOG_PAGE
from .gitloads import MAX_PATH_CHARS as MAX_PATH_CHARS
from .gitloads import MAX_THEME_LEN as MAX_THEME_LEN
from .gitloads import MIN_LOG_PAGE as MIN_LOG_PAGE
from .gitloads import MODES as MODES
from .gitloads import RANGE_DOTS as RANGE_DOTS
from .gitloads import RANGE_KEY as RANGE_KEY
from .gitloads import SHOW_KEY as SHOW_KEY
from .gitloads import VIEWER_HUNK as VIEWER_HUNK
from .gitloads import VIEWER_NATIVE as VIEWER_NATIVE
from .gitloads import VIEWERS as VIEWERS
from .gitloads import Loaded as Loaded
from .gitloads import Options as Options
from .gitloads import breadcrumb as breadcrumb
from .gitloads import commit_subject as commit_subject
from .gitloads import commit_subject_and_sha as commit_subject_and_sha
from .gitloads import decode_sidebar as decode_sidebar
from .gitloads import decode_state as decode_state
from .gitloads import diff_file_path as diff_file_path
from .gitloads import encode_state as encode_state
from .gitloads import initial_mode as initial_mode
from .gitloads import is_range as is_range
from .gitloads import is_show as is_show
from .gitloads import load_for_key as load_for_key
from .gitloads import loaded_ok as loaded_ok
from .gitloads import range_halves as range_halves
from .gitloads import range_of as range_of
from .gitloads import resolve_commit as resolve_commit
from .gitloads import safe_ref as safe_ref
from .gitloads import safe_theme as safe_theme
from .gitloads import short_ref as short_ref
from .gitloads import show_diff_load as show_diff_load
from .gitloads import show_ref as show_ref
from .gitloads import tab_title as tab_title
from .i18n import _

HUNK = "hunk"
# How many files a `session get` reply may list before the rest is
# ignored: a changeset that size is beyond what a list is for, and the
# reply is parsed on every 2 s tick.
MAX_SESSION_FILES = 5000
# What the native sidebar's buttons feed hunk's pty: the collins-git
# extension's keys for stage (the hunk or the anchored range), stage the
# file, anchor a line, clear the anchor (escape) and discard. hunk's CLI
# can't run an extension command by name, so the keys are pinned — a user
# who rebinds them under `[keybindings]` finds the buttons still press
# these bytes. tests/test_hunkctl.py greps index.ts's registerCommand keys
# for exactly these.
STAGE_KEY = b"x"
STAGE_FILE_KEY = b"X"
ANCHOR_KEY = b"v"
CLEAR_ANCHOR_KEY = b"\x1b"
DISCARD_KEY = b"D"
# The anchor's sides in the sidecar (`anchor.side`): which column of the
# diff the anchored line is counted in.
ANCHOR_SIDES: tuple[str, ...] = ("old", "new")
# The hunk extension Collins ships as package data (collins/hunkext/
# collins-git): the commits and files panels and the staging keys. Handed to
# hunk with `--extension <dir>`, so nothing lands in the user's hunk config
# and no trust prompt appears.
EXTENSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hunkext", "collins-git")
# The sidecar (see the module docstring): the variable its path travels in,
# and the contract's version, stamped on every write (the extension stamps
# the same on its own). Version 1 carried the parent and default branch
# names, the page size and the narrow-page level for the panes the
# extension used to draw; version 2 is the untracked switch one way and
# the cursor's file, the anchor and the freshness record the other.
SIDECAR_ENV = "COLLINS_GIT_STATE"
SIDECAR_VERSION = 2
# `hunk diff --no-sidebar` (0.21: "hide files pane"): on every spawn, since
# the native sidebar lists the files and hunk's own pane would only take
# columns from the diff (spawn_flags).
NO_SIDEBAR_FLAG = "--no-sidebar"
# The first release with `hunk diff --no-sidebar` (the page runs hunk with
# its files pane hidden; the native sidebar draws the lists) — 0.20 had the
# session API and extension API v8 the rest rides on, but not the flag.
MIN_VERSION: tuple[int, int] = (0, 21)
# Where the install card sends the user: hunk's own site carries the install
# instructions, so Collins doesn't copy lines that can go stale.
INSTALL_URL = "https://hunk.dev"
# Backoff between `hunk session list --json` polls after a spawn, ms: the
# viewer registers with hunk's daemon within half a second on a warm machine,
# but a cold node start behind the npm wrapper can take a few.
RESOLVE_DELAYS_MS: tuple[int, ...] = (500, 1000, 2000, 4000, 8000)
PROBE_TIMEOUT_S = 5.0  # `hunk --version` (node startup included)
# Where hunk's session daemon keeps its records and credentials, under the
# user runtime dir ($XDG_RUNTIME_DIR). hunk 0.21 refuses to run the daemon
# while this directory is readable by anyone but its owner ("Hunk session
# credentials are unavailable because their owner-private runtime state is
# unsafe or malformed") — and 0.20 created it with the process umask, so a
# machine that ran 0.20 under umask 002 carries a 0775 directory 0.21 won't
# touch. Every viewer then auto-spawns a daemon that exits at once, no
# viewer registers, and every load the extension sends finds no session.
# See repair_daemon_dir.
DAEMON_DIR = "hunk-mcp"
DAEMON_DIR_MODE = 0o700
# What to run in a terminal when the viewer never registers: the daemon's
# auto-spawn swallows its own stderr, the foreground run prints it.
DAEMON_DIAGNOSTIC = "hunk daemon serve"
# How long ProbeCache trusts one answer: the show_diff tool is offered to a
# session only while a hunk the page can drive is on PATH, and tools/list
# is asked per session start — a `hunk --version` each time would be a
# node start per session. Half a minute means an install (or `hunk
# update`) reaches the next session started after it without a restart.
PROBE_CACHE_TTL_S = 30.0
SESSION_TIMEOUT_S = 10.0  # session list / get / reload
# How long show_diff gives the whole call — the page opening, hunk spawning,
# the session id resolving, the load landing, the navigate answering —
# before it replies with an error. The CLI gives up on an MCP call at about
# 17 s (see app.py's _START_SESSION_DEADLINE_MS), so this sits under it with
# room for the reply to travel; a cold hunk start behind the npm wrapper
# plus the session-list backoff (RESOLVE_DELAYS_MS) fits inside it, and a
# page that takes longer is stuck, not slow.
SHOW_DIFF_DEADLINE_S = 12.0
SHOW_DIFF_POLL_MS = 250

_VERSION = re.compile(r"(\d+(?:\.\d+)+)")

# The tails of hunk's session titles (`"<repo> working tree"` and friends —
# main.js's titleFor). The repo name in front may hold spaces, so the match
# is on the tail alone.
_TITLE_WORKING_TREE = " working tree"
_TITLE_STAGED = " staged changes"
# A branch load's title ends in "<target>...HEAD"; `show <ref>` ends in
# those two tokens. Anything else (`a..b`, `a...b` between two branches —
# the commits panel's parent and default headers) is a load Collins shows
# by its title and doesn't reload.
_TITLE_BRANCH_SUFFIX = "...HEAD"

# What hunk 0.20.1 writes to stderr when a session id names nothing live —
# "No active session matches sessionId X." for a viewer that has gone, "No
# active Hunk sessions are registered with the daemon." when the daemon has
# none at all (or isn't up). Every other failure ("`hunk diff X` could not
# resolve Git revision or range", a timeout) leaves the viewer alive and
# showing what it showed.
_SESSION_GONE = re.compile(r"\bNo active (?:Hunk )?sessions?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Probe:
    """Where hunk is and which version answered; `status` is the card decision."""

    path: str | None
    version: tuple[int, ...] | None

    @property
    def status(self) -> str:
        """"missing" (no path), "old" (version None or < MIN_VERSION), else "ok"."""
        if self.path is None:
            return "missing"
        return "ok" if version_ok(self.version) else "old"


@dataclass(frozen=True)
class SessionFile:
    """One file of the loaded changeset as a session record lists it
    (hunk 0.21.1's fileSummarySchema: id, path, previousPath?, additions,
    deletions, hunkCount — no binary flag; a binary change lists 0/0/0).
    *previous_path* is set for a rename."""

    id: str
    path: str
    previous_path: str | None
    additions: int
    deletions: int
    hunk_count: int


@dataclass(frozen=True)
class Session:
    """One live hunk viewer as `session list`/`session get` report it —
    with the loaded changeset's files in review order (*files*, capped at
    MAX_SESSION_FILES) and the file and hunk hunk's cursor is on
    (*selected_path* / *selected_hunk*, from `snapshot.state`; None when
    the record doesn't say)."""

    session_id: str
    pid: int
    title: str
    repo_root: str
    files: tuple[SessionFile, ...] = ()
    selected_path: str | None = None
    selected_hunk: int | None = None


@dataclass(frozen=True)
class Selection:
    """What the extension wrote for hunk's cursor (sidecar `selection`):
    the file's path and the hunk index within it (None when the file has
    no hunk under the cursor)."""

    path: str
    hunk: int | None = None


@dataclass(frozen=True)
class Anchor:
    """The line `v` was pressed on (sidecar `anchor`): the file, which side
    of the diff the line is counted in (one of ANCHOR_SIDES) and the
    1-based line number."""

    path: str
    side: str
    line: int


@dataclass(frozen=True)
class Reply:
    """What one `hunk session …` run came back with. *returncode* None means
    it never answered (couldn't be run, or timed out)."""

    stdout: str
    stderr: str
    returncode: int | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def session_gone(self) -> bool:
        """Whether the failure says the session id names no live viewer (see
        session_gone) — the one failure a respawn is the answer to."""
        return not self.ok and session_gone(self.stderr)


def session_gone(stderr: str) -> bool:
    """Whether hunk's *stderr* says the session (or every session) is gone:
    "No active session matches sessionId …", "No active Hunk sessions are
    registered with the daemon." A refused target ("could not resolve Git
    revision or range") or an empty stderr (a timeout) is not that."""
    return _SESSION_GONE.search(stderr or "") is not None


def run(argv: list[str], run=subprocess.run, timeout: float = SESSION_TIMEOUT_S) -> Reply:
    """*argv* to completion as a Reply — never raises: a run that can't start
    or times out is Reply("", "", None)."""
    try:
        result = run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return Reply("", "", None)
    return Reply(result.stdout or "", result.stderr or "", result.returncode)


def parse_version(text: str) -> tuple[int, ...] | None:
    """`hunk --version` output ("0.20.1\\n", tolerates a leading word) → (0, 20, 1);
    None when no dotted number is found."""
    match = _VERSION.search(text or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def version_ok(version: tuple[int, ...] | None) -> bool:
    """True when *version* is known and >= MIN_VERSION."""
    return version is not None and tuple(version) >= MIN_VERSION


def probe(which: Callable[[str], str | None] = shutil.which, run=subprocess.run) -> Probe:
    """Find hunk on PATH and ask its version (one subprocess, PROBE_TIMEOUT_S).

    Never raises: a run that fails or times out yields Probe(path, None) →
    status "old" (a hunk that can't say its version can't be trusted to have
    the session API either; the card names the version "unknown"). *which*
    and *run* are injectable so the tests never touch PATH.
    """
    path = which(HUNK)
    if not path:
        return Probe(None, None)
    try:
        result = run([path, "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return Probe(path, None)
    if getattr(result, "returncode", 1) != 0:
        return Probe(path, None)
    return Probe(path, parse_version(result.stdout or ""))


def repair_daemon_dir(runtime_dir: str | None, chmod: Callable[[str, int], None] = os.chmod) -> str:
    """Make <runtime_dir>/hunk-mcp owner-only when it isn't, so hunk 0.21's
    session daemon will start (see DAEMON_DIR). The directory is the user's
    own, on tmpfs, and holds nothing but the daemon's records, so tightening
    it needs no asking. Never raises. Returns what happened: "absent" (no
    runtime dir, no such directory yet — hunk creates it 0700 itself — or a
    directory we can't even stat, which is equally nothing to repair), "ok"
    (already owner-only), "repaired" (chmod done) or "failed" (chmod
    refused — not ours, a read-only mount)."""
    if not runtime_dir:
        return "absent"
    path = os.path.join(runtime_dir, DAEMON_DIR)
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return "absent"
    if not stat.S_ISDIR(mode):
        return "absent"
    if stat.S_IMODE(mode) & 0o077 == 0:
        return "ok"
    try:
        chmod(path, DAEMON_DIR_MODE)
    except OSError:
        return "failed"
    return "repaired"


class ProbeCache:
    """The last `probe()` answer, kept for PROBE_CACHE_TTL_S: what gates the
    show_diff tool's place in a session's tools/list (mcptools.enabled_tools
    through App._mcp_tool_available).

    `ok` answers from what is known — the cached probe's status is "ok" —
    and never runs anything; `stale` says when a fresh `refresh()` is due,
    which the app runs on a thread (the probe is a subprocess). The one
    exception is a cache that has never been filled: `ok` then probes on
    the spot rather than answer wrong, so the first session started before
    the startup refresh landed still sees the tool. No hook from the git
    page's own probes or the install card's *Check again*: the TTL covers
    them — a session already running keeps the list it was handed at
    startup anyway (see tokensettings.MCP_DESCRIPTION), so the tool
    appearing reaches only the next session started, and that one is at
    most half a minute away. *probe* and *clock* are injectable for the
    tests."""

    def __init__(
        self,
        probe: Callable[[], Probe] = probe,
        ttl: float = PROBE_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._probe = probe
        self._ttl = ttl
        self._clock = clock
        self._result: Probe | None = None
        self._at: float | None = None

    @property
    def result(self) -> Probe | None:
        """The last answer, None before the first refresh."""
        return self._result

    @property
    def stale(self) -> bool:
        """Whether a refresh is due: never filled, or older than the TTL."""
        return self._at is None or self._clock() - self._at >= self._ttl

    def refresh(self) -> Probe:
        """Run the probe now and remember it. Meant for a worker thread."""
        result = self._probe()
        self._result, self._at = result, self._clock()
        return result

    def ok(self) -> bool:
        """Whether the last probe found a hunk the page can drive (status
        "ok"); a never-filled cache probes first."""
        if self._result is None:
            self.refresh()
        return self._result.status == "ok"


# -- the show_diff session tool ---------------------------------------------------


def navigate_argv(hunk: str, session_id: str, path: str, line: int | None = None) -> list[str]:
    """[hunk, "session", "navigate", session_id, "--json", "--file", path,
    "--new-line", "<line>"] — or "--hunk", "1" without a *line*: hunk
    0.20 wants exactly one target, and the file's first hunk is where a
    file with no line named lands."""
    target = ["--new-line", str(line)] if line else ["--hunk", "1"]
    return [hunk, "session", "navigate", session_id, "--json", "--file", path, *target]


def navigate_error(reply: Reply) -> str:
    """hunk's own word for a navigate it refused ("No diff file matches
    src/x.ts.", "No diff hunk in x.ts matches the requested target."), the
    `hunk: ` prefix dropped; a run that never answered says so."""
    text = (reply.stderr or "").strip().splitlines()
    line = text[-1].strip() if text else ""
    if line.startswith("hunk:"):
        line = line[len("hunk:") :].strip()
    if not line:
        return "hunk didn't answer the navigate" + (
            " in time" if reply.returncode is None else f" (exit {reply.returncode})"
        )
    return line


def show_diff_reply(
    breadcrumb: str, session_id: str, path: str | None = None, line: int | None = None
) -> str:
    """What the agent reads back from a show_diff that landed: the load by
    its breadcrumb and the hunk session id, the spot navigated to (when a
    file was named), and the one thing it needs next — that everything
    else the viewer can do is `hunk session …` from its own shell."""
    lines = [f"Loaded {breadcrumb} in the session's git page (hunk session {session_id})."]
    if path:
        where = f"{path}, line {line}" if line else path
        lines.append(f"Navigated the viewer to {where}.")
    lines.append(
        "Anything else in the viewer — navigate, highlight lines, comments — is "
        "`hunk session <command> " + session_id + " …` from your shell; "
        "`hunk skill path` names the skill file that documents the commands."
    )
    return "\n".join(lines)


def diff_args(mode: str, parent_target: str | None) -> list[str]:
    """The `hunk diff` positional/flag tail for *mode*: [] / ["--staged"] /
    ["<parent_target>...HEAD"]. ValueError for an unknown mode, or "branch"
    with no parent_target."""
    if mode == "unstaged":
        return []
    if mode == "staged":
        return ["--staged"]
    if mode == "branch":
        if not parent_target:
            raise ValueError("branch mode needs a parent target")
        return [f"{parent_target}...HEAD"]
    raise ValueError(f"unknown git page mode: {mode!r}")


def _load_tail(loaded: Loaded, parent_target: str | None, options: Options | None = None) -> list[str]:
    """The subcommand and its arguments for *loaded*: ["diff", *diff_args]
    for a mode — with "--exclude-untracked" right after "diff" when
    *options* says untracked files are out (a `diff` option; `show`
    refuses it) — ["show", ref] for a commit, ["diff", "a...b"] (the
    same untracked flag in between) for a range. ValueError (diff_args's,
    or for a malformed dict) otherwise."""
    if is_show(loaded):
        return ["show", show_ref(loaded)]
    excluded = ["--exclude-untracked"] if options is not None and not options.untracked else []
    if is_range(loaded):
        return ["diff", *excluded, range_of(loaded)]
    if not isinstance(loaded, str):
        raise ValueError(f"unknown git page load: {loaded!r}")
    return ["diff", *excluded, *diff_args(loaded, parent_target)]


def spawn_flags(options: Options | None) -> list[str]:
    """The spawn-only flags: ["--no-sidebar"] always — hunk draws its
    review alone, the native sidebar (gitsidebar) lists the files — then
    what *options* adds: ["--mode", layout] when the layout isn't hunk's
    own, ["--theme", name] when a theme is set. None of them reapplies to
    a running viewer (hunk's session API has no way to set them), so a
    change to the layout or theme is a respawn, and none goes on a reload
    tail. hunk's own `s` key can still pop its files pane inside the VTE;
    that is hunk's, and a reload keeps whatever the user did with it."""
    flags: list[str] = [NO_SIDEBAR_FLAG]
    if options is None:
        return flags
    if options.layout != DEFAULT_LAYOUT:
        flags += ["--mode", options.layout]
    if options.theme:
        flags += ["--theme", options.theme]
    return flags


def spawn_argv(
    hunk: str,
    loaded: Loaded,
    parent_target: str | None,
    extension_dir: str | None = None,
    options: Options | None = None,
) -> list[str]:
    """[hunk, "diff", "--watch", "--transparent-bg", *spawn_flags, "--extension",
    dir, *diff_args] for a mode, [hunk, "show", "--watch", "--transparent-bg",
    *spawn_flags, "--extension", dir, ref] for a commit; spawn_flags always
    starts with "--no-sidebar"; the "--extension" pair only with an
    *extension_dir* (see extension_dir()), the `--mode`/`--theme` flags and
    a `diff`'s "--exclude-untracked" only as *options* asks (None, or the
    defaults: none of them). *hunk* is an
    absolute path (VTE spawns with GLib.SpawnFlags.DEFAULT, no PATH search)."""
    command, *tail = _load_tail(loaded, parent_target, options)
    flags = ["--watch", "--transparent-bg", *spawn_flags(options)]
    if extension_dir:
        flags += ["--extension", extension_dir]
    return [hunk, command, *flags, *tail]


def extension_dir() -> str | None:
    """EXTENSION_DIR when the bundled extension is really there (its
    package.json, which is what hunk reads first), else None — a broken
    install runs hunk bare (the native sidebar hides the buttons that feed
    its keys) rather than a hunk that refuses to start."""
    return EXTENSION_DIR if os.path.isfile(os.path.join(EXTENSION_DIR, "package.json")) else None


def list_argv(hunk: str) -> list[str]:
    """[hunk, "session", "list", "--json"]"""
    return [hunk, "session", "list", "--json"]


def get_argv(hunk: str, session_id: str) -> list[str]:
    """[hunk, "session", "get", session_id, "--json"]"""
    return [hunk, "session", "get", session_id, "--json"]


def reload_argv(
    hunk: str, session_id: str, loaded: Loaded, parent_target: str | None, options: Options | None = None
) -> list[str]:
    """[hunk, "session", "reload", session_id, "--json", "--", "diff", *diff_args(...)]
    for a mode — "--exclude-untracked" first among the diff args when
    *options* excludes untracked files: hunk re-reads the option on every
    reload, and a tail without it brings them back — the same with "--",
    "show", ref for a commit. The layout and theme never ride here (see
    spawn_flags)."""
    tail = _load_tail(loaded, parent_target, options)
    return [hunk, "session", "reload", session_id, "--json", "--", *tail]


def _load_json(text: str) -> dict | None:
    try:
        data = json.loads(text or "")
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _session_from(record: object) -> Session | None:
    """A Session out of one `sessions[]`/`session` object, None for anything
    short of a dict with a string id and an integer pid."""
    if not isinstance(record, dict):
        return None
    session_id = record.get("sessionId")
    pid = record.get("pid")
    if not isinstance(session_id, str) or not session_id:
        return None
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    title = record.get("title")
    repo_root = record.get("repoRoot")
    selected_path, selected_hunk = _selection_from(record.get("snapshot"))
    return Session(
        session_id=session_id,
        pid=pid,
        title=title if isinstance(title, str) else "",
        repo_root=repo_root if isinstance(repo_root, str) else "",
        files=_files_from(record.get("files")),
        selected_path=selected_path,
        selected_hunk=selected_hunk,
    )


def _count(value: object) -> int | None:
    """A non-negative int out of a JSON value — never a bool, never a
    float (hunk's `nonnegative` schema), None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _path_field(value: object) -> str | None:
    """A path out of a JSON value: a non-empty str of at most
    MAX_PATH_CHARS, else None."""
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_CHARS:
        return None
    return value


def _files_from(value: object) -> tuple[SessionFile, ...]:
    """The SessionFiles in a record's `files` list, in hunk's order: a
    record that isn't a dict with a string id, a path within
    MAX_PATH_CHARS and three non-negative integer counts is skipped (a
    field a future hunk renames leaves the list shorter, never wrong);
    never more than MAX_SESSION_FILES; () for anything but a list."""
    if not isinstance(value, list):
        return ()
    files: list[SessionFile] = []
    for record in value:
        if not isinstance(record, dict):
            continue
        file_id = record.get("id")
        path = _path_field(record.get("path"))
        additions = _count(record.get("additions"))
        deletions = _count(record.get("deletions"))
        hunk_count = _count(record.get("hunkCount"))
        if not isinstance(file_id, str) or path is None or None in (additions, deletions, hunk_count):
            continue
        previous = record.get("previousPath")
        files.append(SessionFile(file_id, path, _path_field(previous), additions, deletions, hunk_count))
        if len(files) >= MAX_SESSION_FILES:
            break
    return tuple(files)


def _selection_from(snapshot: object) -> tuple[str | None, int | None]:
    """(selectedFilePath, selectedHunkIndex) out of a record's `snapshot`
    ({"updatedAt": …, "state": {…}}); (None, None) when either is
    missing or not its shape — a path is only a path within
    MAX_PATH_CHARS, an index only a non-negative int."""
    if not isinstance(snapshot, dict):
        return None, None
    state = snapshot.get("state")
    if not isinstance(state, dict):
        return None, None
    return _path_field(state.get("selectedFilePath")), _count(state.get("selectedHunkIndex"))


def session_for_pid(text: str, child_pid: int, children: Collection[int] = ()) -> Session | None:
    """The session in a `session list --json` reply ({"sessions": [...]}) that
    belongs to the process VTE spawned: its `pid` equals *child_pid* or is one
    of *children* (the npm wrapper spawnSyncs the real viewer, so hunk reports
    the child's pid). None for no match or malformed JSON."""
    data = _load_json(text)
    if data is None:
        return None
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return None
    wanted = {child_pid, *children}
    for record in sessions:
        session = _session_from(record)
        if session is not None and session.pid in wanted:
            return session
    return None


def parse_session_get(text: str) -> Session | None:
    """A `session get --json` reply ({"session": {...}}) as a Session, None if malformed."""
    data = _load_json(text)
    if data is None:
        return None
    return _session_from(data.get("session"))


def parse_reload_reply(text: str) -> str | None:
    """The `title` inside a `session reload --json` reply
    ({"result": {"title": ...}}), None if malformed."""
    data = _load_json(text)
    if data is None:
        return None
    result = data.get("result")
    if not isinstance(result, dict):
        return None
    title = result.get("title")
    return title if isinstance(title, str) else None


def loaded_from_title(title: str) -> tuple[str | None, str | None]:
    """What hunk says it has loaded, from a session title: "<repo> working
    tree" → ("unstaged", None), "<repo> staged changes" → ("staged", None),
    "<repo> <X>...HEAD" → ("branch", "<X>"), "<repo> show <ref>" → ("show",
    "<ref>") for a safe ref, "<repo> <a>...<b>" → ("range", "<a>...<b>")
    when the last token is a range range_halves accepts (three dots, two
    safe refs, not ending in ...HEAD — that is the branch load); anything
    else — `a..b`, a range with a pathspec, an unsafe ref — → (None,
    None), the load Collins shows by its title and leaves alone. The repo
    name may contain spaces; match on the tail."""
    text = (title or "").rstrip()
    if text.endswith(_TITLE_WORKING_TREE):
        return "unstaged", None
    if text.endswith(_TITLE_STAGED):
        return "staged", None
    tokens = text.split()
    last = tokens[-1] if tokens else ""
    if last.endswith(_TITLE_BRANCH_SUFFIX):
        target = last[: -len(_TITLE_BRANCH_SUFFIX)]
        return "branch", target or None
    if len(tokens) >= 2 and tokens[-2] == "show" and safe_ref(last):
        return "show", last
    if range_halves(last) is not None:
        return "range", last
    return None, None


def title_tail(title: str, repo_root: str | None) -> str:
    """A session title without the repo name hunk puts in front of it:
    "<repo> show HEAD" → "show HEAD" when *repo_root*'s last path segment is
    <repo>; the whole title otherwise. What the breadcrumb shows for a load
    Collins didn't make (see loaded_from_title's (None, None))."""
    text = (title or "").strip()
    name = posixpath.basename((repo_root or "").rstrip("/"))
    if name and text.startswith(name + " "):
        return text[len(name) + 1 :].strip() or text
    return text


def foreign_tab_title(tail: str) -> str:
    """Tab text for a load Collins didn't make: _("Git · {what}") over title_tail's answer."""
    return _("Git · {what}").format(what=tail or "?")


# -- the sidecar ------------------------------------------------------------------


def sidecar_path(runtime_dir: str, pid: int, serial: int) -> str:
    """<runtime_dir>/collins/git-<pid>-<serial>.json: one file per page of
    one Collins process (*serial* counts the pages), under the runtime dir
    (GLib.get_user_runtime_dir(): $XDG_RUNTIME_DIR, tmpfs, per user)."""
    return os.path.join(runtime_dir, "collins", f"git-{pid}-{serial}.json")


def sidecar_payload(untracked: bool = True) -> dict:
    """The keys Collins owns in the sidecar, plus the version: {"version":
    SIDECAR_VERSION, "untracked": bool} — whether the working-tree reviews
    Collins loads include untracked files (absent or garbled reads as True
    on the other side). The extension's own keys (`selection`, `anchor`,
    `refreshed`) are never in this payload; write_sidecar keeps them."""
    return {"version": SIDECAR_VERSION, "untracked": bool(untracked)}


def write_sidecar(path: str, payload: dict) -> bool:
    """Merge *payload* into the sidecar at *path*: what is there already
    (the extension's `selection`, `anchor` and `refreshed`, anything a
    newer extension adds) survives, the payload's keys win, and the file
    is replaced whole (a temp file beside it, then os.replace) so a reader
    never sees half a document. Creates the directory. Never raises: False
    when the write failed (no runtime dir, a read-only one), and the page
    then runs hunk without a sidecar."""
    merged: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if isinstance(existing, dict):
            merged = existing
    except (OSError, ValueError):
        pass
    merged.update(payload)
    merged["version"] = SIDECAR_VERSION
    temp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh)
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
        return False
    return True


def read_sidecar_refreshed(text: str) -> tuple[int, str] | None:
    """(index mtime ns, HEAD sha) out of the sidecar's "refreshed" record —
    what the extension observed right after reloading the review for a
    mutation of its own (an `x`, a commit): {"refreshed": {"index":
    "<ns>", "head": "<sha>"}}, the mtime a string because JavaScript's
    numbers can't hold it exactly. None when absent or malformed."""
    data = _load_json(text)
    record = data.get("refreshed") if data is not None else None
    if not isinstance(record, dict):
        return None
    index = record.get("index")
    head = record.get("head")
    if not isinstance(index, str) or not index.isdigit():
        return None
    if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head):
        return None
    return int(index), head


def read_sidecar_selection(text: str) -> Selection | None:
    """What the extension wrote for hunk's cursor (sidecar v2's
    `selection`: {"path": "<path>", "hunkIndex": n | null}) — a Selection
    with the hunk index when it is a non-negative int, None as the index
    when it is null; None as the whole answer when the key is absent,
    explicitly null (no file under the cursor), or not its shape (a path
    over MAX_PATH_CHARS, garbage)."""
    data = _load_json(text)
    record = data.get("selection") if data is not None else None
    if not isinstance(record, dict):
        return None
    path = _path_field(record.get("path"))
    if path is None:
        return None
    return Selection(path, _count(record.get("hunkIndex")))


def read_sidecar_anchor(text: str) -> Anchor | None:
    """The line `v` anchored (sidecar v2's `anchor`: {"path": "<path>",
    "side": "old" | "new", "line": n}) as an Anchor, None when absent,
    null (cleared), or not its shape (a side outside ANCHOR_SIDES, a line
    under 1, a bool for a number)."""
    data = _load_json(text)
    record = data.get("anchor") if data is not None else None
    if not isinstance(record, dict):
        return None
    path = _path_field(record.get("path"))
    side = record.get("side")
    line = _count(record.get("line"))
    if path is None or side not in ANCHOR_SIDES or not line:
        return None
    return Anchor(path, side, line)


def shown_by_extension(
    refreshed: tuple[int, str] | None, signature: tuple | None, previous: tuple | None
) -> bool:
    """Whether a tree_signature move from *previous* to *signature* is one
    the extension already reloaded the review for — its record names
    exactly the index mtime and HEAD the tree now has, and the base (which
    the extension doesn't watch) stayed put. The page then leaves hunk
    alone: a `session reload` would only cancel whatever dialog the user
    has open by the time it lands (a `D` confirm, a `C` summary)."""
    if refreshed is None or signature is None or previous is None:
        return False
    return (signature[0], signature[1]) == refreshed and signature[2] == previous[2]


def spawn_env(sidecar: str | None, environ: dict | None = None) -> list[str] | None:
    """The envv for hunk's spawn: None (inherit) without a *sidecar*; else
    every variable of *environ* (os.environ by default) as "K=V" plus
    SIDECAR_ENV=<sidecar> — VTE takes the whole list or nothing, the same
    shape terminal._agent_tab_environment builds."""
    if not sidecar:
        return None
    env = dict(os.environ if environ is None else environ)
    env[SIDECAR_ENV] = sidecar
    return [f"{key}={value}" for key, value in env.items()]


def terminate_tree(
    pid: int,
    children: Collection[int] = (),
    *,
    getpgid: Callable[[int], int] = os.getpgid,
    killpg: Callable[[int, int], None] = os.killpg,
    kill: Callable[[int, int], None] = os.kill,
) -> None:
    """SIGTERM the hunk VTE spawned — its whole process group, not just *pid*.

    The npm wrapper spawnSyncs the real viewer and never forwards a signal
    to it: SIGTERM to the wrapper alone leaves the viewer running, orphaned,
    and once its pty is gone it stops answering SIGTERM at all (verified
    against hunk 0.20.1). VTE starts the child as a session leader, so the
    group is the wrapper plus everything it spawned; when the group can't be
    signalled (already reaped, a child that moved groups), each of
    *children* and then *pid* is signalled on its own. Never raises.
    """
    try:
        killpg(getpgid(pid), signal.SIGTERM)
        return
    except OSError:
        pass
    for target in (*children, pid):
        try:
            kill(target, signal.SIGTERM)
        except OSError:
            pass
