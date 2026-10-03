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
import threading
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


def test_the_child_starts_with_default_signals_and_no_mask(server):
    pty = spawn_sh(server, "grep -E 'SigIgn|SigBlk' /proc/self/status")
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    pump(lambda: sink.exited(), what="exit")
    text = sink.live().decode()
    sigign = [line for line in text.splitlines() if line.startswith("SigIgn")][0].split()[1]
    sigblk = [line for line in text.splitlines() if line.startswith("SigBlk")][0].split()[1]
    assert int(sigign, 16) & (1 << 12) == 0  # SIGPIPE (13) not ignored
    assert int(sigign, 16) & (1 << 24) == 0  # SIGXFSZ (25) not ignored
    assert int(sigblk, 16) == 0


def test_a_bad_argv_or_cwd_is_a_spawn_error_with_no_leak(server):
    def fds():
        return set(os.listdir("/proc/self/fd"))

    spawn_cat(server)
    pump(lambda: True)
    before = fds()
    with pytest.raises(ptyserver.SpawnError) as info:
        server.spawn("shell", ["/nonexistent/binary"], os.getcwd())
    assert info.value.errno == 2 and info.value.filename == "/nonexistent/binary"
    with pytest.raises(ptyserver.SpawnError) as info:
        server.spawn("shell", ["cat"], "/nonexistent/dir")
    assert info.value.errno == 2 and info.value.filename == "/nonexistent/dir"
    assert fds() == before
    assert len(server.ptys) == 1  # neither made it into the table
    assert server.recorder.calls[-1][1] is not None


def test_the_row_and_the_record(server):
    pty = spawn_cat(
        server, cols=80, rows=24, session="abc", box="box1", plan="/tmp/p.json", options={"model": "x"}
    )
    row = server.get(pty).row()
    assert row["kind"] == "shell"
    assert (row["cols"], row["rows"]) == (80, 24)
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


def test_sinks_are_told_of_size_and_active_changes(server):
    pty = spawn_cat(server)
    a, b = Sink(device="laptop"), Sink(device="desk")
    server.attach(pty, a, 80, 24)
    server.attach(pty, b, 100, 30)

    def last(sink):
        return [e for e in sink.events if e["t"] == "pty"][-1]

    assert last(a)["active"] is True and last(a)["sized_for"] == "laptop" and last(a)["cols"] == 80
    assert last(b)["active"] is False and last(b)["sized_for"] == "laptop"
    server.write(pty, b"z", sink=b)  # b takes over and its size applies
    assert last(a) == {"t": "pty", "pty": pty, "kind": "shell", "cols": 100, "rows": 30,
                       "active": False, "sized_for": "desk"}
    assert last(b)["active"] is True
    server.resize(pty, 90, 28, sink=b)
    assert last(a)["cols"] == 90 and last(b)["cols"] == 90
    server.detach(pty, b)
    assert last(a)["active"] is False and last(a)["sized_for"] == ""


def test_a_resize_from_the_active_sink_is_applied_at_once(server):
    pty = spawn_cat(server)
    a = Sink()
    server.attach(pty, a, 80, 24)
    server.resize(pty, 132, 50, sink=a)
    assert winsize(server.get(pty).master) == (132, 50)
    assert server.get(pty).state.cols == 132
    # The table is written once a second for size changes, at once for
    # spawn and exit.
    assert server.recorder.rows[pty]["cols"] == 120
    pump(lambda: server.recorder.rows[pty]["cols"] == 132, timeout=5, what="the debounced record")


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
            if not flags & FLAG_REDRAW:  # live bytes only
                server.drained(pty, self, len(data))

    sink = Draining()
    server.attach(pty, sink, 120, 40)
    pump(lambda: len(sink.live()) >= size, timeout=20, what="all the output")
    assert sink.drops == 0
    assert len(sink.redraws()) == 1


def test_a_redraw_is_never_counted_against_the_bound_and_is_capped(server):
    pty = spawn_cat(server, cols=120, rows=40)
    screen = server.get(pty).screen
    tokenizer = termstream.Tokenizer()
    line = b"".join(
        b"\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;1;2m%s" % (i, i * 3, i * 5, i * 9, b"abcde") for i in range(20)
    ) + b"\x1b[0m\r\n"
    screen.feed(tokenizer.feed(line * 10_040))
    full = screen.snapshot(b"")
    assert len(full) > ptyserver.REDRAW_MAX  # the model's redraw is over the cap

    class Async(Sink):
        """Drains a little later, as a real transport does."""

        def send_output(self, data, flags):
            super().send_output(data, flags)
            if flags & FLAG_REDRAW:
                return  # live bytes only
            n = len(data)
            GLib.timeout_add(20, lambda: (server.drained(pty, self, n), False)[1])

    sink = Async()
    server.attach(pty, sink, 120, 40)
    attachment = server.get(pty).attachments[id(sink)]
    assert attachment.queued == 0  # the redraw is not counted
    redraw = sink.redraws()[0]
    assert len(redraw) <= ptyserver.REDRAW_MAX
    # The newest rows, and the whole screen, are in it.
    fresh = feed_to_fresh(redraw, 120, 40)
    assert fresh.rows() == screen.rows()
    assert list(fresh.scrollback) == list(screen.scrollback)[-len(fresh.scrollback):]
    assert 1000 < len(fresh.scrollback) < len(screen.scrollback)
    for _ in range(10):
        server.write(pty, b"k", sink=sink)
        settle(0.002)
    settle(0.3)
    assert len(sink.redraws()) == 1  # no storm
    assert sink.drops == 0
    assert sink.live() == b"k" * 10


