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
| `x` | stage the current hunk (unstage it, in the Staged view) — or, with an anchor set, the lines from the anchor to the cursor; with no hunk selected, the file — a binary or oversized file is refused with "use X" either way |
| `X` | stage / unstage the current file (a rename: both paths in one `git add -A -- a b`) |
| `v` | anchor a line range at the cursor line; painted amber (see "Line ranges") |
| `esc` | clear the anchor (silent when there is none; an open dialog takes `esc` first) |
| `D` | discard the current hunk — or the anchored range — from the working tree, after a confirmation; Unstaged view only. On a file deleted in the working tree it restores the file whole from the index (`git checkout -- <path>`, after the same confirmation); a new or renamed file is refused |
| `C` | commit the index: one input for the summary, `git commit -m` |
| `B` | commit the index with a summary and a body (two inputs; a blank body is none) |
| `F` | pick an unpushed commit from a list, confirm, and commit the index as `fixup! <sha>` for it — the rebase that folds it in is named, not run |
| `A` | stage all changes, after a confirmation naming the count |
| `U` | unstage all changes, likewise |
| `n` / `p` | load the next / previous row of the current commits-panel group |
| `P` | set the parent branch (a `select` over local branches; *Automatic* hands the choice back) |

Every key avoids hunk's own defaults. Users can rebind them under
`[keybindings]` as `collins-git.stage-hunk`, `stage-file`, `set-anchor`,
`clear-anchor`, `discard`, `commit`, `commit-with-body`, `fixup`, `stage-all`,
`unstage-all`, `next-row`, `previous-row`, `set-parent`.

`C`, `B` and `F` act on the index as it is — whatever `x`/`X`/`A` put there,
from either view — and refuse before asking anything when nothing is staged or
a rebase, merge, cherry-pick or revert is half-finished in the repository. They
run `git commit -q -m …`, so no editor opens, on an asynchronous runner so
hunk keeps painting while hooks and signing take their time: a `committing…`
toast stands meanwhile, a second commit key waits for the first, and the
review is reloaded whether the commit succeeded or not (hooks may have moved
the tree; one that outlives the 10-minute deadline may have been made).

`F` lists the commits panel's current group (`<parent>..HEAD`) minus anything
a remote-tracking ref reaches (`git log <parent>..HEAD --not --remotes`) — the
same rule the panel's `↑` marks follow, and never `@{upstream}..HEAD`, which
after a rebase onto a pushed base holds the base's own pushed commits, and
which a branch pushed without `-u` does not have at all. With no remotes at
all every commit is unpushed. It writes `fixup! <full sha>` in the subject
rather than `--fixup=`: subjects repeat, hashes do not, and `git rebase
--autosquash` matches either. The command it names to fold the fixup in is
`git rebase -i --autosquash --autostash <sha>^` — interactive on purpose,
since `--autosquash` without `-i` is ignored by every git before 2.44 (Debian
12 and Ubuntu 24.04 ship older); the todo opens already arranged, save and
quit.

## Line ranges

