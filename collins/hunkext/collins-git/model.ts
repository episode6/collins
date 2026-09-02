// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * The commits panel's model and the files panel's sections, as pure
 * functions over what git and hunk report.
 *
 * Nothing here runs git or touches hunk: the composition root (index.ts)
 * gathers commits, status and the session title, and this module turns
 * them into rows, decides which row is the loaded one, and walks the
 * groups for `n`/`p`. That split is what keeps the panels testable under
 * `bun test` without a terminal.
 */

import { EMPTY_TREE, type Commit, type Status, type StatusCode } from "./git.ts";

/**
 * What hunk has loaded, decoded from its session title.
 *
 * The title is the only thing that names a load: hunk's own titles are
 * `<repo> working tree`, `<repo> staged changes`, `<repo> show <ref>` and
 * `<repo> <range>`; anything else is a load neither we nor Collins made.
 */
export type Loaded =
  | { readonly kind: "unstaged" }
  | { readonly kind: "staged" }
  | { readonly kind: "show"; readonly ref: string }
  | { readonly kind: "range"; readonly range: string }
  | { readonly kind: "foreign"; readonly tail: string };

const TITLE_WORKING_TREE = "working tree";
const TITLE_STAGED = "staged changes";

/**
 * Decode a session title. The repo name in front may contain spaces, so it
 * is stripped by name when known and the tail is matched on its own.
 */
export function decodeTitle(title: string, repoName: string): Loaded {
  let text = (title ?? "").trim();
  if (repoName !== "" && text.startsWith(`${repoName} `)) {
    text = text.slice(repoName.length + 1).trim();
  }
  if (text === TITLE_WORKING_TREE || text.endsWith(` ${TITLE_WORKING_TREE}`)) {
    return { kind: "unstaged" };
  }
  if (text === TITLE_STAGED || text.endsWith(` ${TITLE_STAGED}`)) {
    return { kind: "staged" };
  }
  const words = text.split(" ");
  const last = words[words.length - 1] ?? "";
  const beforeLast = words[words.length - 2];
  if (beforeLast === "show" && last !== "") {
    return { kind: "show", ref: last };
  }
  if (last.includes("..") && !/\s/.test(last)) {
    return { kind: "range", range: last };
  }
  return { kind: "foreign", tail: text };
}

/**
 * The load a `session reload` tail asks for — the inverse of a row's `load`,
 * so a pending reload can be compared with rows before its title arrives.
 */
export function decodeTail(tail: readonly string[]): Loaded {
  const [command, arg] = tail;
  if (command === "show" && arg !== undefined) {
    return { kind: "show", ref: arg };
  }
  if (command === "diff") {
    if (arg === undefined) {
      return { kind: "unstaged" };
    }
    if (arg === "--staged" || arg === "--cached") {
      return { kind: "staged" };
    }
    if (arg.includes("..")) {
      return { kind: "range", range: arg };
    }
  }
  return { kind: "foreign", tail: tail.join(" ") };
}

export type RowKind = "header" | "worktree" | "commit" | "more";
export type Group = "current" | "parent" | "default";

/** One line of the commits panel. */
export interface Row {
  readonly id: string;
  readonly kind: RowKind;
  readonly group: Group;
  readonly label: string;
  readonly sha?: string;
  readonly abbrev?: string;
  readonly unpushed?: boolean;
  /** The `session reload` tail a click sends; empty for `load more…`. */
  readonly load: readonly string[];
}

/** A branch as the model needs it: its name and the ref git is given for it. */
export interface BranchRef {
  readonly name: string;
  readonly target: string;
}

/** Everything `buildRows` reads; the composition root gathers it. */
export interface RowsInput {
  readonly branch: string;
  readonly parent: BranchRef | null;
  readonly defaultBranch: BranchRef | null;
  /** The current branch's own commits, newest first (`<parent>..HEAD`). */
  readonly current: readonly Commit[];
  readonly currentMore: boolean;
  /** The parent's commits not on the default branch (`<default>..<parent>`). */
  readonly parentCommits: readonly Commit[];
  readonly parentMore: boolean;
  /** The default branch's most recent commits (`log <default>`). */
  readonly defaultCommits: readonly Commit[];
  readonly defaultMore: boolean;
  readonly unpushed: ReadonlySet<string>;
  /** First parent of the oldest loaded default-branch commit; null for a root. */
  readonly defaultOldestParent: string | null;
}

