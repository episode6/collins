# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for gitloads: the git page's `Loaded` vocabulary — the shape checks
on modes, commit and range loads and the refs inside them, the Options
Preferences → Git normalises into, the breadcrumb and tab title each load
wears, a commit's subject and sha and the sha a ref resolves to (against a
fake `run`), the Ctrl+1/2/3 chords, the initial mode, the layout slot, and
the show_diff tool's reading of its `what` and `file` arguments. Split out
of the old viewer module's tests with the module."""

import ast
import json
import subprocess

import pytest

from collins import gitloads

SHA = "0123456789abcdef0123456789abcdef01234567"


# -- Preferences → Git ---------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"git_layout": "split"}, gitloads.Options(layout="split")),
        ({"git_layout": "stack"}, gitloads.Options(layout="stack")),
        ({"git_layout": "bogus"}, gitloads.Options()),
        ({"git_layout": None}, gitloads.Options()),
        ({"git_untracked": 0}, gitloads.Options(untracked=False)),
        ({"git_untracked": False}, gitloads.Options(untracked=False)),
        ({"git_untracked": "yes"}, gitloads.Options(untracked=True)),
        ({"git_log_page": 50}, gitloads.Options(log_page=50)),
        ({"git_log_page": "50"}, gitloads.Options(log_page=50)),
        ({"git_log_page": 3}, gitloads.Options(log_page=5)),
        ({"git_log_page": 1000}, gitloads.Options(log_page=500)),
        ({"git_log_page": "abc"}, gitloads.Options(log_page=20)),
        ({"git_log_page": None}, gitloads.Options(log_page=20)),
        ({"git_line_numbers": 0}, gitloads.Options(line_numbers=False)),
        ({"git_wrap_lines": 1}, gitloads.Options(wrap=True)),
        ({"git_word_diff": False}, gitloads.Options(word_diff=False)),
    ],
)
def test_options_from_settings_normalises_each_key(settings, expected):
    assert gitloads.Options.from_settings(settings) == expected


def test_options_from_settings_reads_the_whole_dict():
    settings = {
        "font": "Monospace 11",
        "git_layout": "stack",
        "git_theme": "catppuccin-mocha",  # a stale key from the terminal viewer's days: ignored
        "git_untracked": False,
        "git_log_page": 40,
    }
    expected = gitloads.Options("stack", False, 40)
    assert gitloads.Options.from_settings(settings) == expected


def test_options_have_no_theme_and_no_viewer():
    """Decision 3 of the native-diff spec: the diff follows the editor's
    scheme, and the viewer switch went with the terminal viewer."""
    assert not hasattr(gitloads.Options(), "theme")
    assert not hasattr(gitloads.Options(), "viewer")
    assert not hasattr(gitloads, "safe_theme")


# -- loads -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("main", True),
        ("origin/main", True),
        ("feature/x-1", True),
        (SHA, True),
        ("HEAD", True),
        ("", False),
        (None, False),
        (42, False),
        ("-x", False),
        ("--staged", False),
        ("a..b", False),
        ("a...b", False),
        ("with space", False),
        ("tab\there", False),
        ("x" * 129, False),
    ],
)
def test_safe_ref(name, ok):
    assert gitloads.safe_ref(name) is ok


def test_show_helpers():
    assert gitloads.is_show({"show": SHA})
    assert gitloads.show_ref({"show": "HEAD"}) == "HEAD"
    assert not gitloads.is_show({"show": "-x"})
    assert not gitloads.is_show({"show": ""})
    assert not gitloads.is_show("show")
    assert not gitloads.is_show({})
    assert gitloads.show_ref("unstaged") is None
    for mode in gitloads.MODES:
        assert gitloads.loaded_ok(mode)
    assert gitloads.loaded_ok({"show": SHA})
    assert not gitloads.loaded_ok("commit")
    assert not gitloads.loaded_ok({"show": "a..b"})
    assert not gitloads.loaded_ok(None)


@pytest.mark.parametrize(
    ("text", "halves"),
    [
        ("main...feat", ("main", "feat")),
        ("origin/main...release/v1", ("origin/main", "release/v1")),
        (SHA + "...HEAD", (SHA, "HEAD")),
        (SHA + "^...HEAD", (SHA + "^", "HEAD")),
        ("main..feat", None),
        ("main....feat", None),
        ("main...feat...x", None),
        ("...feat", None),
        ("main...", None),
        ("main...-x", None),
        ("-x...main", None),
        ("main....", None),
        ("main.... feat", None),
        ("a b...c", None),
        ("main", None),
        ("", None),
        (None, None),
        (3, None),
        ("x" * 129 + "...y", None),
    ],
)
def test_range_halves(text, halves):
    assert gitloads.range_halves(text) == halves
    assert gitloads.is_range({"range": text}) is (halves is not None)


