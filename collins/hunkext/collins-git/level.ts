// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * Levels: what a narrow terminal shows, and how `<` / `>` move between them.
 *
 * Hunk lays its left panes out from a fixed budget (0.20.1, src/ui/App.tsx
 * and lib/extensionPanes.ts): two columns of body padding, then each open
 * pane in registration order takes its preferred width if the review keeps
 * its 48-column minimum behind a one-column divider, else it is omitted;
 * and the whole sidebar area stays hidden until a terminal narrower than
 * 220 columns can fit hunk's own 22-column pane beside that minimum. With
 * the commits pane at 26 preferred / 18 minimum and the files pane at 30 /
 * 22, that makes three regimes by terminal width:
 *
 *   < 73 columns   nothing but the diff can be shown (`none`)
 *   73..99         one pane beside a 48-column diff (`one`)
 *   >= 100         both panes (`two`) — the wide layout, as before
 *
 * In the `one` regime the page is a stack of three levels the user walks
 * with `<` (up) and `>` (down), or Collins' header buttons, which feed the
 * same keys: `diff` (no pane open), `files` (the files pane alone) and
 * `commits` (the commits pane alone). Picking a row goes down a level by
 * itself — a commit clicked loads it and shows the files, a file clicked
 * selects it and shows the diff — so the mouse alone drills down. Both
 * panes open in this regime is the `commits` level: hunk omits the files
 * pane, so that is what shows. In the `two` regime up means "show me the
 * panels" (open both) and down does nothing; in `none` both refuse with a
 * word. Everything here is arithmetic on a width and two booleans; index.ts
 * applies the plans to `ctx.panes` and reports the level to Collins.
 */

export type Level = "diff" | "files" | "commits";

/** The levels, bottom to top. */
export const LEVELS: readonly Level[] = ["diff", "files", "commits"];

export type PaneId = "commits" | "files";

/** Which of the two panes are open (hunk's `isOpen`, hidden or not). */
export interface PaneState {
  readonly commits: boolean;
  readonly files: boolean;
}

/** Hunk 0.20.1's layout budget, in columns. */
export const BODY_PADDING = 2;
export const DIFF_MIN_WIDTH = 48;
export const PANE_DIVIDER = 1;
/** Hunk's gate for showing the sidebar area at all below 220 columns: its own pane's minimum. */
export const SIDEBAR_MIN_WIDTH = 22;
/** What this extension registers the panes with (index.ts). */
export const COMMITS_WIDTH = { preferred: 26, min: 18 } as const;
export const FILES_WIDTH = { preferred: 30, min: 22 } as const;

/** The narrowest terminal that shows one pane beside the diff. */
export const ONE_PANE_COLUMNS = BODY_PADDING + SIDEBAR_MIN_WIDTH + PANE_DIVIDER + DIFF_MIN_WIDTH;
/** The narrowest terminal that shows both panes: the commits pane at its preferred width, the files pane at its minimum. */
export const TWO_PANE_COLUMNS =
  BODY_PADDING + COMMITS_WIDTH.preferred + PANE_DIVIDER + FILES_WIDTH.min + PANE_DIVIDER + DIFF_MIN_WIDTH;

export type Fit = "none" | "one" | "two";

/** How many panes a terminal *columns* wide can show beside the diff. */
export function fit(columns: number): Fit {
  if (!Number.isFinite(columns) || columns < ONE_PANE_COLUMNS) {
    return "none";
  }
  return columns < TWO_PANE_COLUMNS ? "one" : "two";
}

/** Whether the level stack applies: anything short of both panes. */
export function isNarrow(columns: number): boolean {
  return fit(columns) !== "two";
}

/** The level a pane state shows: the commits pane wins (hunk omits the files pane beside it when only one fits). */
export function levelOf(open: PaneState): Level {
  if (open.commits) {
    return "commits";
  }
  return open.files ? "files" : "diff";
}

/** The level above *level*, or null at the top. */
export function levelUp(level: Level): Level | null {
  const at = LEVELS.indexOf(level);
  return LEVELS[at + 1] ?? null;
}

