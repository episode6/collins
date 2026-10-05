# Original to the ghackett fork of agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0): this file has no
# upstream version, so it carries no modification notice. Licensed GPL-3.0
# with the rest of the project.

"""The client's half of the session tools (split-service spec §3.7, PR-1.11).

The service dispatches every tool call (`service.tools.SessionTools`: the
identity, the switches, what a sandboxed session is offered, the tools a
session's own data serves). A tool that needs a person's screen reaches
the session's active client as a `tool` event; this module is what the
client does with it: finds the tab whose `Session` the event's `handle`
names, runs the tool's half that touches widgets, and answers with a
`tool-reply` (at once, or when a `mcptools.DeferredResult` it started
resolves). The halves are the bodies `App._mcp_*` had, taking the same
``(window, tab)`` and the service's `sandboxed` reading of the caller:

- `open_in_editor`, `show_image` (the lightbox; remote images fetched on a
  thread), `notify_user` (the delivery table, with this client's focus;
  the row itself is minted by the service through the notification mirror),
- `show_diff` (the git page opened unfocused, the load awaited, the reveal;
  `_ShowDiff`), `diff_context`, `annotate_diff`, `highlight_diff`,
  `clear_diff_marks` (on the page, its load awaited; the marks written
  through to the service's store by the page's mirror),
- `read_terminal`, `run_in_terminal` (the tab's panel shells: the service's ptys,
  where a shell opened for the call is a pty the service spawns and this
  client reveals, never focused),
- `start_session` (a background tab, its prompt and its id, serialized per
  project root; `_BackgroundSpawn`).

A tab is found by its session's handle (`ClientSession.handle`, the
service's name for it), so a session whose id has not resolved yet is
still found. The sibling's sandbox plan a `start_session` from a sandboxed
session inherits is derived on the service (`sandbox.derive`), and let go
of there when the sibling will not launch after all (`sandbox.drop`). GTK
(it drives widgets); never imported by the service.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import replace

from gi.repository import GLib

from . import (
    apilink,
    attachrecords,
    checkouts,
    diffmodel,
    diffnotes,
    editorfiles,
    gitinfo,
    gitloads,
    mcptools,
    notifycenter,
    remoteimages,
)
from .api.protocol import RequestRefused
from .lightbox import present_image_lightbox
from .providers import SessionOptions
from .sessions import worktree_project_root
from .terminal import TerminalTab

log = logging.getLogger(__name__)

# The start_session tool holds its reply until the spawned session has taken
# the prompt and reported its id (see _BackgroundSpawn). This is the deadline
# on that whole wait: past it the call fails rather than hanging, and must come
# in under the CLI's own MCP tool-call timeout (a reply sent after the CLI has
# given up helps no one — the caller sees an opaque "did not respond in time"
# instead of our own honest failure, and loses the id). The tab it opened stays
# — visible, closable, and still resolving in the background — so a late id
# isn't lost, only unreported.
#
# Probed 2026-08-15: that CLI timeout is ~17s (a start_session call returned
# "did not respond in time" 16.9s after it was made). So the earlier 20s
# guess sat *above* it — the CLI always gave up first. Keep a wide margin below
# it, both for the reply to travel back before the cutoff and against a caller
# whose timeout is configured lower. A healthy spawn resolves in a few seconds
# (~5s even through a fresh-worktree launch), well inside this.
_START_SESSION_DEADLINE_MS = 12_000
# How often the spawn polls the fresh terminal for its input box to be ready
# before injecting the prompt (takes_prompt). Nothing can be mid-turn behind a
# brand-new spawn, so a yes is safe the moment the box is drawn.
_START_SESSION_POLL_MS = 300


def _await_page_settled(tab, page, then, what: str | None = None) -> None:
    """Poll *page* (GitPage) until it has settled — no read or reveal in
    flight — then call *then(error)*: None when it did, else the reason it
    never will (the page or its tab closed, the not-a-repo card, the
    deadline). What every diff tool waits on: the page reads its diff on a
    thread, so its public face (card, opening, settled) is read on a
    timeout rather than hooked into. One deadline bounds the wait
    (gitloads.SHOW_DIFF_DEADLINE_S, under the CLI's own MCP timeout);
    whatever happens the page stays open, showing what it shows. *what*
    names the load in the deadline's words when the caller asked for one.
    """
    deadline = time.monotonic() + gitloads.SHOW_DIFF_DEADLINE_S

    def poll() -> bool:
        if tab.get_root() is None or tab.git_page is not page:
            then("The git page closed before the diff loaded")
            return GLib.SOURCE_REMOVE
        if page.card == "not-a-repo" and not page.opening:
            # The card is final only once no open is out: a page that stood
            # on it when the tree turned up (open_git_page's load re-opens
            # the view) shows it until the open's thread lands.
            then("The session's working directory isn't inside a git repository")
            return GLib.SOURCE_REMOVE
        if page.settled():
            then(None)
            return GLib.SOURCE_REMOVE
        if time.monotonic() >= deadline:
            loading = f"finish loading {what}" if what else "settle"
            then(
                f"The git page didn't {loading} in time; it is open in Collins and shows "
                + (page.breadcrumb_text() or "nothing yet")
            )
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    GLib.timeout_add(gitloads.SHOW_DIFF_POLL_MS, poll)


class _ShowDiff:
    """One show_diff tool call in flight: a commit ref resolved to its sha
    on a thread, the git page opened (or fronted, never focused) on the
    load, the page awaited until the load has landed, then — with a file
    named — the view's reveal (synchronous); the deferred reply resolves
    with what the page ended up showing (mcptools.reveal_reply: the load,
    the file's hunk count and the hunk the view landed on), or the first
    reason it couldn't. A failed call is reported, not undone.
    """

    def __init__(self, tab, root: str, loaded, path, side, line, hunk, deferred) -> None:
        self._tab = tab
        self._root = root
        self._loaded = loaded
        self._path = path
        self._side = side or diffmodel.NEW
        self._line = line
        self._hunk = hunk
        self._deferred = deferred
        self._page = None

    def begin(self) -> None:
        ref = gitloads.show_ref(self._loaded)
        if ref is None:
            self._open()
            return
        cwd = self._tab.current_agent_cwd()

        def work() -> None:
            sha = gitloads.resolve_commit(cwd, ref)
            GLib.idle_add(self._resolved, ref, sha, priority=GLib.PRIORITY_DEFAULT)

        threading.Thread(target=work, name="show-diff-rev-parse", daemon=True).start()

    def _resolved(self, ref: str, sha: str | None) -> bool:
        if sha is None:
            self._finish(False, f"No commit named {ref} in {self._root}")
        elif not sha:
            self._finish(False, f"git couldn't be asked about {ref} (is it on PATH?)")
        else:
            self._loaded = {gitloads.SHOW_KEY: sha}
            self._open()
        return GLib.SOURCE_REMOVE

    def _what(self) -> str:
        ref = gitloads.show_ref(self._loaded)
        return f"commit {gitloads.short_ref(ref)}" if ref else f"the {self._loaded} diff"

    def _open(self) -> None:
        tab = self._tab
        if tab.get_root() is None:
            self._finish(False, "That session's tab closed before the diff could be shown")
            return
        # Revealed, never focused: the agent asked, nobody clicked, and the
        # user's keyboard stays where it was (the attachments panel's
        # autodock makes the same choice).
        if not tab.open_git_page(mode=self._loaded, focus=False):
            self._finish(False, "The session's working directory isn't inside a git repository")
            return
        page = tab.git_page
        if page is None:
            self._finish(False, "Collins couldn't open the git page")
            return
        self._page = page
        _await_page_settled(tab, page, self._settled, what=self._what())

    def _settled(self, error: str | None) -> None:
        if error is not None:
            self._finish(False, error)
            return
        page = self._page
        if not page.shows(self._loaded):
            hint = ""
            if self._loaded == "branch":
                hint = (
                    " — no parent branch resolves for this branch; the user can "
                    "set one in Preferences → Git"
                )
            self._finish(
                False,
                f"The git page couldn't load {self._what()}{hint}; it shows " + page.breadcrumb_text(),
            )
            return
        try:
            self._reveal()
        except Exception:  # never leave the shim waiting on a widget's surprise
            logging.getLogger(__name__).exception("show_diff: reveal failed")
            self._finish(False, "Collins couldn't reveal that in the git page")

    def _reveal(self) -> None:
        page = self._page
        breadcrumb = page.breadcrumb_text()
        path, side = self._path, self._side
        if path is None:
            self._finish(True, mcptools.reveal_reply(breadcrumb))
            return
        file = diffmodel.find_file(page.context().files, path, side)
        refusal = None
        if file is None:
            refusal = f"{path} isn't in that diff"
        elif self._hunk is not None:
            refusal = mcptools.hunk_refusal(path, self._hunk, file)
        if refusal is None and not page.reveal(
            path, hunk=None if self._hunk is None else self._hunk - 1, side=side, line=self._line, focus=False
        ):
            refusal = f"{path} isn't in that diff"
        if refusal is not None:
            self._finish(False, f"The git page loaded {self._what()}, but {refusal}")
            return
        view = page.diff_view
        holds = self._line is None or view.holds_line(path, side, self._line)
        self._finish(
            True,
            mcptools.reveal_reply(
                breadcrumb, path, side, self._line, self._hunk, file, view.current()[1], holds
            ),
        )

    def _finish(self, ok: bool, text: str) -> None:
        self._deferred.resolve(ok, text)


def _drop_sibling_box(plan: str, box: str) -> None:
    """A start_session sibling that will not launch after all: the service
    releases the plan derived for it (it describes a box in full) and lets
    go of the box minted with it, which goes — with the grants recorded for
    it — when nothing else needs it (`sandbox.drop`)."""
    if not box:
        return
    message: dict = {"t": "sandbox.drop", "box": box}
    if plan:
        message["plan"] = plan
    try:
        apilink.call(message)
    except RequestRefused as refusal:
        log.info("sandbox.drop %s refused: %s", box, refusal.msgid)


class _BackgroundSpawn:
    """One start_session tool call in flight: spawn a background session,
    submit its prompt once the box is ready, and resolve the deferred reply
    with the id (or a reason it couldn't).

    Ordered exactly as the spec's "Resolving the id" requires — poll
    takes_prompt, inject, *then* wait for session-resolved — because a
    brand-new session's transcript, and so its id, only appears after the
    first prompt is submitted. A single deadline covers the whole wait; a
    process that exits on launch fails the call in a second rather than
    twenty. Whatever the outcome, the tab and its sidebar row stay: a failed
    call is unreported, not undone.

    These run one at a time per project root (App._start_session_advance): two
    fresh spawns polling the same cwd both baseline the transcripts present at
    arm time and each treats any new arrival as its own, so a back-to-back
    pair — the tool's normal case — could bind to each other's session for
    life. Serializing means the second's baseline already contains the first's
    resolved transcript.
    """

    def __init__(
        self, window, cwd, provider, options, worktree, prompt, deferred, on_done
    ) -> None:
        self._window = window
        self._cwd = cwd
        self._provider = provider
        self._options = options
        self._worktree = worktree
        self._prompt = prompt
        self._deferred = deferred
        self._on_done = on_done
        self._tab: TerminalTab | None = None
        self._deadline_source: int | None = None
        self._poll_source: int | None = None
        self._exit_handler: int | None = None
        self._resolved_handler: int | None = None
        self._finished = False

    def begin(self) -> None:
        """Start the spawn. A worktree launch needs to know whether the
        project is a git checkout, which is the service's answer
        (`checkouts.ask`, off the main loop, so the tab opens when it
        lands); a launch that will not use a worktree (said so, or left it
        to a project default that is off) asks nothing and opens its tab
        at once."""
        wanted = self._worktree
        if wanted is None:
            wanted = self._window._worktree_for_new_session(self._cwd)
        if wanted:
            checkouts.ask(self._cwd, self._begin)
        else:
            self._begin(False)

    def _begin(self, is_git: bool) -> None:
        if self._finished:
            return
        tab = self._window.start_background_session(
            self._cwd, self._provider, self._options, self._worktree, is_git=is_git
        )
        if tab is None:  # trust is a refusal here, never a dialog over the user
            # A plan derived for the sibling has no tab to adopt it, and
            # the box minted with it no session to live in.
            _drop_sibling_box(self._options.sandbox_plan, self._options.sandbox_box)
            self._finish(
                False,
                f"Collins hasn't been trusted to run agents in {self._cwd}; open "
                "a session there once by hand and it will be.",
            )
            return
        self._tab = tab
        self._exit_handler = tab.connect("process-exited", self._on_exited)
        self._resolved_handler = tab.connect("session-resolved", self._on_resolved)
        self._deadline_source = GLib.timeout_add(
            _START_SESSION_DEADLINE_MS, self._on_deadline
        )
        self._poll_source = GLib.timeout_add(_START_SESSION_POLL_MS, self._poll_prompt)

    def _poll_prompt(self) -> bool:
        if self._finished:
            return GLib.SOURCE_REMOVE
        tab = self._tab
        if tab.get_root() is None:  # the user closed the tab before we injected
            self._finish(False, "The session's tab was closed before it could start.")
            return GLib.SOURCE_REMOVE
        if not tab.takes_prompt():
            return GLib.SOURCE_CONTINUE
        if not tab.inject_prompt_unfocused(self._prompt, when_empty=True):
            return GLib.SOURCE_CONTINUE  # the live box wasn't empty after all: next tick
        self._poll_source = None
        # From here the id arrives on session-resolved (connected in begin) —
        # never synchronously: it comes from a transcript this submit only now
        # creates, which the resolver finds on a later poll.
        return GLib.SOURCE_REMOVE

    def _on_resolved(self, tab, session_id: str) -> None:
        if self._finished:
            return
        # By now the resolver has followed the agent into any worktree it moved
        # to (that is how it found the transcript), so the live cwd is the
        # session's real directory — and whether it differs from the launch dir
        # is whether a worktree was actually used, however `worktree` resolved.
        actual = tab.current_agent_cwd() or self._cwd
        if os.path.realpath(actual) != os.path.realpath(self._cwd):
            text = f"Started session {session_id} in a fresh worktree at {actual}."
        elif self._worktree:  # asked for, but silently dropped outside a checkout
            text = (
                f"Started session {session_id} in {actual} — a worktree was "
                "requested but this isn't a git checkout, so it shares the tree."
            )
        else:
            text = f"Started session {session_id} in {actual}."
        self._finish(True, text)

    def _on_exited(self, _tab, status: int) -> None:
        if self._finished:
            return
        self._finish(
            False,
            f"The session exited on launch (status {status}) before it could "
            "start; its tab is still open in Collins.",
        )

    def _on_deadline(self) -> bool:
        self._deadline_source = None
        if not self._finished:
            self._finish(
                False,
                "The session didn't report its id within the time limit. Its tab "
                "is open in Collins and will keep trying to resolve.",
            )
        return GLib.SOURCE_REMOVE

    def _finish(self, ok: bool, text: str) -> None:
        if self._finished:
            return
        self._finished = True
        tab = self._tab
        if tab is not None:
            if self._exit_handler is not None:
                tab.disconnect(self._exit_handler)
            if self._resolved_handler is not None:
                tab.disconnect(self._resolved_handler)
        for source in (self._deadline_source, self._poll_source):
            if source is not None:
                GLib.source_remove(source)
        self._deadline_source = self._poll_source = None
        self._deferred.resolve(ok, text)
        # Let the next spawn for this root go. On a deadline the tab's resolver
        # is still live, so a genuinely concurrent spawn could still race it —
        # but a deadline means something already went wrong (20s with no id),
        # and the back-to-back success case this serialization is for resolves
        # in seconds, well ahead of it.
        self._on_done()


class ToolClient:
    """The client's end of the `tool` events (see the module docstring).
    *app* is the App: its windows are where the tabs are."""

    def __init__(self, app, link) -> None:
        self._app = app
        self._link = link
        # start_session spawns, serialized per project root so a back-to-back
        # pair can't race each other's transcript (_BackgroundSpawn): root ->
        # queue of pending spawns, its head the one running. The app's own
        # attribute, shared: the spawns are its windows' tabs, and it is
        # where they were always kept (the e2e checks read it there).
        if not isinstance(getattr(app, "_start_session_chains", None), dict):
            app._start_session_chains = {}
        self._start_session_chains: dict[str, deque] = app._start_session_chains
        if link is not None:
            link.on("tool", self._on_tool)

    # -- finding the caller's tab -------------------------------------------------

    def _main_windows(self) -> list:
        from .window import MainWindow

        return [w for w in self._app.get_windows() if isinstance(w, MainWindow)]

    def found_for(self, session):
        """The window and tab holding *session* (a `service.session.Session`
        of the service's, by its handle), or None: what every half below
        takes."""
        return self.found_for_handle(getattr(session, "handle", "") or "")

    def found_for_handle(self, handle: str):
        if not handle:
            return None
        for window in self._main_windows():
            for i in range(window.tab_view.get_n_pages()):
                tab = window.tab_view.get_nth_page(i).get_child()
                if isinstance(tab, TerminalTab) and tab.session.handle == handle:
                    return window, tab
        return None

    # -- the events ------------------------------------------------------------

    def _on_tool(self, event: dict) -> None:
        call_id = event.get("call", "")
        name = event.get("name", "")
        args = event.get("arguments") or {}
        sandboxed = bool(event.get("sandboxed"))
        # The arguments came over the wire: the schema again (rule 5).
        error = mcptools.validate_args(name, args)
        if error is not None:
            self._reply(call_id, False, error)
            return
        found = self.found_for_handle(event.get("handle") or "")
        handler = getattr(self, name, None) if name in mcptools.tool_names() else None
        if found is None or handler is None:
            self._reply(call_id, False, mcptools.NOT_FROM_TAB_ERROR)
            return
        try:
            result = handler(found, args, sandboxed)
        except Exception:
            log.exception("tools: the %s half failed", name)
            result = (False, f"Collins couldn't run {name}")
        if isinstance(result, mcptools.DeferredResult):
            result.watch(lambda ok, text: self._reply(call_id, ok, text))
        else:
            self._reply(call_id, *result)

    def _reply(self, call_id: str, ok: bool, text: str) -> None:
        if self._link is not None:
            self._link.send_event({"t": "tool-reply", "call": call_id, "ok": bool(ok), "text": str(text)})

    # -- the halves -------------------------------------------------------------

    @staticmethod
    def resolve_file(tab, raw: str) -> str | None:
        """The existing file a tool's path argument names, or None.

        Relative paths try the running agent's cwd first (it may have cd'd
        into a worktree), then the tab's project root — the same order
        clickable file references resolve in (terminal._reference_roots).
        """
        expanded = os.path.expanduser(raw)
        if os.path.isabs(expanded):
            trials = [expanded]
        else:
            roots = [tab.current_agent_cwd(), tab.editor_root]
            trials = [os.path.join(root, expanded) for root in roots if root]
        for trial in trials:
            if os.path.isfile(trial):
                return os.path.normpath(trial)
        return None

    def open_in_editor(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        """Whether the file is inside the session's project is the service's
        answer (`TerminalTab.ask_can_open_in_editor`, PR-2.4), so the reply
        waits for it (mcptools.DeferredResult)."""
        window, tab = found
        path = self.resolve_file(tab, args["path"])
        if path is None:
            return False, f"No such file: {args['path']}"
        line = args.get("line")
        deferred = mcptools.DeferredResult()

        def landed(inside: bool) -> None:
            if tab.get_root() is None:
                deferred.resolve(False, "That session's tab closed before the file could open")
            elif not inside:
                deferred.resolve(False, "That file is outside this session's project")
            else:
                window.open_in_tab_editor(tab, path, [line - 1, 0] if line else None)
                deferred.resolve(True, "Opened in the editor.")

        tab.ask_can_open_in_editor(path, landed)
        return deferred

    def show_diff(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        """Open the session's git page on a diff, and point it at a file.

        Everything an obviously-bad call can be refused for is checked here,
        synchronously: no repository under the agent, a `what` that is
        neither a mode nor a ref, a `file` that can't be a path in the
        diff, a `line` with no file. The rest — a commit ref checked
        against the repository (one git call, on a thread), the page opened
        and its load landed (the diff read on a thread), then the view's
        reveal — is _ShowDiff's, and the reply waits for it
        (mcptools.DeferredResult), so the agent reads back what actually
        loaded.
        """
        _window, tab = found
        cwd = tab.current_agent_cwd()
        root = gitinfo.repo_root(cwd)
        if root is None:
            return False, "The session's working directory isn't inside a git repository"
        loaded = gitloads.show_diff_load(args["what"])
        if loaded is None:
            return False, (
                f"'what' must be unstaged, staged, branch, or a commit ref: {args['what']!r}"
            )
        path = None
        if "file" in args:
            path = gitloads.diff_file_path(args["file"], str(root), cwd)
            if path is None:
                return False, f"'file' must be a path inside the repository: {args['file']!r}"
        else:
            for key in ("line", "hunk", "side"):
                if key in args:
                    return False, f"'{key}' needs 'file'"
        if "line" in args and "hunk" in args:
            return False, "'line' and 'hunk' are exclusive: give one"
        deferred = mcptools.DeferredResult()
        _ShowDiff(
            tab, str(root), loaded, path, args.get("side"), args.get("line"), args.get("hunk"), deferred
        ).begin()
        return deferred

    # -- the other diff tools: the page must be open; a load in flight is waited for --

    @staticmethod
    def _on_diff_page(tab, act) -> mcptools.ToolResult:
        """Run *act(page)* → (ok, text) on the tab's git page once it has
        settled. The page must be open (show_diff opens it — the refusal
        says so); a load in flight (the watch or the footer's tick may
        have just started one, and a reveal may be waiting on it) is
        waited for behind a DeferredResult, which always resolves."""
        page = tab.git_page
        if page is None or not (page.opened or page.opening):
            return False, mcptools.PAGE_NOT_OPEN
        if page.settled():
            return act(page)
        deferred = mcptools.DeferredResult()

        def then(error: str | None) -> None:
            if error is not None:
                deferred.resolve(False, error)
                return
            try:
                deferred.resolve(*act(page))
            except Exception:
                logging.getLogger(__name__).exception("diff tool failed on the git page")
                deferred.resolve(False, "Collins couldn't act on the git page")

        _await_page_settled(tab, page, then)
        return deferred

    @staticmethod
    def _diff_path_resolver(tab, page):
        """The tool's file → repo-relative path (gitloads.diff_file_path)
        against the repository *page* shows — not the tab's live cwd,
        which an agent may have `cd`ed out of since show_diff — or None
        while the page is on its card. The cwd only breaks a tie for a
        relative path that exists there and not under the root."""
        root = page.repo_root
        if root is None:
            return None
        return lambda raw: gitloads.diff_file_path(raw, str(root), tab.current_agent_cwd())

    def diff_context(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        _window, tab = found

        def act(page) -> tuple[bool, str]:
            return True, mcptools.diff_context_reply(
                page.context(),
                files=args.get("files", True),
                patch=args.get("patch", False),
                notes=args.get("notes", False),
            )

        return self._on_diff_page(tab, act)

    def annotate_diff(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        _window, tab = found

        def act(page) -> tuple[bool, str]:
            resolve = self._diff_path_resolver(tab, page)
            if resolve is None:
                return False, mcptools.PAGE_NOT_OVER_A_REPO
            specs = mcptools.note_specs(args["notes"], resolve)
            if isinstance(specs, str):
                return False, f"No notes added: {specs}"
            # The batch lands whole or not at all (diffnotes.MarkStore): the
            # reason names the first address the loaded diff doesn't carry.
            added = page.add_notes(specs, focus=bool(args.get("focus")), source=diffnotes.AGENT)
            if isinstance(added, str):
                return False, f"No notes added: {added}"
            return True, mcptools.annotate_reply(added)

        return self._on_diff_page(tab, act)

    def highlight_diff(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        _window, tab = found

        def act(page) -> tuple[bool, str]:
            resolve = self._diff_path_resolver(tab, page)
            if resolve is None:
                return False, mcptools.PAGE_NOT_OVER_A_REPO
            specs = mcptools.highlight_specs(args["marks"], resolve)
            if isinstance(specs, str):
                return False, f"No highlights added: {specs}"
            added = page.add_highlights(specs, focus=bool(args.get("focus")))
            if isinstance(added, str):
                return False, f"No highlights added: {added}"
            return True, mcptools.highlight_reply(added)

        return self._on_diff_page(tab, act)

    def clear_diff_marks(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        _window, tab = found

        def act(page) -> tuple[bool, str]:
            path = None
            if "file" in args:
                resolve = self._diff_path_resolver(tab, page)
                path = resolve(args["file"]) if resolve is not None else None
                if path is None:
                    return False, f"'file' must be a path inside the repository: {args['file']!r}"
            targets = mcptools.clear_targets(args)
            if isinstance(targets, str):
                return False, targets
            notes, highlights = targets
            gone_notes = gone_highlights = None
            if notes:
                gone_notes = page.clear_marks(path, notes=True, include_user=bool(args.get("user")))
            if highlights:
                gone_highlights = page.clear_marks(path, highlights=True)
            return True, mcptools.clear_reply(gone_notes, gone_highlights, path)

        return self._on_diff_page(tab, act)

    def show_image(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        window, tab = found
        raw = args["path"]
        if remoteimages.looks_remote(raw):
            return self._show_remote_image(window, tab, raw, args.get("caption"))
        path = self.resolve_file(tab, raw)
        if path is None:
            return False, f"No such file: {raw}"
        if not editorfiles.is_image_path(path):
            return False, f"Not an image Collins can display: {raw}"
        # Project membership (the "Open in Editor" button) is the service's
        # answer (PR-2.4): the reply waits for it.
        deferred = mcptools.DeferredResult()
        caption = args.get("caption")

        def landed(inside: bool) -> None:
            if tab.get_root() is None:
                deferred.resolve(False, "That session's tab closed before the image could show")
                return
            try:
                deferred.resolve(*self._present_image(window, tab, path, caption, raw, can_edit=inside))
            except Exception:  # noqa: BLE001 - the reply must land regardless
                logging.getLogger(__name__).exception("show_image lightbox failed")
                deferred.resolve(False, f"Collins couldn't show {raw}")

        tab.ask_can_open_in_editor(path, landed)
        return deferred

    def _present_image(
        self, window, tab, path: str, caption: str | None, origin: str, can_edit: bool = False
    ) -> tuple[bool, str]:
        """Float *path* over *tab*'s window, the way a clicked image
        reference does (terminal._present_image): the lightbox shows any
        readable image, project membership (*can_edit*, the service's
        answer; never for a remote image's copy, which is this device's
        cache) only gates its "Open in Editor" button. *origin* is what the
        agent asked for — the file it named, or the URL the copy came from —
        which is what a failed decode names on the status page rather than
        the cache file nobody chose."""
        on_open = (lambda: window.open_in_tab_editor(tab, path)) if can_edit else None
        # The one place every `show_image` passes through, local and remote
        # alike, and the only one that has the agent's own caption in hand.
        # A remote image is written down under its URL, never under *path* —
        # that is the cache copy, and the cache is pruned after a day.
        tab.record_attachment(
            origin if attachrecords.is_remote(origin) else path,
            caption=caption,
            origin=origin,
        )
        present_image_lightbox(
            tab,
            path,
            can_open_in_editor=can_edit,
            on_open_in_editor=on_open,
            caption=caption,
            origin=origin,
        )
        return True, "Image shown."

    def _show_remote_image(
        self, window, tab, url: str, caption: str | None
    ) -> mcptools.ToolResult:
        """`show_image` given an http(s) URL: fetch it, then show the copy.

        The download runs on a worker thread and the session's reply waits
        for it (mcptools.DeferredResult) — a blocking fetch on the main loop
        would freeze the window for as long as the server felt like taking.
        The thread only fetches; the widget half runs back on the main loop
        (GLib.idle_add), where the tab may by then be gone.
        """
        error = remoteimages.url_error(url)
        if error is not None:
            return False, error
        deferred = mcptools.DeferredResult()
        directory = remoteimages.default_directory()

        def fetched(path: str | None, failure: str | None) -> bool:
            if failure is not None:
                deferred.resolve(False, failure)
            elif tab.get_root() is None:  # the tab closed while we fetched
                deferred.resolve(
                    False, "That session's tab closed before the image arrived"
                )
            else:
                try:
                    deferred.resolve(
                        *self._present_image(window, tab, path, caption, url)
                    )
                except Exception:  # noqa: BLE001 - the reply must land regardless
                    # An unresolved call is a connection that never speaks
                    # again, so the session would hang on it until its own
                    # timeout; answer, then let the log carry the details.
                    logging.getLogger(__name__).exception("show_image lightbox failed")
                    deferred.resolve(False, f"Collins couldn't show {url}")
            return GLib.SOURCE_REMOVE

        def download() -> None:
            try:
                path = remoteimages.fetch_to_file(url, directory)
            except remoteimages.FetchError as failure:
                GLib.idle_add(fetched, None, str(failure))
            else:
                GLib.idle_add(fetched, str(path), None)

        threading.Thread(target=download, name="show-image-fetch", daemon=True).start()
        return deferred

    def notify_user(self, found, args: dict, sandboxed: bool = False) -> tuple[bool, str]:
        """The reply says where the message went — a card in Collins, the
        desktop, or straight into the history because the user is looking
        at this very session — since the model can only know by asking
        (see MainWindow.notify_session and notifycenter.tool_reply)."""
        window, tab = found
        deliveries = window.notify_session(tab, args["message"])
        if deliveries is None:
            return False, "Collins couldn't post a notification"
        return True, notifycenter.tool_reply(deliveries)

    def read_terminal(self, found, args: dict, sandboxed: bool = False) -> tuple[bool, str]:
        """Hand the agent its session's terminal-panel shells, as text: each
        one's scrollback tail under a header naming it. The dump is VTE's
        own (capture_contents — what panel history saves), or on the server
        backend the service's screen model of the shell's pty (the shell
        routes it, as it routes its busy read to the pty server), read here
        on the main loop like every dispatch, so the screen can't change mid-read;
        mcptools.terminal_reply does the tailing and keeps the reply inside
        the socket's frame limit. A sandboxed session reads only the
        shells running inside its box (mcptools.tool_shells): the user's
        own shell's scrollback is host output, and one left running in the
        box before a *Restart to apply* is no longer the session's own."""
        _window, tab = found
        visible = mcptools.tool_shells(tab.panel_shells(), sandboxed, tab.sandbox_plan_path)
        shells = visible
        if not shells:
            if sandboxed:
                return True, "No sandboxed shells are open in this session."
            return True, "No terminal-panel tabs are open in this session."
        wanted = args.get("terminal")
        if wanted is not None:
            shells = [shell for shell in shells if shell.number == wanted]
            if not shells:
                numbers = ", ".join(str(s.number) for s in visible)
                kind = "sandboxed shell" if sandboxed else "terminal"
                return False, f"No {kind} numbered {wanted} — open: {numbers}"
        sections = [
            (
                shell.number,
                shell.has_running_command(),
                shell.capture_contents(),
                "Sandboxed shell" if getattr(shell, "sandboxed", False) else "Terminal",
            )
            for shell in shells
        ]
        lines = args.get("lines", mcptools.TERMINAL_DEFAULT_LINES)
        return True, mcptools.terminal_reply(sections, lines)

    def run_in_terminal(self, found, args: dict, sandboxed: bool = False) -> tuple[bool, str]:
        """Type a command into one of the session's panel shells — an idle
        one, never a busy one (its stdin belongs to the running program),
        opening a fresh tab when there is nothing idle to type into. The
        target is revealed but never focused: the user must see what the
        agent runs, and must not have their keyboard moved by it. A
        sandboxed session types only into a shell running inside its box
        (mcptools.tool_shells), opening one when none is idle — never into
        the user's own unconfined shell. The shell
        is a pty of the service and the command reaches it as input through
        the service (PanelTerminal.run_command), its busy read is the pty
        server's, and a shell opened for it is spawned there."""
        _window, tab = found
        wanted = args.get("terminal")
        opened = False
        shells = mcptools.tool_shells(tab.panel_shells(), sandboxed, tab.sandbox_plan_path)
        kind = "Sandboxed shell" if sandboxed else "Terminal"
        if wanted is not None:
            target = next((s for s in shells if s.number == wanted), None)
            if target is None:
                numbers = ", ".join(str(s.number) for s in shells)
                return False, f"No {kind.lower()} numbered {wanted} — open: {numbers or 'none'}"
            if target.has_running_command():
                return False, (
                    f"{kind} {wanted} is busy running a command — pick an "
                    "idle one, or omit 'terminal' to open a new tab"
                )
        else:
            target = next((s for s in shells if not s.has_running_command()), None)
            if target is None:
                target = tab.open_panel_shell(sandboxed=sandboxed)
                opened = True
            if target is None:
                return False, "Collins couldn't open a terminal in this session"
        tab.reveal_panel_shell(target)
        target.run_command(args["command"])
        prefix = "Running in new" if opened else "Running in"
        return True, f"{prefix} {kind} {target.number}."

    def start_session(self, found, args: dict, sandboxed: bool = False) -> mcptools.ToolResult:
        """Spawn a sibling session in a background tab and hand it a prompt.

        The reply is deferred (mcptools.DeferredResult): the model gets a real
        session id, not a promise, so the whole spawn → submit → resolve dance
        runs before this returns — bounded by a deadline, and driven by
        _BackgroundSpawn. Everything up to the spawn is validated synchronously
        here so an obviously-bad call fails fast and cheap. The launch dir is
        collapsed to the project root (never a nested worktree) before the
        spawn — see the cwd normalization below.
        """
        window, tab = found
        provider = tab.provider
        # Every provider that serves the tools can spawn (--mcp-config is
        # unconditional), so this is belt-and-braces — but a provider that
        # can't hand the sibling the tools shouldn't pretend to.
        if not getattr(provider, "supports_mcp_config", False):
            return False, "This session's agent can't start Collins sessions."

        raw_cwd = args.get("cwd")
        if raw_cwd:
            cwd = os.path.abspath(os.path.expanduser(raw_cwd))
            if not os.path.isdir(cwd):
                return False, f"No such directory: {raw_cwd}"
        else:
            # The agent's live cwd — a worktree, once the CLI has moved — the
            # same root open_in_editor resolves relative paths against.
            cwd = tab.current_agent_cwd()
            if not cwd:
                return False, "Couldn't work out a directory to start the session in."
        # New sessions belong in the project proper, never inside an existing
        # worktree: launching from `<repo>/.claude/worktrees/<name>` roots the
        # fresh spawn there, and the transcript resolver — which baselines and
        # follows relative to the launch dir — mismaps the tab, so the id never
        # comes back. The foreground new-session path collapses the same way
        # (see window._visible_project_dir); mirror it here. No-op (returns
        # None) for any cwd that isn't a Claude-managed worktree.
        cwd = worktree_project_root(cwd) or cwd

        # Whether the sibling runs inside a sandbox: always when its parent
        # does — an unsandboxed sibling from a sandboxed parent is never
        # possible (mcptools.sibling_sandboxed) — and otherwise per the
        # project's default for new sessions, as any new session follows
        # it. A sandboxed sibling is what bypassPermissions is for — the box
        # bounds it, not the prompt — so bypass is granted to one, explicit
        # or inherited. *sandboxed* here is run_tool_call's reading of the
        # calling tab, never the caller's word.
        sandboxed = mcptools.sibling_sandboxed(sandboxed, window._sandbox_for_new_session(cwd))
        parent_sandboxed = tab.sandboxed
        if parent_sandboxed:
            # The parent's box again, around a home of the sibling's own:
            # its plan re-issued for the sibling's directory and box
            # (sandboxplan.derive_plan, on the service: `sandbox.derive`) —
            # the same workspace, grants, shares and settings protection it
            # was *launched* with, whatever the switches say now, and never
            # a directory the parent holds only live. The tool takes nothing
            # that can loosen it, and a cwd the box doesn't reach — an
            # agent asking for a sibling in ~/.ssh — is refused here.
            if not tab.sandbox_box:
                return False, mcptools.sibling_cwd_refusal(
                    "the parent session's sandbox plan isn't available"
                )
            try:
                derived = apilink.call(
                    {"t": "sandbox.derive", "box": tab.sandbox_box, "handle": tab.session.handle, "cwd": cwd}
                )
            except RequestRefused as refusal:
                return False, mcptools.sibling_cwd_refusal(refusal.msgid or "the sandbox host refused")
            sibling_plan, sibling_box = derived.get("plan"), derived.get("box") or ""
            if not sibling_plan:
                return False, mcptools.sibling_cwd_refusal(derived.get("reason") or "")
        else:
            sibling_plan = sibling_box = ""

        mode = args.get("permission_mode")
        if mode:
            # The provider's own modes, minus bypass: handing a sibling
            # bypassPermissions is privilege the user never saw, and the only
            # human gate on this call is the caller's own MCP permission prompt.
            allowed = {value for value, _label in provider.permission_modes()}
            if not sandboxed:
                allowed.discard("bypassPermissions")
            if mode not in allowed:
                # Every refusal past the derive releases the sibling's plan
                # file and its box: nothing will launch from them, and the
                # plan describes a box in full.
                _drop_sibling_box(sibling_plan, sibling_box)
                if mode == "bypassPermissions":
                    return False, (
                        "start_session won't grant bypassPermissions to a spawned "
                        "session."
                    )
                return False, (
                    f"permission_mode must be one of: {', '.join(sorted(allowed))}."
                )
        else:
            # No explicit choice: the sibling works the way its spawner does,
            # so it inherits the caller's *current* mode — the one live in the
            # CLI now (shift+tab changes included), read off its transcript —
            # not whatever flag this tab launched with. bypassPermissions is
            # capped, junk is dropped; see inherited_permission_mode.
            mode = mcptools.inherited_permission_mode(
                tab.current_permission_mode(), sandboxed=sandboxed
            )

        model = args.get("model")
        if model:
            if not mcptools.valid_model(model):
                _drop_sibling_box(sibling_plan, sibling_box)
                return False, (
                    "model must be a CLI alias (opus, sonnet, haiku) or a full "
                    "model id."
                )
        else:
            # No explicit choice: the sibling runs on what its spawner runs on
            # *now* — the model of the caller's last reply, read off its
            # transcript — not whatever flag this tab launched with, and not
            # the CLI's configured default, which /model may have left behind.
            model = mcptools.inherited_model(tab.current_model())

        effort = args.get("effort")
        if not effort:
            # The schema's enum already refuses a level the CLI doesn't name,
            # so an explicit pick passes straight through; without one the
            # sibling answers at the effort its spawner is answering at *now*
            # — the level stamped on the caller's last reply, /effort switches
            # included — not whatever flag this tab launched with. See
            # inherited_effort.
            effort = mcptools.inherited_effort(tab.current_effort())

        worktree = args.get("worktree")  # bool, or None to use the project default
        options = SessionOptions(model=model, effort=effort, permission_mode=mode or "")
        if sandboxed:
            options = window._sandboxed_options(options)
        if sibling_plan:
            # The derived plan and its box travel with the options; the
            # sibling's tab adopts them at spawn and releases them when its
            # shell exits.
            options = replace(options, sandbox_plan=sibling_plan, sandbox_box=sibling_box)
        # A missing CLI drops the new tab to a plain shell the takes_prompt poll
        # could never say yes to — a leaked shell, not a session. Refuse before
        # anything is spawned.
        if provider.new_command(options) is None:
            _drop_sibling_box(sibling_plan, sibling_box)
            return False, f"The {provider.name} CLI isn't available to start a session."

        deferred = mcptools.DeferredResult()
        # cwd is already collapsed to the project root above, so this is just
        # the per-project serialization key for the spawn queue.
        root = os.path.realpath(cwd)
        spawn = _BackgroundSpawn(
            window, cwd, provider, options, worktree, args["prompt"], deferred,
            on_done=lambda: self._start_session_advance(root),
        )
        self._start_session_chains.setdefault(root, deque()).append(spawn)
        if len(self._start_session_chains[root]) == 1:
            spawn.begin()  # nothing ahead of it; the queue is otherwise idle
        return deferred

    def _start_session_advance(self, root: str) -> None:
        """A spawn for *root* finished: drop it and start the next one waiting."""
        queue = self._start_session_chains.get(root)
        if not queue:
            return
        queue.popleft()  # the spawn that just finished, always the head
        if queue:
            queue[0].begin()
        else:
            del self._start_session_chains[root]
