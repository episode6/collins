"""termstream: the pty stream filter, query responder and mode tracker
(split-service spec §3.3, PR-1.1)."""

import os
import random
import subprocess
import sys
import time

import pytest

from collins.service import termstream
from collins.service.termstream import (
    STRING_MAX,
    AltScreen,
    Bad,
    Bell,
    Control,
    Csi,
    Esc,
    ModeChanged,
    Progress,
    Str,
    StreamFilter,
    TerminalState,
    Text,
    Title,
    Tokenizer,
    rgb16,
)

ESC = b"\x1b"
CSI = b"\x1b["
OSC = b"\x1b]"
DCS = b"\x1bP"
ST = b"\x1b\\"
BEL = b"\x07"

# F8 (VTE 0.84.0, 120x40, default colours), plus F12's additions. Every row
# answered byte for byte and stripped. b"" is "VTE answers nothing".
F8 = [
    ("DA1", CSI + b"c", CSI + b"?61;1;21;22;28c"),
    ("DA1 0", CSI + b"0c", CSI + b"?61;1;21;22;28c"),
    ("DA2", CSI + b">c", CSI + b">61;8400;1c"),
    ("DA2 0", CSI + b">0c", CSI + b">61;8400;1c"),
    ("DA3", CSI + b"=c", DCS + b"!|7E565445" + ST),
    ("XTVERSION", CSI + b">q", DCS + b">|VTE(8400)" + ST),
    ("XTVERSION 0", CSI + b">0q", DCS + b">|VTE(8400)" + ST),
    ("kitty keyboard", CSI + b"?u", b""),
    ("DSR 5", CSI + b"5n", CSI + b"0n"),
    ("CPR", CSI + b"6n", CSI + b"1;1R"),
    ("DECXCPR", CSI + b"?6n", CSI + b"?1;1;1R"),
    ("OSC 10 ST", OSC + b"10;?" + ST, OSC + b"10;rgb:c000/c000/c000" + ST),
    ("OSC 10 BEL", OSC + b"10;?" + BEL, OSC + b"10;rgb:c000/c000/c000" + BEL),
    ("OSC 11 ST", OSC + b"11;?" + ST, OSC + b"11;rgb:0000/0000/0000" + ST),
    ("OSC 11 BEL", OSC + b"11;?" + BEL, OSC + b"11;rgb:0000/0000/0000" + BEL),
    ("OSC 12 ST", OSC + b"12;?" + ST, OSC + b"12;rgb:c000/c000/c000" + ST),
    ("OSC 12 BEL", OSC + b"12;?" + BEL, OSC + b"12;rgb:c000/c000/c000" + BEL),
    ("OSC 4 ST", OSC + b"4;1;?" + ST, OSC + b"4;1;rgb:c000/0000/0000" + ST),
    ("OSC 4 BEL", OSC + b"4;200;?" + BEL, OSC + b"4;200;rgb:ffff/0000/d7d7" + BEL),
    ("DECRQM 2004", CSI + b"?2004$p", CSI + b"?2004;2$y"),
    ("DECRQM 25", CSI + b"?25$p", CSI + b"?25;1$y"),
    ("DECRQM 2026", CSI + b"?2026$p", CSI + b"?2026;4$y"),
    ("DECRQM 1016", CSI + b"?1016$p", CSI + b"?1016;4$y"),
    ("DECRQM 9999", CSI + b"?9999$p", CSI + b"?9999;0$y"),
    ("DECRQM ANSI 4", CSI + b"4$p", CSI + b"4;2$y"),
    ("XTGETTCAP TN", DCS + b"+q544e" + ST, DCS + b"1+r544e=787465726D2D323536636F6C6F72" + ST),
    ("DECRQSS SGR", DCS + b"$qm" + ST, DCS + b"1$r0m" + ST),
    ("size in cells", CSI + b"18t", CSI + b"8;40;120t"),
    ("size in pixels", CSI + b"14t", CSI + b"4;720;1080t"),
    ("cell size", CSI + b"16t", b""),
    ("title report", CSI + b"21t", OSC + b"l" + ST),
    ("colour scheme", CSI + b"?996n", CSI + b"?997;1n"),
    ("XTQMODKEYS", CSI + b"?4m", b""),
    ("ENQ", b"\x05", b""),
    ("kitty graphics query", b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA" + ST, b""),
]


@pytest.mark.parametrize(("name", "query", "answer"), F8, ids=[row[0] for row in F8])
def test_f8_answered_and_stripped(name, query, answer):
    out = StreamFilter().feed(b"A" + query + b"B")
    assert out.forward == b"AB"
    assert out.replies == ([answer] if answer else [])


def test_answers_follow_live_state():
    state = TerminalState(cols=100, rows=30, cursor=(9, 4), cell_width=10, cell_height=20)
    state.foreground = rgb16("#d0d0d0")
    state.background = rgb16("#fafafa")
    state.vte_version = 8200
    out = StreamFilter(state).feed(
        CSI + b"18t" + CSI + b"14t" + CSI + b"6n" + CSI + b"?6n" + OSC + b"10;?" + BEL
        + OSC + b"11;?" + BEL + OSC + b"12;?" + BEL + CSI + b"?996n" + CSI + b">c" + CSI + b">q"
    )  # fmt: skip
    assert out.replies == [
        CSI + b"8;30;100t",
        CSI + b"4;600;1000t",
        CSI + b"5;10R",
        CSI + b"?5;10;1R",
        OSC + b"10;rgb:d0d0/d0d0/d0d0" + BEL,
        OSC + b"11;rgb:fafa/fafa/fafa" + BEL,
        OSC + b"12;rgb:d0d0/d0d0/d0d0" + BEL,  # no cursor colour: the foreground
        CSI + b"?997;2n",  # a light background
        CSI + b">61;8200;1c",
        DCS + b">|VTE(8200)" + ST,
    ]
    state.cursor_colour = rgb16("#00ff00")
    state.dark = True
    out = StreamFilter(state).feed(OSC + b"12;?" + ST + CSI + b"?996n")
    assert out.replies == [OSC + b"12;rgb:0000/ffff/0000" + ST, CSI + b"?997;1n"]


def test_cursor_report_clamps_and_counts_from_the_region_in_origin_mode():
    state = TerminalState(cursor=(120, 0))  # a pending wrap reads as column = cols
    assert StreamFilter(state).feed(CSI + b"6n").replies == [CSI + b"1;120R"]
    state = TerminalState(cursor=(3, 6))
    f = StreamFilter(state)
    out = f.feed(CSI + b"5;20r" + CSI + b"?6h" + CSI + b"6n" + CSI + b"?6n")
    assert out.replies == [CSI + b"3;4R", CSI + b"?3;4;1R"]  # as VTE answered


class FakeScreen:
    """A screen that moves its cursor on CUP, to show the cursor is read at
    the query, after every token before it."""

    def __init__(self):
        self.pos = (0, 0)
        self.applied = []

    def apply(self, token):
        self.applied.append(token)
        if isinstance(token, Csi) and token.final == ord("H"):
            nums = token.numbers() or [1, 1]
            self.pos = (nums[1] - 1, nums[0] - 1)

    def cursor(self):
        return self.pos

    def sgr(self):
        return "0;1;31"


def test_screen_hook_is_current_at_each_query():
    screen = FakeScreen()
    f = StreamFilter(screen=screen)
    out = f.feed(CSI + b"6n" + CSI + b"5;10H" + CSI + b"6n" + DCS + b"$qm" + ST)
    assert out.replies == [CSI + b"1;1R", CSI + b"5;10R", DCS + b"1$r0;1;31m" + ST]
    assert screen.applied == out.tokens


def test_a_round_closed_by_da1_is_answered_in_the_order_asked():
    # F11: the CLI closes a round with DA1 and takes what arrived before its
    # answer as the round's answers. One read, as the CLI's startup arrives.
    round_ = CSI + b">0q" + OSC + b"11;?" + ST + CSI + b"?2026$p" + CSI + b"?1016$p" + CSI + b"?u"
    round_ += CSI + b"16t" + b"\x1b_Gi=1,a=q;" + ST + CSI + b"c"
    out = StreamFilter().feed(CSI + b"?2004h" + round_ + b"after")
    assert out.forward == CSI + b"?2004h" + b"after"
    assert out.replies == [
        DCS + b">|VTE(8400)" + ST,
        OSC + b"11;rgb:0000/0000/0000" + ST,
        CSI + b"?2026;4$y",
        CSI + b"?1016;4$y",
        CSI + b"?61;1;21;22;28c",
    ]


@pytest.mark.parametrize(
    "query",
    [
        CSI + b"23t",
        DCS + b"$qr" + ST,
        CSI + b"5;1n",
        CSI + b"1c",
        CSI + b"?$p",
        CSI + b"?70000$p",
        DCS + b"+q436f" + ST,
        DCS + b"+q544e;436f" + ST,
        OSC + b"4;1;?;2;#ff0000" + BEL,
        OSC + b"4;300;?" + BEL,
        OSC + b"10;?;?" + BEL,
        OSC + b"17;?" + BEL,
        CSI + b"20t",
        CSI + b"?15n",
        b"\x1b_Gi=1,a=T;AAAA" + ST,
    ],
)
def test_queries_not_in_the_table_pass_through_untouched(query):
    out = StreamFilter().feed(b"A" + query + b"B")
    assert out.forward == b"A" + query + b"B"
    assert out.replies == []


def test_a_colour_the_program_set_is_left_to_the_client():
    f = StreamFilter()
    assert f.feed(OSC + b"4;3;rgb:12/34/56" + BEL).forward == OSC + b"4;3;rgb:12/34/56" + BEL
    out = f.feed(OSC + b"4;3;?" + BEL + OSC + b"4;2;?" + BEL)
    assert out.replies == [OSC + b"4;2;rgb:0000/c000/0000" + BEL]
    assert out.forward == OSC + b"4;3;?" + BEL
    f.feed(OSC + b"104;3" + BEL)
    assert f.feed(OSC + b"4;3;?" + BEL).replies == [OSC + b"4;3;rgb:c000/c000/0000" + BEL]
    f.feed(OSC + b"10;#ff0000" + BEL)
    out = f.feed(OSC + b"10;?" + BEL + OSC + b"12;?" + BEL + OSC + b"11;?" + BEL)
    assert out.forward == OSC + b"10;?" + BEL + OSC + b"12;?" + BEL
    assert out.replies == [OSC + b"11;rgb:0000/0000/0000" + BEL]
    f.feed(ESC + b"c")
    assert f.feed(OSC + b"10;?" + BEL).replies == [OSC + b"10;rgb:c000/c000/c000" + BEL]


def test_the_default_palette_is_vtes():
    palette = termstream.VTE_PALETTE
    assert len(palette) == 256
    assert palette[8] == (0x3FFF, 0x3FFF, 0x3FFF)
    assert palette[15] == (0xFFFF, 0xFFFF, 0xFFFF)
    assert palette[244] == (0x8080, 0x8080, 0x8080)
    assert palette[255] == (0xEEEE, 0xEEEE, 0xEEEE)


# ---------------------------------------------------------------- splitting

CORPUS = [
    b"hello ",
    "❯ ".encode(),  # a 3-byte UTF-8 character and a 2-byte one
    "🎉".encode(),
    CSI + b"38;2;10;20;30m",
    CSI + b"?2004h",
    CSI + b"c",
    OSC + b"11;?" + BEL,
    OSC + b"10;?" + ST,
    OSC + b"0;a title \xe2\x9c\xbb" + BEL,
    OSC + b"9;4;3" + ST,
    OSC + b"9;4;1;42" + BEL,
    DCS + b"$qm" + ST,
    b"\x1b_Gi=31,a=q;" + ST,
    b"\x1b^private" + ST,
    b"\x1bXstart of string" + ST,
    OSC + b"8;;https://example.com" + ST + b"link" + OSC + b"8;;" + ST,
    ESC + b"(B",
    b"\x0f",
    BEL,
    CSI + b">5u",
    CSI + b"<u",
    CSI + b">4;2m",
    CSI + b"6n",
    CSI + b"?2026$p",
    CSI + b"?1049h",
    b"\r\n",
    ESC + b"7",
    CSI + b"?25l",
]
STREAM = b"".join(CORPUS)


def merged(tokens):
    out = []
    for tok in tokens:
        if out and isinstance(tok, Text) and isinstance(out[-1], Text):
            out[-1] = Text(out[-1].raw + tok.raw)
        else:
            out.append(tok)
    return out


def run(chunks):
    f = StreamFilter()
    forward, replies, tokens, events = b"", [], [], []
    for chunk in chunks:
        out = f.feed(chunk)
        forward += out.forward
        replies += out.replies
        tokens += out.tokens
        events += out.events
    return forward, replies, merged(tokens), events, f.preamble()


def test_a_split_at_every_byte_gives_the_unsplit_result():
    whole = run([STREAM])
    assert whole[1] and whole[3]  # the corpus does hold queries and events
    for i in range(len(STREAM) + 1):
        assert run([STREAM[:i], STREAM[i:]]) == whole, i


def test_byte_at_a_time_gives_the_unsplit_result():
    assert run([STREAM[i : i + 1] for i in range(len(STREAM))]) == run([STREAM])


def test_text_tokens_hold_whole_characters():
    tokens = Tokenizer().feed("ab❯".encode()[:-1])
    assert tokens == [Text(b"ab")]


def test_tokens_reassemble_the_stream():
    tokens = Tokenizer().feed(STREAM)
    assert b"".join(t.raw for t in tokens) == STREAM
    kinds = {type(t) for t in tokens}
    assert {Text, Control, Esc, Csi, Str} <= kinds


# ---------------------------------------------------------------- strings


def test_an_unterminated_osc_is_flushed_at_64_kib():
    f = StreamFilter()
    head = OSC + b"0;"
    body = b"a" * (70 * 1024)
    out = f.feed(head + body[: STRING_MAX - 100])
    assert out.forward == b""  # held while it may still end
    out2 = f.feed(body[STRING_MAX - 100 :])
    first = out2.tokens[0]
    assert isinstance(first, Str) and not first.complete and len(first.raw) == STRING_MAX
    assert out2.forward == head + body  # flushed as-is, then passed on
    out3 = f.feed(BEL + b"x")
    assert out3.forward == BEL + b"x"
    assert out3.tokens[-1] == Text(b"x")
    assert not [e for e in out.events + out2.events + out3.events if isinstance(e, Title)]


def test_a_long_string_gives_the_same_bytes_whatever_the_reads():
    data = OSC + b"0;" + b"z" * (STRING_MAX * 2) + BEL + b"tail" + CSI + b"c"
    whole = StreamFilter().feed(data)
    f = StreamFilter()
    pieces = [f.feed(data[i : i + 4093]) for i in range(0, len(data), 4093)]
    assert b"".join(p.forward for p in pieces) == whole.forward == data[: -len(CSI + b"c")]
    assert sum((p.replies for p in pieces), []) == whole.replies == [CSI + b"?61;1;21;22;28c"]


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"\x1b_Gf=100,a=T;iVBORw0KGgo=" + ST, "apc"),
        (b"\x1b^a privacy message" + ST, "pm"),
        (b"\x1bXa start of string\x07with a bell inside" + ST, "sos"),
        (DCS + b"1000p\x07bell is data in a DCS" + ST, "dcs"),
    ],
)
def test_apc_pm_sos_dcs_are_one_token_passed_whole(data, kind):
    out = StreamFilter().feed(data + b"z")
    assert out.forward == data + b"z"
    assert out.tokens[0] == Str(data, kind, data[2:-2], ST, True)
    assert out.replies == []


