"""The notice that Collins is better with the GitHub CLI installed.

Shown on a launch where `gh` is missing or signed out (see ghsetup for how
that is asked, and why it is asked locally) — a first launch especially, where
a tool that isn't there reads as a feature that doesn't exist.

It comes back every such launch until it is told not to, and the only thing
that tells it is the "Don't show this again" box in it. Dismissing without
ticking that is "not now", which is a real answer to a suggestion about a tool
you haven't got yet — installing gh is a thing to do later, and a notice that
retired itself on the first Escape would be a suggestion made once, to
someone in the middle of something else. A launch that finds gh set up says
nothing and records nothing, so the box is the only way this ends and a
`gh` that appears in the meantime ends it just as well.

A dialog that only *said* "pull request support needs gh" would be asking for
trust it hasn't earned yet, so this one shows the thing itself: the real
status marks the chips and the sidebar draw, built by prmenu from sample pull
requests, and the real menu rows practions offers over them. Nothing here
writes its own copy of either — the marks come out of the same builder the
footer uses and the labels out of the same actions the menu lists, so the
notice can't promise a feature that has since been renamed or dropped.

Then the fix, which is one of two. A missing gh gets a link to cli.github.com
and nothing else: the install differs by platform and by package manager, and
transcribing any one of them here would be Collins owning instructions it
can't keep current. A signed-out gh gets the single command that fixes it,
click-to-copy.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import ghsetup, practions, prmenu  # noqa: E402
from .copylabel import enable_copy_on_click, open_uri  # noqa: E402
from .i18n import _  # noqa: E402
from .prstatus import PullRequest  # noqa: E402
from .state import AppState  # noqa: E402

log = logging.getLogger(__name__)

# The setting the "Don't show this again" box writes, and the only thing that
# stops the notice coming back. Read before anything is spawned, so an install
# that has waved it off pays nothing for it ever again.
DISMISSED_SETTING = "gh_welcome_dismissed"

# How long after the window opens to ask. The check is a subprocess on a
# thread, and a launch already has a shell per restored tab to spawn — this
# keeps the notice off the first paint, and lands it while someone is still
# looking at a new window rather than mid-sentence in a session.
_DELAY_MS = 1500

# An AlertDialog sizes itself to its heading, so a wide extra child has to ask
# (see the rename dialogs); wide enough here for the sample rows to read as
# rows rather than as wrapped paragraphs.
_CONTENT_WIDTH_PX = 400
# Between the rows of one list. Tight: they are one thing said six times, and
# the dialog has two lists in it.
_ROW_SPACING_PX = 3
# How long the "Don't show this again" box stays out of the tab ring after the
# dialog is built (see `_arm_silence_focus`). The scroll it would otherwise
# cause starts ~50ms in and settles by ~250ms; a second is clear of that on a
# slower machine and still nowhere near a first Tab.
_FOCUSABLE_AFTER_MS = 1000


def _samples() -> tuple[tuple[PullRequest, str], ...]:
    """Sample pull requests, one per thing a mark can say, and what it says.

    Built when the dialog is (rather than at import) so the descriptions are
    translated in the language the app is actually running in. The marks
    themselves come from prmenu, exactly as the footer's chips do — a state
    that starts drawing differently starts drawing differently here too.
    """
    return (
        (
            PullRequest(203, "", state="OPEN", passed=4),
            _("Open, every check passed"),
        ),
        (
            PullRequest(204, "", state="OPEN", passed=1, pending=3),
            _("Checks still running"),
        ),
        (
            PullRequest(205, "", state="OPEN", failed=1, passed=3),
            _("A check failed"),
        ),
        (
            PullRequest(206, "", state="OPEN", passed=4, unresolved=True),
            _("A reviewer is waiting on a reply"),
        ),
        (
            PullRequest(207, "", state="DRAFT", mergeable="CONFLICTING"),
            _("Draft, and the branch conflicts"),
        ),
        (PullRequest(202, "", state="MERGED"), _("Merged")),
    )


# Which actions to show, in this order — the menu itself is per-PR and no one
# pull request offers all of them. The labels and tooltips come from practions
# (see `_sample_actions`), so this is a running order, not a second copy of
# the menu.
_ACTION_KEYS = (
    practions.READY,
    practions.MERGE,
    practions.FIX_CI,
    practions.COMMENTS,
    practions.REVIEW,
)


def _sample_actions() -> list[practions.Action]:
    """The rows in `_ACTION_KEYS` order, each built by practions itself.

    Asked of the PR states that offer them — a draft is what offers "mark
    ready", a red build is what offers the CI fix — and the first answer for
    each key wins. An action practions stops offering simply drops out of the
    notice.
    """
    states = (
        PullRequest(1, "", state="DRAFT"),
        PullRequest(2, "", state="OPEN", passed=4),
        PullRequest(3, "", state="OPEN", failed=1),
        PullRequest(4, "", state="OPEN", passed=4, unresolved=True),
    )
    offered: dict[str, practions.Action] = {}
    for pr in states:
        for action in practions.actions_for(pr):
            offered.setdefault(action.key, action)
    return [offered[key] for key in _ACTION_KEYS if key in offered]


def maybe_show(parent: Gtk.Widget, state: AppState) -> None:
    """Show the notice unless it has been waved off, or gh is ready anyway.

    Costs nothing at all once dismissed — the setting is read before anything
    is spawned. Otherwise the check runs on a worker thread and the dialog is
    presented back on the main loop, so a `gh` that hangs delays a dialog and
    never a window.
    """
    if state.get_setting(DISMISSED_SETTING):
        return
    GLib.timeout_add(_DELAY_MS, lambda: _start(parent, state))


def _start(parent: Gtk.Widget, state: AppState) -> bool:
    def work() -> None:
        status = ghsetup.check()
        GLib.idle_add(_land, parent, state, status)

    threading.Thread(target=work, daemon=True).start()
    return GLib.SOURCE_REMOVE


def _land(parent: Gtk.Widget, state: AppState, status: str) -> bool:
    """Put the notice up, unless there is nothing to say or nowhere to say it."""
    if status == ghsetup.READY:
        return GLib.SOURCE_REMOVE  # nothing missing; nothing to say
    if not parent.get_visible():
        # The window was closed while gh was being asked — a launch someone
        # quit out of within a second or two. Nothing to present on.
        return GLib.SOURCE_REMOVE
    log.info("ghsetup: showing the GitHub CLI notice (%s)", status)
    _dialog(status, state).present(parent)
    return GLib.SOURCE_REMOVE


def _dialog(status: str, state: AppState) -> Adw.AlertDialog:
    missing = status == ghsetup.MISSING
    dialog = Adw.AlertDialog(
        heading=_("Collins is better with the GitHub CLI"),
        body=(
            _(
                "Collins follows the pull requests your sessions open — and acts on them — "
                "through gh, GitHub's own command-line tool, which isn't installed here."
            )
            if missing
            else _(
                "Collins follows the pull requests your sessions open — and acts on them — "
                "through gh, GitHub's own command-line tool, which is installed here but "
                "never signed in."
            )
        ),
    )
    silence = _silence_check()
    dialog.set_extra_child(_content(missing, silence))
    dialog.add_response("dismiss", _("Not now"))
    dialog.set_close_response("dismiss")
    # Either way the dialog has one thing to press: the page that carries the
    # install, or the command on the clipboard ready to paste into a shell.
    # The command is click-to-copy in the body as well, but a notice about a
    # tool the user hasn't got shouldn't rely on them discovering that.
    done = "install" if missing else "copy"
    dialog.add_response(done, _("Get the GitHub CLI") if missing else _("Copy command"))
    dialog.set_response_appearance(done, Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response(done)
    dialog.connect("response", _on_response, silence, state)
    _arm_silence_focus(silence)
    return dialog


def _on_response(
    dialog: Adw.AlertDialog, response: str, silence: Gtk.CheckButton, state: AppState
) -> None:
    """Act on the button, and honour the box however the dialog was left.

    Every way out counts — the suggested action, "Not now", Escape — because
    the box is a statement about the notice rather than about the button
    beside it: someone who ticks it and then goes to fetch gh anyway has still
    said they don't need telling again.
    """
    if response == "install":
        open_uri(dialog, ghsetup.INSTALL_URL)
    elif response == "copy":
        dialog.get_clipboard().set(ghsetup.LOGIN_COMMAND)
    if silence.get_active():
        log.info("ghsetup: the GitHub CLI notice was waved off for good")
        state.set_setting(DISMISSED_SETTING, True)


def _content(missing: bool, silence: Gtk.CheckButton) -> Gtk.Widget:
    """Everything under the body: what gh buys, how to get it, and the box
    that stops it being brought up again.

    Two tight groups inside a loose one — the rows of a group are a list and
    belong together, the groups are separate points. A dialog this tall is
    already close to a small window's height (an AlertDialog scrolls its extra
    child, but a notice nobody can read in one look is a notice nobody reads),
    so the spacing is where the room comes from.
    """
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_size_request(_CONTENT_WIDTH_PX, -1)
    box.append(_section(_("With it, every session's pull requests carry their status:")))
    box.append(_group(_sample_row(pr, description) for pr, description in _samples()))
    box.append(_section(_("…and a click on one does something about it:")))
    box.append(_group(_action_row(action) for action in _sample_actions()))
    box.append(Gtk.Separator())
    box.append(_fix(missing))
    box.append(silence)
    return box


def _silence_check() -> Gtk.CheckButton:
    """The box that retires the notice, at the bottom where the answers are.

    Unticked, and unfocusable for the first moment of the dialog's life — see
    `_arm_silence_focus`, which gives it back.
    """
    check = Gtk.CheckButton(label=_("Don't show this again"))
    check.add_css_class("caption")
    check.set_margin_top(4)
    check.set_can_focus(False)
    return check


def _arm_silence_focus(check: Gtk.CheckButton) -> None:
    """Put the box back in the tab ring, once doing so can't move the dialog.

    An AlertDialog keeps its body and extra child in a scrolled window, which
    is what saves this notice on a screen too short for it. But a scrolled
    window animates itself to reveal a focusable child as the dialog presents,
    and the box is both the only focusable thing in here and the last thing in
    it: the dialog would glide, over about a quarter of a second, from its
    heading down to its own tail — everything it exists to show scrolled off
    the top. (Focus lands on the default response, correctly; it is the reveal
    that moves.) Undoing that afterwards means racing an animation, and
    leaving the box unfocusable means taking it off the keyboard.

    So it is unfocusable only while that pass happens, and focusable a second
    later — far past the animation, and far short of anyone reaching for Tab.
    """
    GLib.timeout_add(_FOCUSABLE_AFTER_MS, lambda: check.set_can_focus(True) or GLib.SOURCE_REMOVE)


def _group(rows: Iterable[Gtk.Widget]) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=_ROW_SPACING_PX)
    for row in rows:
        box.append(row)
    return box


def _section(text: str) -> Gtk.Widget:
    label = Gtk.Label(label=text, xalign=0.0, wrap=True)
    label.add_css_class("caption")
    label.add_css_class("dim-label")
    return label


def _sample_row(pr: PullRequest, description: str) -> Gtk.Widget:
    """One mark as the footer would draw it — mark, number — and what it means."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    mark = prmenu.status_mark(pr)
    mark.set_valign(Gtk.Align.CENTER)
    row.append(mark)
    number = Gtk.Label(label=f"#{pr.number}", xalign=0.0)
    number.add_css_class("caption")
    number.add_css_class("dim-label")
    row.append(number)
    row.append(Gtk.Label(label=description, xalign=0.0, hexpand=True, wrap=True))
    return row


