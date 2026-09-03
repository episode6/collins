// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import {
  describe as describePlan,
  planAll,
  planDiscard,
  planFileToggle,
  planHunkToggle,
  planRangeToggle,
  type DiscardInput,
  type RangeToggleInput,
} from "../staging.ts";

const threeHunks = `diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -2,1 +2,3 @@
 b
+NEW1
+NEW2
@@ -10,2 +12,1 @@
-x
-y
+z
@@ -20,1 +21,2 @@
 q
+Q
`;

const hostHunks = [
  { index: 0, newRange: [2, 4] as [number, number] },
  { index: 1, newRange: [12, 12] as [number, number] },
  { index: 2, newRange: [21, 22] as [number, number] },
];

const file = { path: "f.txt", changeType: "change" as const, hunks: hostHunks };

describe("planHunkToggle", () => {
  test("stages hunk 2 of 3 with the later hunk dropped and the start renumbered", () => {
    const plan = planHunkToggle({ file, hunkIndex: 1, live: "unstaged", patchText: threeHunks });
    expect(plan.kind).toBe("apply");
    if (plan.kind !== "apply") {
      return;
    }
    expect(plan.reverse).toBe(false);
    expect(plan.label).toBe("staged hunk 2 of f.txt");
    expect(plan.patch).toBe(`diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -10,2 +10 @@
-x
-y
+z
`);
    expect(describePlan(plan)).toBe("staged hunk 2 of f.txt");
  });

  test("the staged view reverses the apply", () => {
    const plan = planHunkToggle({ file, hunkIndex: 0, live: "staged", patchText: threeHunks });
    expect(plan).toMatchObject({ kind: "apply", reverse: true, label: "unstaged hunk 1 of f.txt" });
  });

  test("refuses binaries, oversized files and a binary patch", () => {
    expect(planHunkToggle({ file: { ...file, isBinary: true }, hunkIndex: 0, live: "unstaged", patchText: "" })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/binary/),
    });
    expect(planHunkToggle({ file: { ...file, isTooLarge: true }, hunkIndex: 0, live: "unstaged", patchText: "" })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/too large/),
    });
    const binaryPatch = "diff --git a/i.png b/i.png\nindex 1111111..2222222 100644\nBinary files a/i.png and b/i.png differ\n";
    expect(planHunkToggle({ file: { path: "i.png", hunks: [] }, hunkIndex: 0, live: "unstaged", patchText: binaryPatch })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/binary/),
    });
  });

  test("renames and untracked files fall through to the whole file", () => {
    expect(planHunkToggle({ file: { path: "n.txt", isUntracked: true, changeType: "new" }, hunkIndex: 0, live: "unstaged", patchText: "" })).toEqual({
      kind: "file",
    });
    expect(planHunkToggle({ file: { path: "b", previousPath: "a", changeType: "rename-changed" }, hunkIndex: 0, live: "staged", patchText: threeHunks })).toEqual({
      kind: "file",
    });
    expect(planHunkToggle({ file: { path: "n.txt", changeType: "new" }, hunkIndex: 0, live: "unstaged", patchText: "\n" })).toEqual({ kind: "file" });
  });

  test("no hunk selected means the whole file — after the same refusals", () => {
    // hunk selects no hunk in a binary or oversized file: `x` there must
    // say "use X", not quietly become X.
    expect(planHunkToggle({ file: { ...file, isBinary: true }, hunkIndex: null, live: "unstaged", patchText: "" })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/binary.*X/),
    });
    expect(planHunkToggle({ file: { ...file, isTooLarge: true }, hunkIndex: null, live: "unstaged", patchText: "" })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/too large.*X/),
    });
    const binaryPatch = "diff --git a/i.png b/i.png\nindex 1111111..2222222 100644\nBinary files a/i.png and b/i.png differ\n";
    expect(planHunkToggle({ file: { path: "i.png", hunks: [] }, hunkIndex: null, live: "unstaged", patchText: binaryPatch })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/binary/),
    });
    // The cursor on a text file rather than one of its hunks: the file.
    expect(planHunkToggle({ file, hunkIndex: null, live: "unstaged", patchText: threeHunks })).toEqual({ kind: "file" });
    expect(planHunkToggle({ file: { path: "n.txt", isUntracked: true, changeType: "new" }, hunkIndex: null, live: "unstaged", patchText: "" })).toEqual({
      kind: "file",
    });
    // A file whose review is stale is still fine whole: no hunk arithmetic is involved.
    const stale = { ...file, hunks: [{ index: 0, newRange: [99, 99] as [number, number] }] };
    expect(planHunkToggle({ file: stale, hunkIndex: null, live: "staged", patchText: threeHunks })).toEqual({ kind: "file" });
  });

  test("refuses when the file changed since the review loaded", () => {
    const stale = [hostHunks[0]!, { index: 1, newRange: [13, 13] as [number, number] }, hostHunks[2]!];
    const plan = planHunkToggle({ file: { ...file, hunks: stale }, hunkIndex: 1, live: "unstaged", patchText: threeHunks });
    expect(plan).toMatchObject({ kind: "refuse", reason: expect.stringMatching(/changed since the review loaded — press r/) });
    const fewer = planHunkToggle({ file: { ...file, hunks: hostHunks.slice(0, 2) }, hunkIndex: 1, live: "unstaged", patchText: threeHunks });
    expect(fewer.kind).toBe("refuse");
  });

  test("refuses an empty patch, a bad index, an unsafe path and an odd mode", () => {
    expect(planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText: "" })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/nothing to stage in f.txt/),
    });
    expect(planHunkToggle({ file, hunkIndex: 3, live: "unstaged", patchText: threeHunks })).toMatchObject({
      kind: "refuse",
      reason: "no hunk 4 in f.txt",
    });
    const escaped = threeHunks.replace(/f\.txt/g, "../f.txt");
    expect(planHunkToggle({ file: { ...file, path: "../f.txt" }, hunkIndex: 0, live: "unstaged", patchText: escaped })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/outside/),
    });
    const link = threeHunks.replace("index 1111111..2222222 100644", "index 1111111..2222222 120000");
    expect(planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText: link })).toMatchObject({
      kind: "refuse",
      reason: expect.stringMatching(/symbolic link/),
    });
  });
});

