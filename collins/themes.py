# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-12. Full change history: git log for this file.
"""Built-in terminal color palettes for the VTE terminal."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gtk, Vte  # noqa: E402

# Each theme: foreground, background, and 16 ANSI colors (hex without '#').
# "Default" follows the system / app light-dark scheme (no custom colors).
_THEMES: dict[str, dict | None] = {
    "Default": None,
    "Tango Dark": {
        "fg": "d3d7cf", "bg": "2e3436",
        "palette": ["2e3436", "cc0000", "4e9a06", "c4a000", "3465a4", "75507b",
                    "06989a", "d3d7cf", "555753", "ef2929", "8ae234", "fce94f",
                    "729fcf", "ad7fa8", "34e2e2", "eeeeec"],
    },
    "Solarized Dark": {
        "fg": "839496", "bg": "002b36",
        "palette": ["073642", "dc322f", "859900", "b58900", "268bd2", "d33682",
                    "2aa198", "eee8d5", "002b36", "cb4b16", "586e75", "657b83",
                    "839496", "6c71c4", "93a1a1", "fdf6e3"],
    },
    "Solarized Light": {
        "fg": "657b83", "bg": "fdf6e3",
        "palette": ["073642", "dc322f", "859900", "b58900", "268bd2", "d33682",
                    "2aa198", "eee8d5", "002b36", "cb4b16", "586e75", "657b83",
                    "839496", "6c71c4", "93a1a1", "fdf6e3"],
    },
    "Dracula": {
        "fg": "f8f8f2", "bg": "282a36",
        "palette": ["21222c", "ff5555", "50fa7b", "f1fa8c", "bd93f9", "ff79c6",
                    "8be9fd", "f8f8f2", "6272a4", "ff6e6e", "69ff94", "ffffa5",
                    "d6acff", "ff92df", "a4ffff", "ffffff"],
    },
    "Gruvbox Dark": {
        "fg": "ebdbb2", "bg": "282828",
        "palette": ["282828", "cc241d", "98971a", "d79921", "458588", "b16286",
                    "689d6a", "a89984", "928374", "fb4934", "b8bb26", "fabd2f",
                    "83a598", "d3869b", "8ec07c", "ebdbb2"],
    },
    "Nord": {
        "fg": "d8dee9", "bg": "2e3440",
        "palette": ["3b4252", "bf616a", "a3be8c", "ebcb8b", "81a1c1", "b48ead",
                    "88c0d0", "e5e9f0", "4c566a", "bf616a", "a3be8c", "ebcb8b",
                    "81a1c1", "b48ead", "8fbcbb", "eceff4"],
    },
    "Catppuccin Mocha": {
        "fg": "cdd6f4", "bg": "1e1e2e",
        "palette": ["45475a", "f38ba8", "a6e3a1", "f9e2af", "89b4fa", "f5c2e7",
                    "94e2d5", "bac2de", "585b70", "f38ba8", "a6e3a1", "f9e2af",
                    "89b4fa", "f5c2e7", "94e2d5", "a6adc8"],
    },
    "Tokyo Night": {
        "fg": "c0caf5", "bg": "1a1b26",
        "palette": ["15161e", "f7768e", "9ece6a", "e0af68", "7aa2f7", "bb9af7",
                    "7dcfff", "a9b1d6", "414868", "f7768e", "9ece6a", "e0af68",
                    "7aa2f7", "bb9af7", "7dcfff", "c0caf5"],
    },
    "Monokai": {
        "fg": "f8f8f2", "bg": "272822",
        "palette": ["272822", "f92672", "a6e22e", "f4bf75", "66d9ef", "ae81ff",
                    "a1efe4", "f8f8f2", "75715e", "f92672", "a6e22e", "f4bf75",
                    "66d9ef", "ae81ff", "a1efe4", "f9f8f5"],
    },
    "One Dark": {
        "fg": "abb2bf", "bg": "282c34",
        "palette": ["282c34", "e06c75", "98c379", "e5c07b", "61afef", "c678dd",
                    "56b6c2", "abb2bf", "5c6370", "e06c75", "98c379", "e5c07b",
                    "61afef", "c678dd", "56b6c2", "ffffff"],
    },
    "Catppuccin Latte": {
        "fg": "4c4f69", "bg": "eff1f5",
        "palette": ["5c5f77", "d20f39", "40a02b", "df8e1d", "1e66f5", "ea76cb",
                    "179299", "acb0be", "6c6f85", "d20f39", "40a02b", "df8e1d",
                    "1e66f5", "ea76cb", "179299", "bcc0cc"],
    },
}

THEME_NAMES = list(_THEMES)
DEFAULT_THEME = "Default"


def get_theme(name: str | None) -> dict | None:
    """The palette dict for a theme name, or None for 'Default'/unknown."""
    return _THEMES.get(name or DEFAULT_THEME)


def terminal_foreground(name: str | None) -> tuple[int, int, int] | None:
    """The colour a named theme draws plain terminal text in, as 0-255 RGB.

    None for "Default" (and for a theme we don't know), where the terminal
    follows the system colours and VTE offers no getter for the one it settled
    on. Callers that can't work without it — telling a dimmed foreground from a
    colour of the agent's own, see vtehtml.is_dim_run — fall back rather than
    guess.
    """
    theme = _THEMES.get(name or DEFAULT_THEME)
    if not theme:
        return None
    fg = theme["fg"]
    return (int(fg[0:2], 16), int(fg[2:4], 16), int(fg[4:6], 16))


def _rgba(hex_str: str) -> Gdk.RGBA:
    color = Gdk.RGBA()
    color.parse(f"#{hex_str}")
    return color


def apply_terminal_theme(terminal: Vte.Terminal, name: str | None) -> None:
    theme = _THEMES.get(name or DEFAULT_THEME)
    if not theme:  # "Default" / unknown → follow the system colors
        terminal.set_default_colors()
    else:
        terminal.set_colors(
            _rgba(theme["fg"]),
            _rgba(theme["bg"]),
            [_rgba(c) for c in theme["palette"]],
        )
    _apply_dynamic_theme_css(theme)


# A few places need to track whatever `apply_terminal_theme` just set on the
# VTE widget itself, so they read as part of the terminal rather than a
# mismatched frame around it: the empty space beside a width-clamped
# terminal (.terminal-gutter, see TerminalTab's Adw.Clamp), the selected
# tab's row in the tab bar, which sits directly above the terminal it shows,
# the floating composer button in the terminal's corner (.attach-overlay),
# which inverts the terminal's colors so it contrasts with any palette, and
# the composer panel that button opens (.composer-panel), drawn as a surface
# of the terminal itself. One provider for the whole app: terminal_theme is
# a single global setting, not per-tab.
_dynamic_theme_provider: Gtk.CssProvider | None = None


def _apply_dynamic_theme_css(theme: dict | None) -> None:
    global _dynamic_theme_provider
    if _dynamic_theme_provider is None:
        _dynamic_theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            _dynamic_theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    # "Default" tracks the system light/dark scheme, so it has no fixed hex —
    # @window_bg_color is the same stand-in the chat-card overlay uses for it.
    bg_css = f"#{theme['bg']}" if theme else "@window_bg_color"
    fg_css = f"#{theme['fg']}" if theme else "@window_fg_color"
    _dynamic_theme_provider.load_from_data(
        f".terminal-gutter {{ background-color: {bg_css}; }}"
        f"tabbar tab:selected {{ background-color: {bg_css}; }}"
        # Semi-transparent terminal-fg pill with a terminal-bg icon: readable
        # over the terminal without fully hiding what's underneath, solidifying
        # on hover. Shape and placement are static (see app.py's _CSS).
        f".attach-overlay {{ background-color: alpha({fg_css}, 0.45); color: {bg_css}; }}"
        f".attach-overlay:hover {{ background-color: alpha({fg_css}, 0.8); }}"
        f".attach-overlay:active {{ background-color: {fg_css}; }}"
        # The composer panel is a surface of the terminal itself: terminal bg
        # at full strength (it covers the prompt it stands in for), fenced off
        # by a faint fg-colored top edge. Shape is static (app.py's _CSS).
        f".composer-panel {{ background-color: {bg_css}; color: {fg_css}; "
        f"border-top: 1px solid alpha({fg_css}, 0.3); }}"
        f".composer-panel textview, .composer-panel textview text {{ "
        f"background-color: {bg_css}; color: {fg_css}; caret-color: {fg_css}; }}".encode()
    )
