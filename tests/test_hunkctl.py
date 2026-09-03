# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for hunkctl: the git page's decisions that need no widget — the
version gate, argv (the bundled extension on it, and what Preferences → Git
adds), the Options those settings normalise into, hunk's JSON replies,
session titles (a commit's included), the chords, the layout slot (with the
user-set parent), the sidecar the page shares with the extension, and the
show_diff tool's decisions (what it loads, the file path it hands hunk, the
navigate argv, the reply)."""

import json
import os
import signal
import subprocess

import pytest

from collins import hunkctl

EXT = "/opt/collins/hunkext/collins-git"
SHA = "0123456789abcdef0123456789abcdef01234567"

# -- version gate ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.20.1\n", (0, 20, 1)),
        ("0.19.9", (0, 19, 9)),
        ("hunk 0.21.0", (0, 21, 0)),
        ("1.0", (1, 0)),
        ("", None),
        ("no version here", None),
    ],
)
def test_parse_version(text, expected):
    assert hunkctl.parse_version(text) == expected


def test_version_ok_gates_on_min_version():
    assert hunkctl.version_ok((0, 20, 1))
    assert hunkctl.version_ok((0, 20))
    assert hunkctl.version_ok((1, 0, 0))
    assert not hunkctl.version_ok((0, 19, 9))
    assert not hunkctl.version_ok(None)


def _run_answering(stdout: str, returncode: int = 0):
    def run(argv, **kwargs):
        assert argv[-1] == "--version"
        assert kwargs["timeout"] == hunkctl.PROBE_TIMEOUT_S
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return run


def test_probe_missing():
    probe = hunkctl.probe(which=lambda _name: None, run=_run_answering("0.20.1"))
    assert probe == hunkctl.Probe(None, None)
    assert probe.status == "missing"


def test_probe_old():
    probe = hunkctl.probe(which=lambda _name: "/usr/local/bin/hunk", run=_run_answering("0.19.9\n"))
    assert probe.path == "/usr/local/bin/hunk"
    assert probe.version == (0, 19, 9)
    assert probe.status == "old"


def test_probe_ok():
    probe = hunkctl.probe(which=lambda _name: "/usr/local/bin/hunk", run=_run_answering("0.20.1\n"))
    assert probe.status == "ok"
    assert probe.version == (0, 20, 1)


def test_probe_run_raising_reads_as_old():
    """A hunk that can't say its version can't be trusted to have the session
    API either; the card names the version unknown."""

    def run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("hunk", hunkctl.PROBE_TIMEOUT_S)

    probe = hunkctl.probe(which=lambda _name: "/usr/local/bin/hunk", run=run)
    assert probe == hunkctl.Probe("/usr/local/bin/hunk", None)
    assert probe.status == "old"


def test_probe_nonzero_exit_reads_as_old():
    probe = hunkctl.probe(which=lambda _name: "/x/hunk", run=_run_answering("boom", returncode=1))
    assert probe.status == "old"


# -- argv --------------------------------------------------------------------


def test_diff_args_per_mode():
    assert hunkctl.diff_args("unstaged", "main") == []
    assert hunkctl.diff_args("unstaged", None) == []
    assert hunkctl.diff_args("staged", None) == ["--staged"]
    assert hunkctl.diff_args("branch", "main") == ["main...HEAD"]
    assert hunkctl.diff_args("branch", "origin/main") == ["origin/main...HEAD"]


def test_diff_args_rejects_branch_without_parent_and_unknown_modes():
    with pytest.raises(ValueError):
        hunkctl.diff_args("branch", None)
    with pytest.raises(ValueError):
        hunkctl.diff_args("show", "main")


def test_spawn_argv():
    assert hunkctl.spawn_argv("/usr/local/bin/hunk", "unstaged", None) == [
        "/usr/local/bin/hunk",
        "diff",
        "--watch",
        "--transparent-bg",
    ]
    assert hunkctl.spawn_argv("/h", "staged", None)[-1] == "--staged"
    assert hunkctl.spawn_argv("/h", "branch", "main")[-1] == "main...HEAD"


def test_spawn_argv_with_the_extension():
    """The --extension pair sits between the flags and the diff tail, and only
    when a directory is given (none: a broken install runs hunk bare)."""
    assert hunkctl.spawn_argv("/h", "unstaged", None, EXT) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--extension",
        EXT,
    ]
    assert hunkctl.spawn_argv("/h", "branch", "main", EXT)[-3:] == ["--extension", EXT, "main...HEAD"]
    assert hunkctl.spawn_argv("/h", "staged", None, None) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--staged",
    ]


def test_spawn_argv_show():
    """A saved commit spawns `hunk show <ref>`, the same flags in front."""
    assert hunkctl.spawn_argv("/h", {"show": SHA}, None, EXT) == [
        "/h",
        "show",
        "--watch",
        "--transparent-bg",
        "--extension",
        EXT,
        SHA,
    ]
    assert hunkctl.spawn_argv("/h", {"show": "HEAD"}, "main") == [
        "/h",
        "show",
        "--watch",
        "--transparent-bg",
        "HEAD",
    ]
    with pytest.raises(ValueError):
        hunkctl.spawn_argv("/h", {"show": "-x"}, None)
    with pytest.raises(ValueError):
        hunkctl.spawn_argv("/h", {"other": "x"}, None)


# -- Preferences → Git ---------------------------------------------------------


def test_options_defaults_reproduce_the_shipped_settings():
    """A page that never received settings, and an empty dict, both run on
    the defaults — and the defaults change no argv (see the exact lists
    above)."""
    options = hunkctl.Options()
    assert options == hunkctl.Options(layout="auto", theme="", untracked=True, log_page=20)
    assert hunkctl.Options.from_settings({}) == options
    assert hunkctl.LAYOUTS == ("auto", "split", "stack")
    assert hunkctl.DEFAULT_LAYOUT == "auto"
    assert (hunkctl.MIN_LOG_PAGE, hunkctl.LOG_PAGE, hunkctl.MAX_LOG_PAGE) == (5, 20, 500)
    assert hunkctl.spawn_flags(None) == []
    assert hunkctl.spawn_flags(options) == []


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"git_layout": "split"}, hunkctl.Options(layout="split")),
        ({"git_layout": "stack"}, hunkctl.Options(layout="stack")),
        ({"git_layout": "bogus"}, hunkctl.Options()),
        ({"git_layout": None}, hunkctl.Options()),
        ({"git_theme": "nord"}, hunkctl.Options(theme="nord")),
        ({"git_theme": "  nord "}, hunkctl.Options(theme="nord")),
        ({"git_theme": "-x"}, hunkctl.Options()),
        ({"git_theme": "a b"}, hunkctl.Options()),
        ({"git_theme": "x" * 65}, hunkctl.Options()),
        ({"git_theme": "x" * 64}, hunkctl.Options(theme="x" * 64)),
        ({"git_theme": 3}, hunkctl.Options()),
        ({"git_untracked": 0}, hunkctl.Options(untracked=False)),
        ({"git_untracked": False}, hunkctl.Options(untracked=False)),
        ({"git_untracked": "yes"}, hunkctl.Options(untracked=True)),
        ({"git_log_page": 50}, hunkctl.Options(log_page=50)),
        ({"git_log_page": "50"}, hunkctl.Options(log_page=50)),
        ({"git_log_page": 3}, hunkctl.Options(log_page=5)),
        ({"git_log_page": 1000}, hunkctl.Options(log_page=500)),
        ({"git_log_page": "abc"}, hunkctl.Options(log_page=20)),
        ({"git_log_page": None}, hunkctl.Options(log_page=20)),
    ],
)
def test_options_from_settings_normalises_each_key(settings, expected):
    assert hunkctl.Options.from_settings(settings) == expected


def test_options_from_settings_reads_the_whole_dict():
    settings = {
        "font": "Monospace 11",
        "git_layout": "stack",
        "git_theme": "catppuccin-mocha",
        "git_untracked": False,
        "git_log_page": 40,
    }
    assert hunkctl.Options.from_settings(settings) == hunkctl.Options("stack", "catppuccin-mocha", False, 40)


def test_safe_theme():
    assert hunkctl.safe_theme("nord")
    assert hunkctl.safe_theme("github-light-default")
    assert hunkctl.safe_theme("auto")
    assert not hunkctl.safe_theme("")
    assert not hunkctl.safe_theme("-nord")
    assert not hunkctl.safe_theme("no rd")
    assert not hunkctl.safe_theme("nord\n")
    assert not hunkctl.safe_theme(None)


OPTIONS = hunkctl.Options(layout="split", theme="nord", untracked=False, log_page=50)


def test_spawn_argv_with_options():
    """--mode/--theme after --transparent-bg and before --extension;
    --exclude-untracked right after `diff`, before --staged or the range."""
    assert hunkctl.spawn_argv("/h", "unstaged", None, EXT, OPTIONS) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--mode",
        "split",
        "--theme",
        "nord",
        "--extension",
        EXT,
        "--exclude-untracked",
    ]
    assert hunkctl.spawn_argv("/h", "staged", None, None, OPTIONS) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--mode",
        "split",
        "--theme",
        "nord",
        "--exclude-untracked",
        "--staged",
    ]
    assert hunkctl.spawn_argv("/h", "branch", "main", EXT, OPTIONS)[-4:] == [
        "--extension",
        EXT,
        "--exclude-untracked",
        "main...HEAD",
    ]


def test_spawn_argv_show_with_options():
    """`show` takes the layout and theme but never --exclude-untracked (hunk
    0.20.1 refuses it there: "unknown option")."""
    assert hunkctl.spawn_argv("/h", {"show": SHA}, None, EXT, OPTIONS) == [
        "/h",
        "show",
        "--watch",
        "--transparent-bg",
        "--mode",
        "split",
        "--theme",
        "nord",
        "--extension",
        EXT,
        SHA,
    ]


def test_spawn_argv_each_option_alone():
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(layout="stack")) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--mode",
        "stack",
    ]
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(theme="dracula")) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--theme",
        "dracula",
    ]
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(untracked=False)) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--exclude-untracked",
    ]
    # The log page is the sidecar's, never argv's.
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(log_page=50)) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
    ]


def test_reload_argv_with_options():
    """The untracked switch rides every diff reload (hunk re-reads it each
    time); the layout and theme never do — they don't reapply to a running
    viewer, and the respawn covers them."""
    assert hunkctl.reload_argv("/h", "abc", "staged", None, OPTIONS) == [
        "/h",
        "session",
        "reload",
        "abc",
        "--json",
        "--",
        "diff",
        "--exclude-untracked",
        "--staged",
    ]
    assert hunkctl.reload_argv("/h", "abc", "unstaged", None, OPTIONS)[-3:] == [
        "--",
        "diff",
        "--exclude-untracked",
    ]
    assert hunkctl.reload_argv("/h", "abc", "branch", "main", OPTIONS)[-3:] == [
        "diff",
        "--exclude-untracked",
        "main...HEAD",
    ]
    assert hunkctl.reload_argv("/h", "abc", {"show": SHA}, None, OPTIONS)[-3:] == ["--", "show", SHA]
    assert hunkctl.reload_argv("/h", "abc", "staged", None, hunkctl.Options()) == hunkctl.reload_argv(
        "/h", "abc", "staged", None
    )


def test_extension_dir_is_package_data():
    """The bundled extension is really in the tree — the argv check in
    scripts/check_git_page.py spawns with it — and extension_dir() only
    names it while its package.json (what hunk reads first) is there."""
    assert hunkctl.EXTENSION_DIR.endswith(os.path.join("collins", "hunkext", "collins-git"))
    assert os.path.isfile(os.path.join(hunkctl.EXTENSION_DIR, "package.json"))
    assert hunkctl.extension_dir() == hunkctl.EXTENSION_DIR


def test_extension_dir_none_without_package_json(monkeypatch):
    monkeypatch.setattr(hunkctl, "EXTENSION_DIR", "/nowhere/collins-git")
    assert hunkctl.extension_dir() is None


def test_session_argvs():
    assert hunkctl.list_argv("/h") == ["/h", "session", "list", "--json"]
    assert hunkctl.get_argv("/h", "abc") == ["/h", "session", "get", "abc", "--json"]
    assert hunkctl.reload_argv("/h", "abc", "staged", None) == [
        "/h",
        "session",
        "reload",
        "abc",
        "--json",
        "--",
        "diff",
        "--staged",
    ]
    assert hunkctl.reload_argv("/h", "abc", "branch", "main")[-1] == "main...HEAD"
    assert hunkctl.reload_argv("/h", "abc", "unstaged", None)[-2:] == ["--", "diff"]
    assert hunkctl.reload_argv("/h", "abc", {"show": SHA}, None)[-3:] == ["--", "show", SHA]


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
    assert hunkctl.safe_ref(name) is ok


def test_show_helpers():
    assert hunkctl.is_show({"show": SHA})
    assert hunkctl.show_ref({"show": "HEAD"}) == "HEAD"
    assert not hunkctl.is_show({"show": "-x"})
    assert not hunkctl.is_show({"show": ""})
    assert not hunkctl.is_show("show")
    assert not hunkctl.is_show({})
    assert hunkctl.show_ref("unstaged") is None
    for mode in hunkctl.MODES:
        assert hunkctl.loaded_ok(mode)
    assert hunkctl.loaded_ok({"show": SHA})
    assert not hunkctl.loaded_ok("commit")
    assert not hunkctl.loaded_ok({"show": "a..b"})
    assert not hunkctl.loaded_ok(None)


def test_short_ref():
    assert hunkctl.short_ref(SHA) == "0123456"
    assert hunkctl.short_ref("HEAD") == "HEAD"
    assert hunkctl.short_ref("a1b2c3d") == "a1b2c3d"
    assert hunkctl.short_ref("main") == "main"
    assert hunkctl.short_ref("") == ""


# -- replies -----------------------------------------------------------------


def _session(pid: int, session_id: str = "s1", title: str = "repo working tree") -> dict:
    return {
        "sessionId": session_id,
        "pid": pid,
        "cwd": "/tmp/repo",
        "repoRoot": "/tmp/repo",
        "title": title,
        "fileCount": 2,
        "files": [],
    }


def test_session_for_pid_direct():
    text = json.dumps({"sessions": [_session(41, "other"), _session(42, "mine")]})
    session = hunkctl.session_for_pid(text, 42)
    assert session == hunkctl.Session("mine", 42, "repo working tree", "/tmp/repo")


def test_session_for_pid_via_child():
    """The npm wrapper spawnSyncs the real viewer: hunk reports the child's pid."""
    text = json.dumps({"sessions": [_session(4300, "viewer")]})
    assert hunkctl.session_for_pid(text, 4200, children=[4300]).session_id == "viewer"


