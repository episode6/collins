# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""Tests for gitmodel: the git page's native panels as pure functions —
the log and status parsers, the commits list's rows and which of them is
the loaded one, the files list's sections, and the action row's words.
Ports of the former collins-git extension's `bun test` cases
(test/model.test.ts, test/git.test.ts) that the native panels replaced."""

import pytest

from collins import gitloads, gitmodel
from collins.gitmodel import (
    BranchPage,
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


DEVELOP = BranchRef("develop", "origin/develop")


def base_rows(**overrides):
    args = dict(
        branch="feat/panel",
        parent=DEVELOP,
        default=BranchRef("main", "main"),
        current=[commit(3), commit(2)],
        current_more=False,
        stack=[BranchPage(DEVELOP, (commit(5),), False)],
        default_commits=[commit(9), commit(8)],
        default_more=True,
        unpushed={commit(3).sha},
    )
    args.update(overrides)
    return build_rows(**args)


def test_build_rows_lists_current_parent_and_default_groups_in_order_with_the_specs_loads():
    rows = base_rows()
    assert [f"{row.group}/{row.kind}:{row.label}" for row in rows] == [
        "worktree/worktree:working tree",
        "current/header:feat/panel",
        "current/commit:commit 3",
        "current/commit:commit 2",
        "stack:develop/header:develop",
        "stack:develop/commit:commit 5",
        "default/header:main",
        "default/commit:commit 9",
        "default/commit:commit 8",
        "default/more:load more…",
    ]
    assert rows[0].load == "unstaged"
    assert rows[0].id == gitmodel.WORKTREE_ROW_ID
    assert rows[1].load == "branch"
    assert rows[1].id == "header:current"
    assert rows[2].load == {"show": commit(3).sha}
    assert rows[2].id == f"commit:{commit(3).sha}"
    assert (rows[2].sha, rows[2].abbrev, rows[2].unpushed) == (commit(3).sha, commit(3).abbrev, True)
    assert rows[3].unpushed is False
    assert rows[4].load == {"range": "main...origin/develop"}
    assert rows[4].id == "header:stack:develop"
    assert rows[6].load is None
    assert rows[6].id == "header:default"
    assert rows[9].load is None
    assert rows[9].id == "more:default"
    for row in rows:
        assert row.load is None or gitloads.loaded_ok(row.load)


def test_build_rows_branches_at_one_commit_share_a_header_with_slashes():
    """The current branch's twins (the other branches at HEAD) join its
    header, the branch itself never repeated; a stack BranchRef's twins
    join that branch's header; the group id and the load stay the first
    branch's."""
    rows = base_rows(twins=("feat/panel", "feat/panel-2", "wip"))
    assert rows[1].label == "feat/panel / feat/panel-2 / wip"
    assert rows[1].id == "header:current"
    develop = gitmodel.BranchRef("develop", "origin/develop", ("develop-twin",))
    rows = base_rows(stack=[gitmodel.BranchPage(develop, (commit(5),), False)])
    header = next(row for row in rows if row.group == "stack:develop")
    assert header.label == "develop / develop-twin"
    assert header.id == "header:stack:develop"
    assert header.load == {"range": "main...origin/develop"}
    assert gitmodel.branch_label("a", ()) == "a"


def test_row_folded_hides_every_row_of_a_collapsed_group_but_its_header():
    rows = base_rows()
    folded = [row.id for row in rows if gitmodel.row_folded(row, {"current", "default"})]
    assert folded == [
        f"commit:{commit(3).sha}",
        f"commit:{commit(2).sha}",
        f"commit:{commit(9).sha}",
        f"commit:{commit(8).sha}",
        "more:default",
    ]
    assert not any(gitmodel.row_folded(row, set()) for row in rows)
    # The working tree row sits above every header, in a group of its own
    # that no caret folds.
    assert rows[0].group == gitmodel.WORKTREE_GROUP
    assert not gitmodel.row_folded(rows[0], {"current", "default", "worktree"})


