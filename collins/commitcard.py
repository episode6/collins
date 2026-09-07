# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page's commit card: the message of the commit the page shows,
over the diff, folded the way the PR page folds a description.

`CommitCard` sits at the top of the git page's view column and is empty
(hidden) for every load but a commit. For a commit it shows the subject,
a byline (author, age with the absolute stamp in the tooltip, the short
sha — a link to the commit on GitHub when the repository has a page
there) and the body as markdown through the PR page's own fold
(`prview.folded_body`): eight lines of preview, then "Show more" /
"Show less". A one-line message is the subject and the byline alone. The
whole card scrolls within `MAX_HEIGHT` so an unfolded novel of a commit
message can't push the diff off the page.

The message is repository content (rule 5): every field arrives bounded
from `gitloads.commit_message`, the subject is escaped before Pango, the
body goes through the markdown block layer that escapes everything it
draws, and the GitHub URL is `gitinfo.github_url`'s, built from
``owner/repo`` it already vetted. `#123`, `@user` and commit references
in the body link into the repository's GitHub page (`mdblocks.
repo_context`) when there is one, at the commit's own sha for relative
links.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import mdblocks, mdwidgets  # noqa: E402
from .editor import GtkSource  # noqa: E402 — require_version + friendly exit live there
from .formatting import format_relative, format_timestamp  # noqa: E402
from .gitloads import CommitMessage, reflow_body, short_ref  # noqa: E402
from .i18n import _  # noqa: E402
from .prview import Fold, folded_body  # noqa: E402

# The card's tallest, unfolded: past this it scrolls on its own so the
# diff under it keeps most of the page.
MAX_HEIGHT = 360


class CommitCard(Gtk.ScrolledWindow):
    """See the module docstring. `show` / `clear` are the page's two
    verbs; `message` says what is shown; `set_scheme` restyles the body's
    code blocks when the editor's scheme or the app's light/dark moves."""

    __gtype_name__ = "CollinsCommitCard"

    def __init__(self) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_propagate_natural_height(True)
        self.set_max_content_height(MAX_HEIGHT)
        self.set_vexpand(False)
        self.add_css_class("git-commit-card-scroller")
        self._message: CommitMessage | None = None
        self._scheme: GtkSource.StyleScheme | None = None
        self._github_url: str | None = None  # part of the no-op guard: the links are built from it
        self._fold: Fold | None = None
        self._body: Gtk.Widget | None = None

        self._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._card.add_css_class("pr-card")
        self._card.add_css_class("git-commit-card")
        self._subject = Gtk.Label(xalign=0.0, selectable=True, wrap=True, hexpand=True)
        self._subject.add_css_class("heading")
        self._card.append(self._subject)
        self._byline = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._author = Gtk.Label(xalign=0.0)
        self._author.add_css_class("caption-heading")
        self._author.set_ellipsize(Pango.EllipsizeMode.END)
        self._when = Gtk.Label(xalign=0.0)
        self._when.add_css_class("caption")
        self._when.add_css_class("dim-label")
        self._sha = Gtk.Label(xalign=0.0, selectable=True)
        self._sha.add_css_class("caption")
        self._sha.add_css_class("monospace")
        self._sha.add_css_class("dim-label")
        self._byline.append(self._author)
        self._byline.append(self._when)
        self._byline.append(self._sha)
        self._card.append(self._byline)
        self.set_child(self._card)
        self.set_visible(False)

    # -- the page's verbs --

    @property
    def message(self) -> CommitMessage | None:
        """The commit shown, None while the card is empty."""
        return self._message

    def show(
        self, message: CommitMessage, github_url: str | None, scheme: GtkSource.StyleScheme | None
    ) -> None:
        """Show *message*. The same message again, with the same GitHub URL
        and scheme (a reload of the same commit; the byline's and the
        body's links are built from the URL, so a remote that appeared or
        went away rebuilds), leaves the card alone, so a fold the reader opened stays
        open; a different one rebuilds it, carrying the fold's state the
        way the PR page's description does across a refresh."""
        if message == self._message and scheme is self._scheme and github_url == self._github_url:
            self.set_visible(True)
            return
        expanded = self._fold.expanded if self._fold is not None else False
        self._message = message
        self._scheme = scheme
        self._github_url = github_url
        self._subject.set_text(message.subject or _("(no subject)"))
        self._author.set_text(message.author or _("unknown"))
        self._when.set_text(format_relative(message.authored_at))
        self._when.set_tooltip_text(format_timestamp(message.authored_at))
        commit_url = f"{github_url}/commit/{message.sha}" if github_url else ""
        if commit_url:
            href = GLib.markup_escape_text(commit_url)
            self._sha.set_markup(f'<a href="{href}">{GLib.markup_escape_text(short_ref(message.sha))}</a>')
        else:
            self._sha.set_text(short_ref(message.sha))
        self._sha.set_tooltip_text(message.sha)
        if self._body is not None:
            self._card.remove(self._body)
            self._body = None
        self._fold = None
        if message.body:
            text = reflow_body(message.body)  # git's 72-column wraps undone: the card wraps to its width
            body = folded_body(text, False, commit_url, scheme, _refs(github_url, message.sha))
            if isinstance(body, Fold):
                self._fold = body
                body.set_expanded(expanded)
            self._body = body
            self._card.append(body)
        self.set_visible(True)

    def clear(self) -> None:
        """Empty and hide the card (a load that isn't a commit)."""
        if self._body is not None:
            self._card.remove(self._body)
            self._body = None
        self._fold = None
        self._message = None
        self.set_visible(False)

    def set_scheme(self, scheme: GtkSource.StyleScheme | None) -> None:
        """Restyle the body's code blocks (the editor's scheme moved)."""
        self._scheme = scheme
        mdwidgets.restyle_code(self, scheme)

    # -- probes (the e2e's) --

    def subject_text(self) -> str:
        return self._subject.get_text()

    def byline_text(self) -> str:
        return " ".join(label.get_text() for label in (self._author, self._when, self._sha))

    def folded(self) -> bool | None:
        """True while the body waits behind "Show more", False once out,
        None when the body has no fold (it fits, or there is none)."""
        return None if self._fold is None else not self._fold.expanded

    def set_folded(self, folded: bool) -> None:
        if self._fold is not None:
            self._fold.set_expanded(not folded)

    def body_labels(self) -> list[str]:
        """The text of every label in the visible half of the body."""
        found: list[str] = []
        if self._body is None:
            return found
        _walk_labels(self._body, found)
        return found


def _refs(github_url: str | None, sha: str) -> mdblocks.RepoContext | None:
    """The repository the body's references link into, from the GitHub
    page URL gitinfo vetted — None without one (the references stay text)."""
    if not github_url:
        return None
    parts = urlsplit(github_url)
    return mdblocks.repo_context(parts.path.strip("/"), parts.netloc, sha)


def _walk_labels(widget: Gtk.Widget, found: list[str]) -> None:
    if not widget.get_visible():
        return
    if isinstance(widget, Gtk.Label):
        found.append(widget.get_text())
    child = widget.get_first_child()
    while child is not None:
        _walk_labels(child, found)
        child = child.get_next_sibling()
