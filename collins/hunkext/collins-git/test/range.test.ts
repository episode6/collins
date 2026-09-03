// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import { parseFilePatch } from "../patch.ts";
import {
  comparePos,
  countSelected,
  locate,
  orderRange,
  RangeRefusal,
  sameHunks,
  writeSelectedLines,
  type LinePos,
} from "../range.ts";

const HEADER = `diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
`;

/** Three hunks: a 1:2 replacement, a 1:1 replacement, a pure insertion. */
const threeHunks = parseFilePatch(`${HEADER}@@ -1,4 +1,5 @@
 a
-b
+B
+B2
 c
 d
@@ -10,3 +11,3 @@
 j
-k
+K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
`);

const at = (hunk: number, index: number): LinePos => ({ hunk, index });
const span = (from: LinePos, to: LinePos) => ({ from, to });

describe("locate", () => {
  test("a `-` line answers to its old-side number only, a `+` line to its new-side number only", () => {
    expect(locate(threeHunks, { side: "old", line: 2 })).toEqual(at(0, 1)); // -b
    expect(locate(threeHunks, { side: "new", line: 2 })).toEqual(at(0, 2)); // +B
    expect(locate(threeHunks, { side: "new", line: 3 })).toEqual(at(0, 3)); // +B2
    expect(locate(threeHunks, { side: "old", line: 11 })).toEqual(at(1, 1)); // -k
    expect(locate(threeHunks, { side: "new", line: 12 })).toEqual(at(1, 2)); // +K
    expect(locate(threeHunks, { side: "new", line: 23 })).toEqual(at(2, 2)); // +U2
  });

  test("a context line answers to either side", () => {
    expect(locate(threeHunks, { side: "old", line: 1 })).toEqual(at(0, 0));
    expect(locate(threeHunks, { side: "new", line: 1 })).toEqual(at(0, 0));
    expect(locate(threeHunks, { side: "old", line: 3 })).toEqual(at(0, 4)); // c
    expect(locate(threeHunks, { side: "new", line: 4 })).toEqual(at(0, 4)); // c, as hunk reports it
    expect(locate(threeHunks, { side: "old", line: 22 })).toEqual(at(2, 3)); // v
    expect(locate(threeHunks, { side: "new", line: 24 })).toEqual(at(2, 3));
  });

  test("null for a line no hunk carries: between hunks, past the end, on a header", () => {
    expect(locate(threeHunks, { side: "old", line: 5 })).toBeNull();
    expect(locate(threeHunks, { side: "new", line: 6 })).toBeNull();
    expect(locate(threeHunks, { side: "old", line: 23 })).toBeNull();
    expect(locate(threeHunks, { side: "new", line: 0 })).toBeNull();
    expect(locate(threeHunks, { side: "old", line: 99 })).toBeNull();
    expect(locate(parseFilePatch(HEADER), { side: "new", line: 1 })).toBeNull();
  });
});

describe("comparePos and orderRange", () => {
  test("orders by hunk, then by index, and takes a range either way round", () => {
    expect(comparePos(at(0, 5), at(1, 0))).toBeLessThan(0);
    expect(comparePos(at(1, 0), at(1, 1))).toBeLessThan(0);
    expect(comparePos(at(1, 1), at(1, 1))).toBe(0);
    expect(orderRange(at(2, 1), at(0, 3))).toEqual(span(at(0, 3), at(2, 1)));
    expect(orderRange(at(0, 3), at(2, 1))).toEqual(span(at(0, 3), at(2, 1)));
  });

  test("countSelected counts the changed lines in patch order across hunks", () => {
    expect(countSelected(threeHunks, span(at(0, 1), at(1, 2)))).toEqual({ added: 3, removed: 2 });
    expect(countSelected(threeHunks, span(at(0, 4), at(0, 5)))).toEqual({ added: 0, removed: 0 });
    expect(countSelected(threeHunks, span(at(0, 0), at(2, 3)))).toEqual({ added: 4, removed: 2 });
  });
});

