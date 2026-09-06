# New in the ghackett fork of agent-session-manager (GPL-3.0).
# Portions adapted from muzomer/hunk-commit (MIT, © 2026 hunk-jj-stage
# contributors), by way of the collins-git hunk extension's test/patch.test.ts;
# see collins/THIRD_PARTY_LICENSES.md.

"""Tests for gitpatch: the partial-patch writers, the cross-checks and the
planners, then the same against a real index in a temp repository. Ports
of the collins-git extension's `bun test` cases — test/patch.test.ts,
range.test.ts, staging.test.ts, anchor.test.ts, range.integration.test.ts
and staging.integration.test.ts — onto diffmodel's File / Hunk / Line.

What did not port, and why: anchor.ts's module-level anchor state (set /
current / clear) is the view's selection now and lives in the widget;
`anchorMarks`, the amber line highlight hunk painted, is the view's own
text selection. The address → position lookups the extension did for the
cursor (`locate`) survive as `locate_address`; the planners take line
indexes, so the "not on a diff line" refusals become index-bounds
refusals. The integration tests run the view's git through gitops
(file_patch, apply_patch, read_diff, stage_paths / unstage_paths /
checkout_paths); only the repository setup and the read-backs shell out on
their own (_run, _git)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from collins import diffmodel, gitinfo, gitops, gitpatch
from collins.diffmodel import (
    ADD,
    CONTEXT,
    DEL,
    KIND_BINARY,
    KIND_CHANGE,
    KIND_RENAME,
    KIND_TOO_LARGE,
    NEW,
    OLD,
)
from collins.gitmodel import Status, StatusRow
from collins.gitpatch import (
    OP_ADD,
    OP_APPLY_CACHED,
    OP_APPLY_CACHED_REVERSE,
    OP_APPLY_WORKTREE_REVERSE,
    OP_CHECKOUT,
    OP_RESET,
    OP_TRASH,
    STAGED,
    UNSTAGED,
    LinePos,
    LineRange,
    Plan,
    RangeRefusal,
    Refusal,
)

# -- fixtures -----------------------------------------------------------------

HEADER = "diff --git a/f.txt b/f.txt\nindex 1111111..2222222 100644\n--- a/f.txt\n+++ b/f.txt\n"

MODIFIED = """diff --git a/src/app.ts b/src/app.ts
index 1111111..2222222 100644
--- a/src/app.ts
+++ b/src/app.ts
@@ -1,3 +1,4 @@
 const a = 1;
-const b = 2;
+const b = 3;
+const c = 4;
 const d = 5;
"""

TWO_HUNKS_TEXT = """diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -2,1 +2,3 @@ context heading
 b
+NEW1
+NEW2
@@ -15,1 +17,1 @@
-old
+new
"""

# Three hunks: a 1:2 replacement, a 1:1 replacement, a pure insertion.
THREE_HUNKS_TEXT = f"""{HEADER}@@ -1,4 +1,5 @@
 a
-b
+B
+B2
 c
 d
@@ -10,3 +11,3 @@
 j
-k
+K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
"""

# staging.test.ts's fixture: hunk 2 is `-x -y +z` at old 10-11 / new 12.
STAGING_TEXT = """diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -2,1 +2,3 @@
 b
+NEW1
+NEW2
@@ -10,2 +12,1 @@
-x
-y
+z
@@ -20,1 +21,2 @@
 q
+Q
"""

BINARY_PATCH = (
    "diff --git a/i.png b/i.png\nindex 1111111..2222222 100644\nBinary files a/i.png and b/i.png differ\n"
)


def parse_one(text: str) -> diffmodel.File:
    files = diffmodel.parse(text)
    assert len(files) == 1, text
    return files[0]


def at(hunk: int, index: int) -> LinePos:
    return LinePos(hunk, index)


def span(a: LinePos, b: LinePos) -> LineRange:
    return LineRange(a, b)


def as_binary(file: diffmodel.File) -> diffmodel.File:
    return replace(file, kind=KIND_BINARY, hunks=())


def as_too_large(file: diffmodel.File) -> diffmodel.File:
    return replace(file, kind=KIND_TOO_LARGE, hunks=())


def as_untracked(file: diffmodel.File) -> diffmodel.File:
    return replace(file, untracked=True)


def as_renamed(file: diffmodel.File, previous: str) -> diffmodel.File:
    return replace(file, previous_path=previous, kind=KIND_RENAME)


def moved(file: diffmodel.File, hunk_index: int, new_start: int) -> diffmodel.File:
    """The loaded file with one hunk's new-side start moved — what a stale
    view shows once the disk changed underneath it."""
    hunks = list(file.hunks)
    hunks[hunk_index] = replace(hunks[hunk_index], new_start=new_start)
    return replace(file, hunks=tuple(hunks))


def pos(file: diffmodel.File, side: str, line: int) -> LinePos:
    found = gitpatch.locate_address(file, side, line)
    assert found is not None, (side, line)
    return found


def lines_plan(file, side_a, a, side_b, b, load, fresh, discard=False):
    """plan_lines for the lines between two (side, line) addresses of one
    hunk — the extension's anchor and cursor."""
    start, end = pos(file, side_a, a), pos(file, side_b, b)
    assert start.hunk == end.hunk
    return gitpatch.plan_lines(file, start.hunk, start.index, end.index, load, fresh, discard=discard)


def reason(plan) -> str:
    assert isinstance(plan, Refusal), plan
    return plan.reason


TWO_HUNKS = parse_one(TWO_HUNKS_TEXT)
THREE_HUNKS = parse_one(THREE_HUNKS_TEXT)
STAGING = parse_one(STAGING_TEXT)


# -- patch.test.ts: the parser, through diffmodel -----------------------------


def test_parse_reads_paths_change_kind_modes_and_hunk_geometry():
    patch = parse_one(MODIFIED)
    assert (patch.path, patch.kind) == ("src/app.ts", KIND_CHANGE)
    assert gitpatch.declared_modes(patch) == ["100644"]
    assert len(patch.hunks) == 1
    hunk = patch.hunks[0]
    assert (hunk.index, hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (0, 1, 3, 1, 4)
    assert [(line.kind, line.text) for line in hunk.lines] == [
        (CONTEXT, "const a = 1;"),
        (DEL, "const b = 2;"),
        (ADD, "const b = 3;"),
        (ADD, "const c = 4;"),
        (CONTEXT, "const d = 5;"),
    ]
    assert gitpatch.hunk_side_lines(hunk, OLD) == ["const a = 1;", "const b = 2;", "const d = 5;"]
    assert diffmodel.hunk_range(hunk, NEW) == (1, 4)


def test_an_empty_side_spans_one_line_and_an_omitted_count_is_one():
    deletion = parse_one("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -5,3 +4,0 @@\n-one\n-two\n-three\n")
    assert diffmodel.hunk_range(deletion.hunks[0], NEW) == (4, 4)
    single = parse_one("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -7 +7 @@\n-old\n+new\n")
    hunk = single.hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (7, 1, 7, 1)


def test_keeps_no_newline_markers_bare_empty_context_lines_and_carriage_returns():
    patch = parse_one(
        "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n\n-b\\r\n"
        "\\ No newline at end of file\n+B\\r\n\\ No newline at end of file\n"
    )
    hunk = patch.hunks[0]
    assert gitpatch.hunk_side_lines(hunk, NEW) == ["a", "", "B\\r"]
    assert hunk.lines[2].no_newline and hunk.lines[3].no_newline


def test_recognises_additions_deletions_renames_and_binaries():
    added = parse_one(
        "diff --git a/n b/n\nnew file mode 100644\n--- /dev/null\n+++ b/n\n@@ -0,0 +1,1 @@\n+hi\n"
    )
    assert (added.path, added.kind) == ("n", diffmodel.KIND_NEW)
    deleted = parse_one(
        "diff --git a/g b/g\ndeleted file mode 100644\n--- a/g\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-bye\n"
    )
    assert (deleted.path, deleted.kind) == ("g", diffmodel.KIND_DELETED)
    renamed = parse_one(
        "diff --git a/old b/new\nsimilarity index 90%\nrename from old\nrename to new\n"
        "--- a/old\n+++ b/new\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert (renamed.path, renamed.previous_path, renamed.kind) == ("new", "old", KIND_RENAME)
    binary = parse_one(
        "diff --git a/img.png b/img.png\nindex 1111111..2222222 100644\n"
        "Binary files a/img.png and b/img.png differ\n"
    )
    assert (binary.path, binary.kind, binary.hunks) == ("img.png", KIND_BINARY, ())


def test_collects_both_modes_of_a_mode_change():
    patch = parse_one(
        "diff --git a/s b/s\nold mode 100644\nnew mode 120000\n--- a/s\n+++ b/s\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert gitpatch.declared_modes(patch) == ["100644", "120000"]


def test_a_body_that_stops_short_or_holds_a_stray_line_is_unreadable():
    # diffmodel drops the hunk rather than throwing; unreadable_reason says so.
    short = parse_one("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n")
    assert short.hunks == ()
    assert gitpatch.unreadable_reason(short) == "hunk 1 could not be read"
    stray = parse_one("diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,1 +1,1 @@\n*a\n")
    assert gitpatch.unreadable_reason(stray) == "hunk 1 could not be read"
    assert gitpatch.unreadable_reason(TWO_HUNKS) is None
    assert gitpatch.unreadable_reason(parse_one(BINARY_PATCH)) is None


# -- patch.test.ts: writeSelectedHunks ----------------------------------------


def test_write_selected_hunks_keeps_the_header_verbatim_and_emits_only_the_selected_hunk():
    written = gitpatch.write_selected_hunks(TWO_HUNKS, {1})
    assert written is not None
    assert written.split("\n")[:4] == [
        "diff --git a/f.txt b/f.txt",
        "index 1111111..2222222 100644",
        "--- a/f.txt",
        "+++ b/f.txt",
    ]
    assert "-old\n+new\n" in written
    assert "NEW1" not in written


def test_write_selected_hunks_leaves_a_mode_change_out_of_the_header():
    text = (
        "diff --git a/f b/f\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\n"
        "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    )
    patch = parse_one(text)
    assert gitpatch.declared_modes(patch) == ["100644", "100755"]
    assert gitpatch.partial_header_lines(patch) == [
        "diff --git a/f b/f", "index 1111111..2222222", "--- a/f", "+++ b/f",
    ]
    assert gitpatch.write_selected_hunks(patch, {0}) == (
        "diff --git a/f b/f\nindex 1111111..2222222\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    )


def test_write_selected_hunks_renumbers_later_hunks_by_what_was_dropped():
    assert "@@ -15 +15 @@" in gitpatch.write_selected_hunks(TWO_HUNKS, {1})
    assert "@@ -15 +17 @@" in gitpatch.write_selected_hunks(TWO_HUNKS, {0, 1})
    assert "@@ -2 +2,3 @@ context heading" in gitpatch.write_selected_hunks(TWO_HUNKS, {0})
    # In reverse the target holds the new side, so it is the old-side start
    # that moves — by the two lines hunk 1 leaves in the target.
    assert "@@ -17 +17 @@" in gitpatch.write_selected_hunks(TWO_HUNKS, {1}, reverse=True)
    assert "@@ -2 +2,3 @@ context heading" in gitpatch.write_selected_hunks(TWO_HUNKS, {0}, reverse=True)


def test_write_selected_hunks_round_trips_a_whole_patch_and_preserves_a_no_newline_marker():
    text = (
        "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n one\n-two\n"
        "\\ No newline at end of file\n+two!\n\\ No newline at end of file\n"
    )
    assert gitpatch.write_selected_hunks(parse_one(text), {0}) == text


def test_write_selected_hunks_is_none_when_nothing_is_selected():
    assert gitpatch.write_selected_hunks(TWO_HUNKS, set()) is None


# -- patch.test.ts: findDisagreement and the guards ---------------------------


def test_find_disagreement_agrees_on_matching_counts_and_new_side_spans():
    assert gitpatch.find_disagreement(TWO_HUNKS, [(2, 4), (17, 17)]) is None
    assert gitpatch.find_disagreement(TWO_HUNKS, [None, None]) is None
    assert gitpatch.hunk_spans(TWO_HUNKS) == [(2, 4), (17, 17)]
    assert gitpatch.find_disagreement(TWO_HUNKS, gitpatch.hunk_spans(TWO_HUNKS)) is None


def test_find_disagreement_names_a_count_mismatch_or_a_moved_hunk():
    assert "1 hunk(s)" in gitpatch.find_disagreement(TWO_HUNKS, [(2, 4)])
    assert "hunk 2 spans lines 17-17" in gitpatch.find_disagreement(TWO_HUNKS, [(2, 4), (18, 18)])


def test_only_regular_files_may_be_staged_by_hunk():
    assert gitpatch.unsupported_mode_reason(["100644", "100755"]) is None
    assert "symbolic link" in gitpatch.unsupported_mode_reason(["120000"])
    assert "submodule" in gitpatch.unsupported_mode_reason(["160000"])
    assert "unrecognised" in gitpatch.unsupported_mode_reason(["123456"])


def test_paths_must_be_relative_and_inside_the_repository():
    assert gitpatch.unsafe_path_reason("src/app.ts") is None
    assert "does not name" in gitpatch.unsafe_path_reason("")
    assert "does not name" in gitpatch.unsafe_path_reason(None)
    assert "absolute" in gitpatch.unsafe_path_reason("/etc/passwd")
    assert "absolute" in gitpatch.unsafe_path_reason("C:\\x")
    assert "outside" in gitpatch.unsafe_path_reason("../x")
    assert "outside" in gitpatch.unsafe_path_reason("a\\..\\x")
    assert "dash" in gitpatch.unsafe_path_reason("-rf")
    assert "control" in gitpatch.unsafe_path_reason("a\nb")


# -- range.test.ts: locate ----------------------------------------------------


def test_locate_address_a_minus_line_answers_to_old_only_a_plus_line_to_new_only():
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 2) == at(0, 1)  # -b
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 2) == at(0, 2)  # +B
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 3) == at(0, 3)  # +B2
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 11) == at(1, 1)  # -k
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 12) == at(1, 2)  # +K
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 23) == at(2, 2)  # +U2


