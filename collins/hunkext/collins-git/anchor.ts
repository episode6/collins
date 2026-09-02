// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * The range anchor: the one line `v` remembered, so that the next `x` or
 * `D` can act on everything between it and the cursor.
 *
 * Hunk has no range selection of its own, and a command only learns the
 * cursor line when it fires (`ctx.selection` is a snapshot), so a range is
 * two keystrokes: `v` stores this anchor, the user moves, and `x`/`D` read
 * the cursor again as the head. The anchor is extension state, not hunk's,
 * and a reload renumbers file ids and may move every line — so it carries
 * the file's *review patch* as its identity: after a reload the
 * composition root asks `rebindAnchor` whether the same file, with the
 * same patch, is still loaded on the same side; if so the anchor follows
 * it (its `fileId` re-pointed), else it is dropped and the user told.
 * `anchorMarks` is what the line highlighter paints so the user can see
 * where it is; it matches by path and patch too, so the mark reappears
 * on a reload without a refresh call.
 */

import type { ExtensionLineHighlight } from "hunkdiff/extension";
import type { Side } from "./model.ts";
import { parseFilePatch } from "./patch.ts";
import { locate, type PatchSide } from "./range.ts";

export interface Anchor {
  /** Hunk's id for the file when `v` fired, or as last re-pointed by `rebindAnchor`. */
  readonly fileId: string;
  /** The file's path, the stable half of the address. */
  readonly path: string;
  /** The file's review patch (`ExtensionDiffFile.patch`) when the anchor was set. */
  readonly patch: string;
  /** Which working-tree side was loaded when the anchor was set. */
  readonly live: Side;
  readonly side: PatchSide;
  readonly line: number;
}

/** The part of a changeset's file `rebindAnchor` and `anchorMarks` look at. */
export interface AnchoredFile {
  readonly id: string;
  readonly path: string;
  readonly patch: string;
}

/**
 * A mark wide enough for any line when the anchored text cannot be measured:
 * hunk paints only the cells that carry text, so overshooting is harmless.
 */
const FALLBACK_RANGE: readonly [number, number] = [0, 200];

let anchor: Anchor | null = null;

export function setAnchor(next: Anchor): void {
  anchor = next;
}

/** Forget the anchor; true when there was one to forget. */
export function clearAnchor(): boolean {
  const had = anchor !== null;
  anchor = null;
  return had;
}

export function currentAnchor(): Anchor | null {
  return anchor;
}

/** Whether `file` is the one the anchor was set in: same path, same review patch. */
export function anchorMatches(target: Anchor, file: AnchoredFile): boolean {
  return target.path === file.path && target.patch === file.patch;
}

/**
 * After a reload: keep the anchor when the loaded side is the one it was
 * set on and a file with its path still shows the very same patch —
 * re-pointing `fileId` at the file's new id — else drop it. `none` when
 * there was no anchor, `kept` or `dropped` otherwise.
 */
export function rebindAnchor(files: readonly AnchoredFile[], live: Side | null): "none" | "kept" | "dropped" {
  if (anchor === null) {
    return "none";
  }
  const same = live === anchor.live ? files.find((file) => anchorMatches(anchor!, file)) : undefined;
  if (same === undefined) {
    anchor = null;
    return "dropped";
  }
  anchor = { ...anchor, fileId: same.id };
  return "kept";
}

/** The mark that paints the anchored line: the whole of its text, in amber. */
function markFor(file: { readonly patch: string }, target: Anchor): ExtensionLineHighlight {
  let range: readonly [number, number] = FALLBACK_RANGE;
  try {
    const patch = parseFilePatch(file.patch);
    const pos = locate(patch, { side: target.side, line: target.line });
    const text = pos === null ? undefined : patch.hunks.find((hunk) => hunk.index === pos.hunk)?.lines[pos.index]?.text;
    if (text !== undefined) {
      range = [0, Math.max(text.length, 1)];
    }
  } catch {
    // A patch this parser cannot read is still one hunk renders; paint by address.
  }
  return { side: target.side, line: target.line, range, tone: "warning" };
}

/**
 * What the `range-anchor` highlighter returns for one file: the anchored
 * line when the anchor is in this file — the same path and the same
 * patch, never the id, which hunk reissues on every reload — else nothing.
 */
export function anchorMarks(file: AnchoredFile, target: Anchor | null): ExtensionLineHighlight[] | null {
  if (target === null || !anchorMatches(target, file)) {
    return null;
  }
  return [markFor(file, target)];
}
