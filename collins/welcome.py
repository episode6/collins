# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The first thing Collins says: what it will do with the user's quota, and
— when a launch can't find it — where the Claude Code CLI is.

Collins runs Claude on the user's behalf in a few places they never asked
for in so many words: naming new sessions, drawing project icons, renewing
an expired login, and offering every session a set of tools whose
definitions ride in its context (see tokensettings, and the Token use group
in Preferences). Nothing used to tell a new user any of that. This dialog
does, once per install, before the first of those runs happens: the same
rows Preferences shows — built by the same module, so the disclosure can't
drift from the switches — under a heading that says why they are here.
Toggling writes the setting at once, as Preferences does, so Escape after a
toggle loses nothing; Continue (or a close) records the dialog as seen.
The gate is welcomegate.should_show, GTK-free so the unit suite holds it.

The dialog's first group is the CLI, in one of two states. Found on PATH:
one row naming which `claude` Collins is using, so a wrong auto-detect is
visible. Not found: the ask the CLI-only welcome used to be (see clisetup
for why a desktop launch so often can't find it, and for what counts as a
good answer). It comes before the GitHub CLI notice — gh is a tool Collins
is better with, the agent CLI is the tool Collins is *about* — and unlike
that notice it blocks: there is no "not now" for the thing every feature
runs through, so in this state the dialog can't be Escaped, only answered
or quit out of.

