---
name: collins-testing
description: >-
  How Collins is tested and how to test a change in it: the GTK-free pytest
  unit suite and its import blocklist, the scripts/check_*.py end-to-end
  checks driven by scripts/run_e2e.py under a headless display, the claude
  shim pattern, headless probe scripts, ruff, and what CI runs. Use whenever writing or running tests for Collins, adding
  an e2e check, debugging a CI-only failure (e2e flaky, idle starvation, a
  dialog on top), or deciding where a piece of logic must live so it can be
  tested.
---

# Testing Collins

## The two suites

**Unit suite** — `python3 -m pytest tests/ -q`. Runs on `python3-gi` alone:
GLib, GObject and Gio are importable, but `tests/conftest.py` installs a
meta-path finder that raises `ImportError` for `gi.repository.{Gtk, Adw, Gdk,
Gsk, Graphene, Vte}`, in both import styles (`gi.require_version` and a bare
`from gi.repository import Gtk`). So a test may import only GTK-free modules.
This is why the codebase splits every feature into a pure module (`editorfiles`,
`gitinfo`, `docktree`, `prstatus`, `notifycenter`, `traymodel`, `composerkeys`,
`gitloads`, `diffmodel`, `gitpatch`, …) and a widget module that imports it. If logic you want to test
sits in a widget module, move it into the pure sibling first — that is the
convention, not a workaround. Modules whose pure half needs key constants
spell keyvals and modifier bits as integers for the same reason
(`composerkeys`, `panelkeys`).

Fixtures worth knowing (all in `tests/conftest.py`): `projects_dir` (a fake
`~/.claude/projects` with two projects, monkeypatched into `sessions` and
forcing `ClaudeProvider.available()` True), `app_state` (an `AppState`
isolated to a temp config dir, panel-history dir redirected), and an autouse
fixture clearing `prstatus._listeners` (every `SessionStore` a test builds
registers a `PrStore` there). `make_transcript_lines(cwd, text)` builds
realistic JSONL entries. Modules with injectable transports (`usage`,
`remotearchive`, `remoteimages`, `updatecheck`, `claudemodels`) are tested
against fakes or a local HTTP server, never the network.

**E2E checks** — `scripts/check_<name>.py`, each a self-contained script that
stages its own scratch tree and app id, builds a real `App`, drives real
widgets (and where needed a real VTE child behind a `claude` shim on `PATH`),
prints `ok`/`FAIL` lines and exits non-zero on failure. `scripts/run_e2e.py`
discovers them (no registration), runs them serially — each under its own
`dbus-run-session` so bus-name owners like `check_status_icon.py` never
collide — with a per-check timeout, one retry (a pass on retry is reported
"flaky", not failed), and a GitHub step summary. Run locally behind the
headless compositor so nothing appears on screen:

```bash
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
    python3 scripts/run_e2e.py --only new_chat --timeout 120
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
    python3 scripts/run_e2e.py --shard 2/5          # what CI's second leg runs
python3 scripts/run_e2e.py --list --shard 2/5       # …and its estimate; no display needed
```

