# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Everything the git page decides without a widget: driving hunk from outside.

The git page (gitpage) is hunk — hunk.dev, the terminal diff viewer — running
in a VTE, steered over its session API: `hunk session list --json` names the
live viewers by pid, `hunk session reload <id> --json -- diff …` swaps what
one of them shows. This module is the GTK-free half of that: the version
gate behind the install card, the argv for each command, the runner that
turns one into a Reply, the parsers for their JSON replies (and for the
stderr that says a session is gone, as opposed to a load hunk refused), and
the small mappings between the three things the page can load ("unstaged",
"staged", "branch"), hunk's own session titles, the breadcrumb and tab title
Collins shows for them, the Ctrl+1/2/3 chords that pick them, and the layout
slot they persist in. Kept importable by the unit tests (see
tests/conftest.py), which is where all of it is exercised.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import signal
import subprocess
from collections.abc import Callable, Collection
from dataclasses import dataclass

from .i18n import _

HUNK = "hunk"
MODES: tuple[str, ...] = ("unstaged", "staged", "branch")
DEFAULT_MODE = "unstaged"
# The first release whose session API the page relies on (and, for PR 2, the
# first with extension API v8).
MIN_VERSION: tuple[int, int] = (0, 20)
# The three install lines from hunk's README, click-to-copy on the install card.
INSTALL_COMMANDS: tuple[str, ...] = (
    "npm i -g hunkdiff",
    "curl -fsSL https://hunk.dev/install.sh | sh",
    "brew install hunk",
)
# Backoff between `hunk session list --json` polls after a spawn, ms: the
# viewer registers with hunk's daemon within half a second on a warm machine,
# but a cold node start behind the npm wrapper can take a few.
RESOLVE_DELAYS_MS: tuple[int, ...] = (500, 1000, 2000, 4000, 8000)
PROBE_TIMEOUT_S = 5.0  # `hunk --version` (node startup included)
SESSION_TIMEOUT_S = 10.0  # session list / get / reload

# Keyvals and modifier bits as integers rather than Gdk constants: this
# module is imported by the unit tests, which run without the GTK stack, and
# the values are ABI (X11's keysymdef, GDK's ModifierType) — see panelkeys.
_KEYVAL_MODES: dict[int, str] = {
    0x31: "unstaged",  # GDK_KEY_1
    0x32: "staged",  # GDK_KEY_2
    0x33: "branch",  # GDK_KEY_3
    0xFFB1: "unstaged",  # GDK_KEY_KP_1
    0xFFB2: "staged",  # GDK_KEY_KP_2
    0xFFB3: "branch",  # GDK_KEY_KP_3
}
_CONTROL_MASK = 1 << 2
_ALT_MASK = 1 << 3  # Mod1
_SUPER_MASK = 1 << 26
_HYPER_MASK = 1 << 27
_META_MASK = 1 << 28
# Any of these on top of Control makes it somebody else's chord. Shift is
# not among them: a keyboard layout may need Shift to reach a digit, and
# Ctrl+Shift+1 has no other claimant in the page.
_OTHER_CHORD_MASK = _ALT_MASK | _SUPER_MASK | _HYPER_MASK | _META_MASK

_VERSION = re.compile(r"(\d+(?:\.\d+)+)")

# The tails of hunk's session titles (`"<repo> working tree"` and friends —
# main.js's titleFor). The repo name in front may hold spaces, so the match
# is on the tail alone.
_TITLE_WORKING_TREE = " working tree"
_TITLE_STAGED = " staged changes"

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
class Session:
    """One live hunk viewer as `session list`/`session get` report it."""

    session_id: str
    pid: int
    title: str
    repo_root: str


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


def spawn_argv(hunk: str, mode: str, parent_target: str | None) -> list[str]:
    """[hunk, "diff", "--watch", "--transparent-bg", *diff_args(mode, parent_target)]
    — *hunk* is an absolute path (VTE spawns with GLib.SpawnFlags.DEFAULT, no
    PATH search)."""
    return [hunk, "diff", "--watch", "--transparent-bg", *diff_args(mode, parent_target)]


