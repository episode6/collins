// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import { EMPTY_TREE, type Commit } from "../git.ts";
import {
  buildRows,
  decodeTail,
  decodeTitle,
  fileCode,
  filesSections,
  loadedRow,
  neighbour,
  sideTail,
  withoutUntracked,
  type RowsInput,
} from "../model.ts";

function commit(n: number, subject = `commit ${n}`): Commit {
  const sha = n.toString(16).padStart(2, "0").repeat(20);
  return { sha, abbrev: sha.slice(0, 7), subject, refs: [] };
}

describe("decodeTitle", () => {
  test("recognises all five kinds, with the repo name stripped", () => {
    expect(decodeTitle("collins working tree", "collins")).toEqual({ kind: "unstaged" });
    expect(decodeTitle("collins staged changes", "collins")).toEqual({ kind: "staged" });
    expect(decodeTitle("collins show abc1234", "collins")).toEqual({ kind: "show", ref: "abc1234" });
    expect(decodeTitle("collins main...HEAD", "collins")).toEqual({ kind: "range", range: "main...HEAD" });
    expect(decodeTitle("collins a1..b2", "collins")).toEqual({ kind: "range", range: "a1..b2" });
    expect(decodeTitle("collins something else", "collins")).toEqual({ kind: "foreign", tail: "something else" });
  });

  test("copes with a repo name holding spaces, or an unknown one", () => {
    expect(decodeTitle("my repo working tree", "my repo")).toEqual({ kind: "unstaged" });
    expect(decodeTitle("my repo show HEAD", "my repo")).toEqual({ kind: "show", ref: "HEAD" });
    expect(decodeTitle("my repo staged changes", "")).toEqual({ kind: "staged" });
    expect(decodeTitle("other origin/main...HEAD", "")).toEqual({ kind: "range", range: "origin/main...HEAD" });
  });
});

describe("decodeTail", () => {
  test("inverts a row's load", () => {
    expect(decodeTail(["diff"])).toEqual({ kind: "unstaged" });
    expect(decodeTail(["diff", "--staged"])).toEqual({ kind: "staged" });
    expect(decodeTail(["show", "abc"])).toEqual({ kind: "show", ref: "abc" });
    expect(decodeTail(["diff", "main...HEAD"])).toEqual({ kind: "range", range: "main...HEAD" });
    expect(decodeTail(["diff", "HEAD"])).toEqual({ kind: "foreign", tail: "diff HEAD" });
    expect(sideTail("staged")).toEqual(["diff", "--staged"]);
    expect(sideTail("unstaged")).toEqual(["diff"]);
  });
});

const base: RowsInput = {
  branch: "feat/panel",
  parent: { name: "develop", target: "origin/develop" },
  defaultBranch: { name: "main", target: "main" },
  current: [commit(3), commit(2)],
  currentMore: false,
  parentCommits: [commit(5)],
  parentMore: false,
  defaultCommits: [commit(9), commit(8)],
  defaultMore: true,
  unpushed: new Set([commit(3).sha]),
  defaultOldestParent: commit(7).sha,
};

