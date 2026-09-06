# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for diffmodel: the native diff panel's parser over every header
form git writes, the numstat pre-pass and hunk's caps, gaps, split rows,
word emphasis, the palette blend, locate and stable keys — and what garbage
and oversized input do to each."""

import shutil
import subprocess
from pathlib import Path

import pytest

from collins import diffmodel
from collins.diffmodel import (
    ADD,
    BEFORE,
    CONTEXT,
    DEL,
    NEW,
    OLD,
    TRAILING,
    Emphasis,
    File,
    Gap,
    Hunk,
    Line,
    SplitRow,
    gaps,
    locate,
    palette,
    parse,
    parse_numstat,
    split_rows,
    stable_key,
    too_large,
    word_emphasis,
)

# -- fixtures -------------------------------------------------------------------

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

NEW_FILE = """\
diff --git a/notes.txt b/notes.txt
new file mode 100644
index 0000000..94954ab
--- /dev/null
+++ b/notes.txt
@@ -0,0 +1,2 @@
+hello
+world
"""

DELETED_FILE = """\
diff --git a/old.txt b/old.txt
deleted file mode 100755
index 94954ab..0000000
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-hello
-world
"""

PURE_RENAME = """\
diff --git a/lib/before.py b/lib/after.py
similarity index 100%
rename from lib/before.py
rename to lib/after.py
"""

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

BINARY = """\
diff --git a/img/logo.png b/img/logo.png
index 3b18e51..a1b2c3d 100644
Binary files a/img/logo.png and b/img/logo.png differ
"""

BINARY_NEW = """\
diff --git a/blob.bin b/blob.bin
new file mode 100644
index 0000000..6772730
Binary files /dev/null and b/blob.bin differ
"""

GIT_BINARY_PATCH = """\
diff --git a/x.dat b/x.dat
index 3b18e51..a1b2c3d 100644
GIT binary patch
literal 4
LcmZQzWMT#Y01f~L

literal 0
HcmV?d00001

"""

MODE_ONLY = """\
diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
"""

MODE_AND_CONTENT = """\
diff --git a/run.sh b/run.sh
old mode 100644
new mode 100755
index 3b18e51..a1b2c3d
--- a/run.sh
+++ b/run.sh
@@ -1 +1,2 @@
 #!/bin/sh
