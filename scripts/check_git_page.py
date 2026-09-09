#!/usr/bin/env python3
# New in the ghackett fork of agent-session-manager (GPL-3.0).
"""Wiring check for the git page (collins/gitpage.py) — run on a dev machine.

Exercises the GTK side that tests/test_gitloads.py, tests/test_diffmodel.py
and tests/test_gitinfo.py can't reach: a real GitPage in a real window over
a real repository, on a PATH holding git alone. A pass over the sidebar
(collins/gitsidebar.py) checks its commits list off the repository (the
working tree row, the branch header, the `main..HEAD` commits, the default
branch's group; no stack group while the parent is the default, no `↑`
without a remote), the header toggle and its persistence (page_state's
"sidebar", a restore with it off), the collapse under the breakpoint on a
500 px window and the return on a 900 px one, a commit row's click loading
`show <sha>` (and the default header's doing nothing), a staged-side file
click loading the index then revealing the file, stage_all and commit
moving the repository with exactly one reload each (and none on the
following tick), and `git_log_page` paging the list. A pass over the view
then stages every kind of change in the repository (two unstaged hunks
with gaps around them, a staged edit and a staged rename, a modified
binary, an image before and after, an untracked text file and an untracked
picture, a deletion, a mode change) and checks each section kind renders
through the view's probes (`file_rows`, `hunk_rows`, `gap_rows`,
`badge_rows`, `hunk_serials`), a gap expands, a files-list click reveals,
an external edit that keeps the line counts reloads through the file
monitors within 2 s keeping the untouched hunk's widget and the keyboard,
the `git.*` actions are routed, the highlight follows a scroll to the end
(and the pinned header), the files filter hides a section, Ctrl+F counts
across hunks, the staged and commit loads land, settings and the keys
reach the view with `page_state` untouched by a layout change, the notes
and the staging interface work (check_native_notes,
check_native_mutations), a page restores into a commit (and into the
default mode for a commit git no longer has), and a page opened outside a
repository shows the not-a-repository card.

This is a script, not a pytest test, on purpose: tests/conftest.py blocks
the GTK-stack namespaces for the whole suite so local runs reproduce CI.
Testing widgets for real means running this by hand, behind a display
nobody is looking at:

    bash .agents/capture-screenshots/scripts/with-headless-display.sh \\
        python3 scripts/check_git_page.py

(CI runs it under Xvfb through scripts/run_e2e.py.) Skips, exiting 0 with a
message, when git isn't installed — the temp repository needs it.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

Adw.init()

from collins import gitloads, gitops, gitpage  # noqa: E402
from collins.diffnotes import HighlightSpec, NoteSpec  # noqa: E402
from collins.editor import GtkSource  # noqa: E402
from collins.gitpage import GitPage  # noqa: E402

PASSED = 0
FAILED = 0

# How long the page gets for each asynchronous step (the open's stack read
# plus the first diff read, a reload).
STEP_TIMEOUT_S = 8.0

def check(label: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  ok  {label}")
    else:
        FAILED += 1
        print(f"FAIL  {label}  {detail}")


def wait_for(condition, timeout: float = STEP_TIMEOUT_S) -> bool:
    """Spin the main loop until *condition()* holds or *timeout* passes."""
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if condition():
            return True
        time.sleep(0.02)
    return condition()


# Resolved before PATH is swapped out from under the page.
GIT = shutil.which("git")


def git(repo: str, *args: str) -> None:
    subprocess.run(
        [GIT, "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def make_repo(root: str) -> str:
    """main and feat at one commit, plus `base`: a second branch at that
    commit (on the trunk, so never in feat's stack). The identity is set
    in the repository's own config:
    the sidebar's native commit runs a plain `git commit`, which needs one
    (CI's container has no global identity)."""
    repo = os.path.join(root, "repo")
    os.mkdir(repo)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "Test")
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-qm", "first")
    git(repo, "branch", "base")
    git(repo, "checkout", "-qb", "feat")
    return repo


def head_sha(repo: str) -> str:
    return subprocess.run(
        [GIT, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def log_shas(repo: str, *range_args: str) -> list[str]:
    """`git log --format=%H <range>`, newest first — what the commits list
    is expected to show for a group."""
    return subprocess.run(
        [GIT, "log", "--format=%H", *range_args, "--"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.split()


def git_out(repo: str, *args: str) -> str:
    return subprocess.run([GIT, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def card_title(page: GitPage) -> str:
    card = page._card_slot.get_child()
    return card.get_title() if card is not None else ""


# What every page in this check runs on: stacked, so the split padding
# pass never enters the timings.
SETTINGS = {"git_layout": "stack"}
# The `second` commit's body (check_sidebar): markdown, and more lines than
# the card's fold shows, so the commit card has something to fold.
COMMIT_BODY = "\n".join(
    [
        "Why this commit exists, in **bold** terms.",
        "",
        "- first item",
        "- second item",
        "- third item",
        "",
        "```sh",
        "echo one",
        "echo two",
        "```",
        "",
        "Fourth paragraph.",
        "",
        "Fifth paragraph.",
        "",
        "Last line of the body.",
    ]
)


def _descendants(widget: Gtk.Widget) -> list[Gtk.Widget]:
    found = []
    child = widget.get_first_child()
    while child is not None:
        found.append(child)
        found.extend(_descendants(child))
        child = child.get_next_sibling()
    return found


def check_sidebar(repo: str) -> None:
    """The sidebar (collins/gitsidebar.py) beside the view: its lists off
    the real repository, the header toggle and its persistence, the
    collapse under the breakpoint, clicks that load and reveal, the
    mutations, and the page size. The page's own `loaded` / `shows` say
    what landed and the view's current file is what the highlight
    follows."""
    print("-- the sidebar")
    # feat gets a commit of its own first (one line added to a.txt), so
    # the current group has something to list and a row to click.
    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("two\n")
    # ... with a markdown body long enough to fold, for the commit card.
    git(repo, "commit", "-qam", "second", "-m", COMMIT_BODY)
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
    )
    page.apply_settings(SETTINGS)
    sidebar = page.sidebar
    # A 500 px window first: under the breakpoint the page is one column
    # at a time — the diff, until the toggle swaps the panels in for it.
    window = Gtk.Window(title="sidebar", default_width=500, default_height=600)
    window.set_child(page)
    window.present()
    check("sidebar: the view is up", wait_for(page.settled))
    narrow = wait_for(lambda: page.narrow)
    check("a 500 px window is under the breakpoint", narrow)
    check("the sidebar is hidden there, the diff shown", not page.sidebar_shown and page.diff_shown)
    check("the toggle is sensitive, off, and offers the panels",
          page._sidebar_toggle.get_sensitive() and not page._sidebar_toggle.get_active()
          and page._sidebar_toggle.get_tooltip_text() == "Show the commits and files panels",
          (page._sidebar_toggle.get_sensitive(), page._sidebar_toggle.get_tooltip_text()))
    check("the toggle's word still reads shown (it persists)", page.sidebar_wanted and "sidebar" not in page.page_state())
    page._sidebar_toggle.set_active(True)  # the header press
    check("pressed, the panels stand in for the diff", page.sidebar_shown and not page.diff_shown)
    check("the toggle then reads Back to the diff", page._sidebar_toggle.get_tooltip_text() == "Back to the diff")
    check("the word is untouched by the swap", page.sidebar_wanted and "sidebar" not in page.page_state())
    narrow_rows = wait_for(lambda: sidebar.click_commit_row("worktree"))
    check("a row picked from the panels drops back to the diff",
          narrow_rows and page.diff_shown and not page.sidebar_shown and not page._sidebar_toggle.get_active(),
          (page.diff_shown, page.sidebar_shown))
    page.show_panels(True)
    page.sidebar.set_filter_text("a")
    page.sidebar.emit("filter-escaped")
    check("Escape in the filter drops back to the diff too", page.diff_shown and not page.sidebar_shown)
    page.show_panels(True)
    page.load("staged")
    check("a load (Ctrl+2, the host, the tool) drops back to the diff", page.diff_shown and not page.sidebar_shown)
    check("sidebar: the staged load landed", wait_for(page.settled))
    page.load("unstaged")
    check("sidebar: the unstaged load landed", wait_for(page.settled))
    page.show_panels(True)
    page.set_size_request(900, -1)  # the toplevel grows to its child's minimum
    wide = wait_for(lambda: not page.narrow and page.sidebar_shown)
    check("a 900 px page shows the sidebar beside the diff again", wide and page.diff_shown, (page.narrow, page.sidebar_shown))
    check("the toggle reads the word there", page._sidebar_toggle.get_active()
          and page._sidebar_toggle.get_tooltip_text() == "Hide the commits and files panels")
    check(
        "the view keeps its width beside the sidebar",
        wait_for(lambda: page.diff_view.get_width() >= 400),
        page.diff_view.get_width(),
    )

    # -- the toggle and its persistence -------------------------------------------------
    page.set_sidebar_wanted(False)
    check("the toggle hides the sidebar", not page.sidebar_shown)
    check("page_state says sidebar: false", page.page_state().get("sidebar") is False, page.page_state())
    page.set_sidebar_wanted(True)
    check("and shows it again, dropping the key", page.sidebar_shown and "sidebar" not in page.page_state())
    restored = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        sidebar=gitloads.decode_sidebar({"kind": "git", "loaded": "unstaged", "sidebar": False}),
    )
    check(
        "a page restored with sidebar: false keeps it hidden and says so before it is shown",
        not restored.sidebar_wanted and restored.page_state() == {"kind": "git", "loaded": "unstaged", "sidebar": False},
        restored.page_state(),
    )

    # -- the commits list off the real repository ---------------------------------------
    current = log_shas(repo, "main..HEAD")
    trunk = log_shas(repo, "main")
    landed = wait_for(lambda: [r.sha for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"] == current)
    rows = sidebar.commit_rows()
    check("the current group lists main..HEAD, newest first", landed, [r.label for r in rows])
    check(
        "the working tree row, then header feat",
        len(rows) > 1 and rows[0].kind == "worktree" and rows[1].kind == "header" and rows[1].label == "feat",
        [(r.kind, r.label) for r in rows[:2]],
    )
    check(
        "the default group: header main and its commits",
        any(r.kind == "header" and r.group == "default" and r.label == "main" for r in rows)
        and [r.sha for r in rows if r.kind == "commit" and r.group == "default"] == trunk,
        [(r.kind, r.group, r.label) for r in rows],
    )
    check("no stack group while the parent is the default", not any(r.group.startswith("stack:") for r in rows))
    check("no ↑ without a remote", not any(r.unpushed for r in rows))
    check("no load more… under a page of 20", not any(r.kind == "more" for r in rows))
    check("the working tree row is the loaded one", sidebar.loaded_row_id() == "worktree", sidebar.loaded_row_id())
    check(
        "the loaded row wears the mark; the working tree is no branch's, so no header the highlight",
        sidebar._commit_widgets["worktree"].has_css_class("git-row-loaded")
        and not any(w.has_css_class("git-group-loaded") for w in sidebar._commit_widgets.values()),
    )

    # -- the caret folds a group's rows under its header -----------------------------------
    sidebar.collapse_group("current")
    folded = [r.id for r in rows if r.group == "current" and r.kind != "header"]
    check(
        "folding the current group hides its rows, the header and the working tree row stay",
        sidebar.collapsed_groups() == {"current"}
        and folded
        and not any(sidebar._commit_widgets[i].get_visible() for i in folded)
        and sidebar._commit_widgets["worktree"].get_visible()
        and sidebar._commit_widgets["header:current"].get_visible()
        and sidebar._commit_widgets["header:default"].get_visible(),
        sidebar.collapsed_groups(),
    )
    sidebar.refresh_commits()
    wait_for(lambda: sidebar.commit_rows() and not sidebar._commit_widgets[folded[0]].get_visible())
    check(
        "a re-read keeps the fold",
        sidebar.collapsed_groups() == {"current"} and not sidebar._commit_widgets[folded[0]].get_visible(),
    )
    sidebar.collapse_group("current")
    check(
        "the caret again unfolds them",
        not sidebar.collapsed_groups() and all(sidebar._commit_widgets[i].get_visible() for i in folded),
    )
    check("folding loaded nothing", sidebar.loaded_row_id() == "worktree" and page.loaded == "unstaged")

    # -- a commit row click loads it; the default header loads nothing --------------------
    def shows(loaded) -> bool:
        return page.shows(loaded) and page.settled()

    sha = current[0]
    subject = git_out(repo, "log", "-1", "--format=%s", sha).strip()
    check("the commit row is drawn", sidebar.click_commit_row(f"commit:{sha}"))
    landed = wait_for(lambda: shows({"show": sha}))
    check("a commit row click loads `show <sha>`", landed, page.loaded)
    check("the ▸ row follows the load", wait_for(lambda: sidebar.loaded_row_id() == f"commit:{sha}"), sidebar.loaded_row_id())
    check(
        "the breadcrumb names the commit, <sha7> <subject>",
        wait_for(lambda: page.breadcrumb_text() == f"{sha[:7]} {subject}"),
        page.breadcrumb_text(),
    )
    # -- the commit card: subject, byline, the body folded like a PR description --------
    card = page.commit_card
    check("the commit card shows for a commit load", card.get_visible() and card.subject_text() == subject, card.subject_text())
    check("its byline names the author and the short sha", card.byline_text().startswith("Test ") and card.byline_text().endswith(sha[:7]), card.byline_text())
    check("the body waits whole behind Show more", card.folded() is True and card.toggle_text() == "Show more", (card.folded(), card.toggle_text()))
    check("folded, the card is the subject and the byline alone", card.body_labels() == [], card.body_labels())
    card.set_folded(False)
    full = card.body_labels()
    check("Show more brings the whole body out, markdown rendered", any(t.startswith("Why this commit") for t in full) and any("Last line" in t for t in full) and any(t.startswith("first item") for t in full) and not any("**" in t for t in full), full)
    check("the handle reads Show less and stays above the body's scroller", card.toggle_text() == "Show less" and card.handle_is_sticky(), card.toggle_text())
    check("the body's code fence is a source view", any(isinstance(w, GtkSource.View) for w in _descendants(card)))
    landed = wait_for(lambda: sidebar.file_rows().mode == "flat" and [f.path for f in sidebar.file_rows().flat] == ["a.txt"])
    check("a commit load lists its files flat, with counts", landed and sidebar.file_rows().flat[0].additions == 1, sidebar.file_rows())
    reads: list[object] = []
    original_read = page._read_diff

    def counted_read(loaded) -> None:
        reads.append(loaded)
        original_read(loaded)

    page._read_diff = counted_read
    sidebar.click_commit_row("header:default")
    wait_for(lambda: False, timeout=0.3)
    check("the default header loads nothing", shows({"show": sha}) and reads == [], (page.loaded, reads))
    sidebar.click_commit_row("header:current")
    landed = wait_for(lambda: shows("branch"))
    check("the current header loads the branch diff", landed, page.loaded)
    check("the header row is the loaded one", wait_for(lambda: sidebar.loaded_row_id() == "header:current"))
    check("a load that isn't a commit empties the card", not card.get_visible() and card.message is None)

    # -- the files list on the working tree: the other side's click loads it ------------
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("staged\n")
    git(repo, "add", "a.txt")  # the index differs from HEAD: a.txt is on the staged side
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("staged\nand more\n")  # and the tree from the index: on the unstaged side too
    sidebar.click_commit_row("worktree")
    landed = wait_for(lambda: shows("unstaged"))
    check("the working tree row loads the unstaged changes", landed, page.loaded)
    landed = wait_for(
        lambda: sidebar.file_rows().mode == "split"
        and sidebar.file_rows().live == "unstaged"
        and [f.path for f in sidebar.file_rows().staged] == ["a.txt"]
        and [f.path for f in sidebar.file_rows().unstaged] == ["a.txt"]
    )
    check("the working tree splits: the view's files live, the other side off git status", landed, sidebar.file_rows())
    check(
        "the live row carries counts and the status letter, the other side its letter alone",
        sidebar.file_rows().unstaged[0].live
        and sidebar.file_rows().unstaged[0].code == "M"
        and not sidebar.file_rows().staged[0].live
        and sidebar.file_rows().staged[0].code == "M",
        sidebar.file_rows(),
    )
    check(
        "the view's current file puts the highlight on its row",
        wait_for(lambda: sidebar.selected_path == "a.txt"),
        sidebar.selected_path,
    )
    check("the live row is highlighted", sidebar._file_widgets[("unstaged", "a.txt")].has_css_class("git-file-selected"))
    check("the staged-side row is drawn", sidebar.click_file_row("a.txt", "staged"))
    landed = wait_for(lambda: shows("staged") and page.diff_view.current()[0] == "a.txt")
    check("a staged-side click loads the index, then reveals the file", landed, (page.loaded, page.diff_view.current()))
    focus = window.get_focus()
    check("the keyboard landed in the view", focus is not None and focus.is_ancestor(page.diff_view), focus)
    check("the staged side is live now", wait_for(lambda: sidebar.file_rows().live == "staged"), sidebar.file_rows())
    reads_before = len(reads)
    sidebar.click_file_row("a.txt", "staged")
    wait_for(lambda: False, timeout=0.3)
    check("a live-side click reveals without a reload", shows("staged") and page.diff_view.current()[0] == "a.txt" and len(reads) == reads_before, (page.loaded, len(reads) - reads_before))
    sidebar.click_section("unstaged")
    landed = wait_for(lambda: shows("unstaged"))
    check("the other side's heading loads that side", landed, page.loaded)
    check(
        "stage all, unstage all and commit are live on the working tree",
        sidebar._stage_all_button.get_sensitive() and sidebar._commit_button.get_sensitive(),
    )

    # -- mutations: stage all, commit --------------------------------------------------------
    # Each reloads the view exactly once: the page's read is counted
    # through its own method.
    reads_before = len(reads)
    sidebar.stage_all()
    landed = wait_for(lambda: not sidebar.busy and len(reads) == reads_before + 1 and page.settled())
    check("stage_all reloads the view once", landed, len(reads) - reads_before)
    check("and staged the tree", git_out(repo, "diff", "--name-only") == "" and git_out(repo, "diff", "--cached", "--name-only").split() == ["a.txt"])
    check("the unstaged view emptied", wait_for(lambda: page.diff_view.file_rows() == []), page.diff_view.file_rows())
    check(
        "the files list moved a.txt to the staged side",
        wait_for(lambda: [f.path for f in sidebar.file_rows().staged] == ["a.txt"] and not sidebar.file_rows().unstaged),
        sidebar.file_rows(),
    )
    page.poll_tick()
    wait_for(page.settled)
    wait_for(lambda: False, timeout=0.3)
    check("the following tick reloads nothing more", len(reads) == reads_before + 1, len(reads) - reads_before)
    reads_before = len(reads)
    sidebar.commit("native commit", None)
    landed = wait_for(lambda: not sidebar.busy and len(reads) == reads_before + 1 and page.settled())
    check("commit reloads the view once", landed, len(reads) - reads_before)
    check("and made the commit", git_out(repo, "log", "-1", "--format=%s").strip() == "native commit", git_out(repo, "log", "-1", "--format=%s"))
    page.poll_tick()
    wait_for(page.settled)
    wait_for(lambda: False, timeout=0.3)
    check("the following tick reloads nothing more", len(reads) == reads_before + 1, len(reads) - reads_before)
    landed = wait_for(lambda: [r.sha for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"] == log_shas(repo, "main..HEAD"))
    check("the commits list gained the commit", landed, [r.label for r in sidebar.commit_rows()])

    # -- revert a commit: the menu, committed, then into the working tree ------------------
    native_sha = log_shas(repo, "-1", "HEAD")[0]
    native_row = next(r for r in sidebar.commit_rows() if r.sha == native_sha)
    check("a commit row's menu offers Copy sha, Revert… and Reload", sidebar.commit_menu_labels(native_row.id) == ["Copy sha", "Revert…", "Reload"], sidebar.commit_menu_labels(native_row.id))
    check("the working tree row's menu is Reload alone", sidebar.commit_menu_labels("worktree") == ["Reload"], sidebar.commit_menu_labels("worktree"))
    reads_before = len(reads)
    sidebar.revert(native_sha, True)
    landed = wait_for(lambda: not sidebar.busy and len(reads) == reads_before + 1 and page.settled())
    check("a committed revert reloads the view once", landed, len(reads) - reads_before)
    check("and made the revert commit", git_out(repo, "log", "-1", "--format=%s").startswith('Revert "native commit"'), git_out(repo, "log", "-1", "--format=%s"))
    check("with the tree clean", git_out(repo, "status", "--porcelain") == "", git_out(repo, "status", "--porcelain"))
    revert_sha = log_shas(repo, "-1", "HEAD")[0]
    landed = wait_for(lambda: any(r.sha == revert_sha for r in sidebar.commit_rows()))
    check("the commits list gained the revert", landed, [r.label for r in sidebar.commit_rows()])
    reads_before = len(reads)
    sidebar.revert(revert_sha, False)
    landed = wait_for(lambda: not sidebar.busy and len(reads) == reads_before + 1 and page.settled())
    check("a working-tree revert reloads the view once", landed, len(reads) - reads_before)
    check("and staged the reverse change without committing", git_out(repo, "diff", "--cached", "--name-only").split() == ["a.txt"] and log_shas(repo, "-1", "HEAD")[0] == revert_sha, git_out(repo, "status", "--porcelain"))
    check("leaving no revert half-finished", gitops.in_progress_operation(os.path.join(repo, ".git")) is None, gitops.in_progress_operation(os.path.join(repo, ".git")))
    check(
        "the files list shows a.txt on the staged side",
        wait_for(lambda: [f.path for f in sidebar.file_rows().staged] == ["a.txt"] and not sidebar.file_rows().unstaged),
        sidebar.file_rows(),
    )
    git(repo, "reset", "-q", "--hard", "HEAD")
    page.poll_tick()
    wait_for(page.settled)
    check("the tree is clean again for what follows", wait_for(lambda: not sidebar.file_rows().staged and not sidebar.file_rows().unstaged), sidebar.file_rows())

    # -- a half-finished operation: the bar over the diff, Abort… and Continue -------------
    # A commit that rewrites a.txt whole, then the native revert of the
    # commit that last touched it: the revert stops on a conflict and the
    # bar comes up over the working-tree diff.
    bar = page.operation_bar
    check("nothing half-finished: the bar is hidden", not bar.get_visible() and bar.operation is None, bar.operation)
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("clash\n")
    git(repo, "commit", "-qam", "clash")
    sidebar.revert(native_sha, True)
    landed = wait_for(lambda: not sidebar.busy and page.settled() and bar.operation is not None)
    check("a revert that stops on conflicts brings the bar up", landed and bar.get_visible(), (landed, bar.get_visible(), bar.operation))
    check("naming the revert", bar.operation is not None and bar.operation.kind == "revert" and bar.title_text() == "Revert in progress", bar.title_text())
    check("and counting the unmerged file", bar.hint_text().startswith("Unmerged files: 1."), bar.hint_text())
    check("with its buttons live", bar.buttons_sensitive())
    # The clash is listed under CONFLICTS the moment the operation stops,
    # whichever side is loaded; on the unstaged load its diff is the
    # working tree against our side, the markers painted, the file
    # header offering Stage file alone.
    files = sidebar.file_rows()
    check("the clash is listed under CONFLICTS at once", [r.path for r in files.conflicts] == ["a.txt"] and all(r.path != "a.txt" for r in files.unstaged), files)
    if page.loaded != "unstaged":
        sidebar.click_file_row("a.txt", "unstaged")
    landed = wait_for(lambda: page.settled() and page.loaded == "unstaged" and ("a.txt", "change", True) in page.diff_view.file_rows())
    check("and its diff shows on the unstaged load", landed, (page.loaded, page.diff_view.file_rows()))
    check("as a live row with counts", landed and sidebar.file_rows().conflicts[0].live and sidebar.file_rows().conflicts[0].additions, sidebar.file_rows().conflicts)
    check("wearing the conflict badge", any(p == "a.txt" and "conflict" in b for p, b, _ in page.diff_view.badge_rows()), page.diff_view.badge_rows())
    markers = page.diff_view.conflict_rows("a.txt", 0)
    check("with the three markers painted", [m.split(" ")[0] for m in markers] == ["<<<<<<<", "=======", ">>>>>>>"], markers)
    check("and the file header offering Stage file alone", page.diff_view.file_action_labels("a.txt") == ("Stage file", None), page.diff_view.file_action_labels("a.txt"))
    # The conflict row's context menu: Stage, the two resolutions with the
    # revert's hints, and the editor (no footer app is configured, so no
    # Open In…). Resolve with ours asks — the body says what each side is
    # — then checks the revert's copy out and stages it as resolved
    # (ours would be HEAD's own bytes: nothing to stage, a clean tree).
    labels = sidebar.file_menu_labels("a.txt", "unstaged")
    check("the conflict row's menu offers Stage, the two resolutions and the editor", labels == ["Stage file", "Resolve with ours (HEAD)", "Resolve with theirs (the revert)", "Open in editor"], labels)
    check("with no Open In… while no footer app is configured", sidebar.file_open_with_labels("a.txt", "unstaged") == [], sidebar.file_open_with_labels("a.txt", "unstaged"))
    resolve_asked: list[tuple[str, str, str]] = []
    real_confirm = gitpage.dialogs.confirm_dialog

    def confirm_resolve(parent, heading, body, confirm_label, on_confirm, *args, **kwargs):
        resolve_asked.append((heading, body, confirm_label))
        on_confirm()

    gitpage.dialogs.confirm_dialog = confirm_resolve
    try:
        check("Resolve with theirs from the row's menu", sidebar.activate_file_menu("a.txt", "Resolve with theirs (the revert)", "unstaged"))
        landed = wait_for(lambda: not sidebar.busy and page.settled() and git_out(repo, "ls-files", "-u", "--", "a.txt") == "")
    finally:
        gitpage.dialogs.confirm_dialog = real_confirm
    check("it asked, naming both sides for a revert", len(resolve_asked) == 1 and resolve_asked[0][0] == "Resolve a.txt with theirs?" and resolve_asked[0][2] == "Resolve" and "Ours is HEAD, what the branch has now. Theirs is what the revert restores" in resolve_asked[0][1], resolve_asked)
    check("and the revert's copy is checked out and staged as resolved", landed and open(os.path.join(repo, "a.txt")).read() != "clash\n" and git_out(repo, "status", "--porcelain").split("\n")[0] == "M  a.txt", (landed, git_out(repo, "status", "--porcelain")))
    landed = wait_for(lambda: page.settled() and bar.hint_text().startswith("Nothing is left unmerged"))
    check("the bar's hint follows the resolution", landed, bar.hint_text())
    check("and the CONFLICTS section is gone", not sidebar.file_rows().conflicts and [r.path for r in sidebar.file_rows().staged] == ["a.txt"], sidebar.file_rows())
    # Abort… asks first (the resolutions made since are lost); confirmed,
    # the tree goes back and the bar goes down with the reload.
    asked: list[tuple[str, str]] = []

    def confirm_abort(parent, heading, body, confirm_label, on_confirm, *args, **kwargs):
        asked.append((heading, confirm_label))
        on_confirm()

    gitpage.dialogs.confirm_dialog = confirm_abort
    try:
        reads_before = len(reads)
        bar.click_abort()
        landed = wait_for(lambda: not sidebar.busy and page.settled() and bar.operation is None)
    finally:
        gitpage.dialogs.confirm_dialog = real_confirm
    check("Abort… asked: Abort the revert?", asked == [("Abort the revert?", "Abort")], asked)
    check("and the confirmed abort took the bar down", landed and not bar.get_visible(), (landed, bar.get_visible()))
    check("with the tree back where it stood", git_out(repo, "status", "--porcelain") == "" and gitops.in_progress(os.path.join(repo, ".git")) is None, git_out(repo, "status", "--porcelain"))
    check("and the view reloaded once", len(reads) == reads_before + 1, len(reads) - reads_before)
    # A cherry-pick stopped from a shell: the tick's signature (the marker
    # is part of it) brings the bar up; the conflict resolved and staged,
    # the next tick re-words the hint; Continue finishes it with no editor.
    picked = subprocess.run([GIT, "-c", "user.email=t@example.com", "-c", "user.name=Test", "cherry-pick", native_sha], cwd=repo, capture_output=True, text=True)
    check("a cherry-pick of the same commit stops on the clash", picked.returncode != 0, picked.stderr)
    page.poll_tick()
    landed = wait_for(lambda: page.settled() and bar.operation is not None and bar.operation.kind == "cherry-pick")
    check("the tick brings the bar up for a cherry-pick started elsewhere", landed and bar.title_text() == "Cherry-pick in progress", (landed, bar.get_visible(), bar.title_text()))
    with open(os.path.join(repo, "a.txt"), "w") as fh:
        fh.write("resolved\n")
    git(repo, "add", "a.txt")
    page.poll_tick()
    landed = wait_for(lambda: page.settled() and bar.hint_text().startswith("Nothing is left unmerged"))
    check("resolved and staged, the hint says so", landed, bar.hint_text())
    bar.click_continue()
    landed = wait_for(lambda: not sidebar.busy and page.settled() and bar.operation is None)
    check("Continue finishes the cherry-pick and takes the bar down", landed and not bar.get_visible(), (landed, bar.get_visible()))
    check("with the commit made and no editor asked", git_out(repo, "log", "-1", "--format=%s").strip() == "native commit" and git_out(repo, "status", "--porcelain") == "", git_out(repo, "log", "-1", "--format=%s"))
    check("and nothing half-finished", gitops.in_progress(os.path.join(repo, ".git")) is None, gitops.in_progress(os.path.join(repo, ".git")))

    # -- an ask that lands while a read is out re-reads --------------------------------------
    # The read runs at once but lands late (gated), so it carries the tree
    # before the `git add`; the tick meanwhile sees the index move and asks
    # for the same load. The landed read must not stand in for that ask:
    # the tick's signature already moved past the add, so nothing else
    # would ever reload, and the view would show a.txt as unstaged for good.
    gate = threading.Event()
    real_read_diff = gitops.read_diff
    git_reads: list[object] = []  # the reads git actually ran (a parked ask is a _read_diff call, not a read)

    def late_read_diff(*args, **kwargs):
        read = real_read_diff(*args, **kwargs)
        git_reads.append(args[1] if len(args) > 1 else kwargs.get("load"))
        gate.wait(STEP_TIMEOUT_S)
        return read

    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("pending\n")
    gitops.read_diff = late_read_diff
    try:
        page.load("unstaged")
        wait_for(lambda: len(git_reads) == 1)  # the worker is past its read, held before landing
        git(repo, "add", "a.txt")
        page.poll_tick()
        check("the tick parked its ask behind the read in flight", page._pending_load == "unstaged" and not page.settled(), (page._pending_load, page.settled()))
        gate.set()
        landed = wait_for(lambda: len(git_reads) == 2 and page.settled())
    finally:
        gitops.read_diff = real_read_diff
    check("the parked ask re-reads after the stale read lands", landed, git_reads)
    check("the re-read shows the index move: the unstaged view is empty", wait_for(lambda: page.diff_view.file_rows() == []), page.diff_view.file_rows())
    check(
        "and the files list has a.txt on the staged side alone",
        wait_for(lambda: [f.path for f in sidebar.file_rows().staged] == ["a.txt"] and not sidebar.file_rows().unstaged),
        sidebar.file_rows(),
    )
    # The same race with a navigate queued: a staged-side click on a.txt
    # asks for the unstaged side (a.txt is dirty again) and waits for that
    # read; the file is staged while the read is out. The reveal must ride
    # the re-read — where a.txt is gone, so it toasts — not the stale
    # view, where it would have scrolled to a section about to be redrawn.
    page.load("staged")
    wait_for(lambda: page.settled() and page.loaded == "staged")
    with open(os.path.join(repo, "a.txt"), "a") as fh:
        fh.write("more\n")
    toasts: list[str] = []
    real_toast = page._toast
    page._toast = lambda text: (toasts.append(text), real_toast(text))
    gate.clear()
    git_reads.clear()
    gitops.read_diff = late_read_diff
    try:
        sidebar.emit("navigate-requested", "a.txt", "unstaged")
        wait_for(lambda: len(git_reads) == 1)
        check("the click queued its reveal behind the load", page._pending_navigate == ("a.txt", "unstaged") and page.loaded == "unstaged", page._pending_navigate)
        git(repo, "add", "a.txt")
        page.poll_tick()
        check("the tick parked a re-read again", page._pending_load == "unstaged", page._pending_load)
        gate.set()
        landed = wait_for(lambda: len(git_reads) == 2 and page.settled())
    finally:
        gitops.read_diff = real_read_diff
        page._toast = real_toast
    check("the re-read landed and the queued reveal ran on it: a.txt is gone, so it toasted", landed and page._pending_navigate is None and toasts == ["a.txt isn't in this diff"], (toasts, page._pending_navigate))
    check("the unstaged view is empty again", wait_for(lambda: page.diff_view.file_rows() == []), page.diff_view.file_rows())
    git(repo, "commit", "-qm", "pending committed")
    page.poll_tick()
    wait_for(page.settled)

    # -- the page size pages the current group ------------------------------------------------
    while len(log_shas(repo, "main..HEAD")) < 7:
        with open(os.path.join(repo, "a.txt"), "a") as fh:
            fh.write("more\n")
        git(repo, "commit", "-qam", f"filler {len(log_shas(repo, 'main..HEAD'))}")
    page.apply_settings({**SETTINGS, "git_log_page": 5})
    landed = wait_for(lambda: any(r.kind == "more" and r.group == "current" for r in sidebar.commit_rows()))
    check("git_log_page 5 over 7 commits shows load more…", landed, [(r.kind, r.label) for r in sidebar.commit_rows()])
    check("five commits listed", len([r for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"]) == 5)
    sidebar.load_more("current")
    landed = wait_for(
        lambda: [r.sha for r in sidebar.commit_rows() if r.kind == "commit" and r.group == "current"] == log_shas(repo, "main..HEAD")
        and not any(r.kind == "more" and r.group == "current" for r in sidebar.commit_rows())
    )
    check("load more… lists them all and goes away", landed, [(r.kind, r.label) for r in sidebar.commit_rows()])
    wait_for(page.settled)
    page.page_closed()
    window.destroy()


def range_settled(view, hold_s: float = 0.3):
    """A wait_for condition: the view's scroll range has held still for
    *hold_s* — the expanded context views have validated their heights."""
    seen = {"upper": None, "since": 0.0}

    def settled() -> bool:
        upper = view._scroller.get_vadjustment().get_upper()
        now = time.monotonic()
        if upper != seen["upper"]:
            seen["upper"], seen["since"] = upper, now
            return False
        return now - seen["since"] >= hold_s

    return settled


def png_bytes(rgb: tuple[int, int, int]) -> bytes:
    """A 4×4 PNG of one colour — a picture git calls binary and the view
    calls an image."""
    import struct
    import zlib

    raw = b"".join(b"\x00" + bytes(rgb) * 4 for _ in range(4))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def write_file(repo: str, name: str, data: bytes | str) -> None:
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(os.path.join(repo, name), mode) as fh:
        fh.write(data)


def stage_native_fixture(repo: str) -> list[str]:
    """Every kind of change the view draws, on a fresh commit: unstaged
    edits (two hunks with gaps around them), a staged edit, an untracked
    file, a staged rename, a modified binary, an image before/after and an
    untracked image, a deleted file and a mode change. Returns text.txt's
    lines (the check edits them again)."""
    lines = [f"line {n}\n" for n in range(1, 61)]
    write_file(repo, "text.txt", "".join(lines))
    write_file(repo, "staged.txt", "".join(f"staged {n}\n" for n in range(1, 11)))
    write_file(repo, "old.txt", "".join(f"old {n}\n" for n in range(1, 6)))
    write_file(repo, "blob.bin", bytes(range(256)) * 4)
    write_file(repo, "pic.png", png_bytes((200, 30, 30)))
    write_file(repo, "gone.txt", "".join(f"gone {n}\n" for n in range(1, 4)))
    write_file(repo, "run.sh", "#!/bin/sh\necho run\n")
    os.chmod(os.path.join(repo, "run.sh"), 0o644)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "native fixture")
    # unstaged: two hunks in text.txt (a 1-line gap before, 33 between, 12 after)
    lines[4] = "line 5 changed\n"
    lines[44] = "line 45 changed\n"
    write_file(repo, "text.txt", "".join(lines))
    # staged: an edit, and a rename
    write_file(repo, "staged.txt", "".join(f"staged {n}\n" for n in range(1, 11)).replace("staged 3", "staged three"))
    git(repo, "add", "staged.txt")
    git(repo, "mv", "old.txt", "renamed.txt")
    # unstaged: a binary, an image, a deletion, a mode change
    write_file(repo, "blob.bin", bytes(range(255, -1, -1)) * 4)
    write_file(repo, "pic.png", png_bytes((30, 200, 30)))
    os.remove(os.path.join(repo, "gone.txt"))
    os.chmod(os.path.join(repo, "run.sh"), 0o755)
    # untracked: a text file and a picture
    write_file(repo, "untracked.txt", "nothing tracked here\n")
    write_file(repo, "new.png", png_bytes((30, 30, 200)))
    return lines


def clear_native_fixture(repo: str) -> None:
    git(repo, "reset", "-q", "--hard")
    git(repo, "clean", "-qfd")


def check_native(repo: str) -> None:
    """The view over a repository holding every kind of change: it opens
    and draws each section kind (badges,
    pictures, a rename on the staged side); a gap expands; the watch
    reloads an external edit within two seconds keeping an untouched
    hunk's widget and the keyboard; the files list follows the scroll and
    a click reveals; the page-local keys are routed; the filter hides
    sections; the find bar counts across hunks; loads switch (staged, a
    commit); settings and the keys reach the view and page_state
    round-trips a layout change; a page restores into a commit (and into
    the default mode for a commit git no longer has)."""
    print("-- the view")
    lines = stage_native_fixture(repo)
    fixture_sha = head_sha(repo)
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
    )
    page.apply_settings(SETTINGS)
    check("nothing is read before the page maps", not page.opened and not page.settled())
    opened: list[tuple[str, int]] = []
    page.diff_view.connect("open-requested", lambda _v, path, line: opened.append((path, line)))
    # An Adw.Window: the mutations check reaches the page's real confirm
    # dialog through get_visible_dialog (a bare Gtk.Window would host an
    # Adw.Dialog in a window of its own).
    window = Adw.Window(title="native", default_width=900, default_height=600)
    window.set_content(page)
    window.present()
    check("the view opens and the first read lands", wait_for(page.settled))
    check(
        "the stack shows the view, no card",
        page._stack.get_visible_child_name() == "view" and page.card is None,
        (page._stack.get_visible_child_name(), page.card),
    )
    check(
        "the header's find and menu show, and the sidebar's filter",
        page._find_toggle.get_visible()
        and page._menu_button.get_visible()
        and page.sidebar._filter_entry.get_visible(),
    )
    check("the breadcrumb reads the working tree", page.breadcrumb_text() == "working tree · unstaged")
    check("nothing to reveal for a file that isn't loaded", not page.reveal("nowhere.txt"))

    # -- every section kind renders -----------------------------------------------------
    view = page.diff_view
    kinds = {path: kind for path, kind, _shown in view.file_rows()}
    check(
        "the unstaged load draws a change, a binary, an image, a deletion, a mode change and the untracked files (a picture reads binary)",
        kinds == {
            "text.txt": "change",
            "blob.bin": "binary",
            "pic.png": "binary",
            "gone.txt": "deleted",
            "run.sh": "mode",
            "untracked.txt": "new",
            "new.png": "binary",
        },
        kinds,
    )
    badges = {label: (badge, picture) for label, badge, picture in view.badge_rows()}
    check(
        "the headers wear their badges; the images their pictures",
        badges.get("blob.bin") == ("binary", False)
        and badges.get("pic.png") == ("binary", True)
        and badges.get("gone.txt") == ("deleted", False)
        and badges.get("run.sh") == ("mode 100644 → 100755", False)
        and badges.get("text.txt") == ("", False)
        and badges.get("untracked.txt") == ("new", False)
        and badges.get("new.png") == ("new · binary", True),
        badges,
    )
    check("text.txt draws its two hunks", len(view.hunk_rows("text.txt")) == 2, view.hunk_rows("text.txt"))
    check(
        "the deleted file's one hunk holds its lines, the untracked file's its own",
        len(view.hunk_rows("gone.txt")) == 1 and len(view.hunk_rows("untracked.txt")) == 1,
        (view.hunk_rows("gone.txt"), view.hunk_rows("untracked.txt")),
    )
    check("a binary and a mode change have no hunk", view.hunk_rows("blob.bin") == [] and view.hunk_rows("run.sh") == [])
    check(
        "the files list lists the unstaged side from the read's own status, the staged from git status",
        wait_for(
            lambda: {r.path for r in page.sidebar.file_rows().unstaged} >= {"text.txt", "blob.bin", "gone.txt", "untracked.txt"}
            and {r.path for r in page.sidebar.file_rows().staged} == {"staged.txt", "renamed.txt"}
        ),
        page.sidebar.file_rows(),
    )
    first = view.file_rows()[0][0]
    check(
        "the sidebar highlights the file at the top of the view",
        wait_for(lambda: page.sidebar.selected_path == first),
        (page.sidebar.selected_path, first),
    )

    # -- a gap expands -----------------------------------------------------------------
    gaps = dict((address, (remaining, shown)) for address, remaining, shown in view.gap_rows("text.txt"))
    check(
        "the gaps before, between and after the hunks are measured",
        gaps.get("before:0") == (1, 0) and gaps.get("before:1") == (33, 0) and "trailing:1" in gaps,
        gaps,
    )
    check("▼ 20 on the gap between the hunks", view.expand_gap("text.txt", "before:1", "down", 20))
    check(
        "twenty lines of context are drawn, thirteen remain",
        wait_for(lambda: dict((a, (r, s)) for a, r, s in view.gap_rows("text.txt")).get("before:1") == (13, 20)),
        view.gap_rows("text.txt"),
    )
    check("`all` on the trailing gap", view.expand_gap("text.txt", "trailing:1", "all", 0))
    check(
        "the trailing gap is measured off the file and drawn whole",
        wait_for(lambda: dict((a, (r, s)) for a, r, s in view.gap_rows("text.txt")).get("trailing:1") == (0, 12)),
        view.gap_rows("text.txt"),
    )

    # -- a files-list click reveals and focuses ------------------------------------------
    page.sidebar.click_file_row("text.txt", "unstaged")
    check(
        "a files-list click focuses the file's first hunk",
        wait_for(lambda: view.current()[:2] == ("text.txt", 0)),
        view.current(),
    )
    focus = window.get_focus()
    check("the keyboard is in the view", focus is not None and focus.is_ancestor(view), focus)
    check("the highlight followed the click", page.sidebar.selected_path == "text.txt", page.sidebar.selected_path)
    check(
        "the click shows the file's section alone",
        view.soloed == "text.txt" and [p for p, _k, shown in view.file_rows() if shown] == ["text.txt"],
        (view.soloed, view.file_rows()),
    )
    check("`.` walks into the next file, which shows alone in turn", view.focus_file(1) and view.soloed not in (None, "text.txt"))
    check(
        "and only that file shows",
        [p for p, _k, shown in view.file_rows() if shown] == [view.soloed],
        (view.soloed, view.file_rows()),
    )
    page.sidebar.click_section("unstaged")
    check(
        "the live heading's click shows every section again",
        view.soloed is None and all(shown for _p, _k, shown in view.file_rows()),
        (view.soloed, view.file_rows()),
    )
    check("solo text.txt again", view.solo("text.txt") and view.soloed == "text.txt")
    check("a filter word the soloed file matches keeps the solo", view.filter("text") == 1 and view.soloed == "text.txt")
    check(
        "a word that leaves the soloed file out drops the solo: the filter's files show",
        view.filter("") >= 1 and view.solo("text.txt") and view.soloed == "text.txt"
        and view.filter("png") >= 1 and view.soloed is None,
        (view.soloed, view.file_rows()),
    )
    view.filter("")

    # -- the watch: an external edit reloads within 2 s, an untouched hunk keeps its widget --
    # A line selection in the untouched hunk must ride the reload too (the
    # kept buffer keeps its range; a rebuilt hunk 1 must not clear it).
    check("a line selection in hunk 0 before the edit", view.select_lines("text.txt", 0, 2, 3) and view.selection() == ("text.txt", 0, 2, 3), view.selection())
    ids_before = view.hunk_serials("text.txt")
    focus_before = window.get_focus()
    lines[44] = "line 45 changed again\n"
    write_file(repo, "text.txt", "".join(lines))
    started = time.monotonic()
    landed = wait_for(lambda: view.hunk_serials("text.txt")[1:] != ids_before[1:] and page.settled(), timeout=2.0)
    elapsed = time.monotonic() - started
    check(f"an edit reloads the view through the watch within 2 s ({elapsed:.1f} s)", landed)
    ids_after = view.hunk_serials("text.txt")
    check("the edited hunk was rebuilt, the untouched one kept its widget", len(ids_after) == 2 and ids_after[0] == ids_before[0] and ids_after[1] != ids_before[1], (ids_before, ids_after))
    check("the keyboard stayed in the untouched hunk", window.get_focus() is focus_before and view.current()[:2] == ("text.txt", 0), (window.get_focus(), view.current()))
    check("and so did its line selection", view.selection() == ("text.txt", 0, 2, 3) and view.hunk_action_labels("text.txt", 0) == ("Stage lines", "Discard lines"), (view.selection(), view.hunk_action_labels("text.txt", 0)))
    view.clear_selection()
    check("the file's badge is still none and its hunks two", len(view.hunk_rows("text.txt")) == 2)
    check("the sections speak the read's hunk indexes", view.hunk_indexes("text.txt") == [0, 1], view.hunk_indexes("text.txt"))

    # -- a hunk above going away: the survivor keeps its widget and takes index 0 --
    lines[4] = "line 5\n"
    write_file(repo, "text.txt", "".join(lines))
    landed = wait_for(lambda: len(view.hunk_serials("text.txt")) == 1 and page.settled(), timeout=2.0)
    check("reverting the first hunk's edit reloads to one hunk", landed, view.hunk_serials("text.txt"))
    check(
        "the surviving hunk kept its widget and now speaks index 0",
        view.hunk_serials("text.txt") == ids_after[1:] and view.hunk_indexes("text.txt") == [0],
        (ids_after, view.hunk_serials("text.txt"), view.hunk_indexes("text.txt")),
    )
    emitted: list[tuple[str, int]] = []
    handler = view.connect("current-changed", lambda _v, p, h: emitted.append((p, h)))
    check(
        "reveal(hunk=0) lands on it and names index 0 to the sidebar",
        page.reveal("text.txt", hunk=0)
        and view.current()[:2] == ("text.txt", 0)
        and all(e == ("text.txt", 0) for e in emitted),  # unchanged from before the reload: nothing to emit
        (view.current(), emitted),
    )
    view.disconnect(handler)
    check("z finds the gap above it under its new address", any(a == "before:0" and remaining > 0 for a, remaining, _s in view.gap_rows("text.txt")), view.gap_rows("text.txt"))
    lines[4] = "line 5 changed\n"
    write_file(repo, "text.txt", "".join(lines))
    check("the edit put back, the two hunks return", wait_for(lambda: view.hunk_indexes("text.txt") == [0, 1] and page.settled(), timeout=2.0), view.hunk_indexes("text.txt"))
    check("the untouched hunk kept its widget through both reloads", view.hunk_serials("text.txt")[1:] == ids_after[1:], (ids_after, view.hunk_serials("text.txt")))

    # -- reveal by a line outside every hunk: the nearest hunk, not a refusal --
    check("a line in the gap between the hunks reveals the file on its nearest hunk", page.reveal("text.txt", line=20) and view.current()[:2] == ("text.txt", 0), view.current())
    check("and holds_line says the line itself is not in the diff", not view.holds_line("text.txt", None, 20) and view.holds_line("text.txt", None, 5))
    check("a line inside a hunk reveals that hunk", page.reveal("text.txt", line=45) and view.current()[:2] == ("text.txt", 1), view.current())

    # -- the page-local keys, through their actions ----------------------------------------
    page.reveal("text.txt", hunk=0)
    check("git.next-hunk is routed to the view", view.activate_action("git.next-hunk", None))
    check("and moved the current hunk", wait_for(lambda: view.current()[:2] == ("text.txt", 1)), view.current())
    # j / k: the cursor row within the focused hunk (wherever the last
    # reveal left it), then across the hunk's edges.
    hunk_view = view._focused_hunk.focused_view if view._focused_hunk is not None else None
    check("the keyboard sits in a hunk view", hunk_view is not None)
    row_before = hunk_view.cursor_row() if hunk_view is not None else -1
    for _ in range(row_before):
        view.activate_action("git.cursor-up", None)
    check("k walks the cursor up to the hunk's first row", hunk_view is not None and hunk_view.cursor_row() == 0, hunk_view.cursor_row() if hunk_view else None)
    check("j moves the cursor a row down", view.activate_action("git.cursor-down", None) and hunk_view.cursor_row() == 1, hunk_view.cursor_row() if hunk_view else None)
    check("k moves it back up", view.activate_action("git.cursor-up", None) and hunk_view.cursor_row() == 0)
    check(
        "k past the first row enters the hunk before, on its last row",
        view.activate_action("git.cursor-up", None)
        and view.current()[:2] == ("text.txt", 0)
        and view._focused_hunk.focused_view.cursor_row() == len(view._focused_hunk.focused_view.rows) - 1,
        (view.current(), view._focused_hunk.focused_view.cursor_row() if view._focused_hunk else None),
    )
    check(
        "j past its last row comes back to the hunk after, on its first row",
        view.activate_action("git.cursor-down", None)
        and view.current()[:2] == ("text.txt", 1)
        and view._focused_hunk.focused_view.cursor_row() == 0,
        view.current(),
    )
    view.activate_action("git.open-editor", None)
    check(
        "`e` asks for the file at the cursor's line",
        bool(opened) and opened[-1][0] == "text.txt" and opened[-1][1] >= 1,
        opened,
    )
    check("git.expand-gap draws the gap above the focused hunk", view.activate_action("git.expand-gap", None))
    check(
        "the gap between the hunks is spent",
        wait_for(lambda: any(a == "before:1" and remaining == 0 for a, remaining, _s in view.gap_rows("text.txt"))),
        view.gap_rows("text.txt"),
    )
    check("git.prev-file moves to the file before", view.activate_action("git.prev-file", None) and view.current()[0] != "text.txt", view.current())

    # -- the sidebar highlight follows the scroll ----------------------------------------------
    order = [path for path, _k, _s in view.file_rows()]
    window.set_focus(None)  # a focused hunk still in view would hold the highlight
    # The context views just drawn validate their heights a beat after
    # allocation (the column's range grows then): scroll once the range
    # has held still, or "the end" is the end of a shorter column.
    check("the column's range settles", wait_for(range_settled(view)))
    view.set_scroll(1.0)
    check(
        "scrolled to the end, the highlight moves to a later file",
        wait_for(lambda: page.sidebar.selected_path is not None and order.index(page.sidebar.selected_path) > 0),
        (page.sidebar.selected_path, order),
    )
    check(
        "the pinned header names the file scrolled into",
        wait_for(lambda: view.pinned_header_text() == "text.txt"),
        view.pinned_header_text(),
    )
    view.set_scroll(0.0)
    check("scrolled back, the first file again", wait_for(lambda: page.sidebar.selected_path == order[0]), page.sidebar.selected_path)

    # -- the files filter ------------------------------------------------------------------
    # (A GtkSearchEntry's search-changed is debounced: the words land a
    # beat after the text does, so every read below waits for them.)
    page.sidebar.set_filter_text("zzz")
    check(
        "the filter hides the sections and rows that don't match",
        wait_for(
            lambda: all(not shown for _p, _k, shown in view.file_rows())
            and all(not w.get_visible() for w in page.sidebar._file_widgets.values())
        ),
        view.file_rows(),
    )
    page.sidebar.set_filter_text("text")
    check(
        "and shows what does",
        wait_for(lambda: [p for p, _k, shown in view.file_rows() if shown] == ["text.txt"]),
        view.file_rows(),
    )
    page.sidebar.set_filter_text("")
    check("clearing shows every section", wait_for(lambda: all(shown for _p, _k, shown in view.file_rows())))

    # -- the find bar -------------------------------------------------------------------------
    page._search_bar.set_search_mode(True)
    page._search_entry.set_text("changed")
    check(
        "the find bar counts the matches across both hunks",
        wait_for(lambda: page._search_label.get_text() == "1 of 2"),
        page._search_label.get_text(),
    )
    check("the first match put the current hunk on the first", view.current()[:2] == ("text.txt", 0), view.current())
    page._search_entry.emit("next-match")
    check("Enter steps to the next hunk's match", page._search_label.get_text() == "2 of 2", page._search_label.get_text())
    check("the current hunk followed the match", view.current()[:2] == ("text.txt", 1), view.current())
    page._search_entry.emit("previous-match")
    check("Shift+Enter steps back", page._search_label.get_text() == "1 of 2", page._search_label.get_text())
    page._search_entry.set_text("nowhere")
    check("no match says so", wait_for(lambda: page._search_label.get_text() == "No matches"), page._search_label.get_text())
    page._search_bar.set_search_mode(False)
    check("closing the bar clears the label", page._search_label.get_text() == "")
    check("Escape is not held with the bar closed", not page.holds_escape())

    # -- other loads: the index (a rename), a commit -------------------------------------------
    page.load("staged")
    check("Ctrl+2 loads the index", wait_for(lambda: page.settled() and page.loaded == "staged"))
    check("the breadcrumb says staged", page.breadcrumb_text() == "working tree · staged", page.breadcrumb_text())
    check(
        "the files list shows the staged side live",
        wait_for(lambda: any(r.path == "staged.txt" and r.live for r in page.sidebar.file_rows().staged)),
        page.sidebar.file_rows(),
    )
    kinds = {path: kind for path, kind, _shown in view.file_rows()}
    check("the index shows the edit and the rename", kinds == {"staged.txt": "change", "renamed.txt": "rename"}, kinds)
    badges = {label: badge for label, badge, _p in view.badge_rows()}
    check("the rename reads `old → new` and its similarity", badges.get("old.txt → renamed.txt") == "renamed 100%", badges)
    check("a pure rename has no hunk", view.hunk_rows("renamed.txt") == [])
    check("no matches remain from the other side", page._search_label.get_text() == "")
    page.load({"show": fixture_sha})
    check("a commit load lands", wait_for(lambda: page.settled() and page.shows({"show": fixture_sha})))
    check(
        "the breadcrumb names the commit off the read's own git log",
        page.breadcrumb_text() == f"{fixture_sha[:7]} native fixture" and page._resolved_sha == fixture_sha,
        (page.breadcrumb_text(), page._resolved_sha),
    )
    kinds = {path: kind for path, kind, _shown in view.file_rows()}
    check(
        "the commit's files are drawn, its new files as new",
        kinds.get("text.txt") == "new" and kinds.get("pic.png") == "binary" and kinds.get("old.txt") == "new",
        kinds,
    )
    check("a picture added by the commit shows its one side", dict((label, p) for label, _b, p in view.badge_rows()).get("pic.png") is True)
    check("no monitors on a commit load", page._monitors == [])
    page.load("unstaged")
    check("back to the working tree", wait_for(lambda: page.settled() and page.loaded == "unstaged"))
    check("monitors are back", page._monitors != [])
    check("page_state carries the load", page.page_state() == {"kind": "git", "loaded": "unstaged"}, page.page_state())

    check_native_notes(repo, page, window, lines)
    check_native_mutations(repo, page, window, lines)

    # -- settings and the keys that write them; page_state round-trips a layout change ----------
    page.apply_settings({**SETTINGS, "git_wrap_lines": True, "git_layout": "split", "git_line_numbers": False})
    check(
        "wrap, layout and line numbers reach the view",
        view.options.wrap and view.is_split() and not view.options.line_numbers,
        view.options,
    )
    check("the menu's states follow", page._wrap_action.get_state().get_boolean() and page._layout_action.get_state().get_string() == "split")
    check("a layout change leaves page_state alone (the layout is a preference)", page.page_state() == {"kind": "git", "loaded": "unstaged"}, page.page_state())
    view.activate_action("git.layout-stack", None)
    check("the `2` key stacks the layout (applied to the page with no window action)", not view.is_split())
    view.activate_action("git.line-numbers", None)
    check("`l` toggles the line numbers back on", view.options.line_numbers, view.options)
    restored = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=gitloads.decode_state(page.page_state()),
    )
    restored.apply_settings({**SETTINGS, "git_layout": "split"})
    check("a page restored from page_state reads the same state back", restored.page_state() == page.page_state(), restored.page_state())
    check("and takes its layout from the setting before it maps", restored.diff_view.options.split)
    page.apply_settings(SETTINGS)

    # -- unparented for good, the view closes; re-parented, it stays ---------------------------------
    window.set_content(None)  # a drag to another strip: unrealized, then realized again
    window.set_content(page)
    wait_for(lambda: False, timeout=0.3)
    check("a re-parented page keeps its view and monitors", page.opened and page._monitors != [])
    page.page_closed()
    window.destroy()
    check("closing dropped the monitors", page._monitors == [])

    # -- restored from a layout: a commit, and a commit git no longer has -----------------------
    print("-- the view restored from a layout")
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=gitloads.decode_state({"kind": "git", "loaded": {"show": fixture_sha}, "parent": "base"}),
    )
    page.apply_settings(SETTINGS)
    check(
        "page_state before the open: the load, and no parent",
        page.page_state() == {"kind": "git", "loaded": {"show": fixture_sha}},
        page.page_state(),
    )
    check("no subject before the open", page.breadcrumb_text() == f"commit {fixture_sha[:7]}", page.breadcrumb_text())
    window = Gtk.Window(title="restore (native)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("the view opens into the saved commit", wait_for(lambda: page.settled() and page.shows({"show": fixture_sha})))
    check("no card stands in for it", page.card is None)
    check("breadcrumb reads <sha7> <subject>", page.breadcrumb_text() == f"{fixture_sha[:7]} native fixture", page.breadcrumb_text())
    check("the tab title stays short", page.page_title() == f"Git · {fixture_sha[:7]}", page.page_title())
    check("the ▸ row is the commit's", wait_for(lambda: page.sidebar.loaded_row_id() == f"commit:{fixture_sha}"), page.sidebar.loaded_row_id())
    page.page_closed()
    window.destroy()
    gone = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    page = GitPage(
        cwd_provider=lambda: repo,
        parent_provider=lambda _cwd: "main",
        on_closed=lambda p: None,
        loaded=gitloads.decode_state({"kind": "git", "loaded": {"show": gone}}),
    )
    page.apply_settings(SETTINGS)
    window = Gtk.Window(title="restore (native, gone commit)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    check("a commit git doesn't know opens the default mode", wait_for(lambda: page.settled() and page.loaded == "unstaged"), page.loaded)
    check("no card", page._stack.get_visible_child_name() == "view" and page.card is None)
    page.page_closed()
    window.destroy()
    clear_native_fixture(repo)


def check_native_notes(repo: str, page: GitPage, window: Gtk.Window, lines: list[str]) -> None:
    """The notes and highlights (PR 3 of the native-diff stack), on the
    working-tree view the native check left: `c` opens a draft card under
    the focused hunk with its editor (the page-local chords off, the page
    holding Escape), Ctrl+Enter saves the first line as the summary and
    the rest as the rationale, `E` re-opens it, Esc drops an edit or a
    draft, `}` / `{` walk the annotated hunks, the agent tools' doors
    (add_notes / add_highlights / clear_marks) land a batch whole or not
    at all, `a` folds the agent's cards, Delete drops one, and a reload
    keeps the notes of an untouched hunk and drops the changed hunk's."""
    print("-- the native viewer's notes and highlights")
    view = page.diff_view
    group = page._git_actions
    changed: list[int] = []
    editing_events: list[bool] = []
    handlers = [
        view.connect("notes-changed", lambda _v: changed.append(1)),
        view.connect("editing-changed", lambda _v, e: editing_events.append(e)),
    ]

    def enabled(name: str) -> bool:
        action = group.lookup_action(name)
        return action is not None and action.get_enabled()

    try:
        check("no notes to begin with", view.notes() == [] and view.highlights() == [] and not view.editing())
        check("reveal hunk 0 of text.txt", page.reveal("text.txt", hunk=0) and view.current()[:2] == ("text.txt", 0), view.current())

        # -- c: a draft card, its editor, the chords off meanwhile --------------------------------
        check("c opens a draft card with its editor", view.add_note_at_cursor() and view.editing() and page.holds_escape())
        rows = view.note_rows("text.txt", 0)
        check("the draft is anchored to the cursor line (new line 2, the hunk's first)", rows and rows[-1][:4] == ("", "user", "new", 2), rows)
        check("the page-local chords are off while the editor is open", not enabled("stage") and not enabled("close") and not enabled("add-note") and editing_events == [True], (editing_events, enabled("stage")))
        focus = window.get_focus()
        check("the keyboard is in the editor", isinstance(focus, Gtk.TextView) and focus.has_css_class("git-note-editor"), focus)
        check("the editor opens empty", view.note_editor_text() == "", view.note_editor_text())
        check("Ctrl+Enter on an empty text keeps the editor open", not view.commit_note() and view.editing())
        view.set_note_editor_text("Keep line 5\nit is load-bearing\r\n")
        check("Ctrl+Enter saves the note and closes the editor", view.commit_note() and not view.editing() and editing_events == [True, False], editing_events)
        notes = view.notes()
        check(
            "the note is a user note on new line 2 of text.txt, summary and rationale split",
            len(notes) == 1
            and (notes[0].source, notes[0].path, notes[0].side, notes[0].line, notes[0].summary, notes[0].rationale, notes[0].author)
            == ("user", "text.txt", "new", 2, "Keep line 5", "it is load-bearing", None),
            notes,
        )
        check("notes-changed fired once", changed == [1], changed)
        check("the card shows under hunk 0", view.note_rows("text.txt", 0) == [(notes[0].id, "user", "new", 2, "Keep line 5", True)], view.note_rows("text.txt", 0))
        check("the marker sits on the hunk's first line", view.note_marks("text.txt", 0) == [0], view.note_marks("text.txt", 0))
        check("the chords are back on and the page lets Escape go", enabled("stage") and enabled("close") and not page.holds_escape())
        check("the keyboard is back in the hunk", view._focused_hunk is not None and view.current()[:2] == ("text.txt", 0), (window.get_focus(), view.current()))

        # -- } / {: the annotated hunks --------------------------------------------------------------
        check("] moves to hunk 1", view.focus_hunk(1) and view.current()[:2] == ("text.txt", 1), view.current())
        check("{ walks back to the annotated hunk", view.focus_annotated(-1) and view.current()[:2] == ("text.txt", 0), view.current())
        check("} finds no annotated hunk after it", not view.focus_annotated(1) and view.current()[:2] == ("text.txt", 0))

        # -- E: edit; Esc: drop an edit, drop a draft -------------------------------------------------
        check("E opens the note's editor on its text", view.edit_first_note() and view.editing() and view.note_editor_text() == "Keep line 5\nit is load-bearing", view.note_editor_text())
        view.set_note_editor_text("Keep line five")
        edited = view.commit_note() and view.notes()
        check("saving re-words the note under the same id", edited and (view.notes()[0].id, view.notes()[0].summary, view.notes()[0].rationale) == (notes[0].id, "Keep line five", None), view.notes())
        check("E again, Esc drops the edit", view.edit_first_note() and view.set_note_editor_text("scratch") and view.cancel_note() and not view.editing() and view.notes()[0].summary == "Keep line five", view.notes())
        check("c then Esc leaves no draft behind", view.add_note_at_cursor() and view.cancel_note() and not view.editing() and len(view.note_rows("text.txt", 0)) == 1, view.note_rows("text.txt", 0))
        check("the chords are on again", enabled("stage") and not page.holds_escape())
        # With lines selected the cursor is the selection's last row: the
        # note lands there, not on the line after (hunk 0's rows 3..4 are
        # `-line 5` / `+line 5 changed`; row 5 is `line 6`).
        check("c with a selection anchors on its last line", view.select_lines("text.txt", 0, 3, 4) and view.add_note_at_cursor() and view.note_rows("text.txt", 0)[-1][:4] == ("", "user", "new", 5), view.note_rows("text.txt", 0))
        check("Esc drops that draft too", view.cancel_note() and not view.editing() and len(view.note_rows("text.txt", 0)) == 1)
        # The same selection made upward (Shift+Up, a drag that ends above
        # where it started) parks the insert mark on the first row: the
        # cursor row, and the note, are still the selection's last line.
        upward = False
        if view.select_lines("text.txt", 0, 3, 4) and view._focused_hunk is not None:
            hunk_view = view._focused_hunk.focused_view
            rows = hunk_view.selected_rows()
            if rows is not None:
                _ok, top = hunk_view.buffer.get_iter_at_line(rows[0])
                _ok, after = hunk_view.buffer.get_iter_at_line(rows[1] + 1)
                hunk_view.buffer.select_range(top, after)  # the insert mark at the top
                upward = (
                    hunk_view.buffer.get_iter_at_mark(hunk_view.buffer.get_insert()).get_line() == rows[0]
                    and view.selection() == ("text.txt", 0, 3, 4)
                    and hunk_view.cursor_row() == rows[1]
                )
        check("an upward selection's cursor row is its last row too", upward, view.selection())
        check("and c anchors on its last line", view.add_note_at_cursor() and view.note_rows("text.txt", 0)[-1][:4] == ("", "user", "new", 5), view.note_rows("text.txt", 0))
        check("Esc drops this draft as well", view.cancel_note() and not view.editing() and len(view.note_rows("text.txt", 0)) == 1)
        view.clear_selection()
        # The context menu's *Add note*: a draft anchored to the menu's row
        # (the cursor's, with no pointer here: the hunk's first line).
        section_1 = view._section_for("text.txt", "new").hunks[1]
        view.request_note(section_1)
        rows = view.note_rows("text.txt", 1)
        check("the menu's Add note opens a draft under that hunk", view.editing() and rows and rows[-1][:4] == ("", "user", "new", 42), rows)
        check("Esc drops it", view.cancel_note() and view.note_rows("text.txt", 1) == [])

        # -- the agent's doors: add_notes, a batch whole or not at all ------------------------------------
        ids = view.add_notes([NoteSpec("text.txt", "Agent says", rationale="why", author="claude", line=45)])
        check("add_notes lands an agent note on hunk 1", isinstance(ids, list) and len(ids) == 1 and view.note_rows("text.txt", 1) == [(ids[0], "agent", "new", 45, "Agent says", True)], (ids, view.note_rows("text.txt", 1)))
        check("the note is marked on its line (the addition, index 4)", view.note_marks("text.txt", 1) == [4], view.note_marks("text.txt", 1))
        check("[ back to hunk 0 (the cancelled draft's hunk kept the keyboard)", view.focus_hunk(-1) and view.current()[:2] == ("text.txt", 0), view.current())
        check("} walks to it", view.focus_annotated(1) and view.current()[:2] == ("text.txt", 1), view.current())
        bad = view.add_notes([NoteSpec("text.txt", "fine", line=45), NoteSpec("nope.txt", "x", line=1)])
        check("a batch with a bad address lands nothing and names the offender", isinstance(bad, str) and "nope.txt" in bad and len(view.notes()) == 2, (bad, len(view.notes())))
        by_hunk = view.add_notes([NoteSpec("text.txt", "by hunk", hunk=2, side="old")])
        check("a hunk address anchors on its first line of that side", isinstance(by_hunk, list) and (view.notes()[-1].side, view.notes()[-1].line) == ("old", 42), view.notes()[-1:])
        check("the hunk's cards follow the store's order", [r[4] for r in view.note_rows("text.txt", 1)] == ["Agent says", "by hunk"], view.note_rows("text.txt", 1))
        view.set_agent_notes_shown(False)
        check("a folds the agent's cards, the marks stay", all(not r[5] for r in view.note_rows("text.txt", 1)) and view.note_marks("text.txt", 1) == [0, 4], (view.note_rows("text.txt", 1), view.note_marks("text.txt", 1)))
        check("the user's card stays shown", view.note_rows("text.txt", 0)[0][5])
        view.set_agent_notes_shown(True)
        check("a again shows them", all(r[5] for r in view.note_rows("text.txt", 1)))

        # -- highlights ------------------------------------------------------------------------------------
        count = view.add_highlights([HighlightSpec("text.txt", 45, 0, 4), HighlightSpec("text.txt", 45, 5, 7, side="old", tone="error")])
        check("add_highlights paints two ranges", count == 2 and view.highlight_rows("text.txt", 1) == [(3, 5, 7, "error"), (4, 0, 4, "match")], (count, view.highlight_rows("text.txt", 1)))
        painted = [span for v in section_1.views for span in v.highlights]
        check("the views hold the tags", sorted(painted) == [(3, 5, 7, "error"), (4, 0, 4, "match")], painted)
        bad = view.add_highlights([HighlightSpec("text.txt", 45, 0, 99)])
        check("a range past the line is refused", isinstance(bad, str) and "not within" in bad and len(view.highlights()) == 2, bad)

        # -- delete and clear --------------------------------------------------------------------------------
        check("Delete drops the agent's note", view.delete_note(ids[0]) and ids[0] not in [n.id for n in view.notes()] and [r[4] for r in view.note_rows("text.txt", 1)] == ["by hunk"], view.note_rows("text.txt", 1))
        check("an unknown id is refused", not view.delete_note("n999"))
        check("clear_marks(notes) spares the user's", view.clear_marks(notes=True) == 1 and [n.source for n in view.notes()] == ["user"], view.notes())
        check("clear_marks(highlights) clears them", view.clear_marks(highlights=True) == 2 and view.highlights() == [] and view.highlight_rows("text.txt", 1) == [] and not any(v.highlights for v in section_1.views))

        # -- a reload keeps the untouched hunk's notes, drops the changed hunk's -----------------------------
        check("a note on hunk 1 again", isinstance(view.add_notes([NoteSpec("text.txt", "on hunk 1", line=45)]), list) and len(view.notes()) == 2)
        serials = view.hunk_serials("text.txt")
        lines[44] = "line 45 changed thrice\n"
        write_file(repo, "text.txt", "".join(lines))
        check("an edit to hunk 1 reloads the view", wait_for(lambda: view.hunk_serials("text.txt")[1:] != serials[1:] and page.settled(), timeout=2.0), view.hunk_serials("text.txt"))
        check(
            "the untouched hunk kept its note and card, the changed hunk lost its",
            [n.summary for n in view.notes()] == ["Keep line five"]
            and view.note_rows("text.txt", 0) == [(notes[0].id, "user", "new", 2, "Keep line five", True)]
            and view.note_rows("text.txt", 1) == []
            and view.hunk_serials("text.txt")[0] == serials[0],
            (view.notes(), view.note_rows("text.txt", 0), view.note_rows("text.txt", 1)),
        )
        serials = view.hunk_serials("text.txt")
        lines[44] = "line 45 changed again\n"
        write_file(repo, "text.txt", "".join(lines))
        check("the edit put back reloads again", wait_for(lambda: view.hunk_serials("text.txt")[1:] != serials[1:] and page.settled(), timeout=2.0))
        check("a second user note, by the menu on hunk 1, saved", (view.request_note(view._section_for("text.txt", "new").hunks[1]) or True) and view.set_note_editor_text("Second") and view.commit_note() and [n.summary for n in view.notes()] == ["Keep line five", "Second"], view.notes())
        fired = len(changed)
        check("Delete drops the user's note and its card", view.delete_note(notes[0].id) and [n.summary for n in view.notes()] == ["Second"] and view.note_rows("text.txt", 0) == [], (view.notes(), view.note_rows("text.txt", 0)))
        check("notes-changed fired for the delete", len(changed) == fired + 1, (fired, len(changed)))
        check("clear with include_user empties the store", view.clear_marks(notes=True, include_user=True) == 1 and view.notes() == [] and view.note_rows("text.txt", 1) == [])
        check("no editor is left open", not view.editing() and enabled("stage"))
    finally:
        for handler in handlers:
            view.disconnect(handler)


def check_native_mutations(repo: str, page: GitPage, window: Gtk.Window, lines: list[str]) -> None:
    """The staging interface (PR 3 of the native-diff stack), on the view
    the native check left on the working tree: the headers' buttons and
    their words per load and selection, a line selection (one hunk at a
    time, Esc clears, the page holds Escape meanwhile), staging lines
    and a hunk with the index read back (gitops.read_status, what the
    files list reads), staging a file (the binary, whole), unstaging a
    hunk and a file from the staged load, the real confirm dialog
    cancelled and confirmed on a discard, then a discard and an untracked
    file's trash with dialogs.confirm_dialog stubbed, a revert of a hunk
    from a commit and from `show HEAD` (no question asked), the sidebar's
    *Revert file* on a file row, the three-way retry over a committed
    context move, a binary's revert refused with a toast, and every
    mutation reloading the view by key with the untouched hunk's widget
    kept."""
    print("-- the native viewer's staging interface")
    view = page.diff_view
    sidebar = page.sidebar

    def status(*args: str) -> list[str]:
        return git_out(repo, *args).split()

    def index_paths() -> set[str]:
        """The staged paths as the files list reads them (gitops.read_status)."""
        read = gitops.read_status(repo)
        return {row.path for row in read.staged} if read is not None else {"<no status>"}

    def unstaged_paths() -> set[str]:
        read = gitops.read_status(repo)
        return {row.path for row in read.unstaged} if read is not None else {"<no status>"}

    def shown_paths() -> list[str]:
        return [p for p, _k, _s in view.file_rows()]

    def idle() -> bool:
        return not sidebar.busy and not view.busy() and page.settled()

    toasts: list[str] = []
    real_toast = page._toast

    def recording_toast(text: str) -> None:
        toasts.append(text)
        real_toast(text)

    page._toast = recording_toast

    asked: list[tuple[str, str, str]] = []
    answers: list[bool] = []

    def fake_confirm(_parent, heading, body, confirm_label, on_confirm, on_dismiss=None, **_kw) -> None:
        asked.append((heading, body, confirm_label))
        answer = answers.pop(0) if answers else True
        if answer:
            on_confirm()
        elif on_dismiss is not None:
            on_dismiss()

    # Gio refuses to trash on "system internal" mounts (a tmpfs /tmp, where
    # this repository lives): the mover is stubbed with one that records
    # the ask and moves the file aside, as the trash would.
    trashed: list[tuple[str, tuple[str, ...]]] = []
    aside = tempfile.mkdtemp(prefix="collins-trash-")

    def fake_trash(root: str, paths) -> gitops.GitResult:
        trashed.append((root, tuple(paths)))
        for path in paths:
            os.rename(os.path.join(root, path), os.path.join(aside, os.path.basename(path)))
        return gitops.GitResult(True, "", "")

    real_confirm = gitpage.dialogs.confirm_dialog
    real_trash = gitpage._trash_paths
    gitpage.dialogs.confirm_dialog = fake_confirm
    gitpage._trash_paths = fake_trash
    try:
        # -- the buttons' words on the unstaged load -----------------------------------
        check("the file header offers Stage file · Discard file", view.file_action_labels("text.txt") == ("Stage file", "Discard file"), view.file_action_labels("text.txt"))
        check("the hunk header offers Stage hunk · Discard hunk", view.hunk_action_labels("text.txt", 0) == ("Stage hunk", "Discard hunk"), view.hunk_action_labels("text.txt", 0))
        check("an untracked file's header offers the same (Discard file trashes it)", view.file_action_labels("untracked.txt") == ("Stage file", "Discard file"))
        check("a deleted file's header too (Discard file restores it)", view.file_action_labels("gone.txt") == ("Stage file", "Discard file"))

        # -- a line selection: one hunk at a time, the words follow, Esc clears --------------
        # text.txt's hunk 1: three context lines, `-line 45`, `+line 45
        # changed again`, three context lines (indexes 3 and 4 are the change).
        check("nothing is selected to begin with", view.selection() is None and not page.holds_escape())
        check("select_lines selects the change of hunk 1", view.select_lines("text.txt", 1, 3, 4))
        check("the selection reads (path, hunk, first, last)", view.selection() == ("text.txt", 1, 3, 4), view.selection())
        check("current() carries it", view.current() == ("text.txt", 1, ("text.txt", 1, 3, 4)), view.current())
        check("the hunk's buttons read Stage lines · Discard lines", view.hunk_action_labels("text.txt", 1) == ("Stage lines", "Discard lines"), view.hunk_action_labels("text.txt", 1))
        check("the other hunk's still read hunk", view.hunk_action_labels("text.txt", 0) == ("Stage hunk", "Discard hunk"))
        check("the page holds Escape while lines are selected", page.holds_escape())
        # A selection made across the change and into the context counts its
        # patch lines (every row of hunk 0 is a patch line here).
        check("selecting in another hunk moves the selection there", view.select_lines("text.txt", 0, 2, 5) and view.selection() == ("text.txt", 0, 2, 5), view.selection())
        check("and cleared the first hunk's", view.hunk_action_labels("text.txt", 1) == ("Stage hunk", "Discard hunk") and view.hunk_action_labels("text.txt", 0) == ("Stage lines", "Discard lines"))
        hunk_view = view._focused_hunk.focused_view if view._focused_hunk is not None else None
        check("the buffer's selection is snapped to whole rows", hunk_view is not None and hunk_view.selected_rows() == (2, 5), hunk_view.selected_rows() if hunk_view else None)
        check("Esc clears the selection", view.clear_selection() and view.selection() is None and not page.holds_escape())
        check("and the words go back", view.hunk_action_labels("text.txt", 0) == ("Stage hunk", "Discard hunk"))
        check("a second Esc has nothing to clear", not view.clear_selection())

        # -- the line numbers: press, drag, shift-press, through the gutter's y mapping ----------
        check("a press on the line numbers selects the row under it", view.gutter_press("text.txt", 0, 2) and view.selection() == ("text.txt", 0, 2, 2), view.selection())
        check("a drag down extends to the row under the pointer", view.gutter_drag("text.txt", 0, 5) and view.selection() == ("text.txt", 0, 2, 5), view.selection())
        check("a drag back up shrinks it", view.gutter_drag("text.txt", 0, 3) and view.selection() == ("text.txt", 0, 2, 3), view.selection())
        check("a Shift+press extends from the far end", view.gutter_press("text.txt", 0, 6, shift=True) and view.selection() == ("text.txt", 0, 2, 6), view.selection())
        check("a Shift+press above extends from the last row", view.gutter_press("text.txt", 0, 1, shift=True) and view.selection() == ("text.txt", 0, 1, 6), view.selection())
        check("the cursor reads the selection's last row (c and e speak of it)", view._focused_hunk is not None and view._focused_hunk.cursor_line() == ("new", 7), view._focused_hunk.cursor_line() if view._focused_hunk else None)
        view.clear_selection()

        # -- the right-click menu: the cursor lands under the pointer, the items -----------------------
        labels = view.context_menu_labels("text.txt", 1, 3)
        check("a right-click with nothing selected opens the menu", labels == ["Stage hunk", "Discard hunk", "Copy", "Open in editor", "Add note", "Expand context"], labels)
        section_1 = view._section_for("text.txt", "new").hunks[1]
        check("and put the cursor on the row under the pointer (the deletion, old 45)", section_1.anchor_at_cursor(section_1.views[-1]) == ("old", 45) and view.selection() is None, section_1.anchor_at_cursor(section_1.views[-1]))
        check("with lines selected the menu reads lines and keeps the selection", view.select_lines("text.txt", 1, 3, 4) and view.context_menu_labels("text.txt", 1, 0) == ["Stage lines", "Discard lines", "Copy", "Open in editor", "Add note", "Expand context"] and view.selection() == ("text.txt", 1, 3, 4), view.selection())
        view.clear_selection()

        # -- a hunk above shifting the ones below: the later hunk keeps everything, renumbered --
        # A line added inside hunk 0 moves hunk 1's new numbers; staging hunk
        # 0 then moves its old numbers (and its index). Neither touches hunk
        # 1's lines, so it keeps its widget, its selection and its note
        # through both — the header, the gutters and the note's line moved.
        # (Row 4 of hunk 1 is the addition, row 3 the deletion.)
        serials = view.hunk_serials("text.txt")
        later = view.add_notes([NoteSpec("text.txt", "on the later hunk", line=45)])
        check("a note on hunk 1's new line 45 and a selection in it", isinstance(later, list) and view.select_lines("text.txt", 1, 3, 4) and view.selection() == ("text.txt", 1, 3, 4), (later, view.selection()))
        check("hunk 1 stands at 42 on both sides", view.hunk_rows("text.txt")[1].startswith("@@ -42,7 +42,7 @@") and view.gutter_numbers("text.txt", 1)["new"][4] == 45, (view.hunk_rows("text.txt"), view.gutter_numbers("text.txt", 1)))
        write_file(repo, "text.txt", "".join(lines[:5] + ["line 5b\n"] + lines[5:]))
        check("a line added in hunk 0 reloads: hunk 0 rebuilt, hunk 1 kept", wait_for(lambda: view.hunk_serials("text.txt")[:1] != serials[:1] and page.settled(), timeout=2.0) and view.hunk_serials("text.txt")[1:] == serials[1:], (serials, view.hunk_serials("text.txt")))
        check("hunk 1's header and gutters moved with its new side", view.hunk_rows("text.txt")[1].startswith("@@ -42,7 +43,7 @@") and view.gutter_numbers("text.txt", 1)["new"][4] == 46 and view.gutter_numbers("text.txt", 1)["old"][3] == 45, (view.hunk_rows("text.txt"), view.gutter_numbers("text.txt", 1)))
        check("its selection held", view.selection() == ("text.txt", 1, 3, 4) and view.hunk_action_labels("text.txt", 1) == ("Stage lines", "Discard lines"), view.selection())
        check("its note held, renumbered to new line 46", [(n.summary, n.line) for n in view.notes()] == [("on the later hunk", 46)] and view.note_rows("text.txt", 1) == [(later[0], "agent", "new", 46, "on the later hunk", True)], (view.notes(), view.note_rows("text.txt", 1)))
        check("Stage hunk on hunk 0 (the selection stays hunk 1's: one selection in the stream)", view.hunk_action_labels("text.txt", 0) == ("Stage hunk", "Discard hunk") and view.click_hunk_action("text.txt", 0), view.hunk_action_labels("text.txt", 0))
        check("hunk 0 lands in the index", wait_for(idle, timeout=5.0) and wait_for(lambda: "text.txt" in index_paths() and page.settled(), timeout=5.0) and "+line 5b" in git_out(repo, "diff", "--cached", "--", "text.txt") and "line 45" not in git_out(repo, "diff", "--cached", "--", "text.txt"), git_out(repo, "diff", "--cached", "--", "text.txt"))
        check("the reload kept the later hunk's widget, now at index 0 with its old side moved", wait_for(lambda: len(view.hunk_rows("text.txt")) == 1 and view.hunk_rows("text.txt")[0].startswith("@@ -43,7 +43,7 @@") and page.settled(), timeout=5.0) and view.hunk_serials("text.txt") == serials[1:] and view.gutter_numbers("text.txt", 0)["old"][3] == 46 and view.gutter_numbers("text.txt", 0)["new"][4] == 46, (view.hunk_rows("text.txt"), view.hunk_serials("text.txt"), view.gutter_numbers("text.txt", 0)))
        check("the selection rode the staging of the hunk above, re-indexed", view.selection() == ("text.txt", 0, 3, 4) and view.hunk_action_labels("text.txt", 0) == ("Stage lines", "Discard lines"), (view.selection(), view.hunk_action_labels("text.txt", 0)))
        check("and so did its note", view.note_rows("text.txt", 0) == [(later[0], "agent", "new", 46, "on the later hunk", True)], view.note_rows("text.txt", 0))
        # Back to the fixture: the index reset for text.txt, the line taken out.
        git(repo, "reset", "-q", "--", "text.txt")
        write_file(repo, "text.txt", "".join(lines))
        check("the fixture's two hunks are back", wait_for(lambda: view.hunk_rows("text.txt")[:1] and view.hunk_rows("text.txt")[-1].startswith("@@ -42,7 +42,7 @@") and page.settled(), timeout=5.0) and len(view.hunk_rows("text.txt")) == 2 and "text.txt" not in index_paths(), (view.hunk_rows("text.txt"), index_paths()))
        check("the note followed hunk 1 back to line 45", view.note_rows("text.txt", 1) == [(later[0], "agent", "new", 45, "on the later hunk", True)] and view.delete_note(later[0]) and view.notes() == [], view.note_rows("text.txt", 1))
        view.clear_selection()

        # -- stage lines: the partial patch lands in the index, the view reloads by key --------
        serials = view.hunk_serials("text.txt")
        check("select the change of hunk 1 again", view.select_lines("text.txt", 1, 3, 4))
        check("the Stage lines button is pressed", view.click_hunk_action("text.txt", 1))
        check("the request runs behind the busy and the view reloads", wait_for(idle, timeout=5.0) and wait_for(lambda: len(view.hunk_rows("text.txt")) == 1, timeout=5.0), (view.hunk_rows("text.txt"), sidebar.busy, view.busy()))
        check("read_status lists text.txt in the index beside the fixture's staged files", index_paths() == {"renamed.txt", "staged.txt", "text.txt"}, index_paths())
        check("and still under unstaged: the other hunk is in the tree", "text.txt" in unstaged_paths(), unstaged_paths())
        cached = git_out(repo, "diff", "--cached", "--", "text.txt")
        check("and exactly the selected change", "+line 45 changed again" in cached and "line 5 changed" not in cached, cached)
        check("the toast counted the lines", toasts[-1:] == ["Staged 2 lines of text.txt"], toasts[-1:])
        check("the untouched hunk kept its widget", view.hunk_serials("text.txt") == serials[:1], (serials, view.hunk_serials("text.txt")))
        check("the selection went with its hunk", view.selection() is None)

        # -- stage hunk: the whole remaining hunk --------------------------------------------------
        check("Stage hunk on the remaining hunk", view.click_hunk_action("text.txt", 0))
        check("text.txt leaves the unstaged view", wait_for(idle, timeout=5.0) and wait_for(lambda: "text.txt" not in shown_paths(), timeout=5.0), view.file_rows())
        check("the working tree is clean of text.txt", "text.txt" not in unstaged_paths() and "text.txt" in index_paths(), (unstaged_paths(), index_paths()))
        check("the toast named the hunk", toasts[-1:] == ["Staged hunk 1 of text.txt"], toasts[-1:])
        check("the files list moved it to the staged side", wait_for(lambda: any(r.path == "text.txt" for r in sidebar.file_rows().staged) and not any(r.path == "text.txt" for r in sidebar.file_rows().unstaged)), sidebar.file_rows())

        # -- stage file: the binary, whole (git add; no patch to read) ------------------------------------
        check("Stage file on the binary", view.click_file_action("blob.bin"))
        check("blob.bin leaves the unstaged view", wait_for(idle, timeout=5.0) and wait_for(lambda: "blob.bin" not in shown_paths(), timeout=5.0), view.file_rows())
        check("read_status moved it to the index", "blob.bin" in index_paths() and "blob.bin" not in unstaged_paths(), (index_paths(), unstaged_paths()))
        check("the toast named the file", toasts[-1:] == ["Staged blob.bin"], toasts[-1:])

        # -- the staged load: Unstage hunk, Unstage file ----------------------------------------------
        page.load("staged")
        check("the staged load lands", wait_for(lambda: page.settled() and page.loaded == "staged"))
        check("the binary is on the staged load with Unstage file alone", view.file_action_labels("blob.bin") == ("Unstage file", None), view.file_action_labels("blob.bin"))
        check("Unstage file on the binary", view.click_file_action("blob.bin"))
        check("blob.bin leaves the staged view", wait_for(idle, timeout=5.0) and wait_for(lambda: "blob.bin" not in shown_paths(), timeout=5.0), view.file_rows())
        check("read_status has it back under unstaged", "blob.bin" in unstaged_paths() and "blob.bin" not in index_paths(), (index_paths(), unstaged_paths()))
        check("the words read Unstage, with no discard", view.file_action_labels("text.txt") == ("Unstage file", None) and view.hunk_action_labels("text.txt", 0) == ("Unstage hunk", None), (view.file_action_labels("text.txt"), view.hunk_action_labels("text.txt", 0)))
        check("a selection reads Unstage lines", view.select_lines("text.txt", 0, 3, 4) and view.hunk_action_labels("text.txt", 0) == ("Unstage lines", None))
        view.clear_selection()
        check("Unstage hunk on hunk 0", view.click_hunk_action("text.txt", 0))
        check("the index keeps the other hunk", wait_for(idle, timeout=5.0) and wait_for(lambda: len(view.hunk_rows("text.txt")) == 1, timeout=5.0), view.hunk_rows("text.txt"))
        cached = git_out(repo, "diff", "--cached", "--", "text.txt")
        check("hunk 0 is back in the working tree, hunk 1 still staged", "line 5 changed" not in cached and "line 45 changed again" in cached, cached)
        check("Unstage file", view.click_file_action("text.txt"))
        check("text.txt leaves the staged view", wait_for(idle, timeout=5.0) and wait_for(lambda: "text.txt" not in [p for p, _k, _s in view.file_rows()], timeout=5.0), view.file_rows())
        check("the index has none of it", "text.txt" not in index_paths() and "text.txt" in unstaged_paths(), (index_paths(), unstaged_paths()))
        check("no dialog was asked for a stage or unstage", asked == [], asked)

        # -- discard, after the real confirm dialog (cancelled, then confirmed) ---------------------------
        page.load("unstaged")
        check("back on the unstaged load with both hunks", wait_for(lambda: page.settled() and page.loaded == "unstaged" and len(view.hunk_rows("text.txt")) == 2, timeout=5.0), view.hunk_rows("text.txt"))
        gitpage.dialogs.confirm_dialog = real_confirm
        try:
            check("Discard hunk opens the confirm dialog over the window", view.click_hunk_action("text.txt", 0, discard=True) and wait_for(lambda: isinstance(window.get_visible_dialog(), Adw.AlertDialog), timeout=5.0), window.get_visible_dialog())
            dialog = window.get_visible_dialog()
            if isinstance(dialog, Adw.AlertDialog):
                check("its heading, body and Cancel", dialog.get_heading() == "Discard the changes?" and dialog.get_body().startswith("Discard hunk 1 in text.txt?") and dialog.get_close_response() == "cancel" and dialog.get_default_response() == "cancel", (dialog.get_heading(), dialog.get_body(), dialog.get_default_response()))
                check("the pressed button spins while the question is up", view.busy() and view.acting_button_spinning(), (view.busy(), view.acting_button_spinning()))
                dialog.close()  # Escape: the close response, cancel
            check("cancelled: the dialog is gone, the tree untouched, the view free", wait_for(lambda: window.get_visible_dialog() is None and idle(), timeout=5.0) and "line 5 changed" in git_out(repo, "diff", "--", "text.txt"), (window.get_visible_dialog(), view.busy()))
            check("and the spinner is off", not view.acting_button_spinning())
            check("Discard file on the binary asks", view.click_file_action("blob.bin", discard=True) and wait_for(lambda: isinstance(window.get_visible_dialog(), Adw.AlertDialog), timeout=5.0), window.get_visible_dialog())
            dialog = window.get_visible_dialog()
            if isinstance(dialog, Adw.AlertDialog):
                check("the question names the file, its button Discard", dialog.get_heading() == "Discard the changes?" and dialog.get_body().startswith("Discard the changes to blob.bin?") and dialog.get_response_label("confirm") == "Discard", (dialog.get_heading(), dialog.get_body()))
                dialog.set_close_response("confirm")
                dialog.close()  # the Discard button
                check("the pressed button still spins while the plan runs", view.busy() and view.acting_button_spinning(), (view.busy(), view.acting_button_spinning()))
            check("confirmed: the binary's change is checked out of the index", wait_for(lambda: window.get_visible_dialog() is None and idle(), timeout=5.0) and wait_for(lambda: "blob.bin" not in shown_paths(), timeout=5.0) and "blob.bin" not in unstaged_paths(), (shown_paths(), unstaged_paths()))
            check("the toast said so", toasts[-1:] == ["Discarded the changes to blob.bin"], toasts[-1:])
        finally:
            gitpage.dialogs.confirm_dialog = fake_confirm
        check("no dialog is left up", window.get_visible_dialog() is None)

        # -- discard, the confirm stubbed: the words, the lines, the trash, the restore ----------------------
        serials = view.hunk_serials("text.txt")
        answers.append(False)
        check("Discard hunk asks first", view.click_hunk_action("text.txt", 0, discard=True) and wait_for(lambda: len(asked) == 1, timeout=5.0), asked)
        check("the question names the hunk and the file, its button Discard", asked[-1][0] == "Discard the changes?" and asked[-1][1].startswith("Discard hunk 1 in text.txt?") and asked[-1][2] == "Discard", asked[-1])
        check("Cancel leaves the tree alone and the view free", wait_for(idle) and "line 5 changed" in git_out(repo, "diff", "--", "text.txt"))
        # A confirm can sit open while the page moves on: the plan was read
        # for the unstaged load, and an answer given after the sidebar
        # loaded the staged side runs nothing.
        held: list = []

        def holding_confirm(_parent, heading, body, confirm_label, on_confirm, on_dismiss=None, **_kw) -> None:
            held.append(on_confirm)

        gitpage.dialogs.confirm_dialog = holding_confirm
        check("Discard hunk asks, the question held open", view.click_hunk_action("text.txt", 0, discard=True) and wait_for(lambda: len(held) == 1, timeout=5.0), held)
        page.load("staged")
        check("the page moved to the staged load meanwhile", wait_for(lambda: page.loaded == "staged" and page.settled(), timeout=5.0), page.loaded)
        if held:
            held[0]()
        check("the late answer runs nothing: the tree is untouched, the toast says so, the view is free", wait_for(idle, timeout=5.0) and "line 5 changed" in git_out(repo, "diff", "--", "text.txt") and toasts[-1:] == ["The view changed since the request: nothing was done"], toasts[-1:])
        gitpage.dialogs.confirm_dialog = fake_confirm
        page.load("unstaged")
        check("back on the unstaged load", wait_for(lambda: page.loaded == "unstaged" and page.settled(), timeout=5.0), page.loaded)
        serials = view.hunk_serials("text.txt")  # the load switch rebuilt the sections
        check("Discard lines of the second hunk, confirmed", view.select_lines("text.txt", 1, 3, 4) and view.click_hunk_action("text.txt", 1, discard=True))
        check("the lines are gone from the working tree and the view", wait_for(idle, timeout=5.0) and wait_for(lambda: len(view.hunk_rows("text.txt")) == 1, timeout=5.0) and "line 45 changed again" not in git_out(repo, "diff", "--", "text.txt"), (view.hunk_rows("text.txt"), asked[-1]))
        check("the confirm counted the lines", asked[-1][1].startswith("Discard 2 lines in text.txt?"), asked[-1])
        check("the untouched hunk kept its widget through the discard", view.hunk_serials("text.txt") == serials[:1], (serials, view.hunk_serials("text.txt")))
        lines[44] = "line 45\n"
        check("Discard file on an untracked file moves it to the trash after the confirm", view.click_file_action("untracked.txt", discard=True) and wait_for(idle, timeout=5.0) and wait_for(lambda: not os.path.exists(os.path.join(repo, "untracked.txt")), timeout=5.0), (asked[-1], os.path.exists(os.path.join(repo, "untracked.txt"))))
        check("its question said trash", asked[-1] == ("Move to the trash?", "Move untracked.txt to the trash?", "Move to trash"), asked[-1])
        check("the mover was handed the repository root and the one path", trashed == [(repo, ("untracked.txt",))], trashed)
        check("the section left the view", wait_for(lambda: "untracked.txt" not in [p for p, _k, _s in view.file_rows()], timeout=5.0), view.file_rows())
        check("Discard file on a deleted file restores it", view.click_file_action("gone.txt", discard=True) and wait_for(idle, timeout=5.0) and wait_for(lambda: os.path.exists(os.path.join(repo, "gone.txt")), timeout=5.0), asked[-1])
        check("its question said restore", asked[-1][0] == "Restore the file?" and asked[-1][2] == "Restore", asked[-1])

        # -- revert from a commit: no question, and the sidebar's Revert file -------------------------------
        # -- the file rows' context menu on the working tree ------------------------------------
        # A staged row offers Unstage and the editor; an unstaged one Stage,
        # Discard… and the editor. Stage / Unstage run at once with the
        # row's toast; Discard… asks the header button's words; Open in
        # editor is the window's open-in-editor action with no line.
        labels = sidebar.file_menu_labels("staged.txt", "staged")
        check("a staged row's menu offers Unstage file and the editor", labels == ["Unstage file", "Open in editor"], labels)
        with open(os.path.join(repo, "menu.txt"), "w") as fh:
            fh.write("menu\n")
        check("an untracked file arrives with the watch", wait_for(lambda: idle() and "menu.txt" in shown_paths(), timeout=5.0), shown_paths())
        labels = sidebar.file_menu_labels("menu.txt", "unstaged")
        check("an unstaged row's menu offers Stage file, Discard file… and the editor", labels == ["Stage file", "Discard file…", "Open in editor"], labels)
        check("Stage file from the row's menu", sidebar.activate_file_menu("menu.txt", "Stage file", "unstaged"))
        check("stages it, with the toast", wait_for(idle, timeout=5.0) and wait_for(lambda: "menu.txt" in index_paths(), timeout=5.0) and toasts[-1:] == ["Staged menu.txt"], (index_paths(), toasts[-1:]))
        check("the row moved to STAGED", wait_for(lambda: idle() and sidebar.file_menu_labels("menu.txt", "staged") == ["Unstage file", "Open in editor"], timeout=5.0), sidebar.file_rows())
        check("Unstage file from the row's menu", sidebar.activate_file_menu("menu.txt", "Unstage file", "staged"))
        check("unstages it, with the toast", wait_for(idle, timeout=5.0) and wait_for(lambda: "menu.txt" not in index_paths(), timeout=5.0) and toasts[-1:] == ["Unstaged menu.txt"], (index_paths(), toasts[-1:]))
        check("the row is back under UNSTAGED", wait_for(lambda: idle() and sidebar.file_menu_labels("menu.txt", "unstaged") is not None, timeout=5.0), sidebar.file_rows())
        opened_paths: list[tuple[str, int, int]] = []
        win_group = Gio.SimpleActionGroup()
        open_action = Gio.SimpleAction.new("open-in-editor", GLib.VariantType.new("(sii)"))
        open_action.connect("activate", lambda _a, param: opened_paths.append(param.unpack()))
        win_group.add_action(open_action)
        window.insert_action_group("win", win_group)
        check("Open in editor from the row's menu", sidebar.activate_file_menu("menu.txt", "Open in editor", "unstaged"))
        check("activates the window's open-in-editor with the full path and no line", opened_paths == [(os.path.join(repo, "menu.txt"), 0, 0)], opened_paths)
        window.insert_action_group("win", None)
        # Open In…: two footer apps configured, one that takes a file and a
        # terminal that doesn't — the submenu lists the first alone, and a
        # pick reaches footerapps.launch_app_file with the file's full path.
        # The desktop's app registry is stubbed: CI has no .desktop entries.

        class FakeApp:
            def __init__(self, app_id: str, name: str, files: bool) -> None:
                self.app_id, self.name, self.files = app_id, name, files

            def get_id(self) -> str:
                return self.app_id

            def get_display_name(self) -> str:
                return self.name

            def get_icon(self):
                return None

            def supports_files(self) -> bool:
                return self.files

            def supports_uris(self) -> bool:
                return False

        apps = {"editor.desktop": FakeApp("editor.desktop", "Fake Editor", True), "term.desktop": FakeApp("term.desktop", "Fake Terminal", False)}
        launched: list[tuple[str, str]] = []
        footerapps = gitpage.footerapps
        real_apps = (footerapps.resolve_apps, footerapps.resolve_app, footerapps.launch_app_file)
        footerapps.resolve_apps = lambda ids: [(i, apps[i]) for i in ids if i in apps]
        footerapps.resolve_app = lambda app_id: apps.get(app_id)
        footerapps.launch_app_file = lambda info, path: launched.append((info.get_id(), path)) or True
        try:
            page.apply_settings({**SETTINGS, "footer_apps": ["editor.desktop", "term.desktop", "gone.desktop"]})
            check("with footer apps configured the row's menu grows Open In…", sidebar.file_menu_labels("menu.txt", "unstaged") == ["Stage file", "Discard file…", "Open in editor", "Open In…"], sidebar.file_menu_labels("menu.txt", "unstaged"))
            check("listing the app that takes a file alone", sidebar.file_open_with_labels("menu.txt", "unstaged") == ["Fake Editor"], sidebar.file_open_with_labels("menu.txt", "unstaged"))
            check("a pick from the submenu", sidebar.activate_file_menu("menu.txt", "Open In…", "unstaged", app_id="editor.desktop"))
            check("hands the file's full path to the app", launched == [("editor.desktop", os.path.join(repo, "menu.txt"))], launched)
            check("an app the submenu doesn't list can't be picked", not sidebar.activate_file_menu("menu.txt", "Open In…", "unstaged", app_id="term.desktop") and len(launched) == 1, launched)
        finally:
            footerapps.resolve_apps, footerapps.resolve_app, footerapps.launch_app_file = real_apps
            page.apply_settings(SETTINGS)
        check("with the apps gone the submenu is gone", sidebar.file_open_with_labels("menu.txt", "unstaged") == [], sidebar.file_open_with_labels("menu.txt", "unstaged"))
        asks = len(asked)
        answers.append(False)
        check("Discard file… from the row's menu, cancelled", sidebar.activate_file_menu("menu.txt", "Discard file…", "unstaged"))
        check("asked the trash question and left the file", wait_for(idle, timeout=5.0) and asked[asks:] == [("Move to the trash?", "Move menu.txt to the trash?", "Move to trash")] and os.path.exists(os.path.join(repo, "menu.txt")), asked[asks:])
        check("Discard file… from the row's menu, confirmed", sidebar.activate_file_menu("menu.txt", "Discard file…", "unstaged"))
        check("moves it to the trash, with the toast", wait_for(idle, timeout=5.0) and wait_for(lambda: not os.path.exists(os.path.join(repo, "menu.txt")), timeout=5.0) and trashed[-1] == (repo, ("menu.txt",)) and toasts[-1:] == ["Moved menu.txt to the trash"], (trashed[-1:], toasts[-1:]))
        check("and the row left the list", wait_for(lambda: idle() and sidebar.file_menu_labels("menu.txt", "unstaged") is None, timeout=5.0), sidebar.file_rows())
        view.set_busy(True)
        before = len(toasts)
        check("Unstage file from the menu while the view is busy", sidebar.activate_file_menu("staged.txt", "Unstage file", "staged"))
        check("is refused with the busy toast", toasts[before:] == ["Another git operation is still running"] and "staged.txt" in index_paths(), toasts[before:])
        view.set_busy(False)
        git(repo, "commit", "-qm", "staged edit")
        edit_sha = head_sha(repo)
        page.poll_tick()
        page.load({"show": edit_sha})
        check("the commit of the staged edit loads", wait_for(lambda: page.settled() and page.shows({"show": edit_sha}), timeout=5.0), page.loaded)
        check("the words read Revert, with no discard", view.file_action_labels("staged.txt") == ("Revert file", None) and view.hunk_action_labels("staged.txt", 0) == ("Revert hunk", None), (view.file_action_labels("staged.txt"), view.hunk_action_labels("staged.txt", 0)))
        check("a selection reads Revert lines", view.select_lines("staged.txt", 0, 0, 1) and view.hunk_action_labels("staged.txt", 0) == ("Revert lines", None))
        view.clear_selection()
        asks = len(asked)
        check("Revert hunk", view.click_hunk_action("staged.txt", 0))
        check("the working tree got the reverse of the hunk", wait_for(idle, timeout=5.0) and wait_for(lambda: "staged 3\n" in open(os.path.join(repo, "staged.txt")).read(), timeout=5.0), asked[asks:])
        check("with no question asked", len(asked) == asks, asked[asks:])
        check("the toast", toasts[-1:] == ["Reverted hunk 1 of staged.txt"], toasts[-1:])
        check("the commit's view is unchanged by a revert into the tree", view.hunk_rows("staged.txt") != [] and page.shows({"show": edit_sha}))
        git(repo, "checkout", "-q", "--", "staged.txt")
        check("the file row's context menu on a commit offers Revert file and the editor", sidebar.file_menu_labels("staged.txt") == ["Revert file", "Open in editor"], sidebar.file_menu_labels("staged.txt"))
        loaded_row = next(r for r in sidebar.commit_rows() if r.sha == edit_sha)
        check("a commit row's menu still offers Revert… while that commit is loaded", sidebar.commit_menu_labels(loaded_row.id) == ["Copy sha", "Revert…", "Reload"], sidebar.commit_menu_labels(loaded_row.id))
        check("Revert file from the sidebar", sidebar.activate_file_menu("staged.txt", "Revert file"))
        check("the working tree got the reverse of the whole file, unstaged", wait_for(idle, timeout=5.0) and wait_for(lambda: "staged 3\n" in open(os.path.join(repo, "staged.txt")).read(), timeout=5.0) and "staged.txt" in unstaged_paths() and "staged.txt" not in index_paths(), (unstaged_paths(), index_paths()))
        check("with no question asked, and the file's toast", len(asked) == asks and toasts[-1:] == ["Reverted staged.txt"], (asked[asks:], toasts[-1:]))
        check("the view is free again", wait_for(idle) and page.shows({"show": edit_sha}))
        # The menu is not greyed while a mutation runs: the page's busy
        # gate meets it with the buttons' toast, and nothing is asked.
        view.set_busy(True)
        before = len(toasts)
        check("Revert file from the sidebar while the view is busy", sidebar.activate_file_menu("staged.txt", "Revert file"))
        check("is refused with the busy toast", toasts[before:] == ["Another git operation is still running"] and len(asked) == asks, toasts[before:])
        view.set_busy(False)
        check("and the tree is untouched", wait_for(idle) and "staged 3\n" in open(os.path.join(repo, "staged.txt")).read())
        # A revert whose context moved retries three-way: a later commit
        # changed line 6 (inside the hunk's context), so the reverse apply
        # of the older commit's hunk misses, and the merge lands both.
        # (`--3way` implies `--index`, so the file must be clean first — a
        # dirty one is refused with git's "does not match index".)
        git(repo, "checkout", "-q", "--", "staged.txt")
        ten = "".join(f"staged {n}\n" for n in range(1, 11)).replace("staged 3", "staged three")
        write_file(repo, "staged.txt", ten.replace("staged 6", "staged SIX"))
        git(repo, "add", "staged.txt")
        git(repo, "commit", "-qm", "six")
        page.poll_tick()
        wait_for(page.settled)
        check("Revert hunk of the older commit over the moved context", view.click_hunk_action("staged.txt", 0) and wait_for(idle, timeout=5.0))
        merged = open(os.path.join(repo, "staged.txt")).read()
        check("the three-way retry reverted the line and kept the later edit", wait_for(lambda: "staged 3\n" in open(os.path.join(repo, "staged.txt")).read(), timeout=5.0) and "staged SIX\n" in merged and "<<<<" not in merged, merged)
        check("and staged the result, as --3way does", "staged.txt" in index_paths(), index_paths())
        check("the toast said merged three-way", toasts[-1:] == ["Reverted hunk 1 of staged.txt — merged three-way (the result is staged)"], toasts[-1:])
        git(repo, "reset", "-q", "--", "staged.txt")
        git(repo, "checkout", "-q", "--", "staged.txt")

        # -- revert a hunk from `show HEAD` into the working tree ------------------------------------------
        page.load({"show": "HEAD"})
        check("show HEAD loads (the `six` commit)", wait_for(lambda: page.settled() and page.shows({"show": "HEAD"}), timeout=5.0) and page._resolved_sha == head_sha(repo), (page.loaded, page._resolved_sha))
        check("its hunk offers Revert hunk", view.hunk_action_labels("staged.txt", 0) == ("Revert hunk", None), view.hunk_action_labels("staged.txt", 0))
        asks = len(asked)
        check("Revert hunk", view.click_hunk_action("staged.txt", 0) and wait_for(idle, timeout=5.0) and wait_for(lambda: "staged 6\n" in open(os.path.join(repo, "staged.txt")).read(), timeout=5.0), asked[asks:])
        check("no question asked", len(asked) == asks, asked[asks:])
        check("the reverse of HEAD's hunk is in the working tree, unstaged", "staged SIX" not in open(os.path.join(repo, "staged.txt")).read() and "staged.txt" in unstaged_paths() and "staged.txt" not in index_paths(), (unstaged_paths(), index_paths()))
        check("the toast", toasts[-1:] == ["Reverted hunk 1 of staged.txt"], toasts[-1:])
        check("the commit's view stays", page.shows({"show": "HEAD"}) and view.hunk_rows("staged.txt") != [])
        git(repo, "checkout", "-q", "--", "staged.txt")

        # -- a binary's revert is refused with a toast (its data is not in the patch) --------------------------
        write_file(repo, "blob.bin", bytes(range(0, 256, 2)) * 8)
        git(repo, "add", "blob.bin")
        git(repo, "commit", "-qm", "binary")
        page.poll_tick()
        page.load("unstaged")
        check("off the commit", wait_for(lambda: page.settled() and page.loaded == "unstaged", timeout=5.0))
        page.load({"show": "HEAD"})
        check("show HEAD loads the binary commit", wait_for(lambda: page.settled() and page.shows({"show": "HEAD"}) and page._resolved_sha == head_sha(repo), timeout=5.0) and shown_paths() == ["blob.bin"], (shown_paths(), page._resolved_sha))
        check("a binary has no hunk to revert; its file header offers Revert file", view.hunk_rows("blob.bin") == [] and view.file_action_labels("blob.bin") == ("Revert file", None), view.file_action_labels("blob.bin"))
        asks, before = len(asked), len(toasts)
        check("Revert file on the binary", view.click_file_action("blob.bin"))
        check("the refusal toasts, asks nothing and changes nothing", wait_for(lambda: len(toasts) > before, timeout=5.0) and toasts[-1] == "blob.bin is binary: use git from a shell" and len(asked) == asks and wait_for(idle) and "blob.bin" not in unstaged_paths() and index_paths() == set(), (toasts[-1:], asked[asks:], unstaged_paths(), index_paths()))
        check("the view is free after the refusal", not view.busy() and page.shows({"show": "HEAD"}))

        page.load("unstaged")
        check("back on the working tree", wait_for(lambda: page.settled() and page.loaded == "unstaged", timeout=5.0))
        check("no lingering busy", idle())
    finally:
        page._toast = real_toast
        gitpage.dialogs.confirm_dialog = real_confirm
        gitpage._trash_paths = real_trash
        shutil.rmtree(aside, ignore_errors=True)


def check_outside_a_repo(scratch: str) -> None:
    print("-- outside a repository")
    nowhere = os.path.join(scratch, "nowhere")
    os.mkdir(nowhere)
    page = GitPage(
        cwd_provider=lambda: nowhere,
        parent_provider=lambda _cwd: None,
        on_closed=lambda p: None,
    )
    page.apply_settings(SETTINGS)
    window = Gtk.Window(title="check_git_page (no repo)", default_width=900, default_height=600)
    window.set_child(page)
    window.present()
    shown = wait_for(lambda p=page: p._stack.get_visible_child_name() == "card")
    check(
        "the not-a-repository card comes up",
        shown and card_title(page) == "Not a git repository",
        card_title(page),
    )
    check("no parent to name", page._parent_target is None)
    check("Escape is not held by a card", not page.holds_escape())
    check("the view is not open", not page.opened and not page.settled())
    # A load asked of the page on the card leaves the card up while there
    # is still no tree...
    page.load("staged")
    wait_for(lambda: False, timeout=0.3)
    check("a load with no tree keeps the card", page.card == "not-a-repo" and not page.opened and not page.opening, (page.card, page.opened))
    page.recheck_tree()
    wait_for(lambda: False, timeout=0.3)
    check("a re-check with no tree keeps the card too", page.card == "not-a-repo" and not page.opened and not page.opening, (page.card, page.opened))
    # ...and opens the view at once when the tree turned up (the host's
    # open_git_page on a page that stood on the card — recheck_tree with
    # no mode, the footer's button; load(mode) takes the same path): the
    # card is not the page's last word while the open is out.
    git(nowhere, "init", "-q", "-b", "main")
    git(nowhere, "config", "user.email", "t@example.com")
    git(nowhere, "config", "user.name", "Test")
    git(nowhere, "commit", "-q", "--allow-empty", "-m", "first")
    page.recheck_tree()
    check("a re-check once the tree exists opens the view (card still up, opening)", page.opening and page.card == "not-a-repo", (page.opening, page.card))
    check("the view opens into the load it held", wait_for(lambda: page.settled() and page.loaded == "staged" and page.card is None), (page.card, page.loaded, page.opened))
    page.load("unstaged")
    check("a load then reads as usual", wait_for(lambda: page.settled() and page.loaded == "unstaged" and page.card is None), (page.card, page.loaded))
    page.page_closed()
    window.destroy()


def main() -> int:
    if GIT is None:
        print("check_git_page: git isn't installed; skipping")
        return 0
    scratch = tempfile.mkdtemp(prefix="collins-git-page-")
    try:
        # The page's git (the reads, a commit's subject, a saved commit's
        # existence) comes off a PATH holding nothing but git: the page
        # needs no other program.
        bindir = os.path.join(scratch, "bin")
        os.mkdir(bindir)
        os.symlink(GIT, os.path.join(bindir, "git"))
        repo = make_repo(scratch)

        real_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir
        try:
            check_sidebar(repo)
            check_native(repo)
            check_outside_a_repo(scratch)
        finally:
            os.environ["PATH"] = real_path
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
