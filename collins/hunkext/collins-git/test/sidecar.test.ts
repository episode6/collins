// New in the ghackett fork of agent-session-manager (GPL-3.0).

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { GitRunner } from "../git.ts";
import { configFromEnv, effectiveConfig, readSidecar, writeSidecar } from "../sidecar.ts";

let dir: string;
let path: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "collins-git-sidecar-"));
  path = join(dir, "collins", "git-1-1.json");
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

const collinsWrote = { version: 1, parent: "main", parentSource: "auto" as const, default: "main", logPage: 20 };

/** A git that knows one local branch, `develop`, and nothing about origin. */
const git: GitRunner = (args) => {
  if (args[0] === "symbolic-ref") {
    return { ok: false, stdout: "", stderr: "" };
  }
  if (args[0] === "rev-parse" && args[3] === "refs/heads/develop") {
    return { ok: true, stdout: "a".repeat(40) + "\n", stderr: "" };
  }
  return { ok: false, stdout: "", stderr: "fatal: nope" };
};

describe("readSidecar", () => {
  test("reads what Collins wrote", () => {
    writeSidecar(path, collinsWrote);
    expect(readSidecar(path)).toEqual({ parent: "main", parentSource: "auto", default: "main", logPage: 20 });
  });

  test("is tolerant: missing file, garbage, wrong shapes, unsafe names, out-of-range pages", () => {
    expect(readSidecar(path)).toBeNull();
    writeFileSync(path.replace("collins/", ""), "{not json", "utf8");
    expect(readSidecar(path.replace("collins/", ""))).toBeNull();
    writeSidecar(path, {});
    writeFileSync(path, JSON.stringify([1, 2]), "utf8");
    expect(readSidecar(path)).toBeNull();
    writeFileSync(path, JSON.stringify({ parent: "-x", parentSource: "nonsense", default: "a b", logPage: 1000 }), "utf8");
    expect(readSidecar(path)).toEqual({ parent: null, parentSource: "auto", default: null, logPage: 500 });
    writeFileSync(path, JSON.stringify({ logPage: 1 }), "utf8");
    expect(readSidecar(path)?.logPage).toBe(5);
    writeFileSync(path, JSON.stringify({ logPage: "20" }), "utf8");
    expect(readSidecar(path)?.logPage).toBe(20);
  });
});

describe("writeSidecar", () => {
  test("merges into what is there, keeping the other side's keys and unknown ones", () => {
    writeSidecar(path, {});
    writeFileSync(path, JSON.stringify({ ...collinsWrote, future: true }), "utf8");
    expect(writeSidecar(path, { parent: "develop", parentSource: "user" })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({ ...collinsWrote, parent: "develop", parentSource: "user", future: true });
    expect(writeSidecar(path, { parentSource: "auto" })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toMatchObject({ parent: "develop", parentSource: "auto", version: 1 });
  });

  test("creates the directory and the file when there is none, and leaves no temp file", () => {
    expect(writeSidecar(path, { parent: "develop", parentSource: "user" })).toBe(true);
    expect(JSON.parse(readFileSync(path, "utf8"))).toEqual({ parent: "develop", parentSource: "user", version: 1 });
    const { readdirSync } = require("node:fs") as typeof import("node:fs");
    expect(readdirSync(join(dir, "collins"))).toEqual(["git-1-1.json"]);
  });

  test("reports failure instead of throwing", () => {
    expect(writeSidecar(join(dir, "not-a-dir.txt", "x.json"), {})).toBe(true);
    writeFileSync(join(dir, "file"), "", "utf8");
    expect(writeSidecar(join(dir, "file", "x.json"), {})).toBe(false);
  });
});

describe("configFromEnv and effectiveConfig", () => {
  test("the path comes from COLLINS_GIT_STATE alone", () => {
    expect(configFromEnv({ COLLINS_GIT_STATE: "/run/x.json" })).toEqual({ path: "/run/x.json" });
    expect(configFromEnv({ COLLINS_GIT_STATE: "  " })).toEqual({ path: null });
    expect(configFromEnv({})).toEqual({ path: null });
  });

  test("the sidecar's names win; without them the extension guesses", () => {
    expect(effectiveConfig({ parent: "feature", parentSource: "user", default: "trunk", logPage: 7 }, git)).toEqual({
      parent: "feature",
      parentSource: "user",
      default: "trunk",
      logPage: 7,
    });
    expect(effectiveConfig({ parent: null, parentSource: "auto", default: null, logPage: 20 }, git)).toEqual({
      parent: null,
      parentSource: "auto",
      default: null,
      logPage: 20,
    });
    const guessing: GitRunner = (args) =>
      args[0] === "rev-parse" && args[3] === "refs/heads/master"
        ? { ok: true, stdout: "b".repeat(40), stderr: "" }
        : { ok: false, stdout: "", stderr: "" };
    expect(effectiveConfig(null, guessing)).toEqual({ parent: "master", parentSource: "auto", default: "master", logPage: 20 });
  });
});
