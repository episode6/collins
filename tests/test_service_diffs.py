# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The marks on a session's diff, kept by the service and mirrored
(collins.service.diffs, collins.remotediffs, spec §3.7, §3.8, PR-1.11):
the records round-trip, a page's write is persisted and echoed without a
loop, and with no client attached the diff tools work on the service's
own read of the diff."""

import shutil
import subprocess

import pytest

from collins import diffnotes, mcptools
from collins.api import protocol
from collins.apilink import Link
from collins.remotediffs import DiffNotesMirror
from collins.service.diffs import DiffNotes

GIT = shutil.which("git")


class State:
    def __init__(self):
        self.diff_notes = {}
        self.pending = {}
        self.saves = 0

    def get_diff_notes(self, key):
        return dict(self.diff_notes.get(key) or {})

    def set_diff_notes(self, key, marks):
        if marks and (marks.get("notes") or marks.get("highlights")):
            self.diff_notes[key] = marks
        else:
            self.diff_notes.pop(key, None)

    def get_pending_diffs(self):
        return dict(self.pending)

    def save(self):
        self.saves += 1


NOTE = {
    "id": "n3",
    "source": "agent",
    "path": "a.py",
    "side": "new",
    "line": 2,
    "summary": "Why",
    "rationale": "Because",
    "hunk_key": "k",
    "line_index": 1,
}
HIGHLIGHT = {
    "id": "h1",
    "path": "a.py",
    "side": "new",
    "line": 2,
    "start": 0,
    "end": 3,
    "tone": "warning",
    "hunk_key": "k",
    "line_index": 1,
}


def test_marks_round_trip_and_new_ids_mint_past_them():
    store = diffnotes.MarkStore()
    store.load([NOTE, {"id": "bad"}], [HIGHLIGHT, {**HIGHLIGHT, "id": "h2", "start": 5, "end": 1}])
    notes, highlights = store.export()
    assert notes == [NOTE] and highlights == [HIGHLIGHT]
    assert store._mint("n") == "n4"


class Wire(Link):
    def __init__(self, diffs):
        super().__init__()
        self.diffs = diffs
        self.sent = []

    def _request(self, message):
        self.sent.append(message)
        checked = protocol.validate({**message, "id": 1}, protocol.CLIENT)
        assert isinstance(checked, protocol.Message), checked
        return dict(protocol.validate_response(self.diffs.set_notes(checked), checked.type).fields)


@pytest.fixture
def linked():
    state = State()
    events = []
    holder = {}

    def broadcast(event):
        assert isinstance(protocol.validate(dict(event), protocol.SERVICE), protocol.Message)
        events.append(event)
        holder["wire"].dispatch(event)

    diffs = DiffNotes(state, broadcast, dispatch=lambda fn, *a: fn(*a), spawn=lambda fn: fn())
    wire = Wire(diffs)
    holder["wire"] = wire
    mirror = DiffNotesMirror(wire)
    return state, diffs, wire, mirror, events


def test_a_pages_write_is_kept_persisted_and_its_echo_ignored(linked):
    state, _diffs, wire, mirror, events = linked
    heard = []
    mirror.listen("sid", heard.append)
    mirror.send("sid", True, [NOTE], [])
    assert state.diff_notes["sid"]["notes"] == [NOTE] and state.saves == 1
    assert len(events) == 1 and heard == []  # the echo: what the copy holds already
    mirror.send("sid", True, [NOTE], [])
    assert len(wire.sent) == 1  # unchanged: nothing sent


def test_a_handles_marks_are_not_persisted(linked):
    state, _diffs, _wire, mirror, events = linked
    mirror.send("s-4", False, [NOTE], [])
    assert state.diff_notes == {} and events[-1]["handle"] == "s-4"


def test_the_snapshot_brings_saved_marks(linked):
    state, diffs, _wire, _mirror, _events = linked
    state.diff_notes["sid"] = {"notes": [NOTE], "highlights": []}
    seen = []
    assert diffs.subscribe(seen.append) == 1
    assert seen[0]["session"] == "sid" and seen[0]["notes"] == [NOTE]


class FakeSession:
    def __init__(self, cwd):
        self.session_id = "sid"
        self.handle = "s-1"
        self.cwd = cwd

    def current_agent_cwd(self):
        return self.cwd


@pytest.mark.skipif(GIT is None, reason="git is not installed")
def test_with_no_client_the_tools_work_on_the_services_read(tmp_path, linked):
    state, diffs, _wire, mirror, events = linked
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for argv in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "A"]):
        subprocess.run([GIT, *argv], cwd=repo, check=True, env=env)
    (repo / "a.py").write_text("one\ntwo\n")
    subprocess.run([GIT, "add", "a.py"], cwd=repo, check=True, env=env)
    subprocess.run([GIT, "commit", "-qm", "c"], cwd=repo, check=True, env=env)
    (repo / "a.py").write_text("one\nTWO\nthree\n")
    session = FakeSession(str(repo))

    ok, text = diffs.context(session, {"files": True})
    assert ok and "a.py" in text
    got = diffs.annotate(session, {"notes": [{"file": "a.py", "line": 2, "summary": "changed"}]})
    assert got[0] is True
    assert state.diff_notes["sid"]["notes"][0]["summary"] == "changed"
    assert mirror.marks("sid")[0][0]["summary"] == "changed"  # mirrored to the client
    got = diffs.highlight(session, {"marks": [{"file": "a.py", "line": 2, "start": 0, "end": 3}]})
    assert got[0] is True
    got = diffs.clear(session, {"notes": True})
    assert got[0] is True and state.diff_notes["sid"]["notes"] == []
    assert diffs.annotate(session, {"notes": [{"file": "nope.py", "line": 1, "summary": "x"}]})[0] is False


def test_not_a_repository_is_refused(tmp_path, linked):
    _state, diffs, _wire, _mirror, _events = linked
    assert diffs.context(FakeSession(str(tmp_path)), {}) == (
        False,
        "The session's working directory isn't inside a git repository",
    )
    assert mcptools.PAGE_NOT_OPEN  # the client half's refusal is unchanged


def test_a_read_that_raises_answers_at_once(tmp_path, linked, monkeypatch):
    """An exception out of the diff read lands as a failed read, not a
    call left hanging until the shim's timeout."""
    from collins import gitinfo, gitops

    _state, diffs, _wire, _mirror, _events = linked
    monkeypatch.setattr(gitinfo, "repo_root", lambda cwd: tmp_path)

    def boom(*_args, **_kwargs):
        raise OSError("git vanished")

    monkeypatch.setattr(gitops, "read_diff", boom)
    got = diffs.context(FakeSession(str(tmp_path)), {})
    assert got == (False, "git vanished")
    got = diffs.annotate(FakeSession(str(tmp_path)), {"notes": [{"file": "a.py", "line": 1, "summary": "s"}]})
    assert got == (False, "git vanished")