def test_build_rows_a_stack_is_one_group_per_branch_each_ranging_to_the_one_below():
    """feat/panel over step2 over step1 over main: the parent's group
    ranges to the branch under it, the last one to the default."""
    step2, step1 = BranchRef("step2", "step2"), BranchRef("step1", "step1")
    rows = base_rows(
        parent=step2,
        stack=[BranchPage(step2, (commit(5),), True), BranchPage(step1, (commit(4),), False)],
    )
    assert [f"{row.group}/{row.kind}:{row.label}" for row in rows] == [
        "worktree/worktree:working tree",
        "current/header:feat/panel",
        "current/commit:commit 3",
        "current/commit:commit 2",
        "stack:step2/header:step2",
        "stack:step2/commit:commit 5",
        "stack:step2/more:load more…",
        "stack:step1/header:step1",
        "stack:step1/commit:commit 4",
        "default/header:main",
        "default/commit:commit 9",
        "default/commit:commit 8",
        "default/more:load more…",
    ]
    assert rows[4].load == {"range": "step1...step2"} and rows[4].id == "header:stack:step2"
    assert rows[6].id == "more:stack:step2"
    assert rows[7].load == {"range": "main...step1"} and rows[7].id == "header:stack:step1"
    main = BranchRef("main", "main")
    assert gitmodel.stack_ranges([step2, step1], main) == [(step2, "step1"), (step1, "main")]
    assert gitmodel.stack_ranges([step2, step1], None) == [(step2, "step1"), (step1, None)]
    assert gitmodel.stack_ranges([], BranchRef("main", "main")) == []


def test_build_rows_omits_the_stack_when_the_parent_is_the_default_branch():
    rows = base_rows(parent=BranchRef("main", "main"), stack=[])
    assert not any(row.group.startswith(gitmodel.STACK_GROUP_PREFIX) for row in rows)
    assert rows[1].load == "branch"


def test_build_rows_on_the_default_branch_the_current_group_is_left_out():
    """main checked out: a `main` header over an empty `main..HEAD` above
    the default group's `main` header would name the branch twice, so the
    list is the working tree row and the default group alone — the
    default's header wearing the twins at HEAD — and a "branch" load marks
    that header."""
    main = BranchRef("main", "main")
    rows = base_rows(branch="main", parent=main, stack=[], current=[], twins=("main", "release"))
    assert [f"{row.group}/{row.kind}:{row.label}" for row in rows] == [
        "worktree/worktree:working tree",
        "default/header:main / release",
        "default/commit:commit 9",
        "default/commit:commit 8",
        "default/more:load more…",
    ]
    assert rows[1].id == "header:default" and rows[1].load is None
    assert loaded_row_id(rows, "branch") == "header:default"
    assert loaded_row_id(rows, "unstaged") == gitmodel.WORKTREE_ROW_ID
    # A branch of the same name as the default is only "on" it when a
    # default is known at all.
    rows = base_rows(branch="main", parent=None, default=None, stack=[], default_commits=[])
    assert rows[1].id == "header:current"


def test_build_rows_with_no_parent_at_all_the_header_loads_what_the_group_lists():
    """No parent, no default (a `git init` repository on a branch called
    something else): the header ranges over the listed commits, from the
    oldest one's parent — read as `diff <sha>^...HEAD`."""
    rows = base_rows(parent=None, default=None, stack=[], default_commits=[])
    assert [row.group for row in rows] == ["worktree"] + ["current"] * 3
    assert rows[1].load == {"range": f"{commit(2).sha}^...HEAD"}
    assert gitloads.loaded_ok(rows[1].load)
    # And nothing listed: nothing to load.
    rows = base_rows(parent=None, default=None, stack=[], current=[], default_commits=[])
    assert rows[1].load is None
    assert [row.kind for row in rows] == ["worktree", "header"]


def test_build_rows_the_default_branchs_header_loads_nothing():
    rows = base_rows(default_more=False)
    header = next(row for row in rows if row.group == "default" and row.kind == "header")
    assert header.load is None
    assert not any(row.kind == "more" for row in rows)


def test_build_rows_load_more_rows_appear_per_group_when_a_page_was_full():
    rows = base_rows(current_more=True, stack=[BranchPage(DEVELOP, (commit(5),), True)])
    assert [row.group for row in rows if row.kind == "more"] == ["current", "stack:develop", "default"]
    assert [row.id for row in rows if row.kind == "more"] == [
        "more:current", "more:stack:develop", "more:default",
    ]


