"""Telling an agent's ghost text from the user's own, by how VTE drew it.

Every string below came out of `Vte.Terminal.get_text_range_format(HTML, …)`
run against a live Claude Code 2.1.223 screen (or, for the themed cases, the
same escape sequences fed straight into a VTE), read from the cursor column —
which is where TerminalTab.takes_prompt starts the range.
"""

from collins.vtehtml import is_dim_run


def test_a_dim_suggestion_is_ghost_text():
    """What the CLI prints into an empty input for Tab to accept: ESC[2m over
    the default foreground, which VTE halves to a neutral grey."""
    assert is_dim_run('<pre><font color="#808080">Try &quot;fix the flaky test&quot;</font></pre>')
    assert is_dim_run('<pre><font color="#808080">close both PRs and delete the branches</font></pre>')


def test_a_dimmed_themed_foreground_is_still_ghost_text():
    """Dim halves each channel, so a near-neutral foreground stays near-neutral:
    Adwaita dark's #DEDDDA becomes #6F6E6D, light's #2E3436 becomes #171A1B."""
    assert is_dim_run('<pre><font color="#6F6E6D">summarise what changed</font></pre>')
    assert is_dim_run('<pre><font color="#171A1B">summarise what changed</font></pre>')


def test_plain_text_is_not_ghost_text():
    """Anything the user typed is drawn in the plain foreground, which VTE
    writes with no <font> at all."""
    assert not is_dim_run("<pre>close both PRs and delete the branches</pre>")
    assert not is_dim_run("<pre></pre>")
    assert not is_dim_run("")


def test_a_syntax_colour_is_not_ghost_text():
    """Claude colours a typed slash command (#B1B9F9) — a colour it chose, not
    a dimming of the foreground, and nowhere near neutral."""
    assert not is_dim_run('<pre><font color="#B1B9F9">/review</font></pre>')


def test_a_line_only_partly_coloured_is_not_ghost_text():
    """Ghost text is the whole rest of the line. A coloured run with typed text
    around it is someone writing a prompt."""
    assert not is_dim_run('<pre><font color="#B1B9F9">/review</font> the last commit</pre>')
    assert not is_dim_run('<pre>and then <font color="#808080">dim</font></pre>')


def test_a_dim_run_of_only_whitespace_is_not_ghost_text():
    """Trailing dim padding says nothing about the box being empty; the caller
    already treats a blank tail as empty on its own."""
    assert not is_dim_run('<pre><font color="#808080">   </font></pre>')
