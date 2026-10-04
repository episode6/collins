# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The job shape (PR-1.11): `service.jobs.JobRunner` and its workers'
outcomes, `job.start` / `job.cancel` through the loopback and the core,
and the client's end (`jobclient`): events in order, the final one last,
translated text, events that arrive before the reply, a refused start."""

import threading

import pytest

from collins import apilink, jobclient
from collins.api import loopback, protocol
from collins.service import jobs
from collins.service.core import ServiceCore


def inline_runner(workers):
    return jobs.JobRunner(workers, dispatch=lambda fn: fn(), spawn=lambda fn, _name: fn())


def run(runner, kind, args=None):
    events = []
    job_id = runner.start(kind, args or {}, events.append)
    return job_id, events


def test_a_job_reports_progress_then_its_result():
    def worker(job, args):
        job.progress("Cloning…", result={"so_far": 1})
        return {"path": args["dest"]}

    job_id, events = run(inline_runner({"clone": worker}), "clone", {"dest": "/x"})
    assert [e["state"] for e in events] == ["running", "done"]
    assert events[0]["msgid"] == "Cloning…" and events[0]["result"] == {"so_far": 1}
    assert events[1]["result"] == {"path": "/x"}
    assert all(e["job"] == job_id and e["kind"] == "clone" for e in events)
    for event in events:
        assert isinstance(protocol.validate(event, protocol.SERVICE), protocol.Message)


def test_a_failure_carries_its_msgid_and_args():
    def worker(job, args):
        raise jobs.JobFailed("The clone failed (exit status {code})", {"code": 128})

    _job, events = run(inline_runner({"clone": worker}), "clone")
    assert events[-1]["state"] == "failed"
    assert events[-1]["msgid"] == "The clone failed (exit status {code})"
    assert events[-1]["args"] == {"code": 128}


def test_foreign_words_cross_as_the_argument_of_error():
    failure = jobs.failure("fatal: repository not found")
    assert failure.msgid == "{error}" and failure.details == {"error": "fatal: repository not found"}


def test_bad_arguments_are_refused_and_an_unknown_kind_too():
    _job, events = run(inline_runner(dict(jobs.WORKERS)), "worktree.trash", {"state": "nope"})
    assert events[-1]["state"] == "refused"
    _job, events = run(inline_runner({}), "clone")
    assert events[-1]["state"] == "refused" and events[-1]["args"] == {"kind": "clone"}


def test_a_surprise_is_a_failure_not_a_hang():
    def worker(job, args):
        raise RuntimeError("boom")

    _job, events = run(inline_runner({"icon": worker}), "icon")
    assert events[-1]["state"] == "failed"


def test_cancel_runs_the_jobs_callbacks_and_ends_it_cancelled():
    started, release = threading.Event(), threading.Event()
    killed = []

    def worker(job, args):
        job.on_cancel(lambda: killed.append(True))
        started.set()
        release.wait(5)
        job.check()
        return {}

    events = []
    runner = jobs.JobRunner({"clone": worker}, dispatch=lambda fn: fn())
    job_id = runner.start("clone", {}, events.append)
    assert started.wait(5)
    assert runner.cancel(job_id)
    release.set()
    for _ in range(500):
        if events:
            break
        threading.Event().wait(0.01)
    assert killed == [True]
    assert events[-1]["state"] == "cancelled"
    assert not runner.cancel(job_id)  # over


def test_a_gone_client_hears_nothing_and_the_job_still_ends():
    held = []
    runner = jobs.JobRunner({"clone": lambda job, args: {}}, dispatch=held.append, spawn=lambda fn, _n: fn())
    events = []
    deliver = events.append
    job_id = runner.start("clone", {}, deliver)
    runner.forget(deliver)
    for fn in held:
        fn()
    assert events == []
    assert job_id not in runner.jobs


def test_chats_trust_refuses_a_relative_folder():
    _job, events = run(inline_runner(dict(jobs.WORKERS)), "chats.trust", {"cwd": "relative"})
    assert events[-1]["state"] == "refused"


def test_login_repair_takes_a_mode(monkeypatch):
    from collins import tokenrefresh

    calls = []

    def fake_start(on_refreshed):
        calls.append("start")
        thread = threading.Thread(target=on_refreshed)
        thread.start()
        return thread

    monkeypatch.setattr(tokenrefresh, "maybe_start", fake_start)
    _job, events = run(inline_runner(dict(jobs.WORKERS)), "login.repair", {"mode": "start"})
    assert calls == ["start"]
    assert events[-1]["state"] == "done" and events[-1]["result"] == {"refreshed": True, "ran": True}
    _job, events = run(inline_runner(dict(jobs.WORKERS)), "login.repair", {"mode": "sometimes"})
    assert events[-1]["state"] == "refused"


# -- through the loopback and the client's end


class _Link(apilink.LoopbackLink):
    pass


@pytest.fixture
def linked(tmp_path):
    core = ServiceCore(state_dir=tmp_path / "pty")
    core.jobs = inline_runner(
        {
            "clone": lambda job, args: (job.progress("Cloning…"), {"path": "/home/u/r"})[1],
            "icon": lambda job, args: (_ for _ in ()).throw(
                jobs.JobFailed("Icon generation failed: {error}", {"error": "no SVG"})
            ),
        }
    )
    server = loopback.LoopbackServer(core)
    link = _Link()
    link.bind(server.connect(lambda *_a: None, link.dispatch))
    yield link
    server.shutdown()


def test_the_client_hears_a_job_in_order_and_translated(linked):
    seen = []
    job_id = jobclient.jobs_for(linked).start("clone", {"source": "o/r"}, seen.append)
    assert job_id
    # The inline runner answers inside the request: the events came before
    # the reply named the job, and were held for it.
    assert [e.state for e in seen] == ["running", "done"]
    assert seen[0].text == "Cloning…" and not seen[0].finished
    assert seen[1].ok and seen[1].result == {"path": "/home/u/r"}


def test_a_failed_job_reads_as_its_words(linked):
    seen = []
    jobclient.jobs_for(linked).start("icon", {}, seen.append)
    assert seen[-1].state == "failed" and seen[-1].text == "Icon generation failed: no SVG"


def test_a_refused_start_is_one_final_event(linked):
    seen = []
    assert jobclient.jobs_for(linked).start("clone", {"x": float("nan")}, seen.append) is None
    assert len(seen) == 1 and seen[0].state == "refused" and seen[0].finished


def test_cancelling_a_finished_job_is_quiet(linked):
    seen = []
    job_id = jobclient.jobs_for(linked).start("clone", {}, seen.append)
    jobclient.jobs_for(linked).cancel(job_id)  # nothing raises


def test_translate_formats_only_with_args():
    assert jobclient.translate("use {foo} here") == "use {foo} here"
    assert jobclient.translate("Exit status {code}", {"code": 2}) == "Exit status 2"
    assert jobclient.translate("Missing {name}", {"code": 2}) == "Missing {name}"
    assert jobclient.translate("") == ""


def test_a_cancelled_login_repair_stops_waiting(monkeypatch):
    """The repair's own thread runs on (tokenrefresh is single-flight); the
    job stops waiting for it the moment it is cancelled."""
    from collins import tokenrefresh

    release = threading.Event()
    started = threading.Event()

    def fake_repair(on_refreshed):
        thread = threading.Thread(target=release.wait, args=(10,), daemon=True)
        thread.start()
        started.set()
        return thread

    monkeypatch.setattr(tokenrefresh, "maybe_repair", fake_repair)
    events = []
    runner = jobs.JobRunner(dict(jobs.WORKERS), dispatch=lambda fn: fn())
    job_id = runner.start("login.repair", {"mode": "repair"}, events.append)
    try:
        assert started.wait(5)
        assert runner.cancel(job_id)
        for _ in range(200):
            if events:
                break
            threading.Event().wait(0.01)
        assert [e["state"] for e in events] == ["cancelled"]
    finally:
        release.set()
