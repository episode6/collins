# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The client's end of git over the API (collins.remotegit, split-service
spec §3.23, PR-2.1), through a fake link that records every request and
answers from a table: the per-cwd mirror serves a fresh entry and
refreshes an old one, `gitinfo`'s reads come off it, `has_changes` asks
fresh with ``changes``, `git-changed` refreshes the entry without blocking
and reaches the page's listener, the transport sends builders by name
and never an argv, an argv no builder makes is `unreachable`, a `stale`
refusal is `ApplyResult.stale`, and a blob is the HTTP GET."""

from __future__ import annotations

import pytest

from collins import apilink, gitinfo, gitops, gitpatch, remotegit
from collins.api import protocol
from collins.api.protocol import RequestRefused
from collins.gitfiles import GitInfo

SHA = "a" * 40
INFO = {
    "root": "/srv/project",
    "git_dir": "/srv/project/.git",
    "branch": "feat",
    "default_branch": "main",
    "github_url": "https://github.com/o/r",
    "index_mtime": 1700000000000,
    "head": SHA,
    "markers": [],
    "operation": None,
    "refs": "r1",
    "remotes": ["origin"],
    "heads": {"feat": SHA, "main": "b" * 40},
    "remote_heads": {"origin/main": "b" * 40, "origin/dev": "c" * 40},
}


class FakeLink(apilink.Link):
    """Answers `call` and `send` from `answers` (by type), recording each
    request; a refusal is a RequestRefused in the table."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[dict, float | None]] = []
        self.sent: list[dict] = []
        self.answers: dict[str, object] = {}
        self.hello = {"service_id": "svc-1"}
        self.gets: list[tuple[str, dict]] = []
        self.get_answer: tuple[int, dict, bytes] = (200, {"ETag": '"x"'}, b"bytes")

    def _request(self, message: dict, timeout: float | None = None) -> dict:
        self.calls.append((dict(message), timeout))
        answer = self.answers.get(message["t"])
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(message)
        return dict(answer or {})

    def send(self, message: dict, on_reply=None, on_refused=None) -> None:
        self.sent.append(dict(message))
        answer = self.answers.get(message["t"])
        if isinstance(answer, Exception):
            if on_refused is not None:
                on_refused(answer)
            return
        if on_reply is not None:
            on_reply(dict(answer(message) if callable(answer) else (answer or {})))

    def http_get(self, path_query: str, headers=None, timeout: float = 60.0):
        self.gets.append((path_query, dict(headers or {})))
        return self.get_answer


@pytest.fixture
def link(monkeypatch):
    fake = FakeLink()
    fake.answers["git.info"] = lambda m: dict(INFO)
    monkeypatch.setattr(apilink, "_current", fake)
    mirror = remotegit.Mirror()
    transport = remotegit.Transport(mirror)
    monkeypatch.setattr(remotegit, "_mirror", mirror)
    monkeypatch.setattr(remotegit, "_transport", transport)
    remotegit.install(fake)
    yield fake
    remotegit.uninstall()


# -- the mirror -----------------------------------------------------------------------


def test_gitinfo_reads_come_off_the_mirror_and_one_fetch_serves_them_all(link):
    assert gitinfo.current_branch("/srv/project") == "feat"
    assert gitinfo.default_branch("/srv/project") == "main"
    assert gitinfo.github_url("/srv/project") == "https://github.com/o/r"
    assert str(gitinfo.repo_root("/srv/project")) == "/srv/project"
    assert str(gitinfo.git_dir("/srv/project")) == "/srv/project/.git"
    assert gitinfo.head_sha("/srv/project") == SHA and gitinfo.index_mtime("/srv/project") == 1700000000000
    assert gitinfo.resolve_branch("/srv/project", "main") == ("main", "b" * 40)
    assert gitinfo.resolve_branch("/srv/project", "dev") == ("origin/dev", "c" * 40)
    assert gitinfo.resolve_branch("/srv/project", "-x") is None
    assert gitinfo.remote_branch_name("/srv/project", "origin/dev") == "dev"
    assert gitinfo.parent_branch("/srv/project", (None, "origin/dev")) == "dev"
    assert gitinfo.tree_signature("/srv/project", "main") == (1700000000000, SHA, "b" * 40, ())
    assert gitinfo.refs_signature("/srv/project") == "r1"
    assert gitinfo.operation_markers("/srv/project") == ()
    assert [m["t"] for m, _t in link.calls] == ["git.info"], link.calls
    assert link.calls[0][0]["cwd"] == "/srv/project" and "changes" not in link.calls[0][0]
    # The in-progress gate asks fresh (the markers as they are now).
    assert gitops.in_progress_at("/srv/project") is None
    assert len(link.calls) == 2 and "changes" not in link.calls[1][0]
    link.answers["git.info"] = lambda m: {**INFO, "operation": "rebase", "markers": ["rebase-merge"]}
    assert gitops.in_progress_at("/srv/project").kind == "rebase"
    assert gitops.in_progress_operation_at("/srv/project") == "rebase"


