// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from sadick254/hunk-commit-log and joshedler/hunk-git-lite
// (MIT, © 2026 Sadick, © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

/**
 * Git, as the collins-git extension runs it: one runner, the parsers for the
 * porcelain it reads, and the handful of operations the panels, the
 * staging keys and the commit keys need.
 *
 * Everything below takes the runner as an argument rather than reaching for
 * a global, so the tests feed it a temp repository (or a canned reply) and
 * the composition root feeds it the review's cwd. Both output streams are
 * captured, never inherited: the renderer owns the terminal, and one line of
 * git chatter on stderr would corrupt the frame.
 */

import { execFile, execFileSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { basename, join } from "node:path";

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

/**
 * One git invocation in a fixed working directory, with optional stdin.
 * `R` is the result's shape: a `GitResult` from the synchronous runner the
 * reads and applies use, a promise of one from `gitRunnerAsync`.
 */
export type AnyGitRunner<R> = (args: readonly string[], stdin?: string, options?: GitRunOptions) => R;
export type GitRunner = AnyGitRunner<GitResult>;
export type AsyncGitRunner = AnyGitRunner<Promise<GitResult>>;

const GIT_TIMEOUT_MS = 5_000;
/**
 * A commit runs the user's hooks and maybe a signer, and it runs on the
 * asynchronous runner so a slow hook never freezes the renderer — so the
 * deadline only has to catch a git that will never come back (a pinentry
 * nobody can see). A test suite in a pre-commit hook fits comfortably.
 */
export const COMMIT_TIMEOUT_MS = 600_000;
const GIT_MAX_BUFFER = 32 * 1024 * 1024;

/** The sha of git's empty tree: the "parent" a root commit diffs against. */
export const EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904";

/** Remotes tried first when a branch only exists remotely, most trusted first. */
const REMOTE_ORDER = ["origin", "upstream"];

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
 * The same runner, without blocking the event loop: for a commit, whose
 * hooks and signer can take as long as they like while hunk keeps painting
 * (a synchronous wait freezes the renderer — no toast, no repaint, keys
 * queued — for the whole of it). Reads and applies stay synchronous: they
 * are milliseconds, and their callers read `ctx.selection` in one go.
 */
export function gitRunnerAsync(cwd: string): AsyncGitRunner {
  return (args, stdin, options) =>
    new Promise((resolve) => {
      let child: ReturnType<typeof execFile>;
      try {
        child = execFile(
          "git",
          [...args],
          {
            cwd,
            encoding: "utf8",
            timeout: options?.timeoutMs ?? GIT_TIMEOUT_MS,
            maxBuffer: GIT_MAX_BUFFER,
          },
          (error, stdout, stderr) => {
            resolve(error === null ? { ok: true, stdout, stderr } : { ...failedResult(error), stdout, stderr: stderr || failedResult(error).stderr });
          },
        );
      } catch (error) {
        resolve(failedResult(error));
        return;
      }
      child.stdin?.on("error", () => {
        // git closed its end first (it read no stdin); the exit status says the rest.
      });
      child.stdin?.end(stdin ?? "");
    });
}

/**
 * A branch or ref name that is safe to hand to git as a positional argument.
 *
 * Names arrive from the sidecar Collins writes and from dialogs, so they are
 * untrusted: no whitespace, no leading dash (git would read an option), no
 * `..` (a range where a name was expected). Returns the trimmed name, or null.
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

/** One commit as the commits panel lists it. */
export interface Commit {
  readonly sha: string;
  readonly abbrev: string;
  readonly subject: string;
  /** The decorations git printed for `%D`, split on ", " (may be empty). */
  readonly refs: readonly string[];
}

/** The log format `parseLog` reads: fields NUL-separated, records RS-terminated. */
export const LOG_FORMAT = "--format=%H%x00%h%x00%s%x00%D%x1e";

/** Parse `git log` output written with `LOG_FORMAT` (a record per commit). */
export function parseLog(text: string): Commit[] {
  const commits: Commit[] = [];
  for (const record of text.split("\x1e")) {
    const line = record.replace(/^\n/, "");
    if (line.trim() === "") {
      continue;
    }
    const [sha, abbrev, subject, refs] = line.split("\0");
    if (sha === undefined || abbrev === undefined || !/^[0-9a-f]{40}$/.test(sha)) {
      continue;
    }
    commits.push({
      sha,
      abbrev,
      subject: subject ?? "",
      refs: (refs ?? "")
        .split(", ")
        .map((ref) => ref.trim())
        .filter((ref) => ref !== ""),
    });
  }
  return commits;
}

/** The one-letter state a file row carries; `?` is untracked, `U` unmerged. */
export type StatusCode = "M" | "A" | "D" | "R" | "?" | "T" | "U" | "C";

/** One path in `git status`, on one side (index or working tree). */
export interface StatusRow {
  readonly path: string;
  /** The old path, present only for a rename or copy. */
  readonly previousPath?: string;
  readonly code: StatusCode;
  /** Whether git reported the blob as binary; status never says, so unset. */
  readonly binary?: boolean;
}

/** The working tree's changes, split the way the files panel shows them. */
export interface Status {
  readonly unstaged: StatusRow[];
  readonly staged: StatusRow[];
}

function statusCode(letter: string): StatusCode | null {
  switch (letter) {
    case "M":
    case "A":
    case "D":
    case "R":
    case "T":
    case "C":
    case "U":
      return letter;
    default:
      return null;
  }
}

/**
 * Parse `git status --porcelain=v2 -z --untracked-files=all`.
 *
 * Entry kinds: `1` (ordinary: `1 XY sub mH mI mW hH hI path`), `2` (rename or
 * copy: `2 XY sub mH mI mW hH hI Xscore path`, followed by the original path
 * as its own NUL-terminated token), `u` (unmerged: ten fields then the
 * path), `?` (untracked) and `!` (ignored, skipped). X is the index side, Y
 * the working-tree side; a path lands in `staged` when X is not `.` and in
 * `unstaged` when Y is not `.` — both, for a file changed on top of a staged
 * change. Unmerged paths are listed under `unstaged` as `U`.
 */
export function parseStatusV2(text: string): Status {
  const staged: StatusRow[] = [];
  const unstaged: StatusRow[] = [];
  const tokens = text.split("\0");
  for (let index = 0; index < tokens.length; index += 1) {
    const entry = tokens[index] ?? "";
    if (entry === "") {
      continue;
    }
    const kind = entry.slice(0, 1);
    if (kind === "?") {
      unstaged.push({ path: entry.slice(2), code: "?" });
      continue;
    }
    if (kind === "!" || kind === "#") {
      continue;
    }
    const fieldCount = kind === "1" ? 8 : kind === "2" ? 9 : kind === "u" ? 10 : 0;
    if (fieldCount === 0) {
      continue;
    }
    const fields = splitFields(entry, fieldCount);
    if (fields === null) {
      continue;
    }
    const [head, path] = fields;
    const xy = head[1] ?? "..";
    const x = xy.slice(0, 1);
    const y = xy.slice(1, 2);
    let previousPath: string | undefined;
    if (kind === "2") {
      index += 1;
      previousPath = tokens[index] ?? undefined;
    }
    if (kind === "u") {
      unstaged.push({ path, code: "U" });
      continue;
    }
    if (x !== ".") {
      const code = statusCode(x);
      if (code !== null) {
        staged.push(previousPath === undefined ? { path, code } : { path, previousPath, code });
      }
    }
    if (y !== ".") {
      const code = statusCode(y);
      if (code !== null) {
        unstaged.push({ path, code });
      }
    }
  }
  return { unstaged, staged };
}

/** Split `count` space-separated fields off the front; the rest is the path. */
function splitFields(entry: string, count: number): [string[], string] | null {
  const head: string[] = [];
  let rest = entry;
  for (let taken = 0; taken < count; taken += 1) {
    const space = rest.indexOf(" ");
    if (space < 0) {
      return null;
    }
    head.push(rest.slice(0, space));
    rest = rest.slice(space + 1);
  }
  return rest === "" ? null : [head, rest];
}

/** Parse `git for-each-ref --format=%(refname:short)` output. */
export function parseRefs(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** How much of a log to read: the page size and how many commits to skip. */
export interface LogWindow {
  readonly limit: number;
  readonly skip?: number;
}

/**
 * Read one page of commits, newest first, for a revision range.
 *
 * `range` is passed through verbatim (`main..HEAD`, `main`, or nothing for
 * HEAD); the trailing `--` keeps a branch that shares a file's name from
 * being read as a path.
 */
export function readLog(git: GitRunner, range: readonly string[], window: LogWindow): Commit[] {
  const args = ["log", "--no-decorate", LOG_FORMAT, "-n", String(window.limit)];
  if (window.skip !== undefined && window.skip > 0) {
    args.push(`--skip=${window.skip}`);
  }
  const result = git([...args, ...range, "--"]);
  return result.ok ? parseLog(result.stdout) : [];
}

/**
 * "Not on any remote": the revision arguments that leave only the commits
 * no remote-tracking ref reaches. Not `@{upstream}..HEAD` — after a rebase
 * onto a pushed base that range holds the base's own pushed commits, a
 * branch pushed without `-u` has no upstream at all, and a branch pushed
 * to a second remote is on that remote. With no remotes configured every
 * commit is unpushed, which is also what this reports.
 */
export const NOT_ON_ANY_REMOTE: readonly string[] = ["--not", "--remotes"];

/** The shas on HEAD that no remote-tracking ref has — what the commits panel marks `↑`. */
export function unpushedShas(git: GitRunner): Set<string> {
  const result = git(["rev-list", "HEAD", ...NOT_ON_ANY_REMOTE, "--"]);
  if (!result.ok) {
    return new Set();
  }
  return new Set(
    result.stdout
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== ""),
  );
}

/** The working tree's status, or null when git could not report it. */
export function readStatus(git: GitRunner): Status | null {
  const result = git(["status", "--porcelain=v2", "-z", "--untracked-files=all"]);
  return result.ok ? parseStatusV2(result.stdout) : null;
}

/** A branch name resolved to the ref git should be given, and its sha. */
export interface ResolvedBranch {
  /** `name` when a local branch exists, else `<remote>/<name>`. */
  readonly target: string;
  readonly sha: string;
}

/**
 * Resolve a branch name to a local branch first, then to a remote-tracking
 * one (`origin` and `upstream` ahead of the rest, alphabetically after) —
 * the same rule Collins applies in gitinfo.resolve_branch, so both sides
 * mean the same commit by "main".
 */
export function resolveBranch(git: GitRunner, name: string): ResolvedBranch | null {
  const ref = safeRef(name);
  if (ref === null) {
    return null;
  }
  const local = revParse(git, `refs/heads/${ref}`);
  if (local !== null) {
    return { target: ref, sha: local };
  }
  for (const remote of remotesInOrder(git)) {
    const sha = revParse(git, `refs/remotes/${remote}/${ref}`);
    if (sha !== null) {
      return { target: `${remote}/${ref}`, sha };
    }
  }
  return null;
}

function remotesInOrder(git: GitRunner): string[] {
  const result = git(["remote"]);
  if (!result.ok) {
    return [];
  }
  const remotes = parseRefs(result.stdout);
  const rank = (remote: string): number => {
    const index = REMOTE_ORDER.indexOf(remote);
    return index < 0 ? REMOTE_ORDER.length : index;
  };
  return remotes.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** `git rev-parse --verify --quiet <rev>` as a sha, or null. */
export function revParse(git: GitRunner, rev: string): string | null {
  const result = git(["rev-parse", "--verify", "--quiet", rev]);
  const sha = result.stdout.trim();
  return result.ok && /^[0-9a-f]{40}$/.test(sha) ? sha : null;
}

/** The checked-out branch, or "HEAD" when detached. */
export function currentBranch(git: GitRunner): string {
  const result = git(["symbolic-ref", "--short", "-q", "HEAD"]);
  const name = result.stdout.trim();
  return result.ok && name !== "" ? name : "HEAD";
}

/**
 * The working tree's top-level directory (absolute), or null outside a
 * repository. Every other call here should run in it: the paths hunk
 * reports and `git status` prints are relative to the top, while a pathspec
 * given to `git diff -- <path>` or `git add -- <path>` is read relative to
 * the process's cwd — so from a subdirectory (an agent that `cd`'d into
 * `packages/foo`, which is where hunk starts) the same paths name nothing.
 */
export function repoToplevel(git: GitRunner): string | null {
  const result = git(["rev-parse", "--show-toplevel"]);
  const top = result.stdout.trim();
  return result.ok && top !== "" ? top : null;
}

/** The repository's directory name — what hunk puts in front of its titles. */
export function repoName(git: GitRunner): string {
  const top = repoToplevel(git);
  return top === null ? "" : basename(top);
}

/** The first parent of a commit, or null for a root commit (or an unknown one). */
export function parentOf(git: GitRunner, sha: string): string | null {
  return revParse(git, `${sha}^`);
}

/**
 * The default branch when nobody told us: `origin/HEAD`'s target, else
 * `main`, else `master`, else null.
 */
export function guessDefault(git: GitRunner): string | null {
  const head = git(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"]);
  const name = head.stdout.trim();
  if (head.ok && name !== "") {
    return name.startsWith("origin/") ? name.slice("origin/".length) : name;
  }
  for (const candidate of ["main", "master"]) {
    if (revParse(git, `refs/heads/${candidate}`) !== null) {
      return candidate;
    }
  }
  return null;
}

/** Every local branch, sorted by name. */
export function localBranches(git: GitRunner): string[] {
  const result = git(["for-each-ref", "--format=%(refname:short)", "--sort=refname", "refs/heads"]);
  return result.ok ? parseRefs(result.stdout) : [];
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

/** `git add -A`: stage everything, untracked files included. */
export function stageAll(git: GitRunner): GitResult {
  return git(["add", "-A"]);
}

/** `git reset -q`: the index back to HEAD, the working tree untouched. */
export function unstageAll(git: GitRunner): GitResult {
  return git(["reset", "-q"]);
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

/** The markers git leaves in its directory while an operation waits on the user. */
const IN_PROGRESS_MARKERS: readonly (readonly [string, string])[] = [
  ["rebase-merge", "a rebase"],
  ["rebase-apply", "a rebase"],
  ["MERGE_HEAD", "a merge"],
  ["CHERRY_PICK_HEAD", "a cherry-pick"],
  ["REVERT_HEAD", "a revert"],
];

/**
 * What is half-finished in this repository — "a rebase", "a merge", "a
 * cherry-pick" or "a revert" — or null when nothing is. A commit made
 * while one waits would be that operation's next step, not the user's,
 * so the commit keys ask this before they ask for a message.
 */
export function inProgressOperation(git: GitRunner): string | null {
  const dir = gitDir(git);
  if (dir === null) {
    return null;
  }
  for (const [marker, name] of IN_PROGRESS_MARKERS) {
    if (existsSync(join(dir, marker))) {
      return name;
    }
  }
  return null;
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

/** The paths the index differs from HEAD in — what a commit now would carry. */
export function stagedPaths(git: GitRunner): string[] {
  const result = git(["diff", "--cached", "--name-only", "-z"]);
  if (!result.ok) {
    return [];
  }
  return result.stdout.split("\0").filter((path) => path !== "");
}

/**
 * `git commit -q -m <summary> [-m <body>]`: git joins the two with a blank
 * line, and `-m` means no editor is ever opened on a terminal the renderer
 * owns. Hooks and signing run, hence the longer timeout — and the runner
 * is the caller's choice, so the composition root hands in the
 * asynchronous one (the tests, the synchronous one).
 */
export function commit<R>(git: AnyGitRunner<R>, summary: string, body?: string): R {
  const message = body === undefined || body === "" ? ["-m", summary] : ["-m", summary, "-m", body];
  return git(["commit", "-q", ...message], undefined, { timeoutMs: COMMIT_TIMEOUT_MS });
}

/**
 * Commit the index as a fixup of `sha`. The subject is `fixup! <full sha>`
 * rather than `--fixup=`'s copy of the target's title: titles repeat,
 * hashes do not, and `rebase --autosquash` matches either form.
 */
export function commitFixup<R>(git: AnyGitRunner<R>, sha: string): R {
  return git(["commit", "-q", "-m", `fixup! ${sha}`], undefined, { timeoutMs: COMMIT_TIMEOUT_MS });
}

/**
 * The commits `F` may fold into, newest first: those in `range` (the
 * commits panel's current group, `<parent>..HEAD`) that no remote-tracking
 * ref reaches — the only ones a fixup may target without rewriting what
 * somebody else has. The parent's own commits are out whether pushed or
 * not (a fixup for one of those belongs on the parent branch); the rest
 * are filtered by NOT_ON_ANY_REMOTE, which is exactly what the panel's
 * `↑` marks.
 */
export function unpushedCommits(git: GitRunner, range: readonly string[], limit: number): Commit[] {
  return readLog(git, [...range, ...NOT_ON_ANY_REMOTE], { limit });
}