def test_paint_lands_in_the_model_and_on_every_sink(server):
    pty = spawn_cat(server)
    a, b = Sink(), Sink()
    server.attach(pty, a, 120, 40)
    server.attach(pty, b, 120, 40)
    server.paint(pty, "[session manager] hello\r\n")
    assert a.live() == b.live() == b"[session manager] hello\r\n"
    assert server.get(pty).screen.rows()[0] == "[session manager] hello"


# -- the write queue


def test_replies_go_out_in_stream_order_ahead_of_typing_even_when_blocked(server):
    pty = spawn_sh(server, "sleep 0.3; printf '\\033[6n\\033[18t\\033[c'; exec cat")
    sink = Sink()
    server.attach(pty, sink, 100, 30)
    server.write(pty, b"a" * 200_000, sink=sink)  # blocks the master: nobody reads yet
    p = server.get(pty)
    pump(lambda: len(p._replies) + (p._current is not None) >= 3 or len(sink.live()) > 0, timeout=5,
         what="the three replies to queue up")
    # cat starts reading: the answers come out in the order asked, before
    # the rest of the paste, then the paste.
    wait_live(sink, b"\x1b[1;1R\x1b[8;30;100t\x1b[?61;1;21;22;28c", timeout=10)
    assert b"\x1b[1;1R\x1b[8;30;100t\x1b[?61;1;21;22;28c" in sink.live()
    pump(lambda: sink.live().count(b"a") >= 200_000, timeout=10, what="the paste")


def test_replies_are_never_dropped_by_the_input_bound(server):
    pty = spawn_sh(server, "sleep 30")  # reads nothing
    p = server.get(pty)
    server.write(pty, b"k" * (ptyserver.INPUT_QUEUE_BYTES - 100))  # accepted, blocked
    # The kernel took a few KB of it; a megabyte more is over the bound
    # whatever it took: dropped, and the latch set.
    server.write(pty, b"k" * 2**20)
    assert p._dropping and p.dropped_input == 2**20
    server._enqueue_reply(p, b"\x1b[0n")
    assert list(p._replies) == [b"\x1b[0n"]  # queued behind the blocked entry, not dropped


