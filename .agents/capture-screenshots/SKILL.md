---
name: capture-screenshots
description: >-
  How to capture screenshots of the Collins app for PR descriptions, docs, or
  demos: launch an isolated instance alongside any real/debug instance (a
  custom COLLINS_APP_ID e2e identifier is mandatory), stage fake session data,
  and render the window to a PNG in-process. Use whenever a UI change needs a
  screenshot of the running app, including before/after comparisons. Covers
  capturing only — embedding/hosting the image is handled by the separate
  publish-screenshots skill.
---

# Capturing Collins screenshots

## Rule 1: always use a custom e2e app identifier

GTK applications are single-instance per application id. If an instance with
the same id is already running (the user's installed `com.episode6.Collins`
or the `start-debug` instance `com.episode6.Collins.Debug`), launching
"again" does not start a new process — it just activates the existing window.
Your staged demo data never appears, and you may end up screenshotting the
user's **real sessions** instead.

So every screenshot run must set a custom id, by convention:

```bash
export COLLINS_APP_ID=com.episode6.Collins.E2E
```

Never capture from the user's live instance (real or debug) — always launch a
dedicated e2e instance with staged data.

## Rule 2: isolate all data

Point every data source at a scratch directory so the run neither reads nor
writes the user's real state:

```bash
export COLLINS_PROJECTS_DIR="$E2E/projects"     # session transcripts
export COLLINS_CLAUDE_CONFIG="$E2E/claude.json" # echo '{}' > it
export XDG_CONFIG_HOME="$E2E/config"            # app state lives in config/collins/state.json
export XDG_STATE_HOME="$E2E/state"              # saved panel-terminal history
```

## Staging demo data

`.agents/capture-screenshots/scripts/stage-demo-data.sh <dir>` — script paths
in this skill are from the repo root — writes a ready-made scene (two
projects, one favorited session, saved panel history for the first
alpha-widgets session `$U1`). Adapt it, or hand-roll following these rules:

- Transcripts live at `projects/<encoded-cwd>/<uuid>.jsonl`. The directory
  name is the session's cwd with every non-alphanumeric character replaced by
  `-` (e.g. `/home/me/dev/foo` → `-home-me-dev-foo`). Filenames must be
  UUID-shaped or they're ignored.
- Each transcript needs at least one line carrying `timestamp`, `cwd`, and a
  `user` message whose text doesn't start with `<`:

  ```json
  {"type":"user","timestamp":"2026-07-25T14:12:03Z","cwd":"/home/me/dev/foo","message":{"role":"user","content":"Fix the widget"}}
  ```

- `config/collins/state.json` controls presentation. Useful keys:
  - `names`: `{uuid: title}` — deterministic row titles for the screenshot.
  - `favorites` / `archived`: lists of uuids (a project whose sessions are all
    favorited or archived shows an empty header — count 0).
  - `expanded_groups`: groups start **collapsed**; list `"proj:<project-name>"`
    for each project plus `"fav:"` for favorites.
  - `settings`: set `"auto_title_sessions": false` (otherwise the app spawns
    headless `claude` runs to title your fake sessions) and
    `"notify_idle": false`; `window_width`/`window_height` size the shot
    (1100×720 reads well).
- Saved panel-terminal history is plain text at
  `state/collins/panel_history/<uuid>.txt`; a session with a file there
  replays it when its tab's panel opens.

## Capturing

Compositor screenshot APIs are locked down on GNOME Wayland (the Shell D-Bus
call is AccessDenied for unprivileged callers, and no CLI grabbers are
installed), so don't fight the compositor: render the window in-process
instead. `.agents/capture-screenshots/scripts/capture.py` launches the app
from a given source tree, waits for the first paint, renders the window
widget tree via Gsk to a PNG, and quits:

```bash
COLLINS_APP_ID=com.episode6.Collins.E2E \
COLLINS_PROJECTS_DIR="$E2E/projects" \
COLLINS_CLAUDE_CONFIG="$E2E/claude.json" \
XDG_CONFIG_HOME="$E2E/config" \
XDG_STATE_HOME="$E2E/state" \
python3 .agents/capture-screenshots/scripts/capture.py <repo-root> "$E2E/shot.png"
```

Optional flags, for shots beyond the default sidebar-only window:

- `--open-session <uuid>`: wait for the store to discover that session, then
  open its tab. The tab spawns a real shell and types the provider CLI into
  it (`claude --resume <uuid>`), so with a staged fake uuid the top terminal
  shows the CLI's startup/trust prompt — fine when the shot is about the
  surrounding chrome, not the agent conversation.
- `--panel` (requires `--open-session`): also open the tab's secondary
  terminal panel — e.g. to demo restored panel history for a staged session.
- `--settle-ms <n>`: delay before the shot (default 2500).

The window flashes on screen for a few seconds — harmless. Always **look at
the resulting PNG** before using it; a blank or half-populated frame means
the store hadn't settled (raise `--settle-ms`).

## Refreshing the docs screenshot set

`.agents/capture-screenshots/scripts/stage-docs-data.sh <dir>` stages the
richer scene used by the docs site (three projects, MCP config, a usage
fixture, git repos for the branch footer, transcripts with models/tokens, and
a `claude` shim in `<dir>/bin` that renders demo output for `--resume`).
`scripts/capture-docs.py <repo-root> <out.png> --scene NAME` then captures one
of: `main-window`, `sidebar-search`, `quick-switcher`, `tab-emoji`,
`session-details`, `mcp-servers`, `preferences`, `terminal-panel`, `hero`
(the last one is `data/screenshot.png`). Run it with the isolation env from
above **plus** `COLLINS_USAGE_FIXTURE=<dir>/usage-fixture.json`, `HOME=<dir>`
(so paths render as `~/dev/...`), and `PATH=<dir>/bin:$PATH` (so the typed
command is the shim). The two sidebar images in `docs/public/img/` are crops
of the leftmost ~417px of the `hero` and `sidebar-search` shots.

## Before/after comparisons

Capture "before" from a temporary worktree of main — the first argument to
`capture.py` selects the source tree, and the staged data is shared:

```bash
git worktree add "$E2E/main-wt" origin/main
python3 .agents/capture-screenshots/scripts/capture.py \
  "$E2E/main-wt" "$E2E/before.png"   # + env as above
git worktree remove "$E2E/main-wt"
```

Crop to the region that changed with Pillow (installed system-wide), e.g.
the sidebar is the leftmost ~417px at the default 1100×720 size:

```python
from PIL import Image
Image.open("shot.png").crop((0, 85, 417, 420)).save("shot-sidebar.png")
```

Publishing the PNGs somewhere embeddable is out of scope here — see the
global `publish-screenshots` skill.