def test_osc_9_4_is_progress_with_bel_and_with_st():
    data = (
        OSC + b"9;4;1;50" + BEL + OSC + b"9;4;3" + ST + OSC + b"9;4;0;" + BEL + OSC + b"9;4" + ST
        + OSC + b"9;4;2;150" + BEL + OSC + b"9;4;4;7" + ST + OSC + b"9;4;9" + BEL + OSC + b"9;hi" + BEL
    )  # fmt: skip
    out = StreamFilter().feed(data)
    assert out.forward == data  # forwarded: the client's VTE shows it too
    assert out.events == [
        Progress(1, 50),
        Progress(3, None),
        Progress(0, None),
        Progress(0, None),
        Progress(2, 100),
        Progress(4, 7),
    ]


def test_bell_and_title_are_events_and_forwarded():
    data = b"x" + BEL + OSC + b"2;my title" + ST + OSC + b"0;\xe2\x9c\xbb two" + BEL + OSC + b"1;icon" + BEL
    out = StreamFilter().feed(data)
    assert out.forward == data
    assert out.events == [Bell(), Title("my title"), Title("✻ two")]


def test_a_bel_inside_a_string_is_not_a_bell():
    out = StreamFilter().feed(DCS + b"q\x07" + ST)
    assert out.events == []


