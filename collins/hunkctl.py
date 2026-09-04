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
one). The `show_diff` session tool (app.py's _mcp_show_diff, driving the
page) keeps its decisions here too: what its `what` argument names, the
commit check behind a ref, the repo-relative file path it hands hunk, the
`session navigate` argv, and the reply the agent reads back.

What Preferences → Git decides about hunk arrives as the whole settings
dict and is read into an `Options` (from_settings, a pure normaliser):
the layout (`--mode`) and theme (`--theme`) go on the spawn argv, since
nothing in hunk's session API changes them on a running viewer; the
untracked switch goes on every `diff` tail, spawn and reload alike
(`--exclude-untracked`, which hunk resolves afresh on each reload — and
which `show` refuses); the log page rides in the sidecar.

The extension and the page share a *sidecar*: a small JSON file under the
runtime dir whose path rides to hunk's child in COLLINS_GIT_STATE. Collins
writes the parent and default branch names, the log page size and the
untracked switch into it (the extension puts `--exclude-untracked` on the
`diff` tails it sends itself, so its loads agree with Collins' own);
the extension writes the user's "Set parent branch…" pick back, and the
index mtime and HEAD it last reloaded the review for on its own (so the
page's freshness reload stays home for a move hunk has already shown —
shown_by_extension), and the level a narrow page shows — the extension's
level.ts: `diff`, `files` or `commits`, one pane at a time when only one
fits beside the diff — which the page's header buttons follow
(read_sidecar_level, level_button); each side re-reads when the other
wrote (the extension watches the file, the page stats it on the footer
tick). The path, payload, readers and writer live here, and the column
arithmetic that says when the page is narrow (pane_fit): hunk's layout
budget, the same numbers level.ts holds. Kept importable by the unit
tests (see tests/conftest.py), which is where all of it is exercised.
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
from collections.abc import Callable, Collection, Mapping
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
# The sidecar (see the module docstring): the variable its path travels in.
SIDECAR_ENV = "COLLINS_GIT_STATE"
# hunk's layouts, `--mode`'s words (`hunk diff --help`, 0.20.1): the
# git_layout setting's domain. LAYOUTS[0] is hunk's own choice and sends
# no flag; hunk exits at once on a word it doesn't know, so anything else
# normalises to it (Options.from_settings). Not MODES — those are the
# page's loads.
LAYOUTS: tuple[str, ...] = ("auto", "split", "stack")
DEFAULT_LAYOUT = LAYOUTS[0]
# The commits-per-group page the extension loads (the git_log_page setting),
# its default and the clamp — the same numbers as sidecar.ts's, so what
# Collins writes is what the extension reads.
LOG_PAGE = 20
MIN_LOG_PAGE = 5
MAX_LOG_PAGE = 500
# A theme name (git_theme) that can go on hunk's argv: hunk falls back to
# its default theme on a name it doesn't know (verified against 0.20.1),
# so the gate is only against something that isn't one argument. The
# longest theme id hunk ships is 26 characters. Preferences validates
# against the same number, imported from here.
MAX_THEME_LEN = 64
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
GIT_TIMEOUT_S = 5.0  # commit_subject's `git log -1`, resolve_commit's `git rev-parse`
# How long show_diff gives the whole call — the page opening, hunk spawning,
# the session id resolving, the load landing, the navigate answering —
# before it replies with an error. The CLI gives up on an MCP call at about
# 17 s (see app.py's _START_SESSION_DEADLINE_MS), so this sits under it with
# room for the reply to travel; a cold hunk start behind the npm wrapper
# plus the session-list backoff (RESOLVE_DELAYS_MS) fits inside it, and a
# page that takes longer is stuck, not slow.
SHOW_DIFF_DEADLINE_S = 12.0
SHOW_DIFF_POLL_MS = 250

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


@dataclass(frozen=True)
class Options:
    """What Preferences → Git decides about hunk, normalised (see
    from_settings): the layout (one of LAYOUTS) and theme name ("" for
    hunk's own default), whether working-tree reviews include untracked
    files, and the commits-per-group page. The defaults are the shipped
    settings' — a page that never received settings runs on them, and
    with them every argv here is the one it was before the settings
    existed."""

    layout: str = DEFAULT_LAYOUT
    theme: str = ""
    untracked: bool = True
    log_page: int = LOG_PAGE

    @classmethod
    def from_settings(cls, settings: Mapping) -> Options:
        """An Options out of the whole settings dict, tolerant of every
        key being missing or wrong: git_layout not in LAYOUTS → "auto";
        git_theme stripped, kept only when it is one argument (no
        whitespace, no leading "-", at most MAX_THEME_LEN chars) else "";
        git_untracked as a bool (absent: on); git_log_page as an int
        clamped to MIN_LOG_PAGE..MAX_LOG_PAGE (garbage: LOG_PAGE)."""
        layout = settings.get("git_layout")
        if layout not in LAYOUTS:
            layout = DEFAULT_LAYOUT
        theme = settings.get("git_theme")
        theme = theme.strip() if isinstance(theme, str) else ""
        if not safe_theme(theme):
            theme = ""
        untracked = settings.get("git_untracked", True)
        try:
            log_page = int(settings.get("git_log_page", LOG_PAGE))
        except (TypeError, ValueError):
            log_page = LOG_PAGE
        log_page = max(MIN_LOG_PAGE, min(MAX_LOG_PAGE, log_page))
        return cls(layout=layout, theme=theme, untracked=bool(untracked), log_page=log_page)


def safe_theme(name: object) -> bool:
    """Whether *name* can go on hunk's argv as `--theme <name>`: a non-empty
    str of at most MAX_THEME_LEN chars, no whitespace, no leading "-". Not
    whether hunk knows it — it can't be listed (built-ins, `auto`, aliases
    and the user's own `[themes.<id>]`), and an unknown one degrades to
    hunk's default rather than failing the spawn."""
    if not isinstance(name, str) or not name or len(name) > MAX_THEME_LEN:
        return False
    if any(ch.isspace() for ch in name):
        return False
    return not name.startswith("-")


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
    runtime dir, or no such directory yet — hunk creates it 0700 itself),
    "ok" (already owner-only), "repaired" (chmod done) or "failed" (chmod
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


def resolve_commit(
    cwd: str | None, ref: object, run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> str | None:
    """The full sha of the commit *ref* names in the repository at *cwd* —
    `git rev-parse --verify --quiet <ref>^{commit}`, one subprocess, for
    show_diff's worker thread: a load is asked for by sha, so a branch
    name or `HEAD~2` handed to the tool lands as the commit it meant at
    the time, and persists as one.

    The same three answers as commit_subject: the sha when git resolved
    the ref; None when git says it names no commit (or *ref* isn't safe to
    ask about); "" when git couldn't be asked at all (not on PATH, no cwd,
    a timeout)."""
    if not cwd or not safe_ref(ref):
        return None
    argv = ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]
    try:
        result = run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    if getattr(result, "returncode", 1) != 0:
        return None
    sha = (result.stdout or "").strip()
    return sha if safe_ref(sha) else None


# -- the show_diff session tool ---------------------------------------------------


def show_diff_load(what: object) -> Loaded | None:
    """What the tool's `what` argument asks for: one of MODES as itself, any
    other safe ref (safe_ref) as a commit load {"show": ref} — resolved to
    a sha by resolve_commit before it is loaded — and None for anything
    else (whitespace, a leading "-", a range)."""
    if not isinstance(what, str):
        return None
    if what in MODES:
        return what
    return {SHOW_KEY: what} if safe_ref(what) else None


def diff_file_path(
    raw: object, repo_root: str | None, cwd: str | None = None, exists=os.path.exists
) -> str | None:
    """The path hunk's `--file` takes — repo-relative, "/"-separated, as the
    files panel shows it — out of what the agent handed the tool: an
    absolute path inside *repo_root*, or a relative one. A relative path
    is taken against the repository root (that is how a diff names files)
    unless it is only there against the agent's *cwd* (an agent that cd'd
    into a subdirectory and named a file the way its shell sees it) —
    *exists* decides, injectable for the tests. None for anything that
    can't be a file in the diff: empty, escaping the root, a leading "-"
    (hunk's parser would read it as a flag)."""
    if not isinstance(raw, str) or not raw.strip() or not repo_root:
        return None
    root = os.path.normpath(repo_root)
    text = os.path.expanduser(raw.strip())
    if os.path.isabs(text):
        full = os.path.normpath(text)
    else:
        full = os.path.normpath(os.path.join(root, text))
        if cwd and os.path.normpath(cwd) != root:
            from_cwd = os.path.normpath(os.path.join(cwd, text))
            if not exists(full) and exists(from_cwd):
                full = from_cwd
    try:
        relative = os.path.relpath(full, root)
    except ValueError:
        return None
    if relative == os.curdir or relative.startswith(os.pardir) or os.path.isabs(relative):
        return None
    relative = relative.replace(os.sep, "/")
    return None if relative.startswith("-") else relative


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
    refuses it) — ["show", ref] for a commit. ValueError (diff_args's, or
    for a malformed dict) otherwise."""
    if is_show(loaded):
        return ["show", show_ref(loaded)]
    if not isinstance(loaded, str):
        raise ValueError(f"unknown git page load: {loaded!r}")
    excluded = ["--exclude-untracked"] if options is not None and not options.untracked else []
    return ["diff", *excluded, *diff_args(loaded, parent_target)]


def spawn_flags(options: Options | None) -> list[str]:
    """The spawn-only flags *options* adds: ["--mode", layout] when the
    layout isn't hunk's own, ["--theme", name] when a theme is set. Neither
    reapplies to a running viewer (hunk's session API has no way to set
    them), so a change to either is a respawn, and neither goes on a
    reload tail."""
    if options is None:
        return []
    flags: list[str] = []
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
    *spawn_flags, "--extension", dir, ref] for a commit; the "--extension"
    pair only with an *extension_dir* (see extension_dir()), the
    `--mode`/`--theme` flags and a `diff`'s "--exclude-untracked" only as
    *options* asks (None, or the defaults: none of them). *hunk* is an
    absolute path (VTE spawns with GLib.SpawnFlags.DEFAULT, no PATH search)."""
    command, *tail = _load_tail(loaded, parent_target, options)
    flags = ["--watch", "--transparent-bg", *spawn_flags(options)]
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


