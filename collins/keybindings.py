# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Every keyboard shortcut Collins owns, and what the user has rebound.

The catalogue below is the one list the window's shortcut controller, the
application accelerators, the editor's controller, the terminals' key
handlers, the Keyboard Bindings dialog and the tooltips that quote a chord
all read from. Each entry is an action name in GTK's own `prefix.name` form
and the accelerator strings that fire it by default, in GTK accelerator
syntax (`<Control><Shift>t`) — the one form `Gtk.ShortcutTrigger`,
`Gio.Application.set_accels_for_action` and `Gtk.accelerator_parse` all
agree on.

The user's overrides live in the "keybindings" setting as a plain mapping
of action name to list of accelerators; an empty list unbinds the action,
an absent key keeps the default. `resolve` folds the two together and is
what every reader consumes.

Deliberately GTK-free: CI has no GTK typelibs (see tests/conftest.py), and
everything here — the parse, the canonical spelling, the human label, the
conflict check — is string work. Turning an accelerator into a keyval is
the runtime's job (see keymap.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .i18n import N_

SETTING = "keybindings"

# Groups, in the order the dialog lists them. Labels here and in the
# catalogue are N_-marked, not translated: this module is imported before
# i18n.init() has picked a language, so the dialog runs _() over them when
# it draws.
GROUP_SESSIONS = "sessions"
GROUP_TABS = "tabs"
GROUP_PANELS = "panels"
GROUP_EDITOR = "editor"
GROUP_TERMINAL = "terminal"
GROUP_APP = "app"

GROUP_LABELS = {
    GROUP_SESSIONS: N_("Sessions"),
    GROUP_TABS: N_("Tabs and windows"),
    GROUP_PANELS: N_("Panels"),
    GROUP_EDITOR: N_("Editor"),
    GROUP_TERMINAL: N_("Terminal"),
    GROUP_APP: N_("Application"),
}


@dataclass(frozen=True)
class Binding:
    """One rebindable action: its GTK action name, what to call it, and the
    accelerator(s) it ships with."""

    action: str
    label: str
    defaults: tuple[str, ...]
    group: str
    # A second line for the dialog, only where the label can't carry the
    # condition the binding works under.
    note: str = ""


