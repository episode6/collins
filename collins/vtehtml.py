"""Reading a run's colour back out of VTE's HTML export.

`Vte.Terminal.get_text_range_format(Vte.Format.HTML, ...)` is the only way to
see how the terminal *drew* something rather than what it says, and it has one
quirk worth isolating here: an attribute VTE stores as a flag rather than a
colour — dim (SGR 2) being the one that matters — is folded into a `<font
color>` only for the run the range *starts* on. Mid-range, dim comes back as
plain text. So the caller must start the range at the run it wants to inspect;
see TerminalTab.takes_prompt, which starts it at the cursor.
"""

from __future__ import annotations

import re

# What VTE writes for a range that is one solid colour: the whole thing inside a
# single <font>, with no other markup (bold and italic get their own tags, and
# nested tags mean more than one run).
_ONE_COLOUR_RUN = re.compile(r'\A<pre><font color="#([0-9A-Fa-f]{6})"[^>]*>([^<]*)</font></pre>\Z')

# How far apart the red, green and blue of a colour may sit and still read as a
# neutral grey. VTE draws dim text by halving each channel of the foreground, so
# a dimmed neutral foreground stays neutral — #DEDDDA (Adwaita dark's) halves to
# #6F6E6D, a spread of 2. A syntax colour the agent picked itself is nowhere
# near neutral (Claude's slash-command blue is #B1B9F9, a spread of 72).
_GREY_SPREAD = 24


def is_dim_run(html: str) -> bool:
    """Whether *html* is one solid run of dimmed text and nothing else.

    The signature of an agent CLI's ghost text — the suggestion it prints into
    an empty input box for Tab to accept — as opposed to anything the user
    typed, which is drawn in the plain foreground (or, for a slash command,
    in a colour of its own that covers only part of the line).
    """
    match = _ONE_COLOUR_RUN.match(html.strip())
    if match is None or not match.group(2).strip():
        return False
    rgb = bytes.fromhex(match.group(1))
    return max(rgb) - min(rgb) <= _GREY_SPREAD
