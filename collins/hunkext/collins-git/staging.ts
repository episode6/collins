// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
// contributors); see collins/THIRD_PARTY_LICENSES.md.

/**
 * What `x`, `X` and `D` will do, decided before anything is done.
 *
 * Each planner is pure over its inputs (the file and hunk selected, the
 * line range between the anchor and the cursor, the patch re-read from
 * git) and returns a plan the composition root carries out — or a refusal
 * with the reason the user sees. Keeping the decision apart from the git
 * call is what lets the tests feed patches from a temp repository and
 * check every guard.
 *
 * A range (`x` or `D` with an anchor set) meets more guards than a hunk:
 * its line numbers came out of the review, so besides hunk's own hunk
 * spans (`findDisagreement`) the review's patch lines are compared with
 * the fresh ones (`sameHunks`) before either number is trusted.
 */

import type { Side } from "./model.ts";
import {
  findDisagreement,
  parseFilePatch,
  unsafePathReason,
  unsupportedModeReason,
  writeSelectedHunks,
  type FilePatch,
  type HostHunk,
} from "./patch.ts";
import {
  countSelected,
  locate,
  orderRange,
  RangeRefusal,
  sameHunks,
  writeSelectedLines,
  type LineAddress,
  type LineRange,
} from "./range.ts";

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

/** `x` with an anchor set: the lines between it and the cursor. */
export interface RangeToggleInput {
  readonly file: StagingFile;
  /** hunk's own patch text for the file (`file.patch`), for `sameHunks`. */
  readonly reviewPatchText: string;
  readonly anchor: LineAddress;
  readonly head: LineAddress;
  readonly live: Side;
  /** `git diff [--cached] -- <path>`, re-read at action time. */
  readonly patchText: string;
}

export type RangePlan =
  | {
      readonly kind: "apply";
      readonly patch: string;
      readonly reverse: boolean;
      readonly label: string;
      /** The `+`/`-` lines the patch carries. */
      readonly lines: number;
    }
  | { readonly kind: "refuse"; readonly reason: string };

/** `D`: the selected hunk, or the anchor..cursor range when one is set. */
export interface DiscardInput {
  readonly file: StagingFile;
  readonly reviewPatchText: string;
  readonly live: Side;
  readonly hunkIndex: number | null;
  /** Null means the hunk. */
  readonly range: { readonly anchor: LineAddress; readonly head: LineAddress } | null;
  /** `git diff -- <path>` (never `--cached`), re-read at action time. */
  readonly patchText: string;
}

export type DiscardPlan =
  | {
      readonly kind: "apply";
      /** For `applyWorktreeReverse`. */
      readonly patch: string;
      readonly label: string;
      readonly lines: number;
      /** The hunk discarded whole, or null for a range. */
      readonly hunk: number | null;
    }
  | {
      /** A file deleted in the working tree comes back from the index (`restoreFile`). */
      readonly kind: "restore";
      readonly path: string;
      readonly label: string;
    }
  | { readonly kind: "refuse"; readonly reason: string };

type Refusal = { readonly kind: "refuse"; readonly reason: string };

/** Why a file can only be taken whole, when it can. */
type WholeReason = "binary" | "tooLarge" | "untracked" | "rename" | "new" | "deleted";

/** The words a key uses for the guards every partial operation shares. */
interface Wording {
  readonly action: "stage" | "unstage" | "discard";
  readonly whole: (path: string, why: WholeReason) => string;
}

function stagingWording(stage: boolean): Wording {
  return {
    action: stage ? "stage" : "unstage",
    whole: (path, why) => {
      switch (why) {
        case "binary":
          return `${path} is binary: stage the whole file with X`;
        case "tooLarge":
          return `${path} is too large to stage by hunk: use X`;
        case "untracked":
          return `${path} is untracked: stage the whole file with X`;
        case "rename":
          return "renames stage whole: use X";
        case "new":
        case "deleted":
          return "partial staging of a new or deleted file: use X";
      }
    },
  };
}

const DISCARD_WORDING: Wording = {
  action: "discard",
  whole: (path, why) => {
    switch (why) {
      case "binary":
        return `${path} is binary: use git from a shell`;
      case "tooLarge":
        return `${path} is too large to discard by hunk: use git from a shell`;
      case "deleted":
        return `${path} is deleted: D with no anchor restores it whole — esc, then D`;
      default:
        return "D reverts changes inside a modified file — use git from a shell for a new or renamed file";
    }
  },
};