def test_session_for_pid_no_match_and_empty():
    text = json.dumps({"sessions": [_session(1)]})
    assert hunkctl.session_for_pid(text, 2, children=[3]) is None
    assert hunkctl.session_for_pid(json.dumps({"sessions": []}), 2) is None


@pytest.mark.parametrize(
    "text",
    ["", "not json", "[]", "42", json.dumps({"sessions": "nope"}), json.dumps({"sessions": [1, {}]})],
)
def test_session_for_pid_malformed(text):
    assert hunkctl.session_for_pid(text, 1) is None


def test_session_for_pid_ignores_bad_records():
    text = json.dumps({"sessions": [{"sessionId": "x", "pid": "42"}, _session(42, "good")]})
    assert hunkctl.session_for_pid(text, 42).session_id == "good"


def test_parse_session_get():
    text = json.dumps({"session": _session(7, "s7", "repo staged changes")})
    assert hunkctl.parse_session_get(text) == hunkctl.Session(
        "s7", 7, "repo staged changes", "/tmp/repo"
    )
    assert hunkctl.parse_session_get("{}") is None
    assert hunkctl.parse_session_get("garbage") is None


# -- replies: ok, gone, refused --------------------------------------------------


@pytest.mark.parametrize(
    ("stderr", "gone"),
    [
        ("hunk: No active session matches sessionId abc.\n", True),
        (
            "hunk: No active Hunk sessions are registered with the daemon. Open Hunk and wait to connect.",
            True,
        ),
        ("hunk: `hunk diff nosuch...HEAD` could not resolve Git revision or range `nosuch...HEAD`.", False),
        ("", False),
        ("hunk: something else went wrong", False),
    ],
)
def test_session_gone(stderr, gone):
    """Only hunk 0.20.1's two "no session" lines mean the viewer is gone; a
    refused target (the viewer keeps what it had) and a silent failure don't."""
    assert hunkctl.session_gone(stderr) is gone