def test_build_rows_parent_without_a_default_has_no_range_to_load():
    rows = base_rows(default=None, default_commits=[])
    header = next(row for row in rows if row.group == "stack:develop" and row.kind == "header")
    assert header.load is None
    assert not any(row.group == "default" for row in rows)


def test_build_rows_bounds_what_it_shows():
    """A subject is cut; an unsafe target makes no range load; the whole
    list is capped at MAX_ROWS."""
    long = commit(1, "x" * (gitmodel.SUBJECT_MAX_CHARS + 9))
    rows = base_rows(current=[long])
    assert rows[2].label == "x" * gitmodel.SUBJECT_MAX_CHARS
    odd = BranchRef("dev", "a b")
    rows = base_rows(parent=odd, stack=[BranchPage(odd)])
    assert next(row for row in rows if row.id == "header:stack:dev").load is None
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
    assert loaded_row_id(rows, {"range": "main...origin/develop"}) == "header:stack:develop"
    assert loaded_row_id(rows, {"range": "x...y"}) is None
    assert loaded_row_id(rows, {"range": "x..y"}) is None
    assert loaded_row_id(rows, None) is None
    assert loaded_row_id(rows, "foreign") is None
    assert loaded_row_id([], "unstaged") is None


# -- files_sections --------------------------------------------------------------


def session_file(file_id, path, additions, deletions, hunk_count=1, previous=None):
    return gitmodel.FileSummary(file_id, path, previous, additions, deletions, hunk_count)


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


def test_files_sections_splits_when_the_working_tree_is_loaded_live_side_from_the_diffs_files():
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
    """A FileSummary has no binary flag; a binary change is the one with
    no hunk and no line counts. A status-side row never claims it."""
    sections = files_sections(STATUS, FILES, "unstaged")
    assert [row.binary for row in sections.unstaged] == [False, False, True, True]
    assert [row.binary for row in sections.staged] == [False, False]
    assert FileRow("x", live=True, additions=0, deletions=0, hunk_count=1).binary is False


def test_files_sections_the_staged_view_puts_the_diffs_files_on_the_staged_side():
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
    assert [row.path for row in sections.unstaged] == ["a.txt"]
    assert [row.path for row in sections.conflicts] == ["u.txt"]
    shown = files_sections(with_new, FILES[:1], "staged", untracked=True)
    assert [row.code for row in shown.unstaged] == ["M", "?"]
    # The live side is the diff's own list, read without untracked files already.
    live = files_sections(with_new, FILES[:2], "unstaged", untracked=False)
    assert [row.path for row in live.unstaged] == ["a.txt", "n.txt"]


def test_files_sections_lists_the_unmerged_paths_in_a_section_of_their_own():
    """A half-finished operation's `U` rows leave the unstaged side for
    the conflicts section: on the unstaged load the diff's own row (the
    read carried it as `diff --ours`, counts and all), or a status row
    for one the read did not carry — so the clash is listed the moment
    the operation stops, whatever the diff read managed; on the staged
    load status rows, a click reloading the unstaged side."""
    clashing = Status(
        unstaged=(StatusRow("a.txt", "M"), StatusRow("u.txt", "U"), StatusRow("v.txt", "U")),
        staged=(StatusRow("s.txt", "A"),),
    )
    read = [*FILES[:1], session_file("f9", "u.txt", 4, 0)]
    live = files_sections(clashing, read, "unstaged")
    assert [(row.path, row.code, row.live, row.additions) for row in live.conflicts] == [
        ("u.txt", "U", True, 4),
        ("v.txt", "U", False, None),
    ]
    assert [row.path for row in live.unstaged] == ["a.txt"]
    assert [row.path for row in live.staged] == ["s.txt"]
    other = files_sections(clashing, FILES[:1], "staged")
    assert other.conflicts == (
        FileRow("u.txt", "U", None, None, None, False),
        FileRow("v.txt", "U", None, None, None, False),
    )
    assert [row.path for row in other.unstaged] == ["a.txt"]
    # No clash, no section; a flat load never has one.
    assert files_sections(STATUS, FILES, "unstaged").conflicts == ()
    assert files_sections(clashing, read, {"show": "HEAD"}).conflicts == ()