# -- the tools' files, resolved on the service (PR-3.0 item 5, D54 (6)) ------------------


def test_a_file_only_there_against_the_agents_cwd_is_made_absolute(monkeypatch):
    """An agent that cd'd into a subdirectory names files the way its shell
    sees them; a path that is only there against its cwd is taken from
    there — and one that exists against the root wins outright. Resolved
    on the service, whose disk it is, before any client sees the call."""
    from collins.service.diffs import resolve_file_arg

    root, cwd = "/repo", "/repo/pkg"
    only_in_pkg = {"/repo/pkg/a.py"}
    assert resolve_file_arg("a.py", root, cwd, exists=only_in_pkg.__contains__) == "/repo/pkg/a.py"
    both = {"/repo/a.py", "/repo/pkg/a.py"}
    assert resolve_file_arg("a.py", root, cwd, exists=both.__contains__) == "a.py"
    assert resolve_file_arg("a.py", root, cwd, exists=set().__contains__) == "a.py"
    assert resolve_file_arg("a.py", root, root, exists=only_in_pkg.__contains__) == "a.py"  # no subdirectory
    assert resolve_file_arg("a.py", None, cwd, exists=only_in_pkg.__contains__) == "a.py"  # no repository
    # A `~` is this machine's home; anything that is no path is left alone.
    monkeypatch.setenv("HOME", "/srv/u")
    assert resolve_file_arg("~/repo/a.py", root, cwd) == "/srv/u/repo/a.py"
    assert resolve_file_arg("", root, cwd) == "" and resolve_file_arg(None, root, cwd) is None
    # The client's half then reads it against the root: the cwd never lifts
    # a path out of the repository.
    from collins import gitloads

    lifted = resolve_file_arg("../../x.py", root, cwd, exists=lambda p: p == "/x.py")
    assert gitloads.diff_file_path(lifted, root) is None


def test_every_diff_tools_file_is_resolved_and_the_rest_left_alone(tmp_path):
    from collins.service.diffs import resolve_tool_files

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "pkg" / "a.py").write_text("x")
    cwd = str(repo / "pkg")
    args = {"what": "staged", "file": "a.py", "line": 3}
    got = resolve_tool_files("show_diff", args, cwd)
    assert got == {"what": "staged", "file": str(repo / "pkg" / "a.py"), "line": 3}
    assert args["file"] == "a.py"  # the caller's dict untouched
    notes = {"notes": [
        {"file": "a.py", "line": 1, "summary": "s"}, {"file": "pkg/a.py", "line": 2, "summary": "t"},
    ]}
    got = resolve_tool_files("annotate_diff", notes, cwd)
    assert [n["file"] for n in got["notes"]] == [str(repo / "pkg" / "a.py"), "pkg/a.py"]
    marks = {"marks": [{"file": "a.py", "line": 1, "start": 0, "end": 1}]}
    assert resolve_tool_files("highlight_diff", marks, cwd)["marks"][0]["file"] == str(repo / "pkg" / "a.py")
    other = {"file": "a.py"}
    assert resolve_tool_files("open_in_editor", other, cwd) is other