+set -e
"""

NO_NEWLINE = """\
diff --git a/nonl.txt b/nonl.txt
index 3b18e51..a1b2c3d 100644
--- a/nonl.txt
+++ b/nonl.txt
@@ -1 +1 @@
-no newline
\\ No newline at end of file
+still none
\\ No newline at end of file
"""

UNTRACKED = """\
diff --git a/new.txt b/new.txt
new file mode 100644
index 0000000..94954ab
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
"""

QUOTED = """\
diff --git "a/caf\\303\\251 x.txt" "b/caf\\303\\251 x.txt"
index 3b18e51..a1b2c3d 100644
--- "a/caf\\303\\251 x.txt"
+++ "b/caf\\303\\251 x.txt"
@@ -1 +1 @@
-a
+b
"""

COPY = """\
diff --git a/orig.txt b/copy.txt
similarity index 100%
copy from orig.txt
copy to copy.txt
"""


def only(text: str) -> File:
    files = parse(text)
    assert len(files) == 1, files
    return files[0]


def hunk_of(*body: str, old=(1, 3), new=(1, 3), context="") -> Hunk:
    """A hunk from sign-prefixed lines, numbered the way the parser would."""
    lines = []
    old_no, new_no = old[0], new[0]
    for raw in body:
        kind = {"+": ADD, "-": DEL}.get(raw[:1], CONTEXT)
        text = raw[1:]
        if kind == ADD:
            lines.append(Line(ADD, text, None, new_no))
            new_no += 1
        elif kind == DEL:
            lines.append(Line(DEL, text, old_no, None))
            old_no += 1
        else:
            lines.append(Line(CONTEXT, text, old_no, new_no))
            old_no += 1
            new_no += 1
    header = f"@@ -{old[0]},{old[1]} +{new[0]},{new[1]} @@" + (f" {context}" if context else "")
    return Hunk(0, header, old[0], old[1], new[0], new[1], context, tuple(lines))


# -- parse: header forms ----------------------------------------------------------


def test_parse_reads_a_modified_file_with_two_hunks():
    file = only(MODIFIED)
    assert file.path == "src/app.py"
    assert file.previous_path is None
    assert file.kind == diffmodel.KIND_CHANGE
    assert (file.old_mode, file.new_mode) == ("100644", "100644")
    assert file.similarity is None
    assert not file.untracked
    assert (file.additions, file.deletions) == (3, 2)
    assert file.patch == MODIFIED
    assert len(file.patch_hash) == 40
    first, second = file.hunks
    assert (first.index, second.index) == (0, 1)
    assert first.header == "@@ -1,4 +1,5 @@ def main():"
    assert first.context == "def main():"
    assert (first.old_start, first.old_count, first.new_start, first.new_count) == (1, 4, 1, 5)
    assert first.lines == (
        Line(CONTEXT, "import os", 1, 1),
        Line(DEL, "import sys", 2, None),
        Line(ADD, "import sys, re", None, 2),
        Line(ADD, "import json", None, 3),
        Line(CONTEXT, "", 3, 4),
        Line(CONTEXT, "print(os.name)", 4, 5),
    )
    assert second.context == "class App:"
    assert [line.kind for line in second.lines] == [CONTEXT, DEL, ADD, CONTEXT]
    assert second.lines[1] == Line(DEL, "        return 1", 21, None)
    assert second.lines[2] == Line(ADD, "        return 2", None, 22)


def test_parse_reads_a_new_file():
    file = only(NEW_FILE)
    assert file.path == "notes.txt"
    assert file.kind == diffmodel.KIND_NEW
    assert (file.old_mode, file.new_mode) == (None, "100644")
    assert (file.additions, file.deletions) == (2, 0)
    hunk = file.hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (0, 0, 1, 2)
    assert hunk.context == ""
    assert hunk.header == "@@ -0,0 +1,2 @@"
    assert hunk.lines == (Line(ADD, "hello", None, 1), Line(ADD, "world", None, 2))


def test_parse_reads_a_deleted_file_under_its_old_name():
    file = only(DELETED_FILE)
    assert file.path == "old.txt"
    assert file.kind == diffmodel.KIND_DELETED
    assert (file.old_mode, file.new_mode) == ("100755", None)
    assert (file.additions, file.deletions) == (0, 2)
    assert file.hunks[0].lines == (Line(DEL, "hello", 1, None), Line(DEL, "world", 2, None))


def test_parse_reads_a_pure_rename_with_no_hunks():
    file = only(PURE_RENAME)
    assert (file.path, file.previous_path) == ("lib/after.py", "lib/before.py")
    assert file.kind == diffmodel.KIND_RENAME
    assert file.similarity == 100
    assert file.hunks == ()
    assert (file.additions, file.deletions) == (0, 0)


def test_parse_reads_a_rename_with_content_changes():
    file = only(RENAME_WITH_CHANGE)
    assert (file.path, file.previous_path) == ("b.txt", "a.txt")
    assert file.kind == diffmodel.KIND_RENAME
    assert file.similarity == 66
    assert len(file.hunks) == 1
    assert (file.additions, file.deletions) == (1, 1)


def test_parse_reads_a_copy_like_a_rename_with_its_source():
    file = only(COPY)
    assert (file.path, file.previous_path) == ("copy.txt", "orig.txt")
    assert file.kind == diffmodel.KIND_RENAME
    assert file.similarity == 100


def test_parse_reads_binary_stanzas_as_placeholders():
    file = only(BINARY)
    assert file.path == "img/logo.png"
    assert file.kind == diffmodel.KIND_BINARY
    assert file.hunks == ()
    assert (file.old_mode, file.new_mode) == ("100644", "100644")
    new = only(BINARY_NEW)
    assert new.path == "blob.bin"
    assert new.kind == diffmodel.KIND_BINARY
    assert (new.old_mode, new.new_mode) == (None, "100644")
    patch = only(GIT_BINARY_PATCH)
    assert patch.path == "x.dat"
    assert patch.kind == diffmodel.KIND_BINARY
    assert patch.hunks == ()


def test_parse_reads_a_mode_only_change():
    file = only(MODE_ONLY)
    assert file.path == "run.sh"
    assert file.kind == diffmodel.KIND_MODE
    assert (file.old_mode, file.new_mode) == ("100644", "100755")
    assert file.hunks == ()


def test_parse_reads_a_mode_change_with_content_as_a_change():
    file = only(MODE_AND_CONTENT)
    assert file.kind == diffmodel.KIND_CHANGE
    assert (file.old_mode, file.new_mode) == ("100644", "100755")
    hunk = file.hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (1, 1, 1, 2)
    assert hunk.header == "@@ -1 +1,2 @@"


def test_parse_marks_no_newline_at_end_of_file_on_the_line_before_it():
    file = only(NO_NEWLINE)
    assert file.hunks[0].lines == (
        Line(DEL, "no newline", 1, None, True),
        Line(ADD, "still none", None, 1, True),
    )


def test_parse_marks_untracked_files_when_asked():
    file = parse(UNTRACKED, untracked=True)[0]
    assert file.untracked
    assert file.kind == diffmodel.KIND_NEW
    assert file.path == "new.txt"
    assert not parse(UNTRACKED)[0].untracked


def test_parse_unquotes_c_quoted_paths():
    file = only(QUOTED)
    assert file.path == "café x.txt"
    assert file.kind == diffmodel.KIND_CHANGE


def test_parse_handles_a_path_containing_b_slash_off_the_diff_line():
    text = "diff --git a/x b/y.png b/x b/y.png\nBinary files a/x b/y.png and b/x b/y.png differ\n"
    assert only(text).path == "x b/y.png"


def test_parse_reads_the_whole_stream_in_order_and_skips_the_preamble():
    text = "warning: something\n" + MODIFIED + NEW_FILE + PURE_RENAME + BINARY + MODE_ONLY + DELETED_FILE
    files = parse(text)
    assert [f.path for f in files] == [
        "src/app.py", "notes.txt", "lib/after.py", "img/logo.png", "run.sh", "old.txt",
    ]
    assert [f.kind for f in files] == [
        "change", "new", "rename", "binary", "mode", "deleted",
    ]
    assert files[0].patch == MODIFIED
    assert files[-1].patch == DELETED_FILE
    assert files[0].patch_hash != files[1].patch_hash


def test_parse_skips_combined_diff_stanzas_of_a_merge():
    combined = "diff --cc m.txt\nindex 1,2..3\n--- a/m.txt\n+++ b/m.txt\n@@@ -1,2 -1,2 +1,2 @@@\n  same\n"
    assert [f.path for f in parse(combined + NEW_FILE)] == ["notes.txt"]


# -- parse: garbage and the caps ----------------------------------------------------


@pytest.mark.parametrize(
    "text", ["", None, 42, "not a diff at all\n", "diff --git\n", "@@ -1 +1 @@\n-a\n+b\n"]
)
def test_parse_tolerates_garbage(text):
    assert parse(text) == []


def test_parse_drops_a_hunk_whose_body_ends_short_and_keeps_the_ones_before():
    text = MODIFIED.rsplit("     # end\n", 1)[0]  # the second hunk lost its last context line
    file = only(text)
    assert len(file.hunks) == 1
    assert file.hunks[0].header == "@@ -1,4 +1,5 @@ def main():"
    assert file.kind == diffmodel.KIND_CHANGE
    assert file.patch.endswith("+        return 2\n")


def test_parse_drops_a_hunk_with_a_line_no_unified_diff_has():
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n a\n*b\n"
    file = only(text)
    assert file.hunks == ()
    assert file.kind == diffmodel.KIND_CHANGE


def test_parse_tolerates_an_empty_context_line_written_bare():
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"
    file = only(text)
    assert [line.kind for line in file.hunks[0].lines] == [CONTEXT, CONTEXT, DEL, ADD]
    assert file.hunks[0].lines[1].text == ""


def test_parse_drops_a_stanza_with_no_path_or_an_overlong_one():
    assert parse("diff --git \nindex 1..2\n") == []
    long_path = "x" * (diffmodel.MAX_PATH_CHARS + 1)
    text = MODE_AND_CONTENT.replace("run.sh", long_path)
    assert parse(text) == []
    assert parse(text + NEW_FILE)[0].path == "notes.txt"


def test_parse_never_returns_more_than_max_files(monkeypatch):
    monkeypatch.setattr(diffmodel, "MAX_FILES", 3)
    text = "".join(NEW_FILE.replace("notes.txt", f"n{i}.txt") for i in range(6))
    assert [f.path for f in parse(text)] == ["n0.txt", "n1.txt", "n2.txt"]


def test_parse_cuts_the_stream_at_max_patch_chars_and_drops_the_cut_stanza(monkeypatch):
    stanzas = [NEW_FILE.replace("notes.txt", f"n{i}.txt") for i in range(4)]
    text = "".join(stanzas)
    monkeypatch.setattr(diffmodel, "MAX_PATCH_CHARS", len(stanzas[0]) * 2 + 10)
    assert [f.path for f in parse(text)] == ["n0.txt", "n1.txt"]
    monkeypatch.setattr(diffmodel, "MAX_PATCH_CHARS", len(text))
    assert len(parse(text)) == 4


def test_parse_turns_an_oversized_file_into_a_placeholder(monkeypatch):
    monkeypatch.setattr(diffmodel, "TOO_LARGE_LINES", 4)
    file = only(MODIFIED)  # 3 + 2 changed lines
    assert file.kind == diffmodel.KIND_TOO_LARGE
    assert file.hunks == ()
    assert (file.additions, file.deletions) == (3, 2)
    assert file.patch == MODIFIED
    monkeypatch.setattr(diffmodel, "TOO_LARGE_LINES", 5)
    assert only(MODIFIED).kind == diffmodel.KIND_CHANGE
    monkeypatch.setattr(diffmodel, "TOO_LARGE_BYTES", 10)
    assert only(MODIFIED).kind == diffmodel.KIND_TOO_LARGE
    assert only(BINARY).kind == diffmodel.KIND_BINARY  # binary stays binary


# -- numstat and too_large ---------------------------------------------------------


def test_parse_numstat_reads_the_z_form_with_renames_and_skips_binaries():
    text = "3\t1\tsrc/app.py\0" "2\t0\t\0a.txt\0b.txt\0" "-\t-\timg/logo.png\0" "0\t0\tempty\0"
    assert parse_numstat(text) == {"src/app.py": (3, 1), "b.txt": (2, 0), "empty": (0, 0)}


def test_parse_numstat_reads_the_no_index_dev_null_pair_by_the_new_path():
    assert parse_numstat("2\t0\t\0/dev/null\0new.txt\0") == {"new.txt": (2, 0)}


def test_parse_numstat_reads_newline_separated_output_too():
    assert parse_numstat("3\t1\tsrc/app.py\n-\t-\tlogo.png\n") == {"src/app.py": (3, 1)}


@pytest.mark.parametrize("text", ["", None, 7, "garbage\0", "3\t1\0", "x\ty\tpath\0", "1\t2\t\0only-old\0"])
def test_parse_numstat_tolerates_garbage(text):
    assert parse_numstat(text) == {}


def test_parse_numstat_is_bounded_like_the_stream_before_it_is_tokenized(monkeypatch):
    monkeypatch.setattr(diffmodel, "MAX_PATCH_CHARS", 20)
    # 20 characters end inside the third record's path: it is dropped, not
    # read as a file called "cccc".
    text = "1\t1\ta\0" "1\t1\tb\0" "1\t1\tccccccccccc\0" "1\t1\td\0"
    assert parse_numstat(text) == {"a": (1, 1), "b": (1, 1)}
    assert parse_numstat(text.replace("\0", "\n")) == {"a": (1, 1), "b": (1, 1)}
    assert parse_numstat("1\t1\ta\0" "1\t1\tb\0") == {"a": (1, 1), "b": (1, 1)}  # under the cap: whole


def test_parse_numstat_bounds_paths_and_the_file_count(monkeypatch):
    long_path = "x" * (diffmodel.MAX_PATH_CHARS + 1)
    assert parse_numstat(f"1\t1\t{long_path}\0" "1\t1\tok\0") == {"ok": (1, 1)}
    monkeypatch.setattr(diffmodel, "MAX_FILES", 2)
    assert parse_numstat("1\t1\ta\0" "1\t1\tb\0" "1\t1\tc\0") == {"a": (1, 1), "b": (1, 1)}


def test_too_large_applies_hunks_caps():
    assert diffmodel.TOO_LARGE_LINES == 20_000
    assert diffmodel.TOO_LARGE_BYTES == 1_048_576
    assert not too_large(10_000, 10_000, 1_048_576)
    assert too_large(10_001, 10_000, 0)
    assert too_large(0, 0, 1_048_577)
    assert not too_large(0, 0, 0)


# -- gaps -------------------------------------------------------------------------


def test_gaps_names_the_stretches_before_each_hunk_and_after_the_last():
    file = only(MODIFIED)  # hunks at old 1-4 / new 1-5 and old 20-22 / new 21-23
    assert gaps(file) == [Gap(BEFORE, 1, (5, 19), (6, 20), 15)]
    assert gaps(file, old_len=30, new_len=31) == [
        Gap(BEFORE, 1, (5, 19), (6, 20), 15),
        Gap(TRAILING, 1, (23, 30), (24, 31), 8),
    ]


def test_gaps_skips_a_hunk_at_the_top_and_a_file_that_ends_with_its_hunk():
    file = only(NEW_FILE)
    assert gaps(file) == []
    assert gaps(file, old_len=0, new_len=2) == []


def test_gaps_handles_a_pure_insertion_and_one_known_side():
    text = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -5,0 +6,2 @@\n+x\n+y\n"
    file = only(text)
    assert gaps(file) == [Gap(BEFORE, 0, (1, 5), (1, 5), 5)]
    trailing = Gap(TRAILING, 0, (6, 10), (8, 12), 5)
    assert gaps(file, old_len=10) == [Gap(BEFORE, 0, (1, 5), (1, 5), 5), trailing]
    assert gaps(file, new_len=12)[-1] == trailing


def test_gaps_are_empty_for_placeholders():
    assert gaps(only(BINARY), 10, 10) == []
    assert gaps(only(MODE_ONLY), 10, 10) == []


def test_hunk_range_spans_at_least_one_line():
    hunk = only(NEW_FILE).hunks[0]
    assert diffmodel.hunk_range(hunk, OLD) == (0, 0)
    assert diffmodel.hunk_range(hunk, NEW) == (1, 2)


# -- split rows -------------------------------------------------------------------


def test_split_rows_pairs_deletions_with_additions_and_pads_the_shorter_side():
    hunk = only(MODIFIED).hunks[0]
    ctx, del1, add1, add2, blank, tail = hunk.lines
    assert split_rows(hunk) == [
        SplitRow(ctx, ctx),
        SplitRow(del1, add1),
        SplitRow(None, add2),
        SplitRow(blank, blank),
        SplitRow(tail, tail),
    ]


def test_split_rows_pads_the_new_side_when_deletions_outnumber_additions():
    hunk = hunk_of(" a", "-b", "-c", "-d", "+e", " f", old=(1, 5), new=(1, 3))
    a, b, c, d, e, f = hunk.lines
    assert split_rows(hunk) == [
        SplitRow(a, a), SplitRow(b, e), SplitRow(c, None), SplitRow(d, None), SplitRow(f, f),
    ]


def test_split_rows_treats_each_change_block_on_its_own():
    hunk = hunk_of("-a", "+b", " c", "+d", " e", "-f", old=(1, 4), new=(1, 4))
    a, b, c, d, e, f = hunk.lines
    assert split_rows(hunk) == [
        SplitRow(a, b), SplitRow(c, c), SplitRow(None, d), SplitRow(e, e), SplitRow(f, None),
    ]
    assert split_rows(hunk_of(old=(1, 0), new=(1, 0))) == []


# -- word emphasis -----------------------------------------------------------------


def test_word_emphasis_marks_the_words_that_differ_in_a_paired_line():
    hunk = only(MODIFIED).hunks[0]  # "import sys" -> "import sys, re"
    assert word_emphasis(hunk) == [Emphasis(NEW, 2, ((10, 14),))]
    assert hunk.lines[2].text[10:14] == ", re"


def test_word_emphasis_marks_each_changed_word_on_both_sides():
    hunk = hunk_of("-the quick brown fox", "+the slow red fox", old=(1, 1), new=(1, 1))
    assert word_emphasis(hunk) == [
        Emphasis(OLD, 0, ((4, 9), (10, 15))),
        Emphasis(NEW, 1, ((4, 8), (9, 12))),
    ]
    assert (hunk.lines[0].text[4:9], hunk.lines[0].text[10:15]) == ("quick", "brown")
    assert (hunk.lines[1].text[4:8], hunk.lines[1].text[9:12]) == ("slow", "red")


def test_word_emphasis_merges_spans_that_touch():
    spans: list[tuple[int, int]] = []
    diffmodel._add_span(spans, 0, 3)
    diffmodel._add_span(spans, 3, 5)
    diffmodel._add_span(spans, 7, 9)
    assert spans == [(0, 5), (7, 9)]


def test_word_emphasis_skips_unrelated_lines_and_identical_ones():
    unrelated = hunk_of("-alpha beta gamma", "+one(two, three)", old=(1, 1), new=(1, 1))
    assert word_emphasis(unrelated) == []
    same = hunk_of("-same text", "+same text", old=(1, 1), new=(1, 1))
    assert word_emphasis(same) == []


def test_word_emphasis_pairs_by_position_inside_a_block_and_leaves_extras_alone():
    hunk = hunk_of("-a = 1", "-b = 2", "+a = 10", "+b = 2", "+c = 3", old=(1, 2), new=(1, 3))
    assert word_emphasis(hunk) == [Emphasis(OLD, 0, ((4, 5),)), Emphasis(NEW, 2, ((4, 6),))]


def test_word_emphasis_gives_up_on_huge_hunks_and_wide_lines(monkeypatch):
    hunk = hunk_of("-a b", "+a c", old=(1, 1), new=(1, 1))
    assert word_emphasis(hunk) != []
    monkeypatch.setattr(diffmodel, "MAX_EMPHASIS_LINES", 1)
    assert word_emphasis(hunk) == []
    monkeypatch.setattr(diffmodel, "MAX_EMPHASIS_LINES", 2000)
    monkeypatch.setattr(diffmodel, "MAX_EMPHASIS_CHARS", 2)
    assert word_emphasis(hunk) == []


# -- palette -----------------------------------------------------------------------


def test_palette_blends_the_sign_colours_into_the_text_background():
    pal = palette("#1e1e1e", "#33B2A4", "#F66151", dark=True)
    # 0x1e = 30; 30 * 0.82 + 0x33 * 0.18 = 33.78 -> 0x22; green 30 * .82 + 178 * .18 = 56.64 -> 0x39
    assert pal.added_bg == "#223936"
    assert pal.removed_bg == "#452a27"
    assert pal.added_emphasis_bg == "#265954"
    assert pal.removed_emphasis_bg == "#743932"
    assert pal.padding_bg == "#2c2c2c"
    assert (pal.added_fg, pal.removed_fg) == ("#33b2a4", "#f66151")
    assert diffmodel.blend((0, 0, 0), (255, 255, 255), 0.5) == (128, 128, 128)
    assert diffmodel.blend((10, 20, 30), (10, 20, 30), 1.0) == (10, 20, 30)


def test_palette_reads_short_and_alpha_hex_and_pushes_padding_the_right_way():
    light = palette("#fff", "#26a269ff", "#c01c28", dark=False)
    assert light.added_bg == "#d8eee4"
    assert light.padding_bg == "#f0f0f0"
    dark = palette("#000", "#26a269", "#c01c28", dark=True)
    assert dark.padding_bg == "#0f0f0f"


def test_palette_falls_back_to_the_adwaita_colours_for_garbage():
    assert palette(None, "red", 42, dark=True) == palette("#1e1e1e", "#33b2a4", "#f66151", dark=True)
    assert palette("", "", "", dark=False) == palette("#ffffff", "#26a269", "#c01c28", dark=False)
    assert palette("#12345", "#ggg", "#", dark=False).added_fg == "#26a269"


# -- locate and stable keys -----------------------------------------------------------


def test_locate_finds_the_hunk_and_line_an_address_names():
    files = parse(MODIFIED + RENAME_WITH_CHANGE)
    app = files[0]
    assert locate(files, "src/app.py", NEW, 3) == (app, 0, 3)  # "import json"
    assert locate(files, "src/app.py", OLD, 2) == (app, 0, 1)  # "import sys"
    assert locate(files, "src/app.py", NEW, 22) == (app, 1, 2)
    assert locate(files, "src/app.py", OLD, 21) == (app, 1, 1)
    assert locate(files, "src/app.py", NEW, 5) == (app, 0, 5)


def test_locate_reads_a_renames_old_name_on_the_old_side_only():
    files = parse(RENAME_WITH_CHANGE)
    assert locate(files, "a.txt", OLD, 2) == (files[0], 0, 1)
    assert locate(files, "b.txt", NEW, 2) == (files[0], 0, 2)
    assert locate(files, "a.txt", NEW, 2) is None


def test_locate_answers_none_for_lines_outside_every_hunk_and_bad_addresses():
    files = parse(MODIFIED + BINARY)
    assert locate(files, "src/app.py", NEW, 10) is None  # in the gap
    assert locate(files, "src/app.py", OLD, 3) == (files[0], 0, 4)
    assert locate(files, "src/app.py", OLD, 5) is None
    assert locate(files, "src/app.py", NEW, 2) == (files[0], 0, 2)  # the added line, not the deleted one
    assert locate(files, "img/logo.png", NEW, 1) is None
    assert locate(files, "missing", NEW, 1) is None
    assert locate(files, "src/app.py", "left", 1) is None
    assert locate(files, "src/app.py", NEW, 0) is None
    assert locate(files, "src/app.py", NEW, True) is None
    assert locate(files, "src/app.py", NEW, "3") is None
    assert locate(files, None, NEW, 3) is None
    assert locate([], "src/app.py", NEW, 3) is None


def test_stable_key_survives_an_untouched_hunk_and_changes_with_a_moved_one():
    before = only(MODIFIED)
    after = only(
        MODIFIED.replace("+1,5 @@", "+1,6 @@")
        .replace("+import json\n", "+import json\n+import re\n")
        .replace("@@ -20,3 +21,3 @@", "@@ -20,3 +22,3 @@")
    )
    assert stable_key(before, before.hunks[0]) != stable_key(after, after.hunks[0])  # the first hunk changed
    assert stable_key(before, before.hunks[1]) != stable_key(after, after.hunks[1])  # the second moved
    same = only(MODIFIED)
    assert stable_key(before, before.hunks[1]) == stable_key(same, same.hunks[1])
    assert stable_key(before) == stable_key(same)
    assert stable_key(before) != stable_key(after)
    assert stable_key(before).startswith("src/app.py\x00")


def test_stable_key_tells_files_of_the_same_content_apart_by_path_and_rename():
    a = only(NEW_FILE)
    b = only(NEW_FILE.replace("notes.txt", "other.txt"))
    assert stable_key(a, a.hunks[0]) != stable_key(b, b.hunks[0])
    renamed = only(RENAME_WITH_CHANGE)
    assert stable_key(renamed).startswith("a.txt\x00b.txt\x00")


# -- against real git ------------------------------------------------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't on PATH")


def git(cwd: Path, *args: str) -> str:
    """tests/test_gitops.py's runner: the diff calls exit 1 when there is a
    difference, so the status is not checked."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True).stdout


