# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The menus that switch a running session's model, and its effort.

Two places open the model menu — the footer's model chip and the composer
chrome's model button, each a MenuButton hosting this popover — and both
list the same catalog the Preferences pickers draw from: the Models API,
asked with the CLI's own OAuth token (claudemodels), with the CLI's
version-agnostic aliases standing in when the API can't be asked. The
model the session last answered with wears the mark, read live off the
same transcript-fed value the footer label shows. Picking one hands the id
back to the host (terminal.switch_model), which posts the provider's
switch command to the chat; the menu itself never touches a terminal.

The list arrives asynchronously: the menu pops at once — holding
whatever the cache already has, or a placeholder on a cold start — and a
worker thread swaps the live answer in underneath. A Gio.Menu is a live
model, so an open popover follows the change.

The effort menu (new_effort_popover) sits beside it in both places and
lists the CLI's effort levels (claudemodels.EFFORT_LEVELS) the same way:
the level the session last answered at wears the mark, and a pick posts
``/effort`` (terminal.switch_effort). Levels the session's current model
can't take — as the catalog's capabilities say — draw insensitive, so the
menu never offers a ``/effort xhigh`` the CLI would refuse.

The new-chat screen's pickers (new_launch_model_popover,
new_launch_effort_popover) are the same lists with different semantics:
they choose the ``--model`` and ``--effort`` a session not yet started
will launch with, so the mark follows the pick at once — and until there
is a pick it sits on the CLI's own default, when the settings say what
that is (claudemodels.cli_default_model, cli_default_effort): the list is
the catalog alone, with the default pre-selected on it rather than given
a row of its own.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import claudemodels  # noqa: E402
from .i18n import _  # noqa: E402

_GROUP = "modelmenu"


def new_model_popover(
    current_model: Callable[[], str | None],
    on_pick: Callable[[str], None],
) -> Gtk.PopoverMenu:
    """A popover listing the models *on_pick* can switch to, refilled on
    every show so the mark tracks *current_model* and a stale catalog
    refreshes itself. Hand it to a MenuButton, which owns its popover for
    the widget's life and — this matters — re-measures it as the catalog
    fills in under the open menu; a hand-parented popover keeps the size it
    had over the still-empty menu it popped up on."""
    menu = Gio.Menu()
    popover = Gtk.PopoverMenu.new_from_model(menu)

    # One stateful string action carries the whole list: each model is an
    # item targeting its id, and the action's state — the current model —
    # is what draws the mark on the matching row. Picking doesn't move the
    # state; the label this menu marks itself by only changes when the
    # transcript shows the CLI confirming the switch (transcript
    # _record_switch), and the next show re-reads it. A menu that marked
    # the pick immediately would claim a switch the session hasn't made.
    pick = Gio.SimpleAction.new_stateful(
        "pick", GLib.VariantType.new("s"), GLib.Variant.new_string("")
    )
    pick.connect("activate", lambda _action, param: on_pick(param.get_string()))
    copy = Gio.SimpleAction.new("copy-id", None)

    def on_copy(_action, _param) -> None:
        # The copy the label's own click used to be (see the footer): the
        # full id, not the short name it shows.
        model = current_model()
        if model:
            popover.get_clipboard().set(model)

    copy.connect("activate", on_copy)
    group = Gio.SimpleActionGroup()
    group.add_action(pick)
    group.add_action(copy)
    popover.insert_action_group(_GROUP, group)
    popover.connect("show", lambda *_a: _refresh(menu, pick, copy, current_model))
    return popover


def _refresh(
    menu: Gio.Menu,
    pick: Gio.SimpleAction,
    copy: Gio.SimpleAction,
    current_model: Callable[[], str | None],
) -> None:
    """Fill *menu* for this show: the cached catalog now, the live one when
    a worker thread has it. The thread runs every show — a fresh cache
    makes it a cheap no-op inside claudemodels — so a menu first filled
    from the aliases heals to the real list without waiting out the TTL."""
    current = current_model() or ""
    pick.set_state(GLib.Variant.new_string(current))
    copy.set_enabled(bool(current))
    cached = claudemodels.cached_models()
    _fill(menu, cached, bool(current))

    def work() -> None:
        live = claudemodels.available_models() or list(claudemodels.FALLBACK_MODELS)
        GLib.idle_add(apply_models, live)

    def apply_models(live: list[claudemodels.ClaudeModel]) -> bool:
        if live != cached:
            _fill(menu, live, bool(current))
        return GLib.SOURCE_REMOVE

    threading.Thread(target=work, name="model-menu", daemon=True).start()