# ---------------------------------------------------------------- malformed


def test_can_aborts_a_sequence_and_esc_restarts_one():
    out = StreamFilter().feed(CSI + b"\x186n" + CSI + b"1" + CSI + b"6n")
    # CSI CAN: aborted, then "6n" is text; CSI 1 ESC: aborted, then a CPR
    assert out.replies == [CSI + b"1;1R"]
    assert out.forward == CSI + b"\x186n" + CSI + b"1"
    assert out.tokens[:3] == [Bad(CSI), Control(b"\x18"), Text(b"6n")]


def test_an_esc_inside_a_string_aborts_it():
    out = StreamFilter().feed(OSC + b"0;title" + CSI + b"c")
    assert out.tokens[0] == Str(OSC + b"0;title", "osc", b"0;title", b"", False)
    assert out.replies == [CSI + b"?61;1;21;22;28c"]
    assert out.events == []  # an aborted OSC sets no title


def test_a_control_inside_a_csi_is_executed_where_it_stands():
    out = StreamFilter().feed(CSI + b"6\nn")
    assert out.tokens == [Control(b"\n"), Csi(CSI + b"6n", b"", b"6", b"", ord("n"))]
    assert out.forward == b"\n"
    assert out.replies == [CSI + b"1;1R"]


def test_a_parameter_after_an_intermediate_is_bad_to_the_final():
    out = StreamFilter().feed(CSI + b"1$2pX")
    assert out.tokens == [Bad(CSI + b"1$2p"), Text(b"X")]