def test_locate_address_a_context_line_answers_to_either_side():
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 1) == at(0, 0)
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 1) == at(0, 0)
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 3) == at(0, 4)  # c
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 4) == at(0, 4)  # c, by its new number
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 22) == at(2, 3)  # v
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 24) == at(2, 3)


def test_locate_address_is_none_for_a_line_no_hunk_carries():
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 5) is None
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 6) is None
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 23) is None
    assert gitpatch.locate_address(THREE_HUNKS, NEW, 0) is None
    assert gitpatch.locate_address(THREE_HUNKS, OLD, 99) is None
    assert gitpatch.locate_address(THREE_HUNKS, "sideways", 1) is None
    assert gitpatch.locate_address(THREE_HUNKS, NEW, True) is None
    assert gitpatch.locate_address(parse_one(f"{HEADER}@@ -1 +1 @@\n-a\n+b\n"), NEW, 2) is None


# -- range.test.ts: positions, ranges and counts ------------------------------


def test_positions_order_by_hunk_then_index_and_a_range_takes_either_way_round():
    assert at(0, 5) < at(1, 0)
    assert at(1, 0) < at(1, 1)
    assert at(1, 1) == at(1, 1)
    assert gitpatch.order_range(at(2, 1), at(0, 3)) == span(at(0, 3), at(2, 1))
    assert gitpatch.order_range(at(0, 3), at(2, 1)) == span(at(0, 3), at(2, 1))
    assert at(1, 0) in span(at(0, 3), at(2, 1))
    assert at(2, 2) not in span(at(0, 3), at(2, 1))
    assert "x" not in span(at(0, 3), at(2, 1))
    assert gitpatch.hunk_lines_range(THREE_HUNKS, 1, 2, 0) == span(at(1, 0), at(1, 2))
    assert gitpatch.hunk_lines_range(THREE_HUNKS, 3, 0, 0) is None
    assert gitpatch.hunk_lines_range(THREE_HUNKS, 1, 0, 4) is None
    assert gitpatch.hunk_lines_range(THREE_HUNKS, 1, -1, 0) is None


def test_count_selected_counts_the_changed_lines_in_patch_order_across_hunks():
    assert gitpatch.count_selected(THREE_HUNKS, span(at(0, 1), at(1, 2))) == (3, 2)
    assert gitpatch.count_selected(THREE_HUNKS, span(at(0, 4), at(0, 5))) == (0, 0)
    assert gitpatch.count_selected(THREE_HUNKS, span(at(0, 0), at(2, 3))) == (4, 2)
    assert gitpatch.count_selected(THREE_HUNKS, {at(0, 1), at(2, 2)}) == (1, 1)


# -- range.test.ts: writeSelectedLines, forward -------------------------------


def test_write_lines_forward_keeps_the_selected_plus_drops_the_other_demotes_the_minus():
    assert gitpatch.write_lines(THREE_HUNKS, span(at(0, 2), at(0, 2))) == f"""{HEADER}@@ -1,4 +1,5 @@
 a
 b
+B
 c
 d
"""
    assert gitpatch.write_selected_lines(THREE_HUNKS, 0, 2, 2) == gitpatch.write_lines(
        THREE_HUNKS, span(at(0, 2), at(0, 2))
    )
    assert gitpatch.write_selected_lines(THREE_HUNKS, 9, 0, 0) is None


def test_write_lines_forward_renumbers_a_later_hunks_new_start_by_what_earlier_hunks_no_longer_add():
    # Hunk 1 is left out entirely (it added one line), so hunk 2 lands one earlier.
    assert gitpatch.write_lines(THREE_HUNKS, span(at(1, 2), at(1, 2))) == f"""{HEADER}@@ -10,3 +10,4 @@
 j
 k
+K
 l
"""
    # Hunk 1 left out, hunk 2 whole, hunk 3 partially: the shift is 1 from
    # hunk 1 and 0 from hunk 2 (nothing of it was lost).
    assert gitpatch.write_lines(THREE_HUNKS, span(at(1, 1), at(2, 2))) == f"""{HEADER}@@ -10,3 +10,3 @@
 j
-k
+K
 l
@@ -20,3 +20,4 @@
 t
 u
+U2
 v
"""
    # Hunk 1 trimmed to one of its two additions: `-b` demotes to context
    # and `+B` is dropped, so the hunk still nets one added line — the later
    # starts do not move.
    assert gitpatch.write_lines(THREE_HUNKS, span(at(0, 3), at(2, 2))) == f"""{HEADER}@@ -1,4 +1,5 @@
 a
 b
+B2
 c
 d
@@ -10,3 +11,3 @@
 j
-k
+K
 l
@@ -20,3 +21,4 @@
 t
 u
+U2
 v
"""


def test_a_trimmed_hunk_shifts_the_later_starts_by_what_its_dropped_lines_would_have_added():
    patch = parse_one(f"""{HEADER}@@ -1,5 +1,6 @@
 a
+A2
 b
-c
+C
 d
 e
@@ -10,2 +11,3 @@
 j
+J2
 k
""")
    # `+A2` is dropped: hunk 1 nets nothing now, and hunk 2 lands one earlier.
    assert gitpatch.write_lines(patch, span(at(0, 3), at(1, 1))) == f"""{HEADER}@@ -1,5 +1,5 @@
 a
 b
-c
+C
 d
 e
@@ -10,2 +10,3 @@
 j
+J2
 k
"""
    # In reverse `+A2` demotes to context and stays, so nothing shifts.
    assert gitpatch.write_lines(patch, span(at(0, 3), at(1, 1)), reverse=True) == f"""{HEADER}@@ -1,6 +1,6 @@
 a
 A2
 b
-c
+C
 d
 e
@@ -11,2 +11,3 @@
 j
+J2
 k
"""


