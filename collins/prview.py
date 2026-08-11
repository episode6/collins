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

The Conversation column ends in the page's write half: a composer that posts
its text as a comment or a review verdict through practions' write calls
(bodies over stdin, never argv), with "Reply via Claude" beside them typing
the COMMENTS prompt into the owning session instead — the composer is for
answering a reviewer yourself, the prompt for making the agent do it.

Review threads render as their own cards (`_ThreadCard`): anchored in the
Conversation timeline by when they started, and again under their file's
section in the Files view, each with the thread's write half — a reply
composer behind a revealer, and Resolve/Unresolve. Resolved threads collapse
behind an expander. Reply drafts live on the page keyed by thread id, so the
rebuild that lands a background refresh never eats one — the main composer's
lesson, thread-sized. `reveal_unresolved` is the unresolved badge's deep
link: the Conversation view fronted and scrolled to the first unresolved
thread, deferred until the first fetch when the page is fresh.

Everything shown is repository content and therefore untrusted: bodies go
through `formatting.md_to_pango`'s escaping (with the plain-text fallback on
malformed markup), only http(s) URLs ever reach a browser (prdetail already
enforced that on the way in), and a pathological body renders capped behind
a "Show more" step so building labels can't wedge the main loop.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk, Pango  # noqa: E402

from . import dialogs, practions, prdetail, prmenu  # noqa: E402
from .copylabel import open_tooltip, open_uri  # noqa: E402
from .editor import GtkSource, style_scheme  # noqa: E402 — require_version + friendly exit live there
from .formatting import format_relative, format_timestamp, md_to_pango  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .prstatus import PullRequest, invalidate  # noqa: E402

log = logging.getLogger(__name__)

