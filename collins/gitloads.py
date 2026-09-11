# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What the git page can load, named without a viewer: the `Loaded` vocabulary.

The page shows one of five things — the working tree's unstaged changes,
the index ("staged"), the current branch against its parent ("branch"), a
commit as {"show": "<ref>"} and a range between two refs as {"range":
"<a>...<b>"} (three dots exactly, both halves a safe ref) — and every
surface that names, saves, restores or asks for one of them reads the
rules here: the shape checks (is_show, show_ref, is_range, range_halves,
range_of, loaded_ok, safe_ref for the refs inside), the words the header
and the tab wear for each (breadcrumb, tab_title, short_ref for a sha),
the Ctrl+1/2/3 chords that pick the three modes (load_for_key, integers
rather than Gdk constants), what the footer click opens on
(initial_mode), the panel-layout slot a page persists in (encode_state /
decode_state / decode_sidebar), the `show_diff` session tool's reading of
its `what` argument and the repo-relative file path it hands the view
(show_diff_load, diff_file_path), and the three git calls behind them —
commit_subject for a commit's name, commit_message for the whole of it (the
page's commit card: sha, author, date, subject and body, each bounded),
resolve_commit for the sha a ref means right now — each one subprocess on
a worker thread with commit_subject's three answers (a value, None for
"git says no", "" for "git couldn't be asked").

Preferences → Git arrives as the whole settings dict and normalises into
an Options (from_settings): the layout (one of LAYOUTS), the untracked
switch, the commits-per-group page (LOG_PAGE, clamped to
MIN_LOG_PAGE..MAX_LOG_PAGE) and the diff view's three knobs (line
numbers, wrap, word diff). MAX_PATH_CHARS bounds every path that arrives
from a tool call or a saved layout. SHOW_DIFF_DEADLINE_S and
SHOW_DIFF_POLL_MS bound the show_diff tool's wait for a page to settle.

No GTK, no git beyond the three calls named above; imported by the unit
tests (tests/test_gitloads.py).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

from .i18n import _

MODES: tuple[str, ...] = ("unstaged", "staged", "branch")
DEFAULT_MODE = "unstaged"
# The fourth kind of load, a commit: {"show": "<ref>"} beside the three mode
# strings; the fifth, a range between two refs: {"range": "<a>...<b>"} —
# three dots exactly, both halves a safe ref (is_range) — git's `diff
# a...b`, what the commits list's parent-branch header loads
# (`<default>...<parent>`). Two-dot ranges are not a load: Collins names
# nothing for them. `Loaded` is what the page's loaded/load()/page_state
# carry.
SHOW_KEY = "show"
RANGE_KEY = "range"
RANGE_DOTS = "..."
Loaded = str | dict
# The longest path a tool call or a saved layout may name: a repo-relative
# path, and still foreign content (a widget's label, a reveal target).
# Anything longer is dropped, never truncated — a cut path names nothing.
MAX_PATH_CHARS = 512
# The diff view's layouts (the git_layout setting's domain, diffview.
# set_options): side by side, stacked, or whichever fits the width.
# LAYOUTS[0] is the default; anything else normalises to it
# (Options.from_settings). Not MODES — those are the page's loads.
LAYOUTS: tuple[str, ...] = ("auto", "split", "stack")
DEFAULT_LAYOUT = LAYOUTS[0]
# The commits-per-group page the sidebar's commits list loads (the
# git_log_page setting), its default and the clamp.
LOG_PAGE = 20
MIN_LOG_PAGE = 5
MAX_LOG_PAGE = 500
# The most footer apps the files list's "Open In…" submenu lists (the
# setting is the user's own, but a menu longer than this helps nobody).
MAX_FOOTER_APPS = 32
# The show_diff session tool's whole budget — under the CLI's own MCP
# timeout (the shim's 15 s) — and how often it polls the page for its
# load to land (app._ShowDiff).
SHOW_DIFF_DEADLINE_S = 12.0
SHOW_DIFF_POLL_MS = 250
# A ref that is safe as an argument and as a title token: the same rule as
# gitinfo._safe_branch_name (non-empty, no whitespace, no leading "-", no
# "..") plus a length cap, since a title token or a persisted string is
# nobody's promise. Full shas are 40 (64 for sha256 repositories).
_MAX_REF_LEN = 128
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA_LEN = 7
# commit_subject's `git log -1`, resolve_commit's `git rev-parse`: one
# subprocess each, on a worker thread, against a repository that is there.
GIT_TIMEOUT_S = 5.0

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


