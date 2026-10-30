# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The notification history on the service and its client mirror (spec
§3.13, §3.14, PR-1.11): the service mints and keeps every row and tells
every subscriber; a client's `RemoteNotifications` is a NotificationCenter
over that, its rows' text translated from msgid and args, its reads told
back as `seen`, which every other client hears."""

import os
import time

import pytest

from collins import notifycenter
from collins.api import protocol
from collins.api.protocol import RequestRefused
from collins.apilink import Link
from collins.notifycenter import KIND_BELL, KIND_MESSAGE, KIND_UPDATE
from collins.remotenotify import RemoteNotifications
from collins.service import notifications as noti
from collins.service.notifications import ServiceNotifications


class State:
    def __init__(self, records=None):
        self.records = list(records or [])
        self.writes = 0

    def get_notifications(self):
        return list(self.records)

    def set_notifications(self, records):
        if records != self.records:
            self.records = list(records)
            self.writes += 1


class Link_(Link):
    """A link straight to a ServiceNotifications: every request and event
    validated as the loopback validates them."""

    def __init__(self, service):
        super().__init__()
        self.service = service
        self.sent = []
        self._next = 1

    def deliver(self, event):
        checked = protocol.validate(dict(event), protocol.SERVICE)
        assert isinstance(checked, protocol.Message), checked
        self.dispatch(event)

    def _request(self, message):
        message = {**message, "id": self._next}
        self._next += 1
        self.sent.append(message)
        checked = protocol.validate(message, protocol.CLIENT)
        assert isinstance(checked, protocol.Message), checked
        answer = protocol.validate_response(self.service.handle(checked, self), checked.type)
        if not answer.ok:
            raise RequestRefused(answer.error, answer.msgid, answer.args)
        return dict(answer.fields)


def connect(service):
    link = Link_(service)
    mirror = RemoteNotifications(link)
    service.subscribe(link, link.deliver)
    return link, mirror


@pytest.fixture
def service():
    return ServiceNotifications(State())


def test_a_row_is_minted_on_the_service_and_mirrored(service):
    link, mirror = connect(service)
    calls = []
    mirror.connect(lambda: calls.append(1))
    row = mirror.post(mirror.make(KIND_MESSAGE, "s1", "Fix it", "proj", "Done!"))
    assert row is mirror.get(row.id) and row.id == service.center.rows()[0].id
    assert row.msgid == "Done!" and row.body == "Done!" and not row.read
    assert calls == [1]  # one change per write, however many events
    assert service.state.records[0]["msgid"] == "Done!"


def test_a_bell_coalesces_on_the_service(service):
    _link, mirror = connect(service)
    first = mirror.post(
        mirror.make(KIND_BELL, "s1", "t", "p", "Rang the bell", msgid=notifycenter.BELL_MSGID)
    )
    again = mirror.post(
        mirror.make(KIND_BELL, "s1", "t", "p", "Rang the bell", msgid=notifycenter.BELL_MSGID)
    )
    assert again.id == first.id and again.count == 2 and len(mirror.rows()) == 1


def test_the_body_is_translated_from_its_msgid_and_args(service, monkeypatch):
    from collins import i18n

    monkeypatch.setattr(i18n, "_", lambda text: {"Hi {who}": "Hallo {who}"}.get(text, text))
    _link, mirror = connect(service)
    service.post(KIND_MESSAGE, "s1", "t", "p", "Hi {who}", {"who": "Ada"})
    assert mirror.rows()[0].body == "Hallo Ada"
    assert service.center.rows()[0].body == "Hi Ada"  # the service writes English


def test_seen_reads_rows_everywhere(service):
    link_a, a = connect(service)
    link_b, b = connect(service)
    row = a.post(a.make(KIND_MESSAGE, "s1", "t", "p", "one"))
    a.post(a.make(KIND_MESSAGE, "s2", "t", "p", "two"))
    assert b.unread_count() == 2
    assert a.mark_read(row.id)
    assert a.unread_count() == 1 and b.unread_count() == 1 and service.center.unread_count() == 1
    assert any(m["t"] == "seen" and m["ids"] == [row.id] for m in link_a.sent)
    b.mark_session_read("s2")
    assert a.unread_count() == 0 and service.center.unread_count() == 0


def test_mark_all_read_and_clear_and_remove(service):
    _link, mirror = connect(service)
    gone = mirror.post(mirror.make(KIND_MESSAGE, "s1", "t", "p", "gone"))
    mirror.post(mirror.make(KIND_MESSAGE, "s1", "t", "p", "kept"))
    assert mirror.remove(gone.id) and mirror.get(gone.id) is None
    assert service.center.get(gone.id) is None
    mirror.set_green("s3", True, title="t", project="p")
    assert mirror.mark_all_read() == 2
    assert mirror.clear() == 1
    # The finished run's row stays: it is the flag's to take down.
    assert [r.kind for r in mirror.rows()] == [notifycenter.KIND_FINISHED]
    assert mirror.rows()[0].body == "Finished a run"


def test_green_is_raised_and_lowered_through_the_service(service):
    _link, mirror = connect(service)
    assert mirror.set_green("s1", True, title="t", project="p")
    assert mirror.is_green("s1") and service.center.is_green("s1")
    assert not mirror.set_green("s1", True)  # a no-op asks nothing
    assert mirror.set_green("s1", False) and not mirror.is_green("s1")


def test_a_placeholders_rows_are_rekeyed(service):
    _link, mirror = connect(service)
    mirror.post(mirror.make(KIND_MESSAGE, "placeholder-3", "t", "p", "early"))
    assert mirror.rekey_session("placeholder-3", "sid") == 1
    assert mirror.rows()[0].session_id == "sid"


def test_an_update_keeps_its_fixed_id_and_replaces_the_last(service):
    _link, mirror = connect(service)
    for version in ("0.2.0", "0.2.1"):
        row = mirror.make(
            KIND_UPDATE, "", f"Collins {version}", "", "x", msgid="Running {version}", args={"version": "0.1"}
        )
        row.id = notifycenter.update_id(version)
        row.url = "https://example.com/r"
        mirror.post(row)
    assert [r.id for r in mirror.rows()] == ["update:0.2.1"]
    assert mirror.rows()[0].body == "Running 0.1" and mirror.rows()[0].url == "https://example.com/r"


def test_a_fixed_id_is_refused_for_anything_but_an_update(service):
    link, _mirror = connect(service)
    with pytest.raises(RequestRefused):
        link.call({"t": "notify.post", "kind": "message", "msgid": "x", "key": "update:1"})


def test_the_snapshot_keeps_the_order_and_existing_history(service):
    old = [
        {
            "id": "b",
            "session_id": "s",
            "title": "t",
            "project": "p",
            "kind": KIND_BELL,
            "body": "Hat geklingelt",
            "when": 2e9,
            "read": False,
            "count": 2,
        },
        {
            "id": "a",
            "session_id": "s",
            "title": "t",
            "project": "p",
            "kind": KIND_MESSAGE,
            "body": "older",
            "when": 2e9 - 5,
            "read": True,
            "count": 1,
        },
    ]
    svc = ServiceNotifications(State(old))
    _link, mirror = connect(svc)
    assert [r.id for r in mirror.rows()] == ["b", "a"]
    # A record from before msgids: its body is its msgid, shown as it was.
    assert mirror.rows()[0].body == "Hat geklingelt" and mirror.rows()[0].count == 2


def test_a_refused_write_changes_nothing(service):
    _link, mirror = connect(service)
    mirror._link = type(
        "Dead",
        (Link,),
        {
            "_request": lambda self, m: (_ for _ in ()).throw(
                RequestRefused(protocol.ERROR_GONE, "Not connected to the service", {})
            )
        },
    )()
    row = mirror.make(KIND_MESSAGE, "s1", "t", "p", "lost")
    assert mirror.post(row) is row and mirror.rows() == []


# -- the sink: notify_command (PR-3.4, spec §3.13, §3.24) ------------------------------------------

SCRIPT = """#!/bin/sh
# Records what it was run with: argv one per line, the five variables, and
# whatever its stdin held (nothing: it is /dev/null).
{
  for a in "$@"; do printf 'arg=%s\\n' "$a"; done
  printf 'COLLINS_NOTIFY_KIND=%s\\n' "$COLLINS_NOTIFY_KIND"
  printf 'COLLINS_NOTIFY_TITLE=%s\\n' "$COLLINS_NOTIFY_TITLE"
  printf 'COLLINS_NOTIFY_BODY=%s\\n' "$COLLINS_NOTIFY_BODY"
  printf 'COLLINS_NOTIFY_SESSION=%s\\n' "$COLLINS_NOTIFY_SESSION"
  printf 'COLLINS_NOTIFY_PROJECT=%s\\n' "$COLLINS_NOTIFY_PROJECT"
  printf 'stdin=%s\\n' "$(cat)"
} > "$OUT"
exit "${EXIT:-0}"
"""


@pytest.fixture
def script(tmp_path, monkeypatch):
    path = tmp_path / "record.sh"
    path.write_text(SCRIPT)
    path.chmod(0o755)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("OUT", str(out))

    def recorded() -> list[str]:
        return out.read_text().splitlines()

    return path, recorded


def sink_service(template, clients=False, **kwargs):
    return ServiceNotifications(
        State(),
        get_setting={"notify_command": template}.get,
        has_clients=lambda: clients,
        run_async=lambda fn: fn(),
        dispatch=lambda fn: fn(),
        **kwargs,
    )


def test_the_sink_runs_only_with_no_client_subscribed(script):
    path, recorded = script
    svc = ServiceNotifications(
        State(), get_setting={"notify_command": str(path)}.get, run_async=lambda fn: fn()
    )
    link, _mirror = connect(svc)
    assert svc.sink("message", "s1", "Fix it", "proj", "Done") is False
    assert not (path.parent / "out.txt").exists()
    svc.unsubscribe(link)
    assert svc.sink("message", "s1", "Fix it", "proj", "Done") is True
    assert "COLLINS_NOTIFY_BODY=Done" in recorded()


def test_the_cores_subscribers_decide_when_given(script):
    path, _recorded = script
    attached = [object()]
    svc = ServiceNotifications(
        State(),
        get_setting={"notify_command": str(path)}.get,
        has_clients=lambda: bool(attached),
        run_async=lambda fn: fn(),
    )
    assert svc.sink("message", "s1", "t", "p", "b") is False
    attached.clear()
    assert svc.sink("message", "s1", "t", "p", "b") is True


def test_an_empty_setting_runs_nothing():
    for value in ("", "   ", None, 7):
        svc = sink_service(value)
        assert svc.sink("message", "s1", "t", "p", "b") is False


def test_the_five_variables_stdin_and_argv(script):
    path, recorded = script
    svc = sink_service(f"{path} one 'two words'")
    assert svc.sink("finished", "sid-1", "Fix the thing", "collins", "Finished a run")
    assert recorded() == [
        "arg=one",
        "arg=two words",
        "COLLINS_NOTIFY_KIND=finished",
        "COLLINS_NOTIFY_TITLE=Fix the thing",
        "COLLINS_NOTIFY_BODY=Finished a run",
        "COLLINS_NOTIFY_SESSION=sid-1",
        "COLLINS_NOTIFY_PROJECT=collins",
        "stdin=",  # /dev/null: EOF at once, the cat did not hang
    ]


def test_the_template_is_shlex_split_and_never_a_shell(script, tmp_path):
    path, recorded = script
    marker = tmp_path / "marker"
    svc = sink_service(f"{path} '$COLLINS_NOTIFY_TITLE' $HOME `id` ; touch {marker} | cat > /dev/null")
    svc.sink("message", "s1", "T", "p", "b")
    args = [line for line in recorded() if line.startswith("arg=")]
    # Nothing was expanded, nothing was a separator: a shell would have
    # run touch, and shlex hands the metacharacters to the program as text.
    assert "arg=$COLLINS_NOTIFY_TITLE" in args and "arg=$HOME" in args and "arg=`id`" in args
    assert "arg=;" in args and "arg=|" in args and "arg=>" in args
    assert not marker.exists()


def test_the_values_are_bounded_and_carry_no_nul():
    env = noti.command_env("message", "t" * 5000, "b\0ad" + "x" * 20000, "s" * 999, "p")
    assert len(env["COLLINS_NOTIFY_TITLE"]) == 1024
    assert "\0" not in env["COLLINS_NOTIFY_BODY"] and len(env["COLLINS_NOTIFY_BODY"]) == 8192
    assert len(env["COLLINS_NOTIFY_SESSION"]) == 256
    assert set(env) == {f"COLLINS_NOTIFY_{n}" for n in ("KIND", "TITLE", "BODY", "SESSION", "PROJECT")}


def test_the_timeout_kills_the_command(tmp_path):
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    started = time.monotonic()
    status, error = noti.run_command(str(slow), {}, timeout=0.3)
    assert status is None and error == "Timed out after 0.3 s"
    assert time.monotonic() - started < 5
    assert noti.COMMAND_TIMEOUT_S == 10.0


def test_a_bad_template_or_missing_program_fails_soft(tmp_path):
    with pytest.raises(ValueError):
        noti.split_command("echo 'unterminated")
    assert noti.run_command("echo 'unterminated", {})[0] is None
    status, error = noti.run_command(str(tmp_path / "nowhere"), {})
    assert status is None and error == "No such file or directory"
    # Through the sink: skipped and logged, nothing raised.
    svc = sink_service(str(tmp_path / "nowhere"))
    assert svc.sink("message", "s1", "t", "p", "b") is True

    def boom(*_args):
        raise RuntimeError("boom")

    assert sink_service("x", run=boom).sink("message", "s1", "t", "p", "b") is True


def test_the_sink_runs_off_the_loop():
    started = []
    svc = ServiceNotifications(
        State(),
        get_setting={"notify_command": "x"}.get,
        run_async=started.append,
        run=lambda *_a: (0, ""),
    )
    assert svc.sink("message", "s1", "t", "p", "b") is True and len(started) == 1


def ask_test(svc, command):
    message = protocol.validate({"t": "notify.test", "id": 7, "command": command}, protocol.CLIENT)
    assert isinstance(message, protocol.Message), message
    answer = svc.handle(message, None)
    if isinstance(answer, protocol.Deferred):
        assert answer.settled
        answer = answer.reply
    return protocol.validate_response(answer, "notify.test")


def test_notify_test_runs_the_box_text_with_a_subscriber_attached(script, monkeypatch):
    path, recorded = script
    monkeypatch.setenv("EXIT", "3")
    # The saved setting says something else; the box's text is what runs.
    svc = ServiceNotifications(
        State(),
        get_setting={"notify_command": "false"}.get,
        run_async=lambda fn: fn(),
        dispatch=lambda fn: fn(),
    )
    connect(svc)  # a client is subscribed: the test runs regardless
    answer = ask_test(svc, f"{path} one")
    assert answer.ok and answer.fields == {"status": 3}  # exactly one of status / error
    assert recorded() == [
        "arg=one",
        "COLLINS_NOTIFY_KIND=test",
        "COLLINS_NOTIFY_TITLE=Collins test notification",
        "COLLINS_NOTIFY_BODY=Sent from Preferences",
        "COLLINS_NOTIFY_SESSION=",
        "COLLINS_NOTIFY_PROJECT=",
        "stdin=",
    ]


def test_notify_test_replies_with_the_error_for_a_command_that_wont_start(tmp_path):
    answer = ask_test(sink_service(""), str(tmp_path / "nowhere"))
    assert answer.ok and answer.fields == {"error": "No such file or directory"}
    assert len(answer.fields["error"]) <= protocol.ARG_TEXT_MAX


def test_notify_test_replies_with_the_error_on_the_timeout(tmp_path, monkeypatch):
    slow = tmp_path / "slow.sh"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    real = noti.run_command
    monkeypatch.setattr(noti, "run_command", lambda template, env: real(template, env, timeout=0.3))
    svc = ServiceNotifications(State(), run_async=lambda fn: fn(), dispatch=lambda fn: fn())
    answer = ask_test(svc, str(slow))
    assert answer.ok and answer.fields == {"error": "Timed out after 0.3 s"}


def test_notify_test_runs_off_the_loop_and_settles_later():
    started = []
    svc = ServiceNotifications(
        State(), run_async=started.append, run=lambda *_a: (0, ""), dispatch=lambda fn: fn()
    )
    message = protocol.validate({"t": "notify.test", "id": 7, "command": "true"}, protocol.CLIENT)
    deferred = svc.handle(message, None)
    assert isinstance(deferred, protocol.Deferred) and not deferred.settled and len(started) == 1
    started[0]()
    assert deferred.settled and deferred.reply["status"] == 0


def test_notify_test_refuses_an_empty_or_unsplittable_command():
    for command in ("", "   ", "echo 'unterminated"):
        answer = ask_test(sink_service("true"), command)
        assert not answer.ok and answer.error == protocol.ERROR_INVALID


# -- the client's end of Send test (remotenotify, PR-3.4, D62) -----------------------------------


class SendLink:
    """A link whose `send` answers with what the test says."""

    def __init__(self, reply=None, refusal=None):
        self.reply, self.refusal, self.sent = reply, refusal, []

    def send(self, message, on_reply=None, on_refused=None):
        self.sent.append(message)
        if self.refusal is not None:
            on_refused(self.refusal)
        else:
            on_reply(self.reply)


def test_the_box_has_two_verdicts():
    from collins import remotenotify

    assert remotenotify.command_ok("") and remotenotify.command_ok("  ")
    assert remotenotify.command_ok("tpush 'a b'") and not remotenotify.command_ok("tpush 'a")
    assert not remotenotify.command_runnable("") and not remotenotify.command_runnable("   ")
    assert remotenotify.command_runnable("tpush x") and not remotenotify.command_runnable("tpush 'x")


def test_send_test_sends_the_boxs_text_and_hears_the_status(monkeypatch):
    from collins import apilink, remotenotify

    link = SendLink(reply={"status": 2})
    monkeypatch.setattr(apilink, "current", lambda: link)
    got = []
    remotenotify.run_test("tpush hi", lambda status, error: got.append((status, error)))
    assert link.sent == [{"t": "notify.test", "command": "tpush hi"}]
    assert got == [(2, "")]


def test_send_test_hears_the_error_a_refusal_and_no_link(monkeypatch):
    from collins import apilink, remotenotify

    got = []
    monkeypatch.setattr(apilink, "current", lambda: SendLink(reply={"error": "Timed out after 10 s"}))
    remotenotify.run_test("x", lambda status, error: got.append((status, error)))
    refusal = RequestRefused(protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": "notify.test"})
    monkeypatch.setattr(apilink, "current", lambda: SendLink(refusal=refusal))
    remotenotify.run_test("x", lambda status, error: got.append((status, error)))
    monkeypatch.setattr(apilink, "current", lambda: None)
    remotenotify.run_test("x", lambda status, error: got.append((status, error)))
    assert got == [
        (None, "Timed out after 10 s"),
        (None, "notify.test: not served here"),
        (None, "Not connected to the service"),
    ]


# -- the review of PR 626: the spawn environment, and a wait on the program ------------------------


def test_the_command_spawns_from_the_services_environment_not_the_processs(tmp_path, monkeypatch):
    """M1: the sink and the test use the environment the service's shells
    do (`environment`, the core's `_spawn_env`), the five variables on top."""
    dump = tmp_path / "dump.sh"
    dump.write_text('#!/bin/sh\nenv > "$OUT"\n')
    dump.chmod(0o755)
    out = tmp_path / "env.txt"
    monkeypatch.setenv("ONLY_IN_THE_PROCESS", "1")
    base = {"PATH": "/usr/bin:/bin", "FROM_LOGIN_SHELL": "yes", "OUT": str(out)}
    svc = ServiceNotifications(
        State(),
        get_setting={"notify_command": str(dump)}.get,
        run_async=lambda fn: fn(),
        dispatch=lambda fn: fn(),
        environment=lambda: dict(base),
    )
    assert svc.sink("message", "s1", "T", "p", "B")
    seen = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    assert seen["FROM_LOGIN_SHELL"] == "yes" and "ONLY_IN_THE_PROCESS" not in seen
    assert seen["COLLINS_NOTIFY_KIND"] == "message" and seen["COLLINS_NOTIFY_TITLE"] == "T"
    out.unlink()
    answer = ask_test(svc, str(dump))
    assert answer.ok and answer.fields == {"status": 0}
    seen = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    assert seen["FROM_LOGIN_SHELL"] == "yes" and seen["COLLINS_NOTIFY_KIND"] == "test"
    assert "ONLY_IN_THE_PROCESS" not in seen


def _script(tmp_path, body):
    path = tmp_path / "s.sh"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return str(path)


def test_a_background_job_is_not_a_timeout(tmp_path):
    """M2: the runner waits on the program, not on its stderr pipe."""
    started = time.monotonic()
    result = noti.run_command(_script(tmp_path, "sleep 3 &\nexit 0"), dict(os.environ), timeout=1)
    assert result == (0, "") and time.monotonic() - started < 1


def test_a_daemonized_child_does_not_hold_the_runner(tmp_path):
    started = time.monotonic()
    status, error = noti.run_command(
        _script(tmp_path, "setsid sleep 3 &\nsleep 30"), dict(os.environ), timeout=0.3
    )
    assert status is None and error == "Timed out after 0.3 s"
    assert time.monotonic() - started < 1.5


def _vm_rss_kb() -> int:
    """This process's resident set now (not a high-water mark, which an
    earlier test may have raised past anything this one reaches)."""
    with open("/proc/self/status", encoding="ascii") as fh:
        return next(int(line.split()[1]) for line in fh if line.startswith("VmRSS:"))


def test_output_is_not_held_in_memory(tmp_path):
    import threading

    peak = [0]
    stop = threading.Event()
    before = _vm_rss_kb()

    def watch() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], _vm_rss_kb() - before)
            stop.wait(0.005)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        status, error = noti.run_command(
            _script(tmp_path, "head -c 52428800 /dev/zero >&2\nexit 1"), dict(os.environ), timeout=10
        )
    finally:
        stop.set()
        watcher.join()
    # 50 MB of stderr: held (communicate) it would peak past 50 MB; not held, a few.
    assert (status, error) == (1, "") and peak[0] < 20 * 1024, peak
    started = time.monotonic()
    chatty = _script(tmp_path, "while :; do echo fail >&2; done")
    result = noti.run_command(chatty, dict(os.environ), timeout=0.3)
    assert result == (None, "Timed out after 0.3 s") and time.monotonic() - started < 1.5


def test_notify_test_error_is_not_cut_at_a_short_bound(tmp_path):
    """S1: the OS's words run past 32 characters (ELOOP's strerror is 33)."""
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    answer = ask_test(sink_service(""), str(loop))
    assert answer.ok and answer.fields == {"error": os.strerror(40)} and len(os.strerror(40)) > 32
    # And whatever a runner raises is bounded at the table's, not unbounded.
    svc = ServiceNotifications(
        State(),
        run_async=lambda fn: fn(),
        dispatch=lambda fn: fn(),
        run=lambda *_a: (None, "x" * (protocol.ARG_TEXT_MAX + 50)),
    )
    answer = ask_test(svc, "true")
    assert answer.ok and len(answer.fields["error"]) == protocol.ARG_TEXT_MAX
