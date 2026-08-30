---
name: capture-screenshots
description: >-
  How to capture screenshots of the Collins app for PR descriptions, docs, or
  demos: launch a throwaway instance alongside any real/debug instance (a
  freshly generated COLLINS_APP_ID and per-run scratch directories are
  mandatory, so concurrent agent sessions never collide), stage fake session
  data, and render the window to a PNG in-process — headlessly, so no window
  appears on the user's screen. Use whenever a UI change needs a screenshot of
  the running app, including before/after comparisons. Covers capturing only —
  embedding/hosting the image is handled by the separate publish-screenshots
  skill.
---

# Capturing Collins screenshots

## Rule 1: generate a fresh app identifier for every run

GTK applications are single-instance per application id. If an instance with
the same id is already running, launching "again" does not start a new process
— it just activates the existing window. Your staged demo data never appears,
and you may end up screenshotting someone else's sessions instead.

That applies to the user's installed `com.episode6.Collins` and the
`start-debug` instance `com.episode6.Collins.Debug` — but **also to other agent
sessions**, which are frequently capturing at the same time. A shared, fixed
e2e id collides exactly like the real one does, so derive a unique id per run
rather than hardcoding one:

```bash
E2E=$(mktemp -d)                                    # unique scratch tree, see Rule 2
RUN=r$(basename "$E2E" | tr -cd 'A-Za-z0-9')        # e.g. rtmp4mK9zQx1
export COLLINS_APP_ID=com.episode6.Collins.E2E.$RUN
```

The `r` prefix matters: a GApplication id element may not start with a digit,
and `mktemp` names sometimes do.

Never capture from the user's live instance (real or debug) — always launch a
dedicated e2e instance with staged data.

## Rule 2: isolate all data, in a per-run scratch tree

Point every data source at a scratch directory **created fresh for this run**
(`E2E=$(mktemp -d)` above — never a fixed path, which two concurrent runs would
fight over) so the run neither reads nor writes the user's real state:

```bash
export COLLINS_PROJECTS_DIR="$E2E/projects"     # session transcripts
export COLLINS_CLAUDE_CONFIG="$E2E/claude.json" # echo '{}' > it
export COLLINS_CHATS_DIR="$E2E/chats"           # throwaway chat working dirs
export XDG_CONFIG_HOME="$E2E/config"            # app state lives in config/collins/state.json
export XDG_STATE_HOME="$E2E/state"              # saved panel-terminal history
```

Redirecting `XDG_CONFIG_HOME` also hides `gh`'s credentials from the app, so a
footer PR chip captures as a bare `#72` with no CI glyph (Collins refreshes PR
status with `gh pr view`/`gh pr list`, which then fail auth). Add
`GH_CONFIG_DIR="$HOME/.config/gh"` when the shot is meant to show the glyph.

**`COLLINS_CHATS_DIR` is not optional.** On its first scan every instance
reaps chat directories that none of the sessions it discovered point at. An
instance reading staged transcripts discovers no real chats, so it treats the
user's entire real chats root as orphaned — and "only empty ones" is no
safety net, because a live chat that hasn't written a file yet has an empty
directory. Omitting this override deletes the working directory out from
under the user's running chats.

## Rule 3: capture headlessly

Wrap the capture in `.agents/capture-screenshots/scripts/with-headless-display.sh`
so no window ever opens on the user's screen:

```bash
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
  python3 .agents/capture-screenshots/scripts/capture.py <repo-root> "$E2E/shot.png"
```

It runs GNOME Shell in `--headless` mode on its own session bus and its own
Wayland display, renders there, and tears the whole thing down afterwards —
adding roughly two seconds of shell startup. Multiple runs can't collide: the
display name is unique per invocation. If a headless compositor isn't available
it warns and falls back to the current display, so the capture still succeeds,
just visibly.

`HEADLESS_SIZE` sets the virtual monitor (default `1920x1200`). Keep it
comfortably larger than the window you're capturing — the compositor constrains
a window to its monitor, so a monitor the same size as the window yields a
shrunken shot (a 1100×720 monitor gives a 1034×688 window).

Without this wrapper the window appears on the user's desktop and takes focus
for the whole settle period, interrupting whatever they're doing.

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
  - `settings`: set `"welcome_seen": true` (otherwise the first-launch
    welcome dialog — collins/welcome.py — opens over every shot; leave it
    unset only to shoot that dialog), `"title_model": "none"` (otherwise
    the app spawns headless `claude` runs to title your fake sessions);
    `window_width`/`window_height` size the shot (1100×720 reads well).
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

Putting the three rules together — a complete, collision-free, invisible run:

```bash
E2E=$(mktemp -d)
RUN=r$(basename "$E2E" | tr -cd 'A-Za-z0-9')
bash .agents/capture-screenshots/scripts/stage-demo-data.sh "$E2E"

export COLLINS_APP_ID=com.episode6.Collins.E2E.$RUN
export COLLINS_PROJECTS_DIR="$E2E/projects"
export COLLINS_CLAUDE_CONFIG="$E2E/claude.json"
export COLLINS_CHATS_DIR="$E2E/chats"
export XDG_CONFIG_HOME="$E2E/config"
export XDG_STATE_HOME="$E2E/state"

bash .agents/capture-screenshots/scripts/with-headless-display.sh \
  python3 .agents/capture-screenshots/scripts/capture.py <repo-root> "$E2E/shot.png"
```