describe("planFileToggle and planAll", () => {
  test("a rename names both paths in one call", () => {
    expect(planFileToggle({ file: { path: "b", previousPath: "a" }, live: "unstaged" })).toEqual({
      paths: ["a", "b"],
      stage: true,
      label: "staged b",
    });
    expect(planFileToggle({ file: { path: "f.txt" }, live: "staged" })).toEqual({
      paths: ["f.txt"],
      stage: false,
      label: "unstaged f.txt",
    });
  });

  test("counts the side the key acts on", () => {
    const status = {
      unstaged: [{ path: "a", code: "M" as const }, { path: "b", code: "?" as const }],
      staged: [{ path: "c", code: "A" as const }],
    };
    expect(planAll(status, true)).toEqual({ count: 2, stage: true });
    expect(planAll(status, false)).toEqual({ count: 1, stage: false });
    expect(planAll(null, true)).toEqual({ count: 0, stage: true });
  });

  test("describe names what was done", () => {
    expect(describePlan({ count: 4, stage: true })).toBe("staged 4 files");
    expect(describePlan({ count: 1, stage: false })).toBe("unstaged 1 file");
    expect(describePlan({ paths: ["x"], stage: false, label: "unstaged x" })).toBe("unstaged x");
    expect(describePlan({ kind: "refuse", reason: "why" })).toBe("why");
    expect(describePlan({ kind: "apply", patch: "", reverse: false, label: "staged 3 lines of f.txt", lines: 3 })).toBe("staged 3 lines of f.txt");
    expect(describePlan({ kind: "apply", patch: "", label: "discarded hunk 2 of f.txt", lines: 2, hunk: 1 })).toBe("discarded hunk 2 of f.txt");
  });
});

/** The range keys' inputs: hunk 2 of `threeHunks` is `-x -y +z` at old 10-11 / new 12. */
const rangeInput: RangeToggleInput = {
  file,
  reviewPatchText: threeHunks,
  anchor: { side: "old", line: 10 },
  head: { side: "new", line: 12 },
  live: "unstaged",
  patchText: threeHunks,
};

