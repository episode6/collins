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
 * `X` the current file, `A`/`U` everything (after a confirmation), `n`/`p`
 * walk the current group of the commits panel, `P` (or a right click on
 * the panel) picks the parent branch.
 *
 * This file only composes: git.ts runs git, model.ts builds rows, store.ts
 * holds what the panes paint, session.ts reloads the window, sidecar.ts
 * talks to Collins, staging.ts plans the keys. Runs standalone too:
 * `hunk diff --extension <this dir>` with no sidecar guesses the branches.
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
import { CommitsPane } from "./commits.tsx";
import { FilesPane } from "./files.tsx";
import {
  applyCached,
  currentBranch,
  gitRunner,
  localBranches,
  parentOf,
  readFilePatch,
  readLog,
  readStatus,
  repoToplevel,
  resolveBranch,
  revParse,
  stageAll,
  stageFiles,
  unpushedShas,
  unstageAll,
  unstageFiles,
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
  type BranchRef,
  type Group,
  type Loaded,
  type Row,
  type Side,
} from "./model.ts";
import { firstLine, onPendingChange, pendingLoad, requestLoad, type Report } from "./session.ts";
import {
  configFromEnv,
  effectiveConfig,
  readSidecar,
  watchSidecar,
  writeSidecar,
  type SidecarConfig,
} from "./sidecar.ts";
import { describe, planAll, planFileToggle, planHunkToggle } from "./staging.ts";
import { publishCommits, publishFiles, setPaneHandlers } from "./store.ts";

/** The bus event a right click on the commits panel raises. */
export const SET_PARENT_EVENT = "collins-git:set-parent";

const READ_ONLY = "read-only view — load working tree first";

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
      repo = top === null ? "" : basename(top);
      error = top === null ? "not a git repository" : null;
    }
    return git;
  }

  function config(): SidecarConfig {
    const runner = ensureGit(cwd);
    let sidecar = sidecarPath !== null ? readSidecar(sidecarPath) : null;
    if (sidecar === null && memoryParent !== null) {
      sidecar = { parent: memoryParent, parentSource: "user", default: null, logPage: 20 };
    }
    if (sidecar !== null && awaitingAuto && sidecarPath !== null) {
      sidecar = { ...sidecar, parent: null, parentSource: "auto" };
    }
    return effectiveConfig(sidecar, runner);
  }

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
    const resolve = (name: string | null): BranchRef | null => {
      if (name === null) {
        return null;
      }
      const found = resolveBranch(runner, name);
      return found === null ? null : { name, target: found.target };
    };
    const defaultBranch = resolve(cfg.default);
    const parent = resolve(cfg.parent) ?? defaultBranch;
    const current = readPage(runner, parent !== null ? [`${parent.target}..HEAD`] : ["HEAD"], "current", cfg.logPage);
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

  /** Re-read status (working-tree loads only) and republish the files panel. */
  function refreshFiles(): void {
    const runner = ensureGit(cwd);
    status = error === null && (loaded.kind === "unstaged" || loaded.kind === "staged") ? readStatus(runner) : null;
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
    requestLoad(row.load, report);
    publishCommitsState();
  }

  function loadSide(side: Side, path: string | null, report: Report): void {
    pendingSelectPath = path;
    publishFiles({ status, loaded, pendingSelectPath, error });
    requestLoad(sideTail(side), report);
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
    debug(`changeset id=${changeset.id} title=${JSON.stringify(changeset.title)} loaded=${JSON.stringify(loaded)} files=${files.length}`);
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

  /** After a mutation: reload in place, re-read status, say what happened. */
  function afterMutation(ctx: ExtensionCommandContext, message: string, path: string | null): void {
    pendingSelectPath = path;
    if (!ctx.commands.execute("hunk.app.refresh")) {
      requestLoad(currentTail(), ctx.notify);
    }
    refreshFiles();
    ctx.notify(message, "info");
  }

  function toggleFile(ctx: ExtensionCommandContext, file: ExtensionDiffFile, side: Side): void {
    const runner = ensureGit(ctx.cwd);
    const plan = planFileToggle({ file, live: side });
    const result = plan.stage ? stageFiles(runner, plan.paths) : unstageFiles(runner, plan.paths);
    if (reportGit(ctx.notify, result, plan.label)) {
      afterMutation(ctx, describe(plan), file.path);
    }
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

  hunk.registerCommand({ id: "stage-hunk", title: "Stage or unstage the current hunk", key: "x" }, stageHunk);
  hunk.registerCommand({ id: "stage-file", title: "Stage or unstage the current file", key: "X" }, stageFile);
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
      unwatch = watchSidecar(sidecarPath, () => {
        awaitingAuto = false;
        debug(`sidecar changed: ${JSON.stringify(readSidecar(sidecarPath))}`);
        refreshCommits();
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
