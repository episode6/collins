# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""`collins.service.ptyserver` against real ptys (split-service spec
PR-1.5). GTK-free: the server needs GLib only, and the tests drive the
default main context by hand (`pump`), so the whole suite runs without a
display. The children are `cat` in raw mode, `printf` and `sh -c`."""

from __future__ import annotations

import fcntl
import json
import os
import struct
import subprocess
import sys
import termios
import time
import tty

import pytest
from gi.repository import GLib

from collins.api.protocol import FLAG_REDRAW, FLAG_REDRAW_END, QUEUE_BYTES
from collins.service import ptyserver, termscreen, termstream

CAT = ["cat"]


def pump(condition, timeout: float = 5.0, what: str = "condition"):
    """Iterate the default main context until `condition()` holds."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        if not context.iteration(False):
            time.sleep(0.002)


def settle(seconds: float = 0.05):
    """Run the loop for a short while (for things that must NOT happen)."""
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not context.iteration(False):
            time.sleep(0.002)


class Sink:
    def __init__(self, device: str = ""):
        self.frames: list[tuple[bytes, int]] = []
        self.events: list[dict] = []
        self.drops = 0
        self.device = device

    def send_output(self, data: bytes, flags: int) -> None:
        self.frames.append((data, flags))

    def send_event(self, event: dict) -> None:
        self.events.append(event)

    def drop_queued(self) -> None:
        self.drops += 1

    def live(self) -> bytes:
        return b"".join(data for data, flags in self.frames if not flags & FLAG_REDRAW)

    def redraws(self) -> list[bytes]:
        """Each complete redraw, REDRAW through REDRAW_END, as one bytes."""
        out, current = [], []
        for data, flags in self.frames:
            if flags & FLAG_REDRAW:
                current.append(data)
                if flags & FLAG_REDRAW_END:
                    out.append(b"".join(current))
                    current = []
        return out

    def exited(self):
        return [e for e in self.events if e.get("t") == "pty-exited"]


class Recorder:
    def __init__(self):
        self.rows: dict[int, dict | None] = {}
        self.calls: list[tuple[int, dict | None]] = []

    def __call__(self, pty_id: int, row: dict | None) -> None:
        self.calls.append((pty_id, row))
        self.rows[pty_id] = row


@pytest.fixture
def server(tmp_path):
    recorder = Recorder()
    events: list = []
    srv = ptyserver.PtyServer(
        state_dir=tmp_path / "pty", record=recorder, on_event=lambda i, e: events.append((i, e))
    )
    srv.recorder = recorder  # type: ignore[attr-defined]
    srv.events = events  # type: ignore[attr-defined]
    yield srv
    srv.shutdown()
    pump(lambda: not srv.ptys, timeout=5, what="every pty to end")


def winsize(fd: int) -> tuple[int, int]:
    rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return cols, rows


def raw(server, pty: int) -> int:
    """Put the pty in raw mode from the master side (the line discipline
    is shared), so cat echoes bytes and not the tty's ``^[`` rendering.
    Race-free: cat never touches termios itself."""
    tty.setraw(server.get(pty).master)
    return pty


def spawn_cat(server, cols=120, rows=40, **kw) -> int:
    return raw(server, server.spawn("shell", CAT, os.getcwd(), cols=cols, rows=rows, **kw))


def spawn_sh(server, script: str, **kw) -> int:
    return raw(server, server.spawn("shell", ["sh", "-c", script], os.getcwd(), **kw))


def wait_live(sink: Sink, needle: bytes, timeout=5.0):
    pump(lambda: needle in sink.live(), timeout, f"{needle[:20]!r} on the sink")


# -- spawn and the stream


def test_output_reaches_two_attached_sinks(server):
    pty = spawn_cat(server)
    a, b = Sink(), Sink()
    server.attach(pty, a, 120, 40)
    server.attach(pty, b, 120, 40)
    server.write(pty, b"hello", sink=a)
    wait_live(a, b"hello")
    wait_live(b, b"hello")
    assert a.live() == b.live() == b"hello"
    # The model saw it too.
    assert server.get(pty).screen.rows()[0] == "hello"