def test_reply_classification():
    ok = hunkctl.Reply('{"result": {}}', "", 0)
    assert ok.ok and not ok.session_gone
    gone = hunkctl.Reply("", "hunk: No active session matches sessionId abc.", 1)
    assert not gone.ok and gone.session_gone
    refused = hunkctl.Reply("", "hunk: could not resolve Git revision or range `x...HEAD`.", 1)
    assert not refused.ok and not refused.session_gone
    silent = hunkctl.Reply("", "", None)
    assert not silent.ok and not silent.session_gone
    # A zero exit with a "no session" line is still a success (it isn't hunk's shape, but the
    # exit code is the word).
    assert not hunkctl.Reply("{}", "No active session", 0).session_gone


def test_run_wraps_the_result():
    def fake(argv, capture_output, text, timeout):
        assert argv == ["/h", "session", "get", "abc", "--json"]
        assert capture_output and text and timeout == hunkctl.SESSION_TIMEOUT_S
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="hunk: No active session matches sessionId abc."
        )

    reply = hunkctl.run(hunkctl.get_argv("/h", "abc"), run=fake)
    assert reply == hunkctl.Reply("", "hunk: No active session matches sessionId abc.", 1)
    assert reply.session_gone


def test_run_never_raises():
    def timeout(argv, **_kw):
        raise subprocess.TimeoutExpired(argv, 1)

    def missing(argv, **_kw):
        raise FileNotFoundError(argv[0])

    assert hunkctl.run(["/h"], run=timeout) == hunkctl.Reply("", "", None)
    assert hunkctl.run(["/h"], run=missing) == hunkctl.Reply("", "", None)
    assert hunkctl.run(["/h"], run=lambda argv, **_kw: subprocess.CompletedProcess(argv, 0, None, None)) == (
        hunkctl.Reply("", "", 0)
    )


