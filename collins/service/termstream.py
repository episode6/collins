# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The pty stream filter: tokenizer, query responder and mode tracker.

Every byte the service reads from a pty master goes through one
`StreamFilter` (split-service spec §3.3). `StreamFilter.feed(data)` returns
a `Filtered`:

- ``forward``: what clients are sent. Everything in the stream except the
  terminal queries the service answers itself.
- ``replies``: what the service writes back to the pty, one entry per
  answered query, **in the order the queries appeared** (F11: the CLI closes
  a round of questions with DA1 and takes what arrived before that answer
  as the round's answers; answering DA1 ahead of a question asked before it
  made the CLI skip its second round).
- ``tokens``: every token of the stream, queries included, in order, for the
  screen model (`termscreen`, PR-1.2).
- ``events``: `Progress` (OSC 9;4, BEL- or ST-terminated), `Bell`, `Title`
  (OSC 0 and 2), `ModeChanged`, `AltScreen`.

GTK-free and stdlib only, like everything in `collins.service`: no `gi`
import at all.

The tokenizer
-------------
Splits bytes into `Text` runs, single C0 `Control` bytes, `Esc` sequences,
`Csi` sequences, `Str` strings (OSC, DCS, APC, PM, SOS) and `Bad` (bytes that
began a sequence and did not make one). Each token carries its raw bytes.
A sequence cut by a read boundary is carried to the next `feed`, and so is
an incomplete UTF-8 character at the end of a text run (at most three
bytes), so every `Text` decodes on its own; `flush()` gives back whatever is
carried, for the end of a stream. Both BEL and ST (``ESC \\``) end an OSC;
only ST ends DCS, APC, PM and SOS (VTE 0.84 does not answer a DECRQSS ended
by BEL, measured). A string still open at `STRING_MAX` (64 KiB) is flushed
as-is, as a `Str` with ``complete=False``; the tokenizer stays inside it and
passes the rest on in further incomplete pieces until its terminator, as
VTE stays inside it. An incomplete string is never interpreted.

Malformed input follows the DEC/VT500 parser VTE implements (measured where
it matters): CAN or SUB abort any sequence or string; an ESC inside a CSI
aborts it and starts a new sequence; an ESC inside a string not followed by
``\\`` aborts the string and starts a new sequence; a parameter byte after an
intermediate makes the whole CSI, through its final byte, one `Bad`; a C0
control inside an ESC or CSI sequence is executed where it stands, so it is
emitted as its own `Control` token ahead of the sequence it interrupted.
Invalid UTF-8 never raises: text runs stay bytes, decoded by the consumer.

The responder
-------------
Answers F8's table (VTE 0.84.0, measured), plus what CLI 2.1.285 asks (F12),
from live state: a `TerminalState` (grid, cell size, colours, scheme, VTE
version, the SGR answer) and the mode tracker. With a `ScreenHook` attached
the cursor (and the SGR answer, when the hook has ``sgr()``) is read from
the screen as it stands at the query, every earlier token having been
applied to it; without one, `TerminalState.cursor`.

==========================  ===========================================
Query                       Answer
==========================  ===========================================
``CSI c``, ``CSI 0 c``      ``CSI ?61;1;21;22;28c`` (DA1)
``CSI > c``, ``CSI >0c``    ``CSI >61;<vte>;1c`` (DA2)
``CSI = c``, ``CSI =0c``    ``DCS !|7E565445 ST`` (DA3)
``CSI > q``, ``CSI >0q``    ``DCS >|VTE(<vte>) ST`` (XTVERSION)
``CSI 5 n``                 ``CSI 0 n``
``CSI 6 n``                 ``CSI row;col R``
``CSI ? 6 n``               ``CSI ?row;col;1R``
``CSI ? 996 n``             ``CSI ?997;1n`` dark, ``;2n`` light
``CSI ? n $ p``             ``CSI ?n;s$y`` from the tracker and VTE's table
``CSI n $ p``               ``CSI n;s$y`` (ANSI modes), the same way
``CSI 18 t``                ``CSI 8;rows;cols t``
``CSI 14 t``                ``CSI 4;height;width t`` (pixels)
``CSI 21 t``                ``OSC l ST`` (VTE never reports the title)
``OSC 10/11/12 ; ?``        ``OSC n;rgb:rrrr/gggg/bbbb`` + the query's
                            terminator
``OSC 4 ; i ; ? …``         one ``OSC 4;i;rgb:…`` per pair, same rule
``DCS +q544e ST``           ``DCS 1+r544e=787465726D2D323536636F6C6F72 ST``
``DCS $qm ST``              ``DCS 1$r<sgr>m ST``
``CSI ? u``, ``CSI 16 t``,  nothing (and stripped)
``CSI ? 4 m``, ENQ,
APC ``G…a=q…``
==========================  ===========================================

Every query in the table is stripped from ``forward``. A query not in the
table, or not in exactly the table's shape (``CSI 23 t``, ``DCS $q r ST``,
``CSI 5;1 n``, an OSC 4 that also sets a colour), passes through untouched:
the active client's VTE answers it, late, or nobody does with no client
attached (the user's decision, §3.3 and §2.5 item 5).

The mode tracker
----------------
`ModeTracker` records every DEC private and ANSI mode set and reset, the
kitty keyboard flag stack, modifyOtherKeys, the scroll region, the character
sets and shift, cursor visibility and the alternate screen, and answers
DECRQM from them with VTE's own table of the modes it knows (which ones are
permanent, and the defaults: a full sweep of 0 to 65535 on VTE 0.84.0).
`preamble()` re-asserts what differs from a fresh VTE, in this order: the
alternate screen, the modes, the scroll region, the kitty flags,
modifyOtherKeys, the character sets, cursor visibility. RIS (``ESC c``)
resets everything; DECSTR (``CSI ! p``) does what VTE does (measured): every
mode VTE can set goes back to its default, the alternate-screen ones too
(DECRQM then says reset) though the screen shown does not change, and the
scroll region and the character sets are cleared.