def test_range_helpers():
    assert gitloads.range_of({"range": "main...feat"}) == "main...feat"
    assert gitloads.range_of({"range": "main..feat"}) is None
    assert gitloads.range_of({"show": SHA}) is None
    assert gitloads.range_of("branch") is None
    assert gitloads.range_of(None) is None
    assert not gitloads.is_range({"show": "main...feat"})
    assert not gitloads.is_range({})
    assert gitloads.loaded_ok({"range": "main...feat"})
    assert not gitloads.loaded_ok({"range": "main..feat"})
    assert not gitloads.is_show({"range": "main...feat"})
    # show_diff keeps refusing ranges: safe_ref rejects `..`.
    assert gitloads.show_diff_load("main...feat") is None


def test_range_breadcrumb_and_tab_title():
    """`left...right` reads as right against left: b vs a."""
    assert gitloads.breadcrumb({"range": "main...feat"}, "x", "y") == "feat vs main"
    assert gitloads.breadcrumb({"range": SHA + "...HEAD"}, None, None) == "HEAD vs 0123456"
    assert gitloads.tab_title({"range": "main...feat"}, "main") == "Git · feat"
    assert gitloads.tab_title({"range": "main..." + SHA}, None) == "Git · 0123456"


def test_short_ref():
    assert gitloads.short_ref(SHA) == "0123456"
    assert gitloads.short_ref("HEAD") == "HEAD"
    assert gitloads.short_ref("a1b2c3d") == "a1b2c3d"
    assert gitloads.short_ref("main") == "main"
    assert gitloads.short_ref("") == ""


# -- titles for loads --------------------------------------------------------


def test_breadcrumb():
    assert gitloads.breadcrumb("unstaged", "feat", "main") == "working tree · unstaged"
    assert gitloads.breadcrumb("staged", None, None) == "working tree · staged"
    assert gitloads.breadcrumb("branch", "feat", "main") == "feat vs main"
    assert gitloads.breadcrumb("branch", None, None) == "HEAD vs ?"
    assert gitloads.breadcrumb({"show": SHA}, "feat", "main") == "commit 0123456"
    assert gitloads.breadcrumb({"show": "HEAD"}, None, None) == "commit HEAD"
    # A commit is named by its subject once known: the spec's `a1b2c3 Wire the mode switch`.
    named = gitloads.breadcrumb({"show": SHA}, "feat", "main", "Wire the mode switch")
    assert named == "0123456 Wire the mode switch"
    assert gitloads.breadcrumb({"show": "HEAD"}, None, None, "") == "commit HEAD"
    assert gitloads.breadcrumb("unstaged", None, None, "ignored for a mode") == "working tree · unstaged"


def test_commit_subject_resolves():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="Wire the mode switch\n", stderr="")

    assert gitloads.commit_subject("/repo", SHA, run=run) == "Wire the mode switch"
    argv, kwargs = calls[0]
    assert argv == ["git", "log", "-1", "--format=%s", f"{SHA}^{{commit}}", "--"]
    assert kwargs["cwd"] == "/repo"
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["timeout"] == gitloads.GIT_TIMEOUT_S


def test_commit_subject_and_sha_resolves():
    """One `git log -1 --format=%s%x00%H`: the subject for the breadcrumb
    and the full sha the native commits list matches a `show HEAD` title
    against."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=f"Wire the mode switch\0{SHA}\n", stderr="")

    assert gitloads.commit_subject_and_sha("/repo", "HEAD", run=run) == ("Wire the mode switch", SHA)
    argv, kwargs = calls[0]
    assert argv == ["git", "log", "-1", "--format=%s%x00%H", "HEAD^{commit}", "--"]
    assert kwargs["cwd"] == "/repo"
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["timeout"] == gitloads.GIT_TIMEOUT_S


def test_commit_subject_and_sha_three_answers():
    """The subject follows commit_subject's answers; the sha is None
    whenever there isn't a full one — an empty subject still carries it."""

    def gone(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: bad revision")

    def empty_subject(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=f"\0{SHA}\n", stderr="")

    def short(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="subject\0abc123\n", stderr="")

    def nothing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="\n", stderr="")

    def missing(argv, **kwargs):
        raise FileNotFoundError("git")

    assert gitloads.commit_subject_and_sha("/repo", "deadbeef", run=gone) == (None, None)
    assert gitloads.commit_subject_and_sha("/repo", "HEAD", run=empty_subject) == ("", SHA)
    assert gitloads.commit_subject_and_sha("/repo", "HEAD", run=short) == ("subject", None)
    assert gitloads.commit_subject_and_sha("/repo", "HEAD", run=nothing) == ("", None)
    assert gitloads.commit_subject_and_sha("/repo", "HEAD", run=missing) == ("", None)
    assert gitloads.commit_subject_and_sha(None, "HEAD", run=short) == (None, None)
    assert gitloads.commit_subject_and_sha("/repo", "a..b", run=short) == (None, None)