def test_an_old_entry_is_refreshed_and_the_refs_digest_travels(link, monkeypatch):
    gitinfo.current_branch("/srv/project")
    entry = remotegit.mirror().entry("/srv/project")
    monkeypatch.setattr(remotegit.time, "monotonic", lambda: entry.fetched_at + 5.0)
    gitinfo.current_branch("/srv/project")
    assert len(link.calls) == 2 and link.calls[1][0]["known_refs"] == "r1"
    # A reply without heads (the digest matched) keeps the heads it had.
    slim = {k: v for k, v in INFO.items() if k not in ("heads", "remote_heads", "remotes")}
    link.answers["git.info"] = lambda m: dict(slim)
    gitinfo.refresh("/srv/project")
    assert gitinfo.resolve_branch("/srv/project", "main") == ("main", "b" * 40)


def test_refresh_forces_a_fetch_and_a_fresh_entry_is_served(link):
    gitinfo.current_branch("/srv/project")
    gitinfo.default_branch("/srv/project")
    assert len(link.calls) == 1
    gitinfo.refresh("/srv/project")
    assert len(link.calls) == 2


def test_has_changes_asks_fresh_with_changes(link):
    link.answers["git.info"] = lambda m: {**INFO, "changes": {"staged": True, "unstaged": False}}
    assert gitinfo.change_summary("/srv/project") == (True, False)
    assert gitinfo.has_changes("/srv/project")
    assert all(m["changes"] is True for m, _t in link.calls)
    link.answers["git.info"] = lambda m: {**INFO, "changes": {"staged": False, "unstaged": False}}
    assert not gitinfo.has_changes("/srv/project")


def test_a_service_that_cannot_be_asked_reads_as_no_repository(link):
    link.answers["git.info"] = RequestRefused(protocol.ERROR_GONE, "gone")
    assert gitinfo.current_branch("/srv/project") is None
    assert gitinfo.repo_root("/srv/project") is None
    assert gitinfo.has_changes("/srv/project") is False


def test_git_changed_refreshes_the_entry_without_blocking_and_reaches_the_listener(link):
    gitinfo.current_branch("/srv/project")
    heard: list[dict] = []
    remotegit.mirror().on_changed("/srv/project", heard.append)
    link.answers["git.info"] = lambda m: {**INFO, "branch": "other"}
    before = len(link.calls)
    link.dispatch({"t": "git-changed", "cwd": "/srv/project", "tree": "t2", "refs": "r1", "state": "s2"})
    assert heard == [{"t": "git-changed", "cwd": "/srv/project", "tree": "t2", "refs": "r1", "state": "s2"}]
    assert len(link.calls) == before  # no blocking call: the refresh went by send
    assert link.sent[-1]["t"] == "git.info" and link.sent[-1]["known_refs"] == "r1"
    assert gitinfo.current_branch("/srv/project") == "other"
    remotegit.mirror().off_changed("/srv/project", heard.append)
    link.dispatch({"t": "git-changed", "cwd": "/srv/project", "tree": "t3", "refs": "r1", "state": "s3"})
    assert len(heard) == 1


