# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page: hunk in a VTE beside the session, driven over its session API.

What the page shows is hunk's — hunk.dev, the terminal diff viewer, running
with the collins-git extension Collins ships as package data, which draws
the commits and files panels and binds the staging keys — and all Collins
adds is a one-row header (the branch, a breadcrumb of what is loaded,
refresh and close) and the plumbing that keeps hunk pointed at the right
thing: it spawns `hunk diff --watch --extension <collins-git>` in the
agent's working tree the first time the page is shown, finds the session
hunk registered for that process (`hunk session list --json`, matched by
pid — the npm wrapper spawns the real viewer as a child, so both pids are
tried), and swaps what is loaded with `hunk session reload <id> -- diff …`
(or `-- show <ref>`) whenever Ctrl+1/2/3 or the host asks for another load:
the unstaged working tree, the index, the branch against its parent, or a
commit. When the session id can't be had (an old hunk, a daemon that never
answered), every switch is a respawn instead — slower, never wrong.

The extension does its own loads — a click on a commit, a branch header, the
Staged section — through the same session API, and the page learns of them
the way it learns of any load it didn't make: freshness rides the tab
footer's 2 s tick, the host forwards it while the page is mapped, and the
page reloads what it shows when the index, HEAD or the parent branch moved
(gitinfo.tree_signature) — hunk's own `--watch` covers edits to files, this
covers commits and staging done from a shell or by the agent. The same tick
asks `hunk session get <id>` what the viewer has loaded, and the breadcrumb
and tab title follow *that* rather than Collins' own asks: a `show <sha>`
becomes the page's commit load (reloaded on freshness, persisted, restored
into `hunk show` — after the probe checked the commit still exists, else
the default mode), its breadcrumb naming the commit (`<sha7> <subject>`,
the subject read by the worker thread that brought the title back, one
`git log -1`, see hunkctl.commit_subject), and a load Collins has no name
for (a range between two branches) is shown by hunk's title, left alone by
the freshness reload, and reclaimed by the next Ctrl+1/2/3. A reload hunk
refuses (a parent ref that
vanished between the header and the ask, a timeout) keeps the viewer — it is
still showing what it showed — and only a reply saying the session itself is
gone respawns. Everything that leaves the main loop (the version probe, the
session list, the get, the reload) runs on a daemon thread and comes back
through GLib.idle_add behind a generation counter, so a reply to a spawn
that has since been replaced changes nothing — except a spawn's own
callback, which terminates the child it announces when nobody wants it any
more (the page closed, or was unparented, while the spawn was in flight),
since its pty is the only one the viewer will ever answer a signal on.