def test_a_mode_change_in_the_header_stays_behind_the_lines_are_staged_the_mode_is_not():
    with_mode = parse_one(
        "diff --git a/f.txt b/f.txt\nold mode 100644\nnew mode 100755\nindex 1111111..2222222\n"
        "--- a/f.txt\n+++ b/f.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    )
    assert gitpatch.write_lines(with_mode, span(at(0, 1), at(0, 2))) == (
        "diff --git a/f.txt b/f.txt\nindex 1111111..2222222\n--- a/f.txt\n+++ b/f.txt\n"
        "@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    )
    assert "mode" not in gitpatch.write_lines(with_mode, span(at(0, 1), at(0, 2)), reverse=True)


def test_the_whole_patch_selected_is_the_patch_unchanged():
    whole = gitpatch.write_lines(THREE_HUNKS, span(at(0, 0), at(2, 3)))
    assert whole is not None
    assert parse_one(whole) == THREE_HUNKS


def test_a_range_over_context_only_makes_no_patch():
    assert gitpatch.write_lines(THREE_HUNKS, span(at(0, 4), at(1, 0))) is None
    assert gitpatch.write_lines(THREE_HUNKS, span(at(0, 4), at(1, 0)), reverse=True) is None


def test_a_hunk_header_count_of_one_is_written_without_the_comma_as_git_does():
    one_line = parse_one(f"{HEADER}@@ -1 +1,3 @@\n a\n+b\n+c\n")
    assert gitpatch.write_lines(one_line, span(at(0, 1), at(0, 1))) == f"{HEADER}@@ -1 +1,2 @@\n a\n+b\n"
    assert gitpatch.hunk_header(1, 1, 1, 3, "") == "@@ -1 +1,3 @@"
    assert gitpatch.hunk_header(2, 0, 2, 3, "def f():") == "@@ -2,0 +2,3 @@ def f():"


# -- range.test.ts: writeSelectedLines, reverse -------------------------------


def test_write_lines_reverse_keeps_the_selected_plus_drops_the_other_minus_demotes_the_other_plus():
    reverse = gitpatch.write_lines(THREE_HUNKS, span(at(0, 2), at(0, 2)), reverse=True)
    assert reverse == f"""{HEADER}@@ -1,4 +1,5 @@
 a
+B
 B2
 c
 d
"""
    # Hunk 1 (net +1) is left in the target, so hunk 2's old side starts one later.
    reverse = gitpatch.write_lines(THREE_HUNKS, span(at(1, 1), at(1, 1)), reverse=True)
    assert reverse == f"""{HEADER}@@ -11,4 +11,3 @@
 j
-k
 K
 l
"""


def test_write_lines_reverse_renumbers_a_later_hunks_old_start_by_what_earlier_hunks_no_longer_remove():
    reverse = gitpatch.write_lines(THREE_HUNKS, span(at(1, 2), at(1, 2)), reverse=True)
    assert reverse == f"""{HEADER}@@ -11,2 +11,3 @@
 j
+K
 l
"""
    last = gitpatch.write_lines(THREE_HUNKS, span(at(2, 2), at(2, 2)), reverse=True)
    assert last == f"""{HEADER}@@ -21,3 +21,4 @@
 t
 u
+U2
 v
"""


# -- range.test.ts: the no-newline marker -------------------------------------

EOF_TEXT = f"""{HEADER}@@ -1,2 +1,3 @@
 a
-b
\\ No newline at end of file
+b
+c
"""
EOF = parse_one(EOF_TEXT)


def test_the_marker_travels_with_its_line_when_the_line_is_kept():
    assert gitpatch.write_lines(EOF, span(at(0, 1), at(0, 2))) == f"""{HEADER}@@ -1,2 +1,2 @@
 a
-b
\\ No newline at end of file
+b
"""
    assert gitpatch.write_lines(EOF, span(at(0, 1), at(0, 1))) == f"""{HEADER}@@ -1,2 +1 @@
 a
-b
\\ No newline at end of file
"""


def test_the_marker_goes_with_its_line_when_the_line_is_dropped():
    assert gitpatch.write_lines(EOF, span(at(0, 3), at(0, 3)), reverse=True) == f"""{HEADER}@@ -1,2 +1,3 @@
 a
 b
+c
"""


def test_refuses_a_selection_that_would_put_the_marker_mid_file():
    # Forward, `+c` alone: `-b` demotes to context and keeps its marker,
    # then `+c` follows it on the new side — a file with a line after its last.
    with pytest.raises(RangeRefusal, match="select the whole end-of-file change"):
        gitpatch.write_lines(EOF, span(at(0, 3), at(0, 3)))
    # Reverse, `-b`/`+b` without `+c`: `+c` demotes to context after the marked `-b`.
    with pytest.raises(RangeRefusal):
        gitpatch.write_lines(EOF, span(at(0, 1), at(0, 2)), reverse=True)


def test_the_marker_stays_on_a_context_line_that_carries_it():
    tail = parse_one(f"{HEADER}@@ -1,3 +1,3 @@\n a\n-x\n+y\n z\n\\ No newline at end of file\n")
    assert gitpatch.write_lines(tail, span(at(0, 2), at(0, 2))) == (
        f"{HEADER}@@ -1,3 +1,4 @@\n a\n x\n+y\n z\n\\ No newline at end of file\n"
    )


def test_a_crlf_files_cr_survives_the_round_trip_on_kept_demoted_and_context_lines():
    crlf = parse_one(f"{HEADER}@@ -1,3 +1,3 @@\n a\r\n-b\r\n+B\r\n c\r\n")
    assert gitpatch.write_lines(crlf, span(at(0, 2), at(0, 2))) == (
        f"{HEADER}@@ -1,3 +1,4 @@\n a\r\n b\r\n+B\r\n c\r\n"
    )


# -- range.test.ts: sameHunks -------------------------------------------------


def test_same_hunks_is_none_for_the_same_hunks_whatever_the_headers_say():
    assert gitpatch.same_hunks(THREE_HUNKS, parse_one(THREE_HUNKS_TEXT)) is None
    renumbered = parse_one(THREE_HUNKS_TEXT.replace("@@ -20,3 +21,4 @@", "@@ -30,3 +31,4 @@"))
    assert gitpatch.same_hunks(THREE_HUNKS, renumbered) is None


def test_same_hunks_names_the_first_hunk_and_line_that_differ():
    edited = parse_one(THREE_HUNKS_TEXT.replace("+B2\n", "+B3\n"))
    assert gitpatch.same_hunks(THREE_HUNKS, edited) == (
        "hunk 1 differs: line 4 is `B2` in the view but `B3` on disk"
    )
    longer = parse_one(
        THREE_HUNKS_TEXT.replace("@@ -1,4 +1,5 @@", "@@ -1,4 +1,6 @@")
        .replace("+B2\n", "+B2\n+B3\n")
        .replace("@@ -10,3 +11,3 @@", "@@ -10,3 +12,3 @@")
        .replace("@@ -20,3 +21,4 @@", "@@ -20,3 +22,4 @@")
    )
    assert gitpatch.same_hunks(THREE_HUNKS, longer) == "hunk 1 has 6 lines in the view but 7 on disk"
    fewer = parse_one(f"{HEADER}@@ -1,4 +1,5 @@\n a\n-b\n+B\n+B2\n c\n d\n")
    assert gitpatch.same_hunks(THREE_HUNKS, fewer) == "the view shows 3 hunk(s) but the disk has 1"
    kinds = parse_one(THREE_HUNKS_TEXT.replace("-k\n+K\n", "+k\n-K\n"))
    assert gitpatch.same_hunks(THREE_HUNKS, kinds) == (
        "hunk 2 differs: line 2 is a removal in the view but an addition on disk"
    )


# -- staging.test.ts: planHunkToggle ------------------------------------------


def test_plan_hunk_stages_hunk_2_of_3_with_the_later_hunk_dropped_and_the_start_renumbered():
    plan = gitpatch.plan_hunk(STAGING, 1, UNSTAGED, STAGING_TEXT)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.confirm, plan.done, plan.lines, plan.hunk_index) == (
        OP_APPLY_CACHED, None, "Staged hunk 2 of f.txt", 3, 1
    )
    assert plan.patch == """diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -10,2 +10 @@
-x
-y
+z
"""
    assert gitpatch.describe(plan) == "Staged hunk 2 of f.txt"


def test_plan_hunk_on_the_staged_load_reverses_the_apply():
    plan = gitpatch.plan_hunk(STAGING, 0, STAGED, STAGING_TEXT)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done) == (OP_APPLY_CACHED_REVERSE, "Unstaged hunk 1 of f.txt")
    # Hunk 2 with hunk 1's two lines left in the index: the old side starts at 12.
    second = gitpatch.plan_hunk(STAGING, 1, STAGED, STAGING_TEXT)
    assert isinstance(second, Plan) and "@@ -12,2 +12 @@" in second.patch


def test_plan_hunk_refuses_binaries_oversized_files_and_a_binary_patch():
    assert "binary" in reason(gitpatch.plan_hunk(as_binary(STAGING), 0, UNSTAGED, ""))
    assert "too large" in reason(gitpatch.plan_hunk(as_too_large(STAGING), 0, UNSTAGED, ""))
    image = replace(parse_one(BINARY_PATCH), kind=KIND_CHANGE)
    assert "binary" in reason(gitpatch.plan_hunk(image, 0, UNSTAGED, BINARY_PATCH))


def test_plan_hunk_renames_and_untracked_files_fall_through_to_the_whole_file():
    untracked = as_untracked(replace(parse_one(
        "diff --git a/n.txt b/n.txt\nnew file mode 100644\n--- /dev/null\n+++ b/n.txt\n@@ -0,0 +1 @@\n+hi\n"
    ), path="n.txt"))
    staged_new = Plan(OP_ADD, ("n.txt",), None, None, "Staged n.txt")
    assert gitpatch.plan_hunk(untracked, 0, UNSTAGED, "") == staged_new
    rename = as_renamed(replace(STAGING, path="b"), "a")
    assert gitpatch.plan_hunk(rename, 0, STAGED, STAGING_TEXT) == Plan(
        OP_RESET, ("a", "b"), None, None, "Unstaged b"
    )
    fresh_new = replace(untracked, untracked=False)
    assert gitpatch.plan_hunk(fresh_new, 0, UNSTAGED, "\n") == staged_new


def test_plan_hunk_with_no_hunk_selected_means_the_whole_file_after_the_same_refusals():
    # The view selects no hunk in a binary or oversized file: the hunk
    # button there must say "use Stage file", not quietly become it.
    assert reason(gitpatch.plan_hunk(as_binary(STAGING), None, UNSTAGED, "")) == (
        "f.txt is binary: use Stage file"
    )
    assert reason(gitpatch.plan_hunk(as_too_large(STAGING), None, UNSTAGED, "")) == (
        "f.txt is too large to stage by hunk: use Stage file"
    )
    image = replace(parse_one(BINARY_PATCH), kind=KIND_CHANGE)
    assert "binary" in reason(gitpatch.plan_hunk(image, None, UNSTAGED, BINARY_PATCH))
    # The file header's button on a text file: the file.
    assert gitpatch.plan_hunk(STAGING, None, UNSTAGED, STAGING_TEXT) == Plan(
        OP_ADD, ("f.txt",), None, None, "Staged f.txt"
    )
    untracked = as_untracked(replace(STAGING, kind=diffmodel.KIND_NEW, path="n.txt"))
    assert gitpatch.plan_hunk(untracked, None, UNSTAGED, "") == Plan(
        OP_ADD, ("n.txt",), None, None, "Staged n.txt"
    )
    # A file whose view is stale is still fine whole: no hunk arithmetic is involved.
    stale = moved(STAGING, 0, 99)
    assert gitpatch.plan_hunk(stale, None, STAGED, STAGING_TEXT) == Plan(
        OP_RESET, ("f.txt",), None, None, "Unstaged f.txt"
    )


def test_plan_hunk_refuses_when_the_file_changed_since_the_view_loaded():
    plan = gitpatch.plan_hunk(moved(STAGING, 1, 13), 1, UNSTAGED, STAGING_TEXT)
    assert isinstance(plan, Refusal) and plan.stale
    assert plan.reason == (
        "f.txt changed since it was loaded, reloading "
        "(hunk 2 spans lines 12-12 in the patch but 13-13 in the view)"
    )
    fewer = gitpatch.plan_hunk(replace(STAGING, hunks=STAGING.hunks[:2]), 1, UNSTAGED, STAGING_TEXT)
    assert isinstance(fewer, Refusal) and fewer.stale


def test_plan_hunk_refuses_an_empty_patch_a_bad_index_an_unsafe_path_and_an_odd_mode():
    empty = gitpatch.plan_hunk(STAGING, 0, UNSTAGED, "")
    assert reason(empty) == "nothing to stage in f.txt: reloading" and empty.stale
    assert reason(gitpatch.plan_hunk(STAGING, 0, UNSTAGED, None)) == "nothing to stage in f.txt: reloading"
    assert reason(gitpatch.plan_hunk(STAGING, 3, UNSTAGED, STAGING_TEXT)) == "no hunk 4 in f.txt"
    assert reason(gitpatch.plan_hunk(STAGING, -1, UNSTAGED, STAGING_TEXT)) == "no hunk 0 in f.txt"
    escaped = STAGING_TEXT.replace("f.txt", "../f.txt")
    assert "outside" in reason(gitpatch.plan_hunk(replace(STAGING, path="../f.txt"), 0, UNSTAGED, escaped))
    link = STAGING_TEXT.replace("index 1111111..2222222 100644", "index 1111111..2222222 120000")
    assert reason(gitpatch.plan_hunk(STAGING, 0, UNSTAGED, link)) == (
        "cannot stage f.txt by hunk: it is a symbolic link, which hunks cannot describe"
    )
    truncated = "diff --git a/f.txt b/f.txt\n@@ -1,3 +1,3 @@\n a\n"
    unreadable = "cannot read the patch for f.txt"
    assert reason(gitpatch.plan_hunk(STAGING, 0, UNSTAGED, truncated)).startswith(unreadable)
    other = STAGING_TEXT.replace("f.txt", "g.txt") + STAGING_TEXT.replace("f.txt", "h.txt")
    assert reason(gitpatch.plan_hunk(STAGING, 0, UNSTAGED, other)).startswith(unreadable)


# -- staging.test.ts: planFileToggle ------------------------------------------


def test_plan_file_a_rename_names_both_paths_in_one_call():
    rename = as_renamed(replace(STAGING, path="b"), "a")
    assert gitpatch.plan_file(rename, UNSTAGED) == Plan(OP_ADD, ("a", "b"), None, None, "Staged b")
    assert gitpatch.plan_file(STAGING, STAGED) == Plan(OP_RESET, ("f.txt",), None, None, "Unstaged f.txt")
    assert "outside" in reason(gitpatch.plan_file(replace(STAGING, path="../f.txt"), UNSTAGED))
    assert "outside" in reason(gitpatch.plan_file(as_renamed(STAGING, "../a"), UNSTAGED))