describe("planRangeToggle", () => {
  test("stages the lines between the anchor and the cursor, in either order", () => {
    const plan = planRangeToggle(rangeInput);
    expect(plan).toMatchObject({ kind: "apply", reverse: false, label: "staged 3 lines of f.txt", lines: 3 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(plan.patch).toBe(`diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -10,2 +10 @@
-x
-y
+z
`);
    expect(planRangeToggle({ ...rangeInput, anchor: rangeInput.head, head: rangeInput.anchor })).toEqual(plan);
    const one = planRangeToggle({ ...rangeInput, head: { side: "old", line: 10 } });
    expect(one).toMatchObject({ kind: "apply", label: "staged 1 line of f.txt", lines: 1 });
    if (one.kind === "apply") {
      // `-y` demotes to context, `+z` is dropped.
      expect(one.patch).toContain("@@ -10,2 +10 @@\n-x\n y\n");
    }
  });

  test("the staged view unstages, with the reverse rule", () => {
    const plan = planRangeToggle({ ...rangeInput, live: "staged", head: { side: "old", line: 10 } });
    expect(plan).toMatchObject({ kind: "apply", reverse: true, label: "unstaged 1 line of f.txt", lines: 1 });
    if (plan.kind === "apply") {
      // `-y` is dropped, `+z` demoted: the index keeps both. Hunk 1's two
      // added lines stay in the index too, so the old side starts at 12.
      expect(plan.patch).toContain("@@ -12,2 +12 @@\n-x\n z\n");
    }
  });

  test("refuses, in order: binary, too large, untracked, rename, empty, unreadable, new or deleted", () => {
    const refusal = (input: Partial<RangeToggleInput>) => {
      const plan = planRangeToggle({ ...rangeInput, ...input });
      return plan.kind === "refuse" ? plan.reason : plan;
    };
    expect(refusal({ file: { ...file, isBinary: true, isTooLarge: true, isUntracked: true } })).toBe("f.txt is binary: stage the whole file with X");
    expect(refusal({ file: { ...file, isTooLarge: true, isUntracked: true } })).toBe("f.txt is too large to stage by hunk: use X");
    expect(refusal({ file: { ...file, isUntracked: true, previousPath: "e.txt" } })).toBe("f.txt is untracked: stage the whole file with X");
    expect(refusal({ file: { ...file, previousPath: "e.txt" } })).toBe("renames stage whole: use X");
    expect(refusal({ file: { ...file, changeType: "rename-pure" } })).toBe("renames stage whole: use X");
    expect(refusal({ patchText: "\n" })).toBe("nothing to stage in f.txt — press r to refresh");
    expect(refusal({ patchText: "\n", live: "staged" })).toBe("nothing to unstage in f.txt — press r to refresh");
    expect(refusal({ patchText: "diff --git a/f.txt b/f.txt\n@@ -1,3 +1,3 @@\n a\n" })).toMatch(/^cannot read the patch for f.txt/);
    const binaryPatch = "diff --git a/f.txt b/f.txt\nindex 1111111..2222222 100644\nBinary files a/f.txt and b/f.txt differ\n";
    expect(refusal({ patchText: binaryPatch })).toBe("f.txt is binary: stage the whole file with X");
    expect(refusal({ patchText: threeHunks.replace(/f\.txt/g, "../f.txt") })).toMatch(/outside/);
    expect(refusal({ patchText: threeHunks.replace("100644", "120000") })).toBe("cannot stage f.txt by hunk: it is a symbolic link, which hunks cannot describe");
    // A mode change rides along in the header but is not a line: the range stages, the mode does not.
    const modeChange = threeHunks.replace("index 1111111..2222222 100644", "old mode 100644\nnew mode 100755\nindex 1111111..2222222");
    const withMode = planRangeToggle({ ...rangeInput, patchText: modeChange, reviewPatchText: modeChange });
    expect(withMode).toMatchObject({ kind: "apply", lines: 3 });
    if (withMode.kind === "apply") {
      expect(withMode.patch.split("\n").slice(0, 4)).toEqual(["diff --git a/f.txt b/f.txt", "index 1111111..2222222", "--- a/f.txt", "+++ b/f.txt"]);
    }
    const added = threeHunks.replace("index 1111111..2222222 100644", "new file mode 100644\nindex 0000000..2222222").replace("--- a/f.txt", "--- /dev/null");
    expect(refusal({ patchText: added })).toBe("partial staging of a new or deleted file: use X");
    const deleted = threeHunks.replace("index 1111111..2222222 100644", "deleted file mode 100644\nindex 1111111..0000000").replace("+++ b/f.txt", "+++ /dev/null");
    expect(refusal({ patchText: deleted })).toBe("partial staging of a new or deleted file: use X");
    const renamedPatch = threeHunks.replace("index 1111111..2222222 100644", "similarity index 90%\nrename from e.txt\nrename to f.txt\nindex 1111111..2222222 100644");
    expect(refusal({ patchText: renamedPatch })).toBe("renames stage whole: use X");
  });

  test("refuses when hunk's spans or the review's lines no longer match the disk", () => {
    const refusal = (input: Partial<RangeToggleInput>) => {
      const plan = planRangeToggle({ ...rangeInput, ...input });
      return plan.kind === "refuse" ? plan.reason : plan;
    };
    const stale = [hostHunks[0]!, { index: 1, newRange: [13, 13] as [number, number] }, hostHunks[2]!];
    expect(refusal({ file: { ...file, hunks: stale } })).toBe(
      "f.txt changed since the review loaded — press r (hunk 2 spans lines 12-12 in the patch but 13-13 in hunk)",
    );
    // Same spans, a line's text changed: only sameHunks sees it.
    expect(refusal({ reviewPatchText: threeHunks.replace("-y\n", "-Y\n") })).toBe(
      "f.txt changed since the review loaded — press r (hunk 2 differs: line 2 is `Y` in the review but `y` on disk)",
    );
    // A review patch with no hunks in it is a count mismatch; a truncated one is unreadable.
    expect(refusal({ reviewPatchText: "garbage" })).toBe(
      "f.txt changed since the review loaded — press r (the review shows 0 hunk(s) but the disk has 3)",
    );
    expect(refusal({ reviewPatchText: "diff --git a/f.txt b/f.txt\n@@ -1,3 +1,3 @@\n a\n" })).toMatch(/^cannot read the review's patch for f.txt/);
  });

  test("refuses an anchor or cursor that names no diff line, and a range with no change in it", () => {
    expect(planRangeToggle({ ...rangeInput, anchor: { side: "old", line: 5 } })).toEqual({
      kind: "refuse",
      reason: "the anchor is not on a diff line — press v again",
    });
    // `+z` is new 12; there is no old 12 in hunk 2 (old 10-11) and old 12 is outside every hunk.
    expect(planRangeToggle({ ...rangeInput, head: { side: "old", line: 12 } })).toEqual({
      kind: "refuse",
      reason: "the cursor is not on a diff line",
    });
    expect(planRangeToggle({ ...rangeInput, anchor: { side: "new", line: 2 }, head: { side: "old", line: 2 } })).toEqual({
      kind: "refuse",
      reason: "no changes between the anchor and the cursor",
    });
  });

  test("refuses to split an end-of-file change", () => {
    const eof = `diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -1,2 +1,3 @@
 a
-b
\\ No newline at end of file
+b
+c
`;
    const eofFile = { path: "f.txt", changeType: "change" as const, hunks: [{ index: 0, newRange: [1, 3] as [number, number] }] };
    expect(
      planRangeToggle({ ...rangeInput, file: eofFile, reviewPatchText: eof, patchText: eof, anchor: { side: "new", line: 3 }, head: { side: "new", line: 3 } }),
    ).toEqual({ kind: "refuse", reason: "select the whole end-of-file change" });
  });
});

describe("planDiscard", () => {
  const discardInput: DiscardInput = {
    file,
    reviewPatchText: threeHunks,
    live: "unstaged",
    hunkIndex: 1,
    range: null,
    patchText: threeHunks,
  };

  test("a hunk: the reversed-in-worktree patch with the other hunks' effect on the old side accounted for", () => {
    const plan = planDiscard(discardInput);
    expect(plan).toMatchObject({ kind: "apply", label: "discarded hunk 2 of f.txt", lines: 3, hunk: 1 });
    if (plan.kind !== "apply") {
      return;
    }
    // Hunk 1 added two lines that stay in the working tree: hunk 2's old side moves down by two.
    expect(plan.patch).toBe(`diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -12,2 +12 @@
-x
-y
+z
`);
    expect(describePlan(plan)).toBe("discarded hunk 2 of f.txt");
  });

  test("a range: the same writer, in reverse", () => {
    const plan = planDiscard({ ...discardInput, range: { anchor: { side: "old", line: 11 }, head: { side: "new", line: 12 } } });
    expect(plan).toMatchObject({ kind: "apply", label: "discarded 2 lines of f.txt", lines: 2, hunk: null });
    if (plan.kind === "apply") {
      // `-x` is outside the range and, in reverse, is dropped: `x` is not put back.
      expect(plan.patch).toContain("@@ -12 +12 @@\n-y\n+z\n");
    }
  });

  test("refuses the staged view before anything else", () => {
    expect(planDiscard({ ...discardInput, live: "staged", file: { ...file, isBinary: true } })).toEqual({
      kind: "refuse",
      reason: "D discards working-tree changes: load the Unstaged view",
    });
  });

  test("a deleted file is restored whole: by hunk's word for it without a parse, or by the patch's", () => {
    const restore = { kind: "restore" as const, path: "f.txt", label: "restored f.txt" };
    const gone = { ...file, changeType: "deleted" as const, hunks: [] };
    expect(planDiscard({ ...discardInput, file: gone, patchText: "Binary files a/f.txt and /dev/null differ\n" })).toEqual(restore);
    expect(planDiscard({ ...discardInput, file: { ...gone, isBinary: true }, hunkIndex: null })).toEqual(restore);
    // Already back on disk: the review is behind.
    expect(planDiscard({ ...discardInput, file: gone, patchText: "" })).toEqual({ kind: "refuse", reason: "nothing to discard in f.txt — press r to refresh" });
    const deleted = threeHunks.replace("index 1111111..2222222 100644", "deleted file mode 100644\nindex 1111111..0000000").replace("+++ b/f.txt", "+++ /dev/null");
    expect(planDiscard({ ...discardInput, patchText: deleted, hunkIndex: 0 })).toEqual(restore);
    expect(describePlan(restore)).toBe("restored f.txt");
    // A range in it is not a restore: the whole file is.
    expect(planDiscard({ ...discardInput, patchText: deleted, range: { anchor: { side: "old", line: 10 }, head: { side: "old", line: 11 } } })).toEqual({
      kind: "refuse",
      reason: "f.txt is deleted: D with no anchor restores it whole — esc, then D",
    });
    // The staged view still comes first, and so does the refusal of the wrong kind of file.
    expect(planDiscard({ ...discardInput, file: gone, live: "staged" }).kind).toBe("refuse");
  });

  test("refuses anything but a plain modification, and a hunk that is not there", () => {
    const refusal = (input: Partial<typeof discardInput>) => {
      const plan = planDiscard({ ...discardInput, ...input });
      return plan.kind === "refuse" ? plan.reason : plan;
    };
    const wholeOnly = "D reverts changes inside a modified file — use git from a shell for a new or renamed file";
    expect(refusal({ file: { ...file, isBinary: true } })).toBe("f.txt is binary: use git from a shell");
    expect(refusal({ file: { ...file, isTooLarge: true } })).toBe("f.txt is too large to discard by hunk: use git from a shell");
    expect(refusal({ file: { ...file, isUntracked: true } })).toBe(wholeOnly);
    expect(refusal({ file: { ...file, previousPath: "e.txt" } })).toBe(wholeOnly);
    const added = threeHunks.replace("index 1111111..2222222 100644", "new file mode 100644\nindex 0000000..2222222").replace("--- a/f.txt", "--- /dev/null");
    expect(refusal({ patchText: added })).toBe(wholeOnly);
    expect(refusal({ patchText: "" })).toBe("nothing to discard in f.txt — press r to refresh");
    expect(refusal({ hunkIndex: null })).toBe("put the cursor on a hunk — D discards a hunk, or a range after v");
    expect(refusal({ hunkIndex: 3 })).toBe("no hunk 4 in f.txt");
    expect(refusal({ reviewPatchText: threeHunks.replace("+z\n", "+Z\n") })).toMatch(/changed since the review loaded — press r \(hunk 2 differs/);
    expect(refusal({ range: { anchor: { side: "old", line: 1 }, head: { side: "old", line: 1 } } })).toBe("the anchor is not on a diff line — press v again");
    expect(refusal({ range: { anchor: { side: "new", line: 2 }, head: { side: "new", line: 2 } } })).toBe("no changes between the anchor and the cursor");
  });
});
