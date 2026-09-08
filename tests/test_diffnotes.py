# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for diffnotes: the text bounds, the address resolution, and the
MarkStore — batches landing whole or not at all, edits, clears, and what
survives a reload (the hunk's stable key)."""

from __future__ import annotations

from dataclasses import replace

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
@@ -1,1 +1,1 @@
-old title
+new title
 body
"""

# The second hunk of app.py edited (its key moves; the first hunk's stays).
RENAME_WITH_CHANGE = """\
diff --git a/a.txt b/b.txt
similarity index 66%
rename from a.txt
rename to b.txt
index 3b18e51..a1b2c3d 100644
--- a/a.txt
+++ b/b.txt
@@ -1,3 +1,3 @@
 one
-two
+deux
 three
"""

MODIFIED_HUNK_2_CHANGED = MODIFIED.replace("+        return 2", "+        return 3")
# The first hunk grown by a line: every later line number shifts, but the
# second hunk's lines do not, so its key holds (the spans are not in it).
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


def test_bound_text_treats_c1_controls_and_the_unicode_separators_like_the_c0_ones():
    # A label breaks a line on NEL, U+2028 and U+2029 as on "\n": folded,
    # so what a note shows is what its text says. C1 controls and DEL go.
    assert diffnotes.bound_text("a\x85b\u2028c\u2029d") == "a\nb\nc\nd"
    assert diffnotes.bound_text("a\x80b\x9fc\x7fd\x1bе") == "abcdе"


def test_summary_text_is_one_line():
    assert diffnotes.summary_text("first\n\n second \u2028third\t") == "first second third"
    assert diffnotes.summary_text("  \n ") == ""
    assert diffnotes.summary_text(None) == ""


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


def test_a_refused_batch_mints_no_ids_and_a_multi_line_summary_lands_as_one():
    store = MarkStore()
    loaded = files(MODIFIED)
    specs = [NoteSpec("src/app.py", "fine", line=3), NoteSpec("src/app.py", "x", line=999)]
    refused = store.add_notes(loaded, specs, diffnotes.AGENT)
    assert isinstance(refused, str) and store.notes() == []
    added = store.add_notes(loaded, [NoteSpec("src/app.py", "first\nsecond", line=3)], diffnotes.AGENT)
    assert [(n.id, n.summary) for n in added] == [("n1", "first second")]


def test_a_rename_is_addressed_by_its_old_name_on_the_old_side_only():
    loaded = files(RENAME_WITH_CHANGE)
    old = resolve_anchor(loaded, "a.txt", side="old", line=2)
    assert not isinstance(old, str)
    assert (old.file.path, old.side, old.line) == ("b.txt", "old", 2)
    assert old.hunk.lines[old.line_index].text == "two"
    assert resolve_anchor(loaded, "a.txt", line=2) == "a.txt is not in the loaded diff"
    store = MarkStore()
    added = store.add_notes(loaded, [NoteSpec("a.txt", "was two", side="old", line=2)], diffnotes.AGENT)
    assert [(n.path, n.side, n.line) for n in added] == [("b.txt", "old", 2)]


def test_highlights_are_capped_per_batch_and_per_page(monkeypatch):
    monkeypatch.setattr(diffnotes, "MAX_HIGHLIGHTS_PER_BATCH", 3)
    monkeypatch.setattr(diffnotes, "MAX_HIGHLIGHTS", 5)
    store = MarkStore()
    loaded = files(MODIFIED)
    spec = HighlightSpec("src/app.py", 3, 0, 6)
    assert store.add_highlights(loaded, [spec] * 4) == "at most 3 highlights at once"
    assert not isinstance(store.add_highlights(loaded, [spec] * 3), str)
    assert store.add_highlights(loaded, [spec] * 3) == "the page holds at most 5 highlights"
    assert len(store.highlights()) == 3
    assert not isinstance(store.add_highlights(loaded, [spec] * 2), str)
    # The refused batches minted no ids.
    assert [h.id for h in store.highlights()] == ["h1", "h2", "h3", "h4", "h5"]


def test_edit_and_remove_a_note():
    store = MarkStore()
    loaded = files(MODIFIED)
    added = store.add_notes(loaded, [NoteSpec("src/app.py", "first", line=3)], diffnotes.USER)
    note = added[0]
    edited = store.edit(note.id, "second\n", "because")
    assert edited is not None and (edited.summary, edited.rationale) == ("second", "because")
    assert store.note(note.id) == edited
    assert edited.id == note.id and edited.hunk_key == note.hunk_key
    # Both words are replaced: a rationale left out clears the note's.
    cleared = store.edit(note.id, "third")
    assert cleared is not None and (cleared.summary, cleared.rationale) == ("third", None)
    # A summary is one line however it arrives; the editor's split reads it back.
    folded = store.edit(note.id, "one\ntwo", "why")
    assert folded is not None and folded.summary == "one two"
    joined = diffnotes.join_note_text(folded.summary, folded.rationale)
    assert diffnotes.split_note_text(joined) == ("one two", "why")
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


def test_prune_renumbers_a_mark_whose_hunk_only_moved_and_parks_one_whose_file_is_absent():
    store = MarkStore()
    loaded = files(MODIFIED, OTHER)
    store.add_notes(
        loaded,
        [NoteSpec("src/app.py", "on hunk 2", line=22), NoteSpec("README.md", "readme", line=1)],
        diffnotes.AGENT,
    )
    store.add_highlights(
        loaded, [HighlightSpec("src/app.py", 22, 0, 6), HighlightSpec("src/app.py", 21, 0, 3, side="old")]
    )
    note = store.notes()[0]
    assert (note.line, note.line_index) == (22, 2)
    # The first hunk grew: hunk 2's lines are unchanged but its numbers
    # shifted by one. The marks stay, renumbered; the old side's did not move.
    grown = files(MODIFIED_HUNK_1_GROWN)
    assert store.prune(grown) == 2
    moved = store.notes()[0]
    assert (moved.id, moved.line, moved.line_index, moved.hunk_key) == (note.id, 23, 2, note.hunk_key)
    assert [n.summary for n in store.notes()] == ["on hunk 2", "readme"]
    assert [(h.side, h.line) for h in store.highlights()] == [("new", 23), ("old", 21)]
    placed = store.placed_notes(grown)
    assert [(n.summary, index) for n, index in placed[("src/app.py", 1)]] == [("on hunk 2", 2)]
    assert [(h.line, index) for h, index in store.placed_highlights(grown)[("src/app.py", 1)]] == [
        (23, 2),
        (21, 1),
    ]
    assert grown[0].hunks[1].lines[2].new == 23
    # Back to the original: renumbered back (two marks moved, none went).
    assert store.prune(files(MODIFIED)) == 2
    assert store.notes()[0].line == 22
    # A load without README.md keeps its note, unplaced; the next load
    # that shows it unchanged places it again.
    assert store.prune(files(MODIFIED)) == 0
    assert list(store.placed_notes(files(MODIFIED))) == [("src/app.py", 1)]
    assert list(store.placed_notes(files(OTHER))) == [("README.md", 0)]
    assert store.prune(files(OTHER.replace("+new title", "+newer title"))) == 1
    assert [n.summary for n in store.notes()] == ["on hunk 2"]


TWINS = """\
diff --git a/twins.txt b/twins.txt
index 3b18e51..a1b2c3d 100644
--- a/twins.txt
+++ b/twins.txt
@@ -1,1 +1,1 @@
-a
+b
@@ -10,1 +10,1 @@
-a
+b
"""


def test_marks_on_equal_hunks_stay_on_their_own_across_a_shift():
    store = MarkStore()
    loaded = files(TWINS)
    store.add_notes(
        loaded,
        [NoteSpec("twins.txt", "first", line=1), NoteSpec("twins.txt", "second", line=10)],
        diffnotes.USER,
    )
    first, second = store.notes()
    assert first.hunk_key != second.hunk_key
    # Both twins shifted by an edit above them: each note stays on its own.
    shifted = files(
        TWINS.replace("@@ -1,1 +1,1 @@", "@@ -3,1 +5,1 @@").replace("@@ -10,1 +10,1 @@", "@@ -12,1 +14,1 @@")
    )
    assert store.prune(shifted) == 2
    assert [(n.summary, n.line, n.hunk_key) for n in store.notes()] == [
        ("first", 5, first.hunk_key),
        ("second", 14, second.hunk_key),
    ]
    assert store.prune(loaded) == 2 and [n.line for n in store.notes()] == [1, 10]
    # The first twin changes: the bodies no longer say which hunk the
    # second note was on, so both are dropped rather than one landing on
    # the wrong twin.
    changed = files(
        TWINS.replace("@@ -1,1 +1,1 @@\n-a\n+b\n", "@@ -1,1 +1,3 @@\n-a\n+b\n+x\n+y\n").replace(
            "@@ -10,1 +10,1 @@", "@@ -10,1 +12,1 @@"
        )
    )
    assert store.prune(changed) == 2 and store.notes() == []


def test_a_mark_whose_line_index_is_off_the_hunk_is_dropped():
    store = MarkStore()
    loaded = files(MODIFIED)
    store.add_notes(loaded, [NoteSpec("src/app.py", "x", line=3)], diffnotes.USER)
    note = store.notes()[0]
    store._notes[note.id] = replace(note, line_index=99)
    assert store.prune(loaded) == 1 and store.notes() == []


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