def test_commit_subject_three_answers():
    """A subject; None when git says the ref names no commit; "" when git
    couldn't be asked — the ref is not disproven."""

    def gone(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: bad revision")

    def empty(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="\n", stderr="")

    def missing(argv, **kwargs):
        raise FileNotFoundError("git")

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    assert gitloads.commit_subject("/repo", "deadbeef", run=gone) is None
    assert gitloads.commit_subject("/repo", "HEAD", run=empty) == ""
    assert gitloads.commit_subject("/repo", "HEAD", run=missing) == ""
    assert gitloads.commit_subject("/repo", "HEAD", run=slow) == ""
    # Nothing to ask about: no cwd, or a ref git could misread.
    assert gitloads.commit_subject(None, "HEAD", run=empty) is None
    assert gitloads.commit_subject("/repo", "-x", run=empty) is None
    assert gitloads.commit_subject("/repo", "a..b", run=empty) is None
    assert gitloads.commit_subject("/repo", None, run=empty) is None


def test_tab_title():
    assert gitloads.tab_title("unstaged", "main") == "Git · unstaged"
    assert gitloads.tab_title("staged", None) == "Git · staged"
    assert gitloads.tab_title("branch", "main") == "Git · vs main"
    assert gitloads.tab_title("branch", None) == "Git · vs ?"
    assert gitloads.tab_title({"show": SHA}, "main") == "Git · 0123456"
    assert gitloads.tab_title({"show": "v1.2"}, None) == "Git · v1.2"


# -- chords ------------------------------------------------------------------

CONTROL = 1 << 2
SHIFT = 1 << 0
ALT = 1 << 3
SUPER = 1 << 26


def test_load_for_key_ctrl_digits():
    assert gitloads.load_for_key(0x31, CONTROL) == "unstaged"
    assert gitloads.load_for_key(0x32, CONTROL) == "staged"
    assert gitloads.load_for_key(0x33, CONTROL) == "branch"


def test_load_for_key_keypad():
    assert gitloads.load_for_key(0xFFB1, CONTROL) == "unstaged"
    assert gitloads.load_for_key(0xFFB2, CONTROL) == "staged"
    assert gitloads.load_for_key(0xFFB3, CONTROL) == "branch"


def test_load_for_key_shift_tolerated():
    assert gitloads.load_for_key(0x31, CONTROL | SHIFT) == "unstaged"


def test_load_for_key_other_modifiers_refused():
    assert gitloads.load_for_key(0x31, CONTROL | ALT) is None
    assert gitloads.load_for_key(0x31, CONTROL | SUPER) is None


def test_load_for_key_bare_and_other_digits():
    assert gitloads.load_for_key(0x31, 0) is None
    assert gitloads.load_for_key(0x31, SHIFT) is None
    assert gitloads.load_for_key(0x34, CONTROL) is None
    assert gitloads.load_for_key(0xFF1B, CONTROL) is None


# -- initial mode ------------------------------------------------------------


@pytest.mark.parametrize(
    ("staged", "unstaged", "expected"),
    [
        (False, False, "unstaged"),
        (False, True, "unstaged"),
        (True, True, "unstaged"),
        (True, False, "staged"),
    ],
)
def test_initial_mode(staged, unstaged, expected):
    assert gitloads.initial_mode(staged, unstaged) == expected


# -- layout slot -------------------------------------------------------------


def test_encode_state():
    assert gitloads.encode_state("staged") == {"kind": "git", "loaded": "staged"}
    assert gitloads.encode_state("branch") == {"kind": "git", "loaded": "branch"}
    state = gitloads.encode_state({"show": SHA})
    assert state == {"kind": "git", "loaded": {"show": SHA}}
    assert json.loads(json.dumps(state)) == state  # what panellayout writes


def test_encode_state_copies_the_show_dict():
    loaded = {"show": SHA}
    state = gitloads.encode_state(loaded)
    state["loaded"]["show"] = "HEAD"
    assert loaded == {"show": SHA}


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ({"kind": "git", "loaded": "staged"}, "staged"),
        ({"kind": "git", "loaded": "branch"}, "branch"),
        ({"kind": "git", "loaded": "unstaged"}, "unstaged"),
        ({"kind": "git", "loaded": {"show": "abc"}}, {"show": "abc"}),
        ({"kind": "git", "loaded": {"show": SHA}, "parent": "base"}, {"show": SHA}),
        ({"kind": "git", "loaded": {"show": "-x"}}, "unstaged"),
        ({"kind": "git", "loaded": {"show": ""}}, "unstaged"),
        ({"kind": "git", "loaded": {"commit": "abc"}}, "unstaged"),
        ({"kind": "git", "loaded": "commit"}, "unstaged"),
        ({"kind": "git"}, "unstaged"),
        ("git", "unstaged"),
        (None, "unstaged"),
    ],
)
def test_decode_state(page, expected):
    assert gitloads.decode_state(page) == expected


