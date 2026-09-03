// New in the ghackett fork of agent-session-manager (GPL-3.0).

/** The staging keys against a real git index, in a temp repository. */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import {
  applyCached,
  gitRunner,
  parentOf,
  readFilePatch,
  readLog,
  readStatus,
  resolveBranch,
  revParse,
  stageAll,
  stageFiles,
  unstageAll,
  unstageFiles,
  currentBranch,
  guessDefault,
  localBranches,
  repoName,
  repoToplevel,
  unpushedShas,
} from "../git.ts";
import { parseFilePatch } from "../patch.ts";
import { planAll, planFileToggle, planHunkToggle } from "../staging.ts";
import { createTestGitRepository, hasGit, type TestGitRepository } from "./support/gitRepo.ts";
import { realpathSync } from "node:fs";
import { basename, join } from "node:path";

const TEN_LINES = Array.from({ length: 10 }, (_, i) => `line ${i + 1}`).join("\n") + "\n";

describe.skipIf(!hasGit())("staging against a real index", () => {
  let repo: TestGitRepository;

  beforeEach(() => {
    repo = createTestGitRepository();
    repo.write("f.txt", TEN_LINES);
    repo.write("old.txt", "keep me around\nfor a while\n");
    repo.run("add", "-A");
    repo.run("commit", "-qm", "first");
  });

  afterEach(() => {
    repo.dispose();
  });

  /** Host hunks the way hunk would report them, from our own parse of the same text. */
  function hostHunksOf(patchText: string) {
    return parseFilePatch(patchText).hunks.map((hunk) => ({
      index: hunk.index,
      newRange: [hunk.newStart, hunk.newStart + Math.max(hunk.newCount, 1) - 1] as [number, number],
    }));
  }

  test("x stages hunk 2 of 2 and only it; x in the staged view puts it back", () => {
    repo.write("f.txt", TEN_LINES.replace("line 1\n", "line 1 changed\n").replace("line 10\n", "line 10 changed\n"));
    const unstaged = readFilePatch(repo.git, "f.txt", false);
    expect(unstaged.ok).toBe(true);
    const file = { path: "f.txt", changeType: "change" as const, hunks: hostHunksOf(unstaged.stdout) };
    expect(file.hunks).toHaveLength(2);

    const plan = planHunkToggle({ file, hunkIndex: 1, live: "unstaged", patchText: unstaged.stdout });
    expect(plan.kind).toBe("apply");
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse).ok).toBe(true);

    const cached = repo.run("diff", "--cached", "--", "f.txt");
    expect(cached).toContain("+line 10 changed");
    expect(cached).not.toContain("line 1 changed");
    expect(readStatus(repo.git)).toEqual({
      unstaged: [{ path: "f.txt", code: "M" }],
      staged: [{ path: "f.txt", code: "M" }],
    });

    // Now the staged side: the one staged hunk, unstaged with the reversed apply.
    const staged = readFilePatch(repo.git, "f.txt", true);
    const stagedFile = { path: "f.txt", changeType: "change" as const, hunks: hostHunksOf(staged.stdout) };
    const back = planHunkToggle({ file: stagedFile, hunkIndex: 0, live: "staged", patchText: staged.stdout });
    expect(back).toMatchObject({ kind: "apply", reverse: true, label: "unstaged hunk 1 of f.txt" });
    if (back.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, back.patch, back.reverse).ok).toBe(true);
    expect(repo.run("diff", "--cached")).toBe("");
    expect(readStatus(repo.git)?.unstaged).toEqual([{ path: "f.txt", code: "M" }]);
  });

  test("x works under diff.noprefix and diff.mnemonicPrefix (the patch is normalised)", () => {
    repo.run("config", "diff.noprefix", "true");
    repo.run("config", "diff.mnemonicPrefix", "true");
    repo.write("f.txt", TEN_LINES.replace("line 1\n", "line 1 changed\n").replace("line 10\n", "line 10 changed\n"));
    // The user's config would drop the a/ and b/ apply strips...
    expect(repo.run("diff", "--no-color", "--", "f.txt")).toContain("\n--- f.txt\n");
    // ...and the patch we feed apply carries them regardless.
    const unstaged = readFilePatch(repo.git, "f.txt", false);
    expect(unstaged.ok).toBe(true);
    expect(unstaged.stdout).toContain("\n--- a/f.txt\n+++ b/f.txt\n");
    const file = { path: "f.txt", changeType: "change" as const, hunks: hostHunksOf(unstaged.stdout) };
    const plan = planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText: unstaged.stdout });
    expect(plan.kind).toBe("apply");
    if (plan.kind !== "apply") {
      return;
    }
    const applied = applyCached(repo.git, plan.patch, plan.reverse);
    expect(applied).toMatchObject({ ok: true, stderr: "" });
    expect(repo.run("diff", "--cached", "--", "f.txt")).toContain("+line 1 changed");
    expect(readStatus(repo.git)).toEqual({
      unstaged: [{ path: "f.txt", code: "M" }],
      staged: [{ path: "f.txt", code: "M" }],
    });
  });

  test("a runner built from a subdirectory resolves to the top level, where hunk's paths make sense", () => {
    repo.write("sub/g.txt", "x\n");
    repo.run("add", "sub");
    repo.run("commit", "-qm", "sub");
    repo.write("sub/g.txt", "y\n");
    const fromSub = gitRunner(join(repo.root, "sub"));
    // Straight from the subdirectory, hunk's top-relative path names nothing.
    expect(readFilePatch(fromSub, "sub/g.txt", false).stdout).toBe("");
    expect(stageFiles(fromSub, ["sub/g.txt"]).ok).toBe(false);
    // Resolved the way index.ts's ensureGit does it, it works.
    const top = repoToplevel(fromSub);
    expect(top).not.toBeNull();
    expect(realpathSync(top!)).toBe(realpathSync(repo.root));
    const git = gitRunner(top!);
    const patch = readFilePatch(git, "sub/g.txt", false);
    expect(patch.stdout).toContain("+y");
    const file = { path: "sub/g.txt", changeType: "change" as const, hunks: hostHunksOf(patch.stdout) };
    const plan = planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText: patch.stdout });
    expect(plan.kind).toBe("apply");
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(git, plan.patch, plan.reverse).ok).toBe(true);
    expect(readStatus(git)?.staged).toEqual([{ path: "sub/g.txt", code: "M" }]);
    expect(unstageFiles(git, ["sub/g.txt"]).ok).toBe(true);
    expect(stageFiles(git, ["sub/g.txt"]).ok).toBe(true);
    expect(readStatus(git)?.unstaged).toEqual([]);
  });

  test("X on a rename stages and unstages both paths as one R", () => {
    repo.run("mv", "old.txt", "new.txt");
    expect(readStatus(repo.git)?.staged).toEqual([{ path: "new.txt", previousPath: "old.txt", code: "R" }]);

    const unstage = planFileToggle({ file: { path: "new.txt", previousPath: "old.txt", changeType: "rename-pure" }, live: "staged" });
    expect(unstage.paths).toEqual(["old.txt", "new.txt"]);
    expect(unstageFiles(repo.git, unstage.paths).ok).toBe(true);
    expect(readStatus(repo.git)).toEqual({
      unstaged: [
        { path: "old.txt", code: "D" },
        { path: "new.txt", code: "?" },
      ],
      staged: [],
    });

    const stage = planFileToggle({ file: { path: "new.txt", previousPath: "old.txt" }, live: "unstaged" });
    expect(stageFiles(repo.git, stage.paths).ok).toBe(true);
    expect(readStatus(repo.git)?.staged).toEqual([{ path: "new.txt", previousPath: "old.txt", code: "R" }]);
  });

  test("an untracked file goes whole, and A then U round-trips", () => {
    repo.write("n.txt", "new\n");
    repo.write("f.txt", "changed\n");
    const empty = readFilePatch(repo.git, "n.txt", false);
    expect(empty.ok).toBe(true);
    expect(planHunkToggle({ file: { path: "n.txt", isUntracked: true, changeType: "new" }, hunkIndex: 0, live: "unstaged", patchText: empty.stdout })).toEqual({ kind: "file" });

    const before = readStatus(repo.git);
    expect(planAll(before, true)).toEqual({ count: 2, stage: true });
    expect(stageAll(repo.git).ok).toBe(true);
    const staged = readStatus(repo.git);
    expect(staged?.unstaged).toEqual([]);
    expect(staged?.staged.map((row) => `${row.code} ${row.path}`).sort()).toEqual(["A n.txt", "M f.txt"]);

    expect(planAll(staged, false)).toEqual({ count: 2, stage: false });
    expect(unstageAll(repo.git).ok).toBe(true);
    expect(readStatus(repo.git)).toEqual(before);
  });

  test("the read-side helpers answer for a real repository", () => {
    const head = revParse(repo.git, "HEAD")!;
    expect(head).toMatch(/^[0-9a-f]{40}$/);
    expect(parentOf(repo.git, head)).toBeNull();
    expect(currentBranch(repo.git)).toBe("main");
    expect(guessDefault(repo.git)).toBe("main");
    expect(repoName(repo.git)).toBe(basename(repo.root));
    expect(resolveBranch(repo.git, "main")).toEqual({ target: "main", sha: head });
    expect(resolveBranch(repo.git, "nope")).toBeNull();
    expect(resolveBranch(repo.git, "-x")).toBeNull();
    expect(unpushedShas(repo.git).size).toBe(0);

    repo.run("checkout", "-qb", "feat");
    repo.write("f.txt", "second\n");
    repo.run("commit", "-qam", "second");
    expect(localBranches(repo.git)).toEqual(["feat", "main"]);
    const log = readLog(repo.git, ["main..HEAD"], { limit: 5 });
    expect(log.map((commit) => commit.subject)).toEqual(["second"]);
    expect(parentOf(repo.git, log[0]!.sha)).toBe(head);
    expect(readLog(repo.git, ["HEAD"], { limit: 1, skip: 1 }).map((commit) => commit.subject)).toEqual(["first"]);
  });
});
