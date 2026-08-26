# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The menu that switches a running session's model.

Two places open one — the footer's model chip and the composer chrome's
model button, each a MenuButton hosting this popover — and both list the
same catalog the Preferences pickers draw from: the Models API, asked with
the CLI's own OAuth token (claudemodels), with the CLI's version-agnostic
aliases standing in when the API can't be asked. The model the session last
answered with wears the mark, read live off the same transcript-fed
value the footer label shows. Picking one hands the id back to the host
(terminal.switch_model), which posts the provider's switch command to
the chat; the menu itself never touches a terminal.

The list arrives asynchronously: the menu pops at once — holding
whatever the cache already has, or a placeholder on a cold start — and a
worker thread swaps the live answer in underneath. A Gio.Menu is a live
model, so an open popover follows the change.

The new-chat screen's picker (new_launch_model_popover) is the same list
with different semantics: it chooses the ``--model`` a session not yet
started will launch with, so the mark follows the pick at once, and a
*Default* row heads the list — the CLI's own default, named when the
settings say what it is (claudemodels.cli_default_model).
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
    # transcript shows a reply from the new model, and the next show
    # re-reads it. A menu that marked the pick immediately would claim a
    # switch the session hasn't made yet.
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


def default_label(default_model: str | None) -> str:
    """What the launch picker calls the CLI's default: named after the model
    the settings resolve to, or bare when nothing sets one."""
    name = claudemodels.short_name(default_model or "")
    return _("Default ({model})").format(model=name) if name else _("Default")


def model_label(model_id: str) -> str:
    """What the launch picker calls a picked model: the catalog's display
    name when the id is listed, else the short name read off the id."""
    for model in claudemodels.cached_models() or claudemodels.FALLBACK_MODELS:
        if model.id == model_id:
            return model.display_name
    return claudemodels.short_name(model_id) or model_id


def new_launch_model_popover(
    default_model: Callable[[], str | None],
    choice: Callable[[], str],
    on_pick: Callable[[str], None],
) -> Gtk.PopoverMenu:
    """A popover choosing the ``--model`` a session not yet started launches
    with: a *Default* row (the CLI's own default, *default_model* naming it
    when known — re-read on every show, since ``/model`` in another session
    can move it) over the catalog. *choice* is the current pick, "" for the
    default; picking hands the id (or "") to *on_pick* and moves the mark
    at once — this menu chooses, it doesn't wait on a session to answer.
    Hand it to a MenuButton, for the same reason new_model_popover asks."""
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
        head = Gio.Menu()
        item = Gio.MenuItem.new(default_label(default_model()), None)
        item.set_action_and_target_value(f"{_GROUP}.pick", GLib.Variant.new_string(""))
        head.append_item(item)
        menu.append_section(None, head)
        _append_families(menu, models)

    def refresh(*_a) -> None:
        pick.set_state(GLib.Variant.new_string(choice()))
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
