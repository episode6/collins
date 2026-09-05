// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SIDECAR_VERSION, configFromEnv, readSidecar, writeSidecar } from "../sidecar.ts";

let dir: string;
let path: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "collins-git-sidecar-"));
  path = join(dir, "collins", "git-1-1.json");
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

/** What Collins writes before spawning hunk (hunkctl.sidecar_payload). */
const collinsWrote = { version: 2, untracked: true };

describe("readSidecar", () => {
  test("reads what Collins wrote", () => {
    writeFileSync(path.replace("collins/", ""), JSON.stringify(collinsWrote), "utf8");
    expect(readSidecar(path.replace("collins/", ""))).toEqual({ untracked: true });
  });

  test("untracked is false only when Collins says so; absent or garbled reads as true", () => {
    const at = path.replace("collins/", "");
    writeFileSync(at, JSON.stringify({ ...collinsWrote, untracked: false }), "utf8");
    expect(readSidecar(at)?.untracked).toBe(false);
    writeFileSync(at, JSON.stringify({ version: 2 }), "utf8");
    expect(readSidecar(at)?.untracked).toBe(true);
    writeFileSync(at, JSON.stringify({ untracked: "false" }), "utf8");
    expect(readSidecar(at)?.untracked).toBe(true);
    writeFileSync(at, JSON.stringify({ untracked: 0 }), "utf8");
    expect(readSidecar(at)?.untracked).toBe(true);
  });

  test("is tolerant: missing file, garbage, wrong shapes", () => {
    expect(readSidecar(path)).toBeNull();
    const at = path.replace("collins/", "");
    writeFileSync(at, "{not json", "utf8");
    expect(readSidecar(at)).toBeNull();
    writeFileSync(at, JSON.stringify([1, 2]), "utf8");
    expect(readSidecar(at)).toBeNull();
    writeFileSync(at, JSON.stringify({ parent: "main", level: "files" }), "utf8"); // v1 keys: ignored
    expect(readSidecar(at)).toEqual({ untracked: true });
  });
});

describe("writeSidecar", () => {
  test("merges into what is there, keeping Collins' keys and unknown ones, and stamps the version", () => {
    expect(SIDECAR_VERSION).toBe(2);
    writeSidecar(path, {});
    writeFileSync(path, JSON.stringify({ ...collinsWrote, future: true }), "utf8");
    expect(writeSidecar(path, { selection: { path: "a.txt", hunkIndex: 1 } })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({
      ...collinsWrote,
      future: true,
      selection: { path: "a.txt", hunkIndex: 1 },
    });
    expect(writeSidecar(path, { anchor: { path: "a.txt", side: "new", line: 4 } })).toBe(true);
    expect(writeSidecar(path, { refreshed: { index: "1756800000123456789", head: "a".repeat(40) } })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({
      version: 2,
      untracked: true,
      future: true,
      selection: { path: "a.txt", hunkIndex: 1 },
      anchor: { path: "a.txt", side: "new", line: 4 },
      refreshed: { index: "1756800000123456789", head: "a".repeat(40) },
    });
  });

  test("null clears a key rather than dropping it, so Collins reads 'no selection' and 'no anchor'", () => {
    writeSidecar(path, { selection: { path: "a.txt", hunkIndex: null }, anchor: { path: "a.txt", side: "old", line: 2 } });
    expect(writeSidecar(path, { selection: null, anchor: null })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({ version: 2, selection: null, anchor: null });
  });

  test("creates the directory and the file when there is none, and leaves no temp file", () => {
    expect(writeSidecar(path, { selection: null })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({ selection: null, version: 2 });
    expect(readdirSync(join(dir, "collins"))).toEqual(["git-1-1.json"]);
  });

  test("reports failure instead of throwing", () => {
    expect(writeSidecar(join(dir, "not-a-dir.txt", "x.json"), {})).toBe(true);
    writeFileSync(join(dir, "file"), "", "utf8");
    expect(writeSidecar(join(dir, "file", "x.json"), {})).toBe(false);
  });
});

describe("configFromEnv", () => {
  test("the path comes from COLLINS_GIT_STATE alone", () => {
    expect(configFromEnv({ COLLINS_GIT_STATE: "/run/x.json" })).toEqual({ path: "/run/x.json" });
    expect(configFromEnv({ COLLINS_GIT_STATE: "  " })).toEqual({ path: null });
    expect(configFromEnv({})).toEqual({ path: null });
  });
});
