# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Everything the git page decides without a widget: driving hunk from outside.

The git page (gitpage) is hunk — hunk.dev, the terminal diff viewer — running
in a VTE, steered over its session API: `hunk session list --json` names the
live viewers by pid, `hunk session reload <id> --json -- diff …` swaps what
one of them shows. This module is the GTK-free half of that: the version
gate behind the install card, the argv for each command (with the bundled
collins-git extension on it, see extension_dir), the runner that turns one
into a Reply, the parsers for their JSON replies (and for the stderr that
says a session is gone, as opposed to a load hunk refused), and the small
mappings between the things the page can load — the three modes "unstaged",
"staged", "branch", and a commit as {"show": ref} — hunk's own session
titles, the breadcrumb and tab title Collins shows for them (a commit's
breadcrumb names it, `<sha7> <subject>`, through the one git call here,
commit_subject), the Ctrl+1/2/3 chords that pick the modes, and the layout
slot they persist in (with the parent branch the user set, when they set
one).

The extension and the page share a *sidecar*: a small JSON file under the
runtime dir whose path rides to hunk's child in COLLINS_GIT_STATE. Collins
writes the parent and default branch names and the log page size into it;
the extension writes the user's "Set parent branch…" pick back; each side
re-reads when the other wrote (the extension watches the file, the page
stats it on the footer tick). The path, payload, reader and writer live
here. Kept importable by the unit tests (see tests/conftest.py), which is
where all of it is exercised.
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
# The fourth kind of load, a commit: {"show": "<ref>"} beside the three mode
# strings. `Loaded` is what the page's loaded/load()/page_state carry.
SHOW_KEY = "show"
Loaded = str | dict
# The hunk extension Collins ships as package data (collins/hunkext/
# collins-git): the commits and files panels and the staging keys. Handed to
# hunk with `--extension <dir>`, so nothing lands in the user's hunk config
# and no trust prompt appears.
EXTENSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hunkext", "collins-git")
# The sidecar (see the module docstring): the variable its path travels in,
# and the commits-per-group page the extension loads (a setting in PR 4).
SIDECAR_ENV = "COLLINS_GIT_STATE"
LOG_PAGE = 20
# A ref that is safe as an argument and as a title token: the same rule as
# gitinfo._safe_branch_name (non-empty, no whitespace, no leading "-", no
# "..") plus a length cap, since a title token or a persisted string is
# nobody's promise. Full shas are 40 (64 for sha256 repositories).
_MAX_REF_LEN = 128
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_LEN = 7
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
GIT_TIMEOUT_S = 5.0  # commit_subject's `git log -1`

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


def safe_ref(name: object) -> bool:
    """Whether *name* can be handed to git (and to hunk's argv) as one
    revision: a non-empty str, no whitespace, no leading "-", no ".." (a
    range), at most _MAX_REF_LEN chars. gitinfo._safe_branch_name's rule,
    for what arrives from a title, a sidecar or a saved layout."""
    if not isinstance(name, str) or not name or len(name) > _MAX_REF_LEN:
        return False
    if any(ch.isspace() for ch in name):
        return False
    return not name.startswith("-") and ".." not in name


def is_show(loaded: object) -> bool:
    """Whether *loaded* is a commit load, {"show": ref} with a safe ref."""
    return isinstance(loaded, dict) and safe_ref(loaded.get(SHOW_KEY))


def show_ref(loaded: object) -> str | None:
    """The ref of a commit load, None for anything else."""
    return loaded[SHOW_KEY] if is_show(loaded) else None


def loaded_ok(loaded: object) -> bool:
    """Whether *loaded* is something the page can spawn into: one of MODES
    or a well-formed commit load."""
    return (isinstance(loaded, str) and loaded in MODES) or is_show(loaded)


def short_ref(ref: str) -> str:
    """A full sha cut to its first 7 characters; any other ref (a branch, a
    tag, `HEAD`, an abbreviation already) as it is."""
    return ref[:_SHORT_SHA_LEN] if _FULL_SHA.match(ref or "") else ref


