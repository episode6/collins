---
name: collins-testing
description: >-
  How Collins is tested and how to test a change in it: the GTK-free pytest
  unit suite and its import blocklist, the scripts/check_*.py end-to-end
  checks driven by scripts/run_e2e.py under a headless display, the claude
  shim pattern, headless probe scripts, ruff, the hunk extension's bun tests,
  and what CI runs. Use whenever writing or running tests for Collins, adding
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
`hunkctl`, …) and a widget module that imports it. If logic you want to test
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
```

Also: `ruff check collins/ tests/` (CI pins `ruff==0.16.4`, rules
`E F W I UP B`, `E402` ignored for `gi.require_version` ordering; UP035 means
`Callable` comes from `collections.abc`), and `bun test` in
`collins/hunkext/collins-git` for the hunk extension.

## Writing an e2e check

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
- Long inline shell pipelines are refused by the worktree guard; put probes
  in a scratchpad `.py`/`.sh` and run them with `PYTHONPATH=<worktree>`.

## CI

`.github/workflows/ci.yml`: `lint` and `verify-versions` on the bare runner;
`test`, `e2e` (`xvfb-run … scripts/run_e2e.py --timeout 120`, 60-minute job
cap), `packaging` and `ppa-source (resolute)` inside the resolute CI image;
`ppa-source (noble)` in the noble packaging image; `rpm` in the Fedora image;
`hunk-ext` on the bare runner with `setup-bun`. The e2e job `needs: image`
and shows up **after** the first `gh pr checks --watch` may have exited green
— keep watching until an `e2e` row is listed and finished. A check that hangs
is one that needs more than 120 s; the whole suite passes in under two
minutes. Reproduce the e2e job on any machine with Docker:

```bash
docker run --rm -it --init -v "$PWD:/src" -w /src ghcr.io/episode6/collins-ci:<tag> \
  xvfb-run -a -s "-screen 0 1920x1200x24" python3 scripts/run_e2e.py
```

(`--init` matters for the unit suite: as pid 1 a proctree test fails.) The
tag is in the image job's step summary of any recent run.

Related skills: `capture-screenshots` (the launch/staging recipe these checks
share), `collins-gtk-sharp-edges`.
