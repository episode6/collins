// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * The sidecar file Collins and this extension share (contract version 2).
 *
 * Hunk has no way to hand an extension parameters from the command line,
 * and `[extension.collins-git]` config only comes from config files. So
 * Collins writes a small JSON file before spawning hunk and names it in
 * `COLLINS_GIT_STATE`; both sides read and write it (whole-file writes via
 * a temp file and rename, read-merge-write so the other side's keys
 * survive):
 *
 *     {"version": 2, "untracked": true}
 *
 * `untracked` is Collins' word for whether the working-tree reviews it
 * loads include untracked files (absent or garbled reads as true). The
 * rest of the file is this extension's, written for Collins' native
 * panels to read on their poll:
 *
 * - `selection` — `{"path": "<path>", "hunkIndex": n | null}`, the file
 *   and hunk under hunk's cursor, or `null` when the cursor is on no
 *   file; rewritten on every `selection_changed` (and every changeset
 *   event) whose answer differs from the last written one.
 * - `anchor` — `{"path": "<path>", "side": "old" | "new", "line": n}`,
 *   the line `v` was pressed on, or `null` once cleared.
 * - `refreshed` — `{"index": "<mtime ns>", "head": "<sha>"}`, the index
 *   mtime and HEAD (git.ts's TreeMark) observed right after reloading
 *   the review for a mutation of ours. Collins' freshness poll reloads
 *   the page when the index or HEAD moves; a move that matches this
 *   record is one hunk has already shown, and a reload for it would only
 *   cancel whatever dialog the user has open by then.
 *
 * Readers tolerate a missing or garbled file, and unknown keys pass
 * through untouched.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { TreeMark } from "./git.ts";
import type { PatchSide } from "./range.ts";

export interface SidecarConfig {
  /** Whether working-tree reviews include untracked files (Collins' load flag; informational here). */
  readonly untracked: boolean;
}

export const SIDECAR_VERSION = 2;

/** The environment variable Collins sets to the sidecar's path. */
export const SIDECAR_ENV = "COLLINS_GIT_STATE";

/** Where the sidecar is, from the process environment: `{path: null}` standalone. */
export function configFromEnv(env: Record<string, string | undefined>): { path: string | null } {
  const value = env[SIDECAR_ENV];
  return { path: typeof value === "string" && value.trim() !== "" ? value : null };
}

/** The config inside a sidecar's JSON object, with every field validated. */
function configFrom(data: Record<string, unknown>): SidecarConfig {
  return { untracked: data.untracked !== false };
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

/** The file and hunk under hunk's cursor, as Collins reads it. */
export interface SidecarSelection {
  readonly path: string;
  readonly hunkIndex: number | null;
}

/** The line `v` anchored, as Collins reads it. */
export interface SidecarAnchor {
  readonly path: string;
  readonly side: PatchSide;
  readonly line: number;
}

/** What this extension writes: the cursor, the anchor and the freshness record. `null` clears a key. */
export interface SidecarPatch {
  readonly selection?: SidecarSelection | null;
  readonly anchor?: SidecarAnchor | null;
  readonly refreshed?: TreeMark;
}

/**
 * Write a patch into the sidecar, keeping every key the patch does not
 * name (Collins' `untracked`, anything newer). Atomic: a temp file beside
 * the target, then a rename. Returns false when it could not.
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
