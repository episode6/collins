# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for diffnotes: the text bounds, the address resolution, and the
MarkStore — batches landing whole or not at all, edits, clears, and what
survives a reload (the hunk's stable key)."""

from __future__ import annotations

import pytest

from collins import diffmodel, diffnotes
from collins.diffnotes import HighlightSpec, MarkStore, NoteSpec, resolve_anchor

MODIFIED = """\
diff --git a/src/app.py b/src/app.py
index 3b18e51..a1b2c3d 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,4 +1,5 @@ def main():
 import os
-import sys
+import sys, re
+import json

 print(os.name)
@@ -20,3 +21,3 @@ class App:
     def run(self):
-        return 1
+        return 2
     # end
"""

OTHER = """\
diff --git a/README.md b/README.md
index 3b18e51..a1b2c3d 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-old title
+new title
 body
"""

# The second hunk of app.py edited (its key moves; the first hunk's stays).
MODIFIED_HUNK_2_CHANGED = MODIFIED.replace("+        return 2", "+        return 3")
# The first hunk grown by a line: every later line number shifts, so the
# second hunk's key moves too (its new_start is part of it).
MODIFIED_HUNK_1_GROWN = (
    MODIFIED.replace("+import json\n", "+import json\n+import re\n")
    .replace("@@ -1,4 +1,5 @@", "@@ -1,4 +1,6 @@")
    .replace("@@ -20,3 +21,3 @@", "@@ -20,3 +22,3 @@")
)


def files(*texts: str) -> list[diffmodel.File]:
    out: list[diffmodel.File] = []
    for text in texts:
        out.extend(diffmodel.parse(text))
    return out


# -- text -----------------------------------------------------------------------


def test_bound_text_folds_newlines_drops_controls_and_caps():
    assert diffnotes.bound_text("  a\r\nb\rc\x00d\te  ") == "a\nb\ncd\te"
    assert diffnotes.bound_text(None) == ""
    assert diffnotes.bound_text(12) == ""
    long = "x" * (diffnotes.NOTE_MAX_CHARS + 50)
    assert len(diffnotes.bound_text(long)) == diffnotes.NOTE_MAX_CHARS


def test_split_and_join_note_text_round_trip():
    assert diffnotes.split_note_text("Summary\n\nwhy it matters\nmore") == ("Summary", "why it matters\nmore")
    assert diffnotes.split_note_text("just a line") == ("just a line", None)
    assert diffnotes.split_note_text("   \n  ") == ("", None)
    assert diffnotes.join_note_text("s", "r") == "s\nr"
    assert diffnotes.join_note_text("s", None) == "s"
    summary, rationale = diffnotes.split_note_text(diffnotes.join_note_text("s", "r\nr2"))
    assert (summary, rationale) == ("s", "r\nr2")


# -- addresses ------------------------------------------------------------------


def test_resolve_anchor_by_line_on_either_side():
    loaded = files(MODIFIED)
    anchor = resolve_anchor(loaded, "src/app.py", line=3)
    assert not isinstance(anchor, str)
    assert (anchor.hunk_index, anchor.line_index, anchor.side, anchor.line) == (0, 3, diffmodel.NEW, 3)
    assert anchor.hunk.lines[anchor.line_index].text == "import json"
    old = resolve_anchor(loaded, "src/app.py", side="old", line=21)
    assert not isinstance(old, str)
    assert (old.hunk_index, old.line_index) == (1, 1)
    assert old.hunk.lines[old.line_index].text == "        return 1"
    assert old.key == diffmodel.stable_key(loaded[0], loaded[0].hunks[1])


def test_resolve_anchor_by_hunk_takes_its_first_line_on_the_side():
    loaded = files(MODIFIED)
    anchor = resolve_anchor(loaded, "src/app.py", hunk=2)
    assert not isinstance(anchor, str)
    assert (anchor.hunk_index, anchor.line_index, anchor.side, anchor.line) == (1, 0, diffmodel.NEW, 21)
    old = resolve_anchor(loaded, "src/app.py", side="old", hunk=1)
    assert not isinstance(old, str)
    assert (old.line_index, old.line) == (0, 1)


@pytest.mark.parametrize(
    "kwargs, words",
    [
        ({"path": "", "line": 1}, "needs a file path"),
        ({"path": "src/app.py", "side": "left", "line": 1}, "side must be"),
        ({"path": "src/app.py"}, "exactly one of line and hunk"),
        ({"path": "src/app.py", "line": 1, "hunk": 1}, "exactly one of line and hunk"),
        ({"path": "nope.py", "line": 1}, "nope.py is not in the loaded diff"),
        ({"path": "src/app.py", "hunk": 3}, "has no hunk 3"),
        ({"path": "src/app.py", "hunk": 0}, "has no hunk 0"),
        ({"path": "src/app.py", "hunk": True}, "has no hunk True"),
        ({"path": "src/app.py", "line": 10}, "line 10 (new) of src/app.py is not in a hunk"),
        ({"path": "src/app.py", "line": "3"}, "is not in a hunk"),
    ],
)
def test_resolve_anchor_refuses_with_a_reason(kwargs, words):
    reason = resolve_anchor(files(MODIFIED), **kwargs)
    assert isinstance(reason, str) and words in reason, reason


def test_resolve_anchor_by_hunk_refuses_a_side_with_no_lines():
    loaded = files(
        "diff --git a/n.txt b/n.txt\nnew file mode 100644\n--- /dev/null\n+++ b/n.txt\n"
        "@@ -0,0 +1,2 @@\n+a\n+b\n"
    )
    reason = resolve_anchor(loaded, "n.txt", side="old", hunk=1)
    assert reason == "hunk 1 of n.txt has no lines on the old side"


# -- notes ----------------------------------------------------------------------


def test_add_notes_lands_a_batch_with_minted_ids_and_bounded_words():
    store = MarkStore()
    loaded = files(MODIFIED, OTHER)
    added = store.add_notes(
        loaded,
        [
            NoteSpec("src/app.py", " Rename this ", rationale="  it shadows re\n", author="claude", line=3),
            NoteSpec("README.md", "Title", hunk=1, side="old"),
        ],
        diffnotes.AGENT,
    )
    assert not isinstance(added, str)
    assert [n.id for n in added] == ["n1", "n2"]
    first, second = added
    assert (
        first.path,
        first.side,
        first.line,
        first.summary,
        first.rationale,
        first.author,
        first.source,
    ) == (
        "src/app.py",
        "new",
        3,
        "Rename this",
        "it shadows re",
        "claude",
        "agent",
    )
    assert (second.path, second.side, second.line, second.rationale, second.author) == (
        "README.md",
        "old",
        1,
        None,
        None,
    )
    assert store.notes() == added
    assert store.notes("README.md") == [second]
    assert store.note("n1") == first and store.note(None) is None
    assert len(store) == 2


def test_add_notes_refuses_the_whole_batch_on_one_bad_address():
    store = MarkStore()
    loaded = files(MODIFIED)
    reason = store.add_notes(
        loaded,
        [NoteSpec("src/app.py", "fine", line=3), NoteSpec("src/app.py", "not in a hunk", line=12)],
        diffnotes.USER,
    )
    assert reason == "line 12 (new) of src/app.py is not in a hunk of the loaded diff"
    assert store.notes() == []
    assert store.add_notes(loaded, [NoteSpec("src/app.py", "   ", line=3)], diffnotes.USER) == (
        "src/app.py: a note needs a summary"
    )
    assert store.add_notes(loaded, [], diffnotes.USER) == "no notes given"
    assert (
        store.add_notes(loaded, [NoteSpec("src/app.py", "x", line=3)], "bot") == "unknown note source 'bot'"
    )
    many = [NoteSpec("src/app.py", "x", line=3)] * (diffnotes.MAX_NOTES_PER_BATCH + 1)
    assert "at most" in store.add_notes(loaded, many, diffnotes.USER)


def test_notes_are_capped_per_page():
    store = MarkStore()
    loaded = files(MODIFIED)
    spec = NoteSpec("src/app.py", "x", line=3)
    while len(store.notes()) < diffnotes.MAX_NOTES:
        batch = [spec] * min(diffnotes.MAX_NOTES_PER_BATCH, diffnotes.MAX_NOTES - len(store.notes()))
        assert not isinstance(store.add_notes(loaded, batch, diffnotes.AGENT), str)
    assert "at most" in store.add_notes(loaded, [spec], diffnotes.AGENT)


def test_edit_and_remove_a_note():
    store = MarkStore()
    loaded = files(MODIFIED)
    added = store.add_notes(loaded, [NoteSpec("src/app.py", "first", line=3)], diffnotes.USER)
    note = added[0]
    edited = store.edit(note.id, "second\n", "because")
    assert edited is not None and (edited.summary, edited.rationale) == ("second", "because")
    assert store.note(note.id) == edited
    assert edited.id == note.id and edited.hunk_key == note.hunk_key
    assert store.edit(note.id, "  ") is None  # a note needs a summary
    assert store.edit("n99", "x") is None
    assert store.remove(note.id) and not store.remove(note.id) and not store.remove(None)
    assert store.notes() == []


def test_clear_spares_user_notes_unless_asked():
    store = MarkStore()
    loaded = files(MODIFIED, OTHER)
    store.add_notes(loaded, [NoteSpec("src/app.py", "agent", line=3)], diffnotes.AGENT)
    store.add_notes(loaded, [NoteSpec("src/app.py", "mine", line=2)], diffnotes.USER)
    store.add_notes(loaded, [NoteSpec("README.md", "agent too", line=1)], diffnotes.AGENT)
    store.add_highlights(loaded, [HighlightSpec("README.md", 1, 0, 3)])
    assert store.clear(path="src/app.py", notes=True) == 1
    assert [n.summary for n in store.notes()] == ["mine", "agent too"]
    assert store.clear(notes=True) == 1
    assert [n.summary for n in store.notes()] == ["mine"]
    assert store.clear(notes=True, include_user=True) == 1
    assert store.notes() == [] and len(store.highlights()) == 1
    assert store.clear(highlights=True) == 1
    assert len(store) == 0
    assert store.clear() == 0


# -- highlights -------------------------------------------------------------------


def test_add_highlights_checks_the_range_against_the_line():
    store = MarkStore()
    loaded = files(MODIFIED)
    added = store.add_highlights(
        loaded,
        [
            HighlightSpec("src/app.py", 3, 7, 11),
            HighlightSpec("src/app.py", 21, 0, 4, side="old", tone="error"),
        ],
    )
    assert not isinstance(added, str)
    assert [(h.id, h.line, h.start, h.end, h.tone, h.side) for h in added] == [
        ("h1", 3, 7, 11, "match", "new"),
        ("h2", 21, 0, 4, "error", "old"),
    ]
    assert store.highlights() == added
    # "import json" is 11 characters: [7, 12) runs off the end, [3, 3) is empty.
    assert "not within its 11 characters" in store.add_highlights(
        loaded, [HighlightSpec("src/app.py", 3, 7, 12)]
    )
    assert "not within" in store.add_highlights(loaded, [HighlightSpec("src/app.py", 3, 3, 3)])
    assert "tone must be" in store.add_highlights(loaded, [HighlightSpec("src/app.py", 3, 0, 1, tone="loud")])
    assert "integers" in store.add_highlights(loaded, [HighlightSpec("src/app.py", 3, 0, True)])
    assert "is not in a hunk" in store.add_highlights(loaded, [HighlightSpec("src/app.py", 12, 0, 1)])
    assert store.add_highlights(
        loaded, [HighlightSpec("src/app.py", 3, 0, 1), HighlightSpec("x", 1, 0, 1)]
    ) == ("x is not in the loaded diff")
    assert len(store.highlights()) == 2  # nothing of the refused batches landed
    assert store.add_highlights(loaded, []) == "no highlights given"


# -- a reload ---------------------------------------------------------------------


def test_prune_keeps_marks_on_an_untouched_hunk_and_drops_the_changed_ones():
    store = MarkStore()
    loaded = files(MODIFIED)
    store.add_notes(
        loaded,
        [NoteSpec("src/app.py", "on hunk 1", line=3), NoteSpec("src/app.py", "on hunk 2", line=22)],
        diffnotes.USER,
    )
    store.add_highlights(
        loaded, [HighlightSpec("src/app.py", 3, 0, 6), HighlightSpec("src/app.py", 22, 0, 6)]
    )
    reloaded = files(MODIFIED_HUNK_2_CHANGED)
    assert store.prune(reloaded) == 2
    assert [n.summary for n in store.notes()] == ["on hunk 1"]
    assert [h.line for h in store.highlights()] == [3]
    placed = store.placed_notes(reloaded)
    assert list(placed) == [("src/app.py", 0)]
    assert [(n.summary, index) for n, index in placed[("src/app.py", 0)]] == [("on hunk 1", 3)]
    assert store.placed_highlights(reloaded) == {("src/app.py", 0): [(store.highlights()[0], 3)]}


def test_prune_drops_a_mark_whose_hunk_moved_and_parks_one_whose_file_is_absent():
    store = MarkStore()
    loaded = files(MODIFIED, OTHER)
    store.add_notes(
        loaded,
        [NoteSpec("src/app.py", "on hunk 2", line=22), NoteSpec("README.md", "readme", line=1)],
        diffnotes.AGENT,
    )
    # The first hunk grew: hunk 2's lines are unchanged but its numbers shifted.
    assert store.prune(files(MODIFIED_HUNK_1_GROWN)) == 1
    assert [n.summary for n in store.notes()] == ["readme"]
    # A load without README.md keeps its note, unplaced; the next load
    # that shows it unchanged places it again.
    assert store.prune(files(MODIFIED)) == 0
    assert store.placed_notes(files(MODIFIED)) == {}
    assert list(store.placed_notes(files(OTHER))) == [("README.md", 0)]
    assert store.prune(files(OTHER.replace("+new title", "+newer title"))) == 1
    assert store.notes() == []


def test_placed_notes_follow_insertion_order_within_a_hunk():
    store = MarkStore()
    loaded = files(MODIFIED)
    store.add_notes(
        loaded,
        [NoteSpec("src/app.py", "later line", line=4), NoteSpec("src/app.py", "earlier line", line=2)],
        diffnotes.USER,
    )
    placed = store.placed_notes(loaded)[("src/app.py", 0)]
    assert [(n.summary, index) for n, index in placed] == [("later line", 4), ("earlier line", 2)]