def test_plan_file_discard_checks_out_trashes_or_restores_after_a_confirmation():
    tracked = gitpatch.plan_file(STAGING, UNSTAGED, discard=True)
    assert tracked == Plan(
        OP_CHECKOUT, ("f.txt",), None,
        "Discard the changes to f.txt? This cannot be undone.", "Discarded the changes to f.txt", lines=6,
    )
    new = as_untracked(replace(STAGING, kind=diffmodel.KIND_NEW))
    assert gitpatch.plan_file(new, UNSTAGED, discard=True) == Plan(
        OP_TRASH, ("f.txt",), None, "Move f.txt to the trash?", "Moved f.txt to the trash"
    )
    deleted = gitpatch.plan_file(replace(STAGING, kind=diffmodel.KIND_DELETED), UNSTAGED, discard=True)
    assert deleted == Plan(OP_CHECKOUT, ("f.txt",), None, "Restore f.txt from the index?", "Restored f.txt")
    assert "Unstaged view" in reason(gitpatch.plan_file(STAGING, STAGED, discard=True))
    assert "renamed" in reason(gitpatch.plan_file(as_renamed(STAGING, "e.txt"), UNSTAGED, discard=True))


def test_plan_file_on_a_read_only_load_reverts_the_whole_patch_after_a_confirmation():
    for load in ("branch", {"show": "abc123"}, {"range": "a...b"}):
        plan = gitpatch.plan_file(STAGING, load)
        assert plan == Plan(
            OP_APPLY_WORKTREE_REVERSE, ("f.txt",), STAGING_TEXT,
            "Revert f.txt in the working tree? This cannot be undone.", "Reverted f.txt", lines=6,
        )
        assert gitpatch.plan_file(STAGING, load, discard=True) == plan
    assert reason(gitpatch.plan_file(as_binary(STAGING), "branch")) == "f.txt is binary: use git from a shell"
    assert "too large" in reason(gitpatch.plan_file(as_too_large(STAGING), "branch"))
    assert reason(gitpatch.plan_file(replace(STAGING, patch=""), "branch")) == (
        "nothing to revert in f.txt: reloading"
    )


def test_describe_names_what_was_done():
    assert gitpatch.describe(Plan(OP_RESET, ("x",), None, None, "Unstaged x")) == "Unstaged x"
    assert gitpatch.describe(Refusal("why")) == "why"
    assert gitpatch.describe(Plan(OP_APPLY_CACHED, ("f.txt",), "", None, "Staged 3 lines of f.txt", 3)) == (
        "Staged 3 lines of f.txt"
    )
    assert gitpatch.describe(
        Plan(OP_APPLY_WORKTREE_REVERSE, ("f.txt",), "", "Discard?", "Discarded hunk 2 of f.txt", 2, 1)
    ) == "Discarded hunk 2 of f.txt"


# -- staging.test.ts: planRangeToggle -----------------------------------------


def test_plan_lines_stages_the_selected_lines_in_either_order():
    plan = lines_plan(STAGING, OLD, 10, NEW, 12, UNSTAGED, STAGING_TEXT)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done, plan.lines, plan.hunk_index) == (
        OP_APPLY_CACHED, "Staged 3 lines of f.txt", 3, None
    )
    assert plan.patch == """diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -10,2 +10 @@
-x
-y
+z
"""
    assert lines_plan(STAGING, NEW, 12, OLD, 10, UNSTAGED, STAGING_TEXT) == plan
    one = lines_plan(STAGING, OLD, 10, OLD, 10, UNSTAGED, STAGING_TEXT)
    assert isinstance(one, Plan) and (one.done, one.lines) == ("Staged 1 line of f.txt", 1)
    # `-y` demotes to context, `+z` is dropped.
    assert "@@ -10,2 +10 @@\n-x\n y\n" in one.patch


def test_plan_lines_on_the_staged_load_unstages_with_the_reverse_rule():
    plan = lines_plan(STAGING, OLD, 10, OLD, 10, STAGED, STAGING_TEXT)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done, plan.lines) == (OP_APPLY_CACHED_REVERSE, "Unstaged 1 line of f.txt", 1)
    # `-y` is dropped, `+z` demoted: the index keeps both. Hunk 1's two added
    # lines stay in the index too, so the old side starts at 12.
    assert "@@ -12,2 +12 @@\n-x\n z\n" in plan.patch


def test_plan_lines_refuses_in_order_binary_too_large_untracked_rename_empty_unreadable_new_or_deleted():
    def refusal(file=STAGING, fresh=STAGING_TEXT, load=UNSTAGED):
        return reason(gitpatch.plan_lines(file, 1, 0, 2, load, fresh))

    assert refusal(as_untracked(as_binary(STAGING))) == "f.txt is binary: use Stage file"
    assert refusal(as_untracked(as_too_large(STAGING))) == (
        "f.txt is too large to stage by hunk: use Stage file"
    )
    assert refusal(as_untracked(as_renamed(STAGING, "e.txt"))) == "f.txt is untracked: use Stage file"
    assert refusal(as_renamed(STAGING, "e.txt")) == "renames stage whole: use Stage file"
    assert refusal(replace(STAGING, kind=KIND_RENAME)) == "renames stage whole: use Stage file"
    assert refusal(fresh="\n") == "nothing to stage in f.txt: reloading"
    assert refusal(fresh="\n", load=STAGED) == "nothing to unstage in f.txt: reloading"
    assert refusal(fresh="diff --git a/f.txt b/f.txt\n@@ -1,3 +1,3 @@\n a\n").startswith(
        "cannot read the patch for f.txt"
    )
    binary = (
        "diff --git a/f.txt b/f.txt\nindex 1111111..2222222 100644\nBinary files a/f.txt and b/f.txt differ\n"
    )
    assert refusal(fresh=binary) == "f.txt is binary: use Stage file"
    # A fresh stanza for another path is not the file's, whatever else the
    # stream holds; an outside path is refused once the stanza is found.
    assert refusal(fresh=STAGING_TEXT.replace("f.txt", "../f.txt")) == (
        "cannot read the patch for f.txt: no stanza for it"
    )
    assert "outside" in refusal(
        file=replace(STAGING, path="../f.txt"), fresh=STAGING_TEXT.replace("f.txt", "../f.txt")
    )
    assert refusal(fresh=STAGING_TEXT.replace("100644", "120000")) == (
        "cannot stage f.txt by hunk: it is a symbolic link, which hunks cannot describe"
    )
    # A mode change rides along in the header but is not a line: the lines stage, the mode does not.
    mode_change = STAGING_TEXT.replace(
        "index 1111111..2222222 100644", "old mode 100644\nnew mode 100755\nindex 1111111..2222222"
    )
    with_mode = gitpatch.plan_lines(parse_one(mode_change), 1, 0, 2, UNSTAGED, mode_change)
    assert isinstance(with_mode, Plan) and with_mode.lines == 3
    assert with_mode.patch.split("\n")[:4] == [
        "diff --git a/f.txt b/f.txt", "index 1111111..2222222", "--- a/f.txt", "+++ b/f.txt",
    ]
    added = STAGING_TEXT.replace(
        "index 1111111..2222222 100644", "new file mode 100644\nindex 0000000..2222222"
    ).replace("--- a/f.txt", "--- /dev/null")
    assert refusal(fresh=added) == "f.txt is a new or deleted file: use Stage file"
    deleted = STAGING_TEXT.replace(
        "index 1111111..2222222 100644", "deleted file mode 100644\nindex 1111111..0000000"
    ).replace("+++ b/f.txt", "+++ /dev/null")
    assert refusal(fresh=deleted) == "f.txt is a new or deleted file: use Stage file"
    renamed = STAGING_TEXT.replace(
        "index 1111111..2222222 100644",
        "similarity index 90%\nrename from e.txt\nrename to f.txt\nindex 1111111..2222222 100644",
    )
    assert refusal(fresh=renamed) == "f.txt changed since it was loaded, reloading (it is a rename now)"


def test_plan_lines_refuses_when_the_spans_or_the_lines_no_longer_match_the_disk():
    def refusal(file=STAGING, fresh=STAGING_TEXT):
        plan = gitpatch.plan_lines(file, 1, 0, 2, UNSTAGED, fresh)
        assert isinstance(plan, Refusal) and plan.stale, plan
        return plan.reason

    assert refusal(moved(STAGING, 1, 13)) == (
        "f.txt changed since it was loaded, reloading "
        "(hunk 2 spans lines 12-12 in the patch but 13-13 in the view)"
    )
    # Same spans, a line's text changed: only same_hunks sees it.
    assert refusal(parse_one(STAGING_TEXT.replace("-y\n", "-Y\n"))) == (
        "f.txt changed since it was loaded, reloading "
        "(hunk 2 differs: line 2 is `Y` in the view but `y` on disk)"
    )
    # A view with no hunks in it is a count mismatch; so is a truncated one
    # (its dropped hunks are missing from the count).
    assert refusal(replace(STAGING, hunks=())) == (
        "f.txt changed since it was loaded, reloading (the view shows 0 hunk(s) where the patch has 3)"
    )
    assert refusal(parse_one("diff --git a/f.txt b/f.txt\n@@ -1,3 +1,3 @@\n a\n")) == (
        "f.txt changed since it was loaded, reloading (the view shows 0 hunk(s) where the patch has 3)"
    )


def test_plan_lines_a_view_whose_own_patch_has_a_hunk_it_could_not_read_is_refused():
    # Three good hunks and a fourth the parser dropped: the spans agree, the text does not.
    garbled = parse_one(STAGING_TEXT + "@@ -30,2 +32,2 @@\n r\n")
    assert len(garbled.hunks) == 3
    plan = gitpatch.plan_lines(garbled, 1, 0, 2, UNSTAGED, STAGING_TEXT)
    assert reason(plan) == "cannot read the view's patch for f.txt: hunk 4 could not be read"


def test_plan_lines_refuses_a_selection_outside_the_hunk_and_one_with_no_change_in_it():
    # The extension's "anchor is not on a diff line": old 5 is in no hunk.
    assert gitpatch.locate_address(STAGING, OLD, 5) is None
    # `+z` is new 12; there is no old 12 in hunk 2 (old 10-11) and old 12 is outside every hunk.
    assert gitpatch.locate_address(STAGING, OLD, 12) is None
    assert gitpatch.plan_lines(STAGING, 1, 0, 3, UNSTAGED, STAGING_TEXT) == Refusal(
        "the selection is not inside hunk 2 of f.txt"
    )
    assert gitpatch.plan_lines(STAGING, 1, -1, 0, UNSTAGED, STAGING_TEXT) == Refusal(
        "the selection is not inside hunk 2 of f.txt"
    )
    assert gitpatch.plan_lines(STAGING, 5, 0, 0, UNSTAGED, STAGING_TEXT) == Refusal("no hunk 6 in f.txt")
    # The context line `b`, addressed from either side: nothing changed in it.
    assert lines_plan(STAGING, NEW, 2, OLD, 2, UNSTAGED, STAGING_TEXT) == Refusal(
        "no changes in the selection"
    )


def test_plan_lines_refuses_to_split_an_end_of_file_change():
    plan = gitpatch.plan_lines(EOF, 0, 3, 3, UNSTAGED, EOF_TEXT)
    assert plan == Refusal("select the whole end-of-file change")


# -- staging.test.ts: planDiscard ---------------------------------------------