describe("buildRows", () => {
  test("lists current, parent and default groups in order with the spec's loads", () => {
    const rows = buildRows(base);
    expect(rows.map((row) => `${row.group}/${row.kind}:${row.label}`)).toEqual([
      "current/header:FEAT/PANEL",
      "current/worktree:working tree",
      "current/commit:commit 3",
      "current/commit:commit 2",
      "parent/header:DEVELOP",
      "parent/commit:commit 5",
      "default/header:MAIN",
      "default/commit:commit 9",
      "default/commit:commit 8",
      "default/more:load more…",
    ]);
    expect(rows[0]!.load).toEqual(["diff", "origin/develop...HEAD"]);
    expect(rows[1]!.load).toEqual(["diff"]);
    expect(rows[2]!.load).toEqual(["show", commit(3).sha]);
    expect(rows[2]!.unpushed).toBe(true);
    expect(rows[3]!.unpushed).toBe(false);
    expect(rows[4]!.load).toEqual(["diff", "main...origin/develop"]);
    expect(rows[6]!.load).toEqual(["diff", `${commit(7).sha}..${commit(9).sha}`]);
    expect(rows[9]!.load).toEqual([]);
  });

  test("omits the parent group when the parent is the default branch", () => {
    const rows = buildRows({ ...base, parent: { name: "main", target: "main" } });
    expect(rows.some((row) => row.group === "parent")).toBe(false);
    expect(rows[0]!.load).toEqual(["diff", "main...HEAD"]);
  });

  test("with no parent at all, the header loads what the group lists", () => {
    const rows = buildRows({ ...base, parent: null, defaultBranch: null, defaultCommits: [] });
    expect(rows.map((row) => row.group)).toEqual(["current", "current", "current", "current"]);
    expect(rows[0]!.load).toEqual(["diff", `${commit(2).sha}^..HEAD`]);
  });

  test("a root commit at the bottom of the default page diffs from the empty tree", () => {
    const rows = buildRows({ ...base, defaultOldestParent: null, defaultMore: false });
    const header = rows.find((row) => row.group === "default" && row.kind === "header")!;
    expect(header.load).toEqual(["diff", `${EMPTY_TREE}..${commit(9).sha}`]);
    expect(rows.some((row) => row.kind === "more")).toBe(false);
  });

  test("load more rows appear per group when a page was full", () => {
    const rows = buildRows({ ...base, currentMore: true, parentMore: true });
    expect(rows.filter((row) => row.kind === "more").map((row) => row.group)).toEqual([
      "current",
      "parent",
      "default",
    ]);
  });
});

describe("loadedRow", () => {
  const rows = buildRows(base);

  test("the working tree row stands for both working-tree loads", () => {
    expect(loadedRow(rows, { kind: "unstaged" })?.id).toBe("worktree");
    expect(loadedRow(rows, { kind: "staged" })?.id).toBe("worktree");
  });

  test("a show matches by sha prefix, then by the resolved ref", () => {
    expect(loadedRow(rows, { kind: "show", ref: commit(9).sha.slice(0, 8) })?.label).toBe("commit 9");
    expect(loadedRow(rows, { kind: "show", ref: "HEAD" })).toBeNull();
    expect(loadedRow(rows, { kind: "show", ref: "HEAD" }, commit(3).sha)?.label).toBe("commit 3");
  });

  test("a range matches the header that loads it; anything else no row", () => {
    expect(loadedRow(rows, { kind: "range", range: "main...origin/develop" })?.id).toBe("header:parent");
    expect(loadedRow(rows, { kind: "range", range: "x..y" })).toBeNull();
    expect(loadedRow(rows, { kind: "foreign", tail: "?" })).toBeNull();
  });
});

describe("neighbour", () => {
  const rows = buildRows({ ...base, defaultMore: true });

  test("from the working tree, n lands on the newest commit and p on nothing", () => {
    expect(neighbour(rows, { kind: "unstaged" }, 1)?.label).toBe("commit 3");
    expect(neighbour(rows, { kind: "unstaged" }, -1)).toBeNull();
  });

  test("stays inside the group and skips load more", () => {
    const show = (n: number) => ({ kind: "show" as const, ref: commit(n).sha });
    expect(neighbour(rows, show(2), 1)).toBeNull();
    expect(neighbour(rows, show(2), -1)?.label).toBe("commit 3");
    expect(neighbour(rows, show(3), -1)?.kind).toBe("worktree");
    expect(neighbour(rows, show(9), 1)?.label).toBe("commit 8");
    expect(neighbour(rows, show(8), 1)).toBeNull();
    expect(neighbour(rows, show(9), -1)).toBeNull();
  });

  test("from a header, one step down is the group's first row", () => {
    expect(neighbour(rows, { kind: "range", range: "main...origin/develop" }, 1)?.label).toBe("commit 5");
    expect(neighbour(rows, { kind: "range", range: "main...origin/develop" }, -1)).toBeNull();
    expect(neighbour(rows, { kind: "foreign", tail: "x" }, 1)).toBeNull();
  });
});