def sidecar_payload(
    parent: str | None, source: str, default: str | None, log_page: int, untracked: bool = True
) -> dict:
    """The keys Collins owns in the sidecar, plus the version: {"version": 1,
    "parent": name|None, "parentSource": "auto"|"user", "default": name|None,
    "logPage": n, "untracked": bool}. *parent* is a branch NAME, never a
    target like "origin/main" — each side resolves the name itself.
    *untracked* False is what makes the extension put "--exclude-untracked"
    on the diff tails it sends (absent or garbled reads as True there)."""
    return {
        "version": 1,
        "parent": parent,
        "parentSource": "user" if source == "user" else "auto",
        "default": default,
        "logPage": int(log_page),
        "untracked": bool(untracked),
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


# -- levels of a narrow page (the extension's level.ts) ------------------------------

# Hunk 0.20.1's layout budget in columns: two of body padding, a one-column
# divider per pane, 48 for the diff at its narrowest, 22 before the sidebar
# area shows at all (hunk's own pane's minimum), and the extension's panes at
# 26 (commits, preferred) and 22 (files, minimum). The numbers are level.ts's,
# and its tests pin them; a hunk that changes them moves both.
ONE_PANE_COLUMNS = 2 + 22 + 1 + 48  # 73: one pane beside the diff
TWO_PANE_COLUMNS = 2 + 26 + 1 + 22 + 1 + 48  # 100: both panes — the wide layout

# The levels a narrow page stacks, bottom to top.
LEVELS = ("diff", "files", "commits")
DEFAULT_LEVEL = LEVELS[0]

# What the header's buttons feed hunk: the extension's `<` (level-up) and
# `>` (level-down), keys neither hunk nor the extension use otherwise.
LEVEL_UP_KEY = b"<"
LEVEL_DOWN_KEY = b">"


def pane_fit(columns: int) -> str:
    """How many of the extension's panes a terminal *columns* wide shows
    beside the diff: "none" under ONE_PANE_COLUMNS, "one" under
    TWO_PANE_COLUMNS, else "two". A count that isn't known yet (0, or
    negative) is "none"."""
    if columns < ONE_PANE_COLUMNS:
        return "none"
    return "one" if columns < TWO_PANE_COLUMNS else "two"


def page_is_narrow(columns: int) -> bool:
    """Whether the page shows one pane at a time — anything short of both —
    and the header shows its level buttons."""
    return pane_fit(columns) != "two"


def level_ok(level: object) -> bool:
    return isinstance(level, str) and level in LEVELS


def level_up(level: str) -> str | None:
    """The level above *level*, None at the top (or for a level unknown here)."""
    if not level_ok(level):
        return None
    at = LEVELS.index(level)
    return LEVELS[at + 1] if at + 1 < len(LEVELS) else None


def level_down(level: str) -> str | None:
    """The level below *level*, None at the bottom (or for a level unknown here)."""
    if not level_ok(level):
        return None
    at = LEVELS.index(level)
    return LEVELS[at - 1] if at > 0 else None


def read_sidecar_level(text: str) -> str | None:
    """The level the extension last reported — {"level": "files"} — or None
    when absent or not one of LEVELS."""
    data = _load_json(text)
    level = data.get("level") if data is not None else None
    return level if level_ok(level) else None


def level_button(up: bool, level: str, columns: int) -> tuple[str, bool]:
    """(tooltip, sensitive) for the header's up (or down) button at *level*
    on a page *columns* wide. Insensitive at the end of the stack, and on a
    page too narrow for any pane — where the tooltip says what would help."""
    if pane_fit(columns) == "none":
        return _("Too narrow for a panel — widen the page"), False
    target = level_up(level) if up else level_down(level)
    if target == "files":
        return (_("Show the files") if up else _("Back to the files")), True
    if target == "commits":
        return _("Show the commits"), True
    if target == "diff":
        return _("Back to the diff"), True
    if up:
        return _("The commits are shown — the top level"), False
    return _("The diff is shown — the bottom level"), False


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