The answer is a path, so the group is a path box: pre-filled from the
places the CLI is known to land when one of them has it, a Browse button
for when none does, and a live verdict beside it — a green check for a
path that will work, a red cross for one that won't, with the reason
underneath. The verdict is clisetup.validate()'s, which refuses
version-numbered paths (they die on the CLI's next self-update) and never
resolves symlinks (the stable launcher *is* a symlink; resolving it would
store the very path just refused). Accepting stores the path, puts its
directory on the app's PATH, and sets the world in motion exactly as a
CLI-found launch would have: the session store rescans, the remembered
session reopens, and the rest of the launch's welcome-work gets its turn.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import clisetup, tokensettings, welcomegate  # noqa: E402
from .i18n import _  # noqa: E402
from .state import AppState  # noqa: E402

log = logging.getLogger(__name__)

# Re-exported: the gate and its setting are welcomegate's (GTK-free, for the
# unit suite), and callers that have the dialog module need nothing else.
SEEN_SETTING = welcomegate.SEEN_SETTING
should_show = welcomegate.should_show

# Where to send someone who has no CLI to point at. The product page, not
# any one install command — which package manager to name is a question
# this app can't answer (the same reasoning as ghsetup.INSTALL_URL).
INSTALL_URL = "https://claude.com/claude-code"

# The dialog's size: three preferences groups, the Token use one with four
# tall rows, and an action bar. As wide as Preferences, and for the same
# rows' sake: a combo row's value label is what gives way when its subtitle
# is long, and at 560px "Default (latest Haiku)" was an ellipsis. Small
# screens still clamp it down (an Adw.Dialog becomes a bottom sheet on a
# narrow one by itself).
_CONTENT_WIDTH_PX = 640
_CONTENT_HEIGHT_PX = 640

# The mark each verdict wears: green check for a path that will keep
# working, yellow warning for one that works but can't be promised to
# (a version manager's tree), red cross for one that won't. Public, like
# reason_for below: the Preferences row that lets a stored answer be
# changed later (prefs) judges paths with the same marks and words.
MARKS = {
    clisetup.OK: ("object-select-symbolic", "success"),
    clisetup.VERSION_MANAGED: ("dialog-warning-symbolic", "warning"),
}
BAD_MARK = ("window-close-symbolic", "error")


def maybe_show(window, state: AppState, store, then: Callable[[], None]):
    """Show the welcome if this launch owes it, then run `then` — at once
    when it doesn't, on acceptance when it does.

    `then` is the rest of the launch's welcome-work: the GitHub CLI notice,
    and the expired-login repair — which must not fire before the switch
    that governs it has been seen, or the dialog is disclosing a run that
    already happened. (Sequencing `then` is not what holds that line — the
    usage panel asks for the same repair from under the dialog — tokenrefresh
    refusing until ``welcome_seen`` is written is; `then` just keeps the
    launch check from being refused for nothing.) Returns the dialog, or
    None when nothing showed.
    """
    cli_found = clisetup.on_path()
    if not welcomegate.should_show(state, cli_found):
        then()
        return None
    if not cli_found:
        log.info("clisetup: claude CLI not on PATH; asking where it is")
    log.info("welcome: showing the first-launch dialog")
    dialog = WelcomeDialog(window, state, store, then, cli_found)
    dialog.present(window)
    return dialog


class WelcomeDialog(Adw.Dialog):
    """The dialog itself: a preferences page of three groups and one
    button. See the module docstring for what it is for."""

    def __init__(
        self,
        window,
        state: AppState,
        store,
        then: Callable[[], None],
        cli_found: bool,
    ) -> None:
        super().__init__(
            title=_("Before you start"),
            content_width=_CONTENT_WIDTH_PX,
            content_height=_CONTENT_HEIGHT_PX,
        )
        self._window = window
        self._state = state
        self._store = store
        self._then = then
        self._cli_found = cli_found
        self._done = False  # answered (seen recorded, then() run)
        self._quit = False  # Quit pressed: closing must not count as an answer

        # The button first: the CLI group's prefilled path judges itself as
        # it is set, and the verdict is what the button says.
        self._button = Gtk.Button()
        self._button.add_css_class("suggested-action")
        self._button.connect("clicked", self._on_continue)

        page = Adw.PreferencesPage(
            description=_(
                "Collins runs Claude for you in a few places. Here's where, and the "
                "switches for each."
            )
        )
        page.add(self._build_cli_group())
        page.add(self._build_token_group())
        page.add(self._build_mcp_group())

        header = Adw.HeaderBar()
        bar = Gtk.ActionBar()
        bar.pack_end(self._button)
        if not cli_found:
            # No way out but an answer (or Quit): without the CLI every pane
            # of the app is a convincing drawing of an empty one.
            # can_close=False refuses more than Escape: the header's close
            # button too (hidden, rather than shown inert), and the host
            # window's close() while the dialog is up — so every answered
            # path below must force_close() itself.
            self.set_can_close(False)
            header.set_show_end_title_buttons(False)
            quit_button = Gtk.Button(label=_("Quit"))
            quit_button.connect("clicked", self._on_quit)
            bar.pack_start(quit_button)
            self._update_verdict()
        else:
            self._button.set_label(_("Continue"))

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        view.add_bottom_bar(bar)
        self.set_child(view)
        self.set_default_widget(self._button)
        # Typing is the not-found state's whole job; don't make the first
        # keystroke a click. set_focus, not a map-idle grab (see the rename
        # dialogs). With the CLI found, Enter is Continue.
        self.set_focus(self._entry if not cli_found else self._button)
        self.connect("closed", self._on_closed)

    # -- the groups ----------------------------------------------------------

    def _build_cli_group(self) -> Adw.PreferencesGroup:
        if self._cli_found:
            # One row, so the dialog's first group is the same thing on
            # every machine — and so a wrong auto-detect is visible.
            # Preferences has the row that changes it.
            group = Adw.PreferencesGroup(title=_("Claude Code CLI"))
            row = Adw.ActionRow(
                title=_("Using claude at {path}").format(path=clisetup.found_at()),
                subtitle=_("Change it later in Preferences"),
                activatable=False,
                selectable=False,
            )
            icon, style = MARKS[clisetup.OK]
            mark = Gtk.Image.new_from_icon_name(icon)
            mark.set_valign(Gtk.Align.CENTER)
            mark.add_css_class(style)
            row.add_suffix(mark)
            group.add(row)
            return group

        group = Adw.PreferencesGroup(
            title=_("Collins needs the Claude Code CLI"),
            description=_(
                "Every session runs through the claude command, and it isn't on the PATH "
                "that launches from the desktop are given — that PATH doesn't include the "
                "folders your shell adds. Point Collins at the CLI once; the location is "
                "remembered from then on."
            ),
        )
        self._entry = Adw.EntryRow(title=_("Path to the claude executable"))
        self._verdict = Gtk.Image(valign=Gtk.Align.CENTER)
        browse = Gtk.Button(label=_("Browse…"), valign=Gtk.Align.CENTER)
        browse.add_css_class("flat")
        browse.connect("clicked", self._on_browse)
        self._entry.add_suffix(self._verdict)
        self._entry.add_suffix(browse)
        self._entry.connect("changed", lambda *_a: self._update_verdict())
        group.add(self._entry)
        # The verdict's reason, directly under the box: a title-less row in
        # the same list, as the Preferences row does it.
        self._reason = Adw.ActionRow(activatable=False, selectable=False)
        self._reason.add_css_class("dim-label")
        group.add(self._reason)
        group.add(_install_hint())
        prefilled = clisetup.detect()
        if prefilled:
            self._entry.set_text(prefilled)  # fires "changed" → first verdict
            self._entry.set_position(-1)
        return group

    def _build_token_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=_(tokensettings.TITLE), description=_(tokensettings.DESCRIPTION)
        )
        for row in tokensettings.build_token_rows(self._state, self._on_change):
            group.add(row)
        return group

    def _build_mcp_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=_(tokensettings.MCP_TITLE), description=_(tokensettings.MCP_DESCRIPTION)
        )
        for row in tokensettings.build_mcp_rows(self._state, self._on_change).values():
            group.add(row)
        return group

    def _on_change(self) -> None:
        # The rows have written their settings; the window pushes them where
        # they take effect at once (the MCP tool switches, above all), the
        # way it does after a Preferences change.
        self._window.apply_preferences()

    # -- the CLI ask ---------------------------------------------------------

    def _update_verdict(self) -> None:
        """Re-judge the path on every keystroke: the mark, the reason, and
        what the accept button is — Use This CLI, disabled, for a path that
        won't do; Continue for one that will. One button, two labels."""
        text = self._entry.get_text()
        status = clisetup.validate(text)
        icon, style = MARKS.get(status, BAD_MARK)
        self._verdict.set_from_icon_name(icon)
        for name in ("success", "warning", "error"):
            _set_class(self._verdict, name, name == style)
        self._reason.set_subtitle(reason_for(status, text.strip()))
        acceptable = status in MARKS
        self._button.set_sensitive(acceptable)
        self._button.set_label(_("Continue") if acceptable else _("Use This CLI"))

    def _on_browse(self, _button: Gtk.Button) -> None:
        picker = Gtk.FileDialog(title=_("Choose the claude executable"))

        def picked(picker: Gtk.FileDialog, result) -> None:
            try:
                file = picker.open_finish(result)
            except Exception:
                return  # dismissed
            if file is not None and file.get_path():
                # The picked path goes in as picked — a symlink stays a
                # symlink, and validate() is the one that judges it.
                self._entry.set_text(file.get_path())
                self._entry.set_position(-1)

        picker.open(self.get_root(), None, picked)

    # -- the answers ---------------------------------------------------------

    def _on_quit(self, _button: Gtk.Button) -> None:
        # The one other way out of the not-found state. Nothing is running
        # (nothing could be), so this is the ordinary close path, not a
        # forced exit — but the dialog has to go down first: while it's
        # presented, its can_close=False blocks the window's close too.
        self._quit = True
        self.force_close()
        self._window.close()

    def _on_continue(self, _button: Gtk.Button) -> None:
        if self._cli_found:
            self.close()  # → "closed" → _on_closed records the answer
            return
        text = self._entry.get_text().strip()
        if clisetup.validate(text) not in MARKS:
            return  # the button is insensitive; a default-widget Enter isn't
        self._done = True
        # can_close=False would refuse this click's own close, so take the
        # dialog down before then() presents the GitHub notice, which a
        # lingering dialog would shadow.
        self.force_close()
        # Stored as given — unexpanded, unresolved — so the stable launcher
        # the user pointed at is the thing remembered, not tonight's version
        # of it.
        self._state.set_setting(clisetup.PATH_SETTING, text)
        if clisetup.apply(text):
            log.info("clisetup: claude CLI confirmed at %s", text)
        else:  # validated a moment ago; only a same-instant uninstall gets here
            log.warning("clisetup: %s accepted but claude still not findable", text)
        self._state.set_setting(SEEN_SETTING, True)
        # Now the launch that would have happened, happens: rescan (which
        # also arms the file monitors), reopen the remembered session, and
        # let the rest of the welcome-work take its turn. restore_last_session
        # is a second call — do_activate already made one, whose one-shot
        # burned on the store's empty first scan — and deliberately so: it
        # re-arms against the rescan, and open_session re-selects rather than
        # duplicates an existing tab.
        self._store.refresh(force_rebuild=True)
        self._window.restore_last_session()
        self._then()

    def _on_closed(self, _dialog: Adw.Dialog) -> None:
        """Any close of the found-state dialog — Continue, Escape, the header's
        close button — is the answer: the rows have already written whatever
        was toggled, and the dialog has been seen. In the not-found state a
        close is never the user's (can_close is off); a programmatic
        force_close (tests, probes) must not count as one."""
        if self._done or self._quit or not self._cli_found:
            return
        self._done = True
        self._state.set_setting(SEEN_SETTING, True)
        self._then()


