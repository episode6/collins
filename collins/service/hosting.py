# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""A `Session` hosted by the service: its record, host, ports and facts.

Spec §3.19 (PR-1.12a). Every agent pty the service spawns has a
`SessionRecord`: the `session.Session` built for it, the `SessionHost` the
session reports through (this object), the two ports it reaches its pty
and screen model by (`RecordPtyPort`, `RecordScreenPort`), the cut sinks of
the composers that asked for an open-cut, and the **facts** a client reads
synchronously off its tab — pushed as fields of the `session` event to the
pty's attached clients, whole on attach (`snapshot`) and as the fields that
changed after (`send`, `refresh_facts`: D28).

The host's surface sorts as `session.SessionHost`'s docstring said it
would: the client facts it used to read (`composer_open`) arrive as
request fields and are remembered here for the session's held switches;
`mapped` is True (every poll runs for every live pty, §3.6); `alive` is
"the pty has not exited"; the reactions are `session`, `cut`, `composer`,
`focus` and `shells` events; `spawn_shell` is `PtyServer.spawn` followed
by `shell_spawned`; `shown_prs` is the list this record last handed out.
`paint` formats the "[session manager]" line here and inserts it into the
pty's stream (`PtyServer.paint`, rule 2 of §3.1); a line painted before the
shell is spawned waits for the pty.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable
from typing import Any

from .. import attachrecords, prstatus
from ..api import protocol
from ..providers import EnteredPrompt, options_record
from . import ptyserver
from .session import PROMPT_BLOCK_MSGID, Session

log = logging.getLogger(__name__)

_cuts = itertools.count(1)

# How long a burst of output is let settle before the box facts are re-read
# and the redraw verdict taken (§3.19, D28): the same 50 ms the composer's
# cut settles on. A stream that never settles still gets a read every 50 ms.
OUTPUT_SETTLE_MS = 50

# The editor's agent files: as many as the `session` event carries.
TOUCHED_FILES_MAX = 64


def paint_line(text: str) -> str:
    """The "[session manager]" line, as `TerminalTab.feed_message` always
    drew it."""
    return f"\r\n\x1b[1;33m[session manager]\x1b[0m {text}\r\n"


