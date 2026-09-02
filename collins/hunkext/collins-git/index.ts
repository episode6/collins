// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from joshedler/hunk-git-lite, muzomer/hunk-commit and
// sadick254/hunk-commit-log (MIT, © 2026 Josh Edler, © 2026 hunk-jj-stage
// contributors, © 2026 Sadick); see collins/THIRD_PARTY_LICENSES.md.

/**
 * collins-git: the hunk extension Collins loads into its git page.
 *
 * Two panes on the left of hunk's review — the commits panel (current
 * branch with a `working tree` row, parent branch, default branch) and the
 * files panel that takes over hunk's own (`replaces: "hunk:files"`), split
 * into Unstaged and Staged while the working tree is loaded — plus the
 * keys that act on what is shown: `x` stages or unstages the current hunk,
 * `X` the current file, `A`/`U` everything (after a confirmation), `v`
 * anchors a line range that the next `x` or `D` acts on (`esc` clears it),
 * `D` discards the current hunk or range from the working tree after a
 * confirmation, `C`/`B` commit the index with a summary (and a body), `F`
 * makes a `fixup!` commit for an unpushed commit picked from a list,
 * `n`/`p` walk the current group of the commits panel, `P` (or a right
 * click on the panel) picks the parent branch.
 *
 * This file only composes: git.ts runs git, model.ts builds rows, store.ts
 * holds what the panes paint, session.ts reloads the window, sidecar.ts
 * talks to Collins, staging.ts plans the keys, range.ts does the line-range
 * arithmetic, anchor.ts remembers where `v` was pressed and paints it.
 * Runs standalone too: `hunk diff --extension <this dir>` with no sidecar
 * guesses the branches.
 */

import { appendFileSync } from "node:fs";
import { basename } from "node:path";
import type {
  ExtensionChangeset,
  ExtensionCommandContext,
  ExtensionDialogs,
  ExtensionDiffFile,
  ExtensionEventContext,
  ExtensionNotifyType,
  ExtensionPaneControls,
  HunkExtensionAPI,
} from "hunkdiff/extension";
import { anchorMarks, clearAnchor, currentAnchor, rebindAnchor, setAnchor, type Anchor } from "./anchor.ts";
import { CommitsPane } from "./commits.tsx";
import { FilesPane } from "./files.tsx";
import {
  applyCached,
  applyWorktreeReverse,
  commit,
  commitFixup,
  currentBranch,
  gitRunner,
  gitRunnerAsync,
  inProgressOperation,
  localBranches,
  parentOf,
  readFilePatch,
  readLog,
  readStatus,
  repoToplevel,
  resolveBranch,
  restoreFile,
  revParse,
  stageAll,
  stagedPaths,
  stageFiles,
  treeMark,
  unpushedCommits,
  unpushedShas,
  unstageAll,
  unstageFiles,
  type AsyncGitRunner,
  type Commit,
  type GitResult,
  type GitRunner,
  type Status,
} from "./git.ts";
import {
  buildRows,
  decodeTail,
  decodeTitle,
  loadedRow,
  neighbour,
  sideTail,
  withoutUntracked,
  type BranchRef,
  type Group,
  type Loaded,
  type Row,
  type Side,
} from "./model.ts";
import {
  firstLine,
  hunkRunner,
  onPendingChange,
  pendingLoad,
  requestLoad,
  type Report,
  type SessionDeps,
} from "./session.ts";
import {
  configFromEnv,
  effectiveConfig,
  readSidecar,
  watchSidecar,
  writeSidecar,
  type SidecarConfig,
} from "./sidecar.ts";
import type { LineAddress } from "./range.ts";
import { describe, planAll, planDiscard, planFileToggle, planHunkToggle, planRangeToggle } from "./staging.ts";
import { publishCommits, publishFiles, setPaneHandlers } from "./store.ts";

/** The bus event a right click on the commits panel raises. */
export const SET_PARENT_EVENT = "collins-git:set-parent";