def test_parse_reload_reply():
    text = json.dumps({"result": {"sessionId": "s1", "title": "repo staged changes", "fileCount": 3}})
    assert hunkctl.parse_reload_reply(text) == "repo staged changes"
    assert hunkctl.parse_reload_reply(json.dumps({"result": {}})) is None
    assert hunkctl.parse_reload_reply(json.dumps({"result": "x"})) is None
    assert hunkctl.parse_reload_reply("hunk: No active session matches sessionId X.") is None


# -- titles ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("wondrous-inventing-orbit working tree", ("unstaged", None)),
        ("wondrous-inventing-orbit staged changes", ("staged", None)),
        ("wondrous-inventing-orbit main...HEAD", ("branch", "main")),
        ("wondrous-inventing-orbit origin/main...HEAD", ("branch", "origin/main")),
        ("my repo with spaces working tree", ("unstaged", None)),
        ("my repo with spaces main...HEAD", ("branch", "main")),
        ("repo show HEAD", ("show", "HEAD")),
        ("repo show " + SHA, ("show", SHA)),
        ("my repo with spaces show abc1234", ("show", "abc1234")),
        ("show show HEAD", ("show", "HEAD")),  # a repository named "show"
        # The commits panel's other headers: a range between two branches is
        # not a branch load, and is left to hunk.
        ("repo main...feat", (None, None)),
        ("repo main..feat", (None, None)),
        ("repo " + SHA + ".." + SHA, (None, None)),
        ("repo show -x", (None, None)),
        ("repo show a..b", (None, None)),
        ("repo show", (None, None)),
        ("repo a1b2c3d", (None, None)),
        ("", (None, None)),
    ],
)
def test_loaded_from_title(title, expected):
    assert hunkctl.loaded_from_title(title) == expected


@pytest.mark.parametrize(
    ("title", "root", "expected"),
    [
        ("repo show HEAD", "/home/me/repo", "show HEAD"),
        ("repo show HEAD", "/home/me/repo/", "show HEAD"),
        ("my repo show a1b2c3", "/srv/my repo", "show a1b2c3"),
        ("repo show HEAD", "/home/me/other", "repo show HEAD"),
        ("repo show HEAD", None, "repo show HEAD"),
        ("repo show HEAD", "", "repo show HEAD"),
        ("repo ", "/home/me/repo", "repo"),  # nothing after the name: keep the title
        ("", "/home/me/repo", ""),
    ],
)
def test_title_tail(title, root, expected):
    assert hunkctl.title_tail(title, root) == expected


def test_foreign_tab_title():
    assert hunkctl.foreign_tab_title("show HEAD") == "Git · show HEAD"
    assert hunkctl.foreign_tab_title("") == "Git · ?"


