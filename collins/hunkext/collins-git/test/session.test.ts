// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from sadick254/hunk-commit-log (MIT, © 2026 Sadick); see
// collins/THIRD_PARTY_LICENSES.md.

import { beforeEach, describe, expect, test } from "bun:test";
import {
  findSessionId,
  firstLine,
  load,
  onPendingChange,
  pendingLoad,
  requestLoad,
  resetForTests,
  type CommandResult,
  type CommandRunner,
} from "../session.ts";

const OWN_PID = 4242;
const SESSION_ID = "c47344a1-66d4-43b7-a898-b3058e129354";
const SHA = "73f50cf2".padEnd(40, "0");

function listing(...sessions: { pid: number; sessionId: string }[]): string {
  return JSON.stringify({ sessions });
}

const ok = (stdout = "{}"): CommandResult => ({ ok: true, stdout, stderr: "" });
const failed = (stderr: string): CommandResult => ({ ok: false, stdout: "", stderr });

interface FakeCli {
  run: CommandRunner;
  calls: string[][];
}

function fakeCli(answer: (args: readonly string[]) => CommandResult): FakeCli {
  const calls: string[][] = [];
  return {
    calls,
    run: (args) => {
      calls.push([...args]);
      return Promise.resolve(answer(args));
    },
  };
}

function daemon(options: { sessions?: string; reload?: CommandResult } = {}): FakeCli {
  return fakeCli((args) =>
    args[1] === "list" ? ok(options.sessions ?? listing({ pid: OWN_PID, sessionId: SESSION_ID })) : (options.reload ?? ok()),
  );
}

/** A daemon whose reloads finish only when the test says so. */
function deferredCli(): FakeCli & { finish: () => void } {
  const calls: string[][] = [];
  const waiting: ((value: CommandResult) => void)[] = [];
  return {
    calls,
    finish: () => {
      for (const resolve of waiting.splice(0)) {
        resolve(ok());
      }
    },
    run: (args) => {
      calls.push([...args]);
      return args[1] === "list"
        ? Promise.resolve(ok(listing({ pid: OWN_PID, sessionId: SESSION_ID })))
        : new Promise((resolve) => {
            waiting.push(resolve);
          });
    },
  };
}

function settle(): Promise<void> {
  return new Promise((resolve) => {
    setImmediate(resolve);
  });
}

function reloadedTails(calls: string[][]): string[] {
  return calls.filter((call) => call[1] === "reload").map((call) => call.slice(5).join(" "));
}

beforeEach(() => {
  resetForTests();
});

describe("findSessionId", () => {
  test("finds the window by its own pid, not by its repository", () => {
    const rows = listing({ pid: 111, sessionId: "another-window" }, { pid: OWN_PID, sessionId: SESSION_ID });
    expect(findSessionId(rows, OWN_PID)).toBe(SESSION_ID);
  });

  test("resolves to nothing for a listing that names no such window", () => {
    expect(findSessionId(listing({ pid: 111, sessionId: "other" }), OWN_PID)).toBeNull();
    expect(findSessionId(listing(), OWN_PID)).toBeNull();
    expect(findSessionId("{}", OWN_PID)).toBeNull();
    expect(findSessionId("not json", OWN_PID)).toBeNull();
    expect(findSessionId(JSON.stringify({ sessions: [{ pid: OWN_PID }] }), OWN_PID)).toBeNull();
  });
});

describe("load", () => {
  test("reloads this session with the tail, resolving the id once", async () => {
    const cli = daemon();
    const deps = { run: cli.run, pid: OWN_PID };
    expect(await load(["show", SHA], deps)).toBeNull();
    expect(await load(["diff", "--staged"], deps)).toBeNull();
    expect(cli.calls).toEqual([
      ["session", "list", "--json"],
      ["session", "reload", SESSION_ID, "--json", "--", "show", SHA],
      ["session", "reload", SESSION_ID, "--json", "--", "diff", "--staged"],
    ]);
  });

  test("a diff load carries --exclude-untracked when the deps say so; show never does", async () => {
    const cli = daemon();
    const deps = { run: cli.run, pid: OWN_PID, excludeUntracked: () => true };
    expect(await load(["diff"], deps)).toBeNull();
    expect(await load(["diff", "--staged"], deps)).toBeNull();
    expect(await load(["diff", "main...HEAD"], deps)).toBeNull();
    expect(await load(["show", SHA], deps)).toBeNull();
    expect(reloadedTails(cli.calls)).toEqual([
      "diff --exclude-untracked",
      "diff --exclude-untracked --staged",
      "diff --exclude-untracked main...HEAD",
      `show ${SHA}`,
    ]);
  });

  test("the untracked switch is read as each reload goes out; off or absent sends the bare tail", async () => {
    const cli = daemon();
    let exclude = false;
    const deps = { run: cli.run, pid: OWN_PID, excludeUntracked: () => exclude };
    expect(await load(["diff"], deps)).toBeNull();
    exclude = true;
    expect(await load(["diff"], deps)).toBeNull();
    expect(await load(["diff", "--staged"], { run: cli.run, pid: OWN_PID })).toBeNull();
    expect(reloadedTails(cli.calls)).toEqual(["diff", "diff --exclude-untracked", "diff --staged"]);
  });

  test("an unknown window is reported instead of reloading blindly", async () => {
    const cli = daemon({ sessions: listing({ pid: 111, sessionId: "other" }) });
    expect(await load(["diff"], { run: cli.run, pid: OWN_PID })).toMatch(/session daemon/);
    expect(cli.calls.map((call) => call[1])).toEqual(["list"]);
  });

  test("a refusal names git's complaint; a CLI timeout is not a failure", async () => {
    const refused = daemon({ reload: failed("`hunk diff nope` could not resolve Git revision or range\n") });
    expect(await load(["diff", "nope"], { run: refused.run, pid: OWN_PID })).toMatch(/could not resolve/);

    const slow = daemon({ reload: failed("Timed out waiting for the Hunk session daemon to respond.\n") });
    expect(await load(["show", SHA], { run: slow.run, pid: OWN_PID })).toBeNull();
  });
});

