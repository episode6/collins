// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
// contributors); see collins/THIRD_PARTY_LICENSES.md.

/**
 * collins-git: the hunk extension Collins loads into its git page.
 *
 * Only the keys that need hunk's cursor live here; everything else the git
 * page does (the commits and files lists, commit and fixup, stage all, the
 * parent branch) is native GTK in Collins, and hunk runs with
 * `--no-sidebar` so it draws nothing but its review. Five commands: `x`
 * stages or unstages the current hunk — or, with an anchor set, the lines
 * from the anchor to the cursor — `X` the current file, `v` anchors a line
 * range (`esc` clears it) and `D` discards the current hunk or range from
 * the working tree after a confirmation (a deleted file is restored
 * whole). Collins' buttons press the same keys through the terminal, so
 * the keys are pinned (see the README).
 *
 * What this extension tells Collins goes through the sidecar (sidecar.ts):
 * `selection` — the file and hunk under hunk's cursor, written on every
 * `selection_changed` — so the native files list follows the cursor at
 * once rather than on Collins' next poll; `anchor` — the line `v` was
 * pressed on — so the native buttons say "Stage lines" and "Clear anchor";
 * and `refreshed` — the index mtime and HEAD right after a mutation of
 * ours reloaded the review — so Collins' freshness poll leaves a move hunk
 * has already shown alone.
 *
 * This file only composes: git.ts runs git, staging.ts plans the keys,
 * range.ts does the line-range arithmetic, anchor.ts remembers where `v`
 * was pressed and paints it, model.ts reads hunk's title, sidecar.ts talks
 * to Collins. Runs standalone too: `hunk diff --extension <this dir>` with
 * no sidecar is the same five keys, telling nobody.
 */

import { appendFileSync } from "node:fs";
import type {
  ExtensionChangeset,
  ExtensionCommandContext,
  ExtensionDiffFile,
  ExtensionEventContext,
  ExtensionNotifyType,
  HunkExtensionAPI,
} from "hunkdiff/extension";
import { anchorMarks, clearAnchor, currentAnchor, rebindAnchor, setAnchor, type Anchor } from "./anchor.ts";
import {
  applyCached,
  applyWorktreeReverse,
  gitRunner,
  readFilePatch,
  repoToplevel,
  restoreFile,
  stageFiles,
  treeMark,
  unstageFiles,
  type GitResult,
  type GitRunner,
} from "./git.ts";
import { decodeTitle, type Loaded, type Side } from "./model.ts";
import type { LineAddress } from "./range.ts";
import { configFromEnv, writeSidecar, type SidecarAnchor, type SidecarSelection } from "./sidecar.ts";
import { describe, planDiscard, planFileToggle, planHunkToggle, planRangeToggle } from "./staging.ts";

const READ_ONLY = "read-only view — load working tree first";

/** The line highlighter that paints the range anchor (`v`) in amber. */
const ANCHOR_HIGHLIGHTER = "range-anchor";

/** Hunk's own command that turns its cursor line on, for a `v` pressed with it off. */
const CURSOR_LINE_ROW = "hunk.view.cursorLineRow";

/** Hunk's own command that reloads the review in place, after a mutation of ours. */
const REFRESH = "hunk.app.refresh";

const DISCARD_BODY = "The working tree will be reverted. This cannot be undone — the changes exist nowhere else.";
const RESTORE_BODY = "The file comes back from the index, as the last stage or commit left it.";

/** A file the debugging run can watch; never the terminal, which hunk owns. */
const DEBUG_LOG = process.env.COLLINS_GIT_DEBUG_LOG;

function debug(message: string): void {
  if (DEBUG_LOG !== undefined && DEBUG_LOG !== "") {
    try {
      appendFileSync(DEBUG_LOG, `${message}\n`);
    } catch {
      // Diagnostics must never take the extension down.
    }
  }
}

type Notify = (message: string, type?: ExtensionNotifyType) => void;

/** The parts of a `selection_changed` payload this extension keeps. */
interface Cursor {
  readonly fileId: string | null;
  readonly hunkIndex: number | null;
}

