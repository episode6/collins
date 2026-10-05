# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Git over the API, the service's end (split-service spec §3.23, PR-2.1,
D33).

`GitFeed` serves the `git.*` requests `ServiceCore` routes to it and the
blob GET the API server hands it:

- `git.run {cwd, builder, args, stdin, timeout, env}`: the named builder
  of `gitops.BUILDERS` (every ``*_argv`` of gitops, plus the nine ad-hoc
  argv of gitloads, gitinfo and the window promoted to builders) is
  called with the args (`gitops.build`: a name outside the registry is
  ``unknown``, args the builder refuses or an argv that isn't bounded
  text ``invalid``) and the result runs on this machine's git, in the
  `cwd` the client named — a directory the service can see, inside a
  root it knows or anything for a `local` client (`files.allowed_cwd`;
  refused otherwise). The reply is git's exit status (null when git
  couldn't be run) and both streams as text. **The wire never carries an
  argv**, and the service never runs one it did not build.
- `git.info {cwd, changes, state, known_refs}`: `gitfiles.read_info` — every
  read gitinfo makes at once, the heads only when the client's refs digest
  differs — plus, when asked, the `git status` behind has_changes /
  change_summary and the watch's tree-state digest.
- `git.sizes`, `git.watch` / `git.unwatch` with the `git-changed` event
  (`_Watch`: the page's directory monitors, debounce and slow tick, here,
  per watching client; `git-changed` carries the three signature digests
  and a `.git` basename event is ignored as the page's monitors ignored
  it), and `git.plan` (`gitops.run_plan` with the stale check: the stable
  keys the plan names must all be in the fresh `file_patch`, else
  ``stale`` and nothing is applied; the trash is `files.trash_paths`).
- The blob GET (`blob`): `gitops.file_at` of the working tree, the index
  or a revision, with the commit-and-path (or mtime-and-size) ETag,
  ``304`` on a matching ``If-None-Match``, ``404`` for no such file,
  ``413`` over `gitops.MAX_BLOB_BYTES`.

**Nothing blocks the main loop.** Every request that runs git or reads
the tree does so on a daemon thread and answers through a
`protocol.Deferred` settled on the main loop (`GLib.idle_add` at
`PRIORITY_DEFAULT`); the transport sends the reply when it settles. The
watch's compares run the same way.

GLib and Gio only; nothing here imports GTK.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from urllib.parse import parse_qs

from gi.repository import Gio, GLib

from .. import diffmodel, gitfiles, gitinfo, gitloads, gitops, gitpatch
from ..api import protocol
from . import files

log = logging.getLogger(__name__)

# The page's watch, now the service's: the same bounds as gitpage had.
MAX_DIR_MONITORS = 64
WATCH_DEBOUNCE_MS = 300
WATCH_SLOW_TICK_S = 10
# A blob's tag for a working-tree file: mtime and size, as §3.11 has it.
_OCTET_STREAM = "application/octet-stream"


def _dispatch_default(fn: Callable[[], object]) -> None:
    GLib.idle_add(lambda: fn() and False, priority=GLib.PRIORITY_DEFAULT)


def _spawn_default(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, name=name, daemon=True).start()