def test_state_round_trips():
    for mode in gitloads.MODES:
        assert gitloads.decode_state(gitloads.encode_state(mode)) == mode
    state = gitloads.encode_state({"show": SHA})
    assert gitloads.decode_state(state) == {"show": SHA}
    # A layout from before git alone named the stack carries the branch the
    # user had set; it is ignored, not refused.
    assert gitloads.decode_state({"kind": "git", "loaded": "branch", "parent": "base"}) == "branch"


def test_range_state_round_trips():
    state = gitloads.encode_state({"range": "main...feat"})
    assert state == {"kind": "git", "loaded": {"range": "main...feat"}}
    assert json.loads(json.dumps(state)) == state
    assert gitloads.decode_state(state) == {"range": "main...feat"}
    # A malformed saved range reads as the default, like a malformed show.
    assert gitloads.decode_state({"kind": "git", "loaded": {"range": "main..feat"}}) == "unstaged"
    assert gitloads.decode_state({"kind": "git", "loaded": {"range": "-x...y"}}) == "unstaged"
    assert gitloads.decode_state({"kind": "git", "loaded": {"range": 3}}) == "unstaged"
    loaded = {"range": "main...feat"}
    gitloads.encode_state(loaded)["loaded"]["range"] = "x...y"
    assert loaded == {"range": "main...feat"}  # copied, as a show is


def test_sidebar_state_round_trips():
    """"sidebar" is written only when hidden; absent reads as shown."""
    assert gitloads.encode_state("staged") == {"kind": "git", "loaded": "staged"}
    assert gitloads.encode_state("staged", sidebar=True) == {"kind": "git", "loaded": "staged"}
    hidden = gitloads.encode_state("staged", sidebar=False)
    assert hidden == {"kind": "git", "loaded": "staged", "sidebar": False}
    assert gitloads.decode_sidebar(hidden) is False
    assert gitloads.decode_sidebar(gitloads.encode_state("staged")) is True
    assert gitloads.decode_state(hidden) == "staged"


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ({"kind": "git", "sidebar": False}, False),
        ({"kind": "git", "sidebar": True}, True),
        ({"kind": "git"}, True),
        ({"kind": "git", "sidebar": 0}, True),
        ({"kind": "git", "sidebar": "no"}, True),
        ({"kind": "git", "sidebar": None}, True),
        ("git", True),
        (None, True),
    ],
)
def test_decode_sidebar(page, expected):
    assert gitloads.decode_sidebar(page) is expected


# -- the show_diff tool's decisions ------------------------------------------


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_resolve_commit_runs_rev_parse_and_returns_the_sha():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _Result(0, SHA + "\n")

    assert gitloads.resolve_commit("/repo", "HEAD~1", run=run) == SHA
    argv, kwargs = calls[0]
    assert argv == ["git", "rev-parse", "--verify", "--quiet", "HEAD~1^{commit}"]
    assert kwargs["cwd"] == "/repo"
    assert kwargs["timeout"] == gitloads.GIT_TIMEOUT_S


def test_resolve_commit_three_answers():
    """None: git says it names no commit. "": git couldn't be asked. Neither
    call happens for a ref that isn't safe as an argument."""
    assert gitloads.resolve_commit("/repo", "nope", run=lambda *a, **k: _Result(1)) is None
    assert gitloads.resolve_commit("/repo", "HEAD", run=lambda *a, **k: _Result(0, "garbage sha")) is None

    def boom(*a, **k):
        raise OSError("no git")

    assert gitloads.resolve_commit("/repo", "HEAD", run=boom) == ""

    def never(*a, **k):
        raise AssertionError("must not run")

    assert gitloads.resolve_commit("/repo", "--output=x", run=never) is None
    assert gitloads.resolve_commit("/repo", "a..b", run=never) is None
    assert gitloads.resolve_commit(None, "HEAD", run=never) is None


