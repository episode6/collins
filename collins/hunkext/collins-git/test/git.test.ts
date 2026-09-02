// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import { parseLog, parseRefs, parseStatusV2, safeRef } from "../git.ts";

const SHA_A = "bdda3818b622d8af5190c55f25c15356d76c7806";
const SHA_B = "8a681ae56e3d0a1015c5fcce494db95597a5326b";

describe("parseLog", () => {
  test("reads NUL-separated fields out of RS-terminated records", () => {
    const text = `${SHA_A}\x00bdda381\x00ours\x00HEAD -> main, origin/main\x1e\n${SHA_B}\x008a681ae\x00first\x00base\x1e\n`;
    expect(parseLog(text)).toEqual([
      { sha: SHA_A, abbrev: "bdda381", subject: "ours", refs: ["HEAD -> main", "origin/main"] },
      { sha: SHA_B, abbrev: "8a681ae", subject: "first", refs: ["base"] },
    ]);
  });

  test("tolerates empty output, empty refs and a subject with a NUL-free comma", () => {
    expect(parseLog("")).toEqual([]);
    expect(parseLog(`${SHA_A}\x00bdda381\x00fix a, b\x00\x1e`)).toEqual([
      { sha: SHA_A, abbrev: "bdda381", subject: "fix a, b", refs: [] },
    ]);
  });

  test("skips a record that does not start with a sha", () => {
    expect(parseLog(`garbage\x00x\x00y\x00\x1e${SHA_B}\x008a681ae\x00first\x00\x1e`)).toHaveLength(1);
  });
});

describe("parseStatusV2", () => {
  const H = "2cdcdb0cb0170be576e43fd27c48d1f64f800df7";
  const Z = "0000000000000000000000000000000000000000";
  const text = [
    `1 MM N... 100644 100644 100644 ${H} ${H} a.txt`,
    `1 A. N... 000000 100644 100644 ${Z} ${H} added.txt`,
    `1 .M N... 100644 100644 100644 ${H} ${H} bin.dat`,
    `1 D. N... 100644 000000 000000 ${H} ${Z} gone.txt`,
    `2 R. N... 100644 100644 100644 ${H} ${H} R100 new.txt`,
    "old.txt",
    `2 RM N... 100644 100644 100644 ${H} ${H} R087 moved.txt`,
    "orig.txt",
    `u UU N... 100644 100644 100644 100644 ${H} ${H} ${H} merge.txt`,
    "? untracked.txt",
    "! ignored.txt",
    "",
  ].join("\x00");

  test("splits every entry kind between the index and the working tree", () => {
    const status = parseStatusV2(text);
    expect(status.staged).toEqual([
      { path: "a.txt", code: "M" },
      { path: "added.txt", code: "A" },
      { path: "gone.txt", code: "D" },
      { path: "new.txt", previousPath: "old.txt", code: "R" },
      { path: "moved.txt", previousPath: "orig.txt", code: "R" },
    ]);
    expect(status.unstaged).toEqual([
      { path: "a.txt", code: "M" },
      { path: "bin.dat", code: "M" },
      { path: "moved.txt", code: "M" },
      { path: "merge.txt", code: "U" },
      { path: "untracked.txt", code: "?" },
    ]);
  });

  test("keeps spaces in paths and survives an empty status", () => {
    expect(parseStatusV2("")).toEqual({ unstaged: [], staged: [] });
    const spaced = parseStatusV2(`1 .M N... 100644 100644 100644 ${H} ${H} dir name/a b.txt\x00? new file.txt\x00`);
    expect(spaced.unstaged).toEqual([
      { path: "dir name/a b.txt", code: "M" },
      { path: "new file.txt", code: "?" },
    ]);
  });
});

describe("parseRefs", () => {
  test("one name per line, blanks dropped", () => {
    expect(parseRefs("base\nmain\n\nfeat/x\n")).toEqual(["base", "main", "feat/x"]);
  });
});

describe("safeRef", () => {
  test("accepts ordinary branch names", () => {
    expect(safeRef("main")).toBe("main");
    expect(safeRef(" feat/diff-panel ")).toBe("feat/diff-panel");
    expect(safeRef("release/v0.1")).toBe("release/v0.1");
  });

  test("refuses anything git could misread", () => {
    for (const bad of ["", "  ", "-x", "--all", "a b", "a..b", "a\nb", "a:b", "a^", "x/", "x.lock", 42, null]) {
      expect(safeRef(bad)).toBeNull();
    }
  });
});