describe("writeSelectedLines, forward (staging: the index holds the old side)", () => {
  test("keeps the selected `+`, drops the other `+`, demotes the `-` to context, and omits the untouched hunks", () => {
    expect(writeSelectedLines(threeHunks, span(at(0, 2), at(0, 2)), { reverse: false })).toBe(`${HEADER}@@ -1,4 +1,5 @@
 a
 b
+B
 c
 d
`);
  });

  test("renumbers a later hunk's new-side start by what the earlier hunks no longer add", () => {
    // Hunk 1 is left out entirely (it added one line), so hunk 2 lands one earlier.
    expect(writeSelectedLines(threeHunks, span(at(1, 2), at(1, 2)), { reverse: false })).toBe(`${HEADER}@@ -10,3 +10,4 @@
 j
 k
+K
 l
`);
    // Hunk 1 is left out, hunk 2 is taken whole, hunk 3 partially: the
    // shift is 1 from hunk 1 and 0 from hunk 2 (nothing of it was lost).
    expect(writeSelectedLines(threeHunks, span(at(1, 1), at(2, 2)), { reverse: false })).toBe(`${HEADER}@@ -10,3 +10,3 @@
 j
-k
+K
 l
@@ -20,3 +20,4 @@
 t
 u
+U2
 v
`);
    // Hunk 1 trimmed to one of its two additions: `-b` demotes to context
    // and `+B` is dropped, so the hunk still nets one added line — the
    // later starts do not move.
    expect(writeSelectedLines(threeHunks, span(at(0, 3), at(2, 2)), { reverse: false })).toBe(`${HEADER}@@ -1,4 +1,5 @@
 a
 b
+B2
 c
 d
@@ -10,3 +11,3 @@
 j
-k
+K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
`);
  });

  test("a trimmed hunk shifts the later starts by what its dropped lines would have added", () => {
    const patch = parseFilePatch(`${HEADER}@@ -1,5 +1,6 @@
 a
+A2
 b
-c
+C
 d
 e
@@ -10,2 +11,3 @@
 j
+J2
 k
`);
    // `+A2` is dropped: hunk 1 nets nothing now, and hunk 2 lands one earlier.
    expect(writeSelectedLines(patch, span(at(0, 3), at(1, 1)), { reverse: false })).toBe(`${HEADER}@@ -1,5 +1,5 @@
 a
 b
-c
+C
 d
 e
@@ -10,2 +10,3 @@
 j
+J2
 k
`);
    // In reverse `+A2` demotes to context and stays, so nothing shifts.
    expect(writeSelectedLines(patch, span(at(0, 3), at(1, 1)), { reverse: true })).toBe(`${HEADER}@@ -1,6 +1,6 @@
 a
 A2
 b
-c
+C
 d
 e
@@ -11,2 +11,3 @@
 j
+J2
 k
`);
  });

  test("a mode change in the header stays behind: the lines are staged, the mode is not", () => {
    const withMode = parseFilePatch(
      `diff --git a/f.txt b/f.txt\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\n--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n`,
    );
    expect(writeSelectedLines(withMode, span(at(0, 1), at(0, 2)), { reverse: false })).toBe(
      `diff --git a/f.txt b/f.txt\nindex 1111111..2222222\n--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n`,
    );
    expect(writeSelectedLines(withMode, span(at(0, 1), at(0, 2)), { reverse: true })).not.toContain("mode");
  });

  test("the whole patch selected is the patch, unchanged", () => {
    const whole = writeSelectedLines(threeHunks, span(at(0, 0), at(2, 3)), { reverse: false });
    expect(whole).not.toBeNull();
    expect(parseFilePatch(whole!)).toEqual(threeHunks);
  });

  test("a range over context only makes no patch", () => {
    expect(writeSelectedLines(threeHunks, span(at(0, 4), at(1, 0)), { reverse: false })).toBeNull();
    expect(writeSelectedLines(threeHunks, span(at(0, 4), at(1, 0)), { reverse: true })).toBeNull();
  });

  test("a hunk header count of one is written without the comma, as git does", () => {
    const oneLine = parseFilePatch(`${HEADER}@@ -1 +1,3 @@
 a
+b
+c
`);
    expect(writeSelectedLines(oneLine, span(at(0, 1), at(0, 1)), { reverse: false })).toBe(`${HEADER}@@ -1 +1,2 @@
 a
+b
`);
  });
});

describe("writeSelectedLines, reverse (unstaging or discarding: the target holds the new side)", () => {
  test("keeps the selected `+`, drops the other `-`, demotes the other `+` to context", () => {
    expect(writeSelectedLines(threeHunks, span(at(0, 2), at(0, 2)), { reverse: true })).toBe(`${HEADER}@@ -1,4 +1,5 @@
 a
+B
 B2
 c
 d
`);
    // Hunk 1 (net +1) is left in the target, so hunk 2's old side starts one later.
    expect(writeSelectedLines(threeHunks, span(at(1, 1), at(1, 1)), { reverse: true })).toBe(`${HEADER}@@ -11,4 +11,3 @@
 j
-k
 K
 l
`);
  });

  test("renumbers a later hunk's old-side start by what the earlier hunks no longer remove", () => {
    // Hunk 1 (net +1) stays in the target, so the result keeps that line and
    // hunk 2 sits one later on the old side than git wrote it.
    expect(writeSelectedLines(threeHunks, span(at(1, 2), at(1, 2)), { reverse: true })).toBe(`${HEADER}@@ -11,2 +11,3 @@
 j
+K
 l
`);
    expect(writeSelectedLines(threeHunks, span(at(2, 2), at(2, 2)), { reverse: true })).toBe(`${HEADER}@@ -21,3 +21,4 @@
 t
 u
+U2
 v
`);
  });
});