def test_invalid_utf8_does_not_raise():
    rng = random.Random(4)
    data = bytes(rng.randrange(256) for _ in range(256 * 1024))
    f = StreamFilter()
    total = 0
    pos = 0
    while pos < len(data):
        step = rng.randrange(1, 300)
        out = f.feed(data[pos : pos + step])
        total += len(out.forward)
        pos += step
    total += len(f.flush().forward)
    assert 0 < total <= len(data)
    for tok in StreamFilter().feed(b"\xff\xfe\xc3(\xe2\x82" + CSI + b"c\x9b6n").tokens:
        if isinstance(tok, Text):
            tok.raw.decode("utf-8", "replace")


def test_flush_gives_back_what_was_carried():
    f = StreamFilter()
    assert f.feed(b"ok\xe2\x9d").forward == b"ok"
    assert f.flush().forward == b"\xe2\x9d"
    f.feed(CSI + b"12;")
    assert f.flush().tokens == [Bad(CSI + b"12;")]
    f.feed(OSC + b"0;t" + ESC)
    assert f.flush().forward == OSC + b"0;t" + ESC


def test_a_megabyte_of_text_is_quick():
    data = (b"The quick brown fox jumps over the lazy dog. " * 24000)[: 1024 * 1024]
    start = time.perf_counter()
    out = StreamFilter().feed(data)
    assert time.perf_counter() - start < 2.0
    assert out.forward == data