/**
 * The guards a range or a discard meets before any line number is read,
 * in the order the user would want to hear about them: what the review
 * says of the file, then what the fresh patch says, then whether the two
 * still agree. Returns both parses when everything holds. With
 * `deletedWhole`, a patch that deletes the file is handed back as
 * `deleted` instead of refused, before the hunk comparisons (hunk's span
 * for a deletion is not one this code reasons about): the caller takes
 * the file whole.
 */
function guardPartial(
  file: StagingFile,
  reviewPatchText: string,
  patchText: string,
  wording: Wording,
  deletedWhole = false,
):
  | Refusal
  | { readonly kind: "ok"; readonly parsed: FilePatch; readonly review: FilePatch }
  | { readonly kind: "deleted"; readonly parsed: FilePatch } {
  const refuse = (reason: string): Refusal => ({ kind: "refuse", reason });
  if (file.isBinary) {
    return refuse(wording.whole(file.path, "binary"));
  }
  if (file.isTooLarge) {
    return refuse(wording.whole(file.path, "tooLarge"));
  }
  if (file.isUntracked) {
    return refuse(wording.whole(file.path, "untracked"));
  }
  if (isRename(file)) {
    return refuse(wording.whole(file.path, "rename"));
  }
  if (patchText.trim() === "") {
    return refuse(`nothing to ${wording.action} in ${file.path} — press r to refresh`);
  }
  let parsed: FilePatch;
  try {
    parsed = parseFilePatch(patchText);
  } catch (error) {
    return refuse(`cannot read the patch for ${file.path}: ${String(error)}`);
  }
  if (parsed.binary) {
    return refuse(wording.whole(file.path, "binary"));
  }
  const unsafe = unsafePathReason(parsed.path);
  if (unsafe !== null) {
    return refuse(`refusing ${parsed.path}: ${unsafe}`);
  }
  const mode = unsupportedModeReason(parsed.declaredModes);
  if (mode !== null) {
    return refuse(`cannot ${wording.action} ${file.path} by hunk: ${mode}`);
  }
  if (parsed.change === "renamed") {
    return refuse(wording.whole(file.path, "rename"));
  }
  if (parsed.change === "deleted" && deletedWhole) {
    return { kind: "deleted", parsed };
  }
  if (parsed.change !== "modified") {
    // A partial patch for a new or deleted file would need its header
    // rewritten (no `new file mode`, a real path for `/dev/null`): not in v1.
    return refuse(wording.whole(file.path, parsed.change === "added" ? "new" : "deleted"));
  }
  const disagreement = findDisagreement(parsed, file.hunks ?? []);
  if (disagreement !== null) {
    return refuse(`${file.path} changed since the review loaded — press r (${disagreement})`);
  }
  let review: FilePatch;
  try {
    review = parseFilePatch(reviewPatchText);
  } catch (error) {
    return refuse(`cannot read the review's patch for ${file.path}: ${String(error)}`);
  }
  const drift = sameHunks(review, parsed);
  if (drift !== null) {
    return refuse(`${file.path} changed since the review loaded — press r (${drift})`);
  }
  return { kind: "ok", parsed, review };
}

/** The anchor and the cursor as patch positions, or the refusal for the one that is not on a diff line. */
function locateRange(parsed: FilePatch, anchor: LineAddress, head: LineAddress): LineRange | Refusal {
  const from = locate(parsed, anchor);
  if (from === null) {
    return { kind: "refuse", reason: "the anchor is not on a diff line — press v again" };
  }
  const to = locate(parsed, head);
  if (to === null) {
    return { kind: "refuse", reason: "the cursor is not on a diff line" };
  }
  return orderRange(from, to);
}

/** A partial patch for the range, or the refusal when the range cannot make one. */
function writeRange(
  parsed: FilePatch,
  range: LineRange,
  reverse: boolean,
): Refusal | { readonly kind: "ok"; readonly patch: string; readonly lines: number } {
  let patch: string | null;
  try {
    patch = writeSelectedLines(parsed, range, { reverse });
  } catch (error) {
    if (error instanceof RangeRefusal) {
      return { kind: "refuse", reason: error.message };
    }
    throw error;
  }
  if (patch === null) {
    return { kind: "refuse", reason: "no changes between the anchor and the cursor" };
  }
  const { added, removed } = countSelected(parsed, range);
  return { kind: "ok", patch, lines: added + removed };
}

function lineCount(lines: number): string {
  return `${lines} ${lines === 1 ? "line" : "lines"}`;
}