export const WORKTREE_ROW_ID = "worktree";

export function headerRowId(group: Group): string {
  return `header:${group}`;
}

export function moreRowId(group: Group): string {
  return `more:${group}`;
}

export function commitRowId(sha: string): string {
  return `commit:${sha}`;
}

function commitRows(commits: readonly Commit[], group: Group, unpushed: ReadonlySet<string>): Row[] {
  return commits.map((commit) => ({
    id: commitRowId(commit.sha),
    kind: "commit" as const,
    group,
    label: commit.subject,
    sha: commit.sha,
    abbrev: commit.abbrev,
    unpushed: unpushed.has(commit.sha),
    load: ["show", commit.sha],
  }));
}

function moreRow(group: Group): Row {
  return { id: moreRowId(group), kind: "more", group, label: "load more…", load: [] };
}

/**
 * The rows of the commits panel, top to bottom: the current branch (its
 * header, `working tree`, its commits, `load more…`), the parent branch
 * when it is not the default, then the default branch.
 *
 * Header loads follow the spec's table: the current branch's header loads
 * `diff <parent>...HEAD` (or, with no parent at all, `diff <oldest^>..HEAD`
 * over what is listed), the parent's `diff <default>...<parent>`, and the
 * default's `diff <oldest loaded>^..<tip>` — the commits the group shows.
 */
export function buildRows(input: RowsInput): Row[] {
  const rows: Row[] = [];
  const { parent, defaultBranch } = input;

  const oldestCurrent = input.current[input.current.length - 1];
  const currentLoad =
    parent !== null
      ? ["diff", `${parent.target}...HEAD`]
      : oldestCurrent !== undefined
        ? ["diff", `${oldestCurrent.sha}^..HEAD`]
        : ["diff", "HEAD"];
  rows.push({
    id: headerRowId("current"),
    kind: "header",
    group: "current",
    label: input.branch.toUpperCase(),
    load: currentLoad,
  });
  rows.push({ id: WORKTREE_ROW_ID, kind: "worktree", group: "current", label: "working tree", load: ["diff"] });
  rows.push(...commitRows(input.current, "current", input.unpushed));
  if (input.currentMore) {
    rows.push(moreRow("current"));
  }

  const parentIsDefault = parent === null || (defaultBranch !== null && parent.name === defaultBranch.name);
  if (parent !== null && !parentIsDefault) {
    rows.push({
      id: headerRowId("parent"),
      kind: "header",
      group: "parent",
      label: parent.name.toUpperCase(),
      load: defaultBranch !== null ? ["diff", `${defaultBranch.target}...${parent.target}`] : ["diff", parent.target],
    });
    rows.push(...commitRows(input.parentCommits, "parent", input.unpushed));
    if (input.parentMore) {
      rows.push(moreRow("parent"));
    }
  }

  if (defaultBranch !== null) {
    const tip = input.defaultCommits[0];
    const oldestParent = input.defaultOldestParent ?? EMPTY_TREE;
    rows.push({
      id: headerRowId("default"),
      kind: "header",
      group: "default",
      label: defaultBranch.name.toUpperCase(),
      load: tip !== undefined ? ["diff", `${oldestParent}..${tip.sha}`] : ["diff", defaultBranch.target],
    });
    rows.push(...commitRows(input.defaultCommits, "default", input.unpushed));
    if (input.defaultMore) {
      rows.push(moreRow("default"));
    }
  }

  return rows;
}

/**
 * The row that describes what hunk has loaded, or null when no row does.
 *
 * `resolvedRef` is the sha a `show <ref>` title's ref resolves to (a branch
 * name or `HEAD` names a commit too), when the caller could resolve it.
 */
export function loadedRow(rows: readonly Row[], loaded: Loaded, resolvedRef: string | null = null): Row | null {
  switch (loaded.kind) {
    case "unstaged":
    case "staged":
      return rows.find((row) => row.kind === "worktree") ?? null;
    case "show": {
      const ref = loaded.ref;
      const byRef =
        rows.find((row) => row.kind === "commit" && row.sha !== undefined && row.sha.startsWith(ref)) ?? null;
      if (byRef !== null || resolvedRef === null) {
        return byRef;
      }
      return rows.find((row) => row.kind === "commit" && row.sha === resolvedRef) ?? null;
    }
    case "range":
      return rows.find((row) => row.kind === "header" && row.load[1] === loaded.range) ?? null;
    case "foreign":
      return null;
  }
}

