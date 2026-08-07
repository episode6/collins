"""Reading a run's colour back out of VTE's HTML export.

`Vte.Terminal.get_text_range_format(Vte.Format.HTML, ...)` is the only way to
see how the terminal *drew* something rather than what it says, and it has one
quirk worth isolating here: an attribute VTE stores as a flag rather than a
colour — dim (SGR 2) being the one that matters — is folded into a `<font
color>` only for the run the range *starts* on. Mid-range, dim comes back as
plain text. So the caller must start the range at the run it wants to inspect;
see TerminalTab.takes_prompt, which starts it at the cursor.

Which leaves the colour that comes back to be recognised as a dimming rather
than a colour of its own — and since VTE dims by scaling the foreground, that
is only answerable against the foreground the terminal is actually using (see
themes.terminal_foreground).
"""

from __future__ import annotations

import re

# What VTE writes for a range that is one solid colour: the whole thing inside a
# single <font>, with no other markup (bold and italic get their own tags, and
# nested tags mean more than one run).
_ONE_COLOUR_RUN = re.compile(r'\A<pre><font color="#([0-9A-Fa-f]{6})"[^>]*>([^<]*)</font></pre>\Z')

# VTE draws dim by scaling every channel of the foreground by the same factor —
# two thirds, measured on 0.84 (#FFFFFF → #AAAAAA, Tokyo Night's #C0CAF5 →
# #8087A3), and independent of the background. The range is wide enough to
# survive VTE picking a different factor, and the spread is what makes this a
# test of *dimming* rather than of darkness: the three channels have to be
# scaled together, which no colour an agent chose for itself will be (Claude's
# slash-command blue, #B1B9F9, sits at 0.92/0.92/1.02 of Tokyo Night's).
_DIM_FACTORS = (0.45, 0.85)
# Rounding to whole channels is all that separates the three factors of a real
# dimming: 0.009 across the shipped themes, at the worst (Catppuccin Latte's
# #4C4F69 → #323446), so this leaves an order of magnitude of room.
_DIM_FACTOR_SPREAD = 0.08

# The fallback when the foreground isn't known (the "Default" theme follows the
# system colours, and VTE has no getter for what it will draw with): how far
# apart red, green and blue may sit and still read as a neutral grey. Scaling
# keeps a neutral foreground neutral — VTE's own #C0C0C0 dims to #808080,
# Adwaita dark's #DEDDDA to #949391 — while a syntax colour stays as far from
# neutral as it started (#B1B9F9 spans 72).
_GREY_SPREAD = 24


def is_dim_run(html: str, foreground: tuple[int, int, int] | None = None) -> bool:
    """Whether *html* is one solid run of dimmed text and nothing else.

    The signature of an agent CLI's ghost text — the suggestion it prints into
    an empty input box for Tab to accept — as opposed to anything the user
    typed, which is drawn in the plain foreground (or, for a slash command,
    in a colour of its own that covers only part of the line).

    *foreground* is the colour the terminal draws plain text in, as 0-255 RGB,
    which turns "is this dim?" into a question with an answer: dim is that
    colour scaled down. Without it the test falls back to asking whether the
    run is a grey, which is what a dimmed *neutral* foreground is.
    """
    match = _ONE_COLOUR_RUN.match(html.strip())
    if match is None or not match.group(2).strip():
        return False
    rgb = tuple(bytes.fromhex(match.group(1)))
    if foreground is None:
        return max(rgb) - min(rgb) <= _GREY_SPREAD
    return _is_scaled_down(rgb, foreground)


def _is_scaled_down(rgb: tuple[int, ...], foreground: tuple[int, int, int]) -> bool:
    """Whether *rgb* is *foreground* with every channel scaled by one factor."""
    factors = []
    for drawn, plain in zip(rgb, foreground):
        if plain == 0:
            if drawn:  # nothing dims *out of* an empty channel
                return False
            continue  # 0 → 0 fixes no factor; let the other channels say
        factors.append(drawn / plain)
    if not factors:  # a black foreground dims to itself: unanswerable
        return False
    low, high = _DIM_FACTORS
    return (
        all(low <= factor <= high for factor in factors)
        and max(factors) - min(factors) <= _DIM_FACTOR_SPREAD
    )
