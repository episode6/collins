---
name: collins-token-use-and-claude-api
description: >-
  Everything in Collins that runs Claude on the user's behalf or calls
  Anthropic directly on the CLI's OAuth token: headless claude -p runs
  (titles.py session titles, icongen.py project icons, tokenrefresh.py login
  repair) and titles.headless_argv, the model catalog and CLI default
  model/effort resolution (claudemodels.py), the usage panel (usage.py,
  usagepanel.py), claude.ai archive mirroring (remotearchive.py), the Token
  use preferences and first-launch welcome dialog (tokensettings.py,
  welcome.py, welcomegate.py), and locating the claude CLI (clisetup.py). Use
  when adding any feature that spends tokens or talks to Anthropic, changing
  how a model is picked or named, debugging an expired-login symptom, or
  changing the welcome dialog.
---

# Token use and the Claude API

There is no API key anywhere. Every Claude-shaped feature rides the `claude`
CLI's own login: headless `claude -p` runs, or direct HTTPS calls with the
OAuth access token from `~/.claude/.credentials.json` (`claudeAiOauth.
accessToken`; read via `usage.read_credentials`, path overridable with
`COLLINS_CLAUDE_CREDENTIALS`). The file is **never written**; refreshing a
token is the CLI's job at the start of any run, which is what the login
repair exploits. Anthropic can change any of these endpoints without notice —
degrade, never crash.

## Headless runs

All three go through `titles.headless_argv(cli, model, effort)`:
`[cli, "-p", "--strict-mcp-config", "--tools", "", "--model", model, …]`
(`--effort low` for titles). `--tools ""` is the CLI's own spelling for no
built-in tools (pass a real empty argv element); `--strict-mcp-config` drops
every MCP server not named on the command line. Together they took a one-line
Haiku prompt from ~23k to ~8k input tokens. `--bare` would also drop the
global `~/.claude/CLAUDE.md` but disables OAuth, so it is out. Each run
executes from its own child of the scratch dir (`titles.scratch_workdir`:
`~/.config/collins/title-scratch/<uuid>`), which keeps project `CLAUDE.md` /
`.claude/settings.json` out of reach, makes discovery skip the run's own
transcript (`is_scratch_project`), and is swept afterwards. Always
`stdin=subprocess.DEVNULL`. E2E shims log the argv; the empty `--tools`
value shows as two spaces, so derive expected strings from the helper.

- **Session titles** (`titles.py`, `TitleGenerator` driven by the store):
  pre-existing sessions get the free local title (`fallback_title`, first 10
  words); sessions appearing while the app runs get a ≤5-word summary on the
  `title_model` setting (`""` = newest Haiku; `NO_MODEL` = off, the local
  title still runs). A prompt that only names a PR ("review PR 183") gets the
  PR's title from `gh` handed in as quoted, untrusted context
  (`pr_references`, `quote_for_prompt`; at most 2 lookups). Results persist
  once; *Regenerate name* re-runs on demand even under None
  (`regenerate_model`, label from `regenerate_name_label`). Three consecutive
  failures pause the queue.
- **Project icons** (`icongen.py`): `build_prompt` describes the project
  (name, top-level entries, README excerpt — all fenced as untrusted data) with
  the constraints `projecticons` enforces; `IconRun` is one cancellable
  `claude -p` on `icon_model` (`NO_MODEL` by default — the dialog waits for a
  pick); `extract_svg` vets the reply with `usable_generated_icon_bytes`
  (pure vector, no `data:` URIs). Only `save_icon` writes into the project.
- **Login repair** (`tokenrefresh.py`): when the token is dead — past
  `expiresAt` at launch (`maybe_start`) or a usage fetch refused mid-run
  (`maybe_repair`, trusting the observed error over the file) — one
  throwaway one-word Haiku prompt runs; success is the credentials file
  afterwards, not the exit code. Single-flight, cooldown doubling per
  consecutive failure (1h → 2h → 4h … a day), reset on success. On success it
  refreshes the model catalog if a query had failed and refetches the usage
  panels. No credentials file = not logged in = no attempt. Off under
  `COLLINS_USAGE_FIXTURE`, under `auto_renew_login = False`, and until the
  welcome dialog has been answered.

## Direct API calls

