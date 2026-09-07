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
resume/compact; `transcript.TranscriptModel` keeps **every** distinct PR in
first-seen order. Two more sources: `prattach` reads each *new* session's
first prompt for `/pull/` URLs — only that form of the grammar session titling
parses (`titles.pr_url_references`, not `pr_references_all`): "PR 1" and
`owner/repo#12` were dropped after prompts about *opening* PR 0 / PR 1 / PR 2
attached the repository's real, unrelated PRs 0, 1 and 2 — resolving each
with `gh pr view` off the main thread (a URL attaches unverified when gh
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
Claude comments as one of `prstatus._CLAUDE_LOGINS` — `claude`, `claude-code`,
`claude-bot`, compared lowercased with any `[bot]` suffix stripped; gh reads
list fields one page of 100 deep, oldest first, so a full page reads as
"can't say"), and derived `badge` / `settled`. `to_record` / `from_record` persist the **whole** status
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
= loudest live problem). A PR page's *tab* takes a GIcon, not a widget, so
`mark_icon(pr, dark, scale)` rasterizes the same mark into a `Gdk.Texture`
(a GIcon) with a `Gsk.CairoRenderer` realized for the display — colors
straight from `MARK_COLORS` (app.py's scheme CSS paints those same shades
onto the `.pr-*` classes), cached per state/badge/scheme/scale; the page
re-emits `title-changed` on the style manager's `notify::dark` and on its own
`notify::scale-factor` so the strip re-reads it. The badge glyphs are Octicons drawn as filled paths
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
description selects itself.

**Bodies are markdown blocks.** Two modules, the parse/render split the
codebase uses everywhere:

- `mdblocks.py` (GTK-free; GLib only, unit-tested in
  `tests/test_mdblocks.py`) runs markdown-it-py's `gfm-like` preset — the
  one preset, pinned by a unit test: `gfm-like2` exists only on 4.1+, and
  the code runs on both Ubuntu's 3.0.0 and Fedora/Arch's 4.2.0 — behind an
  import latch (`available()`), and folds the token stream into frozen
  dataclasses (`Text(markup, source)`, `Heading`, `CodeBlock`, `Table`,
  `Quote(kind)`, `ListBlock`/`ListItem(check)`, `Details`, `ImageRow`,
  `Rule`), each carrying its `source`, nesting capped at `MAX_DEPTH` (6).
  Inline runs become Pango markup through a walker with an open-tag stack
  (well-formed by construction): hrefs escaped and gated to http(s); a
  linkify link kept only when its visible text starts with `http://`,
  `https://` or `www.` (GitHub's autolink rule — markdown-it's fuzzy
  linkify would link `example.com`, and is switched off in `_load`, being
  quadratic, so `www.` autolinks come from `mdblocks.link_www`, a linear
  pass over text tokens); `<img>`, `<br>`, `<sub>`, `<sup>`,
  `<kbd>` honoured and every other tag escaped literal (a `<br>` alone on
  its line — an HTML block — is dropped). An image inside a link is its
  alt text, the author's link kept (an anchor in an anchor is markup a
  label can't honour); a paragraph that is only a linked image is an
  `ImageRow`, the link dropped for the lightbox. `line_cost` estimates
  what each block draws; `cut_block` is the front of a list or table that
  fits a budget (the preview cut a paragraph gets from `body_head`).
