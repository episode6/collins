// New in the ghackett fork of agent-session-manager (GPL-3.0).

/**
 * What hunk has loaded, decoded from its session title — the one thing the
 * keys need to know: whether the working tree is loaded, and which side.
 *
 * The title is the only thing that names a load: hunk's own titles are
 * `<repo> working tree` and `<repo> staged changes` for the two working
 * tree sides; anything else (`<repo> show <ref>`, `<repo> <range>`, a
 * pathspec) is a read-only view here, where every key says so and does
 * nothing.
 */

export type Side = "unstaged" | "staged";

export type Loaded = { readonly kind: Side } | { readonly kind: "other" };

const TITLE_WORKING_TREE = "working tree";
const TITLE_STAGED = "staged changes";

/**
 * Decode a session title. The repo name in front may contain spaces, so it
 * is stripped by name when known and the tail is matched on its own.
 */
export function decodeTitle(title: string, repoName: string): Loaded {
  let text = (title ?? "").trim();
  if (repoName !== "" && text.startsWith(`${repoName} `)) {
    text = text.slice(repoName.length + 1).trim();
  }
  if (text === TITLE_WORKING_TREE || text.endsWith(` ${TITLE_WORKING_TREE}`)) {
    return { kind: "unstaged" };
  }
  if (text === TITLE_STAGED || text.endsWith(` ${TITLE_STAGED}`)) {
    return { kind: "staged" };
  }
  return { kind: "other" };
}