def test_files_sections_everything_else_is_flat_and_so_is_a_working_tree_with_no_status():
    shown = files_sections(STATUS, FILES, {"show": "abc"})
    assert shown.mode == "flat"
    assert shown.live is None
    assert len(shown.flat) == 4
    assert shown.flat[2].code == "R"  # a rename the diff reports, with no status to ask
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


def test_revert_words():
    assert gitmodel.revert_done("bdda381", True, "e4f5a6b") == (
        "Reverted bdda381 as e4f5a6b — undo with `git reset --keep HEAD~1`"
    )
    assert gitmodel.revert_done("bdda381", True, None).startswith("Reverted bdda381 as ?")
    assert gitmodel.revert_done("bdda381", False, None) == (
        "Reverted bdda381 into the working tree — staged, nothing committed"
    )
    assert gitmodel.revert_failed("bdda381", "error: local changes", False) == "error: local changes"
    assert gitmodel.revert_failed("bdda381", "", False) == "git revert failed"
    assert "git revert --continue" in gitmodel.revert_failed("bdda381", "error: could not revert", True)


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


def test_unmerged_count_counts_the_status_u_rows():
    assert gitmodel.unmerged_count(None) == 0
    status = gitmodel.Status(
        unstaged=(gitmodel.StatusRow("a", "U"), gitmodel.StatusRow("b", "M"), gitmodel.StatusRow("c", "U")),
        staged=(gitmodel.StatusRow("d", "A"),),
    )
    assert gitmodel.unmerged_count(status) == 2


def test_operation_words_name_the_kind_and_its_commands():
    assert gitmodel.operation_title("rebase") == "Rebase in progress"
    assert gitmodel.operation_title("am") == "git am in progress"
    assert gitmodel.operation_title("nosuch") == "Operation in progress"
    with_conflicts = gitmodel.operation_hint("rebase", 2)
    assert "Unmerged files: 2" in with_conflicts
    assert "`git rebase --continue`" in with_conflicts and "`git rebase --abort`" in with_conflicts
    clean = gitmodel.operation_hint("cherry-pick", 0)
    assert "Nothing is left unmerged" in clean and "`git cherry-pick --continue`" in clean
    assert gitmodel.abort_heading("merge") == "Abort the merge?"
    body = gitmodel.abort_body("merge", "merge", "/repo")
    assert body.startswith("`git merge --abort` in /repo") and "lost" in body
    assert gitmodel.abort_done("rebase") == "Aborted the rebase — the tree is back where it stood"
    assert gitmodel.continue_done("rebase", None) == "Finished the rebase"
    stopped = gitmodel.continue_done("rebase", "rebase")
    assert stopped == "Continued the rebase — it stopped again on the next step"
    assert gitmodel.operation_failed("rebase", "error: unmerged", False) == "error: unmerged"
    assert gitmodel.operation_failed("rebase", "", False) == "`git rebase --continue` failed"
    assert gitmodel.operation_failed("merge", "", True) == "`git merge --abort` failed"


# -- the files list's context menu --------------------------------------------------


def test_file_menu_actions_per_side_code_and_disk():
    opens = ("open", "open-with")
    assert gitmodel.file_menu_actions("unstaged", "M", True) == (("stage", "discard"), opens)
    assert gitmodel.file_menu_actions("unstaged", "?", True) == (("stage", "discard"), opens)
    assert gitmodel.file_menu_actions("unstaged", None, True) == (("stage", "discard"), opens)
    # A deleted file: nothing on disk to open.
    assert gitmodel.file_menu_actions("unstaged", "D", False) == (("stage", "discard"),)
    assert gitmodel.file_menu_actions("staged", "A", True) == (("unstage",), opens)
    assert gitmodel.file_menu_actions("staged", "D", False) == (("unstage",),)
    # An unmerged row (always unstaged-side): stage marks it resolved, and
    # the two resolutions; never a discard.
    resolve = ("stage", "resolve-ours", "resolve-theirs")
    assert gitmodel.file_menu_actions("unstaged", "U", True) == (resolve, opens)
    assert gitmodel.file_menu_actions("unstaged", "U", False) == (resolve,)
    # A flat list (a commit, the branch, a range): revert.
    assert gitmodel.file_menu_actions("", "M", True) == (("revert",), opens)
    assert gitmodel.file_menu_actions("", None, False) == (("revert",),)
    assert gitmodel.resolve_side("resolve-ours") == "ours"
    assert gitmodel.resolve_side("resolve-theirs") == "theirs"
    assert gitmodel.resolve_side("stage") is None
    assert set(gitmodel.MENU_ACTIONS) == {
        "stage", "unstage", "discard", "resolve-ours", "resolve-theirs", "revert", "open", "open-with",
    }