/** The level below *level*, or null at the bottom. */
export function levelDown(level: Level): Level | null {
  const at = LEVELS.indexOf(level);
  return at > 0 ? (LEVELS[at - 1] ?? null) : null;
}

/** The panes a level has open: exactly one, or none. */
export function panesFor(level: Level): PaneState {
  return { commits: level === "commits", files: level === "files" };
}

/** What to close and open to get from *open* to *target* — closes first, so the area never holds both for a frame. */
export interface Plan {
  readonly close: readonly PaneId[];
  readonly open: readonly PaneId[];
}

export const NO_CHANGE: Plan = { close: [], open: [] };

export function planPanes(open: PaneState, target: PaneState): Plan {
  const close: PaneId[] = [];
  const opens: PaneId[] = [];
  for (const id of ["commits", "files"] as const) {
    if (open[id] && !target[id]) {
      close.push(id);
    } else if (!open[id] && target[id]) {
      opens.push(id);
    }
  }
  return { close, open: opens };
}

export const BOTH_PANES: PaneState = { commits: true, files: true };

/** What a step does: change the panes, say why it cannot, or nothing at all. */
export type Step =
  | { readonly kind: "show"; readonly level: Level | "all"; readonly plan: Plan }
  | { readonly kind: "refuse"; readonly reason: string }
  | { readonly kind: "noop" };

export const TOO_NARROW = "too narrow for a panel — widen the page";
const AT_THE_TOP = "at the top — the commits are shown";
const AT_THE_BOTTOM = "at the bottom — the diff is shown";
const BOTH_SHOWN = "both panels are shown";

/** `<`: one level up, or both panes when the terminal is wide. */
export function stepUp(columns: number, open: PaneState): Step {
  const regime = fit(columns);
  if (regime === "none") {
    return { kind: "refuse", reason: TOO_NARROW };
  }
  if (regime === "two") {
    if (open.commits && open.files) {
      return { kind: "refuse", reason: BOTH_SHOWN };
    }
    return { kind: "show", level: "all", plan: planPanes(open, BOTH_PANES) };
  }
  const next = levelUp(levelOf(open));
  if (next === null) {
    return { kind: "refuse", reason: AT_THE_TOP };
  }
  return { kind: "show", level: next, plan: planPanes(open, panesFor(next)) };
}

/** `>`: one level down; nothing when the terminal is wide. */
export function stepDown(columns: number, open: PaneState): Step {
  const regime = fit(columns);
  if (regime === "none") {
    return { kind: "refuse", reason: TOO_NARROW };
  }
  if (regime === "two") {
    return { kind: "noop" };
  }
  const next = levelDown(levelOf(open));
  if (next === null) {
    return { kind: "refuse", reason: AT_THE_BOTTOM };
  }
  return { kind: "show", level: next, plan: planPanes(open, panesFor(next)) };
}

/**
 * The level a row pick in *pane* drills down to when one pane fits: a
 * commit picked shows the files, a file picked shows the diff. Null when
 * the terminal is wide (the panes stay) or shows no pane at all.
 */
export function descendAfterPick(columns: number, open: PaneState, pane: PaneId): Level | null {
  if (fit(columns) !== "one") {
    return null;
  }
  const target: Level = pane === "commits" ? "files" : "diff";
  return levelOf(open) === target ? null : target;
}

/**
 * What a width change does to the panes: crossing into the wide regime
 * opens both (the wide default); crossing out of it closes both, so a
 * narrowed page shows the diff, the bottom of the stack; a change inside
 * one regime changes nothing (a pane the user opened or closed stays).
 */
export function planResize(before: number, after: number, open: PaneState): Plan {
  const was = fit(before);
  const now = fit(after);
  if (was === now) {
    return NO_CHANGE;
  }
  if (now === "two") {
    return planPanes(open, BOTH_PANES);
  }
  if (was === "two") {
    return planPanes(open, panesFor("diff"));
  }
  return NO_CHANGE;
}