const READ_ONLY = "read-only view — load working tree first";

/** The line highlighter that paints the range anchor (`v`) in amber. */
const ANCHOR_HIGHLIGHTER = "range-anchor";

/** Hunk's own command that turns its cursor line on, for a `v` pressed with it off. */
const CURSOR_LINE_ROW = "hunk.view.cursorLineRow";

const DISCARD_BODY = "The working tree will be reverted. This cannot be undone — the changes exist nowhere else.";
const RESTORE_BODY = "The file comes back from the index, as the last stage or commit left it.";

/** How long a commit runs before a toast says so: longer than a hookless commit, shorter than a lint hook. */
const COMMIT_TOAST_MS = 700;

/** The panes this extension registers, in registration (left-to-right) order. */
const PANE_IDS = ["commits", "files"] as const;

/** How long a burst of terminal resizes settles before the panes are re-revealed. */
const RESIZE_SETTLE_MS = 300;

/**
 * How often the terminal's width is re-read besides SIGWINCH: a resize that
 * lands while hunk is still starting (Collins widens its git column as the
 * page settles) precedes any handler this extension could install, and
 * `process.stdout.columns` is what the process saw at start.
 */
const RESIZE_POLL_MS = 1_000;

/** The terminal's current width in columns, by ioctl; 0 when there is no tty. */
function readColumns(): number {
  const out = process.stdout as { getWindowSize?: () => [number, number]; columns?: number };
  try {
    const size = out.getWindowSize?.();
    if (size !== undefined && Number.isFinite(size[0]) && size[0] > 0) {
      return size[0];
    }
  } catch {
    // Not a tty (a test, a pipe): fall through.
  }
  return out.columns ?? 0;
}

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