def test_watch_and_unwatch_are_sends(link):
    remotegit.mirror().watch("/srv/project", ["a.txt", "b/c.txt"], state="s1")
    assert link.sent[-1] == {
        "t": "git.watch", "cwd": "/srv/project", "files": ["a.txt", "b/c.txt"], "state": "s1"
    }
    remotegit.mirror().unwatch("/srv/project")
    assert link.sent[-1] == {"t": "git.unwatch", "cwd": "/srv/project"}


def test_reset_forgets_every_entry(link):
    gitinfo.current_branch("/srv/project")
    remotegit.reset()
    assert remotegit.mirror().entry("/srv/project") is None


def test_the_mirror_evicts_the_least_recently_read(link, monkeypatch):
    """A hit moves an entry to the back: past MAX_ENTRIES the cwd nobody
    has read for longest goes, never a long-lived tab's that was just
    read because it was stored first."""
    monkeypatch.setattr(remotegit, "MAX_ENTRIES", 3)
    link.answers["git.info"] = lambda m: dict(INFO, root=m["cwd"])
    mirror = remotegit.mirror()
    for cwd in ("/a", "/b", "/c"):
        gitinfo.current_branch(cwd)
    gitinfo.current_branch("/a")  # a hit: /a is now the most recently read
    gitinfo.current_branch("/d")  # the fourth entry evicts the least recently read
    assert mirror.entry("/b") is None
    assert all(mirror.entry(cwd) is not None for cwd in ("/a", "/c", "/d"))
    gitinfo.refresh("/c")  # a re-store of a kept entry moves it to the back too
    gitinfo.current_branch("/e")
    assert mirror.entry("/a") is None and mirror.entry("/c") is not None


# -- the transport ----------------------------------------------------------------------


def test_run_git_sends_the_builder_by_name_never_an_argv(link):
    link.answers["git.run"] = {"status": 0, "stdout": "out", "stderr": ""}
    result = gitops.run_git("/srv/project", gitops.log_argv(["main..HEAD"], 7), timeout=3.0)
    assert result == gitops.GitResult(True, "out", "")
    message, timeout = link.calls[-1]
    assert message["t"] == "git.run" and message["builder"] == "log_argv"
    assert message["args"] == {"range_args": ["main..HEAD"], "limit": 7}
    assert "argv" not in message and message["env"] == "default" and message["timeout"] == 3.0
    assert timeout == 3.0 + remotegit.CALL_MARGIN_S


def test_run_git_bytes_carries_stdin_and_ok_statuses(link):
    link.answers["git.run"] = {"status": 1, "stdout": "diff --git", "stderr": ""}
    result = gitops.run_git_bytes("/srv/project", gitops.untracked_diff_argv("new.txt"), ok_statuses=(0, 1))
    assert result.ok and result.stdout == "diff --git"
    link.answers["git.run"] = {"status": 0, "stdout": "a\0", "stderr": ""}
    result = gitops.run_git_bytes("/srv/project", gitinfo.check_ignore_argv(), stdin=b"a\0b\0")
    assert result.ok and link.calls[-1][0]["stdin"] == "a\0b\0"


def test_an_argv_no_builder_makes_is_unreachable_and_logged(link, caplog):
    result = gitops.run_git("/srv/project", ["push", "--force"])
    assert result.unreachable and not result.ok
    assert not any(m["t"] == "git.run" for m, _t in link.calls)
    assert any("no builder" in record.message for record in caplog.records)


def test_environments_map_to_their_names(link):
    link.answers["git.run"] = {"status": 0, "stdout": "", "stderr": ""}
    gitops.run_git("/srv/project", gitops.pull_argv(), env={"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"})
    assert link.calls[-1][0]["env"] == "no_prompt"
    gitops.run_git("/srv/project", gitops.continue_argv("rebase"), env=gitops.no_editor_env({}))
    assert link.calls[-1][0]["env"] == "no_editor"
    result = gitops.run_git("/srv/project", gitops.pull_argv(), env={"SECRET": "1"})
    assert result.unreachable


def test_a_refused_run_and_an_unreachable_service_read_as_unreachable(link):
    link.answers["git.run"] = RequestRefused(protocol.ERROR_REFUSED, "{cwd} is not allowed", {"cwd": "/x"})
    result = gitops.run_git("/srv/project", gitops.status_argv())
    assert result.unreachable and result.stderr == "/x is not allowed"
    link.answers["git.run"] = {"status": None, "stdout": "", "stderr": "no git", "unreachable": True}
    result = gitops.run_git("/srv/project", gitops.status_argv())
    assert result.unreachable and result.stderr == "no git"