@needs_git
def test_parse_reads_what_git_writes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("one\ntwo\nthree\n")
    (repo / "old.txt").write_text("gone\n")
    (repo / "move.txt").write_text("x\ny\nz\n" * 10)
    (repo / "run.sh").write_text("#!/bin/sh\n")
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02bin")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    (repo / "a.txt").write_text("one\ndeux\nthree\nfour")  # no trailing newline
    (repo / "old.txt").unlink()
    (repo / "move.txt").rename(repo / "moved.txt")
    (repo / "run.sh").chmod(0o755)
    (repo / "blob.bin").write_bytes(b"\x00\x01\x03bin")
    (repo / "new.txt").write_text("hello\n")
    git(repo, "add", "-A")
    text = git(repo, "diff", "--cached", "--no-ext-diff", "--find-renames", "--no-color")
    files = {f.path: f for f in parse(text)}
    assert set(files) == {"a.txt", "blob.bin", "moved.txt", "new.txt", "old.txt", "run.sh"}
    assert files["a.txt"].kind == "change"
    assert files["a.txt"].hunks[0].lines[-1] == Line(ADD, "four", None, 4, True)
    assert files["blob.bin"].kind == "binary"
    assert (files["moved.txt"].kind, files["moved.txt"].previous_path, files["moved.txt"].similarity) == (
        "rename", "move.txt", 100,
    )
    assert files["new.txt"].kind == "new" and files["new.txt"].additions == 1
    assert files["old.txt"].kind == "deleted" and files["old.txt"].deletions == 1
    assert (files["run.sh"].kind, files["run.sh"].old_mode, files["run.sh"].new_mode) == (
        "mode", "100644", "100755",
    )
    numstat = parse_numstat(git(repo, "diff", "--cached", "--numstat", "-z", "--find-renames"))
    assert numstat["a.txt"] == (2, 1)
    assert numstat["moved.txt"] == (0, 0)
    assert "blob.bin" not in numstat
    (repo / "loose.txt").write_text("untracked\n")
    loose = parse(git(repo, "diff", "--no-index", "--", "/dev/null", "loose.txt"), untracked=True)
    assert [(f.path, f.kind, f.untracked) for f in loose] == [("loose.txt", "new", True)]
