// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from sadick254/hunk-commit-log and joshedler/hunk-git-lite
// (MIT, © 2026 Sadick, © 2026 Josh Edler); see collins/THIRD_PARTY_LICENSES.md.

/**
 * Git, as the collins-git extension runs it: one runner, the parsers for the
 * porcelain it reads, and the handful of operations the panels and the
 * staging keys need.
 *
 * Everything below takes the runner as an argument rather than reaching for
 * a global, so the tests feed it a temp repository (or a canned reply) and
 * the composition root feeds it the review's cwd. Both output streams are
 * captured, never inherited: the renderer owns the terminal, and one line of
 * git chatter on stderr would corrupt the frame.
 */

import { execFileSync } from "node:child_process";
import { basename } from "node:path";

/** What one git invocation came back with. `ok` is "exit status 0". */
export interface GitResult {
  readonly ok: boolean;
  readonly stdout: string;
  readonly stderr: string;
}

/** One git invocation in a fixed working directory, with optional stdin. */
export type GitRunner = (args: readonly string[], stdin?: string) => GitResult;

const GIT_TIMEOUT_MS = 5_000;
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
  return (args, stdin) => {
    try {
      const stdout = execFileSync("git", [...args], {
        cwd,
        encoding: "utf8",
        timeout: GIT_TIMEOUT_MS,
        maxBuffer: GIT_MAX_BUFFER,
        stdio: ["pipe", "pipe", "pipe"],
        input: stdin ?? "",
      });
      return { ok: true, stdout, stderr: "" };
    } catch (error) {
      const failure = error as { stdout?: unknown; stderr?: unknown; message?: unknown };
      return {
        ok: false,
        stdout: typeof failure.stdout === "string" ? failure.stdout : "",
        stderr:
          typeof failure.stderr === "string" && failure.stderr !== ""
            ? failure.stderr
            : String(failure.message ?? error),
      };
    }
  };
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

/** The shas on HEAD that its upstream lacks; empty when there is no upstream. */
export function unpushedShas(git: GitRunner): Set<string> {
  const result = git(["rev-list", "@{upstream}..HEAD"]);
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
 * Apply a patch to the index only — `git apply --cached -p1 [--reverse] -`
 * with the patch on stdin. Reverse against a `--cached` patch is how a hunk
 * is unstaged. `-p1` is apply's default, spelled out: the patch always
 * carries `a/` and `b/` (readFilePatch), whatever the user's diff config
 * says, and the strip count has to match that rather than the config.
 */
export function applyCached(git: GitRunner, patch: string, reverse: boolean): GitResult {
  return git(["apply", "--cached", "-p1", ...(reverse ? ["--reverse"] : []), "-"], patch);
}