def test_discard_a_hunk_is_the_reversed_patch_with_the_other_hunks_effect_on_the_old_side_accounted_for():
    plan = gitpatch.plan_hunk(STAGING, 1, UNSTAGED, STAGING_TEXT, discard=True)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done, plan.lines, plan.hunk_index) == (
        OP_APPLY_WORKTREE_REVERSE, "Discarded hunk 2 of f.txt", 3, 1
    )
    assert plan.confirm == "Discard hunk 2 in f.txt? This cannot be undone."
    # Hunk 1 added two lines that stay in the working tree: hunk 2's old side moves down by two.
    assert plan.patch == """diff --git a/f.txt b/f.txt
index 1111111..2222222 100644
--- a/f.txt
+++ b/f.txt
@@ -12,2 +12 @@
-x
-y
+z
"""
    assert gitpatch.describe(plan) == "Discarded hunk 2 of f.txt"


def test_discard_a_selection_is_the_same_writer_in_reverse():
    plan = lines_plan(STAGING, OLD, 11, NEW, 12, UNSTAGED, STAGING_TEXT, discard=True)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done, plan.lines, plan.hunk_index) == (
        OP_APPLY_WORKTREE_REVERSE, "Discarded 2 lines of f.txt", 2, None
    )
    assert plan.confirm == "Discard 2 lines in f.txt? This cannot be undone."
    # `-x` is outside the selection and, in reverse, is dropped: `x` is not put back.
    assert "@@ -12 +12 @@\n-y\n+z\n" in plan.patch


def test_discard_refuses_the_staged_load_before_anything_else():
    assert gitpatch.plan_hunk(as_binary(STAGING), 1, STAGED, STAGING_TEXT, discard=True) == Refusal(
        "Discard works on the working tree: load the Unstaged view"
    )
    assert gitpatch.plan_lines(as_binary(STAGING), 1, 0, 0, STAGED, STAGING_TEXT, discard=True) == Refusal(
        "Discard works on the working tree: load the Unstaged view"
    )


def test_discard_a_deleted_file_is_restored_whole_by_the_views_word_without_a_parse_or_by_the_patchs():
    restore = Plan(OP_CHECKOUT, ("f.txt",), None, "Restore f.txt from the index?", "Restored f.txt")
    gone = replace(STAGING, kind=diffmodel.KIND_DELETED, hunks=())
    binary_gone = "Binary files a/f.txt and /dev/null differ\n"
    assert gitpatch.plan_hunk(gone, 1, UNSTAGED, binary_gone, discard=True) == restore
    # A deleted binary parses as a binary placeholder; its header still says deleted.
    binary_deletion = parse_one(
        "diff --git a/f.txt b/f.txt\ndeleted file mode 100644\nindex 1111111..0000000\n"
        "Binary files a/f.txt and /dev/null differ\n"
    )
    assert binary_deletion.kind == KIND_BINARY and gitpatch.is_deletion(binary_deletion)
    assert not gitpatch.is_deletion(STAGING)
    assert gitpatch.plan_hunk(binary_deletion, None, UNSTAGED, binary_deletion.patch, discard=True) == restore
    assert gitpatch.plan_file(binary_deletion, UNSTAGED, discard=True) == restore
    # Already back on disk: the view is behind.
    assert gitpatch.plan_hunk(gone, 1, UNSTAGED, "", discard=True) == Refusal(
        "nothing to discard in f.txt: reloading", stale=True
    )
    deleted = STAGING_TEXT.replace(
        "index 1111111..2222222 100644", "deleted file mode 100644\nindex 1111111..0000000"
    ).replace("+++ b/f.txt", "+++ /dev/null")
    assert gitpatch.plan_hunk(STAGING, 0, UNSTAGED, deleted, discard=True) == restore
    assert gitpatch.describe(restore) == "Restored f.txt"
    # A selection in it is not a restore: the whole file is.
    assert gitpatch.plan_lines(STAGING, 1, 0, 1, UNSTAGED, deleted, discard=True) == Refusal(
        "f.txt is deleted: Discard file restores it whole"
    )
    # The staged load still comes first, and so does the refusal of the wrong kind of file.
    assert isinstance(gitpatch.plan_hunk(gone, 1, STAGED, STAGING_TEXT, discard=True), Refusal)


def test_discard_refuses_anything_but_a_plain_modification_and_a_hunk_that_is_not_there():
    def refusal(file=STAGING, hunk_index=1, fresh=STAGING_TEXT, lines=None):
        if lines is None:
            plan = gitpatch.plan_hunk(file, hunk_index, UNSTAGED, fresh, discard=True)
        else:
            plan = gitpatch.plan_lines(file, hunk_index, *lines, UNSTAGED, fresh, discard=True)
        return reason(plan)

    whole_only = (
        "discard reverts changes inside a modified file: use git from a shell for a new or renamed file"
    )
    assert refusal(as_binary(STAGING)) == "f.txt is binary: use git from a shell"
    assert refusal(as_too_large(STAGING)) == "f.txt is too large to discard by hunk: use git from a shell"
    assert refusal(as_untracked(STAGING)) == "f.txt is untracked: Discard file moves it to the trash"
    assert refusal(as_renamed(STAGING, "e.txt")) == whole_only
    added = STAGING_TEXT.replace(
        "index 1111111..2222222 100644", "new file mode 100644\nindex 0000000..2222222"
    ).replace("--- a/f.txt", "--- /dev/null")
    assert refusal(fresh=added) == whole_only
    assert refusal(fresh="") == "nothing to discard in f.txt: reloading"
    assert refusal(hunk_index=None) == "select a hunk: Discard hunk takes a hunk, Discard lines a selection"
    assert refusal(hunk_index=3) == "no hunk 4 in f.txt"
    assert refusal(parse_one(STAGING_TEXT.replace("+z\n", "+Z\n"))).startswith(
        "f.txt changed since it was loaded, reloading (hunk 2 differs"
    )
    assert refusal(lines=(0, 9)) == "the selection is not inside hunk 2 of f.txt"
    assert refusal(hunk_index=0, lines=(0, 0)) == "no changes in the selection"


# -- revert from a read-only load (native only; no extension counterpart) -----


def test_revert_a_hunk_or_lines_from_a_commit_is_a_reverse_apply_in_the_working_tree_after_a_confirmation():
    load = {"show": "abc123"}
    plan = gitpatch.plan_hunk(STAGING, 1, load, STAGING_TEXT)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done, plan.lines, plan.hunk_index) == (
        OP_APPLY_WORKTREE_REVERSE, "Reverted hunk 2 of f.txt", 3, 1
    )
    assert plan.confirm == "Revert hunk 2 of f.txt in the working tree? This cannot be undone."
    assert "@@ -12,2 +12 @@\n-x\n-y\n+z\n" in plan.patch
    lines = gitpatch.plan_lines(STAGING, 1, 1, 2, load, STAGING_TEXT)
    assert isinstance(lines, Plan)
    assert (lines.op, lines.done, lines.lines) == (OP_APPLY_WORKTREE_REVERSE, "Reverted 2 lines of f.txt", 2)
    assert lines.confirm == "Revert 2 lines of f.txt in the working tree? This cannot be undone."
    assert "@@ -12 +12 @@\n-y\n+z\n" in lines.patch
    assert gitpatch.plan_hunk(STAGING, 1, load, STAGING_TEXT, discard=True) == plan
    assert reason(gitpatch.plan_hunk(as_binary(STAGING), 1, load, "")) == (
        "f.txt is binary: use git from a shell"
    )
    assert reason(gitpatch.plan_hunk(as_too_large(STAGING), 1, load, "")) == (
        "f.txt is too large to revert by hunk: use Revert file"
    )
    assert reason(gitpatch.plan_hunk(as_renamed(STAGING, "e.txt"), 1, load, STAGING_TEXT)) == (
        "renames revert whole: use Revert file"
    )
    assert reason(gitpatch.plan_hunk(STAGING, None, "branch", STAGING_TEXT)) == (
        "select a hunk: Revert hunk takes a hunk, Revert lines a selection"
    )
    assert reason(gitpatch.plan_hunk(STAGING, 4, "branch", STAGING_TEXT)) == "no hunk 5 in f.txt"
    assert reason(gitpatch.plan_lines(STAGING, 0, 0, 0, "branch", STAGING_TEXT)) == (
        "no changes in the selection"
    )
    deleted = STAGING_TEXT.replace(
        "index 1111111..2222222 100644", "deleted file mode 100644\nindex 1111111..0000000"
    ).replace("+++ b/f.txt", "+++ /dev/null")
    assert reason(gitpatch.plan_hunk(STAGING, 0, "branch", deleted)) == (
        "f.txt is a new or deleted file: use Revert file"
    )
    assert reason(gitpatch.plan_hunk(STAGING, 0, "branch", "")) == "nothing to revert in f.txt: reloading"


def test_a_fresh_stanza_for_another_path_is_never_taken_as_the_files():
    """parse_file_patch answers only the stanza whose path is the one asked
    for — a lone stanza for another file used to be accepted, and the
    planners then wrote a patch for that file under a confirm naming this
    one. Every planner path refuses without a plan."""
    other = STAGING_TEXT.replace("f.txt", "g.txt")
    assert gitpatch.parse_file_patch(other, "f.txt") is None
    assert gitpatch.parse_file_patch(other, "g.txt").path == "g.txt"
    assert gitpatch.parse_file_patch(other + STAGING_TEXT, "f.txt").path == "f.txt"
    # A rename re-read names the new path; the old one alone is not it.
    renamed = STAGING_TEXT.replace(
        "index 1111111..2222222 100644",
        "similarity index 90%\nrename from e.txt\nrename to f.txt\nindex 1111111..2222222 100644",
    )
    assert gitpatch.parse_file_patch(renamed, "f.txt").previous_path == "e.txt"
    assert gitpatch.parse_file_patch(renamed, "e.txt") is None
    none = "cannot read the patch for f.txt: no stanza for it"
    assert reason(gitpatch.plan_hunk(STAGING, 1, UNSTAGED, other)) == none
    assert reason(gitpatch.plan_hunk(STAGING, None, UNSTAGED, other)) == none
    assert reason(gitpatch.plan_lines(STAGING, 1, 0, 2, STAGED, other)) == none
    assert reason(gitpatch.plan_hunk(STAGING, 1, UNSTAGED, other, discard=True)) == none
    assert reason(gitpatch.plan_lines(STAGING, 1, 0, 2, UNSTAGED, other, discard=True)) == none
    assert reason(gitpatch.plan_hunk(STAGING, 1, {"show": "abc123"}, other)) == none
    assert reason(gitpatch.plan_lines(STAGING, 1, 0, 2, "branch", other)) == none
    # The last check before a patch is written: a File whose path is not
    # the shown one is refused as a change, whichever way it got there.
    shown = replace(STAGING, path="g.txt")
    refused = gitpatch._same_file(shown, STAGING, gitpatch._Wording("stage"))
    assert refused == Refusal(
        "g.txt changed since it was loaded, reloading (the patch names f.txt)", stale=True
    )
    assert gitpatch._same_file(STAGING, STAGING, gitpatch._Wording("stage")) is None


