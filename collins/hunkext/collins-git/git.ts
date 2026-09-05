// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from joshedler/hunk-git-lite (MIT, © 2026 Josh Edler);
// see collins/THIRD_PARTY_LICENSES.md.

/**
 * Git, as the collins-git extension runs it: one runner and the handful
 * of operations the staging keys need — a file's patch re-read at action
 * time, the applies, the file-level add and reset, the restore, and the
 * tree mark Collins' freshness poll compares.
 *
 * Everything below takes the runner as an argument rather than reaching for
 * a global, so the tests feed it a temp repository (or a canned reply) and
 * the composition root feeds it the review's cwd. Both output streams are
 * captured, never inherited: the renderer owns the terminal, and one line of
 * git chatter on stderr would corrupt the frame. The log, status and branch
 * readers that fed the panes live in Collins now (collins/gitmodel.py,
 * collins/gitops.py).
 */

import { execFileSync } from "node:child_process";
import { statSync } from "node:fs";
import { join } from "node:path";

/** What one git invocation came back with. `ok` is "exit status 0". */
export interface GitResult {
  readonly ok: boolean;
  readonly stdout: string;
  readonly stderr: string;
}

/** Per-call knobs a caller may set; everything else the runner decides. */
export interface GitRunOptions {
  /** How long to wait before giving up; the default suits reads and applies. */
  readonly timeoutMs?: number;
}

/** One git invocation in a fixed working directory, with optional stdin. */
export type GitRunner = (args: readonly string[], stdin?: string, options?: GitRunOptions) => GitResult;

const GIT_TIMEOUT_MS = 5_000;
const GIT_MAX_BUFFER = 32 * 1024 * 1024;

/**
 * The `-c` options hunk itself puts in front of every `git diff` it reads
 * (its DIFF_PREFIX_NORMALIZATION_ARGS), and for the same reason: a patch
 * this extension re-reads is fed back to `git apply`, and a user's
 * `diff.noprefix` / `diff.mnemonicPrefix` / custom prefixes would leave
 * the headers without the `a/` and `b/` the apply strips (verified with git
 * 2.53: under `diff.noprefix=true`, `git diff | git apply --cached -` fails
 * with "git diff header lacks filename information").
 */
export const DIFF_PREFIX_ARGS: readonly string[] = [
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
];

/**
 * Run git in one working directory.
 *
 * A thrown error (non-zero exit, timeout, no git at all) becomes a failed
 * result carrying whatever stderr there was, so callers decide what to tell
 * the user without a try/catch each.
 */
export function gitRunner(cwd: string): GitRunner {
  return (args, stdin, options) => {
    try {
      const stdout = execFileSync("git", [...args], {
        cwd,
        encoding: "utf8",
        timeout: options?.timeoutMs ?? GIT_TIMEOUT_MS,
        maxBuffer: GIT_MAX_BUFFER,
        stdio: ["pipe", "pipe", "pipe"],
        input: stdin ?? "",
      });
      return { ok: true, stdout, stderr: "" };
    } catch (error) {
      return failedResult(error);
    }
  };
}

/** The failed result a thrown child-process error stands for. */
function failedResult(error: unknown): GitResult {
  const failure = error as { stdout?: unknown; stderr?: unknown; message?: unknown };
  return {
    ok: false,
    stdout: typeof failure.stdout === "string" ? failure.stdout : "",
    stderr:
      typeof failure.stderr === "string" && failure.stderr !== "" ? failure.stderr : String(failure.message ?? error),
  };
}

/**
 * A branch or ref name that is safe to hand to git as a positional argument.
 *
 * Names arrive from outside (a title, a dialog), so they are untrusted: no
 * whitespace, no leading dash (git would read an option), no `..` (a range
 * where a name was expected). Returns the trimmed name, or null.
 */
