# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The native pull-request page: one PR rendered as GTK widgets, no browser.

`PrViewPage` is the `pr` kind of PanelPage (see panelstrip): a panel-dock tab
beside the agent terminal showing what the PR's own page would — header,
description, checks, conversation, and the per-file diff — off the `gh` auth
Collins already has. Two views under one switcher: **Conversation** (the
description, checks and timeline column) and **Files** (a navigation list
beside every file's patch in a GtkSource diff buffer, styled by the editor's
own scheme setting). The data is `prdetail.fetch`'s reply, fetched on a
worker thread on first show, on the Refresh button, and (throttled) whenever
the page comes back into view; the widgets here only ever render what that
GTK-free layer parsed and bounded. Failures keep the last-loaded content
under an inline banner — stale beats blank, as everywhere in the PR stack.

On the switcher's own row, at its end, sits the action that moves the PR
itself along, as one button: whatever `practions.header_actions` says its
state offers — "Ready" for a draft, "Auto-Merge" or "Merge" for an open one,
whichever fits its checks, and "Disable Auto-Merge" once GitHub is already
holding it to land on its own, naming in full on its tooltip what it is about to
do — behind the same confirmation and the same `gh` call the chip's actions
menu runs it with (`_ActionBar`), and no button at all where a PR offers
none. A right-click on that button opens the alternatives to what it says
(`practions.alternate_actions`): "Close pull request" while there is still
one to close, "Merge pull request" where the button has stopped offering it,
on a draft the ready-and-merged shortcuts past "Ready" itself,
and — beside any merge-now — the merge-and-archive, which lands the
PR and then puts the session that opened it away, in that order and only if
the merge worked. Everything else the PR offers
is still a chip's right-click menu away.

The Checks list carries the page's other button. A conflicting branch shows
there as a failed check of its own (prdetail adds the row: it blocks the merge
exactly as a failure does), and under a list with anything red in it sits the
one prompt that would clear it — "Fix errors", "Resolve conflicts", or both at
once — typed into the owning session (`practions.repair_action`). The list
shows `_CHECK_ROWS_SHOWN` rows: past that the rest folds behind "Show more"
(`_fold`, the description's own step), the heading counts the lot, and the
shown rows are the ones that block the merge, so a fifty-context rollup can't
push the conversation off the page or fold away the very failures the button is
offering to fix.

The Conversation column ends in the page's write half: a composer that posts
its text as a comment or a review verdict through practions' write calls
(bodies over stdin, never argv), with a Claude button beside them that either
types the COMMENTS prompt into the owning session ("Address comments", while
someone is waiting on a reply) or asks the repository's workflow for a review
("Request review") — the composer is for answering a reviewer yourself, the
button for making the agent do it. The verdicts sit out a pull request the
signed-in account opened, which GitHub won't let anyone review their own of.

Review threads render as their own cards (`_ThreadCard`): anchored in the
Conversation timeline by when they started, and again under their file's
section in the Files view, each with the thread's write half — a reply
composer behind a revealer, and Resolve/Unresolve. Resolved threads collapse
behind an expander. Reply drafts live on the page keyed by thread id, so the
rebuild that lands a background refresh never eats one — the main composer's
lesson, thread-sized. `reveal_unresolved` is the unresolved badge's deep
link: the Conversation view fronted and scrolled to the first unresolved
thread, deferred until the first fetch when the page is fresh.

Bylines lead with the author's picture (see avatars), the description folds
past a few lines behind "Show more", and the page's reading text renders at
the ``pr_font_scale`` setting's size — buttons and menus excepted — via one
display-level provider keyed off the page's css class (`_apply_font_scale`).

Bodies show the images they embed, in place (`formatting.split_body` finds
them, bodyimages fetches and sizes them, a click opens the lightbox) — a
screenshot in a PR description is most of what that description says, and
rendering it as the word "screenshot" underlined threw that away. The
``pr_inline_images`` setting turns it off, which is also the answer for
anyone who would rather a repository's bodies didn't make their machine
fetch anything: off, an image renders as the alt-text link it always did.

The Files view renders a *changed* image the same way, and for the same
reason: `prfileimages.preview` puts before and after pictures at the top of
the file's section (fetched by commit through `prblobs`) where a patch could
only ever have said that two binary files differ. A file with a real diff as
well — an SVG — keeps it under the pictures, behind its own line-count
expander when it is a long one, so the section can lead with the artwork
without eagerly building a buffer. The same setting gates it.

Everything shown is repository content and therefore untrusted: bodies go
through `formatting.md_to_pango`'s escaping (with the plain-text fallback on
malformed markup), only http(s) URLs ever reach a browser or a fetch
(prdetail and split_body both enforce that on the way in), and a
pathological body renders capped behind a "Show more" step — with a cap on
images too — so building labels can't wedge the main loop.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

from . import (  # noqa: E402
    avatars,
    bodyimages,
    dialogs,
    practions,
    prdetail,
    prfileimages,
    prmenu,
)
from .copylabel import (  # noqa: E402
    copy_hint,
    enable_copy_on_secondary_click,
    open_tooltip,
    open_uri,
)
from .editor import GtkSource, style_scheme  # noqa: E402 — require_version + friendly exit live there
from .formatting import (  # noqa: E402
    format_relative,
    format_timestamp,
    md_to_pango,
    split_body,
)
from .i18n import _, ngettext  # noqa: E402
from .prstatus import PullRequest, invalidate, known  # noqa: E402

log = logging.getLogger(__name__)

# Re-showing the page re-reads the PR, but not more often than this — the
# same arrival throttle the footer chips use (terminal._PR_FOCUS_REFRESH_MIN_US).
_FOCUS_REFRESH_MIN_US = 10 * 1_000_000
# Where a body stops rendering until "Show more" is pressed. Well under
# prdetail's storage bound: a label this long is already a scroll of its own,
# and Pango layout cost grows with every character the main loop hands it.
_RENDER_CAP = 20_000
# Where the description folds by default: about the eight lines the card
# shows before "Show more", and the size past which a body plainly can't fit
# them even without a newline (long paragraphs wrap).
_FOLD_LINES = 8
_FOLD_CHARS = 550
# What a row of body images costs the fold's line budget: a picture caps at
# bodyimages._MAX_HEIGHT, several lines' worth of the preview's height.
_IMAGE_FOLD_LINES = 4
# The title may wrap this far before it ellipsizes — a PR title is a sentence,
# not a phrase, and one header line cut most of it off.
_TITLE_LINES = 3
# Byline avatars, GitHub's own inline size.
_AVATAR_PX = 24
# A patch past this many lines starts collapsed and only builds its buffer on
# first expand: GtkSource renders it fine, but a fetch landing a handful of
# eagerly built multi-thousand-line buffers would wedge the main loop.
_LARGE_PATCH_LINES = 2_000
# The file list's share of the Files view until the user drags the divider —
# the editor gives its file tree the same kind of sliver (_TREE_INITIAL_WIDTH).
_FILE_LIST_WIDTH = 170
# What Adwaita leaves between the switcher's own two buttons, borrowed for the
# seam between Files and the action button beside it — applied where the two
# meet, on the switcher row's end widget (see `switcher_row` below).
_SWITCHER_GAP = 3
# The one width the page asks for, in every state it is ever in. A page whose
# minimum grew when its fetch landed would shove the panel divider out from
# under a panel already squeezed narrow — so nothing built below may ask for
# more than this: everything long wraps or ellipsizes, and the composer's
# button row wraps onto further lines (see _WrapRow) rather than setting a
# floor of its own. Comfortably above what the header alone needs, which is
# what the page shows while the first fetch is still in flight.
_MIN_PAGE_WIDTH = 320
# How many check rows the Checks list shows before the rest fold behind "Show
# more", and the gap between two of them. A repository is free to put fifty
# contexts on a pull request, and a section that grew a row per context would
# push the description off the top of the panel and the conversation off the
# bottom; past this the list folds like the description does (see _fold).
_CHECK_ROWS_SHOWN = 4
_CHECK_ROW_GAP = 2


def _verdict(state: str) -> tuple[str, str | None, str]:
    """A review's verdict as its card heading: icon, color class, wording.

    The colors are the PR marks' own (see app.py _SCHEME_CSS), so approved
    reads green here exactly as a passed check does on the chip. Translated
    at call time, so import order can't outrun locale setup.
    """
    verdicts = {
        "APPROVED": ("check-circle-fill-symbolic", "pr-checks-passed", _("Approved")),
        "CHANGES_REQUESTED": (
            "x-circle-fill-symbolic",
            "pr-checks-failed",
            _("Changes requested"),
        ),
        "DISMISSED": ("circle-fill-symbolic", None, _("Review dismissed")),
    }
    return verdicts.get(state, ("chat-bubble-symbolic", None, _("Commented")))


