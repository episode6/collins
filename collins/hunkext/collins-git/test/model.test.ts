// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import { decodeTitle } from "../model.ts";

describe("decodeTitle", () => {
  test("names the two working-tree sides, with the repo name stripped; everything else is read-only", () => {
    expect(decodeTitle("collins working tree", "collins")).toEqual({ kind: "unstaged" });
    expect(decodeTitle("collins staged changes", "collins")).toEqual({ kind: "staged" });
    expect(decodeTitle("collins show abc1234", "collins")).toEqual({ kind: "other" });
    expect(decodeTitle("collins main...HEAD", "collins")).toEqual({ kind: "other" });
    expect(decodeTitle("collins a1..b2", "collins")).toEqual({ kind: "other" });
    expect(decodeTitle("collins something else", "collins")).toEqual({ kind: "other" });
    expect(decodeTitle("", "collins")).toEqual({ kind: "other" });
  });

  test("copes with a repo name holding spaces, or an unknown one", () => {
    expect(decodeTitle("my repo working tree", "my repo")).toEqual({ kind: "unstaged" });
    expect(decodeTitle("my repo staged changes", "")).toEqual({ kind: "staged" });
    expect(decodeTitle("my repo show HEAD", "my repo")).toEqual({ kind: "other" });
    // A repo named after a title word does not fool it: the tail is what is matched.
    expect(decodeTitle("working tree working tree", "working tree")).toEqual({ kind: "unstaged" });
  });
});
