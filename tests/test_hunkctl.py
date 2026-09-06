# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for hunkctl: the git page's decisions about hunk that need no
widget — the version gate, argv (the bundled extension on it, and what
Preferences → Git adds), hunk's JSON replies, the session's files and
cursor, hunk's session titles read back into loads, the sidecar the page
shares with the extension, the pinned keys, terminate_tree, and the
show_diff tool's hunk-side decisions (the navigate argv, the reply). The
`Loaded` vocabulary itself — safe_ref, the show and range helpers, Options,
breadcrumb and tab_title, the chords, the layout slot, show_diff_load,
diff_file_path, the git calls behind a commit's name — is tested in
tests/test_gitloads.py; hunkctl re-exports every one of those names, which
test_hunkctl_re_exports_the_loaded_vocabulary pins."""

import json
import os
import re
import signal
import stat
import subprocess

import pytest

from collins import gitloads, hunkctl

EXT = "/opt/collins/hunkext/collins-git"
SHA = "0123456789abcdef0123456789abcdef01234567"

# -- the re-export -------------------------------------------------------------


def test_hunkctl_re_exports_the_loaded_vocabulary():
    """Every public name gitloads defines is the same object on hunkctl:
    nothing moved for the callers when the vocabulary was split out."""
    names = [
        name
        for name, value in vars(gitloads).items()
        if not name.startswith("_")
        and not isinstance(value, type(gitloads))  # the modules it imports
        and getattr(value, "__module__", None) in (gitloads.__name__, None)  # not Mapping / dataclass
    ]
    assert "safe_ref" in names and "Options" in names and "encode_state" in names
    for name in names:
        assert getattr(hunkctl, name) is getattr(gitloads, name), name

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
    """0.21 brought `hunk diff --no-sidebar`, which every spawn carries."""
    assert hunkctl.MIN_VERSION == (0, 21)
    assert hunkctl.version_ok((0, 21, 1))
    assert hunkctl.version_ok((0, 21))
    assert hunkctl.version_ok((1, 0, 0))
    assert not hunkctl.version_ok((0, 20, 1))
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
    # 0.20 had the session API but not `diff --no-sidebar`: the install card.
    probe = hunkctl.probe(which=lambda _name: "/usr/local/bin/hunk", run=_run_answering("0.20.1\n"))
    assert probe.version == (0, 20, 1)
    assert probe.status == "old"


def test_probe_ok():
    probe = hunkctl.probe(which=lambda _name: "/usr/local/bin/hunk", run=_run_answering("0.21.1\n"))
    assert probe.status == "ok"
    assert probe.version == (0, 21, 1)


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


# -- the daemon's runtime dir -----------------------------------------------


def test_repair_daemon_dir_tightens_a_group_readable_dir(tmp_path):
    """hunk 0.20 left the dir at the umask; 0.21 refuses to run its daemon
    on anything but owner-only."""
    path = tmp_path / hunkctl.DAEMON_DIR
    path.mkdir(mode=0o775)
    assert hunkctl.repair_daemon_dir(str(tmp_path)) == "repaired"
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert hunkctl.repair_daemon_dir(str(tmp_path)) == "ok"


def test_repair_daemon_dir_leaves_an_owner_only_dir_alone(tmp_path):
    (tmp_path / hunkctl.DAEMON_DIR).mkdir(mode=0o700)
    calls = []
    assert hunkctl.repair_daemon_dir(str(tmp_path), chmod=lambda *a: calls.append(a)) == "ok"
    assert calls == []


def test_repair_daemon_dir_absent(tmp_path):
    """No dir yet (hunk creates it 0700 itself), a file in its place, or no
    runtime dir at all: nothing to do."""
    assert hunkctl.repair_daemon_dir(str(tmp_path)) == "absent"
    (tmp_path / hunkctl.DAEMON_DIR).write_text("")
    assert hunkctl.repair_daemon_dir(str(tmp_path)) == "absent"
    assert hunkctl.repair_daemon_dir(None) == "absent"
    assert hunkctl.repair_daemon_dir("") == "absent"


def test_repair_daemon_dir_refused_chmod(tmp_path):
    (tmp_path / hunkctl.DAEMON_DIR).mkdir(mode=0o775)

    def refuse(_path, _mode):
        raise PermissionError("not yours")

    assert hunkctl.repair_daemon_dir(str(tmp_path), chmod=refuse) == "failed"


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
    """Every spawn carries --no-sidebar: hunk draws its review alone, the
    native sidebar lists the files."""
    assert hunkctl.NO_SIDEBAR_FLAG == "--no-sidebar"
    assert hunkctl.spawn_argv("/usr/local/bin/hunk", "unstaged", None) == [
        "/usr/local/bin/hunk",
        "diff",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
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
        "--no-sidebar",
        "--extension",
        EXT,
    ]
    assert hunkctl.spawn_argv("/h", "branch", "main", EXT)[-3:] == ["--extension", EXT, "main...HEAD"]
    assert hunkctl.spawn_argv("/h", "staged", None, None) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
        "--staged",
    ]


def test_spawn_argv_show():
    """A saved commit spawns `hunk show <ref>`, the same flags in front."""
    assert hunkctl.spawn_argv("/h", {"show": SHA}, None, EXT) == [
        "/h",
        "show",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
        "--extension",
        EXT,
        SHA,
    ]
    assert hunkctl.spawn_argv("/h", {"show": "HEAD"}, "main") == [
        "/h",
        "show",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
        "HEAD",
    ]
    with pytest.raises(ValueError):
        hunkctl.spawn_argv("/h", {"show": "-x"}, None)
    with pytest.raises(ValueError):
        hunkctl.spawn_argv("/h", {"other": "x"}, None)


# -- Preferences → Git ---------------------------------------------------------


def test_default_options_change_no_argv():
    """A page that never received settings, and an empty dict, both run on
    the defaults (gitloads.Options — see test_gitloads) — and the defaults
    change no argv (see the exact lists above)."""
    options = hunkctl.Options.from_settings({})
    assert options == hunkctl.Options()
    assert hunkctl.spawn_flags(None) == ["--no-sidebar"]
    assert hunkctl.spawn_flags(options) == ["--no-sidebar"]


OPTIONS = hunkctl.Options(layout="split", theme="nord", untracked=False, log_page=50)


def test_spawn_argv_with_options():
    """--mode/--theme after --transparent-bg and before --extension;
    --exclude-untracked right after `diff`, before --staged or the range."""
    assert hunkctl.spawn_argv("/h", "unstaged", None, EXT, OPTIONS) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
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
        "--no-sidebar",
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
        "--no-sidebar",
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
        "--no-sidebar",
        "--mode",
        "stack",
    ]
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(theme="dracula")) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
        "--theme",
        "dracula",
    ]
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(untracked=False)) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
        "--exclude-untracked",
    ]
    # The log page is the sidecar's, never argv's.
    assert hunkctl.spawn_argv("/h", "unstaged", None, None, hunkctl.Options(log_page=50)) == [
        "/h",
        "diff",
        "--watch",
        "--transparent-bg",
        "--no-sidebar",
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


def test_range_load_tails_and_argv():
    """A range rides `diff a...b`, the untracked flag in between; spawn and
    reload alike."""
    assert hunkctl.spawn_argv("/h", {"range": "main...feat"}, None, EXT) == [
        "/h", "diff", "--watch", "--transparent-bg", "--no-sidebar", "--extension", EXT, "main...feat",
    ]
    assert hunkctl.spawn_argv("/h", {"range": "main...feat"}, None, None, OPTIONS)[-2:] == [
        "--exclude-untracked", "main...feat",
    ]
    assert hunkctl.reload_argv("/h", "abc", {"range": "main...feat"}, "other")[-3:] == [
        "--", "diff", "main...feat",
    ]
    assert hunkctl.reload_argv("/h", "abc", {"range": "main...feat"}, None, OPTIONS)[-3:] == [
        "diff", "--exclude-untracked", "main...feat",
    ]
    with pytest.raises(ValueError):
        hunkctl.spawn_argv("/h", {"range": "main..feat"}, None)


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


# -- the session's files and cursor (hunk 0.21.1's session record) -------------------


def _file(file_id: str, path: str, additions: int = 1, deletions: int = 0, hunks: int = 1, **extra) -> dict:
    record = {"id": file_id, "path": path, "additions": additions, "deletions": deletions}
    return {**record, "hunkCount": hunks, **extra}


def _snapshot(path: str | None = "a.txt", hunk: int = 0) -> dict:
    state = {"selectedHunkIndex": hunk, "showAgentNotes": False, "liveComments": []}
    if path is not None:
        state["selectedFileId"] = f"/tmp/repo:0:{path}"
        state["selectedFilePath"] = path
    return {"updatedAt": "2026-09-05T11:50:25.450Z", "state": state}


def test_session_files_in_review_order_with_counts_and_rename_pairs():
    """What hunk 0.21.1 lists (verified: a text change, a staged rename
    with previousPath, a binary change at 0/0/0)."""
    record = _session(7)
    record["files"] = [
        _file("/tmp/repo:0:a.txt", "a.txt"),
        _file("/tmp/repo:1:new.txt", "new.txt", 1, 0, 1, previousPath="old.txt"),
        _file("/tmp/repo:2:img.bin", "img.bin", 0, 0, 0),
    ]
    record["snapshot"] = _snapshot("new.txt", 0)
    session = hunkctl.parse_session_get(json.dumps({"session": record}))
    assert session.files == (
        hunkctl.SessionFile("/tmp/repo:0:a.txt", "a.txt", None, 1, 0, 1),
        hunkctl.SessionFile("/tmp/repo:1:new.txt", "new.txt", "old.txt", 1, 0, 1),
        hunkctl.SessionFile("/tmp/repo:2:img.bin", "img.bin", None, 0, 0, 0),
    )
    assert session.selected_path == "new.txt"
    assert session.selected_hunk == 0
    # The same record shape in a `session list` reply.
    listed = hunkctl.session_for_pid(json.dumps({"sessions": [record]}), 7)
    assert listed.files == session.files and listed.selected_path == "new.txt"


def test_session_files_skip_garbage_records_and_keep_the_rest():
    record = _session(7)
    record["files"] = [
        "not a record",
        {"id": "x"},  # no path
        _file(3, "no-id.txt"),  # id isn't a string
        _file("f", "neg.txt", -1),  # a negative count
        _file("f", "bool.txt", True),  # a bool for a count
        _file("f", "float.txt", 1.5),
        {"id": "f", "path": "nohunkcount.txt", "additions": 1, "deletions": 0},
        _file("f", "p" * (hunkctl.MAX_PATH_CHARS + 1)),
        _file("f", ""),
        _file("ok", "kept.txt", 2, 3, 4, previousPath=7),  # a non-string previousPath is none
    ]
    session = hunkctl.parse_session_get(json.dumps({"session": record}))
    assert session.files == (hunkctl.SessionFile("ok", "kept.txt", None, 2, 3, 4),)
    record["files"] = "nope"
    assert hunkctl.parse_session_get(json.dumps({"session": record})).files == ()
    del record["files"]
    assert hunkctl.parse_session_get(json.dumps({"session": record})).files == ()


def test_session_files_are_capped():
    record = _session(7)
    record["files"] = [_file(f"f{i}", f"file{i}.txt") for i in range(hunkctl.MAX_SESSION_FILES + 10)]
    session = hunkctl.parse_session_get(json.dumps({"session": record}))
    assert len(session.files) == hunkctl.MAX_SESSION_FILES == 5000
    assert session.files[-1].path == f"file{hunkctl.MAX_SESSION_FILES - 1}.txt"


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (_snapshot("src/a.py", 3), ("src/a.py", 3)),
        (_snapshot(None, 0), (None, 0)),
        (_snapshot("a.txt", -1), ("a.txt", None)),
        ({"state": {"selectedFilePath": "a.txt", "selectedHunkIndex": True}}, ("a.txt", None)),
        ({"updatedAt": "x", "state": {"selectedFilePath": 3, "selectedHunkIndex": 1}}, (None, 1)),
        ({"updatedAt": "x", "state": {"selectedFilePath": "p" * 513, "selectedHunkIndex": 1}}, (None, 1)),
        ({"updatedAt": "x", "state": "nope"}, (None, None)),
        ({"updatedAt": "x"}, (None, None)),
        ("garbage", (None, None)),
        (None, (None, None)),
    ],
)
def test_session_selection_is_shape_gated(snapshot, expected):
    record = _session(7)
    record["snapshot"] = snapshot
    session = hunkctl.parse_session_get(json.dumps({"session": record}))
    assert (session.selected_path, session.selected_hunk) == expected


def test_session_without_a_snapshot_has_no_selection():
    session = hunkctl.parse_session_get(json.dumps({"session": _session(7)}))
    assert session.selected_path is None and session.selected_hunk is None and session.files == ()


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
        # The commits list's parent header: a three-dot range between two
        # refs is the range load; a two-dot one, a range with a pathspec
        # and a malformed one are left to hunk.
        ("repo main...feat", ("range", "main...feat")),
        ("repo origin/main...release/v1", ("range", "origin/main...release/v1")),
        ("my repo with spaces main...feat", ("range", "main...feat")),
        ("repo " + SHA + "..." + SHA, ("range", SHA + "..." + SHA)),
        ("repo main...feat -- src/", (None, None)),
        ("repo main....feat", (None, None)),
        ("repo main...feat...x", (None, None)),
        ("repo ...feat", (None, None)),
        ("repo main...", (None, None)),
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


# -- the sidecar -------------------------------------------------------------


def test_sidecar_path():
    assert hunkctl.sidecar_path("/run/user/1000", 4242, 3) == "/run/user/1000/collins/git-4242-3.json"


def test_sidecar_payload():
    """Version 2: the untracked switch is all Collins tells the extension;
    the parent, default, page size and level of version 1 fed the panes
    the extension no longer draws."""
    assert hunkctl.SIDECAR_VERSION == 2
    assert hunkctl.sidecar_payload() == {"version": 2, "untracked": True}
    assert hunkctl.sidecar_payload(False) == {"version": 2, "untracked": False}
    assert hunkctl.sidecar_payload(untracked=0)["untracked"] is False
    assert set(hunkctl.sidecar_payload()) == {"version", "untracked"}


def test_write_sidecar_creates_and_replaces(tmp_path):
    path = str(tmp_path / "collins" / "git-1-1.json")
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload())
    with open(path) as fh:
        assert json.load(fh) == {"version": 2, "untracked": True}
    assert sorted(os.listdir(tmp_path / "collins")) == ["git-1-1.json"]  # no temp file left behind
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload(False))
    with open(path) as fh:
        assert json.load(fh) == {"version": 2, "untracked": False}


def test_write_sidecar_keeps_the_other_sides_keys(tmp_path):
    """Read-merge-write: what the extension (or a newer one) put in the file
    survives Collins' rewrite — its selection, anchor and freshness record,
    a v1 file's stale keys too; Collins' own keys win; the version is
    stamped 2."""
    path = str(tmp_path / "git.json")
    with open(path, "w") as fh:
        json.dump(
            {
                "version": 7,
                "untracked": False,
                "selection": {"path": "a.txt", "hunkIndex": 1},
                "anchor": None,
                "parent": "x",
                "extra": {"a": 1},
            },
            fh,
        )
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload())
    with open(path) as fh:
        data = json.load(fh)
    assert data["extra"] == {"a": 1}
    assert data["selection"] == {"path": "a.txt", "hunkIndex": 1} and data["anchor"] is None
    assert data["parent"] == "x"
    assert data["untracked"] is True and data["version"] == 2


def test_write_sidecar_over_garbage(tmp_path):
    path = str(tmp_path / "git.json")
    with open(path, "w") as fh:
        fh.write("not json")
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload())
    with open(path) as fh:
        assert json.load(fh) == {"version": 2, "untracked": True}


def test_write_sidecar_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    # The parent "directory" is a file: mkdir fails, the write reports False.
    assert not hunkctl.write_sidecar(str(blocker / "collins" / "git.json"), hunkctl.sidecar_payload())


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"selection": {"path": "src/a.py", "hunkIndex": 2}}', hunkctl.Selection("src/a.py", 2)),
        ('{"selection": {"path": "a.txt", "hunkIndex": 0}}', hunkctl.Selection("a.txt", 0)),
        ('{"selection": {"path": "a.txt", "hunkIndex": null}}', hunkctl.Selection("a.txt", None)),
        ('{"selection": {"path": "a.txt"}}', hunkctl.Selection("a.txt", None)),
        ('{"version": 2, "untracked": true, "selection": {"path": "dir name/b c.txt", "hunkIndex": 1}}',
         hunkctl.Selection("dir name/b c.txt", 1)),
        ('{"selection": {"path": "a.txt", "hunkIndex": -1}}', hunkctl.Selection("a.txt", None)),
        ('{"selection": {"path": "a.txt", "hunkIndex": true}}', hunkctl.Selection("a.txt", None)),
        ('{"selection": {"path": "a.txt", "hunkIndex": "2"}}', hunkctl.Selection("a.txt", None)),
        ('{"selection": null}', None),
        ('{"selection": {}}', None),
        ('{"selection": {"path": "", "hunkIndex": 0}}', None),
        ('{"selection": {"path": 3, "hunkIndex": 0}}', None),
        ('{"selection": {"path": "' + "p" * 513 + '", "hunkIndex": 0}}', None),
        ('{"selection": "a.txt"}', None),
        ('{"parent": "main"}', None),
        ("[]", None),
        ("garbage", None),
        ("", None),
    ],
)
def test_read_sidecar_selection(text, expected):
    assert hunkctl.read_sidecar_selection(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"anchor": {"path": "src/a.py", "side": "new", "line": 12}}', hunkctl.Anchor("src/a.py", "new", 12)),  # noqa: E501
        ('{"anchor": {"path": "a.txt", "side": "old", "line": 1}}', hunkctl.Anchor("a.txt", "old", 1)),
        ('{"anchor": {"path": "a.txt", "side": "new", "line": 0}}', None),
        ('{"anchor": {"path": "a.txt", "side": "new", "line": -3}}', None),
        ('{"anchor": {"path": "a.txt", "side": "new", "line": true}}', None),
        ('{"anchor": {"path": "a.txt", "side": "new", "line": "12"}}', None),
        ('{"anchor": {"path": "a.txt", "side": "left", "line": 12}}', None),
        ('{"anchor": {"path": "a.txt", "side": "new"}}', None),
        ('{"anchor": {"path": "", "side": "new", "line": 1}}', None),
        ('{"anchor": {"side": "new", "line": 1}}', None),
        ('{"anchor": null}', None),
        ('{"anchor": "a.txt:12"}', None),
        ('{"selection": {"path": "a.txt", "hunkIndex": 0}}', None),
        ("garbage", None),
        ("", None),
    ],
)
def test_read_sidecar_anchor(text, expected):
    assert hunkctl.read_sidecar_anchor(text) == expected


def test_sidecar_v2_keys_survive_collins_write(tmp_path):
    """The extension's `selection` and `anchor` are the other side's keys:
    Collins' merge keeps them, and a v2 file's unknown keys ride through
    the v1 writer untouched."""
    path = str(tmp_path / "git.json")
    with open(path, "w") as fh:
        fh.write(
            '{"version": 2, "untracked": true, "selection": {"path": "a.txt", "hunkIndex": 1},'
            ' "anchor": {"path": "a.txt", "side": "new", "line": 4}}'
        )
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload())
    with open(path) as fh:
        text = fh.read()
    assert hunkctl.read_sidecar_selection(text) == hunkctl.Selection("a.txt", 1)
    assert hunkctl.read_sidecar_anchor(text) == hunkctl.Anchor("a.txt", "new", 4)


# -- the pinned keys ------------------------------------------------------------------


def test_pinned_keys_are_the_extensions_registered_keys():
    """The native buttons feed these bytes to hunk's pty; the extension's
    registerCommand keys in index.ts must be exactly these, or a button
    presses a key nobody listens to."""
    assert hunkctl.STAGE_KEY == b"x"
    assert hunkctl.STAGE_FILE_KEY == b"X"
    assert hunkctl.ANCHOR_KEY == b"v"
    assert hunkctl.CLEAR_ANCHOR_KEY == b"\x1b"
    assert hunkctl.DISCARD_KEY == b"D"
    with open(os.path.join(hunkctl.EXTENSION_DIR, "index.ts"), encoding="utf-8") as fh:
        source = fh.read()
    registered = dict(re.findall(r'registerCommand\(\s*\{\s*id:\s*"([^"]+)"[^}]*?key:\s*"([^"]+)"', source))
    names = {"escape": b"\x1b"}
    pinned = {
        "stage-hunk": hunkctl.STAGE_KEY,
        "stage-file": hunkctl.STAGE_FILE_KEY,
        "set-anchor": hunkctl.ANCHOR_KEY,
        "clear-anchor": hunkctl.CLEAR_ANCHOR_KEY,
        "discard": hunkctl.DISCARD_KEY,
    }
    for command, key in pinned.items():
        assert command in registered, f"{command} is not registered in index.ts"
        assert names.get(registered[command], registered[command].encode()) == key, command


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
    assert hunkctl.write_sidecar(path, hunkctl.sidecar_payload())
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
        return hunkctl.Probe("/usr/bin/hunk", (0, 21, 1))

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
        (hunkctl.Probe("/usr/bin/hunk", (0, 21, 0)), True),
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
    answers = [hunkctl.Probe(None, None), hunkctl.Probe("/usr/bin/hunk", (0, 21, 1))]
    cache = hunkctl.ProbeCache(probe=lambda: answers.pop(0), clock=_Clock())
    assert cache.ok() is False
    cache.refresh()
    assert cache.ok() is True
