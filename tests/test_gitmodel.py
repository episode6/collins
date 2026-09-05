# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for gitmodel: the git page's native panels as pure functions —
the log and status parsers, the commits list's rows and which of them is
the loaded one, the files list's sections, and the action row's words.
Ports of the collins-git extension's `bun test` cases (test/model.test.ts,
test/git.test.ts) that the native panels replace."""

import pytest

from collins import gitmodel, hunkctl
from collins.gitmodel import (
    BranchRef,
    Commit,
    FileRow,
    Status,
    StatusRow,
    build_rows,
    files_sections,
    loaded_row_id,
    parse_log,
    parse_status_v2,
    without_untracked,
)

SHA_A = "bdda3818b622d8af5190c55f25c15356d76c7806"
SHA_B = "8a681ae56e3d0a1015c5fcce494db95597a5326b"


def commit(n: int, subject: str | None = None) -> Commit:
    sha = f"{n:02x}" * 20
    return Commit(sha, sha[:7], subject if subject is not None else f"commit {n}")


# -- parse_log ----------------------------------------------------------------


def test_parse_log_reads_nul_separated_fields_out_of_rs_terminated_records():
    text = f"{SHA_A}\x00bdda381\x00ours\x1e\n{SHA_B}\x008a681ae\x00first\x1e\n"
    assert parse_log(text) == [Commit(SHA_A, "bdda381", "ours"), Commit(SHA_B, "8a681ae", "first")]
    assert gitmodel.LOG_FORMAT == "--format=%H%x00%h%x00%s%x1e"


def test_parse_log_tolerates_empty_output_and_a_subject_with_a_comma():
    assert parse_log("") == []
    assert parse_log(None) == []
    assert parse_log(f"{SHA_A}\x00bdda381\x00fix a, b\x1e") == [Commit(SHA_A, "bdda381", "fix a, b")]


def test_parse_log_skips_a_record_that_does_not_start_with_a_sha():
    text = f"garbage\x00x\x00y\x1e{SHA_B}\x008a681ae\x00first\x1e"
    assert parse_log(text) == [Commit(SHA_B, "8a681ae", "first")]
    assert parse_log(f"{SHA_A[:39]}\x00abc\x00short\x1e") == []
    assert parse_log(f"{SHA_A.upper()}\x00abc\x00upper\x1e") == []


def test_parse_log_bounds_foreign_content():
    """A subject is cut to SUBJECT_MAX_CHARS; an abbreviation that isn't hex
    falls back to the sha's head; a missing subject is empty; never more
    than MAX_ROWS records."""
    long = "s" * (gitmodel.SUBJECT_MAX_CHARS + 50)
    (only,) = parse_log(f"{SHA_A}\x00not hex\x00{long}\x1e")
    assert only == Commit(SHA_A, SHA_A[:7], "s" * gitmodel.SUBJECT_MAX_CHARS)
    assert parse_log(f"{SHA_A}\x00bdda381\x1e") == [Commit(SHA_A, "bdda381", "")]
    assert parse_log(f"{SHA_A}\x1e") == [Commit(SHA_A, SHA_A[:7], "")]
    text = "".join(f"{i:040x}\x00{i:07x}\x00c{i}\x1e" for i in range(gitmodel.MAX_ROWS + 5))
    assert len(parse_log(text)) == gitmodel.MAX_ROWS


# -- parse_status_v2 ----------------------------------------------------------

H = "2cdcdb0cb0170be576e43fd27c48d1f64f800df7"
Z = "0" * 40
STATUS_TEXT = "\x00".join(
    [
        f"1 MM N... 100644 100644 100644 {H} {H} a.txt",
        f"1 A. N... 000000 100644 100644 {Z} {H} added.txt",
        f"1 .M N... 100644 100644 100644 {H} {H} bin.dat",
        f"1 D. N... 100644 000000 000000 {H} {Z} gone.txt",
        f"2 R. N... 100644 100644 100644 {H} {H} R100 new.txt",
        "old.txt",
        f"2 RM N... 100644 100644 100644 {H} {H} R087 moved.txt",
        "orig.txt",
        f"u UU N... 100644 100644 100644 100644 {H} {H} {H} merge.txt",
        "? untracked.txt",
        "! ignored.txt",
        "",
    ]
)


def test_parse_status_v2_splits_every_entry_kind_between_the_index_and_the_working_tree():
    status = parse_status_v2(STATUS_TEXT)
    assert status.staged == (
        StatusRow("a.txt", "M"),
        StatusRow("added.txt", "A"),
        StatusRow("gone.txt", "D"),
        StatusRow("new.txt", "R", "old.txt"),
        StatusRow("moved.txt", "R", "orig.txt"),
    )
    assert status.unstaged == (
        StatusRow("a.txt", "M"),
        StatusRow("bin.dat", "M"),
        StatusRow("moved.txt", "M"),
        StatusRow("merge.txt", "U"),
        StatusRow("untracked.txt", "?"),
    )


def test_parse_status_v2_keeps_spaces_in_paths_and_survives_an_empty_status():
    assert parse_status_v2("") == Status()
    assert parse_status_v2(None) == Status((), ())
    spaced = parse_status_v2(f"1 .M N... 100644 100644 100644 {H} {H} dir name/a b.txt\x00? new file.txt\x00")
    assert spaced.unstaged == (StatusRow("dir name/a b.txt", "M"), StatusRow("new file.txt", "?"))
    assert spaced.staged == ()


def test_parse_status_v2_drops_what_does_not_fit():
    """A short entry, an unknown kind, an unknown code letter, a path over
    PATH_MAX_CHARS: dropped, never mis-filed."""
    long_path = "p" * (gitmodel.PATH_MAX_CHARS + 1)
    text = "\x00".join(
        [
            "1 M.",  # too short
            f"9 M. N... 100644 100644 100644 {H} {H} what.txt",  # unknown kind
            f"1 Z. N... 100644 100644 100644 {H} {H} odd.txt",  # unknown code on the index side
            f"1 .M N... 100644 100644 100644 {H} {H} {long_path}",
            f"? {long_path}",
            f"2 R. N... 100644 100644 100644 {H} {H} R100 kept.txt",
            long_path,  # the rename's old path is too long: the row keeps only the new one
            f"1 .M N... 100644 100644 100644 {H} {H} fine.txt",
        ]
    )
    status = parse_status_v2(text)
    assert status.staged == (StatusRow("kept.txt", "R", None),)
    assert status.unstaged == (StatusRow("fine.txt", "M"),)


def test_without_untracked():
    status = Status(
        unstaged=(StatusRow("a.txt", "M"), StatusRow("new.txt", "?"), StatusRow("u.txt", "U")),
        staged=(StatusRow("s.txt", "A"),),
    )
    filtered = without_untracked(status)
    assert [row.code for row in filtered.unstaged] == ["M", "U"]
    assert filtered.staged is status.staged
    assert without_untracked(None) is None


# -- build_rows ---------------------------------------------------------------


def base_rows(**overrides):
    args = dict(
        branch="feat/panel",
        parent=BranchRef("develop", "origin/develop"),
        default=BranchRef("main", "main"),
        current=[commit(3), commit(2)],
        current_more=False,
        parent_commits=[commit(5)],
        parent_more=False,
        default_commits=[commit(9), commit(8)],
        default_more=True,
        unpushed={commit(3).sha},
    )
    args.update(overrides)
    return build_rows(**args)


def test_build_rows_lists_current_parent_and_default_groups_in_order_with_the_specs_loads():
    rows = base_rows()
    assert [f"{row.group}/{row.kind}:{row.label}" for row in rows] == [
        "current/header:feat/panel",
        "current/worktree:working tree",
        "current/commit:commit 3",
        "current/commit:commit 2",
        "parent/header:develop",
        "parent/commit:commit 5",
        "default/header:main",
        "default/commit:commit 9",
        "default/commit:commit 8",
        "default/more:load more…",
    ]
    assert rows[0].load == "branch"
    assert rows[0].id == "header:current"
    assert rows[1].load == "unstaged"
    assert rows[1].id == gitmodel.WORKTREE_ROW_ID
    assert rows[2].load == {"show": commit(3).sha}
    assert rows[2].id == f"commit:{commit(3).sha}"
    assert (rows[2].sha, rows[2].abbrev, rows[2].unpushed) == (commit(3).sha, commit(3).abbrev, True)
    assert rows[3].unpushed is False
    assert rows[4].load == {"range": "main...origin/develop"}
    assert rows[4].id == "header:parent"
    assert rows[6].load is None
    assert rows[6].id == "header:default"
    assert rows[9].load is None
    assert rows[9].id == "more:default"
    for row in rows:
        assert row.load is None or hunkctl.loaded_ok(row.load)


def test_build_rows_omits_the_parent_group_when_the_parent_is_the_default_branch():
    rows = base_rows(parent=BranchRef("main", "main"))
    assert not any(row.group == "parent" for row in rows)
    assert rows[0].load == "branch"


def test_build_rows_with_no_parent_at_all_the_header_loads_what_the_group_lists():
    """No parent, no default (a `git init` repository on a branch called
    something else): the header ranges over the listed commits, from the
    oldest one's parent — a load hunk takes as `diff <sha>^...HEAD`."""
    rows = base_rows(parent=None, default=None, default_commits=[])
    assert [row.group for row in rows] == ["current"] * 4
    assert rows[0].load == {"range": f"{commit(2).sha}^...HEAD"}
    assert hunkctl.loaded_ok(rows[0].load)
    # And nothing listed: nothing to load.
    rows = base_rows(parent=None, default=None, current=[], default_commits=[])
    assert rows[0].load is None
    assert [row.kind for row in rows] == ["header", "worktree"]


