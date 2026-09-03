// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * Line ranges inside one file's patch: where a `(side, line)` address the
 * review reports lands among the patch's lines, and the partial patch
 * that stages (or unstages, or discards) just the lines between two such
 * addresses.
 *
 * A range is an inclusive span of positions in *patch order* — the hunk
 * index, then the line's index inside the hunk. It is never a count of
 * cursor stops: the split layout visits a replaced block row by row
 * (`old 25, new 25, old 26`) while the stacked one visits it as the patch
 * reads (`old 25, old 26, new 25`), and both must select the same lines.
 *
 * The writer follows the rule every line-level stager ends up with. To
 * stage forward, the index holds the old side: selected `+`/`-` lines are
 * kept, an unselected `+` is dropped (the index never saw it) and an
 * unselected `-` becomes context (the index still has it). To apply in
 * reverse — unstaging against a `--cached` patch, discarding in the
 * working tree — the target holds the new side, so the roles swap: an
 * unselected `-` is dropped and an unselected `+` becomes context.
 * Context is always kept, so no hunk loses its anchoring and the counts
 * are ours (`git apply` gets no `--recount`; it does get `--unidiff-zero`,
 * since the source diff may have had no context to keep — see git.ts).
 */

import {
  formatHunkHeader,
  partialHeaderLines,
  renderPatchLines,
  type FilePatch,
  type PatchHunk,
  type PatchLine,
  type PatchLineKind,
} from "./patch.ts";

export type PatchSide = "old" | "new";

/** A line the way hunk names it: a side and a 1-based number on that side. */
export interface LineAddress {
  readonly side: PatchSide;
  readonly line: number;
}

/** A line's place in the patch: `PatchHunk.index`, then its index in `hunk.lines`. */
export interface LinePos {
  readonly hunk: number;
  readonly index: number;
}

/** An inclusive span of positions with `from <= to`. */
export interface LineRange {
  readonly from: LinePos;
  readonly to: LinePos;
}

/** Thrown by `writeSelectedLines` for a range no patch can describe. */
export class RangeRefusal extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RangeRefusal";
  }
}

/**
 * The hunk line a `(side, line)` names, or null when no hunk line carries
 * that address — a hunk or file header, a line outside every hunk, or a
 * number that went stale with the file.
 *
 * A `-` line answers only to its old-side number and a `+` line only to
 * its new-side number; a context line answers to either, because hunk
 * reports context by its new-side number while an address that arrived
 * through `revealLine` may name the same line from the old side.
 */
export function locate(patch: FilePatch, address: LineAddress): LinePos | null {
  for (const hunk of patch.hunks) {
    let old = hunk.oldStart;
    let fresh = hunk.newStart;
    for (const [index, line] of hunk.lines.entries()) {
      const hit =
        line.kind === "context"
          ? (address.side === "old" ? old : fresh) === address.line
          : line.kind === "removed"
            ? address.side === "old" && old === address.line
            : address.side === "new" && fresh === address.line;
      if (hit) {
        return { hunk: hunk.index, index };
      }
      if (line.kind !== "added") {
        old += 1;
      }
      if (line.kind !== "removed") {
        fresh += 1;
      }
    }
  }
  return null;
}

/** Order positions by hunk, then by line within the hunk. */
export function comparePos(a: LinePos, b: LinePos): number {
  return a.hunk - b.hunk || a.index - b.index;
}

/** The range between two positions, whichever way round they came. */
export function orderRange(a: LinePos, b: LinePos): LineRange {
  return comparePos(a, b) <= 0 ? { from: a, to: b } : { from: b, to: a };
}

function inRange(range: LineRange, pos: LinePos): boolean {
  return comparePos(range.from, pos) <= 0 && comparePos(pos, range.to) <= 0;
}

/** The `+` and `-` lines inside the range — what the toasts count. */
export function countSelected(patch: FilePatch, range: LineRange): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const hunk of patch.hunks) {
    for (const [index, line] of hunk.lines.entries()) {
      if (line.kind === "context" || !inRange(range, { hunk: hunk.index, index })) {
        continue;
      }
      if (line.kind === "added") {
        added += 1;
      } else {
        removed += 1;
      }
    }
  }
  return { added, removed };
}

/** How many lines of one side a hunk's lines describe. */
function sideCount(lines: readonly PatchLine[], side: PatchSide): number {
  const excluded: PatchLineKind = side === "new" ? "removed" : "added";
  return lines.filter((line) => line.kind !== excluded).length;
}

/**
 * A `\ No newline at end of file` marker means "this is the last line of
 * its side, and the file stops without a newline". Writing a marked line
 * anywhere but last on a side would describe a file that cannot exist —
 * which is what a demoted `-last` followed by a kept `+` does. Null when
 * every marker sits where it must, else the offending side.
 */