Throughput
----------
Measured 2026-10-02 on Python 3.14.4 (the dev box): generated streams of
the shapes below, 8 MiB fed in 64 KiB reads, best of three:

==============================================  ==========  ============
Stream                                          Tokenizer   StreamFilter
==============================================  ==========  ============
Shaped like the CLI's paint: a sync pair per        17 MB/s      14 MB/s
frame, CUP + EL per row, truecolour SGR runs
and faint every few words (8.4 bytes a token)
Nothing but short CSIs (5.3 bytes a token)          10 MB/s       8 MB/s
Plain text, long lines                             350 MB/s     350 MB/s
==============================================  ==========  ============

Against the CLI's busiest second, 5.1 KB (F5), that is three thousand times
over; §3.18's "8 MB/s tokenizer" holds for the filter on the worst shape.

Choices the spec left to this module
------------------------------------
- **Progress and Bell are forwarded** (§3.1's one stream); the events are
  in addition, for the service's own detection (§3.6).
- **Known-silent queries are stripped** (``CSI ? u``, ``CSI 16 t``,
  ``CSI ? 4 m``, ENQ, the APC graphics query), since VTE answers nothing.
- **8-bit C1 is text**: a raw 0x80 to 0x9F byte, and a C1 control encoded
  in UTF-8 too (VTE executes the latter; the CLI never sends one).
- **A colour the program set** (OSC 4 / 10 / 11 / 12) is left to the
  client: its queries pass through until OSC 104 / 110 / 111 / 112 or RIS.
- **OSC 12** answers the cursor colour, else the foreground, as VTE does.
- **The scheme** is `TerminalState.dark`, else whether the background's
  luminance is below one half.
- **CPR** clamps the column to the grid (a pending wrap reports the last
  column) and counts rows from the scroll region's top in origin mode.
- **The preamble's screen switch and scroll region are optional**
  (``screen=False``, for a redraw from `termscreen`, which paints the
  screens and sets the region, shared by both screens in VTE, itself),
  since ``?1049h`` on the alternate screen clears it; ``?3`` and ``?1048``
  are never re-asserted; ``?25`` goes last.
- **Bounds** (rule 5): 256 distinct modes VTE does not know; the kitty
  stack holds 32 and evicts the oldest; a CSI or ESC sequence past 4096
  bytes is `Bad`.
- **Passed through, not answered:** DECRQM with an empty parameter or a
  mode above 65535 (VTE would answer, clamping the mode; the client's VTE
  does), and a mode above 65535 is not tracked.
- **Tokens include the stripped queries**; a screen model ignores them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

STRING_MAX = 64 * 1024
SEQUENCE_MAX = 4096
UNKNOWN_MODES_MAX = 256
KITTY_STACK_MAX = 32
MODE_MAX = 65535

# ------------------------------------------------------------------ tokens


@dataclass(slots=True)
class Text:
    """A run of printable bytes (UTF-8, not decoded)."""

    raw: bytes


@dataclass(slots=True)
class Control:
    """One C0 control byte, or DEL."""

    raw: bytes

    @property
    def code(self) -> int:
        return self.raw[0]


@dataclass(slots=True)
class Esc:
    """``ESC`` intermediates final (not CSI, not a string)."""

    raw: bytes
    intermediates: bytes
    final: int


@dataclass(slots=True)
class Csi:
    """``ESC [`` prefix params intermediates final. ``prefix`` is the private
    marker run (``?``, ``>``, ``<``, ``=``) the parameters start with."""

    raw: bytes
    prefix: bytes
    params: bytes
    intermediates: bytes
    final: int

    def numbers(self) -> list[int] | None:
        """The parameters as numbers, an empty field as 0; None when they are
        not plain decimal fields (sub-parameters, a marker in the middle)."""
        if not self.params:
            return []
        out = []
        for part in self.params.split(b";"):
            if not part:
                out.append(0)
            elif part.isdigit():
                out.append(int(part))
            else:
                return None
        return out


@dataclass(slots=True)
class Str:
    """A control string. ``kind`` is ``osc``, ``dcs``, ``apc``, ``pm`` or
    ``sos``; ``body`` is what lies between the introducer and the
    terminator. ``complete`` is False for a string aborted, cut at
    `STRING_MAX`, or a later piece of one so cut (whose ``body`` is its raw
    bytes less any terminator)."""

    raw: bytes
    kind: str
    body: bytes
    terminator: bytes
    complete: bool


@dataclass(slots=True)
class Bad:
    """Bytes that began a sequence and did not make one."""

    raw: bytes


Token = Text | Control | Esc | Csi | Str | Bad

# ------------------------------------------------------------------ events


@dataclass(slots=True)
class Progress:
    """OSC 9;4: ``state`` 0 remove, 1 normal, 2 error, 3 indeterminate,
    4 paused; ``percent`` 0 to 100, or None when not given."""

    state: int
    percent: int | None


@dataclass(slots=True)
class Bell:
    pass


@dataclass(slots=True)
class Title:
    text: str


@dataclass(slots=True)
class ModeChanged:
    mode: int
    private: bool
    value: bool


@dataclass(slots=True)
class AltScreen:
    entered: bool


StreamEvent = Progress | Bell | Title | ModeChanged | AltScreen


@dataclass(slots=True)
class Filtered:
    forward: bytes
    replies: list[bytes]
    tokens: list[Token]
    events: list[StreamEvent]


# --------------------------------------------------------------- tokenizer

_FAST = re.compile(
    rb"(?P<text>[^\x00-\x1f\x7f]+)"
    rb"|(?P<c0>[\x00-\x1a\x1c-\x1f\x7f])"
    rb"|\x1b\[(?P<csi_m>[<=>?]*)(?P<csi_p>[\x30-\x3f]*)(?P<csi_i>[\x20-\x2f]*)(?P<csi>[\x40-\x7e])"
    rb"|\x1b\](?P<osc_b>[^\x07\x18\x1a\x1b]*)(?P<osc>\x07|\x1b\\)"
    rb"|\x1b(?P<str_k>[P_^X])(?P<str_b>[^\x18\x1a\x1b]*)(?P<str>\x1b\\)"
    rb"|\x1b(?P<esc_i>[\x20-\x2f]+)(?P<esc>[\x30-\x7e])"
    rb"|\x1b(?P<esc1>[\x30-\x4f\x51-\x57\x59\x5a\x5c\x60-\x7e])"
)
_STR_KINDS = {0x5D: "osc", 0x50: "dcs", 0x5F: "apc", 0x5E: "pm", 0x58: "sos"}
_STR_STOP = {
    "osc": re.compile(rb"[\x07\x18\x1a\x1b]"),
    "other": re.compile(rb"[\x18\x1a\x1b]"),
}
_PRIVATE_MARKERS = b"<=>?"

_GROUND, _ESC, _CSI, _CSI_IGNORE, _STR, _STR_ESC = range(6)


def _split_prefix(params: bytes) -> tuple[bytes, bytes]:
    k = 0
    while k < len(params) and params[k] in _PRIVATE_MARKERS:
        k += 1
    return params[:k], params[k:]


def _utf8_tail(buf: bytes, start: int, end: int) -> int:
    """How many bytes at the end of buf[start:end] are an incomplete UTF-8
    character (0 to 3)."""
    for i in range(1, 4):
        if end - i < start:
            return 0
        b = buf[end - i]
        if 0x80 <= b <= 0xBF:
            continue
        if b < 0xC0:
            return 0
        need = 2 if b < 0xE0 else 3 if b < 0xF0 else 4 if b < 0xF8 else 0
        return i if need > i else 0
    return 0


class Tokenizer:
    """Bytes in, `Token`s out, with what a read boundary cut carried over."""

    def __init__(self) -> None:
        self._state = _GROUND
        self._seq = bytearray()
        self._kind = ""
        self._head = True  # the open string still holds its introducer
        self._carry = b""  # an incomplete UTF-8 character, in ground

    def feed(self, data: bytes) -> list[Token]:
        out: list[Token] = []
        buf = self._carry + data if self._carry else data
        self._carry = b""
        n = len(buf)
        pos = 0
        if self._state != _GROUND:
            pos = self._slow(buf, 0, out)
        match = _FAST.match
        while pos < n:
            m = match(buf, pos)
            if m is None:
                # An ESC the fast path can't take whole: cut, malformed, a
                # control inside, or a string past STRING_MAX.
                self._state = _ESC
                self._seq = bytearray(b"\x1b")
                pos = self._slow(buf, pos + 1, out)
                continue
            kind = m.lastgroup
            end = m.end()
            if kind == "text":
                if end == n:
                    cut = _utf8_tail(buf, pos, end)
                    if cut:
                        self._carry = buf[end - cut : end]
                        end -= cut
                        if end == pos:
                            break
                out.append(Text(buf[pos:end]))
            elif kind == "c0":
                out.append(Control(buf[pos:end]))
            elif kind == "csi":
                raw = buf[pos:end]
                if len(raw) > SEQUENCE_MAX:
                    # The slow path's bound, kept here too: a CSI past it is
                    # `Bad`, never handed to a handler as one sequence.
                    out.append(Bad(raw[:SEQUENCE_MAX]))
                    pos += SEQUENCE_MAX
                    continue
                prefix, params, intermediates = m.group("csi_m", "csi_p", "csi_i")
                out.append(Csi(raw, prefix, params, intermediates, raw[-1]))
            elif kind == "osc" or kind == "str":
                if end - pos - len(m.group(kind)) >= STRING_MAX:
                    self._state = _STR
                    self._kind = _STR_KINDS[buf[pos + 1]]
                    self._head = True
                    self._seq = bytearray(buf[pos : pos + 2])
                    pos = self._slow(buf, pos + 2, out)
                    continue
                raw = buf[pos:end]
                if kind == "osc":
                    out.append(Str(raw, "osc", m.group("osc_b"), m.group("osc"), True))
                else:
                    out.append(Str(raw, _STR_KINDS[raw[1]], m.group("str_b"), b"\x1b\\", True))
            elif kind == "esc":
                raw = buf[pos:end]
                if len(raw) > SEQUENCE_MAX:
                    out.append(Bad(raw[:SEQUENCE_MAX]))
                    pos += SEQUENCE_MAX
                    continue
                out.append(Esc(raw, m.group("esc_i"), raw[-1]))
            else:  # esc1
                raw = buf[pos:end]
                out.append(Esc(raw, b"", raw[-1]))
            pos = end
        return out

    def flush(self) -> list[Token]:
        """Everything carried, as-is: for the end of a stream."""
        out: list[Token] = []
        if self._carry:
            out.append(Text(self._carry))
            self._carry = b""
        state, seq = self._state, bytes(self._seq)
        if state in (_ESC, _CSI, _CSI_IGNORE) and seq:
            out.append(Bad(seq))
        elif state == _STR:
            self._emit_str(out, seq, b"", False)
        elif state == _STR_ESC:
            self._emit_str(out, seq + b"\x1b", b"", False)
        self._state = _GROUND
        self._seq = bytearray()
        return out

    # -- the slow path: one sequence, byte by byte

    def _emit_str(self, out, raw: bytes, terminator: bytes, complete: bool) -> None:
        if not raw and not terminator:
            return
        if self._head:
            body = raw[2 : len(raw) - len(terminator)]
        else:
            body = raw[: len(raw) - len(terminator)]
        out.append(Str(raw, self._kind, body, terminator, complete and self._head))

    def _slow(self, buf: bytes, pos: int, out: list) -> int:
        """Run the sequence state machine from pos until the sequence ends
        (returns the position after it, back in ground) or the buffer does
        (the state is kept for the next feed; returns len(buf))."""
        n = len(buf)
        seq = self._seq
        while pos < n:
            state = self._state
            if state == _STR:
                stop = _STR_STOP["osc" if self._kind == "osc" else "other"].search(buf, pos)
                j = stop.start() if stop else n
                while j - pos + len(seq) >= STRING_MAX and j > pos:
                    take = STRING_MAX - len(seq)
                    seq += buf[pos : pos + take]
                    pos += take
                    self._emit_str(out, bytes(seq), b"", False)
                    self._head = False
                    seq.clear()
                if stop is None:
                    seq += buf[pos:n]
                    if not self._head and seq:
                        # past the bound nothing is interpreted: pass it on
                        self._emit_str(out, bytes(seq), b"", False)
                        seq.clear()
                    return n
                seq += buf[pos:j]
                b = buf[j]
                if b == 0x07:
                    self._emit_str(out, bytes(seq) + b"\x07", b"\x07", True)
                    return self._ground(j + 1)
                if b == 0x1B:
                    self._state = _STR_ESC
                    pos = j + 1
                    continue
                # CAN or SUB: the string is aborted.
                self._emit_str(out, bytes(seq), b"", False)
                out.append(Control(buf[j : j + 1]))
                return self._ground(j + 1)
            b = buf[pos]
            if state == _STR_ESC:
                if b == 0x5C:
                    self._emit_str(out, bytes(seq) + b"\x1b\\", b"\x1b\\", True)
                    return self._ground(pos + 1)
                self._emit_str(out, bytes(seq), b"", False)
                seq.clear()
                seq.append(0x1B)
                self._state = _ESC
                continue  # b is looked at again, as the byte after an ESC
            # ESC, CSI, CSI_IGNORE
            if b == 0x1B:
                out.append(Bad(bytes(seq)))
                seq.clear()
                seq.append(0x1B)
                self._state = _ESC
                pos += 1
                continue
            if b == 0x18 or b == 0x1A:
                out.append(Bad(bytes(seq)))
                out.append(Control(buf[pos : pos + 1]))
                return self._ground(pos + 1)
            if b < 0x20 or b == 0x7F:
                out.append(Control(buf[pos : pos + 1]))
                pos += 1
                continue
            if b >= 0x80:
                out.append(Bad(bytes(seq)))
                return self._ground(pos)
            if len(seq) >= SEQUENCE_MAX:
                out.append(Bad(bytes(seq)))
                seq.clear()
                if state == _ESC:
                    return self._ground(pos)
                self._state = state = _CSI_IGNORE
            if state == _ESC:
                if len(seq) == 1:
                    if b == 0x5B:
                        seq.append(b)
                        self._state = _CSI
                        pos += 1
                        continue
                    kind = _STR_KINDS.get(b)
                    if kind is not None:
                        seq.append(b)
                        self._state = _STR
                        self._kind = kind
                        self._head = True
                        pos += 1
                        continue
                seq.append(b)
                pos += 1
                if b >= 0x30:
                    raw = bytes(seq)
                    out.append(Esc(raw, raw[1:-1], b))
                    return self._ground(pos)
                continue
            seq.append(b)
            pos += 1
            if b >= 0x40:
                raw = bytes(seq)
                if state == _CSI_IGNORE:
                    out.append(Bad(raw))
                else:
                    body = raw[2:-1]
                    # intermediates are the 0x20-0x2f tail; the CSI state
                    # never lets a parameter byte follow one
                    split = len(body)
                    while split > 0 and body[split - 1] < 0x30:
                        split -= 1
                    prefix, params = _split_prefix(body[:split])
                    out.append(Csi(raw, prefix, params, body[split:], b))
                return self._ground(pos)
            if b >= 0x30 and state == _CSI and seq[-2] < 0x30 and len(seq) > 3:
                # a parameter byte after an intermediate: ignore to the final
                self._state = _CSI_IGNORE
        return n

    def _ground(self, pos: int) -> int:
        self._state = _GROUND
        self._seq = bytearray()
        return pos


# ------------------------------------------------------------ VTE's tables

# DECRQM status per DEC private mode, VTE 0.84.0, every mode 0 to 65535
# swept (2026-10-02): 1 and 2 are a settable mode's default (set, reset),
# 3 and 4 permanently set and reset. Absent: 0, not recognised.
VTE_PRIVATE_MODES = {
    1: 2, 2: 3, 3: 2, 4: 4, 5: 2, 6: 2, 7: 1, 8: 3, 9: 2, 10: 4, 11: 4,
    12: 4, 13: 4, 14: 4, 16: 4, 18: 4, 19: 4, 25: 1, 30: 4, 34: 4, 35: 4,
    36: 4, 38: 4, 40: 2, 41: 4, 42: 4, 43: 4, 44: 4, 45: 4, 46: 4, 47: 2,
    53: 4, 57: 4, 58: 4, 59: 3, 60: 4, 61: 3, 64: 3, 66: 2, 67: 4, 68: 4,
    69: 2, 73: 4, 80: 2, 81: 4, 83: 4, 84: 4, 85: 4, 90: 4, 95: 4, 96: 4,
    97: 4, 98: 4, 99: 4, 100: 4, 101: 4, 102: 4, 103: 4, 104: 4, 106: 4,
    108: 4, 109: 4, 110: 4, 111: 4, 112: 3, 113: 4, 114: 4, 115: 4, 116: 4,
    117: 4, 1000: 2, 1001: 2, 1002: 2, 1003: 2, 1004: 2, 1005: 4, 1006: 2,
    1007: 1, 1010: 4, 1011: 4, 1014: 4, 1015: 4, 1016: 4, 1021: 3, 1034: 4,
    1035: 4, 1036: 1, 1037: 4, 1039: 4, 1040: 4, 1041: 4, 1042: 4, 1043: 4,
    1044: 4, 1046: 3, 1047: 2, 1048: 2, 1049: 2, 1050: 4, 1051: 4, 1052: 4,
    1053: 4, 1060: 4, 1061: 4, 1070: 1, 1243: 1, 2001: 4, 2002: 4, 2003: 4,
    2004: 2, 2005: 4, 2006: 4, 2016: 3, 2017: 4, 2026: 4, 2027: 4, 2028: 4,
    2029: 4, 2030: 4, 2031: 2, 2048: 4, 2500: 2, 2501: 2, 7700: 4, 7711: 4,
    7727: 4, 7728: 4, 7730: 4, 7766: 4, 7767: 4, 7783: 4, 7786: 4, 7787: 4,
    7796: 4, 8428: 4, 8452: 4, 8800: 4,
}  # fmt: skip
# The same for ANSI modes (``CSI n $ p``).
VTE_ANSI_MODES = {
    1: 4, 2: 4, 3: 4, 4: 2, 5: 4, 6: 4, 7: 4, 8: 1, 9: 3, 10: 4, 11: 4,
    12: 3, 13: 4, 14: 4, 15: 4, 16: 4, 17: 4, 18: 4, 19: 4, 20: 4, 21: 3,
    22: 4, 30: 4, 31: 4, 32: 4, 33: 4, 34: 4, 35: 3, 36: 4, 37: 4, 38: 4,
    40: 3, 42: 3,
}  # fmt: skip
ALT_SCREEN_MODES = (47, 1047, 1049)
# Tracked, never re-asserted: DECCOLM resizes, 1048 is an action, the
# alternate screen goes first and the cursor last.
_PREAMBLE_SKIP = frozenset((3, 25, 1048) + ALT_SCREEN_MODES)


def _default(table: dict[int, int], mode: int) -> bool:
    return table.get(mode) in (1, 3)


# ------------------------------------------------------------ mode tracker


class ModeTracker:
    """What a program has asked of its terminal that an attach must repeat."""

    def __init__(self) -> None:
        self.private: dict[int, bool] = {}
        self.ansi: dict[int, bool] = {}
        self.kitty_base = 0
        self.kitty_stack: list[int] = []
        self.modify_other_keys: int | None = None
        self.scroll_region: tuple[int, int] | None = None
        self.charsets: list[bytes] = [b"B", b"B", b"B", b"B"]
        self.shift = 0
        self.alt_screen = False
        self.alt_mode = 1049
        self._unknown = 0

    # -- reads

    def private_mode(self, mode: int) -> bool:
        value = self.private.get(mode)
        return _default(VTE_PRIVATE_MODES, mode) if value is None else value

    def ansi_mode(self, mode: int) -> bool:
        value = self.ansi.get(mode)
        return _default(VTE_ANSI_MODES, mode) if value is None else value

    @property
    def cursor_visible(self) -> bool:
        return self.private_mode(25)

    @property
    def origin(self) -> bool:
        return self.private_mode(6)

    @property
    def kitty_flags(self) -> int:
        return self.kitty_stack[-1] if self.kitty_stack else self.kitty_base

    def decrqm(self, mode: int, private: bool) -> int:
        """VTE's DECRQM status: 1 set, 2 reset, 3/4 permanent, 0 unknown."""
        table = VTE_PRIVATE_MODES if private else VTE_ANSI_MODES
        status = table.get(mode, 0)
        if status in (1, 2):
            value = self.private_mode(mode) if private else self.ansi_mode(mode)
            return 1 if value else 2
        return status

    # -- writes

    def set_mode(self, mode: int, private: bool, value: bool, events: list) -> None:
        if mode > MODE_MAX:
            return
        modes = self.private if private else self.ansi
        table = VTE_PRIVATE_MODES if private else VTE_ANSI_MODES
        old = modes.get(mode)
        if old is None:
            if mode not in table:
                if self._unknown >= UNKNOWN_MODES_MAX:
                    return
                self._unknown += 1
            old = _default(table, mode)
        modes[mode] = value
        if old != value:
            events.append(ModeChanged(mode, private, value))
        if private and mode in ALT_SCREEN_MODES and value != self.alt_screen:
            self.alt_screen = value
            if value:
                self.alt_mode = mode
            events.append(AltScreen(value))
        elif private and value and mode in ALT_SCREEN_MODES:
            self.alt_mode = mode

    def kitty_push(self, flags: int) -> None:
        self.kitty_stack.append(flags)
        if len(self.kitty_stack) > KITTY_STACK_MAX:
            del self.kitty_stack[0]

    def kitty_pop(self, count: int) -> None:
        count = max(count, 1)
        if count >= len(self.kitty_stack):
            # kitty: a pop that empties the stack resets the flags
            self.kitty_stack.clear()
            self.kitty_base = 0
            return
        del self.kitty_stack[-count:]

    def kitty_set(self, flags: int, how: int) -> None:
        current = self.kitty_flags
        if how == 2:
            flags = current | flags
        elif how == 3:
            flags = current & ~flags
        elif how != 1:
            return
        if self.kitty_stack:
            self.kitty_stack[-1] = flags
        else:
            self.kitty_base = flags

    def designate(self, slot: int, charset: bytes) -> None:
        self.charsets[slot] = charset

    def reset(self, events: list) -> None:
        """RIS: everything back to a fresh terminal."""
        for mode, value in list(self.private.items()):
            if value != _default(VTE_PRIVATE_MODES, mode):
                events.append(ModeChanged(mode, True, not value))
        for mode, value in list(self.ansi.items()):
            if value != _default(VTE_ANSI_MODES, mode):
                events.append(ModeChanged(mode, False, not value))
        if self.alt_screen:
            events.append(AltScreen(False))
        self.__init__()

    def soft_reset(self, events: list) -> None:
        """DECSTR, as VTE 0.84 does it (measured): every mode it can set back
        to its default, the alternate-screen bits too, while the screen
        shown stays the one it was (a later ``?1049l`` still leaves it)."""
        kinds = ((True, self.private, VTE_PRIVATE_MODES), (False, self.ansi, VTE_ANSI_MODES))
        for private, modes, table in kinds:
            for mode, value in modes.items():
                status = table.get(mode)
                if status in (1, 2) and value != (status == 1):
                    modes[mode] = status == 1
                    events.append(ModeChanged(mode, private, status == 1))
        self.scroll_region = None
        self.charsets = [b"B", b"B", b"B", b"B"]
        self.shift = 0

    # -- the attach

    def preamble(self, screen: bool = True) -> bytes:
        """The sequences that bring a fresh terminal to the tracked state:
        the alternate screen and the scroll region (unless ``screen`` is
        False: a redraw from the screen model paints the screens and sets
        the region itself, `termscreen.Screen.snapshot`), the modes, the
        kitty flags, modifyOtherKeys, the character sets, cursor
        visibility."""
        out = bytearray()
        if screen and self.alt_screen:
            out += b"\x1b[?%dh" % self.alt_mode
        for mode, value in self.private.items():
            if mode not in _PREAMBLE_SKIP and value != _default(VTE_PRIVATE_MODES, mode):
                out += b"\x1b[?%d%c" % (mode, 0x68 if value else 0x6C)
        for mode, value in self.ansi.items():
            if value != _default(VTE_ANSI_MODES, mode):
                out += b"\x1b[%d%c" % (mode, 0x68 if value else 0x6C)
        if screen and self.scroll_region is not None:
            out += b"\x1b[%d;%dr" % self.scroll_region
        if self.kitty_base:
            out += b"\x1b[=%du" % self.kitty_base
        for flags in self.kitty_stack:
            out += b"\x1b[>%du" % flags
        if self.modify_other_keys:
            out += b"\x1b[>4;%dm" % self.modify_other_keys
        for slot, charset in enumerate(self.charsets):
            if charset != b"B":
                out += b"\x1b" + b"()*+"[slot : slot + 1] + charset
        if self.shift:
            out += b"\x0e"
        if not self.cursor_visible:
            out += b"\x1b[?25l"
        return bytes(out)


# ------------------------------------------------------------- live state

RGB = tuple[int, int, int]  # sixteen bits a channel, as VTE answers


def rgb16(hex_colour: str) -> RGB:
    """``#rrggbb`` as VTE holds it (a channel times 257, which is what a
    Gdk.RGBA parsed from it becomes)."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        raise ValueError(hex_colour)
    return tuple(int(value[i : i + 2], 16) * 257 for i in (0, 2, 4))  # type: ignore[return-value]


def _vte_palette() -> tuple[RGB, ...]:
    """VTE 0.84's built-in 256 colours (measured): its own sixteen, then
    xterm's cube and greys."""
    out: list[RGB] = []
    for bright in (False, True):
        for i in range(8):
            on, off = (0xFFFF, 0x3FFF) if bright else (0xC000, 0)
            out.append(tuple(on if i & bit else off for bit in (1, 2, 4)))  # type: ignore[arg-type]
    steps = (0, 95, 135, 175, 215, 255)
    for i in range(216):
        out.append((steps[i // 36] * 257, steps[i // 6 % 6] * 257, steps[i % 6] * 257))
    for i in range(24):
        out.append(((8 + 10 * i) * 257,) * 3)  # type: ignore[arg-type]
    return tuple(out)


VTE_PALETTE = _vte_palette()
VTE_FOREGROUND: RGB = (0xC000, 0xC000, 0xC000)
VTE_BACKGROUND: RGB = (0, 0, 0)


@dataclass(slots=True)
class TerminalState:
    """What the responder answers from, kept current by the caller: the
    pty's grid, and the active client's terminal (its ``term``; VTE's own
    dark defaults when no client has ever attached)."""

    cols: int = 120
    rows: int = 40
    cursor: tuple[int, int] = (0, 0)  # (column, row), 0-based
    cell_width: int = 9  # pixels; VTE's default font at scale 1
    cell_height: int = 18
    foreground: RGB = VTE_FOREGROUND
    background: RGB = VTE_BACKGROUND
    cursor_colour: RGB | None = None  # None: the foreground, as VTE does
    palette: tuple[RGB, ...] = field(default=VTE_PALETTE)
    dark: bool | None = None  # None: read off the background
    vte_version: int = 8400
    sgr: str = "0"  # the DECRQSS SGR answer's body, when no screen says

    def is_dark(self) -> bool:
        if self.dark is not None:
            return self.dark
        r, g, b = (c / 65535 for c in self.background)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


class ScreenHook(Protocol):
    """A screen model the filter keeps current token by token, so a query is
    answered from the screen as it stands at the query. ``sgr()`` (the
    DECRQSS SGR body) is optional."""

    def apply(self, token: Token) -> None: ...

    def cursor(self) -> tuple[int, int]: ...


# ----------------------------------------------------------------- filter

_DA1 = b"\x1b[?61;1;21;22;28c"
_DA3 = b"\x1bP!|7E565445\x1b\\"
_TN = b"=787465726D2D323536636F6C6F72\x1b\\"  # "xterm-256color"
_SILENT = b""
_FG, _BG, _CURSOR = "fg", "bg", "cursor"
_OSC_COLOURS = {10: _FG, 11: _BG, 12: _CURSOR}
_OSC_RESETS = {110: _FG, 111: _BG, 112: _CURSOR}


def _rgb_answer(prefix: bytes, rgb: RGB, terminator: bytes) -> bytes:
    return b"\x1b]%srgb:%04x/%04x/%04x%s" % (prefix, rgb[0], rgb[1], rgb[2], terminator)


class StreamFilter:
    """One per pty. `feed` every read; see the module docstring."""

    def __init__(self, state: TerminalState | None = None, screen: ScreenHook | None = None):
        self.state = state if state is not None else TerminalState()
        self.screen = screen
        self.modes = ModeTracker()
        self.tokenizer = Tokenizer()
        # Colours a program set itself: queries for them pass through.
        self._overridden: set = set()

    def feed(self, data: bytes) -> Filtered:
        return self._process(self.tokenizer.feed(data))

    def flush(self) -> Filtered:
        """What the tokenizer carried, as-is (the pty closed)."""
        return self._process(self.tokenizer.flush())

    def preamble(self, screen: bool = True) -> bytes:
        return self.modes.preamble(screen)

    # -- the loop

    def _process(self, tokens: list[Token]) -> Filtered:
        forward: list[bytes] = []
        replies: list[bytes] = []
        events: list[StreamEvent] = []
        screen = self.screen
        for tok in tokens:
            cls = type(tok)
            if cls is Text:
                answer = None
            elif cls is Csi:
                answer = self._csi(tok, events)  # type: ignore[arg-type]
            elif cls is Str:
                answer = self._str(tok, events)  # type: ignore[arg-type]
            elif cls is Control:
                answer = self._control(tok, events)  # type: ignore[arg-type]
            elif cls is Esc:
                answer = self._esc(tok, events)  # type: ignore[arg-type]
            else:
                answer = None
            if answer is None:
                forward.append(tok.raw)
            elif answer:
                if isinstance(answer, list):
                    replies.extend(answer)
                else:
                    replies.append(answer)
            if screen is not None:
                screen.apply(tok)
        return Filtered(b"".join(forward), replies, tokens, events)

    # Each handler returns None (forward the token), b"" (strip it, answer
    # nothing), or the answer (strip it, send the answer).

    def _control(self, tok: Control, events: list):
        code = tok.raw[0]
        if code == 0x07:
            events.append(Bell())
        elif code == 0x0E:
            self.modes.shift = 1
        elif code == 0x0F:
            self.modes.shift = 0
        elif code == 0x05:
            return _SILENT  # ENQ: VTE's answerback is empty
        return None

    def _esc(self, tok: Esc, events: list):
        inter = tok.intermediates
        if not inter:
            if tok.final == 0x63:  # RIS
                self.modes.reset(events)
                self._overridden.clear()
        elif inter[0] in b"()*+":
            self.modes.designate(b"()*+".index(inter[0]), inter[1:] + bytes((tok.final,)))
        return None

    def _csi(self, tok: Csi, events: list):
        final = tok.final
        prefix = tok.prefix
        if final == 0x6D and not prefix:  # SGR, the commonest by far
            return None
        inter = tok.intermediates
        if final == 0x68 or final == 0x6C:  # SM / RM
            if inter or prefix not in (b"", b"?"):
                return None
            nums = tok.numbers()
            if nums:
                value = final == 0x68
                for mode in nums:
                    self.modes.set_mode(mode, prefix == b"?", value, events)
            return None
        nums = tok.numbers()
        if nums is None:
            return None
        state = self.state
        if final == 0x70:  # p
            if inter == b"$" and len(nums) == 1 and prefix in (b"", b"?"):
                mode = nums[0]
                if not tok.params or mode > MODE_MAX:
                    return None
                status = self.modes.decrqm(mode, prefix == b"?")
                return b"\x1b[%s%d;%d$y" % (prefix, mode, status)
            if inter == b"!" and not prefix and not nums:
                self.modes.soft_reset(events)
            return None
        if inter:
            return None
        if final == 0x63:  # c
            if nums in ([], [0]):
                if not prefix:
                    return _DA1
                if prefix == b">":
                    return b"\x1b[>61;%d;1c" % state.vte_version
                if prefix == b"=":
                    return _DA3
            return None
        if final == 0x71:  # q
            if prefix == b">" and nums in ([], [0]):
                return b"\x1bP>|VTE(%d)\x1b\\" % state.vte_version
            return None
        if final == 0x6E:  # n
            if len(nums) != 1:
                return None
            if not prefix:
                if nums[0] == 5:
                    return b"\x1b[0n"
                if nums[0] == 6:
                    return b"\x1b[%d;%dR" % self._cursor_report()
            elif prefix == b"?":
                if nums[0] == 6:
                    return b"\x1b[?%d;%d;1R" % self._cursor_report()
                if nums[0] == 996:
                    return b"\x1b[?997;%dn" % (1 if state.is_dark() else 2)
            return None
        if final == 0x74:  # t
            if prefix or len(nums) != 1:
                return None
            what = nums[0]
            if what == 18:
                return b"\x1b[8;%d;%dt" % (state.rows, state.cols)
            if what == 14:
                return b"\x1b[4;%d;%dt" % (
                    state.rows * state.cell_height,
                    state.cols * state.cell_width,
                )
            if what == 16:
                return _SILENT
            if what == 21:
                return b"\x1b]l\x1b\\"
            return None
        if final == 0x75:  # u: the kitty keyboard protocol
            modes = self.modes
            if prefix == b"?":
                return _SILENT if not nums else None
            if prefix == b">":
                modes.kitty_push(nums[0] if nums else 0)
            elif prefix == b"<":
                modes.kitty_pop(nums[0] if nums else 1)
            elif prefix == b"=":
                modes.kitty_set(nums[0] if nums else 0, nums[1] if len(nums) > 1 else 1)
            return None
        if final == 0x6D:  # m with a prefix
            if prefix == b">":
                if not nums:
                    self.modes.modify_other_keys = None
                elif nums[0] == 4:
                    self.modes.modify_other_keys = nums[1] if len(nums) > 1 else None
            elif prefix == b"?" and nums == [4]:
                return _SILENT  # XTQMODKEYS: VTE answers nothing
            return None
        if final == 0x72 and not prefix and len(nums) <= 2:  # DECSTBM
            top = (nums[0] if nums else 0) or 1
            bottom = min((nums[1] if len(nums) > 1 else 0) or state.rows, state.rows)
            if top >= bottom:
                return None  # VTE ignores an empty region
            whole = top == 1 and bottom == state.rows
            self.modes.scroll_region = None if whole else (top, bottom)
            return None
        return None

    def _cursor_report(self) -> tuple[int, int]:
        state = self.state
        screen = self.screen
        col, row = screen.cursor() if screen is not None else state.cursor
        col = min(max(col, 0), state.cols - 1) + 1
        row = min(max(row, 0), state.rows - 1) + 1
        if self.modes.origin and self.modes.scroll_region is not None:
            row = max(row - (self.modes.scroll_region[0] - 1), 1)
        return row, col

    def _sgr(self) -> bytes:
        sgr = getattr(self.screen, "sgr", None)
        return (sgr() if sgr is not None else self.state.sgr).encode("ascii", "replace")

    def _str(self, tok: Str, events: list):
        if not tok.complete:
            return None
        kind = tok.kind
        if kind == "osc":
            return self._osc(tok, events)
        if kind == "dcs":
            body = tok.body
            if len(body) == 6 and body[:5] == b"+q544" and body[5] in b"eE":
                return b"\x1bP1+r" + body[2:] + _TN
            if body == b"$qm":
                return b"\x1bP1$r" + self._sgr() + b"m\x1b\\"
            return None
        if kind == "apc":
            body = tok.body
            if body[:1] == b"G":
                keys = body[1:].split(b";", 1)[0].split(b",")
                if b"a=q" in keys:
                    return _SILENT  # the kitty graphics query: VTE says nothing
            return None
        return None

    def _osc(self, tok: Str, events: list):
        body = tok.body
        number, _, rest = body.partition(b";")
        if not number.isdigit() or len(number) > 4:
            return None
        n = int(number)
        if n == 0 or n == 2:
            events.append(Title(rest.decode("utf-8", "replace")))
            return None
        if n == 9:
            if rest == b"4" or rest[:2] == b"4;":
                self._progress(rest, events)
            return None
        state = self.state
        term = tok.terminator
        if n in _OSC_COLOURS:
            which = _OSC_COLOURS[n]
            if rest != b"?":
                if rest[:1] != b"?":
                    self._overridden.add(which)
                return None
            if which in self._overridden:
                return None
            if which == _FG:
                return _rgb_answer(b"10;", state.foreground, term)
            if which == _BG:
                return _rgb_answer(b"11;", state.background, term)
            if state.cursor_colour is not None:
                return _rgb_answer(b"12;", state.cursor_colour, term)
            if _FG in self._overridden:
                return None
            return _rgb_answer(b"12;", state.foreground, term)
        if n == 4:
            fields = rest.split(b";")
            if len(fields) % 2:
                return None
            pairs = list(zip(fields[0::2], fields[1::2], strict=True))
            answers = []
            for index, spec in pairs:
                if not index.isdigit() or len(index) > 3 or int(index) > 255:
                    return None
                if spec != b"?":
                    answers = None
            if answers is None:
                for index, spec in pairs:
                    if spec != b"?":
                        self._overridden.add(int(index))
                return None
            for index, _spec in pairs:
                i = int(index)
                if i in self._overridden:
                    return None
                answers.append(_rgb_answer(b"4;%d;" % i, state.palette[i], term))
            return answers
        if n == 104:
            if not rest:
                self._overridden = {k for k in self._overridden if isinstance(k, str)}
            else:
                for index in rest.split(b";"):
                    if index.isdigit():
                        self._overridden.discard(int(index))
            return None
        if n in _OSC_RESETS:
            self._overridden.discard(_OSC_RESETS[n])
            return None
        return None

    @staticmethod
    def _progress(rest: bytes, events: list) -> None:
        parts = rest.split(b";")
        state_field = parts[1] if len(parts) > 1 else b""
        if state_field and not state_field.isdigit():
            return
        state = int(state_field) if state_field else 0
        if state > 4:
            return
        percent = None
        if len(parts) > 2 and parts[2].isdigit():
            percent = min(int(parts[2]), 100)
        events.append(Progress(state, percent))