def _rgb(colour: object) -> tuple[int, int, int] | None:
    """``#rrggbb`` as the screen model's (r, g, b), None for anything else."""
    if not isinstance(colour, str) or len(colour) != 7 or not colour.startswith("#"):
        return None
    try:
        return tuple(int(colour[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    except ValueError:
        return None


class RecordPtyPort:
    """`ports.PtyPort` over the record's pty on the pty server (None before
    the spawn and after the exit: every read answers "no child")."""

    def __init__(self, record: SessionRecord) -> None:
        self._record = record

    def _pty(self) -> ptyserver.Pty | None:
        return self._record.pty()

    def write(self, data: bytes) -> None:
        pty = self._pty()
        if pty is not None:
            self._record.core.ptys.write(pty.id, data)

    def resize(self, cols: int, rows: int) -> None:
        pty = self._pty()
        if pty is not None:
            self._record.core.ptys.resize(pty.id, cols, rows)

    def child_pid(self) -> int | None:
        pty = self._pty()
        return pty.child_pid() if pty is not None else None

    def foreground_pgrp(self) -> int | None:
        pty = self._pty()
        return pty.foreground_pgrp() if pty is not None else None


class RecordScreenPort:
    """`ports.ScreenPort` over the record's pty's screen model, the dim
    judgement made with the colours of the pty's active client (its hello's
    or `theme` event's term: foreground, background and the sixteen-colour
    palette), as the client's own read made it."""

    def __init__(self, record: SessionRecord) -> None:
        self._record = record

    def _screen(self):
        pty = self._record.pty()
        return pty.screen if pty is not None else None

    def rows(self) -> list[str]:
        screen = self._screen()
        return screen.rows() if screen is not None else []

    def cursor(self) -> tuple[int, int]:
        screen = self._screen()
        return screen.cursor() if screen is not None else (0, 0)

    def columns(self) -> int:
        screen = self._screen()
        return screen.columns() if screen is not None else 0

    def row_count(self) -> int:
        screen = self._screen()
        return screen.row_count() if screen is not None else 0

    def tail_is_faint(self, row: int, column: int) -> bool:
        pty = self._record.pty()
        if pty is None or not 0 <= row < pty.screen.row_count():
            return False
        term = self._record.core.ptys.pty_term(pty)
        kw: dict[str, Any] = {}
        fg, bg = _rgb(term.get("fg")), _rgb(term.get("bg"))
        if fg is not None:
            kw["foreground"] = fg
        if bg is not None:
            kw["background"] = bg
        palette = term.get("palette")
        if isinstance(palette, list) and len(palette) == 16:
            colours = [_rgb(c) for c in palette]
            if all(c is not None for c in colours):
                kw["palette"] = tuple(colours)
        return pty.screen.tail_is_faint(row, column, **kw)

    def first_column(self) -> tuple[str, ...]:
        screen = self._screen()
        return screen.first_column() if screen is not None else ()

    def capture_contents(self) -> str:
        screen = self._screen()
        return screen.capture_contents() if screen is not None else ""

    def row_text(self, row: int, end_column: int) -> str:
        screen = self._screen()
        if screen is None or not 0 <= row < screen.row_count():
            return ""
        from .termscreen import line_text

        return line_text(screen.cells(row)[:end_column])

    def visible_text(self) -> str:
        screen = self._screen()
        if screen is None:
            return ""
        _, cursor_row = screen.cursor()
        out: list[str] = []
        wrapped = screen.grid.wrapped
        for y in range(cursor_row + 1):
            out.append(screen.row_text(y))
            if y < cursor_row and not wrapped[y]:
                out.append("\n")
        return "".join(out)


class CutSink:
    """`session.CutSink` for one open-cut: the composer that asked is the
    client's, found by the cut's handle; `alive` is "that client is still
    attached and hasn't called the cut off"."""

    def __init__(self, record: SessionRecord, sink, handle: str) -> None:
        self.record = record
        self.sink = sink  # the client's pty sink (the attachment's)
        self.handle = handle
        self.cancelled = False

    def alive(self) -> bool:
        pty = self.record.pty()
        attached = pty is not None and id(self.sink) in pty.attachments
        return attached and not self.cancelled

    def seed(self, text: str) -> None:
        """The box's text for the composer. The record lets the handle go
        here — the client has what it asked for; the chain's verify rounds
        go on under `alive` (the client still attached) and are called
        off by the client's `cut.cancel` through the record's chain."""
        self._tell("seeded", text)
        self.record.cuts.pop(self.handle, None)

    def refuse(self) -> None:
        self._tell("refused")
        self.record.cuts.pop(self.handle, None)

    def ended(self) -> None:
        """The cut ended with nothing to seed (an empty box, a box that
        never settled): the client hears `cancelled` and lets the handle
        go, as this record does."""
        if not self.cancelled:
            self.cancelled = True
            self.record.cuts.pop(self.handle, None)
            self._tell("cancelled")

    def cancel(self, tell: bool = True) -> None:
        """The client called it off, or went away (`client_gone`: nothing
        to tell)."""
        if not self.cancelled:
            self.cancelled = True
            self.record.cuts.pop(self.handle, None)
            if tell:
                self._tell("cancelled")

    def _tell(self, state: str, text: str | None = None) -> None:
        # The record's pty id, not the live pty: a sink is told its cut's
        # fate from the session's main-loop callbacks, and `process_exited`
        # cancels every cut synchronously before the record lets go of its
        # pty, so a cut can't outlive the id it was begun under.
        event: dict = {"t": "cut", "pty": self.record.pty_id, "handle": self.handle, "state": state}
        if text is not None:
            # Never cut back (D55): a seed that would not frame is refused
            # before the box is erased (`Session.cut_fits_now`, and the
            # settle's own check), so what is told here is the box's text
            # whole.
            event["text"] = text[: protocol.TEXT_MAX]
        try:
            self.sink.send_event(event)
        except Exception:
            log.exception("cut %s: the client's sink failed", self.handle)


class SessionRecord:
    """One agent pty's session on the service. See the module docstring."""

    def __init__(self, core, *, provider, **session_kwargs) -> None:
        self.core = core
        self.pty_id: int | None = None
        # A fork's own conversation, once its resolver found it: the
        # session its pty runs, as the pty table tells it (PR-1.12c).
        self.forked: str | None = None
        # The grid the spawn request asked for (the client's VTE's).
        self.grid: tuple[int, int] = (80, 24)
        self.exited = False
        self.spawn_error: ptyserver.SpawnError | None = None
        self.cuts: dict[str, CutSink] = {}
        self._chain: CutSink | None = None  # the cut whose chain the session runs now
        self._composer_open = False  # the last word a request carried
        self._shown_prs: list[prstatus.PullRequest] = []
        self._pending_paints: list[str] = []
        self._sent: dict[str, Any] = {}  # the facts the clients were last sent
        self._landed = False  # a transcript read has landed since the last reset
        self._settle: int | None = None  # the output settle's timer, while one runs
        # A close budget ran out and was told to a client, whose answer
        # (the forced close) is awaited; cleared with that answer or the
        # service's own force (`close_budget`, `client_detached`).
        self.budget_spent = False
        # Whether the CLI has ever been seen owning the terminal
        # (`ServiceCore.cli_changed(True)`), and when the shell was spawned:
        # a pty whose CLI never ran is held for ORPHAN_HOLD_S before it can
        # be closed as a shell alone (`close_if_orphan_shell`).
        self.cli_ever_ran = False
        self.spawned_at = time.monotonic()
        self._orphan_recheck: int | None = None  # the hold's one re-check, while scheduled
        self.on_settled: Callable[[SessionRecord], None] | None = None  # the tracker's hook
        self.session = Session(
            provider=provider,
            pty=RecordPtyPort(self),
            screen=RecordScreenPort(self),
            host=self,
            sandbox_host=lambda: core.sandbox_host,
            sandbox_grants=lambda: core.sandbox_grants,
            # D55: a cut whose text would not frame as a `cut` event is
            # refused before the erase, never cut back.
            seed_fits=lambda text: protocol.json_bytes(text) <= protocol.EVENT_TEXT_BYTES,
            **session_kwargs,
        )

    @property
    def handle(self) -> str:
        return self.session.handle

    def pty(self) -> ptyserver.Pty | None:
        if self.pty_id is None or self.exited:
            return None
        return self.core.ptys.ptys.get(self.pty_id)

    # -- the output settle -----------------------------------------------------------

    def output_arrived(self) -> None:
        """Output came off the pty (after the filter): once the burst has
        settled, the tracker's verdict (`on_settled`) and the box facts."""
        if self._settle is not None:
            return
        from gi.repository import GLib

        self._settle = GLib.timeout_add(OUTPUT_SETTLE_MS, self._on_settled)

    def _on_settled(self) -> bool:
        self._settle = None
        if self.exited:
            return False
        if self.on_settled is not None:
            try:
                self.on_settled(self)
            except Exception:
                log.exception("session %s: the settle's listener failed", self.handle)
        try:
            self.refresh_facts()
        except Exception:  # noqa: BLE001 - a fact that can't be read goes stale, not down (rule 4)
            log.exception("session %s: the facts' refresh failed", self.handle)
        return False

    # -- the session event ---------------------------------------------------------

    def send(self, fields: dict, active_only: bool = False) -> None:
        """A `session` event with *fields* to the pty's attached clients
        (the active one alone when *active_only*)."""
        pty = self.pty()
        if pty is None or not pty.attachments:
            return
        event = {"t": "session", "pty": pty.id, "handle": self.handle, **fields}
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("session %s: an event the protocol refuses: %s", self.handle, checked.msgid)
            return
        targets = list(pty.attachments.values())
        if active_only and pty.active is not None and pty.active in pty.attachments:
            targets = [pty.attachments[pty.active]]
        for attachment in targets:
            try:
                attachment.sink.send_event(dict(event))
            except Exception:
                log.exception("session %s: a client's sink failed", self.handle)

    def send_to(self, sink, fields: dict) -> None:
        """A `session` event to one client (the one attaching), checked
        against the protocol as every event is."""
        if self.pty_id is None:
            return
        event = {"t": "session", "pty": self.pty_id, "handle": self.handle, **fields}
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("session %s: a snapshot the protocol refuses: %s", self.handle, checked.msgid)
            return
        try:
            sink.send_event(event)
        except Exception:
            log.exception("session %s: a client's sink failed", self.handle)

    def _fit(self, fields: dict) -> dict:
        """*fields* as the `session` event may carry them (rule 5: a fact
        read off the transcript, the screen or the process tree is foreign
        content): each fitted to its own field of the protocol — a string
        over its bound cut to it — and one that doesn't fit dropped, with
        a log line, so the others still go."""
        kept = {}
        for name, value in fields.items():
            spec = protocol.SESSION_FIELDS.get(name)
            if spec is None:
                continue
            ok, fitted = protocol.fit_field(name, value, spec)
            if ok:
                kept[name] = fitted
            else:
                log.warning("session %s: the fact %s doesn't fit the protocol: %s", self.handle, name, fitted)
        return kept

    def _send_changed(self, fields: dict, always: tuple[str, ...] = ()) -> None:
        """Send the fields of *fields* that differ from what was last sent
        (*always* names the one-shots, sent whatever was sent before). A
        fact that doesn't fit the protocol is dropped before anything is
        recorded, so it is tried again next time and never costs the
        others their event."""
        changed = {}
        for name, value in self._fit(fields).items():
            if name in always or self._sent.get(name, _UNSENT) != value:
                changed[name] = value
            if name not in always:
                self._sent[name] = value
        if changed:
            self.send(changed)
        if "running_command" in fields:
            # The pty table's word on whether the session runs (PR-1.12c):
            # told on every read, the core keeps only the flips.
            cli_changed = getattr(self.core, "cli_changed", None)
            if cli_changed is not None:
                cli_changed(self, bool(fields["running_command"]))

    def snapshot(self) -> dict:
        """Every fact, for a client attaching. `landed` rides along once a
        transcript read has landed (the tab acts on the transcript facts
        — labels, chips, agent files — only under it), and `busy` and
        `lookup_empty` as the tracker and the last landing left them."""
        session = self.session
        facts = {
            "session": session.session_id,
            "fork": bool(session.fork),
            "provider": str(getattr(session.provider, "id", "") or "")[: protocol.SHORT_MAX],
            "options": options_record(session.options),
            "continue_recent": bool(session.continue_recent),
            "cwd": session.cwd,
            "agent_cwd": self._sent.get("agent_cwd", session.cwd),
            "pid": session.child_pid(),
            "resolver_cwd": session.resolver_cwd,
            **self.launch_facts(),
            **self.sandbox_facts(),
            **self.box_facts(),
            **self.process_facts(),
            **self.transcript_facts(),
            "lookup_empty": bool(self._sent.get("lookup_empty", False)),
            "busy": bool(self._sent.get("busy", False)),
        }
        if self._landed:
            facts["landed"] = True
        return self._fit(facts)

    def busy_changed(self, busy: bool) -> None:
        """The tracker's verdict for this session, by handle (a placeholder
        row's word): sent on the edge and remembered, so an attach in the
        middle of a turn is told `busy` as it stands."""
        self._send_changed({"busy": bool(busy)})

    def launch_facts(self) -> dict:
        session = self.session
        return {
            "cwd": session.cwd,  # moves once: a reaped worktree put back (`_recreated`)
            "options": options_record(session.options),
            "initial_command": session.initial_command,
            "worktree_launch": bool(session.worktree_launch),
            # A preview (PREVIEW_MAX): the whole prompt came in on the
            # spawn, and TEXT_MAX of it would not fit a frame. The
            # paste-back's record (`pasted_back`, `paste_back_pending`) is
            # not a fact at all: no client reads it, the checks probe it.
            "new_chat_prompt": (
                session.new_chat_prompt[: protocol.PREVIEW_MAX]
                if session.new_chat_prompt is not None
                else None
            ),
            "shells_follow_armed": bool(session.shells_follow_armed),
            "closing": bool(session.closing),
        }

    def sandbox_facts(self) -> dict:
        session = self.session
        return {
            "options": options_record(session.options),
            "sandboxed": bool(session.sandboxed),
            "sandbox_box": session.sandbox_box or "",
            "sandbox_plan_path": session.sandbox_plan_path,
            "defaults_owed": bool(session.sandbox_defaults_owed),
            "can_restart": bool(session.can_restart_sandboxed()),
        }

    def box_facts(self) -> dict:
        """The box reads (D28): what a client's `takes_prompt` and friends
        answer from."""
        session = self.session
        takes = session.takes_prompt()
        entered: EnteredPrompt | None = session.entered_prompt()
        facts: dict = {
            "takes_prompt": takes,
            "entered": (
                {
                    "text": entered.text[: protocol.PREVIEW_MAX],
                    "rows_below": max(0, int(entered.rows_below)),
                }
                if entered is not None
                else None
            ),
            "prompt_block": "" if takes else PROMPT_BLOCK_MSGID,
            "unstarted": bool(session.unstarted_thread()),
            "foreign_paste": bool(entered is not None and session.foreign_paste_in_box()),
            "running_command": bool(session.has_running_command()),
        }
        return facts

    def process_facts(self) -> dict:
        return {"agent_running": bool(self.session.agent_is_running())}

    def transcript_facts(self) -> dict:
        session = self.session
        transcript = session.transcript
        touched = [p for p in transcript.touched_files() if isinstance(p, str) and p.startswith("/")]
        facts = {
            "model": transcript.model(),
            "effort": transcript.effort(),
            "permission_mode": transcript.permission_mode(),
            "transcript_path": session.transcript_path,
            "prs": prstatus.to_records(self._shown_prs)[: protocol.PRS_MAX],
            "touched_files": touched[:TOUCHED_FILES_MAX],
            "attachments": attachrecords.to_records(transcript.attachments())[:1000],
            "ledger_armed": bool(session.finish_ledger.armed),
        }
        # The lists are read off the transcript (rule 5) and their item
        # bounds alone could pass a frame: each is cut to its byte budget
        # as storefeed caps an item, so the event always frames.
        for name, budget in protocol.SESSION_LIST_BYTES.items():
            facts[name] = protocol.fit_list_bytes(facts[name], budget)
        return facts

    def refresh_facts(self) -> None:
        """The screen settled (or the process questions moved): re-read the
        box facts and send what changed. Before the spawn and after the
        exit there is nothing to read."""
        if self.pty() is None:
            return
        facts = {**self.box_facts(), **self.launch_facts()}
        # The process questions walk the tree: re-asked here only when the
        # foreground flips; the tracker's 2 s poll re-reads them for every
        # session (`refresh_process_facts`: a sandboxed launch's
        # `agent_running` turns true under it), so a settle never walks
        # /proc on its own account (a command streaming output would have
        # it walk twenty times a second).
        if facts.get("running_command") != self._sent.get("running_command"):
            facts.update(self.process_facts())
        self._send_changed(facts)

    def refresh_process_facts(self) -> None:
        """The /proc poll's tick: the questions that walk the process tree."""
        if self.pty() is None:
            return
        self._send_changed(
            {**self.process_facts(), "running_command": bool(self.session.has_running_command())}
        )

    # -- SessionHost -----------------------------------------------------------------

    def alive(self) -> bool:
        return not self.exited

    def recorded_cwd(self) -> str | None:
        """The store row's starting directory for the session, None with no
        store or no row (a fresh spawn)."""
        store = getattr(self.core, "store", None)
        session_id = self.session.session_id
        if store is None or not session_id:
            return None
        row = store.get_session(session_id)
        cwd = getattr(row, "cwd", None) if row is not None else None
        return cwd if isinstance(cwd, str) and cwd else None

    def paint(self, text: str) -> None:
        line = paint_line(text)
        pty = self.pty()
        if pty is None:
            self._pending_paints.append(line)
            return
        self.core.ptys.paint(pty.id, line)

    def focus_terminal(self) -> None:
        pty = self.pty()
        if pty is None:
            return
        self._event({"t": "focus.terminal", "pty": pty.id}, active_only=True)

    def refocus_composer(self) -> None:
        self._composer("refocus")

    def resend_composed(self) -> None:
        self._composer("resend")

    def stash_draft(self, text: str) -> None:
        self._composer("stash", text)

    def _composer(self, what: str, text: str | None = None) -> None:
        pty = self.pty()
        if pty is None:
            return
        event: dict = {"t": "composer", "pty": pty.id, "what": what}
        if text is not None:
            # The stash's belt: a text the frame can't carry loses its tail
            # here rather than the event (a cut's seed never comes this way).
            # Inside a try with the rest (rule 4): the fit must not take the
            # event down on a text it can't measure.
            try:
                text = protocol.fit_text_bytes(text[: protocol.TEXT_MAX], protocol.EVENT_TEXT_BYTES)
            except Exception:  # noqa: BLE001 - the measure failing is not the stash's loss
                log.exception("session %s: the stash's text could not be fitted", self.handle)
                text = text[: protocol.TEXT_MAX]
            event["text"] = text
        self._event(event, active_only=True)

    def mapped(self) -> bool:
        """"At least one client is attached to this agent's pty" — main's
        "has a tab" (§3.19). The one thing the session asks this for is
        the resolver's budget (`Session._resolve_transcript`): with a
        client attached it polls without end, as an on-screen tab did;
        with none, for RESOLVER_BACKGROUND_TICKS, and an attach re-arms it
        (`client_attached`), as a tab's map does through `resolver.arm`.
        The session's own submit re-arms it too, before the write lands
        (`ServiceActivity.input_sent` / `on_input`), so a re-arm's
        baseline can never swallow the session's own transcript. The cwd
        poll runs for as long as the session lives (`alive`)."""
        pty = self.pty()
        return pty is not None and bool(pty.attachments)

    def composer_open(self) -> bool:
        """The client fact `_post_switch` reads: whether the composer is up
        over the box, as the last `switch`, `send`, `cut`, `cut.cancel` or
        `draft.restore` request said."""
        return self._composer_open

    def set_composer_open(self, flag: bool) -> None:
        self._composer_open = bool(flag)

    def shown_prs(self) -> list[prstatus.PullRequest]:
        return self._shown_prs

    def transcript_reset(self) -> None:
        self._shown_prs = []
        self._landed = False
        # Through `_fit` as every fact goes (rule 5): one that doesn't fit
        # is dropped alone, never the whole event with it.
        facts = self._fit(self.transcript_facts())
        self._sent.update(facts)
        self._sent["lookup_empty"] = False
        self.send({**facts, "reset": True})

    def transcript_landed(self, prs: list[prstatus.PullRequest], lookup_empty: bool) -> None:
        self._shown_prs = list(prs)
        self._landed = True
        facts = self._fit(self.transcript_facts())
        changed = {k: v for k, v in facts.items() if self._sent.get(k, _UNSENT) != v}
        self._sent.update(facts)
        self._sent["lookup_empty"] = bool(lookup_empty)
        changed["landed"] = True
        if lookup_empty:
            changed["lookup_empty"] = True
        self.send(changed)
        self.core.session_transcript_landed(self)

    def running_session_ids(self) -> set[str]:
        """The ids every *other* live agent pty runs (`agent_pty_sessions`),
        for the resolver's refusal to bind one of them (N-M2)."""
        return {
            sid for pty_id, sid in self.core.agent_pty_sessions().items()
            if sid and pty_id != self.pty_id
        }

    def session_resolved(self, session_id: str) -> None:
        self.core.session_resolved(self, session_id)
        # Where the agent is and what it launched with go in the same event:
        # a client acts on the id the moment it lands (start_session's reply),
        # and the cwd poll may not have ticked since a `-w` launch moved the
        # agent, nor a settle since a refused one dropped the flag.
        self._send_changed(
            {
                "session": session_id,
                "agent_cwd": self.session.current_agent_cwd(),
                **self.launch_facts(),
                **self.box_facts(),
                **self.sandbox_facts(),
            }
        )

    def fork_resolved(self, session_id: str) -> None:
        self.core.session_forked(self, session_id)
        self.send({"forked": session_id})

    def cwd_polled(self, cwd: str | None) -> None:
        self._send_changed({"agent_cwd": cwd})

    def spawn_shell(self, cwd: str, env: list[str] | None) -> None:
        self.core.spawn_agent_pty(self, cwd)
        pty = self.pty()
        if pty is None:
            return  # refused: the request's reply says so
        for line in self._pending_paints:
            self.core.ptys.paint(pty.id, line)
        self._pending_paints = []
        self.spawned_at = time.monotonic()
        self.session.shell_spawned()
        self.session.start_cwd_poll()
        self.refresh_facts()
        self._sent.update(self.process_facts())

    def sandbox_changed(self) -> None:
        self._send_changed(self.sandbox_facts())

    def mark_stale_shells(self) -> None:
        pty = self.pty()
        if pty is None:
            return
        self._event({"t": "shells", "pty": pty.id, "what": "stale"})

    def input_sent(self, text: str) -> None:
        """The session is about to type *text* into its pty (`Session.
        write_text`: an injected prompt, a switch, a close flow's keys),
        told before the write lands. The gate the session pokes itself;
        the tracker takes the process baseline's last pristine snapshot on
        a "\\r" here, as it does on a client's input frame carrying one
        (`ServiceActivity.input_sent`; the window's `_on_input_sent` of
        PR 602, moved with the tracker)."""
        activity = self.core.activity
        if activity is not None:
            try:
                activity.input_sent(self, text)
            except Exception:  # noqa: BLE001 - the tracker's failure is not the write's
                log.exception("session %s: the tracker failed on the session's own write", self.handle)

    def process_exited(self, status: int) -> None:
        self.exited = True
        self._cancel_orphan_recheck()
        if self._settle is not None:
            from gi.repository import GLib

            GLib.source_remove(self._settle)
            self._settle = None
        for cut in list(self.cuts.values()):
            cut.cancel()

    def close_budget(self) -> None:
        """The close poll's budget ran out (`Session.begin_close`'s force,
        for the `exit` and `background` modes alike). With a client attached
        the window decides, as its own budget always had it (a `close`
        event, `state: budget`: the forced close, or a dialog). With none —
        the tab detached mid-drain, a quit the user insisted on, a client
        that crashed — nobody would answer, and the session the user asked
        to stop would run on with nothing watching it (§3.10): the service
        forces the close itself, as `ServiceCore.stop_sessions` does
        (`end_close`, then the pty closed)."""
        pty = self.pty()
        if pty is None:
            return
        if not pty.attachments:
            self._force_close("no client is attached")
            return
        self.budget_spent = True
        self._event({"t": "close", "pty": pty.id, "state": "budget", "phase": self.session.close_phase()})

    def client_detached(self) -> None:
        """A client left this pty (a `detach`, or the client gone). A close
        whose budget ran out while it was attached waits for its answer;
        once no client is left to give one, the force is the service's.
        And a pty nobody shows whose CLI is not running is a shell alone
        (`close_if_orphan_shell`)."""
        pty = self.pty()
        if pty is None or pty.attachments:
            return
        if self.budget_spent and self.session.closing:
            self._force_close("the client the budget was told to has gone")
            return
        self.close_if_orphan_shell("the last client detached")

    def close_if_orphan_shell(self, why: str) -> bool:
        """A shell alone with no client showing it is nothing the service
        keeps: the CLI never came up (no `claude` on PATH, a bad
        `claude_cli_path`, a sandbox launch that failed) and the tab was
        detached or its window went, or the CLI left by itself after the
        tab was detached. No running row stands for it, nothing attaches to
        it, and only a resume of the same session would ever close it: it
        is closed here instead (PR 605's verification). Not while a close
        flow owns the pty (`closing`: the poll ends the shell itself).

        A pty whose CLI has never been seen running (`cli_ever_ran`) is held
        for ORPHAN_HOLD_S from its spawn: the shell is still running its rc
        and has not forked the typed command, so "no CLI" says nothing yet.
        One re-check is scheduled for the hold's end, with the same guards;
        an attach in the meantime cancels it (`client_attached`). A CLI seen
        running and then gone is closed at once. Returns whether it was
        closed."""
        pty = self.pty()
        if pty is None or pty.attachments or self.session.closing or self.exited:
            return False
        if pty.master < 0:
            return False  # already closing (the master is gone): no second force
        if self.session.has_running_command():
            return False
        if not self.cli_ever_ran:
            left = ORPHAN_HOLD_S - (time.monotonic() - self.spawned_at)
            if left > 0:
                self._schedule_orphan_recheck(left, why)
                return False
        self._cancel_orphan_recheck()
        log.info("session %s: a shell alone with no client, %s: the pty is ended", self.handle, why)
        self.core.force_close(pty.id)
        return True

    def _schedule_orphan_recheck(self, delay_s: float, why: str) -> None:
        if self._orphan_recheck is not None:
            return  # one re-check per hold

        from gi.repository import GLib

        def recheck() -> bool:
            self._orphan_recheck = None
            self.close_if_orphan_shell(f"{why}; the startup hold is over")
            return GLib.SOURCE_REMOVE

        self._orphan_recheck = GLib.timeout_add(int(delay_s * 1000) + 50, recheck)

    def _cancel_orphan_recheck(self) -> None:
        if self._orphan_recheck is None:
            return

        from gi.repository import GLib

        GLib.source_remove(self._orphan_recheck)
        self._orphan_recheck = None

    def _force_close(self, why: str) -> None:
        pty = self.pty()
        if pty is None:
            return
        log.info("session %s: close budget ran out, %s: the pty is ended", self.handle, why)
        self.budget_spent = False
        self.core.force_close(pty.id)

    def _event(self, event: dict, active_only: bool = False) -> None:
        pty = self.pty()
        if pty is None:
            return
        checked = protocol.validate(dict(event), protocol.SERVICE)
        if isinstance(checked, protocol.Refusal):
            log.error("session %s: an event the protocol refuses: %s", self.handle, checked.msgid)
            return
        targets = list(pty.attachments.values())
        if active_only and pty.active is not None and pty.active in pty.attachments:
            targets = [pty.attachments[pty.active]]
        for attachment in targets:
            try:
                attachment.sink.send_event(dict(event))
            except Exception:
                log.exception("session %s: a client's sink failed", self.handle)

    # -- the composer's cut ----------------------------------------------------------

    def begin_cut(self, sink) -> str:
        """Start an open-cut for the composer behind *sink* (the client's
        pty sink); its handle, which the `cut` events carry."""
        handle = f"cut-{next(_cuts)}"
        cut = CutSink(self, sink, handle)
        superseded = self._chain
        if superseded is not None and superseded.handle in self.cuts:
            superseded.ended()  # the session runs one chain: the older cut is over
        self.cuts[handle] = cut
        self._chain = cut
        self.session.begin_cut(cut)
        return handle

    def cancel_cuts(self, sink=None, handle: str | None = None) -> None:
        """Call cuts off: one by handle, every one of a client's (*sink*),
        or all. The session's own chain is called off with the last cut
        (another client's cut, still live, keeps its chain)."""
        for cut in list(self.cuts.values()):
            if handle is not None and cut.handle != handle:
                continue
            if sink is not None and cut.sink is not sink:
                continue
            cut.cancel()
        chain = self._chain
        ours = chain is not None and (sink is None or chain.sink is sink)
        if ours and (handle is None or chain.handle == handle):
            # The chain in flight is this client's (its seed may have been
            # told already, its verify rounds still running): called off, so
            # no round of it erases what the composer's close puts back.
            self._chain = None
            self.session.cancel_cut()

    # -- the attach -------------------------------------------------------------------

    def client_attached(self, sink) -> None:
        """The facts whole to the one attaching — after what moved since
        the last send went to everyone already attached, so a second attach
        inside a settle starves no one (the snapshot records nothing). A
        resolver paused for want of a client resumes (`mapped`). A shell
        alone's startup hold (`close_if_orphan_shell`) is off: somebody
        shows it again."""
        self._cancel_orphan_recheck()
        self.refresh_facts()
        self.send_to(sink, self.snapshot())
        self.session.arm_resolver()

    def client_gone(self, sink) -> None:
        for cut in list(self.cuts.values()):
            if cut.sink is sink:
                cut.cancel(tell=False)


_UNSENT = object()

# How long a pty whose CLI has never been seen running is held before it can
# be closed as a shell alone: the shell's rc runs first (bash or zsh with a
# heavy one takes a second or more), and until it forks the typed command
# `has_running_command` is the shell's own group, so a detach or a client
# crash right after a new-chat Send must not read as "the CLI never came up"
# and lose the first prompt riding on the command line.
ORPHAN_HOLD_S = 30.0


def sandbox_plan_of(records: Callable[[], list], box: str) -> str | None:
    """The plan file the session running in *box* launched from, or None:
    what a sandboxed panel shell is spawned on (`ServiceCore`'s lookup)."""
    if not box:
        return None
    for record in records():
        session = record.session
        if session.sandbox_box == box and session.sandbox_plan_path:
            return session.sandbox_plan_path
    return None
