// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
// contributors); see collins/THIRD_PARTY_LICENSES.md.

/** A throwaway git repository for the integration tests. */

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { gitRunner, type GitRunner } from "../../git.ts";

export interface TestGitRepository {
  readonly root: string;
  readonly git: GitRunner;
  /** Run git and return stdout; throws on failure (fixtures must not fail quietly). */
  run(...args: string[]): string;
  write(path: string, content: string): void;
  read(path: string): string;
  dispose(): void;
}

/** Whether a `git` binary is available; the integration tests skip without one. */
export function hasGit(): boolean {
  try {
    execFileSync("git", ["--version"], { stdio: ["ignore", "pipe", "pipe"] });
    return true;
  } catch {
    return false;
  }
}

export function createTestGitRepository(): TestGitRepository {
  const root = mkdtempSync(join(tmpdir(), "collins-git-test-"));
  const git = gitRunner(root);
  const repository: TestGitRepository = {
    root,
    git,
    run(...args) {
      const result = git(args);
      if (!result.ok) {
        throw new Error(`git ${args.join(" ")} failed: ${result.stderr}`);
      }
      return result.stdout;
    },
    write(path, content) {
      const absolute = join(root, path);
      mkdirSync(dirname(absolute), { recursive: true });
      writeFileSync(absolute, content, "utf8");
    },
    read: (path) => readFileSync(join(root, path), "utf8"),
    dispose: () => rmSync(root, { recursive: true, force: true }),
  };
  repository.run("init", "-q", "-b", "main", ".");
  repository.run("config", "user.email", "test@example.com");
  repository.run("config", "user.name", "Test");
  repository.run("config", "commit.gpgsign", "false");
  return repository;
}