def _fill(
    menu: Gio.Menu,
    models: list[claudemodels.ClaudeModel] | None,
    have_current: bool,
) -> None:
    menu.remove_all()
    _append_families(menu, models)
    if have_current:
        extra = Gio.Menu()
        extra.append(_("Copy model id"), f"{_GROUP}.copy-id")
        menu.append_section(None, extra)


def _append_families(menu: Gio.Menu, models: list[claudemodels.ClaudeModel] | None) -> None:
    if not models:
        # An actionless item draws insensitive: a placeholder the worker
        # thread replaces, not a choice.
        section = Gio.Menu()
        section.append(_("Loading models…"), None)
        menu.append_section(None, section)
    # One section per tier family, so the PopoverMenu draws a divider between
    # families (Fable | Opus | Sonnet | Haiku) without any label on the rule.
    for family in claudemodels.grouped_models(models or []):
        section = Gio.Menu()
        for model in family:
            item = Gio.MenuItem.new(model.display_name, None)
            item.set_action_and_target_value(
                f"{_GROUP}.pick", GLib.Variant.new_string(model.id)
            )
            section.append_item(item)
        menu.append_section(None, section)


# -- the new-chat screen's launch picker ---------------------------------------


def model_label(model_id: str) -> str:
    """What the launch picker calls a model — a pick, or the CLI's default
    as its settings write it: the display name of the catalog row the id
    resolves to (claudemodels.catalog_id, so an ``opus`` alias reads as the
    Opus it stands for, the row the picker marks), else the short name read
    off the id."""
    catalog = list(claudemodels.cached_models() or claudemodels.FALLBACK_MODELS)
    listed = claudemodels.catalog_id(model_id, catalog)
    for model in catalog:
        if model.id == listed:
            return model.display_name
    return claudemodels.short_name(model_id) or model_id


def _launch_mark(
    choice: str, default_model: str | None, models: list[claudemodels.ClaudeModel] | None
) -> str:
    """The row the launch picker marks: the pick, else the row the CLI's
    default names in *models* (claudemodels.catalog_id), else none."""
    if choice:
        return choice
    return claudemodels.catalog_id(default_model, models or []) or ""


def new_launch_model_popover(
    default_model: Callable[[], str | None],
    choice: Callable[[], str],
    on_pick: Callable[[str], None],
) -> Gtk.PopoverMenu:
    """A popover choosing the ``--model`` a session not yet started launches
    with: the catalog, marked at *choice* — the current pick, "" for none —
    or, with nothing picked, at the row the CLI's own default names
    (*default_model*, as the settings write it; re-read on every show, since
    ``/model`` in another session can move it). No row wears the mark when
    the settings name no default, or one the catalog doesn't list. Picking
    hands the id to *on_pick* and moves the mark at once — this menu
    chooses, it doesn't wait on a session to answer. Hand it to a
    MenuButton, for the same reason new_model_popover asks."""
    menu = Gio.Menu()
    popover = Gtk.PopoverMenu.new_from_model(menu)
    pick = Gio.SimpleAction.new_stateful(
        "pick", GLib.VariantType.new("s"), GLib.Variant.new_string("")
    )

    def on_activate(_action, param) -> None:
        pick.set_state(param)
        on_pick(param.get_string())

    pick.connect("activate", on_activate)
    group = Gio.SimpleActionGroup()
    group.add_action(pick)
    popover.insert_action_group(_GROUP, group)

    def fill(models: list[claudemodels.ClaudeModel] | None) -> None:
        menu.remove_all()
        _append_families(menu, models)
        # The mark is resolved against the list it sits on: an alias in
        # the settings names a different row on the live catalog than on
        # the aliases the menu opened with.
        pick.set_state(GLib.Variant.new_string(_launch_mark(choice(), default_model(), models)))

    def refresh(*_a) -> None:
        cached = claudemodels.cached_models()
        fill(cached)

        def work() -> None:
            live = claudemodels.available_models() or list(claudemodels.FALLBACK_MODELS)
            GLib.idle_add(apply_models, live)

        def apply_models(live: list[claudemodels.ClaudeModel]) -> bool:
            if live != cached:
                fill(live)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name="launch-model-menu", daemon=True).start()

    popover.connect("show", refresh)
    return popover


