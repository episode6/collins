# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The git page's in-progress bar: a half-finished rebase, merge,
cherry-pick, revert or `git am` named over the working-tree diff, with
the two ways on — Continue and Abort.

`OperationBar` sits at the top of the git page's view column, above the
commit card, and is hidden for every load but a working-tree one
(unstaged, staged) and whenever `gitops.in_progress` finds nothing. The
page hands it what its read found (`show(operation, unmerged)`): the
heading names the operation (`gitmodel.operation_title`), the hint under
it counts the unmerged paths and names the commands the buttons run
(`gitmodel.operation_hint`). **Continue** runs `git <kind> --continue`
with no editor — the step's message taken as it is — and **Abort…**
asks first, since the resolutions made since the stop are lost. The bar
only emits; the page runs the commands behind the sidebar's busy and
toasts the outcome (`GitPage._on_continue_requested` /
`_on_abort_requested`), and `set_busy` greys the buttons while one runs.

Nothing shown here is repository content: the kind is one of
`gitops.OPERATION_KINDS`, the words are Collins' own.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk, Pango  # noqa: E402

from . import gitmodel  # noqa: E402
from .gitops import InProgress  # noqa: E402
from .i18n import _  # noqa: E402


class OperationBar(Gtk.Box):
    """See the module docstring. `show` / `clear` are the page's two
    verbs; `operation` says what is shown (None while hidden); `set_busy`
    greys the buttons while the page runs one of them."""

    __gtype_name__ = "CollinsGitOperationBar"

    __gsignals__ = {
        # Continue was pressed: the page runs `git <kind> --continue`.
        "continue-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # Abort… was pressed: the page asks, then runs `git <kind> --abort`.
        "abort-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("pr-card")
        self.add_css_class("git-operation-bar")
        self._operation: InProgress | None = None
        self._busy = False

        words = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        self._title = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        self._title.add_css_class("heading")
        words.append(self._title)
        self._hint = Gtk.Label(xalign=0.0, wrap=True)
        self._hint.add_css_class("dim-label")
        self._hint.add_css_class("caption")
        words.append(self._hint)
        self.append(words)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, valign=Gtk.Align.CENTER)
        self._abort_button = Gtk.Button(label=_("Abort…"))
        self._abort_button.connect("clicked", lambda *_a: self._pressed("abort-requested"))
        buttons.append(self._abort_button)
        self._continue_button = Gtk.Button(label=_("Continue"))
        self._continue_button.add_css_class("suggested-action")
        self._continue_button.connect("clicked", lambda *_a: self._pressed("continue-requested"))
        buttons.append(self._continue_button)
        self.append(buttons)
        self.set_visible(False)

    # -- the page's verbs ---------------------------------------------------------------

    @property
    def operation(self) -> InProgress | None:
        """What the bar names, None while it is hidden."""
        return self._operation

    def show(self, operation: InProgress, unmerged: int) -> None:
        """Name *operation* with *unmerged* paths standing in its way (the
        `U` rows of the read's status) and come up."""
        self._operation = operation
        self._title.set_text(gitmodel.operation_title(operation.kind))
        self._hint.set_text(gitmodel.operation_hint(operation.kind, unmerged))
        self._abort_button.set_tooltip_text(f"git {operation.kind} --abort")
        self._continue_button.set_tooltip_text(f"git {operation.kind} --continue")
        self._sync_buttons()
        self.set_visible(True)

    def clear(self) -> None:
        """Hide (nothing half-finished, or not a working-tree load)."""
        self._operation = None
        self.set_visible(False)

    def set_busy(self, busy: bool) -> None:
        """Grey the buttons while the page runs one of them."""
        self._busy = busy
        self._sync_buttons()

    # -- probes (the e2e) --------------------------------------------------------------

    def title_text(self) -> str:
        return self._title.get_text()

    def hint_text(self) -> str:
        return self._hint.get_text()

    def buttons_sensitive(self) -> bool:
        return self._continue_button.get_sensitive() and self._abort_button.get_sensitive()

    def click_continue(self) -> None:
        self._continue_button.emit("clicked")

    def click_abort(self) -> None:
        self._abort_button.emit("clicked")

    # -- internals ---------------------------------------------------------------------

    def _sync_buttons(self) -> None:
        sensitive = self._operation is not None and not self._busy
        self._continue_button.set_sensitive(sensitive)
        self._abort_button.set_sensitive(sensitive)

    def _pressed(self, signal: str) -> None:
        if self._operation is None or self._busy:
            return
        self.emit(signal)
