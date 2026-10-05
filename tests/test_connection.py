# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""The connection manager (collins.connection, spec §3.20) with a fake
transport: start → wait → connect → lost → backoff → reconnect →
resubscribe, the systemd-or-spawn starter, and the backoff ladder."""

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
    assert calls == [["systemctl", "--user", "start", "collins-service.service"]]


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