def _action_row(action: practions.Action) -> Gtk.Widget:
    """One menu row, without the menu: the same mark and the same label.

    Label only — practions' tooltips name the pull request they were built
    for ("Merge episode6/collins#2 now"), and the ones behind these rows are
    made up.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    row.append(prmenu.action_icon(action.key))
    row.append(Gtk.Label(label=action.label, xalign=0.0, hexpand=True, wrap=True))
    return row


def _fix(missing: bool) -> Gtk.Widget:
    """How to get from here to there: a sentence, and for a signed-out gh the
    command that does it, click-to-copy."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.append(
        Gtk.Label(
            label=(
                _("Install it from cli.github.com — Collins picks it up the next time it starts.")
                if missing
                # No second login for Collins: the CLI's own credentials are
                # what every call it makes runs under, exactly as the agent
                # CLI's login is.
                else _("Run this once in any terminal. Collins asks for no login of its own.")
            ),
            xalign=0.0,
            wrap=True,
        )
    )
    if not missing:
        box.append(command_row(ghsetup.LOGIN_COMMAND))
    return box


def command_row(command: str) -> Gtk.Widget:
    """A command to run elsewhere, shown as one and copied by a click on it.

    Click-to-copy rather than a copy button beside it: the label is the thing
    being offered, it flashes its own confirmation (see enable_copy_on_click),
    and a command nobody wants to retype by hand is the whole reason it is
    here. Not selectable — a selectable label takes focus from the dialog's
    default response and would start a drag-select on the same press that is
    meant to copy.
    """
    label = Gtk.Label(label=command, xalign=0.0, hexpand=True)
    label.add_css_class("monospace")
    label.set_margin_top(8)
    label.set_margin_bottom(8)
    label.set_margin_start(12)
    label.set_margin_end(12)
    # The label's own text is the whole value, so it comes back from the
    # "Copied" flash unchanged rather than through a path formatter.
    enable_copy_on_click(label, lambda: command, format_text=lambda text: text)
    label.set_tooltip_text(_("Click to copy"))
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
    box.add_css_class("card")
    box.append(label)
    return box
