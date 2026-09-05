// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * Line ranges against a real git index and working tree. The partial
 * patches the range writer emits are fed to `git apply` here: git is the
 * oracle for whether a rewritten hunk header and body describe the file
 * they claim to, and `git show :path` for what the index ended up with.
 */

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { chmodSync, statSync } from "node:fs";
import { join } from "node:path";
import { applyCached, applyWorktreeReverse, readFilePatch } from "../git.ts";
import { parseFilePatch } from "../patch.ts";
import { planDiscard, planHunkToggle, planRangeToggle, type StagingFile } from "../staging.ts";
import type { LineAddress } from "../range.ts";
import { createTestGitRepository, hasGit, type TestGitRepository } from "./support/gitRepo.ts";
import { readStatus } from "./support/status.ts";

const lines = (count: number): string[] => Array.from({ length: count }, (_, i) => `line ${i + 1}`);
const THIRTY = lines(30).join("\n") + "\n";

const old = (line: number): LineAddress => ({ side: "old", line });
const fresh = (line: number): LineAddress => ({ side: "new", line });

describe.skipIf(!hasGit())("line ranges against a real index", () => {
  let repo: TestGitRepository;

  beforeEach(() => {
    repo = createTestGitRepository();
    repo.write("f.txt", THIRTY);
    repo.run("add", "-A");
    repo.run("commit", "-qm", "first");
  });

  afterEach(() => {
    repo.dispose();
  });

  /** The file the way hunk would hand it over, from our own parse of the same text. */
  function fileFrom(patchText: string, path = "f.txt", extra: Partial<StagingFile> = {}): StagingFile {
    return {
      path,
      changeType: "change",
      hunks: parseFilePatch(patchText).hunks.map((hunk) => ({
        index: hunk.index,
        newRange: [hunk.newStart, hunk.newStart + Math.max(hunk.newCount, 1) - 1] as [number, number],
      })),
      ...extra,
    };
  }

  function unstagedPatch(path = "f.txt"): string {
    const result = readFilePatch(repo.git, path, false);
    expect(result.ok).toBe(true);
    return result.stdout;
  }

  function stagedPatch(path = "f.txt"): string {
    const result = readFilePatch(repo.git, path, true);
    expect(result.ok).toBe(true);
    return result.stdout;
  }

  function indexContent(path = "f.txt"): string {
    return repo.run("show", `:${path}`);
  }

  test("x stages two of a hunk's five added lines; the other three stay in the working tree", () => {
    const inserted = [...lines(10), "new 1", "new 2", "new 3", "new 4", "new 5", ...lines(30).slice(10)].join("\n") + "\n";
    repo.write("f.txt", inserted);
    const patchText = unstagedPatch();
    const plan = planRangeToggle({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      anchor: fresh(12),
      head: fresh(13),
      live: "unstaged",
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", reverse: false, label: "staged 2 lines of f.txt", lines: 2 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe([...lines(10), "new 2", "new 3", ...lines(30).slice(10)].join("\n") + "\n");
    // What is left in the working tree still reads as one clean hunk.
    const rest = parseFilePatch(unstagedPatch());
    expect(rest.hunks.map((hunk) => hunk.lines.filter((line) => line.kind === "added").map((line) => line.text))).toEqual([
      ["new 1", "new 4", "new 5"],
    ]);
    expect(readStatus(repo.git)).toEqual({
      unstaged: [{ path: "f.txt", code: "M" }],
      staged: [{ path: "f.txt", code: "M" }],
    });
  });

  test("x in the staged view unstages a range: the reversed partial patch against the --cached patch", () => {
    const inserted = [...lines(10), "new 1", "new 2", "new 3", "new 4", "new 5", ...lines(30).slice(10)].join("\n") + "\n";
    repo.write("f.txt", inserted);
    repo.run("add", "f.txt");
    const patchText = stagedPatch();
    const plan = planRangeToggle({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      anchor: fresh(13),
      head: fresh(12),
      live: "staged",
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", reverse: true, label: "unstaged 2 lines of f.txt", lines: 2 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe([...lines(10), "new 1", "new 4", "new 5", ...lines(30).slice(10)].join("\n") + "\n");
    const rest = parseFilePatch(unstagedPatch());
    expect(rest.hunks.map((hunk) => hunk.lines.filter((line) => line.kind === "added").map((line) => line.text))).toEqual([
      ["new 2", "new 3"],
    ]);
    expect(repo.read("f.txt")).toBe(inserted);
  });

  test("a range spanning two of three hunks leaves the third for a clean hunk-level x afterwards", () => {
    const edited = THIRTY.replace("line 3\n", "line 3 changed\n")
      .replace("line 15\n", "line 15 changed\nline 15 extra\n")
      .replace("line 27\n", "line 27 changed\n");
    repo.write("f.txt", edited);
    const patchText = unstagedPatch();
    const parsed = parseFilePatch(patchText);
    expect(parsed.hunks).toHaveLength(3);
    // From the `-` of hunk 1 (old 3) to the extra line of hunk 2, either order in.
    const plan = planRangeToggle({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      anchor: fresh(16),
      head: old(3),
      live: "unstaged",
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", label: "staged 5 lines of f.txt", lines: 5 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe(
      THIRTY.replace("line 3\n", "line 3 changed\n").replace("line 15\n", "line 15 changed\nline 15 extra\n"),
    );
    // The third hunk is the only one left, and stages whole the ordinary way.
    const remaining = unstagedPatch();
    const file = fileFrom(remaining);
    expect(file.hunks).toHaveLength(1);
    const hunkPlan = planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText: remaining });
    expect(hunkPlan.kind).toBe("apply");
    if (hunkPlan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, hunkPlan.patch, hunkPlan.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe(edited);
    expect(readStatus(repo.git)?.unstaged).toEqual([]);
  });

  test("a range of context lines only is refused and the index is left alone", () => {
    repo.write("f.txt", THIRTY.replace("line 15\n", "line 15 changed\n"));
    const patchText = unstagedPatch();
    const plan = planRangeToggle({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      anchor: fresh(12),
      head: fresh(13),
      live: "unstaged",
      patchText,
    });
    expect(plan).toEqual({ kind: "refuse", reason: "no changes between the anchor and the cursor" });
    expect(repo.run("diff", "--cached")).toBe("");
    // As is an address nothing carries: line 13 on the old side is context
    // (fine), but line 40 is past the file.
    expect(
      planRangeToggle({ file: fileFrom(patchText), reviewPatchText: patchText, anchor: old(40), head: fresh(15), live: "unstaged", patchText }),
    ).toEqual({ kind: "refuse", reason: "the anchor is not on a diff line — press v again" });
  });

  test("D on a range reverts just those lines in the working tree, the index untouched", () => {
    const edited = THIRTY.replace("line 5\n", "line 5 changed\n").replace("line 7\n", "line 7 changed\nline 7 extra\n");
    repo.write("f.txt", edited);
    const patchText = unstagedPatch();
    const plan = planDiscard({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      live: "unstaged",
      hunkIndex: 0,
      range: { anchor: old(7), head: fresh(8) },
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", label: "discarded 3 lines of f.txt", lines: 3, hunk: null });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyWorktreeReverse(repo.git, plan.patch)).toMatchObject({ ok: true });
    expect(repo.read("f.txt")).toBe(THIRTY.replace("line 5\n", "line 5 changed\n"));
    expect(repo.run("diff", "--cached")).toBe("");
  });

  test("D on the added lines alone removes them without restoring the line they replaced", () => {
    repo.write("f.txt", THIRTY.replace("line 7\n", "line 7 changed\nline 7 extra\n"));
    const patchText = unstagedPatch();
    const plan = planDiscard({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      live: "unstaged",
      hunkIndex: 0,
      range: { anchor: fresh(7), head: fresh(8) },
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", label: "discarded 2 lines of f.txt", lines: 2 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyWorktreeReverse(repo.git, plan.patch)).toMatchObject({ ok: true });
    expect(repo.read("f.txt")).toBe(THIRTY.replace("line 7\n", ""));
  });

  test("D on a hunk reverts the hunk and no other, with the earlier hunk left in place", () => {
    const edited = THIRTY.replace("line 3\n", "line 3 changed\nline 3 extra\n").replace("line 20\n", "line 20 changed\n");
    repo.write("f.txt", edited);
    const patchText = unstagedPatch();
    const plan = planDiscard({
      file: fileFrom(patchText),
      reviewPatchText: patchText,
      live: "unstaged",
      hunkIndex: 1,
      range: null,
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", label: "discarded hunk 2 of f.txt", lines: 2, hunk: 1 });
    if (plan.kind !== "apply") {
      return;
    }
    // git wrote `@@ -17,7 +18,7 @@`; with hunk 1 (net +1) left in the
    // working tree, the old side of the reversed patch starts one later.
    expect(plan.patch).toContain("@@ -18,7 +18,7 @@");
    expect(applyWorktreeReverse(repo.git, plan.patch)).toMatchObject({ ok: true });
    expect(repo.read("f.txt")).toBe(THIRTY.replace("line 3\n", "line 3 changed\nline 3 extra\n"));
  });

  test("a whole end-of-file change round-trips; a split of it is refused", () => {
    repo.write("f.txt", THIRTY.trimEnd());
    repo.run("commit", "-qam", "no trailing newline");
    repo.write("f.txt", `${THIRTY}line 31\n`);
    const patchText = unstagedPatch();
    expect(patchText).toContain("\\ No newline at end of file");
    const file = fileFrom(patchText);
    // Only the new last line: the newline added to line 30 cannot be left behind.
    expect(
      planRangeToggle({ file, reviewPatchText: patchText, anchor: fresh(31), head: fresh(31), live: "unstaged", patchText }),
    ).toEqual({ kind: "refuse", reason: "select the whole end-of-file change" });
    expect(repo.run("diff", "--cached")).toBe("");
    // The whole change, as a range: the marker travels and git accepts it.
    const plan = planRangeToggle({ file, reviewPatchText: patchText, anchor: old(30), head: fresh(31), live: "unstaged", patchText });
    expect(plan).toMatchObject({ kind: "apply", lines: 3 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe(`${THIRTY}line 31\n`);
    expect(readStatus(repo.git)?.unstaged).toEqual([]);
    // Just the newline on line 30 is a legitimate partial: line 31 stays unstaged.
    repo.run("reset", "-q");
    const newline = planRangeToggle({ file, reviewPatchText: patchText, anchor: old(30), head: fresh(30), live: "unstaged", patchText });
    expect(newline).toMatchObject({ kind: "apply", lines: 2 });
    if (newline.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, newline.patch, newline.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe(THIRTY);
    expect(parseFilePatch(unstagedPatch()).hunks[0]?.lines.filter((line) => line.kind !== "context")).toEqual([
      { kind: "added", text: "line 31", noNewlineAtEof: false },
    ]);
  });

  test("a CRLF file staged by range keeps its line endings byte for byte", () => {
    const crlf = lines(6).join("\r\n") + "\r\n";
    repo.write("crlf.txt", crlf);
    repo.run("add", "crlf.txt");
    repo.run("commit", "-qm", "crlf");
    const edited = crlf.replace("line 2\r\n", "line 2 changed\r\n").replace("line 5\r\n", "line 5 changed\r\n");
    repo.write("crlf.txt", edited);
    const patchText = unstagedPatch("crlf.txt");
    expect(patchText).toContain("+line 2 changed\r\n");
    const plan = planRangeToggle({
      file: fileFrom(patchText, "crlf.txt"),
      reviewPatchText: patchText,
      anchor: old(2),
      head: fresh(2),
      live: "unstaged",
      patchText,
    });
    expect(plan).toMatchObject({ kind: "apply", lines: 2 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(indexContent("crlf.txt")).toBe(crlf.replace("line 2\r\n", "line 2 changed\r\n"));
    expect(repo.read("crlf.txt")).toBe(edited);
  });

  test("a file whose mode changed too: a range stages its lines and not the bit; a discard leaves the bit on", () => {
    repo.write("f.txt", [...lines(10), "new 1", "new 2", "new 3", ...lines(30).slice(10)].join("\n") + "\n");
    chmodSync(join(repo.root, "f.txt"), 0o755);
    const patchText = unstagedPatch();
    expect(patchText).toContain("old mode 100644\nnew mode 100755\n");
    const file = fileFrom(patchText);
    const plan = planRangeToggle({ file, reviewPatchText: patchText, anchor: fresh(11), head: fresh(12), live: "unstaged", patchText });
    expect(plan).toMatchObject({ kind: "apply", label: "staged 2 lines of f.txt", lines: 2 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(repo.run("ls-files", "-s", "f.txt")).toMatch(/^100644 /);
    expect(indexContent()).toBe([...lines(10), "new 1", "new 2", ...lines(30).slice(10)].join("\n") + "\n");
    // The same for a whole hunk (PR 2's x): the mode stays for X.
    repo.run("reset", "-q");
    const hunkPlan = planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText });
    expect(hunkPlan.kind).toBe("apply");
    if (hunkPlan.kind === "apply") {
      expect(applyCached(repo.git, hunkPlan.patch, hunkPlan.reverse)).toMatchObject({ ok: true });
      expect(repo.run("ls-files", "-s", "f.txt")).toMatch(/^100644 /);
      expect(unstagedPatch()).toContain("old mode 100644\nnew mode 100755\n"); // left for X
    }
    repo.run("reset", "-q");
    const discard = planDiscard({ file, reviewPatchText: patchText, live: "unstaged", hunkIndex: 0, range: { anchor: fresh(11), head: fresh(12) }, patchText });
    expect(discard).toMatchObject({ kind: "apply", label: "discarded 2 lines of f.txt" });
    if (discard.kind !== "apply") {
      return;
    }
    expect(applyWorktreeReverse(repo.git, discard.patch)).toMatchObject({ ok: true });
    expect(statSync(join(repo.root, "f.txt")).mode & 0o111).toBe(0o111);
    expect(repo.read("f.txt")).toBe([...lines(10), "new 3", ...lines(30).slice(10)].join("\n") + "\n");
  });

  test("with diff.context = 0 a hunk has no context to anchor by, and both x and D still apply", () => {
    repo.run("config", "diff.context", "0");
    repo.write("f.txt", THIRTY.replace("line 15\nline 16\n", "changed 15\nchanged 16\n"));
    const patchText = unstagedPatch();
    expect(patchText).toContain("@@ -15,2 +15,2 @@");
    expect(patchText).not.toContain("\n line 14\n");
    const file = fileFrom(patchText);
    // One of the two new lines: the demoted `-` lines are its only context, and nothing trails.
    const plan = planRangeToggle({ file, reviewPatchText: patchText, anchor: fresh(15), head: fresh(15), live: "unstaged", patchText });
    expect(plan).toMatchObject({ kind: "apply", lines: 1 });
    if (plan.kind !== "apply") {
      return;
    }
    expect(applyCached(repo.git, plan.patch, plan.reverse)).toMatchObject({ ok: true });
    expect(indexContent()).toBe(THIRTY.replace("line 16\n", "line 16\nchanged 15\n"));
    repo.run("reset", "-q");
    // The whole hunk, as PR 2's x sends it: no context at all.
    const hunkPlan = planHunkToggle({ file, hunkIndex: 0, live: "unstaged", patchText });
    expect(hunkPlan.kind).toBe("apply");
    if (hunkPlan.kind === "apply") {
      expect(applyCached(repo.git, hunkPlan.patch, hunkPlan.reverse)).toMatchObject({ ok: true });
      expect(indexContent()).toBe(THIRTY.replace("line 15\nline 16\n", "changed 15\nchanged 16\n"));
    }
    repo.run("reset", "-q");
    const discard = planDiscard({ file, reviewPatchText: patchText, live: "unstaged", hunkIndex: 0, range: null, patchText });
    expect(discard).toMatchObject({ kind: "apply", hunk: 0 });
    if (discard.kind === "apply") {
      expect(applyWorktreeReverse(repo.git, discard.patch)).toMatchObject({ ok: true });
      expect(repo.read("f.txt")).toBe(THIRTY);
    }
  });

  test("a range in a rename is refused, whether hunk flagged the rename or only the patch shows it", () => {
    repo.run("mv", "f.txt", "g.txt");
    repo.write("g.txt", THIRTY.replace("line 2\n", "line 2 changed\n"));
    repo.run("add", "g.txt");
    const patchText = stagedPatch("g.txt");
    const result = readFilePatch(repo.git, "g.txt", true, "f.txt");
    expect(result.stdout).toContain("rename from f.txt");
    const flagged: StagingFile = { path: "g.txt", previousPath: "f.txt", changeType: "rename-changed" };
    expect(
      planRangeToggle({ file: flagged, reviewPatchText: result.stdout, anchor: fresh(2), head: fresh(2), live: "staged", patchText: result.stdout }),
    ).toEqual({ kind: "refuse", reason: "renames stage whole: use X" });
    const unflagged = fileFrom(result.stdout, "g.txt");
    expect(
      planRangeToggle({ file: unflagged, reviewPatchText: patchText, anchor: fresh(2), head: fresh(2), live: "staged", patchText: result.stdout }),
    ).toEqual({ kind: "refuse", reason: "renames stage whole: use X" });
    expect(indexContent("g.txt")).toBe(THIRTY.replace("line 2\n", "line 2 changed\n"));
  });
});