/**
 * Plan `x` on the lines between the anchor and the cursor: a partial
 * patch for `git apply --cached` (reversed when the staged side is
 * loaded), or a refusal. There is no whole-file fall-through here — a
 * range names lines, and a file that can only go whole is told so.
 */
export function planRangeToggle(input: RangeToggleInput): RangePlan {
  const { file, live } = input;
  const stage = live === "unstaged";
  const guard = guardPartial(file, input.reviewPatchText, input.patchText, stagingWording(stage));
  if (guard.kind === "refuse") {
    return guard;
  }
  if (guard.kind === "deleted") {
    return { kind: "refuse", reason: stagingWording(stage).whole(file.path, "deleted") }; // not asked for; here for the types
  }
  const range = locateRange(guard.parsed, input.anchor, input.head);
  if ("kind" in range) {
    return range;
  }
  const written = writeRange(guard.parsed, range, !stage);
  if (written.kind === "refuse") {
    return written;
  }
  return {
    kind: "apply",
    patch: written.patch,
    reverse: !stage,
    label: `${verb(stage)} ${lineCount(written.lines)} of ${file.path}`,
    lines: written.lines,
  };
}

/**
 * Plan `D`: the patch `git apply --reverse` takes back out of the working
 * tree — the selected hunk, or the anchor..cursor range — or, for a file
 * deleted in the working tree, the restore that brings it back whole.
 * Only the Unstaged view qualifies (the Staged view shows the index,
 * which `D` does not touch), and only a plain modification has an
 * "inside" to revert part of: a new or renamed file is refused, a deleted
 * one is restored when `D` is asked for the hunk (a range in it is
 * refused — the restore is the whole file).
 *
 * A whole hunk goes through the range writer too, as the range of all its
 * lines: that is what renumbers the earlier-omitted hunks' starts for a
 * reverse apply (the working tree holds the patch's new side exactly, so
 * it is the old-side starts that move).
 */
export function planDiscard(input: DiscardInput): DiscardPlan {
  const { file } = input;
  if (input.live === "staged") {
    return { kind: "refuse", reason: "D discards working-tree changes: load the Unstaged view" };
  }
  const restore: DiscardPlan = { kind: "restore", path: file.path, label: `restored ${file.path}` };
  if (input.range === null && file.changeType === "deleted" && !file.isUntracked) {
    // Hunk says the file is gone: no patch to read — `git checkout` puts the
    // index's copy back, a binary's included. An empty patch means it is
    // already back and the review is behind.
    return input.patchText.trim() === "" ? { kind: "refuse", reason: `nothing to discard in ${file.path} — press r to refresh` } : restore;
  }
  const guard = guardPartial(file, input.reviewPatchText, input.patchText, DISCARD_WORDING, input.range === null);
  if (guard.kind === "refuse") {
    return guard;
  }
  if (guard.kind === "deleted") {
    return restore;
  }
  const { parsed } = guard;
  if (input.range === null) {
    const hunkIndex = input.hunkIndex;
    if (hunkIndex === null) {
      return { kind: "refuse", reason: "put the cursor on a hunk — D discards a hunk, or a range after v" };
    }
    const hunk = parsed.hunks[hunkIndex];
    if (hunkIndex < 0 || hunk === undefined) {
      return { kind: "refuse", reason: `no hunk ${hunkIndex + 1} in ${file.path}` };
    }
    const whole: LineRange = { from: { hunk: hunkIndex, index: 0 }, to: { hunk: hunkIndex, index: hunk.lines.length - 1 } };
    const written = writeRange(parsed, whole, true);
    if (written.kind === "refuse") {
      return { kind: "refuse", reason: `nothing to discard in hunk ${hunkIndex + 1} of ${file.path}` };
    }
    return {
      kind: "apply",
      patch: written.patch,
      label: `discarded hunk ${hunkIndex + 1} of ${file.path}`,
      lines: written.lines,
      hunk: hunkIndex,
    };
  }
  const range = locateRange(parsed, input.range.anchor, input.range.head);
  if ("kind" in range) {
    return range;
  }
  const written = writeRange(parsed, range, true);
  if (written.kind === "refuse") {
    return written;
  }
  return {
    kind: "apply",
    patch: written.patch,
    label: `discarded ${lineCount(written.lines)} of ${file.path}`,
    lines: written.lines,
    hunk: null,
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

/** The toast after a plan was carried out. */
export function describe(plan: HunkPlan | FilePlan | RangePlan | DiscardPlan): string {
  if ("kind" in plan) {
    return plan.kind === "refuse" ? plan.reason : "label" in plan ? plan.label : "";
  }
  return plan.label;
}
