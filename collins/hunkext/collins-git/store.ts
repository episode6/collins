// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from sadick254/hunk-commit-log and joshedler/hunk-git-lite
// (MIT, © 2026 Sadick, © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

/**
 * Module-level state for the two panes, bridged into React with
 * `useSyncExternalStore`.
 *
 * Panes unmount when closed and remount when opened, so nothing durable
 * can live in a component; it lives here, and the panes only read it.
 * Every snapshot is immutable and a publish keeps the previous object when
 * nothing changed — React compares snapshots by identity, and a fresh
 * object on every read would re-render without end.
 */

import type { Loaded, Row, Side } from "./model.ts";
import type { Status } from "./git.ts";
import type { Report } from "./session.ts";

/** What the commits pane paints. */
export interface CommitsSnapshot {
  readonly rows: readonly Row[];
  /** The row describing what hunk has loaded, or null. */
  readonly loadedRowId: string | null;
  /** The row a reload is on its way to, or null. */
  readonly pendingRowId: string | null;
  /** A one-line problem to show instead of rows, or null. */
  readonly error: string | null;
}

/** What the files pane paints, beyond the `files` prop hunk hands it. */
export interface FilesSnapshot {
  readonly status: Status | null;
  readonly loaded: Loaded;
  /**
   * A path to select once it appears after a reload. File ids renumber by
   * position across a reload, so a path is the only handle that survives.
   */
  readonly pendingSelectPath: string | null;
  readonly error: string | null;
}

/** What a pane can ask the composition root to do; set once at load. */
export interface PaneHandlers {
  /** A left click on a commits-panel row; `report` is the pane's toast. */
  activateRow(row: Row, report: Report): void;
  /** A right click on a commits-panel row: the "Set parent branch…" chooser. */
  contextRow(row: Row): void;
  /** A click on the files panel's other side: load it, then select `path`. */
  loadSide(side: Side, path: string | null, report: Report): void;
  /** A click on a live-side file, after hunk selected it: a narrow page drops to the diff (level.ts). */
  selectedFile(): void;
}

export const EMPTY_COMMITS: CommitsSnapshot = { rows: [], loadedRowId: null, pendingRowId: null, error: null };
export const EMPTY_FILES: FilesSnapshot = {
  status: null,
  loaded: { kind: "foreign", tail: "" },
  pendingSelectPath: null,
  error: null,
};

class Store<T> {
  private snapshot: T;
  private readonly listeners = new Set<() => void>();
  private readonly same: (left: T, right: T) => boolean;

  constructor(initial: T, same: (left: T, right: T) => boolean) {
    this.snapshot = initial;
    this.same = same;
  }

  read = (): T => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  publish(next: T): void {
    if (this.same(this.snapshot, next)) {
      return;
    }
    this.snapshot = next;
    for (const listener of this.listeners) {
      listener();
    }
  }
}

function sameRows(left: readonly Row[], right: readonly Row[]): boolean {
  return (
    left.length === right.length &&
    left.every((row, index) => {
      const other = right[index];
      return (
        other !== undefined &&
        row.id === other.id &&
        row.label === other.label &&
        row.unpushed === other.unpushed &&
        row.load.join("\0") === other.load.join("\0")
      );
    })
  );
}

function sameCommits(left: CommitsSnapshot, right: CommitsSnapshot): boolean {
  return (
    left.loadedRowId === right.loadedRowId &&
    left.pendingRowId === right.pendingRowId &&
    left.error === right.error &&
    sameRows(left.rows, right.rows)
  );
}

function sameLoaded(left: Loaded, right: Loaded): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function sameStatus(left: Status | null, right: Status | null): boolean {
  if (left === null || right === null) {
    return left === right;
  }
  return JSON.stringify(left) === JSON.stringify(right);
}

function sameFiles(left: FilesSnapshot, right: FilesSnapshot): boolean {
  return (
    left.pendingSelectPath === right.pendingSelectPath &&
    left.error === right.error &&
    sameLoaded(left.loaded, right.loaded) &&
    sameStatus(left.status, right.status)
  );
}

const commits = new Store<CommitsSnapshot>(EMPTY_COMMITS, sameCommits);
const files = new Store<FilesSnapshot>(EMPTY_FILES, sameFiles);

export const snapshotCommits = commits.read;
export const subscribeCommits = commits.subscribe;
export function publishCommits(next: CommitsSnapshot): void {
  commits.publish(next);
}

export const snapshotFiles = files.read;
export const subscribeFiles = files.subscribe;
export function publishFiles(next: FilesSnapshot): void {
  files.publish(next);
}

let handlers: PaneHandlers | null = null;

/** Install the pane handlers; the composition root does this once. */
export function setPaneHandlers(next: PaneHandlers | null): void {
  handlers = next;
}

/** The installed handlers, or a set that does nothing (panes render before load). */
export function paneHandlers(): PaneHandlers {
  return handlers ?? { activateRow() {}, contextRow() {}, loadSide() {}, selectedFile() {} };
}

/** Back to the initial state; only tests need this. */
export function resetStoresForTests(): void {
  commits.publish(EMPTY_COMMITS);
  files.publish(EMPTY_FILES);
  handlers = null;
}
