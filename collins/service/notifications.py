# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The notification history, kept by the service (split-service spec §3.13).

The service produces the notification record (its kind, its session, its
text as a msgid and args, §3.14) and owns the history and the unread set;
each client applies `notifycenter`'s delivery table with its own focus
state and does the card, the sound, the desktop notification, the flash
and the tray, and reports what was seen, so unread is one number
everywhere. `ServiceNotifications` is the service's end of that: a
`notifycenter.NotificationCenter` over state.json's records (the center's
whole behaviour, coalescing and capping included, unchanged), persisted
after every change of what is persisted, and published to every
subscriber as `notify` events: the snapshot at `subscribe` (every row,
oldest first, so a mirror that puts each new row at the top ends up in the
center's order), then each row that changed or appeared, and each that
left (``removed``). A `seen` request reads rows (by id, by session, or
all) and is told to every other subscriber as a `seen` event.

What a client asks for (a window's bell and `notify_user`): a row
(`notify.post`, minted here: its id, its time; a bell coalesced into the
unread bell of its session; an update replacing every other), a row's
removal, the history cleared, a finished run's synthetic row raised or
taken down (`notify.green`: the window decides green — unread and not
working, `traymodel.session_marker` — from the row's `unread` and `busy`,
which are the service's verdicts), a
placeholder's rows filed under the session it resolved to
(`notify.rekey`). A producer on this side (`post`) is the `notify_user`
tool with no client attached. The command sink for when no client is
attached is `notify_command` (the sink, below).

**The sink.** `notify_command` (a service setting, default empty) is an
argv template run for a `notify_user` call and for a counted finish when
no client is subscribed (`has_clients`, the core's subscriber list),
with `COLLINS_NOTIFY_KIND`, `_TITLE`, `_BODY`, `_SESSION` and `_PROJECT`
in its environment, `stdin=DEVNULL` and a 10 s timeout. The template is
split with `shlex.split` and run as an argv, never through a shell, and
the values are in the environment only (nothing in the template is
expanded: a person who wants them on a command line wraps it in a
script). It runs on a daemon thread, so a slow program never holds the
loop, and it fails soft: an empty setting, a template that does not
split, a program that is missing, a timeout or a non-zero exit is
logged and skipped. `notify.test` runs the same command once, whether or
not a client is attached, and answers with its exit status or the error.

GLib only; nothing here imports GTK.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import threading
from collections.abc import Callable

from .. import notifycenter
from ..api import protocol
from ..i18n import english
from ..notifycenter import Notification, NotificationCenter

log = logging.getLogger(__name__)

SETTING = "notify_command"
COMMAND_TIMEOUT_S = 10.0
# What goes into the command's environment is bounded (rule 5: an agent's
# message is foreign text): the kind and the session id are short, the
# title and project are names, the body is the message.
_ENV_MAX = {"KIND": 32, "TITLE": 1024, "BODY": 8192, "SESSION": 256, "PROJECT": 1024}
_STDERR_KEEP = 200


def command_env(kind: str, title: str, body: str, session: str, project: str) -> dict[str, str]:
    """The five variables the command is told its notification by, each
    bounded and free of the NUL an environment cannot carry."""
    values = {"KIND": kind, "TITLE": title, "BODY": body, "SESSION": session, "PROJECT": project}
    return {
        f"COLLINS_NOTIFY_{name}": str(value or "").replace("\0", "")[: _ENV_MAX[name]]
        for name, value in values.items()
    }


def run_command(
    template: str, env: dict[str, str], timeout: float = COMMAND_TIMEOUT_S
) -> tuple[int | None, str]:
    """Run *template* (an argv template, `shlex.split`, never a shell) with
    *env* added to the service's own, `stdin=DEVNULL`, for at most *timeout*
    seconds. Returns ``(exit status, "")`` once it ended (negative: the
    signal that ended it) or ``(None, why)`` when it did not run or did not
    finish, in English. Blocking: call it off the main loop. Never raises."""
    template = (template or "").strip()
    if not template:
        return None, "No notify command is set"
    try:
        argv = shlex.split(template)
    except ValueError as error:
        return None, f"The notify command doesn't parse: {error}"
    if not argv:
        return None, "No notify command is set"
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={**os.environ, **env},
            start_new_session=True,
        )
    except (OSError, ValueError) as error:
        return None, f"Couldn't run {argv[0]}: {getattr(error, 'strerror', None) or error}"
    try:
        _out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The whole group, so a script's children go with it.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.communicate()
        return None, f"{argv[0]} didn't finish in {timeout:g} s"
    except Exception as error:  # fail soft: never into the service's loop
        log.exception("notify_command: waiting on %s failed", argv[0])
        return None, f"Couldn't run {argv[0]}: {error}"
    if proc.returncode:
        tail = (err or b"").decode("utf-8", "replace").strip()[-_STDERR_KEEP:]
        log.warning("notify_command: %s exited %s%s", argv[0], proc.returncode, f": {tail}" if tail else "")
    return proc.returncode, ""


def _thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="notify-command", daemon=True).start()


def _dispatch(fn: Callable[[], object]) -> None:
    from .gitfeed import dispatch_default

    dispatch_default(fn)


def event_for(row: Notification) -> dict:
    """A row as the `notify` event carries it, bounded."""
    event = {
        "t": "notify",
        "notification": row.id,
        "kind": row.kind[: protocol.SHORT_MAX],
        "session": row.session_id,
        "title": row.title[: protocol.NAME_MAX],
        "project": row.project[: protocol.NAME_MAX],
        "msgid": (row.text_id or " ")[: protocol.MSGID_MAX],
        "when": float(row.when),
        "read": bool(row.read),
        "count": max(1, int(row.count)),
    }
    if row.args:
        event["args"] = dict(list(row.args.items())[: protocol.ARGS_MAX])
    if row.url:
        event["url"] = row.url
    return event


def _key(event: dict) -> str:
    """An event's identity for the change check: its JSON, keys sorted."""
    return json.dumps(event, sort_keys=True, ensure_ascii=False)


class ServiceNotifications:
    """See the module docstring. *state* is the service's AppState."""

    def __init__(
        self,
        state,
        get_setting: Callable[[str], object] | None = None,
        has_clients: Callable[[], bool] | None = None,
        run_async: Callable[[Callable[[], None]], None] | None = None,
        run: Callable[..., tuple[int | None, str]] | None = None,
        dispatch: Callable[[Callable[[], object]], None] | None = None,
    ) -> None:
        """*get_setting* reads the service's settings (the core's),
        *has_clients* says whether a client is subscribed (the core's
        `_subscribers`; by default this class's own subscribers),
        *run_async* starts the sink's blocking work off the loop, *run* is
        `run_command` and *dispatch* lands a thread's answer on the main
        loop: a test's own."""
        self.state = state
        self._get_setting = get_setting or (lambda key: None)
        self._has_clients = has_clients or (lambda: bool(self._subscribers))
        self._run_async = run_async or _thread
        self._run = run or run_command
        self._dispatch = dispatch or _dispatch
        self.center = NotificationCenter(records=state.get_notifications())
        self._subscribers: list[tuple[object, Callable[[dict], None]]] = []
        self._published: dict[str, dict] = {row.id: event_for(row) for row in self.center.rows()}
        self.center.connect(self._on_changed)

    # -- subscribers ---------------------------------------------------------------

    def subscribe(self, client, deliver: Callable[[dict], None]) -> int:
        """The snapshot: every row, oldest first. Returns how many."""
        self._subscribers = [(c, d) for c, d in self._subscribers if c is not client]
        self._subscribers.append((client, deliver))
        rows = self.center.rows()
        for row in reversed(rows):
            self._send(deliver, event_for(row))
        return len(rows)

    def unsubscribe(self, client) -> None:
        self._subscribers = [(c, d) for c, d in self._subscribers if c is not client]

    def _send(self, deliver: Callable[[dict], None], event: dict) -> None:
        try:
            deliver(event)
        except Exception:
            log.exception("notifications: a subscriber's delivery failed")

    def _broadcast(self, event: dict, skip=None) -> None:
        for client, deliver in list(self._subscribers):
            if client is not skip:
                self._send(deliver, event)

    def _on_changed(self) -> None:
        """Persist what is persisted, then tell every subscriber what moved:
        rows that appeared or changed (oldest first, so each lands on top in
        order), then rows that left. Each change rebuilds every row's event
        to compare it with what was published: O(rows), and rows are capped
        at notifycenter.ROW_CAP (200) plus the finished runs' rows, so a
        change costs a few hundred small dicts at most."""
        self.state.set_notifications(self.center.to_records())
        rows = self.center.rows()
        current = {row.id: event_for(row) for row in rows}
        for row in reversed(rows):
            event = current[row.id]
            old = self._published.get(row.id)
            if old is None or _key(old) != _key(event):
                self._broadcast(event)
        for row_id, old in list(self._published.items()):
            if row_id not in current:
                gone = dict(old)
                gone["removed"] = True
                self._broadcast(gone)
        self._published = current

    # -- what the service produces ---------------------------------------------

    def post(
        self,
        kind: str,
        session_id: str,
        title: str,
        project: str,
        msgid: str,
        args: dict | None,
        *,
        read: bool = False,
        url: str = "",
        key: str = "",
    ) -> Notification:
        """Mint a row and post it (the center coalesces a bell, replaces an
        update). Its body here is the English source (clients translate)."""
        row = self.center.make(kind, session_id, title, project, english(msgid, args), msgid=msgid, args=args)
        row.read = bool(read)
        row.url = url or ""
        if key:
            row.id = key
        return self.center.post(row)

    # -- the sink (notify_command) ----------------------------------------------

    def command(self) -> str:
        value = self._get_setting(SETTING)
        return value.strip() if isinstance(value, str) else ""

    def sink(self, kind: str, session_id: str, title: str, project: str, body: str) -> bool:
        """A notification for nobody to see: runs the notify_command, off
        the loop, when one is set and no client is subscribed. Returns
        whether a run was started. Never raises."""
        try:
            template = self.command()
            if not template or self._has_clients():
                return False
            env = command_env(kind, title, body, session_id, project)
            run = self._run

            def work() -> None:
                try:
                    _status, error = run(template, env)
                except Exception:
                    log.exception("notify_command: the run failed")
                    return
                if error:
                    log.warning("notify_command: %s", error)

            self._run_async(work)
            return True
        except Exception:
            log.exception("notify_command: couldn't start")
            return False

    def test(self, message: protocol.Message) -> protocol.Deferred:
        """`notify.test`: the command once, with or without a client, on a
        thread; the reply is settled on the main loop with the exit status
        or the error."""
        deferred = protocol.Deferred()
        template = self.command()
        env = command_env("test", "Collins", "Test notification", "", "")
        run = self._run

        def work() -> None:
            try:
                status, error = run(template, env)
            except Exception as exc:
                log.exception("notify_command: the test failed")
                status, error = None, f"Couldn't run the command: {exc}"
            fields: dict = {"error": error[: protocol.NAME_MAX]}
            if status is not None:
                fields["status"] = max(-255, min(255, int(status)))
            self._dispatch(lambda: deferred.settle(protocol.reply(message.id, **fields)))

        self._run_async(work)
        return deferred

    # -- requests -----------------------------------------------------------------

    def handle(self, message: protocol.Message, client) -> dict:
        kind = message.type
        if kind == "notify.post":
            post_kind = message.get("kind")
            key = message.get("key") or ""
            fixed = post_kind == notifycenter.KIND_UPDATE and key.startswith(notifycenter.UPDATE_PREFIX)
            if key and not fixed:
                return protocol.refuse(
                    message.id, protocol.ERROR_REFUSED, "Only an update row has a fixed id"
                )
            row = self.post(
                post_kind,
                message.get("session") or "",
                message.get("title") or "",
                message.get("project") or "",
                message.get("msgid"),
                message.get("args"),
                read=bool(message.get("read")),
                url=message.get("url") or "",
                key=key,
            )
            return protocol.reply(message.id, notification=row.id)
        if kind == "notify.remove":
            removed = sum(1 for row_id in message.get("ids") if self.center.remove(row_id))
            return protocol.reply(message.id, removed=removed)
        if kind == "notify.clear":
            return protocol.reply(message.id, removed=self.center.clear())
        if kind == "notify.green":
            changed = self.center.set_green(
                message.get("session"),
                bool(message.get("on")),
                title=message.get("title") or "",
                project=message.get("project") or "",
            )
            return protocol.reply(message.id, changed=bool(changed))
        if kind == "notify.rekey":
            return protocol.reply(
                message.id, moved=self.center.rekey_session(message.get("session"), message.get("to"))
            )
        if kind == "notify.test":
            return self.test(message)
        if kind == "seen":
            return self._seen(message, client)
        return protocol.refuse(message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": kind})

    def _seen(self, message: protocol.Message, client) -> dict:
        """Rows read: by id, by session, or all; the other subscribers hear
        it as a `seen` event (each row's `notify` says so too)."""
        if message.get("all"):
            self.center.mark_all_read()
        for row_id in message.get("ids") or []:
            self.center.mark_read(row_id)
        if message.get("session"):
            self.center.mark_session_read(message.get("session"))
        event = {"t": "seen"}
        for name in ("ids", "session", "all"):
            if message.get(name) is not None:
                event[name] = message.get(name)
        self._broadcast(event, skip=client)
        return protocol.reply(message.id)