@dataclass(frozen=True)
class Options:
    """What Preferences → Git decides about the git page, normalised (see
    from_settings): the layout (one of LAYOUTS), whether working-tree
    reviews include untracked files, the commits-per-group page, and the
    diff view's four knobs (the line-number columns, wrapping, the word
    emphasis, whitespace-only changes drawn as context). The defaults are
    the shipped settings' — a page that never received settings runs on
    them."""

    layout: str = DEFAULT_LAYOUT
    untracked: bool = True
    log_page: int = LOG_PAGE
    line_numbers: bool = True
    wrap: bool = False
    word_diff: bool = True
    hide_whitespace: bool = False
    # Preferences → Footer apps, not Git: the desktop-file ids the files
    # list's "Open In…" submenu offers (gitsidebar resolves them live).
    footer_apps: tuple[str, ...] = ()

    @classmethod
    def from_settings(cls, settings: Mapping) -> Options:
        """An Options out of the whole settings dict, tolerant of every
        key being missing or wrong: git_layout not in LAYOUTS → "auto";
        git_untracked as a bool (absent: on); git_log_page as an int
        clamped to MIN_LOG_PAGE..MAX_LOG_PAGE (garbage: LOG_PAGE);
        git_line_numbers, git_wrap_lines, git_word_diff and
        git_hide_whitespace as bools (absent: on, off, on, off);
        footer_apps as the str entries of a list, at most MAX_FOOTER_APPS
        (anything else: none)."""
        layout = settings.get("git_layout")
        if layout not in LAYOUTS:
            layout = DEFAULT_LAYOUT
        untracked = settings.get("git_untracked", True)
        try:
            log_page = int(settings.get("git_log_page", LOG_PAGE))
        except (TypeError, ValueError):
            log_page = LOG_PAGE
        log_page = max(MIN_LOG_PAGE, min(MAX_LOG_PAGE, log_page))
        apps = settings.get("footer_apps")
        if not isinstance(apps, list | tuple):
            apps = ()
        footer_apps = tuple(
            app for app in apps if isinstance(app, str) and 0 < len(app) <= MAX_PATH_CHARS
        )[:MAX_FOOTER_APPS]
        return cls(
            layout=layout,
            untracked=bool(untracked),
            log_page=log_page,
            line_numbers=bool(settings.get("git_line_numbers", True)),
            wrap=bool(settings.get("git_wrap_lines", False)),
            word_diff=bool(settings.get("git_word_diff", True)),
            hide_whitespace=bool(settings.get("git_hide_whitespace", False)),
            footer_apps=footer_apps,
        )


def safe_ref(name: object) -> bool:
    """Whether *name* can be handed to git as one revision: a non-empty
    str, no whitespace, no leading "-", no ".." (a range), at most
    _MAX_REF_LEN chars. gitinfo._safe_branch_name's rule, for what arrives
    from a tool call or a saved layout."""
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


def range_halves(text: object) -> tuple[str, str] | None:
    """(left, right) of a range load's text — `a...b` with exactly one
    three-dot separator, both halves safe refs (safe_ref, which refuses
    `..`, so `a....b` and `a...b...c` are out too) that neither start nor
    end with a dot (no ref does; `.b` would read as a range git can't
    take). None for anything else: a two-dot range, a range with a
    pathspec, a non-string."""
    if not isinstance(text, str) or len(text) > 2 * _MAX_REF_LEN + len(RANGE_DOTS):
        return None
    parts = text.split(RANGE_DOTS)
    if len(parts) != 2:
        return None
    left, right = parts
    for half in (left, right):
        if not safe_ref(half) or half.startswith(".") or half.endswith("."):
            return None
    return left, right


def is_range(loaded: object) -> bool:
    """Whether *loaded* is a range load, {"range": "a...b"} with a text
    range_halves accepts."""
    return isinstance(loaded, dict) and range_halves(loaded.get(RANGE_KEY)) is not None


def range_of(loaded: object) -> str | None:
    """The `a...b` of a range load, None for anything else."""
    return loaded[RANGE_KEY] if is_range(loaded) else None