# Spelled as the code used to spell them — `<Control><Shift>t`, lower-case
# letter — so an untouched install produces byte-identical triggers.
BINDINGS: tuple[Binding, ...] = (
    Binding("win.new-session", N_("New session"), ("<Control><Shift>t",), GROUP_SESSIONS),
    Binding("win.quick-switch", N_("Quick switcher"), ("<Control>k",), GROUP_SESSIONS),
    Binding(
        "win.archive-current-session",
        N_("Archive the current session"),
        ("<Control><Shift>a",),
        GROUP_SESSIONS,
    ),
    Binding("win.undo-archive", N_("Undo the last archive"), ("<Control><Shift>z",), GROUP_SESSIONS),
    Binding("win.open-pr-page", N_("Open the pull request page"), ("F7",), GROUP_SESSIONS),
    Binding(
        "win.focus-search",
        N_("Search sessions"),
        (),
        GROUP_SESSIONS,
        N_("Unbound by default; the sidebar's search button does the same."),
    ),
    Binding("win.close-tab", N_("Close tab"), ("<Control>w",), GROUP_TABS),
    Binding("win.next-tab", N_("Next tab"), ("<Control>Page_Down",), GROUP_TABS),
    Binding("win.prev-tab", N_("Previous tab"), ("<Control>Page_Up",), GROUP_TABS),
    Binding("win.toggle-tab-emoji", N_("Toggle the tab marker"), ("<Control><Shift>e",), GROUP_TABS),
    Binding("win.toggle-sidebar", N_("Show/hide the sidebar"), ("F9",), GROUP_TABS),
    Binding("app.new-window", N_("New window"), ("<Control><Shift>n",), GROUP_TABS),
    Binding("win.toggle-panel", N_("Show/hide the terminal panel"), ("<Control>j",), GROUP_PANELS),
    Binding("win.clear-panel", N_("Clear the terminal panel"), ("<Control><Shift>k",), GROUP_PANELS),
    Binding(
        "win.rotate-panel-page",
        N_("Move the panel tab to the other side"),
        ("<Control>semicolon",),
        GROUP_PANELS,
    ),
    Binding("win.toggle-composer", N_("Show/hide the composer"), ("<Control>period",), GROUP_PANELS),
    Binding(
        "win.toggle-attachments",
        N_("Show/hide the attachments gallery"),
        ("<Control>apostrophe",),
        GROUP_PANELS,
    ),
    Binding(
        "win.swap-panel",
        N_("Swap the panel's sides"),
        (),
        GROUP_PANELS,
        N_("Unbound by default."),
    ),
    Binding(
        "win.move-panel-page",
        N_("Move the panel tab to the other strip"),
        (),
        GROUP_PANELS,
        N_("Unbound by default."),
    ),
    Binding("win.toggle-editor", N_("Show/hide the editor"), ("F8",), GROUP_EDITOR),
    Binding("win.quick-open", N_("Quick open a file"), ("<Control><Shift>o",), GROUP_EDITOR),
    Binding(
        "win.focus-editor",
        N_("Focus the editor"),
        (),
        GROUP_EDITOR,
        N_("Unbound by default."),
    ),
    Binding("editor.save", N_("Save the file"), ("<Control>s",), GROUP_EDITOR, N_("In the editor.")),
    Binding("editor.find", N_("Find in the file"), ("<Control>f",), GROUP_EDITOR, N_("In the editor.")),
    Binding(
        "terminal.copy",
        N_("Copy the selection"),
        ("<Control>c",),
        GROUP_TERMINAL,
        N_("With easy copy and paste on; without a selection the key reaches the terminal."),
    ),
    Binding(
        "terminal.paste",
        N_("Paste"),
        ("<Control>v",),
        GROUP_TERMINAL,
        N_("With easy copy and paste on."),
    ),
    Binding("terminal.copy-always", N_("Copy (terminal-style)"), ("<Control><Shift>c",), GROUP_TERMINAL),
    Binding("terminal.paste-always", N_("Paste (terminal-style)"), ("<Control><Shift>v",), GROUP_TERMINAL),
    Binding("terminal.find", N_("Find in the terminal"), ("<Control><Shift>g",), GROUP_TERMINAL),
    Binding(
        "terminal.newline",
        N_("Insert a newline in the prompt"),
        ("<Shift>Return",),
        GROUP_TERMINAL,
    ),
    Binding(
        "terminal.zoom-in",
        N_("Zoom in"),
        ("<Control>plus", "<Control>equal", "<Control>KP_Add"),
        GROUP_TERMINAL,
    ),
    Binding(
        "terminal.zoom-out",
        N_("Zoom out"),
        ("<Control>minus", "<Control>underscore", "<Control>KP_Subtract"),
        GROUP_TERMINAL,
    ),
    Binding("terminal.zoom-reset", N_("Reset zoom"), ("<Control>0", "<Control>KP_0"), GROUP_TERMINAL),
    Binding("win.preferences", N_("Preferences"), ("<Control>comma",), GROUP_APP),
    Binding("win.keyboard-bindings", N_("Keyboard bindings"), (), GROUP_APP, N_("Unbound by default.")),
    Binding("app.quit", N_("Quit"), ("<Control>q",), GROUP_APP),
)

BY_ACTION: dict[str, Binding] = {binding.action: binding for binding in BINDINGS}

# -- accelerator strings -------------------------------------------------------