- `mdwidgets.py` turns blocks into widgets under a `Budget` of ~400
  leaves — a list's items and a table's rows count too — and past it the
  remaining blocks are one plain label of their source
  (`mdwidgets.rest_source`), never dropped. What the page hands in travels
  as trailing keywords through `build` / `build_one` / `_list` / `_quote`
  and into a `DetailsExpander`: `page_url` (the body's own place on
  GitHub, for the "more on GitHub" links), `scheme` (the code views'), and
  through `prview` also `refs`.

`prview._segments` picks the block walk (`_fill_blocks`) or the fallback
ladder: `available()` False → `split_body` + `md_to_pango` for every body;
a body `parse_blocks` raises on → that body alone; bad markup on a label →
escaped source (GTK 4's `set_markup` blanks a label on bad markup instead
of raising, so a `try/except GLib.GError` around it guards nothing).
`_fill_blocks` spends the fold's character and line budgets per block
(`line_cost`), cuts the overrunning *paragraph* on its source with
`body_head` and re-renders the front through `mdblocks.render_inline`
(`set_lines` + ellipsize on that label alone), shows the front of an
overrunning list or table through `cut_block`, and leaves any other block
that doesn't fit whole for "Show more"; what the widget budget leaves over
takes the one-label shape (`_rest_head`).

A `Table` is a `Gtk.Grid` of selectable cell labels (header bold under
`.pr-md-th`, `xalign` per the delimiter row's colons, each cell wrapping
past 60 chars) inside `mdwidgets._TableScroller` — sideways scrolling
only, natural height, `hscroll-policy` NATURAL on the viewport — so the
page body never scrolls sideways; `mdblocks.cap_table` squares the rows to
the header and trims to `TABLE_MAX_ROWS` (50) × `TABLE_MAX_COLUMNS` (8),
and what it cut (plus what the widget budget stopped — a row spends one
leaf) is a dim "N more rows on GitHub" link to `page_url`
(`mdwidgets._link_label`).

A `CodeBlock` is `mdwidgets.CodeBlockView`, a `Gtk.Overlay` holding a
read-only `GtkSource.View` (`.view`, `.pr-md-code`) in its own
`AUTOMATIC/NEVER` scroller (`.pr-md-code-scroller`, natural height) built
like the Files view's patch view — no cursor, monospace, 6/4 px margins,
no line numbers. A right-click (a `GestureClick` claimed in the CAPTURE
phase, ahead of the text view's own context menu) copies the block's whole
text (`.text`, `copy()`) and shows the `.pr-md-copied` OSD pill over the
top-right corner for `copylabel.FLASH_MS`; the tooltip says so. The buffer
holds `CODE_CAP` (20 000) characters; past it, `code_view` adds a
"N more lines on GitHub" link (`.pr-md-code-more`). The language is the
fence's info word through `editorfiles.fence_language_id` (`python`/`py` →
`python3`, `js`/`ts`/`jsx`/`tsx` → `js`, `bash`/`zsh`/`shell`/`console` →
`sh`, `suggestion` → plain), else the word itself when
`LanguageManager.get_language` knows it, else plain. The style scheme is
the page's — `PrViewPage._body_scheme()` = `editor.style_scheme(setting,
dark)` — and `_apply_scheme` restyles every live code view with
`mdwidgets.restyle_code(page, scheme)` when the setting or the app's
light/dark changes; an unopened `DetailsExpander` gets its `scheme`
attribute refreshed so a fence built later wears the current one.

**GitHub references** are a post-pass over `text` tokens alone
(`mdblocks.link_refs`, called from the walker's `_text` — never for a
`code_inline` token, never while a `link` entry is on the open-tag stack,
so an author's own link text and a link the http(s) gate refused both
stay theirs; the text between references goes through `link_www`, so both
passes share one token): `#123` and `owner/repo#123` → `/issues/N` (GitHub forwards a
PR's number), `@user` → the profile, a lowercase 7–40 hex word with at
least one digit and one letter → `/commit/`, with GitHub's boundaries (not
on the tail of a word, `&`, `/`, `#`, `@`, `.` or `-`; not followed by a
word character, `/` or `-`; logins on `mdblocks.LOGIN_RE`, the gate
`avatars` shares). Every URL is built from a `mdblocks.RepoContext` —
`repo_context(repository, host, head)` holds each part to its shape
(owner/name, a hostname, a 40-hex oid or "") — that `PrViewPage._refs()`
makes from the PR's own `repository`, its URL's host and the detail's
`head_oid`, threaded as `refs` beside `page_url` and `scheme` through
`_body_label` / `_folded_body` / `_segments` / `_fill_blocks` /
`_cut_markup` / `_ThreadCard` into `parse_blocks` and `render_inline`;
None means no reference links. With a head, `mdblocks.relative_href` turns
a relative link destination (`docs/a.md`, `./b.md`; no scheme, leading
slash, fragment, query or `..` step — percent-decoded too) into
`/blob/<head>/<path>` before the gate — never a linkify token, which is a
domain not a path.