def commit_subject(
    cwd: str | None, ref: object, run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> str | None:
    """The subject line of the commit *ref* names in the repository at *cwd*
    — `git log -1 --format=%s <ref>^{commit} --`, one subprocess, meant for
    the page's worker threads (the poll itself never runs git).

    Three answers: the subject (maybe empty) when git resolved the ref; None
    when git answered that it names no commit (the one answer a restored
    {"show": sha} whose commit was rebased away falls back on); "" when git
    couldn't be asked at all (not on PATH, no cwd, a timeout) — the ref is
    not disproven, only unnamed. A real commit with an empty subject
    (`--allow-empty-message`) also answers "", so callers must not read ""
    as "git was unreachable" — today none does; both mean "no subject to
    show". An unsafe *ref* is None without a call."""
    if not cwd or not safe_ref(ref):
        return None
    argv = ["git", "log", "-1", "--format=%s", f"{ref}^{{commit}}", "--"]
    try:
        result = run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(result, "returncode", 1) != 0:
        return None
    lines = (result.stdout or "").strip().splitlines()
    return lines[0].strip() if lines else ""


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


def _load_tail(loaded: Loaded, parent_target: str | None) -> list[str]:
    """The subcommand and its arguments for *loaded*: ["diff", *diff_args]
    for a mode, ["show", ref] for a commit. ValueError (diff_args's, or for
    a malformed dict) otherwise."""
    if is_show(loaded):
        return ["show", show_ref(loaded)]
    if not isinstance(loaded, str):
        raise ValueError(f"unknown git page load: {loaded!r}")
    return ["diff", *diff_args(loaded, parent_target)]


def spawn_argv(
    hunk: str, loaded: Loaded, parent_target: str | None, extension_dir: str | None = None
) -> list[str]:
    """[hunk, "diff", "--watch", "--transparent-bg", "--extension", dir, *diff_args]
    for a mode, [hunk, "show", "--watch", "--transparent-bg", "--extension",
    dir, ref] for a commit; the "--extension" pair only with an
    *extension_dir* (see extension_dir()). *hunk* is an absolute path (VTE
    spawns with GLib.SpawnFlags.DEFAULT, no PATH search)."""
    command, *tail = _load_tail(loaded, parent_target)
    flags = ["--watch", "--transparent-bg"]
    if extension_dir:
        flags += ["--extension", extension_dir]
    return [hunk, command, *flags, *tail]


def extension_dir() -> str | None:
    """EXTENSION_DIR when the bundled extension is really there (its
    package.json, which is what hunk reads first), else None — a broken
    install runs hunk bare, with its own files pane and no commits panel,
    rather than a hunk that refuses to start."""
    return EXTENSION_DIR if os.path.isfile(os.path.join(EXTENSION_DIR, "package.json")) else None


def list_argv(hunk: str) -> list[str]:
    """[hunk, "session", "list", "--json"]"""
    return [hunk, "session", "list", "--json"]


def get_argv(hunk: str, session_id: str) -> list[str]:
    """[hunk, "session", "get", session_id, "--json"]"""
    return [hunk, "session", "get", session_id, "--json"]


def reload_argv(hunk: str, session_id: str, loaded: Loaded, parent_target: str | None) -> list[str]:
    """[hunk, "session", "reload", session_id, "--json", "--", "diff", *diff_args(...)]
    for a mode; the same with "--", "show", ref for a commit."""
    return [hunk, "session", "reload", session_id, "--json", "--", *_load_tail(loaded, parent_target)]


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
    "<repo> <X>...HEAD" → ("branch", "<X>"), "<repo> show <ref>" → ("show",
    "<ref>") for a safe ref; anything else — `a...b` between two branches,
    `a..b`, a range with a pathspec, an unsafe ref — → (None, None), the
    load Collins shows by its title and leaves alone. The repo name may
    contain spaces; match on the tail."""
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


def breadcrumb(loaded: Loaded, branch: str | None, parent: str | None, subject: str | None = None) -> str:
    """Header text: _("working tree · unstaged"), _("working tree · staged"),
    _("{branch} vs {parent}") (branch/parent fall back to "HEAD" / "?" when
    None), or for a commit "<ref> <subject>" with the ref cut short
    (short_ref) — `a1b2c3d Wire the mode switch` — and _("commit {ref}")
    while the *subject* isn't known (see commit_subject)."""
    if is_show(loaded):
        ref = short_ref(show_ref(loaded))
        if subject:
            return f"{ref} {subject}"
        return _("commit {ref}").format(ref=ref)
    if loaded == "unstaged":
        return _("working tree · unstaged")
    if loaded == "staged":
        return _("working tree · staged")
    return _("{branch} vs {parent}").format(branch=branch or "HEAD", parent=parent or "?")


def tab_title(loaded: Loaded, parent: str | None) -> str:
    """Tab text: _("Git · unstaged"), _("Git · staged"), _("Git · vs {parent}"),
    _("Git · {ref}") for a commit (short_ref)."""
    if is_show(loaded):
        return _("Git · {ref}").format(ref=short_ref(show_ref(loaded)))
    if loaded == "unstaged":
        return _("Git · unstaged")
    if loaded == "staged":
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


def encode_state(loaded: Loaded, parent: str | None = None) -> dict:
    """{"kind": "git", "loaded": loaded, "parent": parent} — the page's
    panel_layout slot; "parent" (the branch the user set) only when there
    is one. A commit load is its dict, {"show": ref}, copied."""
    state = {"kind": "git", "loaded": dict(loaded) if isinstance(loaded, dict) else loaded}
    if parent:
        state["parent"] = parent
    return state


def decode_state(page: object) -> Loaded:
    """What a saved page dict asks for: one of MODES, or a validated
    {"show": ref}; anything else (garbage, an unsafe ref, a non-dict) reads
    as DEFAULT_MODE — the layout is a preference, restore never refuses on
    it."""
    if not isinstance(page, dict):
        return DEFAULT_MODE
    loaded = page.get("loaded")
    if is_show(loaded):
        return {SHOW_KEY: loaded[SHOW_KEY]}
    return loaded if isinstance(loaded, str) and loaded in MODES else DEFAULT_MODE


def decode_parent(page: object) -> str | None:
    """The parent branch name a saved page dict carries, when it is a safe
    one; None otherwise (no parent set, or a string git couldn't take)."""
    if not isinstance(page, dict):
        return None
    parent = page.get("parent")
    return parent if safe_ref(parent) else None


# -- the sidecar ------------------------------------------------------------------


def sidecar_path(runtime_dir: str, pid: int, serial: int) -> str:
    """<runtime_dir>/collins/git-<pid>-<serial>.json: one file per page of
    one Collins process (*serial* counts the pages), under the runtime dir
    (GLib.get_user_runtime_dir(): $XDG_RUNTIME_DIR, tmpfs, per user)."""
    return os.path.join(runtime_dir, "collins", f"git-{pid}-{serial}.json")


def sidecar_payload(parent: str | None, source: str, default: str | None, log_page: int) -> dict:
    """The keys Collins owns in the sidecar, plus the version: {"version": 1,
    "parent": name|None, "parentSource": "auto"|"user", "default": name|None,
    "logPage": n}. *parent* is a branch NAME, never a target like
    "origin/main" — each side resolves the name itself."""
    return {
        "version": 1,
        "parent": parent,
        "parentSource": "user" if source == "user" else "auto",
        "default": default,
        "logPage": int(log_page),
    }


def write_sidecar(path: str, payload: dict) -> bool:
    """Merge *payload* into the sidecar at *path*: what is there already
    (the extension's keys, anything a newer extension adds) survives, the
    payload's keys win, and the file is replaced whole (a temp file beside
    it, then os.replace) so a reader never sees half a document. Creates the
    directory. Never raises: False when the write failed (no runtime dir, a
    read-only one), and the page then runs hunk without a sidecar."""
    merged: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if isinstance(existing, dict):
            merged = existing
    except (OSError, ValueError):
        pass
    merged.update(payload)
    merged["version"] = 1
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


def read_sidecar(text: str) -> tuple[str | None, str]:
    """(parent, source) out of a sidecar's *text*: the parent branch name
    when it is a safe one (else None) and "user" when parentSource says so
    (else "auto"). Tolerant: garbage, a non-object, missing keys all read as
    (None, "auto")."""
    data = _load_json(text)
    if data is None:
        return None, "auto"
    parent = data.get("parent")
    source = "user" if data.get("parentSource") == "user" else "auto"
    return (parent if safe_ref(parent) else None), source


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
