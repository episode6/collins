// New in the ghackett fork of agent-session-manager (GPL-3.0).

/** The commit keys' git operations against a temp repository. */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import {
  commit,
  commitFixup,
  gitRunnerAsync,
  inProgressOperation,
  parentOf,
  revParse,
  stagedPaths,
  treeMark,
  unpushedCommits,
  unpushedShas,
} from "../git.ts";
import { createTestGitRepository, hasGit, type TestGitRepository } from "./support/gitRepo.ts";

describe.skipIf(!hasGit())("commit, fixup and their preconditions against a real repository", () => {
  let repo: TestGitRepository;

  beforeEach(() => {
    repo = createTestGitRepository();
    repo.write("f.txt", "one\n");
    repo.run("add", "-A");
    repo.run("commit", "-qm", "first");
  });

  afterEach(() => {
    repo.dispose();
  });

  test("stagedPaths lists what the index differs from HEAD in, NUL-safe", () => {
    expect(stagedPaths(repo.git)).toEqual([]);
    repo.write("f.txt", "two\n");
    expect(stagedPaths(repo.git)).toEqual([]); // the working tree alone does not count
    repo.run("add", "f.txt");
    repo.write("dir name/a b.txt", "x\n");
    repo.run("add", "-A");
    expect(stagedPaths(repo.git).sort()).toEqual(["dir name/a b.txt", "f.txt"]);
  });

  test("commit with a summary only, then with a body: git joins them with a blank line", () => {
    repo.write("f.txt", "two\n");
    repo.run("add", "f.txt");
    const summary = commit(repo.git, "Second thing");
    expect(summary).toMatchObject({ ok: true, stderr: "" });
    expect(repo.run("log", "-1", "--format=%B")).toBe("Second thing\n\n");
    expect(stagedPaths(repo.git)).toEqual([]);

    repo.write("f.txt", "three\n");
    repo.run("add", "f.txt");
    expect(commit(repo.git, "Third thing", "Because the second\nwas not enough.").ok).toBe(true);
    expect(repo.run("log", "-1", "--format=%B")).toBe("Third thing\n\nBecause the second\nwas not enough.\n\n");
    // An empty body is no body.
    repo.write("f.txt", "four\n");
    repo.run("add", "f.txt");
    expect(commit(repo.git, "Fourth", "").ok).toBe(true);
    expect(repo.run("log", "-1", "--format=%B")).toBe("Fourth\n\n");
  });

  test("commit with nothing staged fails the way git says it does", () => {
    const result = commit(repo.git, "Nothing");
    expect(result.ok).toBe(false);
    expect(`${result.stdout}${result.stderr}`).toMatch(/nothing to commit|no changes added/);
  });

  test("the asynchronous runner commits without blocking, and reports a failure the same way", async () => {
    const git = gitRunnerAsync(repo.root);
    repo.write("f.txt", "two\n");
    repo.run("add", "f.txt");
    let ticked = false;
    const pending = commit(git, "Async second");
    setImmediate(() => {
      ticked = true; // the event loop turned while git ran
    });
    expect(await pending).toMatchObject({ ok: true });
    expect(ticked).toBe(true);
    expect(repo.run("log", "-1", "--format=%s")).toBe("Async second\n");
    const failure = await commit(git, "Nothing");
    expect(failure.ok).toBe(false);
    expect(`${failure.stdout}${failure.stderr}`).toMatch(/nothing to commit|no changes added/);
    // Stdin reaches git too.
    expect(await git(["hash-object", "--stdin"], "payload\n")).toMatchObject({ ok: true, stdout: expect.stringMatching(/^[0-9a-f]{40}\n$/) });
  });

  test("inProgressOperation is null normally and names a merge once MERGE_HEAD exists", () => {
    expect(inProgressOperation(repo.git)).toBeNull();
    const gitDir = repo.run("rev-parse", "--absolute-git-dir").trim();
    writeFileSync(join(gitDir, "MERGE_HEAD"), `${revParse(repo.git, "HEAD")}\n`);
    expect(inProgressOperation(repo.git)).toBe("a merge");
    writeFileSync(join(gitDir, "CHERRY_PICK_HEAD"), `${revParse(repo.git, "HEAD")}\n`);
    // The list is ordered; a rebase marker would win, a merge beats a cherry-pick.
    expect(inProgressOperation(repo.git)).toBe("a merge");
  });

  test("treeMark reads the index mtime and HEAD, and moves when either does", () => {
    const before = treeMark(repo.git)!;
    expect(before).toEqual({ index: expect.stringMatching(/^\d{16,}$/), head: revParse(repo.git, "HEAD")! });
    repo.write("f.txt", "two\n");
    repo.run("add", "f.txt");
    const staged = treeMark(repo.git)!;
    expect(staged.head).toBe(before.head);
    expect(staged.index).not.toBe(before.index);
    repo.run("commit", "-qm", "second");
    expect(treeMark(repo.git)!.head).not.toBe(before.head);
  });

  test("unpushedCommits: the group's commits on no remote-tracking ref — not @{upstream}..HEAD", () => {
    // No remote at all: everything is unpushed, and the group range still cuts.
    repo.write("f.txt", "two\n");
    repo.run("commit", "-qam", "second");
    expect(unpushedCommits(repo.git, ["HEAD"], 20).map((c) => c.subject)).toEqual(["second", "first"]);
    expect(unpushedCommits(repo.git, ["HEAD"], 1).map((c) => c.subject)).toEqual(["second"]);
    expect(unpushedCommits(repo.git, ["HEAD~1..HEAD"], 20).map((c) => c.subject)).toEqual(["second"]);

    // main pushed; feat forks, is pushed, and main moves on (pushed too).
    repo.markPushed();
    expect(unpushedCommits(repo.git, ["HEAD"], 20)).toEqual([]);
    repo.run("checkout", "-qb", "feat");
    repo.write("f.txt", "feat\n");
    repo.run("commit", "-qam", "feat work");
    repo.markPushed();
    repo.run("checkout", "-q", "main");
    repo.write("g.txt", "g\n");
    repo.run("add", "-A");
    repo.run("commit", "-qm", "main moved on (pushed)");
    repo.markPushed();
    repo.run("checkout", "-q", "feat");
    repo.run("rebase", "-q", "main");
    repo.write("f.txt", "local\n");
    repo.run("commit", "-qam", "feat local only");
    // The rebase put main's pushed commit into @{upstream}..HEAD; it is on origin/main, so not here.
    expect(repo.run("log", "--format=%s", "@{upstream}..HEAD")).toBe("feat local only\nfeat work\nmain moved on (pushed)\n");
    expect(unpushedCommits(repo.git, ["main..HEAD"], 20).map((c) => c.subject)).toEqual(["feat local only", "feat work"]);
    expect(unpushedCommits(repo.git, ["HEAD"], 20).map((c) => c.subject)).toEqual(["feat local only", "feat work"]);
    const marks = unpushedShas(repo.git);
    expect(unpushedCommits(repo.git, ["main..HEAD"], 20).every((c) => marks.has(c.sha))).toBe(true);
    expect(marks.size).toBe(2);
    // Pushed since (a push without -u leaves no upstream; the ref is what counts).
    repo.run("update-ref", "refs/remotes/origin/feat", "HEAD");
    expect(unpushedCommits(repo.git, ["main..HEAD"], 20)).toEqual([]);
    expect(unpushedShas(repo.git).size).toBe(0);
  });

  test("commitFixup writes `fixup! <full sha>` and the named interactive autosquash folds it into the target", () => {
    repo.markPushed();
    repo.write("f.txt", "two\n");
    repo.run("commit", "-qam", "second");
    repo.write("g.txt", "g\n");
    repo.run("add", "g.txt");
    repo.run("commit", "-qm", "third");
    const target = unpushedCommits(repo.git, ["HEAD"], 20).find((c) => c.subject === "second")!;
    expect(target.sha).toMatch(/^[0-9a-f]{40}$/);

    repo.write("f.txt", "two, fixed\n");
    repo.run("add", "f.txt");
    expect(commitFixup(repo.git, target.sha)).toMatchObject({ ok: true });
    expect(repo.run("log", "-1", "--format=%s")).toBe(`fixup! ${target.sha}\n`);
    expect(stagedPaths(repo.git)).toEqual([]);

    // The command the toast quotes does what it promises — with `-i`, since
    // `--autosquash` alone is ignored by every git before 2.44; the todo
    // editor is a no-op here, as a user saving the pre-arranged todo is.
    const parent = parentOf(repo.git, target.sha)!;
    const rebase = repo.git(["-c", "sequence.editor=:", "rebase", "-i", "--autosquash", "--autostash", parent]);
    expect(rebase.ok).toBe(true);
    expect(repo.run("log", "--format=%s", "-3")).toBe("third\nsecond\nfirst\n");
    expect(repo.run("show", "HEAD~1:f.txt")).toBe("two, fixed\n");
  });
});
