---
name: collins-git-page
description: >-
  How Collins' git page works: hunk (hunk.dev, the terminal diff viewer)
  running in a VTE beside the session and driven over its session API
  (gitpage.py), the GTK-free decisions in hunkctl.py (argv, version gate,
  session lookup by pid, titles, sidecar, show_diff), the collins-git hunk
  extension shipped as package data (TypeScript in collins/hunkext/collins-git:
  commits and files panels, staging, line ranges, commits, the sidecar
  contract), Preferences → Git, the parent-branch rule, freshness reloads, and
  gitinfo.py's cheap .git reads for the footer branch. Use when changing the
  git page, the extension, the show_diff tool's page-driving half, git-branch
  detection, or debugging "clicking a commit does nothing" / a stranded hunk
  viewer.
---

# The git page

## Shape

`GitPage` (`gitpage.py`) is a `PanelPage` (`page_kind="git"`, F6 / the
footer's branch label / the `show_diff` tool) holding a VTE that runs
`hunk diff --watch --transparent-bg --extension <collins-git> [--mode …]
[--theme …] [--exclude-untracked]` in the agent's working tree, under a
one-row header: the branch, a breadcrumb of what is loaded, back/forward
level buttons on narrow pages, and refresh. Everything else on screen is
hunk's plus the extension's. A machine without hunk (or with one older than
the version gate) gets an install card linking to hunk.dev with a "Check
again" button — never an error. `hunkctl.ProbeCache` probes `hunk --version`
at most every 30 s; `App._refresh_hunk_probe` also gates the `show_diff` tool's
presence in the MCP list.

**Loads go through hunk's session API, not respawns.** After spawning, the
page resolves the viewer's session id from `hunk session list --json`,
matching `pid in proctree.process_children(child_pid)` — `/usr/local/bin/hunk`
is an npm wrapper (`bin/hunk.cjs`) that `spawnSync`s the real viewer, so VTE's
child pid is the wrapper and hunk reports the child. Then Ctrl+1/2/3 (unstaged
/ staged / branch) and any host request run `hunk session reload <id> --json
-- diff …` or `-- show <ref>`, swapping the contents in place. Only a reply
whose stderr says the session is gone (`hunkctl.session_gone`: "No active
session(s)…") triggers a respawn; a refused load ("could not resolve Git
revision") leaves the healthy viewer alone. The three modes plus
`{"show": ref}` are the `Loaded` value; `hunkctl.breadcrumb` / `tab_title` /
`loaded_from_title` map between them and hunk's own session titles.

**Closing** must kill the process **group** VTE started
(`hunkctl.terminate_tree`, `gitpage._shutdown`; `app.do_shutdown` calls
`gitpage.shutdown_all()`): SIGTERM to the wrapper leaves the viewer alive, and
a viewer whose pty is gone ignores SIGTERM/SIGINT. The auto-spawned `hunk
daemon serve` is in its own group and rightly survives.

**Freshness.** hunk's `--watch` covers file edits; commits and staging done
from a shell or by the agent are caught by the tab footer's 2 s tick
forwarding `poll_tick` while the page is mapped: `gitinfo.tree_signature`
(index mtime, HEAD sha, parent ref) changed → reload what is shown. A move the
extension made itself is recorded in the sidecar (`refreshed`: index mtime +
HEAD) and skipped, or the reload would land on the dialog the user opened
next (hunk cancels dialogs on any reload).

**Parent branch.** `TerminalTab._git_parent_branch`: an attached open PR's
base, else the `git_parent_branch` setting (`origin/x` read as `x`), else the
repository's default branch (`gitinfo.default_branch`: `refs/remotes/*/HEAD`,
then local `main`/`master`, loose or packed — no subprocess; a `git init`
repo has no remote HEAD). The user can override with `P` in the extension,
which writes `parentSource: "user"` to the sidecar; Collins recomputes on
"Automatic". The pick persists in the page's layout slot
(`hunkctl.encode_state` / `decode_parent`).

## The sidecar contract (`COLLINS_GIT_STATE`)

A JSON file under `$XDG_RUNTIME_DIR` (`hunkctl.sidecar_path`), created before
spawn and deleted on close, read-merge-written by both sides (temp file +
rename, unknown keys pass through, garbled file tolerated):

- Collins writes `version`, `parent`, `parentSource` (`auto`|`user`),
  `default`, `logPage` (5..500, from `git_log_page`), `untracked` (from
  `git_untracked`; false makes the extension add `--exclude-untracked` to
  every diff tail so its loads match Collins').
- The extension writes `parent` + `parentSource: "user"` on a pick,
  `refreshed` (`{"index": "<mtime ns as a string>", "head": "<sha>"}`) after
  its own reloads, and `level` (`diff`|`files`|`commits`) for narrow pages —
  the header buttons read their tooltips and sensitivity from it and feed
  hunk the bytes `<` / `>` (hunk's CLI can't run an extension command by name,
  so those two keys are pinned).

The extension polls the file every 2 s (`fs.watchFile`) and re-reads it on
every `changeset_loaded` / `session_reload`. Without the env var it runs
standalone with guessed defaults. Preferences → Git: layout (`--mode`
auto/split/stack) and theme go on the spawn argv (a change restarts hunk in
place — nothing in the session API changes them); untracked goes on every
diff tail; page size through the sidecar. `prefslayout` pins hunk's own
`--mode` words so the two sides can't drift. `hunkctl.Options.from_settings`
normalises the settings dict; `safe_theme` gates the theme string.

## The extension (`collins/hunkext/collins-git`)

TypeScript hunk transpiles itself — no build step, no `node_modules` shipped;
package data is `package.json`, `README.md`, `*.ts`, `*.tsx` (pyproject). Its
README is the authoritative feature and key reference; the short version:
`commits.tsx` (groups: current branch with a `working tree` row and `↑`
unpushed marks, parent branch, default branch with `load more…`),
`files.tsx` (replaces hunk's files pane; splits UNSTAGED/STAGED on the
working tree — hunk holds one side at a time), `staging.ts` / `patch.ts` /
`range.ts` / `anchor.ts` (x/X stage, `v` anchors a line range, `D` discards,
`A`/`U` all; partial patches applied with `--unidiff-zero`, mode changes left
to `X`, ranges refused across files or when the file changed since the
anchor), `git.ts` (`C`/`B`/`F` commit / commit with body / `fixup! <sha>`
on an async runner with a `committing…` toast; `F` lists `<parent>..HEAD
--not --remotes`, never `@{upstream}`), `level.ts` (the three narrow-terminal
regimes: <73 cols diff only, 73–99 one pane, ≥100 both; `<`/`>` step),
`session.ts` (reloads via `hunk session reload` by pid-resolved id; a daemon
timeout is "unknown", not failed), `sidecar.ts`. Keys avoid hunk's defaults
and are rebindable under `[keybindings]` as `collins-git.*`. It never writes
to the terminal; `COLLINS_GIT_DEBUG_LOG=<file>` logs what it does.

Tests: `bun test` in that directory (no `bun install` needed — modules import
only `node:*`, each other and erased `import type`s; CI installs bun 1.4.0).
`bun install && bun run typecheck` is dev-only. `bun.lock` is gitignored.

## gitinfo (`gitinfo.py`, GTK-free)

Cheap reads straight from `.git` for the footer's 2 s poll and every
right-click: `current_branch`, `default_branch`, `github_url`, `repo_root`,
`index_mtime`, `head_sha`, `resolve_branch`, `base_ref`, `tree_signature`,
`parent_branch`. Anything that needs `git` (`has_changes`, `change_summary`,
`ignored_names`) shells out and is asked on demand only.

## Footguns

- **hunk 0.21 refuses its daemon when `$XDG_RUNTIME_DIR/hunk-mcp` is not
  0700** (0.20 created it with the umask). Symptom: `hunk session list --json`
  answers `{"sessions": []}`, a commit click toasts "cannot find this hunk
  window in the session daemon", the page stays on the working tree. Fix:
  `chmod 700` the directory, kill daemons born before it, reopen the page
  (a viewer launched while the daemon was dead does not reconnect). `hunk
  daemon serve` in the foreground prints the reason.
- Matching the session by `pid == child_pid` finds nothing (wrapper vs
  viewer); use the children set.
- Narrow terminals: hunk hides its sidebar under 73 columns and never brings
  it back by itself on resize; the extension re-opens panes on `SIGWINCH` and
  a 1 s tty-size poll because Collins widens the column while hunk starts.
- Two tabs on one worktree make `--repo` ambiguous; pid is the only key.
- Hunk cancels any open dialog on reload; a shell-side commit while a `D`
  confirmation is up closes it — by design, press the key again.

Related: `collins-session-mcp-tools` (`show_diff`), `collins-panel-dock`,
`collins-terminal-tab`.