Every later capture in the same run reuses that exported env, so the shots stay
comparable. Take the whole block as the starting point rather than reassembling
it — the pieces that look boilerplate (a unique id, `COLLINS_CHATS_DIR`, the
headless wrapper) are the ones with teeth.

Optional flags, for shots beyond the default sidebar-only window:

- `--open-session <uuid>`: wait for the store to discover that session, then
  open its tab. The tab spawns a real shell and types the provider CLI into
  it (`claude --resume <uuid>`), so with a staged fake uuid the top terminal
  shows the CLI's startup/trust prompt — fine when the shot is about the
  surrounding chrome, not the agent conversation.
- `--panel` (requires `--open-session`): also open the tab's secondary
  terminal panel — e.g. to demo restored panel history for a staged session.
- `--settle-ms <n>`: delay before the shot (default 2500).

Under the headless wrapper nothing appears on screen at all. Always **look at
the resulting PNG** before using it; a blank or half-populated frame means
the store hadn't settled (raise `--settle-ms`).

## Refreshing the docs screenshot set

One command recaptures every screenshot the docs site and the README embed:

```bash
bash .agents/capture-screenshots/scripts/refresh-docs-screenshots.sh <repo-root> [scene ...]
```

It stages the docs scene with
`.agents/capture-screenshots/scripts/stage-docs-data.sh <dir>` (three projects,
each a git checkout wearing a `project-icon.svg`; MCP config; a usage fixture;
transcripts with models, tokens and edited files; pull requests on the sidebar
rows via `session_prs`; an attachments gallery; and a `claude` shim in
`<dir>/bin` that clears the screen and renders demo output for `--resume`),
then runs `scripts/capture-docs.py <repo-root> <out.png> --scene NAME` once
per scene behind the headless wrapper, with the isolation env from above
**plus** `COLLINS_USAGE_FIXTURE=<dir>/usage-fixture.json`, `HOME=<dir>` (so
paths render as `~/dev/...`) and `PATH=<dir>/bin:$PATH` (so the typed command
is the shim). Scenes: `main-window`, `hero` (also `data/screenshot.png`, and
cropped to its sidebar column as `sidebar.png`), `quick-switcher`,
`session-details`, `mcp-servers`, `preferences`, `terminal-panel`,
`composer`, `pr-page` (a fabricated `prdetail.fetch` reply — nothing reaches
GitHub), `editor-panel`, `editor-picker` (a PR shot: the editor with nothing
opened — with `--set editor_width=380`, below the `editor_narrow_width`
setting, that's the narrow pane's picker column; `editor-panel` at the same
width is its file view with the back button), `attachments-panel`,
`notifications` (rows staged
straight through the app's notification center, the sheet opened from the
bell — no real bell rings), `notification-card` (the same rows, two of them
shown as in-app cards over the session through the window's card stack,
their auto-hide clocks stopped), `preferences-notifications` (a PR shot,
not a docs one: the Preferences dialog filtered to the Notifications
group), `welcome` and `welcome-cli`
(the first-launch dialog with the CLI found, and with it not found — the
shot runs with `welcome_seen` set back to false, and the not-found one
hides `claude` from clisetup and seeds `~/.local/bin/claude` for the
prefill), and `new-chat`. Name scenes to redo only those. `capture-docs.py` takes `--size WxH` and `--set KEY=JSON`,
which edit the staged `state.json`'s settings before launch — that is how one
staged tree serves every window size and panel width.

Two traps the driver already handles: `new-chat` runs last, because the draft
it writes to `state.json` would show as a Draft row in every scene shot after
it; and the shim's output is wrapped at ~58 columns, so the terminal beside a
PR page or editor doesn't rewrap it. Look at every PNG before committing.

## Before/after comparisons

Capture "before" from a temporary worktree of main — the first argument to
`capture.py` selects the source tree, and the staged data is shared:

```bash
git worktree add "$E2E/main-wt" origin/main
bash .agents/capture-screenshots/scripts/with-headless-display.sh \
  python3 .agents/capture-screenshots/scripts/capture.py \
    "$E2E/main-wt" "$E2E/before.png"   # + env as above
git worktree remove "$E2E/main-wt"
```

The worktree lives inside this run's own `$E2E`, so concurrent runs don't
collide there either — but do remove it, since the repo's worktree list is
shared.

Crop to the region that changed with Pillow (installed system-wide), e.g.
the sidebar is the leftmost ~417px at the default 1100×720 size:

```python
from PIL import Image
Image.open("shot.png").crop((0, 85, 417, 420)).save("shot-sidebar.png")
```

Publishing the PNGs somewhere embeddable is out of scope here — see the
global `publish-screenshots` skill.