describe("the `\\ No newline at end of file` marker", () => {
  const eof = parseFilePatch(`${HEADER}@@ -1,2 +1,3 @@
 a
-b
\\ No newline at end of file
+b
+c
`);

  test("travels with its line when the line is kept", () => {
    expect(writeSelectedLines(eof, span(at(0, 1), at(0, 2)), { reverse: false })).toBe(`${HEADER}@@ -1,2 +1,2 @@
 a
-b
\\ No newline at end of file
+b
`);
    expect(writeSelectedLines(eof, span(at(0, 1), at(0, 1)), { reverse: false })).toBe(`${HEADER}@@ -1,2 +1 @@
 a
-b
\\ No newline at end of file
`);
  });

  test("goes with its line when the line is dropped", () => {
    expect(writeSelectedLines(eof, span(at(0, 3), at(0, 3)), { reverse: true })).toBe(`${HEADER}@@ -1,2 +1,3 @@
 a
 b
+c
`);
  });

  test("refuses a selection that would put the marker mid-file", () => {
    // Forward, `+c` alone: `-b` demotes to context and keeps its marker,
    // then `+c` follows it on the new side — a file with a line after its last.
    expect(() => writeSelectedLines(eof, span(at(0, 3), at(0, 3)), { reverse: false })).toThrow(RangeRefusal);
    expect(() => writeSelectedLines(eof, span(at(0, 3), at(0, 3)), { reverse: false })).toThrow(
      "select the whole end-of-file change",
    );
    // Reverse, `-b`/`+b` without `+c`: `+c` demotes to context after the marked `-b`.
    expect(() => writeSelectedLines(eof, span(at(0, 1), at(0, 2)), { reverse: true })).toThrow(RangeRefusal);
  });

  test("stays on a context line that carries it", () => {
    const tail = parseFilePatch(`${HEADER}@@ -1,3 +1,3 @@
 a
-x
+y
 z
\\ No newline at end of file
`);
    expect(writeSelectedLines(tail, span(at(0, 2), at(0, 2)), { reverse: false })).toBe(`${HEADER}@@ -1,3 +1,4 @@
 a
 x
+y
 z
\\ No newline at end of file
`);
  });
});

describe("line text is carried verbatim", () => {
  test("a CRLF file's `\\r` survives the round trip on kept, demoted and context lines", () => {
    const crlf = parseFilePatch(`${HEADER}@@ -1,3 +1,3 @@
 a\r
-b\r
+B\r
 c\r
`);
    expect(writeSelectedLines(crlf, span(at(0, 2), at(0, 2)), { reverse: false })).toBe(`${HEADER}@@ -1,3 +1,4 @@
 a\r
 b\r
+B\r
 c\r
`);
  });
});

describe("sameHunks", () => {
  const fresh = parseFilePatch(`${HEADER}@@ -1,4 +1,5 @@
 a
-b
+B
+B2
 c
 d
@@ -10,3 +11,3 @@
 j
-k
+K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
`);

  test("null for the same hunks, whatever the headers say", () => {
    expect(sameHunks(threeHunks, fresh)).toBeNull();
    const renumbered = parseFilePatch(fresh.headerLines.join("\n") + "\n@@ -1,4 +1,5 @@\n a\n-b\n+B\n+B2\n c\n d\n@@ -10,3 +11,3 @@\n j\n-k\n+K\n l\n@@ -30,3 +31,4 @@\n t\n u\n+U2\n v\n");
    expect(sameHunks(threeHunks, renumbered)).toBeNull();
  });

  test("names the first hunk and line that differ", () => {
    const edited = parseFilePatch(`${HEADER}@@ -1,4 +1,5 @@
 a
-b
+B
+B3
 c
 d
@@ -10,3 +11,3 @@
 j
-k
+K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
`);
    expect(sameHunks(threeHunks, edited)).toBe("hunk 1 differs: line 4 is `B2` in the review but `B3` on disk");
    const longer = parseFilePatch(`${HEADER}@@ -1,4 +1,6 @@
 a
-b
+B
+B2
+B3
 c
 d
@@ -10,3 +12,3 @@
 j
-k
+K
 l
@@ -20,3 +22,4 @@
 t
 u
+U2
 v
`);
    expect(sameHunks(threeHunks, longer)).toBe("hunk 1 has 6 lines in the review but 7 on disk");
    const fewer = parseFilePatch(`${HEADER}@@ -1,4 +1,5 @@
 a
-b
+B
+B2
 c
 d
`);
    expect(sameHunks(threeHunks, fewer)).toBe("the review shows 3 hunk(s) but the disk has 1");
    const kinds = parseFilePatch(`${HEADER}@@ -1,4 +1,5 @@
 a
-b
+B
+B2
 c
 d
@@ -10,3 +11,3 @@
 j
+k
-K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
`);
    expect(sameHunks(threeHunks, kinds)).toBe("hunk 2 differs: line 2 is a removal in the review but an addition on disk");
  });
});
