// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
// contributors); see collins/THIRD_PARTY_LICENSES.md.

import { describe, expect, test } from "bun:test";
import {
  findDisagreement,
  hunkRange,
  hunkSideLines,
  parseFilePatch,
  PatchParseError,
  unsafePathReason,
  unsupportedModeReason,
  writeSelectedHunks,
} from "../patch.ts";

const modified = `diff --git a/src/app.ts b/src/app.ts
index 1111111..2222222 100644
--- a/src/app.ts
+++ b/src/app.ts
@@ -1,3 +1,4 @@
 const a = 1;
-const b = 2;
+const b = 3;
+const c = 4;
 const d = 5;
`;

const twoHunks = parseFilePatch(`diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -2,1 +2,3 @@ context heading
 b
+NEW1
+NEW2
@@ -15,1 +17,1 @@
-old
+new
`);

describe("parseFilePatch", () => {
  test("reads paths, change kind, modes and hunk geometry", () => {
    const patch = parseFilePatch(modified);
    expect(patch).toMatchObject({ path: "src/app.ts", change: "modified", binary: false });
    expect(patch.declaredModes).toEqual(["100644"]);
    expect(patch.hunks).toHaveLength(1);
    expect(patch.hunks[0]).toMatchObject({ index: 0, oldStart: 1, oldCount: 3, newStart: 1, newCount: 4 });
    expect(patch.hunks[0]!.lines.map((line) => [line.kind, line.text])).toEqual([
      ["context", "const a = 1;"],
      ["removed", "const b = 2;"],
      ["added", "const b = 3;"],
      ["added", "const c = 4;"],
      ["context", "const d = 5;"],
    ]);
    expect(hunkSideLines(patch.hunks[0]!, "old")).toEqual(["const a = 1;", "const b = 2;", "const d = 5;"]);
    expect(hunkRange(patch.hunks[0]!, "new")).toEqual([1, 4]);
  });

  test("an empty side spans one line, an omitted count is one", () => {
    const deletion = parseFilePatch("--- a/f\n+++ b/f\n@@ -5,3 +4,0 @@\n-one\n-two\n-three\n");
    expect(hunkRange(deletion.hunks[0]!, "new")).toEqual([4, 4]);
    const single = parseFilePatch("--- a/f\n+++ b/f\n@@ -7 +7 @@\n-old\n+new\n");
    expect(single.hunks[0]).toMatchObject({ oldStart: 7, oldCount: 1, newStart: 7, newCount: 1 });
  });

  test("keeps no-newline markers, bare empty context lines and carriage returns", () => {
    const patch = parseFilePatch("--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-b\\r\n\\ No newline at end of file\n+B\\r\n\\ No newline at end of file\n");
    const [hunk] = patch.hunks;
    expect(hunkSideLines(hunk!, "new")).toEqual(["a", "", "B\\r"]);
    expect(hunk!.lines[2]!.noNewlineAtEof).toBe(true);
    expect(hunk!.lines[3]!.noNewlineAtEof).toBe(true);
  });

  test("recognises additions, deletions, renames and binaries", () => {
    expect(parseFilePatch("diff --git a/n b/n\nnew file mode 100644\n--- /dev/null\n+++ b/n\n@@ -0,0 +1,1 @@\n+hi\n")).toMatchObject({
      path: "n",
      change: "added",
    });
    expect(parseFilePatch("diff --git a/g b/g\ndeleted file mode 100644\n--- a/g\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-bye\n")).toMatchObject({
      path: "g",
      change: "deleted",
    });
    expect(
      parseFilePatch("diff --git a/old b/new\nsimilarity index 90%\nrename from old\nrename to new\n--- a/old\n+++ b/new\n@@ -1 +1 @@\n-a\n+b\n"),
    ).toMatchObject({ path: "new", previousPath: "old", change: "renamed" });
    const binary = parseFilePatch("diff --git a/img.png b/img.png\nindex 1111111..2222222 100644\nBinary files a/img.png and b/img.png differ\n");
    expect(binary).toMatchObject({ path: "img.png", binary: true });
    expect(binary.hunks).toEqual([]);
  });

  test("collects both modes of a mode change", () => {
    const patch = parseFilePatch("diff --git a/s b/s\nold mode 100644\nnew mode 120000\n--- a/s\n+++ b/s\n@@ -1 +1 @@\n-a\n+b\n");
    expect(patch.declaredModes).toEqual(["100644", "120000"]);
  });

  test("throws on a body that stops short or holds a stray line", () => {
    expect(() => parseFilePatch("--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n")).toThrow(PatchParseError);
    expect(() => parseFilePatch("--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n*a\n")).toThrow(PatchParseError);
  });
});

describe("writeSelectedHunks", () => {
  test("keeps the header verbatim and emits only the selected hunk", () => {
    const written = writeSelectedHunks(twoHunks, new Set([1]))!;
    expect(written.split("\n").slice(0, 4)).toEqual([
      "diff --git a/f.txt b/f.txt",
      "index 1111111..2222222 100644",
      "--- a/f.txt",
      "+++ b/f.txt",
    ]);
    expect(written).toContain("-old\n+new\n");
    expect(written).not.toContain("NEW1");
  });

  test("renumbers later hunks by what was dropped, leaves earlier ones alone", () => {
    expect(writeSelectedHunks(twoHunks, new Set([1]))).toContain("@@ -15 +15 @@");
    expect(writeSelectedHunks(twoHunks, new Set([0, 1]))).toContain("@@ -15 +17 @@");
    expect(writeSelectedHunks(twoHunks, new Set([0]))).toContain("@@ -2 +2,3 @@ context heading");
  });

  test("round-trips a whole patch and preserves a no-newline marker", () => {
    const text = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n one\n-two\n\\ No newline at end of file\n+two!\n\\ No newline at end of file\n";
    expect(writeSelectedHunks(parseFilePatch(text), new Set([0]))).toBe(text);
  });

  test("returns null when nothing is selected", () => {
    expect(writeSelectedHunks(twoHunks, new Set())).toBeNull();
  });
});

describe("findDisagreement", () => {
  test("agrees on matching counts and new-side spans", () => {
    expect(findDisagreement(twoHunks, [{ index: 0, newRange: [2, 4] }, { index: 1, newRange: [17, 17] }])).toBeNull();
    expect(findDisagreement(twoHunks, [{ index: 0 }, { index: 1 }])).toBeNull();
  });

  test("names a count mismatch or a moved hunk", () => {
    expect(findDisagreement(twoHunks, [{ index: 0, newRange: [2, 4] }])).toMatch(/1 hunk\(s\)/);
    expect(findDisagreement(twoHunks, [{ index: 0, newRange: [2, 4] }, { index: 1, newRange: [18, 18] }])).toMatch(
      /hunk 2 spans lines 17-17/,
    );
  });
});

describe("guards", () => {
  test("only regular files may be staged by hunk", () => {
    expect(unsupportedModeReason(["100644", "100755"])).toBeNull();
    expect(unsupportedModeReason(["120000"])).toMatch(/symbolic link/);
    expect(unsupportedModeReason(["160000"])).toMatch(/submodule/);
    expect(unsupportedModeReason(["123456"])).toMatch(/unrecognised/);
  });

  test("paths must be relative and inside the repository", () => {
    expect(unsafePathReason("src/app.ts")).toBeNull();
    expect(unsafePathReason("")).toMatch(/does not name/);
    expect(unsafePathReason("/etc/passwd")).toMatch(/absolute/);
    expect(unsafePathReason("C:\\x")).toMatch(/absolute/);
    expect(unsafePathReason("../x")).toMatch(/outside/);
  });
});