def test_a_single_write_over_the_bound_does_not_latch_an_empty_queue(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.write(pty, b"k" * (ptyserver.INPUT_QUEUE_BYTES + 1), sink=sink)
    p = server.get(pty)
    assert p.dropped_input == ptyserver.INPUT_QUEUE_BYTES + 1 and not p._dropping
    server.write(pty, b"after", sink=sink)
    wait_live(sink, b"after")


def test_a_100kb_write_against_an_echoing_child_does_not_block_the_loop(server):
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    payload = bytes((i % 26) + 97 for i in range(100 * 1024))
    ticks = []
    GLib.timeout_add(5, lambda: ticks.append(1) or True)
    started = time.monotonic()
    server.write(pty, payload, sink=sink)
    # write() itself returned without the whole payload gone: the rest is
    # queued behind the writability watch, and the loop keeps turning.
    assert time.monotonic() - started < 0.5
    pump(lambda: len(sink.live()) >= len(payload), timeout=10, what="the echo")
    assert sink.live() == payload
    assert len(ticks) >= 2


def test_the_input_queue_is_bounded_and_drops_whole_until_it_drains(server):
    pty = spawn_sh(server, "sleep 0.5; exec cat")  # reads nothing for a while
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    p = server.get(pty)
    big = b"\x1b[200~" + b"k" * (ptyserver.INPUT_QUEUE_BYTES - 100) + b"\x1b[201~"
    server.write(pty, big, sink=sink)
    second = b"\x1b[200~" + b"s" * 2**20 + b"\x1b[201~"
    server.write(pty, second, sink=sink)  # over the bound: dropped whole
    assert p._dropping
    assert p.dropped_input == len(second)
    server.write(pty, b"third", sink=sink)  # still draining: dropped too
    assert p.dropped_input == len(second) + 5
    assert p._queued <= ptyserver.INPUT_QUEUE_BYTES
    pump(lambda: not p._dropping, timeout=20, what="the queue to drain")
    server.write(pty, b"fourth", sink=sink)  # accepted again
    wait_live(sink, b"fourth", timeout=20)
    live = sink.live()
    assert live.startswith(big) and b"s" not in live and b"third" not in live


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


def test_close_kills_a_child_that_ignores_sighup(server, monkeypatch):
    monkeypatch.setattr(ptyserver, "CLOSE_GRACE_MS", 300)
    pty = spawn_sh(server, "trap '' HUP; echo up; sleep 30; echo done")
    sink = Sink()
    server.attach(pty, sink, 80, 24)
    wait_live(sink, b"up")
    started = time.monotonic()
    server.close(pty)
    settle(0.1)
    assert pty in server.ptys  # SIGHUP was ignored
    pump(lambda: sink.exited(), timeout=5, what="the kill")
    assert 0.25 < time.monotonic() - started < 3
    assert sink.exited()[0]["status"] == -9


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
    server.wait_for_saves()
    pump(lambda: server.get(pty)._save_thread is None, what="the save to land")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
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
    server.wait_for_saves()
    assert path.exists()
    assert server.load_model(path).rows()[0] == "farewell"
    path.write_text("{not json", encoding="utf-8")
    assert server.load_model(path) is None
    path.write_text(json.dumps({"format": 99}), encoding="utf-8")
    assert server.load_model(path) is None
    assert server.load_model(tmp_path / "missing.model") is None
    big = tmp_path / "big.model"
    with open(big, "wb") as f:
        f.truncate(ptyserver.MODEL_FILE_MAX + 1)
    assert server.load_model(big) is None


def test_a_save_asked_for_while_one_is_in_flight_follows_it(server, tmp_path, monkeypatch):
    pty = spawn_cat(server)
    p = server.get(pty)
    started = threading.Event()
    release = threading.Event()
    real = ptyserver.write_model_file

    def slow(path, data):
        started.set()
        release.wait(5)
        real(path, data)

    monkeypatch.setattr(ptyserver, "write_model_file", slow)
    server.paint(pty, "one")
    server.save_model(pty)
    assert started.wait(2)
    server.paint(pty, "two")
    server.save_model(pty)  # in flight: noted, not started
    assert p._save_again and p._save_thread is not None
    release.set()
    pump(lambda: p._save_thread is None and not p._save_again, timeout=5, what="both saves")
    assert server.load_model(p.model_path()).rows()[0] == "onetwo"


def test_the_model_is_written_on_a_timer_while_output_arrives(server, tmp_path, monkeypatch):
    monkeypatch.setattr(ptyserver, "SAVE_INTERVAL_MS", 30)
    pty = spawn_cat(server)
    sink = Sink()
    server.attach(pty, sink, 120, 40)
    server.write(pty, b"tick", sink=sink)
    path = tmp_path / "pty" / f"{pty}.model"
    pump(path.exists, timeout=5, what="the timed save")


def test_shutdown_writes_the_model_a_save_in_flight_would_have_lost(tmp_path, monkeypatch):
    real = ptyserver.write_model_file

    def slow(path, data):
        time.sleep(0.3)
        real(path, data)

    monkeypatch.setattr(ptyserver, "write_model_file", slow)
    srv = ptyserver.PtyServer(state_dir=tmp_path / "pty")
    pty = spawn_cat(srv)
    sink = Sink()
    srv.attach(pty, sink, 80, 24)
    srv.write(pty, b"FIRST\r\n", sink=sink)
    wait_live(sink, b"FIRST")
    srv.save_model(pty)  # a periodic save in flight, holding FIRST
    settle(0.05)
    srv.write(pty, b"SECOND\r\n", sink=sink)
    wait_live(sink, b"SECOND")
    srv.shutdown()
    # No loop iteration after this: what a process that exits now leaves.
    loaded = ptyserver.PtyServer(state_dir=tmp_path / "pty").load_model(tmp_path / "pty" / f"{pty}.model")
    rows = loaded.rows()
    assert rows[:2] == ["FIRST", "SECOND"]
    assert not any(t.is_alive() for t in srv._in_flight.values())
    pump(lambda: not srv.ptys, what="every pty to end")


def test_a_failed_save_is_retried(server, tmp_path, monkeypatch):
    calls = []
    real = ptyserver.write_model_file

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 1:
            raise OSError(5, "disk on fire")
        real(path, data)

    monkeypatch.setattr(ptyserver, "write_model_file", flaky)
    monkeypatch.setattr(ptyserver, "SAVE_INTERVAL_MS", 30)
    pty = spawn_cat(server)
    p = server.get(pty)
    server.paint(pty, "keep me")
    server.save_model(pty)
    pump(lambda: p._save_thread is None, what="the failed save to land")
    assert p._dirty  # still owed
    pump(lambda: len(calls) >= 2 and p._save_thread is None, timeout=5, what="the retry")
    assert not p._dirty
    assert server.load_model(p.model_path()).rows()[0] == "keep me"


def test_the_next_pty_id_is_persisted_and_never_reused(tmp_path):
    seen = []
    srv = ptyserver.PtyServer(state_dir=tmp_path / "pty", next_id=7, record_next_id=seen.append)
    first = spawn_cat(srv)
    second = spawn_cat(srv)
    assert (first, second) == (7, 8)
    assert seen == [8, 9]
    srv.shutdown()
    pump(lambda: not srv.ptys, what="every pty to end")
    again = ptyserver.PtyServer(state_dir=tmp_path / "pty", next_id=seen[-1])
    assert spawn_cat(again) == 9
    again.shutdown()
    pump(lambda: not again.ptys, what="every pty to end")


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