# ---------------------------------------------------------------- modes

F1_STARTUP = (
    CSI + b"?2004h" + CSI + b"?1004h" + CSI + b"?2031h" + CSI + b"<u" + CSI + b">5u"
    + CSI + b">4;2m" + CSI + b"c" + CSI + b">0q" + CSI + b"?u" + OSC + b"11;?" + ST
    + CSI + b"?1049h" + CSI + b"?1000h" + CSI + b"?1002h" + CSI + b"?1003h" + CSI + b"?1006h"
    + CSI + b"?2026h" + b"paint" + CSI + b"?2026l"
)  # fmt: skip


def test_the_preamble_after_f1s_startup_is_f1s_mode_list():
    f = StreamFilter()
    f.feed(F1_STARTUP)
    assert f.preamble() == (
        CSI + b"?1049h" + CSI + b"?2004h" + CSI + b"?1004h" + CSI + b"?2031h" + CSI + b"?1000h"
        + CSI + b"?1002h" + CSI + b"?1003h" + CSI + b"?1006h" + CSI + b">5u" + CSI + b">4;2m"
    )  # fmt: skip
    # a redraw that switches screens itself leaves the switch out
    assert not f.preamble(screen=False).startswith(CSI + b"?1049h")


def test_the_preamble_order_and_what_it_repeats():
    f = StreamFilter()
    f.feed(
        CSI + b"?25l" + CSI + b"?7l" + CSI + b"?1;2004h" + CSI + b"4h" + CSI + b"3;20r"
        + CSI + b"=1u" + CSI + b">3u" + CSI + b">4;1m" + ESC + b")0" + b"\x0e" + CSI + b"?3h"
        + CSI + b"?1048h"
    )  # fmt: skip
    assert f.preamble() == (
        CSI + b"?7l" + CSI + b"?1h" + CSI + b"?2004h" + CSI + b"4h" + CSI + b"3;20r"
        + CSI + b"=1u" + CSI + b">3u" + CSI + b">4;1m" + ESC + b")0" + b"\x0e" + CSI + b"?25l"
    )  # fmt: skip
    assert StreamFilter().preamble() == b""
    # a redraw from the screen model sets the region itself (shared by both screens)
    assert CSI + b"3;20r" not in f.preamble(screen=False)
    assert CSI + b"?2004h" in f.preamble(screen=False)