Hunk has no range selection, so the extension builds one from its cursor line
in two keystrokes and without a keyboard mode: `v` stores the cursor line as
the **anchor** and paints it amber through a line highlighter (`range-anchor`);
the user moves with hunk's own keys; `x` or `D` reads the cursor line again as
the **head** and acts on every hunk line between the two, inclusive, either
order, across hunks of one file. A range across two files is refused, and so is
one whose file changed since `v` (the review's patch text for the file, kept
with the anchor, is compared with the review's now) or since the review loaded
(the review's own hunk lines are compared with a fresh `git diff`). A reload —
`r`, a `--watch` reload, Collins' own — keeps the anchor when the same side
still shows the file with the same patch (an unrelated file saved, something
else staged): it is re-pointed at hunk's new id for the file and the amber mark
repaints, since the highlighter matches by path and patch rather than id.
Otherwise the anchor is dropped and a warning toast says so — never silently,
because `x` with no anchor stages the whole hunk.

The range is computed in **patch order** from the patch re-read at action time,
never by counting cursor stops: verified on hunk 0.20.1, `ctx.selection.currentLine`
advances with `j`/`k` in both layouts and reports the same addresses (`-` lines
old-side, `+` lines new-side, context rows new-side), but the *order* of stops
inside a replaced block differs — split visits `old 25, new 25, old 26`, stack
`old 25, old 26, new 25`. Addressing each hunk line by the side it exists on
(context lines answer to either) and ordering the two ends by their position in
the patch gives the same range in both.

The partial patch keeps the selected `+`/`-` lines, drops the unselected `+`
and demotes the unselected `-` to context when staging (the mirror image when
unstaging against `--cached` or discarding in the working tree), leaves out
hunks with nothing selected, and recomputes every hunk header; `git apply` gets
no `--recount`. It does get `--unidiff-zero` (every apply here does, hunk-level
`x` included): the source diff honours the user's `diff.context`, and with
`diff.context = 0` — or once the demoted lines are all the context a hunk has —
apply's rule that a hunk with no trailing context must sit at the end of the
file would refuse a perfectly good patch. The header goes out without its `old
mode` / `new mode` pair, so a range or a hunk never stages, unstages or reverts
a mode change; that stays behind for `X`. A range in a new or deleted file, or
in a rename, is refused with "use X"; a range that splits an end-of-file
newline change is refused too.

Hunk cancels an open dialog on any reload. The extension reloads the review
itself after each of its own mutations, and Collins' 2 s freshness poll would
reload it again for the same move — landing on the `D`, `C`, `B` or `F` dialog
you opened next — so the extension records the index mtime and HEAD it just
showed in the sidecar (`refreshed`, below) and Collins leaves a move that
matches alone. What remains is by design: a move made from a shell or by the
agent while a dialog stands still reloads the page, and the dialog closes;
press the key again.

## The Collins contract

Collins hands the extension its parameters through a JSON sidecar named in the
environment variable `COLLINS_GIT_STATE`, created before hunk is spawned and
deleted when the page closes:

```json
{"version": 1, "parent": "main", "parentSource": "auto", "default": "main", "logPage": 20, "untracked": true}
```

- `parent` — the parent branch *name* (each side resolves it: a local branch,
  else `origin/<name>`, `upstream/<name>`, …). Collins writes the automatic
  rung (an open PR's base, else the default branch) or the user's pick.
- `parentSource` — `"auto"` or `"user"`. The extension writes `"user"` plus
  `parent` when the user picks a branch, or `"auto"` (leaving `parent` alone)
  for *Automatic*; Collins then recomputes and rewrites `parent`.
- `default` — the default branch name; Collins only.
- `logPage` — commits per group page, 5..500; Collins only.
- `untracked` — whether working-tree reviews include untracked files (true by
  default; absent or garbled reads as true); Collins only. When false the
  extension adds `--exclude-untracked` to every `diff` tail it sends, so its
  loads agree with Collins' own — hunk resolves the option afresh on each
  reload, and a bare tail would bring the files back — and leaves the `?`
  rows out of the files panel's status-fed section (the UNSTAGED list of a
  staged load), rebuilt when the sidecar changes.
- `refreshed` — `{"index": "<mtime ns>", "head": "<sha>"}`, the index file's
  mtime (a string: the number is past what a JSON double holds) and HEAD as
  the extension saw them right after reloading the review for a mutation of
  its own; extension only. Collins' freshness poll skips a move that matches.

Both sides read-merge-write (temp file + rename), tolerate a missing or garbled
file, and pass unknown keys through. The extension polls the file every 2 s
(`fs.watchFile`), rebuilds the commits panel when the config in it changed (its
own `refreshed` writes move the file too), and re-reads it on every
`changeset_loaded` / `session_reload`.

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
page; untracked files in. A parent picked with `P` is kept in memory for the
session.

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