def test_breadcrumb():
    assert hunkctl.breadcrumb("unstaged", "feat", "main") == "working tree · unstaged"
    assert hunkctl.breadcrumb("staged", None, None) == "working tree · staged"
    assert hunkctl.breadcrumb("branch", "feat", "main") == "feat vs main"
    assert hunkctl.breadcrumb("branch", None, None) == "HEAD vs ?"
    assert hunkctl.breadcrumb({"show": SHA}, "feat", "main") == "commit 0123456"
    assert hunkctl.breadcrumb({"show": "HEAD"}, None, None) == "commit HEAD"
    # A commit is named by its subject once known: the spec's `a1b2c3 Wire the mode switch`.
    named = hunkctl.breadcrumb({"show": SHA}, "feat", "main", "Wire the mode switch")
    assert named == "0123456 Wire the mode switch"
    assert hunkctl.breadcrumb({"show": "HEAD"}, None, None, "") == "commit HEAD"
    assert hunkctl.breadcrumb("unstaged", None, None, "ignored for a mode") == "working tree · unstaged"


def test_commit_subject_resolves():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="Wire the mode switch\n", stderr="")

    assert hunkctl.commit_subject("/repo", SHA, run=run) == "Wire the mode switch"
    argv, kwargs = calls[0]
    assert argv == ["git", "log", "-1", "--format=%s", f"{SHA}^{{commit}}", "--"]
    assert kwargs["cwd"] == "/repo"
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["timeout"] == hunkctl.GIT_TIMEOUT_S


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

    assert hunkctl.commit_subject("/repo", "deadbeef", run=gone) is None
    assert hunkctl.commit_subject("/repo", "HEAD", run=empty) == ""
    assert hunkctl.commit_subject("/repo", "HEAD", run=missing) == ""
    assert hunkctl.commit_subject("/repo", "HEAD", run=slow) == ""
    # Nothing to ask about: no cwd, or a ref git could misread.
    assert hunkctl.commit_subject(None, "HEAD", run=empty) is None
    assert hunkctl.commit_subject("/repo", "-x", run=empty) is None
    assert hunkctl.commit_subject("/repo", "a..b", run=empty) is None
    assert hunkctl.commit_subject("/repo", None, run=empty) is None


def test_tab_title():
    assert hunkctl.tab_title("unstaged", "main") == "Git · unstaged"
    assert hunkctl.tab_title("staged", None) == "Git · staged"
    assert hunkctl.tab_title("branch", "main") == "Git · vs main"
    assert hunkctl.tab_title("branch", None) == "Git · vs ?"
    assert hunkctl.tab_title({"show": SHA}, "main") == "Git · 0123456"
    assert hunkctl.tab_title({"show": "v1.2"}, None) == "Git · v1.2"


# -- chords ------------------------------------------------------------------

CONTROL = 1 << 2
SHIFT = 1 << 0
ALT = 1 << 3
SUPER = 1 << 26


def test_load_for_key_ctrl_digits():
    assert hunkctl.load_for_key(0x31, CONTROL) == "unstaged"
    assert hunkctl.load_for_key(0x32, CONTROL) == "staged"
    assert hunkctl.load_for_key(0x33, CONTROL) == "branch"


def test_load_for_key_keypad():
    assert hunkctl.load_for_key(0xFFB1, CONTROL) == "unstaged"
    assert hunkctl.load_for_key(0xFFB2, CONTROL) == "staged"
    assert hunkctl.load_for_key(0xFFB3, CONTROL) == "branch"


def test_load_for_key_shift_tolerated():
    assert hunkctl.load_for_key(0x31, CONTROL | SHIFT) == "unstaged"


def test_load_for_key_other_modifiers_refused():
    assert hunkctl.load_for_key(0x31, CONTROL | ALT) is None
    assert hunkctl.load_for_key(0x31, CONTROL | SUPER) is None


def test_load_for_key_bare_and_other_digits():
    assert hunkctl.load_for_key(0x31, 0) is None
    assert hunkctl.load_for_key(0x31, SHIFT) is None
    assert hunkctl.load_for_key(0x34, CONTROL) is None
    assert hunkctl.load_for_key(0xFF1B, CONTROL) is None


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
    assert hunkctl.initial_mode(staged, unstaged) == expected


# -- layout slot -------------------------------------------------------------


def test_encode_state():
    assert hunkctl.encode_state("staged") == {"kind": "git", "loaded": "staged"}
    assert hunkctl.encode_state("staged", None) == {"kind": "git", "loaded": "staged"}
    assert hunkctl.encode_state("branch", "base") == {"kind": "git", "loaded": "branch", "parent": "base"}
    state = hunkctl.encode_state({"show": SHA}, "base")
    assert state == {"kind": "git", "loaded": {"show": SHA}, "parent": "base"}
    assert json.loads(json.dumps(state)) == state  # what panellayout writes


def test_encode_state_copies_the_show_dict():
    loaded = {"show": SHA}
    state = hunkctl.encode_state(loaded)
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
    assert hunkctl.decode_state(page) == expected


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ({"kind": "git", "loaded": "branch", "parent": "base"}, "base"),
        ({"kind": "git", "loaded": "branch", "parent": "release/v1"}, "release/v1"),
        ({"kind": "git", "loaded": "branch"}, None),
        ({"kind": "git", "parent": ""}, None),
        ({"kind": "git", "parent": "-x"}, None),
        ({"kind": "git", "parent": "a..b"}, None),
        ({"kind": "git", "parent": 3}, None),
        ("git", None),
        (None, None),
    ],
)
def test_decode_parent(page, expected):
    assert hunkctl.decode_parent(page) == expected


