// New in the ghackett fork of agent-session-manager (GPL-3.0).
// Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
// contributors); see collins/THIRD_PARTY_LICENSES.md.

/**
 * A minimal parser and writer for single-file git patches, trimmed from
 * hunk-commit's `src/patch/*` to what staging one hunk needs.
 *
 * Hunk already parses these patches for rendering and reports each hunk's
 * index and line spans, but a summary is not enough to *rebuild* a patch
 * with one hunk in it: that needs the hunk's actual lines. So the text is
 * parsed again here, and the two parses are cross-checked
 * (`findDisagreement`) before anything is written to the index.
 */

export type PatchLineKind = "context" | "added" | "removed";

export interface PatchLine {
  readonly kind: PatchLineKind;
  /** Line content with the diff marker stripped and no trailing newline. */
  readonly text: string;
  /** True when `\ No newline at end of file` followed this line. */
  readonly noNewlineAtEof: boolean;
}

export interface PatchHunk {
  readonly index: number;
  /** Whatever git wrote after the closing `@@`, including its leading space. */
  readonly heading: string;
  readonly oldStart: number;
  readonly oldCount: number;
  readonly newStart: number;
  readonly newCount: number;
  readonly lines: readonly PatchLine[];
}

export type FileChangeKind = "added" | "deleted" | "renamed" | "modified";

export interface FilePatch {
  /**
   * The patch's own header lines, verbatim, so a patch written back out
   * keeps its modes, blob hashes and rename records — details this parser
   * has no reason to understand but `git apply` does.
   */
  readonly headerLines: readonly string[];
  /** The file's new-side path, or its old path when the file was deleted. */
  readonly path: string;
  /** The old path, present only for a rename. */
  readonly previousPath?: string;
  readonly change: FileChangeKind;
  /** Every file mode the header names, both sides, in the order named. */
  readonly declaredModes: readonly string[];
  /** True when the patch carries no usable text hunks because the file is binary. */
  readonly binary: boolean;
  readonly hunks: readonly PatchHunk[];
}

export class PatchParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PatchParseError";
  }
}

const HUNK_HEADER = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/;
const DEV_NULL = "/dev/null";

/** Strip git's `a/` or `b/` prefix from a `---`/`+++` path. */
function stripPathPrefix(value: string): string {
  if (value === DEV_NULL) {
    return DEV_NULL;
  }
  return value.startsWith("a/") || value.startsWith("b/") ? value.slice(2) : value;
}

interface Header {
  oldPath: string;
  newPath: string;
  /** Paths recovered from the `diff --git` line, used when there are no `---`/`+++` lines. */
  gitLinePaths?: { old: string; new: string };
  renamedFrom?: string;
  renamedTo?: string;
  explicitChange?: "added" | "deleted";
  modes: string[];
  binary: boolean;
  /** Index of the first line that is not part of the header. */
  bodyStart: number;
}

/**
 * Recover both paths from a `diff --git a/x b/y` line — the one place they
 * still appear in a binary patch, which has no `---`/`+++` lines. The form
 * is ambiguous for paths containing " b/", so prefer a split where both
 * sides agree (only a rename makes them differ).
 */
function parseGitLinePaths(rest: string): { old: string; new: string } | undefined {
  const candidates: { old: string; new: string }[] = [];
  for (let index = rest.indexOf(" b/"); index !== -1; index = rest.indexOf(" b/", index + 1)) {
    if (!rest.startsWith("a/")) {
      break;
    }
    candidates.push({ old: rest.slice(2, index), new: rest.slice(index + 3) });
  }
  return candidates.find((candidate) => candidate.old === candidate.new) ?? candidates[0];
}

/** Note a mode named by a header line, ignoring anything that is not one. */
function recordMode(header: Header, value: string): void {
  const mode = value.trim();
  if (/^\d{6}$/.test(mode)) {
    header.modes.push(mode);
  }
}

function parseHeader(lines: readonly string[]): Header {
  const header: Header = { oldPath: "", newPath: "", modes: [], binary: false, bodyStart: lines.length };
  for (const [index, line] of lines.entries()) {
    if (HUNK_HEADER.test(line)) {
      header.bodyStart = index;
      break;
    }
    if (line.startsWith("diff --git ")) {
      header.gitLinePaths = parseGitLinePaths(line.slice("diff --git ".length));
    } else if (line.startsWith("--- ")) {
      header.oldPath = stripPathPrefix(line.slice(4));
    } else if (line.startsWith("+++ ")) {
      header.newPath = stripPathPrefix(line.slice(4));
    } else if (line.startsWith("rename from ")) {
      header.renamedFrom = line.slice("rename from ".length);
    } else if (line.startsWith("rename to ")) {
      header.renamedTo = line.slice("rename to ".length);
    } else if (line.startsWith("new file mode")) {
      header.explicitChange = "added";
      recordMode(header, line.slice("new file mode".length));
    } else if (line.startsWith("deleted file mode")) {
      header.explicitChange = "deleted";
      recordMode(header, line.slice("deleted file mode".length));
    } else if (line.startsWith("old mode ")) {
      recordMode(header, line.slice("old mode ".length));
    } else if (line.startsWith("new mode ")) {
      recordMode(header, line.slice("new mode ".length));
    } else if (line.startsWith("index ")) {
      // `index <old>..<new> <mode>`; the mode appears only when it did not change.
      recordMode(header, line.slice("index ".length).split(" ").slice(1).join(" "));
    } else if (line.startsWith("Binary files ") || line.startsWith("GIT binary patch")) {
      header.binary = true;
    }
  }
  return header;
}