# Re-showing the page re-reads the PR, but not more often than this — the
# same arrival throttle the footer chips use (terminal._PR_FOCUS_REFRESH_MIN_US).
_FOCUS_REFRESH_MIN_US = 10 * 1_000_000
# Where a body stops rendering until "Show more" is pressed. Well under
# prdetail's storage bound: a label this long is already a scroll of its own,
# and Pango layout cost grows with every character the main loop hands it.
_RENDER_CAP = 20_000
# A patch past this many lines starts collapsed and only builds its buffer on
# first expand: GtkSource renders it fine, but a fetch landing a handful of
# eagerly built multi-thousand-line buffers would wedge the main loop.
_LARGE_PATCH_LINES = 2_000
# The file list's share of the Files view until the user drags the divider —
# the editor gives its file tree the same kind of sliver (_TREE_INITIAL_WIDTH).
_FILE_LIST_WIDTH = 170

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
    builder (`TerminalTab._pr_action_host`): the header's actions menu is the
    same menu the chip opens, minus the row that would open this very page.
    """

    page_kind = "pr"

    __gsignals__ = {
        # The tab's title/icon inputs changed (a fetch landed a new state);
        # the strip re-reads page_title/page_icon.
        "title-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, pr: PullRequest, host_factory) -> None:
        super().__init__()
        self._pr = pr
        self._host_factory = host_factory
        self._detail: prdetail.PullRequestDetail | None = None
        self._fetching = False
        self._fetch_gen = 0
        self._fetched_at = 0  # monotonic µs of the last fetch *attempt*
        # The Conversation view's thread cards in timeline order — what the
        # unresolved deep link scans — and the reply drafts, keyed by thread
        # id so they survive the rebuilds that replace the cards.
        self._thread_cards: list[tuple[prdetail.PrThread, Gtk.Widget]] = []
        self._thread_drafts: dict[str, str] = {}
        self._pending_reveal = False  # reveal_unresolved asked before data came

        # -- header ---------------------------------------------------------
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.add_css_class("pr-view-header")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._mark_slot = Adw.Bin(child=prmenu.status_icon(pr))
        self._mark_slot.set_valign(Gtk.Align.CENTER)
        top.append(self._mark_slot)
        self._number = Gtk.Label(label=f"#{pr.number}")
        self._number.add_css_class("dim-label")
        top.append(self._number)
        self._title = Gtk.Label(xalign=0.0, hexpand=True, selectable=True)
        self._title.add_css_class("pr-view-title")
        self._title.set_ellipsize(Pango.EllipsizeMode.END)
        top.append(self._title)

        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        top.append(self._spinner)
        self._refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        self._refresh_btn.add_css_class("flat")
        self._refresh_btn.set_tooltip_text(_("Reload this pull request"))
        self._refresh_btn.connect("clicked", lambda *_a: self.refresh())
        top.append(self._refresh_btn)
        github_btn = Gtk.Button(icon_name="github-symbolic")
        github_btn.add_css_class("flat")
        github_btn.set_tooltip_text(open_tooltip(pr.url))
        github_btn.connect("clicked", lambda b: open_uri(b, self.pr_url))
        top.append(github_btn)
        self._menu = prmenu.new_popover(Gtk.PositionType.BOTTOM)
        menu_btn = Gtk.MenuButton(icon_name="view-more-horizontal-symbolic")
        menu_btn.add_css_class("flat")
        menu_btn.set_tooltip_text(_("Pull request actions"))
        menu_btn.set_popover(self._menu)
        menu_btn.set_create_popup_func(self._fill_menu)
        top.append(menu_btn)
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
        self._composer = _Composer(host_factory, self._posted)

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

        # -- the two views under one switcher ---------------------------------
        self._stack = Adw.ViewStack(vexpand=True)
        self._stack.add_titled_with_icon(
            self._scroller, "conversation", _("Conversation"), "chat-bubble-symbolic"
        )
        self._stack.add_titled_with_icon(files_paned, "files", _("Files"), "ft-file-symbolic")
        switcher = Adw.ViewSwitcher(stack=self._stack)
        switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        switcher.set_halign(Gtk.Align.CENTER)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self._banner)  # above the stack: it speaks for both views
        column.append(self._stack)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(switcher)
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
        """Only the editor's style scheme matters here: the diff buffers wear
        it. Everything else renders in the app font and theme already."""
        scheme = settings.get("editor_style_scheme") or ""
        if scheme != self._scheme_setting:
            self._scheme_setting = scheme
            self._apply_scheme()

    def page_state(self) -> dict:
        """This page's slot in a serialized dock layout (see panellayout):
        the URL is the whole state — a restored page refetches the rest."""
        return {"kind": "pr", "url": self.pr_url}

    # -- fetching -------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the PR now — the Refresh button, and the dedupe path's
        "you asked for this page again"."""
        self._fetch(force=True)

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
            return
        now = GLib.get_monotonic_time()
        if not force and self._fetched_at and now - self._fetched_at < _FOCUS_REFRESH_MIN_US:
            return
        self._fetched_at = now
        self._fetching = True
        self._spinner.set_visible(True)
        self._spinner.start()
        self._refresh_btn.set_sensitive(False)
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
        self._spinner.stop()
        self._spinner.set_visible(False)
        self._refresh_btn.set_sensitive(True)
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

    def _fill_menu(self, _button: Gtk.MenuButton) -> None:
        """The chip's actions menu, rebuilt fresh on every open — minus
        "View in Collins" (this page *is* the view), and with a refresh that
        also re-reads the page an action just changed."""
        host = self._host_factory()
        host = replace(
            host,
            view_pr=None,
            # The deep link stays, as an in-page jump: from here "view the
            # unresolved comments" means scroll to them, not open a twin.
            view_unresolved=lambda _pr: self.reveal_unresolved(),
            refresh=lambda base=host.refresh: (base(), self.refresh()),
        )
        prmenu.show_actions(self._menu, self._pr, host)

    def _sync_header(self) -> None:
        pr = self._pr
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
        self._composer.sync(self._pr)
        self._content.append(self._composer)

    def _posted(self) -> None:
        """A comment or review just landed on GitHub: re-read everything that
        shows this PR — the page itself (whose fetch re-absorbs into the
        summary cache), and the summary the tab's own poll holds."""
        invalidate(self.pr_url)
        self._host_factory().refresh()
        self.refresh()

    def _description_card(self, detail: prdetail.PullRequestDetail) -> Gtk.Widget:
        card = _card(detail.author, detail.created_at)
        if detail.body:
            card.append(_body_label(detail.body))
        else:
            none = Gtk.Label(label=_("No description provided."), xalign=0.0)
            none.add_css_class("dim-label")
            card.append(none)
        return card

    def _checks_section(self, checks) -> Gtk.Widget:
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading = Gtk.Label(label=_("Checks"), xalign=0.0)
        heading.add_css_class("caption-heading")
        heading.set_margin_bottom(2)
        section.append(heading)
        for check in checks:
            section.append(self._check_row(check))
        return section

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
        card.append(_body_label(comment.body))
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
            card.append(_body_label(review.body))
        return card

    def _thread_card(self, thread: prdetail.PrThread) -> _ThreadCard:
        """One review thread as a card, wired to this page's PR, drafts and
        post-refresh. Built per view — a widget has one parent, and a thread
        shows in both — so the shared draft dict is what keeps the copies
        agreeing on a half-typed reply."""
        return _ThreadCard(thread, self._pr, self._thread_drafts, self._posted)

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
            section = _FileSection(file, scheme)
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