def test_a_revert_of_a_dirty_file_opens_its_confirmation_with_the_conflict_warning():
    load = {"show": "abc123"}
    warning = "f.txt has unstaged changes in the working tree; reverting may conflict."
    assert gitpatch.revert_warning("f.txt") == warning
    clean = gitpatch.plan_file(STAGING, load)
    dirty = gitpatch.plan_file(STAGING, load, dirty=True)
    assert dirty == replace(clean, confirm=f"{warning} {clean.confirm}")
    hunk = gitpatch.plan_hunk(STAGING, 1, load, STAGING_TEXT, dirty=True)
    assert hunk.confirm == f"{warning} Revert hunk 2 of f.txt in the working tree? This cannot be undone."
    clean_hunk = gitpatch.plan_hunk(STAGING, 1, load, STAGING_TEXT)
    assert replace(hunk, confirm=None) == replace(clean_hunk, confirm=None)
    lines = gitpatch.plan_lines(STAGING, 1, 1, 2, load, STAGING_TEXT, dirty=True)
    assert lines.confirm == f"{warning} Revert 2 lines of f.txt in the working tree? This cannot be undone."
    # The flag means nothing to the working-tree loads: a stage asks
    # nothing, a discard's question is its own.
    assert gitpatch.plan_hunk(STAGING, 1, UNSTAGED, STAGING_TEXT, dirty=True).confirm is None
    assert gitpatch.plan_file(STAGING, UNSTAGED, discard=True, dirty=True).confirm == (
        "Discard the changes to f.txt? This cannot be undone."
    )
    discard = gitpatch.plan_lines(STAGING, 1, 1, 2, UNSTAGED, STAGING_TEXT, discard=True, dirty=True)
    assert discard.confirm == "Discard 2 lines in f.txt? This cannot be undone."


# -- anchor.test.ts: the selection across a reload ----------------------------


def test_select_keys_the_selection_by_its_hunk_and_rebind_follows_the_hunk_to_its_new_index():
    selection = gitpatch.select(THREE_HUNKS, 1, 2, 0, UNSTAGED)
    assert selection is not None
    assert (selection.path, selection.hunk_index, selection.first, selection.last, selection.load) == (
        "f.txt", 1, 0, 2, UNSTAGED
    )
    assert selection.hunk_key == diffmodel.stable_key(THREE_HUNKS, THREE_HUNKS.hunks[1])
    assert gitpatch.select(THREE_HUNKS, 3, 0, 0, UNSTAGED) is None
    assert gitpatch.select(THREE_HUNKS, 1, 0, 9, UNSTAGED) is None
    # The same file shows the same hunk after a reload, behind another file
    # and an extra hunk of its own: the selection follows it to index 2.
    other = parse_one(THREE_HUNKS_TEXT.replace("f.txt", "g.txt"))
    grown = parse_one(THREE_HUNKS_TEXT.replace("@@ -1,4 +1,5 @@", "@@ -0,0 +1 @@\n+top\n@@ -1,4 +2,5 @@"))
    assert len(grown.hunks) == 4 and grown.hunks[2].lines == THREE_HUNKS.hunks[1].lines
    assert gitpatch.rebind_selection(selection, [other, grown], UNSTAGED) == replace(selection, hunk_index=2)
    assert gitpatch.rebind_selection(selection, [THREE_HUNKS], UNSTAGED) == selection


def test_rebind_drops_the_selection_when_the_hunk_changed_the_file_is_gone_or_another_load_shows():
    selection = gitpatch.select(THREE_HUNKS, 1, 1, 2, UNSTAGED)
    assert selection is not None
    edited = parse_one(THREE_HUNKS_TEXT.replace(" l\n", " l!\n"))
    assert gitpatch.rebind_selection(selection, [edited], UNSTAGED) is None
    # A hunk that moved keeps its lines but not its spans: a new selection is needed.
    shifted = parse_one(THREE_HUNKS_TEXT.replace("@@ -10,3 +11,3 @@", "@@ -10,3 +12,3 @@"))
    assert gitpatch.rebind_selection(selection, [shifted], UNSTAGED) is None
    gone = parse_one(THREE_HUNKS_TEXT.replace("f.txt", "g.txt"))
    assert gitpatch.rebind_selection(selection, [gone], UNSTAGED) is None
    assert gitpatch.rebind_selection(selection, [THREE_HUNKS], STAGED) is None
    assert gitpatch.rebind_selection(selection, [THREE_HUNKS], None) is None
    shown = gitpatch.select(THREE_HUNKS, 1, 1, 2, {"show": "abc"})
    assert gitpatch.rebind_selection(shown, [THREE_HUNKS], {"show": "abc"}) == shown
    assert gitpatch.rebind_selection(shown, [THREE_HUNKS], {"show": "abd"}) is None


# -- the integration suites, against a real index -----------------------------

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git isn't on PATH")



def _run(cwd: Path, *args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    # Bytes, not text: universal newlines would fold a CRLF patch's `\r`.
    result = subprocess.run(
        ["git", *args], cwd=cwd, input=None if stdin is None else stdin.encode(), capture_output=True
    )
    if check and result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr.decode()}")
    return result


def _git(cwd: Path, *args: str) -> str:
    return _run(cwd, *args).stdout.decode()


def _file_patch(cwd: Path, path: str = "f.txt", staged: bool = False, previous: str | None = None) -> str:
    """The re-read the view makes at action time — gitops.file_patch, the
    prefixes pinned whatever the repository's diff config says."""
    text = gitops.file_patch(cwd, STAGED if staged else UNSTAGED, path, previous)
    assert text is not None, f"file_patch({path}, staged={staged}) couldn't be read"
    return text


def _apply(cwd: Path, plan) -> bool:
    """Carry an apply plan out the way the view does: gitops.apply_patch."""
    assert isinstance(plan, Plan) and plan.patch is not None, plan
    cached = plan.op in (OP_APPLY_CACHED, OP_APPLY_CACHED_REVERSE)
    result = gitops.apply_patch(cwd, plan.patch, cached=cached, reverse=plan.op != OP_APPLY_CACHED)
    assert result.ok, result.stderr
    return True


def _run_plan(cwd: Path, plan) -> None:
    """Carry a whole-file plan out the way the view does, through gitops."""
    assert isinstance(plan, Plan), plan
    if plan.op == OP_ADD:
        result = gitops.stage_paths(cwd, plan.paths)
    elif plan.op == OP_RESET:
        result = gitops.unstage_paths(cwd, plan.paths)
    elif plan.op == OP_CHECKOUT:
        result = gitops.checkout_paths(cwd, plan.paths)
    else:
        _apply(cwd, plan)
        return
    assert result.ok, result.stderr


def _index(cwd: Path, path: str = "f.txt") -> str:
    return _git(cwd, "show", f":{path}")


def _read(cwd: Path, path: str) -> str:
    return (cwd / path).read_bytes().decode()


def _write(cwd: Path, path: str, content: str) -> None:
    (cwd / path).parent.mkdir(parents=True, exist_ok=True)
    (cwd / path).write_bytes(content.encode())


def _loaded(cwd: Path, path: str = "f.txt", staged: bool = False) -> tuple[diffmodel.File, str]:
    """The file the way the view holds it — our own parse of the patch — and
    the patch text a re-read hands the planner."""
    text = _file_patch(cwd, path, staged)
    return parse_one(text), text


def _added_lines(text: str) -> list[list[str]]:
    return [[line.text for line in hunk.lines if line.kind == ADD] for hunk in parse_one(text).hunks]


def _lines(count: int) -> list[str]:
    return [f"line {i + 1}" for i in range(count)]


THIRTY = "\n".join(_lines(30)) + "\n"
TWO_ENDS = THIRTY.replace("line 1\n", "line 1 changed\n").replace("line 30\n", "line 30 changed\n")


