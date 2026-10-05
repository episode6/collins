# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Git over the API, the client's end (split-service spec §3.23, PR-2.1).

The git page, the sidebar's menus, the footer's branch label and the
window's pull / checkout keep calling `gitinfo` and `gitops` as they did;
this module is what makes those calls reach the service instead of this
machine's `.git` and git:

- **The mirror** (`Mirror`): a per-cwd copy of the service's `git.info`
  reply (`gitfiles.GitInfo`), installed as `gitinfo`'s reader. A read
  serves the entry when it is younger than `gitinfo.MAX_AGE_S`, else asks
  the service (one blocking `call` on the caller's thread: the footer's
  tick, a right-click, a page's worker — never the link's I/O thread);
  `gitinfo.refresh` forces one (the page's tick, before it compares the
  signatures); `has_changes` / `change_summary` always ask fresh, with
  ``changes`` (one `git status` on the service, on demand as before). The
  service's `git-changed` events refresh the entry without blocking
  (`send`) and are handed to the page watching that cwd (`on_changed`).
  `watch` / `unwatch` are the page's directory monitors, installed on the
  service.
- **The transport** (`Transport`, `gitops.GitTransport`): every argv a
  runner is handed is mapped back to its builder and keyword args
  (`gitops.match_argv`, D33) and sent as `git.run`; an argv no builder
  makes is `unreachable` with a logged error. A blob is the HTTP GET on
  the same socket (`SocketLink.http_get`), the sizes `git.sizes`, the
  watch's tree state `git.info` with ``state``, and a plan `git.plan`,
  whose ``stale`` refusal comes back as `ApplyResult.stale`.

`install(link)` wires both (the app, once the link is connected);
`reset()` empties the mirror (a reconnect). GTK-free; the unit tests
drive it through a fake link.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from urllib.parse import urlencode

from . import apilink, gitinfo, gitops, gitpatch
from .api import protocol
from .api.protocol import RequestRefused
from .gitfiles import GitInfo
from .i18n import _


def _words(refusal: RequestRefused) -> str:
    """A refusal's msgid translated and filled, for a toast or a dialog
    (§3.14); the code when the service sent no words."""
    try:
        return _(refusal.msgid).format_map(refusal.details) if refusal.msgid else refusal.error
    except (KeyError, IndexError, ValueError):
        return refusal.msgid

log = logging.getLogger(__name__)

# How much longer than git's own timeout a `git.run` reply is waited for:
# the service kills git at the timeout and answers `unreachable`; the
# margin covers the round trip and a loaded service.
CALL_MARGIN_S = 10.0
# How long a blob GET may take (a 64 MiB blob over a local socket).
BLOB_TIMEOUT_S = 60.0
# The most entries the mirror keeps (a cwd per tab, plus the menus').
MAX_ENTRIES = 256

Listener = Callable[[dict], None]