@pytest.mark.parametrize(
    ("what", "expected"),
    [
        ("unstaged", "unstaged"),
        ("staged", "staged"),
        ("branch", "branch"),
        ("HEAD~2", {"show": "HEAD~2"}),
        (SHA, {"show": SHA}),
        ("v1.2", {"show": "v1.2"}),
        ("", None),
        ("main..feat", None),
        ("--output=x", None),
        ("two words", None),
        (None, None),
        (7, None),
    ],
)
def test_show_diff_load(what, expected):
    assert gitloads.show_diff_load(what) == expected


def test_diff_file_path_is_repo_relative():
    root = "/repo"
    assert gitloads.diff_file_path("src/a.py", root) == "src/a.py"
    assert gitloads.diff_file_path("./src/a.py", root) == "src/a.py"
    assert gitloads.diff_file_path("/repo/src/a.py", root) == "src/a.py"
    assert gitloads.diff_file_path(" src/a.py ", root) == "src/a.py"
    assert gitloads.diff_file_path("src/../a.py", root) == "a.py"


def test_diff_file_path_refuses_what_cannot_be_in_the_diff():
    root = "/repo"
    assert gitloads.diff_file_path("", root) is None
    assert gitloads.diff_file_path("   ", root) is None
    assert gitloads.diff_file_path(".", root) is None
    assert gitloads.diff_file_path("../x.py", root) is None
    assert gitloads.diff_file_path("/etc/passwd", root) is None
    assert gitloads.diff_file_path("-flag", root) is None
    assert gitloads.diff_file_path("src/a.py", None) is None
    assert gitloads.diff_file_path(None, root) is None


def test_diff_file_path_prefers_the_agent_cwd_when_only_it_has_the_file():
    """An agent that cd'd into a subdirectory names files the way its shell
    sees them; a path that is only there against its cwd is taken from
    there — and one that exists against the root wins outright."""
    root = "/repo"
    cwd = "/repo/pkg"
    only_in_pkg = {"/repo/pkg/a.py"}
    assert gitloads.diff_file_path("a.py", root, cwd, exists=only_in_pkg.__contains__) == "pkg/a.py"
    both = {"/repo/a.py", "/repo/pkg/a.py"}
    assert gitloads.diff_file_path("a.py", root, cwd, exists=both.__contains__) == "a.py"
    neither = set()
    assert gitloads.diff_file_path("a.py", root, cwd, exists=neither.__contains__) == "a.py"
    # The cwd never lifts a path out of the repository.
    assert gitloads.diff_file_path("../../x.py", root, cwd, exists=lambda _p: True) is None


# -- the module itself ----------------------------------------------------------


def test_options_defaults_reproduce_the_shipped_settings():
    """A page that never received settings, and an empty dict, both run on
    the defaults."""
    options = gitloads.Options()
    assert options == gitloads.Options(layout="auto", untracked=True, log_page=20)
    assert gitloads.Options.from_settings({}) == options
    assert gitloads.LAYOUTS == ("auto", "split", "stack")
    assert gitloads.DEFAULT_LAYOUT == "auto"
    assert (gitloads.MIN_LOG_PAGE, gitloads.LOG_PAGE, gitloads.MAX_LOG_PAGE) == (5, 20, 500)


def test_vocabulary_constants():
    assert gitloads.MODES == ("unstaged", "staged", "branch")
    assert gitloads.DEFAULT_MODE == "unstaged"
    assert (gitloads.SHOW_KEY, gitloads.RANGE_KEY, gitloads.RANGE_DOTS) == ("show", "range", "...")
    assert gitloads.MAX_PATH_CHARS == 512
    assert gitloads.GIT_TIMEOUT_S == 5.0
    assert gitloads.SHOW_DIFF_DEADLINE_S == 12.0 and gitloads.SHOW_DIFF_POLL_MS == 250


def test_gitloads_stands_alone():
    """The module is the Loaded vocabulary alone: nothing of a widget's,
    nothing of GTK's."""
    with open(gitloads.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {name for name in imported if name.startswith("gi")}
    assert imported == {"__future__", "os", "re", "subprocess", "collections.abc", "dataclasses", "i18n"}
