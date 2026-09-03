// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from sadick254/hunk-commit-log (MIT, © 2026 Sadick); see
// collins/THIRD_PARTY_LICENSES.md.

/**
 * Loading something else into this hunk window.
 *
 * The extension API can navigate inside a loaded changeset but cannot load
 * a different one, so a click on a commit goes the long way round: run the
 * hunk CLI that is running us (`process.execPath`), find this window's
 * session id by our own pid in `session list --json`, and ask the session
 * daemon to `reload` it with a new tail. One reload runs at a time and
 * later requests coalesce to the last one, so a burst of `n` presses lands
 * on the row the reviewer stopped on rather than every row passed through.
 */

import { execFile } from "node:child_process";

/** What one CLI run came back with. */
export interface CommandResult {
  readonly ok: boolean;
  readonly stdout: string;
  readonly stderr: string;
}

/** One command run against the hunk CLI. */
export type CommandRunner = (args: readonly string[]) => Promise<CommandResult>;

/**
 * Longer than hunk's own 5 s CLI timeout: when the daemon is slow the CLI
 * gives up and says so on stderr, which we want to read rather than kill.
 */
const COMMAND_TIMEOUT_MS = 8_000;

/** What hunk's CLI prints when it stopped waiting for the daemon's answer. */
const TIMED_OUT = /Timed out waiting/i;

/**
 * Run the hunk binary that is running this extension. `process.execPath`
 * rather than a PATH lookup: the child has to speak the same session
 * protocol as the window it steers, and it is the same process image.
 */
export function hunkRunner(): CommandRunner {
  return (args) =>
    new Promise((resolve) => {
      execFile(
        process.execPath,
        [...args],
        { timeout: COMMAND_TIMEOUT_MS, maxBuffer: 8 * 1024 * 1024, encoding: "utf8" },
        (error, stdout, stderr) => {
          resolve({ ok: error === null, stdout: String(stdout ?? ""), stderr: String(stderr ?? "") });
        },
      );
    });
}

/**
 * This window's session id, out of everything the daemon has registered.
 * Every hunk viewer registers with its own pid, and an extension runs
 * inside that process, so `process.pid` names this window exactly.
 */
export function findSessionId(listing: string, pid: number): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(listing);
  } catch {
    return null;
  }
  const sessions = (parsed as { sessions?: unknown }).sessions;
  if (!Array.isArray(sessions)) {
    return null;
  }
  for (const session of sessions) {
    const record = session as { pid?: unknown; sessionId?: unknown };
    if (record.pid === pid && typeof record.sessionId === "string" && record.sessionId !== "") {
      return record.sessionId;
    }
  }
  return null;
}

/** Everything a load needs from outside itself. */
export interface SessionDeps {
  readonly run: CommandRunner;
  readonly pid: number;
  /**
   * Whether a `diff` load should leave untracked files out (Collins'
   * sidecar `untracked: false`). Read when the reload is sent, not when
   * it is queued, so a switch flipped meanwhile lands on the next load.
   */
  readonly excludeUntracked?: () => boolean;
}

export type Report = (message: string, type?: "info" | "warning" | "error") => void;

let cachedSessionId: string | null = null;

async function sessionId(deps: SessionDeps): Promise<string | null> {
  if (cachedSessionId !== null) {
    return cachedSessionId;
  }
  const listing = await deps.run(["session", "list", "--json"]);
  cachedSessionId = listing.ok ? findSessionId(listing.stdout, deps.pid) : null;
  return cachedSessionId;
}

/**
 * Ask the daemon to reload this window with `tail` (`["show", sha]`,
 * `["diff", "--staged"]`, …). A `diff` tail goes out with
 * `--exclude-untracked` right after the subcommand when the deps say so
 * (hunk resolves the option afresh on every reload, so a bare tail would
 * bring untracked files back); `show` never takes it. The tails the
 * model holds stay bare. Resolves what went wrong, or null when the
 * window followed — and also null when the CLI merely timed out waiting
 * for the daemon: the viewer reloads anyway, and `session_reload` will say
 * what it did.
 */
export async function load(tail: readonly string[], deps: SessionDeps): Promise<string | null> {
  const id = await sessionId(deps);
  if (id === null) {
    return "cannot find this hunk window in the session daemon";
  }
  const sent = tail[0] === "diff" && deps.excludeUntracked?.() === true ? ["diff", "--exclude-untracked", ...tail.slice(1)] : tail;
  const result = await deps.run(["session", "reload", id, "--json", "--", ...sent]);
  if (result.ok || TIMED_OUT.test(result.stderr)) {
    return null;
  }
  const line = firstLine(result.stderr) || firstLine(result.stdout);
  return line !== "" ? line : `cannot load ${tail.join(" ")}`;
}

/** The first non-empty line of some CLI output, trimmed. */
export function firstLine(text: string): string {
  for (const line of (text ?? "").split("\n")) {
    const trimmed = line.trim();
    if (trimmed !== "") {
      return trimmed;
    }
  }
  return "";
}

let inFlight: readonly string[] | null = null;
let queued: readonly string[] | null = null;
const pendingListeners = new Set<() => void>();

function sameTail(left: readonly string[] | null, right: readonly string[] | null): boolean {
  if (left === null || right === null) {
    return left === right;
  }
  return left.length === right.length && left.every((part, index) => part === right[index]);
}

function notifyPending(): void {
  for (const listener of pendingListeners) {
    listener();
  }
}

/**
 * The load this window is on its way to, if it is on its way anywhere.
 * Stepping counts from here rather than from what is loaded: a reload
 * takes long enough that three `n` presses arrive before the first lands.
 */
export function pendingLoad(): readonly string[] | null {
  return queued ?? inFlight;
}

/** Be told whenever `pendingLoad()` changes; returns the unsubscribe. */
export function onPendingChange(listener: () => void): () => void {
  pendingListeners.add(listener);
  return () => {
    pendingListeners.delete(listener);
  };
}

/**
 * Ask this window to load `tail`. A click is not a promise the caller can
 * await: the handler returns at once and the review arrives when the
 * daemon has rebuilt it. One reload at a time; further requests coalesce.
 */
export function requestLoad(
  tail: readonly string[],
  report: Report,
  deps: SessionDeps = { run: hunkRunner(), pid: process.pid },
): void {
  if (inFlight !== null) {
    queued = sameTail(tail, inFlight) ? null : [...tail];
    notifyPending();
    return;
  }
  inFlight = [...tail];
  notifyPending();
  void load(tail, deps).then((problem) => {
    inFlight = null;
    if (problem !== null) {
      report(problem, "warning");
    }
    const next = queued;
    queued = null;
    notifyPending();
    if (next !== null && !sameTail(next, tail)) {
      requestLoad(next, report, deps);
    }
  });
}

/** Forget the session id and any request in progress; only tests need this. */
export function resetForTests(): void {
  cachedSessionId = null;
  inFlight = null;
  queued = null;
  pendingListeners.clear();
}
