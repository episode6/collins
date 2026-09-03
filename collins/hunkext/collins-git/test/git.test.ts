// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { describe, expect, test } from "bun:test";
import {
  applyCached,
  applyWorktreeReverse,
  COMMIT_TIMEOUT_MS,
  commit,
  commitFixup,
  parseLog,
  parseRefs,
  parseStatusV2,
  restoreFile,
  safeRef,
  stagedPaths,
  unpushedCommits,
  unpushedShas,
  type GitResult,
  type GitRunner,
} from "../git.ts";

const SHA_A = "bdda3818b622d8af5190c55f25c15356d76c7806";
const SHA_B = "8a681ae56e3d0a1015c5fcce494db95597a5326b";

/** A runner that records every call and answers from a table keyed by the first argument. */
function fakeRunner(answers: Record<string, GitResult | ((args: readonly string[]) => GitResult)>) {
  const calls: { args: readonly string[]; stdin?: string; options?: { timeoutMs?: number } }[] = [];
  const git: GitRunner = (args, stdin, options) => {
    calls.push({ args, stdin, options });
    const answer = answers[args[0] ?? ""] ?? answers[args.find((arg) => !arg.startsWith("-")) ?? ""];
    const result = typeof answer === "function" ? answer(args) : answer;
    return result ?? { ok: false, stdout: "", stderr: `no answer for ${args.join(" ")}` };
  };
  return { git, calls };
}

const ok = (stdout = ""): GitResult => ({ ok: true, stdout, stderr: "" });
const failed = (stderr: string): GitResult => ({ ok: false, stdout: "", stderr });

describe("the commit keys' git calls", () => {
  test("commit passes -q and -m per part, never opens an editor, and asks for the long timeout", () => {
    const { git, calls } = fakeRunner({ commit: ok() });
    expect(commit(git, "A summary").ok).toBe(true);
    expect(calls[0]).toEqual({ args: ["commit", "-q", "-m", "A summary"], stdin: undefined, options: { timeoutMs: COMMIT_TIMEOUT_MS } });
    expect(COMMIT_TIMEOUT_MS).toBeGreaterThanOrEqual(600_000); // hooks run a test suite; the runner is asynchronous
    commit(git, "A summary", "A body\nof two lines.");
    expect(calls[1]?.args).toEqual(["commit", "-q", "-m", "A summary", "-m", "A body\nof two lines."]);
    commit(git, "A summary", "");
    expect(calls[2]?.args).toEqual(["commit", "-q", "-m", "A summary"]);
    // A summary that starts with a dash is still the value of -m.
    commit(git, "-x looks like a flag");
    expect(calls[3]?.args).toEqual(["commit", "-q", "-m", "-x looks like a flag"]);
  });

  test("commitFixup writes the full sha into the subject", () => {
    const { git, calls } = fakeRunner({ commit: ok() });
    commitFixup(git, SHA_A);
    expect(calls[0]).toEqual({ args: ["commit", "-q", "-m", `fixup! ${SHA_A}`], stdin: undefined, options: { timeoutMs: COMMIT_TIMEOUT_MS } });
  });

  test("commit and commitFixup take an asynchronous runner and hand its promise back", async () => {
    const seen: (readonly string[])[] = [];
    const git = async (args: readonly string[]): Promise<GitResult> => {
      seen.push(args);
      return ok();
    };
    await expect(commit(git, "Async summary", "body")).resolves.toEqual(ok());
    await expect(commitFixup(git, SHA_A)).resolves.toEqual(ok());
    expect(seen).toEqual([
      ["commit", "-q", "-m", "Async summary", "-m", "body"],
      ["commit", "-q", "-m", `fixup! ${SHA_A}`],
    ]);
  });

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

  test("stagedPaths splits on NUL and is empty when git fails", () => {
    expect(stagedPaths(fakeRunner({ diff: ok("a.txt\0dir/b c.txt\0") }).git)).toEqual(["a.txt", "dir/b c.txt"]);
    expect(stagedPaths(fakeRunner({ diff: ok("") }).git)).toEqual([]);
    expect(stagedPaths(fakeRunner({ diff: failed("not a git repository") }).git)).toEqual([]);
  });

  test("unpushedCommits lists the group's commits that are on no remote-tracking ref, never @{upstream}", () => {
    const record = `${SHA_A}\x00bdda381\x00ours\x00\x1e\n`;
    const { git, calls } = fakeRunner({ log: ok(record) });
    expect(unpushedCommits(git, ["main..HEAD"], 20)).toEqual([{ sha: SHA_A, abbrev: "bdda381", subject: "ours", refs: [] }]);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.args).toEqual([
      "log",
      "--no-decorate",
      "--format=%H%x00%h%x00%s%x00%D%x1e",
      "-n",
      "20",
      "main..HEAD",
      "--not",
      "--remotes",
      "--",
    ]);
    expect(unpushedCommits(fakeRunner({ log: failed("bad revision") }).git, ["HEAD"], 5)).toEqual([]);
  });

  test("unpushedShas asks the same question of every commit on HEAD", () => {
    const tracked = { "for-each-ref": ok("refs/remotes/origin/main\n") };
    const { git, calls } = fakeRunner({ ...tracked, "rev-list": ok(`${SHA_A}\n${SHA_B}\n`) });
    expect(unpushedShas(git)).toEqual(new Set([SHA_A, SHA_B]));
    expect(calls[0]?.args).toEqual(["for-each-ref", "--count=1", "--format=%(refname)", "refs/remotes/"]);
    expect(calls[1]?.args).toEqual(["rev-list", "HEAD", "--not", "--remotes", "--"]);
    expect(unpushedShas(fakeRunner({ ...tracked, "rev-list": failed("") }).git)).toEqual(new Set());
  });

  test("unpushedShas marks nothing, and never walks, without a remote-tracking ref", () => {
    const { git, calls } = fakeRunner({ "for-each-ref": ok(""), "rev-list": ok(`${SHA_A}\n`) });
    expect(unpushedShas(git)).toEqual(new Set());
    expect(calls.map((call) => call.args[0])).toEqual(["for-each-ref"]);
  });
});

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