The parent branch travels both ways through a sidecar file (hunkctl's
sidecar_*): Collins writes the name it resolved — the newest PR's base, else
the default branch, else what the user set — and the default branch, so the
extension groups its commits the same way the breadcrumb reads; the
extension writes the user's "Set parent branch…" pick back, and the tick
picks it up (one os.stat, a read when the mtime moved), re-resolves, and
reloads a branch diff that now has another base. The user's pick persists
with the page (page_state's "parent") from the moment it is set or restored
— a restored page that was never shown still carries it into the next
layout save — and is dropped only once a resolution found the branch
missing.

The decisions with no widget in them — argv, reply parsing, title →
breadcrumb, the chords, the layout slot, the sidecar — live in hunkctl,
where the unit tests can reach them. This module never imports terminal.py
(which imports it) and declares no "shell-exited": the strip closes any page
that emits it, and a hunk that exits should show a Reopen card, not take
the page with it.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import weakref
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango, Vte  # noqa: E402

from . import gitinfo, hunkctl, keybindings, keymap, proctree, themes  # noqa: E402
from .ghwelcome import command_row  # noqa: E402
from .i18n import _  # noqa: E402

log = logging.getLogger(__name__)

# Every page built and not yet shut down, for shutdown_all: the app's own
# shutdown is the one moment a hunk can still be told to go while its pty is
# alive (see GitPage._shutdown).
_LIVE_PAGES: weakref.WeakSet = weakref.WeakSet()

# The width a column has to reach for the dock to spend free gutter on one
# (see PanelDock._column_floor): hunk's diff on its own, at a readable
# width. The extension's panes want more — hunk 0.20 shows the commits pane
# from about 75 columns and both panes from about 100 — and drops a pane
# that wouldn't leave the diff its minimum, so at this width hunk shows the
# diff alone and the panes appear as the column is widened (a drag of the
# divider, the page's maximize): the extension re-reveals them on a resize,
# since hunk itself doesn't bring a hidden sidebar back.
_MIN_PAGE_WIDTH = 560

# Sidecar serial numbers, one per page built in this process (see
# hunkctl.sidecar_path): a closed page's file is removed and the number is
# never reused, so a late write from a page that is gone can't land in a
# newer page's file.
_SERIAL = itertools.count(1)

# The tab's icon, bundled under data/icons.
_ICON = "git-merge-symbolic"

# The zoom chords' step and clamp, the same numbers PanelTerminal uses
# (terminal.py's _FONT_SCALE_*), copied rather than imported: terminal.py
# imports this module.
_FONT_SCALE_MIN = 0.25
_FONT_SCALE_MAX = 4.0
_FONT_SCALE_STEP = 1.1

# How the branch label is cut when a branch name runs long: the header has
# the breadcrumb to fit beside it.
_BRANCH_MAX_CHARS = 24

# Stack page names.
_HUNK = "hunk"
_CARD = "card"

# Which card the stack shows, when it shows one (see _show_card).
_INSTALL = "install"
_EXITED = "exited"
_NOT_A_REPO = "not-a-repo"


class GitPage(Adw.Bin):
    """The session's git page: hunk in a VTE under a one-row header (PanelPage,
    see panelstrip).

    One per session tab. Spawns hunk on first "map" (a restored page may sit
    unselected in a hidden strip and must not spawn until shown), resolves
    its session id by pid, and switches what is loaded with `hunk session
    reload` — respawning whenever that path is unavailable.
    """

    page_kind = "git"
    column_floor = _MIN_PAGE_WIDTH  # the dock spends free gutter on a column at least this wide

    __gsignals__ = {
        # The tab title follows the breadcrumb: the strip re-reads
        # page_title/page_icon.
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
    # Deliberately NO "shell-exited": the strip closes any page that emits it.

    def __init__(
        self,
        cwd_provider: Callable[[], str | None],
        parent_provider: Callable[[str | None], str | None],
        on_close: Callable[[GitPage], None],
        on_closed: Callable[[GitPage], None],
        loaded: hunkctl.Loaded = hunkctl.DEFAULT_MODE,
        parent: str | None = None,
    ) -> None:
        """*cwd_provider*: the agent's live cwd (TerminalTab.current_agent_cwd)
        — read at every spawn and poll. *parent_provider(cwd)*: the parent
        branch NAME ("main") the host computes (the newest PR's base, else
        the default branch), or None; the page resolves it to a diff target
        itself (gitinfo.resolve_branch). *on_close(page)*: the header ✕ — the
        host routes it through the dock (PanelDock.close_page) so the strip's
        funnel runs. *on_closed(page)*: fired from page_closed(), after hunk
        is signalled, so the host drops its reference. *loaded*: what to
        spawn into (a restored layout's, or the footer's choice): a mode, or
        a commit as {"show": ref}. *parent*: the branch the user set through
        the extension's "Set parent branch…", restored from the layout; it
        beats *parent_provider* while it resolves."""
        super().__init__()
        self.add_css_class("git-page")
        self.set_size_request(_MIN_PAGE_WIDTH, -1)
        self._cwd_provider = cwd_provider
        self._parent_provider = parent_provider
        self._on_close = on_close
        self._on_closed = on_closed
        self._loaded: hunkctl.Loaded = loaded if hunkctl.loaded_ok(loaded) else hunkctl.DEFAULT_MODE
        # The user-set parent branch NAME, or None for the automatic rung
        # (see _resolve_parent). Comes in from the layout, changes through
        # the sidecar, goes out in page_state.
        self._user_parent: str | None = parent if hunkctl.safe_ref(parent) else None
        # Set by _resolve_parent when the user's branch was looked for and
        # not found; cleared when it resolves again or another pick lands.
        # Only this puts the pick out of force (see _parent_source): a page
        # that hasn't resolved anything yet — restored into a hidden strip,
        # or shown over a directory that isn't a repository — keeps it.
        self._user_parent_missing = False

        # -- hunk process state ----------------------------------------------
        self._hunk_path: str | None = None
        self._child_pid: int | None = None
        self._session_id: str | None = None
        # From the start of a spawn attempt (the probe included) until the
        # child exits or the attempt fails: guards the "map" hook.
        self._spawned = False
        # The load the running child was spawned into — what a give-up on
        # the session id compares a queued load against.
        self._spawned_mode: hunkctl.Loaded | None = None
        # From the spawn until the session id is known or given up on: loads
        # in that window queue in _pending_mode rather than respawn.
        self._resolving = False
        self._pending_mode: hunkctl.Loaded | None = None
        self._reloading = False
        # A `session get` on the poll, in flight.
        self._syncing_session = False
        # While a reload or get is out: the mode a newer ask wants loaded
        # once it lands (the user's word beats any title the reply carries),
        # and whether the tree moved meanwhile (reload what hunk shows).
        self._pending_reload: hunkctl.Loaded | None = None
        self._stale = False
        # A respawn asked while a child is alive: the child is signalled and
        # the exit handler spawns the next one instead of showing the card.
        self._respawn_wanted = False
        self._closing = False
        # Every thread reply carries the generation it was dispatched under;
        # a bump orphans everything in flight.
        self._gen = 0

        # -- what the page knows about the tree ------------------------------
        self._repo_root: Path | None = None
        self._branch: str | None = None
        # The parent branch's name and the target a diff names it by
        # ("main", or "origin/main" when only the remote has it).
        self._parent_name: str | None = None
        self._parent_target: str | None = None
        # The target hunk's own title reported for a branch load — shown in
        # the breadcrumb over the resolved one, so the header says what hunk
        # has rather than what was asked.
        self._shown_target: str | None = None
        # hunk's title (repo name stripped) for a load Collins has no name
        # for — a range between two branches, from the extension's parent
        # and default headers, a shell or the agent. None while hunk shows
        # a mode or a commit; while set, _loaded is the last load Collins
        # knew.
        self._foreign: str | None = None
        # The subject of the loaded commit, for the breadcrumb; None for a
        # mode, or while it isn't known (a load just asked for, git not
        # answering). Read on the worker thread that brings a title back.
        self._subject: str | None = None
        self._signature: tuple | None = None

        # -- the sidecar shared with the extension (see hunkctl) ---------------
        # One path per page, kept across respawns, removed at shutdown.
        self._sidecar: str = hunkctl.sidecar_path(GLib.get_user_runtime_dir(), os.getpid(), next(_SERIAL))
        # The (parent, source, default, log page) last written, so the tick
        # rewrites only on a change; None until the first write lands (and
        # again after the file went missing, so it is written afresh).
        self._sidecar_written: tuple | None = None
        # The file's mtime after our own last write — or the extension's,
        # once read — so the tick reads only what the other side wrote.
        self._sidecar_mtime_ns: int | None = None

        self._keys = keymap.KeyMatcher(keybindings.current())

        # -- header -------------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.add_css_class("git-header")
        self._branch_label = Gtk.Label(xalign=0.0)
        self._branch_label.add_css_class("dim-label")
        self._branch_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._branch_label.set_max_width_chars(_BRANCH_MAX_CHARS)
        header.append(self._branch_label)
        self._breadcrumb = Gtk.Label(xalign=0.0, hexpand=True)
        self._breadcrumb.add_css_class("git-breadcrumb")
        self._breadcrumb.set_ellipsize(Pango.EllipsizeMode.END)
        header.append(self._breadcrumb)

        # No switch: the extension's commits panel is the switch, and says
        # more (which commit, which branch). Ctrl+1/2/3 stay as shortcuts to
        # the three most common rows.
        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_tooltip_text(_("Reload the diff"))
        refresh.connect("clicked", lambda *_a: self.refresh())
        header.append(refresh)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("flat")
        close.set_tooltip_text(_("Close the git page"))
        close.connect("clicked", lambda *_a: self._on_close(self))
        header.append(close)

        # -- hunk's terminal, and the cards that stand in for it ----------------
        self.terminal = Vte.Terminal()
        self.terminal.set_scrollback_lines(0)  # hunk is full-screen; nothing scrolls off
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        self.terminal.set_audible_bell(False)
        # Pixel scrolling, as the other terminals (terminal._setup_smooth_scroll).
        self.terminal.set_enable_fallback_scrolling(False)
        self.terminal.set_scroll_unit_is_pixels(True)
        self.terminal.connect("child-exited", self._on_child_exited)
        scrolled = Gtk.ScrolledWindow(child=self.terminal, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._card_slot = Adw.Bin(vexpand=True)
        self._card_button: Gtk.Button | None = None
        self._card: str | None = None  # which card is up, None while hunk is
        self._stack = Gtk.Stack(vexpand=True)
        self._stack.add_named(scrolled, _HUNK)
        self._stack.add_named(self._card_slot, _CARD)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self._stack)
        self.set_child(box)

        # Ctrl+1/2/3 and the zoom chords, ahead of VTE: in the capture phase
        # on the page they fire wherever the focus sits inside it, and before
        # the terminal would feed the press to hunk.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        self.add_controller(keys)

        self._sync_header()
        # First shown (and every re-show): make sure hunk is running. A
        # restored page is built unselected, maybe in a hidden strip, and
        # must not spawn a process nobody is looking at.
        self.connect("map", lambda *_a: self._ensure_spawned())
        # Gone with its tab, or with the window: see _on_unrealize.
        self.connect("unrealize", self._on_unrealize)
        _LIVE_PAGES.add(self)

    # -- public -------------------------------------------------------------

    @property
    def loaded(self) -> hunkctl.Loaded:
        """What the page is showing (or will spawn into): one of hunkctl.MODES,
        or a commit as {"show": ref}."""
        return self._loaded

    @property
    def hunk_alive(self) -> bool:
        """Whether a hunk child is running in the VTE right now."""
        return self._child_pid is not None

    def load(self, loaded: hunkctl.Loaded) -> None:
        """Show *loaded* — "unstaged" | "staged" | "branch", or a commit as
        {"show": ref}: Ctrl+1/2/3 and the host's open_git_page(mode) land
        here. Updates the breadcrumb/tab title at once, then reloads the
        live session (or respawns when there is no session id, or queues
        the load while the id is still being resolved). On the "hunk
        exited" card it is the Reopen button, into this load. "branch"
        with no resolvable parent is a no-op. Anything else raises
        ValueError."""
        if not hunkctl.loaded_ok(loaded):
            raise ValueError(f"unknown git page load: {loaded!r}")
        if loaded == "branch" and self._resolve_parent() is None:
            self._sync_header()
            return
        self._loaded = dict(loaded) if isinstance(loaded, dict) else loaded
        self._foreign = None
        self._subject = None
        if loaded != "branch":
            self._shown_target = None
        self._sync_header()
        self.emit("title-changed")
        if not self._spawned:
            if self._card == _EXITED and self.get_mapped():
                self._spawn()  # Ctrl+1/2/3 on a dead viewer: reopen into that load
            return  # else the spawn on map reads _loaded
        if self._resolving:
            self._pending_mode = self._loaded
        elif self._session_id is None:
            self._respawn()
        else:
            self._reload(self._loaded)

    def refresh(self) -> None:
        """Reload what is loaded (the header's ⟳): `hunk session reload` of
        the same target — a mode's or a commit's — or a respawn without a
        session id. No-op on a card, and while hunk shows a load Collins
        can't name (hunk's own `r` key and `--watch` cover that one)."""
        if not self.hunk_alive:
            return
        if self._resolving:
            return  # the first load is still landing
        if self._foreign is not None:
            return
        self._reload(self._loaded)

    def poll_tick(self) -> None:
        """The footer's 2 s tick, forwarded by the host only for a mapped page
        (the page checks get_mapped() again itself). Re-reads the branch
        label; respawns when the agent's repo root moved (a worktree entry)
        or shows the "not a repository" card when there is none; picks up a
        parent the user set through the extension (the sidecar's mtime
        moved), re-resolves the parent and publishes it and the default
        branch back into the sidecar when either changed; seeds and compares
        gitinfo.tree_signature and reloads the current load when it moved —
        after asking hunk what it has loaded (`session get`), so the reload
        re-asks for what hunk shows, not what Collins last asked for. Never
        spawns a git process: file reads, one stat, and a write when the
        sidecar is due one."""
        if not self.get_mapped() or self._closing:
            return
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None:
            # The tree went away under a running hunk (a worktree removed):
            # take the child down — its exit shows the card — and say why
            # once it is gone. A card already saying so is left alone.
            if self.hunk_alive:
                self._respawn_wanted = False
                self._terminate_child()
            elif not self._spawned and self._card != _NOT_A_REPO:
                self._show_not_a_repo()
            return
        if self._repo_root is not None and root != self._repo_root:
            self._repo_root = root
            self._signature = None
            self._respawn()
            return
        if self._card == _NOT_A_REPO and not self._spawned:
            self._spawn()  # the tree turned up (the agent cd'd into one): no click needed
            return
        branch = gitinfo.current_branch(cwd)
        if branch != self._branch:
            self._branch = branch
            self._sync_header()
        self._read_sidecar()
        target_before = self._parent_target
        self._resolve_parent()
        self._write_sidecar()
        parent_moved = self._parent_target != target_before
        if parent_moved:
            self._sync_header()
            self.emit("title-changed")
        signature = gitinfo.tree_signature(cwd, self._parent_name)
        # A parent that changed changes the signature's base too; that is
        # not the tree moving, and only a branch diff has to follow it.
        moved = self._signature is not None and signature != self._signature and not parent_moved
        self._signature = signature
        if not self.hunk_alive or self._resolving:
            return
        if parent_moved and target_before is not None and self._loaded == "branch" and not self._foreign:
            # The branch diff's base is another branch now: reload against
            # it. Not from None — that is a parent coming back after a
            # refused reload put it down (_reloaded), which the header
            # follows without asking again.
            self._reload("branch")
            return
        if self._session_id is None:
            if moved:
                self._reload(self._loaded)  # no session id: a respawn
            return
        if self._reloading or self._syncing_session:
            # One ask out at a time; a move seen meanwhile is honoured by
            # whichever reply lands (see _reloaded, _synced).
            self._stale = self._stale or moved
            return
        self._sync_session(reload=moved)

    # -- PanelPage protocol (see panelstrip) -----------------------------------

    def page_title(self) -> str:
        if self._foreign is not None:
            return hunkctl.foreign_tab_title(self._foreign)
        return hunkctl.tab_title(self._loaded, self._target_label())

    def page_icon(self) -> str | None:
        return _ICON

    def grab_page_focus(self) -> None:
        if self._stack.get_visible_child_name() == _CARD and self._card_button is not None:
            self._card_button.grab_focus()
        else:
            self.terminal.grab_focus()

    def has_page_focus(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return focus is not None and (focus is self or focus.is_ancestor(self))

    def page_busy(self) -> bool:
        return False  # nothing is lost by closing: hunk holds no state of ours

    def holds_escape(self) -> bool:
        """hunk reads Escape itself while it runs; the dock's restore-from-
        maximized yields to it (see paneldock)."""
        return self.hunk_alive

    def apply_settings(self, settings: dict) -> None:
        """Font ("font"), terminal theme ("terminal_theme") and the KeyMatcher
        for the zoom chords. The scrollback setting is ignored (hunk runs at
        scrollback 0). Safe before the spawn."""
        font = settings.get("font") or ""
        self.terminal.set_font(Pango.FontDescription.from_string(font) if font else None)
        themes.apply_terminal_theme(self.terminal, settings.get("terminal_theme"))
        self._keys = keymap.KeyMatcher.from_settings(settings)

    def page_state(self) -> dict:
        """This page's slot in a serialized dock layout (see panellayout):
        what is loaded, and the user-set parent while it is in force."""
        parent = self._user_parent if self._parent_source() == "user" else None
        return hunkctl.encode_state(self._loaded, parent)

    def page_closed(self) -> None:
        """The tab is really closing: SIGTERM the hunk child if alive, then
        on_closed(self)."""
        self._shutdown()
        self._on_closed(self)

    def _shutdown(self) -> None:
        """The page is done for good: no more spawns, every reply in flight
        orphaned, hunk signalled. Idempotent — the strip's close funnel, the
        widget's dispose and the app's shutdown can each be the first to
        say so.

        The signal has to go out while hunk's pty is still open. Left to the
        pty's hangup alone, the npm wrapper dies and the viewer it spawnSyncs
        lives on, orphaned and deaf to SIGTERM once its terminal is gone
        (hunk 0.20.1, verified) — hence hunkctl.terminate_tree, and hence
        the three ways in."""
        if getattr(self, "_closing", True):  # a dispose can find a half-built page
            return
        self._closing = True
        self._gen += 1
        self._terminate_child()
        _LIVE_PAGES.discard(self)
        try:
            os.remove(self._sidecar)
        except OSError:
            pass  # never written, or already gone with the runtime dir

    def do_dispose(self) -> None:
        self._shutdown()
        Adw.Bin.do_dispose(self)

    def _on_unrealize(self, *_args) -> None:
        """Unrealized: the tab closed, or the page is being re-parented (a
        drag to another strip, a lift-out). The two look the same here, so
        the decision waits for idle, when a re-parented page is realized
        again and a closed one isn't. That one loses its hunk — signalled
        now, while the pty is up — without being marked closing: were the
        call wrong (a re-parent into a window shown later), the next map
        simply spawns hunk again."""
        GLib.idle_add(self._after_unrealize, priority=GLib.PRIORITY_DEFAULT)

    def _after_unrealize(self) -> bool:
        if self.get_realized() or self._closing:
            return GLib.SOURCE_REMOVE
        if self.hunk_alive:
            log.debug("gitpage: page went unrealized; stopping hunk")
            self._respawn_wanted = False
            self._terminate_child()
        elif self._spawned:
            # A spawn in flight for a page nobody shows: orphan it, so the
            # callback terminates the child it announces (see _on_spawned),
            # and let the next map start over.
            log.debug("gitpage: page went unrealized mid-spawn; dropping it")
            self._gen += 1
            self._respawn_wanted = False
            self._spawn_failed()
        return GLib.SOURCE_REMOVE

    # -- header -------------------------------------------------------------------

    def _target_label(self) -> str | None:
        """The parent shown in the breadcrumb and tab title: hunk's own word
        for a branch load, else the resolved target, else the parent's name."""
        return self._shown_target or self._parent_target or self._parent_name

    def _sync_header(self) -> None:
        self._branch_label.set_text(f"⎇ {self._branch}" if self._branch else "")
        self._branch_label.set_visible(bool(self._branch))
        if self._foreign is not None:
            self._breadcrumb.set_text(self._foreign)
        else:
            self._breadcrumb.set_text(
                hunkctl.breadcrumb(self._loaded, self._branch, self._target_label(), self._subject)
            )

    def _on_key_pressed(self, ctrl, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        mode = hunkctl.load_for_key(int(keyval), int(state))
        if mode is not None:
            self.load(mode)
            return True
        event = ctrl.get_current_event()
        if event is None:
            return False
        if self._keys.matches("terminal.zoom-in", event):
            self._zoom_by(_FONT_SCALE_STEP)
            return True
        if self._keys.matches("terminal.zoom-out", event):
            self._zoom_by(1 / _FONT_SCALE_STEP)
            return True
        if self._keys.matches("terminal.zoom-reset", event):
            self._zoom_by(None)
            return True
        return False

    def _zoom_by(self, factor: float | None) -> None:
        scale = 1.0
        if factor is not None:
            scale = max(_FONT_SCALE_MIN, min(_FONT_SCALE_MAX, self.terminal.get_font_scale() * factor))
        self.terminal.set_font_scale(scale)

    def _apply_title(self, title: str, repo_root: str | None = None, subject: str | None = None) -> None:
        """Take hunk's word for what it has loaded (a session or reload
        reply's title): the breadcrumb and tab title follow it, so Collins
        never claims a load hunk didn't make. `<repo> show <ref>` becomes
        the page's commit load, named by *subject* when the worker that
        brought the title read one (_title_subject); a title naming nothing
        Collins can load (`<repo> main...feat`) is shown as it is, less the
        repo name (*repo_root*'s, or the page's own)."""
        kind, target = hunkctl.loaded_from_title(title)
        self._subject = None
        if kind is None:
            root = repo_root or (str(self._repo_root) if self._repo_root else None)
            self._foreign = hunkctl.title_tail(title, root)
        elif kind == "show":
            self._foreign = None
            self._loaded = {hunkctl.SHOW_KEY: target}
            self._shown_target = None
            self._subject = subject or None
        else:
            self._foreign = None
            self._loaded = kind
            self._shown_target = target if kind == "branch" else None
        self._sync_header()
        self.emit("title-changed")

    def _resolve_parent(self) -> str | None:
        """The diff target for the parent branch (`main`, `origin/main`),
        re-read from the tree each time: a fetch or a checkout can create the
        ref between two asks. The user's pick (_user_parent) while it
        resolves, else the host's automatic rung (parent_provider: the
        newest PR's base, else the default branch). None when there is no
        parent to name."""
        cwd = self._cwd_provider()
        name = None
        if self._user_parent and gitinfo.resolve_branch(cwd, self._user_parent):
            name = self._user_parent
            self._user_parent_missing = False
        else:
            self._user_parent_missing = self._user_parent is not None
            name = self._parent_provider(cwd)
        resolved = gitinfo.resolve_branch(cwd, name)
        # The name goes out in the sidecar (and into the header) whether or
        # not it resolves yet, so it passes the same gate the read side
        # applies to what comes back: a PR base or a provider answer that
        # does not look like a ref is not a parent at all.
        self._parent_name = name if hunkctl.safe_ref(name) else None
        self._parent_target = resolved[0] if resolved else None
        return self._parent_target

    def _parent_source(self) -> str:
        """"user" while the user-set parent is in force — set, and not found
        missing by the last _resolve_parent (a page that never resolved,
        being unshown or over no repository, keeps the pick) — else "auto":
        the sidecar's parentSource, and whether page_state carries the
        name."""
        if self._user_parent and not self._user_parent_missing:
            return "user"
        return "auto"

    # -- the sidecar -----------------------------------------------------------------

    def _write_sidecar(self) -> None:
        """Publish (parent, source, default, log page) to the extension when
        any of them changed since the last write (or the file is gone).
        Records the mtime of our own write so the tick doesn't read it
        back."""
        cwd = self._cwd_provider()
        payload = (self._parent_name, self._parent_source(), gitinfo.default_branch(cwd), hunkctl.LOG_PAGE)
        if payload == self._sidecar_written:
            return
        if not hunkctl.write_sidecar(self._sidecar, hunkctl.sidecar_payload(*payload)):
            log.debug("gitpage: could not write the sidecar %s", self._sidecar)
            return
        self._sidecar_written = payload
        try:
            self._sidecar_mtime_ns = os.stat(self._sidecar).st_mtime_ns
        except OSError:
            self._sidecar_mtime_ns = None

    def _read_sidecar(self) -> None:
        """Pick up what the extension wrote: one os.stat, and a read only
        when the mtime isn't the one recorded after our own last write.
        "user" with a parent sets the user's pick; "auto" clears it. The
        caller re-resolves and writes the resolved name back. Reading our
        own write (a write and a pick a moment apart) is harmless: it says
        what the page already holds."""
        try:
            mtime_ns = os.stat(self._sidecar).st_mtime_ns
        except OSError:
            self._sidecar_written = None  # gone (a cleaned runtime dir): write it afresh
            return
        if mtime_ns == self._sidecar_mtime_ns:
            return
        self._sidecar_mtime_ns = mtime_ns
        try:
            with open(self._sidecar, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            return
        parent, source = hunkctl.read_sidecar(text)
        if source == "user" and parent:
            self._user_parent = parent
            self._user_parent_missing = False  # a fresh pick: resolve it before judging
        elif source == "auto":
            self._user_parent = None
            self._user_parent_missing = False

    # -- cards ----------------------------------------------------------------------

    def _show_card(
        self,
        kind: str,
        icon: str,
        title: str,
        description: str,
        button: str,
        on_click: Callable[[], None],
        commands: tuple[str, ...] = (),
    ) -> None:
        """Replace hunk with an Adw.StatusPage: *commands* become click-to-copy
        rows above the one *button*, which does *on_click*."""
        page = Adw.StatusPage(icon_name=icon, title=title, description=description)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.set_halign(Gtk.Align.CENTER)
        for command in commands:
            body.append(command_row(command))
        action = Gtk.Button(label=button)
        action.add_css_class("pill")
        action.add_css_class("suggested-action")
        action.set_halign(Gtk.Align.CENTER)
        action.set_margin_top(8)
        action.connect("clicked", lambda *_a: on_click())
        body.append(action)
        page.set_child(body)
        self._card_button = action
        self._card = kind
        self._card_slot.set_child(page)
        self._stack.set_visible_child_name(_CARD)

    def _show_hunk(self) -> None:
        self._stack.set_visible_child_name(_HUNK)
        self._card_slot.set_child(None)
        self._card_button = None
        self._card = None

    def _show_install_card(self, probe: hunkctl.Probe) -> None:
        if probe.status == "missing":
            title = _("hunk isn't installed")
        else:
            title = _("hunk {version} or newer is needed").format(
                version=".".join(str(part) for part in hunkctl.MIN_VERSION)
            )
        description = _("Collins shows diffs through hunk (hunk.dev). Install it, then check again.")
        if probe.status == "old":
            found = ".".join(str(part) for part in probe.version) if probe.version else _("unknown")
            description = (
                _("The hunk on PATH is version {found}.").format(found=found) + " " + description
            )
        self._show_card(
            _INSTALL,
            "git-merge-symbolic",
            title,
            description,
            _("Check again"),
            self._spawn,
            hunkctl.INSTALL_COMMANDS,
        )

    def _show_exited_card(self, detail: str | None = None) -> None:
        description = _("The diff viewer closed. Reopen it to keep going.")
        if detail:
            description = f"{description}\n{detail}"
        self._show_card(
            _EXITED, "git-merge-symbolic", _("hunk exited"), description, _("Reopen"), self._spawn
        )

    def _show_not_a_repo(self) -> None:
        self._show_card(
            _NOT_A_REPO,
            "folder-symbolic",
            _("Not a git repository"),
            _("The session's working directory has no repository to show. Check again once it does."),
            _("Check again"),
            self._spawn,
        )

    # -- spawning ---------------------------------------------------------------------

    def _ensure_spawned(self) -> None:
        if not self._spawned and not self._closing:
            self._spawn()

    def _spawn(self) -> None:
        """Start (or restart, from a card) a hunk child in the current mode:
        the version probe on a thread — and, for a commit load, whether the
        commit still exists (hunkctl.commit_subject: `hunk show` of a sha
        that was rebased away exits at once, and the page would sit on the
        exited card, Reopen after Reopen) — then the VTE spawn on the main
        loop."""
        if self._closing or self.hunk_alive:
            return
        self._spawned = True
        self._resolving = True
        self._pending_mode = None
        self._session_id = None
        self._gen += 1
        gen = self._gen
        cwd = self._cwd_provider()
        show_ref = hunkctl.show_ref(self._loaded)

        def work() -> None:
            probe = hunkctl.probe()
            subject = hunkctl.commit_subject(cwd, show_ref) if show_ref else None
            GLib.idle_add(self._probed, gen, probe, subject)

        threading.Thread(target=work, name="git-page-probe", daemon=True).start()

    def _probed(self, gen: int, probe: hunkctl.Probe, subject: str | None = None) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        if probe.status != "ok":
            self._spawn_failed()
            self._show_install_card(probe)
            return GLib.SOURCE_REMOVE
        self._hunk_path = probe.path
        cwd = self._cwd_provider()
        root = gitinfo.repo_root(cwd)
        if root is None or not cwd:
            self._spawn_failed()
            self._show_not_a_repo()
            return GLib.SOURCE_REMOVE
        self._repo_root = root
        self._branch = gitinfo.current_branch(cwd)
        parent = self._resolve_parent()
        if self._loaded == "branch" and parent is None:
            self._loaded = hunkctl.DEFAULT_MODE  # a saved "vs main" in a tree with no main
        if hunkctl.is_show(self._loaded) and subject is None:
            # git said the commit is gone (a saved sha the branch was rebased
            # past, a layout restored in another clone): not a load to
            # spawn into. "" — git couldn't be asked — spawns as asked.
            log.debug("gitpage: commit %s doesn't resolve; opening %s", self._loaded, hunkctl.DEFAULT_MODE)
            self._loaded = hunkctl.DEFAULT_MODE
        self._subject = (subject or None) if hunkctl.is_show(self._loaded) else None
        self._shown_target = None
        self._foreign = None
        self._signature = gitinfo.tree_signature(cwd, self._parent_name)
        self._sync_header()
        self.emit("title-changed")
        self._spawned_mode = self._loaded
        # A respawn asked while the probe was out is answered by this very
        # spawn: the cwd and mode were read just now.
        self._respawn_wanted = False
        extension = hunkctl.extension_dir()
        if extension is None:
            log.warning(
                "gitpage: the collins-git extension is missing from %s; running hunk bare",
                hunkctl.EXTENSION_DIR,
            )
        # The sidecar goes down before the child comes up, so the extension
        # finds it on startup; a write that failed leaves the variable out
        # and the extension guesses (see hunkctl.spawn_env).
        self._write_sidecar()
        sidecar = self._sidecar if self._sidecar_written is not None else None
        argv = hunkctl.spawn_argv(probe.path, self._loaded, parent, extension)
        self._show_hunk()
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            str(cwd),
            argv,
            hunkctl.spawn_env(sidecar),  # None: inherit
            GLib.SpawnFlags.DEFAULT,
            None,  # child_setup
            None,  # child_setup_data
            -1,  # timeout
            None,  # cancellable
            lambda terminal, pid, error: self._on_spawned(terminal, pid, error, gen),
        )
        return GLib.SOURCE_REMOVE

    def _spawn_failed(self) -> None:
        self._spawned = False
        self._resolving = False
        self._pending_mode = None

    def _on_spawned(self, terminal: Vte.Terminal, pid: int, error: GLib.Error | None, gen: int) -> None:
        if gen != self._gen or self._closing:
            # Nobody wants this child (the page closed or went unparented
            # while the spawn was out): take it down now, while its pty is
            # open and the group signal reaches the viewer. Its exit lands
            # in _on_child_exited with no _child_pid to match.
            if error is None:
                log.debug("gitpage: hunk %s spawned for a page that moved on; stopping it", pid)
                hunkctl.terminate_tree(pid, proctree.process_children(pid))
            return
        if error is not None:
            log.debug("gitpage: spawning hunk failed: %s", error.message)
            self._spawn_failed()
            self._show_exited_card(error.message)
            return
        self._child_pid = pid
        if self._respawn_wanted:
            # Asked to start over (the repo root moved) between the probe's
            # answer and this: the child's exit spawns the next one.
            self._terminate_child()
            return
        GLib.timeout_add(hunkctl.RESOLVE_DELAYS_MS[0], self._resolve_step, gen, 0)

    def _resolve_step(self, gen: int, step: int) -> bool:
        """One `hunk session list --json` on a thread, matched against the
        child's pid (or its children's) back on the main loop."""
        if gen != self._gen or self._closing or self._child_pid is None:
            return GLib.SOURCE_REMOVE
        hunk = self._hunk_path
        pid = self._child_pid
        cwd = self._cwd_provider()

        def work() -> None:
            reply = hunkctl.run(hunkctl.list_argv(hunk))
            session = hunkctl.session_for_pid(reply.stdout, pid, proctree.process_children(pid))
            subject = _title_subject(cwd, session.title) if session else None
            GLib.idle_add(self._resolved, gen, step, pid, session, subject)

        threading.Thread(target=work, name="git-page-session", daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _resolved(
        self, gen: int, step: int, pid: int, session: hunkctl.Session | None, subject: str | None
    ) -> bool:
        if gen != self._gen or self._closing or self._child_pid != pid:
            return GLib.SOURCE_REMOVE
        if session is None:
            if step + 1 < len(hunkctl.RESOLVE_DELAYS_MS):
                GLib.timeout_add(hunkctl.RESOLVE_DELAYS_MS[step + 1], self._resolve_step, gen, step + 1)
                return GLib.SOURCE_REMOVE
            # Given up: every switch from here on is a respawn.
            log.debug("gitpage: no hunk session for pid %s after %d tries", pid, step + 1)
            self._resolving = False
            pending, self._pending_mode = self._pending_mode, None
            if pending and pending != self._spawned_mode:
                self._respawn()
            return GLib.SOURCE_REMOVE
        self._session_id = session.session_id
        self._resolving = False
        pending, self._pending_mode = self._pending_mode, None
        self._apply_title(session.title, session.repo_root, subject)
        if pending and pending != self._loaded:
            self._loaded = pending
            self._sync_header()
            self.emit("title-changed")
            self._reload(pending)
        return GLib.SOURCE_REMOVE

    def _respawn(self) -> None:
        """Start over in the current mode: signal the running child and let
        its exit spawn the next one; leave a note for a spawn still in flight
        (the probe, or VTE's fork) to act on when it lands; or spawn straight
        away when there is nothing."""
        if self._closing:
            return
        if self.hunk_alive:
            self._respawn_wanted = True
            self._terminate_child()
        elif self._spawned:
            self._respawn_wanted = True
        else:
            self._spawned = False
            self._spawn()

    def _terminate_child(self) -> None:
        """SIGTERM hunk — the whole process group VTE started, since the npm
        wrapper never passes a signal on to the viewer it spawnSyncs (see
        hunkctl.terminate_tree). child-exited follows for the wrapper."""
        pid = self._child_pid
        if pid is None:
            return
        hunkctl.terminate_tree(pid, proctree.process_children(pid))

    def _on_child_exited(self, _terminal: Vte.Terminal, _status: int) -> None:
        if self._child_pid is None:
            # A child this page never took on — spawned for a generation
            # that had moved on and terminated in _on_spawned. Whatever the
            # page is doing now (a card, a fresh probe) is not its business.
            self.terminal.reset(True, True)
            return
        self._child_pid = None
        self._session_id = None
        self._spawned = False
        self._resolving = False
        self._reloading = False
        self._syncing_session = False
        self._pending_reload = None
        self._stale = False
        self._pending_mode = None
        self._gen += 1  # orphan any session list / get / reload still out
        self.terminal.reset(True, True)
        if self._closing:
            return
        if self._respawn_wanted:
            self._respawn_wanted = False
            self._spawn()
            return
        self._show_exited_card()

    # -- reloading ----------------------------------------------------------------------

    def _reload(self, loaded: hunkctl.Loaded) -> None:
        """`hunk session reload` into *loaded* (a mode, or a commit) on a
        thread. A reply saying the session is gone respawns; any other
        refusal (a target hunk can't diff, a timeout) leaves the viewer —
        still showing what it showed — and asks it what that is (see
        _reloaded)."""
        if self._closing:
            return
        if self._session_id is None or self._hunk_path is None:
            self._respawn()
            return
        if self._reloading or self._syncing_session:
            self._pending_reload = loaded
            return
        if loaded == "branch" and self._resolve_parent() is None:
            return
        self._reloading = True
        gen = self._gen
        argv = hunkctl.reload_argv(self._hunk_path, self._session_id, loaded, self._parent_target)
        cwd = self._cwd_provider()

        def work() -> None:
            reply = hunkctl.run(argv)
            title = hunkctl.parse_reload_reply(reply.stdout) if reply.ok else None
            GLib.idle_add(self._reloaded, gen, loaded, reply, title, _title_subject(cwd, title))

        threading.Thread(target=work, name="git-page-reload", daemon=True).start()

    def _reloaded(
        self, gen: int, mode: hunkctl.Loaded, reply: hunkctl.Reply, title: str | None, subject: str | None
    ) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        self._reloading = False
        if title is None and reply.session_gone:
            log.debug("gitpage: session reload found no session; respawning")
            self._pending_reload = None
            self._stale = False
            self._respawn()
            return GLib.SOURCE_REMOVE
        pending, self._pending_reload = self._pending_reload, None
        if title is None:
            # hunk kept what it had (verified against 0.20.1: a bad range
            # exits 1 and the viewer stays put). A parent that stopped
            # resolving is put down until it resolves again (the next tick
            # re-asks); a newer ask queued meanwhile goes out; else the
            # header goes back to what hunk says it shows (and a move seen
            # meanwhile reloads that, see _synced).
            log.debug("gitpage: session reload into %s refused: %s", mode, reply.stderr.strip())
            if mode == "branch" and reply.returncode is not None:
                self._parent_target = None
                self._sync_header()
            if pending is not None:
                self._reload(pending)
            else:
                self._sync_session(reload=False)
            return GLib.SOURCE_REMOVE
        self._stale = False  # whatever moved, this load is newer
        if pending is not None:
            self._reload(pending)  # the header already says so (load); no flicker back
        else:
            self._apply_title(title, None, subject)
        return GLib.SOURCE_REMOVE

    def _sync_session(self, reload: bool) -> None:
        """`hunk session get` on a thread: the header follows the title hunk
        reports, then, with *reload* (the tree moved), what hunk shows is
        reloaded — a load Collins has no name for is left to hunk's own
        watch. One at a time; a move seen meanwhile waits in _stale."""
        if self._closing or self._session_id is None or self._hunk_path is None:
            return
        if self._syncing_session or self._reloading:
            self._stale = self._stale or reload
            return
        self._syncing_session = True
        gen = self._gen
        argv = hunkctl.get_argv(self._hunk_path, self._session_id)
        cwd = self._cwd_provider()

        def work() -> None:
            reply = hunkctl.run(argv)
            session = hunkctl.parse_session_get(reply.stdout) if reply.ok else None
            subject = _title_subject(cwd, session.title) if session else None
            GLib.idle_add(self._synced, gen, reload, reply, session, subject)

        threading.Thread(target=work, name="git-page-get", daemon=True).start()

    def _synced(
        self,
        gen: int,
        reload: bool,
        reply: hunkctl.Reply,
        session: hunkctl.Session | None,
        subject: str | None,
    ) -> bool:
        if gen != self._gen or self._closing:
            return GLib.SOURCE_REMOVE
        self._syncing_session = False
        if reply.session_gone:
            log.debug("gitpage: session get found no session; respawning")
            self._pending_reload = None
            self._stale = False
            self._respawn()
            return GLib.SOURCE_REMOVE
        pending, self._pending_reload = self._pending_reload, None
        if pending is not None:
            self._reload(pending)  # asked meanwhile: the title here is already old news
            return GLib.SOURCE_REMOVE
        if session is not None:
            self._apply_title(session.title, session.repo_root, subject)
        stale, self._stale = reload or self._stale, False
        if stale and self._foreign is None:
            self._reload(self._loaded)
        return GLib.SOURCE_REMOVE


def _title_subject(cwd: str | None, title: str | None) -> str | None:
    """The subject of the commit a `<repo> show <ref>` title names, for the
    worker threads that carry a title back to the main loop (one `git log
    -1`, hunkctl.commit_subject); None for any other title, or when git
    couldn't say."""
    if not title:
        return None
    kind, ref = hunkctl.loaded_from_title(title)
    if kind != "show":
        return None
    return hunkctl.commit_subject(cwd, ref) or None


def shutdown_all() -> None:
    """Stop every page's hunk: the application's shutdown hook. A window
    destroyed on quit unrealizes its pages but needn't dispose them before
    the process ends, and the pty closing behind them would strand hunk's
    viewer (see GitPage._shutdown) — this is the call that doesn't rely on
    the main loop coming round again."""
    for page in list(_LIVE_PAGES):
        page._shutdown()