def test_the_fast_path_bounds_a_sequence_too():
    """A CSI or ESC sequence past SEQUENCE_MAX is `Bad` whichever path cut
    it (found by PR-1.2's review: the fast path handed a 5003-byte CSI to
    the responder, whose int() of 5000 digits raised)."""
    from collins.service.termstream import SEQUENCE_MAX, Bad

    huge = CSI + b"9" * 5000 + b"H"
    tokens = Tokenizer().feed(huge)
    assert all(isinstance(t, Bad) for t in tokens[:1])
    assert sum(len(t.raw) for t in tokens) == len(huge)
    assert all(len(t.raw) <= SEQUENCE_MAX for t in tokens)
    f = StreamFilter()
    out = f.feed(huge + b"x")
    assert out.replies == []
    assert out.forward.endswith(b"x")
    esc = ESC + b" " * 5000 + b"F"
    assert all(len(t.raw) <= SEQUENCE_MAX for t in Tokenizer().feed(esc))


def test_decrqm_follows_the_tracker():
    f = StreamFilter()
    out = f.feed(
        CSI + b"?2004;1000h" + CSI + b"?2004$p" + CSI + b"?1000$p" + CSI + b"?25l" + CSI + b"?25$p"
        + CSI + b"?2026h" + CSI + b"?2026$p" + CSI + b"4h" + CSI + b"4$p" + CSI + b"?1234h"
        + CSI + b"?1234$p"
    )  # fmt: skip
    assert out.replies == [
        CSI + b"?2004;1$y",
        CSI + b"?1000;1$y",
        CSI + b"?25;2$y",
        CSI + b"?2026;4$y",  # permanently reset, whatever is asked
        CSI + b"4;1$y",
        CSI + b"?1234;0$y",  # unknown to VTE, set or not
    ]


def test_mode_events_fire_on_change_only():
    out = StreamFilter().feed(CSI + b"?2004h" + CSI + b"?2004h" + CSI + b"?25h" + CSI + b"?2004l")
    assert out.events == [ModeChanged(2004, True, True), ModeChanged(2004, True, False)]


def test_alt_screen_events():
    out = StreamFilter().feed(CSI + b"?1049h" + CSI + b"?47h" + CSI + b"?1049l" + CSI + b"?1047h")
    alt = [e for e in out.events if isinstance(e, AltScreen)]
    assert alt == [AltScreen(True), AltScreen(False), AltScreen(True)]


def test_ris_clears_the_tracker():
    f = StreamFilter()
    f.feed(F1_STARTUP + CSI + b"?25l" + ESC + b"(0")
    out = f.feed(ESC + b"c")
    assert f.preamble() == b""
    assert AltScreen(False) in out.events
    assert ModeChanged(2004, True, False) in out.events
    assert f.feed(CSI + b"?2004$p").replies == [CSI + b"?2004;2$y"]


