// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import { describe as describePlan, planAll, planFileToggle, planHunkToggle } from "../staging.ts";

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
  });
});