export function safeRef(name: unknown): string | null {
  if (typeof name !== "string") {
    return null;
  }
  const value = name.trim();
  if (value === "" || value.length > 128) {
    return null;
  }
  if (/\s/.test(value) || value.startsWith("-") || value.includes("..")) {
    return null;
  }
  if (/[\0~^:?*[\\]/.test(value) || value.endsWith("/") || value.endsWith(".lock")) {
    return null;
  }
  return value;
}

/** `git rev-parse --verify --quiet <rev>` as a sha, or null. */
export function revParse(git: GitRunner, rev: string): string | null {
  const result = git(["rev-parse", "--verify", "--quiet", rev]);
  const sha = result.stdout.trim();
  return result.ok && /^[0-9a-f]{40}$/.test(sha) ? sha : null;
}

/**
 * The working tree's top-level directory (absolute), or null outside a
 * repository. Every other call here should run in it: the paths hunk
 * reports are relative to the top, while a pathspec given to `git diff --
 * <path>` or `git add -- <path>` is read relative to the process's cwd — so
 * from a subdirectory (an agent that `cd`'d into `packages/foo`, which is
 * where hunk starts) the same paths name nothing.
 */
export function repoToplevel(git: GitRunner): string | null {
  const result = git(["rev-parse", "--show-toplevel"]);
  const top = result.stdout.trim();
  return result.ok && top !== "" ? top : null;
}

/**
 * The patch for one file, re-read at action time so its numbers are exact:
 * `git diff -- <path>` against the working tree, or `--cached` against the
 * index, its header prefixes normalised (DIFF_PREFIX_ARGS) so the user's
 * diff config can't change what `git apply` is later handed. A rename names
 * both paths so the patch carries the rename record.
 */
export function readFilePatch(
  git: GitRunner,
  path: string,
  staged: boolean,
  previousPath?: string,
): GitResult {
  const paths = previousPath !== undefined && previousPath !== path ? [previousPath, path] : [path];
  return git([
    ...DIFF_PREFIX_ARGS,
    "diff",
    ...(staged ? ["--cached"] : []),
    "--no-color",
    "--no-ext-diff",
    "--find-renames",
    "--",
    ...paths,
  ]);
}

/** `git add -A -- <paths>`: stage the paths as they are, deletions included. */
export function stageFiles(git: GitRunner, paths: readonly string[]): GitResult {
  return git(["add", "-A", "--", ...paths]);
}

/** `git reset -q -- <paths>`: put the paths' index entries back to HEAD's. */
export function unstageFiles(git: GitRunner, paths: readonly string[]): GitResult {
  return git(["reset", "-q", "--", ...paths]);
}

/**
 * The options every apply here gets. `-p1` is apply's default, spelled
 * out: the patch always carries `a/` and `b/` (readFilePatch), whatever
 * the user's diff config says, and the strip count has to match that
 * rather than the config. `--unidiff-zero` turns off apply's rule that a
 * hunk with no trailing context must sit at the end of the file (and one
 * with none leading at the start): the patches come from the user's `git
 * diff`, whose `diff.context` may be 0, and a hunk from such a diff — or a
 * partial patch that keeps only a `+` line after its demoted context —
 * has no trailing context anywhere in the file. Every line the patch does
 * carry still has to match; only the at-the-edge heuristic goes.
 */
const APPLY_ARGS: readonly string[] = ["-p1", "--unidiff-zero"];

/**
 * Apply a patch to the index only — `git apply --cached -p1 --unidiff-zero
 * [--reverse] -` with the patch on stdin. Reverse against a `--cached`
 * patch is how a hunk is unstaged.
 */
export function applyCached(git: GitRunner, patch: string, reverse: boolean): GitResult {
  return git(["apply", "--cached", ...APPLY_ARGS, ...(reverse ? ["--reverse"] : []), "-"], patch);
}

/**
 * Take a patch back out of the working tree — `git apply -p1
 * --unidiff-zero --reverse -` with no `--cached`: the index is not
 * consulted and not touched. This is what `D` does with a `git diff --
 * <path>` patch, whole or trimmed to a range; apply itself refuses when
 * the file no longer looks like the patch's new side, so a file edited
 * under us fails loudly rather than losing something else.
 */
export function applyWorktreeReverse(git: GitRunner, patch: string): GitResult {
  return git(["apply", ...APPLY_ARGS, "--reverse", "-"], patch);
}

/**
 * Put a path back the way the index has it — `git checkout -q -- <path>`.
 * What `D` does for a file deleted in the working tree: the deletion's
 * patch reversed would say the same, but this needs no patch and restores
 * a binary too.
 */
export function restoreFile(git: GitRunner, path: string): GitResult {
  return git(["checkout", "-q", "--", path]);
}

/** This working tree's git directory (absolute; a worktree's own, not the common one), or null. */
export function gitDir(git: GitRunner): string | null {
  const result = git(["rev-parse", "--absolute-git-dir"]);
  const dir = result.stdout.trim();
  return result.ok && dir !== "" ? dir : null;
}

/**
 * What Collins' freshness poll compares, as this extension sees it after a
 * mutation of its own: the index file's mtime in nanoseconds (a string —
 * the number is past what a JSON reader's double holds exactly) and HEAD's
 * sha. Written to the sidecar so Collins can tell a move hunk has already
 * reloaded for from one made in a shell. Null when either cannot be read.
 */
export interface TreeMark {
  readonly index: string;
  readonly head: string;
}

export function treeMark(git: GitRunner): TreeMark | null {
  const dir = gitDir(git);
  const head = revParse(git, "HEAD");
  if (dir === null || head === null) {
    return null;
  }
  try {
    return { index: statSync(join(dir, "index"), { bigint: true }).mtimeNs.toString(), head };
  } catch {
    return null;
  }
}
