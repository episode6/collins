# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""The rows for the settings that spend the user's Claude quota, and the
switches for the built-in MCP tools — built once, shown wherever they are
needed.

Collins runs Claude on the user's behalf in a few places they never asked
for in so many words: naming new sessions, drawing project icons, renewing
an expired login, and offering every session a set of tools whose
definitions ride in its context. Preferences shows these settings in a
Token use group directly under General, and a first-launch dialog can show
the same rows so the disclosure comes before the first run. One builder
each, rather than a copy per dialog, so the two can't drift: the row that
says what a setting does is the row that writes it.

Every row writes its setting the moment it changes and calls *on_change*,
as the preferences dialog's rows do — a dialog closed with Escape after a
toggle loses nothing. Search keywords are the preferences dialog's own
business (its `_searchable`), applied on the rows this module returns, in
the order prefslayout.TOKEN_USE_ROWS promises them.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import claudemodels, formatting, mcptools, prefslayout, tokenrefresh  # noqa: E402
from .i18n import N_, _  # noqa: E402
from .state import AppState  # noqa: E402

# The Token use group: title, description, and the words its search index
# carries beyond them ("Token use" is the heading; what people type is any
# of these). Translated at use — `_(TITLE)` — like every N_ constant.
TITLE = N_("Token use")
DESCRIPTION = N_(
    "Each of these runs Claude on your behalf, against your subscription's "
    "usage limits, without a prompt from you. Every run is a headless "
    "claude -p from a scratch directory, carrying none of your skills, MCP "
    "servers, or the CLI's tools, so it never appears as a session and "
    "costs little more than its prompt."
)
SEARCH_TERMS = ("token", "tokens", "quota", "usage", "cost")

# The Built-in MCP tools group, which sits right under Token use: it spends
# no tokens by itself, but every enabled tool's definition is in each
# session's context, and two of the tools feed a session what it would
# otherwise have to be given.
MCP_TITLE = N_("Built-in MCP tools")
MCP_DESCRIPTION = N_(
    "Every enabled tool's definition rides in each session's context, "
    "read_terminal sends the panel's text into the conversation, and a "
    "session start_session starts is titled like any other. Turning one "
    "off takes effect immediately; sessions already running are only "
    "offered the tool again once they restart"
)

# User-facing names for the tools Collins offers the sessions it starts, by
# tool name (see mcptools.TOOLS). The table's own descriptions are written
# for the agent and left untranslated by design, so the switches get labels
# of their own: what the user gives up by turning one off, said in their
# language. The tool's real name goes in the subtitle — it is what shows up
# in the session's /mcp list and in its permission prompts.
_MCP_TOOL_LABELS = {
    "set_session_title": (
        N_("Name its own session"),
        N_("set_session_title — the session titles its own tab and sidebar row"),
    ),
    "open_in_editor": (
        N_("Open files in the editor"),
        N_("open_in_editor — put a file from the project on screen, at a line"),
    ),
    "show_diff": (
        N_("Show diffs in the git page"),
        N_(
            "show_diff — open the git page on a working-tree, branch or commit diff, at a file "
            "and line; offered to sessions only while hunk is installed"
        ),
    ),
    "show_image": (
        N_("Show images"),
        # The URL half is disclosure, not detail: it is the only tool that
        # sends Collins to an address the agent chose (attach_pr reaches the
        # network too, but only to ask gh about a GitHub PR).
        N_("show_image — a screenshot, plot, render, or image URL in the in-app lightbox"),
    ),
    "notify_user": (
        N_("Send notifications"),
        N_(
            "notify_user — a card in the window or a desktop notification, titled with "
            "the session; clicking it opens the tab"
        ),
    ),
    "attach_pr": (
        N_("Attach pull requests"),
        N_("attach_pr — put a pull request on the session's own footer and sidebar row"),
    ),
    "start_session": (
        N_("Start sessions in the background"),
        N_("start_session — spawn a sibling agent in a new background tab, with a prompt"),
    ),
    "read_terminal": (
        N_("Read the terminal panel"),
        # "everything you typed" is the disclosure: the dump carries the
        # user's own input, echoed passwords included, not just output.
        N_("read_terminal — the panel tabs' text and scrollback, your own typing included"),
    ),
    "run_in_terminal": (
        N_("Run commands in the terminal panel"),
        N_("run_in_terminal — type a command into an idle panel tab (or a new one) and run it"),
    ),
}


