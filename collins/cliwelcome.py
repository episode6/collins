"""The ask that stands between a launch and a blank sidebar: where is the
Claude Code CLI?

Shown when a launch can't find `claude` on PATH (see clisetup for why a
desktop launch so often can't, and for what counts as a good answer). It
comes before the GitHub CLI notice — gh is a tool Collins is better with,
the agent CLI is the tool Collins is *about* — and unlike that notice it
blocks: there is no "not now" for the thing every feature runs through,
so the dialog can't be Escaped, only answered or quit out of.

The answer is a path, so the dialog is a path box: pre-filled from the
places the CLI is known to land when one of them has it, a Browse button
for when none does, and a live verdict beside it — a green check for a
path that will work, a red cross for one that won't, with the reason
underneath. The verdict is clisetup.validate()'s, which refuses
version-numbered paths (they die on the CLI's next self-update) and never
resolves symlinks (the stable launcher *is* a symlink; resolving it would
store the very path just refused).

Accepting stores the path, puts its directory on the app's PATH, and sets
the world in motion exactly as a CLI-found launch would have: the session
store rescans, the remembered session reopens, and the GitHub notice gets
its turn.
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import clisetup  # noqa: E402
from .i18n import _  # noqa: E402
from .state import AppState  # noqa: E402

log = logging.getLogger(__name__)

# An AlertDialog sizes itself to its heading (see the rename dialogs and
# ghwelcome), so the extra child asks for the room the path box needs.
_CONTENT_WIDTH_PX = 420

# Where to send someone who has no CLI to point at. The product page, not
# any one install command — which package manager to name is a question
# this app can't answer (the same reasoning as ghsetup.INSTALL_URL).
INSTALL_URL = "https://claude.com/claude-code"


def maybe_show(window, state: AppState, store, then) -> None:
    """Ask for the CLI if it needs asking, then run `then` — immediately
    when PATH already answers, on acceptance when the dialog had to.

    `then` is the rest of the launch's welcome-work (the GitHub CLI
    notice): it belongs after this either way, so it rides along rather
    than racing the dialog. The check is `shutil.which` — no subprocess,
    so unlike ghsetup it can be asked inline on the main thread.
    """
    if clisetup.on_path():
        then()
        return
    log.info("clisetup: claude CLI not on PATH; asking where it is")
    _dialog(window, state, store, then).present(window)


def _dialog(window, state: AppState, store, then) -> Adw.AlertDialog:
    dialog = Adw.AlertDialog(
        heading=_("Collins needs the Claude Code CLI"),
        body=_(
            "Every session runs through the claude command, and it isn't on the PATH "
            "that launches from the desktop are given — that PATH doesn't include the "
            "folders your shell adds. Point Collins at the CLI once; the location is "
            "remembered from then on."
        ),
    )
    # No way out but an answer (or Quit below): without the CLI every pane
    # of the app is a convincing drawing of an empty one.
    dialog.set_can_close(False)

    entry = Gtk.Entry(hexpand=True)
    entry.set_placeholder_text(_("Path to the claude executable"))
    verdict = Gtk.Image()
    verdict.set_valign(Gtk.Align.CENTER)
    browse = Gtk.Button(label=_("Browse…"))
    browse.set_valign(Gtk.Align.CENTER)
    reason = Gtk.Label(xalign=0.0, wrap=True)
    reason.add_css_class("caption")
    reason.add_css_class("dim-label")

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.append(entry)
    row.append(verdict)
    row.append(browse)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_size_request(_CONTENT_WIDTH_PX, -1)
    box.append(row)
    box.append(reason)
    box.append(_install_hint())
    dialog.set_extra_child(box)

    dialog.add_response("quit", _("Quit"))
    dialog.add_response("use", _("Use This CLI"))
    dialog.set_response_appearance("use", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("use")

    entry.connect("changed", lambda *_a: _update(dialog, entry, verdict, reason))
    browse.connect("clicked", lambda *_a: _browse(dialog, entry))
    dialog.connect("response", _on_response, entry, state, store, window, then)

    prefilled = clisetup.detect()
    if prefilled:
        entry.set_text(prefilled)  # fires "changed" → first verdict
        entry.set_position(-1)
    else:
        _update(dialog, entry, verdict, reason)
    # Typing is the dialog's whole job; don't make the first keystroke a
    # click. set_focus, not a map-idle grab (see the rename dialogs).
    dialog.set_focus(entry)
    return dialog


# The mark each verdict wears: green check for a path that will keep
# working, yellow warning for one that works but can't be promised to
# (a version manager's tree), red cross for one that won't.
_MARKS = {
    clisetup.OK: ("object-select-symbolic", "success"),
    clisetup.VERSION_MANAGED: ("dialog-warning-symbolic", "warning"),
}
_BAD_MARK = ("window-close-symbolic", "error")


def _update(dialog: Adw.AlertDialog, entry: Gtk.Entry, verdict: Gtk.Image, reason: Gtk.Label) -> None:
    """Re-judge the path on every keystroke: the mark, the reason, and
    whether the accept button is live."""
    status = clisetup.validate(entry.get_text())
    icon, style = _MARKS.get(status, _BAD_MARK)
    verdict.set_from_icon_name(icon)
    for name in ("success", "warning", "error"):
        _set_class(verdict, name, name == style)
    reason.set_label(_reason_for(status, entry.get_text().strip()))
    dialog.set_response_enabled("use", status in _MARKS)


def _reason_for(status: str, text: str) -> str:
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
    """For the launch with nothing to point at: where Claude Code comes from."""
    label = Gtk.Label(xalign=0.0, wrap=True, use_markup=True)
    label.set_markup(
        _("No Claude Code yet? Get it at {link}, then come back.").format(
            link=f'<a href="{INSTALL_URL}">claude.com/claude-code</a>'
        )
    )
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    return label


def _browse(dialog: Adw.AlertDialog, entry: Gtk.Entry) -> None:
    picker = Gtk.FileDialog(title=_("Choose the claude executable"))

    def picked(picker: Gtk.FileDialog, result) -> None:
        try:
            file = picker.open_finish(result)
        except Exception:
            return  # dismissed
        if file is not None and file.get_path():
            # The picked path goes in as picked — a symlink stays a
            # symlink, and validate() is the one that judges it.
            entry.set_text(file.get_path())
            entry.set_position(-1)

    picker.open(dialog.get_root(), None, picked)


def _on_response(
    dialog: Adw.AlertDialog,
    response: str,
    entry: Gtk.Entry,
    state: AppState,
    store,
    window,
    then,
) -> None:
    if response == "quit":
        # The one other way out. Nothing is running (nothing could be), so
        # this is the ordinary close path, not a forced exit.
        window.close()
        return
    if response != "use":
        # The close response ("close"). can_close(False) means no user path
        # emits it, but a programmatic force_close (tests, probes) does —
        # and it must not take the window down with it.
        return
    text = entry.get_text().strip()
    # Stored as given — unexpanded, unresolved — so the stable launcher the
    # user pointed at is the thing remembered, not tonight's version of it.
    state.set_setting(clisetup.PATH_SETTING, text)
    if clisetup.apply(text):
        log.info("clisetup: claude CLI confirmed at %s", text)
    else:  # validated a moment ago; only a same-instant uninstall gets here
        log.warning("clisetup: %s accepted but claude still not findable", text)
    # Now the launch that would have happened, happens: rescan (which also
    # arms the file monitors), reopen the remembered session, and let the
    # GitHub notice take its turn. restore_last_session is a second call —
    # do_activate already made one, whose one-shot burned on the store's
    # empty first scan — and deliberately so: it re-arms against the rescan,
    # and open_session re-selects rather than duplicates an existing tab.
    store.refresh(force_rebuild=True)
    window.restore_last_session()
    then()
