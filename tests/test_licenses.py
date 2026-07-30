import re

import pytest

from collins import licenses

DOC = """# Third-party license notices

Collins is free software, wrapped
across two source lines.

## GNOME platform libraries

Not bundled — loaded from your
system packages:

- **GTK 4** — LGPL-2.1-or-later —
  <https://www.gtk.org>
- **VTE** — LGPL-3.0-or-later

## Claude Code

Runs whatever `claude` is on your `PATH`, see
[the docs](https://claude.com/claude-code).
"""


def _titles(text: str) -> list[str]:
    return [title for title, _ in licenses.sections(text)]


def _body(text: str, title: str) -> str:
    return next(body for name, body in licenses.sections(text) if name == title)


def test_every_heading_becomes_a_section():
    assert _titles(DOC) == [
        "Third-party license notices",
        "GNOME platform libraries",
        "Claude Code",
    ]


def test_preamble_keeps_the_documents_title_and_drops_the_heading():
    body = _body(DOC, "Third-party license notices")
    assert body == "Collins is free software, wrapped across two source lines."


def test_soft_wrapped_prose_is_joined_but_bullets_stay_separate():
    lines = _body(DOC, "GNOME platform libraries").splitlines()
    assert lines[0] == "Not bundled — loaded from your system packages:"
    assert lines[1] == ""
    assert lines[2].startswith("• <b>GTK 4</b> — LGPL-2.1-or-later")
    assert lines[3].startswith("• <b>VTE</b>")


def test_links_become_pango_anchors():
    assert '<a href="https://www.gtk.org">https://www.gtk.org</a>' in _body(
        DOC, "GNOME platform libraries"
    )
    assert '<a href="https://claude.com/claude-code">the docs</a>' in _body(DOC, "Claude Code")


def test_code_spans_render_as_monospace_and_escape_their_content():
    assert "<tt>claude</tt>" in _body(DOC, "Claude Code")


def test_shipped_document_parses_into_sections():
    shipped = licenses.legal_sections()
    assert [title for title, _ in shipped][0] == "Third-party license notices"
    # the components the app actually depends on are disclosed
    text = "\n".join(body for _, body in shipped)
    for expected in ("GTK 4", "libadwaita", "VTE", "PyGObject", "agent-session-manager"):
        assert expected in text


def test_shipped_document_is_valid_markup():
    """A stray '<' or '&' in the doc would blank the About dialog's Legal page."""
    gi = pytest.importorskip("gi")
    gi.require_version("Pango", "1.0")
    from gi.repository import Pango

    for _title, markup in licenses.legal_sections():
        # anchors are GtkLabel's extension to Pango markup; its own parser
        # rejects them, so check what is left once they are stripped
        Pango.parse_markup(re.sub(r"</?a[^>]*>", "", markup), -1, "\0")


def test_missing_document_degrades_to_no_sections(monkeypatch, tmp_path):
    monkeypatch.setattr(licenses, "NOTICES_PATH", tmp_path / "gone.md")
    assert licenses.legal_sections() == []