def loaded_ok(loaded: object) -> bool:
    """Whether *loaded* is something the page can spawn into: one of MODES,
    a well-formed commit load, or a well-formed range load."""
    return (isinstance(loaded, str) and loaded in MODES) or is_show(loaded) or is_range(loaded)


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


# The whole of a commit message the page's card holds: its subject and
# body are repository content (rule 5) — a body past this is cut, and the
# fold's own "Show more" step is what the reader sees of that.
COMMIT_BODY_MAX_CHARS = 20_000
_COMMIT_FIELD_MAX_CHARS = 200
_COMMIT_FORMAT = "%H%x00%an%x00%aI%x00%s%x00%b"


@dataclass(frozen=True)
class CommitMessage:
    """What `commit_message` reads of one commit: the full *sha*, the
    author's name, the author date as git's strict ISO 8601 (`%aI`), the
    *subject* and the *body* (the message past the subject, the blank line
    dropped, cut at `COMMIT_BODY_MAX_CHARS`; "" for a one-line message)."""

    sha: str
    author: str
    authored_at: str
    subject: str
    body: str


def commit_message(
    cwd: str | None, ref: object, run=subprocess.run, timeout: float = GIT_TIMEOUT_S
) -> CommitMessage | None:
    """The commit *ref* names, whole — one `git log -1
    --format=%H%x00%an%x00%aI%x00%s%x00%b <ref>^{commit} --` on a worker
    thread — for the page's commit card. None whenever there isn't a
    commit to describe: no cwd, a ref that isn't safe to ask about, git
    saying it names no commit, git not answering, or an answer without a
    full sha (the card is optional; a page opened into a saved commit
    already names it through `commit_subject`). Every field is bounded, the
    body last so a huge one can't hide the others."""
    if not cwd or not safe_ref(ref):
        return None
    argv = ["git", "log", "-1", f"--format={_COMMIT_FORMAT}", f"{ref}^{{commit}}", "--"]
    try:
        result = run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    parts = (result.stdout or "").split("\0", 4)
    if len(parts) != 5:
        return None
    sha, author, authored_at, subject, body = parts
    sha = sha.strip()
    if not _FULL_SHA.match(sha):
        return None
    return CommitMessage(
        sha=sha,
        author=author.strip()[:_COMMIT_FIELD_MAX_CHARS],
        authored_at=authored_at.strip()[:_COMMIT_FIELD_MAX_CHARS],
        subject=subject.strip()[:_COMMIT_FIELD_MAX_CHARS],
        body=body.strip("\n")[:COMMIT_BODY_MAX_CHARS].rstrip(),
    )


# A line that starts something other than running prose: a heading, a
# quote, a list item, a table row, a fence, a horizontal rule — or a git
# trailer (`Co-Authored-By: …`), which stays on its own line.
_BLOCK_START_RE = re.compile(r"^ {0,3}(#{1,6}(\s|$)|>|[-*+]\s|\d{1,9}[.)]\s|\||```|~~~|([-*_]\s*){3,}$)")
_TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: \S")
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")


def reflow_body(body: str) -> str:
    """*body* with git's hard wraps undone: consecutive prose lines of one
    paragraph joined by a space, so a message wrapped at 72 columns
    reflows to the card's width like a PR description does. Left as they
    are: blank lines, fenced code (whole), indented code (four spaces),
    headings, quotes, list items, table rows, rules, git trailers, and a
    line ending in two spaces or a backslash (a markdown hard break). A
    line indented one to three spaces with no marker of its own is a
    continuation — of a list item too — and joins the line above."""
    out: list[str] = []
    in_fence = False
    prev_joinable = False  # the last line written is prose (or a list item) a continuation may join
    for line in body.split("\n"):
        if in_fence:
            out.append(line)
            if _FENCE_RE.match(line):
                in_fence = False
            continue
        if _FENCE_RE.match(line):
            in_fence = True
            out.append(line)
            prev_joinable = False
            continue
        stripped = line.strip()
        if not stripped or line.startswith(("    ", "\t")):
            out.append(line)
            prev_joinable = False
            continue
        block_start = _BLOCK_START_RE.match(line) is not None
        trailer = _TRAILER_RE.match(stripped) is not None
        list_item = block_start and re.match(r"^ {0,3}([-*+]|\d{1,9}[.)])\s", line) is not None
        if prev_joinable and not block_start and not trailer:
            out[-1] = out[-1].rstrip() + " " + stripped
        else:
            out.append(line)
        hard_break = line.endswith(("  ", "\\"))
        prev_joinable = (not block_start or list_item) and not trailer and not hard_break
    return "\n".join(out)


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
    """The path the diff view reveals — repo-relative, "/"-separated, as
    the files panel shows it — out of what the agent handed the tool: an
    absolute path inside *repo_root*, or a relative one. A relative path
    is taken against the repository root (that is how a diff names files)
    unless it is only there against the agent's *cwd* (an agent that cd'd
    into a subdirectory and named a file the way its shell sees it) —
    *exists* decides, injectable for the tests. None for anything that
    can't be a file in the diff: empty, escaping the root, a leading "-"
    (git would read it as a flag)."""
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