class Mirror:
    """The per-cwd `git.info` mirror. *link_of* is where the link comes
    from (`apilink.current` by default: the app's, or a harness's)."""

    def __init__(self, link_of: Callable[[], apilink.Link | None] = apilink.current) -> None:
        self._link_of = link_of
        self._lock = threading.Lock()
        self._entries: dict[str, GitInfo] = {}
        self._listeners: dict[str, list[Listener]] = {}
        self._installed_on: apilink.Link | None = None

    # -- the link

    def install(self, link: apilink.Link) -> None:
        """Hear the link's `git-changed` events (once per link: a
        reconnect keeps the handlers)."""
        if self._installed_on is link:
            return
        link.on("git-changed", self._on_changed)
        self._installed_on = link

    def reset(self) -> None:
        """Forget every entry (a reconnect: the service may be another)."""
        with self._lock:
            self._entries.clear()

    # -- gitinfo's reader

    def read(
        self, cwd: str, max_age: float = gitinfo.MAX_AGE_S, changes: bool = False, state: bool = False
    ) -> GitInfo | None:
        """The entry for *cwd*, fetched when missing, older than *max_age*,
        or when *changes* / *state* are wanted (never served stale). None
        when the service could not be asked at all."""
        with self._lock:
            entry = self._entries.get(cwd)
        if entry is not None and not changes and not state and time.monotonic() - entry.fetched_at <= max_age:
            return entry
        return self._fetch(cwd, entry, changes, state)

    def _fetch(self, cwd: str, previous: GitInfo | None, changes: bool, state: bool) -> GitInfo | None:
        message: dict = {"t": "git.info", "cwd": cwd}
        if changes:
            message["changes"] = True
        if state:
            message["state"] = True
        if previous is not None and previous.refs:
            message["known_refs"] = previous.refs
        try:
            fields = apilink.call(message, timeout=CALL_MARGIN_S + gitops.GIT_TIMEOUT_S)
        except RequestRefused as refusal:
            log.debug("git.info %s refused: %s", cwd, refusal.msgid)
            return None
        return self._store(cwd, fields, previous)

    def _store(self, cwd: str, fields: dict, previous: GitInfo | None) -> GitInfo:
        entry = GitInfo.from_fields(fields, previous)
        with self._lock:
            if len(self._entries) >= MAX_ENTRIES and cwd not in self._entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[cwd] = entry
        return entry

    def entry(self, cwd: str) -> GitInfo | None:
        """The entry as it is, no fetch (a probe)."""
        with self._lock:
            return self._entries.get(cwd)

    # -- the watch

    def watch(self, cwd: str, files: Sequence[str], state: str | None = None) -> None:
        """Install the page's monitors on the service for *cwd* (the
        loaded files' directories, `git.watch`); the reply is not waited
        for. Replaces any earlier watch of the cwd by this client. *state*
        is the tree state the page's read sampled: the service's first
        compare is against it, so an edit between the read and the
        watch's first look is a move."""
        link = self._link_of()
        if link is None:
            return
        message: dict = {"t": "git.watch", "cwd": cwd, "files": list(files)[: protocol.REPO_PATHS_MAX]}
        if state is not None:
            message["state"] = state
        link.send(message, on_refused=lambda r: log.debug("git.watch %s refused: %s", cwd, r.msgid))

    def unwatch(self, cwd: str) -> None:
        link = self._link_of()
        if link is None:
            return
        link.send({"t": "git.unwatch", "cwd": cwd}, on_refused=lambda r: None)

    def on_changed(self, cwd: str, listener: Listener) -> None:
        """Call *listener* with every `git-changed` event for *cwd* (the
        page's; on the main loop, where events land)."""
        self._listeners.setdefault(cwd, []).append(listener)

    def off_changed(self, cwd: str, listener: Listener) -> None:
        listeners = self._listeners.get(cwd)
        if listeners and listener in listeners:
            listeners.remove(listener)
            if not listeners:
                del self._listeners[cwd]

    def _on_changed(self, event: dict) -> None:
        cwd = event.get("cwd")
        if not isinstance(cwd, str):
            return
        link = self._link_of()
        with self._lock:
            previous = self._entries.get(cwd)
        if link is not None and previous is not None:
            message: dict = {"t": "git.info", "cwd": cwd}
            if previous.refs:
                message["known_refs"] = previous.refs
            link.send(
                message,
                on_reply=lambda fields: self._store(cwd, fields, self.entry(cwd)),
                on_refused=lambda r: None,
            )
        for listener in list(self._listeners.get(cwd, ())):
            try:
                listener(event)
            except Exception:
                log.exception("remotegit: a git-changed listener failed")


