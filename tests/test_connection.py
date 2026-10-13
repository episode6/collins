# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The connection manager (collins.connection, spec §3.20) with a fake
transport: start → wait → connect → lost → backoff → reconnect →
resubscribe, the systemd-or-spawn starter, and the backoff ladder."""

import os

from collins import APP_ID, connection


class FakeLink:
    def __init__(self, fail_connects=0):
        self.on_lost = None
        self.connects = 0
        self.fail_connects = fail_connects
        self.proved = 0
        self.closed = 0

    def connect(self):
        self.connects += 1
        if self.fail_connects > 0:
            self.fail_connects -= 1
            raise OSError("no answer")
        return {"protocol": 1}

    def prove_local(self):
        self.proved += 1
        return True

    def close(self):
        self.closed += 1


class World:
    """A service that appears some polls after it is started, a loop whose
    timers the test fires by hand, and a clock the test moves."""

    def __init__(self, live=False, appears_after=2):
        self.live = live
        self.appears_after = appears_after
        self.started = []
        self.timers = []  # (ms, fn)
        self.clock = 0.0
        self.states = []
        self.connected = []
        self.lost = []
        self.link = FakeLink()
        self.manager = connection.ConnectionManager(
            "com.example.Test",
            self.link,
            find=self.find,
            start=self.start,
            schedule=self.schedule,
            run_async=lambda fn: fn(),  # the fake transport is synchronous: no thread to race
            sleep=self.sleep,
            now=lambda: self.clock,
            on_state=self.states.append,
            on_connected=self.connected.append,
            on_lost=self.lost.append,
        )

    def find(self, app_id):
        if not self.live and self.started and self.appears_after <= 0:
            self.live = True
        return "/tmp/x/api.sock", "live" if self.live else "none"

    def start(self, app_id):
        self.started.append(app_id)
        return "spawned"

    def schedule(self, ms, fn):
        self.timers.append((ms, fn))
        return len(self.timers)

    def sleep(self, seconds):
        self.clock += seconds
        self.appears_after -= 1

    def fire(self):
        """Run every timer due now (they re-arm themselves)."""
        due, self.timers = self.timers, []
        for ms, fn in due:
            self.clock += ms / 1000
            self.appears_after -= 1
            fn()
        return [ms for ms, _ in due]


def test_start_local_finds_a_live_service_and_connects():
    world = World(live=True)
    world.manager.start_local()
    assert world.started == []
    assert world.states == ["connecting", "connected"]
    assert world.connected == [True] and world.link.proved == 1


def test_start_local_starts_and_waits_for_the_socket():
    world = World(live=False, appears_after=3)
    world.manager.start_local()
    assert world.started == ["com.example.Test"]
    assert world.manager.started_how == "spawned"
    assert world.states == ["starting", "waiting", "connecting", "connected"]
    assert world.link.connects == 1


def test_start_local_gives_up_after_the_wait():
    from collins.api.client import ConnectionLost

    world = World(live=False, appears_after=10_000)
    try:
        world.manager.start_local(wait_s=1.0)
    except ConnectionLost as exc:
        assert "within 1 s" in str(exc)
    else:
        raise AssertionError("expected ConnectionLost")


def test_a_loss_backs_off_restarts_reconnects_and_resubscribes():
    world = World(live=True)
    world.manager.start_local()
    # The link reports the loss; the service is gone with it.
    world.live = False
    world.appears_after = 1
    world.link.on_lost("the service closed the connection")
    assert world.states[-1] == "lost" and world.lost == ["the service closed the connection"]
    assert [ms for ms, _ in world.timers] == [1000]
    # The first retry: find says none, start, then poll until the socket
    # answers, then connect: the second time around.
    world.fire()
    assert world.started == ["com.example.Test"]
    assert "starting" in world.states and "waiting" in world.states
    for _ in range(5):
        if world.manager.state == "connected":
            break
        world.fire()
    assert world.manager.state == "connected"
    assert world.connected == [True, False]
    assert world.link.connects == 2 and world.link.proved == 2


def test_the_backoff_ladder_then_every_thirty_seconds():
    world = World(live=True)
    world.manager.start_local()
    world.live = False
    world.appears_after = 10_000
    world.link.fail_connects = 10_000
    world.link.on_lost("gone")
    delays = []
    for _ in range(8):
        due = world.fire()
        delays.extend(d for d in due if d >= 1000)
        # Each attempt starts, waits out WAIT_S of polls, then backs off.
        for _ in range(200):
            if any(ms >= 1000 for ms, _ in world.timers):
                break
            world.fire()
    assert delays[:7] == [1000, 2000, 5000, 10000, 30000, 30000, 30000]


def test_a_failed_connect_counts_as_lost_and_retries():
    world = World(live=True)
    world.manager.start_local()
    world.link.fail_connects = 1
    world.link.on_lost("gone")
    world.fire()  # find: live; connect fails
    assert world.manager.state == "lost"
    assert world.manager.attempts == 2
    world.fire()  # the next connect works
    assert world.manager.state == "connected"


def test_stop_ends_the_retries():
    world = World(live=True)
    world.manager.start_local()
    world.manager.stop()
    assert world.link.closed >= 1 and world.link.on_lost is None
    world.live = False
    world.manager._on_link_lost("gone")
    assert world.timers == []


def test_backoff_table():
    world = World(live=True)
    for attempts, expected in ((0, 1), (1, 2), (2, 5), (3, 10), (4, 30), (5, 30), (50, 30)):
        world.manager.attempts = attempts
        assert world.manager.backoff_s() == expected


def test_systemctl_is_only_for_the_default_id(monkeypatch):
    calls = []
    monkeypatch.setattr(connection.shutil, "which", lambda name: "/usr/bin/systemctl")

    class Result:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(connection.subprocess, "run", lambda *a, **k: calls.append(a[0]) or Result())
    assert connection.systemctl_start("com.episode6.Collins.Debug") is False
    assert calls == []
    assert connection.systemctl_start(APP_ID) is True
    assert calls == [["systemctl", "--user", "start", "--no-block", "collins-service.service"]]


class DeadUnitWorld(World):
    """systemd queues the start (`--no-block` reports success) and the unit
    dies: no socket ever appears from a systemd start, and `show` says the
    unit is `failed`. Only the spawn fallback makes a socket."""

    unit_states = ["failed"]  # what each `unit_state` ask answers, the last one repeating

    def __init__(self):
        super().__init__(live=False, appears_after=10_000)
        self.fallbacks = []
        self.asked = []
        self.notices = []
        self.manager._fallback = self.fallback
        self.manager._unit_state = self.unit_state
        self.manager.on_notice = self.notices.append

    def start(self, app_id):
        self.started.append(app_id)
        return "systemd"

    def fallback(self, app_id):
        self.fallbacks.append(app_id)
        self.appears_after = 2  # the spawned service comes up after two polls
        return "spawned"

    def unit_state(self, app_id):
        index = min(len(self.asked), len(self.unit_states) - 1)
        self.asked.append(app_id)
        return self.unit_states[index]


class SlowUnitWorld(DeadUnitWorld):
    """A healthy unit that is slow: the old service is still `deactivating`
    (a graceful stop the start job is queued behind) when the first wait
    runs out, then `activating`, and the socket appears on systemd's own
    start. The spawn fallback must never fire."""

    unit_states = ["deactivating", "activating"]

    def unit_state(self, app_id):
        word = super().unit_state(app_id)
        if word == "activating":
            self.appears_after = 2  # systemd's service comes up after two more polls
        return word


def test_a_slow_unit_is_waited_for_and_never_replaced_by_a_spawn():
    """Verification of 2edacbf: the flag was set on ANY wait timeout after a
    systemd start, so a `service.restart` or `systemctl --user stop` with
    sessions to close (socket unlinked at stop_accepting, the graceful
    stop up to 30 s, the start job queued behind it, the 10 s poll out)
    flipped the install to an unmanaged spawned service for the window's
    life. The manager asks systemd and keeps waiting on a busy unit."""
    world = SlowUnitWorld()
    world.live = True
    world.manager.start_local()
    world.live = False
    world.link.on_lost("gone")
    world.fire()  # backoff → _retry → systemd start → polling
    for _ in range(400):
        if world.manager.state == "connected":
            break
        world.fire()
    assert world.manager.state == "connected"
    assert world.asked == ["com.example.Test", "com.example.Test"]  # one ask per wait that ran out
    assert world.fallbacks == [] and world.notices == []
    assert world.manager.systemd_failed is False and world.manager.started_how == "systemd"
    assert world.started == ["com.example.Test"]  # one systemd start for the whole reconnect


def test_start_local_waits_on_a_slow_unit_too():
    world = SlowUnitWorld()
    world.manager.start_local(wait_s=1.0)
    assert world.manager.state == "connected" and world.fallbacks == []
    assert world.asked == ["com.example.Test"] * 2 and world.manager.systemd_failed is False


def test_the_fallback_tells_the_user_once_and_systemd_earns_its_trust_back():
    """The notice goes once, when the fallback first takes over; a later
    connection to a service systemd started again clears the mark, and a
    round that skips the start reads no stale `started_how`."""
    world = DeadUnitWorld()
    world.manager.start_local(wait_s=1.0)
    assert world.manager.systemd_failed is True and world.manager.started_how == "spawned"
    assert world.notices == [connection.FALLBACK_NOTICE]
    # Lost; the spawned service is still there (found live): no start at
    # all this round, and `started_how` says so rather than repeating "spawned".
    world.link.on_lost("gone")
    world.fire()
    assert world.manager.state == "connected" and world.manager.started_how == ""
    assert world.manager.systemd_failed is True  # nothing systemd started answered
    # Lost again, nothing live: the fallback spawns, no second notice.
    world.live = False
    world.appears_after = 10_000  # nothing comes up until a start says so
    world.link.on_lost("gone")
    world.fire()
    for _ in range(10):
        if world.manager.state == "connected":
            break
        world.fire()
    assert world.fallbacks == ["com.example.Test"] * 2 and world.notices == [connection.FALLBACK_NOTICE]
    # The user repairs the unit and restarts it by hand: the next reconnect
    # finds a live service this client did not start, systemd says the unit
    # is `active`, and the mark clears (asked in the handshake, off the loop).
    world.unit_states = ["active"]
    asked_before = len(world.asked)
    world.link.on_lost("gone")
    world.fire()
    assert world.manager.state == "connected" and world.manager.started_how == ""
    assert len(world.asked) == asked_before + 1 and world.manager.systemd_failed is False
    # Systemd is trusted again: the next start is systemd's.
    world.live = False
    world.link.on_lost("gone")
    world.appears_after = 2
    world.fire()
    for _ in range(10):
        if world.manager.state == "connected":
            break
        world.fire()
    assert world.manager.started_how == "systemd" and len(world.fallbacks) == 2


def test_unit_state_reads_systemctl_show(tmp_path, monkeypatch):
    import stat

    fake = tmp_path / "systemctl"
    fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > " + str(tmp_path / "argv") + "\necho deactivating\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    assert connection.unit_state(APP_ID) == "deactivating"
    argv = (tmp_path / "argv").read_text().split()
    assert argv == ["--user", "show", "-p", "ActiveState", "--value", "collins-service.service"]
    assert connection.unit_state("com.episode6.Collins.Debug") == ""
    fake.write_text("#!/bin/sh\nexit 1\n")
    assert connection.unit_state(APP_ID) == ""


def test_a_systemd_start_with_no_socket_falls_back_to_a_spawn_on_reconnect():
    """Verification of the 604 fix pass, NEW-1: with `--no-block` a unit
    that starts and dies (a stale ExecStart, an exit 1) reports success, so
    the manager looped systemd-start → WAITING → LOST → backoff for ever
    and never spawned the service itself. A wait that runs out after a
    systemd start marks systemd failed; the next attempt spawns."""
    world = DeadUnitWorld()
    world.live = True
    world.manager.start_local()
    world.live = False
    world.link.on_lost("gone")
    # Attempt 1: systemd start, the wait polls out to WAIT_S, LOST again.
    world.fire()  # the backoff timer → _retry → systemd, then _poll
    assert world.started == ["com.example.Test"] and world.manager.started_how == "systemd"
    assert world.manager.systemd_failed is False
    polls = 0
    while world.manager.state == "waiting" and polls < 200:
        world.fire()
        polls += 1
    assert world.manager.state == "lost" and world.manager.systemd_failed is True
    assert world.fallbacks == []
    # Attempt 2: the fallback spawns, the socket appears, connected.
    world.fire()  # the backoff timer → _retry → the fallback
    assert world.fallbacks == ["com.example.Test"] and world.manager.started_how == "spawned"
    assert world.started == ["com.example.Test"]  # systemd not asked again
    for _ in range(10):
        if world.manager.state == "connected":
            break
        world.fire()
    assert world.manager.state == "connected" and world.connected == [True, False]


def test_start_local_spawns_once_when_the_systemd_start_leaves_no_socket():
    """The same at startup: the first wait runs out after a systemd start,
    the fallback spawns and a second wait finds the socket, instead of a
    ConnectionLost every launch while the stale unit stands."""
    world = DeadUnitWorld()
    world.manager.start_local(wait_s=1.0)
    assert world.started == ["com.example.Test"] and world.fallbacks == ["com.example.Test"]
    assert world.manager.systemd_failed is True and world.manager.started_how == "spawned"
    assert world.states[:4] == ["starting", "waiting", "starting", "waiting"]
    assert world.manager.state == "connected"


def test_systemctl_start_does_not_wait_for_the_start_job(tmp_path, monkeypatch):
    """The review of PR 604, should-fix 2: `systemctl start` waits for the
    start job (a `Type=notify` unit's whole startup; after a stop, the
    deactivating unit's stop sequence first), and a reconnect's `_retry`
    runs on the main loop. A fake systemctl that blocks like the real one
    unless told `--no-block` must come back at once."""
    import stat
    import time

    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {tmp_path / 'argv'}\n"
        "for arg in \"$@\"; do [ \"$arg\" = --no-block ] && exit 0; done\n"
        "sleep 20\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")
    started = time.monotonic()
    assert connection.systemctl_start(APP_ID) is True
    assert time.monotonic() - started < 5
    argv = (tmp_path / "argv").read_text().split()
    assert argv == ["--user", "start", "--no-block", "collins-service.service"]


def test_the_starter_falls_back_to_a_spawn(monkeypatch):
    spawned = []
    monkeypatch.setattr(connection, "systemctl_start", lambda app_id: False)
    monkeypatch.setattr(connection, "spawn_service", lambda app_id, env=None: spawned.append(app_id) or 4242)
    assert connection.default_starter(APP_ID) == "spawned"
    assert spawned == [APP_ID]
    monkeypatch.setattr(connection, "systemctl_start", lambda app_id: True)
    assert connection.default_starter(APP_ID) == "systemd"


def test_service_argv_prefers_the_installed_command(monkeypatch):
    monkeypatch.setattr(connection.shutil, "which", lambda name: "/home/u/.local/bin/collins-service")
    assert connection.service_argv("x") == ["/home/u/.local/bin/collins-service", "--app-id", "x"]
    monkeypatch.setattr(connection.shutil, "which", lambda name: None)
    argv = connection.service_argv("x")
    assert argv[1:] == ["-m", "collins.service.main", "--app-id", "x"]


def test_a_loss_between_the_handshake_and_its_landing_wins():
    """The threaded handshake lands after the link reported a loss: the
    landing is from a dead attempt and changes nothing."""
    world = World(live=True)
    handshakes = []
    world.manager._run_async = handshakes.append  # held back, run by hand
    world.manager.start_local()
    world.link.on_lost("gone")
    world.fire()  # find: live -> the handshake is taken, not run
    assert handshakes and world.manager.state == "connecting"
    world.link.on_lost("gone again")  # the loss lands first
    assert world.manager.state == "lost"
    handshakes[-1]()  # now the handshake's own landing: ignored
    assert world.manager.state == "lost" and world.connected == [True]


def test_a_landing_on_a_link_already_dead_goes_back_to_lost():
    world = World(live=True)
    world.manager.start_local()
    world.link.connected = False  # dead under the handshake, no on_lost yet
    world.manager._try_connect()
    assert world.manager.state == "lost"
    assert [ms for ms, _ in world.timers][-1] >= 1000  # a retry is scheduled
    assert world.connected == [True]


def test_the_owner_can_declare_the_link_lost():
    world = World(live=True)
    world.manager.start_local()
    world.manager.lost("resubscribe refused gone")
    assert world.manager.state == "lost" and world.lost == ["resubscribe refused gone"]