def test_state_round_trips():
    for mode in hunkctl.MODES:
        assert hunkctl.decode_state(hunkctl.encode_state(mode)) == mode
    state = hunkctl.encode_state({"show": SHA}, "base")
    assert hunkctl.decode_state(state) == {"show": SHA}
    assert hunkctl.decode_parent(state) == "base"
    assert hunkctl.decode_parent(hunkctl.encode_state("branch")) is None


# -- the sidecar -------------------------------------------------------------


def test_sidecar_path():
    assert hunkctl.sidecar_path("/run/user/1000", 4242, 3) == "/run/user/1000/collins/git-4242-3.json"


def test_sidecar_payload():
    assert hunkctl.sidecar_payload("main", "auto", "main", 20) == {
        "version": 1,
        "parent": "main",
        "parentSource": "auto",
        "default": "main",
        "logPage": 20,
        "untracked": True,
    }
    assert hunkctl.sidecar_payload("base", "user", None, hunkctl.LOG_PAGE)["parentSource"] == "user"
    assert hunkctl.sidecar_payload(None, "bogus", None, 20)["parentSource"] == "auto"
    assert hunkctl.sidecar_payload("main", "auto", "main", 50, False) == {
        "version": 1,
        "parent": "main",
        "parentSource": "auto",
        "default": "main",
        "logPage": 50,
        "untracked": False,
    }
    assert hunkctl.sidecar_payload("main", "auto", "main", 20, untracked=0)["untracked"] is False


def test_write_sidecar_creates_and_replaces(tmp_path):
    path = str(tmp_path / "collins" / "git-1-1.json")
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload("main", "auto", "main", 20))
    with open(path) as fh:
        assert json.load(fh) == {
            "version": 1,
            "parent": "main",
            "parentSource": "auto",
            "default": "main",
            "logPage": 20,
            "untracked": True,
        }
    assert sorted(os.listdir(tmp_path / "collins")) == ["git-1-1.json"]  # no temp file left behind
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload("base", "user", "main", 20))
    with open(path) as fh:
        assert json.load(fh)["parent"] == "base"


def test_write_sidecar_keeps_the_other_sides_keys(tmp_path):
    """Read-merge-write: what the extension (or a newer one) put in the file
    survives Collins' rewrite; Collins' own keys win; version stays 1."""
    path = str(tmp_path / "git.json")
    with open(path, "w") as fh:
        json.dump({"version": 7, "parent": "x", "parentSource": "user", "extra": {"a": 1}}, fh)
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload("main", "auto", "main", 20))
    with open(path) as fh:
        data = json.load(fh)
    assert data["extra"] == {"a": 1}
    assert data["parent"] == "main" and data["parentSource"] == "auto" and data["version"] == 1


def test_write_sidecar_over_garbage(tmp_path):
    path = str(tmp_path / "git.json")
    with open(path, "w") as fh:
        fh.write("not json")
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload("main", "auto", None, 20))
    with open(path) as fh:
        assert json.load(fh)["parent"] == "main"


def test_write_sidecar_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    # The parent "directory" is a file: mkdir fails, the write reports False.
    assert not hunkctl.write_sidecar(str(blocker / "collins" / "git.json"), {"parent": "main"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"version": 1, "parent": "base", "parentSource": "user"}', ("base", "user")),
        ('{"version": 1, "parent": "main", "parentSource": "auto"}', ("main", "auto")),
        ('{"parent": null, "parentSource": "auto"}', (None, "auto")),
        ('{"parent": "base"}', ("base", "auto")),
        ('{"parent": "-x", "parentSource": "user"}', (None, "user")),
        ('{"parent": "a..b", "parentSource": "user"}', (None, "user")),
        ('{"parent": 3, "parentSource": "user"}', (None, "user")),
        ('{"parentSource": "other"}', (None, "auto")),
        ("[]", (None, "auto")),
        ("garbage", (None, "auto")),
        ("", (None, "auto")),
    ],
)
def test_read_sidecar(text, expected):
    assert hunkctl.read_sidecar(text) == expected


def test_sidecar_round_trip(tmp_path):
    path = str(tmp_path / "git.json")
    hunkctl.write_sidecar(path, hunkctl.sidecar_payload("base", "user", "main", 20))
    with open(path) as fh:
        assert hunkctl.read_sidecar(fh.read()) == ("base", "user")


_HEAD = "2cdcdb0cb0170be576e43fd27c48d1f64f800df7"
_NS = 1756800000123456789


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f'{{"refreshed": {{"index": "{_NS}", "head": "{_HEAD}"}}}}', (_NS, _HEAD)),
        (f'{{"parent": "main", "refreshed": {{"index": "7", "head": "{_HEAD}"}}}}', (7, _HEAD)),
        (f'{{"refreshed": {{"index": {_NS}, "head": "{_HEAD}"}}}}', None),  # a number: JS would lose digits
        (f'{{"refreshed": {{"index": "12x", "head": "{_HEAD}"}}}}', None),
        ('{"refreshed": {"index": "12", "head": "HEAD"}}', None),
        ('{"refreshed": {"index": "12"}}', None),
        ('{"refreshed": "12"}', None),
        ('{"parent": "main"}', None),
        ("garbage", None),
        ("", None),
    ],
)
def test_read_sidecar_refreshed(text, expected):
    assert hunkctl.read_sidecar_refreshed(text) == expected