class ModelRows:
    """The two model pickers and the status row under them, kept in step
    with the model catalog.

    The catalog arrives in stages — the saved list at once, the query
    behind it a moment later, and a new one on every Refresh — and each
    landing repopulates both pickers around the saved choice. That
    "list landed later" plumbing is what preferences and any other dialog
    showing the rows share, so it lives here rather than in either.

    `rows` holds the pickers by setting key (title_model, icon_model);
    `status_row` is the Model list row with the Refresh button. The object
    stays alive as long as its rows do: the rows' signal handlers hold it.
    """

    # The automatic default's label, per picker: what "" resolves to.
    _DEFAULT_LABELS = {
        "title_model": N_("Default (latest Haiku)"),
        "icon_model": N_("Default (latest Sonnet)"),
    }

    def __init__(self, state: AppState, on_change: Callable[[], None]) -> None:
        self._state = state
        self._on_change = on_change
        self.rows: dict[str, Adw.ComboRow] = {}
        # Per key rather than bound into each row's handler: the list is
        # applied more than once (saved, queried, refreshed) and a handler
        # holding an older one would save the wrong id (see _apply_rows).
        self._ids: dict[str, list[str]] = {}
        self._handlers: dict[str, int] = {}
        for key, title, subtitle in (
            (
                "title_model",
                _("Session title model"),
                _(
                    "Names each new session from its first prompt — every session "
                    "Collins sees under ~/.claude/projects, including ones an agent "
                    "or a terminal started. None: sessions keep the first words of "
                    "their prompt, which costs nothing"
                ),
            ),
            (
                "icon_model",
                _("Icon generation model"),
                _(
                    "Model the sidebar's Generate Icon dialog starts with. None: "
                    "the dialog waits for you to pick a model and click Generate"
                ),
            ),
        ):
            self.rows[key] = Adw.ComboRow(title=title, subtitle=subtitle)
            _keep_value_readable(self.rows[key])
        # None and the default alone until the live list lands (see
        # _populate) — enough for the saved choice to show.
        self._apply_rows([])
        # The list is cached for a day and across restarts (claudemodels), so
        # a model released this morning would otherwise not be offered until
        # tomorrow. This row says how old the list is and asks for a new one.
        # It sits among the token spenders and is not one — a Models API
        # GET — which its subtitle says every time it changes (_set_status).
        self.status_row = Adw.ActionRow(title=_("Model list"))
        self._set_status(_("Checking…"))
        self.refresh_button = Gtk.Button(label=_("Refresh"), valign=Gtk.Align.CENTER)
        self.refresh_button.add_css_class("flat")
        self.refresh_button.set_tooltip_text(
            _("Ask Anthropic for the model list now, rather than waiting for the saved one to age out")
        )
        self.refresh_button.connect("clicked", self._on_refresh)
        self.status_row.add_suffix(self.refresh_button)
        self._populate()

    def _populate(self) -> None:
        """Fill the model pickers, off the main loop.

        The saved list (claudemodels keeps one across restarts) fills the rows
        at once when there is one, so the page opens on real models instead of
        a placeholder; the worker behind it re-queries only if that list has
        aged out, and the CLI's own aliases stand in when the API can't be
        asked and nothing was ever saved.
        """
        cached = claudemodels.cached_models()
        if cached:
            self._apply_rows(cached)
            self._apply_status(
                len(cached), claudemodels.cache_fetched_at(), claudemodels.cache_failed()
            )

        def work() -> None:
            models = claudemodels.available_models()
            GLib.idle_add(
                apply_models,
                models,
                claudemodels.cache_fetched_at(),
                claudemodels.cache_failed(),
                priority=GLib.PRIORITY_DEFAULT,
            )

        def apply_models(
            models: list[claudemodels.ClaudeModel], fetched_at: float, failed: bool
        ) -> bool:
            self._apply_rows(models or list(claudemodels.FALLBACK_MODELS))
            self._apply_status(len(models), fetched_at, failed)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name="prefs-models", daemon=True).start()

    def _apply_rows(self, models: list[claudemodels.ClaudeModel]) -> None:
        """Offer *models* in every picker, keeping each row's saved choice.

        Every picker lists None first (the feature runs nothing by itself),
        then the automatic default, then the catalog. Runs more than once —
        the saved list, the query behind it, and every Refresh — so each pass
        has to leave exactly one "notify::selected" handler per row, and to
        stop set_model/set_selected from firing it: an unblocked repopulate
        would write the settings back as though the user had just picked
        something.
        """
        for key, row in self.rows.items():
            current = (self._state.get_setting(key) or "").strip()
            ids = [claudemodels.NO_MODEL, ""] + [m.id for m in models]
            labels = [_("None"), _(self._DEFAULT_LABELS[key])] + [
                m.display_name for m in models
            ]
            if current and current not in ids:
                # A saved model the API no longer lists stays visible and
                # selected rather than silently snapping to the default.
                ids.append(current)
                labels.append(current)
            handler = self._handlers.get(key)
            if handler is not None:
                row.handler_block(handler)
            row.set_model(Gtk.StringList.new(labels))
            row.set_selected(ids.index(current))
            if handler is not None:
                row.handler_unblock(handler)
            self._ids[key] = ids
            if handler is None:
                self._handlers[key] = row.connect("notify::selected", self._on_row_changed, key)

    def _set_status(self, status: str) -> None:
        # Whatever the list's state, the row ends by saying it costs nothing:
        # among three rows that spend tokens, a fourth that didn't say would
        # read as a fourth spender.
        self.status_row.set_subtitle(_("{status} · free, no tokens").format(status=status))

    def _apply_status(self, count: int, fetched_at: float, failed: bool) -> None:
        """Date the list under the Refresh button, and say when it's a fallback.

        The row never just goes quiet: whenever the list on screen is the
        product of a query that didn't answer, it says so and keeps naming the
        list it fell back to and how old that is.

        *failed* is `claudemodels.cache_failed()` at both call sites, not a
        flag off whichever call got here. Opening the page onto a lapsed TTL
        with the network down is the same broken as pressing Refresh with the
        network down, and the row should read the same either way.
        """
        if fetched_at <= 0:
            self._set_status(
                _("Couldn't reach Anthropic — offering the CLI's aliases (opus, sonnet, haiku)")
            )
            return
        when = formatting.format_relative(
            datetime.fromtimestamp(fetched_at, timezone.utc).isoformat()
        )
        if failed:
            self._set_status(
                _("Couldn't reach Anthropic — still showing the list fetched {when}").format(when=when)
            )
        else:
            self._set_status(_("{count} models, updated {when}").format(count=count, when=when))

    def _on_refresh(self, _button: Gtk.Button) -> None:
        """Query the API outright, cache and backoff ignored."""
        self.refresh_button.set_sensitive(False)
        self._set_status(_("Checking…"))

        def work() -> None:
            models = claudemodels.refresh_models()
            GLib.idle_add(
                done,
                models,
                claudemodels.cache_fetched_at(),
                claudemodels.cache_failed(),
                priority=GLib.PRIORITY_DEFAULT,
            )

        def done(
            models: list[claudemodels.ClaudeModel], fetched_at: float, failed: bool
        ) -> bool:
            self._apply_rows(models or list(claudemodels.FALLBACK_MODELS))
            self._apply_status(len(models), fetched_at, failed)
            self.refresh_button.set_sensitive(True)
            return GLib.SOURCE_REMOVE

        threading.Thread(target=work, name="prefs-models-refresh", daemon=True).start()

    def _on_row_changed(self, row: Adw.ComboRow, _pspec, key: str) -> None:
        ids = self._ids.get(key) or [claudemodels.NO_MODEL, ""]
        selected = row.get_selected()
        if not 0 <= selected < len(ids):
            return  # mid-repopulate; the pass that set it will restore the choice
        self._state.set_setting(key, ids[selected])
        self._on_change()