class Transport:
    """`gitops.GitTransport` over the link (see the module docstring)."""

    def __init__(self, mirror: Mirror, link_of: Callable[[], apilink.Link | None] = apilink.current) -> None:
        self.mirror = mirror
        self._link_of = link_of

    @staticmethod
    def _env_name(env: dict[str, str] | None) -> str | None:
        """The service's name for an environment a runner was handed: the
        two the git page and the window use, or None for one it can't
        name (never sent as is: rule 5)."""
        if env is None:
            return protocol.GIT_ENV_DEFAULT
        if env.get("GIT_TERMINAL_PROMPT") == "0" and env.get("GIT_EDITOR") == "true":
            return protocol.GIT_ENV_NO_PROMPT
        if env.get("GIT_EDITOR") == "true":
            return protocol.GIT_ENV_NO_EDITOR
        return None

    def run(
        self,
        cwd: str,
        argv: Sequence[str],
        stdin: bytes | None,
        timeout: float,
        env: dict[str, str] | None,
        ok_statuses: tuple[int, ...],
    ) -> gitops.GitResult:
        found = gitops.match_argv(argv)
        if found is None:
            log.error("remotegit: no builder makes this argv, not sent: git %s", " ".join(list(argv)[:6]))
            return gitops.GitResult(False, "", "not a git call Collins makes", unreachable=True)
        env_name = self._env_name(env)
        if env_name is None:
            log.error("remotegit: an environment the service has no name for, not sent: %s", sorted(env))
            return gitops.GitResult(False, "", "an environment the service cannot run", unreachable=True)
        name, args = found
        message: dict = {
            "t": "git.run",
            "cwd": cwd,
            "builder": name,
            "args": args,
            "timeout": float(min(max(timeout, 0.1), protocol.GIT_TIMEOUT_MAX)),
            "env": env_name,
        }
        if stdin:
            message["stdin"] = stdin.decode("utf-8", "replace")
        try:
            fields = apilink.call(message, timeout=message["timeout"] + CALL_MARGIN_S)
        except RequestRefused as refusal:
            return gitops.GitResult(False, "", _words(refusal), unreachable=True)
        status = fields.get("status")
        stdout = fields.get("stdout") or ""
        stderr = fields.get("stderr") or ""
        if status is None or fields.get("unreachable"):
            reason = stderr or "git could not be run on the service"
            return gitops.GitResult(False, stdout, reason, unreachable=True)
        return gitops.GitResult(int(status) in tuple(ok_statuses), stdout, stderr)

    def blob(self, cwd: str, at: str, ref: str | None, path: str, timeout: float) -> gitops.BlobResult:
        link = self._link_of()
        if link is None or not hasattr(link, "http_get"):
            return gitops.BlobResult(False, b"", "Not connected to the service", unreachable=True)
        url = blob_url(cwd, at, ref, path)
        try:
            status, _headers, data = link.http_get(url, timeout=max(timeout, BLOB_TIMEOUT_S))
        except Exception as err:
            return gitops.BlobResult(False, b"", str(err) or err.__class__.__name__, unreachable=True)
        if status == 200:
            return gitops.BlobResult(True, data, "")
        return gitops.BlobResult(False, b"", f"blob {status}")

    def sizes(self, root: str, paths: Sequence[str]) -> dict[str, int | None]:
        listed = list(paths)[: protocol.REPO_PATHS_MAX]
        if not listed:
            return {}
        fields = apilink.call({"t": "git.sizes", "cwd": root, "paths": listed}, timeout=CALL_MARGIN_S)
        sizes = fields.get("sizes") or {}
        return {path: sizes.get(path) for path in listed}

    def tree_state(self, cwd: str) -> str | None:
        entry = self.mirror.read(cwd, state=True)
        return entry.state if entry is not None else None

    def plan(
        self, cwd: str, plan: gitpatch.Plan, context: gitops.PlanContext, three_way: bool, timeout: float
    ) -> gitops.ApplyResult:
        message = {
            "t": "git.plan",
            "cwd": cwd,
            "load": context.load,
            "path": context.path,
            "previous_path": context.previous_path,
            "parent_target": context.parent_target,
            "op": plan.op,
            "paths": list(plan.paths),
            "patch": plan.patch,
            "three_way": bool(three_way),
            "keys": list(context.keys),
        }
        try:
            fields = apilink.call(message, timeout=max(timeout, gitops.GIT_TIMEOUT_S) * 2 + CALL_MARGIN_S)
        except RequestRefused as refusal:
            if refusal.error == protocol.ERROR_STALE:
                return gitops.ApplyResult(False, "", _words(refusal), stale=True)
            return gitops.ApplyResult(False, "", _words(refusal), unreachable=True)
        return gitops.ApplyResult(
            bool(fields.get("applied")),
            fields.get("stdout") or "",
            fields.get("stderr") or "",
            unreachable=bool(fields.get("unreachable")),
            three_way=bool(fields.get("three_way")),
            conflicts=bool(fields.get("conflicts")),
        )


def blob_url(cwd: str, at: str, ref: str | None, path: str) -> str:
    """The `GET /api/blob` path and query for a git blob (§3.23): the
    service's `cwd`, where to read it (`at`: worktree, index or a `ref`)
    and the repository-relative `path`."""
    query = {"kind": "git", "cwd": cwd, "at": at, "path": path}
    if at == gitops.AT_REF:
        query["ref"] = ref or ""
    return "/api/blob?" + urlencode(query)


# ---- the app's wiring -----------------------------------------------------------

_mirror = Mirror()
_transport = Transport(_mirror)


def mirror() -> Mirror:
    return _mirror


def install(link: apilink.Link) -> None:
    """Make gitinfo and gitops read and run through the service on
    *link* (the app, once connected; a harness check too)."""
    _mirror.install(link)
    gitinfo.set_reader(_mirror.read)
    gitops.set_transport(_transport)


def uninstall() -> None:
    gitinfo.set_reader(None)
    gitops.set_transport(None)


def reset() -> None:
    """A reconnect: the mirror starts over."""
    _mirror.reset()
