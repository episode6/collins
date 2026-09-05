---
name: collins-pull-requests
description: >-
  How Collins tracks and acts on pull requests through the GitHub CLI: pr-link
  transcript records and the status summary (prstatus.py), the PrStore hub that
  every surface subscribes to (prstore.py), the on-demand detail fetch and the
  native PR page (prdetail.py, prview.py), actions and prompts (practions.py),
  the shared marks, chips and menus (prmenu.py), first-prompt PR attachment
  (prattach.py), image blobs in the Files view (prblobs.py, prfileimages.py),
  body images and avatars, and the gh setup notice (ghsetup.py, ghwelcome.py).
  Use when changing anything that shows or does something with a PR, adding a
  gh field, or debugging a chip, mark or menu that is wrong or stale.
---

# Pull requests

Everything GitHub goes through `gh` — already authenticated, host-aware
(Enterprise included), one transport. Without `gh` a PR is a number and an
empty menu, and a launch that finds `gh` missing or signed out shows the
`ghwelcome` notice until "Don't show this again" (`gh_welcome_dismissed`).
Signed-in-ness is `gh auth token` (local, no network), never `gh auth status`.

## Where a PR comes from

Claude Code appends a `pr-link` record to the transcript the moment a PR URL
shows up in tool output (`prstatus.parse_pr_link`), re-emitting it on
resume/compact; `transcript.TranscriptTail` keeps **every** distinct PR in
first-seen order. Two more sources: `prattach` reads each *new* session's
first prompt for references ("PR 271", `owner/repo#12`, a `/pull/` URL — the
grammar session titling shares, `titles.pr_references_all`), verifying each
with `gh pr view` off the main thread (a bare URL attaches unverified when gh
can't answer); and the `attach_pr` MCP tool. The backlog at launch is never
re-read.

## The hub (`prstore.PrStore`, reachable as `store.pr_store`)

Two halves, one owner:

- **Status by URL** lives in `prstatus._statuses`; every write funnels through
  `_put`, and `prstatus.add_listener` callbacks fire only on an actual change
  — **on the fetching worker thread**. `PrStore` is the app's one listener,
  hops to the main loop, and re-emits `status-changed(url)`.
- **Session → PRs** (`state.json` `session_prs`, oldest first) has `PrStore`
  as sole reader/writer (`records` / `prs` / `set_records` / `set_prs` /
  `attach`), emitting `session-changed(session_id)` on a real change and
  `pr-attached(session_id, url)` once per PR per session. **The equality guard
  is the loop-breaker**: an identical write is swallowed (no disk, no signal),
  so subscribers may write back what they adopted. `state.get/set_session_prs`
  is persistence-only — never call it from UI code.

Subscribers: sidebar rows connect in `__init__` and disconnect in `do_unroot`;
tabs via `set_pr_store` (from `window._add_tab`), disconnected on `destroy`
(tabs survive unroot when moving windows). `PrViewPage.sync_summary()` must
stay a pure `prstatus.known()` read — a fetch there loops
fetch → absorb → hub. Session retitling after PRs (`pr_title_sessions`) rides
`session-changed`. Never reintroduce per-surface relays (`prs-changed`,
`pr-status-changed`, `sidebar.prs_updated` were all removed).

## Status (`prstatus.py`, GTK-free)

`PullRequest` carries number, url, title, state (`OPEN`/`DRAFT`/`MERGED`/
`CLOSED`), check counts, mergeability, `unresolved` (newest non-minimized
comment is someone else's — gh's per-comment `viewerDidAuthor`),
`claude_replied` / `pushed_since` (the "Ask Claude for a review" gate:
Claude comments as login `claude`; gh reads list fields one page of 100
deep, oldest first, so a full page reads as "can't say"), and derived
`badge` / `settled`. `to_record` / `from_record` persist the **whole** status
— **stale beats blank**: a grey "nothing known" mark on cold start is the
least accurate answer, so saved marks paint until the launch sweep
(`refresh_prs_on_launch`, ~2.5 s after the first scan) replaces them.

Fetch policy: the CLI's own `~/.claude/gh-pr-status-cache.json` is trusted
only while its mtime is under 5 min (a free warm start; only FleetView
refreshes it). Otherwise one `gh pr view --json <_GH_FIELDS>` per PR with a
60 s TTL (600 s once settled; 5 min backoff after an error). Only `merged`
skips a fetch; closed PRs refetch so a reopen shows. While a live PR has
pending checks, 10 s **probes** — `gh api` on the head commit's check-runs with
`If-None-Match` — fill the gap: GitHub answers `304` free of the rate limit
until something changes (`gh api -i` exits 1 on a 304; read the status line).
`enrich()` may schedule gh and is worker-only; `known()` is the main-loop
read. The transport `_gh` is argv-only with timeouts, a URL gate
(`_FETCHABLE`), bounded error text and a missing-gh latch; `gh_bytes` is the
one undecoded call (image blobs).

**Field superset rule:** `prdetail.fetch` hands its `gh pr view` reply to
`prstatus.absorb`, so a page load *is* a status fetch. Any field added to
`prstatus._GH_FIELDS` must also be added to `prdetail._GH_DETAIL_FIELDS`, or
every page load overwrites that field with its empty value (the auto-merge
button once flipped back on load). `gh pr view --json` has no PR-level
`viewerDidAuthor`; `viewer_login()` (`gh api user`, memoized, failures not
remembered) answers "is this mine".

## Marks, chips and menus (`prmenu.py`)

One mark everywhere: `prmenu.status_icon` = a base GitHub icon (grey draft /
green open / purple merged / red closed; grey also = nothing fetched) with a
badge at the bottom-left ranked **failed > conflicting > pending >
unresolved > passed**; merged/closed (`settled`) suppress badges. Sidebar rows
aggregate a session's list with `combined_icon` (base = least settled, badge
= loudest live problem). The badge glyphs are Octicons drawn as filled paths
(`circle-fill-symbolic` is in-repo: Octicons' dot vanishes at 8 px). Footer
chips (`terminal._build_pr_chip`; `PrChipRow` measures overflow into an
ellipsis menu) and the row mark open the same popover list: left click asks
what to *do* (actions submenu from `practions.actions_for`), right click
opens the browser; a mark for one PR is the reverse. The list refreshes
before it shows for a tabless session.

## Actions (`practions.py`, GTK-free)

`actions_for(pr, …)` is deliberately narrow: a draft is asked to be marked
ready; an open PR merges now or arms auto-merge (and one armed offers the way
out); review goes through a comment (`@claude review`), offered only while
Claude didn't comment last on unmoved code; red CI, conflicts and unanswered
comments become **prompts sent to the session** (`CI_PROMPT` etc.,
`inject_prompt`; only while `takes_prompt`). `header_actions` are the page's
buttons; `alternate_actions` is what a right-click on that button offers
instead (close, merge-and-archive, ready+merge combos — `READY_MERGES` run
`pr ready` first and stop on failure). Merges confirm unless `confirm_merges`
is off; closing always asks. `perform(key, pr)` is the `gh` call;
`comment` / `review` / `reply_in_thread` / `set_thread_resolved` (GraphQL) back
the page's composer. Never re-draft a PR the user marked ready.

## The page (`prdetail.py` → `prview.py`)

`prdetail.fetch(url)` is three gh calls (four on a run's first load): `gh pr
view --json` with the full field list, a paginated GraphQL query for review
threads (the only way to anchors and resolution), and `gh pr diff` (capped;
over the cap or on failure the load degrades to stat-only files). Everything
parsed is repository content: tolerant parsing, bounded strings, http(s)-only
URLs, refs must be 40-hex commits (`_oid`). `PrViewPage` (`page_kind="pr"`,
docked beside the terminal) has Conversation and Files views under an
`Adw.ViewSwitcher` (its direct children are the per-page buttons; right-click
opens that view on github.com); loads on first show, Refresh, throttled
re-map, and the hub's news; **patches in place** (`_Slots`, keyed rows) so
folds, expansion and scroll survive, re-pinning scroll from the frame clock's
`layout` phase (an idle reads stale bounds). Failures keep the last content
under a banner. Font scale is a display-level provider keyed on
`.pr-view-page` (`pr_font_scale`). Busy buttons swap to a spinner in a
`Gtk.Stack` and stay held until the re-read lands on **both** branches
(`_landed`); a forced fetch colliding with one in flight is re-issued. Before
`_rebuild` clears a box, focus inside it is parked on the scroller
(`_park_focus`), and `gtk-label-select-on-focus` is off app-wide, or the
description selects itself. Bodies render via `formatting.md_to_pango`
(escape everything; `set_lines` caps per *paragraph*, so `_folded_body`
truncates text); images via `bodyimages` / `pictures` (`BoundedPicture`
measures height-for-width in a `Gtk.Box` slot); changed images render
before/after from `prblobs` (`gh api …/contents/{path}?ref=<sha>` with the
raw media type; a binary file *does* get a "Binary files differ" patch, so
`patch is None` means over-cap). Avatars are `github.com/<login>.png`, logins
gated to GitHub's username alphabet.

## Footguns

- Redirecting `XDG_CONFIG_HOME` does **not** reliably hide gh's credentials
  (keyring); force `ghsetup.check` in captures of the notice.
- The auto-opened PR page (`open_pr_panel_on_attach`) opens on a 250 ms
  timeout with `focus=False`; from the chip cascade's idle it segfaulted.
- `prstatus`'s listener registry is module-global; the test suite's autouse
  fixture clears it — assert `scheduled == []` too when a test stubs
  `_schedule`.
- `prview` imports GtkSource *through* `editor` for the friendly-exit path.
- Merge-and-archive rides `MainWindow.archive_session` (always archives, via
  the normal close flow — can be declined), never the toggling row action.
- The e2e env has no gh: stage `session_prs` records in `state.json` to render
  marks and badges.

Related: `collins-sessions-and-sidebar`, `collins-terminal-tab`,
`collins-panel-dock`, `collins-gtk-sharp-edges`.
