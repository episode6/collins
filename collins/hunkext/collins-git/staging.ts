// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
// contributors); see collins/THIRD_PARTY_LICENSES.md.

/**
 * What `x`, `X`, `A` and `U` will do, decided before anything is done.
 *
 * Each planner is pure over its inputs (the file hunk selected, the patch
 * re-read from git, the working tree's status) and returns a plan the
 * composition root carries out — or a refusal with the reason the user
 * sees. Keeping the decision apart from the git call is what lets the
 * tests feed patches from a temp repository and check every guard.
 */

import type { Side } from "./model.ts";
import type { Status } from "./git.ts";
import {
  findDisagreement,
  parseFilePatch,
  unsafePathReason,
  unsupportedModeReason,
  writeSelectedHunks,
  type HostHunk,
} from "./patch.ts";

/** The parts of hunk's `ExtensionDiffFile` the planners read. */
export interface StagingFile {
  readonly path: string;
  readonly previousPath?: string;
  readonly changeType?: "change" | "rename-pure" | "rename-changed" | "new" | "deleted";
  readonly hunks?: readonly HostHunk[];
  readonly isUntracked?: boolean;
  readonly isBinary?: boolean;
  readonly isTooLarge?: boolean;
}

export interface HunkToggleInput {
  readonly file: StagingFile;
  /**
   * The selected hunk, or null when hunk selects none — its cursor on the
   * file rather than a hunk, or a file with no hunks to select (a binary,
   * one skipped for size): `x` then means the whole file, after the same
   * refusals a hunk would meet.
   */
  readonly hunkIndex: number | null;
  /** Which working-tree side the review shows; decides stage vs unstage. */
  readonly live: Side;
  /** `git diff [--cached] -- <path>`, re-read at action time. */
  readonly patchText: string;
}

export type HunkPlan =
  | { readonly kind: "apply"; readonly patch: string; readonly reverse: boolean; readonly label: string }
  | { readonly kind: "file" }
  | { readonly kind: "refuse"; readonly reason: string };

export interface FilePlan {
  readonly paths: readonly string[];
  readonly stage: boolean;
  readonly label: string;
}

export interface AllPlan {
  readonly count: number;
  readonly stage: boolean;
}

function verb(stage: boolean): string {
  return stage ? "staged" : "unstaged";
}

function isRename(file: StagingFile): boolean {
  return (
    file.changeType === "rename-pure" ||
    file.changeType === "rename-changed" ||
    (file.previousPath !== undefined && file.previousPath !== file.path)
  );
}

/**
 * Plan `x` on one hunk: a partial patch for `git apply --cached` (reversed
 * when the staged side is loaded), a fall-through to the whole file where a
 * single hunk makes no sense (an untracked file, a rename, no hunk
 * selected), or a refusal. The refusals come first whatever `hunkIndex`
 * is: a binary or oversized file is what hunk selects no hunk in, and
 * `x` on one must say "use X" rather than quietly become X.
 */
export function planHunkToggle(input: HunkToggleInput): HunkPlan {
  const { file, hunkIndex, live, patchText } = input;
  const stage = live === "unstaged";
  if (file.isBinary) {
    return { kind: "refuse", reason: `${file.path} is binary: stage the whole file with X` };
  }
  if (file.isTooLarge) {
    return { kind: "refuse", reason: `${file.path} is too large to stage by hunk: use X` };
  }
  if (file.isUntracked || isRename(file)) {
    return { kind: "file" };
  }
  if (patchText.trim() === "") {
    return file.changeType === "new"
      ? { kind: "file" }
      : { kind: "refuse", reason: `nothing to ${stage ? "stage" : "unstage"} in ${file.path} — press r to refresh` };
  }
  let parsed;
  try {
    parsed = parseFilePatch(patchText);
  } catch (error) {
    return { kind: "refuse", reason: `cannot read the patch for ${file.path}: ${String(error)}` };
  }
  if (parsed.binary) {
    return { kind: "refuse", reason: `${file.path} is binary: stage the whole file with X` };
  }
  const unsafe = unsafePathReason(parsed.path);
  if (unsafe !== null) {
    return { kind: "refuse", reason: `refusing ${parsed.path}: ${unsafe}` };
  }
  const mode = unsupportedModeReason(parsed.declaredModes);
  if (mode !== null) {
    return { kind: "refuse", reason: `cannot stage ${file.path} by hunk: ${mode}` };
  }
  if (hunkIndex === null) {
    return { kind: "file" }; // the cursor names the file, not a hunk of it
  }
  const disagreement = findDisagreement(parsed, file.hunks ?? []);
  if (disagreement !== null) {
    return { kind: "refuse", reason: `${file.path} changed since the review loaded — press r (${disagreement})` };
  }
  if (hunkIndex < 0 || hunkIndex >= parsed.hunks.length) {
    return { kind: "refuse", reason: `no hunk ${hunkIndex + 1} in ${file.path}` };
  }
  const patch = writeSelectedHunks(parsed, new Set([hunkIndex]));
  if (patch === null) {
    return { kind: "refuse", reason: `nothing to ${stage ? "stage" : "unstage"} in ${file.path}` };
  }
  return {
    kind: "apply",
    patch,
    reverse: !stage,
    label: `${verb(stage)} hunk ${hunkIndex + 1} of ${file.path}`,
  };
}

/** Plan `X`: the paths to add or reset (both of a rename, in one call). */
export function planFileToggle(input: { file: StagingFile; live: Side }): FilePlan {
  const { file, live } = input;
  const stage = live === "unstaged";
  const paths =
    file.previousPath !== undefined && file.previousPath !== file.path ? [file.previousPath, file.path] : [file.path];
  return { paths, stage, label: `${verb(stage)} ${file.path}` };
}

/**
 * Plan `A` (stage everything) or `U` (unstage everything): how many files
 * the confirmation names. Unlike `x`/`X`, the direction is the key's, not
 * the loaded side's — `A` stages all from the staged view too.
 */
export function planAll(status: Status | null, stage: boolean): AllPlan {
  const rows = status === null ? [] : stage ? status.unstaged : status.staged;
  return { count: rows.length, stage };
}

/** The toast after a plan was carried out. */
export function describe(plan: HunkPlan | FilePlan | AllPlan): string {
  if ("kind" in plan) {
    return plan.kind === "apply" ? plan.label : plan.kind === "refuse" ? plan.reason : "";
  }
  if ("paths" in plan) {
    return plan.label;
  }
  return `${verb(plan.stage)} ${plan.count} ${plan.count === 1 ? "file" : "files"}`;
}