# The least a picker's value gets, in pixels: room for "Default (latest
# Sonnet)", the longest of the labels the pickers show by default (see the
# helper below).
_VALUE_MIN_PX = 160


def _keep_value_readable(row: Adw.ComboRow) -> None:
    """Keep a picker's value ("Default (latest Haiku)") from ellipsizing
    beside its long subtitle.

    On a page tall enough to scroll, the row is allocated for a height that
    was settled first, and a wrapping subtitle's minimum width for that
    height is "wide enough to still fit in that many lines" — most of the
    row. What is left goes to the value, whose own minimum is an ellipsis,
    and the default reads "Def…". A minimum width on the value's widget
    (Adwaita's inline list view — not public API, so a row without one is
    left as it is) puts it ahead of the subtitle, which wraps to one more
    line instead; a label the catalog makes longer than the room still
    ellipsizes, as before.
    """
    child = row.get_first_child()
    stack = [child] if child is not None else []
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.ListView) and widget.has_css_class("inline"):
            widget.set_size_request(_VALUE_MIN_PX, -1)
            return
        sibling = widget.get_next_sibling()
        if sibling is not None:
            stack.append(sibling)
        first = widget.get_first_child()
        if first is not None:
            stack.append(first)


def build_token_rows(state: AppState, on_change: Callable[[], None]) -> list[Gtk.Widget]:
    """The Token use group's rows, in prefslayout.TOKEN_USE_ROWS order: the
    two model pickers, the login-renew switch, and the Model list status row.

    Each writes its setting as it changes and calls *on_change* after.
    """
    models = ModelRows(state, on_change)
    renew = Adw.SwitchRow(
        title=_("Auto-renew the Claude login"),
        subtitle=_(
            "When the login the usage panel and model list are fetched with has "
            "expired — at launch, or when a fetch is refused later — run one "
            "throwaway claude -p (a one-word prompt on Haiku) so the CLI renews "
            "it; off, the panel says to run claude yourself"
        ),
    )
    renew.set_active(bool(state.get_setting(tokenrefresh.SETTING)))

    def on_renew_changed(row: Adw.SwitchRow, _pspec) -> None:
        # Read fresh by tokenrefresh at each attempt, so this takes effect
        # from the next expired login; nothing running needs a nudge.
        state.set_setting(tokenrefresh.SETTING, row.get_active())
        on_change()

    renew.connect("notify::active", on_renew_changed)
    rows: dict[str, Gtk.Widget] = {
        "title_model": models.rows["title_model"],
        "icon_model": models.rows["icon_model"],
        "auto_renew_login": renew,
        "model_list": models.status_row,
    }
    return [rows[key] for key in prefslayout.TOKEN_USE_ROWS]


def build_mcp_rows(
    state: AppState, on_change: Callable[[], None]
) -> dict[str, Adw.SwitchRow]:
    """The on/off switch for each tool Collins offers a session (the
    `collins` MCP server in the session's /mcp list), by tool name.

    Driven by mcptools.TOOLS, in the order sessions are served them, so a
    tool added to the table can't ship without a switch — one with no
    entry in _MCP_TOOL_LABELS falls back to its own name rather than
    going unlisted. A dict in insertion order: iterate it to add the rows.
    """

    def on_tool_changed(row: Adw.SwitchRow, _pspec, name: str) -> None:
        state.set_setting(mcptools.tool_setting_key(name), row.get_active())
        on_change()

    rows: dict[str, Adw.SwitchRow] = {}
    for tool in mcptools.TOOLS:
        name = tool["name"]
        title, subtitle = _MCP_TOOL_LABELS.get(name, (name, ""))
        row = Adw.SwitchRow(title=_(title), subtitle=_(subtitle) if subtitle else "")
        row.set_active(bool(state.get_setting(mcptools.tool_setting_key(name))))
        row.connect("notify::active", on_tool_changed, name)
        rows[name] = row
    return rows