/**
 * The row `delta` steps away within the loaded row's group, over the rows
 * that load something on their own (`working tree` and commits — headers
 * and `load more…` are skipped). From a group's header, one step down is
 * its first such row. Null at either edge, and when nothing is loaded.
 */
export function neighbour(
  rows: readonly Row[],
  loaded: Loaded,
  delta: number,
  resolvedRef: string | null = null,
): Row | null {
  const current = loadedRow(rows, loaded, resolvedRef);
  if (current === null) {
    return null;
  }
  const walkable = rows.filter(
    (row) => row.group === current.group && (row.kind === "worktree" || row.kind === "commit"),
  );
  const position = current.kind === "header" ? -1 : walkable.findIndex((row) => row.id === current.id);
  return walkable[position + delta] ?? null;
}

/** A file as hunk hands it to a pane — the fields the files panel reads. */
export interface PaneFile {
  readonly id: string;
  readonly path: string;
  readonly previousPath?: string;
  readonly stats: { readonly additions: number; readonly deletions: number };
  readonly changeType?: "change" | "rename-pure" | "rename-changed" | "new" | "deleted";
  readonly isUntracked?: boolean;
  readonly isBinary?: boolean;
}

/** One row of the files panel; `id` is null on the side hunk has not loaded. */
export interface FileRow {
  readonly id: string | null;
  readonly path: string;
  readonly previousPath?: string;
  readonly code: StatusCode;
  readonly additions?: number;
  readonly deletions?: number;
  readonly binary: boolean;
}

export type Side = "unstaged" | "staged";

/** What the files panel draws: one flat list, or the two working-tree sides. */
export type FileSections =
  | { readonly mode: "flat"; readonly rows: FileRow[] }
  | {
      readonly mode: "split";
      readonly live: Side;
      readonly unstaged: FileRow[];
      readonly staged: FileRow[];
    };

/** The status letter for a file hunk reports. */
export function fileCode(file: PaneFile): StatusCode {
  if (file.isUntracked) {
    return "?";
  }
  switch (file.changeType) {
    case "new":
      return "A";
    case "deleted":
      return "D";
    case "rename-pure":
    case "rename-changed":
      return "R";
    default:
      return file.previousPath !== undefined && file.previousPath !== file.path ? "R" : "M";
  }
}

function liveRow(file: PaneFile): FileRow {
  const row: FileRow = {
    id: file.id,
    path: file.path,
    code: fileCode(file),
    additions: file.stats.additions,
    deletions: file.stats.deletions,
    binary: file.isBinary === true,
  };
  return file.previousPath !== undefined ? { ...row, previousPath: file.previousPath } : row;
}

function statusRow(row: { path: string; previousPath?: string; code: StatusCode; binary?: boolean }): FileRow {
  const out: FileRow = { id: null, path: row.path, code: row.code, binary: row.binary === true };
  return row.previousPath !== undefined ? { ...out, previousPath: row.previousPath } : out;
}

/**
 * The files panel's sections. When the working tree is loaded and status is
 * known, the loaded side's rows come from hunk's own files (with ids and
 * stats) and the other side's from `git status` (navigation only); every
 * other load is one flat list of hunk's files.
 */
export function filesSections(status: Status | null, files: readonly PaneFile[], loaded: Loaded): FileSections {
  const rows = files.map(liveRow);
  if ((loaded.kind !== "unstaged" && loaded.kind !== "staged") || status === null) {
    return { mode: "flat", rows };
  }
  if (loaded.kind === "unstaged") {
    return { mode: "split", live: "unstaged", unstaged: rows, staged: status.staged.map(statusRow) };
  }
  return { mode: "split", live: "staged", unstaged: status.unstaged.map(statusRow), staged: rows };
}

/**
 * `git status` with its untracked (`?`) rows left out: what the files panel
 * shows when Collins' untracked switch is off, so the UNSTAGED section of a
 * staged load lists no file the `diff --exclude-untracked` it would load
 * can't hold. Staging's own reads keep the full status (`A` stages new
 * files whatever the panel shows).
 */
export function withoutUntracked(status: Status | null): Status | null {
  if (status === null) {
    return null;
  }
  return { ...status, unstaged: status.unstaged.filter((row) => row.code !== "?") };
}

/** The `session reload` tail that loads one working-tree side. */
export function sideTail(side: Side): string[] {
  return side === "staged" ? ["diff", "--staged"] : ["diff"];
}