function resolvePaths(header: Header): Pick<FilePatch, "path" | "previousPath" | "change"> {
  const renamed = header.renamedFrom !== undefined && header.renamedTo !== undefined;
  const oldPath = header.oldPath || header.gitLinePaths?.old || "";
  const newPath = header.newPath || header.gitLinePaths?.new || "";
  const path = renamed ? header.renamedTo! : newPath !== DEV_NULL && newPath !== "" ? newPath : oldPath;
  const change: FileChangeKind = header.explicitChange
    ? header.explicitChange
    : renamed
      ? "renamed"
      : oldPath === DEV_NULL
        ? "added"
        : newPath === DEV_NULL
          ? "deleted"
          : "modified";
  return renamed ? { path, previousPath: header.renamedFrom, change } : { path, change };
}

/**
 * Read one hunk's body, consuming exactly the line counts its header
 * declares — counting, rather than pattern-matching, is what keeps an empty
 * context line (written as `""` by some emitters) from ending the hunk.
 */
function parseHunkBody(
  lines: readonly string[],
  start: number,
  oldCount: number,
  newCount: number,
): { lines: PatchLine[]; next: number } {
  const body: PatchLine[] = [];
  let remainingOld = oldCount;
  let remainingNew = newCount;
  let cursor = start;
  while ((remainingOld > 0 || remainingNew > 0) && cursor < lines.length) {
    const line = lines[cursor] ?? "";
    cursor += 1;
    const marker = line.slice(0, 1);
    const text = line.slice(1);
    if (marker === "+") {
      body.push({ kind: "added", text, noNewlineAtEof: false });
      remainingNew -= 1;
    } else if (marker === "-") {
      body.push({ kind: "removed", text, noNewlineAtEof: false });
      remainingOld -= 1;
    } else if (marker === " " || line === "") {
      body.push({ kind: "context", text, noNewlineAtEof: false });
      remainingOld -= 1;
      remainingNew -= 1;
    } else if (marker === "\\") {
      markPreviousLineWithoutNewline(body);
    } else {
      throw new PatchParseError(`Unexpected line in hunk body: ${JSON.stringify(line)}`);
    }
  }
  // A `\ No newline` marker for the hunk's last line sits past the counted lines.
  if (cursor < lines.length && (lines[cursor] ?? "").startsWith("\\")) {
    markPreviousLineWithoutNewline(body);
    cursor += 1;
  }
  if (remainingOld > 0 || remainingNew > 0) {
    throw new PatchParseError("Hunk body ended before its declared line counts were satisfied");
  }
  return { lines: body, next: cursor };
}

function markPreviousLineWithoutNewline(body: PatchLine[]): void {
  const previous = body[body.length - 1];
  if (previous) {
    body[body.length - 1] = { ...previous, noNewlineAtEof: true };
  }
}

/** Parse one file's patch text. Throws `PatchParseError` on anything unrecognised. */
export function parseFilePatch(patchText: string): FilePatch {
  const lines = patchText.split("\n");
  const header = parseHeader(lines);
  const hunks: PatchHunk[] = [];
  let cursor = header.bodyStart;
  while (cursor < lines.length) {
    const match = HUNK_HEADER.exec(lines[cursor] ?? "");
    if (!match) {
      cursor += 1;
      continue;
    }
    const oldStart = Number(match[1]);
    const oldCount = match[2] === undefined ? 1 : Number(match[2]);
    const newStart = Number(match[3]);
    const newCount = match[4] === undefined ? 1 : Number(match[4]);
    const body = parseHunkBody(lines, cursor + 1, oldCount, newCount);
    hunks.push({
      index: hunks.length,
      heading: match[5] ?? "",
      oldStart,
      oldCount,
      newStart,
      newCount,
      lines: body.lines,
    });
    cursor = body.next;
  }
  return {
    ...resolvePaths(header),
    headerLines: lines.slice(0, header.bodyStart),
    declaredModes: header.modes,
    binary: header.binary,
    hunks,
  };
}

/** One hunk's inclusive line span on one side, using hunk's own convention. */
export function hunkRange(hunk: PatchHunk, side: "old" | "new"): [number, number] {
  const start = side === "new" ? hunk.newStart : hunk.oldStart;
  const count = side === "new" ? hunk.newCount : hunk.oldCount;
  return [start, start + Math.max(count, 1) - 1];
}

/** The lines this hunk expects to find on one side of the diff. */
export function hunkSideLines(hunk: PatchHunk, side: "old" | "new"): string[] {
  const excluded: PatchLineKind = side === "new" ? "removed" : "added";
  return hunk.lines.filter((line) => line.kind !== excluded).map((line) => line.text);
}