def test_shown_by_extension():
    """A move the extension recorded (same index mtime and HEAD, base
    untouched) is not one the page reloads for; anything else is."""
    base = "b" * 40
    previous = (100, _HEAD, base)
    other = "3" * 40
    shown = hunkctl.shown_by_extension
    assert shown((200, _HEAD), (200, _HEAD, base), previous)  # an x
    assert shown((200, other), (200, other, base), previous)  # a commit
    assert not shown((200, _HEAD), (300, _HEAD, base), previous)  # a shell moved it since
    assert not shown((200, _HEAD), (200, other, base), previous)
    assert not shown((200, _HEAD), (200, _HEAD, other), previous)  # the base moved too
    assert not shown(None, (200, _HEAD, base), previous)
    assert not shown((200, _HEAD), None, previous)
    assert not shown((200, _HEAD), (200, _HEAD, base), None)


def test_sidecar_refreshed_survives_collins_write(tmp_path):
    """Collins' write merges: the extension's record is still there after it."""
    path = str(tmp_path / "git.json")
    with open(path, "w") as fh:
        fh.write(f'{{"version": 1, "refreshed": {{"index": "200", "head": "{_HEAD}"}}}}')
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload("main", "auto", "main", 20))
    with open(path) as fh:
        assert hunkctl.read_sidecar_refreshed(fh.read()) == (200, _HEAD)


def test_spawn_env():
    assert hunkctl.spawn_env(None) is None
    assert hunkctl.spawn_env("") is None
    env = hunkctl.spawn_env("/run/user/1/collins/git-1-1.json", {"PATH": "/bin", "HOME": "/home/me"})
    assert sorted(env) == ["COLLINS_GIT_STATE=/run/user/1/collins/git-1-1.json", "HOME=/home/me", "PATH=/bin"]
    # An inherited variable of the same name is overridden, not doubled.
    env = hunkctl.spawn_env("/x.json", {"COLLINS_GIT_STATE": "/old.json"})
    assert env == ["COLLINS_GIT_STATE=/x.json"]


def test_spawn_env_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("COLLINS_TEST_MARKER", "1")
    monkeypatch.delenv(hunkctl.SIDECAR_ENV, raising=False)
    env = hunkctl.spawn_env("/x.json")
    assert "COLLINS_TEST_MARKER=1" in env
    assert f"{hunkctl.SIDECAR_ENV}=/x.json" in env
    assert hunkctl.SIDECAR_ENV not in os.environ  # the list is a copy; the app's own env is untouched


# -- terminate_tree ---------------------------------------------------------


def test_terminate_tree_signals_the_process_group():
    calls = []
    hunkctl.terminate_tree(
        42,
        [43, 44],
        getpgid=lambda pid: 40,
        killpg=lambda pgid, sig: calls.append(("pg", pgid, sig)),
        kill=lambda pid, sig: calls.append(("pid", pid, sig)),
    )
    assert calls == [("pg", 40, signal.SIGTERM)]


def test_terminate_tree_falls_back_to_children_then_pid():
    """No group to signal (the wrapper already reaped): the viewer children
    are signalled one by one, the wrapper last, and a missing one is skipped."""
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        if pid == 43:
            raise ProcessLookupError

    def killpg(_pgid, _sig):
        raise ProcessLookupError

    hunkctl.terminate_tree(42, [43, 44], getpgid=lambda pid: 42, killpg=killpg, kill=kill)
    assert calls == [(43, signal.SIGTERM), (44, signal.SIGTERM), (42, signal.SIGTERM)]


def test_terminate_tree_survives_a_vanished_pid():
    def getpgid(_pid):
        raise ProcessLookupError

    def kill(_pid, _sig):
        raise ProcessLookupError

    hunkctl.terminate_tree(42, getpgid=getpgid, killpg=lambda *a: None, kill=kill)  # no raise


# -- the show_diff tool ------------------------------------------------------------


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

    assert hunkctl.resolve_commit("/repo", "HEAD~1", run=run) == SHA
    argv, kwargs = calls[0]
    assert argv == ["git", "rev-parse", "--verify", "--quiet", "HEAD~1^{commit}"]
    assert kwargs["cwd"] == "/repo"
    assert kwargs["timeout"] == hunkctl.GIT_TIMEOUT_S


def test_resolve_commit_three_answers():
    """None: git says it names no commit. "": git couldn't be asked. Neither
    call happens for a ref that isn't safe as an argument."""
    assert hunkctl.resolve_commit("/repo", "nope", run=lambda *a, **k: _Result(1)) is None
    assert hunkctl.resolve_commit("/repo", "HEAD", run=lambda *a, **k: _Result(0, "garbage sha")) is None

    def boom(*a, **k):
        raise OSError("no git")

    assert hunkctl.resolve_commit("/repo", "HEAD", run=boom) == ""

    def never(*a, **k):
        raise AssertionError("must not run")

    assert hunkctl.resolve_commit("/repo", "--output=x", run=never) is None
    assert hunkctl.resolve_commit("/repo", "a..b", run=never) is None
    assert hunkctl.resolve_commit(None, "HEAD", run=never) is None


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
    assert hunkctl.show_diff_load(what) == expected


def test_diff_file_path_is_repo_relative():
    root = "/repo"
    assert hunkctl.diff_file_path("src/a.py", root) == "src/a.py"
    assert hunkctl.diff_file_path("./src/a.py", root) == "src/a.py"
    assert hunkctl.diff_file_path("/repo/src/a.py", root) == "src/a.py"
    assert hunkctl.diff_file_path(" src/a.py ", root) == "src/a.py"
    assert hunkctl.diff_file_path("src/../a.py", root) == "a.py"