def test_build_rows_the_default_branchs_header_loads_nothing():
    rows = base_rows(default_more=False)
    header = next(row for row in rows if row.group == "default" and row.kind == "header")
    assert header.load is None
    assert not any(row.kind == "more" for row in rows)


def test_build_rows_load_more_rows_appear_per_group_when_a_page_was_full():
    rows = base_rows(current_more=True, parent_more=True)
    assert [row.group for row in rows if row.kind == "more"] == ["current", "parent", "default"]
    assert [row.id for row in rows if row.kind == "more"] == ["more:current", "more:parent", "more:default"]


def test_build_rows_parent_without_a_default_has_no_range_to_load():
    rows = base_rows(default=None, default_commits=[])
    header = next(row for row in rows if row.group == "parent" and row.kind == "header")
    assert header.load is None
    assert not any(row.group == "default" for row in rows)


def test_build_rows_bounds_what_it_shows():
    """A subject is cut; an unsafe target makes no range load; the whole
    list is capped at MAX_ROWS."""
    long = commit(1, "x" * (gitmodel.SUBJECT_MAX_CHARS + 9))
    rows = base_rows(current=[long])
    assert rows[2].label == "x" * gitmodel.SUBJECT_MAX_CHARS
    rows = base_rows(parent=BranchRef("dev", "a b"))
    assert next(row for row in rows if row.id == "header:parent").load is None
    many = [commit(i) for i in range(1, 1200)]
    rows = base_rows(current=many, default_commits=many)
    assert len(rows) == gitmodel.MAX_ROWS


