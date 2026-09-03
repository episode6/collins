// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import {
  BOTH_PANES,
  descendAfterPick,
  fit,
  isNarrow,
  LEVELS,
  levelDown,
  levelOf,
  levelUp,
  NO_CHANGE,
  ONE_PANE_COLUMNS,
  panesFor,
  planPanes,
  planResize,
  stepDown,
  stepUp,
  TOO_NARROW,
  TWO_PANE_COLUMNS,
  type PaneState,
} from "../level.ts";

const NONE: PaneState = { commits: false, files: false };
const FILES: PaneState = { commits: false, files: true };
const COMMITS: PaneState = { commits: true, files: false };

describe("the width regimes", () => {
  test("the thresholds are hunk 0.20.1's budget", () => {
    // 2 padding + 22 sidebar minimum + 1 divider + 48 diff minimum
    expect(ONE_PANE_COLUMNS).toBe(73);
    // 2 padding + 26 commits + 1 + 22 files minimum + 1 + 48 diff minimum
    expect(TWO_PANE_COLUMNS).toBe(100);
  });

  test("fit by columns", () => {
    expect(fit(62)).toBe("none");
    expect(fit(72)).toBe("none");
    expect(fit(73)).toBe("one");
    expect(fit(99)).toBe("one");
    expect(fit(100)).toBe("two");
    expect(fit(138)).toBe("two");
    expect(fit(0)).toBe("none");
    expect(fit(Number.NaN)).toBe("none");
  });

  test("narrow is anything short of both panes", () => {
    expect(isNarrow(62)).toBe(true);
    expect(isNarrow(75)).toBe(true);
    expect(isNarrow(99)).toBe(true);
    expect(isNarrow(100)).toBe(false);
  });
});

describe("the level stack", () => {
  test("bottom to top: diff, files, commits", () => {
    expect(LEVELS).toEqual(["diff", "files", "commits"]);
    expect(levelUp("diff")).toBe("files");
    expect(levelUp("files")).toBe("commits");
    expect(levelUp("commits")).toBeNull();
    expect(levelDown("commits")).toBe("files");
    expect(levelDown("files")).toBe("diff");
    expect(levelDown("diff")).toBeNull();
  });

  test("the level of a pane state: the commits pane wins", () => {
    expect(levelOf(NONE)).toBe("diff");
    expect(levelOf(FILES)).toBe("files");
    expect(levelOf(COMMITS)).toBe("commits");
    expect(levelOf(BOTH_PANES)).toBe("commits"); // hunk omits the files pane beside it
  });

  test("a level opens exactly one pane, or none", () => {
    expect(panesFor("diff")).toEqual(NONE);
    expect(panesFor("files")).toEqual(FILES);
    expect(panesFor("commits")).toEqual(COMMITS);
  });

  test("a plan closes before it opens and names only what changes", () => {
    expect(planPanes(NONE, FILES)).toEqual({ close: [], open: ["files"] });
    expect(planPanes(FILES, COMMITS)).toEqual({ close: ["files"], open: ["commits"] });
    expect(planPanes(BOTH_PANES, FILES)).toEqual({ close: ["commits"], open: [] });
    expect(planPanes(COMMITS, COMMITS)).toEqual(NO_CHANGE);
    expect(planPanes(NONE, BOTH_PANES)).toEqual({ close: [], open: ["commits", "files"] });
  });
});

describe("stepping with one pane fitting", () => {
  test("up walks diff → files → commits and stops", () => {
    expect(stepUp(75, NONE)).toEqual({ kind: "show", level: "files", plan: { close: [], open: ["files"] } });
    expect(stepUp(75, FILES)).toEqual({
      kind: "show",
      level: "commits",
      plan: { close: ["files"], open: ["commits"] },
    });
    expect(stepUp(75, COMMITS)).toEqual({ kind: "refuse", reason: "at the top — the commits are shown" });
    // Both open shows the commits: already at the top.
    expect(stepUp(75, BOTH_PANES).kind).toBe("refuse");
  });

  test("down walks commits → files → diff and stops", () => {
    expect(stepDown(99, COMMITS)).toEqual({
      kind: "show",
      level: "files",
      plan: { close: ["commits"], open: ["files"] },
    });
    expect(stepDown(99, FILES)).toEqual({ kind: "show", level: "diff", plan: { close: ["files"], open: [] } });
    expect(stepDown(99, NONE)).toEqual({ kind: "refuse", reason: "at the bottom — the diff is shown" });
    // Both open, down: the files pane stays, the commits pane goes.
    expect(stepDown(99, BOTH_PANES)).toEqual({
      kind: "show",
      level: "files",
      plan: { close: ["commits"], open: [] },
    });
  });
});

describe("stepping in the other regimes", () => {
  test("too narrow for any pane: both refuse with the reason", () => {
    expect(stepUp(62, NONE)).toEqual({ kind: "refuse", reason: TOO_NARROW });
    expect(stepDown(62, BOTH_PANES)).toEqual({ kind: "refuse", reason: TOO_NARROW });
    expect(TOO_NARROW).toBe("too narrow for a panel — widen the page");
  });

  test("wide: up shows both panes, down does nothing", () => {
    expect(stepUp(120, NONE)).toEqual({ kind: "show", level: "all", plan: { close: [], open: ["commits", "files"] } });
    expect(stepUp(120, COMMITS)).toEqual({ kind: "show", level: "all", plan: { close: [], open: ["files"] } });
    expect(stepUp(120, BOTH_PANES)).toEqual({ kind: "refuse", reason: "both panels are shown" });
    expect(stepDown(120, BOTH_PANES)).toEqual({ kind: "noop" });
    expect(stepDown(120, NONE)).toEqual({ kind: "noop" });
  });
});

describe("drilling down with the mouse", () => {
  test("a commit picked shows the files; a file picked shows the diff", () => {
    expect(descendAfterPick(75, COMMITS, "commits")).toBe("files");
    expect(descendAfterPick(75, BOTH_PANES, "commits")).toBe("files");
    expect(descendAfterPick(75, FILES, "files")).toBe("diff");
  });

  test("nothing to do when already there, or outside the one-pane regime", () => {
    expect(descendAfterPick(75, FILES, "commits")).toBeNull();
    expect(descendAfterPick(75, NONE, "files")).toBeNull();
    expect(descendAfterPick(120, BOTH_PANES, "commits")).toBeNull();
    expect(descendAfterPick(62, COMMITS, "commits")).toBeNull();
  });
});

describe("resizes", () => {
  test("growing into the wide regime opens both panes", () => {
    expect(planResize(75, 100, NONE)).toEqual({ close: [], open: ["commits", "files"] });
    expect(planResize(62, 138, FILES)).toEqual({ close: [], open: ["commits"] });
    expect(planResize(75, 138, BOTH_PANES)).toEqual(NO_CHANGE);
  });

  test("shrinking out of it closes both: the diff is the bottom of the stack", () => {
    expect(planResize(138, 80, BOTH_PANES)).toEqual({ close: ["commits", "files"], open: [] });
    expect(planResize(138, 62, COMMITS)).toEqual({ close: ["commits"], open: [] });
  });

  test("a change inside a regime leaves the panes as they are", () => {
    expect(planResize(75, 90, FILES)).toEqual(NO_CHANGE);
    expect(planResize(62, 72, NONE)).toEqual(NO_CHANGE);
    expect(planResize(62, 80, COMMITS)).toEqual(NO_CHANGE);
    expect(planResize(100, 200, NONE)).toEqual(NO_CHANGE);
    expect(planResize(80, 80, FILES)).toEqual(NO_CHANGE);
  });
});