def test_decstr_resets_what_vte_resets():
    f = StreamFilter()
    f.feed(CSI + b"?1049h" + CSI + b"?2004;1h" + CSI + b"?7;25l" + CSI + b"4h" + CSI + b"8l")
    f.feed(CSI + b"5;9r" + ESC + b"(0" + CSI + b">5u" + CSI + b"!p")
    assert f.preamble() == CSI + b"?1049h" + CSI + b">5u"  # the screen and kitty stay
    out = f.feed(CSI + b"?7$p" + CSI + b"?2004$p" + CSI + b"?1049$p" + CSI + b"8$p")
    # VTE resets the alternate screen's bit too, and stays on that screen
    assert out.replies == [CSI + b"?7;1$y", CSI + b"?2004;2$y", CSI + b"?1049;2$y", CSI + b"8;1$y"]
    assert f.feed(CSI + b"?1049l").events[-1] == AltScreen(False)


def test_kitty_flag_stack():
    f = StreamFilter()
    f.feed(CSI + b">1u" + CSI + b">5u" + CSI + b"=2;2u")
    assert f.modes.kitty_stack == [1, 7]
    f.feed(CSI + b"=4;3u" + CSI + b"<u")
    assert f.modes.kitty_stack == [1]
    f.feed(CSI + b"<5u")
    assert f.modes.kitty_stack == [] and f.modes.kitty_flags == 0
    for i in range(40):
        f.feed(CSI + b">%du" % i)
    assert len(f.modes.kitty_stack) == termstream.KITTY_STACK_MAX
    assert f.modes.kitty_stack[-1] == 39


def test_modify_other_keys_and_charsets():
    f = StreamFilter()
    f.feed(CSI + b">4;2m")
    assert f.modes.modify_other_keys == 2
    f.feed(CSI + b">4m")
    assert f.modes.modify_other_keys is None
    f.feed(ESC + b"(0" + b"\x0e")
    assert f.modes.charsets[0] == b"0" and f.modes.shift == 1
    f.feed(b"\x0f" + ESC + b"(B")
    assert f.preamble() == b""


def test_unknown_modes_are_bounded():
    f = StreamFilter()
    f.feed(b"".join(CSI + b"?%dh" % (30000 + i) for i in range(1000)))
    assert len(f.modes.private) == termstream.UNKNOWN_MODES_MAX
    f.feed(CSI + b"?2004h")  # a known mode is always tracked
    assert f.modes.private_mode(2004)


def test_tokens_kinds():
    tokens = Tokenizer().feed(ESC + b"(B" + ESC + b"M" + CSI + b"?1;2$p")
    assert tokens == [
        Esc(ESC + b"(B", b"(", ord("B")),
        Esc(ESC + b"M", b"", ord("M")),
        Csi(CSI + b"?1;2$p", b"?", b"1;2", b"$", ord("p")),
    ]
    assert tokens[2].numbers() == [1, 2]
    assert Csi(CSI + b"38:2::1m", b"", b"38:2::1", b"", ord("m")).numbers() is None


def test_the_trackers_worst_case_fits_the_attach_replys_bound():
    """Every known mode off its default, the unknown-mode allowance full, the
    kitty stack full, modifyOtherKeys on: the assertions list validates
    as an attach reply (protocol.MODES_MAX is sized to it)."""
    from collins.api import protocol

    t = termstream.ModeTracker()
    for mode in termstream.VTE_PRIVATE_MODES:
        t.private[mode] = not termstream._default(termstream.VTE_PRIVATE_MODES, mode)
    for mode in termstream.VTE_ANSI_MODES:
        t.ansi[mode] = not termstream._default(termstream.VTE_ANSI_MODES, mode)
    unknown = 0
    for mode in range(60000, 65535):
        if len(t.private) >= len(termstream.VTE_PRIVATE_MODES) + termstream.UNKNOWN_MODES_MAX:
            break
        t.private[mode] = True
        unknown += 1
    t.kitty_base = 1
    t.kitty_stack = [31] * termstream.KITTY_STACK_MAX
    t.modify_other_keys = 2
    modes = t.assertions()
    assert len(modes) <= protocol.MODES_MAX
    reply = protocol.reply(1, cols=80, rows=24, active=True, sized_for="x", modes=modes)
    checked = protocol.validate_response(reply, "attach")
    assert not isinstance(checked, protocol.Refusal) and checked.ok


def test_the_service_loads_no_gi():
    # conftest blocks only the widget libraries; a stray GLib import in the
    # service would pass the suite, so look in a fresh interpreter.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; import collins.service.termstream; "
        "print(sorted(m for m in sys.modules if m == 'gi' or m.startswith('gi.')))"
    )
    env = dict(os.environ, PYTHONPATH=root)
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "[]"
