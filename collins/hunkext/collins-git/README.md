<!-- New in the ghackett fork of agent-session-manager (GPL-3.0). -->

# collins-git

The [hunk](https://hunk.dev) extension behind Collins' git page. Collins spawns
`hunk diff --watch --transparent-bg --no-sidebar --extension <this directory> …`
in the page's terminal, so hunk draws nothing but its review stream; the
commits and files panels, the action row, commit and fixup, stage all and the
parent branch are Collins' own GTK widgets beside it. What is left for an
extension is the handful of keys that need hunk's cursor — the hunk, the file
and the line under it — and a word back to Collins about where that cursor is.
It also runs on its own, outside Collins — see "Standalone" below.

## Keys

Live only while the working tree is loaded; in a commit or branch view the
keys say "read-only view" and do nothing.

| Key | Does |
|-----|------|
| `x` | stage the current hunk (unstage it, in the Staged view) — or, with an anchor set, the lines from the anchor to the cursor; with no hunk selected, the file — a binary or oversized file is refused with "use X" either way |
| `X` | stage / unstage the current file (a rename: both paths in one `git add -A -- a b`) |
| `v` | anchor a line range at the cursor line; painted amber (see "Line ranges") |
| `esc` | clear the anchor (silent when there is none; an open dialog takes `esc` first) |
| `D` | discard the current hunk — or the anchored range — from the working tree, after a confirmation; Unstaged view only. On a file deleted in the working tree it restores the file whole from the index (`git checkout -- <path>`, after the same confirmation); a new or renamed file is refused |

Every key avoids hunk's own defaults. They are registered as
`collins-git.stage-hunk`, `stage-file`, `set-anchor`, `clear-anchor` and
`discard`, and hunk lets a user rebind them under `[keybindings]` — but
**all five are pinned**: Collins' action row (*Stage hunk* / *Stage lines*,
*Anchor line* / *Clear anchor*, *Discard*) feeds hunk the bytes `x`, `v`,
escape and `D` through the terminal, since hunk's session CLI cannot run an
extension command by name. Rebind one and the button keeps pressing the key,
which then does whatever owns it. `X` has no button; it is pinned for the
same reason, so a later button can rely on it.

After each key the extension reloads the review in place through hunk's own
`hunk.app.refresh` and records the index mtime and HEAD it just showed in the
sidecar (`refreshed`, below), so Collins' freshness poll does not reload the
page a second time for the same move — a reload cancels whatever dialog is
open by then (`D`'s confirmation). When hunk has no refresh to run, nothing is
recorded and Collins' next tick reloads the page instead. What remains is by
design: a move made from a shell or by the agent while a `D` confirmation
stands still reloads the page, and the dialog closes; press the key again.

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

## The Collins contract (sidecar version 2)

Collins hands the extension a JSON sidecar named in the environment variable
`COLLINS_GIT_STATE`, created before hunk is spawned and deleted when the page
closes:

```json
{"version": 2, "untracked": true}
```

- `untracked` — whether the working-tree reviews Collins loads include
  untracked files (true by default; absent or garbled reads as true); Collins
  only. Informational here: the extension reloads through hunk's own refresh,
  which keeps whatever `--exclude-untracked` the load was made with.

The rest of the file is the extension's, written for Collins' native panels,
which read it on their 2 s poll (and, for the selection, at once — the
file's mtime is what they watch):

- `selection` — `{"path": "<path>", "hunkIndex": n | null}`, the file and
  hunk under hunk's cursor, or `null` when the cursor is on no file. Written
  on every `selection_changed` event (keyboard, mouse, `hunk session
  navigate`) and on every changeset event, only when it changed. This is how
  Collins' files list follows the cursor without waiting for its poll of
  `hunk session get`.
- `anchor` — `{"path": "<path>", "side": "old" | "new", "line": n}`, the line
  `v` was pressed on, or `null` once cleared (by `esc`, by a key that used it,
  or by a reload that lost the file). Collins' action row reads it to say
  *Stage lines* and *Clear anchor*.
- `refreshed` — `{"index": "<mtime ns>", "head": "<sha>"}`, the index file's
  mtime (a string: the number is past what a JSON double holds) and HEAD as
  the extension saw them right after reloading the review for a mutation of
  its own. Collins' freshness poll skips a move that matches.

Both sides read-merge-write (temp file + rename), tolerate a missing or garbled
file, and pass unknown keys through. Version 1 of the contract (the parent and
default branch names, the page size and the narrow-page level, which fed the
panes this extension used to draw) is gone with the panes; a v1 file's keys are
ignored.

## Standalone

```sh
hunk diff --extension /path/to/collins/hunkext/collins-git
```

Without `COLLINS_GIT_STATE` the five keys work exactly the same and nothing is
written anywhere.

## Tests and typecheck

```sh
cd collins/hunkext/collins-git
bun test                        # no node_modules needed
bun install && bun run typecheck   # tsc against hunk's types (dev only)
```

The tested modules import only `node:*`, local files and types from
`hunkdiff/extension`. There is no build step: hunk transpiles the files itself.
`bun.lock` and `node_modules` are not committed.

The extension never writes to the terminal (hunk owns it). To watch what it
does, set `COLLINS_GIT_DEBUG_LOG=/path/to/file` in hunk's environment: startup,
every changeset it decodes, every selection and anchor it writes, and each
anchor set or cleared are appended there.