def test_a_local_run_still_runs_locally(link):
    """A caller that brings its own `run` (a test, the service) never
    sees the transport."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return type("R", (), {"returncode": 0, "stdout": "local", "stderr": ""})()

    assert gitops.run_git("/srv/project", ["status"], run=run).stdout == "local"
    assert calls == [["git", "status"]] and not link.calls


def test_file_at_is_the_blob_get(link):
    link.get_answer = (200, {"ETag": '"t"'}, b"bytes")
    assert gitops.file_at("/srv/project", None, "a.txt") == b"bytes"
    assert link.gets[-1][0] == "/api/blob?kind=git&cwd=%2Fsrv%2Fproject&at=worktree&path=a.txt"
    assert gitops.file_at("/srv/project", gitops.INDEX_REF, "a.txt") == b"bytes"
    assert "at=index" in link.gets[-1][0]
    assert gitops.file_at("/srv/project", "HEAD", "a.txt") == b"bytes"
    assert "at=ref&path=a.txt&ref=HEAD" in link.gets[-1][0]
    link.get_answer = (404, {}, b"")
    assert gitops.file_at("/srv/project", "HEAD", "gone.txt") is None


def test_tree_state_and_sizes(link):
    link.answers["git.info"] = lambda m: {**INFO, "state": "s9"} if m.get("state") else dict(INFO)
    assert gitops.tree_state_signature("/srv/project") == "s9"
    link.answers["git.sizes"] = {"sizes": {"a.txt": 3, "b.txt": None}}
    assert gitops.file_sizes("/srv/project", ["a.txt", "b.txt", "../x"]) == {"a.txt": 3, "b.txt": None}
    assert link.calls[-1][0] == {"t": "git.sizes", "cwd": "/srv/project", "paths": ["a.txt", "b.txt"]}


def test_run_plan_is_git_plan_and_stale_comes_back_as_stale(link):
    link.answers["git.plan"] = {
        "applied": True, "stdout": "", "stderr": "", "three_way": True, "conflicts": False
    }
    plan = gitpatch.Plan(gitpatch.OP_APPLY_WORKTREE_REVERSE, ("a.txt",), "patch", None, "")
    context = gitops.PlanContext({"show": SHA}, "a.txt", None, "main", ("a.txt|k|0/1",))
    result = gitops.run_plan("/srv/project", plan, three_way=True, context=context)
    assert result.ok and result.three_way and not result.stale
    message = link.calls[-1][0]
    assert message["t"] == "git.plan" and message["op"] == "apply-worktree-reverse"
    assert message["keys"] == ["a.txt|k|0/1"] and message["patch"] == "patch" and message["three_way"]
    assert message["load"] == {"show": SHA} and message["parent_target"] == "main"
    link.answers["git.plan"] = RequestRefused(protocol.ERROR_STALE, "{path} moved", {"path": "a.txt"})
    result = gitops.run_plan("/srv/project", plan, context=context)
    assert result.stale and not result.ok and result.stderr == "a.txt moved"
    assert gitops.run_plan("/srv/project", plan).stderr == "no plan context"


def test_blob_url():
    assert remotegit.blob_url("/p", gitops.AT_REF, "HEAD~1", "x y.png") == (
        "/api/blob?kind=git&cwd=%2Fp&at=ref&path=x+y.png&ref=HEAD~1"
    )
    assert "ref=" not in remotegit.blob_url("/p", gitops.AT_INDEX, None, "a")


def test_git_info_round_trips_through_fields():
    info = GitInfo.from_fields(INFO)
    assert info.to_fields()["heads"] == INFO["heads"]
    assert "heads" not in info.to_fields(known_refs="r1")
    again = GitInfo.from_fields(info.to_fields(known_refs="r1"), previous=info)
    assert again.heads == info.heads and again.remotes == info.remotes
    assert GitInfo.from_fields({"root": None}).root is None