# -- loaded_row_id ---------------------------------------------------------------


def test_loaded_row_the_working_tree_row_stands_for_both_working_tree_loads():
    rows = base_rows()
    assert loaded_row_id(rows, "unstaged") == "worktree"
    assert loaded_row_id(rows, "staged") == "worktree"


def test_loaded_row_a_branch_load_is_the_current_header():
    assert loaded_row_id(base_rows(), "branch") == "header:current"


def test_loaded_row_a_show_matches_by_sha_prefix_then_by_the_resolved_sha():
    rows = base_rows()
    assert loaded_row_id(rows, {"show": commit(9).sha[:8]}) == f"commit:{commit(9).sha}"
    assert loaded_row_id(rows, {"show": "HEAD"}) is None
    assert loaded_row_id(rows, {"show": "HEAD"}, commit(3).sha) == f"commit:{commit(3).sha}"
    assert loaded_row_id(rows, {"show": "HEAD"}, "f" * 40) is None


def test_loaded_row_a_range_matches_the_header_that_loads_it_anything_else_no_row():
    rows = base_rows()
    assert loaded_row_id(rows, {"range": "main...origin/develop"}) == "header:parent"
    assert loaded_row_id(rows, {"range": "x...y"}) is None
    assert loaded_row_id(rows, {"range": "x..y"}) is None
    assert loaded_row_id(rows, None) is None
    assert loaded_row_id(rows, "foreign") is None
    assert loaded_row_id([], "unstaged") is None


# -- files_sections --------------------------------------------------------------


def session_file(file_id, path, additions, deletions, hunk_count=1, previous=None):
    return hunkctl.SessionFile(file_id, path, previous, additions, deletions, hunk_count)


FILES = [
    session_file("f0", "a.txt", 2, 1),
    session_file("f1", "n.txt", 3, 0),
    session_file("f2", "b.txt", 0, 0, 0, previous="a0.txt"),
    session_file("f3", "img.png", 0, 0, 0),
]
STATUS = Status(
    unstaged=(StatusRow("a.txt", "M"), StatusRow("n.txt", "?")),
    staged=(StatusRow("s.txt", "A"), StatusRow("b.txt", "R", "a0.txt")),
)


def test_files_sections_splits_when_the_working_tree_is_loaded_live_side_from_hunks_files():
    sections = files_sections(STATUS, FILES, "unstaged")
    assert sections.mode == "split"
    assert sections.live == "unstaged"
    assert sections.flat == ()
    assert [(row.path, row.code, row.additions, row.deletions, row.live) for row in sections.unstaged] == [
        ("a.txt", "M", 2, 1, True),
        ("n.txt", "?", 3, 0, True),
        ("b.txt", "R", 0, 0, True),
        ("img.png", None, 0, 0, True),
    ]
    assert sections.unstaged[2].previous_path == "a0.txt"
    assert sections.staged == (
        FileRow("s.txt", "A", None, None, None, False),
        FileRow("b.txt", "R", "a0.txt", None, None, False),
    )