def test_diff_file_path_refuses_what_cannot_be_in_the_diff():
    root = "/repo"
    assert hunkctl.diff_file_path("", root) is None
    assert hunkctl.diff_file_path("   ", root) is None
    assert hunkctl.diff_file_path(".", root) is None
    assert hunkctl.diff_file_path("../x.py", root) is None
    assert hunkctl.diff_file_path("/etc/passwd", root) is None
    assert hunkctl.diff_file_path("-flag", root) is None
    assert hunkctl.diff_file_path("src/a.py", None) is None
    assert hunkctl.diff_file_path(None, root) is None


def test_diff_file_path_prefers_the_agent_cwd_when_only_it_has_the_file():
    """An agent that cd'd into a subdirectory names files the way its shell
    sees them; a path that is only there against its cwd is taken from
    there — and one that exists against the root wins outright."""
    root = "/repo"
    cwd = "/repo/pkg"
    only_in_pkg = {"/repo/pkg/a.py"}
    assert hunkctl.diff_file_path("a.py", root, cwd, exists=only_in_pkg.__contains__) == "pkg/a.py"
    both = {"/repo/a.py", "/repo/pkg/a.py"}
    assert hunkctl.diff_file_path("a.py", root, cwd, exists=both.__contains__) == "a.py"
    neither = set()
    assert hunkctl.diff_file_path("a.py", root, cwd, exists=neither.__contains__) == "a.py"
    # The cwd never lifts a path out of the repository.
    assert hunkctl.diff_file_path("../../x.py", root, cwd, exists=lambda _p: True) is None


def test_navigate_argv():
    assert hunkctl.navigate_argv("/usr/bin/hunk", "abc", "src/a.py", 42) == [
        "/usr/bin/hunk", "session", "navigate", "abc", "--json", "--file", "src/a.py", "--new-line", "42",
    ]
    assert hunkctl.navigate_argv("/usr/bin/hunk", "abc", "src/a.py") == [
        "/usr/bin/hunk", "session", "navigate", "abc", "--json", "--file", "src/a.py", "--hunk", "1",
    ]


def test_navigate_error_passes_hunks_word_through():
    reply = hunkctl.Reply("", "hunk: No diff file matches src/x.ts.\n", 1)
    assert hunkctl.navigate_error(reply) == "No diff file matches src/x.ts."
    reply = hunkctl.Reply("", "warning: x\nhunk: No diff hunk in a.py matches the requested target.", 1)
    assert hunkctl.navigate_error(reply) == "No diff hunk in a.py matches the requested target."
    assert hunkctl.navigate_error(hunkctl.Reply("", "", None)) == "hunk didn't answer the navigate in time"
    assert hunkctl.navigate_error(hunkctl.Reply("", "", 2)) == "hunk didn't answer the navigate (exit 2)"


def test_show_diff_reply():
    text = hunkctl.show_diff_reply("working tree · unstaged", "abc-1")
    lines = text.split("\n")
    assert lines[0] == "Loaded working tree · unstaged in the session's git page (hunk session abc-1)."
    assert "Navigated" not in text
    assert "`hunk session <command> abc-1 …`" in lines[-1]
    assert "`hunk skill path`" in lines[-1]
    text = hunkctl.show_diff_reply("a1b2c3d Wire it", "abc-1", "src/a.py", 12)
    assert text.split("\n")[1] == "Navigated the viewer to src/a.py, line 12."
    text = hunkctl.show_diff_reply("a1b2c3d Wire it", "abc-1", "src/a.py")
    assert text.split("\n")[1] == "Navigated the viewer to src/a.py."


# -- the probe cache ----------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_probe_cache_probes_once_until_the_ttl_passes():
    calls = []
    clock = _Clock()

    def fake_probe():
        calls.append(clock.now)
        return hunkctl.Probe("/usr/bin/hunk", (0, 20, 1))

    cache = hunkctl.ProbeCache(probe=fake_probe, ttl=30.0, clock=clock)
    assert cache.result is None and cache.stale
    assert cache.ok() is True  # never filled: probes on the spot
    assert calls == [100.0]
    assert not cache.stale
    clock.now = 129.0
    assert cache.ok() is True and calls == [100.0]  # answered from the cache
    clock.now = 130.0
    assert cache.stale  # due, but ok() still never re-probes on its own
    assert cache.ok() is True and calls == [100.0]
    assert cache.refresh().status == "ok"
    assert calls == [100.0, 130.0] and not cache.stale


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        (hunkctl.Probe(None, None), False),
        (hunkctl.Probe("/usr/bin/hunk", None), False),
        (hunkctl.Probe("/usr/bin/hunk", (0, 19, 9)), False),
        (hunkctl.Probe("/usr/bin/hunk", (0, 20, 0)), True),
        (hunkctl.Probe("/usr/bin/hunk", (1, 2)), True),
    ],
)
def test_probe_cache_ok_is_the_probes_status(probe, expected):
    cache = hunkctl.ProbeCache(probe=lambda: probe, clock=_Clock())
    assert cache.ok() is expected
    assert cache.result == probe


def test_probe_cache_follows_an_install_on_refresh():
    """hunk installed after the first probe: the next refresh (the app's,
    once the TTL passed) turns the tool on for the next session."""
    answers = [hunkctl.Probe(None, None), hunkctl.Probe("/usr/bin/hunk", (0, 20, 1))]
    cache = hunkctl.ProbeCache(probe=lambda: answers.pop(0), clock=_Clock())
    assert cache.ok() is False
    cache.refresh()
    assert cache.ok() is True