A `Details` is `mdwidgets.DetailsExpander`, a `Gtk.Expander` whose label
widget is its own wrapping label (`summary_label`,
`.pr-md-details-summary`; GTK's built-in expander label neither wraps nor
ellipsizes, and a one-sentence summary set the page's minimum width)
wearing the summary (`_("Details")` when there is none;
`mdblocks.summary_text` strips tags, reads the author's entities back,
folds whitespace and cuts at `SUMMARY_MAX` (300) before its one escape, so
it is set with `use_markup`), collapsed unless the tag said `open`. A
`<details open>` builds its children at construction against the body's
own `Budget` — the attribute is the author's, so what it shows counts like
any other block; only a reader's click builds on the first
`notify::expanded` with a fresh `Budget`, with whatever `image_row` /
`page_url` / `scheme` the page handed the expander. An alert `Quote` is
the quote column wearing `.pr-md-alert` and `.pr-md-alert-<kind>` under a
`.pr-md-alert-head` row of GitHub's Octicon for the kind
(`alert-note/tip/important/caution-symbolic`, the warning one the conflict
mark's `alert-symbolic`) and the translated title; the bar and the head
take the kind's color from app.py's CSS — the note the accent in `_CSS`,
tip/warning/caution/important the passed green, pending yellow, failed red
and merged purple of `_SCHEME_CSS`. Images render via `bodyimages` /
`pictures` (`BoundedPicture` measures height-for-width in a `Gtk.Box`
slot); changed images render before/after from `prblobs` (`gh api
…/contents/{path}?ref=<sha>` with the raw media type; a binary file *does*
get a "Binary files differ" patch, so `patch is None` means over-cap).
Avatars are `github.com/<login>.png`, logins gated to GitHub's username
alphabet.

## Footguns

- Redirecting `XDG_CONFIG_HOME` does **not** reliably hide gh's credentials
  (keyring); force `ghsetup.check` in captures of the notice.
- The auto-opened PR page (`open_pr_panel_on_attach`, on by default) opens on a 250 ms
  timeout with `focus=False`; from the chip cascade's idle it segfaulted.
- `prstatus`'s listener registry is module-global; the test suite's autouse
  fixture clears it — assert `scheduled == []` too when a test stubs
  `_schedule`.
- `prview` imports GtkSource *through* `editor` for the friendly-exit path.
- Merge-and-archive rides `MainWindow.archive_session` (always archives, via
  the normal close flow — can be declined), never the toggling row action.
- The e2e env has no gh: stage `session_prs` records in `state.json` to render
  marks and badges.
- `scripts/check_pr_page_focus.py` finds the description as a `Gtk.Label`
  whose text starts with "First" and `check_pr_page_patch.py` counts column
  children: paragraphs must stay individually selectable labels, and no
  block widget may add a `Gtk.Button` with a `Gtk.Box` child (how the focus
  check finds the fold's toggle). `check_pr_body_blocks.py` asserts on the
  `pr-md-*` CSS classes — keep them when restyling.
- A `Gtk.ScrolledWindow` measures its child's height at width -1, which a
  height-for-width grid of wrapping labels answers with its height at its
  *minimum* width — a two-row table with one long cell came out four
  thousand pixels tall. `mdwidgets._TableScroller.do_measure` reports the
  grid's height at the grid's natural width instead (what `halign START`
  in the viewport allocates it). The widths were never the problem; the
  viewport's default `hscroll-policy` of MINIMUM was, which the table sets
  to NATURAL so the grid scrolls rather than squeezes. Two prices, both
  harmless: GTK logs "Trying to measure GtkGrid for height of N, but it
  needs at least M" (the grid's height at its minimum width is taller than
  the one the scroller reports), and chaining up to
  `Gtk.ScrolledWindow.do_measure` from Python hands back zero baselines —
  return -1 for both or GTK warns of "a horizontal baseline".
- An `<img>` alone on its line is a CommonMark HTML *block* (type 7), not an
  `html_inline`: `mdblocks._html_lines` turns such lines into image rows. A
  `Text.source` is the inline token's content (indents and `>` stripped),
  not the mapped lines — `parseInline` on the cut front would otherwise
  render the markers literally; container sources *are* the mapped lines.
- `<details>` openers are paired with their `</details>` html_blocks in
  one stack pass per `fold` call (`mdblocks._details_closes`), never by a
  forward scan per opener: prdetail's 100 000-char cap admits nine thousand
  unmatched openers, and a scan each was 24 s on the main loop. An opener
  with no close, or closed inside its own block, renders literal.
- Task lists and alerts are text-token detectors (`[ ] `/`[x] ` as the first
  text of an item's first paragraph; `[!NOTE]` + softbreak as a quote's), and
  the detector mutates the token it strips — parse each body once.
- The `gfm-like` preset's linkify runs with `fuzzy_link` and `fuzzy_email`
  on, and both are quadratic per paragraph (markdown-it-py 3.0.0 /
  linkify-it-py 2.0.3: 50 KB of `www.a.com ` parsed in 6 s, 20 KB of
  `a@b.com ` in 4 s — inside prdetail's cap, on the main loop under
  `_rebuild`). `mdblocks._load` sets both False right after constructing
  the parser (`md.linkify` exists on 3.0.0 and 4.2.0); `http(s)://`
  autolinks are unaffected, `www.` ones come back from `link_www` (the
  same linear text-token pass shape as the reference links), and emails
  are never linked. A unit test pins the bound.
- markdown-it's `text_join` folds escapes and entities into the text token,
  so `\#123` and `&#35;123` reach `link_refs` as `#123` and link where
  GitHub would show them literal; a `javascript:` destination is refused by
  markdown-it outright, so `[#7](javascript:…)` is plain text in which `#7`
  links. Neither is worth a token-level detour.
- A right-click on a `GtkSource.View` pops the text view's own context
  menu unless a `GestureClick` for `BUTTON_SECONDARY` claims it in the
  CAPTURE phase (`CodeBlockView`, and the git page's hunk views the same
  way). Under the headless shell the clipboard claim it makes can be
  dropped until the window has seen input: `check_pr_body_blocks.py`
  copies until `clipboard.is_local()`.
- The alert titles, "Details", "Right-click to copy" and the "more … on
  GitHub" counts have no `po/generate.py` entries yet — English until the
  release-cut translation refresh, which must pick them up.

Related: `collins-sessions-and-sidebar`, `collins-terminal-tab`,
`collins-panel-dock`, `collins-gtk-sharp-edges`.