def test_files_sections_binary_reads_off_the_counts():
    """hunk's session record has no binary flag; a binary change is the
    one with no hunk and no line counts (hunk 0.21.1: `additions: 0,
    deletions: 0, hunkCount: 0`). A status-side row never claims it."""
    sections = files_sections(STATUS, FILES, "unstaged")
    assert [row.binary for row in sections.unstaged] == [False, False, True, True]
    assert [row.binary for row in sections.staged] == [False, False]
    assert FileRow("x", live=True, additions=0, deletions=0, hunk_count=1).binary is False


def test_files_sections_the_staged_view_puts_hunks_files_on_the_staged_side():
    sections = files_sections(STATUS, FILES[:1], "staged")
    assert sections.live == "staged"
    assert sections.staged[0].path == "a.txt" and sections.staged[0].live
    assert sections.staged[0].code is None  # a.txt isn't in the staged status: nothing said
    assert [row.path for row in sections.unstaged] == ["a.txt", "n.txt"]
    assert all(not row.live for row in sections.unstaged)


def test_files_sections_with_untracked_files_off_the_staged_views_unstaged_side_lists_none():
    with_new = Status(
        unstaged=(StatusRow("a.txt", "M"), StatusRow("new.txt", "?"), StatusRow("u.txt", "U")),
        staged=STATUS.staged,
    )
    sections = files_sections(with_new, FILES[:1], "staged", untracked=False)
    assert [row.path for row in sections.unstaged] == ["a.txt", "u.txt"]
    shown = files_sections(with_new, FILES[:1], "staged", untracked=True)
    assert [row.code for row in shown.unstaged] == ["M", "?", "U"]
    # The live side is hunk's own list, already filtered by hunk itself.
    live = files_sections(with_new, FILES[:2], "unstaged", untracked=False)
    assert [row.path for row in live.unstaged] == ["a.txt", "n.txt"]


def test_files_sections_everything_else_is_flat_and_so_is_a_working_tree_with_no_status():
    shown = files_sections(STATUS, FILES, {"show": "abc"})
    assert shown.mode == "flat"
    assert shown.live is None
    assert len(shown.flat) == 4
    assert shown.flat[2].code == "R"  # a rename hunk reports, with no status to ask
    assert shown.flat[0].code is None
    assert files_sections(None, FILES, "unstaged").mode == "flat"
    assert files_sections(STATUS, [], {"range": "a...b"}) == gitmodel.FileSections("flat")
    assert files_sections(STATUS, [], "branch").flat == ()


def test_files_sections_drops_a_path_that_does_not_fit():
    long = session_file("fx", "p" * (gitmodel.PATH_MAX_CHARS + 1), 1, 0)
    assert files_sections(None, [long, FILES[0]], "unstaged").flat == (
        FileRow("a.txt", None, None, 2, 1, True, 1),
    )


# -- the action row's words --------------------------------------------------------


def test_plan_all_counts_the_side_the_button_acts_on():
    assert gitmodel.plan_all(STATUS, True) == (2, "Stage all 2 changes?")
    assert gitmodel.plan_all(STATUS, False) == (2, "Unstage all 2 changes?")
    one = Status(unstaged=(StatusRow("a", "M"),), staged=())
    assert gitmodel.plan_all(one, True) == (1, "Stage all 1 change?")
    assert gitmodel.plan_all(one, False) == (0, "")
    assert gitmodel.plan_all(None, True) == (0, "")


def test_all_done_and_stage_noun():
    assert gitmodel.stage_noun(1) == "change"
    assert gitmodel.stage_noun(0) == "changes"
    assert gitmodel.stage_noun(3) == "changes"
    assert gitmodel.all_done(3, True) == "Staged 3 changes"
    assert gitmodel.all_done(1, False) == "Unstaged 1 change"


def test_fixup_options_and_autosquash_command():
    assert gitmodel.fixup_options([commit(3, "Second"), commit(2, "First")]) == [
        f"{commit(3).abbrev}  Second",
        f"{commit(2).abbrev}  First",
    ]
    long = commit(1, "x" * 300)
    assert len(gitmodel.fixup_options([long])[0]) == 7 + 2 + gitmodel.SUBJECT_MAX_CHARS
    assert gitmodel.autosquash_command("bdda381", False) == "git rebase -i --autosquash --autostash bdda381^"
    assert gitmodel.autosquash_command("bdda381", True) == "git rebase -i --autosquash --autostash --root"


@pytest.mark.parametrize("code", sorted(gitmodel.STATUS_CODES))
def test_status_codes_are_the_letters_the_files_list_colours(code):
    assert code in "MADRTCU?"
    assert len(gitmodel.STATUS_CODES) == 8
