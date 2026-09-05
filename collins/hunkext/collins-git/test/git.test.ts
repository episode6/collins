// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import {
  applyCached,
  applyWorktreeReverse,
  readFilePatch,
  restoreFile,
  safeRef,
  stageFiles,
  unstageFiles,
  type GitResult,
  type GitRunner,
} from "../git.ts";

/** A runner that records every call and answers from a table keyed by the git subcommand (the first argument the table knows). */
function fakeRunner(answers: Record<string, GitResult | ((args: readonly string[]) => GitResult)>) {
  const calls: { args: readonly string[]; stdin?: string; options?: { timeoutMs?: number } }[] = [];
  const git: GitRunner = (args, stdin, options) => {
    calls.push({ args, stdin, options });
    const answer = answers[args.find((arg) => arg in answers) ?? ""];
    const result = typeof answer === "function" ? answer(args) : answer;
    return result ?? { ok: false, stdout: "", stderr: `no answer for ${args.join(" ")}` };
  };
  return { git, calls };
}

const ok = (stdout = ""): GitResult => ({ ok: true, stdout, stderr: "" });

describe("the staging keys' git calls", () => {
  test("applyWorktreeReverse never touches the index; every apply is -p1 --unidiff-zero", () => {
    const { git, calls } = fakeRunner({ apply: ok() });
    applyWorktreeReverse(git, "patch text");
    expect(calls[0]).toEqual({ args: ["apply", "-p1", "--unidiff-zero", "--reverse", "-"], stdin: "patch text", options: undefined });
    applyCached(git, "patch text", false);
    expect(calls[1]?.args).toEqual(["apply", "--cached", "-p1", "--unidiff-zero", "-"]);
    applyCached(git, "patch text", true);
    expect(calls[2]?.args).toEqual(["apply", "--cached", "-p1", "--unidiff-zero", "--reverse", "-"]);
  });

  test("restoreFile checks the path out of the index, behind a `--`", () => {
    const { git, calls } = fakeRunner({ checkout: ok() });
    restoreFile(git, "-looks-like-an-option.txt");
    expect(calls[0]?.args).toEqual(["checkout", "-q", "--", "-looks-like-an-option.txt"]);
  });

  test("stageFiles and unstageFiles take every path behind a `--`", () => {
    const { git, calls } = fakeRunner({ add: ok(), reset: ok() });
    stageFiles(git, ["a.txt", "-b.txt"]);
    unstageFiles(git, ["a.txt"]);
    expect(calls.map((call) => call.args)).toEqual([
      ["add", "-A", "--", "a.txt", "-b.txt"],
      ["reset", "-q", "--", "a.txt"],
    ]);
  });

  test("readFilePatch normalises the prefixes, asks --cached for the index, and names both halves of a rename", () => {
    const { git, calls } = fakeRunner({ diff: ok("patch") });
    expect(readFilePatch(git, "f.txt", false).stdout).toBe("patch");
    expect(calls[0]?.args.slice(10)).toEqual(["diff", "--no-color", "--no-ext-diff", "--find-renames", "--", "f.txt"]);
    expect(calls[0]?.args.slice(0, 10)).toEqual([
      "-c",
      "core.quotePath=true",
      "-c",
      "diff.noprefix=false",
      "-c",
      "diff.mnemonicPrefix=false",
      "-c",
      "diff.srcPrefix=a/",
      "-c",
      "diff.dstPrefix=b/",
    ]);
    readFilePatch(git, "f.txt", true);
    expect(calls[1]?.args.slice(10)).toEqual(["diff", "--cached", "--no-color", "--no-ext-diff", "--find-renames", "--", "f.txt"]);
    readFilePatch(git, "new.txt", false, "old.txt");
    expect(calls[2]?.args.slice(-3)).toEqual(["--", "old.txt", "new.txt"]);
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
