"""Telling an agent's ghost text from the user's own, by how VTE drew it.

Every string below came out of `Vte.Terminal.get_text_range_format(HTML, …)`
run against a live Claude Code 2.1.223 screen (or, for the themed cases, the
same escape sequences fed into a VTE carrying that theme's colours), read from
the cursor column — which is where TerminalTab.takes_prompt starts the range.

The dim colours are VTE 0.84's own arithmetic, measured rather than assumed:
every channel of the foreground scaled by two thirds.
"""

from collins.vtehtml import is_dim_run

# themes.py can't be imported here — it pulls in Gtk and Vte, and the test
# runner has no typelibs — so its numbers are copied rather than read:
TOKYO_NIGHT = (0xC0, 0xCA, 0xF5)  # themes._THEMES["Tokyo Night"]["fg"], the
# most coloured foreground the theme list ships
VTE_DEFAULT = (0xC0, 0xC0, 0xC0)  # what an unthemed terminal draws text in


def test_a_dim_suggestion_is_ghost_text():
    """What the CLI prints into an empty input for Tab to accept."""
    dim = '<pre><font color="#808080">Try &quot;fix the flaky test&quot;</font></pre>'
    assert is_dim_run(dim, VTE_DEFAULT)
    assert is_dim_run(dim)  # and without being told the foreground: a grey


def test_a_dimmed_coloured_foreground_is_still_ghost_text():
    """Tokyo Night's #C0CAF5 dims to #8087A3 — nowhere near a grey, and only
    recognisable as ghost text against the foreground it came from."""
    dim = '<pre><font color="#8087A3">close both PRs and delete the branches</font></pre>'
    assert is_dim_run(dim, TOKYO_NIGHT)
    # The fallback can't see it, which is why the foreground is passed at all.
    assert not is_dim_run(dim)


def test_plain_text_is_not_ghost_text():
    """Anything the user typed is drawn in the plain foreground, which VTE
    writes with no <font> at all."""
    assert not is_dim_run("<pre>close both PRs and delete the branches</pre>", TOKYO_NIGHT)
    assert not is_dim_run("<pre></pre>", TOKYO_NIGHT)
    assert not is_dim_run("", TOKYO_NIGHT)


def test_a_syntax_colour_is_not_ghost_text():
    """Claude colours a typed slash command #B1B9F9 — a colour it chose, which
    sits at 0.92/0.92/1.02 of Tokyo Night's foreground: darker in two channels,
    lighter in the third, and so not a dimming of anything."""
    slash = '<pre><font color="#B1B9F9">/review</font></pre>'
    assert not is_dim_run(slash, TOKYO_NIGHT)
    assert not is_dim_run(slash, VTE_DEFAULT)
    assert not is_dim_run(slash)


def test_a_darker_colour_scaled_unevenly_is_not_ghost_text():
    """Dim scales every channel by the same factor. A colour that is merely
    darker than the foreground isn't dim, however close it lands."""
    assert not is_dim_run('<pre><font color="#8087C0">a</font></pre>', TOKYO_NIGHT)


def test_a_line_only_partly_coloured_is_not_ghost_text():
    """Ghost text is the whole rest of the line. A coloured run with typed text
    around it is someone writing a prompt."""
    assert not is_dim_run(
        '<pre><font color="#B1B9F9">/review</font> the last commit</pre>', TOKYO_NIGHT
    )
    assert not is_dim_run('<pre>and then <font color="#808080">dim</font></pre>', VTE_DEFAULT)


def test_a_dim_run_of_only_whitespace_is_not_ghost_text():
    """Trailing dim padding says nothing about the box being empty; the caller
    already treats a blank tail as empty on its own."""
    assert not is_dim_run('<pre><font color="#808080">   </font></pre>', VTE_DEFAULT)


def test_a_black_foreground_dims_to_itself():
    """Nothing can be read off a foreground with no light in it, so nothing is
    claimed — the prompt actions grey out rather than guess."""
    assert not is_dim_run('<pre><font color="#000000">ghost</font></pre>', (0, 0, 0))
    # An empty channel stays empty when dimmed; a lit one there isn't a dimming.
    assert is_dim_run('<pre><font color="#00AA00">ghost</font></pre>', (0, 0xFF, 0))
    assert not is_dim_run('<pre><font color="#22AA00">ghost</font></pre>', (0, 0xFF, 0))