@pytest.fixture
def repo(tmp_path):
    """main at one commit (`first`: f.txt, thirty lines; old.txt, two)."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    _write(root, "f.txt", THIRTY)
    _write(root, "old.txt", "keep me around\nfor a while\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    return root


# range.integration.test.ts


@needs_git
def test_lines_stage_two_of_a_hunks_five_added_lines_the_other_three_stay_in_the_working_tree(repo):
    inserted = "\n".join([*_lines(10), "new 1", "new 2", "new 3", "new 4", "new 5", *_lines(30)[10:]]) + "\n"
    _write(repo, "f.txt", inserted)
    file, text = _loaded(repo)
    plan = lines_plan(file, NEW, 12, NEW, 13, UNSTAGED, text)
    assert isinstance(plan, Plan) and (plan.op, plan.done, plan.lines) == (
        OP_APPLY_CACHED, "Staged 2 lines of f.txt", 2
    )
    _apply(repo, plan)
    assert _index(repo) == "\n".join([*_lines(10), "new 2", "new 3", *_lines(30)[10:]]) + "\n"
    # What is left in the working tree still reads as one clean hunk.
    assert _added_lines(_file_patch(repo)) == [["new 1", "new 4", "new 5"]]
    assert gitops.read_status(repo) == Status(
        unstaged=(StatusRow("f.txt", "M"),), staged=(StatusRow("f.txt", "M"),)
    )


@needs_git
def test_lines_on_the_staged_load_unstage_a_range_the_reversed_partial_patch_against_the_cached_patch(repo):
    inserted = "\n".join([*_lines(10), "new 1", "new 2", "new 3", "new 4", "new 5", *_lines(30)[10:]]) + "\n"
    _write(repo, "f.txt", inserted)
    _git(repo, "add", "f.txt")
    file, text = _loaded(repo, staged=True)
    plan = lines_plan(file, NEW, 13, NEW, 12, STAGED, text)
    assert isinstance(plan, Plan) and (plan.op, plan.done, plan.lines) == (
        OP_APPLY_CACHED_REVERSE, "Unstaged 2 lines of f.txt", 2
    )
    _apply(repo, plan)
    assert _index(repo) == "\n".join([*_lines(10), "new 1", "new 4", "new 5", *_lines(30)[10:]]) + "\n"
    assert _added_lines(_file_patch(repo)) == [["new 2", "new 3"]]
    assert _read(repo, "f.txt") == inserted


@needs_git
def test_a_selection_in_one_hunk_leaves_the_others_for_a_clean_hunk_level_stage_afterwards(repo):
    edited = (
        THIRTY.replace("line 3\n", "line 3 changed\n")
        .replace("line 15\n", "line 15 changed\nline 15 extra\n")
        .replace("line 27\n", "line 27 changed\n")
    )
    _write(repo, "f.txt", edited)
    file, text = _loaded(repo)
    assert len(file.hunks) == 3
    # The extension's range ran from hunk 1's `-` to hunk 2's extra line; a
    # selection is one hunk, so this is hunk 2 whole (either order in) and
    # then hunk 1 by its button.
    plan = lines_plan(file, NEW, 16, OLD, 15, UNSTAGED, text)
    assert isinstance(plan, Plan) and (plan.done, plan.lines) == ("Staged 3 lines of f.txt", 3)
    _apply(repo, plan)
    # The view still shows three hunks; the disk has two: the button refuses and asks for a reload.
    first = gitpatch.plan_hunk(file, 0, UNSTAGED, _file_patch(repo))
    assert isinstance(first, Refusal) and first.stale
    file, text = _loaded(repo)
    assert len(file.hunks) == 2
    _apply(repo, gitpatch.plan_hunk(file, 0, UNSTAGED, text))
    assert _index(repo) == THIRTY.replace("line 3\n", "line 3 changed\n").replace(
        "line 15\n", "line 15 changed\nline 15 extra\n"
    )
    # The third hunk is the only one left, and stages whole the ordinary way.
    file, text = _loaded(repo)
    assert len(file.hunks) == 1
    hunk_plan = gitpatch.plan_hunk(file, 0, UNSTAGED, text)
    assert isinstance(hunk_plan, Plan) and hunk_plan.op == OP_APPLY_CACHED
    _apply(repo, hunk_plan)
    assert _index(repo) == edited
    assert gitops.read_status(repo).unstaged == ()


@needs_git
def test_a_selection_of_context_lines_only_is_refused_and_the_index_is_left_alone(repo):
    _write(repo, "f.txt", THIRTY.replace("line 15\n", "line 15 changed\n"))
    file, text = _loaded(repo)
    assert lines_plan(file, NEW, 12, NEW, 13, UNSTAGED, text) == Refusal("no changes in the selection")
    assert _git(repo, "diff", "--cached") == ""
    # As is an address nothing carries: line 40 is past the file.
    assert gitpatch.locate_address(file, OLD, 40) is None
    assert gitpatch.plan_lines(file, 0, 0, 40, UNSTAGED, text) == Refusal(
        "the selection is not inside hunk 1 of f.txt"
    )


@needs_git
def test_discard_a_selection_reverts_just_those_lines_in_the_working_tree_the_index_untouched(repo):
    edited = THIRTY.replace("line 5\n", "line 5 changed\n").replace(
        "line 7\n", "line 7 changed\nline 7 extra\n"
    )
    _write(repo, "f.txt", edited)
    file, text = _loaded(repo)
    plan = lines_plan(file, OLD, 7, NEW, 8, UNSTAGED, text, discard=True)
    assert isinstance(plan, Plan) and (plan.op, plan.done, plan.lines, plan.hunk_index) == (
        OP_APPLY_WORKTREE_REVERSE, "Discarded 3 lines of f.txt", 3, None
    )
    _apply(repo, plan)
    assert _read(repo, "f.txt") == THIRTY.replace("line 5\n", "line 5 changed\n")
    assert _git(repo, "diff", "--cached") == ""


@needs_git
def test_discard_the_added_lines_alone_removes_them_without_restoring_the_line_they_replaced(repo):
    _write(repo, "f.txt", THIRTY.replace("line 7\n", "line 7 changed\nline 7 extra\n"))
    file, text = _loaded(repo)
    plan = lines_plan(file, NEW, 7, NEW, 8, UNSTAGED, text, discard=True)
    assert isinstance(plan, Plan) and (plan.done, plan.lines) == ("Discarded 2 lines of f.txt", 2)
    _apply(repo, plan)
    assert _read(repo, "f.txt") == THIRTY.replace("line 7\n", "")


@needs_git
def test_discard_a_hunk_reverts_the_hunk_and_no_other_with_the_earlier_hunk_left_in_place(repo):
    edited = THIRTY.replace("line 3\n", "line 3 changed\nline 3 extra\n").replace(
        "line 20\n", "line 20 changed\n"
    )
    _write(repo, "f.txt", edited)
    file, text = _loaded(repo)
    plan = gitpatch.plan_hunk(file, 1, UNSTAGED, text, discard=True)
    assert isinstance(plan, Plan) and (plan.done, plan.lines, plan.hunk_index) == (
        "Discarded hunk 2 of f.txt", 2, 1
    )
    # git wrote `@@ -17,7 +18,7 @@`; with hunk 1 (net +1) left in the
    # working tree, the old side of the reversed patch starts one later.
    assert "@@ -18,7 +18,7 @@" in plan.patch
    _apply(repo, plan)
    assert _read(repo, "f.txt") == THIRTY.replace("line 3\n", "line 3 changed\nline 3 extra\n")


@needs_git
def test_a_whole_end_of_file_change_round_trips_a_split_of_it_is_refused(repo):
    _write(repo, "f.txt", THIRTY.rstrip("\n"))
    _git(repo, "commit", "-qam", "no trailing newline")
    _write(repo, "f.txt", f"{THIRTY}line 31\n")
    file, text = _loaded(repo)
    assert "\\ No newline at end of file" in text
    # Only the new last line: the newline added to line 30 cannot be left behind.
    assert lines_plan(file, NEW, 31, NEW, 31, UNSTAGED, text) == Refusal(
        "select the whole end-of-file change"
    )
    assert _git(repo, "diff", "--cached") == ""
    # The whole change, as a selection: the marker travels and git accepts it.
    plan = lines_plan(file, OLD, 30, NEW, 31, UNSTAGED, text)
    assert isinstance(plan, Plan) and plan.lines == 3
    _apply(repo, plan)
    assert _index(repo) == f"{THIRTY}line 31\n"
    assert gitops.read_status(repo).unstaged == ()
    # Just the newline on line 30 is a legitimate partial: line 31 stays unstaged.
    _git(repo, "reset", "-q")
    newline = lines_plan(file, OLD, 30, NEW, 30, UNSTAGED, text)
    assert isinstance(newline, Plan) and newline.lines == 2
    _apply(repo, newline)
    assert _index(repo) == THIRTY
    rest = parse_one(_file_patch(repo))
    assert [line for line in rest.hunks[0].lines if line.kind != CONTEXT] == [
        diffmodel.Line(ADD, "line 31", None, 31, False)
    ]


@needs_git
def test_a_crlf_file_staged_by_selection_keeps_its_line_endings_byte_for_byte(repo):
    crlf = "\r\n".join(_lines(6)) + "\r\n"
    _write(repo, "crlf.txt", crlf)
    _git(repo, "add", "crlf.txt")
    _git(repo, "commit", "-qm", "crlf")
    edited = crlf.replace("line 2\r\n", "line 2 changed\r\n").replace("line 5\r\n", "line 5 changed\r\n")
    _write(repo, "crlf.txt", edited)
    file, text = _loaded(repo, "crlf.txt")
    assert "+line 2 changed\r\n" in text
    plan = lines_plan(file, OLD, 2, NEW, 2, UNSTAGED, text)
    assert isinstance(plan, Plan) and plan.lines == 2
    _apply(repo, plan)
    assert _index(repo, "crlf.txt") == crlf.replace("line 2\r\n", "line 2 changed\r\n")
    assert _read(repo, "crlf.txt") == edited


@needs_git
def test_a_file_whose_mode_changed_too_a_selection_stages_its_lines_and_not_the_bit(repo):
    _write(repo, "f.txt", "\n".join([*_lines(10), "new 1", "new 2", "new 3", *_lines(30)[10:]]) + "\n")
    os.chmod(repo / "f.txt", 0o755)
    file, text = _loaded(repo)
    assert "old mode 100644\nnew mode 100755\n" in text
    plan = lines_plan(file, NEW, 11, NEW, 12, UNSTAGED, text)
    assert isinstance(plan, Plan) and (plan.done, plan.lines) == ("Staged 2 lines of f.txt", 2)
    _apply(repo, plan)
    assert _git(repo, "ls-files", "-s", "f.txt").startswith("100644 ")
    assert _index(repo) == "\n".join([*_lines(10), "new 1", "new 2", *_lines(30)[10:]]) + "\n"
    # The same for a whole hunk: the mode stays for the file button.
    _git(repo, "reset", "-q")
    hunk_plan = gitpatch.plan_hunk(file, 0, UNSTAGED, text)
    assert isinstance(hunk_plan, Plan)
    _apply(repo, hunk_plan)
    assert _git(repo, "ls-files", "-s", "f.txt").startswith("100644 ")
    assert "old mode 100644\nnew mode 100755\n" in _file_patch(repo)  # left for Stage file
    _git(repo, "reset", "-q")
    discard = lines_plan(file, NEW, 11, NEW, 12, UNSTAGED, text, discard=True)
    assert isinstance(discard, Plan) and discard.done == "Discarded 2 lines of f.txt"
    _apply(repo, discard)
    assert os.stat(repo / "f.txt").st_mode & 0o111 == 0o111
    assert _read(repo, "f.txt") == "\n".join([*_lines(10), "new 3", *_lines(30)[10:]]) + "\n"


@needs_git
def test_with_diff_context_0_a_hunk_has_no_context_to_anchor_by_and_the_applies_still_land(repo):
    _git(repo, "config", "diff.context", "0")
    _write(repo, "f.txt", THIRTY.replace("line 15\nline 16\n", "changed 15\nchanged 16\n"))
    file, text = _loaded(repo)
    assert "@@ -15,2 +15,2 @@" in text and "\n line 14\n" not in text
    # One of the two new lines: the demoted `-` lines are its only context, and nothing trails.
    plan = lines_plan(file, NEW, 15, NEW, 15, UNSTAGED, text)
    assert isinstance(plan, Plan) and plan.lines == 1
    _apply(repo, plan)
    assert _index(repo) == THIRTY.replace("line 16\n", "line 16\nchanged 15\n")
    _git(repo, "reset", "-q")
    # The whole hunk: no context at all.
    hunk_plan = gitpatch.plan_hunk(file, 0, UNSTAGED, text)
    assert isinstance(hunk_plan, Plan)
    _apply(repo, hunk_plan)
    assert _index(repo) == THIRTY.replace("line 15\nline 16\n", "changed 15\nchanged 16\n")
    _git(repo, "reset", "-q")
    discard = gitpatch.plan_hunk(file, 0, UNSTAGED, text, discard=True)
    assert isinstance(discard, Plan) and discard.hunk_index == 0
    _apply(repo, discard)
    assert _read(repo, "f.txt") == THIRTY


@needs_git
def test_a_selection_in_a_rename_is_refused_whether_the_view_or_only_the_patch_shows_the_rename(repo):
    _git(repo, "mv", "f.txt", "g.txt")
    _write(repo, "g.txt", THIRTY.replace("line 2\n", "line 2 changed\n"))
    _git(repo, "add", "g.txt")
    text = _file_patch(repo, "g.txt", staged=True, previous="f.txt")
    assert "rename from f.txt" in text
    flagged = parse_one(text)
    assert (flagged.kind, flagged.previous_path) == (KIND_RENAME, "f.txt")
    whole = Refusal("renames unstage whole: use Unstage file")
    assert lines_plan(flagged, NEW, 2, NEW, 2, STAGED, text) == whole
    # The view loaded a plain change and the path became a rename's
    # destination since: stale, so the view reloads and shows the rename.
    became = Refusal("g.txt changed since it was loaded, reloading (it is a rename now)", stale=True)
    unflagged = replace(flagged, kind=KIND_CHANGE, previous_path=None)
    assert lines_plan(unflagged, NEW, 2, NEW, 2, STAGED, text) == became
    assert _index(repo, "g.txt") == THIRTY.replace("line 2\n", "line 2 changed\n")


@needs_git
def test_a_hunk_of_a_file_that_became_a_rename_since_the_load_is_refused_not_renamed_in_the_index(repo):
    # The shown file is a plain change whose hunk spans still match the
    # fresh patch's; the fresh patch is a rename. A partial patch written
    # from it would carry `rename from` / `rename to`, and `git apply
    # --cached` would move the whole file in the index behind "stage hunk 1".
    _write(repo, "f.txt", THIRTY.replace("line 2\n", "line 2 changed\n"))
    shown, _text = _loaded(repo)
    assert (shown.kind, shown.previous_path) == (KIND_CHANGE, None)
    _git(repo, "mv", "f.txt", "g.txt")
    _git(repo, "add", "g.txt")
    text = _file_patch(repo, "g.txt", staged=True, previous="f.txt")
    assert "rename from f.txt" in text
    stale = replace(shown, path="g.txt")
    became = Refusal("g.txt changed since it was loaded, reloading (it is a rename now)", stale=True)
    for hunk_index in (0, None):
        assert gitpatch.plan_hunk(stale, hunk_index, STAGED, text) == became
    # Nothing moved: the rename is still staged whole, as `git mv` left it.
    assert _index(repo, "g.txt") == THIRTY.replace("line 2\n", "line 2 changed\n")
    assert "rename from f.txt" in _git(repo, "diff", "--cached", "-M", "--", "f.txt", "g.txt")
    # A view that shows the rename takes the file whole, both paths.
    flagged = parse_one(text)
    assert gitpatch.plan_hunk(flagged, 0, STAGED, text) == Plan(
        OP_RESET, ("f.txt", "g.txt"), None, None, "Unstaged g.txt"
    )


# staging.integration.test.ts


@needs_git
def test_stage_hunk_2_of_2_and_only_it_then_unstage_it_from_the_staged_load(repo):
    _write(repo, "f.txt", TWO_ENDS)
    file, text = _loaded(repo)
    assert len(file.hunks) == 2
    plan = gitpatch.plan_hunk(file, 1, UNSTAGED, text)
    assert isinstance(plan, Plan) and plan.op == OP_APPLY_CACHED
    _apply(repo, plan)
    cached = _git(repo, "diff", "--cached", "--", "f.txt")
    assert "+line 30 changed" in cached and "line 1 changed" not in cached
    assert gitops.read_status(repo) == Status(
        unstaged=(StatusRow("f.txt", "M"),), staged=(StatusRow("f.txt", "M"),)
    )
    # Now the staged side: the one staged hunk, unstaged with the reversed apply.
    staged, staged_text = _loaded(repo, staged=True)
    back = gitpatch.plan_hunk(staged, 0, STAGED, staged_text)
    assert isinstance(back, Plan)
    assert (back.op, back.done) == (OP_APPLY_CACHED_REVERSE, "Unstaged hunk 1 of f.txt")
    _apply(repo, back)
    assert _git(repo, "diff", "--cached") == ""
    assert gitops.read_status(repo).unstaged == (StatusRow("f.txt", "M"),)


@needs_git
def test_unstage_the_middle_of_three_staged_hunks_names_its_old_start_where_the_index_has_it(repo):
    edited = (
        THIRTY.replace("line 3\n", "line 3 changed\nline 3 extra\nline 3 more\n")
        .replace("line 15\n", "line 15 changed\n")
        .replace("line 27\n", "line 27 changed\n")
    )
    _write(repo, "f.txt", edited)
    _git(repo, "add", "f.txt")
    file, text = _loaded(repo, staged=True)
    assert len(file.hunks) == 3 and "@@ -12,7 +14,7 @@" in text
    plan = gitpatch.plan_hunk(file, 1, STAGED, text)
    assert isinstance(plan, Plan) and plan.op == OP_APPLY_CACHED_REVERSE
    # Hunk 1's two extra lines stay in the index, so the reversed patch's
    # old side (what the index will look like) starts two later than git wrote it.
    assert "@@ -14,7 +14,7 @@" in plan.patch
    _apply(repo, plan)
    assert _index(repo) == edited.replace("line 15 changed\n", "line 15\n")
    assert _added_lines(_file_patch(repo)) == [["line 15 changed"]]


@needs_git
def test_stage_hunk_works_under_diff_noprefix_and_mnemonic_prefix_the_patch_is_normalised(repo):
    _git(repo, "config", "diff.noprefix", "true")
    _git(repo, "config", "diff.mnemonicPrefix", "true")
    _write(repo, "f.txt", TWO_ENDS)
    # The user's config would drop the a/ and b/ apply strips...
    assert "\n--- f.txt\n" in _git(repo, "diff", "--no-color", "--", "f.txt")
    # ...and the patch fed to apply carries them regardless.
    file, text = _loaded(repo)
    assert "\n--- a/f.txt\n+++ b/f.txt\n" in text
    plan = gitpatch.plan_hunk(file, 0, UNSTAGED, text)
    _apply(repo, plan)
    assert "+line 1 changed" in _git(repo, "diff", "--cached", "--", "f.txt")
    assert gitops.read_status(repo) == Status(
        unstaged=(StatusRow("f.txt", "M"),), staged=(StatusRow("f.txt", "M"),)
    )


@needs_git
def test_from_a_subdirectory_the_paths_name_nothing_until_resolved_to_the_top_level(repo):
    _write(repo, "sub/g.txt", "x\n")
    _git(repo, "add", "sub")
    _git(repo, "commit", "-qm", "sub")
    _write(repo, "sub/g.txt", "y\n")
    sub = repo / "sub"
    # Straight from the subdirectory, git reads the view's top-relative
    # path against the cwd and it names nothing.
    assert _run(sub, "add", "-A", "--", "sub/g.txt", check=False).returncode != 0
    assert _git(sub, "diff", "--", "sub/g.txt") == ""
    # gitops resolves every call to the working tree root (gitinfo.repo_root)
    # so the same paths work from the subdirectory the agent sits in.
    top = gitinfo.repo_root(sub)
    assert top is not None and top.resolve() == repo.resolve()
    file, text = _loaded(sub, "sub/g.txt")
    assert "+y" in text
    plan = gitpatch.plan_hunk(file, 0, UNSTAGED, text)
    assert isinstance(plan, Plan)
    _apply(sub, plan)
    assert gitops.read_status(top).staged == (StatusRow("sub/g.txt", "M"),)
    _run_plan(sub, gitpatch.plan_file(file, STAGED))
    _run_plan(sub, gitpatch.plan_file(file, UNSTAGED))
    assert gitops.read_status(top).unstaged == ()


@needs_git
def test_the_file_button_on_a_rename_stages_and_unstages_both_paths_as_one_r(repo):
    _git(repo, "mv", "old.txt", "new.txt")
    assert gitops.read_status(repo).staged == (StatusRow("new.txt", "R", "old.txt"),)
    text = _file_patch(repo, "new.txt", staged=True, previous="old.txt")
    staged = parse_one(text)
    unstage = gitpatch.plan_file(staged, STAGED)
    assert isinstance(unstage, Plan) and (unstage.op, unstage.paths) == (OP_RESET, ("old.txt", "new.txt"))
    _run_plan(repo, unstage)
    assert gitops.read_status(repo) == Status(
        unstaged=(StatusRow("old.txt", "D"), StatusRow("new.txt", "?")), staged=()
    )
    stage = gitpatch.plan_file(replace(staged, kind=KIND_CHANGE), UNSTAGED)
    assert isinstance(stage, Plan) and (stage.op, stage.paths) == (OP_ADD, ("old.txt", "new.txt"))
    _run_plan(repo, stage)
    assert gitops.read_status(repo).staged == (StatusRow("new.txt", "R", "old.txt"),)


@needs_git
def test_an_untracked_file_goes_whole_and_the_file_button_stages_it_as_a_new_file(repo):
    _write(repo, "n.txt", "new\n")
    _write(repo, "f.txt", "changed\n")
    assert _file_patch(repo, "n.txt") == ""
    # The view's load synthesizes the untracked file (gitops.read_diff over
    # `diff --no-index -- /dev/null n.txt`, which exits 1 when the two
    # differ, as they do).
    file = next(f for f in gitops.read_diff(repo, UNSTAGED).files if f.path == "n.txt")
    assert (file.path, file.kind, file.untracked) == ("n.txt", diffmodel.KIND_NEW, True)
    plan = gitpatch.plan_hunk(file, 0, UNSTAGED, "")
    assert plan == Plan(OP_ADD, ("n.txt",), None, None, "Staged n.txt")
    before = gitops.read_status(repo)
    assert sorted(f"{row.code} {row.path}" for row in before.unstaged) == ["? n.txt", "M f.txt"]
    _run_plan(repo, plan)
    staged = gitops.read_status(repo)
    assert staged.staged == (StatusRow("n.txt", "A"),)
    assert staged.unstaged == (StatusRow("f.txt", "M"),)
    _run_plan(repo, gitpatch.plan_file(file, STAGED))
    assert gitops.read_status(repo) == before


@needs_git
def test_discard_on_a_file_deleted_in_the_working_tree_restores_it_from_the_index_the_index_untouched(repo):
    _write(repo, "old.txt", "keep me around\nstaged edit\n")
    _git(repo, "add", "old.txt")
    (repo / "old.txt").unlink()
    text = _file_patch(repo, "old.txt")
    assert "deleted file mode 100644" in text
    gone = parse_one(text)
    assert gone.kind == diffmodel.KIND_DELETED
    plan = gitpatch.plan_hunk(gone, 0, UNSTAGED, text, discard=True)
    assert plan == Plan(
        OP_CHECKOUT, ("old.txt",), None, "Restore old.txt from the index?", "Restored old.txt"
    )
    assert gitpatch.plan_file(gone, UNSTAGED, discard=True) == plan
    _run_plan(repo, plan)
    assert _read(repo, "old.txt") == "keep me around\nstaged edit\n"  # the index's copy, not HEAD's
    assert gitops.read_status(repo) == Status(unstaged=(), staged=(StatusRow("old.txt", "M"),))


@needs_git
def test_revert_a_hunk_of_a_commit_into_the_working_tree(repo):
    edited = THIRTY.replace("line 3\n", "line 3 changed\n").replace("line 20\n", "line 20 changed\n")
    _write(repo, "f.txt", edited)
    _git(repo, "commit", "-qam", "two changes")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    shown = gitops.read_diff(repo, {"show": sha})
    assert shown.ok and len(shown.files) == 1
    file = shown.files[0]
    fresh = gitops.file_patch(repo, {"show": sha}, "f.txt")
    assert fresh == file.patch
    plan = gitpatch.plan_hunk(file, 1, {"show": sha}, fresh)
    assert isinstance(plan, Plan)
    assert (plan.op, plan.done) == (OP_APPLY_WORKTREE_REVERSE, "Reverted hunk 2 of f.txt")
    _apply(repo, plan)
    assert _read(repo, "f.txt") == THIRTY.replace("line 3\n", "line 3 changed\n")
    assert _git(repo, "diff", "--cached") == ""
    whole = gitpatch.plan_file(file, {"show": sha})
    assert isinstance(whole, Plan) and whole.op == OP_APPLY_WORKTREE_REVERSE
    _git(repo, "checkout", "-q", "--", "f.txt")
    _apply(repo, whole)
    assert _read(repo, "f.txt") == THIRTY


@needs_git
def test_a_view_of_one_file_never_plans_a_change_to_its_glob_twin(repo):
    """The view loaded `foo[1].txt`; by action time that file is clean again
    and foo1.txt carries the same-shaped change. A glob reading of the
    pathspec once returned foo1.txt's stanza for the re-read and the lone
    stanza was taken as the file's: a discard confirmed for `foo[1].txt`
    emptied foo1.txt's change. Now the re-read is literal and empty, the
    plan is a stale refusal, and nothing moves."""
    for name in ("foo1.txt", "foo[1].txt"):
        _write(repo, name, "one\ntwo\nthree\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "twins")
    _write(repo, "foo[1].txt", "one\ntwo\nthree\nchanged\n")
    shown = gitops.read_diff(repo, UNSTAGED)
    assert [f.path for f in shown.files] == ["foo[1].txt"]
    file = shown.files[0]
    _git(repo, "checkout", "-q", "--", ":(literal)foo[1].txt")
    _write(repo, "foo1.txt", "one\ntwo\nthree\nchanged\n")
    fresh = gitops.file_patch(repo, UNSTAGED, "foo[1].txt")
    assert fresh == ""
    plan = gitpatch.plan_hunk(file, 0, UNSTAGED, fresh, discard=True)
    assert plan == Refusal("nothing to discard in foo[1].txt: reloading", stale=True)
    assert isinstance(gitpatch.plan_hunk(file, 0, UNSTAGED, fresh), Refusal)
    # Even handed foo1.txt's stanza outright, the planner has no file for it.
    twin = gitops.file_patch(repo, UNSTAGED, "foo1.txt")
    assert twin.startswith("diff --git a/foo1.txt b/foo1.txt\n")
    assert gitpatch.plan_hunk(file, 0, UNSTAGED, twin, discard=True) == Refusal(
        "cannot read the patch for foo[1].txt: no stanza for it"
    )
    assert gitops.read_status(repo) == Status(unstaged=(StatusRow("foo1.txt", "M"),), staged=())