class PrViewPage(Adw.Bin):
    """One pull request as a panel page: header bar, then the conversation.

    Constructed from the summary record a chip already holds — enough for the
    tab title and header before anything is fetched — and filled in by the
    first `prdetail.fetch`. *host_factory* is the owning tab's ActionHost
    builder (`TerminalTab._pr_action_host`): how the page reaches the session
    the pull request belongs to — the composer's Claude button asks it whether
    a prompt can be typed there, and every landed action asks it to re-poll.
    """

    page_kind = "pr"
    # The width the dock doubles when the terminal's gutter can pay for it:
    # the page opens at up to 2x this without the terminal giving up a pixel,
    # decided before the first fetch lands so the column never resizes under
    # its own data (see PanelDock._column_floor).
    column_floor = _MIN_PAGE_WIDTH

    __gsignals__ = {
        # The tab's title/icon inputs changed (a fetch landed a new state);
        # the strip re-reads page_title/page_icon.
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, pr: PullRequest, host_factory, pr_store=None) -> None:
        super().__init__()
        # The text-scale hook: _apply_font_scale's display-wide rules key off
        # this class, which is how a setting reaches every label in the page
        # without touching each one (see the function's comment).
        self.add_css_class("pr-view-page")
        # The filled page's width, asked for while the first fetch is still
        # in flight (see _MIN_PAGE_WIDTH).
        self.set_size_request(_MIN_PAGE_WIDTH, -1)
        self._pr = pr
        self._host_factory = host_factory
        self._detail: prdetail.PullRequestDetail | None = None
        self._fetching = False
        # A forced read that arrived while one was already in flight, waiting
        # to be re-issued: the fetch in flight was dispatched before whatever
        # asked, so it cannot be the answer (see `_fetch`).
        self._refetch = False
        self._fetch_gen = 0
        self._fetched_at = 0  # monotonic µs of the last fetch *attempt*
        # The app-wide PR hub (see prstore), when the page was given one: a
        # status fetch anyone makes for this URL re-reads the page, so the
        # checks list here follows the footer's probes (see prstatus.probe)
        # instead of waiting for the next map or the refresh button.
        self._pr_store = pr_store
        self._hub_status_id = (
            pr_store.connect("status-changed", self._on_hub_status_changed)
            if pr_store is not None
            else None
        )
        # The Conversation view's thread cards in timeline order — what the
        # unresolved deep link scans — and the reply drafts, keyed by thread
        # id so they survive the rebuilds that replace the cards.
        self._thread_cards: list[tuple[prdetail.PrThread, Gtk.Widget]] = []
        # Every live thread card, both views' copies of each — the ones a
        # landed fetch releases (the timeline list above is the Conversation
        # view's alone, and the deep link's business).
        self._cards: list[_ThreadCard] = []
        self._thread_drafts: dict[str, str] = {}
        # Thread ids with a mutation in flight. On the page rather than the
        # card for the same reason the drafts are: a thread renders as twin
        # cards (one per view) and a fetch can rebuild them mid-flight, and
        # "one press, one mutation" has to hold across all of those copies.
        self._thread_busy: set[str] = set()
        self._pending_reveal = False  # reveal_unresolved asked before data came
        # The pr_inline_images setting, at its shipped default until the
        # first apply_settings lands (which is before anything is fetched).
        self._inline_images = True

        # -- header ---------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("pr-view-header")

        # Everything in the row anchors to its top: the title may run to
        # _TITLE_LINES, and the mark, number and buttons should ride its
        # first line rather than float at the vertical middle of three.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._mark_slot = Adw.Bin(child=prmenu.status_icon(pr))
        self._mark_slot.set_valign(Gtk.Align.START)
        # Optically centers the mark on the title's first line of text.
        self._mark_slot.set_margin_top(4)
        top.append(self._mark_slot)
        self._number = Gtk.Label(label=f"#{pr.number}")
        self._number.add_css_class("dim-label")
        self._number.set_valign(Gtk.Align.START)
        top.append(self._number)
        self._title = Gtk.Label(xalign=0.0, yalign=0.0, hexpand=True, selectable=True)
        self._title.add_css_class("pr-view-title")
        # Wrapping up to _TITLE_LINES before the ellipsis: a one-line header
        # cut most real titles off, and the tooltip still holds the whole.
        self._title.set_wrap(True)
        self._title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._title.set_lines(_TITLE_LINES)
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        top.append(self._title)

        # The Refresh button and the spinner that stands in for it while a
        # fetch is in flight, as two pages of one Stack: the spinner says the
        # reload is running where the button that asked for it was, rather
        # than beside a greyed-out copy of it, and the stack measures as the
        # button either way so the header's buttons never shift.
        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_btn.add_css_class("flat")
        self._refresh_btn.set_tooltip_text(_("Reload this pull request"))
        self._refresh_btn.connect("clicked", lambda *_a: self.refresh())
        self._spinner = Gtk.Spinner()
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._refresh_slot = Gtk.Stack()
        self._refresh_slot.set_valign(Gtk.Align.START)
        self._refresh_slot.add_named(self._refresh_btn, "button")
        self._refresh_slot.add_named(self._spinner, "busy")
        top.append(self._refresh_slot)
        github_btn = Gtk.Button(icon_name="github-symbolic")
        github_btn.add_css_class("flat")
        github_btn.set_valign(Gtk.Align.START)
        github_btn.set_tooltip_text(open_tooltip(pr.url) + "\n" + copy_hint())
        github_btn.connect("clicked", lambda b: open_uri(b, self.pr_url))
        # And the link itself on a right-click, confirmed on the button's own
        # face — the page has the URL and nowhere else offers it.
        enable_copy_on_secondary_click(github_btn, lambda: self.pr_url)
        top.append(github_btn)
        header.append(top)

        sub = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._branches = Gtk.Label(xalign=0.0)
        self._branches.add_css_class("dim-label")
        self._branches.add_css_class("caption")
        self._branches.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        sub.append(self._branches)
        self._additions = Gtk.Label()
        self._additions.add_css_class("caption")
        self._additions.add_css_class("pr-checks-passed")
        sub.append(self._additions)
        self._deletions = Gtk.Label()
        self._deletions.add_css_class("caption")
        self._deletions.add_css_class("pr-checks-failed")
        sub.append(self._deletions)
        self._files = Gtk.Label()
        self._files.add_css_class("caption")
        self._files.add_css_class("dim-label")
        sub.append(self._files)
        self._labels = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sub.append(self._labels)
        self._sub = sub
        sub.set_visible(False)  # nothing to say until a fetch lands
        header.append(sub)

        # -- failure banner --------------------------------------------------
        banner_icon = Gtk.Image.new_from_icon_name("alert-symbolic")
        banner_icon.add_css_class("pr-checks-pending")
        self._banner_label = Gtk.Label(xalign=0.0, hexpand=True, wrap=True)
        self._banner_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        banner_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        banner_box.add_css_class("pr-view-banner")
        banner_box.append(banner_icon)
        banner_box.append(self._banner_label)
        self._banner = Gtk.Revealer(child=banner_box)
        self._banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        # -- conversation -----------------------------------------------------
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._content.set_margin_top(10)
        self._content.set_margin_bottom(10)
        self._content.set_margin_start(10)
        self._content.set_margin_end(10)
        self._scroller = Gtk.ScrolledWindow(child=self._content, vexpand=True)
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_focusable(True)
        # The write half, built once and re-appended across rebuilds: the
        # rebuild that lands a background refresh must not eat a half-typed
        # comment (see _Composer).
        self._composer = _Composer(host_factory, self._acted)

        # -- files ------------------------------------------------------------
        # The diff buffers follow the editor's style-scheme setting (or the
        # app's light/dark when it says "follow"), fanned in via apply_settings
        # and the style manager — the same pair editor.py listens to.
        self._scheme_setting = ""
        style_manager = Adw.StyleManager.get_default()
        self._dark = style_manager.get_dark()
        self._dark_id = style_manager.connect("notify::dark", self._on_dark_changed)
        self.connect("destroy", self._on_destroy)

        self._sections: list[_FileSection] = []
        self._file_list = Gtk.ListBox()
        self._file_list.add_css_class("navigation-sidebar")
        self._file_list.connect("row-activated", self._on_file_row)
        list_scroller = Gtk.ScrolledWindow(child=self._file_list, vexpand=True)
        list_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._files_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._files_column.set_margin_top(10)
        self._files_column.set_margin_bottom(10)
        self._files_column.set_margin_start(10)
        self._files_column.set_margin_end(10)
        self._files_scroller = Gtk.ScrolledWindow(
            child=self._files_column, vexpand=True, hexpand=True
        )
        self._files_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._files_scroller.set_focusable(True)
        files_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, vexpand=True)
        files_paned.set_start_child(list_scroller)
        files_paned.set_resize_start_child(False)
        files_paned.set_end_child(self._files_scroller)
        files_paned.set_position(_FILE_LIST_WIDTH)

        # -- what the PR's state offers ---------------------------------------
        # On the switcher's row rather than inside an actions menu: marking a
        # draft ready and merging are what a PR page is *for*, and both were
        # two clicks and a submenu away. This is the page's only copy of them
        # now — the chip's menu still holds the full practions list.
        self._actions = _ActionBar(host_factory, self._acted)
        self._actions.set_valign(Gtk.Align.CENTER)
        # The gap the switcher keeps between Conversation and Files, kept on
        # this side of Files too: on a narrow page the centered switcher slides
        # right up to this button, and without it the two read as one control.
        self._actions.set_margin_start(_SWITCHER_GAP)

        # -- the two views under one switcher ---------------------------------
        self._stack = Adw.ViewStack(vexpand=True)
        self._stack.add_titled_with_icon(
            self._scroller, "conversation", _("Conversation"), "chat-bubble-symbolic"
        )
        self._stack.add_titled_with_icon(files_paned, "files", _("Files"), "ft-file-symbolic")
        switcher = Adw.ViewSwitcher(stack=self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        # A CenterBox rather than a row with the button packed on the end: it
        # keeps the switcher centered on the *page* whatever button is beside
        # it, which is where it sat when it had the row to itself, and it
        # shifts out of the way only where the two would otherwise meet.
        switcher_row = Gtk.CenterBox()
        switcher_row.add_css_class("pr-view-switcher")
        switcher_row.set_center_widget(switcher)
        switcher_row.set_end_widget(self._actions)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self._banner)  # above the stack: it speaks for both views
        column.append(self._stack)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(switcher_row)
        view.set_content(column)
        self.set_child(view)

        self._show_loading()
        self._files_loading()
        self._sync_header()
        # First shown (and every re-show): read the PR. "map" covers the
        # strip appearing, this tab being selected, and the session tab
        # coming back to the front — exactly the moments a stale page would
        # otherwise be looked at.
        self.connect("map", lambda *_a: self._fetch())

    # -- identity ------------------------------------------------------------

    @property
    def pr_url(self) -> str:
        """The URL this page is keyed by — the tab-level dedupe identity."""
        return self._pr.url

    @property
    def pull_request(self) -> PullRequest:
        """The summary as last fetched (or as constructed, before that)."""
        return self._pr

    # -- PanelPage protocol (see panelstrip) ----------------------------------

    def page_title(self) -> str:
        return f"#{self._pr.number}"

    def page_icon(self) -> str | None:
        return prmenu.state_icon_name(self._pr.state)

    def grab_page_focus(self) -> None:
        if self._stack.get_visible_child_name() == "files":
            self._files_scroller.grab_focus()
        else:
            self._scroller.grab_focus()

    def has_page_focus(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return focus is not None and (focus is self or focus.is_ancestor(self))

    def page_busy(self) -> bool:
        return False  # nothing running: a PR page is cheap to refetch

    def apply_settings(self, settings: dict) -> None:
        """The editor's style scheme (the diff buffers wear it), the page's
        own text scale, and whether bodies render the images they embed.
        Everything else renders in the app font and theme."""
        scheme = settings.get("editor_style_scheme") or ""
        if scheme != self._scheme_setting:
            self._scheme_setting = scheme
            self._apply_scheme()
        _apply_font_scale(self.get_display(), settings.get("pr_font_scale"))
        inline_images = bool(settings.get("pr_inline_images", True))
        if inline_images != self._inline_images:
            self._inline_images = inline_images
            # Bodies are built with the setting baked in, so the switch only
            # means anything once the cards are rebuilt around it.
            if self._detail is not None:
                self._rebuild()
                self._rebuild_files()

    def page_state(self) -> dict:
        """This page's slot in a serialized dock layout (see panellayout):
        the URL is the whole state — a restored page refetches the rest."""
        return {"kind": "pr", "url": self.pr_url}

    # -- fetching -------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the PR now — the Refresh button, and the dedupe path's
        "you asked for this page again"."""
        self._fetch(force=True)

    def refresh_if_stale(self) -> None:
        """Re-read the PR unless one just landed — the news that the world may
        have moved, rather than a demand for a fresh read.

        What the session's finish edge asks for (see
        TerminalTab.note_run_finished): nobody clicked anything, so the page
        keeps the same arrival throttle a re-map gets, and a run that ends
        seconds after the page was read is left with the answer it has.
        """
        self._fetch()

    def sync_summary(self) -> None:
        """Redraw the header from the status cache — the PR hub's news.

        The tab calls this whenever a fetch made anywhere changes what is
        known about this URL (see TerminalTab._on_hub_status_changed), the
        page's own fetch included: absorbing its reply is one of those
        fetches. `known` is a dictionary lookup, so this is main-loop safe
        and re-entry proof — it reads the cache and never asks for it to be
        filled, which is what keeps the page's fetch → absorb → hub → here
        chain from circling back into another fetch. The conversation and
        diff aren't touched: they live outside the summary cache, and re-
        reading them costs real gh calls the Refresh button (or the next
        map) is entitled to spend where a passing status change is not.
        """
        self._pr = known(self._pr)
        self._sync_header()
        self.emit("title-changed")  # the dock tab's icon tracks the state

    def reveal_unresolved(self) -> None:
        """Front the Conversation view at its first unresolved thread.

        The unresolved badge's deep link (prmenu's "View unresolved comments"
        row). Before the first fetch lands there is nothing to land on yet,
        so the ask waits for it (see `_landed`); when no thread is unresolved
        — the unanswered word was an issue comment — it lands at the
        conversation's end, where the newest word is.
        """
        if self._detail is None:
            self._pending_reveal = True
            return
        self._stack.set_visible_child_name("conversation")
        target = next(
            (card for thread, card in self._thread_cards if not thread.is_resolved),
            None,
        )
        self._scroll_to(self._scroller, target)

    def _fetch(self, force: bool = False) -> None:
        """Load the PR off the main loop. Unforced calls (map events) are
        throttled once something is loaded, so flipping between tabs doesn't
        hammer gh; a failure doesn't dodge the throttle either — the stamp is
        the attempt's, or a dead gh would be retried on every map."""
        if self._fetching:
            # A forced read is somebody asking what the PR looks like *after*
            # something they just did. The read in flight was dispatched
            # before it and can't answer that, so the ask is remembered and
            # re-issued when that one lands (see `_landed`) rather than
            # dropped — which would leave the page showing the state the
            # action changed, and let a held button go on that answer.
            self._refetch = self._refetch or force
            return
        now = GLib.get_monotonic_time()
        if not force and self._fetched_at and now - self._fetched_at < _FOCUS_REFRESH_MIN_US:
            return
        self._fetched_at = now
        self._fetching = True
        self._spinner.start()
        self._refresh_slot.set_visible_child_name("busy")
        self._fetch_gen += 1
        gen = self._fetch_gen
        url = self.pr_url

        def work() -> None:
            detail = None
            try:
                detail = prdetail.fetch(url)
            except Exception:  # a page must never take the app down
                log.debug("prview: fetch of %s failed", url, exc_info=True)
            GLib.idle_add(self._landed, gen, detail)

        threading.Thread(target=work, name="pr-view-fetch", daemon=True).start()

    def _landed(self, gen: int, detail: prdetail.PullRequestDetail | None) -> bool:
        if gen != self._fetch_gen:
            return GLib.SOURCE_REMOVE  # a newer fetch owns the page now
        self._fetching = False
        if self._refetch:
            # This answer is to a question asked before the action that has
            # since landed: it isn't dropped so much as unbelievable. Nothing
            # here renders it and nothing lets go of its spinner — the read
            # that was actually asked for goes out now, still spinning, and
            # that one settles the page.
            self._refetch = False
            self._fetch(force=True)
            return GLib.SOURCE_REMOVE
        self._spinner.stop()
        self._refresh_slot.set_visible_child_name("button")
        # Whatever this read says, the buttons that asked for it let go of
        # their spinners here: an action holds its own until the page it
        # changed has been re-read, and a failed read is still an answer.
        self._actions.settled()
        self._composer.settled()
        for card in self._cards:
            card.settled()
        if detail is None:
            self._banner_label.set_text(
                _("Couldn't load this pull request — is the GitHub CLI signed in?")
            )
            self._banner.set_reveal_child(True)
            if self._detail is None:
                self._show_empty()
                self._files_placeholder(_("Nothing loaded yet."))
            return GLib.SOURCE_REMOVE
        self._banner.set_reveal_child(False)
        self._detail = detail
        self._pr = detail.summary
        self._sync_header()
        self._rebuild()
        self._rebuild_files()
        self.emit("title-changed")
        if self._pending_reveal:
            # The deep link arrived before the page had anything to land on.
            self._pending_reveal = False
            self.reveal_unresolved()
        return GLib.SOURCE_REMOVE

    # -- header ---------------------------------------------------------------

    def _sync_header(self) -> None:
        pr = self._pr
        # Before the first fetch this is the summary the chip already held —
        # enough of a state for the buttons, so a draft opened from the footer
        # offers "Mark ready for review" while the fetch is still in flight.
        self._actions.sync(pr)
        self._mark_slot.set_child(prmenu.status_icon(pr))
        self._number.set_label(f"#{pr.number}")
        title = pr.title or pr.repository or _("Pull request")
        self._title.set_label(title)
        self._title.set_tooltip_text(title)
        detail = self._detail
        if detail is None:
            return
        self._sub.set_visible(True)
        # base ← head, the way GitHub draws the merge direction.
        self._branches.set_label(f"{detail.base_ref} ← {detail.head_ref}")
        self._branches.set_tooltip_text(
            _("Merges {head} into {base}").format(head=detail.head_ref, base=detail.base_ref)
        )
        self._additions.set_label(f"+{detail.additions}")
        self._deletions.set_label(f"−{detail.deletions}")
        self._files.set_label(
            ngettext("{n} file", "{n} files", detail.changed_files).format(
                n=detail.changed_files
            )
        )
        child = self._labels.get_first_child()
        while child is not None:
            self._labels.remove(child)
            child = self._labels.get_first_child()
        for name in detail.labels:
            pill = Gtk.Label(label=name)
            pill.add_css_class("pr-label-pill")
            pill.add_css_class("caption")
            self._labels.append(pill)

    # -- content --------------------------------------------------------------

    def _clear_content(self) -> None:
        child = self._content.get_first_child()
        while child is not None:
            self._content.remove(child)
            child = self._content.get_first_child()

    def _show_loading(self) -> None:
        self._clear_content()
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(24, 24)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.set_margin_top(24)
        self._content.append(spinner)

    def _show_empty(self) -> None:
        self._clear_content()
        label = Gtk.Label(label=_("Nothing loaded yet."))
        label.add_css_class("dim-label")
        label.set_margin_top(24)
        self._content.append(label)

    def _rebuild(self) -> None:
        detail = self._detail
        self._clear_content()
        self._thread_cards = []
        # Cleared here rather than in _rebuild_files: the two always run as a
        # pair, this one first, and the files view's twins join the same list.
        self._cards = []
        # Drafts for threads that no longer exist have nowhere to go back to.
        alive = {thread.id for thread in detail.threads}
        for gone in [key for key in self._thread_drafts if key not in alive]:
            del self._thread_drafts[gone]
        self._content.append(self._description_card(detail))
        if detail.checks:
            self._content.append(self._checks_section(detail.checks))
        if detail.timeline:
            heading = Gtk.Label(label=_("Conversation"), xalign=0.0)
            heading.add_css_class("caption-heading")
            self._content.append(heading)
            for entry in detail.timeline:
                if isinstance(entry, prdetail.PrThread):
                    card = self._thread_card(entry)
                    self._thread_cards.append((entry, card))
                    self._content.append(card)
                elif isinstance(entry, prdetail.PrReview):
                    self._content.append(self._review_card(entry))
                else:
                    self._content.append(self._comment_card(entry))
        else:
            empty = Gtk.Label(label=_("No comments yet."), xalign=0.0)
            empty.add_css_class("dim-label")
            self._content.append(empty)
        self._composer.sync(self._pr, detail.viewer_is_author)
        self._content.append(self._composer)

    def _acted(self) -> None:
        """Something the page did just landed on GitHub — a comment, a review,
        or one of the header buttons' actions: re-read everything that shows
        this PR, the page itself (whose fetch re-absorbs into the summary
        cache) and the summary the tab's own poll holds."""
        invalidate(self.pr_url)
        self._host_factory().refresh()
        self.refresh()

    def _description_card(self, detail: prdetail.PullRequestDetail) -> Gtk.Widget:
        card = _card(detail.author, detail.created_at)
        if detail.body:
            card.append(_folded_body(detail.body, self._inline_images))
        else:
            none = Gtk.Label(label=_("No description provided."), xalign=0.0)
            none.add_css_class("dim-label")
            card.append(none)
        return card

    def _checks_section(self, checks) -> Gtk.Widget:
        """The rollup as a list, folded past _CHECK_ROWS_SHOWN rows.

        A repository can put fifty contexts on one pull request, and fifty
        rows here would push the description off the top of the panel and the
        conversation off the bottom — this section says how the checks stand,
        it isn't where they are all read. Past the cap the rest waits behind
        "Show more" (`_fold`, the same step the description takes) and the
        heading starts carrying the count, since a list cut short with
        nothing saying so reads as a list that short.

        A folded list also reorders, blockers first (`prdetail.by_urgency`):
        the rows that get folded away should be the ones with nothing to say,
        not the two failures that happened to be numbered eleven and nineteen.
        An unfolded one keeps gh's own order — every row is on screen, so
        there is nothing for a reshuffle to rescue.

        The heading and the repair button stay outside the fold: what is
        offered about the checks must not hide with them.
        """
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        folded = len(checks) > _CHECK_ROWS_SHOWN
        heading = Gtk.Label(
            label=_("Checks ({n})").format(n=len(checks)) if folded else _("Checks"),
            xalign=0.0,
        )
        heading.add_css_class("caption-heading")
        heading.set_margin_bottom(2)
        section.append(heading)
        ordered = prdetail.by_urgency(checks) if folded else checks
        shown = self._check_rows(ordered[:_CHECK_ROWS_SHOWN])
        # The full list is built afresh rather than growing the preview: a
        # widget has one parent, and the fold swaps whole boxes.
        section.append(_fold(shown, self._check_rows(ordered)) if folded else shown)
        repair = self._repair_button()
        if repair is not None:
            section.append(repair)
        return section

    def _check_rows(self, checks) -> Gtk.Box:
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=_CHECK_ROW_GAP)
        for check in checks:
            rows.append(self._check_row(check))
        return rows

    def _repair_button(self) -> Gtk.Widget | None:
        """The offer to make the agent clear whatever is blocking the merge —
        or None where nothing is.

        Under the list rather than beside the row it is about: a PR can fail
        several checks and conflict as well, and one button asking for all of
        it in the right order (practions.repair_action) is the offer, not one
        per red row. It says the action's `short` wording, with the prompt it
        would send in full on its tooltip, and wears Claude's mark like the
        composer's own agent button — this is the session's work, not gh's.

        Greyed with the reason when the session can't be typed into just now,
        the blocked action rows' treatment: the tooltip goes on a sensitive
        wrapper, since GTK skips insensitive widgets when it picks what the
        pointer is over.
        """
        pr = self._pr
        action = practions.repair_action(pr, self._host_factory().prompt_block())
        if action is None:
            return None
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        content.append(Gtk.Image.new_from_icon_name("agent-claude-symbolic"))
        content.append(Gtk.Label(label=action.short or action.label))
        button = Gtk.Button(child=content)
        button.add_css_class("flat")
        button.set_sensitive(not action.blocked)
        button.connect("clicked", self._on_repair, action.prompt)
        wrap = Gtk.Box()
        wrap.append(button)
        wrap.set_halign(Gtk.Align.START)
        wrap.set_margin_top(4)
        wrap.set_tooltip_text(
            "\n".join(part for part in (action.tooltip, action.blocked) if part)
        )
        return wrap

    def _on_repair(self, _button: Gtk.Button, prompt: str) -> None:
        host = self._host_factory()
        if host.prompt_block():  # sampled again: the section was built a fetch ago
            return
        host.send_prompt(prompt)

    def _check_row(self, check: prdetail.PrCheck) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(prmenu.check_image(check.state))
        name = Gtk.Label(label=check.name, xalign=0.0, hexpand=True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        row.append(name)
        if not check.url:
            row.add_css_class("pr-check-row")
            return row
        # The row is the link: logs and details stay on GitHub in v1.
        button = Gtk.Button(child=row)
        button.add_css_class("flat")
        button.add_css_class("pr-check-row")
        button.set_tooltip_text(open_tooltip(check.url))
        button.connect("clicked", lambda b, url=check.url: open_uri(b, url))
        return button

    def _comment_card(self, comment: prdetail.PrComment) -> Gtk.Widget:
        card = _card(comment.author, comment.created_at, url=comment.url)
        card.append(_body_label(comment.body, self._inline_images))
        return card

    def _review_card(self, review: prdetail.PrReview) -> Gtk.Widget:
        icon_name, css_class, verdict_text = _verdict(review.state)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(prmenu.MERGED_ICON_PX)
        verdict = Gtk.Label(label=verdict_text, xalign=0.0)
        verdict.add_css_class("caption")
        if css_class is not None:
            icon.add_css_class(css_class)
            verdict.add_css_class(css_class)
        else:
            icon.add_css_class("dim-label")
            verdict.add_css_class("dim-label")
        card = _card(review.author, review.created_at, trailing=[icon, verdict])
        if review.body:
            card.append(_body_label(review.body, self._inline_images))
        return card

    def _thread_card(self, thread: prdetail.PrThread) -> _ThreadCard:
        """One review thread as a card, wired to this page's PR, drafts and
        post-refresh. Built per view — a widget has one parent, and a thread
        shows in both — so the shared draft dict is what keeps the copies
        agreeing on a half-typed reply, and the shared busy set what keeps
        them from mutating the same thread twice."""
        card = _ThreadCard(
            thread,
            self._pr,
            self._thread_drafts,
            self._thread_busy,
            self._acted,
            self._inline_images,
        )
        # Every card built for this page, both views' copies: a mutation holds
        # its card until the re-read it asked for lands, and the page is what
        # tells them it did (see `_landed`).
        self._cards.append(card)
        return card

    # -- the files view --------------------------------------------------------

    def _clear_files(self) -> None:
        self._sections = []
        child = self._files_column.get_first_child()
        while child is not None:
            self._files_column.remove(child)
            child = self._files_column.get_first_child()
        row = self._file_list.get_row_at_index(0)
        while row is not None:
            self._file_list.remove(row)
            row = self._file_list.get_row_at_index(0)

    def _files_placeholder(self, text: str) -> None:
        self._clear_files()
        label = Gtk.Label(label=text)
        label.add_css_class("dim-label")
        label.set_margin_top(24)
        self._files_column.append(label)

    def _files_loading(self) -> None:
        """The Conversation column's first-load spinner, Files flavored —
        so a slow first fetch doesn't leave this tab looking idle."""
        self._clear_files()
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(24, 24)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.set_margin_top(24)
        self._files_column.append(spinner)

    def _rebuild_files(self) -> None:
        detail = self._detail
        self._clear_files()
        if not detail.files:
            self._files_placeholder(_("No changed files."))
            return
        scheme = style_scheme(self._scheme_setting, self._dark)
        for file in detail.files:
            section = _FileSection(file, scheme, detail, self._inline_images)
            # The file's threads hang under its diff, top of the file first —
            # visible even while a large patch stays collapsed.
            for thread in prdetail.file_threads(detail.threads, file.path):
                section.append(self._thread_card(thread))
            self._sections.append(section)
            self._files_column.append(section)
            self._file_list.append(self._file_row(file))

    def _file_row(self, file: prdetail.PrFile) -> Gtk.Widget:
        """One navigation-list row: the path, then its +/− counts."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        path = Gtk.Label(label=file.path, xalign=0.0, hexpand=True)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path.set_tooltip_text(file.path)
        path.add_css_class("caption")
        row.append(path)
        row.append(_count_label(f"+{file.additions}", "pr-checks-passed"))
        row.append(_count_label(f"−{file.deletions}", "pr-checks-failed"))
        return row

    def _on_file_row(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if not 0 <= index < len(self._sections):
            return
        section = self._sections[index]
        section.reveal()  # a collapsed big patch was asked for by name
        self._scroll_to_section(section)

    def _scroll_to_section(self, section: Gtk.Widget) -> None:
        """Put *section*'s top at the top of the Files scroll."""
        self._scroll_to(self._files_scroller, section)

    def _scroll_to(self, scroller: Gtk.ScrolledWindow, widget: Gtk.Widget | None) -> None:
        """Put *widget*'s top at the top of *scroller* — or, with None, land
        at the very end of the scroll.

        Placed twice: a just-built (or just-expanded) buffer reports
        estimated heights first, so the first placement lands short — the
        PRIORITY_LOW re-issue runs after layout settles and corrects it
        (the scroll_to_iter lesson from the editor, box-scroll flavored).
        """

        def place() -> bool:
            adj = scroller.get_vadjustment()
            end = adj.get_upper() - adj.get_page_size()
            if widget is None:
                adj.set_value(max(0.0, end))
                return GLib.SOURCE_REMOVE
            ok, bounds = widget.compute_bounds(scroller)
            if ok:
                target = adj.get_value() + bounds.get_y()
                adj.set_value(max(0.0, min(target, end)))
            return GLib.SOURCE_REMOVE

        place()
        GLib.idle_add(place, priority=GLib.PRIORITY_LOW)

    # -- appearance ------------------------------------------------------------

    def _on_dark_changed(self, manager: Adw.StyleManager, _pspec) -> None:
        self._dark = manager.get_dark()
        if not self._scheme_setting:  # "" = following the app's scheme
            self._apply_scheme()

    def _apply_scheme(self) -> None:
        scheme = style_scheme(self._scheme_setting, self._dark)
        for section in self._sections:
            section.set_scheme(scheme)

    def _on_destroy(self, *_args) -> None:
        # The style manager outlives any page; a closed one must let go.
        if self._dark_id is not None:
            Adw.StyleManager.get_default().disconnect(self._dark_id)
            self._dark_id = None
        # So does the hub.
        if self._hub_status_id is not None:
            self._pr_store.disconnect(self._hub_status_id)
            self._hub_status_id = None

    def _on_hub_status_changed(self, _hub, url: str) -> None:
        """What is known about a PR moved — if it is this one, re-read the page.

        Most often the footer's poll: a probe between fetches saw a check
        finish and the full fetch that followed landed a new status (see
        prstatus.probe). A fetch of our own reports through here too — the
        detail reply is absorbed as status before `_landed` runs — and is
        told apart by the read still being in flight: re-reading on its
        account would be asking the same question twice. The page must be on
        screen to bother; an unmapped one re-reads when it is next shown.
        """
        if url != self.pr_url or self._fetching or not self.get_mapped():
            return
        self._fetch(force=True)


class _WrapLayout(Gtk.LayoutManager):
    """A horizontal box's layout, but one that wraps onto further lines.

    Children keep their natural width and their order; what changes is that a
    line too narrow to hold the rest starts a new one. Space left over on a
    line goes to whatever on it expands (a `Gtk.Box(hexpand=True)` used as a
    spacer, exactly as in a `Gtk.Box`), and to the line's end when nothing
    does — these are action rows, and buttons hug that edge. Every line is
    laid out from the start edge and mirrored whole under an RTL direction,
    which is the mirroring a box would have done for free.

    The point is the minimum: a box's is the sum of its children's, which is
    how a row of four buttons ends up dictating how narrow a whole panel page
    can be squeezed (see _MIN_PAGE_WIDTH). Wrapping makes it the *widest
    single child* instead, and pays for it in height, which a scrolling column
    has to spare.

    Hand-rolled rather than `Adw.WrapBox` on purpose: that arrived in
    libadwaita 1.7, and the oldest distribution Collins targets (noble, which
    the PPA builds for) ships 1.5. Don't "simplify" this into it.
    """

    __gtype_name__ = "CollinsPrWrapLayout"

    def __init__(self, spacing: int = 6, row_spacing: int = 6) -> None:
        super().__init__()
        self._spacing = spacing
        self._row_spacing = row_spacing

    def _lines(self, widget: Gtk.Widget, width: int) -> list[list[tuple[Gtk.Widget, int]]]:
        """The visible children packed greedily into lines of *width*, each
        paired with the width it asked for. A child wider than the line gets
        one to itself rather than being dropped."""
        lines: list[list[tuple[Gtk.Widget, int]]] = []
        line: list[tuple[Gtk.Widget, int]] = []
        used = 0
        for child in widget:
            if not child.get_visible():
                continue
            natural = child.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            if line and used + self._spacing + natural > width:
                lines.append(line)
                line, used = [], 0
            used += natural + (self._spacing if line else 0)
            line.append((child, natural))
        if line:
            lines.append(line)
        return lines

    def _line_height(self, line: list[tuple[Gtk.Widget, int]]) -> int:
        return max((c.measure(Gtk.Orientation.VERTICAL, w)[1] for c, w in line), default=0)

    def do_get_request_mode(self, _widget: Gtk.Widget) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(
        self, widget: Gtk.Widget, orientation: Gtk.Orientation, for_size: int
    ) -> tuple[int, int, int, int]:
        children = [child for child in widget if child.get_visible()]
        if orientation == Gtk.Orientation.HORIZONTAL:
            # Every line holds at least one child, so the narrowest the row can
            # be drawn is the widest child's own minimum; the natural width is
            # still the whole row on one line.
            minimum = max(
                (c.measure(orientation, -1)[0] for c in children),
                default=0,
            )
            natural = sum(c.measure(orientation, -1)[1] for c in children)
            natural += self._spacing * max(len(children) - 1, 0)
            return (minimum, max(natural, minimum), -1, -1)
        width = for_size
        if width < 0:  # asked height-for-any-width: answer for one line
            width = self.do_measure(widget, Gtk.Orientation.HORIZONTAL, -1)[1]
        lines = self._lines(widget, width)
        height = sum(self._line_height(line) for line in lines)
        height += self._row_spacing * max(len(lines) - 1, 0)
        return (height, height, -1, -1)

    def do_allocate(self, widget: Gtk.Widget, width: int, height: int, _baseline: int) -> None:
        rtl = widget.get_direction() == Gtk.TextDirection.RTL
        y = 0
        for index, line in enumerate(self._lines(widget, width)):
            if index:
                y += self._row_spacing
            line_height = self._line_height(line)
            spare = width - sum(w for _, w in line) - self._spacing * (len(line) - 1)
            spare = max(spare, 0)
            growers = [child for child, _ in line if child.get_hexpand()]
            # Nothing to expand: the line's slack goes in front of it, which
            # pins the buttons to its end the way the un-wrapped row's own
            # spacer does. The remainder rides the last grower, so the row
            # still ends flush against that edge.
            x = 0 if growers else spare
            share = spare // len(growers) if growers else 0
            for child, natural in line:
                child_width = natural
                if child in growers:
                    child_width += share
                    if child is growers[-1]:
                        child_width += spare - share * len(growers)
                child_width = max(child_width, child.measure(Gtk.Orientation.HORIZONTAL, -1)[0])
                # RTL mirrors the line about its middle, first child at the
                # right edge — what Gtk.Box does with the same children.
                left = width - x - child_width if rtl else x
                transform = Gsk.Transform().translate(Graphene.Point().init(left, y))
                child.allocate(child_width, line_height, -1, transform)
                x += child_width + self._spacing
            y += line_height


class _WrapRow(Gtk.Widget):
    """A button row under _WrapLayout — `append` in order, as with a box."""

    __gtype_name__ = "CollinsPrWrapRow"

    def __init__(self, spacing: int = 6) -> None:
        super().__init__()
        self.set_layout_manager(_WrapLayout(spacing))

    def append(self, child: Gtk.Widget) -> None:
        child.set_parent(self)

    def do_dispose(self) -> None:
        # A plain Gtk.Widget doesn't unparent its children for us, and GTK
        # warns loudly about the ones still parented when it finalizes.
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            child.unparent()
            child = following
        Gtk.Widget.do_dispose(self)


class _BusyButton(Gtk.Button):
    """A button that spins in place of its own word while its action runs.

    The word and the spinner are two pages of one Stack, which measures as the
    larger of the two whichever is showing: the button keeps the width it had
    when it was pressed, so the row it sits on doesn't reshuffle under the
    pointer for the second or two gh takes. And the press reads as *this*
    button working — which a lone spinner parked at the end of the row never
    quite said.

    *child* is for the buttons whose word comes with something else (the
    Claude button's icon): the whole of it swaps for the spinner, and
    `set_word` is then the caller's own business.
    """

    __gtype_name__ = "CollinsPrBusyButton"

    def __init__(self, label: str = "", child: Gtk.Widget | None = None) -> None:
        super().__init__()
        self._word = Gtk.Label(label=label) if child is None else child
        self._spinner = Gtk.Spinner()
        self._spinner.set_halign(Gtk.Align.CENTER)
        self._spinner.set_valign(Gtk.Align.CENTER)
        self._stack = Gtk.Stack()
        self._stack.add_named(self._word, "word")
        self._stack.add_named(self._spinner, "busy")
        self.set_child(self._stack)

    def set_word(self, label: str) -> None:
        """Say *label* — `set_label` would throw the stack away."""
        self._word.set_label(label)

    def set_busy(self, busy: bool) -> None:
        self._stack.set_visible_child_name("busy" if busy else "word")
        self._spinner.set_spinning(busy)


class _ActionBar(Gtk.Box):
    """The button that moves the pull request along, beside the view switcher.

    Whatever `practions.header_actions` says the PR's state offers — one
    action at a time, or none at all: a merged PR, a closed one, a
    conflicting one, or one nothing has been fetched for yet show no button
    rather than a dead one. What is shown is what Collins recommends doing
    next about this PR (the merge that fits its checks, not both merges to
    choose between), so it wears the accent — except the merges, immediate and
    auto alike, which wear GitHub's own merge green (app.py's _SCHEME_CSS), the
    color that button has on the pull request's page, and "Disable Auto-Merge",
    which wears neither: it takes a decision back rather than moving the pull
    request anywhere.

    The button is `practions.perform` on a worker thread behind the merge's
    own confirmation dialog, spinning where its word was and the bar held
    insensitive until the answer lands (one press, one merge). A failure is
    gh's own sentence in a dialog; success is quiet, and *on_done* re-reads
    the PR — which is what takes the button away, since the state it was
    offered for has just changed. The spinner outlives the merge itself and
    stops on that re-read (`settled`): the button is the same width all the
    way through, and never comes back live for the moment between the two.

    It says the action's `short` wording — "Merge", not "Merge pull request" —
    with the full sentence on its tooltip: this row shares a line with the
    view switcher, and what it can afford there is a word.

    A right-click on it opens what the button deliberately isn't offering
    (`practions.alternate_actions`): closing the pull request instead of
    landing it, merging and archiving the session that opened it, where
    the button says "Disable Auto-Merge" the immediate merge that button has
    stopped offering, and where it says "Ready" the shortcut straight to
    landed — ready-and-merged as one pick, with the archive behind it too.
    All are the *end* of this pull request, which
    is what makes them belong on this button rather than anywhere else on the
    page, and none is one to hand a stray click: they ask first, they open
    behind a right-click, and the tooltip says the menu is there. The
    archive-merges archive only once gh comes back without an error — the
    merge is GitHub's half and the archive is the app's, in that order (see
    `_landed`).

    A state can offer alternates and no button to hang them off: an open pull
    request whose branch conflicts is offered no merge at all, and it is the
    likeliest one of all to be closed. There the bar draws a flat ellipsis in
    the button's place, opening the same menu on a plain click — the only way
    "close this pull request" is reachable for as long as `alternate_actions`
    says it is offered.

    The row is a _WrapRow for the same reason the composer's is: its minimum
    is its widest single child rather than the sum, so nothing on it can
    become a width the whole panel page can't go under (_MIN_PAGE_WIDTH).
    """

    def __init__(self, host_factory, on_done: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._host_factory = host_factory
        self._on_done = on_done
        self._pr: PullRequest | None = None
        self._actions: dict[str, practions.Action] = {}
        # What a right-click offers instead, as the last sync worked it out.
        self._alternates: list[practions.Action] = []
        self._running = False
        # Whether the bar is waiting on the page's re-read of an action of its
        # own that landed — what `settled` is allowed to let go of. A fetch
        # that lands for any other reason (the page came back into view while
        # a merge is still running) must not.
        self._holding = False

        # No spacer at the head of the row: the switcher's CenterBox already
        # pins the whole bar to the page's end, and a wrapped line with nothing
        # expanding on it is laid out flush to that same edge (_WrapLayout).
        row = _WrapRow()
        # Built once and shown by key, rather than rebuilt per sync: the set is
        # closed (four actions, at most one of them showing at a time), and a
        # button that only ever changes its visibility can't lose a click to a
        # rebuild landing under the pointer.
        self._buttons: dict[str, _BusyButton] = {}
        for key in (
            practions.READY,
            practions.AUTO_MERGE,
            practions.MERGE,
            practions.DISABLE_AUTO_MERGE,
        ):
            button = _BusyButton()
            # Either merge is green rather than accent-colored (app.py's
            # _SCHEME_CSS), the way both are on the pull request's own page —
            # auto-merge included, since it is the same act on a delay.
            # "Ready for review" is the one recommendation left wearing the
            # accent: it is what moves a draft along, not what lands it.
            # Turning auto-merge back off wears neither: it takes a decision
            # back rather than moving the pull request anywhere, and a plain
            # button is what an undo looks like.
            if key in practions.MERGES:
                button.add_css_class("pr-merge-action")
            elif key != practions.DISABLE_AUTO_MERGE:
                button.add_css_class("suggested-action")
            button.connect("clicked", self._on_clicked, key)
            # GtkButton answers the primary button and only that, so this
            # never doubles up with the click above it.
            self._add_menu_gesture(button)
            row.append(button)
            self._buttons[key] = button
        # Where the alternates go when the state offers no button to hang them
        # off: an open PR whose branch conflicts is offered no merge anywhere
        # (GitHub would refuse it), and that is the state closing the pull
        # request is most often the answer to. Flat and quiet — it is not a
        # course Collins is recommending, it is the way to the ones it keeps
        # behind the button that isn't there.
        self._more = _BusyButton(child=Gtk.Image.new_from_icon_name("view-more-symbolic"))
        self._more.add_css_class("flat")
        self._more.set_tooltip_text(_("More actions"))
        self._more.connect("clicked", lambda button: self._open_menu(button))
        self._add_menu_gesture(self._more)
        row.append(self._more)
        self.append(row)
        self.set_visible(False)

    def _add_menu_gesture(self, button: _BusyButton) -> None:
        """Open the alternates on a right-click. GtkButton answers the primary
        button and only that, so this never doubles up with a plain click."""
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_secondary)
        button.add_controller(secondary)

    def sync(self, pr: PullRequest) -> None:
        """Show what *pr*'s state offers now, as freshly fetched."""
        self._pr = pr
        self._actions = {action.key: action for action in practions.header_actions(pr)}
        self._alternates = practions.alternate_actions(
            pr, can_archive=self._host_factory().archive is not None
        )
        for key, button in self._buttons.items():
            action = self._actions.get(key)
            button.set_visible(action is not None)
            if action is None:
                continue
            button.set_word(action.short or action.label)
            tooltip = action.tooltip
            if self._alternates:
                # A context menu nothing points at is a context menu nobody
                # finds: the tooltip that already says what the button does
                # says where the rest of it is.
                tooltip += "\n" + _("Right-click for more actions")
            button.set_tooltip_text(tooltip)
        # The ellipsis only stands in for a button that isn't there: with one
        # showing, the alternates are a right-click away from it and a second
        # control saying the same thing is one control too many.
        self._more.set_visible(not self._actions and bool(self._alternates))
        self.set_visible(bool(self._actions) or self._more.get_visible())

    def settled(self) -> None:
        """The page's re-read has landed: the pressed button lets its word
        back and the bar goes live again.

        Called by the page rather than by the action's own landing, and that
        is the point — a merge that worked is followed by the fetch that takes
        the button away, and a bar that came back to life in between offered a
        second press of an action that had already happened. Only a read this
        bar is waiting on releases it: any other fetch may land mid-merge."""
        if self._holding:
            self._release()

    def _release(self) -> None:
        self._holding = False
        self._running = False
        self.set_sensitive(True)
        for button in (*self._buttons.values(), self._more):
            button.set_busy(False)

    def _on_clicked(self, button: Gtk.Button, key: str) -> None:
        pr = self._pr
        action = self._actions.get(key)
        if pr is None or action is None or self._running:
            return
        self._pick(button, pr, action)

    def _on_secondary(
        self, gesture: Gtk.GestureClick, _n_press: int, _x: float, _y: float
    ) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._open_menu(gesture.get_widget())

    def _open_menu(self, button: Gtk.Widget) -> None:
        """Show the alternates under *button* — the courses it isn't taking.

        Built fresh on each opening and let go of when it closes, like the
        footer chips' menus: what the alternates are follows the PR's state,
        and a popover outliving the sync that changed them would be offering
        yesterday's answer.
        """
        pr = self._pr
        if pr is None or self._running or not self._alternates:
            return
        popover = prmenu.new_popover(Gtk.PositionType.BOTTOM)
        popover.set_parent(button)
        popover.connect("closed", lambda menu: GLib.idle_add(menu.unparent))
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for action in self._alternates:
            row = prmenu.action_button(action)
            row.connect("clicked", self._on_alternate, popover, pr, action, button)
            rows.append(row)
        popover.set_child(rows)
        popover.popup()

    def _on_alternate(
        self,
        _row: Gtk.Button,
        popover: Gtk.Popover,
        pr: PullRequest,
        action: practions.Action,
        button: _BusyButton,
    ) -> None:
        """An alternate was picked: the menu goes first, then the same
        confirm-and-run the button itself takes. The menu can't stay either
        way — a dialog takes the pointer grab a popover is holding."""
        popover.popdown()
        self._pick(button, pr, action)

    def _pick(self, button: _BusyButton, pr: PullRequest, action: practions.Action) -> None:
        """Ask, if *action* asks, and then run it — spinning in *button*,
        which is the one that was pressed however the action was reached.

        Whether it asks is `practions.confirmation`'s answer, the same one the
        menus take: the merges' dialog can be turned off in Preferences, and
        the page's button and a chip's menu must not disagree about that."""
        if self._running:
            return
        confirm = practions.confirmation(action, self._host_factory().confirm_merges())
        if confirm is None:
            self._start(pr, action, button)
            return
        dialogs.confirm_dialog(
            button.get_root(),
            confirm.heading,
            confirm.body,
            confirm.label,
            lambda: self._start(pr, action, button),
            destructive=confirm.destructive,
            confirm_class=(
                practions.MERGE_CONFIRM_CSS if action.key in practions.MERGES else None
            ),
        )

    def _start(self, pr: PullRequest, action: practions.Action, button: _BusyButton) -> None:
        """Run *action* off the main loop — gh takes a second or two over a
        merge, and the bar says so meanwhile rather than looking ignored."""
        if self._running:  # the dialog's answer could be the second one
            return
        self._running = True
        self.set_sensitive(False)
        button.set_busy(True)

        def work() -> None:
            try:
                error = practions.perform(action.key, pr)
            except Exception:  # a button must never take the app down with it
                log.debug("prview: %s on %s failed", action.key, pr.url, exc_info=True)
                error = _("Collins couldn't run that action.")
            GLib.idle_add(self._landed, action, error)

        threading.Thread(target=work, name="pr-view-action", daemon=True).start()

    def _landed(self, action: practions.Action, error: str | None) -> bool:
        if error:
            self._release()  # nothing is re-reading the page: let go here
            root = self.get_root()
            if root is not None:
                dialogs.error_dialog(
                    root, _("{action} failed").format(action=action.label), error
                )
            return GLib.SOURCE_REMOVE
        # Still spinning, still held: _on_done re-reads the PR, and what this
        # bar offers next is that read's answer (see `settled`).
        self._holding = True
        self._on_done()
        if action.key in practions.MERGE_ARCHIVES:
            # The merge landed, so the session behind it can go — this and no
            # sooner (see practions.merge_archive_action). Last, after the
            # re-read has been asked for: archiving closes the session's tab,
            # and this page goes down with it.
            archive = self._host_factory().archive
            if archive is not None:
                archive()
        return GLib.SOURCE_REMOVE


class _Composer(Gtk.Box):
    """The Conversation view's write half: a comment box and its verdicts.

    One per page, created once and re-appended across rebuilds — the rebuild
    that lands a background refresh must not eat a half-typed comment. The
    buttons run practions' write calls on a worker thread, the pressed one
    spinning where its word was and the whole composer held insensitive until
    the answer lands (one press, one post): Comment posts the text as an issue
    comment, Approve / Request changes submit a review with the text along.
    Comment and Request changes need words to go — GitHub refuses both bare —
    so they grey out over an empty box; Approve stands alone. A failure comes
    back as gh's own sentence in a dialog, the text kept where it was typed;
    success clears the box and re-reads the PR, the spinner running on until
    that read lands (`settled`) so the row holds still while the timeline
    above it is still catching up with what was just posted.

    The two verdicts are only there to be pressed on somebody else's pull
    request: GitHub won't take a review of your own, so on a PR the signed-in
    account opened (see prdetail's `viewer_is_author`) they aren't drawn at
    all — a button whose only possible answer is a refusal is worse than no
    button. Commenting is the half that is always yours to do.

    The Claude button beside them is the complement, not a competitor, and
    which complement depends on who is waiting: "Address comments" while
    somebody's word is unanswered, typing the COMMENTS prompt into the owning
    session (greyed with the reason whenever it can't take a prompt right now
    — the blocked action rows' treatment, tooltip on a sensitive wrapper and
    all), and "Request review" when nobody is, which posts `@claude review`
    on the PR and so needs no session at all. See `_sync_claude`.

    Its buttons sit in a row that wraps (_WrapRow): they read the same at any
    width the panel is dragged to, and none of them may quietly become the
    minimum width of the whole page.
    """

    def __init__(
        self, host_factory: Callable[[], prmenu.ActionHost], on_posted: Callable[[], None]
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("pr-card")
        self._host_factory = host_factory
        self._on_posted = on_posted
        self._pr: PullRequest | None = None
        self._posting = False
        # The button whose press is in flight — the one wearing the spinner,
        # until `settled` gives it its word back.
        self._busy_btn: _BusyButton | None = None
        # Whether the composer is waiting on the page's re-read of a post of
        # its own — what `settled` may release. A fetch landing for any other
        # reason while a post is still in flight must not.
        self._holding = False
        # Which of its two jobs the Claude button is doing, as one of
        # practions' keys — set by every sync, and "" until the first one.
        self._claude_key = ""

        heading = Gtk.Label(label=_("Add a comment"), xalign=0.0)
        heading.add_css_class("caption-heading")
        self.append(heading)

        self._text = Gtk.TextView()
        self._text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text.set_top_margin(6)
        self._text.set_bottom_margin(6)
        self._text.set_left_margin(8)
        self._text.set_right_margin(8)
        self._text.get_buffer().connect("changed", lambda *_a: self._sync_buttons())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self._text.add_controller(keys)
        entry = Gtk.ScrolledWindow(child=self._text)
        entry.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        entry.set_has_frame(True)
        # Room enough to read a few lines back; grows with the text between
        # the two bounds, then scrolls rather than pushing the buttons away.
        entry.set_min_content_height(64)
        entry.set_max_content_height(220)
        entry.set_propagate_natural_height(True)
        self.append(entry)

        # Wrapping rather than a box: four buttons side by side would otherwise
        # be the page's minimum width, and a panel could never be squeezed
        # narrower than they are wide (see _WrapLayout, _MIN_PAGE_WIDTH).
        row = _WrapRow()
        claude = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        claude.append(Gtk.Image.new_from_icon_name("agent-claude-symbolic"))
        self._claude_label = Gtk.Label()
        claude.append(self._claude_label)
        self._reply_btn = _BusyButton(child=claude)
        self._reply_btn.add_css_class("flat")
        self._reply_btn.connect("clicked", self._on_claude)
        # Insensitive widgets are skipped when GTK picks what the pointer is
        # over (the blocked action rows' lesson): the reason a greyed button
        # is grey lives on this wrapper, which stays sensitive.
        self._reply_wrap = Gtk.Box()
        self._reply_wrap.append(self._reply_btn)
        self._reply_wrap.set_visible(False)  # until a sync says which job it has
        row.append(self._reply_wrap)
        row.append(Gtk.Box(hexpand=True))
        self._request_btn = _BusyButton(label=_("Request changes"))
        self._request_btn.connect(
            "clicked",
            lambda *_a: self._post(
                self._request_btn,
                _("Request changes"),
                lambda pr, body: practions.review(pr, practions.REQUEST_CHANGES, body),
            ),
        )
        row.append(self._request_btn)
        self._approve_btn = _BusyButton(label=_("Approve"))
        self._approve_btn.connect(
            "clicked",
            lambda *_a: self._post(
                self._approve_btn,
                _("Approve"),
                lambda pr, body: practions.review(pr, practions.APPROVE, body),
            ),
        )
        row.append(self._approve_btn)
        self._comment_btn = _BusyButton(label=_("Comment"))
        self._comment_btn.add_css_class("suggested-action")
        self._comment_btn.connect(
            "clicked",
            lambda *_a: self._post(self._comment_btn, _("Comment"), practions.comment),
        )
        row.append(self._comment_btn)
        self.append(row)
        self._sync_buttons()

    def sync(self, pr: PullRequest, viewer_is_author: bool = False) -> None:
        """Point the composer at *pr* as freshly fetched, and re-read the
        session behind it. Verdicts only show for a live PR that somebody else
        opened — GitHub refuses a review on a merged or closed one, and
        refuses your own pull request's approval whatever state it is in.
        Commenting stays open in every case."""
        self._pr = pr
        verdicts = pr.state in practions.LIVE and not viewer_is_author
        self._approve_btn.set_visible(verdicts)
        self._request_btn.set_visible(verdicts)
        self._comment_btn.set_tooltip_text(_("Comment on {slug}").format(slug=pr.slug))
        self._approve_btn.set_tooltip_text(_("Approve {slug}").format(slug=pr.slug))
        self._request_btn.set_tooltip_text(
            _("Request changes on {slug}").format(slug=pr.slug)
        )
        self._sync_claude(pr)
        self._sync_buttons()

    def _sync_claude(self, pr: PullRequest) -> None:
        """Point the Claude button at whichever of its two jobs this PR is in
        want of — and hide it when neither is.

        Someone waiting on a reply is what the button is most often for, and
        that one is the session's work: it types the COMMENTS prompt into the
        terminal, greyed with the reason whenever the session can't take a
        prompt right now. With nobody waiting, the useful ask is the other
        direction — a review, which lives on the PR as `@claude review` and so
        goes through gh rather than the session (practions.review_action), and
        needs no terminal at all.

        Neither applies to a settled PR, and the review doesn't apply while
        Claude's own review is still the newest thing here — the menu's rule
        (see practions.actions_for), for the same reason: asking twice under
        an unanswered answer is the app repeating itself.
        """
        self._claude_key = ""
        if pr.state not in practions.LIVE:
            self._reply_wrap.set_visible(False)
            return
        if pr.awaiting_reply:
            prompt = practions.COMMENTS_PROMPT.format(number=pr.number)
            block = self._host_factory().prompt_block()
            self._claude_key = practions.COMMENTS
            self._claude_label.set_label(_("Address comments"))
            self._reply_btn.set_sensitive(not block)
            tooltip = _("Send “{prompt}” to this session").format(prompt=prompt)
            self._reply_wrap.set_tooltip_text(
                "\n".join(part for part in (tooltip, block) if part)
            )
            self._reply_wrap.set_visible(True)
            return
        if pr.claude_had_the_last_word:
            self._reply_wrap.set_visible(False)
            return
        action = practions.review_action(pr)
        self._claude_key = practions.REVIEW
        self._claude_label.set_label(_("Request review"))
        self._reply_btn.set_sensitive(True)  # gh's business; no session needed
        self._reply_wrap.set_tooltip_text(action.tooltip)
        self._reply_wrap.set_visible(True)

    def _body(self) -> str:
        buffer = self._text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _sync_buttons(self) -> None:
        has_body = bool(self._body().strip())
        self._comment_btn.set_sensitive(has_body)
        self._request_btn.set_sensitive(has_body)

    def _on_key(self, _controller, keyval: int, _keycode: int, state) -> bool:
        # Ctrl+Enter comments, as on GitHub itself; a bare Enter stays a newline.
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            if self._comment_btn.get_sensitive():
                self._post(self._comment_btn, _("Comment"), practions.comment)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_claude(self, *_args) -> None:
        pr = self._pr
        if pr is None:
            return
        if self._claude_key == practions.REVIEW:
            # Posted on the PR, not typed at the session — and the box keeps
            # whatever is in it: asking for a review isn't sending a comment.
            self._post(
                self._reply_btn,
                _("Request review"),
                lambda pr, _body: practions.perform(practions.REVIEW, pr),
                clear=False,
            )
            return
        host = self._host_factory()
        if host.prompt_block():  # sampled again: sync was a fetch ago
            return
        host.send_prompt(practions.COMMENTS_PROMPT.format(number=pr.number))

    def _post(
        self,
        button: _BusyButton,
        label: str,
        run: Callable[[PullRequest, str], str | None],
        clear: bool = True,
    ) -> None:
        """Run one write call off the main loop, the composer held insensitive
        and *button* spinning until it lands. *label* is the button's own word,
        for the failure dialog's heading; *clear* is whether landing it empties
        the box — true for everything that posts what was typed there, false
        for the calls that only happen to be run from the same row."""
        pr = self._pr
        if pr is None or self._posting:
            return
        body = self._body().strip()
        self._posting = True
        self.set_sensitive(False)
        self._busy_btn = button
        button.set_busy(True)

        def work() -> None:
            try:
                error = run(pr, body)
            except Exception:  # a text box must never take the app down
                log.debug("prview: posting to %s failed", pr.url, exc_info=True)
                error = _("Collins couldn't run that action.")
            GLib.idle_add(self._landed, label, error, clear)

        threading.Thread(target=work, name="pr-post", daemon=True).start()

    def _landed(self, label: str, error: str | None, clear: bool) -> bool:
        if error:
            self._release()  # nothing is re-reading the page: let go here
            root = self.get_root()
            if root is not None:
                dialogs.error_dialog(root, _("{action} failed").format(action=label), error)
            return GLib.SOURCE_REMOVE
        if clear:
            self._text.get_buffer().set_text("")  # posted: the box has done its job
        # Still spinning, still held: _on_posted re-reads the PR, and the
        # word this posted only shows up in the timeline when that lands.
        self._holding = True
        self._on_posted()
        return GLib.SOURCE_REMOVE

    def settled(self) -> None:
        """The page's re-read has landed (or gave up): the pressed button
        takes its word back and the composer goes live again. Only a read this
        composer is waiting on releases it — any other fetch may land while a
        post of its own is still in flight."""
        if self._holding:
            self._release()

    def _release(self) -> None:
        self._holding = False
        self._posting = False
        self.set_sensitive(True)
        if self._busy_btn is not None:
            self._busy_btn.set_busy(False)
            self._busy_btn = None


class _ThreadCard(Gtk.Box):
    """One review thread as a card: where it anchors, who said what, and the
    thread's write half.

    The header names the anchor (``path:line`` — the line is GitHub's, gone
    when the code moved on) with an Outdated pill when it did; a resolved
    thread collapses whole behind an expander wearing a green Resolved pill,
    the way GitHub folds them. Under the comments sit Reply — a composer
    behind a revealer, posting through `practions.reply_in_thread` — and
    Resolve/Unresolve. One press, one mutation: the card holds insensitive
    with the pressed button spinning in place of its own word until the answer
    lands *and* the page's re-read behind it has (`settled`) — usually the
    read that replaces this card with one saying the new thing. A failure
    comes back as gh's own sentence with the text kept, released on the spot
    since no read is coming; success hands off to the page's posted path,
    which re-reads everything. Every fetch rebuilds the cards
    wholesale (and each thread gets one per view), so the per-thread state
    lives in the page's containers rather than in this widget — the reply
    draft in *drafts* and the in-flight guard in *busy*, both keyed by
    thread id: the composer's own rebuild lesson, thread-sized, with the
    guard held where a twin card in the other view (or a copy a mid-flight
    fetch rebuilt) checks it too.
    """

    def __init__(
        self,
        thread: prdetail.PrThread,
        pr: PullRequest,
        drafts: dict[str, str],
        busy: set[str],
        on_posted: Callable[[], None],
        images: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("pr-card")
        self._thread = thread
        self._pr = pr
        self._drafts = drafts
        self._busy = busy
        self._on_posted = on_posted
        self._posting = False
        # The button whose press is in flight — the one wearing the spinner.
        self._busy_btn: _BusyButton | None = None
        # Whether this card is waiting on the page's re-read of a mutation of
        # its own — what `settled` may release (see the composer's twin flag).
        self._holding = False

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        anchor = thread.path if thread.line is None else f"{thread.path}:{thread.line}"
        where = Gtk.Label(label=anchor, xalign=0.0, hexpand=True)
        where.add_css_class("caption-heading")
        where.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        where.set_tooltip_text(anchor)
        header.append(where)
        if thread.is_outdated:
            outdated = Gtk.Label(label=_("Outdated"))
            outdated.add_css_class("caption")
            outdated.add_css_class("dim-label")
            outdated.set_tooltip_text(_("The code this thread commented on has changed"))
            header.append(outdated)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for comment in thread.comments:
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            block.append(_byline(comment.author, comment.created_at, url=comment.url))
            block.append(_body_label(comment.body, images))
            body.append(block)
        body.append(self._write_row())
        body.append(self._reply_editor())

        if thread.is_resolved:
            check = Gtk.Image.new_from_icon_name("check-circle-fill-symbolic")
            check.set_pixel_size(prmenu.MERGED_ICON_PX)
            check.add_css_class("pr-checks-passed")
            resolved = Gtk.Label(label=_("Resolved"))
            resolved.add_css_class("caption")
            resolved.add_css_class("pr-checks-passed")
            header.append(check)
            header.append(resolved)
            expander = Gtk.Expander(label_widget=header)
            expander.set_expanded(False)
            body.set_margin_top(6)
            expander.set_child(body)
            self.append(expander)
        else:
            self.append(header)
            self.append(body)

        draft = drafts.get(thread.id, "")
        if draft:
            # A half-typed reply from before the rebuild: back where it was,
            # composer open.
            self._text.get_buffer().set_text(draft)
            self._reveal.set_reveal_child(True)
        self._sync_post()
        if thread.id in busy:
            # Built while this thread's mutation is still in flight (a fetch
            # rebuilt the cards under it): look as held as the original did.
            self.set_sensitive(False)

    def _write_row(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reply = Gtk.Button(label=_("Reply"))
        reply.add_css_class("flat")
        reply.set_tooltip_text(_("Reply in this thread"))
        reply.connect("clicked", self._on_reply_toggled)
        row.append(reply)
        row.append(Gtk.Box(hexpand=True))
        resolved = self._thread.is_resolved
        self._resolve_btn = _BusyButton(label=_("Unresolve") if resolved else _("Resolve"))
        self._resolve_btn.add_css_class("flat")
        self._resolve_btn.set_tooltip_text(
            _("Reopen this thread") if resolved else _("Mark this thread resolved")
        )
        self._resolve_btn.connect("clicked", lambda *_a: self._set_resolved(not resolved))
        row.append(self._resolve_btn)
        return row

    def _reply_editor(self) -> Gtk.Widget:
        self._text = Gtk.TextView()
        self._text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._text.set_top_margin(6)
        self._text.set_bottom_margin(6)
        self._text.set_left_margin(8)
        self._text.set_right_margin(8)
        self._text.get_buffer().connect("changed", self._on_draft_changed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self._text.add_controller(keys)
        entry = Gtk.ScrolledWindow(child=self._text)
        entry.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        entry.set_has_frame(True)
        entry.set_min_content_height(48)
        entry.set_max_content_height(160)
        entry.set_propagate_natural_height(True)
        self._post_btn = _BusyButton(label=_("Post reply"))
        self._post_btn.add_css_class("suggested-action")
        self._post_btn.connect("clicked", lambda *_a: self._post_reply())
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.append(Gtk.Box(hexpand=True))
        buttons.append(self._post_btn)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(entry)
        box.append(buttons)
        self._reveal = Gtk.Revealer(child=box)
        self._reveal.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        return self._reveal

    def _on_reply_toggled(self, *_args) -> None:
        show = not self._reveal.get_reveal_child()
        self._reveal.set_reveal_child(show)
        if show:
            self._text.grab_focus()

    def _on_draft_changed(self, *_args) -> None:
        self._drafts[self._thread.id] = self._body()
        self._sync_post()

    def _body(self) -> str:
        buffer = self._text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def _sync_post(self) -> None:
        self._post_btn.set_sensitive(bool(self._body().strip()))

    def _on_key(self, _controller, keyval: int, _keycode: int, state) -> bool:
        # Ctrl+Enter posts, as in the page's own composer.
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            if self._post_btn.get_sensitive():
                self._post_reply()
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _post_reply(self) -> None:
        body = self._body().strip()
        if not body:
            return
        self._post(
            self._post_btn,
            _("Reply"),
            lambda: practions.reply_in_thread(self._pr, self._thread.id, body),
            sent_draft=True,
        )

    def _set_resolved(self, resolved: bool) -> None:
        self._post(
            self._resolve_btn,
            _("Resolve") if resolved else _("Unresolve"),
            lambda: practions.set_thread_resolved(self._pr, self._thread.id, resolved),
        )

    def _post(
        self,
        button: _BusyButton,
        label: str,
        run: Callable[[], str | None],
        sent_draft: bool = False,
    ) -> None:
        """One thread mutation off the main loop, this card held insensitive
        and *button* spinning until it lands. *label* is the button's own word,
        for the failure dialog's heading; *sent_draft* says success should
        clear the reply."""
        if self._posting or self._thread.id in self._busy:
            return  # this card, its twin in the other view, or a rebuilt copy
        self._posting = True
        self._busy.add(self._thread.id)
        self.set_sensitive(False)
        self._busy_btn = button
        button.set_busy(True)

        def work() -> None:
            try:
                error = run()
            except Exception:  # a card must never take the app down
                log.debug("prview: thread action on %s failed", self._pr.url, exc_info=True)
                error = _("Collins couldn't run that action.")
            GLib.idle_add(self._landed, label, error, sent_draft)

        threading.Thread(target=work, name="pr-thread-post", daemon=True).start()

    def _landed(self, label: str, error: str | None, sent_draft: bool) -> bool:
        self._posting = False
        # Out of the shared set as soon as gh answers, whether or not this
        # card is still holding: the set is the guard on the call, and a copy
        # of this card that a mid-flight fetch orphaned would never come back
        # to clear it — the thread would be locked for the rest of the page.
        self._busy.discard(self._thread.id)
        if error:
            self._release()  # nothing is re-reading the page: let go here
            root = self.get_root()
            if root is not None:
                dialogs.error_dialog(root, _("{action} failed").format(action=label), error)
            return GLib.SOURCE_REMOVE
        # Still spinning, still held: _on_posted re-reads the PR, and this
        # card only says what it did once that read lands (see `settled`).
        self._holding = True
        if sent_draft:
            self._text.get_buffer().set_text("")
            self._drafts.pop(self._thread.id, None)  # after set_text re-added it
        self._on_posted()
        return GLib.SOURCE_REMOVE

    def settled(self) -> None:
        """The page's re-read has landed (or gave up): the pressed button
        takes its word back and the card goes live again. A successful
        mutation usually replaces this card outright — this is what happens
        when the read that would have replaced it failed."""
        if self._holding:
            self._release()

    def _release(self) -> None:
        self._holding = False
        self.set_sensitive(True)
        if self._busy_btn is not None:
            self._busy_btn.set_busy(False)
            self._busy_btn = None


class _FileSection(Gtk.Box):
    """One file of the diff: a header row (path, +/− counts), then the patch
    in a GtkSource diff buffer — or, for an image, the picture itself.

    A patch past `_LARGE_PATCH_LINES` starts collapsed showing its line count
    and only builds its buffer on first expand; a file with no patch at all —
    binary, an over-cap diff, or a failed diff call — is the header alone
    with a stat-only note (see prdetail.PrFile).

    A changed image is the one file a patch can't render, so it renders as
    before-and-after pictures instead (`prfileimages.preview`, which fetches
    the blobs the header names). Where those pictures are the whole story —
    a binary file, whose stanza says only that the two differ — they stand in
    place of the diff; where there is a real patch to read as well (an SVG),
    it sits under them, behind its own line-count expander when it is a long
    one, so the section can lead with the picture without eagerly building a
    buffer nobody asked for.
    """

    def __init__(
        self,
        file: prdetail.PrFile,
        scheme: GtkSource.StyleScheme | None,
        detail: prdetail.PullRequestDetail | None = None,
        images: bool = False,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("pr-file-section")
        self._file = file
        self._scheme = scheme
        self._buffer: GtkSource.Buffer | None = None
        self._expander: Gtk.Expander | None = None
        self._diff_expander: Gtk.Expander | None = None
        # Built here rather than on expand: it is a slot plus a fetch, and
        # it is what the section leads with. None whenever this file isn't
        # an image, the setting is off, or the reply didn't name the commits
        # to fetch its blobs from — all of which leave the patch as it was.
        self._preview = (
            prfileimages.preview(file, detail)
            if images and detail is not None
            else None
        )

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, hexpand=True)
        path = Gtk.Label(label=file.path, xalign=0.0, hexpand=True)
        path.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path.set_tooltip_text(file.path)
        path.add_css_class("pr-file-path")
        header.append(path)
        header.append(_count_label(f"+{file.additions}", "pr-checks-passed"))
        header.append(_count_label(f"−{file.deletions}", "pr-checks-failed"))

        if file.patch is None:
            note = Gtk.Label(label=_("no diff — binary or too large"))
            note.add_css_class("caption")
            note.add_css_class("dim-label")
            header.append(note)

        # A patch with no hunk in it is git's "Binary files … differ" — the
        # picture above says all of it, and better.
        self._lines = file.patch.count("\n") if file.patch is not None else 0
        self._large = self._lines > _LARGE_PATCH_LINES
        self._show_diff = file.patch is not None and (
            self._preview is None or _has_hunks(file.patch)
        )
        if self._preview is None and not self._show_diff:
            self.append(header)  # stat-only: the header is the whole section
            return

        # The count rides the header only when the header is what stays
        # visible; with a picture below it, it belongs on the diff's own
        # expander instead.
        if self._large and self._show_diff and self._preview is None:
            header.append(self._count_widget())
        expander = Gtk.Expander(label_widget=header)
        # A section with a picture in it always opens: the preview is the
        # point of it, and the long half — the buffer — has its own gate.
        expander.set_expanded(self._preview is not None or not self._large)
        self._expander = expander
        self.append(expander)
        if expander.get_expanded():
            expander.set_child(self._content_widget())
        else:
            expander.connect("notify::expanded", self._on_expanded)

    def reveal(self) -> None:
        """Expand (building the buffer if this is the first time).

        The nested diff of an image goes with it: the file list's row was
        clicked to read this file, not to look at it.
        """
        if self._expander is not None:
            self._expander.set_expanded(True)
        if self._diff_expander is not None:
            self._diff_expander.set_expanded(True)

    def set_scheme(self, scheme: GtkSource.StyleScheme | None) -> None:
        """Restyle a built buffer now; a lazy one picks *scheme* up on build."""
        self._scheme = scheme
        if self._buffer is not None and scheme is not None:
            self._buffer.set_style_scheme(scheme)

    def _on_expanded(self, expander: Gtk.Expander, _pspec) -> None:
        if expander.get_expanded() and expander.get_child() is None:
            expander.set_child(self._content_widget())

    def _count_widget(self) -> Gtk.Widget:
        """The patch's line count, dimmed — what a collapsed diff says of
        itself, wherever it is collapsed."""
        count = Gtk.Label(
            label=ngettext("{n} line", "{n} lines", self._lines).format(n=self._lines)
        )
        count.add_css_class("caption")
        count.add_css_class("dim-label")
        return count

    def _content_widget(self) -> Gtk.Widget:
        """Everything under the header: the picture, the patch, or both."""
        if self._preview is None:
            return self._diff_widget()
        if not self._show_diff:
            return self._preview
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.append(self._preview)
        if self._large:
            # The image's patch, still lazy: a generated SVG can run to tens
            # of thousands of lines of path data, and building that buffer to
            # show a picture would be the very hitch the cap exists to avoid.
            diff = Gtk.Expander(label_widget=self._count_widget())
            diff.connect("notify::expanded", self._on_diff_expanded)
            self._diff_expander = diff
            body.append(diff)
        else:
            body.append(self._diff_widget())
        return body

    def _on_diff_expanded(self, expander: Gtk.Expander, _pspec) -> None:
        if expander.get_expanded() and expander.get_child() is None:
            expander.set_child(self._diff_widget())

    def _diff_widget(self) -> Gtk.Widget:
        """The patch in a read-only GtkSource view, diff-highlighted.

        Its own horizontal scroll (natural height propagated, no vertical
        bar) so a long diff line pans within the section instead of forcing
        the whole panel wide — the page's column scroller never scrolls
        sideways.
        """
        buffer = GtkSource.Buffer()
        language = GtkSource.LanguageManager.get_default().get_language("diff")
        if language is not None:
            buffer.set_language(language)
        if self._scheme is not None:
            buffer.set_style_scheme(self._scheme)
        buffer.set_text(self._file.patch)
        self._buffer = buffer
        view = GtkSource.View(buffer=buffer)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.set_left_margin(6)
        view.set_right_margin(6)
        view.set_top_margin(4)
        view.set_bottom_margin(4)
        scroller = Gtk.ScrolledWindow(child=view, hexpand=True)
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroller.set_propagate_natural_height(True)
        return scroller


def _byline(
    author: str,
    created_at: str,
    url: str = "",
    trailing: list[Gtk.Widget] | None = None,
) -> Gtk.Widget:
    """A card's byline: avatar, author, age (absolute stamp in the tooltip),
    then any *trailing* widgets — a review's verdict rides there. With a
    *url*, the age is a link to the entry on GitHub."""
    byline = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    face = avatars.avatar(author, _AVATAR_PX)
    face.set_valign(Gtk.Align.CENTER)
    byline.append(face)
    who = Gtk.Label(label=author or _("unknown"), xalign=0.0)
    who.add_css_class("caption-heading")
    byline.append(who)
    if url:
        escaped = GLib.markup_escape_text(url)
        when = Gtk.Label(xalign=0.0)
        when.set_markup(
            f'<a href="{escaped}">{GLib.markup_escape_text(format_relative(created_at))}</a>'
        )
    else:
        when = Gtk.Label(label=format_relative(created_at), xalign=0.0)
    when.add_css_class("caption")
    when.add_css_class("dim-label")
    when.set_tooltip_text(format_timestamp(created_at))
    byline.append(when)
    byline.append(Gtk.Box(hexpand=True))
    for widget in trailing or []:
        byline.append(widget)
    return byline


def _card(
    author: str,
    created_at: str,
    url: str = "",
    trailing: list[Gtk.Widget] | None = None,
) -> Gtk.Box:
    """One conversation card: a byline, then whatever the caller appends."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.add_css_class("pr-card")
    card.append(_byline(author, created_at, url=url, trailing=trailing))
    return card


def _folded_body(text: str, images: bool = False) -> Gtk.Widget:
    """The description's body, folded to `_FOLD_LINES` lines behind "Show
    more" — a long description shouldn't push the conversation off screen.

    A body that plainly fits the fold comes back without a toggle at all.
    The expanded half is `_body_label`, so a pathological body keeps the
    render cap's own second "Show more" step. The preview is truncated
    *content*, not just an ellipsized label: `set_lines` is Pango's negative
    height, a per-paragraph cap, so on a many-paragraph body the label alone
    would show six lines of every paragraph. The line cap stays on as the
    bound for the other shape — one huge paragraph.

    The one thing the fold never hides is every picture: a preview carries
    the body's first image row even when the text ran out of budget before
    reaching it (`keep_first_image`). A description whose screenshots all
    sat behind "Show more" would have made rendering them pointless for
    exactly the bodies this is for.
    """
    segments = split_body(text) if images else [text]
    preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
    if _fill_body(preview, segments, _FOLD_CHARS, _FOLD_LINES, preview=True):
        return preview
    return _fold(preview, _body_widget(segments))


def _fold(preview: Gtk.Widget, full: Gtk.Widget) -> Gtk.Widget:
    """*preview* over a "Show more" toggle that swaps it for *full* — the
    step a description and a long Checks list both take, so the two fold
    the same way: one flat button, the word and a caret, that turns into
    "Show less" once the whole thing is out."""
    full.set_visible(False)
    word = Gtk.Label(label=_("Show more"))
    caret = Gtk.Image.new_from_icon_name("pan-down-symbolic")
    inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    inner.append(word)
    inner.append(caret)
    toggle = Gtk.Button(child=inner)
    toggle.add_css_class("flat")
    toggle.set_halign(Gtk.Align.START)

    def flip(_button: Gtk.Button) -> None:
        expanded = full.get_visible()
        full.set_visible(not expanded)
        preview.set_visible(expanded)
        word.set_label(_("Show more") if expanded else _("Show less"))
        caret.set_from_icon_name("pan-down-symbolic" if expanded else "pan-up-symbolic")

    toggle.connect("clicked", flip)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.append(preview)
    box.append(full)
    box.append(toggle)
    return box


def _body_label(text: str, images: bool = False) -> Gtk.Widget:
    """A markdown body as selectable wrapped text — with the images it
    embeds rendered in place when *images* is on (the `pr_inline_images`
    setting; off, an image stays the alt-text link md_to_pango makes of
    it). Past the render cap the rest waits behind "Show more" (the whole
    text is already bounded by prdetail; this cap is about Pango layout
    cost, which the main loop pays)."""
    return _body_widget(split_body(text) if images else [text])


def _body_widget(segments: list) -> Gtk.Widget:
    """Every segment that fits the render cap, and a "Show more" for the
    rest. Split out from `_body_label` because the fold's expanded half is
    this same widget over the same already-split segments."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
    if _fill_body(box, segments, _RENDER_CAP, None):
        return box
    more = Gtk.Button(label=_("Show more"))
    more.add_css_class("flat")
    more.set_halign(Gtk.Align.START)

    def show_all(button: Gtk.Button) -> None:
        child = box.get_first_child()
        while child is not None:
            box.remove(child)
            child = box.get_first_child()
        _fill_body(box, segments, None, None)
        button.set_visible(False)

    more.connect("clicked", show_all)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    outer.append(box)
    outer.append(more)
    return outer


def _fill_body(
    box: Gtk.Box,
    segments: list,
    chars: int | None,
    lines: int | None,
    preview: bool = False,
    keep_first_image: bool = True,
) -> bool:
    """Append the segments that fit *chars*/*lines* to *box*; return whether
    all of them did.

    Budgets are spent as the segments are walked, so what a reader gets is
    the front of the body rather than a sample of it. An image row spends
    `_IMAGE_FOLD_LINES` of the line budget — a picture is worth several
    lines of a preview's height, and a body that opens with five of them
    shouldn't unfold itself by being mostly pictures.
    """
    spent = False  # budgets exhausted; from here only a first image may pass
    shown_image = False
    complete = True
    for segment in segments:
        if isinstance(segment, tuple):
            if spent and not (keep_first_image and not shown_image):
                complete = False
                continue
            box.append(_image_row(segment))
            shown_image = True
            if lines is not None:
                lines -= _IMAGE_FOLD_LINES
                spent = spent or lines <= 0
            continue
        if spent:
            complete = False
            continue
        head, whole = _head(segment, chars, lines)
        label = Gtk.Label(xalign=0.0, selectable=True, wrap=True, hexpand=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        if preview and not whole:
            # The backstop for the shape a budget can't catch: one paragraph
            # longer than the fold, whose head is still a single line here.
            # Only on the cut segment — a label that holds all its text must
            # not ellipsize, or a body that fits the fold in a narrow panel
            # would hide its tail with no "Show more" to press.
            label.set_lines(_FOLD_LINES)
            label.set_ellipsize(Pango.EllipsizeMode.END)
        # rstrip: an "…" after a kept blank line would render as its own line.
        _set_md(label, head if whole else head.rstrip() + "…")
        box.append(label)
        if not whole:
            spent = True
            complete = False
            continue
        if chars is not None:
            chars -= len(segment)
            spent = spent or chars <= 0
        if lines is not None:
            lines -= segment.count("\n") + 1
            spent = spent or lines <= 0
    return complete


def _head(text: str, chars: int | None, lines: int | None) -> tuple[str, bool]:
    """The front of *text* that fits the budgets, and whether that is all of
    it. Whole lines while the budget lasts — a cut mid-line can sever a
    link's markdown and render it literal — and only a first line over the
    budget by itself is cut mid-way."""
    if (chars is None or len(text) <= chars) and (lines is None or text.count("\n") < lines):
        return text, True
    head = ""
    for line in text.split("\n")[:lines]:
        if head and chars is not None and len(head) + len(line) > chars:
            break
        head = f"{head}\n{line}" if head else line[:chars]
    return head, False


def _image_row(row: tuple) -> Gtk.Widget:
    """One row of body images. Images the body kept on one line stay on one
    line here (a badge strip, a before/after pair); each still shrinks on
    its own, so a narrow panel scales them down rather than clipping."""
    if len(row) == 1:
        return bodyimages.image(row[0])
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    for entry in row:
        box.append(bodyimages.image(entry))
    return box


def _has_hunks(patch: str) -> bool:
    """Whether *patch* has any hunk in it — i.e. whether there is a diff to
    read at all. A binary file's stanza is headers and git's "Binary files …
    differ", which is a sentence, not a diff: the picture beside it
    (prfileimages) says everything that line was standing in for."""
    return patch.startswith("@@") or "\n@@" in patch


def _count_label(text: str, css_class: str) -> Gtk.Label:
    """A +n/−n caption in the checks-passed green or checks-failed red."""
    label = Gtk.Label(label=text)
    label.add_css_class("caption")
    label.add_css_class(css_class)
    return label


# The text-scale provider all PR pages share, display-wide: a provider added
# to one widget's style context styles that widget alone in GTK4, so the only
# way a scale can cascade through a page is a display rule keyed off the
# page's css class. Bounds keep a corrupt setting from rendering the page
# unreadable or absurd.
_FONT_SCALE_MIN = 50
_FONT_SCALE_MAX = 300
_font_provider: Gtk.CssProvider | None = None
_font_scale = 0  # percent last loaded into the provider


def _apply_font_scale(display: Gdk.Display, percent: object) -> None:
    """Load the pr_font_scale setting into the shared display provider.

    Idempotent per value — every page calls this on every settings fan-out.
    Font-size percentages compound through CSS inheritance, so scaling the
    page root scales every label, text view and diff buffer while captions
    keep their relative smallness; buttons and menus get the inverse scale,
    canceling back to the app size — verdicts and menu rows shouldn't grow
    with reading text (and are how the page is asked to *stop* being big).
    """
    global _font_provider, _font_scale
    try:
        percent = int(percent)  # settings files are user-editable text
    except (TypeError, ValueError):
        percent = 100
    percent = min(max(percent, _FONT_SCALE_MIN), _FONT_SCALE_MAX)
    if percent == _font_scale:
        return
    if _font_provider is None:
        _font_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _font_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
    _font_scale = percent
    # Check rows are content that happens to be clickable (the row is the
    # link), so they keep the reading scale — else a linked check would
    # render smaller than a linkless one right below it.
    inverse = 100.0 * 100.0 / percent
    _font_provider.load_from_string(
        f".pr-view-page {{ font-size: {percent}%; }}\n"
        f".pr-view-page button:not(.pr-check-row), .pr-view-page popover"
        f" {{ font-size: {inverse:.2f}%; }}\n"
    )


def _set_md(label: Gtk.Label, text: str) -> None:
    """Markdown onto a label, with chatbubbles' plain-text fallback: markup
    this module built from untrusted text must degrade, never raise."""
    try:
        label.set_markup(md_to_pango(text, links=True))
    except GLib.GError:
        label.set_label(text)