# GTK's modifier names, in the order this codebase has always spelled them, with
# the aliases gtk_accelerator_parse also accepts folded onto them.
_MODIFIER_ORDER = ("Control", "Shift", "Alt", "Super", "Hyper", "Meta")
_MODIFIER_ALIASES = {
    "shift": "Shift", "shft": "Shift",
    "control": "Control", "ctrl": "Control", "ctl": "Control", "primary": "Control",
    "alt": "Alt", "mod1": "Alt",
    "super": "Super", "hyper": "Hyper", "meta": "Meta",
}
_TOKEN = re.compile(r"<([^<>]+)>")


class InvalidAccelerator(ValueError):
    """An accelerator string that is neither a key name nor modifiers + key."""


def parse(accelerator: str) -> tuple[tuple[str, ...], str]:
    """`"<Control><Shift>t"` → `(("Shift", "Control"), "t")`, modifiers in
    GTK's canonical order. Raises InvalidAccelerator for an empty key or an
    unknown modifier; the key name itself is not validated here (only GDK
    knows the keysym table), which is why the runtime parses again."""
    if not isinstance(accelerator, str):
        raise InvalidAccelerator(repr(accelerator))
    text = accelerator.strip()
    mods: set[str] = set()
    pos = 0
    for match in _TOKEN.finditer(text):
        if match.start() != pos:
            break
        name = _MODIFIER_ALIASES.get(match.group(1).lower())
        if name is None:
            raise InvalidAccelerator(accelerator)
        mods.add(name)
        pos = match.end()
    key = text[pos:]
    if not key or "<" in key or ">" in key or any(ch.isspace() for ch in key):
        raise InvalidAccelerator(accelerator)
    # A single letter is spelled lower-case whatever the shift state: the
    # runtime compares lower-cased keyvals, and `<Shift>T` and `<Shift>t`
    # must be the same binding.
    if len(key) == 1:
        key = key.lower()
    ordered = tuple(name for name in _MODIFIER_ORDER if name in mods)
    return ordered, key


def canonical(accelerator: str) -> str:
    """The one spelling of an accelerator, so two bindings can be compared
    as strings: modifiers in GTK order, a single letter lower-cased."""
    mods, key = parse(accelerator)
    return "".join(f"<{name}>" for name in mods) + key


_MODIFIER_LABELS = {
    "Shift": "Shift", "Control": "Ctrl", "Alt": "Alt",
    "Super": "Super", "Hyper": "Hyper", "Meta": "Meta",
}
_KEY_LABELS = {
    "comma": ",", "period": ".", "apostrophe": "'", "semicolon": ";",
    "slash": "/", "backslash": "\\", "grave": "`", "minus": "-", "plus": "+",
    "equal": "=", "underscore": "_", "bracketleft": "[", "bracketright": "]",
    "space": "Space", "Return": "Enter", "KP_Enter": "Keypad Enter",
    "Escape": "Esc", "BackSpace": "Backspace", "Delete": "Del", "Tab": "Tab",
    "Page_Up": "PgUp", "Page_Down": "PgDn", "Home": "Home", "End": "End",
    "Up": "↑", "Down": "↓", "Left": "←", "Right": "→",
    "KP_Add": "Keypad +", "KP_Subtract": "Keypad -", "KP_0": "Keypad 0",
}


def label_parts(accelerator: str) -> list[str]:
    """`"<Control><Shift>t"` → `["Ctrl", "Shift", "T"]`: the keycaps of a
    chord, modifiers in the order people say them (Ctrl before Shift), not
    GTK's."""
    mods, key = parse(accelerator)
    spoken = [name for name in ("Control", "Alt", "Shift", "Super", "Hyper", "Meta") if name in mods]
    if len(key) == 1:
        key_label = key.upper()
    else:
        key_label = _KEY_LABELS.get(key, key.replace("_", " "))
    return [_MODIFIER_LABELS[name] for name in spoken] + [key_label]


def label(accelerator: str) -> str:
    """`"<Control><Shift>t"` → `"Ctrl+Shift+T"`: how a chord is written in
    tooltips and docs."""
    return "+".join(label_parts(accelerator))


# -- the user's overrides ------------------------------------------------------