The store and state over the API (PR-1.10) have three modules of their
own. `tests/test_remote_state.py` drives `remotestate.RemoteState` through
a fake link that holds every reply until the test answers it (a refused
write reverts, notifies and toasts; an event landing while a write is
unanswered does not clobber it; the draft debounce, with a fake timer;
`get_setting`'s routing). `tests/test_remote_store.py` runs the real
`ServiceCore` (its own `AppState` and a `SessionStore` fed the
`projects_dir` sessions) behind the suite's in-process harness
(`tests/inproc.py`: `LoopbackServer` / `LoopbackLink` over a core, the
same dicts and bytes the socket carries, no socket and no thread; the
product's loopback went with PR-1.12b) with both mirrors on it: the
snapshot, an `item` moving one property and its
signal, every mutation, archived paging, the service refusing a device
setting. `tests/test_service_imports.py` imports every module of
`collins.service` and `collins.api` in a subprocess and fails on any
`gi.repository` widget library (GLib, GObject and Gio are allowed).

Real ptys are fine in the unit suite: `tests/test_ptyserver.py` spawns
`cat` and `sh -c` children on ptys the server owns and iterates the
default GLib main context by hand (its `pump`), no display, no GTK. Put the
pty in raw mode from the master side (`tty.setraw(master)`) rather than
with `stty` in the child: the child's `stty` races the first write, and the
line discipline's `^[` echo of an escape then lands in the output.

Also: `ruff check collins/ tests/` (CI pins `ruff==0.16.4`, rules
`E F W I UP B`, `E402` ignored for `gi.require_version` ordering; UP035 means
`Callable` comes from `collections.abc`).

## Writing an e2e check

**The probe** (PR-1.12a, D27). A session's logic runs on the service, so a
check never reads a tab's privates: `tab.probe(name)`,
`tab.probe_set(name, value)` and `tab.probe_call(name, *args, **kwargs)`
are the `debug.session.*` requests (a dotted name walks from the
`Session`: `finish_ledger.armed`, `transcript.set_path`,
`host.session_resolved`, `activity.tracker.mark`, `activity.judge.held`),
`debug.screen` / `debug.pty` read a pty's model and process facts, and
`debug.sandbox` calls the sandbox host, the live grants or the core. The
service serves them only with `COLLINS_DEBUG_API=1` in its environment:
every check sets it in its preamble (the service is in-process) and
`run_e2e.py` sets it for the subprocess. A probe answers None with no pty
(before a new chat's Send, after the exit) and raises on a refusal. Keep a
check's assertions as they were: only the call path moves.

**Every check starts its own service** (PR-1.12b). The service is its
own process, so a check that builds an `App()` calls
`e2e_service.start_service()` right before `App()`: after its scratch
tree, every `COLLINS_*` / `XDG_*` override and the `claude` shim on
`PATH` are in the environment, since the service inherits the environment
it is started with and spawns the shells. The helper runs
`python3 -m collins.service.main --app-id <the check's id>` out of this
checkout with `COLLINS_DEBUG_API=1`, waits for the socket and SIGTERMs it
at exit. A check that drives widgets with **no `App`** behind them and
reaches the service (a PR page's gh requests, a job, the model catalog)
calls `e2e_service.harness_link()` instead: a `SocketLink` to a service
of its own, installed as the current link. `scripts/run_e2e.py` sets
nothing new.

**A patch over a service-side module does not reach the service.** What a
check used to monkeypatch in its own process (`claudemodels.*`,
`prdetail.fetch`, `prstatus.gh_json`) it hands to
`start_service(stubs={...})` / `harness_link(stubs=...)` as JSON records;
`scripts/e2e_stubs.py` applies them inside the service (only under the
probe's flag), re-reads the data on every call (`e2e_service.
update_stubs` restages a step's detail) and records every stubbed call to
a file `e2e_service.stub_calls()` reads. A constant to lower on the
service is `debug.sandbox` with target `core` and name `debug_patch`
(`check_composer_paste_back.py`'s piece limits); the tools' dispatcher is
`debug_tools_list` / `debug_tools_dispatch` the same way.

**A write's outcome lands a moment later.** On the loopback a `state.set`,
a `store.flags`, a `notify.post` was answered, saved and echoed as an
event inside the call. Over the socket the reply and the event land on the
main loop afterwards, so a check that reads the outcome off disk
(`AppState().get_setting`, `state.json`), off a mirror (`center.rows()`,
a green row) or off a tab (`session-resolved` from a probed
`host.session_resolved`) pumps first: `e2e_service.settle()` (150 ms,
then until nothing is pending) or `wait_until(predicate)`. Never loosen
the assertion; wait for it. A check that must observe a transient (the
clone dialog's box locked before the fast fake clone finishes) keeps a
bare pending-pump for that step.

Copy `scripts/check_new_chat.py`'s preamble rather than retyping it. The
essentials, all of which are read at import time somewhere in `collins`:

```python
E2E = tempfile.mkdtemp(prefix="collins-x-")
RUN = "r" + "".join(c for c in os.path.basename(E2E) if c.isalnum())
os.environ["COLLINS_APP_ID"] = f"com.episode6.Collins.E2E.{RUN}"  # unique; 'r' since ids can't start with a digit
os.environ["COLLINS_PROJECTS_DIR"] = f"{E2E}/projects"
os.environ["COLLINS_CLAUDE_CONFIG"] = f"{E2E}/claude.json"        # write "{}" to it
os.environ["COLLINS_CHATS_DIR"] = f"{E2E}/chats"                  # NOT optional: else the app reaps the user's chats
os.environ["XDG_CONFIG_HOME"] = f"{E2E}/config"
os.environ["XDG_STATE_HOME"] = f"{E2E}/state"
```

Seed `config/collins/state.json` with `{"settings": {"welcome_seen": true,
"gh_welcome_dismissed": true}}`: the first-launch welcome dialog otherwise
opens over the window under test, and CI's container has no `gh`, so the
"better with the GitHub CLI" card appears a moment after launch — a dialog on
top makes `grab_focus()` inside anything below return False with every
ancestor looking healthy (libadwaita shadows the lower dialog by clearing
`can-focus` on its bin). `win.get_visible_dialog()` names what is on top.
Set `"title_model": "none"` too unless the check is about titling, or the
app spawns headless `claude` runs against your fake sessions.

Then `sys.path.insert(0, repo_root)` **before** importing `collins`, or the
system-installed copy under `/usr/lib/python3/dist-packages` wins and the
methods you just added "don't exist". Give the script its own deadline
(`GLib.timeout_add` + `os._exit`): an exception inside a GLib callback is
swallowed and the app runs forever.

Sidebar headers need staging: discovery skips a provider whose CLI
`shutil.which` can't find (CI has no real `claude`; a dev box does, so the
miss only shows in CI) — put a shim on `PATH` first. A group the sidebar
never showed starts collapsed (`AppState().set_group_expanded("proj:<name>",
True)` before `App()`), and the Favorites header exists only once something
is starred.

## The claude shim

A stub `claude` that the tab spawns and types into must:

1. `tty.setraw(0)` before reading stdin — `inject_prompt` sends text and a
   lone `\r`; cooked mode turns it into `\n` and a loop waiting for `\r` never
   ends. The real CLI raw-modes stdin too (even `claude agents --json` does,
   which is why every helper spawn in the app uses `stdin=DEVNULL`).
2. Draw the idle prompt as `❯` followed by **U+00A0**, not a space:
   `Provider.takes_prompt` keys on the no-break space. Copy the bytes from
   `check_new_chat.py`; a retyped space makes `takes_prompt()` False forever.
3. Write a transcript under `COLLINS_PROJECTS_DIR/<encoded cwd>/<uuid>.jsonl`
   so the tab's resolver binds (the directory name is the cwd with every
   non-alphanumeric character replaced by `-`; filenames must be UUID-shaped
   and the file must carry `cwd`, `timestamp` and a user message).
4. Log `' '.join(sys.argv[1:])` if the check asserts on the launch argv — and
   derive the expected string from `titles.headless_argv` / the provider
   rather than matching `-p --model X` (the empty `--tools ""` shows as two
   spaces).
5. `sleep` forever if the tab should read as busy; exit if it should not — a
   `sleep infinity` descendant keeps the process poll marking the session
   busy for as long as the tab is open.

Driving a close: a shim whose `agents` subcommand prints `[]` and otherwise
`exec sleep 300` reads busy and dies on Ctrl+C. To exercise busy==0 paths on
a tab running a real CLI, kill the foreground process group
(`os.killpg(os.tcgetpgrp(pty_fd), SIGKILL)`), not the shell.

## Headless probe traps

- `win.activate_action("name", …)` silently does nothing on a `MainWindow`:
  `Gtk.Widget.activate_action` shadows `Gio.ActionGroup`'s. Spell the group:
  `win.activate_action("win.name", variant)` (returns True when dispatched).
  A `SimpleAction` a previous context menu left disabled also no-ops.
- `widget.has_focus()` is False under a headless compositor even for the
  window's focus widget; assert `root.get_focus() is widget`. Entries report
  focus on their inner `GtkText`.
- `GLib.idle_add` at default-idle priority never runs under CI's Xvfb (the
  frame clock never idles). Landings that reset gates must be
  `PRIORITY_DEFAULT`; checks must not assert on cosmetic idle settles.
- Setting the clipboard right after `present()` under the headless GNOME
  Shell is dropped silently (Wayland needs a recent input serial). Retry
  `set_content` every ~50 ms until `clipboard.is_local()`. Under Xvfb it
  always takes, so the flake is local-only.
- To answer an `Adw.AlertDialog`: `dialog.emit("response", "<id>")` runs the
  handler but does not close it — follow with `dialog.force_close()`. An open
  Adw dialog also swallows a window's close attempt.
- `Adw.TabView` pages that were never selected are unmapped and measure 0;
  a `Gtk.Stack` stands in for one in checks (`check_panel_bg_tab_width.py`).
- A `Vte.Terminal` widget that finalizes SIGHUPs its child; holding a Python
  reference keeps the child alive with no window. Assert on a `weakref`, not
  the pid.
- To read a sidebar context menu without compositing, stub
  `SessionSidebar._popup_menu` to capture the `Gio.Menu` and walk its
  sections (`get_item_link(i, Gio.MENU_LINK_SECTION)`).
- Real-CLI probes: copy `~/.claude.json` into the scratch `HOME` so folder
  trust resolves (headless `-p` skips the trust gate and proves nothing);
  scrub `CLAUDE_*` from the environment (an inherited
  `CLAUDE_CODE_CHILD_SESSION` turns transcript saving off); a probe that posts
  `/model` rewrites the user's `~/.claude/settings.json` default — restore it.
- `with-headless-display.sh` swaps the Wayland display and leaves the
  command on the user's session bus. Synthesizing input through
  `org.gnome.Mutter.RemoteDesktop` on that bus types into the user's live
  desktop. Connect to the headless shell's own bus (read it from the
  shell's `/proc/<pid>/environ`) and refuse to run on the session bus; the
  capture-screenshots skill has the recipe. Prefer in-process synthesis
  (`feed_child`, `paste_text`, emitting the signal) when real events are
  not the point.
- Real-CLI probes that must run side by side: `scripts/spike_split_common.py`
  makes a throwaway `HOME` per probe (copies of the login and the config,
  the work directory pre-trusted), refuses when the token is close to
  expiring, and leaves the CLI with Ctrl+C Ctrl+C. `CLAUDE_CODE_NO_FLICKER`
  picks the CLI's mode (`0` classic, `1` fullscreen); unset, the account's
  server gates do, so a fixture always sets it.
- Long inline shell pipelines are refused by the worktree guard; put probes
  in a scratchpad `.py`/`.sh` and run them with `PYTHONPATH=<worktree>`.
- A check that needs only a terminal, not the app:
  `scripts/check_termstream_answers.py` builds a childless `Vte.Terminal`
  in a bare `Gtk.Window` (no `App`, no scratch tree) and compares
  `collins.service.termstream`'s answers with what VTE commits. It reads
  VTE's answers up to a sentinel (`CSI 5 n` fed last; its `CSI 0 n` is the
  last commit), so nothing waits on a timer. `get_cursor_position()` counts
  rows from the top of the buffer, not the screen: take a fresh terminal
  before comparing a cursor.
- `scripts/check_termscreen_parity.py` holds a real VTE to the termscreen
  goldens (`tests/fixtures/streams/*.golden.json`): rows, cursor, every
  drawn cell read one at a time through `Vte.Format.HTML` (a row-level
  HTML read folds faint into a colour only for its first run), the dim
  tail and the grammar's reads. `--write` regenerates the goldens from
  this VTE; the unit suite then holds the model to them. It converts
  VTE's buffer-relative cursor row by homing the cursor with origin mode
  off and reading where it landed. The recorded fixtures were made from
  a neutral directory in an isolated `$HOME` (the terminal-tab skill's
  "Re-recording"), so no path of the user's is in them; a test pins that.

## CI

`.github/workflows/ci.yml`: `lint`, `verify-versions` and `docs` (the
VitePress build of `docs/`, so a page that Vue can't compile — a `<word>`
outside a one-line code span — fails the PR rather than the deploy after
the merge) on the bare runner;
`test`, `e2e-shard (1)` … `(5)` (`xvfb-run … scripts/run_e2e.py
--timeout 120 --shard N/5`: the suite, every tab and panel shell on the
service's pty server over the socket of a service each check starts;
60-minute job cap each),
`packaging` and `ppa-source
(resolute)` inside the resolute CI image; `ppa-source (noble)` in the noble
packaging image; `rpm` in the Fedora image. The e2e suite runs as five
parallel shards dealt out by measured time (the `CHECK_SECONDS` table in
`scripts/run_e2e.py`; a check the table lacks is dealt in at a default
weight, so a new check needs no registration, and a stale name fails
`tests/test_run_e2e.py`). A bare `e2e` job fans the shards back in — it is
the name the main-branch ruleset requires, so it must stay. The shard jobs
`needs: image` and show up **after** the first `gh pr checks --watch` may
have exited green — keep watching until the `e2e` row is listed and
finished. A check that hangs is one that needs more than 120 s; a shard
passes in about a minute. When a leg drifts or a check changes, the
`balance-e2e-shards` skill refreshes the weights from a run's logs and
previews the deal. On a dev box the whole suite is
`python3 scripts/run_e2e.py` under the headless wrapper;
`scripts/check_attach_redraw.py` is about the tab's client terminal on the
service's pty (a second VTE attaching to a running pty is redrawn from the
service's model, scrollback and colours compared, and the redraw guard's
sentinel answer is shown never to reach the pty; `COLLINS_ATTACH_RECORD=<file>`
writes the first client's stream for a replay through the model and a VTE
outside the app). `scripts/probe_server_backend.py` (headless, no quota) is
the pty client's probe set: a flow-control redraw's sentinel never
reaching the pty, the guard's watchdog and a failed attach, a NUL commit,
two attaches back to back, the mouse coalescer under random streams.
Reproduce a shard (or, without
`--shard`, the whole suite) on any machine with Docker:

```bash
docker run --rm -it --init -v "$PWD:/src" -w /src ghcr.io/episode6/collins-ci:<tag> \
  xvfb-run -a -s "-screen 0 1920x1200x24" python3 scripts/run_e2e.py
```

(`--init` matters for the unit suite: as pid 1 a proctree test fails.) The
tag is in the image job's step summary of any recent run.

Related skills: `capture-screenshots` (the launch/staging recipe these checks
share), `collins-gtk-sharp-edges`.
