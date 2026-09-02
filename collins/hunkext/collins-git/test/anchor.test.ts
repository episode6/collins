// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { beforeEach, describe, expect, test } from "bun:test";
import { anchorMarks, clearAnchor, currentAnchor, rebindAnchor, setAnchor, type Anchor } from "../anchor.ts";

const patch = `diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -1,4 +1,4 @@
 one
-two was here
+two
 three
 four
`;

const file = { id: "/repo:1:f.txt", path: "f.txt", patch };

function anchorAt(side: "old" | "new", line: number, overrides: Partial<Anchor> = {}): Anchor {
  return { fileId: file.id, path: file.path, patch, live: "unstaged", side, line, ...overrides };
}

describe("the anchor state", () => {
  beforeEach(() => {
    clearAnchor();
  });

  test("starts empty and clearing an empty anchor says so", () => {
    expect(currentAnchor()).toBeNull();
    expect(clearAnchor()).toBe(false);
    expect(rebindAnchor([file], "unstaged")).toBe("none");
  });

  test("set, read back, clear", () => {
    const anchor = anchorAt("old", 2);
    setAnchor(anchor);
    expect(currentAnchor()).toBe(anchor);
    expect(clearAnchor()).toBe(true);
    expect(currentAnchor()).toBeNull();
  });
});

describe("rebindAnchor, after a reload", () => {
  beforeEach(() => {
    clearAnchor();
  });

  test("follows the file to its new id when the same side shows the same patch", () => {
    setAnchor(anchorAt("old", 2));
    const reloaded = [{ id: "/repo:2:g.txt", path: "g.txt", patch: "other" }, { ...file, id: "/repo:2:f.txt" }];
    expect(rebindAnchor(reloaded, "unstaged")).toBe("kept");
    expect(currentAnchor()).toEqual(anchorAt("old", 2, { fileId: "/repo:2:f.txt" }));
  });

  test("drops the anchor when the file's patch changed, the file is gone, or another side is loaded", () => {
    setAnchor(anchorAt("old", 2));
    expect(rebindAnchor([{ ...file, patch: patch.replace(" four\n", " four!\n") }], "unstaged")).toBe("dropped");
    expect(currentAnchor()).toBeNull();

    setAnchor(anchorAt("old", 2));
    expect(rebindAnchor([{ id: "x", path: "g.txt", patch }], "unstaged")).toBe("dropped");
    expect(currentAnchor()).toBeNull();

    setAnchor(anchorAt("old", 2));
    expect(rebindAnchor([file], "staged")).toBe("dropped");
    setAnchor(anchorAt("old", 2));
    expect(rebindAnchor([file], null)).toBe("dropped");
    expect(currentAnchor()).toBeNull();
  });
});

describe("anchorMarks", () => {
  test("paints nothing without an anchor", () => {
    expect(anchorMarks(file, null)).toBeNull();
  });

  test("paints nothing in a file the anchor is not in — by path or by patch, never by id", () => {
    expect(anchorMarks({ ...file, path: "g.txt" }, anchorAt("old", 2))).toBeNull();
    expect(anchorMarks({ ...file, patch: `${patch} five\n` }, anchorAt("old", 2))).toBeNull();
    // A reload reissues ids; the mark still finds its file.
    expect(anchorMarks({ ...file, id: "/repo:2:f.txt" }, anchorAt("old", 2))).toHaveLength(1);
  });

  test("marks the whole text of a removed line in amber", () => {
    expect(anchorMarks(file, anchorAt("old", 2))).toEqual([
      { side: "old", line: 2, range: [0, "two was here".length], tone: "warning" },
    ]);
  });

  test("marks an added line by its new-side number", () => {
    expect(anchorMarks(file, anchorAt("new", 2))).toEqual([{ side: "new", line: 2, range: [0, 3], tone: "warning" }]);
  });

  test("finds a context line addressed by either side", () => {
    expect(anchorMarks(file, anchorAt("new", 3))).toEqual([{ side: "new", line: 3, range: [0, 5], tone: "warning" }]);
    expect(anchorMarks(file, anchorAt("old", 3))).toEqual([{ side: "old", line: 3, range: [0, 5], tone: "warning" }]);
  });

  test("an empty line still gets a one-cell mark", () => {
    const blank = patch.replace(" four\n", " \n");
    expect(anchorMarks({ ...file, patch: blank }, anchorAt("new", 4, { patch: blank }))).toEqual([
      { side: "new", line: 4, range: [0, 1], tone: "warning" },
    ]);
  });

  test("falls back to a wide mark when the line is not in the patch or the patch cannot be read", () => {
    expect(anchorMarks(file, anchorAt("new", 99))).toEqual([{ side: "new", line: 99, range: [0, 200], tone: "warning" }]);
    expect(anchorMarks({ ...file, patch: "not a patch" }, anchorAt("old", 2, { patch: "not a patch" }))).toEqual([
      { side: "old", line: 2, range: [0, 200], tone: "warning" },
    ]);
  });
});
