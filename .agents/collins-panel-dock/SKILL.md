---
name: collins-panel-dock
description: >-
  How the panel area around a session's agent terminal works: the GTK-free
  DockTree of fixed-axis splits (docktree.py), PanelDock realizing it with
  hardened PanedSizers (paneldock.py, panedsizer.py, panelsizing.py),
  PanelStrip tab strips and the duck-typed PanelPage protocol (panelstrip.py),
  Ctrl+J's panel terminal, the join-don't-split and free-split rules, maximize,
  drag-and-drop with drop zones and the tab guard (paneldnd.py, dockzones.py,
  tabguard.py), and layout/scrollback persistence (panellayout.py,
  panelhistory.py). Use when adding a new kind of docked page, changing how
  panels open, split, move, size or persist, or debugging a panel that
  appeared, vanished or lost focus unexpectedly.
---

# The panel dock

Every `TerminalTab` owns one `PanelDock`: the region around the agent
terminal as a binary tree of splits. Leaves are the terminal (exactly one,
never closable) and `PanelStrip`s — `Adw.TabView`-based tab strips of
*pages*. The editor is deliberately **not** a page (it would stack three tab
levels); it keeps its own end slot on the tab.

## The pieces

- `docktree.DockTree` (GTK-free, unit-tested): splits with a fixed
  orientation for life, `a` = start child (left/top), `b` = end child. New
  layouts come from new splits; nothing ever flips an existing paned's axis
  (the lesson of PR 229's clamp bug).
- `paneldock.PanelDock`: mirrors every tree mutation onto real `Gtk.Paned`s,
  each with a `PanedSizer`. Public surface: `open_page(widget, side, focus)`,
  `reveal_page`, `move_page`, `rotate_page`, `swap_home`, `maximize_page` /
  `restore_maximized`, `panel_terminal` / `panel_terminal_showing`,
  `show_home`, `set_home_position`, `capture_layout` / `restore_layout`,
  `capture_shell_texts`.
- `panelstrip.PanelStrip`: a strip of pages implementing the duck-typed
  protocol — `page_kind` (class attr: `shell`, `pr`, `composer`,
  `attachments`, `git`), `page_title()`, `page_icon()`, `grab_page_focus()`,
  `has_page_focus()`, `page_busy()` (busy → the X confirms), `apply_settings(
  dict)`, optional `page_closed()` (a real close, not a transfer — last chance
  to rescue state) and `holds_escape()` (keep Escape from the maximize
  restore). Pages may emit `bell` and `shell-exited`; the strip wires those on
  `page-attached` and unwires on `page-detached`, so they follow a page moved
  with `Adw.TabView.transfer_page`. Strip signals: `empty`, `bell`,
  `page-touched(widget, arrived)`. Shells come from an injected
  `shell_factory(sandboxed=False)` (avoids a terminal.py import cycle),
  always called with the keyword — a factory that can't build the
  sandboxed kind must raise rather than hand back a plain shell under
  that title (the four check scripts' fakes take and ignore it).
- `panedsizer.PanedSizer` + `panelsizing` (pure arithmetic): remembers an
  end-child size per key, re-applies it across settle passes
  (50/150/300 ms) while a gate stays up, cedes to a live user drag
  (`is_recognized()` gesture on the paned), never records a clamp. An unmapped
  paned (a background tab) parks the apply on `map`.
- `paneldnd`: per-tab custom `DragSource`s (native Adwaita tab drags can't be
  seen by our `DropTarget`s), `DropZones` overlay (geometry in `dockzones`),
  guard wiring. `tabguard` (GTK-free policy) bounces native Adwaita tab drops
  that land in a view of the wrong group (session bar / editor / dock) — DnD
  is process-global in libadwaita with no accept API, so it is undone on
  `page-attached` rather than prevented.
- `panellayout`: (de)serialization of the `panel_layout` entry — `mode`
  (shells' home edge), `sizes`, and the split tree with `{"terminal": true}`
  / `{"strip": {open, home, selected, pages}}` / `{"split": "h"|"v", size,
  managed, a, b}` nodes. `validate` drops malformed trees whole; `prune` drops
  page kinds this build can't rebuild; `from_legacy` converts the pre-tree
  shape. A strip saved `open: false` is hidden, not closed.
- `panelhistory`: one plain-text scrollback file per shell under
  `~/.local/state/collins/panel_history/`, keyed by a persistent **ordinal**
  (never renumbered; pages move between strips); `save_all` takes the live
  mapping as an explicit keep-set. The service
  writes it (below).

## The rules

**Join, don't split.** `open_page(side="right"|"below")` lands a page as a
tab in the first strip past the terminal on that side; only an axis with no
strip splits. Five PRs would otherwise shred the dock. Two things still split
an occupied axis, and only where the column is **free**: `_split_is_free`
asks whether the terminal is already wider than `terminal_max_width` (its
`Adw.Clamp` stops growing and centers, so the gutter is unused width) and
whether the column — measured at the width it would really open at, the
app-wide seed else the complement of `DEFAULT_FRACTION` (0.62 is the
terminal's share, so a new column gets 38%) — fits there above
`MIN_SPLIT_SIZE`. Only "right" is gated this way; bottom pages always join.
With no seed a free split needs a ~1950 px terminal (a ~2400 px window).

**Ctrl+J is bound to a terminal, not a strip.** `panel_terminal` is the first
shell opened in the session and, once that closes, the next shell to
*arrive* in any strip. `panel_terminal_showing` is the toggle state — false
when stowed, when its strip is hidden, and when it is a background tab of a
visible strip (press one fronts, press two hides). A terminal alone in its
strip hides the strip; one sharing a row is lifted into `_StowPane` (a
never-shown `Adw.TabView`) so its neighbours stay put. The *home strip* now
only means "which edge the shells sit on / which divider the home size seed
speaks for"; `show_home()` guarantees a shell in it, so a home pointing at a
shell-less strip conjures a second terminal on the old edge.

**Size seeds are scoped by axis, not kind.** `panel_size_{bottom,right}` for
the shells' home strip, `page_panel_size_{bottom,right}` for the strip docked
pages share (`state.panel_size_key(scope, mode)`). A new page kind inherits
the "page" scope — don't invent a size setting. Per-session sizes ride the
serialized tree's `size` field. A page may declare `column_floor` (the dock
spends free gutter past `terminal_max_width` on a column at least that wide,
doubled when the gutter pays — `panelsizing.spare_floor`; the PR page's 320)
and `column_seed` (the least its column *opens* at whether or not the gutter
pays, paid by the terminal if need be — the git page's ~700, what its own
size request used to force before its sidebar became collapsible). Both are
seeds only: a size the sizer recorded from a drag wins (`PanelDock.
_column_floor`).

**Maximize** transfers the page into `_MaxPane` (a bare `Adw.TabView`, no
bar) so it keeps its `Adw.TabPage`; the host rewires `shell-exited`/`bell`
itself, the emptied strip is *not* collapsed (guard in `_collapse_strip`),
and dock-wide walks (`_strip_pages`) re-insert the lifted page for
scrollback saves, busy checks and settings fan-out. The maximized page owns
the keyboard: `grab_terminal_focus` redirects to it, a root focus trap
bounces focus that lands under it, and a CAPTURE-phase bare Escape restores
unless `holds_escape()` (a shell with a running command) says no
(`panelkeys.escape_restores`, GTK-free).

**Quiet opens need two fixes.** `open_page(..., focus=False)` sets the
strip's one-shot `_quiet_focus` **and** captures/restores `root.get_focus()`
around `_split_leaf`, because unparenting the terminal to re-place it under
a new paned loses the keyboard to the first focusable thing. Any unasked-for
open (auto-opened PR page, attachments docking itself, `run_in_terminal`)
must go through `focus=False` on both the fresh-page and already-open
(`reveal_page`) branches. Don't reinvent a `quiet` flag.

**Schedule unbidden surgery a beat out.** Opening a page from the idle inside
the footer-chip rebuild cascade segfaulted GTK's Wayland backend;
`_on_hub_pr_attached` uses a 250 ms timeout. A widget `_split_leaf` just
reparented reports `get_width() == 0` until the next layout pass, so two
opens in one frame make the second join.

**The sandboxed shell is a shell.** `PanelTerminal(number, plan_lookup=…)`
runs `$SHELL` inside the session's bubblewrap box (see
`collins-sandboxed-sessions`) with `page_kind` still `"shell"` and a
`sandboxed` attribute: history, busy checks and layout persistence hold
(`page_state` adds `"sandboxed": True`, `_restore_node` passes it to
`strip.new_shell(sandboxed=True)`), the title is *Sandboxed shell N* on
the dock-wide numbering, and Ctrl+J never binds to one
(`_on_page_touched`). `open_shell_page(sandboxed=True)` sits one beside
the last shell page; the strip's tab menu offers *New sandboxed shell*
while `set_sandboxed_shell_offer` says there is a plan.

## Panel shells on the pty server

A `PanelTerminal` (PR-1.8 of the split, spec §3.15; PR-1.9 made it the
only kind) does what the session tab does (see `collins-terminal-tab`,
"The tab on the pty server"): its `terminal` is a
childless `ptyclient.ClientVte` and `_view` a `ClientTerminal` over a pty
of kind `shell` on the service's `PtyServer`, through the shell's **own**
`PtyClient` on the app's link (`terminal._pty_client`, one per shell;
PR-1.12b). `_view` is
the glue, as the tab's.

- **Spawn.** `_service_spawn`: a `spawn` request (`kind` `shell`, the cwd,
  the VTE's grid) and `attach`; the service runs `$SHELL` with its own
  environment and **no** progress declarations (a panel shell never had
  them). It returns once the child has exec'd, so `_child_pid` is set at
  once (the e2e checks read `proctree.process_cwd(shell._child_pid)`) and
  input queued before the spawn is sent straight after. A refusal is fed to
  the widget where a spawn error goes. A sandboxed shell asks with
  `sandbox` and the session's `sandbox_box` (`box_lookup`); the service
  finds the plan by the box (`ServiceCore`'s `sandbox_plan`, over the
  service's `Session`s), spawns
  `providers.sandboxed_shell_argv(plan, $SHELL)` and queues the `cd` into
  the workspace itself; `sandbox_plan` is the pty's `plan`. The client
  still checks `plan_lookup()` first, so "no plan" is the same message and
  the same retry on the next show.
- **The cwd is the service's to judge (D39, PR-2.8).** `_spawn` sends the
  cwd it was given (no `Path.is_dir`); the service starts the shell there
  or, when that is no directory on its machine, in its `$HOME`
  (`core.spawn_cwd`), and the reply's `cwd` is kept as `started_cwd`.
  `_sync_cwd` (the panel shown again, `follow_cwd`'s worktree offer) asks
  `fs.stat` off the main loop and types the `cd` when the answer lands,
  if the shell is still idle and still elsewhere (`_cd_seq` drops an
  older ask); a path that is no directory there types nothing. So the
  `cd` is asynchronous: an e2e check waits for `process_cwd`, and a bare
  `PanelTerminal` with no link (`apilink.current()` None) never moves.
- **Reads go to the service.** `capture_contents()` is the screen model's
  (the `pty.capture` request), `has_running_command()` / `_shell_pid()` /
  the cwd `follow_cwd` and `_sync_cwd` compare against are the pty
  server's (`Pty.has_running_command`, `Pty.shell_pid` with the box's
  inner shell, `Pty.process_cwd` from `/proc`), all through the `pty.info`
  request (PR-1.12a). A shell's
  `spawn` names its tab's session by `handle`, so the service re-files
  its history when that session resolves. Typing (`run_command`, the
  `cd`, Ctrl+L) is input frames (`_write`); `note()` is a `paint`.
- **The history.** `TerminalTab.save_panel_history`
  hands the service the key and `{ordinal: shell.history_source()}` (the
  pty id, or the widget's text for a shell with no pty) and
  `ServiceCore.write_panel_history` writes each from its model
  (`PtyServer.capture`: the live model; a pty already gone has none, its
  model file removed with its row) with
  `panelhistory.save_all`'s keep-set, path and cap. The moments are the
  same three (a draft save, the tab's close, the quit), and at the tab's
  close the window then ends the shells (`tab.release_panel_ptys()`, after
  the saves), so the write is the shells' last word. A reopened shell's
  saved text is fed to the widget at once and **painted** into the new
  pty's stream right after the attach, before the main loop can read the
  shell's first byte (`_history_paint`, in `TEXT_MAX` pieces); a sandboxed
  shell with no plan yet keeps it for the spawn that finds one.
- **A shell closed by `exit` loses its history.** Its page closes and its
  ordinal drops out of the next save's keep-set. When a shell's child exits
  with its page still open, the service writes the history itself from the
  model (before the model is dropped) under the key `spawn`'s `history` or
  a `panel.key` request gave it; once the page is closed for good the key
  is none, so a closed shell's history goes with it.
- **The end.** `pty-exited` is `child-exited` (`_on_pty_exited`); the X
  closing a shell page calls `page_closed()` → `release_pty()` (`close`,
  detach, the client closed; idempotent), and so do the shell's own
  `destroy` and the tab's (`release_panel_ptys`), for a window closed with
  idle tabs that never sees a close-page. `clear()` asks the service to
  `clear` the shell's model (a fresh one; the modes stay in the tracker),
  re-attaches to be redrawn from it, then sends Ctrl+L.
- **The theme.** `apply_settings` sends the shell's colours as the
  `theme` event (`ClientTerminal.set_term(_service_term(theme, terminal))`,
  the tab's builder shared). The term is the client's (the client's
  `term`, read by its sinks), and a pty's queries are answered from its
  active client's (`PtyServer.pty_term`; the last one seen while none is
  active), so the service answers as this terminal would even beside a
  new-chat screen with no agent pty.

## Adding a page kind

1. Implement the protocol on the widget; give it a `page_kind`.
2. Make it persistable only if it can be rebuilt from a small dict:
   `TerminalTab._make_panel_page` is the factory, `panellayout.prune`'s
   allow-list in terminal.py names the kinds, and `page_state()` returns
   what to save (a PR page saves its URL and re-gates it on restore; the
   composer saves nothing but its placement).
3. Open it with `self._dock.open_page(widget, side, focus=...)`; front an
   existing one with `reveal_page`. Provide `page_closed()` if closing loses
   something the tab should keep (the composer hands its text back).
4. Exercise with `scripts/check_panel_layout.py` (real strips, no VTE) and
   the sizing checks (`check_panel_resize_save.py`, `check_panel_bg_tab_
   width.py`).

## Footguns

- `PanelTerminal.has_page_focus` is a bare `terminal.has_focus()`, which is
  False under a headless compositor; probes of the focus tier must monkeypatch
  it to a `root.get_focus()` ancestry test (as `PrViewPage` does).
- The rotate shortcut resolves its target by focus, then the `page-touched`
  record (`_recent_page`); the button knows its strip, the keystroke doesn't.
- `panel_tab_drag_handles` rides private `AdwTab` internals; the drag source
  must take Adwaita's `AdwTabBox` gesture out of the chain
  (`set_propagation_phase(NONE)`) rather than race it — `Gtk.DragSource` has a
  100 ms deadband that a flick loses.
- An emptied strip collapses from an idle; space scripted steps ~700 ms apart.
- Hidden ≠ closed: pages in a hidden home strip keep running and are saved.

Related: `collins-terminal-tab`, `collins-composer-and-new-chat`,
`collins-pull-requests`, `collins-gtk-sharp-edges`.