describe("requestLoad", () => {
  const quiet = () => {};

  test("a request made while one is in flight runs after it", async () => {
    const cli = deferredCli();
    const deps = { run: cli.run, pid: OWN_PID };
    requestLoad(["show", "a".repeat(40)], quiet, deps);
    await settle();
    requestLoad(["show", "b".repeat(40)], quiet, deps);
    expect(reloadedTails(cli.calls)).toEqual([`show ${"a".repeat(40)}`]);

    cli.finish();
    await settle();
    await settle();
    cli.finish();
    await settle();
    expect(reloadedTails(cli.calls)).toEqual([`show ${"a".repeat(40)}`, `show ${"b".repeat(40)}`]);
  });

  test("a burst of steps loads where the reviewer stopped, not every stop", async () => {
    const cli = deferredCli();
    const deps = { run: cli.run, pid: OWN_PID };
    requestLoad(["show", "a".repeat(40)], quiet, deps);
    await settle();
    requestLoad(["show", "b".repeat(40)], quiet, deps);
    requestLoad(["diff", "--staged"], quiet, deps);
    cli.finish();
    await settle();
    await settle();
    cli.finish();
    await settle();
    expect(reloadedTails(cli.calls)).toEqual([`show ${"a".repeat(40)}`, "diff --staged"]);
  });

  test("asking again for the load in flight costs nothing", async () => {
    const cli = deferredCli();
    const deps = { run: cli.run, pid: OWN_PID };
    requestLoad(["diff"], quiet, deps);
    await settle();
    requestLoad(["diff"], quiet, deps);
    cli.finish();
    await settle();
    await settle();
    expect(reloadedTails(cli.calls)).toEqual(["diff"]);
  });

  test("the pending load is what stepping counts from, and listeners hear it move", async () => {
    const cli = deferredCli();
    const deps = { run: cli.run, pid: OWN_PID };
    const seen: (readonly string[] | null)[] = [];
    onPendingChange(() => seen.push(pendingLoad()));

    expect(pendingLoad()).toBeNull();
    requestLoad(["show", SHA], quiet, deps);
    await settle();
    expect(pendingLoad()).toEqual(["show", SHA]);
    requestLoad(["diff"], quiet, deps);
    expect(pendingLoad()).toEqual(["diff"]);

    cli.finish();
    await settle();
    await settle();
    cli.finish();
    await settle();
    expect(pendingLoad()).toBeNull();
    expect(seen[0]).toEqual(["show", SHA]);
    expect(seen[seen.length - 1]).toBeNull();
  });

  test("a failure is reported as a warning; a timeout is not reported at all", async () => {
    const reports: string[] = [];
    const report = (message: string) => reports.push(message);
    const refused = daemon({ reload: failed("No active session matches sessionId x.\n") });
    requestLoad(["diff"], report, { run: refused.run, pid: OWN_PID });
    await settle();
    await settle();
    expect(reports).toEqual(["No active session matches sessionId x."]);

    resetForTests();
    const slow = daemon({ reload: failed("Timed out waiting for the Hunk session daemon to respond.") });
    requestLoad(["diff"], report, { run: slow.run, pid: OWN_PID });
    await settle();
    await settle();
    expect(reports).toHaveLength(1);
  });
});

describe("firstLine", () => {
  test("the first non-blank line, trimmed", () => {
    expect(firstLine("\n  error: x \nmore")).toBe("error: x");
    expect(firstLine("")).toBe("");
  });
});