def list_argv(hunk: str) -> list[str]:
    """[hunk, "session", "list", "--json"]"""
    return [hunk, "session", "list", "--json"]


def get_argv(hunk: str, session_id: str) -> list[str]:
    """[hunk, "session", "get", session_id, "--json"]"""
    return [hunk, "session", "get", session_id, "--json"]


def reload_argv(hunk: str, session_id: str, mode: str, parent_target: str | None) -> list[str]:
    """[hunk, "session", "reload", session_id, "--json", "--", "diff", *diff_args(...)]"""
    return [hunk, "session", "reload", session_id, "--json", "--", "diff", *diff_args(mode, parent_target)]


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
    return Session(
        session_id=session_id,
        pid=pid,
        title=title if isinstance(title, str) else "",
        repo_root=repo_root if isinstance(repo_root, str) else "",
    )


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
    "<repo> <X>...HEAD" → ("branch", "<X>"); anything else → (None, None).
    The repo name may contain spaces; match on the tail."""
    text = (title or "").rstrip()
    if text.endswith(_TITLE_WORKING_TREE):
        return "unstaged", None
    if text.endswith(_TITLE_STAGED):
        return "staged", None
    last = text.rsplit(" ", 1)[-1] if text else ""
    if "..." in last:
        target = last.split("...", 1)[0]
        return "branch", target or None
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


def breadcrumb(mode: str, branch: str | None, parent: str | None) -> str:
    """Header text: _("working tree · unstaged"), _("working tree · staged"),
    or _("{branch} vs {parent}") (branch/parent fall back to "HEAD" / "?"
    when None)."""
    if mode == "unstaged":
        return _("working tree · unstaged")
    if mode == "staged":
        return _("working tree · staged")
    return _("{branch} vs {parent}").format(branch=branch or "HEAD", parent=parent or "?")


def tab_title(mode: str, parent: str | None) -> str:
    """Tab text: _("Git · unstaged"), _("Git · staged"), _("Git · vs {parent}")."""
    if mode == "unstaged":
        return _("Git · unstaged")
    if mode == "staged":
        return _("Git · staged")
    return _("Git · vs {parent}").format(parent=parent or "?")


def load_for_key(keyval: int, state: int) -> str | None:
    """Ctrl+1/2/3 (main row 0x31-0x33 or keypad 0xFFB1-0xFFB3) with Control
    held (bit 1<<2) and none of Alt (1<<3) / Super (1<<26) / Hyper (1<<27) /
    Meta (1<<28) → "unstaged"/"staged"/"branch"; Shift is tolerated. None for
    every other press. Integers, not Gdk constants: gi-free (see panelkeys)."""
    mode = _KEYVAL_MODES.get(keyval)
    if mode is None:
        return None
    if not state & _CONTROL_MASK or state & _OTHER_CHORD_MASK:
        return None
    return mode


def initial_mode(staged: bool, unstaged: bool) -> str:
    """What the footer click opens on: "staged" only when the index has
    changes and the tree/untracked have none; "unstaged" otherwise (dirty
    tree, or nothing at all)."""
    return "staged" if staged and not unstaged else "unstaged"


def encode_state(loaded: str) -> dict:
    """{"kind": "git", "loaded": loaded} — the page's panel_layout slot."""
    return {"kind": "git", "loaded": loaded}


def decode_state(page: object) -> str:
    """The mode a saved page dict asks for; anything not in MODES (a future
    {"show": sha}, garbage, a non-dict) reads as DEFAULT_MODE — the layout is
    a preference, restore never refuses on it."""
    if not isinstance(page, dict):
        return DEFAULT_MODE
    loaded = page.get("loaded")
    return loaded if isinstance(loaded, str) and loaded in MODES else DEFAULT_MODE


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
