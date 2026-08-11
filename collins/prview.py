# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The native pull-request page: one PR rendered as GTK widgets, no browser.

`PrViewPage` is the `pr` kind of PanelPage (see panelstrip): a panel-dock tab
beside the agent terminal showing what the PR's own page would — header,
description, checks, conversation — off the `gh` auth Collins already has.
The data is `prdetail.fetch`'s reply, fetched on a worker thread on first
show, on the Refresh button, and (throttled) whenever the page comes back
into view; the widgets here only ever render what that GTK-free layer parsed
and bounded. Failures keep the last-loaded content under an inline banner —
stale beats blank, as everywhere in the PR stack.

Everything shown is repository content and therefore untrusted: bodies go
through `formatting.md_to_pango`'s escaping (with the plain-text fallback on
malformed markup), only http(s) URLs ever reach a browser (prdetail already
enforced that on the way in), and a pathological body renders capped behind
a "Show more" step so building labels can't wedge the main loop.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from . import prdetail, prmenu  # noqa: E402
from .copylabel import open_tooltip, open_uri  # noqa: E402
from .formatting import format_relative, format_timestamp, md_to_pango  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .prstatus import PullRequest  # noqa: E402

log = logging.getLogger(__name__)

# Re-showing the page re-reads the PR, but not more often than this — the
# same arrival throttle the footer chips use (terminal._PR_FOCUS_REFRESH_MIN_US).
_FOCUS_REFRESH_MIN_US = 10 * 1_000_000
# Where a body stops rendering until "Show more" is pressed. Well under
# prdetail's storage bound: a label this long is already a scroll of its own,
# and Pango layout cost grows with every character the main loop hands it.
_RENDER_CAP = 20_000

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

        # -- content ---------------------------------------------------------
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._content.set_margin_top(10)
        self._content.set_margin_bottom(10)
        self._content.set_margin_start(10)
        self._content.set_margin_end(10)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self._banner)
        column.append(self._content)
        self._scroller = Gtk.ScrolledWindow(child=column, vexpand=True)
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroller.set_focusable(True)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self._scroller)
        self.set_child(view)

        self._show_loading()
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
        self._scroller.grab_focus()

    def has_page_focus(self) -> bool:
        root = self.get_root()
        focus = root.get_focus() if root is not None else None
        return focus is not None and (focus is self or focus.is_ancestor(self))

    def page_busy(self) -> bool:
        return False  # nothing running: a PR page is cheap to refetch

    def apply_settings(self, settings: dict) -> None:
        pass  # renders in the app font and theme; nothing terminal-ish here

    def page_state(self) -> dict:
        """This page's slot in a serialized dock layout (see panellayout):
        the URL is the whole state — a restored page refetches the rest."""
        return {"kind": "pr", "url": self.pr_url}

    # -- fetching -------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the PR now — the Refresh button, and the dedupe path's
        "you asked for this page again"."""
        self._fetch(force=True)

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
            return GLib.SOURCE_REMOVE
        self._banner.set_reveal_child(False)
        self._detail = detail
        self._pr = detail.summary
        self._sync_header()
        self._rebuild()
        self.emit("title-changed")
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
        self._content.append(self._description_card(detail))
        if detail.checks:
            self._content.append(self._checks_section(detail.checks))
        if detail.timeline:
            heading = Gtk.Label(label=_("Conversation"), xalign=0.0)
            heading.add_css_class("caption-heading")
            self._content.append(heading)
            for entry in detail.timeline:
                if isinstance(entry, prdetail.PrReview):
                    self._content.append(self._review_card(entry))
                else:
                    self._content.append(self._comment_card(entry))
        else:
            empty = Gtk.Label(label=_("No comments yet."), xalign=0.0)
            empty.add_css_class("dim-label")
            self._content.append(empty)

    def _description_card(self, detail: prdetail.PullRequestDetail) -> Gtk.Widget:
        card = self._card(detail.author, detail.created_at)
        if detail.body:
            card.append(self._body_label(detail.body))
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
        card = self._card(comment.author, comment.created_at, url=comment.url)
        card.append(self._body_label(comment.body))
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
        card = self._card(review.author, review.created_at, trailing=[icon, verdict])
        if review.body:
            card.append(self._body_label(review.body))
        return card

    def _card(
        self,
        author: str,
        created_at: str,
        url: str = "",
        trailing: list[Gtk.Widget] | None = None,
    ) -> Gtk.Box:
        """One conversation card: a byline, then whatever the caller appends.

        The byline is author, age (absolute stamp in the tooltip), and any
        *trailing* widgets — a review's verdict rides there. With a *url*,
        the age is a link to the entry on GitHub.
        """
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("pr-card")
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
        spacer = Gtk.Box(hexpand=True)
        byline.append(spacer)
        for widget in trailing or []:
            byline.append(widget)
        card.append(byline)
        return card

    def _body_label(self, text: str) -> Gtk.Widget:
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


def _set_md(label: Gtk.Label, text: str) -> None:
    """Markdown onto a label, with chatbubbles' plain-text fallback: markup
    this module built from untrusted text must degrade, never raise."""
    try:
        label.set_markup(md_to_pango(text, links=True))
    except GLib.GError:
        label.set_label(text)