describe("filesSections", () => {
  const files = [
    { id: "f0", path: "a.txt", stats: { additions: 2, deletions: 1 }, changeType: "change" as const },
    { id: "f1", path: "n.txt", stats: { additions: 3, deletions: 0 }, changeType: "new" as const, isUntracked: true },
    {
      id: "f2",
      path: "b.txt",
      previousPath: "a0.txt",
      stats: { additions: 0, deletions: 0 },
      changeType: "rename-pure" as const,
    },
    { id: "f3", path: "img.png", stats: { additions: 0, deletions: 0 }, isBinary: true },
  ];
  const status = {
    unstaged: [{ path: "a.txt", code: "M" as const }],
    staged: [
      { path: "s.txt", code: "A" as const },
      { path: "b.txt", previousPath: "a0.txt", code: "R" as const },
    ],
  };

  test("codes follow hunk's change types", () => {
    expect(files.map(fileCode)).toEqual(["M", "?", "R", "M"]);
    expect(fileCode({ id: "x", path: "d", stats: { additions: 0, deletions: 1 }, changeType: "deleted" })).toBe("D");
    expect(fileCode({ id: "x", path: "n", stats: { additions: 1, deletions: 0 }, changeType: "new" })).toBe("A");
  });

  test("splits when the working tree is loaded, live side from hunk's files", () => {
    const sections = filesSections(status, files, { kind: "unstaged" });
    expect(sections.mode).toBe("split");
    if (sections.mode !== "split") {
      return;
    }
    expect(sections.live).toBe("unstaged");
    expect(sections.unstaged.map((row) => [row.id, row.code, row.additions])).toEqual([
      ["f0", "M", 2],
      ["f1", "?", 3],
      ["f2", "R", 0],
      ["f3", "M", 0],
    ]);
    expect(sections.unstaged[2]!.previousPath).toBe("a0.txt");
    expect(sections.unstaged[3]!.binary).toBe(true);
    expect(sections.staged).toEqual([
      { id: null, path: "s.txt", code: "A", binary: false },
      { id: null, path: "b.txt", previousPath: "a0.txt", code: "R", binary: false },
    ]);
  });

  test("the staged view puts hunk's files on the staged side", () => {
    const sections = filesSections(status, files.slice(0, 1), { kind: "staged" });
    expect(sections.mode === "split" && sections.live).toBe("staged");
    expect(sections.mode === "split" && sections.staged[0]!.id).toBe("f0");
    expect(sections.mode === "split" && sections.unstaged[0]!.id).toBeNull();
  });

  test("with untracked files off, the staged view's unstaged side lists none", () => {
    // What index.ts feeds filesSections when the sidecar says untracked:
    // false — the live side comes from a `diff --exclude-untracked`, and
    // the other side's `?` rows would be files a click could never load.
    const withNew = {
      unstaged: [
        { path: "a.txt", code: "M" as const },
        { path: "new.txt", code: "?" as const },
        { path: "u.txt", code: "U" as const },
      ],
      staged: status.staged,
    };
    const filtered = withoutUntracked(withNew);
    expect(filtered?.unstaged.map((row) => row.code)).toEqual(["M", "U"]);
    expect(filtered?.staged).toBe(status.staged);
    expect(withoutUntracked(null)).toBeNull();
    const sections = filesSections(filtered, files.slice(0, 1), { kind: "staged" });
    expect(sections.mode === "split" && sections.unstaged.map((row) => row.path)).toEqual(["a.txt", "u.txt"]);
    // With the switch on nothing is dropped.
    const shown = filesSections(withNew, files.slice(0, 1), { kind: "staged" });
    expect(shown.mode === "split" && shown.unstaged.map((row) => row.code)).toEqual(["M", "?", "U"]);
  });

  test("everything else is flat, and so is a working tree with no status", () => {
    const shown = filesSections(status, files, { kind: "show", ref: "abc" });
    expect(shown.mode).toBe("flat");
    expect(shown.mode === "flat" && shown.rows.length).toBe(4);
    expect(filesSections(null, files, { kind: "unstaged" }).mode).toBe("flat");
    expect(filesSections(status, [], { kind: "range", range: "a..b" })).toEqual({ mode: "flat", rows: [] });
  });
});