class _Composer(Gtk.Box):
    """The Conversation view's write half: a comment box and its verdicts.

    One per page, created once and re-appended across rebuilds — the rebuild
    that lands a background refresh must not eat a half-typed comment. The
    buttons run practions' write calls on a worker thread, the whole composer
    held insensitive until the answer lands (one press, one post): Comment
    posts the text as an issue comment, Approve / Request changes submit a
    review with the text along. Comment and Request changes need words to go
    — GitHub refuses both bare — so they grey out over an empty box; Approve
    stands alone. A failure comes back as gh's own sentence in a dialog, the
    text kept where it was typed; success clears the box and re-reads the PR.

    "Reply via Claude" is the complement, not a competitor: it types the
    COMMENTS prompt into the owning session instead, greyed with the reason
    whenever the session can't take a prompt right now — the blocked action
    rows' treatment, tooltip on a sensitive wrapper and all.
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

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reply = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reply.append(Gtk.Image.new_from_icon_name("agent-claude-symbolic"))
        reply.append(Gtk.Label(label=_("Reply via Claude")))
        self._reply_btn = Gtk.Button(child=reply)
        self._reply_btn.add_css_class("flat")
        self._reply_btn.connect("clicked", self._on_reply)
        # Insensitive widgets are skipped when GTK picks what the pointer is
        # over (the blocked action rows' lesson): the reason a greyed button
        # is grey lives on this wrapper, which stays sensitive.
        self._reply_wrap = Gtk.Box()
        self._reply_wrap.append(self._reply_btn)
        row.append(self._reply_wrap)
        row.append(Gtk.Box(hexpand=True))
        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        row.append(self._spinner)
        self._request_btn = Gtk.Button(label=_("Request changes"))
        self._request_btn.connect(
            "clicked",
            lambda *_a: self._post(
                _("Request changes"),
                lambda pr, body: practions.review(pr, practions.REQUEST_CHANGES, body),
            ),
        )
        row.append(self._request_btn)
        self._approve_btn = Gtk.Button(label=_("Approve"))
        self._approve_btn.connect(
            "clicked",
            lambda *_a: self._post(
                _("Approve"),
                lambda pr, body: practions.review(pr, practions.APPROVE, body),
            ),
        )
        row.append(self._approve_btn)
        self._comment_btn = Gtk.Button(label=_("Comment"))
        self._comment_btn.add_css_class("suggested-action")
        self._comment_btn.connect(
            "clicked", lambda *_a: self._post(_("Comment"), practions.comment)
        )
        row.append(self._comment_btn)
        self.append(row)
        self._sync_buttons()

    def sync(self, pr: PullRequest) -> None:
        """Point the composer at *pr* as freshly fetched, and re-read the
        session behind it. Verdicts only show for a live PR — GitHub refuses
        a review on a merged or closed one, commenting stays open forever."""
        self._pr = pr
        live = pr.state in practions.LIVE
        self._approve_btn.set_visible(live)
        self._request_btn.set_visible(live)
        self._comment_btn.set_tooltip_text(_("Comment on {slug}").format(slug=pr.slug))
        self._approve_btn.set_tooltip_text(_("Approve {slug}").format(slug=pr.slug))
        self._request_btn.set_tooltip_text(
            _("Request changes on {slug}").format(slug=pr.slug)
        )
        prompt = practions.COMMENTS_PROMPT.format(number=pr.number)
        block = self._host_factory().prompt_block()
        self._reply_btn.set_sensitive(not block)
        tooltip = _("Send “{prompt}” to this session").format(prompt=prompt)
        self._reply_wrap.set_tooltip_text("\n".join(part for part in (tooltip, block) if part))
        self._sync_buttons()

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
                self._post(_("Comment"), practions.comment)
            return Gdk.EVENT_STOP
        return Gdk.EVENT_PROPAGATE

    def _on_reply(self, *_args) -> None:
        pr = self._pr
        if pr is None:
            return
        host = self._host_factory()
        if host.prompt_block():  # sampled again: sync was a fetch ago
            return
        host.send_prompt(practions.COMMENTS_PROMPT.format(number=pr.number))

    def _post(self, label: str, run: Callable[[PullRequest, str], str | None]) -> None:
        """Run one write call off the main loop, the composer held insensitive
        until it lands. *label* is the button's own word, for the failure
        dialog's heading."""
        pr = self._pr
        if pr is None or self._posting:
            return
        body = self._body().strip()
        self._posting = True
        self.set_sensitive(False)
        self._spinner.set_visible(True)
        self._spinner.start()

        def work() -> None:
            try:
                error = run(pr, body)
            except Exception:  # a text box must never take the app down
                log.debug("prview: posting to %s failed", pr.url, exc_info=True)
                error = _("Collins couldn't run that action.")
            GLib.idle_add(self._landed, label, error)

        threading.Thread(target=work, name="pr-post", daemon=True).start()

    def _landed(self, label: str, error: str | None) -> bool:
        self._posting = False
        self.set_sensitive(True)
        self._spinner.stop()
        self._spinner.set_visible(False)
        if error:
            root = self.get_root()
            if root is not None:
                dialogs.error_dialog(root, _("{action} failed").format(action=label), error)
            return GLib.SOURCE_REMOVE
        self._text.get_buffer().set_text("")  # posted: the box has done its job
        self._on_posted()
        return GLib.SOURCE_REMOVE