# -- what the page shows for a load, and the layout slot ------------------------


def breadcrumb(loaded: Loaded, branch: str | None, parent: str | None, subject: str | None = None) -> str:
    """Header text: _("working tree · unstaged"), _("working tree · staged"),
    _("{branch} vs {parent}") (branch/parent fall back to "HEAD" / "?" when
    None), or for a commit "<ref> <subject>" with the ref cut short
    (short_ref) — `a1b2c3d Wire the mode switch` — and _("commit {ref}")
    while the *subject* isn't known (see commit_subject). A range `a...b`
    reads right against left, as git's symmetric difference does: the
    same _("{branch} vs {parent}") with b as the branch and a as the
    parent (shas cut short)."""
    if is_show(loaded):
        ref = short_ref(show_ref(loaded))
        if subject:
            return f"{ref} {subject}"
        return _("commit {ref}").format(ref=ref)
    halves = range_halves(range_of(loaded))
    if halves is not None:
        left, right = halves
        return _("{branch} vs {parent}").format(branch=short_ref(right), parent=short_ref(left))
    if loaded == "unstaged":
        return _("working tree · unstaged")
    if loaded == "staged":
        return _("working tree · staged")
    return _("{branch} vs {parent}").format(branch=branch or "HEAD", parent=parent or "?")


def tab_title(loaded: Loaded, parent: str | None) -> str:
    """Tab text: _("Git · unstaged"), _("Git · staged"), _("Git · vs {parent}"),
    _("Git · {ref}") for a commit (short_ref) and for a range `a...b` —
    named by its right half, b, the branch under review."""
    if is_show(loaded):
        return _("Git · {ref}").format(ref=short_ref(show_ref(loaded)))
    halves = range_halves(range_of(loaded))
    if halves is not None:
        return _("Git · {ref}").format(ref=short_ref(halves[1]))
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


def encode_state(loaded: Loaded, sidebar: bool = True) -> dict:
    """{"kind": "git", "loaded": loaded, "sidebar": False} — the page's
    panel_layout slot; "sidebar" only when the native sidebar is hidden
    (absent reads as shown, see decode_sidebar). A commit or range load is
    its dict, copied. A "parent" key older layouts carry (the branch a
    user set before git alone named the stack) is ignored on decode."""
    state = {"kind": "git", "loaded": dict(loaded) if isinstance(loaded, dict) else loaded}
    if not sidebar:
        state["sidebar"] = False
    return state


def decode_state(page: object) -> Loaded:
    """What a saved page dict asks for: one of MODES, a validated {"show":
    ref} or {"range": "a...b"}; anything else (garbage, an unsafe ref, a
    non-dict) reads as DEFAULT_MODE — the layout is a preference, restore
    never refuses on it."""
    if not isinstance(page, dict):
        return DEFAULT_MODE
    loaded = page.get("loaded")
    if is_show(loaded):
        return {SHOW_KEY: loaded[SHOW_KEY]}
    if is_range(loaded):
        return {RANGE_KEY: loaded[RANGE_KEY]}
    return loaded if isinstance(loaded, str) and loaded in MODES else DEFAULT_MODE


def decode_sidebar(page: object) -> bool:
    """Whether a saved page dict shows the native sidebar: False only
    when it says so (`"sidebar": false`); absent, a non-bool or a non-dict
    read as True — the sidebar is the page's default face."""
    if not isinstance(page, dict):
        return True
    sidebar = page.get("sidebar")
    return sidebar if isinstance(sidebar, bool) else True