export default function registerCollinsGit(hunk: HunkExtensionAPI): void {
  const sidecarPath = configFromEnv(process.env).path;

  let cwd = "";
  let git: GitRunner | null = null;
  let repo = "";
  let loaded: Loaded = { kind: "other" };
  let files: readonly ExtensionDiffFile[] = [];
  let lastChangesetId: string | null = null;
  /** Hunk's cursor as last reported, by the ids of the changeset it was reported in. */
  let cursor: Cursor = { fileId: null, hunkIndex: null };
  /** The `selection` and `anchor` last written to the sidecar (as JSON), so an unchanged one is not rewritten. */
  let selectionWritten: string | null = null;
  let anchorWritten: string | null = null;

  const log = (message: string): void => {
    hunk.log(message);
    debug(message);
  };

  /**
   * The runner for *dir*'s repository, built on the working tree's top
   * level rather than *dir* itself: hunk starts in the agent's cwd, which
   * may be a subdirectory, and every path hunk hands us is top-relative
   * (see repoToplevel). *dir* is only the cache key.
   */
  function ensureGit(dir: string): GitRunner {
    if (git === null || dir !== cwd) {
      cwd = dir;
      const top = repoToplevel(gitRunner(dir));
      git = gitRunner(top ?? dir);
      repo = top === null ? "" : top.slice(top.lastIndexOf("/") + 1);
    }
    return git;
  }

  function live(): Side | null {
    return loaded.kind === "unstaged" || loaded.kind === "staged" ? loaded.kind : null;
  }

  // -- the sidecar: what Collins reads back ---------------------------------------

  /** Write one key when its value differs from what was last written; true when written (or unchanged). */
  function publish(key: "selection" | "anchor", value: SidecarSelection | SidecarAnchor | null): void {
    if (sidecarPath === null) {
      return;
    }
    const text = JSON.stringify(value);
    if (key === "selection" ? text === selectionWritten : text === anchorWritten) {
      return;
    }
    if (!writeSidecar(sidecarPath, { [key]: value })) {
      log(`could not record the ${key} in the sidecar`);
      return;
    }
    if (key === "selection") {
      selectionWritten = text;
    } else {
      anchorWritten = text;
    }
    debug(`${key} ${text}`);
  }

  /** The cursor's file and hunk as the sidecar names them: by path, since ids change on every reload. */
  function selectionOf(at: Cursor): SidecarSelection | null {
    const file = at.fileId === null ? undefined : files.find((entry) => entry.id === at.fileId);
    return file === undefined ? null : { path: file.path, hunkIndex: at.hunkIndex };
  }

  function publishSelection(): void {
    publish("selection", selectionOf(cursor));
  }

  function publishAnchor(): void {
    const anchor = currentAnchor();
    publish("anchor", anchor === null ? null : { path: anchor.path, side: anchor.side, line: anchor.line });
  }

  /** Write the tree mark hunk has just reloaded for into the sidecar, for Collins' poll. */
  function recordRefreshed(): void {
    if (sidecarPath === null) {
      return;
    }
    const mark = treeMark(ensureGit(cwd));
    if (mark !== null && !writeSidecar(sidecarPath, { refreshed: mark })) {
      log("could not record the refresh in the sidecar");
    }
  }

  // -- the review's events --------------------------------------------------------

  function onChangeset(changeset: ExtensionChangeset, ctx: ExtensionEventContext): void {
    // One reload raises both `changeset_loaded` and `session_reload` with
    // the same changeset; the second carries nothing new.
    if (changeset.id === lastChangesetId) {
      files = changeset.files;
      return;
    }
    lastChangesetId = changeset.id;
    ensureGit(ctx.cwd);
    files = changeset.files;
    loaded = decodeTitle(changeset.title, repo);
    // A reload renumbers file ids and may move every line. The anchor
    // follows its file when the same side is loaded and the file's patch
    // is what it was (an unrelated file saved, something else staged);
    // otherwise it names nothing now, and the user is told — silently
    // dropping it would turn the next `x` into a whole-hunk stage.
    const fate = rebindAnchor(files, live());
    if (fate === "dropped") {
      ctx.notify("anchor cleared by the reload — press v again", "warning");
    }
    debug(`changeset id=${changeset.id} title=${JSON.stringify(changeset.title)} loaded=${JSON.stringify(loaded)} files=${files.length} anchor=${fate}`);
    publishAnchor();
    // The cursor's id belongs to the changeset that reported it; hunk
    // says where the cursor is now through `selection_changed`. Until it
    // does, the file the old id names in the new list (or none) is the
    // best word there is — and only a change is written.
    publishSelection();
  }

  function onSelectionChanged(payload: Cursor): void {
    cursor = { fileId: payload.fileId, hunkIndex: payload.hunkIndex };
    publishSelection();
  }

  // -- the keys --------------------------------------------------------------------

  function reportGit(notify: Notify, result: GitResult, what: string): boolean {
    if (result.ok) {
      return true;
    }
    const line = firstLine(result.stderr) || firstLine(result.stdout) || `${what} failed`;
    notify(line, "error");
    log(`${what}: ${line}`);
    return false;
  }

  /**
   * After a mutation: reload the review in place through hunk's own
   * refresh, tell Collins the index and HEAD as they are now have been
   * shown (so its own freshness reload — which would cancel any dialog
   * open by the time it lands — stays home), and say what happened. When
   * hunk has no refresh to run, `refreshed` stays unwritten and Collins'
   * next tick reloads the page instead.
   */
  function afterMutation(ctx: ExtensionCommandContext, message: string): void {
    refreshAfter(ctx);
    ctx.notify(message, "info");
  }

  function refreshAfter(ctx: ExtensionCommandContext): void {
    if (ctx.commands.execute(REFRESH)) {
      recordRefreshed();
    } else {
      log(`${REFRESH} is not available; Collins reloads the page on its next tick`);
    }
  }

  function toggleFile(ctx: ExtensionCommandContext, file: ExtensionDiffFile, side: Side): void {
    const runner = ensureGit(ctx.cwd);
    const plan = planFileToggle({ file, live: side });
    const result = plan.stage ? stageFiles(runner, plan.paths) : unstageFiles(runner, plan.paths);
    if (reportGit(ctx.notify, result, plan.label)) {
      afterMutation(ctx, describe(plan));
    }
  }

  /**
   * The range a ranged `x`/`D` acts on — the anchor and the head, the
   * cursor line the command fired from — or the reason it cannot, as a
   * warning. Both ends must sit in one file, with the cursor line on, and
   * the file must be what it was when `v` was pressed: the review's patch
   * for it is compared with the one the anchor kept, so a working copy
   * that changed under the anchor (and reloaded under `--watch`) is
   * caught even when the anchor survived the reload by path.
   */
  function rangeEnds(
    ctx: ExtensionCommandContext,
    file: ExtensionDiffFile,
    anchor: Anchor,
  ): { anchor: LineAddress; head: LineAddress } | string {
    const head = ctx.selection.currentLine;
    if (head === null) {
      return "cursor line is off — press v again once it is on";
    }
    if (file.path !== anchor.path) {
      return "range spans two files — esc, then v again in one file";
    }
    if (file.patch !== anchor.patch) {
      return `${file.path} changed since the anchor was set — press r, then v again`;
    }
    return { anchor: { side: anchor.side, line: anchor.line }, head: { side: head.side, line: head.line } };
  }

  function stageHunk(ctx: ExtensionCommandContext): void {
    const side = live();
    if (side === null) {
      ctx.notify(READ_ONLY, "info");
      return;
    }
    const file = ctx.selection.file;
    if (file === null) {
      ctx.notify("no file selected", "info");
      return;
    }
    const anchor = currentAnchor();
    if (anchor !== null) {
      stageRange(ctx, file, side, anchor);
      return;
    }
    // No hunk selected (the cursor on the file, or a file with no hunks to
    // select — a binary, one skipped for size) goes through the planner
    // too: it is the planner that knows which of those to refuse.
    const runner = ensureGit(ctx.cwd);
    const patch = readFilePatch(runner, file.path, side === "staged", file.previousPath);
    if (!reportGit(ctx.notify, patch, `git diff ${file.path}`)) {
      return;
    }
    const plan = planHunkToggle({ file, hunkIndex: ctx.selection.hunkIndex, live: side, patchText: patch.stdout });
    if (plan.kind === "refuse") {
      ctx.notify(plan.reason, "warning");
      return;
    }
    if (plan.kind === "file") {
      toggleFile(ctx, file, side);
      return;
    }
    const result = applyCached(runner, plan.patch, plan.reverse);
    if (reportGit(ctx.notify, result, plan.label)) {
      afterMutation(ctx, describe(plan));
    }
  }

  /** `x` with an anchor: stage or unstage the lines from the anchor to the cursor. */
  function stageRange(ctx: ExtensionCommandContext, file: ExtensionDiffFile, side: Side, anchor: Anchor): void {
    const ends = rangeEnds(ctx, file, anchor);
    if (typeof ends === "string") {
      ctx.notify(ends, "warning");
      return;
    }
    const runner = ensureGit(ctx.cwd);
    const patch = readFilePatch(runner, file.path, side === "staged", file.previousPath);
    if (!reportGit(ctx.notify, patch, `git diff ${file.path}`)) {
      return;
    }
    const plan = planRangeToggle({
      file,
      reviewPatchText: file.patch,
      anchor: ends.anchor,
      head: ends.head,
      live: side,
      patchText: patch.stdout,
    });
    if (plan.kind === "refuse") {
      ctx.notify(plan.reason, "warning"); // the anchor stays: the user can move the head and retry
      return;
    }
    const result = applyCached(runner, plan.patch, plan.reverse);
    if (reportGit(ctx.notify, result, plan.label)) {
      clearAnchor();
      publishAnchor();
      afterMutation(ctx, describe(plan));
    }
  }

  /**
   * `D`: revert the current hunk, or the anchored range, in the working
   * tree — after a confirmation, since the change exists nowhere else. The
   * patch is read before asking and again after: Collins reloads the page
   * when the index moves and hunk's `--watch` reloads it when a file does,
   * either of which cancels an open dialog, but an agent editing the file
   * while the dialog stood would not be caught by that alone.
   */
  async function discard(ctx: ExtensionCommandContext): Promise<void> {
    const side = live();
    if (side === null) {
      ctx.notify(READ_ONLY, "info");
      return;
    }
    const file = ctx.selection.file;
    if (file === null) {
      ctx.notify("no file selected", "info");
      return;
    }
    const anchor = currentAnchor();
    let range: { anchor: LineAddress; head: LineAddress } | null = null;
    if (anchor !== null) {
      const ends = rangeEnds(ctx, file, anchor);
      if (typeof ends === "string") {
        ctx.notify(ends, "warning");
        return;
      }
      range = ends;
    }
    const runner = ensureGit(ctx.cwd);
    const patch = readFilePatch(runner, file.path, false, file.previousPath);
    if (!reportGit(ctx.notify, patch, `git diff ${file.path}`)) {
      return;
    }
    const plan = planDiscard({
      file,
      reviewPatchText: file.patch,
      live: side,
      hunkIndex: ctx.selection.hunkIndex,
      range,
      patchText: patch.stdout,
    });
    if (plan.kind === "refuse") {
      ctx.notify(plan.reason, "warning");
      return;
    }
    const confirmed = await ctx.dialogs.confirm(
      plan.kind === "restore"
        ? { title: `Restore ${plan.path}?`, body: RESTORE_BODY, confirmLabel: "Restore", cancelLabel: "Keep" }
        : {
            title: `Discard ${plan.label.replace(/^discarded /, "")}?`,
            body: DISCARD_BODY,
            confirmLabel: "Discard",
            cancelLabel: "Keep",
          },
    );
    if (!confirmed) {
      return;
    }
    const again = readFilePatch(runner, file.path, false, file.previousPath);
    if (!reportGit(ctx.notify, again, `git diff ${file.path}`)) {
      return;
    }
    if (again.stdout !== patch.stdout) {
      ctx.notify(`${file.path} changed while asking — press r`, "warning");
      return;
    }
    const result = plan.kind === "restore" ? restoreFile(runner, plan.path) : applyWorktreeReverse(runner, plan.patch);
    if (reportGit(ctx.notify, result, plan.label)) {
      clearAnchor();
      publishAnchor();
      afterMutation(ctx, describe(plan));
    }
  }

  /** `v`: remember the cursor line as the start of a range and paint it. */
  function anchorHere(ctx: ExtensionCommandContext): void {
    const side = live();
    if (side === null) {
      ctx.notify(READ_ONLY, "info");
      return;
    }
    const file = ctx.selection.file;
    if (file === null) {
      ctx.notify("put the cursor on a diff line", "info");
      return;
    }
    const line = ctx.selection.currentLine;
    if (line === null) {
      // Hunk's cursor line is off (`cursor_line = "off"`, or hunk.view.cursorLineOff):
      // without it no command can learn which line the user means.
      ctx.commands.execute(CURSOR_LINE_ROW);
      ctx.notify("cursor line turned on — press v again", "info");
      return;
    }
    if (ctx.selection.hunkIndex === null) {
      ctx.notify("put the cursor on a diff line", "info");
      return;
    }
    const previous = currentAnchor();
    setAnchor({
      fileId: file.id,
      path: file.path,
      patch: file.patch,
      live: side,
      side: line.side,
      line: line.line,
    });
    if (previous !== null && previous.fileId !== file.id) {
      ctx.highlights.refresh(ANCHOR_HIGHLIGHTER, { fileId: previous.fileId });
    }
    ctx.highlights.refresh(ANCHOR_HIGHLIGHTER, { fileId: file.id });
    debug(`anchor set ${file.path} ${line.side}:${line.line}`);
    publishAnchor();
    ctx.notify(`anchor set at ${line.side} ${line.line} · move, then x or D · esc clears`, "info");
  }

  /** `esc`: forget the anchor. Says nothing when there was none. */
  function dropAnchor(ctx: ExtensionCommandContext): void {
    const previous = currentAnchor();
    debug(`anchor clear ${previous === null ? "(none)" : `${previous.path} ${previous.side}:${previous.line}`}`);
    if (previous === null || !clearAnchor()) {
      return;
    }
    ctx.highlights.refresh(ANCHOR_HIGHLIGHTER, { fileId: previous.fileId });
    publishAnchor();
    ctx.notify("anchor cleared", "info");
  }

  function stageFile(ctx: ExtensionCommandContext): void {
    const side = live();
    if (side === null) {
      ctx.notify(READ_ONLY, "info");
      return;
    }
    const file = ctx.selection.file;
    if (file === null) {
      ctx.notify("no file selected", "info");
      return;
    }
    toggleFile(ctx, file, side);
  }

  // -- registration -----------------------------------------------------------------

  hunk.registerLineHighlighter({
    id: ANCHOR_HIGHLIGHTER,
    highlight: (input) => anchorMarks(input.file, currentAnchor()),
  });

  hunk.registerCommand({ id: "stage-hunk", title: "Stage or unstage the current hunk or range", key: "x" }, stageHunk);
  hunk.registerCommand({ id: "stage-file", title: "Stage or unstage the current file", key: "X" }, stageFile);
  hunk.registerCommand({ id: "set-anchor", title: "Anchor a line range at the cursor", key: "v" }, anchorHere);
  hunk.registerCommand({ id: "clear-anchor", title: "Clear the range anchor", key: "escape" }, dropAnchor);
  hunk.registerCommand({ id: "discard", title: "Discard the current hunk or range", key: "D" }, (ctx) =>
    discard(ctx),
  );

  hunk.on("startup", (event) => {
    ensureGit(event.cwd);
    debug(`startup cwd=${event.cwd} sidecar=${sidecarPath ?? "-"} pid=${process.pid}`);
  });
  hunk.on("changeset_loaded", (event, ctx) => onChangeset(event.changeset, ctx));
  hunk.on("session_reload", (event, ctx) => onChangeset(event.changeset, ctx));
  hunk.on("selection_changed", (event) => onSelectionChanged(event));
}

/** The first non-blank line of a git message, trimmed. */
function firstLine(text: string): string {
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed !== "") {
      return trimmed;
    }
  }
  return "";
}