const MARKER: Record<PatchLineKind, string> = { context: " ", added: "+", removed: "-" };
const NO_NEWLINE_MARKER = "\\ No newline at end of file";

function renderHunkHeader(hunk: PatchHunk, newStart: number): string {
  const oldSpan = hunk.oldCount === 1 ? `${hunk.oldStart}` : `${hunk.oldStart},${hunk.oldCount}`;
  const newSpan = hunk.newCount === 1 ? `${newStart}` : `${newStart},${hunk.newCount}`;
  return `@@ -${oldSpan} +${newSpan} @@${hunk.heading}`;
}

function renderHunkBody(hunk: PatchHunk): string[] {
  return hunk.lines.flatMap((line) =>
    line.noNewlineAtEof
      ? [`${MARKER[line.kind]}${line.text}`, NO_NEWLINE_MARKER]
      : [`${MARKER[line.kind]}${line.text}`],
  );
}

/** How many lines a hunk adds to the file it applies to. */
function lineDelta(hunk: PatchHunk): number {
  return hunkSideLines(hunk, "new").length - hunkSideLines(hunk, "old").length;
}

/**
 * Render a patch carrying only the selected hunks.
 *
 * Dropping a hunk shifts every later hunk's position in the *new* file, so
 * new-side starts are renumbered by the running delta of what was left out.
 * `git apply` would tolerate stale numbers, but a patch that describes a
 * file it does not produce is a patch nothing else can trust. Null when
 * nothing is selected.
 */
export function writeSelectedHunks(patch: FilePatch, selected: ReadonlySet<number>): string | null {
  const lines = [...patch.headerLines];
  let hasSelection = false;
  let droppedDelta = 0;
  for (const hunk of patch.hunks) {
    if (!selected.has(hunk.index)) {
      droppedDelta += lineDelta(hunk);
      continue;
    }
    hasSelection = true;
    lines.push(renderHunkHeader(hunk, hunk.newStart - droppedDelta), ...renderHunkBody(hunk));
  }
  return hasSelection ? `${lines.join("\n")}\n` : null;
}

/** One hunk as hunk itself reports it (`ExtensionDiffHunk`), for cross-checking. */
export interface HostHunk {
  readonly index: number;
  readonly newRange?: readonly [number, number];
}

/**
 * Check that this parse of a patch agrees with hunk's: the same count, and
 * the same new-side span per index. If the two ever disagreed about what
 * hunk 2 is, staging hunk 2 would stage something else — so ask, and refuse
 * when the answer is no. Null when they agree.
 */
export function findDisagreement(patch: FilePatch, hostHunks: readonly HostHunk[]): string | null {
  if (patch.hunks.length !== hostHunks.length) {
    return `hunk sees ${hostHunks.length} hunk(s) where the patch has ${patch.hunks.length}`;
  }
  for (const hunk of patch.hunks) {
    const host = hostHunks[hunk.index];
    if (!host?.newRange) {
      continue;
    }
    const [start, end] = hunkRange(hunk, "new");
    if (host.newRange[0] !== start || host.newRange[1] !== end) {
      return `hunk ${hunk.index + 1} spans lines ${start}-${end} in the patch but ${host.newRange[0]}-${host.newRange[1]} in hunk`;
    }
  }
  return null;
}

/** The modes a patch may name: a regular file, executable or not. */
const REGULAR_MODES = new Set(["100644", "100755"]);

const MODE_NAMES: Readonly<Record<string, string>> = {
  "120000": "a symbolic link",
  "160000": "a submodule",
  "040000": "a directory",
  "040755": "a directory",
};

/**
 * Why the file this patch describes cannot be staged by the hunk, or null
 * when it can. An allowlist: a symlink or submodule arrives as ordinary
 * text hunks and neither is a text file, so partial staging of one is not
 * something this code has reasoned about.
 */
export function unsupportedModeReason(modes: readonly string[]): string | null {
  for (const mode of modes) {
    if (REGULAR_MODES.has(mode)) {
      continue;
    }
    const name = MODE_NAMES[mode];
    return name === undefined
      ? `it has an unrecognised file mode (${mode})`
      : `it is ${name}, which hunks cannot describe`;
  }
  return null;
}

/** Read a path the way both POSIX and Windows would: either slash separates. */
function segments(path: string): readonly string[] {
  return path.split(/[/\\]/);
}

/**
 * Why this path must not be touched, or null when it is safe. Every path
 * here is parsed out of patch text; git never emits an absolute path or a
 * `..` segment for a working copy, so refusing them costs nothing.
 */
export function unsafePathReason(path: string): string | null {
  if (path === "") {
    return "the patch does not name a file";
  }
  if (path.startsWith("/") || path.startsWith("\\") || /^[A-Za-z]:/.test(path)) {
    return "it is an absolute path";
  }
  if (segments(path).includes("..")) {
    return "it points outside the repository with a `..` segment";
  }
  return null;
}