def test_file_menu_labels_carry_the_operations_hint():
    assert gitmodel.file_menu_label("stage") == "Stage file"
    assert gitmodel.file_menu_label("unstage") == "Unstage file"
    assert gitmodel.file_menu_label("discard") == "Discard file…"
    assert gitmodel.file_menu_label("revert") == "Revert file"
    assert gitmodel.file_menu_label("open") == "Open in editor"
    assert gitmodel.file_menu_label("open-with") == "Open In…"
    assert gitmodel.file_menu_label("resolve-ours") == "Resolve with ours"
    assert gitmodel.file_menu_label("resolve-theirs") == "Resolve with theirs"
    assert gitmodel.file_menu_label("resolve-ours", "revert") == "Resolve with ours (HEAD)"
    assert gitmodel.file_menu_label("resolve-theirs", "revert") == "Resolve with theirs (the revert)"
    assert gitmodel.file_menu_label("resolve-theirs", "merge") == "Resolve with theirs (the merge)"
    assert gitmodel.file_menu_label("resolve-theirs", "cherry-pick") == "Resolve with theirs (the pick)"
    # A rebase turns the words around: HEAD is the upstream, theirs is the
    # user's own commit.
    assert gitmodel.file_menu_label("resolve-ours", "rebase") == "Resolve with ours (upstream)"
    assert gitmodel.file_menu_label("resolve-theirs", "rebase") == "Resolve with theirs (your commit)"
    assert gitmodel.file_menu_label("resolve-theirs", "am") == "Resolve with theirs (the patch)"
    assert gitmodel.file_menu_label("resolve-ours", "bogus") == "Resolve with ours"
    assert gitmodel.file_menu_label("bogus") == "bogus"


def test_resolve_words_say_what_each_side_is_and_whether_the_file_goes():
    heading, body, button = gitmodel.resolve_words("f.txt", "theirs", "revert", False)
    assert heading == "Resolve f.txt with theirs?"
    assert body.startswith(
        "Ours is HEAD, what the branch has now."
        " Theirs is what the revert restores: the reverted commit's parent."
    )
    assert "`git checkout --theirs -- f.txt && git add -- f.txt`" in body
    assert "Edits made to the conflict markers are lost." in body
    assert button == "Resolve"
    heading, body, _button = gitmodel.resolve_words("g.txt", "ours", "rebase", True)
    assert heading == "Resolve g.txt with ours?"
    assert (
        "Ours is the upstream the branch is being rebased onto. Theirs is your own commit, being replayed."
        in body
    )
    assert "Ours has no version of this file: resolving with it removes g.txt" in body
    assert "`git rm -- g.txt`" in body
    _heading, body, _button = gitmodel.resolve_words("h.txt", "theirs", None, True)
    assert "Ours is the index's ours side (stage 2). Theirs is the index's theirs side (stage 3)." in body
    assert "Theirs has no version of this file" in body
    assert gitmodel.side_meaning("merge", "theirs") == "the branch being merged in"
    assert gitmodel.side_meaning("cherry-pick", "ours") == "HEAD, the branch you are on"
    assert gitmodel.side_meaning("am", "theirs") == "the patch being applied"
    assert gitmodel.resolve_done("f.txt", "ours", False) == "Resolved f.txt with ours: staged"
    assert gitmodel.resolve_done("g.txt", "theirs", True) == "Resolved g.txt with theirs: removed"