def test_input_from_two_sinks_reaches_the_child_in_order(server):
    pty = spawn_cat(server)
    a, b = Sink(), Sink()
    server.attach(pty, a, 120, 40)
    server.attach(pty, b, 120, 40)
    server.write(pty, b"1", sink=a)
    server.write(pty, b"2", sink=b)
    server.write(pty, b"3", sink=a)
    server.write(pty, b"4", sink=b)
    wait_live(a, b"1234")
    assert a.live() == b"1234"


def test_the_filter_answers_queries_and_strips_them(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 100, 30)
    # cat echoes the query back out of the pty; the responder answers it
    # into the pty, and cat echoes the answer: the client never sees the
    # query, and sees the answer only because cat repeats it.
    server.write(pty, b"\x1b[6n", sink=sink)
    wait_live(sink, b"\x1b[1;1R")
    assert b"\x1b[6n" not in sink.live()


def test_the_spawn_environment_carries_the_spoofs(server):
    pty = spawn_sh(
        server,
        'printf "%s|%s|%s|%s|%s" "$TERM" "$COLORTERM" "$VTE_VERSION" "$ConEmuANSI" "$TERM_PROGRAM"; exec cat',
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")},
    )
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    wait_live(sink, b"kitty")
    assert sink.live() == b"xterm-256color|truecolor|8400|ON|kitty"


def test_the_row_and_the_record(server):
    pty = spawn_cat(
        server, cols=80, rows=24, session="abc", box="box1", plan="/tmp/p.json", options={"model": "x"}
    )
    row = server.get(pty).row()
    assert row["kind"] == "shell"
    assert row["cols"], row["rows"] == (80, 24)
    assert row["session"] == "abc" and row["box"] == "box1" and row["plan"] == "/tmp/p.json"
    assert row["options"] == {"model": "x"}
    assert row["pid"] == server.get(pty).pid
    assert server.recorder.rows[pty] == row
    assert server.rows() == {pty: row}