def sanitize(custom) -> dict[str, list[str]]:
    """The "keybindings" setting as read from disk, reduced to what this
    version knows how to honour: known actions only, accelerators that
    parse, each canonical, duplicates dropped. A hand-edited or older file
    never takes the whole map down with it."""
    if not isinstance(custom, dict):
        return {}
    clean: dict[str, list[str]] = {}
    for action, accelerators in custom.items():
        if action not in BY_ACTION or not isinstance(accelerators, (list, tuple)):
            continue
        kept: list[str] = []
        for accelerator in accelerators:
            try:
                spelled = canonical(accelerator)
            except InvalidAccelerator:
                continue
            if spelled not in kept:
                kept.append(spelled)
        clean[action] = kept
    return clean


def resolve(custom) -> dict[str, tuple[str, ...]]:
    """Every action → the accelerators that fire it, overrides applied.
    Defaults already canonical are left as written so an untouched install
    hands the controllers the exact strings it always did."""
    overrides = sanitize(custom)
    return {
        binding.action: tuple(overrides[binding.action])
        if binding.action in overrides
        else binding.defaults
        for binding in BINDINGS
    }


def is_customized(custom, action: str) -> bool:
    """Whether *action* carries an override that differs from its default
    (an override spelling out the default exactly doesn't count)."""
    overrides = sanitize(custom)
    if action not in overrides:
        return False
    return tuple(overrides[action]) != tuple(canonical(a) for a in BY_ACTION[action].defaults)


def with_binding(custom, action: str, accelerators: list[str]) -> dict[str, list[str]]:
    """A new override map with *action* bound to *accelerators* (empty =
    unbound). Restoring an action's default removes its entry rather than
    recording the default, so the setting stays a list of what changed."""
    overrides = sanitize(custom)
    canon = []
    for accelerator in accelerators:
        spelled = canonical(accelerator)
        if spelled not in canon:
            canon.append(spelled)
    default = [canonical(a) for a in BY_ACTION[action].defaults]
    if canon == default:
        overrides.pop(action, None)
    else:
        overrides[action] = canon
    return overrides


def holders(custom, accelerator: str, *, except_action: str | None = None) -> list[str]:
    """The actions *accelerator* already fires, other than *except_action*.
    Every scope is checked against every other: the window's controller
    runs in the capture phase, so a chord it claims never reaches the
    editor or a terminal, and the reverse overlap is just as confusing."""
    spelled = canonical(accelerator)
    return [
        action
        for action, accelerators in resolve(custom).items()
        if action != except_action and spelled in accelerators
    ]


def conflicts(custom) -> dict[str, list[str]]:
    """Accelerator → the actions sharing it, for every chord bound more
    than once."""
    seen: dict[str, list[str]] = {}
    for action, accelerators in resolve(custom).items():
        for accelerator in accelerators:
            seen.setdefault(canonical(accelerator), []).append(action)
    return {accelerator: actions for accelerator, actions in seen.items() if len(actions) > 1}


# -- for tooltips ----------------------------------------------------------------

# The overrides in force, as last told to `set_current`: the app sets them
# at launch and on every change so widgets built afterwards can quote the
# right chord without every constructor threading the settings through.
_current: dict[str, list[str]] = {}


def set_current(custom) -> None:
    global _current
    _current = sanitize(custom)


def current() -> dict[str, list[str]]:
    """The overrides in force, for a widget built between settings pushes."""
    return dict(_current)


def hint(action: str) -> str:
    """The first chord bound to *action* as people write it — `"Ctrl+J"` —
    or "" when it is unbound."""
    accelerators = resolve(_current).get(action, ())
    return label(accelerators[0]) if accelerators else ""


def with_hint(text: str, action: str) -> str:
    """*text* with the action's chord in brackets after it, when it has
    one: the shape every tooltip that names a shortcut uses."""
    chord = hint(action)
    return f"{text} ({chord})" if chord else text