def test_resolve_all_words_count_the_paths_and_name_the_removed_ones():
    heading, body, button = gitmodel.resolve_all_words(("a.txt", "b.txt"), "theirs", "revert", ())
    assert heading == "Resolve all 2 conflicts with theirs?"
    assert body.startswith(
        "Ours is HEAD, what the branch has now."
        " Theirs is what the revert restores: the reverted commit's parent."
    )
    assert "replaced with the theirs version and staged as resolved" in body
    assert "`git checkout --theirs` then `git add`" in body
    assert "Edits made to the conflict markers are lost." in body
    assert "`git rm`" not in body
    assert button == "Resolve all"
    heading, body, _button = gitmodel.resolve_all_words(("g.txt",), "ours", "rebase", ("g.txt",))
    assert heading == "Resolve the conflict with ours?"
    assert "Ours is the upstream the branch is being rebased onto." in body
    assert "Ours has no version of g.txt: resolving removes it from the working tree" in body
    many = tuple(f"f{i}.txt" for i in range(10))
    _heading, body, _button = gitmodel.resolve_all_words(many, "theirs", None, many)
    assert "Theirs has no version of f0.txt, f1.txt, f2.txt, f3.txt, f4.txt, f5.txt, f6.txt, f7.txt, …" in body  # noqa: E501
    assert "removes them from the working tree" in body
    # The action row's two items are the rows' own labels.
    assert [gitmodel.file_menu_label(a, "rebase") for a in gitmodel.RESOLVE_ALL_ACTIONS] == [
        "Resolve with ours (upstream)",
        "Resolve with theirs (your commit)",
    ]
    assert [gitmodel.resolve_side(a) for a in gitmodel.RESOLVE_ALL_ACTIONS] == ["ours", "theirs"]
    assert gitmodel.resolve_all_done(3, 0, "ours") == "Resolved 3 conflicts with ours: staged"
    assert gitmodel.resolve_all_done(0, 2, "theirs") == "Resolved 2 conflicts with theirs: removed"
    mixed = gitmodel.resolve_all_done(2, 1, "theirs")
    assert mixed == "Resolved 3 conflicts with theirs: 2 staged, 1 removed"
    stopped = gitmodel.resolve_all_failed("b.txt", "error: nope", 1)
    assert stopped == "Stopped at b.txt after 1 resolved: error: nope"
    assert gitmodel.resolve_all_failed("a.txt", "", 0) == "Couldn't resolve a.txt: git failed"


def test_unmerged_paths_are_the_status_u_rows_in_order():
    status = gitmodel.parse_status_v2(
        "1 .M N... 100644 100644 100644 aaaa bbbb x.txt\0"
        "u UU N... 100644 100644 100644 100644 aaaa bbbb cccc b.txt\0"
        "u DU N... 100644 100644 100644 100644 aaaa bbbb cccc a.txt\0"
    )
    assert gitmodel.unmerged_paths(status) == ("b.txt", "a.txt")
    assert gitmodel.unmerged_paths(None) == ()


def test_discard_and_stage_words_follow_the_status_code():
    trash = ("Move to the trash?", "Move n.txt to the trash?", "Move to trash")
    assert gitmodel.discard_words("n.txt", "?") == trash
    restore = ("Restore the file?", "Restore d.txt from the index?", "Restore")
    assert gitmodel.discard_words("d.txt", "D") == restore
    assert gitmodel.discard_words("f.txt", "M") == (
        "Discard the changes?", "Discard the changes to f.txt? This cannot be undone.", "Discard",
    )
    assert gitmodel.discard_words("f.txt", None)[0] == "Discard the changes?"
    assert gitmodel.discard_done("n.txt", "?") == "Moved n.txt to the trash"
    assert gitmodel.discard_done("d.txt", "D") == "Restored d.txt"
    assert gitmodel.discard_done("f.txt", "M") == "Discarded the changes to f.txt"
    assert gitmodel.stage_done("f.txt", True) == "Staged f.txt"
    assert gitmodel.stage_done("f.txt", False) == "Unstaged f.txt"