def test_stream_events_reach_the_listener(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.write(pty, b"\x1b]9;4;1;50\x07\x07", sink=sink)
    pump(lambda: any(isinstance(e, termstream.Bell) for _, e in server.events), what="the bell")
    kinds = [type(e).__name__ for _, e in server.events]
    assert "Progress" in kinds and "Bell" in kinds
    # Forwarded too (one stream): the client's VTE rings and shows progress.
    assert b"\x1b]9;4;1;50\x07" in sink.live()


# -- attach, the redraw, the active client


def feed_to_fresh(redraw: bytes, cols: int, rows: int) -> termscreen.Screen:
    screen = termscreen.Screen(cols, rows)
    termstream.StreamFilter(screen=screen).feed(redraw)
    return screen


def test_a_fresh_attach_gets_a_redraw_a_fresh_model_reproduces(server):
    pty = spawn_cat(server, cols=40, rows=5)
    first = Sink()
    server.attach(pty, first, 40, 5)
    lines = b"".join(b"\x1b[3%dmline %d \x1b[1mbold\x1b[0m\r\n" % (i % 8, i) for i in range(12))
    server.write(pty, lines + b"\x1b[33mprompt> ", sink=first)
    wait_live(first, b"prompt> ")
    model = server.get(pty).screen
    assert len(model.scrollback) == 8  # 12 rows through a 5-row screen

    late = Sink()
    reply = server.attach(pty, late, 40, 5)
    assert reply == {"cols": 40, "rows": 5, "active": False, "sized_for": ""}
    redraws = late.redraws()
    assert len(redraws) == 1
    assert late.frames[-1][1] & FLAG_REDRAW_END
    fresh = feed_to_fresh(redraws[0], 40, 5)
    assert fresh.rows() == model.rows()
    assert fresh.cursor() == model.cursor()
    assert fresh.grid.pen == model.grid.pen
    # The scrollback, with its pens: colour survives the redraw.
    assert list(fresh.scrollback) == list(model.scrollback)
    assert any(pen[1] is not None for runs in fresh.scrollback for _, _, pen in runs)
    # Live output after the redraw reaches the late sink too.
    server.write(pty, b"X", sink=first)
    wait_live(late, b"X")


def test_the_first_sink_is_active_and_sizes_the_pty(server):
    pty = spawn_cat(server)  # 120x40
    a = Sink(device="laptop")
    reply = server.attach(pty, a, 80, 24)
    assert reply == {"cols": 80, "rows": 24, "active": True, "sized_for": "laptop"}
    assert winsize(server.get(pty).master) == (80, 24)
    assert server.get(pty).screen.columns() == 80


def test_a_resize_from_a_non_active_sink_waits_until_it_sends_input(server):
    pty = spawn_cat(server)
    a, b = Sink(device="laptop"), Sink(device="desk")
    server.attach(pty, a, 80, 24)
    reply = server.attach(pty, b, 100, 30)
    assert reply == {"cols": 80, "rows": 24, "active": False, "sized_for": "laptop"}
    assert winsize(server.get(pty).master) == (80, 24)
    server.resize(pty, 90, 28, sink=b)
    settle()
    assert winsize(server.get(pty).master) == (80, 24)
    # b types: it is the active client now, and its last size applies.
    server.write(pty, b"z", sink=b)
    assert winsize(server.get(pty).master) == (90, 28)
    assert server.get(pty).screen.columns() == 90
    assert server.get(pty).sized_for == "desk"
    # a is no longer active: its resize waits; its focus takes the size back.
    server.resize(pty, 70, 20, sink=a)
    assert winsize(server.get(pty).master) == (90, 28)
    server.focus(pty, a, True)
    assert winsize(server.get(pty).master) == (70, 20)


def test_a_resize_from_the_active_sink_is_applied_at_once(server):
    pty = spawn_cat(server)
    a = Sink()
    server.attach(pty, a, 80, 24)
    server.resize(pty, 132, 50, sink=a)
    assert winsize(server.get(pty).master) == (132, 50)
    assert server.get(pty).state.cols == 132
    assert server.recorder.rows[pty]["cols"] == 132


def test_a_resize_with_no_sink_is_the_services_own(server):
    pty = spawn_cat(server)
    server.resize(pty, 60, 20)
    assert winsize(server.get(pty).master) == (60, 20)


def test_detaching_the_active_sink_frees_the_size(server):
    pty = spawn_cat(server)
    a, b = Sink(), Sink()
    server.attach(pty, a, 80, 24)
    server.attach(pty, b, 100, 30)
    server.detach(pty, a)
    assert server.get(pty).active is None
    # The next to type takes it, and its remembered size applies.
    server.write(pty, b"q", sink=b)
    assert winsize(server.get(pty).master) == (100, 30)


def test_a_sink_that_stops_consuming_is_cut_off_and_redrawn(server):
    size = QUEUE_BYTES + 512 * 1024
    pty = spawn_sh(server, f"head -c {size} /dev/zero | tr '\\0' x; exec cat")
    sink = Sink()  # never reports draining
    server.attach(pty, sink, 120, 40)
    pump(lambda: sink.drops >= 1, timeout=20, what="the overflow redraw")
    pump(lambda: len(sink.redraws()) >= 2, timeout=20, what="the fresh redraw")
    attachment = server.get(pty).attachments[id(sink)]
    assert attachment.queued <= QUEUE_BYTES
    # The redraw that followed the cut shows the screen as it is: 40 rows of x.
    fresh = feed_to_fresh(sink.redraws()[-1], 120, 40)
    assert fresh.rows()[0] == "x" * 120


def test_a_draining_sink_is_never_cut_off(server):
    size = QUEUE_BYTES + 512 * 1024
    pty = spawn_sh(server, f"head -c {size} /dev/zero | tr '\\0' y; exec cat")

    class Draining(Sink):
        def send_output(self, data, flags):
            super().send_output(data, flags)
            server.drained(pty, self, len(data))

    sink = Draining()
    server.attach(pty, sink, 120, 40)
    pump(lambda: len(sink.live()) >= size, timeout=20, what="all the output")
    assert sink.drops == 0
    assert len(sink.redraws()) == 1


def test_paint_lands_in_the_model_and_on_every_sink(server):
    pty = spawn_cat(server)
    a, b = Sink(), Sink()
    server.attach(pty, a, 120, 40)
    server.attach(pty, b, 120, 40)
    server.paint(pty, "[session manager] hello\r\n")
    assert a.live() == b.live() == b"[session manager] hello\r\n"
    assert server.get(pty).screen.rows()[0] == "[session manager] hello"


# -- the write queue


def test_a_100kb_write_against_an_echoing_child_does_not_block_the_loop(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    payload = bytes((i % 26) + 97 for i in range(100 * 1024))
    ticks = []
    GLib.timeout_add(20, lambda: ticks.append(1) or True)
    started = time.monotonic()
    server.write(pty, payload, sink=sink)
    pump(lambda: len(sink.live()) >= len(payload), timeout=10, what="the echo")
    assert sink.live() == payload
    # The loop kept turning while the write drained.
    assert time.monotonic() - started < 10
    assert len(ticks) >= 1 or time.monotonic() - started < 0.05


def test_the_input_queue_is_bounded_and_drops_beyond_it(server):
    pty = spawn_sh(server, "sleep 30")  # reads nothing
    chunk = b"k" * (256 * 1024)
    for _ in range(8):
        server.write(pty, chunk)
    p = server.get(pty)
    assert p.dropped_input > 0
    assert p._queued <= ptyserver.INPUT_QUEUE_BYTES
    settle(0.02)  # and the loop was never blocked on the write


# -- exit


def test_the_child_exiting_produces_pty_exited_with_its_status(server):
    pty = spawn_sh(server, "printf bye; exit 3")
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    pump(lambda: sink.exited(), what="pty-exited")
    assert sink.exited() == [{"t": "pty-exited", "pty": pty, "status": 3}]
    assert sink.live() == b"bye"  # what it wrote last was drained first
    assert pty not in server.ptys
    assert server.recorder.rows[pty] is None


def test_close_ends_the_child_by_sighup(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.write(pty, b"ping", sink=sink)
    wait_live(sink, b"ping")  # the child is up and running
    server.close(pty)
    pump(lambda: sink.exited(), what="pty-exited after close")
    assert sink.exited()[0]["status"] == -1  # SIGHUP
    assert pty not in server.ptys


def test_close_right_after_spawn_still_ends_the_child(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.close(pty)  # before the child has exec'd, most likely
    pump(lambda: sink.exited(), what="pty-exited after an early close")
    assert pty not in server.ptys


def test_signal_reaches_the_child(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.signal(pty, 9)
    pump(lambda: sink.exited(), what="pty-exited after SIGKILL")
    assert sink.exited()[0]["status"] == -9


def test_no_fd_leaks_after_200_spawns(server):
    def fds():
        return len(os.listdir("/proc/self/fd"))

    spawn_cat(server)  # warm the loop's own sources up
    pump(lambda: True)
    baseline = fds()
    done = []
    for batch in range(10):
        ids = [server.spawn("shell", ["true"], os.getcwd()) for _ in range(20)]
        pump(lambda ids=ids: all(i not in server.ptys for i in ids), timeout=10, what=f"batch {batch}")
        done.extend(ids)
    assert len(done) == 200
    assert fds() == baseline


# -- the saved model


def test_the_model_is_saved_and_loads_back(server, tmp_path):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 60, 10)
    server.write(pty, b"\x1b[32mgreen\x1b[0m\r\nplain", sink=sink)
    wait_live(sink, b"plain")
    path = server.save_model(pty)
    assert path == tmp_path / "pty" / f"{pty}.model"
    loaded = server.load_model(path)
    assert loaded is not None
    assert loaded.dump() == server.get(pty).screen.dump()
    assert loaded.rows()[:2] == ["green", "plain"]


def test_the_model_is_saved_on_exit_and_a_damaged_file_loads_as_none(server, tmp_path):
    pty = spawn_sh(server, "printf farewell")
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    pump(lambda: sink.exited(), what="exit")
    path = tmp_path / "pty" / f"{pty}.model"
    assert path.exists()
    assert server.load_model(path).rows()[0] == "farewell"
    path.write_text("{not json", encoding="utf-8")
    assert server.load_model(path) is None
    path.write_text(json.dumps({"format": 99}), encoding="utf-8")
    assert server.load_model(path) is None
    assert server.load_model(tmp_path / "missing.model") is None


def test_the_model_is_written_on_a_timer_while_output_arrives(server, tmp_path, monkeypatch):
    monkeypatch.setattr(ptyserver, "SAVE_INTERVAL_MS", 30)
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.write(pty, b"tick", sink=sink)
    path = tmp_path / "pty" / f"{pty}.model"
    pump(path.exists, timeout=5, what="the timed save")


def test_shutdown_saves_and_closes_everything(tmp_path):
    srv = ptyserver.PtyServer(state_dir=tmp_path / "pty")
    ids = [spawn_cat(srv) for _ in range(3)]
    sinks = [Sink() for _ in ids]
    for i, s in zip(ids, sinks, strict=True):
        srv.attach(i, s, 120, 40)
        srv.write(i, b"w", sink=s)
    for s in sinks:
        wait_live(s, b"w")
    srv.shutdown()
    pump(lambda: not srv.ptys, what="every pty to end")
    for i, s in zip(ids, sinks, strict=True):
        assert (tmp_path / "pty" / f"{i}.model").exists()
        assert s.exited()


def test_the_default_state_dir_honours_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COLLINS_PTY_STATE_DIR", str(tmp_path / "x"))
    assert ptyserver.default_state_dir() == tmp_path / "x"
    monkeypatch.delenv("COLLINS_PTY_STATE_DIR")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    assert ptyserver.default_state_dir() == tmp_path / "s" / "collins" / "pty"


# -- the ten-thousand-row redraw, measured


def test_the_redraw_of_ten_thousand_scrollback_rows_is_measured(server, capsys):
    pty = spawn_cat(server, cols=120, rows=40)
    screen = server.get(pty).screen
    tokenizer = termstream.Tokenizer()
    line = (
        b"\x1b[31merror\x1b[0m in \x1b[1;34mmodule.py\x1b[0m:42 "
        b"\x1b[2mthe faint tail of the message here\x1b[0m\r\n"
    )
    screen.feed(tokenizer.feed(line * 10_040))
    assert len(screen.scrollback) == 10_000
    sink = Sink()
    started = time.perf_counter()
    server.attach(pty, sink, 120, 40)
    elapsed_ms = (time.perf_counter() - started) * 1000
    size = sum(len(data) for data, flags in sink.frames if flags & FLAG_REDRAW)
    print(f"redraw of 10 000 coloured rows: {size / 1048576:.2f} MiB in {elapsed_ms:.1f} ms")
    assert 500_000 < size < 4 * 1024 * 1024
    assert elapsed_ms < 2000
    assert sink.frames[-1][1] & FLAG_REDRAW_END
    assert all(len(data) <= 1024 * 1024 for data, _ in sink.frames)


def test_the_server_loads_no_gtk():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; import collins.service.ptyserver; "
        "print(sorted(m for m in sys.modules if m.startswith('gi.repository.') "
        "and m.split('.')[-1] in ('Gtk', 'Adw', 'Gdk', 'Gsk', 'Vte', 'GtkSource')))"
    )
    env = dict(os.environ, PYTHONPATH=root)
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