class _ThreadCard(Gtk.Box):
    """One review thread as a card: where it anchors, who said what, and the
    thread's write half.

    The header names the anchor (``path:line`` — the line is GitHub's, gone
    when the code moved on) with an Outdated pill when it did; a resolved
    thread collapses whole behind an expander wearing a green Resolved pill,
    the way GitHub folds them. Under the comments sit Reply — a composer
    behind a revealer, posting through `practions.reply_in_thread` — and
    Resolve/Unresolve. One press, one mutation: the card holds insensitive
    under a spinner until the answer lands, a failure comes back as gh's own
    sentence with the text kept, and success hands off to the page's posted
    path, which re-reads everything. Every fetch rebuilds the cards
    wholesale (and each thread gets one per view), so the reply draft lives
    in the page's dict — *drafts*, keyed by thread id — rather than in this
    widget: the composer's own rebuild lesson, thread-sized.
    """

    def __init__(
        self,
        thread: prdetail.PrThread,
        pr: PullRequest,
        drafts: dict[str, str],
        on_posted: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("pr-card")
        self._thread = thread
        self._pr = pr
        self._drafts = drafts
        self._on_posted = on_posted
        self._posting = False

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
            block.append(_body_label(comment.body))
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

    def _write_row(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reply = Gtk.Button(label=_("Reply"))
        reply.add_css_class("flat")
        reply.set_tooltip_text(_("Reply in this thread"))
        reply.connect("clicked", self._on_reply_toggled)
        row.append(reply)
        row.append(Gtk.Box(hexpand=True))
        self._spinner = Gtk.Spinner()
        self._spinner.set_visible(False)
        row.append(self._spinner)
        resolved = self._thread.is_resolved
        resolve = Gtk.Button(label=_("Unresolve") if resolved else _("Resolve"))
        resolve.add_css_class("flat")
        resolve.set_tooltip_text(
            _("Reopen this thread") if resolved else _("Mark this thread resolved")
        )
        resolve.connect("clicked", lambda *_a: self._set_resolved(not resolved))
        row.append(resolve)
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
        self._post_btn = Gtk.Button(label=_("Post reply"))
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
            _("Reply"),
            lambda: practions.reply_in_thread(self._pr, self._thread.id, body),
            sent_draft=True,
        )

    def _set_resolved(self, resolved: bool) -> None:
        self._post(
            _("Resolve") if resolved else _("Unresolve"),
            lambda: practions.set_thread_resolved(self._pr, self._thread.id, resolved),
        )

    def _post(self, label: str, run: Callable[[], str | None], sent_draft: bool = False) -> None:
        """One thread mutation off the main loop, this card held insensitive
        until it lands. *label* is the button's own word, for the failure
        dialog's heading; *sent_draft* says success should clear the reply."""
        if self._posting:
            return
        self._posting = True
        self.set_sensitive(False)
        self._spinner.set_visible(True)
        self._spinner.start()

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
        self.set_sensitive(True)
        self._spinner.stop()
        self._spinner.set_visible(False)
        if error:
            root = self.get_root()
            if root is not None:
                dialogs.error_dialog(root, _("{action} failed").format(action=label), error)
            return GLib.SOURCE_REMOVE
        if sent_draft:
            self._text.get_buffer().set_text("")
            self._drafts.pop(self._thread.id, None)  # after set_text re-added it
        self._on_posted()
        return GLib.SOURCE_REMOVE


class _FileSection(Gtk.Box):
    """One file of the diff: a header row (path, +/− counts), then the patch
    in a GtkSource diff buffer.

    A patch past `_LARGE_PATCH_LINES` starts collapsed showing its line count
    and only builds its buffer on first expand; a file with no patch at all —
    binary, an over-cap diff, or a failed diff call — is the header alone
    with a stat-only note (see prdetail.PrFile).
    """

    def __init__(self, file: prdetail.PrFile, scheme: GtkSource.StyleScheme | None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("pr-file-section")
        self._file = file
        self._scheme = scheme
        self._buffer: GtkSource.Buffer | None = None
        self._expander: Gtk.Expander | None = None

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
            self.append(header)
            return

        lines = file.patch.count("\n")
        large = lines > _LARGE_PATCH_LINES
        if large:
            count = Gtk.Label(
                label=ngettext("{n} line", "{n} lines", lines).format(n=lines)
            )
            count.add_css_class("caption")
            count.add_css_class("dim-label")
            header.append(count)
        expander = Gtk.Expander(label_widget=header)
        expander.set_expanded(not large)
        self._expander = expander
        self.append(expander)
        if large:
            expander.connect("notify::expanded", self._on_expanded)
        else:
            expander.set_child(self._diff_widget())

    def reveal(self) -> None:
        """Expand (building the buffer if this is the first time)."""
        if self._expander is not None:
            self._expander.set_expanded(True)

    def set_scheme(self, scheme: GtkSource.StyleScheme | None) -> None:
        """Restyle a built buffer now; a lazy one picks *scheme* up on build."""
        self._scheme = scheme
        if self._buffer is not None and scheme is not None:
            self._buffer.set_style_scheme(scheme)

    def _on_expanded(self, expander: Gtk.Expander, _pspec) -> None:
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
    """A card's byline: author, age (absolute stamp in the tooltip), then any
    *trailing* widgets — a review's verdict rides there. With a *url*, the
    age is a link to the entry on GitHub."""
    byline = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
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


def _body_label(text: str) -> Gtk.Widget:
    """A markdown body as a selectable wrapped label; past the render cap
    it starts folded behind "Show more" (the whole text is already
    bounded by prdetail, this cap is about Pango layout cost)."""
    label = Gtk.Label(xalign=0.0, selectable=True, wrap=True, hexpand=True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if len(text) <= _RENDER_CAP:
        _set_md(label, text)
        return label
    _set_md(label, text[:_RENDER_CAP] + "…")
    more = Gtk.Button(label=_("Show more"))
    more.add_css_class("flat")
    more.set_halign(Gtk.Align.START)

    def show_all(button: Gtk.Button) -> None:
        _set_md(label, text)
        button.set_visible(False)

    more.connect("clicked", show_all)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.append(label)
    box.append(more)
    return box


def _count_label(text: str, css_class: str) -> Gtk.Label:
    """A +n/−n caption in the checks-passed green or checks-failed red."""
    label = Gtk.Label(label=text)
    label.add_css_class("caption")
    label.add_css_class(css_class)
    return label


def _set_md(label: Gtk.Label, text: str) -> None:
    """Markdown onto a label, with chatbubbles' plain-text fallback: markup
    this module built from untrusted text must degrade, never raise."""
    try:
        label.set_markup(md_to_pango(text, links=True))
    except GLib.GError:
        label.set_label(text)
