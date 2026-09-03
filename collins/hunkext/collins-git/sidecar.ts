// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * The sidecar file Collins and this extension share.
 *
 * Hunk has no way to hand an extension parameters from the command line,
 * and `[extension.collins-git]` config only comes from config files. So
 * Collins writes a small JSON file before spawning hunk and names it in
 * `COLLINS_GIT_STATE`; both sides read and write it (whole-file writes via
 * a temp file and rename, read-merge-write so the other side's keys
 * survive):
 *
 *     {"version": 1, "parent": "main", "parentSource": "auto",
 *      "default": "main", "logPage": 20, "untracked": true}
 *
 * `parent` is the parent branch's NAME (each side resolves it itself);
 * `parentSource` says who chose it — Collins writes the auto rung, this
 * extension writes `"user"` with the user's pick, or `"auto"` to hand the
 * choice back. `default`, `logPage` and `untracked` are Collins' alone:
 * the last says whether working-tree reviews include untracked files, and
 * when it is false every `diff` tail this extension sends carries
 * `--exclude-untracked` (session.ts), so its loads agree with Collins'
 * own. Readers tolerate a missing or garbled file, and unknown keys pass
 * through untouched.
 *
 * One more key is this extension's alone: `refreshed`, the index mtime
 * and HEAD (git.ts's TreeMark) it observed right after reloading the
 * review for a mutation of its own — `{"refreshed": {"index": "<ns>",
 * "head": "<sha>"}}`. Collins' freshness poll reloads the page when the
 * index or HEAD moves; a move that matches this record is one hunk has
 * already shown, and a reload for it would only cancel whatever dialog
 * the user has open by then. And `level`: which level of the narrow
 * page's stack is shown — `"diff"`, `"files"` or `"commits"` (level.ts) —
 * rewritten whenever it changes, so Collins' header buttons can say what
 * the next step shows.
 */

import { existsSync, mkdirSync, readFileSync, renameSync, unwatchFile, watchFile, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { guessDefault, safeRef, type GitRunner, type TreeMark } from "./git.ts";
import type { Level } from "./level.ts";

export type ParentSource = "auto" | "user";

export interface SidecarConfig {
  readonly parent: string | null;
  readonly parentSource: ParentSource;
  readonly default: string | null;
  readonly logPage: number;
  /** Whether working-tree reviews include untracked files; false adds `--exclude-untracked` to every `diff` load. */
  readonly untracked: boolean;
}

export const DEFAULT_LOG_PAGE = 20;
const MIN_LOG_PAGE = 5;
const MAX_LOG_PAGE = 500;
const SIDECAR_VERSION = 1;
const WATCH_INTERVAL_MS = 2_000;

/** The environment variable Collins sets to the sidecar's path. */
export const SIDECAR_ENV = "COLLINS_GIT_STATE";

/** Where the sidecar is, from the process environment: `{path: null}` standalone. */
export function configFromEnv(env: Record<string, string | undefined>): { path: string | null } {
  const value = env[SIDECAR_ENV];
  return { path: typeof value === "string" && value.trim() !== "" ? value : null };
}

function clampLogPage(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return DEFAULT_LOG_PAGE;
  }
  return Math.min(MAX_LOG_PAGE, Math.max(MIN_LOG_PAGE, Math.floor(value)));
}

/** The config inside a sidecar's JSON object, with every field validated. */
function configFrom(data: Record<string, unknown>): SidecarConfig {
  return {
    parent: safeRef(data.parent),
    parentSource: data.parentSource === "user" ? "user" : "auto",
    default: safeRef(data.default),
    logPage: clampLogPage(data.logPage),
    untracked: data.untracked !== false,
  };
}

function readObject(path: string): Record<string, unknown> | null {
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/** Read the sidecar; null when it is missing, unreadable or not a JSON object. */
export function readSidecar(path: string): SidecarConfig | null {
  const data = readObject(path);
  return data === null ? null : configFrom(data);
}

/** What this extension writes: its share of the config, the freshness record, and the level. */
export interface SidecarPatch extends Partial<SidecarConfig> {
  readonly refreshed?: TreeMark;
  /** What a narrow page shows (level.ts): `diff`, `files` or `commits`; Collins' header buttons follow it. */
  readonly level?: Level;
}

/**
 * Write a patch into the sidecar, keeping every key the patch does not
 * name (Collins' `default` and `logPage`, anything newer). Atomic: a temp
 * file beside the target, then a rename. Returns false when it could not.
 */
export function writeSidecar(path: string, patch: SidecarPatch): boolean {
  const existing = readObject(path) ?? {};
  const merged: Record<string, unknown> = { ...existing, ...patch, version: SIDECAR_VERSION };
  const temp = join(dirname(path), `.${process.pid}-${Date.now()}.tmp`);
  try {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(temp, `${JSON.stringify(merged)}\n`, { encoding: "utf8", mode: 0o600 });
    renameSync(temp, path);
    return true;
  } catch {
    return false;
  }
}

/**
 * Watch the sidecar for Collins' rewrites (a new PR base, a default branch
 * it just learned). Polling, not inotify: the file is replaced by rename,
 * and `fs.watch` on a renamed-over path is unreliable. Returns the unwatch.
 */
export function watchSidecar(path: string, onChange: () => void): () => void {
  const listener = (current: { mtimeMs: number }, previous: { mtimeMs: number }) => {
    if (current.mtimeMs !== previous.mtimeMs) {
      onChange();
    }
  };
  watchFile(path, { interval: WATCH_INTERVAL_MS, persistent: false }, listener);
  return () => {
    unwatchFile(path, listener);
  };
}

/** Whether the sidecar exists right now (Collins may not have managed to write one). */
export function sidecarExists(path: string): boolean {
  return existsSync(path);
}

/**
 * The names the panels run with: the sidecar's when it has them, else the
 * extension's own guesses — default from `origin/HEAD` → main → master,
 * parent = default, twenty commits per page, untracked files in.
 */
export function effectiveConfig(sidecar: SidecarConfig | null, git: GitRunner): SidecarConfig {
  const defaultBranch = sidecar?.default ?? guessDefault(git);
  return {
    parent: sidecar?.parent ?? defaultBranch,
    parentSource: sidecar?.parentSource ?? "auto",
    default: defaultBranch,
    logPage: sidecar?.logPage ?? DEFAULT_LOG_PAGE,
    untracked: sidecar?.untracked ?? true,
  };
}