def reason_for(status: str, text: str) -> str:
    if status == clisetup.OK:
        return _("Found it — Collins will remember this location.")
    if status == clisetup.VERSIONED:
        return _(
            "This path has a version number in it, so it would break the next time "
            "Claude Code updates itself. Point at a stable launcher instead — "
            "usually ~/.local/bin/claude."
        )
    if status == clisetup.VERSION_MANAGED:
        return _(
            "This is inside a version manager's tree, so Collins can't validate a "
            "stable path — it will work until that tool updates, and then this "
            "question comes back."
        )
    if status == clisetup.BAD_NAME:
        return _("That's an executable, but not one named “claude” — pick the claude launcher itself.")
    if not text:
        return _("It wasn't in any of the usual places — enter or browse to where it's installed.")
    return _("There's no executable file at this path.")


def _set_class(widget: Gtk.Widget, name: str, on: bool) -> None:
    if on:
        widget.add_css_class(name)
    else:
        widget.remove_css_class(name)


def _install_hint() -> Gtk.Widget:
    """For the launch with nothing to point at: where Claude Code comes from.
    A plain label, which a preferences group seats below its list."""
    label = Gtk.Label(xalign=0.0, wrap=True, use_markup=True)
    label.set_markup(
        _("No Claude Code yet? Get it at {link}, then come back.").format(
            link=f'<a href="{INSTALL_URL}">claude.com/claude-code</a>'
        )
    )
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    label.set_margin_top(6)
    return label