export default function registerCollinsGit(hunk: HunkExtensionAPI): void {
  const sidecarPath = configFromEnv(process.env).path;

  let cwd = "";
  let git: GitRunner | null = null;
  /** The same repository, for the commits: they may run as long as their hooks take. */
  let gitAsync: AsyncGitRunner | null = null;
  /** Whether a commit is out — a second `C`/`B`/`F` waits for it rather than racing it. */
  let committing = false;
  let repo = "";
  /** The user's pick when there is no sidecar to keep it in (standalone runs). */
  let memoryParent: string | null = null;
  /**
   * After the user chose "Automatic", the sidecar still names their old
   * pick until Collins recomputes it; until the file changes, the parent
   * shown is the default branch instead.
   */
  let awaitingAuto = false;
  let loaded: Loaded = { kind: "foreign", tail: "" };
  /** The sha a `show <ref>` title's ref resolves to, for matching a row. */
  let loadedSha: string | null = null;
  let files: readonly ExtensionDiffFile[] = [];
  let lastChangesetId: string | null = null;
  let status: Status | null = null;
  let rows: Row[] = [];
  let pendingSelectPath: string | null = null;
  let error: string | null = null;
  const pages: Record<Group, number> = { current: 1, parent: 1, default: 1 };
  let unwatch: (() => void) | null = null;
  /** The sidecar's config as last read, so a rewrite that changed none of it (our own `refreshed` record) costs no git. */
  let sidecarSeen = "";
  /**
   * The pane controls of the newest event context, for the resize hook.
   * Controls are leased to one review generation: after any reload (a
   * commit clicked, `r`, a `--watch` reload, our own `hunk.app.refresh`)
   * the startup event's `isOpen` answers false for every pane and its
   * `open` only warns — so every changeset event replaces them.
   */
  let panes: ExtensionPaneControls | null = null;
  let columns = readColumns();
  let resizeTimer: ReturnType<typeof setTimeout> | null = null;
  let resizePoll: ReturnType<typeof setInterval> | null = null;

  const log = (message: string): void => {
    hunk.log(message);
    debug(message);
  };

  /**
   * The runner for *dir*'s repository, built on the working tree's top
   * level rather than *dir* itself: hunk starts in the agent's cwd, which
   * may be a subdirectory, and every path hunk and `git status` hand us is
   * top-relative (see repoToplevel). *dir* is only the cache key.
   */
  function ensureGit(dir: string): GitRunner {
    if (git === null || dir !== cwd) {
      cwd = dir;
      const top = repoToplevel(gitRunner(dir));
      git = gitRunner(top ?? dir);
      gitAsync = gitRunnerAsync(top ?? dir);
      repo = top === null ? "" : basename(top);
      error = top === null ? "not a git repository" : null;
    }
    return git;
  }

  function ensureGitAsync(dir: string): AsyncGitRunner {
    ensureGit(dir);
    return gitAsync!;
  }

  /** The sidecar's config, as a string the watcher can compare. */
  function sidecarConfigText(): string {
    return sidecarPath === null ? "" : JSON.stringify(readSidecar(sidecarPath));
  }

  function config(): SidecarConfig {
    const runner = ensureGit(cwd);
    let sidecar = sidecarPath !== null ? readSidecar(sidecarPath) : null;
    if (sidecar === null && memoryParent !== null) {
      sidecar = { parent: memoryParent, parentSource: "user", default: null, logPage: 20, untracked: true };
    }
    if (sidecar !== null && awaitingAuto && sidecarPath !== null) {
      sidecar = { ...sidecar, parent: null, parentSource: "auto" };
    }
    return effectiveConfig(sidecar, runner);
  }

  /**
   * What every load of ours runs with: the hunk that runs us, our own pid,
   * and Collins' untracked switch — read off the sidecar as each reload
   * goes out, so a `diff` load never brings back files Collins hides.
   */
  const sessionDeps: SessionDeps = {
    run: hunkRunner(),
    pid: process.pid,
    excludeUntracked: () => !config().untracked,
  };

  function readPage(runner: GitRunner, range: string[], group: Group, logPage: number) {
    const limit = logPage * pages[group];
    const commits = readLog(runner, range, { limit: limit + 1 });
    return { commits: commits.slice(0, limit), more: commits.length > limit };
  }

  /** Re-read git and rebuild the commits panel. */
  function refreshCommits(): void {
    const runner = ensureGit(cwd);
    if (error !== null) {
      rows = [];
      publishCommitsState();
      return;
    }
    const cfg = config();
    const branch = currentBranch(runner);
    const { defaultBranch, parent } = resolveGroups(runner, cfg);
    const current = readPage(runner, currentGroupRange(parent), "current", cfg.logPage);
    const parentIsDefault = parent === null || (defaultBranch !== null && parent.name === defaultBranch.name);
    const parentCommits =
      parent !== null && defaultBranch !== null && !parentIsDefault
        ? readPage(runner, [`${defaultBranch.target}..${parent.target}`], "parent", cfg.logPage)
        : { commits: [] as Commit[], more: false };
    const defaultCommits =
      defaultBranch !== null
        ? readPage(runner, [defaultBranch.target], "default", cfg.logPage)
        : { commits: [] as Commit[], more: false };
    const oldest = defaultCommits.commits[defaultCommits.commits.length - 1];
    rows = buildRows({
      branch,
      parent,
      defaultBranch,
      current: current.commits,
      currentMore: current.more,
      parentCommits: parentCommits.commits,
      parentMore: parentCommits.more,
      defaultCommits: defaultCommits.commits,
      defaultMore: defaultCommits.more,
      unpushed: unpushedShas(runner),
      defaultOldestParent: oldest === undefined ? null : parentOf(runner, oldest.sha),
    });
    publishCommitsState();
  }

  /**
   * The branches the panel groups by: the default branch as configured,
   * and the parent — the configured one when it resolves, else the
   * default. One answer for the panel and for `F`, so the fixup list is
   * the panel's current group and never a guess of its own.
   */
  function resolveGroups(runner: GitRunner, cfg: SidecarConfig): { defaultBranch: BranchRef | null; parent: BranchRef | null } {
    const resolve = (name: string | null): BranchRef | null => {
      if (name === null) {
        return null;
      }
      const found = resolveBranch(runner, name);
      return found === null ? null : { name, target: found.target };
    };
    const defaultBranch = resolve(cfg.default);
    return { defaultBranch, parent: resolve(cfg.parent) ?? defaultBranch };
  }

  /** The current group as a log range: `<parent>..HEAD`, or all of HEAD with no parent to cut it at. */
  function currentGroupRange(parent: BranchRef | null): string[] {
    return parent === null ? ["HEAD"] : [`${parent.target}..HEAD`];
  }

  function publishCommitsState(): void {
    const pending = pendingLoad();
    const pendingRow = pending === null ? null : loadedRow(rows, decodeTail(pending));
    publishCommits({
      rows,
      loadedRowId: loadedRow(rows, loaded, loadedSha)?.id ?? null,
      pendingRowId: pendingRow?.id ?? null,
      error,
    });
  }

  /**
   * Re-read status (working-tree loads only) and republish the files panel.
   * With Collins' untracked switch off the `?` rows go: the live side never
   * has them (hunk loaded `--exclude-untracked`), and the other side's
   * list must not offer a file a click could never land on.
   */
  function refreshFiles(): void {
    const runner = ensureGit(cwd);
    const read = error === null && (loaded.kind === "unstaged" || loaded.kind === "staged") ? readStatus(runner) : null;
    status = config().untracked ? read : withoutUntracked(read);
    publishFiles({ status, loaded, pendingSelectPath, error });
  }

  function currentTail(): string[] {
    return loaded.kind === "staged" ? sideTail("staged") : sideTail("unstaged");
  }

  /** What a click or `n`/`p` does with a row. */
  function activateRow(row: Row, report: Report): void {
    if (row.kind === "more") {
      pages[row.group] += 1;
      refreshCommits();
      return;
    }
    if (row.load.length === 0) {
      return;
    }
    requestLoad(row.load, report, sessionDeps);
    publishCommitsState();
  }

  function loadSide(side: Side, path: string | null, report: Report): void {
    pendingSelectPath = path;
    publishFiles({ status, loaded, pendingSelectPath, error });
    requestLoad(sideTail(side), report, sessionDeps);
  }

  function onChangeset(changeset: ExtensionChangeset, ctx: ExtensionEventContext): void {
    panes = ctx.panes; // this generation's lease; the old one is dead (see `panes`)
    // One reload raises both `changeset_loaded` and `session_reload` with
    // the same changeset; the second carries nothing new, so skip the git
    // round trips (every reload, the refresh key included, mints a new id).
    if (changeset.id === lastChangesetId) {
      files = changeset.files;
      return;
    }
    lastChangesetId = changeset.id;
    const runner = ensureGit(ctx.cwd);
    files = changeset.files;
    loaded = decodeTitle(changeset.title, repo);
    loadedSha = loaded.kind === "show" && error === null ? revParse(runner, loaded.ref) : null;
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
    refreshCommits();
    refreshFiles();
    if (pendingSelectPath !== null) {
      const target = files.find((file) => file.path === pendingSelectPath);
      pendingSelectPath = null;
      if (target !== undefined) {
        ctx.navigation.selectFile(target.id);
      }
      publishFiles({ status, loaded, pendingSelectPath, error });
    }
  }

  function live(): Side | null {
    return loaded.kind === "unstaged" || loaded.kind === "staged" ? loaded.kind : null;
  }

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
   * After a mutation: reload in place, re-read status, tell Collins the
   * index and HEAD as they are now have been shown (so its own freshness
   * reload — which would cancel any dialog open by the time it lands —
   * stays home), and say what happened.
   */
  function afterMutation(ctx: ExtensionCommandContext, message: string, path: string | null): void {
    refreshAfter(ctx, path);
    ctx.notify(message, "info");
  }

  function refreshAfter(ctx: ExtensionCommandContext, path: string | null): void {
    pendingSelectPath = path;
    if (!ctx.commands.execute("hunk.app.refresh")) {
      requestLoad(currentTail(), ctx.notify, sessionDeps);
    }
    refreshFiles();
    recordRefreshed();
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

  function toggleFile(ctx: ExtensionCommandContext, file: ExtensionDiffFile, side: Side): void {
    const runner = ensureGit(ctx.cwd);
    const plan = planFileToggle({ file, live: side });
    const result = plan.stage ? stageFiles(runner, plan.paths) : unstageFiles(runner, plan.paths);
    if (reportGit(ctx.notify, result, plan.label)) {
      afterMutation(ctx, describe(plan), file.path);
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
      afterMutation(ctx, describe(plan), file.path);
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
      afterMutation(ctx, describe(plan), file.path);
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
      afterMutation(ctx, describe(plan), file.path);
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
    ctx.notify("anchor cleared", "info");
  }

  /**
   * What stops a commit before any question is asked: a half-finished
   * rebase, merge, cherry-pick or revert, or an empty index. Returns the
   * staged paths otherwise. Asked before the first dialog on purpose —
   * a dialog is cancelled by any reload, so the git reads come first.
   */
  function commitPreconditions(ctx: ExtensionCommandContext, runner: GitRunner): string[] | null {
    const operation = inProgressOperation(runner);
    if (operation !== null) {
      ctx.notify(`${operation} is half-finished here — finish or abort it first`, "warning");
      return null;
    }
    const staged = stagedPaths(runner);
    if (staged.length === 0) {
      ctx.notify("nothing staged — press x or X first", "warning");
      return null;
    }
    return staged;
  }

  function stagedNoun(count: number): string {
    return count === 1 ? "1 staged file" : `${count} staged files`;
  }

  /**
   * Run a commit on the asynchronous runner — hooks and signing take what
   * they take while the renderer keeps painting, with a toast once it is
   * clear they are taking a while (hunk shows toasts for a few seconds
   * each, so a fast commit is not followed by a stale "committing…") —
   * and refresh either way: a failed commit's hooks may have moved the
   * tree, and one that outlived the deadline may have been made. Null
   * when the commit failed (already reported), else the new HEAD's abbrev.
   */
  async function runCommit(ctx: ExtensionCommandContext, work: (git: AsyncGitRunner) => Promise<GitResult>): Promise<string | null> {
    committing = true;
    const slow = setTimeout(() => ctx.notify("committing… (hooks are running)", "info"), COMMIT_TOAST_MS);
    let result: GitResult;
    try {
      result = await work(ensureGitAsync(ctx.cwd));
    } finally {
      clearTimeout(slow);
      committing = false;
    }
    if (!reportGit(ctx.notify, result, "git commit")) {
      refreshAfter(ctx, ctx.selection.file?.path ?? null);
      return null;
    }
    return readLog(ensureGit(ctx.cwd), ["HEAD"], { limit: 1 })[0]?.abbrev ?? "HEAD";
  }

  /** The guards every commit key shares, before it asks anything: not already committing, a working-tree view, the preconditions. */
  function commitGate(ctx: ExtensionCommandContext): string[] | null {
    if (committing) {
      ctx.notify("a commit is still running", "info");
      return null;
    }
    if (live() === null) {
      ctx.notify(READ_ONLY, "info");
      return null;
    }
    return commitPreconditions(ctx, ensureGit(ctx.cwd));
  }

  /**
   * `C` (and `B`, with a body): commit the index as it is. Typing the
   * summary is the confirmation — Esc on either input cancels, and a blank
   * summary commits nothing.
   */
  async function commitIndex(ctx: ExtensionCommandContext, withBody: boolean): Promise<void> {
    const staged = commitGate(ctx);
    if (staged === null) {
      return;
    }
    const summaryInput = await ctx.dialogs.input({
      title: `Commit ${stagedNoun(staged.length)} — summary`,
      placeholder: "One line: what this change does",
    });
    if (summaryInput === null) {
      return;
    }
    const summary = summaryInput.trim();
    if (summary === "") {
      ctx.notify("a commit needs a summary", "warning");
      return;
    }
    let body: string | undefined;
    if (withBody) {
      const bodyInput = await ctx.dialogs.input({
        title: "Body (optional) — Enter to skip",
        placeholder: "Why, or anything the summary leaves out",
      });
      if (bodyInput === null) {
        return;
      }
      body = bodyInput.trim() === "" ? undefined : bodyInput;
    }
    const abbrev = await runCommit(ctx, (git) => commit(git, summary, body));
    if (abbrev === null) {
      return;
    }
    afterMutation(
      ctx,
      `committed ${abbrev} "${summary}" — undo with git reset --soft HEAD~1`,
      ctx.selection.file?.path ?? null,
    );
  }

  /**
   * The autosquash rebase that folds a fixup of `target` in; never run
   * here, only named. Interactive on purpose: `--autosquash` without `-i`
   * is ignored by every git before 2.44 (Debian 12 and Ubuntu 24.04 ship
   * older), and the todo it opens is already arranged — save and quit.
   */
  function autosquashCommand(runner: GitRunner, target: Commit): string {
    const onto = parentOf(runner, target.sha) === null ? "--root" : `${target.abbrev}^`;
    return `git rebase -i --autosquash --autostash ${onto}`;
  }

  /**
   * `F`: commit the index as `fixup! <sha>` for an unpushed commit the user
   * picks — one of the commits panel's current group that is on no
   * remote. The full sha, not `--fixup=`: subjects repeat, hashes do not,
   * and `rebase --autosquash` matches either. No rebase runs here — the
   * toast names the command that folds it in.
   */
  async function fixupIndex(ctx: ExtensionCommandContext): Promise<void> {
    const staged = commitGate(ctx);
    if (staged === null) {
      return;
    }
    const runner = ensureGit(ctx.cwd);
    const cfg = config();
    const commits = unpushedCommits(runner, currentGroupRange(resolveGroups(runner, cfg).parent), cfg.logPage);
    if (commits.length === 0) {
      ctx.notify("no unpushed commit to fix up — press C to make one", "info");
      return;
    }
    const options = commits.map((entry) => `${entry.abbrev}  ${entry.subject}`);
    const choice = await ctx.dialogs.select({ title: "Fix up which commit?", options });
    const target = choice === null ? undefined : commits[options.indexOf(choice)];
    if (target === undefined) {
      return;
    }
    const rebase = autosquashCommand(runner, target);
    const verb = staged.length === 1 ? "becomes" : "become";
    const confirmed = await ctx.dialogs.confirm({
      title: `Fix up ${target.abbrev}?`,
      body: `The ${stagedNoun(staged.length)} ${verb} a fixup! commit for ${target.abbrev} "${target.subject}". Nothing is rewritten now — fold it in with ${rebase}`,
      confirmLabel: "Commit",
    });
    if (!confirmed) {
      return;
    }
    if (await runCommit(ctx, (git) => commitFixup(git, target.sha)) !== null) {
      afterMutation(ctx, `fixup for ${target.abbrev} committed — ${rebase} folds it in`, ctx.selection.file?.path ?? null);
    }
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

  async function everything(ctx: ExtensionCommandContext, stage: boolean): Promise<void> {
    if (live() === null) {
      ctx.notify(READ_ONLY, "info");
      return;
    }
    const runner = ensureGit(ctx.cwd);
    const plan = planAll(readStatus(runner), stage);
    if (plan.count === 0) {
      ctx.notify(stage ? "nothing to stage" : "nothing to unstage", "info");
      return;
    }
    const noun = plan.count === 1 ? "change" : "changes";
    const confirmed = await ctx.dialogs.confirm({
      title: stage ? `Stage all ${plan.count} ${noun}?` : `Unstage all ${plan.count} ${noun}?`,
      confirmLabel: stage ? "Stage" : "Unstage",
    });
    if (!confirmed) {
      return;
    }
    const result = stage ? stageAll(runner) : unstageAll(runner);
    if (reportGit(ctx.notify, result, stage ? "git add -A" : "git reset")) {
      afterMutation(ctx, describe(plan), ctx.selection.file?.path ?? null);
    }
  }

  function step(ctx: ExtensionCommandContext, delta: number): void {
    const pending = pendingLoad();
    const from = pending === null ? loaded : decodeTail(pending);
    const resolved = pending === null ? loadedSha : null;
    if (loadedRow(rows, from, resolved) === null) {
      ctx.notify("load a row of the commits panel first", "info");
      return;
    }
    const target = neighbour(rows, from, delta, resolved);
    if (target === null) {
      ctx.notify(delta > 0 ? "at the bottom of this group" : "at the top of this group", "info");
      return;
    }
    activateRow(target, ctx.notify);
  }

  /** The parent Collins (or our guess) would pick without the user's say. */
  function automaticParent(): string | null {
    const sidecar = sidecarPath !== null ? readSidecar(sidecarPath) : null;
    if (sidecar !== null && sidecar.parentSource === "auto" && sidecar.parent !== null && !awaitingAuto) {
      return sidecar.parent;
    }
    return config().default;
  }

  async function chooseParent(ctx: { dialogs: ExtensionDialogs; notify: Notify; cwd: string }): Promise<void> {
    const runner = ensureGit(ctx.cwd);
    if (error !== null) {
      ctx.notify(error, "warning");
      return;
    }
    const automatic = `Automatic (${automaticParent() ?? "none"})`;
    const options = [automatic, ...localBranches(runner)];
    const choice = await ctx.dialogs.select({ title: "Parent branch", options });
    if (choice === null) {
      return;
    }
    const picked = choice === automatic ? null : choice;
    if (sidecarPath !== null) {
      const written =
        picked === null
          ? writeSidecar(sidecarPath, { parentSource: "auto" })
          : writeSidecar(sidecarPath, { parent: picked, parentSource: "user" });
      if (!written) {
        ctx.notify("could not write the parent branch for Collins", "warning");
      }
      awaitingAuto = picked === null;
    }
    memoryParent = picked;
    pages.current = 1;
    pages.parent = 1;
    refreshCommits();
    ctx.notify(picked === null ? "parent branch: automatic" : `parent branch: ${picked}`, "info");
  }

  hunk.registerPane({
    id: "commits",
    title: "Commits",
    placement: "left",
    width: { preferred: 26, min: 18 },
    component: CommitsPane,
  });

  hunk.registerPane({
    id: "files",
    title: "Files",
    placement: "left",
    replaces: "hunk:files",
    width: { preferred: 30, min: 22 },
    component: FilesPane,
  });

  setPaneHandlers({
    activateRow,
    contextRow: () => {
      hunk.events.emit(SET_PARENT_EVENT, {});
    },
    loadSide,
  });

  onPendingChange(publishCommitsState);

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
  hunk.registerCommand({ id: "commit", title: "Commit the index", key: "C" }, (ctx) => commitIndex(ctx, false));
  hunk.registerCommand({ id: "commit-with-body", title: "Commit the index with a body", key: "B" }, (ctx) =>
    commitIndex(ctx, true),
  );
  hunk.registerCommand({ id: "fixup", title: "Fix up an unpushed commit with the index", key: "F" }, (ctx) =>
    fixupIndex(ctx),
  );
  hunk.registerCommand({ id: "stage-all", title: "Stage all changes", key: "A" }, (ctx) => everything(ctx, true));
  hunk.registerCommand({ id: "unstage-all", title: "Unstage all changes", key: "U" }, (ctx) =>
    everything(ctx, false),
  );
  hunk.registerCommand({ id: "next-row", title: "Load the next row of the commits panel", key: "n" }, (ctx) =>
    step(ctx, 1),
  );
  hunk.registerCommand(
    { id: "previous-row", title: "Load the previous row of the commits panel", key: "p" },
    (ctx) => step(ctx, -1),
  );
  hunk.registerCommand({ id: "set-parent", title: "Set the parent branch…", key: "P" }, (ctx) =>
    chooseParent(ctx),
  );
  hunk.events.on(SET_PARENT_EVENT, (_payload, ctx) => chooseParent(ctx));

  /**
   * Hunk hides its sidebar area when the terminal is too narrow for a pane
   * and does not bring it back when the terminal grows (0.20.1, verified
   * against a 62 → 138 column resize): the panes stay "open" but unseen.
   * Opening an open pane reveals the area again, so a resize that made
   * the terminal wider re-opens whatever is open — never a pane the user
   * closed. Collins' git page starts narrow and is widened by a drag or a
   * maximize, which is exactly this path. Called on SIGWINCH and from a
   * one-second poll (see RESIZE_POLL_MS); a width that did not grow is a
   * no-op, so the poll costs one ioctl. `panes` is the newest event
   * context's (see its comment): a dead lease would answer "closed" for
   * everything and this would quietly do nothing.
   */
  function revealPanes(): void {
    const now = readColumns();
    const grew = now > columns;
    columns = now;
    if (!grew || panes === null) {
      return;
    }
    debug(`resize columns=${now} open=${PANE_IDS.map((id) => `${id}:${panes?.isOpen(id)}`).join(",")}`);
    for (const id of PANE_IDS) {
      if (panes.isOpen(id)) {
        panes.open(id);
      }
    }
  }

  function onResize(): void {
    if (resizeTimer !== null) {
      clearTimeout(resizeTimer);
    }
    resizeTimer = setTimeout(() => {
      resizeTimer = null;
      revealPanes();
    }, RESIZE_SETTLE_MS);
    resizeTimer.unref?.();
  }

  hunk.on("startup", (event, ctx) => {
    ensureGit(event.cwd);
    debug(`startup cwd=${event.cwd} sidecar=${sidecarPath ?? "-"} pid=${process.pid} columns=${columns}`);
    panes = ctx.panes;
    ctx.panes.open("commits");
    ctx.panes.open("files");
    process.on("SIGWINCH", onResize);
    resizePoll = setInterval(revealPanes, RESIZE_POLL_MS);
    resizePoll.unref?.();
    if (sidecarPath !== null) {
      sidecarSeen = sidecarConfigText();
      unwatch = watchSidecar(sidecarPath, () => {
        // Our own `refreshed` record moves the file too; only a config change earns a rebuild.
        const now = sidecarConfigText();
        if (now === sidecarSeen) {
          return;
        }
        sidecarSeen = now;
        awaitingAuto = false;
        debug(`sidecar changed: ${now}`);
        refreshCommits();
        refreshFiles(); // the untracked switch shapes the sections too
      });
    }
    refreshCommits();
    refreshFiles();
  });

  hunk.on("changeset_loaded", (event, ctx) => onChangeset(event.changeset, ctx));
  hunk.on("session_reload", (event, ctx) => onChangeset(event.changeset, ctx));

  hunk.on("shutdown", () => {
    process.off("SIGWINCH", onResize);
    if (resizeTimer !== null) {
      clearTimeout(resizeTimer);
      resizeTimer = null;
    }
    if (resizePoll !== null) {
      clearInterval(resizePoll);
      resizePoll = null;
    }
    panes = null;
    unwatch?.();
    unwatch = null;
  });
}
