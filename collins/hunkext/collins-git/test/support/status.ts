// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from joshedler/hunk-git-lite (MIT, © 2026 Josh Edler);
// see collins/THIRD_PARTY_LICENSES.md.

/**
 * `git status --porcelain=v2 -z` as the integration tests read it back to
 * check what a key did to the index and the working tree. Test support
 * only: the extension itself no longer reads status (Collins' native
 * files list does, through collins/gitmodel.py's port of this parser).
 */

import type { GitRunner } from "../../git.ts";

export type StatusCode = "M" | "A" | "D" | "R" | "?" | "T" | "U" | "C";

export interface StatusRow {
  readonly path: string;
  readonly previousPath?: string;
  readonly code: StatusCode;
}

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

/** Parse `git status --porcelain=v2 -z --untracked-files=all` (entries `1`, `2`, `u`, `?`; `!` skipped). */
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

/** The working tree's status, or null when git could not report it. */
export function readStatus(git: GitRunner): Status | null {
  const result = git(["status", "--porcelain=v2", "-z", "--untracked-files=all"]);
  return result.ok ? parseStatusV2(result.stdout) : null;
}