# -- the effort menus ---------------------------------------------------------


def effort_label(level: str) -> str:
    """What the menus call an effort level: a word for each of the CLI's
    own, and the CLI's spelling for one this build doesn't know."""
    names = {
        "low": _("Low"),
        "medium": _("Medium"),
        "high": _("High"),
        "xhigh": _("Extra high"),
        "max": _("Max"),
    }
    return names.get(level, level)


def _append_efforts(menu: Gio.Menu, allowed: tuple[str, ...] | None) -> None:
    """Every level, one section. *allowed* is what the model in question
    takes (claudemodels.model_efforts): a level outside it gets no action,
    which draws insensitive — a choice the CLI would refuse is shown for
    what it is rather than hidden; None allows them all (an alias, or a
    model the catalog doesn't list). A model that takes none says so."""
    section = Gio.Menu()
    if allowed is not None and not allowed:
        section.append(_("This model has no effort setting"), None)
        menu.append_section(None, section)
        return
    for level in claudemodels.EFFORT_LEVELS:
        item = Gio.MenuItem.new(effort_label(level), None)
        if allowed is None or level in allowed:
            item.set_action_and_target_value(
                f"{_GROUP}.pick-effort", GLib.Variant.new_string(level)
            )
        section.append_item(item)
    menu.append_section(None, section)


def new_effort_popover(
    current_effort: Callable[[], str | None],
    current_model: Callable[[], str | None],
    on_pick: Callable[[str], None],
) -> Gtk.PopoverMenu:
    """A popover listing the effort levels *on_pick* can switch a running
    session to, refilled on every show so the mark tracks *current_effort*
    (the level the session last answered at — the pick itself never moves
    it, for the reason new_model_popover gives) and the levels on offer
    track *current_model*. Hand it to a MenuButton, as the model menu asks."""
    menu = Gio.Menu()
    popover = Gtk.PopoverMenu.new_from_model(menu)
    pick = Gio.SimpleAction.new_stateful(
        "pick-effort", GLib.VariantType.new("s"), GLib.Variant.new_string("")
    )
    pick.connect("activate", lambda _action, param: on_pick(param.get_string()))
    group = Gio.SimpleActionGroup()
    group.add_action(pick)
    popover.insert_action_group(_GROUP, group)

    def refresh(*_a) -> None:
        pick.set_state(GLib.Variant.new_string(current_effort() or ""))
        menu.remove_all()
        _append_efforts(menu, claudemodels.model_efforts(current_model() or ""))

    popover.connect("show", refresh)
    return popover


def new_launch_effort_popover(
    default_effort: Callable[[], str | None],
    choice: Callable[[], str],
    launch_model: Callable[[], str | None],
    on_pick: Callable[[str], None],
) -> Gtk.PopoverMenu:
    """A popover choosing the ``--effort`` a session not yet started launches
    with: the levels *launch_model* — the ``--model`` the launch will pass,
    or the CLI's default when it passes none — takes, marked at *choice*
    (the current pick, "" for none) or, with nothing picked, at the CLI's
    own default level (*default_effort*, as the settings write it; re-read
    on every show, since ``/effort`` in another session can move it). No
    row wears the mark when the settings name no default, or one the CLI
    wouldn't take. Picking hands the level to *on_pick* and moves the mark
    at once, as new_launch_model_popover does. Hand it to a MenuButton,
    for the same reason."""
    menu = Gio.Menu()
    popover = Gtk.PopoverMenu.new_from_model(menu)
    pick = Gio.SimpleAction.new_stateful(
        "pick-effort", GLib.VariantType.new("s"), GLib.Variant.new_string("")
    )

    def on_activate(_action, param) -> None:
        pick.set_state(param)
        on_pick(param.get_string())

    pick.connect("activate", on_activate)
    group = Gio.SimpleActionGroup()
    group.add_action(pick)
    popover.insert_action_group(_GROUP, group)

    def refresh(*_a) -> None:
        default = default_effort() or ""
        if default not in claudemodels.EFFORT_LEVELS:
            default = ""  # a name the CLI wouldn't take marks nothing
        pick.set_state(GLib.Variant.new_string(choice() or default))
        menu.remove_all()
        _append_efforts(menu, claudemodels.model_efforts(launch_model() or ""))

    popover.connect("show", refresh)
    return popover