class GitFeed:
    """See the module docstring. *core* is the `ServiceCore` (for the
    roots a cwd is confined to and the environment git runs in);
    *dispatch* lands a thread's answer on the main loop and *spawn* runs
    one (a test's inline)."""

    def __init__(
        self,
        core,
        dispatch: Callable[[Callable[[], object]], None] | None = None,
        spawn: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        if gitops.transport() is not None:
            # The service runs git on this machine: with a transport
            # installed in its process every builder run here would go
            # back over the API (git.plan recursing into itself).
            raise RuntimeError("gitfeed: a git transport is installed in the service's process")
        self.core = core
        self._dispatch = dispatch or _dispatch_default
        self._spawn = spawn or _spawn_default
        # A watch per client and handle (a page's), not per cwd: two pages
        # on one tree each have their own.
        self._watches: dict[tuple[int, str], _Watch] = {}

    # -- routing -------------------------------------------------------------------------

    def handle(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        kind = message.type
        if kind == "git.run":
            return self.run(message, client)
        if kind == "git.info":
            return self.info(message, client)
        if kind == "git.sizes":
            return self.sizes(message, client)
        if kind == "git.watch":
            return self.watch(message, client)
        if kind == "git.unwatch":
            return self.unwatch(message, client)
        if kind == "git.plan":
            return self.plan(message, client)
        return protocol.refuse(message.id, protocol.ERROR_UNKNOWN, "{type}: not served here", {"type": kind})

    def client_gone(self, client) -> None:
        for key, watch in list(self._watches.items()):
            if key[0] == id(client):
                watch.stop()
                del self._watches[key]

    def shutdown(self) -> None:
        for watch in self._watches.values():
            watch.stop()
        self._watches.clear()

    def _cwd(self, message: protocol.Message, client) -> str | dict:
        cwd = files.allowed_cwd(self.core, client, message.get("cwd"))
        if cwd is None:
            return protocol.refuse(
                message.id, protocol.ERROR_REFUSED, "{cwd} is not a directory the service may run git in",
                {"cwd": str(message.get("cwd"))[: protocol.ARG_TEXT_MAX]},
            )
        return cwd

    def _later(self, name: str, work: Callable[[], dict], re_id: int | None = None) -> protocol.Deferred:
        """*work* on a thread, its reply dict settled on the main loop. A
        *work* that raises settles a ``failed`` refusal of *re_id* (the
        request's id) rather than never settling: the peer gets exactly
        one response either way."""
        deferred = protocol.Deferred()

        def run() -> None:
            try:
                reply = work()
            except Exception:
                log.exception("gitfeed: %s failed on its thread", name)
                reply = protocol.refuse(
                    re_id if re_id is not None else 0, protocol.ERROR_FAILED,
                    "{name} failed on the service", {"name": name},
                )
            self._dispatch(lambda: deferred.settle(reply))

        self._spawn(run, name)
        return deferred

    # -- git.run ---------------------------------------------------------------------------

    def run(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        cwd = self._cwd(message, client)
        if isinstance(cwd, dict):
            return cwd
        name = str(message.get("builder"))
        try:
            argv = gitops.build(name, message.get("args") or {})
        except KeyError:
            return protocol.refuse(
                message.id, protocol.ERROR_UNKNOWN, "No git builder named {builder}", {"builder": name}
            )
        except ValueError as err:
            return protocol.refuse(
                message.id, protocol.ERROR_INVALID, "{builder}: {error}",
                {"builder": name, "error": str(err)[: protocol.ARG_TEXT_MAX]},
            )
        timeout = float(message.get("timeout") or gitops.GIT_TIMEOUT_S)
        env = self._environment(message.get("env") or protocol.GIT_ENV_DEFAULT)
        stdin_text = message.get("stdin")
        stdin = stdin_text.encode("utf-8", "replace") if isinstance(stdin_text, str) and stdin_text else None
        re_id = message.id

        def work() -> dict:
            status, stdout, stderr = run_git_raw(cwd, argv, stdin, timeout, env)
            if status is None:
                return protocol.reply(re_id, status=None, stdout="", stderr=stderr, unreachable=True)
            if len(stdout) > protocol.GIT_OUTPUT_MAX:
                return protocol.refuse(
                    re_id, protocol.ERROR_FAILED, "git's output is too large to carry ({builder})",
                    {"builder": name},
                )
            return protocol.reply(re_id, status=status, stdout=stdout, stderr=stderr, unreachable=False)

        return self._later("git-run", work, re_id)

    def _environment(self, name: str) -> dict[str, str] | None:
        if name == protocol.GIT_ENV_DEFAULT:
            return None
        base = dict(self.core._environment()) if hasattr(self.core, "_environment") else dict(os.environ)
        if name == protocol.GIT_ENV_NO_EDITOR:
            return gitops.no_editor_env(base)
        if name == protocol.GIT_ENV_NO_PROMPT:
            return {**base, "GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
        return None

    # -- git.info --------------------------------------------------------------------------

    def info(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        cwd = self._cwd(message, client)
        if isinstance(cwd, dict):
            return cwd
        wants_changes = bool(message.get("changes"))
        wants_state = bool(message.get("state"))
        known = message.get("known_refs")
        re_id = message.id

        def work() -> dict:
            info = gitfiles.read_info(cwd)
            fields = info.to_fields(known if isinstance(known, str) else None)
            if info.root is not None and wants_changes:
                staged, unstaged = gitinfo.summarize_status(gitfiles.status_porcelain(cwd))
                fields["changes"] = {"staged": staged, "unstaged": unstaged}
            if info.root is not None and wants_state:
                fields["state"] = gitops.tree_state_signature(cwd)
            return protocol.reply(re_id, **fields)

        return self._later("git-info", work, re_id)

    # -- git.sizes -------------------------------------------------------------------------

    def sizes(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        cwd = self._cwd(message, client)
        if isinstance(cwd, dict):
            return cwd
        paths = [p for p in (message.get("paths") or ()) if gitops.safe_path(p)]
        re_id = message.id

        def work() -> dict:
            root = gitfiles.repo_root(cwd)
            base = str(root) if root is not None else cwd
            # A symlink out of the tree is not sized (None, as a file that
            # is gone): the size is as much a leak as the bytes would be.
            inside = [p for p in paths if files.is_inside(base, os.path.join(base, p))]
            sizes = dict.fromkeys(paths)
            sizes.update(gitops.file_sizes(base, inside))
            return protocol.reply(re_id, sizes=sizes)

        return self._later("git-sizes", work, re_id)

    # -- the watch -------------------------------------------------------------------------

    def watch(self, message: protocol.Message, client) -> dict:
        """`git.watch {cwd, handle, files, state}`: a watch of *cwd* under
        the client's *handle* (the page's own name for it: two pages on
        one tree keep two watches, and one page's unwatch never takes the
        other's down). The same handle again replaces that watch."""
        cwd = self._cwd(message, client)
        if isinstance(cwd, dict):
            return cwd
        key = (id(client), str(message.get("handle")))
        old = self._watches.pop(key, None)
        if old is not None:
            old.stop()
        files_ = [p for p in (message.get("files") or ()) if gitops.safe_path(p)]
        seed = message.get("state")
        watch = _Watch(self, cwd, client, files_, seed if isinstance(seed, str) else None)
        self._watches[key] = watch
        watch.start()
        return protocol.reply(message.id)

    def unwatch(self, message: protocol.Message, client) -> dict:
        """`git.unwatch {handle}`: that one watch of the client's, gone."""
        watch = self._watches.pop((id(client), str(message.get("handle"))), None)
        if watch is not None:
            watch.stop()
        return protocol.reply(message.id)

    def watching(self, client, cwd: str) -> bool:
        """Whether any watch of *client* is on *cwd* (the tests' probe)."""
        return any(key[0] == id(client) and watch.cwd == cwd for key, watch in self._watches.items())

    def watch_of(self, client, handle: str) -> _Watch | None:
        return self._watches.get((id(client), handle))

    # -- git.plan --------------------------------------------------------------------------

    def plan(self, message: protocol.Message, client) -> dict | protocol.Deferred:
        cwd = self._cwd(message, client)
        if isinstance(cwd, dict):
            return cwd
        load = message.get("load")
        path = message.get("path")
        previous = message.get("previous_path")
        parent = message.get("parent_target")
        op = message.get("op")
        paths = tuple(message.get("paths") or ())
        patch = message.get("patch")
        keys = tuple(str(k) for k in (message.get("keys") or ()))
        three_way = bool(message.get("three_way"))
        bad = _plan_refusal(load, path, previous, parent, op, paths, patch)
        if bad is not None:
            return protocol.refuse(message.id, protocol.ERROR_INVALID, bad)
        re_id = message.id
        plan = gitpatch.Plan(op, paths, patch, None, "")

        def work() -> dict:
            if op in gitops._APPLY_OPS and keys:
                fresh = gitops.file_patch(cwd, load, path, previous, parent)
                if fresh is None:
                    return protocol.refuse(
                        re_id, protocol.ERROR_FAILED, "git could not re-read {path}", {"path": path}
                    )
                present = set()
                for file in diffmodel.parse(fresh):
                    if file.path == path:
                        present.update(diffmodel.stable_keys(file))
                if any(key not in present for key in keys):
                    return protocol.refuse(
                        re_id, protocol.ERROR_STALE, "{path} changed since the plan was made: reloading",
                        {"path": path},
                    )
            result = gitops.run_plan(cwd, plan, three_way=three_way, trash=files.trash_paths)
            return protocol.reply(
                re_id,
                applied=bool(result.ok),
                stdout=result.stdout[: protocol.GIT_STDERR_MAX],
                stderr=result.stderr[: protocol.GIT_STDERR_MAX],
                unreachable=bool(result.unreachable),
                three_way=bool(result.three_way),
                conflicts=bool(result.conflicts),
            )

        return self._later("git-plan", work, re_id)

    # -- the blob GET ----------------------------------------------------------------------

    def blob(
        self, client, query: str, if_none_match: str | None, respond: Callable[[int, dict, bytes], None]
    ) -> None:
        """`GET /api/blob?kind=git&cwd=&at=&ref=&path=` for *client* (the
        SocketClient the request named, or None): *respond(status,
        headers, body)* is called on the main loop once the read lands."""
        params = {key: values[-1] for key, values in parse_qs(query or "", keep_blank_values=True).items()}
        cwd = files.allowed_cwd(self.core, client, params.get("cwd"))
        at = params.get("at") or gitops.AT_REF
        ref = params.get("ref") or ""
        path = params.get("path") or ""
        if cwd is None:
            self._dispatch(lambda: respond(403, {}, b""))
            return
        if at not in (gitops.AT_WORKTREE, gitops.AT_INDEX, gitops.AT_REF) or not gitops.safe_path(path):
            self._dispatch(lambda: respond(400, {}, b""))
            return
        if at == gitops.AT_REF and not gitloads.safe_ref(ref):
            self._dispatch(lambda: respond(400, {}, b""))
            return

        def work() -> None:
            status, headers, body = read_blob(cwd, at, ref, path, if_none_match)
            self._dispatch(lambda: respond(status, headers, body))

        self._spawn(work, "git-blob")


def _plan_refusal(load, path, previous, parent, op, paths, patch) -> str | None:
    if not gitloads.loaded_ok(load):
        return "load is not one of the git page's loads"
    if not gitops.safe_path(path):
        return "path is not a safe repository path"
    if previous is not None and not gitops.safe_path(previous):
        return "previous_path is not a safe repository path"
    if parent is not None and not gitloads.safe_ref(parent):
        return "parent_target is not a safe ref"
    if op not in gitpatch.OPS:
        return "op is not a plan op"
    if not paths or not all(gitops.safe_path(p) for p in paths):
        return "paths must be safe repository paths"
    if op in gitops._APPLY_OPS and not patch:
        return "an apply needs a patch"
    return None


def run_git_raw(
    cwd: str, argv: Sequence[str], stdin: bytes | None, timeout: float, env: dict[str, str] | None
) -> tuple[int | None, str, str]:
    """`git *argv` in *cwd*, binary, both streams decoded with
    replacement: (exit status or None when git couldn't be run, stdout,
    stderr cut at GIT_STDERR_MAX). Never raises."""
    kwargs = {"env": env} if env is not None else {}
    try:
        result = subprocess.run(
            ["git", *argv], cwd=cwd, input=stdin, capture_output=True, timeout=timeout, **kwargs
        )
    except (OSError, subprocess.SubprocessError) as err:
        return None, "", (str(err) or err.__class__.__name__)[: protocol.GIT_STDERR_MAX]
    stdout = (result.stdout or b"").decode("utf-8", "replace")
    stderr = (result.stderr or b"").decode("utf-8", "replace")[: protocol.GIT_STDERR_MAX]
    return int(result.returncode), stdout, stderr


def blob_tag(cwd: str, at: str, ref: str, path: str) -> str | None:
    """The blob's ETag: the commit and path for a revision (the commit the
    ref names now), the index entry's blob sha for the index, mtime and
    size for the working tree. None when there is nothing to tag (no such
    file, git couldn't say)."""
    root = gitfiles.repo_root(cwd)
    base = str(root) if root is not None else cwd
    if at == gitops.AT_WORKTREE:
        full = os.path.join(base, path)
        if not files.is_inside(base, full):
            return None  # a symlink out of the tree: nothing to tag (read_blob: 404)
        try:
            stat = os.stat(full)
        except OSError:
            return None
        return f'"{stat.st_mtime_ns}-{stat.st_size}"'
    if at == gitops.AT_INDEX:
        result = gitops.run_git(base, ["ls-files", "-s", "--", gitops.literal_pathspec(path)])
        parts = result.stdout.split()
        return f'"{parts[1]}:{path}"' if result.ok and len(parts) >= 3 else None
    sha = gitloads.resolve_commit(base, ref)
    return f'"{sha}:{path}"' if sha else None


def read_blob(cwd: str, at: str, ref: str, path: str, if_none_match: str | None) -> tuple[int, dict, bytes]:
    """The blob GET's answer: (status, headers, body)."""
    tag = blob_tag(cwd, at, ref, path)
    if tag is not None and if_none_match and _tag_matches(if_none_match, tag):
        return 304, {"ETag": tag}, b""
    git_ref: str | None
    if at == gitops.AT_WORKTREE:
        git_ref = None
    elif at == gitops.AT_INDEX:
        git_ref = gitops.INDEX_REF
    else:
        git_ref = ref
    root = gitfiles.repo_root(cwd)
    base = str(root) if root is not None else cwd
    if at == gitops.AT_WORKTREE:
        full = os.path.join(base, path)
        # The path resolved (symlinks followed) must still be in the tree:
        # a link out of it reads as no such file, the size included.
        if not files.is_inside(base, full):
            return 404, {}, b""
        try:
            if os.path.getsize(full) > gitops.MAX_BLOB_BYTES:
                return 413, {}, b""
        except OSError:
            return 404, {}, b""
        data = gitops.file_at(base, None, path)
    else:
        # The index or a revision: git's own read, the size checked here
        # so a blob over the cap is 413 on every side, not 404.
        result = gitops.run_git_blob(base, gitops.file_at_argv(git_ref, path))
        if not result.ok:
            return 404, {}, b""
        if len(result.data) > gitops.MAX_BLOB_BYTES:
            return 413, {}, b""
        data = result.data
    if data is None:
        return 404, {}, b""
    headers = {"Content-Type": _OCTET_STREAM}
    if tag is not None:
        headers["ETag"] = tag
    return 200, headers, data


def _tag_matches(header: str, tag: str) -> bool:
    return any(part.strip() in (tag, "*") for part in header.split(","))


# ---- the watch ------------------------------------------------------------------------


class _Watch:
    """One client's watch of one working tree: the page's monitors,
    debounce, compare and slow tick, here. The first compare seeds the
    signatures without a push; every later move is one `git-changed`."""

    def __init__(
        self, feed: GitFeed, cwd: str, client, paths: Sequence[str], state: str | None = None
    ) -> None:
        self.feed = feed
        self.cwd = cwd
        self.client = client
        self.paths = list(paths)
        self._monitors: list[Gio.FileMonitor] = []
        self._debounce = 0
        self._tick = 0
        self._checking = False
        self._stale = False
        self._stopped = False
        # The client's seed (the tree state its read sampled): the first
        # compare is against it, and only the state is known until then.
        self._seeded = state is not None
        self.last: tuple[str | None, str | None, str | None] = (None, None, state)

    def start(self) -> None:
        root = gitfiles.repo_root(self.cwd)
        base = str(root) if root is not None else self.cwd
        dirs: set[str] = set()
        for path in self.paths:
            dirs.add(os.path.dirname(os.path.join(base, path)))
        if not dirs or len(dirs) > MAX_DIR_MONITORS:
            dirs = {base}
        for directory in sorted(dirs):
            try:
                monitor = Gio.File.new_for_path(directory).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error as exc:
                log.debug("gitfeed: no monitor on %s: %s", directory, exc.message)
                continue
            monitor.connect("changed", self._on_event)
            self._monitors.append(monitor)
        self._tick = GLib.timeout_add_seconds(WATCH_SLOW_TICK_S, self._on_tick)
        self.check()  # the seed

    def stop(self) -> None:
        self._stopped = True
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []
        if self._debounce:
            GLib.source_remove(self._debounce)
            self._debounce = 0
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _on_event(self, _monitor, file: Gio.File, _other, _event) -> None:
        if self._stopped:
            return
        if file is not None and file.get_basename() == ".git":
            return  # its own churn is the tick's business, as on the page
        if self._debounce:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(WATCH_DEBOUNCE_MS, self._fire)

    def _fire(self) -> bool:
        self._debounce = 0
        self.check()
        return GLib.SOURCE_REMOVE

    def _on_tick(self) -> bool:
        if self._stopped:
            return GLib.SOURCE_REMOVE
        self.check()
        return GLib.SOURCE_CONTINUE

    def check(self) -> None:
        if self._stopped:
            return
        if self._checking:
            self._stale = True
            return
        self._checking = True
        cwd = self.cwd

        def work() -> None:
            # A compare that raises still lands (the None triple, as
            # outside a repository), so the watch is never stuck checking.
            try:
                found = signatures(cwd)
            except Exception:
                log.exception("gitfeed: the watch's compare of %s failed", cwd)
                found = (None, None, None)
            self.feed._dispatch(lambda: self._checked(found))

        self.feed._spawn(work, "git-watch")

    def _checked(self, found: tuple[str | None, str | None, str | None]) -> None:
        self._checking = False
        if self._stopped:
            return
        stale, self._stale = self._stale, False
        if not self._seeded:
            self._seeded = True
            self.last = found
        elif self.last[0] is None:
            # Seeded by the client's state alone: the tree and refs digests
            # are adopted now, and only a moved state is a move.
            moved = found[2] != self.last[2]
            self.last = found
            if moved:
                self._deliver(found)
        elif found != self.last:
            self.last = found
            self._deliver(found)
        if stale:
            self.check()

    def _deliver(self, found: tuple[str | None, str | None, str | None]) -> None:
        tree, refs, state = found
        try:
            event = {"t": "git-changed", "cwd": self.cwd, "tree": tree or "", "refs": refs or ""}
            event["state"] = state
            self.client.deliver(event)
        except Exception:
            log.exception("gitfeed: a git-changed delivery failed")


def signatures(cwd: str) -> tuple[str | None, str | None, str | None]:
    """(tree, refs, state) digests of the repository enclosing *cwd*:
    the index's mtime, HEAD and the markers; the refs' mtimes; the
    working tree's state (gitops.tree_state_signature). None each
    outside a repository."""
    git = gitfiles.git_dir(cwd)
    if git is None:
        return None, None, None
    tree = gitfiles.digest(
        (gitfiles.index_mtime(git), gitfiles.head_sha(git), gitfiles.operation_markers(git))
    )
    refs = gitfiles.digest(gitfiles.refs_signature(git))
    return tree, refs, gitops.tree_state_signature(cwd)