function misplacedMarker(lines: readonly PatchLine[]): PatchSide | null {
  for (const side of ["old", "new"] as const) {
    const excluded: PatchLineKind = side === "new" ? "removed" : "added";
    const onSide = lines.filter((line) => line.kind !== excluded);
    if (onSide.some((line, index) => line.noNewlineAtEof && index !== onSide.length - 1)) {
      return side;
    }
  }
  return null;
}

/**
 * One hunk's lines with only the range's changes in it, or null when the
 * range selects no `+`/`-` in this hunk.
 */
function selectHunkLines(hunk: PatchHunk, range: LineRange, reverse: boolean): PatchLine[] | null {
  const demoted: PatchLineKind = reverse ? "added" : "removed";
  const lines: PatchLine[] = [];
  let changed = false;
  for (const [index, line] of hunk.lines.entries()) {
    if (line.kind === "context") {
      lines.push(line);
    } else if (inRange(range, { hunk: hunk.index, index })) {
      lines.push(line);
      changed = true;
    } else if (line.kind === demoted) {
      lines.push({ ...line, kind: "context" });
    }
    // The other unselected kind is dropped, its marker with it.
  }
  return changed ? lines : null;
}

/**
 * Render the partial patch for a range, or null when the range holds no
 * `+`/`-` line at all.
 *
 * Every hunk's counts are recomputed from what was emitted. The source
 * side's start stays where git put it (the target still looks exactly
 * like that side), while the result side's start is shifted by what the
 * earlier hunks — omitted or trimmed — no longer contribute: forward,
 * the new-side start moves by the added lines left out; in reverse, the
 * old-side start moves by the removed lines left out. The header goes out
 * without a mode change (`partialHeaderLines`). Throws a `RangeRefusal`
 * when a `\ No newline` marker would land mid-side.
 */
export function writeSelectedLines(
  patch: FilePatch,
  range: LineRange,
  options: { reverse: boolean },
): string | null {
  const { reverse } = options;
  const out = partialHeaderLines(patch);
  let shift = 0;
  let any = false;
  for (const hunk of patch.hunks) {
    const originalDelta = reverse
      ? sideCount(hunk.lines, "old") - sideCount(hunk.lines, "new")
      : sideCount(hunk.lines, "new") - sideCount(hunk.lines, "old");
    const lines = selectHunkLines(hunk, range, reverse);
    if (lines === null) {
      shift += originalDelta;
      continue;
    }
    if (misplacedMarker(lines) !== null) {
      throw new RangeRefusal("select the whole end-of-file change");
    }
    const oldCount = sideCount(lines, "old");
    const newCount = sideCount(lines, "new");
    const header = reverse
      ? formatHunkHeader(hunk.oldStart - shift, oldCount, hunk.newStart, newCount, hunk.heading)
      : formatHunkHeader(hunk.oldStart, oldCount, hunk.newStart - shift, newCount, hunk.heading);
    out.push(header, ...renderPatchLines(lines));
    shift += originalDelta - (reverse ? oldCount - newCount : newCount - oldCount);
    any = true;
  }
  return any ? `${out.join("\n")}\n` : null;
}

/**
 * Null when the review's patch and the fresh one describe the same hunks
 * — the same count, and per hunk the same line kinds and texts — else a
 * short reason. This is the "did the working copy change since the
 * review loaded" check for a line range: the numbers the range is built
 * from came out of the review, and they mean nothing against a patch
 * with other lines in it. Headers are not compared; the disagreement
 * check already covers the spans.
 */
export function sameHunks(review: FilePatch, fresh: FilePatch): string | null {
  if (review.hunks.length !== fresh.hunks.length) {
    return `the review shows ${review.hunks.length} hunk(s) but the disk has ${fresh.hunks.length}`;
  }
  for (const [hunkIndex, reviewed] of review.hunks.entries()) {
    const current = fresh.hunks[hunkIndex]!;
    const label = `hunk ${hunkIndex + 1}`;
    if (reviewed.lines.length !== current.lines.length) {
      return `${label} has ${reviewed.lines.length} lines in the review but ${current.lines.length} on disk`;
    }
    for (const [lineIndex, line] of reviewed.lines.entries()) {
      const other = current.lines[lineIndex]!;
      if (line.kind !== other.kind) {
        return `${label} differs: line ${lineIndex + 1} is ${describeKind(line.kind)} in the review but ${describeKind(other.kind)} on disk`;
      }
      if (line.text !== other.text) {
        return `${label} differs: line ${lineIndex + 1} is \`${line.text}\` in the review but \`${other.text}\` on disk`;
      }
    }
  }
  return null;
}

function describeKind(kind: PatchLineKind): string {
  return kind === "context" ? "context" : kind === "added" ? "an addition" : "a removal";
}
