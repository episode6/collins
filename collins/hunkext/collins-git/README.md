<!-- New in the ghackett fork of agent-session-manager (GPL-3.0). -->

# collins-git

The [hunk](https://hunk.dev) extension behind Collins' git page. Collins spawns
`hunk diff --watch --transparent-bg --extension <this directory> …` in the page's
terminal; the extension turns hunk's sidebar into a git panel. It also runs on
its own, outside Collins — see "Standalone" below.

## What it shows

Two panes on the left of hunk's review stream:

- **Commits** — one group per branch of interest, top to bottom: the current
  branch (a `working tree` row first, then its own commits `<parent>..HEAD`,
  newest first, unpushed ones marked `↑`), the parent branch (its commits not on
  the default branch; omitted when the parent *is* the default), and the default
  branch (its most recent page of commits and a `load more…` row). The loaded
  row carries `▸`. A click loads a row into the same window:

  | Row                    | Loads                                  |
  |------------------------|----------------------------------------|
  | `working tree`         | `diff` (unstaged)                      |
  | a commit               | `show <sha>`                           |
  | current-branch header  | `diff <parent>...HEAD`                 |
  | parent-branch header   | `diff <default>...<parent>`            |
  | default-branch header  | `diff <oldest listed>^..<tip>`         |

  A right click on the pane opens *Set parent branch…*.

- **Files** — replaces hunk's own files pane: the loaded changeset's files in
  review order with `+`/`-` counts, the selected one highlighted. While the
  working tree is loaded it splits into `UNSTAGED · n` and `STAGED · n`; hunk
  holds one side at a time, so the loaded side is live and clicking a file (or
  the header) on the other side reloads to that side and selects the file.

Hunk hides its sidebar area when the terminal is too narrow for a pane (0.20
shows the commits pane from about 75 columns and both from about 100) and does
not bring it back when the terminal grows, so the extension re-opens whatever
panes are open when the width grows — on `SIGWINCH`, and from a one-second
poll of the tty size, since Collins widens its git column while hunk is still
starting. A pane closed with hunk's own `s` stays closed.

## Keys

Live only while the working tree is loaded; in a commit or branch view the
staging keys say "read-only view" and do nothing.

| Key | Does |
|-----|------|
| `x` | stage the current hunk (unstage it, in the Staged view); with no hunk selected, the file — a binary or oversized file is refused with "use X" either way |
| `X` | stage / unstage the current file (a rename: both paths in one `git add -A -- a b`) |
| `A` | stage all changes, after a confirmation naming the count |
| `U` | unstage all changes, likewise |
| `n` / `p` | load the next / previous row of the current commits-panel group |
| `P` | set the parent branch (a `select` over local branches; *Automatic* hands the choice back) |

Every key avoids hunk's own defaults. Users can rebind them under
`[keybindings]` as `collins-git.stage-hunk`, `stage-file`, `stage-all`,
`unstage-all`, `next-row`, `previous-row`, `set-parent`.

## The Collins contract

Collins hands the extension its parameters through a JSON sidecar named in the
environment variable `COLLINS_GIT_STATE`, created before hunk is spawned and
deleted when the page closes:

```json
{"version": 1, "parent": "main", "parentSource": "auto", "default": "main", "logPage": 20}
```

- `parent` — the parent branch *name* (each side resolves it: a local branch,
  else `origin/<name>`, `upstream/<name>`, …). Collins writes the automatic
  rung (an open PR's base, else the default branch) or the user's pick.
- `parentSource` — `"auto"` or `"user"`. The extension writes `"user"` plus
  `parent` when the user picks a branch, or `"auto"` (leaving `parent` alone)
  for *Automatic*; Collins then recomputes and rewrites `parent`.
- `default` — the default branch name; Collins only.
- `logPage` — commits per group page, 5..500; Collins only.

Both sides read-merge-write (temp file + rename), tolerate a missing or garbled
file, and pass unknown keys through. The extension polls the file every 2 s
(`fs.watchFile`) and re-reads it on every `changeset_loaded` / `session_reload`.

Loads go through hunk's own daemon: `<hunk> session reload <id> --json -- <tail>`,
with the session id resolved once from `session list --json` by `process.pid`.
A CLI timeout ("Timed out waiting for the Hunk session daemon") is treated as
unknown, not failed — the viewer reloads anyway and `session_reload` says what
it did.

## Standalone

```sh
hunk diff --extension /path/to/collins/hunkext/collins-git
```

Without `COLLINS_GIT_STATE` the extension guesses: default branch from
`origin/HEAD`, else `main`, else `master`; parent = default; 20 commits per
page. A parent picked with `P` is kept in memory for the session.

## Tests and typecheck

```sh
cd collins/hunkext/collins-git
bun test                        # no node_modules needed
bun install && bun run typecheck   # tsc against hunk's types (dev only)
```

The tested modules import only `node:*`, local files and types from
`hunkdiff/extension`; the two `.tsx` panes are never imported by a test. There
is no build step: hunk transpiles the files itself and serves its own React and
OpenTUI to them. `bun.lock` and `node_modules` are not committed.

The extension never writes to the terminal (hunk owns it). To watch what it
does, set `COLLINS_GIT_DEBUG_LOG=/path/to/file` in hunk's environment: startup,
every changeset it decodes, and each resize that re-revealed the panes are
appended there.
