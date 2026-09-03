# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for hunkctl: the git page's decisions that need no widget — the
version gate, argv, hunk's JSON replies, session titles, the chords, and the
layout slot."""

import json
import signal
import subprocess

import pytest

from collins import hunkctl

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


def test_tab_title():
    assert hunkctl.tab_title("unstaged", "main") == "Git · unstaged"
    assert hunkctl.tab_title("staged", None) == "Git · staged"
    assert hunkctl.tab_title("branch", "main") == "Git · vs main"
    assert hunkctl.tab_title("branch", None) == "Git · vs ?"


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


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        ({"kind": "git", "loaded": "staged"}, "staged"),
        ({"kind": "git", "loaded": "branch"}, "branch"),
        ({"kind": "git", "loaded": "unstaged"}, "unstaged"),
        ({"kind": "git", "loaded": {"show": "abc"}}, "unstaged"),
        ({"kind": "git", "loaded": "commit"}, "unstaged"),
        ({"kind": "git"}, "unstaged"),
        ("git", "unstaged"),
        (None, "unstaged"),
    ],
)
def test_decode_state(page, expected):
    assert hunkctl.decode_state(page) == expected


def test_state_round_trips():
    for mode in hunkctl.MODES:
        assert hunkctl.decode_state(hunkctl.encode_state(mode)) == mode


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