| Module | Endpoint | Headers | Degrades to |
| --- | --- | --- | --- |
| `usage.py` | `GET /api/oauth/usage` (what `/usage` shows) | Bearer + `anthropic-beta: oauth-2025-04-20` | panel error text |
| `claudemodels.py` | `GET https://api.anthropic.com/v1/models` (paginated, ≤5 pages) | Bearer, `anthropic-version: 2023-06-01`, `anthropic-beta: oauth-2025-04-20` | last cached list, else `FALLBACK_MODELS` aliases |
| `remotearchive.py` | `POST /v1/code/sessions/<cse_id>/archive` or `/unarchive` (id from the transcript's `bridge-session` line) | Bearer, `anthropic-version` | logged and swallowed; 409 = already there = success |

All three are widget-free with injectable transports and tested against
fakes. `COLLINS_USAGE_FIXTURE=<json>` short-circuits the usage fetch for
screenshots and e2e.

**The model catalog** (`claudemodels.py`): cached a day and on disk
(`~/.cache/collins/models.json`, `_CACHE_VERSION` 2), but a single-model
answer gets no lifetime (a page cut short), a failed query never evicts the
last good list, and failures back off 5 min. Preferences → Token use → Model
list dates the list and its Refresh (`refresh_models`) ignores both. Every
query logs at INFO (`COLLINS_LOG=INFO`), failures at WARNING. Sorting:
unrecognized families first, then Mythos, Fable, Opus, Sonnet, Haiku, newest
first (`sort_models`, `grouped_models`); `short_name` strips `[1m]`
suffixes. `ClaudeModel.efforts` comes from `capabilities.effort` (None =
unknown/alias, `()` = none — Haiku 4.5 / Sonnet 4.5 take no effort). A
setting holds `""` (automatic: newest of the preferred tier via
`resolve_model` / `pick_model`), a model id, or `NO_MODEL` ("none"), which the
resolvers **refuse** — callers gate on it first (`titles.enabled`).

**What the CLI would do** (for the new-chat pickers): `cli_default_model(cwd)`
walks `ANTHROPIC_MODEL` → `/etc/claude-code/managed-settings.json` →
`<cwd>/.claude/settings.local.json` → `<cwd>/.claude/settings.json` →
`~/.claude/settings.json` (`CLAUDE_CONFIG_DIR` honoured) — the CLI's own
per-plan default is written nowhere, only the transcript reveals it.
`cli_default_effort(cwd, model)` walks the same chain but **per model**:
`CLAUDE_CODE_EFFORT_LEVEL` first, then within each file
`modelSettings.<normalized id>.effortLevel` beats the file's top-level
`effortLevel` (which `/effort` never clears — a top-level value alone is stale
data). Anything that wants to *inherit* a running session's model or effort
reads the transcript instead. `/model <id>` typed into a session also saves
the user's default.

## The usage panel (`usagepanel.py`)

Session / weekly / model-scoped bars plus extra-usage credits, polled every
5 min on a daemon thread, gated on the widget being mapped, paused while the
window is suspended (GTK ≥ 4.12) or the screen is locked (screensaver
`ActiveChanged` over D-Bus). Idle landings are `PRIORITY_DEFAULT`. With the
login expired and repair off (or the welcome unanswered) it says to run
`claude` yourself.

## Disclosure: Token use and the welcome dialog

Every switch for a token-spending feature is built once in
`tokensettings.py` (`build_token_rows`: Session title model, Icon generation
model, Auto-renew the Claude login, Model list; `build_mcp_rows`: one switch
per session tool) and shown in two places from the same builder: Preferences'
**Token use** group directly under General (order pinned by
`prefslayout.TOKEN_USE_ROWS`) and the first-launch **Before you start**
dialog (`welcome.py`, gate `welcomegate.should_show`: `welcome_seen` unset —
once per install, existing installs included — or the CLI not found on PATH,
which recurs every launch and can't be escaped, only answered or quit). The
dialog's first group is the CLI: found (one row naming which `claude`) or a
path box (prefilled from `clisetup.known_locations`, Browse, live verdict).
Width 640 to match Preferences; ComboRow values get a 160 px minimum
(`_keep_value_readable`). Toggles write immediately; Continue records
`welcome_seen`. E2E checks and capture scenes seed `welcome_seen: true`.

`clisetup.py`: a desktop launch's PATH lacks `~/.local/bin`, where the native
installer puts `claude`, so a blank sidebar is the symptom. The chosen path is
stored **exactly as given** (symlinks unexpanded — the launcher is repointed
on every self-update) and a versioned path is refused (accepted with a warning
only inside nvm/asdf trees); `apply` **appends** its directory to PATH.

## Footguns

- A new token-spending feature needs: `headless_argv`, the scratch dir, a
  `DEFAULT_SETTINGS` switch (or `NO_MODEL` picker), a row in
  `build_token_rows` + `TOKEN_USE_ROWS`, and a line in the README and
  `docs/guide/how-it-works.md`'s "What spends tokens".
- `bridge-session` ids: `cse_…` and `session_…` are the same body with
  different prefixes.
- The one-model-answer rule: never cache a catalog of one.
- `titles` e2e: a fake session with `title_model` unset spawns real `claude`
  runs — seed `"title_model": "none"` in staged state.

Related: `collins-composer-and-new-chat` (pickers),
`collins-sessions-and-sidebar` (title slots), `collins-preferences-keybindings-i18n`.
